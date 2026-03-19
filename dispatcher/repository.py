"""Repository abstraction layer for all DanClaw database access.

Provides async CRUD methods for sessions, messages, and channel_bindings
tables.  No other module should execute SQL directly — all DB access goes
through this layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiosqlite


# ── Row dataclasses ──────────────────────────────────────────────────

@dataclass(frozen=True)
class SessionRow:
    """Represents a row in the sessions table."""

    id: str
    agent_name: str
    state: str
    attribution: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MessageRow:
    """Represents a row in the messages table."""

    id: int
    session_id: str
    role: str
    content: str
    source: str
    channel_ref: str
    user_id: str
    created_at: str


@dataclass(frozen=True)
class ChannelBindingRow:
    """Represents a row in the channel_bindings table."""

    id: int
    session_id: str
    channel_type: str
    channel_ref: str
    created_at: str


# ── Valid session states ─────────────────────────────────────────────

VALID_STATES = frozenset({"ACTIVE", "WAITING_FOR_HUMAN", "DONE", "ERROR"})


# ── Repository ───────────────────────────────────────────────────────

class Repository:
    """Async repository for DanClaw database operations.

    All methods operate on an ``aiosqlite.Connection`` passed at
    construction time.  The caller owns the connection lifecycle.

    Parameters
    ----------
    db:
        An open ``aiosqlite.Connection``.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    # ── Sessions ─────────────────────────────────────────────────────

    async def create_session(
        self,
        agent_name: str,
        *,
        session_id: Optional[str] = None,
        state: str = "ACTIVE",
        attribution: str = "bot",
    ) -> SessionRow:
        """Create a new session and return its row.

        Parameters
        ----------
        agent_name:
            Name of the agent handling this session.
        session_id:
            Optional explicit ID.  A UUID4 is generated if omitted.
        state:
            Initial state (default ``"ACTIVE"``).
        attribution:
            Attribution label for message formatting (default ``"bot"``).

        Raises
        ------
        ValueError:
            If *state* is not one of the valid session states.
        """
        if state not in VALID_STATES:
            raise ValueError(
                f"Invalid session state {state!r}. "
                f"Must be one of {sorted(VALID_STATES)}"
            )

        sid = session_id or uuid.uuid4().hex
        now = _utcnow()
        await self._db.execute(
            "INSERT INTO sessions (id, agent_name, state, attribution, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, agent_name, state, attribution, now, now),
        )
        await self._db.commit()
        return SessionRow(id=sid, agent_name=agent_name, state=state,
                          attribution=attribution, created_at=now,
                          updated_at=now)

    async def get_session(self, session_id: str) -> Optional[SessionRow]:
        """Return a session by ID, or ``None`` if not found."""
        async with self._db.execute(
            "SELECT id, agent_name, state, attribution, created_at, updated_at "
            "FROM sessions WHERE id = ?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return SessionRow(*row)

    async def update_session_state(
        self, session_id: str, new_state: str
    ) -> Optional[SessionRow]:
        """Update a session's state and return the updated row.

        Returns ``None`` if the session does not exist.

        Raises
        ------
        ValueError:
            If *new_state* is not one of the valid session states.
        """
        if new_state not in VALID_STATES:
            raise ValueError(
                f"Invalid session state {new_state!r}. "
                f"Must be one of {sorted(VALID_STATES)}"
            )

        now = _utcnow()
        cursor = await self._db.execute(
            "UPDATE sessions SET state = ?, updated_at = ? WHERE id = ?",
            (new_state, now, session_id),
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get_session(session_id)

    async def update_session_agent(
        self, session_id: str, agent_name: str,
    ) -> Optional[SessionRow]:
        """Update a session's agent_name and return the updated row.

        Returns ``None`` if the session does not exist.
        """
        now = _utcnow()
        cursor = await self._db.execute(
            "UPDATE sessions SET agent_name = ?, updated_at = ? WHERE id = ?",
            (agent_name, now, session_id),
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get_session(session_id)

    async def update_session_attribution(
        self, session_id: str, attribution: str,
    ) -> Optional[SessionRow]:
        """Update a session's attribution label and return the updated row.

        Returns ``None`` if the session does not exist.
        """
        now = _utcnow()
        cursor = await self._db.execute(
            "UPDATE sessions SET attribution = ?, updated_at = ? WHERE id = ?",
            (attribution, now, session_id),
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get_session(session_id)

    async def list_sessions(
        self, *, state: Optional[str] = None
    ) -> list[SessionRow]:
        """Return all sessions, optionally filtered by state.

        Raises
        ------
        ValueError:
            If *state* is provided but not a valid session state.
        """
        if state is not None and state not in VALID_STATES:
            raise ValueError(
                f"Invalid session state {state!r}. "
                f"Must be one of {sorted(VALID_STATES)}"
            )

        if state is None:
            sql = "SELECT id, agent_name, state, attribution, created_at, updated_at FROM sessions ORDER BY created_at"
            params: tuple = ()
        else:
            sql = "SELECT id, agent_name, state, attribution, created_at, updated_at FROM sessions WHERE state = ? ORDER BY created_at"
            params = (state,)

        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [SessionRow(*r) for r in rows]

    # ── Messages ─────────────────────────────────────────────────────

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        source: str,
        channel_ref: str,
        user_id: str,
    ) -> MessageRow:
        """Insert a message and return its row.

        Parameters
        ----------
        session_id:
            The session this message belongs to.
        role:
            Message role (e.g. ``"user"``, ``"assistant"``).
        content:
            The message body text.
        source:
            Origin channel type (e.g. ``"terminal"``, ``"slack"``).
        channel_ref:
            Channel-specific reference for routing responses.
        user_id:
            Identifier for the user who sent the message.
        """
        now = _utcnow()
        cursor = await self._db.execute(
            "INSERT INTO messages (session_id, role, content, source, channel_ref, user_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, content, source, channel_ref, user_id, now),
        )
        await self._db.commit()
        return MessageRow(
            id=cursor.lastrowid,
            session_id=session_id,
            role=role,
            content=content,
            source=source,
            channel_ref=channel_ref,
            user_id=user_id,
            created_at=now,
        )

    async def get_messages_for_session(
        self, session_id: str
    ) -> list[MessageRow]:
        """Return all messages for a session, ordered by creation time."""
        async with self._db.execute(
            "SELECT id, session_id, role, content, source, channel_ref, user_id, created_at "
            "FROM messages WHERE session_id = ? ORDER BY created_at, id",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [MessageRow(*r) for r in rows]

    # ── Channel bindings ─────────────────────────────────────────────

    async def add_channel_binding(
        self,
        session_id: str,
        channel_type: str,
        channel_ref: str,
    ) -> ChannelBindingRow:
        """Bind a channel to a session and return the row.

        The (session_id, channel_type, channel_ref) combination must be
        unique — a duplicate raises ``aiosqlite.IntegrityError``.
        """
        now = _utcnow()
        cursor = await self._db.execute(
            "INSERT INTO channel_bindings (session_id, channel_type, channel_ref, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, channel_type, channel_ref, now),
        )
        await self._db.commit()
        return ChannelBindingRow(
            id=cursor.lastrowid,
            session_id=session_id,
            channel_type=channel_type,
            channel_ref=channel_ref,
            created_at=now,
        )

    async def get_bindings_for_session(
        self, session_id: str
    ) -> list[ChannelBindingRow]:
        """Return all channel bindings for a session."""
        async with self._db.execute(
            "SELECT id, session_id, channel_type, channel_ref, created_at "
            "FROM channel_bindings WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [ChannelBindingRow(*r) for r in rows]

    async def find_session_by_channel(
        self, channel_type: str, channel_ref: str
    ) -> Optional[SessionRow]:
        """Find the most recent active session bound to a channel.

        Returns the session with state ``ACTIVE`` or
        ``WAITING_FOR_HUMAN`` that is bound to the given channel,
        preferring the most recently created one.  Returns ``None`` if
        no matching session exists.
        """
        async with self._db.execute(
            "SELECT s.id, s.agent_name, s.state, s.attribution, s.created_at, s.updated_at "
            "FROM sessions s "
            "JOIN channel_bindings cb ON s.id = cb.session_id "
            "WHERE cb.channel_type = ? AND cb.channel_ref = ? "
            "  AND s.state IN ('ACTIVE', 'WAITING_FOR_HUMAN') "
            "ORDER BY s.created_at DESC LIMIT 1",
            (channel_type, channel_ref),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return SessionRow(*row)


# ── Helpers ──────────────────────────────────────────────────────────

def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
