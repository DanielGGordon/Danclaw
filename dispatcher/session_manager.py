"""High-level session lifecycle management for DanClaw.

Wraps the :class:`Repository` to provide business-logic operations on
sessions: lookup-or-create, state transitions with validation, and
active-session queries.
"""

from __future__ import annotations

from typing import Optional

from dispatcher.models import StandardMessage
from dispatcher.repository import (
    ChannelBindingRow,
    Repository,
    SessionRow,
    VALID_STATES,
)


# States considered "live" — sessions the dispatcher should still route to.
_LIVE_STATES = frozenset({"ACTIVE", "WAITING_FOR_HUMAN"})

# Allowed state transitions.  A session that is DONE or ERROR cannot be
# moved back to ACTIVE (create a new session instead).
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "ACTIVE": frozenset({"WAITING_FOR_HUMAN", "DONE", "ERROR"}),
    "WAITING_FOR_HUMAN": frozenset({"ACTIVE", "DONE", "ERROR"}),
    "DONE": frozenset(),          # terminal
    "ERROR": frozenset({"ACTIVE"}),  # allow retry from ERROR
}


class SessionManager:
    """Manages session lifecycle on top of a :class:`Repository`.

    Parameters
    ----------
    repo:
        The repository instance used for all database access.
    """

    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    # ── Core operations ───────────────────────────────────────────────

    async def get_or_create_session(
        self,
        message: StandardMessage,
        agent_name: str,
    ) -> SessionRow:
        """Find an existing live session for the channel, or create one.

        Resolution order:

        1. If ``message.session_id`` is set, look up that session directly.
           If found and in a live state, return it.
        2. Otherwise, search for the most recent live session bound to the
           message's (source, channel_ref) pair.
        3. If no live session is found, create a new ACTIVE session and
           bind it to the channel.

        Parameters
        ----------
        message:
            The incoming :class:`StandardMessage`.
        agent_name:
            Agent name to assign when creating a new session.

        Returns
        -------
        SessionRow
            An existing or newly created session.
        """
        # 1. Explicit session ID on the message
        if message.session_id is not None:
            session = await self._repo.get_session(message.session_id)
            if session is not None and session.state in _LIVE_STATES:
                # Ensure a channel binding exists for this channel_ref.
                # This is the path taken by ``agent attach``, which sends
                # messages from a new terminal with the session_id set.
                await self._ensure_binding(
                    session.id, message.source, message.channel_ref,
                )
                return session

        # 2. Look up by channel binding
        session = await self._repo.find_session_by_channel(
            message.source, message.channel_ref,
        )
        if session is not None:
            return session

        # 3. Create a new session + channel binding
        session = await self._repo.create_session(agent_name)
        await self._repo.add_channel_binding(
            session.id, message.source, message.channel_ref,
        )
        return session

    async def _ensure_binding(
        self,
        session_id: str,
        channel_type: str,
        channel_ref: str,
    ) -> None:
        """Add a channel binding if one does not already exist.

        Silently succeeds when the exact binding is already present
        (IntegrityError from the UNIQUE constraint is caught).
        """
        try:
            await self._repo.add_channel_binding(
                session_id, channel_type, channel_ref,
            )
        except Exception:
            # Binding already exists — nothing to do.
            pass

    async def get_session(self, session_id: str) -> Optional[SessionRow]:
        """Retrieve a session by its ID.

        Returns ``None`` if the session does not exist.
        """
        return await self._repo.get_session(session_id)

    async def add_binding(
        self,
        session_id: str,
        channel_type: str,
        channel_ref: str,
    ) -> ChannelBindingRow:
        """Add a channel binding to an existing session.

        Parameters
        ----------
        session_id:
            ID of the session to bind.
        channel_type:
            Channel type (e.g. ``"terminal"``, ``"slack"``).
        channel_ref:
            Channel-specific reference for routing responses.

        Returns
        -------
        ChannelBindingRow
            The newly created binding.

        Raises
        ------
        KeyError
            If no session with *session_id* exists.
        aiosqlite.IntegrityError
            If this exact binding already exists.
        """
        session = await self._repo.get_session(session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        return await self._repo.add_channel_binding(
            session_id, channel_type, channel_ref,
        )

    async def get_bindings(
        self, session_id: str,
    ) -> list[ChannelBindingRow]:
        """Return all channel bindings for a session.

        Parameters
        ----------
        session_id:
            ID of the session.

        Returns
        -------
        list[ChannelBindingRow]
            All bindings, ordered by creation time.
        """
        return await self._repo.get_bindings_for_session(session_id)

    async def update_agent(
        self, session_id: str, agent_name: str,
    ) -> SessionRow:
        """Change the agent assigned to a session.

        Parameters
        ----------
        session_id:
            ID of the session to update.
        agent_name:
            New agent name to assign.

        Returns
        -------
        SessionRow
            The updated session row.

        Raises
        ------
        KeyError
            If no session with *session_id* exists.
        """
        updated = await self._repo.update_session_agent(session_id, agent_name)
        if updated is None:
            raise KeyError(f"Session {session_id!r} not found")
        return updated

    async def update_state(
        self, session_id: str, new_state: str,
    ) -> SessionRow:
        """Transition a session to *new_state*.

        Validates that *new_state* is a recognised state and that the
        transition from the session's current state is allowed.

        Parameters
        ----------
        session_id:
            ID of the session to update.
        new_state:
            Target state.

        Returns
        -------
        SessionRow
            The updated session row.

        Raises
        ------
        ValueError
            If *new_state* is not a valid state, or the transition is
            not allowed.
        KeyError
            If no session with *session_id* exists.
        """
        if new_state not in VALID_STATES:
            raise ValueError(
                f"Invalid session state {new_state!r}. "
                f"Must be one of {sorted(VALID_STATES)}"
            )

        session = await self._repo.get_session(session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")

        if new_state == session.state:
            # No-op: already in the target state.
            return session

        allowed = _ALLOWED_TRANSITIONS.get(session.state, frozenset())
        if new_state not in allowed:
            raise ValueError(
                f"Cannot transition from {session.state!r} to {new_state!r}"
            )

        updated = await self._repo.update_session_state(session_id, new_state)
        # update_session_state returns None only if the row vanished between
        # the get and the update — extremely unlikely but handle defensively.
        assert updated is not None, "session disappeared during update"
        return updated

    async def get_attribution(self, session_id: str) -> str:
        """Return the attribution label for a session.

        Parameters
        ----------
        session_id:
            ID of the session.

        Returns
        -------
        str
            The session's attribution label.

        Raises
        ------
        KeyError
            If no session with *session_id* exists.
        """
        session = await self._repo.get_session(session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        return session.attribution

    async def set_attribution(
        self, session_id: str, attribution: str,
    ) -> SessionRow:
        """Set the attribution label for a session.

        Parameters
        ----------
        session_id:
            ID of the session to update.
        attribution:
            New attribution label (e.g. ``"bot"``,
            ``"[via terminal]"``).

        Returns
        -------
        SessionRow
            The updated session row.

        Raises
        ------
        KeyError
            If no session with *session_id* exists.
        """
        updated = await self._repo.update_session_attribution(
            session_id, attribution,
        )
        if updated is None:
            raise KeyError(f"Session {session_id!r} not found")
        return updated

    async def list_active_sessions(self) -> list[SessionRow]:
        """Return all sessions in a live state (ACTIVE or WAITING_FOR_HUMAN)."""
        active = await self._repo.list_sessions(state="ACTIVE")
        waiting = await self._repo.list_sessions(state="WAITING_FOR_HUMAN")
        # Merge and sort by created_at for a stable order.
        combined = active + waiting
        combined.sort(key=lambda s: s.created_at)
        return combined
