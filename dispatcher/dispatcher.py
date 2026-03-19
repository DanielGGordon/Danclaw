"""Core dispatcher that routes messages through the full pipeline.

Accepts a :class:`StandardMessage`, finds or creates a session via
:class:`SessionManager`, passes the message to an executor, stores both
the inbound message and the response in the database via the repository,
updates session state, and returns the response.

Uses the loaded :class:`DanClawConfig` to resolve which agent handles a
message.  For now, the default agent (first in the config) is always
selected; per-channel routing rules will come in a later phase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import DanClawConfig
from dispatcher.executor import Executor, ExecutorResult
from dispatcher.models import StandardMessage
from dispatcher.permissions import resolve_permissions, requires_approval
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from dispatcher.telemetry import TelemetryCollector
from personas import load_persona, PersonaError

logger = logging.getLogger(__name__)


def _parse_switch_command(content: str) -> str | None:
    """Extract the target agent name from a persona switch command.

    Recognised forms:

    * ``/switch <agent>``
    * ``switch to <agent>``

    Returns the target agent name (stripped), or ``None`` if the content
    is not a switch command.
    """
    lower = content.strip().lower()
    if lower.startswith("/switch "):
        return content.strip()[len("/switch "):].strip()
    if lower.startswith("switch to "):
        return content.strip()[len("switch to "):].strip()
    return None


@dataclass(frozen=True)
class DispatchResult:
    """Value object returned after dispatching a message.

    Attributes:
        session_id: The session ID the message was routed to.
        response: The executor's response text.
        backend: Name of the backend that produced the response.
        agent_name: Name of the agent that handled the message.
        fanout_channels: Channel refs bound to the session, excluding the
            source channel.  Listeners use this list to deliver the
            response to other channels that are following the session.
    """

    session_id: str
    response: str
    backend: str
    agent_name: str
    fanout_channels: tuple[str, ...] = ()


class Dispatcher:
    """Routes incoming messages through the full dispatcher pipeline.

    Pipeline steps:
    1. Resolve the agent from config.
    2. Find or create a session via :class:`SessionManager`.
    3. Store the inbound message in the database.
    4. Pass the message to the executor.
    5. Store the executor's response in the database.
    6. Return a :class:`DispatchResult`.

    If the executor raises an exception, the session is transitioned to
    ``ERROR`` state and the exception is re-raised.

    Parameters
    ----------
    session_manager:
        Manages session lifecycle (get-or-create, state transitions).
    repo:
        Repository for persisting messages.
    executor:
        The AI executor implementation to use.
    config:
        The loaded :class:`DanClawConfig` used to resolve agents.
    personas_dir:
        Directory containing persona markdown files.  Defaults to the
        ``personas/`` package directory.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        repo: Repository,
        executor: Executor,
        config: DanClawConfig,
        *,
        personas_dir: str | Path | None = None,
        telemetry: TelemetryCollector | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._repo = repo
        self._executor = executor
        self._config = config
        self._personas_dir = personas_dir
        self._telemetry = telemetry
        self._last_resolved_permissions: frozenset[str] = frozenset()

    def _emit(self, event_type: str, payload: dict | None = None) -> None:
        """Record a telemetry event if a collector is configured."""
        if self._telemetry is not None:
            self._telemetry.record(event_type, payload)

    async def _fanout_channels(
        self, session_id: str, source_channel_ref: str,
    ) -> tuple[str, ...]:
        """Return channel refs bound to *session_id*, excluding *source_channel_ref*."""
        bindings = await self._session_manager.get_bindings(session_id)
        return tuple(
            b.channel_ref for b in bindings
            if b.channel_ref != source_channel_ref
        )

    async def dispatch(self, message: StandardMessage) -> DispatchResult:
        """Route *message* through the full pipeline.

        Parameters
        ----------
        message:
            The incoming :class:`StandardMessage` from a listener.

        Returns
        -------
        DispatchResult
            Contains the session ID, response text, and backend name.

        Raises
        ------
        Exception
            Any exception from the executor is re-raised after setting
            the session state to ``ERROR``.
        """
        # 1. Message received
        self._emit("message_received", {
            "source": message.source,
            "channel_ref": message.channel_ref,
            "user_id": message.user_id,
        })

        # 2. Resolve agent from config (default agent for now)
        agent = self._config.default_agent
        agent_name = agent.name

        # 3. Find or create session
        session = await self._session_manager.get_or_create_session(
            message, agent_name,
        )
        session_id = session.id
        self._emit("session_resolved", {
            "session_id": session_id,
            "agent_name": agent_name,
            "state": session.state,
        })

        # 3a. Resume from WAITING_FOR_HUMAN — transition back to ACTIVE
        resumed_from_waiting = False
        if session.state == "WAITING_FOR_HUMAN":
            session = await self._session_manager.update_state(
                session_id, "ACTIVE",
            )
            resumed_from_waiting = True
            self._emit("session_state_changed", {
                "session_id": session_id,
                "from_state": "WAITING_FOR_HUMAN",
                "to_state": "ACTIVE",
            })
            logger.info(
                "Session %s resumed from WAITING_FOR_HUMAN to ACTIVE",
                session_id,
            )

        # 3b. Handle persona switch commands
        switch_target = _parse_switch_command(message.content)
        if switch_target is not None:
            return await self._handle_switch(
                message, session_id, switch_target,
            )

        # 3c. If session already has an agent, use that agent's config
        #     (supports post-switch messages using the switched persona)
        session_agent = self._config.get_agent(session.agent_name)
        if session_agent is not None:
            agent = session_agent
            agent_name = agent.name

        # 4. Resolve permissions for the channel + user
        allowed_tools = resolve_permissions(
            self._config.permissions, message.source, message.user_id,
        )
        approval_needed = requires_approval(
            self._config.permissions, message.source, message.user_id,
        )
        self._last_resolved_permissions = allowed_tools

        self._emit("permission_resolved", {
            "source": message.source,
            "user_id": message.user_id,
            "allowed_tools_count": len(allowed_tools),
            "approval_required": approval_needed,
        })

        logger.info(
            "Resolved permissions for %s/%s: %d tools, approval=%s",
            message.source, message.user_id,
            len(allowed_tools), approval_needed,
        )

        # 5. Load persona for the resolved agent
        persona_content: str | None = None
        try:
            persona_content = load_persona(
                agent.persona,
                personas_dir=self._personas_dir,
            )
        except PersonaError:
            logger.warning(
                "Could not load persona '%s' for agent '%s'; "
                "proceeding without persona",
                agent.persona, agent_name,
            )

        # 6. Store inbound message
        await self._repo.save_message(
            session_id=session_id,
            role="user",
            content=message.content,
            source=message.source,
            channel_ref=message.channel_ref,
            user_id=message.user_id,
        )

        # 7. Approval gate — if approval is required, pause the session.
        #    Skip this gate when the session was just resumed from
        #    WAITING_FOR_HUMAN — the human's reply *is* the approval.
        if approval_needed and not resumed_from_waiting:
            await self._session_manager.update_state(
                session_id, "WAITING_FOR_HUMAN",
            )
            self._emit("approval_gate_triggered", {
                "session_id": session_id,
                "source": message.source,
                "user_id": message.user_id,
            })
            self._emit("session_state_changed", {
                "session_id": session_id,
                "from_state": "ACTIVE",
                "to_state": "WAITING_FOR_HUMAN",
            })
            approval_msg = (
                "This request requires approval before it can be executed. "
                "A human must approve this session to continue."
            )
            await self._repo.save_message(
                session_id=session_id,
                role="assistant",
                content=approval_msg,
                source=message.source,
                channel_ref=message.channel_ref,
                user_id="system",
            )
            logger.info(
                "Session %s set to WAITING_FOR_HUMAN (approval required)",
                session_id,
            )
            fanout = await self._fanout_channels(
                session_id, message.channel_ref,
            )
            return DispatchResult(
                session_id=session_id,
                response=approval_msg,
                backend="system",
                agent_name=agent_name,
                fanout_channels=fanout,
            )

        logger.info(
            "Dispatching message to session %s (agent=%s)",
            session_id, agent_name,
        )

        # 8. Execute
        self._emit("executor_invoked", {
            "session_id": session_id,
            "agent_name": agent_name,
        })
        try:
            result: ExecutorResult = await self._executor.execute(
                message, persona=persona_content,
                allowed_tools=allowed_tools,
            )
        except Exception as exc:
            logger.exception(
                "Executor failed for session %s", session_id,
            )
            self._emit("error", {
                "session_id": session_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            })
            await self._session_manager.update_state(session_id, "ERROR")
            self._emit("session_state_changed", {
                "session_id": session_id,
                "from_state": "ACTIVE",
                "to_state": "ERROR",
            })
            raise

        # 9. Executor response received
        self._emit("executor_response", {
            "session_id": session_id,
            "backend": result.backend,
        })

        # 10. Store response
        await self._repo.save_message(
            session_id=session_id,
            role="assistant",
            content=result.content,
            source=message.source,
            channel_ref=message.channel_ref,
            user_id="system",
        )

        logger.info(
            "Dispatch complete for session %s (backend=%s)",
            session_id, result.backend,
        )

        # 11. Gather fanout channels and return result
        fanout = await self._fanout_channels(session_id, message.channel_ref)
        return DispatchResult(
            session_id=session_id,
            response=result.content,
            backend=result.backend,
            agent_name=agent_name,
            fanout_channels=fanout,
        )

    async def _handle_switch(
        self,
        message: StandardMessage,
        session_id: str,
        target_name: str,
    ) -> DispatchResult:
        """Handle a persona switch command within a session.

        Validates that the target agent exists in the config, updates the
        session's agent_name, and returns a confirmation response.  If the
        target agent is not found, returns an error message without
        changing the session.
        """
        target_agent = self._config.get_agent(target_name)
        if target_agent is None:
            error_msg = (
                f"Unknown agent '{target_name}'. "
                f"Available agents: "
                f"{', '.join(a.name for a in self._config.agents)}"
            )
            # Store the switch command as a user message
            await self._repo.save_message(
                session_id=session_id,
                role="user",
                content=message.content,
                source=message.source,
                channel_ref=message.channel_ref,
                user_id=message.user_id,
            )
            # Store the error as an assistant message
            await self._repo.save_message(
                session_id=session_id,
                role="assistant",
                content=error_msg,
                source=message.source,
                channel_ref=message.channel_ref,
                user_id="system",
            )
            session = await self._session_manager.get_session(session_id)
            fanout = await self._fanout_channels(
                session_id, message.channel_ref,
            )
            return DispatchResult(
                session_id=session_id,
                response=error_msg,
                backend="system",
                agent_name=session.agent_name,
                fanout_channels=fanout,
            )

        # Update session agent
        await self._session_manager.update_agent(session_id, target_agent.name)

        confirm_msg = f"Switched to agent '{target_agent.name}'."
        # Store the switch command as a user message
        await self._repo.save_message(
            session_id=session_id,
            role="user",
            content=message.content,
            source=message.source,
            channel_ref=message.channel_ref,
            user_id=message.user_id,
        )
        # Store the confirmation as an assistant message
        await self._repo.save_message(
            session_id=session_id,
            role="assistant",
            content=confirm_msg,
            source=message.source,
            channel_ref=message.channel_ref,
            user_id="system",
        )
        fanout = await self._fanout_channels(
            session_id, message.channel_ref,
        )
        return DispatchResult(
            session_id=session_id,
            response=confirm_msg,
            backend="system",
            agent_name=target_agent.name,
            fanout_channels=fanout,
        )
