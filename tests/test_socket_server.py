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
from tests.conftest import make_config


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
    return Dispatcher(mgr, repo, MockExecutor(), config=make_config("test-agent"))


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


async def _send_recv_raw(socket_path: Path, raw_bytes: bytes) -> dict:
    """Send raw bytes to the socket and return the parsed response."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        writer.write(raw_bytes)
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
        async def execute(
            self,
            message: StandardMessage,
            *,
            persona: str | None = None,
            allowed_tools: frozenset[str] | None = None,
        ) -> ExecutorResult:
            raise RuntimeError("executor exploded")

    repo = Repository(db)
    mgr = SessionManager(repo)
    dispatcher = Dispatcher(mgr, repo, _FailingExecutor(), config=make_config("test"))

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


# ── list_sessions request ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sessions_empty(server, socket_path):
    """list_sessions returns an empty list when no sessions exist."""
    resp = await _send_recv(socket_path, {"type": "list_sessions"})
    assert resp["ok"] is True
    assert resp["sessions"] == []


@pytest.mark.asyncio
async def test_list_sessions_one_session(server, socket_path):
    """list_sessions returns a session after a message creates one."""
    # Create a session by dispatching a message
    r1 = await _send_recv(socket_path, _msg_dict(content="hello"))
    assert r1["ok"] is True

    resp = await _send_recv(socket_path, {"type": "list_sessions"})
    assert resp["ok"] is True
    assert len(resp["sessions"]) == 1
    session = resp["sessions"][0]
    assert session["id"] == r1["session_id"]
    assert session["agent_name"] == "test-agent"
    assert session["state"] == "ACTIVE"
    assert "created_at" in session


@pytest.mark.asyncio
async def test_list_sessions_multiple_sessions(server, socket_path):
    """list_sessions returns all sessions when multiple exist."""
    # Create sessions on different channels to get different sessions
    r1 = await _send_recv(socket_path, _msg_dict(channel_ref="ch1", content="a"))
    r2 = await _send_recv(socket_path, _msg_dict(channel_ref="ch2", content="b"))
    r3 = await _send_recv(socket_path, _msg_dict(channel_ref="ch3", content="c"))
    assert r1["ok"] and r2["ok"] and r3["ok"]
    # Verify they are different sessions
    session_ids = {r1["session_id"], r2["session_id"], r3["session_id"]}
    assert len(session_ids) == 3

    resp = await _send_recv(socket_path, {"type": "list_sessions"})
    assert resp["ok"] is True
    assert len(resp["sessions"]) == 3
    returned_ids = {s["id"] for s in resp["sessions"]}
    assert returned_ids == session_ids


# ── get_history request ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_history_empty_session(server, socket_path, db):
    """get_history returns empty messages for a session with no messages."""
    # Manually create a session with no messages via the repo
    repo = Repository(db)
    session = await repo.create_session("test-agent", session_id="empty-sess")

    resp = await _send_recv(socket_path, {
        "type": "get_history",
        "session_id": "empty-sess",
    })
    assert resp["ok"] is True
    assert resp["session_id"] == "empty-sess"
    assert resp["messages"] == []


@pytest.mark.asyncio
async def test_get_history_with_messages(server, socket_path):
    """get_history returns all messages for a session in order."""
    # Create a session by sending a message
    r1 = await _send_recv(socket_path, _msg_dict(content="hello"))
    assert r1["ok"] is True
    session_id = r1["session_id"]

    # Send another message to the same session
    r2 = await _send_recv(
        socket_path,
        _msg_dict(content="world", session_id=session_id),
    )
    assert r2["ok"] is True

    # Fetch history
    resp = await _send_recv(socket_path, {
        "type": "get_history",
        "session_id": session_id,
    })
    assert resp["ok"] is True
    assert resp["session_id"] == session_id
    msgs = resp["messages"]
    # 2 user messages + 2 assistant responses = 4 total
    assert len(msgs) == 4
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hello"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "mock response: hello"
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"] == "world"
    assert msgs[3]["role"] == "assistant"
    assert msgs[3]["content"] == "mock response: world"


@pytest.mark.asyncio
async def test_get_history_nonexistent_session(server, socket_path):
    """get_history returns error for a session that doesn't exist."""
    resp = await _send_recv(socket_path, {
        "type": "get_history",
        "session_id": "no-such-session",
    })
    assert resp["ok"] is False
    assert "Session not found" in resp["error"]


