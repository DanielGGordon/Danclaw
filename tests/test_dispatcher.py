"""Tests for dispatcher.dispatcher — full message routing pipeline."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from dispatcher.database import _SCHEMA_SQL
from dispatcher.dispatcher import Dispatcher, DispatchResult
from dispatcher.executor import ExecutorResult, MockExecutor
from dispatcher.models import StandardMessage
from config import (
    AgentConfig,
    ChannelPermissions,
    DanClawConfig,
    PermissionsConfig,
    UserPermissions,
)
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from tests.conftest import make_config, make_personas_dir


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


@pytest.fixture
def personas_dir(tmp_path):
    """Create a temporary personas directory with a default.md file."""
    return make_personas_dir(tmp_path)


@pytest_asyncio.fixture
async def dispatcher(mgr, repo, personas_dir):
    """Dispatcher with default MockExecutor (echo mode)."""
    return Dispatcher(
        mgr, repo, MockExecutor(),
        config=make_config("test-agent"),
        personas_dir=personas_dir,
    )


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

    async def execute(
        self,
        message: StandardMessage,
        *,
        persona: str | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> ExecutorResult:
        raise RuntimeError("backend crashed")


@pytest.mark.asyncio
async def test_dispatch_sets_error_state_on_executor_failure(mgr, repo, personas_dir):
    dispatcher = Dispatcher(
        mgr, repo, _FailingExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
    )
    with pytest.raises(RuntimeError, match="backend crashed"):
        await dispatcher.dispatch(_msg())

    # The session should be in ERROR state
    sessions = await mgr.list_active_sessions()
    assert len(sessions) == 0  # ERROR is not active

    all_sessions = await repo.list_sessions()
    assert len(all_sessions) == 1
    assert all_sessions[0].state == "ERROR"


@pytest.mark.asyncio
async def test_dispatch_stores_user_message_before_executor_error(mgr, repo, personas_dir):
    dispatcher = Dispatcher(
        mgr, repo, _FailingExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
    )
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
async def test_dispatch_with_fixed_response_executor(mgr, repo, personas_dir):
    executor = MockExecutor(fixed_response="I am a bot.")
    dispatcher = Dispatcher(
        mgr, repo, executor,
        config=make_config("bot"), personas_dir=personas_dir,
    )
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
async def test_full_round_trip_with_followup(mgr, repo, personas_dir):
    """Send a message, get a response, send a follow-up, verify session
    continuity and all messages are persisted in order."""
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor,
        config=make_config("agent"), personas_dir=personas_dir,
    )

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
async def test_default_agent_selected_from_config(mgr, repo, personas_dir):
    """Dispatcher selects the default (first) agent from config."""
    config = DanClawConfig(
        agents=[
            AgentConfig(name="primary", persona="default", backend_preference=["claude"]),
            AgentConfig(name="secondary", persona="default", backend_preference=["codex"]),
        ],
    )
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(), config=config, personas_dir=personas_dir,
    )
    result = await dispatcher.dispatch(_msg(content="hello"))
    assert result.agent_name == "primary"


@pytest.mark.asyncio
async def test_agent_name_stored_in_session(mgr, repo, personas_dir):
    """The resolved agent name is stored in the session row."""
    config = make_config("my-agent")
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(), config=config, personas_dir=personas_dir,
    )
    result = await dispatcher.dispatch(_msg(content="hello"))

    session = await mgr.get_session(result.session_id)
    assert session is not None
    assert session.agent_name == "my-agent"


@pytest.mark.asyncio
async def test_agent_name_in_dispatch_result(mgr, repo, personas_dir):
    """DispatchResult includes the agent name that handled the message."""
    config = make_config("resolver-agent")
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(), config=config, personas_dir=personas_dir,
    )
    result = await dispatcher.dispatch(_msg(content="hello"))
    assert result.agent_name == "resolver-agent"


@pytest.mark.asyncio
async def test_agent_name_consistent_across_session(mgr, repo, personas_dir):
    """Multiple dispatches on the same session report the same agent name."""
    config = make_config("consistent-agent")
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(), config=config, personas_dir=personas_dir,
    )

    r1 = await dispatcher.dispatch(_msg(content="first"))
    r2 = await dispatcher.dispatch(_msg(content="second"))

    assert r1.agent_name == "consistent-agent"
    assert r2.agent_name == "consistent-agent"
    assert r1.session_id == r2.session_id

    session = await mgr.get_session(r1.session_id)
    assert session.agent_name == "consistent-agent"


# ── Persona injection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persona_loaded_from_correct_file(mgr, repo, tmp_path):
    """Dispatcher loads persona content from the file matching the agent's
    persona name in the personas directory."""
    personas_dir = make_personas_dir(tmp_path / "custom", {
        "default": "You are the default.",
        "expert": "You are an expert assistant.",
    })
    config = make_config("agent", persona="expert")
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    await dispatcher.dispatch(_msg(content="hello"))
    assert executor.last_persona == "You are an expert assistant."


@pytest.mark.asyncio
async def test_persona_content_passed_to_executor(mgr, repo, tmp_path):
    """The executor receives the full persona markdown content."""
    persona_text = "You are a helpful bot.\n\nBe concise."
    personas_dir = make_personas_dir(tmp_path / "p", {"default": persona_text})
    config = make_config("agent")
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    await dispatcher.dispatch(_msg(content="hi"))
    assert executor.last_persona == persona_text


@pytest.mark.asyncio
async def test_executor_receives_persona(mgr, repo, personas_dir):
    """MockExecutor stores the persona it receives via last_persona."""
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor,
        config=make_config("agent"), personas_dir=personas_dir,
    )
    await dispatcher.dispatch(_msg(content="test"))
    assert executor.last_persona is not None
    assert isinstance(executor.last_persona, str)
    assert len(executor.last_persona) > 0


@pytest.mark.asyncio
async def test_persona_none_when_file_missing(mgr, repo, tmp_path):
    """When the persona file does not exist, executor receives None."""
    empty_dir = tmp_path / "empty_personas"
    empty_dir.mkdir()
    config = make_config("agent", persona="nonexistent")
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=empty_dir,
    )
    await dispatcher.dispatch(_msg(content="hello"))
    assert executor.last_persona is None


@pytest.mark.asyncio
async def test_persona_persists_across_dispatches(mgr, repo, tmp_path):
    """Persona is loaded and passed on every dispatch call."""
    persona_text = "Persistent persona."
    personas_dir = make_personas_dir(tmp_path / "pp", {"default": persona_text})
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor,
        config=make_config("agent"), personas_dir=personas_dir,
    )
    await dispatcher.dispatch(_msg(content="first"))
    assert executor.last_persona == persona_text
    await dispatcher.dispatch(_msg(content="second"))
    assert executor.last_persona == persona_text


# ── Persona switching ─────────────────────────────────────────────────


def _multi_agent_config() -> DanClawConfig:
    """Build a config with two agents for switch tests."""
    return DanClawConfig(
        agents=[
            AgentConfig(
                name="alpha",
                persona="alpha_persona",
                backend_preference=["claude"],
            ),
            AgentConfig(
                name="beta",
                persona="beta_persona",
                backend_preference=["codex"],
            ),
        ],
    )


def _multi_agent_personas_dir(tmp_path: Path) -> Path:
    """Create a personas dir with files for both agents."""
    return make_personas_dir(tmp_path, {
        "default": "You are the default.",
        "alpha_persona": "You are alpha.",
        "beta_persona": "You are beta.",
    })


@pytest.mark.asyncio
async def test_switch_command_slash(mgr, repo, tmp_path):
    """'/switch beta' switches the session agent to beta."""
    personas_dir = _multi_agent_personas_dir(tmp_path)
    config = _multi_agent_config()
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(), config=config, personas_dir=personas_dir,
    )
    # Start a session
    r1 = await dispatcher.dispatch(_msg(content="hello"))
    assert r1.agent_name == "alpha"
    session_id = r1.session_id

    # Switch persona
    r2 = await dispatcher.dispatch(_msg(content="/switch beta"))
    assert r2.session_id == session_id
    assert r2.agent_name == "beta"
    assert "Switched to agent 'beta'" in r2.response
    assert r2.backend == "system"

    # Verify DB
    session = await mgr.get_session(session_id)
    assert session.agent_name == "beta"


@pytest.mark.asyncio
async def test_switch_command_natural_language(mgr, repo, tmp_path):
    """'switch to beta' switches the session agent to beta."""
    personas_dir = _multi_agent_personas_dir(tmp_path)
    config = _multi_agent_config()
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(), config=config, personas_dir=personas_dir,
    )
    r1 = await dispatcher.dispatch(_msg(content="hello"))
    r2 = await dispatcher.dispatch(_msg(content="switch to beta"))
    assert r2.session_id == r1.session_id
    assert r2.agent_name == "beta"


@pytest.mark.asyncio
async def test_switch_to_invalid_agent_returns_error(mgr, repo, tmp_path):
    """Switching to a nonexistent agent returns an error without changing the session."""
    personas_dir = _multi_agent_personas_dir(tmp_path)
    config = _multi_agent_config()
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(), config=config, personas_dir=personas_dir,
    )
    r1 = await dispatcher.dispatch(_msg(content="hello"))
    r2 = await dispatcher.dispatch(_msg(content="/switch nonexistent"))

    assert r2.session_id == r1.session_id
    assert "Unknown agent 'nonexistent'" in r2.response
    assert r2.backend == "system"
    # Agent should remain unchanged
    assert r2.agent_name == "alpha"

    session = await mgr.get_session(r1.session_id)
    assert session.agent_name == "alpha"


@pytest.mark.asyncio
async def test_subsequent_messages_use_new_persona_after_switch(mgr, repo, tmp_path):
    """After switching, the next message uses the new agent's persona."""
    personas_dir = _multi_agent_personas_dir(tmp_path)
    config = _multi_agent_config()
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    # Send initial message (uses alpha)
    await dispatcher.dispatch(_msg(content="hello"))
    assert executor.last_persona == "You are alpha."

    # Switch to beta
    await dispatcher.dispatch(_msg(content="/switch beta"))

    # Next message should use beta's persona
    r3 = await dispatcher.dispatch(_msg(content="after switch"))
    assert r3.agent_name == "beta"
    assert executor.last_persona == "You are beta."


