"""Tests for telemetry query methods on the Repository."""

from __future__ import annotations

import pytest
import pytest_asyncio

import aiosqlite

from dispatcher.repository import Repository, TelemetryEventRow


@pytest_asyncio.fixture
async def repo():
    """Yield a Repository with schema initialized and sample telemetry data."""
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
        yield Repository(db)


@pytest_asyncio.fixture
async def seeded_repo(repo):
    """Repo with a variety of telemetry events for query testing."""
    # Events span timestamps 100..600 with various attributes
    await repo.save_telemetry_event("message_received", {"n": 1}, 100.0, session_id="s1", source="slack", status="ok")
    await repo.save_telemetry_event("executor_invoked", {"n": 2}, 200.0, session_id="s1", source="slack", status="ok")
    await repo.save_telemetry_event("error", {"n": 3}, 300.0, session_id="s1", source="slack", status="error")
    await repo.save_telemetry_event("message_received", {"n": 4}, 400.0, session_id="s2", source="terminal", status="ok")
    await repo.save_telemetry_event("executor_invoked", {"n": 5}, 500.0, session_id="s2", source="terminal", status="ok")
    await repo.save_telemetry_event("session_state_changed", {"n": 6}, 600.0, session_id="s2", source="terminal", status="ok")
    return repo


# ── get_telemetry_event (by ID) ──────────────────────────────────────


class TestGetTelemetryEvent:
    @pytest.mark.asyncio
    async def test_returns_event_by_id(self, repo):
        saved = await repo.save_telemetry_event("ping", {"k": "v"}, 42.0, session_id="s1")
        result = await repo.get_telemetry_event(saved.id)
        assert result is not None
        assert result.id == saved.id
        assert result.event_type == "ping"
        assert result.payload == {"k": "v"}
        assert result.timestamp == 42.0
        assert result.session_id == "s1"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_id(self, repo):
        result = await repo.get_telemetry_event(99999)
        assert result is None

    @pytest.mark.asyncio
    async def test_deserializes_payload(self, repo):
        saved = await repo.save_telemetry_event("x", {"nested": {"a": [1, 2]}}, 1.0)
        result = await repo.get_telemetry_event(saved.id)
        assert result.payload == {"nested": {"a": [1, 2]}}


# ── query_telemetry_events ───────────────────────────────────────────


