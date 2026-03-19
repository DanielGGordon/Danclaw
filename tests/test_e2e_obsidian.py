"""End-to-end integration tests for the Obsidian tool pipeline.

Two categories of tests:

1. **Manual tests** (``@pytest.mark.manual``) — require a real Slack bot and
   a live AI backend.  Run with::

       pytest tests/test_e2e_obsidian.py -v -m manual

2. **Simulated integration tests** — test the full pipeline from socket
   message through dispatcher/executor, mocking only the AI call but
   verifying that Obsidian tools are wired up and vault content flows
   through the pipeline end-to-end.

Tasks 9.5 and 9.6 from the plan.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from config import (
    AgentConfig,
    ChannelPermissions,
    DanClawConfig,
    ObsidianToolConfig,
    PermissionsConfig,
    ToolsConfig,
)
from dispatcher.database import _SCHEMA_SQL
from dispatcher.dispatcher import Dispatcher
from dispatcher.executor import ExecutorResult, MockExecutor
from dispatcher.models import StandardMessage
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from dispatcher.socket_server import SocketServer
from tools.obsidian_read import read_file
from tools.obsidian_write import write_file
from tools.obsidian_search import search_files


# ── Helpers ───────────────────────────────────────────────────────────


def _make_vault(tmp_path: Path) -> Path:
    """Create a temporary Obsidian vault with sample notes."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "daily").mkdir()
    (vault / "daily" / "2026-03-19.md").write_text(
        "# Daily Note\n\n- Buy groceries\n- Review PR #42\n",
        encoding="utf-8",
    )
    (vault / "projects").mkdir()
    (vault / "projects" / "danclaw.md").write_text(
        "# DanClaw Project\n\nMulti-agent communication platform.\n",
        encoding="utf-8",
    )
    return vault


def _make_config(
    vault_path: str,
    *,
    agent_name: str = "obsidian-agent",
) -> DanClawConfig:
    """Build a config with Obsidian tools enabled and slack permissions."""
    return DanClawConfig(
        agents=[
            AgentConfig(
                name=agent_name,
                persona="default",
                backend_preference=["claude"],
                allowed_tools=[
                    "obsidian_read",
                    "obsidian_write",
                    "obsidian_search",
                ],
            ),
        ],
        permissions=PermissionsConfig(
            channels={
                "slack": ChannelPermissions(
                    allowed_tools=[
                        "obsidian_read",
                        "obsidian_write",
                        "obsidian_search",
                    ],
                ),
            },
        ),
        tools=ToolsConfig(
            obsidian=ObsidianToolConfig(vault_path=vault_path),
        ),
    )


def _make_personas_dir(tmp_path: Path) -> Path:
    """Create a personas directory with a default persona."""
    personas = tmp_path / "personas"
    personas.mkdir(exist_ok=True)
    (personas / "default.md").write_text(
        "You are a helpful assistant with access to an Obsidian vault.",
        encoding="utf-8",
    )
    return personas


def _msg(
    content: str,
    *,
    source: str = "slack",
    channel_ref: str = "C123:ts456",
    user_id: str = "U_TESTER",
    session_id: str | None = None,
) -> StandardMessage:
    return StandardMessage(
        source=source,
        channel_ref=channel_ref,
        user_id=user_id,
        content=content,
        session_id=session_id,
    )


