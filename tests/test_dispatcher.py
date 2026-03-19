"""Tests for dispatcher.dispatcher — full message routing pipeline."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from dispatcher.database import _SCHEMA_SQL
from dispatcher.dispatcher import Dispatcher, DispatchResult
from dispatcher.executor import ExecutorResult, MockExecutor
from dispatcher.models import StandardMessage
from config import AgentConfig, DanClawConfig
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from tests.conftest import make_config


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
    return Repository(db)


@pytest_asyncio.fixture
async def mgr(repo):
    return SessionManager(repo)


@pytest_asyncio.fixture
async def dispatcher(mgr, repo):
    """Dispatcher with default MockExecutor (echo mode)."""
    return Dispatcher(mgr, repo, MockExecutor(), config=make_config("test-agent"))


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


# ── Basic dispatch ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_returns_dispatch_result(dispatcher):
    result = await dispatcher.dispatch(_msg())
    assert isinstance(result, DispatchResult)
    assert result.response == "mock response: hello"
    assert result.backend == "mock"
    assert result.session_id  # non-empty string


@pytest.mark.asyncio
async def test_dispatch_stores_user_message(dispatcher, repo):
    result = await dispatcher.dispatch(_msg(content="ping"))
    messages = await repo.get_messages_for_session(result.session_id)
    user_msgs = [m for m in messages if m.role == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0].content == "ping"
    assert user_msgs[0].source == "terminal"
    assert user_msgs[0].user_id == "u1"


@pytest.mark.asyncio
async def test_dispatch_stores_assistant_response(dispatcher, repo):
    result = await dispatcher.dispatch(_msg(content="ping"))
    messages = await repo.get_messages_for_session(result.session_id)
    assistant_msgs = [m for m in messages if m.role == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0].content == "mock response: ping"
    assert assistant_msgs[0].user_id == "system"


@pytest.mark.asyncio
async def test_dispatch_creates_session(dispatcher, mgr):
    result = await dispatcher.dispatch(_msg())
    session = await mgr.get_session(result.session_id)
    assert session is not None
    assert session.agent_name == "test-agent"
    assert session.state == "ACTIVE"


# ── Session continuity ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_reuses_session_on_same_channel(dispatcher):
    r1 = await dispatcher.dispatch(_msg(content="first"))
    r2 = await dispatcher.dispatch(_msg(content="second"))
    assert r1.session_id == r2.session_id


@pytest.mark.asyncio
async def test_dispatch_accumulates_messages(dispatcher, repo):
    r1 = await dispatcher.dispatch(_msg(content="first"))
    await dispatcher.dispatch(_msg(content="second"))
    messages = await repo.get_messages_for_session(r1.session_id)
    # 2 user + 2 assistant = 4 total
    assert len(messages) == 4
    roles = [m.role for m in messages]
    assert roles == ["user", "assistant", "user", "assistant"]


@pytest.mark.asyncio
async def test_dispatch_different_channels_different_sessions(dispatcher):
    r1 = await dispatcher.dispatch(_msg(channel_ref="tty1"))
    r2 = await dispatcher.dispatch(_msg(channel_ref="tty2"))
    assert r1.session_id != r2.session_id


# ── Explicit session_id ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_with_explicit_session_id(dispatcher):
    r1 = await dispatcher.dispatch(_msg())
    # Send a message on a different channel but referencing the same session
    r2 = await dispatcher.dispatch(
        _msg(channel_ref="other", session_id=r1.session_id)
    )
    assert r2.session_id == r1.session_id


# ── Executor error handling ──────────────────────────────────────────

class _FailingExecutor:
    """Executor that always raises."""

    async def execute(self, message: StandardMessage) -> ExecutorResult:
        raise RuntimeError("backend crashed")


@pytest.mark.asyncio
async def test_dispatch_sets_error_state_on_executor_failure(mgr, repo):
    dispatcher = Dispatcher(mgr, repo, _FailingExecutor(), config=make_config("agent"))
    with pytest.raises(RuntimeError, match="backend crashed"):
        await dispatcher.dispatch(_msg())

    # The session should be in ERROR state
    sessions = await mgr.list_active_sessions()
    assert len(sessions) == 0  # ERROR is not active

    all_sessions = await repo.list_sessions()
    assert len(all_sessions) == 1
    assert all_sessions[0].state == "ERROR"


@pytest.mark.asyncio
async def test_dispatch_stores_user_message_before_executor_error(mgr, repo):
    dispatcher = Dispatcher(mgr, repo, _FailingExecutor(), config=make_config("agent"))
    with pytest.raises(RuntimeError):
        await dispatcher.dispatch(_msg(content="should persist"))

    all_sessions = await repo.list_sessions()
    messages = await repo.get_messages_for_session(all_sessions[0].id)
    # User message should still be stored even though executor failed
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].content == "should persist"


# ── Fixed response executor ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_with_fixed_response_executor(mgr, repo):
    executor = MockExecutor(fixed_response="I am a bot.")
    dispatcher = Dispatcher(mgr, repo, executor, config=make_config("bot"))
    result = await dispatcher.dispatch(_msg(content="anything"))
    assert result.response == "I am a bot."
    assert result.backend == "mock"


# ── DispatchResult frozen ────────────────────────────────────────────

def test_dispatch_result_is_frozen():
    dr = DispatchResult(session_id="s1", response="hi", backend="mock", agent_name="test")
    with pytest.raises(AttributeError):
        dr.response = "changed"  # type: ignore[misc]


# ── Integration: full round-trip with follow-up ──────────────────────

@pytest.mark.asyncio
async def test_full_round_trip_with_followup(mgr, repo):
    """Send a message, get a response, send a follow-up, verify session
    continuity and all messages are persisted in order."""
    executor = MockExecutor()
    dispatcher = Dispatcher(mgr, repo, executor, config=make_config("agent"))

    r1 = await dispatcher.dispatch(_msg(content="hello"))
    assert r1.response == "mock response: hello"

    r2 = await dispatcher.dispatch(_msg(content="follow-up"))
    assert r2.session_id == r1.session_id
    assert r2.response == "mock response: follow-up"

    messages = await repo.get_messages_for_session(r1.session_id)
    assert len(messages) == 4
    assert messages[0].content == "hello"
    assert messages[0].role == "user"
    assert messages[1].content == "mock response: hello"
    assert messages[1].role == "assistant"
    assert messages[2].content == "follow-up"
    assert messages[2].role == "user"
    assert messages[3].content == "mock response: follow-up"
    assert messages[3].role == "assistant"

    # Session remains active
    session = await mgr.get_session(r1.session_id)
    assert session is not None
    assert session.state == "ACTIVE"


# ── Config-driven agent resolution ──────────────────────────────────

@pytest.mark.asyncio
async def test_default_agent_selected_from_config(mgr, repo):
    """Dispatcher selects the default (first) agent from config."""
    config = DanClawConfig(
        agents=[
            AgentConfig(name="primary", persona="default", backend_preference=["claude"]),
            AgentConfig(name="secondary", persona="default", backend_preference=["codex"]),
        ],
    )
    dispatcher = Dispatcher(mgr, repo, MockExecutor(), config=config)
    result = await dispatcher.dispatch(_msg(content="hello"))
    assert result.agent_name == "primary"


@pytest.mark.asyncio
async def test_agent_name_stored_in_session(mgr, repo):
    """The resolved agent name is stored in the session row."""
    config = make_config("my-agent")
    dispatcher = Dispatcher(mgr, repo, MockExecutor(), config=config)
    result = await dispatcher.dispatch(_msg(content="hello"))

    session = await mgr.get_session(result.session_id)
    assert session is not None
    assert session.agent_name == "my-agent"


@pytest.mark.asyncio
async def test_agent_name_in_dispatch_result(mgr, repo):
    """DispatchResult includes the agent name that handled the message."""
    config = make_config("resolver-agent")
    dispatcher = Dispatcher(mgr, repo, MockExecutor(), config=config)
    result = await dispatcher.dispatch(_msg(content="hello"))
    assert result.agent_name == "resolver-agent"


@pytest.mark.asyncio
async def test_agent_name_consistent_across_session(mgr, repo):
    """Multiple dispatches on the same session report the same agent name."""
    config = make_config("consistent-agent")
    dispatcher = Dispatcher(mgr, repo, MockExecutor(), config=config)

    r1 = await dispatcher.dispatch(_msg(content="first"))
    r2 = await dispatcher.dispatch(_msg(content="second"))

    assert r1.agent_name == "consistent-agent"
    assert r2.agent_name == "consistent-agent"
    assert r1.session_id == r2.session_id

    session = await mgr.get_session(r1.session_id)
    assert session.agent_name == "consistent-agent"