@pytest.mark.asyncio
async def test_switch_stores_messages_in_session(mgr, repo, tmp_path):
    """The switch command and confirmation are stored as messages."""
    personas_dir = _multi_agent_personas_dir(tmp_path)
    config = _multi_agent_config()
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(), config=config, personas_dir=personas_dir,
    )
    r1 = await dispatcher.dispatch(_msg(content="hello"))
    await dispatcher.dispatch(_msg(content="/switch beta"))

    messages = await repo.get_messages_for_session(r1.session_id)
    # hello(user), response(assistant), /switch(user), confirmation(assistant)
    assert len(messages) == 4
    assert messages[2].role == "user"
    assert messages[2].content == "/switch beta"
    assert messages[3].role == "assistant"
    assert "Switched to agent 'beta'" in messages[3].content


@pytest.mark.asyncio
async def test_switch_invalid_stores_error_message(mgr, repo, tmp_path):
    """When switching to an invalid agent, the error is stored as a message."""
    personas_dir = _multi_agent_personas_dir(tmp_path)
    config = _multi_agent_config()
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(), config=config, personas_dir=personas_dir,
    )
    r1 = await dispatcher.dispatch(_msg(content="hello"))
    await dispatcher.dispatch(_msg(content="/switch nope"))

    messages = await repo.get_messages_for_session(r1.session_id)
    assert len(messages) == 4
    assert messages[3].role == "assistant"
    assert "Unknown agent 'nope'" in messages[3].content