class ObsidianAwareMockExecutor:
    """Mock executor that simulates an AI reading/writing Obsidian files.

    Instead of calling a real AI, this executor interprets simple commands
    embedded in the message content:

    - ``read:<relative_path>`` — reads the file from the vault and returns
      its content as the response.
    - ``write:<relative_path>:<content>`` — writes content to the vault and
      returns a confirmation.
    - ``search:<query>`` — searches the vault for files matching the query
      and returns the list.

    This lets us test the full pipeline (socket → dispatcher → executor →
    tool → response) without a live AI backend, while verifying that
    Obsidian tools are available and vault content flows correctly.
    """

    def __init__(self, vault_path: str) -> None:
        self._vault_path = vault_path
        self.last_persona: str | None = None
        self.last_allowed_tools: frozenset[str] | None = None
        self.call_count: int = 0

    async def execute(
        self,
        message: StandardMessage,
        *,
        persona: str | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> ExecutorResult:
        self.last_persona = persona
        self.last_allowed_tools = allowed_tools
        self.call_count += 1

        content = message.content.strip()

        # Simulate reading a vault file
        if content.startswith("read:"):
            file_path = content[len("read:"):].strip()
            try:
                vault_content = read_file(self._vault_path, file_path)
                return ExecutorResult(
                    content=f"Here is the content of {file_path}:\n\n{vault_content}",
                    backend="mock-obsidian",
                )
            except Exception as exc:
                return ExecutorResult(
                    content=f"Error reading {file_path}: {exc}",
                    backend="mock-obsidian",
                )

        # Simulate writing a vault file
        if content.startswith("write:"):
            parts = content[len("write:"):].split(":", 1)
            if len(parts) != 2:
                return ExecutorResult(
                    content="Error: write command requires path:content",
                    backend="mock-obsidian",
                )
            file_path, file_content = parts[0].strip(), parts[1].strip()
            try:
                result = write_file(self._vault_path, file_path, file_content)
                return ExecutorResult(
                    content=f"Done. {result}",
                    backend="mock-obsidian",
                )
            except Exception as exc:
                return ExecutorResult(
                    content=f"Error writing {file_path}: {exc}",
                    backend="mock-obsidian",
                )

        # Simulate searching the vault
        if content.startswith("search:"):
            query = content[len("search:"):].strip()
            try:
                matches = search_files(self._vault_path, query=query)
                if matches:
                    listing = "\n".join(f"- {m}" for m in matches)
                    return ExecutorResult(
                        content=f"Found {len(matches)} file(s):\n{listing}",
                        backend="mock-obsidian",
                    )
                return ExecutorResult(
                    content="No matching files found.",
                    backend="mock-obsidian",
                )
            except Exception as exc:
                return ExecutorResult(
                    content=f"Error searching: {exc}",
                    backend="mock-obsidian",
                )

        # Default: echo
        return ExecutorResult(
            content=f"mock response: {content}",
            backend="mock-obsidian",
        )


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """Yield an in-memory aiosqlite connection with schema applied."""
    async with aiosqlite.connect(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.commit()
        yield conn


@pytest_asyncio.fixture
async def repo(db):
    return Repository(db)


@pytest_asyncio.fixture
async def mgr(repo):
    return SessionManager(repo)


@pytest.fixture
def vault(tmp_path):
    return _make_vault(tmp_path)


@pytest.fixture
def personas_dir(tmp_path):
    return _make_personas_dir(tmp_path)


# ══════════════════════════════════════════════════════════════════════
# Simulated Integration Tests
# ══════════════════════════════════════════════════════════════════════


class TestSimulatedReadPipeline:
    """Task 9.5 — simulated: Slack message → agent reads Obsidian note →
    responds with content.

    Uses ObsidianAwareMockExecutor so no real AI is needed.
    """

    @pytest.mark.asyncio
    async def test_read_note_via_dispatcher(self, mgr, repo, vault, personas_dir):
        """Dispatcher dispatches a read request and returns vault content."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        result = await dispatcher.dispatch(
            _msg("read:daily/2026-03-19.md"),
        )

        assert result.response is not None
        assert "Daily Note" in result.response
        assert "Buy groceries" in result.response
        assert result.backend == "mock-obsidian"
        assert result.agent_name == "obsidian-agent"

    @pytest.mark.asyncio
    async def test_read_note_obsidian_tools_in_allowed_set(
        self, mgr, repo, vault, personas_dir,
    ):
        """Executor receives obsidian tools in the allowed_tools set."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        await dispatcher.dispatch(_msg("read:daily/2026-03-19.md"))

        assert executor.last_allowed_tools is not None
        assert "obsidian_read" in executor.last_allowed_tools
        assert "obsidian_write" in executor.last_allowed_tools
        assert "obsidian_search" in executor.last_allowed_tools

    @pytest.mark.asyncio
    async def test_read_note_via_socket(self, mgr, repo, vault, personas_dir, tmp_path):
        """Full socket round-trip: send JSON → get vault content back."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )
        socket_path = tmp_path / "test.sock"
        server = SocketServer(dispatcher, socket_path)
        await server.start()

        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            payload = json.dumps(_msg("read:projects/danclaw.md").to_dict()) + "\n"
            writer.write(payload.encode())
            await writer.drain()

            line = await asyncio.wait_for(reader.readline(), timeout=5)
            response = json.loads(line)

            assert response["ok"] is True
            assert "DanClaw Project" in response["response"]
            assert response["backend"] == "mock-obsidian"
            assert response["agent_name"] == "obsidian-agent"

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_read_missing_note_returns_error(
        self, mgr, repo, vault, personas_dir,
    ):
        """Reading a non-existent note returns an error message."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        result = await dispatcher.dispatch(
            _msg("read:nonexistent/file.md"),
        )

        assert "Error" in result.response
        assert "not found" in result.response.lower() or "File not found" in result.response

    @pytest.mark.asyncio
    async def test_read_stores_messages_in_session(
        self, mgr, repo, vault, personas_dir,
    ):
        """Both the user request and vault-content response are persisted."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        result = await dispatcher.dispatch(
            _msg("read:daily/2026-03-19.md"),
        )

        messages = await repo.get_messages_for_session(result.session_id)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "read:daily/2026-03-19.md"
        assert messages[1].role == "assistant"
        assert "Daily Note" in messages[1].content

    @pytest.mark.asyncio
    async def test_search_then_read_pipeline(
        self, mgr, repo, vault, personas_dir,
    ):
        """Search for a file, then read it — multi-turn session."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        # First: search
        r1 = await dispatcher.dispatch(_msg("search:groceries"))
        assert "daily/2026-03-19.md" in r1.response

        # Second: read the found file (same session)
        r2 = await dispatcher.dispatch(
            _msg("read:daily/2026-03-19.md"),
        )
        assert r2.session_id == r1.session_id
        assert "Buy groceries" in r2.response

    @pytest.mark.asyncio
    async def test_read_with_session_continuity(
        self, mgr, repo, vault, personas_dir,
    ):
        """Verify session continuity across multiple read requests."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        r1 = await dispatcher.dispatch(_msg("read:daily/2026-03-19.md"))
        r2 = await dispatcher.dispatch(_msg("read:projects/danclaw.md"))

        assert r1.session_id == r2.session_id
        messages = await repo.get_messages_for_session(r1.session_id)
        # 2 user + 2 assistant = 4
        assert len(messages) == 4


class TestSimulatedWritePipeline:
    """Task 9.6 — simulated: Slack message → agent creates/updates Obsidian
    note → confirms.

    Uses ObsidianAwareMockExecutor so no real AI is needed.
    """

    @pytest.mark.asyncio
    async def test_create_note_via_dispatcher(
        self, mgr, repo, vault, personas_dir,
    ):
        """Dispatcher dispatches a write request, file is created, confirmation returned."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        result = await dispatcher.dispatch(
            _msg("write:notes/new-note.md:# New Note\n\nCreated by agent."),
        )

        assert "Created" in result.response
        assert "new-note.md" in result.response
        assert result.backend == "mock-obsidian"

        # Verify the file was actually created
        created = vault / "notes" / "new-note.md"
        assert created.is_file()
        assert "New Note" in created.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_update_note_via_dispatcher(
        self, mgr, repo, vault, personas_dir,
    ):
        """Updating an existing note returns 'Updated' confirmation."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        result = await dispatcher.dispatch(
            _msg("write:daily/2026-03-19.md:# Updated Daily\n\n- New items"),
        )

        assert "Updated" in result.response
        assert "2026-03-19.md" in result.response

        # Verify the file was actually updated
        updated = vault / "daily" / "2026-03-19.md"
        assert "Updated Daily" in updated.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_write_obsidian_tools_in_allowed_set(
        self, mgr, repo, vault, personas_dir,
    ):
        """Executor receives obsidian tools in allowed_tools for write operations."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        await dispatcher.dispatch(
            _msg("write:notes/test.md:test content"),
        )

        assert executor.last_allowed_tools is not None
        assert "obsidian_write" in executor.last_allowed_tools

    @pytest.mark.asyncio
    async def test_create_note_via_socket(
        self, mgr, repo, vault, personas_dir, tmp_path,
    ):
        """Full socket round-trip: write a note and get confirmation."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )
        socket_path = tmp_path / "test.sock"
        server = SocketServer(dispatcher, socket_path)
        await server.start()

        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            payload = json.dumps(
                _msg("write:meeting-notes/standup.md:# Standup\n\n- All green").to_dict()
            ) + "\n"
            writer.write(payload.encode())
            await writer.drain()

            line = await asyncio.wait_for(reader.readline(), timeout=5)
            response = json.loads(line)

            assert response["ok"] is True
            assert "Created" in response["response"]
            assert "standup.md" in response["response"]

            # Verify the file exists on disk
            assert (vault / "meeting-notes" / "standup.md").is_file()

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_write_stores_messages_in_session(
        self, mgr, repo, vault, personas_dir,
    ):
        """Both the user request and write confirmation are persisted."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        result = await dispatcher.dispatch(
            _msg("write:notes/persisted.md:# Test"),
        )

        messages = await repo.get_messages_for_session(result.session_id)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert "write:" in messages[0].content
        assert messages[1].role == "assistant"
        assert "Created" in messages[1].content

    @pytest.mark.asyncio
    async def test_read_after_write_pipeline(
        self, mgr, repo, vault, personas_dir,
    ):
        """Write a note, then read it back — round-trip within a session."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        # Write
        r1 = await dispatcher.dispatch(
            _msg("write:roundtrip/test.md:# Round Trip\n\nContent here."),
        )
        assert "Created" in r1.response

        # Read back
        r2 = await dispatcher.dispatch(_msg("read:roundtrip/test.md"))
        assert r2.session_id == r1.session_id
        assert "Round Trip" in r2.response
        assert "Content here." in r2.response

    @pytest.mark.asyncio
    async def test_write_then_search_finds_new_file(
        self, mgr, repo, vault, personas_dir,
    ):
        """After writing a note, searching for its content finds it."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        # Write a note with unique content
        await dispatcher.dispatch(
            _msg("write:searchable/unique-note.md:# Unique\n\nZebra unicorn."),
        )

        # Search for the unique content
        r2 = await dispatcher.dispatch(_msg("search:Zebra unicorn"))
        assert "searchable/unique-note.md" in r2.response

    @pytest.mark.asyncio
    async def test_overwrite_note_then_verify(
        self, mgr, repo, vault, personas_dir,
    ):
        """Overwriting an existing note changes its content."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        # Read original
        r1 = await dispatcher.dispatch(_msg("read:daily/2026-03-19.md"))
        assert "Buy groceries" in r1.response

        # Overwrite
        r2 = await dispatcher.dispatch(
            _msg("write:daily/2026-03-19.md:# New Daily\n\n- Different content"),
        )
        assert "Updated" in r2.response

        # Read again — should see new content
        r3 = await dispatcher.dispatch(_msg("read:daily/2026-03-19.md"))
        assert "Different content" in r3.response
        assert "Buy groceries" not in r3.response


class TestSimulatedPermissions:
    """Verify that Obsidian tools are permission-gated in the pipeline."""

    @pytest.mark.asyncio
    async def test_obsidian_tools_not_in_unlisted_channel(
        self, mgr, repo, vault, personas_dir,
    ):
        """A channel without obsidian tools configured gets an empty tool set."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        # Send from a channel not in the permissions config
        await dispatcher.dispatch(
            _msg("read:daily/2026-03-19.md", source="unknown_channel"),
        )

        # Executor should have received an empty allowed_tools set
        assert executor.last_allowed_tools == frozenset()

    @pytest.mark.asyncio
    async def test_obsidian_tools_available_on_configured_channel(
        self, mgr, repo, vault, personas_dir,
    ):
        """The 'slack' channel gets all obsidian tools."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )

        await dispatcher.dispatch(
            _msg("read:daily/2026-03-19.md", source="slack"),
        )

        assert executor.last_allowed_tools == frozenset({
            "obsidian_read", "obsidian_write", "obsidian_search",
        })

    @pytest.mark.asyncio
    async def test_vault_path_in_config(self, vault):
        """Config correctly stores the vault_path."""
        config = _make_config(str(vault))
        assert config.tools.obsidian is not None
        assert config.tools.obsidian.vault_path == str(vault)


class TestSimulatedSocketPipeline:
    """End-to-end socket tests verifying the full pipeline with Obsidian."""

    @pytest.mark.asyncio
    async def test_multi_turn_read_write_via_socket(
        self, mgr, repo, vault, personas_dir, tmp_path,
    ):
        """Multi-turn conversation via socket: read → write → read back."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )
        socket_path = tmp_path / "test.sock"
        server = SocketServer(dispatcher, socket_path)
        await server.start()

        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))

            # Turn 1: read existing note
            payload = json.dumps(_msg("read:daily/2026-03-19.md").to_dict()) + "\n"
            writer.write(payload.encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            r1 = json.loads(line)
            assert r1["ok"] is True
            assert "Buy groceries" in r1["response"]
            session_id = r1["session_id"]

            # Turn 2: write a new note
            payload = json.dumps(
                _msg(
                    "write:notes/from-socket.md:# Socket Test\n\nWritten via socket.",
                    session_id=session_id,
                ).to_dict()
            ) + "\n"
            writer.write(payload.encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            r2 = json.loads(line)
            assert r2["ok"] is True
            assert "Created" in r2["response"]

            # Turn 3: read it back
            payload = json.dumps(
                _msg("read:notes/from-socket.md", session_id=session_id).to_dict()
            ) + "\n"
            writer.write(payload.encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            r3 = json.loads(line)
            assert r3["ok"] is True
            assert "Socket Test" in r3["response"]
            assert "Written via socket" in r3["response"]

            # All turns should be in the same session
            assert r2["session_id"] == session_id
            assert r3["session_id"] == session_id

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_search_via_socket(
        self, mgr, repo, vault, personas_dir, tmp_path,
    ):
        """Search for vault content via socket."""
        config = _make_config(str(vault))
        executor = ObsidianAwareMockExecutor(str(vault))
        dispatcher = Dispatcher(
            mgr, repo, executor, config=config, personas_dir=personas_dir,
        )
        socket_path = tmp_path / "test.sock"
        server = SocketServer(dispatcher, socket_path)
        await server.start()

        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            payload = json.dumps(_msg("search:DanClaw").to_dict()) + "\n"
            writer.write(payload.encode())
            await writer.drain()

            line = await asyncio.wait_for(reader.readline(), timeout=5)
            response = json.loads(line)

            assert response["ok"] is True
            assert "danclaw.md" in response["response"]

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()


# ══════════════════════════════════════════════════════════════════════
# Manual Integration Tests (require real Slack + AI backend)
# ══════════════════════════════════════════════════════════════════════

_has_claude = shutil.which("claude") is not None
_skip_reason = (
    "Requires 'claude' CLI on PATH and a live Slack bot; "
    "run with: pytest tests/test_e2e_obsidian.py -v -m manual"
)


@pytest.mark.manual
@pytest.mark.skipif(not _has_claude, reason=_skip_reason)
class TestManualSlackObsidianRead:
    """Task 9.5 — manual: Slack message → agent reads Obsidian note →
    responds with content in Slack thread.

    These tests require:
    - ``claude`` CLI on PATH with valid authentication
    - A running dispatcher with Obsidian tools configured
    - A Slack workspace with the bot installed

    Run with::

        pytest tests/test_e2e_obsidian.py::TestManualSlackObsidianRead -v -m manual
    """

    @pytest.mark.asyncio
    async def test_e2e_read_note_via_claude(self, tmp_path):
        """Send a read-note request through the full stack with ClaudeExecutor.

        Pipeline: Client → Socket → Dispatcher → ClaudeExecutor → claude CLI
        → response with vault content.
        """
        from dispatcher.database import init_db
        from dispatcher.executor import ClaudeExecutor

        vault = _make_vault(tmp_path)
        personas_dir = _make_personas_dir(tmp_path)
        db_path = str(tmp_path / "e2e.db")
        socket_path = str(tmp_path / "e2e.sock")

        await init_db(db_path)

        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            repo_obj = Repository(db)
            session_mgr = SessionManager(repo_obj)
            executor = ClaudeExecutor(timeout=60)
            config = _make_config(str(vault))
            dispatcher = Dispatcher(
                session_mgr, repo_obj, executor, config=config,
                personas_dir=str(personas_dir),
            )
            server = SocketServer(dispatcher, socket_path)
            await server.start()

            try:
                reader, writer = await asyncio.open_unix_connection(socket_path)
                msg = StandardMessage(
                    source="slack",
                    channel_ref="e2e-obsidian-read",
                    user_id="tester",
                    content=(
                        f"Read the file at daily/2026-03-19.md from the "
                        f"Obsidian vault at {vault} and tell me what it says."
                    ),
                )
                writer.write((json.dumps(msg.to_dict()) + "\n").encode())
                await writer.drain()

                line = await asyncio.wait_for(reader.readline(), timeout=90)
                response = json.loads(line)

                assert response["ok"] is True, f"Dispatch failed: {response}"
                assert len(response["response"].strip()) > 0
                assert response["backend"] == "claude"

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()


@pytest.mark.manual
@pytest.mark.skipif(not _has_claude, reason=_skip_reason)
class TestManualSlackObsidianWrite:
    """Task 9.6 — manual: Slack message → agent creates/updates Obsidian
    note → confirms in Slack thread.

    Run with::

        pytest tests/test_e2e_obsidian.py::TestManualSlackObsidianWrite -v -m manual
    """

    @pytest.mark.asyncio
    async def test_e2e_write_note_via_claude(self, tmp_path):
        """Send a write-note request through the full stack with ClaudeExecutor.

        Pipeline: Client → Socket → Dispatcher → ClaudeExecutor → claude CLI
        → writes to vault → response with confirmation.
        """
        from dispatcher.database import init_db
        from dispatcher.executor import ClaudeExecutor

        vault = _make_vault(tmp_path)
        personas_dir = _make_personas_dir(tmp_path)
        db_path = str(tmp_path / "e2e.db")
        socket_path = str(tmp_path / "e2e.sock")

        await init_db(db_path)

        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            repo_obj = Repository(db)
            session_mgr = SessionManager(repo_obj)
            executor = ClaudeExecutor(timeout=60)
            config = _make_config(str(vault))
            dispatcher = Dispatcher(
                session_mgr, repo_obj, executor, config=config,
                personas_dir=str(personas_dir),
            )
            server = SocketServer(dispatcher, socket_path)
            await server.start()

            try:
                reader, writer = await asyncio.open_unix_connection(socket_path)
                msg = StandardMessage(
                    source="slack",
                    channel_ref="e2e-obsidian-write",
                    user_id="tester",
                    content=(
                        f"Create a new note at notes/e2e-test.md in the "
                        f"Obsidian vault at {vault} with the title "
                        f"'E2E Test Note' and a body that says "
                        f"'This note was created by an automated test.'"
                    ),
                )
                writer.write((json.dumps(msg.to_dict()) + "\n").encode())
                await writer.drain()

                line = await asyncio.wait_for(reader.readline(), timeout=90)
                response = json.loads(line)

                assert response["ok"] is True, f"Dispatch failed: {response}"
                assert len(response["response"].strip()) > 0
                assert response["backend"] == "claude"

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()