@pytest.mark.asyncio
async def test_get_history_missing_session_id(server, socket_path):
    """get_history without session_id returns an error."""
    resp = await _send_recv(socket_path, {"type": "get_history"})
    assert resp["ok"] is False
    assert "session_id" in resp["error"]


@pytest.mark.asyncio
async def test_get_history_message_fields(server, socket_path):
    """get_history messages contain the expected fields."""
    r1 = await _send_recv(socket_path, _msg_dict(content="test"))
    assert r1["ok"] is True

    resp = await _send_recv(socket_path, {
        "type": "get_history",
        "session_id": r1["session_id"],
    })
    assert resp["ok"] is True
    msg = resp["messages"][0]
    assert "role" in msg
    assert "content" in msg
    assert "source" in msg
    assert "user_id" in msg
    assert "created_at" in msg


# ── Fanout channels in response ──────────────────────────────────────


@pytest.mark.asyncio
async def test_response_includes_fanout_channels(server, socket_path):
    """The dispatch response JSON includes a fanout_channels list."""
    resp = await _send_recv(socket_path, _msg_dict(content="ping"))
    assert resp["ok"] is True
    assert "fanout_channels" in resp
    assert isinstance(resp["fanout_channels"], list)


@pytest.mark.asyncio
async def test_response_fanout_channels_empty_for_single_binding(
    server, socket_path,
):
    """With only one channel bound, fanout_channels is empty."""
    resp = await _send_recv(socket_path, _msg_dict(content="ping"))
    assert resp["ok"] is True
    assert resp["fanout_channels"] == []


# ── detach request ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detach_removes_binding(server, socket_path, db):
    """detach removes the specified channel binding."""
    # Create a session with a binding
    r1 = await _send_recv(socket_path, _msg_dict(content="hello"))
    assert r1["ok"] is True
    session_id = r1["session_id"]

    # Detach the terminal binding
    resp = await _send_recv(socket_path, {
        "type": "detach",
        "session_id": session_id,
        "channel_ref": "tty1",
    })
    assert resp["ok"] is True
    assert resp["removed"] is True

    # Verify binding is gone
    repo = Repository(db)
    bindings = await repo.get_bindings_for_session(session_id)
    refs = {b.channel_ref for b in bindings}
    assert "tty1" not in refs


@pytest.mark.asyncio
async def test_detach_leaves_other_bindings(server, socket_path, db):
    """detach removes only the specified binding, not others."""
    # Create a session
    r1 = await _send_recv(socket_path, _msg_dict(content="hello"))
    assert r1["ok"] is True
    session_id = r1["session_id"]

    # Add a second binding
    repo = Repository(db)
    await repo.add_channel_binding(session_id, "slack", "C123")

    # Detach the terminal binding
    resp = await _send_recv(socket_path, {
        "type": "detach",
        "session_id": session_id,
        "channel_ref": "tty1",
    })
    assert resp["ok"] is True
    assert resp["removed"] is True

    # Verify slack binding remains
    bindings = await repo.get_bindings_for_session(session_id)
    assert len(bindings) == 1
    assert bindings[0].channel_ref == "C123"


@pytest.mark.asyncio
async def test_detach_session_still_active(server, socket_path, db):
    """detach does not affect the session state."""
    r1 = await _send_recv(socket_path, _msg_dict(content="hello"))
    assert r1["ok"] is True
    session_id = r1["session_id"]

    await _send_recv(socket_path, {
        "type": "detach",
        "session_id": session_id,
        "channel_ref": "tty1",
    })

    repo = Repository(db)
    session = await repo.get_session(session_id)
    assert session is not None
    assert session.state == "ACTIVE"


@pytest.mark.asyncio
async def test_detach_nonexistent_binding(server, socket_path):
    """detach with a non-matching channel_ref returns removed=False."""
    r1 = await _send_recv(socket_path, _msg_dict(content="hello"))
    assert r1["ok"] is True
    session_id = r1["session_id"]

    resp = await _send_recv(socket_path, {
        "type": "detach",
        "session_id": session_id,
        "channel_ref": "nonexistent-ref",
    })
    assert resp["ok"] is True
    assert resp["removed"] is False


