"""Telemetry event recording with pluggable sinks.

Provides a :class:`TelemetryCollector` that records structured telemetry
events and fans them out to one or more sinks.  Three sink types are
available:

- **In-memory** (default) — events are stored in a list on the collector.
- **JSONL file** — each event is appended as a JSON line to a file.
- **DB** — events are stored in the ``telemetry_events`` table via the
  :class:`~dispatcher.repository.Repository`.

A module-level default collector is available for convenience, but callers
can also create their own instances.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from dispatcher.repository import Repository


@dataclass(frozen=True)
class TelemetryEvent:
    """A single telemetry event.

    Attributes:
        event_type: Category of the event (e.g. ``"fallback"``).
        session_id: The session this event belongs to, or ``None``.
        source: The channel source (e.g. ``"slack"``, ``"terminal"``), or ``None``.
        status: Outcome status (e.g. ``"ok"``, ``"error"``).  Defaults to ``"ok"``.
        payload: Arbitrary key-value data associated with the event.
        timestamp: Unix timestamp when the event was recorded.
    """

    event_type: str
    payload: dict[str, Any]
    timestamp: float
    session_id: str | None = None
    source: str | None = None
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict representation suitable for JSON serialization."""
        return {
            "event_type": self.event_type,
            "session_id": self.session_id,
            "source": self.source,
            "status": self.status,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


# ── Sink protocol and implementations ────────────────────────────────

class TelemetrySink(Protocol):
    """Protocol for telemetry event sinks."""

    def write(self, event: TelemetryEvent) -> None:
        """Write an event to the sink.

        For async sinks this should schedule the write; the protocol
        intentionally keeps ``write`` synchronous so the hot path
        (``TelemetryCollector.record``) stays sync.
        """
        ...  # pragma: no cover


class JsonlSink:
    """Appends each event as a JSON line to a file.

    Parameters
    ----------
    path:
        Filesystem path for the JSONL file.  Created on first write.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def write(self, event: TelemetryEvent) -> None:
        """Append the event as a single JSON line."""
        with self._path.open("a") as fh:
            fh.write(json.dumps(event.to_dict()) + "\n")


class DbSink:
    """Stores events in the ``telemetry_events`` DB table.

    Because the repository is async and ``write`` is sync, this sink
    schedules the DB insert using the collector's event loop.  The
    actual insert runs as a fire-and-forget coroutine.

    Parameters
    ----------
    repo:
        A :class:`~dispatcher.repository.Repository` instance.
    """

    def __init__(self, repo: Repository) -> None:
        self._repo = repo
        self._pending: list[TelemetryEvent] = []

    @property
    def repo(self) -> Repository:
        return self._repo

    def write(self, event: TelemetryEvent) -> None:
        """Schedule the event for async DB storage.

        The event is added to a pending list.  Call :meth:`flush` from
        an async context to persist pending events.
        """
        self._pending.append(event)

    async def flush(self) -> None:
        """Persist all pending events to the database."""
        while self._pending:
            event = self._pending.pop(0)
            await self._repo.save_telemetry_event(
                event_type=event.event_type,
                payload=event.payload,
                timestamp=event.timestamp,
                session_id=event.session_id,
                source=event.source,
                status=event.status,
            )


# ── Collector ────────────────────────────────────────────────────────

class TelemetryCollector:
    """Collects telemetry events with pluggable sinks.

    Events are always stored in-memory.  Additional sinks (JSONL file,
    database) can be registered via :meth:`add_sink`.  Use :meth:`clear`
    to reset the in-memory event list (sinks are not affected).
    """

    def __init__(self) -> None:
        self._events: list[TelemetryEvent] = []
        self._sinks: list[Any] = []

    @property
    def events(self) -> list[TelemetryEvent]:
        """Return the list of recorded events (read-only copy)."""
        return list(self._events)

    @property
    def sinks(self) -> list[Any]:
        """Return the list of registered sinks (read-only copy)."""
        return list(self._sinks)

    def add_sink(self, sink: Any) -> None:
        """Register an additional sink to receive events."""
        self._sinks.append(sink)

    def record(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        source: str | None = None,
        status: str = "ok",
        timestamp: float | None = None,
    ) -> TelemetryEvent:
        """Record a telemetry event and return it.

        The event is stored in-memory and written to all registered sinks.

        Parameters
        ----------
        event_type:
            Category string for the event.
        payload:
            Optional dict of event-specific data.
        session_id:
            Session ID the event relates to, if any.
        source:
            Channel source (e.g. ``"slack"``), if known.
        status:
            Outcome status.  Defaults to ``"ok"``.
        timestamp:
            Unix timestamp.  Defaults to ``time.time()``.
        """
        event = TelemetryEvent(
            event_type=event_type,
            payload=payload or {},
            timestamp=timestamp if timestamp is not None else time.time(),
            session_id=session_id,
            source=source,
            status=status,
        )
        self._events.append(event)
        for sink in self._sinks:
            sink.write(event)
        return event

    async def flush(self) -> None:
        """Flush all async sinks (e.g. :class:`DbSink`)."""
        for sink in self._sinks:
            if hasattr(sink, "flush"):
                await sink.flush()

    def clear(self) -> None:
        """Remove all recorded events."""
        self._events.clear()


# Module-level default collector for convenience
default_collector = TelemetryCollector()
