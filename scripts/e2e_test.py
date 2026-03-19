#!/usr/bin/env python3
"""End-to-end smoke test: full pipeline from socket to ClaudeExecutor.

Starts a real dispatcher with ClaudeExecutor, sends a message through
the Unix socket, prints the AI response, and exits.

Prerequisites:
    - ``claude`` CLI installed and on PATH
    - Valid authentication configured for ``claude``
    - Project dependencies installed (``pip install -e '.[dev]'``)

Usage:
    python scripts/e2e_test.py

Exit codes:
    0  — test passed (non-empty response received)
    1  — test failed or error occurred
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

import aiosqlite  # noqa: E402

from config import AgentConfig, DanClawConfig  # noqa: E402
from dispatcher.database import init_db  # noqa: E402
from dispatcher.dispatcher import Dispatcher  # noqa: E402
from dispatcher.executor import ClaudeExecutor  # noqa: E402
from dispatcher.models import StandardMessage  # noqa: E402
from dispatcher.repository import Repository  # noqa: E402
from dispatcher.session_manager import SessionManager  # noqa: E402
from dispatcher.socket_server import SocketServer  # noqa: E402


def _check_prerequisites() -> None:
    """Verify that required tools are available."""
    if not shutil.which("claude"):
        print("ERROR: 'claude' CLI not found on PATH.", file=sys.stderr)
        print("Install it and ensure it is authenticated.", file=sys.stderr)
        sys.exit(1)


async def run_e2e() -> bool:
    """Run the end-to-end test and return True on success."""
    with tempfile.TemporaryDirectory(prefix="danclaw_e2e_") as tmp:
        tmp_path = Path(tmp)
        db_path = str(tmp_path / "e2e.db")
        socket_path = str(tmp_path / "e2e.sock")

        # Create a persona file
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        (personas_dir / "default.md").write_text(
            "You are a helpful test assistant. Keep responses very short.",
            encoding="utf-8",
        )

        # Config
        config = DanClawConfig(
            agents=[
                AgentConfig(
                    name="e2e-test",
                    persona="default",
                    backend_preference=["claude"],
                ),
            ],
        )

        # Init DB
        await init_db(db_path)
        print(f"[OK] Database initialised at {db_path}")

        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")

            # Wire components
            repo = Repository(db)
            session_mgr = SessionManager(repo)
            executor = ClaudeExecutor(timeout=60)
            dispatcher = Dispatcher(
                session_mgr, repo, executor, config=config,
                personas_dir=str(personas_dir),
            )
            server = SocketServer(dispatcher, socket_path)

            await server.start()
            print(f"[OK] Socket server listening on {socket_path}")

            try:
                # Connect as client
                reader, writer = await asyncio.open_unix_connection(socket_path)
                print("[OK] Client connected")

                # Send message
                message = StandardMessage(
                    source="terminal",
                    channel_ref="e2e-test",
                    user_id="tester",
                    content="Reply with exactly the word 'hello' and nothing else.",
                )
                payload = json.dumps(message.to_dict()) + "\n"
                writer.write(payload.encode("utf-8"))
                await writer.drain()
                print("[OK] Message sent, waiting for AI response...")

                # Read response
                response_line = await asyncio.wait_for(
                    reader.readline(), timeout=90,
                )
                response = json.loads(response_line)

                writer.close()
                await writer.wait_closed()

                # Evaluate
                if not response.get("ok"):
                    print(f"[FAIL] Dispatch error: {response.get('error')}")
                    return False

                ai_response = response.get("response", "")
                backend = response.get("backend", "unknown")
                session_id = response.get("session_id", "unknown")
                agent_name = response.get("agent_name", "unknown")

                print(f"[OK] Got response from backend '{backend}'")
                print(f"     Agent: {agent_name}")
                print(f"     Session: {session_id}")
                print(f"     Response: {ai_response!r}")

                if ai_response.strip():
                    print("\n[PASS] End-to-end test succeeded!")
                    return True
                else:
                    print("\n[FAIL] Response was empty.")
                    return False

            finally:
                await server.stop()
                print("[OK] Server stopped")


def main() -> None:
    _check_prerequisites()
    print("=" * 60)
    print("DanClaw End-to-End Test")
    print("=" * 60)
    print()

    success = asyncio.run(run_e2e())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
