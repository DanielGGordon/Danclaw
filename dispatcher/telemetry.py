"""In-memory telemetry event recording.

Provides a simple event collector that stores structured telemetry events
in a list.  Events are recorded as :class:`TelemetryEvent` frozen
dataclasses.  A module-level default collector is available for
convenience, but callers can also create their own instances.

Phase 10 will add persistence (JSONL file and SQLite storage).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TelemetryEvent:
    """A single telemetry event.

    Attributes:
        event_type: Category of the event (e.g. ``"fallback"``).
        payload: Arbitrary key-value data associated with the event.
        timestamp: Unix timestamp when the event was recorded.
    """

    event_type: str
    payload: dict[str, Any]
    timestamp: float


class TelemetryCollector:
    """Collects telemetry events in an in-memory list.

    Events can be recorded via :meth:`record` and inspected via
    :attr:`events`.  Use :meth:`clear` to reset the event list.
    """

    def __init__(self) -> None:
        self._events: list[TelemetryEvent] = []

    @property
    def events(self) -> list[TelemetryEvent]:
        """Return the list of recorded events (read-only copy)."""
        return list(self._events)

    def record(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timestamp: float | None = None,
    ) -> TelemetryEvent:
        """Record a telemetry event and return it.

        Parameters
        ----------
        event_type:
            Category string for the event.
        payload:
            Optional dict of event-specific data.
        timestamp:
            Unix timestamp.  Defaults to ``time.time()``.
        """
        event = TelemetryEvent(
            event_type=event_type,
            payload=payload or {},
            timestamp=timestamp if timestamp is not None else time.time(),
        )
        self._events.append(event)
        return event

    def clear(self) -> None:
        """Remove all recorded events."""
        self._events.clear()


# Module-level default collector for convenience
default_collector = TelemetryCollector()