@pytest.mark.asyncio
async def test_detach_nonexistent_session(server, socket_path):
    """detach with a non-existent session_id returns an error."""
    resp = await _send_recv(socket_path, {
        "type": "detach",
        "session_id": "no-such-session",
        "channel_ref": "tty1",
    })
    assert resp["ok"] is False
    assert "not found" in resp["error"]


@pytest.mark.asyncio
async def test_detach_missing_session_id(server, socket_path):
    """detach without session_id returns an error."""
    resp = await _send_recv(socket_path, {
        "type": "detach",
        "channel_ref": "tty1",
    })
    assert resp["ok"] is False
    assert "session_id" in resp["error"]


@pytest.mark.asyncio
async def test_detach_missing_channel_ref(server, socket_path):
    """detach without channel_ref returns an error."""
    resp = await _send_recv(socket_path, {
        "type": "detach",
        "session_id": "some-session",
    })
    assert resp["ok"] is False
    assert "channel_ref" in resp["error"]


@pytest.mark.asyncio
async def test_response_fanout_channels_with_multiple_bindings(
    server, socket_path, db,
):
    """fanout_channels lists non-source channels bound to the session."""
    # Create a session
    r1 = await _send_recv(socket_path, _msg_dict(content="first"))
    assert r1["ok"] is True
    session_id = r1["session_id"]

    # Manually bind a second channel
    repo = Repository(db)
    await repo.add_channel_binding(session_id, "slack", "C123-ts456")

    # Dispatch again from terminal — fanout should include the slack channel
    r2 = await _send_recv(socket_path, _msg_dict(content="second"))
    assert r2["ok"] is True
    assert r2["session_id"] == session_id
    assert "C123-ts456" in r2["fanout_channels"]
    assert "tty1" not in r2["fanout_channels"]


# ── Fanout push to connected clients ──────────────────────────────


@pytest.mark.asyncio
async def test_fanout_push_to_connected_terminal(server, socket_path, db):
    """When a Slack message dispatches, the response is pushed to a connected terminal."""
    # 1. Terminal client connects and sends a message to create a session
    term_reader, term_writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        msg = json.dumps(_msg_dict(channel_ref="cli-term1", content="hello"))
        term_writer.write(msg.encode("utf-8") + b"\n")
        await term_writer.drain()
        r1_line = await term_reader.readline()
        r1 = json.loads(r1_line)
        assert r1["ok"] is True
        session_id = r1["session_id"]

        # 2. Bind a Slack channel to the same session
        repo = Repository(db)
        await repo.add_channel_binding(session_id, "slack", "C123:ts456")

        # 3. Slack client sends a message on the bridged session
        slack_msg = _msg_dict(
            source="slack",
            channel_ref="C123:ts456",
            content="from slack",
            session_id=session_id,
        )
        slack_resp = await _send_recv(socket_path, slack_msg)
        assert slack_resp["ok"] is True

        # 4. The terminal client should receive a fanout push
        fanout_line = await asyncio.wait_for(
            term_reader.readline(), timeout=2,
        )
        fanout = json.loads(fanout_line)
        assert fanout["type"] == "fanout"
        assert fanout["session_id"] == session_id
        assert "from slack" in fanout["response"]
        assert fanout["source"] == "slack"
    finally:
        term_writer.close()
        await term_writer.wait_closed()


@pytest.mark.asyncio
async def test_fanout_push_not_sent_to_source_client(server, socket_path, db):
    """The source client does not receive a fanout push for its own message."""
    # Terminal sends a message, creating a session
    term_reader, term_writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        msg = json.dumps(_msg_dict(channel_ref="cli-term1", content="hello"))
        term_writer.write(msg.encode("utf-8") + b"\n")
        await term_writer.drain()
        r1_line = await term_reader.readline()
        r1 = json.loads(r1_line)
        assert r1["ok"] is True

        # Send another message — should get only the response, no fanout
        msg2 = json.dumps(_msg_dict(channel_ref="cli-term1", content="again"))
        term_writer.write(msg2.encode("utf-8") + b"\n")
        await term_writer.drain()
        r2_line = await term_reader.readline()
        r2 = json.loads(r2_line)
        assert r2["ok"] is True

        # No extra data should be available (no fanout)
        try:
            extra = await asyncio.wait_for(term_reader.readline(), timeout=0.3)
            # If we get anything, it should not be a fanout to ourselves
            if extra:
                parsed = json.loads(extra)
                assert parsed.get("type") != "fanout"
        except asyncio.TimeoutError:
            pass  # Expected — no fanout push
    finally:
        term_writer.close()
        await term_writer.wait_closed()