class TestQueryTelemetryEvents:
    @pytest.mark.asyncio
    async def test_no_filters_returns_all(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events()
        assert len(rows) == 6

    @pytest.mark.asyncio
    async def test_filter_by_event_type(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(event_type="message_received")
        assert len(rows) == 2
        assert all(r.event_type == "message_received" for r in rows)

    @pytest.mark.asyncio
    async def test_filter_by_session_id(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(session_id="s1")
        assert len(rows) == 3
        assert all(r.session_id == "s1" for r in rows)

    @pytest.mark.asyncio
    async def test_filter_by_source(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(source="terminal")
        assert len(rows) == 3
        assert all(r.source == "terminal" for r in rows)

    @pytest.mark.asyncio
    async def test_filter_by_status(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(status="error")
        assert len(rows) == 1
        assert rows[0].event_type == "error"

    @pytest.mark.asyncio
    async def test_filter_by_since(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(since=400.0)
        assert len(rows) == 3
        assert all(r.timestamp >= 400.0 for r in rows)

    @pytest.mark.asyncio
    async def test_filter_by_until(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(until=200.0)
        assert len(rows) == 2
        assert all(r.timestamp <= 200.0 for r in rows)

    @pytest.mark.asyncio
    async def test_filter_by_time_range(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(since=200.0, until=500.0)
        assert len(rows) == 4
        assert all(200.0 <= r.timestamp <= 500.0 for r in rows)

    @pytest.mark.asyncio
    async def test_combined_filters(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(
            event_type="executor_invoked", source="terminal",
        )
        assert len(rows) == 1
        assert rows[0].session_id == "s2"
        assert rows[0].timestamp == 500.0

    @pytest.mark.asyncio
    async def test_limit(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(limit=3)
        assert len(rows) == 3
        # Should be the first 3 by timestamp asc
        assert [r.timestamp for r in rows] == [100.0, 200.0, 300.0]

    @pytest.mark.asyncio
    async def test_offset(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(limit=2, offset=2)
        assert len(rows) == 2
        assert [r.timestamp for r in rows] == [300.0, 400.0]

    @pytest.mark.asyncio
    async def test_offset_without_limit(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(offset=4)
        assert len(rows) == 2
        assert [r.timestamp for r in rows] == [500.0, 600.0]

    @pytest.mark.asyncio
    async def test_order_desc(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(order="desc")
        assert len(rows) == 6
        timestamps = [r.timestamp for r in rows]
        assert timestamps == sorted(timestamps, reverse=True)

    @pytest.mark.asyncio
    async def test_order_desc_with_limit(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(order="desc", limit=2)
        assert len(rows) == 2
        assert [r.timestamp for r in rows] == [600.0, 500.0]

    @pytest.mark.asyncio
    async def test_order_asc_is_default(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events()
        timestamps = [r.timestamp for r in rows]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_invalid_order_raises(self, seeded_repo):
        with pytest.raises(ValueError, match="Invalid order"):
            await seeded_repo.query_telemetry_events(order="random")

    @pytest.mark.asyncio
    async def test_empty_result(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(event_type="nonexistent")
        assert rows == []

    @pytest.mark.asyncio
    async def test_empty_db(self, repo):
        rows = await repo.query_telemetry_events()
        assert rows == []

    @pytest.mark.asyncio
    async def test_returns_telemetry_event_rows(self, seeded_repo):
        rows = await seeded_repo.query_telemetry_events(limit=1)
        assert isinstance(rows[0], TelemetryEventRow)
        assert isinstance(rows[0].payload, dict)

    @pytest.mark.asyncio
    async def test_pagination_covers_all_rows(self, seeded_repo):
        """Paginating through all rows returns the same data as no pagination."""
        all_rows = await seeded_repo.query_telemetry_events()
        page1 = await seeded_repo.query_telemetry_events(limit=3, offset=0)
        page2 = await seeded_repo.query_telemetry_events(limit=3, offset=3)
        assert [r.id for r in page1 + page2] == [r.id for r in all_rows]


# ── count_telemetry_events ───────────────────────────────────────────


class TestCountTelemetryEvents:
    @pytest.mark.asyncio
    async def test_count_all(self, seeded_repo):
        count = await seeded_repo.count_telemetry_events()
        assert count == 6

    @pytest.mark.asyncio
    async def test_count_by_event_type(self, seeded_repo):
        count = await seeded_repo.count_telemetry_events(event_type="message_received")
        assert count == 2

    @pytest.mark.asyncio
    async def test_count_by_session_id(self, seeded_repo):
        count = await seeded_repo.count_telemetry_events(session_id="s2")
        assert count == 3

    @pytest.mark.asyncio
    async def test_count_by_source(self, seeded_repo):
        count = await seeded_repo.count_telemetry_events(source="slack")
        assert count == 3

    @pytest.mark.asyncio
    async def test_count_by_status(self, seeded_repo):
        count = await seeded_repo.count_telemetry_events(status="error")
        assert count == 1

    @pytest.mark.asyncio
    async def test_count_by_time_range(self, seeded_repo):
        count = await seeded_repo.count_telemetry_events(since=200.0, until=500.0)
        assert count == 4

    @pytest.mark.asyncio
    async def test_count_combined_filters(self, seeded_repo):
        count = await seeded_repo.count_telemetry_events(
            event_type="executor_invoked", source="terminal",
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_count_no_matches(self, seeded_repo):
        count = await seeded_repo.count_telemetry_events(event_type="nonexistent")
        assert count == 0

    @pytest.mark.asyncio
    async def test_count_empty_db(self, repo):
        count = await repo.count_telemetry_events()
        assert count == 0

    @pytest.mark.asyncio
    async def test_count_matches_query_length(self, seeded_repo):
        """count and query with same filters should agree."""
        filters = {"event_type": "message_received", "source": "slack"}
        count = await seeded_repo.count_telemetry_events(**filters)
        rows = await seeded_repo.query_telemetry_events(**filters)
        assert count == len(rows)


# ── get_distinct_event_types ─────────────────────────────────────────


class TestGetDistinctEventTypes:
    @pytest.mark.asyncio
    async def test_returns_sorted_types(self, seeded_repo):
        types = await seeded_repo.get_distinct_event_types()
        assert types == ["error", "executor_invoked", "message_received", "session_state_changed"]

    @pytest.mark.asyncio
    async def test_empty_db(self, repo):
        types = await repo.get_distinct_event_types()
        assert types == []


# ── get_distinct_sources ─────────────────────────────────────────────


class TestGetDistinctSources:
    @pytest.mark.asyncio
    async def test_returns_sorted_sources(self, seeded_repo):
        sources = await seeded_repo.get_distinct_sources()
        assert sources == ["slack", "terminal"]

    @pytest.mark.asyncio
    async def test_excludes_null_source(self, repo):
        await repo.save_telemetry_event("a", {}, 1.0, source=None)
        await repo.save_telemetry_event("b", {}, 2.0, source="slack")
        sources = await repo.get_distinct_sources()
        assert sources == ["slack"]

    @pytest.mark.asyncio
    async def test_empty_db(self, repo):
        sources = await repo.get_distinct_sources()
        assert sources == []
