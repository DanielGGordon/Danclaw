"""Tests for dispatcher.repository — async repository abstraction layer."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from dispatcher.database import _SCHEMA_SQL
from dispatcher.repository import (
    ChannelBindingRow,
    MessageRow,
    Repository,
    SessionRow,
    VALID_STATES,
)


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db():
    """Yield an in-memory aiosqlite connection with schema applied."""
    async with aiosqlite.connect(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.commit()
        yield conn


@pytest_asyncio.fixture
async def repo(db):
    """Yield a Repository backed by the in-memory database."""
    return Repository(db)


# ── Session: create ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_session_returns_session_row(repo):
    session = await repo.create_session("test-agent")
    assert isinstance(session, SessionRow)
    assert session.agent_name == "test-agent"
    assert session.state == "ACTIVE"
    assert session.id  # non-empty


@pytest.mark.asyncio
async def test_create_session_with_explicit_id(repo):
    session = await repo.create_session("agent", session_id="custom-id")
    assert session.id == "custom-id"


@pytest.mark.asyncio
async def test_create_session_with_custom_state(repo):
    session = await repo.create_session("agent", state="WAITING_FOR_HUMAN")
    assert session.state == "WAITING_FOR_HUMAN"


@pytest.mark.asyncio
async def test_create_session_invalid_state(repo):
    with pytest.raises(ValueError, match="Invalid session state"):
        await repo.create_session("agent", state="BOGUS")


@pytest.mark.asyncio
async def test_create_session_generates_unique_ids(repo):
    s1 = await repo.create_session("agent")
    s2 = await repo.create_session("agent")
    assert s1.id != s2.id


@pytest.mark.asyncio
async def test_create_session_sets_timestamps(repo):
    session = await repo.create_session("agent")
    assert session.created_at
    assert session.updated_at
    assert session.created_at == session.updated_at


# ── Session: get ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_session_returns_existing(repo):
    created = await repo.create_session("agent", session_id="s1")
    fetched = await repo.get_session("s1")
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.agent_name == "agent"


@pytest.mark.asyncio
async def test_get_session_returns_none_for_missing(repo):
    result = await repo.get_session("nonexistent")
    assert result is None


# ── Session: update state ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_session_state(repo):
    await repo.create_session("agent", session_id="s1")
    updated = await repo.update_session_state("s1", "DONE")
    assert updated is not None
    assert updated.state == "DONE"


@pytest.mark.asyncio
async def test_update_session_state_updates_timestamp(repo):
    created = await repo.create_session("agent", session_id="s1")
    updated = await repo.update_session_state("s1", "ERROR")
    assert updated is not None
    assert updated.updated_at >= created.updated_at


@pytest.mark.asyncio
async def test_update_session_state_nonexistent(repo):
    result = await repo.update_session_state("nonexistent", "DONE")
    assert result is None


@pytest.mark.asyncio
async def test_update_session_state_invalid(repo):
    await repo.create_session("agent", session_id="s1")
    with pytest.raises(ValueError, match="Invalid session state"):
        await repo.update_session_state("s1", "INVALID")


@pytest.mark.asyncio
async def test_update_session_state_all_valid_transitions(repo):
    """Every valid state can be set via update."""
    await repo.create_session("agent", session_id="s1")
    for state in VALID_STATES:
        updated = await repo.update_session_state("s1", state)
        assert updated.state == state


# ── Session: list ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_sessions_empty(repo):
    result = await repo.list_sessions()
    assert result == []


@pytest.mark.asyncio
async def test_list_sessions_returns_all(repo):
    await repo.create_session("a1", session_id="s1")
    await repo.create_session("a2", session_id="s2")
    sessions = await repo.list_sessions()
    assert len(sessions) == 2
    ids = {s.id for s in sessions}
    assert ids == {"s1", "s2"}


@pytest.mark.asyncio
async def test_list_sessions_filter_by_state(repo):
    await repo.create_session("a", session_id="s1", state="ACTIVE")
    await repo.create_session("a", session_id="s2", state="DONE")
    await repo.create_session("a", session_id="s3", state="ACTIVE")
    active = await repo.list_sessions(state="ACTIVE")
    assert len(active) == 2
    assert all(s.state == "ACTIVE" for s in active)


@pytest.mark.asyncio
async def test_list_sessions_invalid_state_filter(repo):
    with pytest.raises(ValueError, match="Invalid session state"):
        await repo.list_sessions(state="NOPE")


# ── Session: attribution ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_session_default_attribution(repo):
    session = await repo.create_session("agent")
    assert session.attribution == "bot"


@pytest.mark.asyncio
async def test_create_session_custom_attribution(repo):
    session = await repo.create_session("agent", attribution="[via terminal]")
    assert session.attribution == "[via terminal]"


@pytest.mark.asyncio
async def test_get_session_includes_attribution(repo):
    await repo.create_session("agent", session_id="s1", attribution="custom-bot")
    fetched = await repo.get_session("s1")
    assert fetched is not None
    assert fetched.attribution == "custom-bot"


@pytest.mark.asyncio
async def test_update_session_attribution(repo):
    await repo.create_session("agent", session_id="s1")
    updated = await repo.update_session_attribution("s1", "[via terminal]")
    assert updated is not None
    assert updated.attribution == "[via terminal]"


@pytest.mark.asyncio
async def test_update_session_attribution_nonexistent(repo):
    result = await repo.update_session_attribution("nonexistent", "custom")
    assert result is None


@pytest.mark.asyncio
async def test_update_session_attribution_persists(repo):
    await repo.create_session("agent", session_id="s1")
    await repo.update_session_attribution("s1", "new-label")
    fetched = await repo.get_session("s1")
    assert fetched is not None
    assert fetched.attribution == "new-label"


@pytest.mark.asyncio
async def test_list_sessions_includes_attribution(repo):
    await repo.create_session("a", session_id="s1", attribution="bot")
    await repo.create_session("a", session_id="s2", attribution="[via slack]")
    sessions = await repo.list_sessions()
    attribs = {s.id: s.attribution for s in sessions}
    assert attribs == {"s1": "bot", "s2": "[via slack]"}


# ── Messages: save ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_message_returns_message_row(repo):
    await repo.create_session("agent", session_id="s1")
    msg = await repo.save_message(
        session_id="s1", role="user", content="hello",
        source="terminal", channel_ref="tty1", user_id="u1",
    )
    assert isinstance(msg, MessageRow)
    assert msg.session_id == "s1"
    assert msg.role == "user"
    assert msg.content == "hello"
    assert msg.source == "terminal"
    assert msg.channel_ref == "tty1"
    assert msg.user_id == "u1"
    assert msg.id is not None


@pytest.mark.asyncio
async def test_save_message_autoincrement_ids(repo):
    await repo.create_session("agent", session_id="s1")
    m1 = await repo.save_message("s1", "user", "a", "t", "r", "u")
    m2 = await repo.save_message("s1", "user", "b", "t", "r", "u")
    assert m2.id > m1.id


@pytest.mark.asyncio
async def test_save_message_sets_created_at(repo):
    await repo.create_session("agent", session_id="s1")
    msg = await repo.save_message("s1", "user", "hi", "t", "r", "u")
    assert msg.created_at  # non-empty ISO string


# ── Messages: get for session ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_messages_for_session_empty(repo):
    await repo.create_session("agent", session_id="s1")
    msgs = await repo.get_messages_for_session("s1")
    assert msgs == []


@pytest.mark.asyncio
async def test_get_messages_for_session_ordered(repo):
    await repo.create_session("agent", session_id="s1")
    await repo.save_message("s1", "user", "first", "t", "r", "u")
    await repo.save_message("s1", "assistant", "second", "t", "r", "u")
    msgs = await repo.get_messages_for_session("s1")
    assert len(msgs) == 2
    assert msgs[0].content == "first"
    assert msgs[1].content == "second"


@pytest.mark.asyncio
async def test_get_messages_filters_by_session(repo):
    await repo.create_session("agent", session_id="s1")
    await repo.create_session("agent", session_id="s2")
    await repo.save_message("s1", "user", "for s1", "t", "r", "u")
    await repo.save_message("s2", "user", "for s2", "t", "r", "u")
    msgs = await repo.get_messages_for_session("s1")
    assert len(msgs) == 1
    assert msgs[0].content == "for s1"


@pytest.mark.asyncio
async def test_get_messages_nonexistent_session(repo):
    """Querying messages for a session that has no messages returns empty list."""
    msgs = await repo.get_messages_for_session("nonexistent")
    assert msgs == []


# ── Channel bindings: add ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_channel_binding_returns_row(repo):
    await repo.create_session("agent", session_id="s1")
    binding = await repo.add_channel_binding("s1", "slack", "#general")
    assert isinstance(binding, ChannelBindingRow)
    assert binding.session_id == "s1"
    assert binding.channel_type == "slack"
    assert binding.channel_ref == "#general"
    assert binding.id is not None


@pytest.mark.asyncio
async def test_add_channel_binding_duplicate_raises(repo):
    await repo.create_session("agent", session_id="s1")
    await repo.add_channel_binding("s1", "slack", "#general")
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.add_channel_binding("s1", "slack", "#general")


@pytest.mark.asyncio
async def test_add_multiple_bindings_same_session(repo):
    await repo.create_session("agent", session_id="s1")
    await repo.add_channel_binding("s1", "slack", "#general")
    await repo.add_channel_binding("s1", "terminal", "tty1")
    bindings = await repo.get_bindings_for_session("s1")
    assert len(bindings) == 2


# ── Channel bindings: get for session ────────────────────────────────

@pytest.mark.asyncio
async def test_get_bindings_for_session_empty(repo):
    await repo.create_session("agent", session_id="s1")
    bindings = await repo.get_bindings_for_session("s1")
    assert bindings == []


@pytest.mark.asyncio
async def test_get_bindings_for_session_filters(repo):
    await repo.create_session("agent", session_id="s1")
    await repo.create_session("agent", session_id="s2")
    await repo.add_channel_binding("s1", "slack", "#a")
    await repo.add_channel_binding("s2", "slack", "#b")
    bindings = await repo.get_bindings_for_session("s1")
    assert len(bindings) == 1
    assert bindings[0].channel_ref == "#a"


# ── Channel bindings: find session by channel ────────────────────────

@pytest.mark.asyncio
async def test_find_session_by_channel_returns_active(repo):
    await repo.create_session("agent", session_id="s1", state="ACTIVE")
    await repo.add_channel_binding("s1", "slack", "#general")
    found = await repo.find_session_by_channel("slack", "#general")
    assert found is not None
    assert found.id == "s1"


@pytest.mark.asyncio
async def test_find_session_by_channel_returns_waiting(repo):
    await repo.create_session("agent", session_id="s1", state="WAITING_FOR_HUMAN")
    await repo.add_channel_binding("s1", "slack", "#general")
    found = await repo.find_session_by_channel("slack", "#general")
    assert found is not None
    assert found.id == "s1"


@pytest.mark.asyncio
async def test_find_session_by_channel_ignores_done(repo):
    await repo.create_session("agent", session_id="s1", state="ACTIVE")
    await repo.add_channel_binding("s1", "slack", "#general")
    await repo.update_session_state("s1", "DONE")
    found = await repo.find_session_by_channel("slack", "#general")
    assert found is None


@pytest.mark.asyncio
async def test_find_session_by_channel_ignores_error(repo):
    await repo.create_session("agent", session_id="s1", state="ACTIVE")
    await repo.add_channel_binding("s1", "slack", "#general")
    await repo.update_session_state("s1", "ERROR")
    found = await repo.find_session_by_channel("slack", "#general")
    assert found is None


@pytest.mark.asyncio
async def test_find_session_by_channel_returns_most_recent(repo):
    await repo.create_session("agent", session_id="s1", state="ACTIVE")
    await repo.add_channel_binding("s1", "slack", "#general")
    await repo.create_session("agent", session_id="s2", state="ACTIVE")
    await repo.add_channel_binding("s2", "slack", "#general")
    found = await repo.find_session_by_channel("slack", "#general")
    assert found is not None
    assert found.id == "s2"


@pytest.mark.asyncio
async def test_find_session_by_channel_no_match(repo):
    found = await repo.find_session_by_channel("slack", "#nonexistent")
    assert found is None


@pytest.mark.asyncio
async def test_find_session_by_channel_different_type(repo):
    """A binding with a different channel_type does not match."""
    await repo.create_session("agent", session_id="s1", state="ACTIVE")
    await repo.add_channel_binding("s1", "slack", "#general")
    found = await repo.find_session_by_channel("terminal", "#general")
    assert found is None


# ── Row dataclass immutability ───────────────────────────────────────

@pytest.mark.asyncio
async def test_session_row_is_frozen(repo):
    session = await repo.create_session("agent")
    with pytest.raises(AttributeError):
        session.state = "DONE"


@pytest.mark.asyncio
async def test_message_row_is_frozen(repo):
    await repo.create_session("agent", session_id="s1")
    msg = await repo.save_message("s1", "user", "hi", "t", "r", "u")
    with pytest.raises(AttributeError):
        msg.content = "changed"


# ── Channel bindings: remove ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_remove_channel_binding_returns_true(repo):
    await repo.create_session("agent", session_id="s1")
    await repo.add_channel_binding("s1", "terminal", "tty1")
    removed = await repo.remove_channel_binding("s1", "tty1")
    assert removed is True


@pytest.mark.asyncio
async def test_remove_channel_binding_actually_deletes(repo):
    await repo.create_session("agent", session_id="s1")
    await repo.add_channel_binding("s1", "terminal", "tty1")
    await repo.remove_channel_binding("s1", "tty1")
    bindings = await repo.get_bindings_for_session("s1")
    assert bindings == []


@pytest.mark.asyncio
async def test_remove_channel_binding_returns_false_when_not_found(repo):
    await repo.create_session("agent", session_id="s1")
    removed = await repo.remove_channel_binding("s1", "nonexistent")
    assert removed is False


@pytest.mark.asyncio
async def test_remove_channel_binding_leaves_other_bindings(repo):
    await repo.create_session("agent", session_id="s1")
    await repo.add_channel_binding("s1", "terminal", "tty1")
    await repo.add_channel_binding("s1", "slack", "#general")
    await repo.remove_channel_binding("s1", "tty1")
    bindings = await repo.get_bindings_for_session("s1")
    assert len(bindings) == 1
    assert bindings[0].channel_ref == "#general"


@pytest.mark.asyncio
async def test_remove_channel_binding_only_affects_target_session(repo):
    """Removing a binding from one session does not affect another."""
    await repo.create_session("agent", session_id="s1")
    await repo.create_session("agent", session_id="s2")
    await repo.add_channel_binding("s1", "terminal", "tty1")
    await repo.add_channel_binding("s2", "terminal", "tty1")
    await repo.remove_channel_binding("s1", "tty1")
    bindings_s2 = await repo.get_bindings_for_session("s2")
    assert len(bindings_s2) == 1
    assert bindings_s2[0].channel_ref == "tty1"


# ── Row dataclass immutability ───────────────────────────────────────

@pytest.mark.asyncio
async def test_channel_binding_row_is_frozen(repo):
    await repo.create_session("agent", session_id="s1")
    binding = await repo.add_channel_binding("s1", "slack", "#g")
    with pytest.raises(AttributeError):
        binding.channel_ref = "changed"
