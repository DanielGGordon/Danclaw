"""Tests for cli.agent — the ``agent chat`` CLI command."""

from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import threading
from unittest.mock import patch

import aiosqlite
import pytest

from cli.agent import _build_message, _connect, _send_recv, chat, main
from dispatcher.database import _SCHEMA_SQL
from dispatcher.dispatcher import Dispatcher
from dispatcher.executor import MockExecutor
from dispatcher.models import StandardMessage
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from dispatcher.socket_server import SocketServer


# ── Helpers ──────────────────────────────────────────────────────────


@pytest.fixture
def server_env(tmp_path):
    """Spin up a SocketServer with MockExecutor in a background thread.

    Yields the socket path as a string.
    """
    socket_path = tmp_path / "test.sock"

    # Create in-memory DB with schema — we need a dedicated event loop
    # for async setup, so we do it inside the server thread's loop.
    # Instead, use a synchronous approach: create the DB in the thread.
    loop = asyncio.new_event_loop()
    server = [None]  # mutable container for the SocketServer
    started = threading.Event()

    async def _setup_and_run():
        conn = await aiosqlite.connect(":memory:")
        await conn.executescript(_SCHEMA_SQL)
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.commit()

        repo = Repository(conn)
        mgr = SessionManager(repo)
        disp = Dispatcher(mgr, repo, MockExecutor(), agent_name="test-agent")

        srv = SocketServer(disp, socket_path)
        server[0] = srv
        await srv.start()
        started.set()

        # Keep running until the loop is stopped
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await srv.stop()
            await conn.close()

    task = [None]

    def _thread_target():
        asyncio.set_event_loop(loop)
        task[0] = loop.create_task(_setup_and_run())
        loop.run_forever()
        # Cleanup: cancel the task and let it finish
        task[0].cancel()
        loop.run_until_complete(task[0])
        loop.close()

    t = threading.Thread(target=_thread_target, daemon=True)
    t.start()
    started.wait(timeout=5)

    yield str(socket_path)

    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5)


# ── _build_message tests ────────────────────────────────────────────


def test_build_message_required_fields():
    """_build_message returns a dict with all required StandardMessage fields."""
    msg = _build_message("hello", "cli-abc12345")
    assert msg["source"] == "terminal"
    assert msg["content"] == "hello"
    assert msg["channel_ref"] == "cli-abc12345"
    assert "user_id" in msg
    assert "session_id" not in msg


def test_build_message_with_session_id():
    """_build_message includes session_id when provided."""
    msg = _build_message("hello", "cli-abc12345", session_id="s123")
    assert msg["session_id"] == "s123"


# ── _connect tests ──────────────────────────────────────────────────


def test_connect_to_nonexistent_socket():
    """_connect raises ConnectionError for a missing socket."""
    with pytest.raises(ConnectionError, match="Cannot connect"):
        _connect("/tmp/nonexistent_danclaw_test.sock")


def test_connect_to_running_server(server_env):
    """_connect successfully connects to a running server."""
    sock = _connect(server_env)
    assert isinstance(sock, socket.socket)
    sock.close()


# ── _send_recv tests ────────────────────────────────────────────────


def test_send_recv_valid_message(server_env):
    """_send_recv sends a message and receives an ok response."""
    sock = _connect(server_env)
    try:
        msg = {
            "source": "terminal",
            "channel_ref": "test-ref",
            "user_id": "test-user",
            "content": "hello",
        }
        resp = _send_recv(sock, msg)
        assert resp["ok"] is True
        assert resp["response"] == "mock response: hello"
        assert "session_id" in resp
    finally:
        sock.close()


def test_send_recv_invalid_message(server_env):
    """_send_recv returns an error for an invalid message."""
    sock = _connect(server_env)
    try:
        resp = _send_recv(sock, {"bad": "data"})
        assert resp["ok"] is False
        assert "error" in resp
    finally:
        sock.close()


# ── chat() integration tests ────────────────────────────────────────


def test_chat_exit_command(server_env):
    """Typing 'exit' ends the chat loop."""
    inputs = iter(["hello", "exit"])
    output: list[str] = []

    chat(server_env, input_fn=lambda _: next(inputs), print_fn=output.append)

    # Should have: connected msg, instructions, agent response, goodbye
    assert any("Connected" in line for line in output)
    assert any("mock response: hello" in line for line in output)
    assert any("Goodbye" in line for line in output)


def test_chat_eof_ends_loop(server_env):
    """EOFError (Ctrl+D) ends the chat loop gracefully."""
    call_count = [0]

    def _input_fn(prompt):
        call_count[0] += 1
        if call_count[0] == 1:
            return "hello"
        raise EOFError

    output: list[str] = []
    chat(server_env, input_fn=_input_fn, print_fn=output.append)

    assert any("mock response: hello" in line for line in output)


def test_chat_keyboard_interrupt(server_env):
    """KeyboardInterrupt (Ctrl+C) ends the chat loop gracefully."""
    call_count = [0]

    def _input_fn(prompt):
        call_count[0] += 1
        if call_count[0] == 1:
            return "hello"
        raise KeyboardInterrupt

    output: list[str] = []
    chat(server_env, input_fn=_input_fn, print_fn=output.append)

    assert any("mock response: hello" in line for line in output)
    assert any("Goodbye" in line for line in output)


def test_chat_empty_input_skipped(server_env):
    """Empty lines are skipped without sending to the server."""
    inputs = iter(["", "  ", "hello", "exit"])
    output: list[str] = []

    chat(server_env, input_fn=lambda _: next(inputs), print_fn=output.append)

    # Only one agent response (for "hello"), not for empty lines
    agent_responses = [line for line in output if "mock response" in line]
    assert len(agent_responses) == 1


def test_chat_session_reuse(server_env):
    """Consecutive messages reuse the same session_id."""
    inputs = iter(["first", "second", "exit"])
    output: list[str] = []

    chat(server_env, input_fn=lambda _: next(inputs), print_fn=output.append)

    agent_lines = [line for line in output if "mock response" in line]
    assert len(agent_lines) == 2


def test_chat_connection_error():
    """chat() handles connection failures gracefully."""
    output: list[str] = []

    with pytest.raises(ConnectionError, match="Cannot connect"):
        chat(
            "/tmp/nonexistent_danclaw_test.sock",
            input_fn=lambda _: "hello",
            print_fn=output.append,
        )


# ── main() argument parsing ─────────────────────────────────────────


def test_main_no_args_exits(capsys):
    """Running with no subcommand prints help and exits."""
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 1


def test_main_chat_subcommand(server_env):
    """main(['chat', '--socket', path]) runs the chat loop."""
    inputs = iter(["hello", "exit"])
    output: list[str] = []

    with patch("builtins.input", side_effect=lambda _: next(inputs)):
        with patch("builtins.print", side_effect=output.append):
            main(["chat", "--socket", server_env])

    # Verify chat ran
    assert any("mock response: hello" in line for line in output)