@pytest.mark.asyncio
async def test_switch_case_insensitive_prefix(mgr, repo, tmp_path):
    """Switch command detection is case-insensitive for the prefix."""
    personas_dir = _multi_agent_personas_dir(tmp_path)
    config = _multi_agent_config()
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(), config=config, personas_dir=personas_dir,
    )
    await dispatcher.dispatch(_msg(content="hello"))
    r2 = await dispatcher.dispatch(_msg(content="Switch To beta"))
    assert r2.agent_name == "beta"


@pytest.mark.asyncio
async def test_switch_then_switch_back(mgr, repo, tmp_path):
    """Switching to beta and then back to alpha works correctly."""
    personas_dir = _multi_agent_personas_dir(tmp_path)
    config = _multi_agent_config()
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    await dispatcher.dispatch(_msg(content="hello"))
    assert executor.last_persona == "You are alpha."

    await dispatcher.dispatch(_msg(content="/switch beta"))
    await dispatcher.dispatch(_msg(content="as beta"))
    assert executor.last_persona == "You are beta."

    await dispatcher.dispatch(_msg(content="/switch alpha"))
    await dispatcher.dispatch(_msg(content="as alpha again"))
    assert executor.last_persona == "You are alpha."

    session = await mgr.get_session(
        (await dispatcher.dispatch(_msg(content="check"))).session_id
    )
    assert session.agent_name == "alpha"


