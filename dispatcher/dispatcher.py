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
from typing import Optional

from config import DanClawConfig
from dispatcher.executor import Executor, ExecutorResult
from dispatcher.models import StandardMessage
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchResult:
    """Value object returned after dispatching a message.

    Attributes:
        session_id: The session ID the message was routed to.
        response: The executor's response text.
        backend: Name of the backend that produced the response.
        agent_name: Name of the agent that handled the message.
    """

    session_id: str
    response: str
    backend: str
    agent_name: str


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
    """

    def __init__(
        self,
        session_manager: SessionManager,
        repo: Repository,
        executor: Executor,
        config: DanClawConfig,
    ) -> None:
        self._session_manager = session_manager
        self._repo = repo
        self._executor = executor
        self._config = config

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
        # 1. Resolve agent from config (default agent for now)
        agent = self._config.default_agent
        agent_name = agent.name

        # 2. Find or create session
        session = await self._session_manager.get_or_create_session(
            message, agent_name,
        )
        session_id = session.id
        logger.info(
            "Dispatching message to session %s (agent=%s)",
            session_id, agent_name,
        )

        # 3. Store inbound message
        await self._repo.save_message(
            session_id=session_id,
            role="user",
            content=message.content,
            source=message.source,
            channel_ref=message.channel_ref,
            user_id=message.user_id,
        )

        # 4. Execute
        try:
            result: ExecutorResult = await self._executor.execute(message)
        except Exception:
            logger.exception(
                "Executor failed for session %s", session_id,
            )
            await self._session_manager.update_state(session_id, "ERROR")
            raise

        # 5. Store response
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

        # 6. Return result
        return DispatchResult(
            session_id=session_id,
            response=result.content,
            backend=result.backend,
            agent_name=agent_name,
        )
