"""Tests for dispatcher.telemetry — in-memory telemetry event recording."""

from __future__ import annotations

import time

from dispatcher.telemetry import TelemetryCollector, TelemetryEvent


# ── TelemetryEvent tests ─────────────────────────────────────────────

class TestTelemetryEvent:
    def test_fields(self):
        event = TelemetryEvent(
            event_type="test", payload={"key": "val"}, timestamp=1000.0,
        )
        assert event.event_type == "test"
        assert event.payload == {"key": "val"}
        assert event.timestamp == 1000.0
        assert event.session_id is None
        assert event.source is None
        assert event.status == "ok"

    def test_fields_with_context(self):
        event = TelemetryEvent(
            event_type="test", payload={}, timestamp=1000.0,
            session_id="sess-1", source="slack", status="error",
        )
        assert event.session_id == "sess-1"
        assert event.source == "slack"
        assert event.status == "error"

    def test_frozen(self):
        event = TelemetryEvent(
            event_type="test", payload={}, timestamp=1000.0,
        )
        import pytest
        with pytest.raises(AttributeError):
            event.event_type = "changed"  # type: ignore[misc]


# ── TelemetryCollector tests ─────────────────────────────────────────

class TestTelemetryCollectorRecord:
    def test_record_stores_event(self):
        collector = TelemetryCollector()
        event = collector.record("login", {"user": "alice"})
        assert len(collector.events) == 1
        assert collector.events[0] is event
        assert event.event_type == "login"
        assert event.payload == {"user": "alice"}

    def test_record_auto_timestamp(self):
        collector = TelemetryCollector()
        before = time.time()
        event = collector.record("tick")
        after = time.time()
        assert before <= event.timestamp <= after

    def test_record_explicit_timestamp(self):
        collector = TelemetryCollector()
        event = collector.record("tick", timestamp=42.0)
        assert event.timestamp == 42.0

    def test_record_default_payload(self):
        collector = TelemetryCollector()
        event = collector.record("empty")
        assert event.payload == {}

    def test_multiple_events(self):
        collector = TelemetryCollector()
        collector.record("a")
        collector.record("b")
        collector.record("c")
        assert len(collector.events) == 3
        assert [e.event_type for e in collector.events] == ["a", "b", "c"]


class TestTelemetryCollectorClear:
    def test_clear_removes_all(self):
        collector = TelemetryCollector()
        collector.record("x")
        collector.record("y")
        assert len(collector.events) == 2
        collector.clear()
        assert len(collector.events) == 0

    def test_clear_allows_new_events(self):
        collector = TelemetryCollector()
        collector.record("before")
        collector.clear()
        collector.record("after")
        assert len(collector.events) == 1
        assert collector.events[0].event_type == "after"


class TestTelemetryCollectorEventsProperty:
    def test_events_returns_copy(self):
        collector = TelemetryCollector()
        collector.record("x")
        events = collector.events
        events.clear()
        # Internal list is not affected
        assert len(collector.events) == 1


# ── Default collector ────────────────────────────────────────────────

class TestDefaultCollector:
    def test_default_collector_is_instance(self):
        from dispatcher.telemetry import default_collector
        assert isinstance(default_collector, TelemetryCollector)