# ── Permission resolution in dispatch ─────────────────────────────────


def _config_with_permissions(
    agent_name: str = "test-agent",
    permissions: PermissionsConfig | None = None,
) -> DanClawConfig:
    """Build a config with a single agent and custom permissions."""
    return DanClawConfig(
        agents=[
            AgentConfig(
                name=agent_name,
                persona="default",
                backend_preference=["claude"],
                allowed_tools=["tool_a", "tool_b"],
            ),
        ],
        permissions=permissions or PermissionsConfig(),
    )


@pytest.mark.asyncio
async def test_dispatch_resolves_permissions_for_channel_and_user(mgr, repo, personas_dir):
    """Dispatcher resolves the effective permissions and stores them."""
    perms = PermissionsConfig(
        channels={"terminal": ChannelPermissions(allowed_tools=["read_file", "write_file"])},
        users={"u1": UserPermissions(additional_tools=["deploy"])},
    )
    config = _config_with_permissions(permissions=perms)
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    await dispatcher.dispatch(_msg(source="terminal", user_id="u1"))
    assert dispatcher._last_resolved_permissions == frozenset(
        {"read_file", "write_file", "deploy"}
    )


@pytest.mark.asyncio
async def test_dispatch_passes_allowed_tools_to_executor(mgr, repo, personas_dir):
    """The resolved permissions are passed to the executor as allowed_tools."""
    perms = PermissionsConfig(
        channels={"terminal": ChannelPermissions(allowed_tools=["read_file"])},
    )
    config = _config_with_permissions(permissions=perms)
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    await dispatcher.dispatch(_msg(source="terminal", user_id="u1"))
    assert executor.last_allowed_tools == frozenset({"read_file"})


@pytest.mark.asyncio
async def test_dispatch_empty_permissions_passes_empty_set(mgr, repo, personas_dir):
    """When no permissions are configured, executor gets an empty frozenset."""
    config = _config_with_permissions()
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    await dispatcher.dispatch(_msg(source="unknown_channel", user_id="unknown_user"))
    assert executor.last_allowed_tools == frozenset()


@pytest.mark.asyncio
async def test_dispatch_override_channel_ignores_user_tools(mgr, repo, personas_dir):
    """When the channel has override=True, user tools are not included."""
    perms = PermissionsConfig(
        channels={"locked": ChannelPermissions(
            allowed_tools=["safe_tool"],
            override=True,
        )},
        users={"u1": UserPermissions(additional_tools=["dangerous_tool"])},
    )
    config = _config_with_permissions(permissions=perms)
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    await dispatcher.dispatch(_msg(source="locked", user_id="u1"))
    assert executor.last_allowed_tools == frozenset({"safe_tool"})
    assert "dangerous_tool" not in executor.last_allowed_tools


# ── Approval gate ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_approval_required_sets_waiting_state(mgr, repo, personas_dir):
    """When approval is required, session is set to WAITING_FOR_HUMAN."""
    perms = PermissionsConfig(
        channels={"slack": ChannelPermissions(
            allowed_tools=["deploy"],
            approval_required=True,
        )},
    )
    config = _config_with_permissions(permissions=perms)
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    result = await dispatcher.dispatch(_msg(source="slack", user_id="u1"))

    # Session should be WAITING_FOR_HUMAN
    session = await mgr.get_session(result.session_id)
    assert session.state == "WAITING_FOR_HUMAN"


