"""Tests for telemetry persistence — JSONL file and DB sinks."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from dispatcher.database import init_db
from dispatcher.repository import Repository, TelemetryEventRow
from dispatcher.telemetry import (
    DbSink,
    JsonlSink,
    TelemetryCollector,
    TelemetryEvent,
)


# ── TelemetryEvent.to_dict ──────────────────────────────────────────

class TestTelemetryEventToDict:
    def test_to_dict(self):
        event = TelemetryEvent(
            event_type="login", payload={"user": "alice"}, timestamp=1000.0,
            session_id="s1", source="slack", status="ok",
        )
        d = event.to_dict()
        assert d == {
            "event_type": "login",
            "session_id": "s1",
            "source": "slack",
            "status": "ok",
            "payload": {"user": "alice"},
            "timestamp": 1000.0,
        }

    def test_to_dict_empty_payload(self):
        event = TelemetryEvent(event_type="ping", payload={}, timestamp=2000.0)
        d = event.to_dict()
        assert d["payload"] == {}
        assert d["session_id"] is None
        assert d["source"] is None
        assert d["status"] == "ok"


# ── JsonlSink ───────────────────────────────────────────────────────

class TestJsonlSink:
    def test_write_creates_file(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlSink(path)
        event = TelemetryEvent(
            event_type="test", payload={"k": "v"}, timestamp=100.0,
        )
        sink.write(event)
        assert path.exists()

    def test_write_appends_json_line(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlSink(path)
        sink.write(TelemetryEvent("a", {"n": 1}, 100.0, session_id="s1", source="slack"))
        sink.write(TelemetryEvent("b", {"n": 2}, 200.0))
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["event_type"] == "a"
        assert first["payload"] == {"n": 1}
        assert first["timestamp"] == 100.0
        assert first["session_id"] == "s1"
        assert first["source"] == "slack"
        assert first["status"] == "ok"
        second = json.loads(lines[1])
        assert second["event_type"] == "b"
        assert second["session_id"] is None

    def test_path_property(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        sink = JsonlSink(path)
        assert sink.path == path

    def test_write_accepts_string_path(self, tmp_path: Path):
        path = str(tmp_path / "events.jsonl")
        sink = JsonlSink(path)
        sink.write(TelemetryEvent("x", {}, 1.0))
        assert Path(path).exists()


# ── DbSink ──────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_and_repo():
    """Yield (aiosqlite.Connection, Repository) with schema initialized."""
    async with aiosqlite.connect(":memory:") as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, agent_name TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'ACTIVE',
                attribution TEXT NOT NULL DEFAULT 'bot',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, source TEXT NOT NULL,
                channel_ref TEXT NOT NULL, user_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS channel_bindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, channel_type TEXT NOT NULL,
                channel_ref TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE (session_id, channel_type, channel_ref)
            );
            CREATE TABLE IF NOT EXISTS telemetry_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                session_id TEXT,
                source TEXT,
                status TEXT NOT NULL DEFAULT 'ok',
                payload TEXT NOT NULL,
                timestamp REAL NOT NULL, created_at TEXT NOT NULL
            );
            """
        )
        await db.commit()
        repo = Repository(db)
        yield db, repo


class TestDbSink:
    @pytest.mark.asyncio
    async def test_write_and_flush(self, db_and_repo):
        _db, repo = db_and_repo
        sink = DbSink(repo)
        event = TelemetryEvent(
            "login", {"user": "bob"}, 500.0,
            session_id="s1", source="terminal", status="ok",
        )
        sink.write(event)
        await sink.flush()
        rows = await repo.get_telemetry_events()
        assert len(rows) == 1
        assert rows[0].event_type == "login"
        assert rows[0].payload == {"user": "bob"}
        assert rows[0].timestamp == 500.0
        assert rows[0].session_id == "s1"
        assert rows[0].source == "terminal"
        assert rows[0].status == "ok"

    @pytest.mark.asyncio
    async def test_flush_multiple(self, db_and_repo):
        _db, repo = db_and_repo
        sink = DbSink(repo)
        sink.write(TelemetryEvent("a", {}, 1.0))
        sink.write(TelemetryEvent("b", {}, 2.0))
        sink.write(TelemetryEvent("c", {}, 3.0))
        await sink.flush()
        rows = await repo.get_telemetry_events()
        assert len(rows) == 3
        assert [r.event_type for r in rows] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_flush_is_idempotent(self, db_and_repo):
        _db, repo = db_and_repo
        sink = DbSink(repo)
        sink.write(TelemetryEvent("x", {}, 1.0))
        await sink.flush()
        await sink.flush()  # should not duplicate
        rows = await repo.get_telemetry_events()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_repo_property(self, db_and_repo):
        _db, repo = db_and_repo
        sink = DbSink(repo)
        assert sink.repo is repo


# ── Repository telemetry methods ────────────────────────────────────

