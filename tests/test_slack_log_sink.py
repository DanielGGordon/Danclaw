"""Tests for SlackLogSink telemetry sink."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from dispatcher.telemetry import (
    SlackLogSink,
    TelemetryCollector,
    TelemetryEvent,
)


# ── Helpers ───────────────────────────────────────────────────────────

def _make_client() -> MagicMock:
    """Return a mock Slack WebClient."""
    return MagicMock()


def _make_sink(client: MagicMock | None = None, channel: str = "C0123456789") -> SlackLogSink:
    return SlackLogSink(client or _make_client(), channel)


# ── Basic properties ──────────────────────────────────────────────────

class TestSlackLogSinkProperties:
    def test_client_property(self):
        client = _make_client()
        sink = SlackLogSink(client, "C1")
        assert sink.client is client

    def test_channel_property(self):
        sink = SlackLogSink(_make_client(), "C42")
        assert sink.channel == "C42"


# ── Session completion posts ──────────────────────────────────────────

class TestSlackLogSinkSessionComplete:
    def test_posts_on_session_done(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="session_state_changed",
            payload={"new_state": "DONE"},
            timestamp=1000.0,
            session_id="sess-1",
        )
        sink.write(event)
        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C0123456789"
        assert "sess-1" in call_kwargs["text"]
        assert "DONE" in call_kwargs["text"]

    def test_posts_on_session_error_state(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="session_state_changed",
            payload={"new_state": "ERROR"},
            timestamp=1000.0,
            session_id="sess-2",
        )
        sink.write(event)
        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args.kwargs
        assert "sess-2" in call_kwargs["text"]
        assert "ERROR" in call_kwargs["text"]


# ── Error event posts ────────────────────────────────────────────────

class TestSlackLogSinkErrorEvent:
    def test_posts_on_error_event(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="error",
            payload={"error": "executor crashed"},
            timestamp=1000.0,
            session_id="sess-3",
        )
        sink.write(event)
        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args.kwargs
        assert "sess-3" in call_kwargs["text"]
        assert "executor crashed" in call_kwargs["text"]

    def test_error_event_without_session_id(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="error",
            payload={"error": "startup failure"},
            timestamp=1000.0,
        )
        sink.write(event)
        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args.kwargs
        assert "unknown" in call_kwargs["text"]
        assert "startup failure" in call_kwargs["text"]

    def test_error_event_without_detail(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="error",
            payload={},
            timestamp=1000.0,
            session_id="sess-4",
        )
        sink.write(event)
        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args.kwargs
        assert "no details" in call_kwargs["text"]


# ── No message on other events ───────────────────────────────────────

class TestSlackLogSinkIgnoredEvents:
    def test_ignores_session_state_active(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="session_state_changed",
            payload={"new_state": "ACTIVE"},
            timestamp=1000.0,
            session_id="sess-5",
        )
        sink.write(event)
        client.chat_postMessage.assert_not_called()

    def test_ignores_session_state_waiting(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="session_state_changed",
            payload={"new_state": "WAITING_FOR_HUMAN"},
            timestamp=1000.0,
            session_id="sess-6",
        )
        sink.write(event)
        client.chat_postMessage.assert_not_called()

    def test_ignores_message_received(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="message_received",
            payload={"content": "hello"},
            timestamp=1000.0,
            session_id="sess-7",
        )
        sink.write(event)
        client.chat_postMessage.assert_not_called()

    def test_ignores_executor_invoked(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="executor_invoked",
            payload={},
            timestamp=1000.0,
        )
        sink.write(event)
        client.chat_postMessage.assert_not_called()

    def test_ignores_fallback(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="fallback",
            payload={"failed_backends": ["claude"], "succeeded_backend": "codex"},
            timestamp=1000.0,
        )
        sink.write(event)
        client.chat_postMessage.assert_not_called()


# ── Message format ───────────────────────────────────────────────────

class TestSlackLogSinkMessageFormat:
    def test_done_format_includes_session_id_and_status(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="session_state_changed",
            payload={"new_state": "DONE"},
            timestamp=1000.0,
            session_id="abc-123",
        )
        sink.write(event)
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "abc-123" in text
        assert "DONE" in text

    def test_error_state_format_includes_session_id_and_status(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="session_state_changed",
            payload={"new_state": "ERROR"},
            timestamp=1000.0,
            session_id="def-456",
        )
        sink.write(event)
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "def-456" in text
        assert "ERROR" in text

    def test_session_done_unknown_session(self):
        client = _make_client()
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="session_state_changed",
            payload={"new_state": "DONE"},
            timestamp=1000.0,
        )
        sink.write(event)
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "unknown" in text


# ── Slack API error handling ─────────────────────────────────────────

class TestSlackLogSinkApiError:
    def test_slack_api_error_is_caught(self):
        client = _make_client()
        client.chat_postMessage.side_effect = SlackApiError(
            message="channel_not_found",
            response=MagicMock(status_code=404, data={"ok": False, "error": "channel_not_found"}),
        )
        sink = _make_sink(client)
        event = TelemetryEvent(
            event_type="session_state_changed",
            payload={"new_state": "DONE"},
            timestamp=1000.0,
            session_id="sess-err",
        )
        # Should not raise
        sink.write(event)


# ── Integration with TelemetryCollector ──────────────────────────────

class TestSlackLogSinkWithCollector:
    def test_sink_added_to_collector(self):
        client = _make_client()
        sink = _make_sink(client)
        collector = TelemetryCollector()
        collector.add_sink(sink)
        assert len(collector.sinks) == 1

    def test_collector_record_triggers_slack_post(self):
        client = _make_client()
        sink = _make_sink(client)
        collector = TelemetryCollector()
        collector.add_sink(sink)
        collector.record(
            "session_state_changed",
            {"new_state": "DONE"},
            session_id="sess-coll",
            timestamp=42.0,
        )
        client.chat_postMessage.assert_called_once()
        text = client.chat_postMessage.call_args.kwargs["text"]
        assert "sess-coll" in text

    def test_collector_record_ignored_event_no_post(self):
        client = _make_client()
        sink = _make_sink(client)
        collector = TelemetryCollector()
        collector.add_sink(sink)
        collector.record(
            "message_received",
            {"content": "hi"},
            session_id="sess-ign",
            timestamp=42.0,
        )
        client.chat_postMessage.assert_not_called()
