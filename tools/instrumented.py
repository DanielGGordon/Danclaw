"""Telemetry-instrumented wrappers for Obsidian vault tool functions.

Each wrapper calls the underlying tool function, records a
``"tool_execution"`` telemetry event via a :class:`TelemetryCollector`,
and re-raises any exceptions after recording failure events.

The payload for each event includes:

- ``tool``: the tool name (e.g. ``"obsidian_read"``)
- ``args``: a dict of the arguments passed to the tool function
- ``success``: boolean indicating whether the call succeeded
- ``duration``: wall-clock seconds the call took
- ``error``: error message string (only present on failure)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from dispatcher.telemetry import TelemetryCollector
from tools.obsidian_read import read_file as _read_file
from tools.obsidian_search import search_files as _search_files
from tools.obsidian_write import write_file as _write_file


def _record_event(
    collector: TelemetryCollector,
    tool: str,
    args: dict[str, Any],
    *,
    success: bool,
    duration: float,
    error: str | None = None,
) -> None:
    """Record a tool_execution telemetry event."""
    payload: dict[str, Any] = {
        "tool": tool,
        "args": args,
        "success": success,
        "duration": duration,
    }
    if error is not None:
        payload["error"] = error
    collector.record("tool_execution", payload)


def read_file(
    vault: str | Path,
    file_path: str,
    *,
    telemetry: TelemetryCollector,
) -> str:
    """Read a file from an Obsidian vault with telemetry.

    Wraps :func:`tools.obsidian_read.read_file`, recording a
    ``tool_execution`` event on both success and failure.
    """
    args = {"vault": str(vault), "file_path": file_path}
    start = time.monotonic()
    try:
        result = _read_file(vault, file_path)
    except Exception as exc:
        duration = time.monotonic() - start
        _record_event(
            telemetry, "obsidian_read", args,
            success=False, duration=duration, error=str(exc),
        )
        raise
    duration = time.monotonic() - start
    _record_event(
        telemetry, "obsidian_read", args,
        success=True, duration=duration,
    )
    return result


def write_file(
    vault: str | Path,
    file_path: str,
    content: str,
    *,
    telemetry: TelemetryCollector,
) -> str:
    """Create or update a file in an Obsidian vault with telemetry.

    Wraps :func:`tools.obsidian_write.write_file`, recording a
    ``tool_execution`` event on both success and failure.
    """
    args = {"vault": str(vault), "file_path": file_path, "content": content}
    start = time.monotonic()
    try:
        result = _write_file(vault, file_path, content)
    except Exception as exc:
        duration = time.monotonic() - start
        _record_event(
            telemetry, "obsidian_write", args,
            success=False, duration=duration, error=str(exc),
        )
        raise
    duration = time.monotonic() - start
    _record_event(
        telemetry, "obsidian_write", args,
        success=True, duration=duration,
    )
    return result


def search_files(
    vault: str | Path,
    *,
    name: str | None = None,
    query: str | None = None,
    telemetry: TelemetryCollector,
) -> list[str]:
    """Search for files in an Obsidian vault with telemetry.

    Wraps :func:`tools.obsidian_search.search_files`, recording a
    ``tool_execution`` event on both success and failure.
    """
    args: dict[str, Any] = {"vault": str(vault)}
    if name is not None:
        args["name"] = name
    if query is not None:
        args["query"] = query
    start = time.monotonic()
    try:
        result = _search_files(vault, name=name, query=query)
    except Exception as exc:
        duration = time.monotonic() - start
        _record_event(
            telemetry, "obsidian_search", args,
            success=False, duration=duration, error=str(exc),
        )
        raise
    duration = time.monotonic() - start
    _record_event(
        telemetry, "obsidian_search", args,
        success=True, duration=duration,
    )
    return result