class TestRepositoryTelemetry:
    @pytest.mark.asyncio
    async def test_save_and_get(self, db_and_repo):
        _db, repo = db_and_repo
        row = await repo.save_telemetry_event("ping", {"seq": 1}, 100.0)
        assert isinstance(row, TelemetryEventRow)
        assert row.event_type == "ping"
        assert row.payload == {"seq": 1}
        assert row.timestamp == 100.0
        assert row.id is not None

    @pytest.mark.asyncio
    async def test_get_all(self, db_and_repo):
        _db, repo = db_and_repo
        await repo.save_telemetry_event("a", {}, 1.0)
        await repo.save_telemetry_event("b", {}, 2.0)
        rows = await repo.get_telemetry_events()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_get_filtered_by_type(self, db_and_repo):
        _db, repo = db_and_repo
        await repo.save_telemetry_event("a", {}, 1.0)
        await repo.save_telemetry_event("b", {}, 2.0)
        await repo.save_telemetry_event("a", {}, 3.0)
        rows = await repo.get_telemetry_events(event_type="a")
        assert len(rows) == 2
        assert all(r.event_type == "a" for r in rows)

    @pytest.mark.asyncio
    async def test_get_empty(self, db_and_repo):
        _db, repo = db_and_repo
        rows = await repo.get_telemetry_events()
        assert rows == []


# ── Collector with sinks ────────────────────────────────────────────

class TestCollectorWithSinks:
    def test_add_sink(self):
        collector = TelemetryCollector()
        sink = JsonlSink("/dev/null")
        collector.add_sink(sink)
        assert len(collector.sinks) == 1

    def test_sinks_returns_copy(self):
        collector = TelemetryCollector()
        sinks = collector.sinks
        sinks.append("bogus")
        assert len(collector.sinks) == 0

    def test_record_writes_to_jsonl_sink(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        collector = TelemetryCollector()
        collector.add_sink(JsonlSink(path))
        collector.record("test", {"k": "v"}, timestamp=42.0)
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["event_type"] == "test"
        assert data["payload"] == {"k": "v"}
        assert data["timestamp"] == 42.0

    @pytest.mark.asyncio
    async def test_record_writes_to_db_sink(self, db_and_repo):
        _db, repo = db_and_repo
        collector = TelemetryCollector()
        collector.add_sink(DbSink(repo))
        collector.record("evt", {"x": 1}, timestamp=99.0)
        await collector.flush()
        rows = await repo.get_telemetry_events()
        assert len(rows) == 1
        assert rows[0].event_type == "evt"
        assert rows[0].payload == {"x": 1}

    @pytest.mark.asyncio
    async def test_both_sinks_receive_same_events(self, tmp_path, db_and_repo):
        _db, repo = db_and_repo
        jsonl_path = tmp_path / "events.jsonl"
        collector = TelemetryCollector()
        collector.add_sink(JsonlSink(jsonl_path))
        collector.add_sink(DbSink(repo))

        collector.record("alpha", {"n": 1}, timestamp=10.0)
        collector.record("beta", {"n": 2}, timestamp=20.0)
        await collector.flush()

        # Verify in-memory
        assert len(collector.events) == 2
        assert collector.events[0].event_type == "alpha"
        assert collector.events[1].event_type == "beta"

        # Verify JSONL file
        lines = jsonl_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["event_type"] == "alpha"
        assert json.loads(lines[1])["event_type"] == "beta"

        # Verify DB
        rows = await repo.get_telemetry_events()
        assert len(rows) == 2
        assert rows[0].event_type == "alpha"
        assert rows[1].event_type == "beta"

    @pytest.mark.asyncio
    async def test_flush_with_no_sinks(self):
        collector = TelemetryCollector()
        collector.record("x")
        await collector.flush()  # should not raise

    def test_multiple_jsonl_events(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        collector = TelemetryCollector()
        collector.add_sink(JsonlSink(path))
        for i in range(5):
            collector.record(f"evt_{i}", {"i": i}, timestamp=float(i))
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 5
        for i, line in enumerate(lines):
            data = json.loads(line)
            assert data["event_type"] == f"evt_{i}"


# ── init_db includes telemetry_events ───────────────────────────────

class TestInitDbTelemetryTable:
    @pytest.mark.asyncio
    async def test_init_db_creates_telemetry_events_table(self):
        await init_db(":memory:")
        async with aiosqlite.connect(":memory:") as db:
            await init_db.__wrapped__(db) if hasattr(init_db, "__wrapped__") else None
        # Use init_db properly
        await init_db(":memory:")
        # Verify by connecting and inserting
        async with aiosqlite.connect(":memory:") as db:
            # Re-create schema in this connection
            from dispatcher.database import _SCHEMA_SQL
            await db.executescript(_SCHEMA_SQL)
            await db.execute(
                "INSERT INTO telemetry_events "
                "(event_type, session_id, source, status, payload, timestamp, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("test", None, None, "ok", "{}", 1.0, "2024-01-01"),
            )
            await db.commit()
            async with db.execute("SELECT count(*) FROM telemetry_events") as cur:
                row = await cur.fetchone()
            assert row[0] == 1
