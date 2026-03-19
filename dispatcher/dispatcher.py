"""Core dispatcher that routes messages through the full pipeline.

Accepts a :class:`StandardMessage`, finds or creates a session via
:class:`SessionManager`, passes the message to an executor, stores both
the inbound message and the response in the database via the repository,
updates session state, and returns the response.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

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
    """

    session_id: str
    response: str
    backend: str


class Dispatcher:
    """Routes incoming messages through the full dispatcher pipeline.

    Pipeline steps:
    1. Find or create a session via :class:`SessionManager`.
    2. Store the inbound message in the database.
    3. Pass the message to the executor.
    4. Store the executor's response in the database.
    5. Return a :class:`DispatchResult`.

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
    agent_name:
        Default agent name for new sessions.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        repo: Repository,
        executor: Executor,
        agent_name: str = "default",
    ) -> None:
        self._session_manager = session_manager
        self._repo = repo
        self._executor = executor
        self._agent_name = agent_name

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
        # 1. Find or create session
        session = await self._session_manager.get_or_create_session(
            message, self._agent_name,
        )
        session_id = session.id
        logger.info(
            "Dispatching message to session %s (agent=%s)",
            session_id, session.agent_name,
        )

        # 2. Store inbound message
        await self._repo.save_message(
            session_id=session_id,
            role="user",
            content=message.content,
            source=message.source,
            channel_ref=message.channel_ref,
            user_id=message.user_id,
        )

        # 3. Execute
        try:
            result: ExecutorResult = await self._executor.execute(message)
        except Exception:
            logger.exception(
                "Executor failed for session %s", session_id,
            )
            await self._session_manager.update_state(session_id, "ERROR")
            raise

        # 4. Store response
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

        # 5. Return result
        return DispatchResult(
            session_id=session_id,
            response=result.content,
            backend=result.backend,
        )

    async def list_sessions(self) -> list:
        """Return all sessions from the repository."""
        return await self._repo.list_sessions()
