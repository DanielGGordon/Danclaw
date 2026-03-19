"""Tests for the logging_config module — structured JSON logging."""

from __future__ import annotations

import json
import logging

import pytest

from logging_config import setup_logging
from logging_config.setup import JSONFormatter


# ---------------------------------------------------------------------------
# JSONFormatter
# ---------------------------------------------------------------------------


class TestJSONFormatter:
    """Tests for the JSONFormatter class."""

    def _make_record(
        self,
        msg: str = "hello",
        level: int = logging.INFO,
        name: str = "test",
        **extra: object,
    ) -> logging.LogRecord:
        record = logging.LogRecord(
            name=name,
            level=level,
            pathname="test.py",
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        return record

    def test_output_is_valid_json(self) -> None:
        formatter = JSONFormatter()
        record = self._make_record("test message")
        output = formatter.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_required_fields_present(self) -> None:
        formatter = JSONFormatter()
        record = self._make_record("test message", level=logging.WARNING, name="mylogger")
        parsed = json.loads(formatter.format(record))

        assert "timestamp" in parsed
        assert parsed["level"] == "WARNING"
        assert parsed["logger"] == "mylogger"
        assert parsed["message"] == "test message"

    def test_timestamp_is_iso8601(self) -> None:
        formatter = JSONFormatter()
        record = self._make_record()
        parsed = json.loads(formatter.format(record))

        # ISO-8601 timestamps contain 'T' and end with timezone info
        ts = parsed["timestamp"]
        assert "T" in ts
        assert "+" in ts or ts.endswith("Z")

    def test_extra_fields_included(self) -> None:
        formatter = JSONFormatter()
        record = self._make_record("hi", session_id="abc-123", user_id="dan")
        parsed = json.loads(formatter.format(record))

        assert parsed["session_id"] == "abc-123"
        assert parsed["user_id"] == "dan"

    def test_internal_logrecord_fields_excluded(self) -> None:
        """LogRecord internals like 'pathname', 'lineno' should not leak."""
        formatter = JSONFormatter()
        record = self._make_record("hi")
        parsed = json.loads(formatter.format(record))

        assert "pathname" not in parsed
        assert "lineno" not in parsed
        assert "args" not in parsed
        assert "exc_info" not in parsed

    def test_format_args_interpolation(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="count=%d",
            args=(42,),
            exc_info=None,
        )
        parsed = json.loads(formatter.format(record))
        assert parsed["message"] == "count=42"

    def test_single_line_output(self) -> None:
        formatter = JSONFormatter()
        record = self._make_record("line one\nline two")
        output = formatter.format(record)
        # JSON encodes newlines as \\n inside the string, so the output
        # itself should be a single line.
        assert "\n" not in output

    def test_non_serializable_extra_uses_str(self) -> None:
        """Non-JSON-serializable extras should fall back to str()."""
        formatter = JSONFormatter()
        record = self._make_record("hi", custom_obj=object())
        # Should not raise
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "custom_obj" in parsed


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


class TestSetupLogging:
    """Tests for the setup_logging function."""

    def _cleanup_root_handlers(self) -> None:
        """Remove DanClaw JSON handlers from root logger."""
        root = logging.getLogger()
        for h in list(root.handlers):
            if getattr(h, "_danclaw_json", False):
                root.removeHandler(h)

    def setup_method(self) -> None:
        self._cleanup_root_handlers()

    def teardown_method(self) -> None:
        self._cleanup_root_handlers()

    def test_adds_handler_to_root_logger(self) -> None:
        setup_logging()
        root = logging.getLogger()
        danclaw_handlers = [
            h for h in root.handlers if getattr(h, "_danclaw_json", False)
        ]
        assert len(danclaw_handlers) == 1

    def test_handler_uses_json_formatter(self) -> None:
        setup_logging()
        root = logging.getLogger()
        danclaw_handlers = [
            h for h in root.handlers if getattr(h, "_danclaw_json", False)
        ]
        assert isinstance(danclaw_handlers[0].formatter, JSONFormatter)

    def test_idempotent_no_duplicate_handlers(self) -> None:
        setup_logging()
        setup_logging()
        setup_logging()
        root = logging.getLogger()
        danclaw_handlers = [
            h for h in root.handlers if getattr(h, "_danclaw_json", False)
        ]
        assert len(danclaw_handlers) == 1

    def test_accepts_string_level(self) -> None:
        setup_logging(level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_accepts_int_level(self) -> None:
        setup_logging(level=logging.WARNING)
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_log_output_is_valid_json(self, capsys) -> None:
        setup_logging(level=logging.INFO)
        logger = logging.getLogger("test.output")
        logger.info("structured test")

        captured = capsys.readouterr()
        # Output goes to stderr
        lines = [l for l in captured.err.strip().splitlines() if l.strip()]
        assert len(lines) >= 1
        parsed = json.loads(lines[-1])
        assert parsed["message"] == "structured test"
        assert parsed["logger"] == "test.output"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed

    def test_extra_context_in_output(self, capsys) -> None:
        setup_logging(level=logging.INFO)
        logger = logging.getLogger("test.extra")
        logger.info("with context", extra={"request_id": "r-999"})

        captured = capsys.readouterr()
        lines = [l for l in captured.err.strip().splitlines() if l.strip()]
        parsed = json.loads(lines[-1])
        assert parsed["request_id"] == "r-999"


# ---------------------------------------------------------------------------
# Integration: entry-point imports
# ---------------------------------------------------------------------------


class TestEntryPointIntegration:
    """Verify each entry point imports and calls setup_logging."""

    def test_dispatcher_main_uses_setup_logging(self) -> None:
        """dispatcher.__main__._setup_logging delegates to setup_logging."""
        from dispatcher.__main__ import _setup_logging

        # After calling _setup_logging, a DanClaw JSON handler should exist.
        root = logging.getLogger()
        # Clean up first
        for h in list(root.handlers):
            if getattr(h, "_danclaw_json", False):
                root.removeHandler(h)

        _setup_logging()

        danclaw_handlers = [
            h for h in root.handlers if getattr(h, "_danclaw_json", False)
        ]
        assert len(danclaw_handlers) == 1
        assert isinstance(danclaw_handlers[0].formatter, JSONFormatter)

        # Cleanup
        for h in list(root.handlers):
            if getattr(h, "_danclaw_json", False):
                root.removeHandler(h)

    def test_cli_agent_imports_setup_logging(self) -> None:
        """cli.agent imports setup_logging from logging_config."""
        import cli.agent as agent_mod

        assert hasattr(agent_mod, "setup_logging")
