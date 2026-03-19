"""Tests for dispatcher.__main__ startup and shutdown behaviour."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dispatcher.__main__ import _run, _setup_logging, main, DEFAULT_CONFIG_PATH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, agents: list[dict] | None = None) -> Path:
    """Write a minimal valid config and persona file, return the config path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir(exist_ok=True)

    if agents is None:
        agents = [
            {"name": "alpha", "persona": "default", "backend_preference": ["mock"]}
        ]

    # Ensure every referenced persona file exists.
    for agent in agents:
        persona_file = personas_dir / f"{agent['persona']}.md"
        if not persona_file.exists():
            persona_file.write_text("You are a helpful assistant.")

    config_path = config_dir / "danclaw.json"
    config_path.write_text(json.dumps({"agents": agents}))
    return config_path


def _tmp_db(tmp_path: Path) -> str:
    """Return a temporary database path."""
    return str(tmp_path / "test.db")


def _tmp_sock(tmp_path: Path) -> str:
    """Return a temporary socket path."""
    return str(tmp_path / "test.sock")


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
        await _run(config_path, db_path=_tmp_db(tmp_path), socket_path=_tmp_sock(tmp_path))

    assert any("Dispatcher ready" in r.message for r in caplog.records)
    assert any("2 agent(s) loaded" in r.message for r in caplog.records)
    assert any("a1, a2" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_logs_clean_shutdown(tmp_path, caplog):
    config_path = _write_config(tmp_path)

    with caplog.at_level(logging.INFO, logger="dispatcher"):
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, lambda: signal.raise_signal(signal.SIGINT))
        await _run(config_path, db_path=_tmp_db(tmp_path), socket_path=_tmp_sock(tmp_path))

    assert any("Dispatcher shut down cleanly" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_responds_to_sigterm(tmp_path, caplog):
    config_path = _write_config(tmp_path)

    with caplog.at_level(logging.INFO, logger="dispatcher"):
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, lambda: signal.raise_signal(signal.SIGTERM))
        await _run(config_path, db_path=_tmp_db(tmp_path), socket_path=_tmp_sock(tmp_path))

    assert any("Shutdown signal received" in r.message for r in caplog.records)
    assert any("Dispatcher shut down cleanly" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Tests: _run — database and socket server integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_creates_database(tmp_path):
    """_run initialises the SQLite database file."""
    config_path = _write_config(tmp_path)
    db_path = _tmp_db(tmp_path)

    loop = asyncio.get_running_loop()
    loop.call_later(0.1, lambda: signal.raise_signal(signal.SIGINT))
    await _run(config_path, db_path=db_path, socket_path=_tmp_sock(tmp_path))

    assert Path(db_path).exists()


@pytest.mark.asyncio
async def test_run_starts_socket_server(tmp_path):
    """_run creates the Unix domain socket file."""
    config_path = _write_config(tmp_path)
    sock_path = _tmp_sock(tmp_path)

    async def _check_and_stop():
        # Wait for the socket to appear
        for _ in range(50):
            if Path(sock_path).exists():
                break
            await asyncio.sleep(0.02)
        assert Path(sock_path).exists(), "Socket file was not created"
        signal.raise_signal(signal.SIGINT)

    loop = asyncio.get_running_loop()
    loop.call_soon(lambda: asyncio.ensure_future(_check_and_stop()))
    await _run(config_path, db_path=_tmp_db(tmp_path), socket_path=sock_path)


@pytest.mark.asyncio
async def test_run_socket_accepts_messages(tmp_path):
    """The started SocketServer can accept and respond to messages."""
    config_path = _write_config(tmp_path)
    sock_path = _tmp_sock(tmp_path)
    db_path = _tmp_db(tmp_path)

    async def _send_and_verify():
        # Wait for socket to appear
        for _ in range(50):
            if Path(sock_path).exists():
                break
            await asyncio.sleep(0.02)

        # Connect and send a message
        reader, writer = await asyncio.open_unix_connection(sock_path)
        msg = json.dumps({
            "source": "terminal",
            "channel_ref": "test-ref",
            "user_id": "test-user",
            "content": "hello",
        }) + "\n"
        writer.write(msg.encode())
        await writer.drain()

        response_line = await reader.readline()
        resp = json.loads(response_line)
        assert resp["ok"] is True
        assert "session_id" in resp
        assert "mock response: hello" in resp["response"]

        writer.close()
        await writer.wait_closed()
        signal.raise_signal(signal.SIGINT)

    loop = asyncio.get_running_loop()
    loop.call_soon(lambda: asyncio.ensure_future(_send_and_verify()))
    await _run(config_path, db_path=db_path, socket_path=sock_path)


@pytest.mark.asyncio
async def test_run_cleans_up_socket_on_shutdown(tmp_path):
    """After shutdown, the socket file is removed."""
    config_path = _write_config(tmp_path)
    sock_path = _tmp_sock(tmp_path)

    loop = asyncio.get_running_loop()
    loop.call_later(0.1, lambda: signal.raise_signal(signal.SIGINT))
    await _run(config_path, db_path=_tmp_db(tmp_path), socket_path=sock_path)

    assert not Path(sock_path).exists(), "Socket file should be removed after shutdown"


# ---------------------------------------------------------------------------
# Tests: smoke test — dispatcher as a subprocess
# ---------------------------------------------------------------------------


def test_dispatcher_starts_as_subprocess(tmp_path):
    """Verify 'python -m dispatcher' starts, creates the socket, and shuts down on SIGTERM."""
    config_path = _write_config(tmp_path)
    sock_path = _tmp_sock(tmp_path)
    db_path = _tmp_db(tmp_path)

    env = os.environ.copy()
    env["DANCLAW_SOCKET"] = sock_path
    env["DANCLAW_DB"] = db_path

    proc = subprocess.Popen(
        [sys.executable, "-m", "dispatcher", str(config_path)],
        env=env,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parent.parent),
    )

    try:
        # Wait for the socket file to appear (up to 5 seconds)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if Path(sock_path).exists():
                break
            time.sleep(0.1)

        assert Path(sock_path).exists(), "Dispatcher did not create the socket file"
        assert Path(db_path).exists(), "Dispatcher did not create the database file"

        # Verify the socket is connectable
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(sock_path)
        finally:
            sock.close()
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    assert proc.returncode == 0, f"Dispatcher exited with code {proc.returncode}"


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
