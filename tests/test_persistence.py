"""Integration test: messages persist across process restarts (SQLite on disk).

Creates a Dispatcher backed by a real SQLite file, dispatches messages,
destroys all Python objects, then creates a brand-new Dispatcher pointing at
the same SQLite file and verifies the previously stored messages are
retrievable.
"""

from __future__ import annotations

import os
import tempfile

import aiosqlite
import pytest

from dispatcher.database import _SCHEMA_SQL
from dispatcher.dispatcher import Dispatcher, DispatchResult
from dispatcher.executor import MockExecutor
from dispatcher.models import StandardMessage
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager


def _msg(
    source: str = "terminal",
    channel_ref: str = "tty1",
    user_id: str = "u1",
    content: str = "hello",
    session_id: str | None = None,
) -> StandardMessage:
    return StandardMessage(
        source=source,
        channel_ref=channel_ref,
        user_id=user_id,
        content=content,
        session_id=session_id,
    )


async def _init_db(db_path: str) -> aiosqlite.Connection:
    """Open a connection, apply schema, enable FK, and return it."""
    conn = await aiosqlite.connect(db_path)
    await conn.executescript(_SCHEMA_SQL)
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.commit()
    return conn


@pytest.mark.asyncio
async def test_messages_persist_across_restart():
    """Dispatch messages, tear everything down, reconnect, verify data."""

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "danclaw_test.db")

        # ── Phase 1: create dispatcher, send messages ─────────────────
        conn1 = await _init_db(db_path)
        repo1 = Repository(conn1)
        mgr1 = SessionManager(repo1)
        dispatcher1 = Dispatcher(mgr1, repo1, MockExecutor(), agent_name="agent-a")

        r1 = await dispatcher1.dispatch(_msg(content="first message"))
        r2 = await dispatcher1.dispatch(_msg(content="second message"))

        session_id = r1.session_id
        assert r2.session_id == session_id  # same channel → same session

        # Verify messages exist before teardown
        msgs_before = await repo1.get_messages_for_session(session_id)
        assert len(msgs_before) == 4  # 2 user + 2 assistant

        # ── Tear down all Python objects ──────────────────────────────
        await conn1.close()
        del dispatcher1, mgr1, repo1, conn1

        # ── Phase 2: reconnect to the same file ──────────────────────
        conn2 = await _init_db(db_path)
        repo2 = Repository(conn2)

        # Verify session survived
        session = await repo2.get_session(session_id)
        assert session is not None
        assert session.agent_name == "agent-a"
        assert session.state == "ACTIVE"

        # Verify all messages survived
        msgs_after = await repo2.get_messages_for_session(session_id)
        assert len(msgs_after) == 4

        assert msgs_after[0].role == "user"
        assert msgs_after[0].content == "first message"
        assert msgs_after[1].role == "assistant"
        assert msgs_after[1].content == "mock response: first message"
        assert msgs_after[2].role == "user"
        assert msgs_after[2].content == "second message"
        assert msgs_after[3].role == "assistant"
        assert msgs_after[3].content == "mock response: second message"

        # Verify channel binding survived
        bindings = await repo2.get_bindings_for_session(session_id)
        assert len(bindings) == 1
        assert bindings[0].channel_type == "terminal"
        assert bindings[0].channel_ref == "tty1"

        # ── Phase 2b: dispatch through a new Dispatcher ──────────────
        mgr2 = SessionManager(repo2)
        dispatcher2 = Dispatcher(
            mgr2, repo2, MockExecutor(), agent_name="agent-a",
        )

        # Same channel should reuse the persisted session
        r3 = await dispatcher2.dispatch(_msg(content="third message"))
        assert r3.session_id == session_id

        msgs_final = await repo2.get_messages_for_session(session_id)
        assert len(msgs_final) == 6  # 3 user + 3 assistant
        assert msgs_final[4].role == "user"
        assert msgs_final[4].content == "third message"
        assert msgs_final[5].role == "assistant"
        assert msgs_final[5].content == "mock response: third message"

        await conn2.close()


@pytest.mark.asyncio
async def test_multiple_sessions_persist():
    """Multiple sessions from different channels all survive a restart."""

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "danclaw_multi.db")

        # Phase 1: dispatch on two different channels
        conn1 = await _init_db(db_path)
        repo1 = Repository(conn1)
        mgr1 = SessionManager(repo1)
        dispatcher1 = Dispatcher(mgr1, repo1, MockExecutor(), agent_name="bot")

        r_a = await dispatcher1.dispatch(_msg(channel_ref="chan-a", content="hello A"))
        r_b = await dispatcher1.dispatch(_msg(channel_ref="chan-b", content="hello B"))

        assert r_a.session_id != r_b.session_id

        await conn1.close()
        del dispatcher1, mgr1, repo1, conn1

        # Phase 2: reconnect and verify both sessions
        conn2 = await _init_db(db_path)
        repo2 = Repository(conn2)

        sessions = await repo2.list_sessions()
        assert len(sessions) == 2

        msgs_a = await repo2.get_messages_for_session(r_a.session_id)
        msgs_b = await repo2.get_messages_for_session(r_b.session_id)

        assert len(msgs_a) == 2
        assert msgs_a[0].content == "hello A"
        assert len(msgs_b) == 2
        assert msgs_b[0].content == "hello B"

        await conn2.close()