@pytest.mark.asyncio
async def test_fanout_push_multiple_terminals(server, socket_path, db):
    """Fanout pushes go to all connected terminals bound to the session."""
    repo = Repository(db)

    # Two terminal clients connect and create sessions
    readers_writers = []
    for ref in ("cli-t1", "cli-t2"):
        r, w = await asyncio.open_unix_connection(str(socket_path))
        readers_writers.append((r, w, ref))
        msg = json.dumps(_msg_dict(channel_ref=ref, content="init"))
        w.write(msg.encode("utf-8") + b"\n")
        await w.drain()
        resp_line = await r.readline()
        resp = json.loads(resp_line)
        assert resp["ok"] is True

    try:
        # Both sessions are different. We need them on the same session.
        # Get terminal 1's session_id and bind terminal 2 to it
        r1, w1, _ = readers_writers[0]
        r2, w2, _ = readers_writers[1]

        # Get session_id from terminal 1
        msg = json.dumps(_msg_dict(channel_ref="cli-t1", content="msg1"))
        w1.write(msg.encode("utf-8") + b"\n")
        await w1.drain()
        resp1 = json.loads(await r1.readline())
        session_id = resp1["session_id"]

        # Bind terminal 2 to that session
        await repo.add_channel_binding(session_id, "terminal", "cli-t2")

        # Also bind a Slack channel
        await repo.add_channel_binding(session_id, "slack", "C999:ts999")

        # Slack sends a message on the session
        slack_msg = _msg_dict(
            source="slack",
            channel_ref="C999:ts999",
            content="slack msg",
            session_id=session_id,
        )
        await _send_recv(socket_path, slack_msg)

        # Both terminals should receive the fanout
        for r, w, ref in readers_writers:
            fanout_line = await asyncio.wait_for(r.readline(), timeout=2)
            fanout = json.loads(fanout_line)
            assert fanout["type"] == "fanout"
            assert "slack msg" in fanout["response"]
    finally:
        for r, w, ref in readers_writers:
            w.close()
            await w.wait_closed()


@pytest.mark.asyncio
async def test_fanout_push_after_disconnect_no_error(server, socket_path, db):
    """Pushing to a disconnected client does not crash the server."""
    repo = Repository(db)

    # Terminal connects and creates a session
    r, w = await asyncio.open_unix_connection(str(socket_path))
    msg = json.dumps(_msg_dict(channel_ref="cli-gone", content="hello"))
    w.write(msg.encode("utf-8") + b"\n")
    await w.drain()
    resp = json.loads(await r.readline())
    session_id = resp["session_id"]

    # Bind a Slack channel
    await repo.add_channel_binding(session_id, "slack", "C000:ts000")

    # Disconnect the terminal
    w.close()
    await w.wait_closed()
    await asyncio.sleep(0.1)  # Let server process disconnect

    # Slack sends a message — should not crash despite stale terminal ref
    slack_msg = _msg_dict(
        source="slack",
        channel_ref="C000:ts000",
        content="after disconnect",
        session_id=session_id,
    )
    slack_resp = await _send_recv(socket_path, slack_msg)
    assert slack_resp["ok"] is True


@pytest.mark.asyncio
async def test_connected_clients_property(server, socket_path):
    """The server tracks connected clients in _connected_clients."""
    assert server._connected_clients == {}

    # Connect a client and send a message to register it
    r, w = await asyncio.open_unix_connection(str(socket_path))
    try:
        msg = json.dumps(_msg_dict(channel_ref="cli-track1", content="hi"))
        w.write(msg.encode("utf-8") + b"\n")
        await w.drain()
        await r.readline()

        assert "cli-track1" in server._connected_clients
    finally:
        w.close()
        await w.wait_closed()

    # After disconnect, the client should be removed
    await asyncio.sleep(0.1)
    assert "cli-track1" not in server._connected_clients


