"""Tests for dispatcher.__main__ startup and shutdown behaviour."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from pathlib import Path

import pytest

from dispatcher.__main__ import _run, _setup_logging, main, DEFAULT_CONFIG_PATH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, agents: list[dict] | None = None) -> Path:
    """Write a minimal valid config and persona file, return the config path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir()

    if agents is None:
        agents = [
            {"name": "alpha", "persona": "default", "backend_preference": ["claude"]}
        ]

    # Ensure every referenced persona file exists.
    for agent in agents:
        persona_file = personas_dir / f"{agent['persona']}.md"
        if not persona_file.exists():
            persona_file.write_text("You are a helpful assistant.")

    config_path = config_dir / "danclaw.json"
    config_path.write_text(json.dumps({"agents": agents}))
    return config_path


# ---------------------------------------------------------------------------
# Tests: logging setup
# ---------------------------------------------------------------------------


def test_setup_logging_configures_root_logger():
    # Reset root logger so basicConfig can take effect.
    root = logging.getLogger()
    original_level = root.level
    original_handlers = root.handlers[:]
    root.handlers.clear()
    root.setLevel(logging.WARNING)

    try:
        _setup_logging()
        assert root.level == logging.INFO
        assert len(root.handlers) > 0
    finally:
        # Restore original state so other tests aren't affected.
        root.handlers = original_handlers
        root.setLevel(original_level)


# ---------------------------------------------------------------------------
# Tests: _run — readiness log + signal shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_logs_readiness_and_agent_count(tmp_path, caplog):
    config_path = _write_config(
        tmp_path,
        agents=[
            {"name": "a1", "persona": "default", "backend_preference": ["claude"]},
            {"name": "a2", "persona": "default", "backend_preference": ["codex"]},
        ],
    )

    with caplog.at_level(logging.INFO, logger="dispatcher"):
        # Schedule a SIGINT shortly after startup so the loop terminates.
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, lambda: signal.raise_signal(signal.SIGINT))
        await _run(config_path)

    assert any("Dispatcher ready" in r.message for r in caplog.records)
    assert any("2 agent(s) loaded" in r.message for r in caplog.records)
    assert any("a1, a2" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_logs_clean_shutdown(tmp_path, caplog):
    config_path = _write_config(tmp_path)

    with caplog.at_level(logging.INFO, logger="dispatcher"):
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, lambda: signal.raise_signal(signal.SIGINT))
        await _run(config_path)

    assert any("Dispatcher shut down cleanly" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_responds_to_sigterm(tmp_path, caplog):
    config_path = _write_config(tmp_path)

    with caplog.at_level(logging.INFO, logger="dispatcher"):
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, lambda: signal.raise_signal(signal.SIGTERM))
        await _run(config_path)

    assert any("Shutdown signal received" in r.message for r in caplog.records)
    assert any("Dispatcher shut down cleanly" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Tests: main() — config error handling
# ---------------------------------------------------------------------------


def test_main_exits_on_bad_config(tmp_path):
    bad_path = tmp_path / "nonexistent.json"
    with pytest.raises(SystemExit) as exc_info:
        main(config_path=bad_path)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Tests: default config path
# ---------------------------------------------------------------------------


def test_default_config_path_points_to_real_file():
    assert DEFAULT_CONFIG_PATH.exists(), f"Expected {DEFAULT_CONFIG_PATH} to exist"
