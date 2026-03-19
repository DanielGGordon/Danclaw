"""Tests for dispatcher.socket_server — Unix domain socket server."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from dispatcher.database import _SCHEMA_SQL
from dispatcher.dispatcher import Dispatcher
from dispatcher.executor import MockExecutor
from dispatcher.models import StandardMessage
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from dispatcher.socket_server import SocketServer


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """Yield an in-memory aiosqlite connection with schema applied."""
    async with aiosqlite.connect(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.commit()
        yield conn


@pytest_asyncio.fixture
async def dispatcher(db):
    """Dispatcher with default MockExecutor (echo mode)."""
    repo = Repository(db)
    mgr = SessionManager(repo)
    return Dispatcher(mgr, repo, MockExecutor(), agent_name="test-agent")


@pytest_asyncio.fixture
async def socket_path(tmp_path):
    """Return a temporary path for the Unix domain socket."""
    return tmp_path / "test.sock"


@pytest_asyncio.fixture
async def server(dispatcher, socket_path):
    """Start a SocketServer and yield it; stop on teardown."""
    srv = SocketServer(dispatcher, socket_path)
    await srv.start()
    yield srv
    await srv.stop()


def _msg_dict(
    source: str = "terminal",
    channel_ref: str = "tty1",
    user_id: str = "u1",
    content: str = "hello",
    session_id: str | None = None,
) -> dict:
    d = {
        "source": source,
        "channel_ref": channel_ref,
        "user_id": user_id,
        "content": content,
    }
    if session_id is not None:
        d["session_id"] = session_id
    return d


async def _send_recv(socket_path: Path, data: dict | str) -> dict:
    """Send a JSON line to the socket and return the parsed response."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        if isinstance(data, dict):
            line = json.dumps(data)
        else:
            line = data
        writer.write(line.encode("utf-8") + b"\n")
        await writer.drain()

        response_line = await reader.readline()
        return json.loads(response_line)
    finally:
        writer.close()
        await writer.wait_closed()


# ── Server lifecycle ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_server_starts_and_creates_socket(server, socket_path):
    """Server creates the socket file and reports is_serving."""
    assert socket_path.exists()
    assert server.is_serving


@pytest.mark.asyncio
async def test_server_stop_removes_socket(dispatcher, socket_path):
    """After stop, the socket file is removed."""
    srv = SocketServer(dispatcher, socket_path)
    await srv.start()
    assert socket_path.exists()
    await srv.stop()
    assert not socket_path.exists()
    assert not srv.is_serving


@pytest.mark.asyncio
async def test_server_removes_stale_socket(dispatcher, socket_path):
    """If a stale socket file exists, start replaces it."""
    # Create a stale file
    socket_path.touch()
    assert socket_path.exists()

    srv = SocketServer(dispatcher, socket_path)
    await srv.start()
    assert srv.is_serving
    await srv.stop()


