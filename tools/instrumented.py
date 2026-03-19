"""Telemetry-instrumented wrappers for tool functions.

Each wrapper calls the underlying tool function, records a
``"tool_execution"`` telemetry event via a :class:`TelemetryCollector`,
and re-raises any exceptions after recording failure events.

The payload for each event includes:

- ``tool``: the tool name (e.g. ``"obsidian_read"``, ``"git_add"``)
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
from tools.deploy import deploy as _deploy
from tools.trigger_deploy import trigger_deploy as _trigger_deploy
from tools.git_ops import git_add as _git_add
from tools.git_ops import git_commit as _git_commit
from tools.git_ops import git_push as _git_push
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


# ── Deploy ─────────────────────────────────────────────────────────────


def deploy(
    *,
    cwd: str | Path,
    rebuild: bool = True,
    telemetry: TelemetryCollector,
) -> str:
    """Execute deploy sequence with telemetry.

    Wraps :func:`tools.deploy.deploy`, recording a
    ``tool_execution`` event on both success and failure.
    """
    args: dict[str, Any] = {"cwd": str(cwd), "rebuild": rebuild}
    start = time.monotonic()
    try:
        result = _deploy(cwd=cwd, rebuild=rebuild)
    except Exception as exc:
        duration = time.monotonic() - start
        _record_event(
            telemetry, "deploy", args,
            success=False, duration=duration, error=str(exc),
        )
        raise
    duration = time.monotonic() - start
    _record_event(
        telemetry, "deploy", args,
        success=True, duration=duration,
    )
    return result


def trigger_deploy(
    *,
    cwd: str | Path | None = None,
    rebuild: bool = True,
    telemetry: TelemetryCollector,
) -> str:
    """Trigger a deploy via agent tool entry point with telemetry.

    Wraps :func:`tools.trigger_deploy.trigger_deploy`, recording a
    ``tool_execution`` event on both success and failure.
    """
    args: dict[str, Any] = {"rebuild": rebuild}
    if cwd is not None:
        args["cwd"] = str(cwd)
    start = time.monotonic()
    try:
        result = _trigger_deploy(cwd=cwd, rebuild=rebuild)
    except Exception as exc:
        duration = time.monotonic() - start
        _record_event(
            telemetry, "trigger_deploy", args,
            success=False, duration=duration, error=str(exc),
        )
        raise
    duration = time.monotonic() - start
    _record_event(
        telemetry, "trigger_deploy", args,
        success=True, duration=duration,
    )
    return result


# ── Git operations ─────────────────────────────────────────────────────


def git_add(
    paths: list[str],
    *,
    cwd: str | Path,
    telemetry: TelemetryCollector,
) -> str:
    """Stage files for commit with telemetry.

    Wraps :func:`tools.git_ops.git_add`, recording a
    ``tool_execution`` event on both success and failure.
    """
    args: dict[str, Any] = {"paths": paths, "cwd": str(cwd)}
    start = time.monotonic()
    try:
        result = _git_add(paths, cwd=cwd)
    except Exception as exc:
        duration = time.monotonic() - start
        _record_event(
            telemetry, "git_add", args,
            success=False, duration=duration, error=str(exc),
        )
        raise
    duration = time.monotonic() - start
    _record_event(
        telemetry, "git_add", args,
        success=True, duration=duration,
    )
    return result


def git_commit(
    message: str,
    *,
    cwd: str | Path,
    telemetry: TelemetryCollector,
) -> str:
    """Create a commit with telemetry.

    Wraps :func:`tools.git_ops.git_commit`, recording a
    ``tool_execution`` event on both success and failure.
    """
    args: dict[str, Any] = {"message": message, "cwd": str(cwd)}
    start = time.monotonic()
    try:
        result = _git_commit(message, cwd=cwd)
    except Exception as exc:
        duration = time.monotonic() - start
        _record_event(
            telemetry, "git_commit", args,
            success=False, duration=duration, error=str(exc),
        )
        raise
    duration = time.monotonic() - start
    _record_event(
        telemetry, "git_commit", args,
        success=True, duration=duration,
    )
    return result


def git_push(
    *,
    remote: str = "origin",
    branch: str | None = None,
    cwd: str | Path,
    telemetry: TelemetryCollector,
) -> str:
    """Push commits to remote with telemetry.

    Wraps :func:`tools.git_ops.git_push`, recording a
    ``tool_execution`` event on both success and failure.
    """
    args: dict[str, Any] = {"remote": remote, "cwd": str(cwd)}
    if branch is not None:
        args["branch"] = branch
    start = time.monotonic()
    try:
        result = _git_push(remote=remote, branch=branch, cwd=cwd)
    except Exception as exc:
        duration = time.monotonic() - start
        _record_event(
            telemetry, "git_push", args,
            success=False, duration=duration, error=str(exc),
        )
        raise
    duration = time.monotonic() - start
    _record_event(
        telemetry, "git_push", args,
        success=True, duration=duration,
    )
    return result
