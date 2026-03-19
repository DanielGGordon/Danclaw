"""Structured JSON logging setup for DanClaw components.

Every log line emitted through Python's standard :mod:`logging` module is
formatted as a single JSON object with at least these fields:

- ``timestamp`` – ISO-8601 UTC timestamp.
- ``level`` – Log level name (``DEBUG``, ``INFO``, …).
- ``logger`` – Logger name.
- ``message`` – The formatted log message.

Extra fields supplied via the ``extra`` dict on individual log calls are
merged into the JSON object, making it easy to attach structured context
(e.g. ``session_id``, ``user_id``).

The output is written to *stderr* so it appears in ``docker logs`` and
``journalctl`` without interfering with stdout-based protocols.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON objects.

    Required fields (``timestamp``, ``level``, ``logger``, ``message``) are
    always present.  Any keys passed via ``extra`` on the log call are merged
    in as additional context fields, excluding internal :class:`logging.LogRecord`
    attributes.
    """

    # Attributes that belong to LogRecord internals and should *not* be
    # forwarded as context fields.
    _RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
        "message",
        "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        """Return a JSON-encoded string for *record*."""
        record.message = record.getMessage()

        entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
        }

        if record.exc_info and record.exc_info[0] is not None:
            entry["traceback"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack_info"] = self.formatStack(record.stack_info)

        # Merge extra context fields.
        for key, value in record.__dict__.items():
            if key not in self._RESERVED:
                entry[key] = value

        return json.dumps(entry, default=str)


def setup_logging(level: int | str = logging.INFO) -> None:
    """Configure the root logger to emit structured JSON to stderr.

    This function is idempotent: calling it more than once replaces any
    previously installed handler added by this function rather than
    adding duplicates.

    Parameters
    ----------
    level:
        The root log level.  Accepts an ``int`` (e.g. ``logging.DEBUG``)
        or a level name string (e.g. ``"INFO"``).
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any handler we previously installed (tagged via attribute).
    for handler in list(root.handlers):
        if getattr(handler, "_danclaw_json", False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JSONFormatter())
    handler._danclaw_json = True  # type: ignore[attr-defined]
    root.addHandler(handler)
