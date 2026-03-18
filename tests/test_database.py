"""Tests for dispatcher.database — schema creation and idempotency."""

from __future__ import annotations

import aiosqlite
import pytest

from dispatcher.database import init_db


@pytest.mark.asyncio
async def test_init_db_creates_sessions_table():
    """The sessions table exists after init_db."""
    async with aiosqlite.connect(":memory:") as db:
        await _init_and_reopen(db)
        tables = await _table_names(db)
        assert "sessions" in tables


@pytest.mark.asyncio
async def test_init_db_creates_messages_table():
    """The messages table exists after init_db."""
    async with aiosqlite.connect(":memory:") as db:
        await _init_and_reopen(db)
        tables = await _table_names(db)
        assert "messages" in tables


@pytest.mark.asyncio
async def test_init_db_creates_channel_bindings_table():
    """The channel_bindings table exists after init_db."""
    async with aiosqlite.connect(":memory:") as db:
        await _init_and_reopen(db)
        tables = await _table_names(db)
        assert "channel_bindings" in tables


@pytest.mark.asyncio
async def test_init_db_idempotent(tmp_path):
    """Calling init_db twice on the same database does not raise."""
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    await init_db(db_path)  # must not raise


@pytest.mark.asyncio
async def test_init_db_idempotent_preserves_data(tmp_path):
    """Calling init_db a second time does not destroy existing rows."""
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)

    # Insert a session
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO sessions (id, agent_name, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("s1", "agent", "ACTIVE", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    # Re-init
    await init_db(db_path)

    # Data still present
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT id FROM sessions") as cur:
            rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "s1"


# ── Column structure checks ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_sessions_columns():
    """sessions table has the expected columns with correct types."""
    expected = {
        "id": "TEXT",
        "agent_name": "TEXT",
        "state": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    }
    columns = await _column_info(":memory:", "sessions")
    for name, ctype in expected.items():
        assert name in columns, f"missing column: {name}"
        assert columns[name]["type"] == ctype, f"column {name} type mismatch"


@pytest.mark.asyncio
async def test_sessions_primary_key():
    """sessions.id is the primary key."""
    columns = await _column_info(":memory:", "sessions")
    assert columns["id"]["pk"] == 1


@pytest.mark.asyncio
async def test_messages_columns():
    """messages table has the expected columns."""
    expected = {
        "id": "INTEGER",
        "session_id": "TEXT",
        "role": "TEXT",
        "content": "TEXT",
        "source": "TEXT",
        "channel_ref": "TEXT",
        "user_id": "TEXT",
        "created_at": "TEXT",
    }
    columns = await _column_info(":memory:", "messages")
    for name, ctype in expected.items():
        assert name in columns, f"missing column: {name}"
        assert columns[name]["type"] == ctype, f"column {name} type mismatch"


@pytest.mark.asyncio
async def test_messages_primary_key_autoincrement():
    """messages.id is an autoincrement primary key."""
    columns = await _column_info(":memory:", "messages")
    assert columns["id"]["pk"] == 1


@pytest.mark.asyncio
async def test_channel_bindings_columns():
    """channel_bindings table has the expected columns."""
    expected = {
        "id": "INTEGER",
        "session_id": "TEXT",
        "channel_type": "TEXT",
        "channel_ref": "TEXT",
        "created_at": "TEXT",
    }
    columns = await _column_info(":memory:", "channel_bindings")
    for name, ctype in expected.items():
        assert name in columns, f"missing column: {name}"
        assert columns[name]["type"] == ctype, f"column {name} type mismatch"


@pytest.mark.asyncio
async def test_channel_bindings_unique_constraint(tmp_path):
    """Inserting a duplicate (session_id, channel_type, channel_ref) raises."""
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO sessions (id, agent_name, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("s1", "agent", "ACTIVE", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.execute(
            "INSERT INTO channel_bindings (session_id, channel_type, channel_ref, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("s1", "slack", "#general", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "INSERT INTO channel_bindings (session_id, channel_type, channel_ref, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("s1", "slack", "#general", "2026-01-01T00:00:00Z"),
            )


@pytest.mark.asyncio
async def test_sessions_state_check_constraint(tmp_path):
    """Inserting an invalid session state raises due to CHECK constraint."""
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)

    async with aiosqlite.connect(db_path) as db:
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "INSERT INTO sessions (id, agent_name, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("s1", "agent", "INVALID_STATE", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )


@pytest.mark.asyncio
async def test_init_db_on_disk(tmp_path):
    """init_db works with a real file path, not just :memory:."""
    db_path = str(tmp_path / "danclaw.db")
    await init_db(db_path)

    async with aiosqlite.connect(db_path) as db:
        tables = await _table_names(db)
    assert "sessions" in tables
    assert "messages" in tables
    assert "channel_bindings" in tables


# ── Helpers ──────────────────────────────────────────────────────────

async def _init_and_reopen(db: aiosqlite.Connection) -> None:
    """Run schema creation inline on an already-open :memory: connection."""
    # We can't use init_db(":memory:") because each connect(":memory:")
    # creates a separate database. Instead, run the SQL directly.
    from dispatcher.database import _SCHEMA_SQL
    await db.executescript(_SCHEMA_SQL)


async def _table_names(db: aiosqlite.Connection) -> set[str]:
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ) as cur:
        rows = await cur.fetchall()
    return {r[0] for r in rows}


async def _column_info(db_path: str, table: str) -> dict:
    """Return {col_name: {type, pk}} for a table after init_db."""
    if db_path == ":memory:":
        # Use a temp file so init_db can open/close its own connection.
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            await init_db(path)
            async with aiosqlite.connect(path) as db:
                async with db.execute(f"PRAGMA table_info({table})") as cur:
                    rows = await cur.fetchall()
        finally:
            os.unlink(path)
    else:
        await init_db(db_path)
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(f"PRAGMA table_info({table})") as cur:
                rows = await cur.fetchall()
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    return {row[1]: {"type": row[2], "pk": row[5]} for row in rows}
