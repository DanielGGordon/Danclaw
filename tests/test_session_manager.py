"""Tests for dispatcher.session_manager — high-level session lifecycle."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from dispatcher.database import _SCHEMA_SQL
from dispatcher.models import StandardMessage
from dispatcher.repository import Repository, SessionRow
from dispatcher.session_manager import SessionManager


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


@pytest_asyncio.fixture
async def mgr(repo):
    """Yield a SessionManager backed by the in-memory repository."""
    return SessionManager(repo)


def _msg(
    source: str = "terminal",
    channel_ref: str = "tty1",
    user_id: str = "u1",
    content: str = "hello",
    session_id: str | None = None,
) -> StandardMessage:
    return StandardMessage(
        source=source,
        channel_ref=channel_ref,
        user_id=user_id,
        content=content,
        session_id=session_id,
    )


# ── get_or_create_session: creation ──────────────────────────────────

@pytest.mark.asyncio
async def test_creates_new_session_when_none_exists(mgr):
    session = await mgr.get_or_create_session(_msg(), "test-agent")
    assert isinstance(session, SessionRow)
    assert session.agent_name == "test-agent"
    assert session.state == "ACTIVE"


@pytest.mark.asyncio
async def test_creates_channel_binding_for_new_session(mgr, repo):
    session = await mgr.get_or_create_session(_msg(), "agent")
    bindings = await repo.get_bindings_for_session(session.id)
    assert len(bindings) == 1
    assert bindings[0].channel_type == "terminal"
    assert bindings[0].channel_ref == "tty1"


# ── get_or_create_session: reuse existing ────────────────────────────

@pytest.mark.asyncio
async def test_reuses_active_session_on_same_channel(mgr):
    s1 = await mgr.get_or_create_session(_msg(), "agent")
    s2 = await mgr.get_or_create_session(_msg(), "agent")
    assert s1.id == s2.id


@pytest.mark.asyncio
async def test_reuses_waiting_session_on_same_channel(mgr):
    s1 = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s1.id, "WAITING_FOR_HUMAN")
    s2 = await mgr.get_or_create_session(_msg(), "agent")
    assert s2.id == s1.id


@pytest.mark.asyncio
async def test_creates_new_session_when_existing_is_done(mgr):
    s1 = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s1.id, "DONE")
    s2 = await mgr.get_or_create_session(_msg(), "agent")
    assert s2.id != s1.id
    assert s2.state == "ACTIVE"


@pytest.mark.asyncio
async def test_creates_new_session_when_existing_is_error(mgr):
    s1 = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s1.id, "ERROR")
    s2 = await mgr.get_or_create_session(_msg(), "agent")
    assert s2.id != s1.id


# ── get_or_create_session: explicit session_id ───────────────────────

@pytest.mark.asyncio
async def test_returns_session_by_explicit_id(mgr):
    s1 = await mgr.get_or_create_session(_msg(), "agent")
    msg = _msg(channel_ref="other-tty", session_id=s1.id)
    s2 = await mgr.get_or_create_session(msg, "agent")
    assert s2.id == s1.id


@pytest.mark.asyncio
async def test_ignores_explicit_id_if_session_done(mgr):
    s1 = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s1.id, "DONE")
    msg = _msg(session_id=s1.id)
    s2 = await mgr.get_or_create_session(msg, "agent")
    # The DONE session should not be returned; a new one is created.
    assert s2.id != s1.id


@pytest.mark.asyncio
async def test_ignores_explicit_id_if_nonexistent(mgr):
    msg = _msg(session_id="nonexistent-id")
    session = await mgr.get_or_create_session(msg, "agent")
    assert session.state == "ACTIVE"
    assert session.id != "nonexistent-id"


# ── get_or_create_session: different channels ────────────────────────

@pytest.mark.asyncio
async def test_different_channels_get_different_sessions(mgr):
    s1 = await mgr.get_or_create_session(_msg(channel_ref="tty1"), "agent")
    s2 = await mgr.get_or_create_session(_msg(channel_ref="tty2"), "agent")
    assert s1.id != s2.id


@pytest.mark.asyncio
async def test_different_sources_get_different_sessions(mgr):
    s1 = await mgr.get_or_create_session(_msg(source="terminal"), "agent")
    s2 = await mgr.get_or_create_session(_msg(source="slack"), "agent")
    assert s1.id != s2.id


# ── get_session ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_session_returns_existing(mgr):
    created = await mgr.get_or_create_session(_msg(), "agent")
    fetched = await mgr.get_session(created.id)
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_session_returns_none_for_missing(mgr):
    assert await mgr.get_session("nonexistent") is None


# ── add_binding ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_binding_to_existing_session(mgr, repo):
    session = await mgr.get_or_create_session(_msg(), "agent")
    binding = await mgr.add_binding(session.id, "slack", "#general")
    assert binding.session_id == session.id
    assert binding.channel_type == "slack"
    assert binding.channel_ref == "#general"


@pytest.mark.asyncio
async def test_add_multiple_bindings_to_session(mgr):
    session = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.add_binding(session.id, "slack", "#general")
    await mgr.add_binding(session.id, "slack", "#random")
    bindings = await mgr.get_bindings(session.id)
    # 3 total: 1 from creation (terminal/tty1) + 2 added
    assert len(bindings) == 3
    channel_refs = {b.channel_ref for b in bindings}
    assert channel_refs == {"tty1", "#general", "#random"}


@pytest.mark.asyncio
async def test_add_binding_nonexistent_session_raises(mgr):
    with pytest.raises(KeyError, match="not found"):
        await mgr.add_binding("nonexistent", "slack", "#general")


@pytest.mark.asyncio
async def test_add_duplicate_binding_raises(mgr):
    session = await mgr.get_or_create_session(_msg(), "agent")
    import aiosqlite
    with pytest.raises(aiosqlite.IntegrityError):
        await mgr.add_binding(session.id, "terminal", "tty1")


# ── get_bindings ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_bindings_returns_all_for_session(mgr):
    session = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.add_binding(session.id, "slack", "#general")
    bindings = await mgr.get_bindings(session.id)
    assert len(bindings) == 2


@pytest.mark.asyncio
async def test_get_bindings_empty_for_no_bindings(mgr, repo):
    # Create a session directly via repo (no automatic binding)
    session = await repo.create_session("agent", session_id="bare")
    bindings = await mgr.get_bindings("bare")
    assert bindings == []


@pytest.mark.asyncio
async def test_lookup_session_by_added_binding(mgr, repo):
    """A session can be found via a binding added after creation."""
    session = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.add_binding(session.id, "slack", "#general")
    found = await repo.find_session_by_channel("slack", "#general")
    assert found is not None
    assert found.id == session.id


# ── update_state: valid transitions ──────────────────────────────────

@pytest.mark.asyncio
async def test_active_to_waiting(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    updated = await mgr.update_state(s.id, "WAITING_FOR_HUMAN")
    assert updated.state == "WAITING_FOR_HUMAN"


@pytest.mark.asyncio
async def test_active_to_done(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    updated = await mgr.update_state(s.id, "DONE")
    assert updated.state == "DONE"


@pytest.mark.asyncio
async def test_active_to_error(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    updated = await mgr.update_state(s.id, "ERROR")
    assert updated.state == "ERROR"


@pytest.mark.asyncio
async def test_waiting_to_active(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s.id, "WAITING_FOR_HUMAN")
    updated = await mgr.update_state(s.id, "ACTIVE")
    assert updated.state == "ACTIVE"


@pytest.mark.asyncio
async def test_waiting_to_done(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s.id, "WAITING_FOR_HUMAN")
    updated = await mgr.update_state(s.id, "DONE")
    assert updated.state == "DONE"


@pytest.mark.asyncio
async def test_error_to_active(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s.id, "ERROR")
    updated = await mgr.update_state(s.id, "ACTIVE")
    assert updated.state == "ACTIVE"


# ── update_state: invalid transitions ────────────────────────────────

@pytest.mark.asyncio
async def test_done_to_active_raises(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s.id, "DONE")
    with pytest.raises(ValueError, match="Cannot transition"):
        await mgr.update_state(s.id, "ACTIVE")


@pytest.mark.asyncio
async def test_done_to_waiting_raises(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s.id, "DONE")
    with pytest.raises(ValueError, match="Cannot transition"):
        await mgr.update_state(s.id, "WAITING_FOR_HUMAN")


@pytest.mark.asyncio
async def test_error_to_done_raises(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s.id, "ERROR")
    with pytest.raises(ValueError, match="Cannot transition"):
        await mgr.update_state(s.id, "DONE")


@pytest.mark.asyncio
async def test_error_to_waiting_raises(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s.id, "ERROR")
    with pytest.raises(ValueError, match="Cannot transition"):
        await mgr.update_state(s.id, "WAITING_FOR_HUMAN")


# ── update_state: edge cases ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_state_invalid_state_raises(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    with pytest.raises(ValueError, match="Invalid session state"):
        await mgr.update_state(s.id, "BOGUS")


@pytest.mark.asyncio
async def test_update_state_missing_session_raises(mgr):
    with pytest.raises(KeyError, match="not found"):
        await mgr.update_state("nonexistent", "DONE")


@pytest.mark.asyncio
async def test_update_state_noop_same_state(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    updated = await mgr.update_state(s.id, "ACTIVE")
    assert updated.state == "ACTIVE"
    assert updated.id == s.id


# ── list_active_sessions ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_active_sessions_empty(mgr):
    result = await mgr.list_active_sessions()
    assert result == []


@pytest.mark.asyncio
async def test_list_active_sessions_includes_active(mgr):
    await mgr.get_or_create_session(_msg(channel_ref="a"), "agent")
    result = await mgr.list_active_sessions()
    assert len(result) == 1
    assert result[0].state == "ACTIVE"


@pytest.mark.asyncio
async def test_list_active_sessions_includes_waiting(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s.id, "WAITING_FOR_HUMAN")
    result = await mgr.list_active_sessions()
    assert len(result) == 1
    assert result[0].state == "WAITING_FOR_HUMAN"


@pytest.mark.asyncio
async def test_list_active_sessions_excludes_done(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s.id, "DONE")
    result = await mgr.list_active_sessions()
    assert result == []


@pytest.mark.asyncio
async def test_list_active_sessions_excludes_error(mgr):
    s = await mgr.get_or_create_session(_msg(), "agent")
    await mgr.update_state(s.id, "ERROR")
    result = await mgr.list_active_sessions()
    assert result == []


@pytest.mark.asyncio
async def test_list_active_sessions_mixed(mgr):
    s1 = await mgr.get_or_create_session(_msg(channel_ref="a"), "agent")
    s2 = await mgr.get_or_create_session(_msg(channel_ref="b"), "agent")
    s3 = await mgr.get_or_create_session(_msg(channel_ref="c"), "agent")
    await mgr.update_state(s2.id, "DONE")
    await mgr.update_state(s3.id, "WAITING_FOR_HUMAN")
    result = await mgr.list_active_sessions()
    ids = {s.id for s in result}
    assert ids == {s1.id, s3.id}