@pytest.mark.asyncio
async def test_detach_unregisters_client(server, socket_path, db):
    """Sending a detach request unregisters the channel_ref from connected clients."""
    r, w = await asyncio.open_unix_connection(str(socket_path))
    try:
        # Send a message to register
        msg = json.dumps(_msg_dict(channel_ref="cli-detach1", content="hi"))
        w.write(msg.encode("utf-8") + b"\n")
        await w.drain()
        resp = json.loads(await r.readline())
        session_id = resp["session_id"]
        assert "cli-detach1" in server._connected_clients

        # Send detach
        detach = json.dumps({
            "type": "detach",
            "session_id": session_id,
            "channel_ref": "cli-detach1",
        })
        w.write(detach.encode("utf-8") + b"\n")
        await w.drain()
        await r.readline()

        assert "cli-detach1" not in server._connected_clients
    finally:
        w.close()
        await w.wait_closed()


# ── Fanout poster (external channel posting) ─────────────────────────


@pytest_asyncio.fixture
async def poster_calls():
    """Return a list that captures fanout poster calls."""
    return []


@pytest_asyncio.fixture
async def server_with_poster(dispatcher, socket_path, poster_calls):
    """Start a SocketServer with a fanout_poster and yield it."""
    async def poster(channel_ref: str, text: str) -> None:
        poster_calls.append((channel_ref, text))

    srv = SocketServer(dispatcher, socket_path, fanout_poster=poster)
    await srv.start()
    yield srv
    await srv.stop()


@pytest.mark.asyncio
async def test_fanout_poster_called_for_unconnected_slack_channel(
    server_with_poster, socket_path, db, poster_calls,
):
    """When a terminal dispatches and a Slack channel is bound, the poster is called.

    Default attribution is "bot", so user messages appear without a
    ``[via ...]`` prefix — they look as if the bot posted them.
    """
    # Create session from terminal
    r1 = await _send_recv(socket_path, _msg_dict(content="hello"))
    session_id = r1["session_id"]

    # Bind a Slack channel (not connected via socket)
    repo = Repository(db)
    await repo.add_channel_binding(session_id, "slack", "C123:ts456")

    # Send another message from terminal
    r2 = await _send_recv(socket_path, _msg_dict(content="what is the weather"))
    assert r2["ok"] is True

    # Poster should have been called with user input and response
    assert len(poster_calls) == 2
    ch_ref, user_text = poster_calls[0]
    assert ch_ref == "C123:ts456"
    # Default attribution="bot" — no [via terminal] prefix
    assert user_text == "what is the weather"
    assert "[via terminal]" not in user_text

    ch_ref2, resp_text = poster_calls[1]
    assert ch_ref2 == "C123:ts456"
    assert resp_text  # response from MockExecutor


@pytest.mark.asyncio
async def test_fanout_poster_not_called_for_connected_client(
    server_with_poster, socket_path, db, poster_calls,
):
    """Poster is not called for channels with a connected socket client."""
    # Two terminals connect — both connected via socket
    r1_reader, r1_writer = await asyncio.open_unix_connection(str(socket_path))
    r2_reader, r2_writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        # Terminal 1 creates a session
        msg1 = json.dumps(_msg_dict(channel_ref="cli-t1", content="init"))
        r1_writer.write(msg1.encode("utf-8") + b"\n")
        await r1_writer.drain()
        resp1 = json.loads(await r1_reader.readline())
        session_id = resp1["session_id"]

        # Terminal 2 registers
        msg2 = json.dumps(_msg_dict(channel_ref="cli-t2", content="init2"))
        r2_writer.write(msg2.encode("utf-8") + b"\n")
        await r2_writer.drain()
        await r2_reader.readline()

        # Bind terminal 2 to terminal 1's session
        repo = Repository(db)
        await repo.add_channel_binding(session_id, "terminal", "cli-t2")

        # Terminal 1 sends — fanout goes to terminal 2 via socket, NOT poster
        msg3 = json.dumps(_msg_dict(channel_ref="cli-t1", content="msg"))
        r1_writer.write(msg3.encode("utf-8") + b"\n")
        await r1_writer.drain()
        await r1_reader.readline()  # response

        # Wait briefly for any fanout
        await asyncio.sleep(0.2)

        # Poster should NOT have been called since cli-t2 is connected
        assert len(poster_calls) == 0
    finally:
        r1_writer.close()
        await r1_writer.wait_closed()
        r2_writer.close()
        await r2_writer.wait_closed()


