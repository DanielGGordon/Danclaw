"""Integration test: sessions persist across process restarts and resume correctly.

Creates sessions in various states (ACTIVE, WAITING_FOR_HUMAN, DONE, ERROR),
destroys all Python objects, reconnects to the same SQLite file, and verifies
that sessions are found with their correct states.  Also verifies that an
ACTIVE session can be resumed by dispatching a new message to it through a
fresh Dispatcher instance (session continuity post-restart).
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
async def test_sessions_persist_with_various_states():
    """Sessions in all four states survive a full teardown and reconnect."""

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "danclaw_states.db")

        # ── Phase 1: create sessions in each state ────────────────────
        conn1 = await _init_db(db_path)
        repo1 = Repository(conn1)
        mgr1 = SessionManager(repo1)
        executor1 = MockExecutor()
        dispatcher1 = Dispatcher(mgr1, repo1, executor1, agent_name="bot")

        # Create four sessions on different channels so each gets its own session
        r_active = await dispatcher1.dispatch(
            _msg(channel_ref="chan-active", content="I am active"),
        )
        r_waiting = await dispatcher1.dispatch(
            _msg(channel_ref="chan-waiting", content="I will wait"),
        )
        r_done = await dispatcher1.dispatch(
            _msg(channel_ref="chan-done", content="I am done"),
        )
        r_error = await dispatcher1.dispatch(
            _msg(channel_ref="chan-error", content="I will fail"),
        )

        # Verify four distinct sessions were created
        session_ids = {
            r_active.session_id,
            r_waiting.session_id,
            r_done.session_id,
            r_error.session_id,
        }
        assert len(session_ids) == 4

        # Transition sessions to their target states
        # (ACTIVE stays as-is)
        await mgr1.update_state(r_waiting.session_id, "WAITING_FOR_HUMAN")
        await mgr1.update_state(r_done.session_id, "DONE")
        await mgr1.update_state(r_error.session_id, "ERROR")

        # Confirm states before teardown
        s_active = await repo1.get_session(r_active.session_id)
        s_waiting = await repo1.get_session(r_waiting.session_id)
        s_done = await repo1.get_session(r_done.session_id)
        s_error = await repo1.get_session(r_error.session_id)

        assert s_active.state == "ACTIVE"
        assert s_waiting.state == "WAITING_FOR_HUMAN"
        assert s_done.state == "DONE"
        assert s_error.state == "ERROR"

        # ── Tear down all Python objects ──────────────────────────────
        await conn1.close()
        del dispatcher1, mgr1, repo1, executor1, conn1
        del s_active, s_waiting, s_done, s_error

        # ── Phase 2: reconnect and verify all states survived ─────────
        conn2 = await _init_db(db_path)
        repo2 = Repository(conn2)

        s2_active = await repo2.get_session(r_active.session_id)
        s2_waiting = await repo2.get_session(r_waiting.session_id)
        s2_done = await repo2.get_session(r_done.session_id)
        s2_error = await repo2.get_session(r_error.session_id)

        assert s2_active is not None
        assert s2_active.state == "ACTIVE"
        assert s2_active.agent_name == "bot"

        assert s2_waiting is not None
        assert s2_waiting.state == "WAITING_FOR_HUMAN"
        assert s2_waiting.agent_name == "bot"

        assert s2_done is not None
        assert s2_done.state == "DONE"
        assert s2_done.agent_name == "bot"

        assert s2_error is not None
        assert s2_error.state == "ERROR"
        assert s2_error.agent_name == "bot"

        # Verify channel bindings also survived
        for sid, expected_ref in [
            (r_active.session_id, "chan-active"),
            (r_waiting.session_id, "chan-waiting"),
            (r_done.session_id, "chan-done"),
            (r_error.session_id, "chan-error"),
        ]:
            bindings = await repo2.get_bindings_for_session(sid)
            assert len(bindings) == 1
            assert bindings[0].channel_ref == expected_ref

        await conn2.close()


@pytest.mark.asyncio
async def test_active_session_resumes_after_restart():
    """An ACTIVE session can be resumed by dispatching a new message post-restart."""

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "danclaw_resume.db")

        # ── Phase 1: create a session and send initial messages ───────
        conn1 = await _init_db(db_path)
        repo1 = Repository(conn1)
        mgr1 = SessionManager(repo1)
        dispatcher1 = Dispatcher(mgr1, repo1, MockExecutor(), agent_name="bot")

        r1 = await dispatcher1.dispatch(
            _msg(channel_ref="resume-chan", content="first message"),
        )
        r2 = await dispatcher1.dispatch(
            _msg(channel_ref="resume-chan", content="second message"),
        )
        session_id = r1.session_id
        assert r2.session_id == session_id  # same channel → same session

        msgs_before = await repo1.get_messages_for_session(session_id)
        assert len(msgs_before) == 4  # 2 user + 2 assistant

        # ── Tear down everything ──────────────────────────────────────
        await conn1.close()
        del dispatcher1, mgr1, repo1, conn1

        # ── Phase 2: reconnect, build fresh Dispatcher, resume ────────
        conn2 = await _init_db(db_path)
        repo2 = Repository(conn2)
        mgr2 = SessionManager(repo2)
        dispatcher2 = Dispatcher(mgr2, repo2, MockExecutor(), agent_name="bot")

        # Dispatch a follow-up on the same channel — should reuse session
        r3 = await dispatcher2.dispatch(
            _msg(channel_ref="resume-chan", content="third message after restart"),
        )
        assert r3.session_id == session_id  # session continuity

        # Verify all messages (old + new) are in the session
        msgs_after = await repo2.get_messages_for_session(session_id)
        assert len(msgs_after) == 6  # 3 user + 3 assistant

        # Old messages intact
        assert msgs_after[0].role == "user"
        assert msgs_after[0].content == "first message"
        assert msgs_after[1].role == "assistant"
        assert msgs_after[1].content == "mock response: first message"
        assert msgs_after[2].role == "user"
        assert msgs_after[2].content == "second message"
        assert msgs_after[3].role == "assistant"
        assert msgs_after[3].content == "mock response: second message"

        # New message after restart
        assert msgs_after[4].role == "user"
        assert msgs_after[4].content == "third message after restart"
        assert msgs_after[5].role == "assistant"
        assert msgs_after[5].content == "mock response: third message after restart"

        # Session still ACTIVE
        session = await repo2.get_session(session_id)
        assert session.state == "ACTIVE"

        await conn2.close()


@pytest.mark.asyncio
async def test_resume_with_explicit_session_id_after_restart():
    """A session can be resumed by explicitly passing session_id in the message."""

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "danclaw_explicit.db")

        # Phase 1: create session
        conn1 = await _init_db(db_path)
        repo1 = Repository(conn1)
        mgr1 = SessionManager(repo1)
        dispatcher1 = Dispatcher(mgr1, repo1, MockExecutor(), agent_name="bot")

        r1 = await dispatcher1.dispatch(
            _msg(channel_ref="explicit-chan", content="initial"),
        )
        session_id = r1.session_id

        await conn1.close()
        del dispatcher1, mgr1, repo1, conn1

        # Phase 2: resume using explicit session_id on a different channel
        conn2 = await _init_db(db_path)
        repo2 = Repository(conn2)
        mgr2 = SessionManager(repo2)
        dispatcher2 = Dispatcher(mgr2, repo2, MockExecutor(), agent_name="bot")

        r2 = await dispatcher2.dispatch(
            _msg(
                channel_ref="different-chan",
                content="resumed via explicit ID",
                session_id=session_id,
            ),
        )
        assert r2.session_id == session_id

        msgs = await repo2.get_messages_for_session(session_id)
        assert len(msgs) == 4  # 2 from phase 1 + 2 from phase 2
        assert msgs[2].content == "resumed via explicit ID"

        await conn2.close()


@pytest.mark.asyncio
async def test_waiting_for_human_session_resumes_after_restart():
    """A WAITING_FOR_HUMAN session found by channel binding resumes correctly."""

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "danclaw_waiting.db")

        # Phase 1: create session, transition to WAITING_FOR_HUMAN
        conn1 = await _init_db(db_path)
        repo1 = Repository(conn1)
        mgr1 = SessionManager(repo1)
        dispatcher1 = Dispatcher(mgr1, repo1, MockExecutor(), agent_name="bot")

        r1 = await dispatcher1.dispatch(
            _msg(channel_ref="wait-chan", content="ask a question"),
        )
        session_id = r1.session_id
        await mgr1.update_state(session_id, "WAITING_FOR_HUMAN")

        await conn1.close()
        del dispatcher1, mgr1, repo1, conn1

        # Phase 2: reconnect, the human replies on the same channel
        conn2 = await _init_db(db_path)
        repo2 = Repository(conn2)
        mgr2 = SessionManager(repo2)
        dispatcher2 = Dispatcher(mgr2, repo2, MockExecutor(), agent_name="bot")

        # Channel binding lookup should find the WAITING_FOR_HUMAN session
        r2 = await dispatcher2.dispatch(
            _msg(channel_ref="wait-chan", content="here is my answer"),
        )
        assert r2.session_id == session_id  # same session resumed

        msgs = await repo2.get_messages_for_session(session_id)
        assert len(msgs) == 4
        assert msgs[2].content == "here is my answer"

        await conn2.close()


@pytest.mark.asyncio
async def test_done_session_not_reused_after_restart():
    """A DONE session is not reused — a new session is created for the channel."""

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "danclaw_done.db")

        # Phase 1: create session, mark DONE
        conn1 = await _init_db(db_path)
        repo1 = Repository(conn1)
        mgr1 = SessionManager(repo1)
        dispatcher1 = Dispatcher(mgr1, repo1, MockExecutor(), agent_name="bot")

        r1 = await dispatcher1.dispatch(
            _msg(channel_ref="done-chan", content="goodbye"),
        )
        old_session_id = r1.session_id
        await mgr1.update_state(old_session_id, "DONE")

        await conn1.close()
        del dispatcher1, mgr1, repo1, conn1

        # Phase 2: reconnect, same channel should get a NEW session
        conn2 = await _init_db(db_path)
        repo2 = Repository(conn2)
        mgr2 = SessionManager(repo2)
        dispatcher2 = Dispatcher(mgr2, repo2, MockExecutor(), agent_name="bot")

        r2 = await dispatcher2.dispatch(
            _msg(channel_ref="done-chan", content="new conversation"),
        )
        assert r2.session_id != old_session_id  # new session

        # Old session still DONE
        old_session = await repo2.get_session(old_session_id)
        assert old_session.state == "DONE"

        # New session is ACTIVE
        new_session = await repo2.get_session(r2.session_id)
        assert new_session.state == "ACTIVE"

        await conn2.close()
