"""End-to-end integration test using ClaudeExecutor over Unix socket.

This test starts a real dispatcher with ClaudeExecutor, sends a message
through the Unix domain socket, and verifies a non-empty AI response is
returned.

Requirements:
    - ``claude`` CLI must be installed and on PATH.
    - A valid API key / authentication must be configured for ``claude``.

Run manually with::

    pytest tests/test_e2e_claude.py -v -m manual

This test is skipped in normal CI runs because it requires a live AI
backend.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

import aiosqlite
import pytest

from config import AgentConfig, DanClawConfig
from dispatcher.database import init_db
from dispatcher.dispatcher import Dispatcher
from dispatcher.executor import ClaudeExecutor
from dispatcher.models import StandardMessage
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from dispatcher.socket_server import SocketServer

# Skip unless explicitly opted in via marker selection or env var
_has_claude = shutil.which("claude") is not None
_skip_reason = (
    "Requires 'claude' CLI on PATH and live AI backend; "
    "run with: pytest tests/test_e2e_claude.py -v -m manual"
)

pytestmark = pytest.mark.manual


def _make_config() -> DanClawConfig:
    """Build a minimal config for the e2e test."""
    return DanClawConfig(
        agents=[
            AgentConfig(
                name="e2e-test",
                persona="default",
                backend_preference=["claude"],
            ),
        ],
    )


@pytest.mark.skipif(not _has_claude, reason=_skip_reason)
@pytest.mark.asyncio
async def test_e2e_claude_via_socket(tmp_path: Path) -> None:
    """Send a message through the full stack and get a real AI response.

    Pipeline exercised:
        Client → Unix socket → SocketServer → Dispatcher → ClaudeExecutor
        → claude CLI subprocess → response back through socket.
    """
    db_path = str(tmp_path / "e2e_test.db")
    socket_path = str(tmp_path / "e2e_test.sock")
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir()
    (personas_dir / "default.md").write_text(
        "You are a helpful test assistant. Keep responses very short.",
        encoding="utf-8",
    )

    await init_db(db_path)

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")

        repo = Repository(db)
        session_mgr = SessionManager(repo)
        executor = ClaudeExecutor(timeout=60)
        config = _make_config()
        dispatcher = Dispatcher(
            session_mgr, repo, executor, config=config,
            personas_dir=str(personas_dir),
        )
        server = SocketServer(dispatcher, socket_path)

        await server.start()
        assert server.is_serving

        try:
            # Connect as a client
            reader, writer = await asyncio.open_unix_connection(socket_path)

            # Send a simple message
            message = StandardMessage(
                source="terminal",
                channel_ref="e2e-test",
                user_id="tester",
                content="Reply with exactly the word 'hello' and nothing else.",
            )
            payload = json.dumps(message.to_dict()) + "\n"
            writer.write(payload.encode("utf-8"))
            await writer.drain()

            # Read response (with timeout)
            response_line = await asyncio.wait_for(
                reader.readline(), timeout=90,
            )
            response = json.loads(response_line)

            # Verify the response
            assert response["ok"] is True, f"Dispatch failed: {response}"
            assert response["response"], "Expected non-empty response"
            assert len(response["response"].strip()) > 0
            assert response["backend"] == "claude"
            assert "session_id" in response
            assert response["agent_name"] == "e2e-test"

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()
