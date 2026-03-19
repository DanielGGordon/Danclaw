"""Tests for telemetry events emitted by the Dispatcher."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from config import (
    ChannelPermissions,
    DanClawConfig,
    PermissionsConfig,
    UserPermissions,
)
from dispatcher.database import _SCHEMA_SQL
from dispatcher.dispatcher import Dispatcher, DispatchResult
from dispatcher.executor import ExecutorResult, MockExecutor
from dispatcher.models import StandardMessage
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from dispatcher.telemetry import TelemetryCollector
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
    return make_personas_dir(tmp_path)


@pytest.fixture
def telemetry():
    return TelemetryCollector()


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


def _event_types(telemetry: TelemetryCollector) -> list[str]:
    """Extract event types in order from the collector."""
    return [e.event_type for e in telemetry.events]


# ── message_received ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emits_message_received(mgr, repo, personas_dir, telemetry):
    """Dispatcher emits a message_received event on every dispatch."""
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
        telemetry=telemetry,
    )
    await dispatcher.dispatch(_msg(source="slack", user_id="u42"))

    events = [e for e in telemetry.events if e.event_type == "message_received"]
    assert len(events) == 1
    assert events[0].payload["source"] == "slack"
    assert events[0].payload["user_id"] == "u42"


# ── session_resolved ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emits_session_resolved(mgr, repo, personas_dir, telemetry):
    """Dispatcher emits a session_resolved event after session lookup."""
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
        telemetry=telemetry,
    )
    result = await dispatcher.dispatch(_msg())

    events = [e for e in telemetry.events if e.event_type == "session_resolved"]
    assert len(events) == 1
    assert events[0].payload["session_id"] == result.session_id
    assert events[0].payload["agent_name"] == "agent"


# ── permission_resolved ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emits_permission_resolved(mgr, repo, personas_dir, telemetry):
    """Dispatcher emits a permission_resolved event."""
    perms = PermissionsConfig(
        channels={"terminal": ChannelPermissions(allowed_tools=["read_file"])},
    )
    config = DanClawConfig(
        agents=[make_config("agent").agents[0]],
        permissions=perms,
    )
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=config, personas_dir=personas_dir,
        telemetry=telemetry,
    )
    await dispatcher.dispatch(_msg(source="terminal"))

    events = [e for e in telemetry.events if e.event_type == "permission_resolved"]
    assert len(events) == 1
    assert events[0].payload["allowed_tools_count"] == 1
    assert events[0].payload["approval_required"] is False


# ── approval_gate_triggered ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_emits_approval_gate_triggered(mgr, repo, personas_dir, telemetry):
    """When approval is required, an approval_gate_triggered event is emitted."""
    perms = PermissionsConfig(
        channels={"slack": ChannelPermissions(
            allowed_tools=["deploy"],
            approval_required=True,
        )},
    )
    config = DanClawConfig(
        agents=[make_config("agent").agents[0]],
        permissions=perms,
    )
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=config, personas_dir=personas_dir,
        telemetry=telemetry,
    )
    await dispatcher.dispatch(_msg(source="slack"))

    events = [e for e in telemetry.events if e.event_type == "approval_gate_triggered"]
    assert len(events) == 1
    assert events[0].payload["source"] == "slack"


@pytest.mark.asyncio
async def test_no_approval_gate_event_when_not_needed(mgr, repo, personas_dir, telemetry):
    """No approval_gate_triggered event when approval is not required."""
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
        telemetry=telemetry,
    )
    await dispatcher.dispatch(_msg())

    events = [e for e in telemetry.events if e.event_type == "approval_gate_triggered"]
    assert len(events) == 0


# ── executor_invoked ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emits_executor_invoked(mgr, repo, personas_dir, telemetry):
    """Dispatcher emits an executor_invoked event before calling the executor."""
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
        telemetry=telemetry,
    )
    result = await dispatcher.dispatch(_msg())

    events = [e for e in telemetry.events if e.event_type == "executor_invoked"]
    assert len(events) == 1
    assert events[0].payload["session_id"] == result.session_id
    assert events[0].payload["agent_name"] == "agent"


@pytest.mark.asyncio
async def test_no_executor_invoked_when_approval_required(mgr, repo, personas_dir, telemetry):
    """No executor_invoked event when approval gate blocks execution."""
    perms = PermissionsConfig(
        channels={"slack": ChannelPermissions(
            allowed_tools=["deploy"],
            approval_required=True,
        )},
    )
    config = DanClawConfig(
        agents=[make_config("agent").agents[0]],
        permissions=perms,
    )
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=config, personas_dir=personas_dir,
        telemetry=telemetry,
    )
    await dispatcher.dispatch(_msg(source="slack"))

    events = [e for e in telemetry.events if e.event_type == "executor_invoked"]
    assert len(events) == 0


# ── executor_response ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emits_executor_response(mgr, repo, personas_dir, telemetry):
    """Dispatcher emits an executor_response event after successful execution."""
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
        telemetry=telemetry,
    )
    result = await dispatcher.dispatch(_msg())

    events = [e for e in telemetry.events if e.event_type == "executor_response"]
    assert len(events) == 1
    assert events[0].payload["session_id"] == result.session_id
    assert events[0].payload["backend"] == "mock"


# ── session_state_changed ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emits_session_state_changed_on_approval(mgr, repo, personas_dir, telemetry):
    """Session state change to WAITING_FOR_HUMAN emits a state change event."""
    perms = PermissionsConfig(
        channels={"slack": ChannelPermissions(
            allowed_tools=["deploy"],
            approval_required=True,
        )},
    )
    config = DanClawConfig(
        agents=[make_config("agent").agents[0]],
        permissions=perms,
    )
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=config, personas_dir=personas_dir,
        telemetry=telemetry,
    )
    await dispatcher.dispatch(_msg(source="slack"))

    events = [e for e in telemetry.events if e.event_type == "session_state_changed"]
    assert len(events) == 1
    assert events[0].payload["to_state"] == "WAITING_FOR_HUMAN"


@pytest.mark.asyncio
async def test_emits_session_state_changed_on_resume(mgr, repo, personas_dir, telemetry):
    """Resuming from WAITING_FOR_HUMAN emits a state change event."""
    perms = PermissionsConfig(
        channels={"slack": ChannelPermissions(
            allowed_tools=["deploy"],
            approval_required=True,
        )},
    )
    config = DanClawConfig(
        agents=[make_config("agent").agents[0]],
        permissions=perms,
    )
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=config, personas_dir=personas_dir,
        telemetry=telemetry,
    )

    # First dispatch triggers approval gate
    await dispatcher.dispatch(_msg(source="slack", channel_ref="thread1"))
    telemetry.clear()

    # Second dispatch resumes
    await dispatcher.dispatch(_msg(source="slack", channel_ref="thread1", content="approved"))

    events = [e for e in telemetry.events if e.event_type == "session_state_changed"]
    assert len(events) == 1
    assert events[0].payload["from_state"] == "WAITING_FOR_HUMAN"
    assert events[0].payload["to_state"] == "ACTIVE"


# ── error ────────────────────────────────────────────────────────────


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
async def test_emits_error_on_executor_failure(mgr, repo, personas_dir, telemetry):
    """Dispatcher emits an error event when the executor fails."""
    dispatcher = Dispatcher(
        mgr, repo, _FailingExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
        telemetry=telemetry,
    )
    with pytest.raises(RuntimeError, match="backend crashed"):
        await dispatcher.dispatch(_msg())

    events = [e for e in telemetry.events if e.event_type == "error"]
    assert len(events) == 1
    assert events[0].payload["error"] == "backend crashed"
    assert events[0].payload["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_emits_session_state_changed_to_error(mgr, repo, personas_dir, telemetry):
    """On executor failure, a session_state_changed event to ERROR is emitted."""
    dispatcher = Dispatcher(
        mgr, repo, _FailingExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
        telemetry=telemetry,
    )
    with pytest.raises(RuntimeError):
        await dispatcher.dispatch(_msg())

    events = [e for e in telemetry.events if e.event_type == "session_state_changed"]
    assert len(events) == 1
    assert events[0].payload["to_state"] == "ERROR"


# ── Full pipeline event ordering ─────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_event_order(mgr, repo, personas_dir, telemetry):
    """A normal dispatch emits events in the expected order."""
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
        telemetry=telemetry,
    )
    await dispatcher.dispatch(_msg())

    types = _event_types(telemetry)
    assert types == [
        "message_received",
        "session_resolved",
        "permission_resolved",
        "executor_invoked",
        "executor_response",
    ]


@pytest.mark.asyncio
async def test_approval_path_event_order(mgr, repo, personas_dir, telemetry):
    """When approval is required, events include the approval gate."""
    perms = PermissionsConfig(
        channels={"slack": ChannelPermissions(
            allowed_tools=["deploy"],
            approval_required=True,
        )},
    )
    config = DanClawConfig(
        agents=[make_config("agent").agents[0]],
        permissions=perms,
    )
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=config, personas_dir=personas_dir,
        telemetry=telemetry,
    )
    await dispatcher.dispatch(_msg(source="slack"))

    types = _event_types(telemetry)
    assert types == [
        "message_received",
        "session_resolved",
        "permission_resolved",
        "approval_gate_triggered",
        "session_state_changed",
    ]


@pytest.mark.asyncio
async def test_error_path_event_order(mgr, repo, personas_dir, telemetry):
    """On executor failure, error and state change events are emitted."""
    dispatcher = Dispatcher(
        mgr, repo, _FailingExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
        telemetry=telemetry,
    )
    with pytest.raises(RuntimeError):
        await dispatcher.dispatch(_msg())

    types = _event_types(telemetry)
    assert types == [
        "message_received",
        "session_resolved",
        "permission_resolved",
        "executor_invoked",
        "error",
        "session_state_changed",
    ]


# ── No telemetry — Dispatcher works without a collector ──────────────


@pytest.mark.asyncio
async def test_dispatch_works_without_telemetry(mgr, repo, personas_dir):
    """Dispatcher works normally when no telemetry collector is provided."""
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
    )
    result = await dispatcher.dispatch(_msg())
    assert result.response == "mock response: hello"
    assert result.backend == "mock"


# ── Multiple dispatches accumulate events ────────────────────────────


@pytest.mark.asyncio
async def test_events_accumulate_across_dispatches(mgr, repo, personas_dir, telemetry):
    """Events from multiple dispatch calls accumulate in the collector."""
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
        telemetry=telemetry,
    )
    await dispatcher.dispatch(_msg(content="first"))
    await dispatcher.dispatch(_msg(content="second"))

    received = [e for e in telemetry.events if e.event_type == "message_received"]
    assert len(received) == 2


# ── Events have timestamps ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_events_have_timestamps(mgr, repo, personas_dir, telemetry):
    """All emitted events have positive timestamps."""
    dispatcher = Dispatcher(
        mgr, repo, MockExecutor(),
        config=make_config("agent"), personas_dir=personas_dir,
        telemetry=telemetry,
    )
    await dispatcher.dispatch(_msg())

    for event in telemetry.events:
        assert event.timestamp > 0