# ── Accepts connections ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_server_accepts_connection(server, socket_path):
    """Can open a connection to the server."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    writer.close()
    await writer.wait_closed()


# ── Valid message dispatch ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_message_returns_ok_response(server, socket_path):
    """A valid StandardMessage gets a valid ok response."""
    resp = await _send_recv(socket_path, _msg_dict(content="ping"))
    assert resp["ok"] is True
    assert resp["response"] == "mock response: ping"
    assert resp["backend"] == "mock"
    assert "session_id" in resp
    assert isinstance(resp["session_id"], str)
    assert len(resp["session_id"]) > 0


@pytest.mark.asyncio
async def test_session_continuity_over_socket(server, socket_path):
    """Subsequent messages on the same channel reuse the session."""
    r1 = await _send_recv(socket_path, _msg_dict(content="first"))
    r2 = await _send_recv(socket_path, _msg_dict(content="second"))
    assert r1["ok"] is True
    assert r2["ok"] is True
    assert r1["session_id"] == r2["session_id"]


@pytest.mark.asyncio
async def test_explicit_session_id(server, socket_path):
    """Can route to an existing session by providing session_id."""
    r1 = await _send_recv(socket_path, _msg_dict(content="first"))
    assert r1["ok"] is True

    r2 = await _send_recv(
        socket_path,
        _msg_dict(
            content="second",
            channel_ref="other",
            session_id=r1["session_id"],
        ),
    )
    assert r2["ok"] is True
    assert r2["session_id"] == r1["session_id"]


# ── Invalid JSON ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_json_returns_error(server, socket_path):
    """Non-JSON input returns an error response."""
    resp = await _send_recv(socket_path, "this is not json")
    assert resp["ok"] is False
    assert "Invalid JSON" in resp["error"]


@pytest.mark.asyncio
async def test_empty_json_object_returns_error(server, socket_path):
    """An empty JSON object (missing required fields) returns an error."""
    resp = await _send_recv(socket_path, {})
    assert resp["ok"] is False
    assert "Invalid message" in resp["error"]


@pytest.mark.asyncio
async def test_partial_message_returns_error(server, socket_path):
    """A JSON object with some but not all required fields returns an error."""
    resp = await _send_recv(socket_path, {"source": "terminal"})
    assert resp["ok"] is False
    assert "Invalid message" in resp["error"]


@pytest.mark.asyncio
async def test_wrong_type_field_returns_error(server, socket_path):
    """A field with the wrong type returns an error."""
    msg = _msg_dict()
    msg["content"] = 42  # should be str
    resp = await _send_recv(socket_path, msg)
    assert resp["ok"] is False
    assert "Invalid message" in resp["error"]


# ── Multiple messages per connection ────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_messages_on_one_connection(server, socket_path):
    """The server handles multiple newline-delimited messages per connection."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        responses = []
        for content in ["hello", "world", "test"]:
            msg = json.dumps(_msg_dict(content=content))
            writer.write(msg.encode("utf-8") + b"\n")
            await writer.drain()

            resp_line = await reader.readline()
            responses.append(json.loads(resp_line))

        assert len(responses) == 3
        for i, content in enumerate(["hello", "world", "test"]):
            assert responses[i]["ok"] is True
            assert responses[i]["response"] == f"mock response: {content}"
    finally:
        writer.close()
        await writer.wait_closed()


# ── Clean shutdown ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clean_shutdown_while_no_clients(dispatcher, socket_path):
    """Server shuts down cleanly when no clients are connected."""
    srv = SocketServer(dispatcher, socket_path)
    await srv.start()
    assert srv.is_serving
    await srv.stop()
    assert not srv.is_serving
    assert not socket_path.exists()


@pytest.mark.asyncio
async def test_clean_shutdown_stops_accepting(dispatcher, socket_path):
    """After stop, the server no longer accepts new connections."""
    srv = SocketServer(dispatcher, socket_path)
    await srv.start()

    # Verify we can connect before shutdown
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    writer.close()
    await writer.wait_closed()

    await srv.stop()

    # After stop, connecting should fail (socket file removed)
    with pytest.raises(
        (FileNotFoundError, ConnectionRefusedError),
    ):
        await asyncio.open_unix_connection(str(socket_path))


# ── Dispatch error handling ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_error_returns_error_response(db, socket_path):
    """If the executor raises, the socket server returns an error response."""
    from dispatcher.executor import ExecutorResult

    class _FailingExecutor:
        async def execute(self, message: StandardMessage) -> ExecutorResult:
            raise RuntimeError("executor exploded")

    repo = Repository(db)
    mgr = SessionManager(repo)
    dispatcher = Dispatcher(mgr, repo, _FailingExecutor(), agent_name="test")

    srv = SocketServer(dispatcher, socket_path)
    await srv.start()
    try:
        resp = await _send_recv(socket_path, _msg_dict(content="boom"))
        assert resp["ok"] is False
        assert "Dispatch error" in resp["error"]
        assert "executor exploded" in resp["error"]
    finally:
        await srv.stop()


# ── Socket path property ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_socket_path_property(dispatcher, socket_path):
    """The socket_path property returns the configured path."""
    srv = SocketServer(dispatcher, socket_path)
    assert srv.socket_path == socket_path