@pytest.mark.asyncio
async def test_dispatch_approval_required_returns_approval_message(mgr, repo, personas_dir):
    """When approval is required, the response asks for human approval."""
    perms = PermissionsConfig(
        channels={"slack": ChannelPermissions(
            allowed_tools=["deploy"],
            approval_required=True,
        )},
    )
    config = _config_with_permissions(permissions=perms)
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    result = await dispatcher.dispatch(_msg(source="slack", user_id="u1"))

    assert "approval" in result.response.lower()
    assert result.backend == "system"


@pytest.mark.asyncio
async def test_dispatch_approval_required_does_not_call_executor(mgr, repo, personas_dir):
    """When approval is required, the executor should NOT be called."""
    perms = PermissionsConfig(
        channels={"slack": ChannelPermissions(
            allowed_tools=["deploy"],
            approval_required=True,
        )},
    )
    config = _config_with_permissions(permissions=perms)
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    await dispatcher.dispatch(_msg(source="slack", user_id="u1"))

    # MockExecutor's last_persona is set only when execute() is called
    assert executor.last_persona is None
    assert executor.last_allowed_tools is None


@pytest.mark.asyncio
async def test_dispatch_approval_stores_user_message_and_approval_response(
    mgr, repo, personas_dir,
):
    """When approval is required, both user message and approval response are stored."""
    perms = PermissionsConfig(
        channels={"slack": ChannelPermissions(
            allowed_tools=["deploy"],
            approval_required=True,
        )},
    )
    config = _config_with_permissions(permissions=perms)
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(), config=config, personas_dir=personas_dir,
    )
    result = await dispatcher.dispatch(_msg(source="slack", user_id="u1", content="do deploy"))

    messages = await repo.get_messages_for_session(result.session_id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "do deploy"
    assert messages[1].role == "assistant"
    assert "approval" in messages[1].content.lower()


@pytest.mark.asyncio
async def test_dispatch_user_approval_required(mgr, repo, personas_dir):
    """When user-level approval_required is True, session waits for approval."""
    perms = PermissionsConfig(
        channels={"terminal": ChannelPermissions(allowed_tools=["read_file"])},
        users={"risky_user": UserPermissions(
            additional_tools=["deploy"],
            approval_required=True,
        )},
    )
    config = _config_with_permissions(permissions=perms)
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    result = await dispatcher.dispatch(_msg(source="terminal", user_id="risky_user"))

    session = await mgr.get_session(result.session_id)
    assert session.state == "WAITING_FOR_HUMAN"
    assert result.backend == "system"
    assert executor.last_persona is None  # executor was not called


@pytest.mark.asyncio
async def test_dispatch_no_approval_required_proceeds_normally(mgr, repo, personas_dir):
    """When no approval is required, dispatch proceeds to executor normally."""
    perms = PermissionsConfig(
        channels={"terminal": ChannelPermissions(allowed_tools=["read_file"])},
    )
    config = _config_with_permissions(permissions=perms)
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )
    result = await dispatcher.dispatch(_msg(source="terminal", user_id="u1", content="hello"))

    assert result.response == "mock response: hello"
    assert result.backend == "mock"
    session = await mgr.get_session(result.session_id)
    assert session.state == "ACTIVE"


@pytest.mark.asyncio
async def test_dispatch_permissions_resolved_on_each_call(mgr, repo, personas_dir):
    """Permissions are resolved fresh on each dispatch call."""
    perms = PermissionsConfig(
        channels={
            "ch1": ChannelPermissions(allowed_tools=["tool_x"]),
            "ch2": ChannelPermissions(allowed_tools=["tool_y"]),
        },
    )
    config = _config_with_permissions(permissions=perms)
    executor = MockExecutor()
    dispatcher = Dispatcher(
        mgr, repo, executor, config=config, personas_dir=personas_dir,
    )

    await dispatcher.dispatch(_msg(source="ch1", user_id="u1", channel_ref="ref1"))
    assert dispatcher._last_resolved_permissions == frozenset({"tool_x"})

    await dispatcher.dispatch(_msg(source="ch2", user_id="u1", channel_ref="ref2"))
    assert dispatcher._last_resolved_permissions == frozenset({"tool_y"})