@pytest.mark.asyncio
async def test_fanout_poster_receives_both_user_input_and_response(
    server_with_poster, socket_path, db, poster_calls,
):
    """Poster receives user input first (as bot by default), then the response."""
    r1 = await _send_recv(socket_path, _msg_dict(content="first"))
    session_id = r1["session_id"]

    repo = Repository(db)
    await repo.add_channel_binding(session_id, "slack", "C999:ts999")

    await _send_recv(socket_path, _msg_dict(content="tell me a joke"))
    assert len(poster_calls) == 2

    # First call: user input posted as bot (no prefix by default)
    assert poster_calls[0][0] == "C999:ts999"
    assert poster_calls[0][1] == "tell me a joke"

    # Second call: agent response
    assert poster_calls[1][0] == "C999:ts999"


@pytest.mark.asyncio
async def test_fanout_poster_not_called_without_poster(
    server, socket_path, db,
):
    """Without a fanout_poster, no external posting occurs (no crash)."""
    r1 = await _send_recv(socket_path, _msg_dict(content="hello"))
    session_id = r1["session_id"]

    repo = Repository(db)
    await repo.add_channel_binding(session_id, "slack", "C123:ts456")

    # This should work fine even though Slack channel has no connected client
    r2 = await _send_recv(socket_path, _msg_dict(content="second"))
    assert r2["ok"] is True


@pytest.mark.asyncio
async def test_fanout_poster_error_does_not_crash_server(
    dispatcher, socket_path, db,
):
    """If the poster raises, the server logs and continues."""
    async def failing_poster(channel_ref: str, text: str) -> None:
        raise RuntimeError("Slack API down")

    srv = SocketServer(dispatcher, socket_path, fanout_poster=failing_poster)
    await srv.start()
    try:
        r1 = await _send_recv(socket_path, _msg_dict(content="hello"))
        session_id = r1["session_id"]

        repo = Repository(db)
        await repo.add_channel_binding(session_id, "slack", "C123:ts456")

        # Should not crash
        r2 = await _send_recv(socket_path, _msg_dict(content="second"))
        assert r2["ok"] is True
    finally:
        await srv.stop()


# ── Attribution modes ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fanout_bot_attribution_no_prefix(
    server_with_poster, socket_path, db, poster_calls,
):
    """Default attribution="bot" posts user messages without a prefix."""
    r1 = await _send_recv(socket_path, _msg_dict(content="init"))
    session_id = r1["session_id"]

    repo = Repository(db)
    await repo.add_channel_binding(session_id, "slack", "C100:ts100")

    r2 = await _send_recv(socket_path, _msg_dict(content="hello from terminal"))
    assert r2["ok"] is True

    # User message posted as-is (bot attribution)
    assert len(poster_calls) == 2
    assert poster_calls[0][1] == "hello from terminal"
    assert "[via" not in poster_calls[0][1]


@pytest.mark.asyncio
async def test_fanout_attributed_mode_adds_prefix(
    server_with_poster, socket_path, db, poster_calls,
):
    """When attribution is set to "attributed", user messages get a [via source] prefix."""
    r1 = await _send_recv(socket_path, _msg_dict(content="init"))
    session_id = r1["session_id"]

    # Change attribution to "attributed"
    repo = Repository(db)
    await repo.update_session_attribution(session_id, "attributed")
    await repo.add_channel_binding(session_id, "slack", "C200:ts200")

    r2 = await _send_recv(socket_path, _msg_dict(content="hello from terminal"))
    assert r2["ok"] is True

    # User message should have the [via terminal] prefix
    assert len(poster_calls) == 2
    assert "[via terminal]" in poster_calls[0][1]
    assert "hello from terminal" in poster_calls[0][1]


@pytest.mark.asyncio
async def test_fanout_attributed_mode_response_no_prefix(
    server_with_poster, socket_path, db, poster_calls,
):
    """Agent responses never get an attribution prefix regardless of mode."""
    r1 = await _send_recv(socket_path, _msg_dict(content="init"))
    session_id = r1["session_id"]

    repo = Repository(db)
    await repo.update_session_attribution(session_id, "attributed")
    await repo.add_channel_binding(session_id, "slack", "C300:ts300")

    r2 = await _send_recv(socket_path, _msg_dict(content="ping"))
    assert r2["ok"] is True

    # Second poster call is the response — no prefix
    assert len(poster_calls) == 2
    resp_text = poster_calls[1][1]
    assert "[via" not in resp_text
    assert "mock response: ping" in resp_text
