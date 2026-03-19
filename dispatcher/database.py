"""SQLite schema initialisation for DanClaw.

Provides ``init_db`` — an async function that creates the core tables
(sessions, messages, channel_bindings, telemetry_events) using
``CREATE TABLE IF NOT EXISTS`` so it is safe to call on every startup.
"""

from __future__ import annotations

import aiosqlite

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    agent_name  TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'ACTIVE'
                    CHECK (state IN ('ACTIVE', 'WAITING_FOR_HUMAN', 'DONE', 'ERROR')),
    attribution TEXT NOT NULL DEFAULT 'bot',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL,
    channel_ref TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_bindings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    channel_type TEXT NOT NULL,
    channel_ref  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE (session_id, channel_type, channel_ref)
);

CREATE TABLE IF NOT EXISTS telemetry_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    session_id  TEXT,
    source      TEXT,
    status      TEXT NOT NULL DEFAULT 'ok',
    payload     TEXT NOT NULL,
    timestamp   REAL NOT NULL,
    created_at  TEXT NOT NULL
);
"""


async def init_db(db_path: str) -> None:
    """Create the DanClaw database schema.

    Uses ``CREATE TABLE IF NOT EXISTS`` so the function is idempotent and
    safe to call on every application startup.

    Parameters
    ----------
    db_path:
        Filesystem path for the SQLite database file.  Use ``":memory:"``
        for an in-memory database (useful in tests).
    """
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA_SQL)
        await db.execute("PRAGMA foreign_keys = ON")
        await db.commit()
