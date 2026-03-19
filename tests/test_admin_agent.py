"""Tests for admin agent configuration, permissions, and dispatch behaviour."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from config import (
    AgentConfig,
    ChannelPermissions,
    DanClawConfig,
    PermissionsConfig,
    UserPermissions,
    load_config,
)
from dispatcher.database import _SCHEMA_SQL
from dispatcher.dispatcher import Dispatcher, DispatchResult
from dispatcher.executor import ExecutorResult, MockExecutor
from dispatcher.models import StandardMessage
from dispatcher.permissions import requires_approval, resolve_permissions
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from tests.conftest import make_personas_dir


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_project(tmp_path: Path):
    """Create a project layout with admin persona and tool stubs."""
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir()
    (personas_dir / "default.md").write_text("Default persona.")
    (personas_dir / "admin.md").write_text("Admin persona.")

    config_dir = tmp_path / "config"
    config_dir.mkdir()

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    # Create stub tools so validation passes
    for tool in ("obsidian_read", "obsidian_write", "obsidian_search", "git_ops", "deploy"):
        (tools_dir / f"{tool}.py").write_text(f"# stub: {tool}")

    class _Project:
        root = tmp_path
        personas = personas_dir
        config = config_dir
        tools = tools_dir

        def write_config(self, data: dict) -> Path:
            p = self.config / "danclaw.json"
            p.write_text(json.dumps(data))
            return p

    return _Project()


def _admin_config(**overrides) -> dict:
    """Return a config dict with both default and admin agents."""
    cfg: dict = {
        "agents": [
            {
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            },
            {
                "name": "admin",
                "persona": "admin",
                "backend_preference": ["claude", "codex"],
                "allowed_tools": [
                    "obsidian_read", "obsidian_write", "obsidian_search",
                    "git_ops", "deploy",
                ],
            },
        ],
        "permissions": {
            "channels": {
                "admin": {
                    "allowed_tools": ["git", "obsidian", "deploy", "git_ops"],
                    "override": True,
                    "approval_required": False,
                },
            },
        },
    }
    cfg.update(overrides)
    return cfg


# ── Admin agent loads from config ───────────────────────────────────────


class TestAdminAgentConfig:
    """Admin agent is defined and loaded from config correctly."""

    def test_admin_agent_exists(self, tmp_project) -> None:
        path = tmp_project.write_config(_admin_config())
        cfg = load_config(
            path,
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        admin = cfg.get_agent("admin")
        assert admin is not None
        assert admin.name == "admin"

    def test_admin_agent_persona(self, tmp_project) -> None:
        path = tmp_project.write_config(_admin_config())
        cfg = load_config(
            path,
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        admin = cfg.get_agent("admin")
        assert admin.persona == "admin"

    def test_admin_agent_has_all_tools(self, tmp_project) -> None:
        path = tmp_project.write_config(_admin_config())
        cfg = load_config(
            path,
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        admin = cfg.get_agent("admin")
        expected = {"obsidian_read", "obsidian_write", "obsidian_search", "git_ops", "deploy"}
        assert set(admin.allowed_tools) == expected

    def test_admin_agent_backend_preference(self, tmp_project) -> None:
        path = tmp_project.write_config(_admin_config())
        cfg = load_config(
            path,
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        admin = cfg.get_agent("admin")
        assert admin.backend_preference == ["claude", "codex"]

    def test_default_agent_unchanged(self, tmp_project) -> None:
        """Adding admin does not break the default agent."""
        path = tmp_project.write_config(_admin_config())
        cfg = load_config(
            path,
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.default_agent.name == "default"
        assert cfg.default_agent.allowed_tools == []

    def test_admin_is_not_default(self, tmp_project) -> None:
        """Admin is the second agent, not the default."""
        path = tmp_project.write_config(_admin_config())
        cfg = load_config(
            path,
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.default_agent.name == "default"
        assert cfg.get_agent("admin") is not cfg.default_agent


# ── Admin channel: no approval gates ────────────────────────────────────


class TestAdminChannelPermissions:
    """Admin channel has full tools and no approval gates."""

    def test_admin_channel_no_approval(self) -> None:
        config = PermissionsConfig(
            channels={
                "admin": ChannelPermissions(
                    allowed_tools=["git", "obsidian", "deploy", "git_ops"],
                    override=True,
                    approval_required=False,
                ),
            },
        )
        assert requires_approval(config, "admin", "dan") is False

    def test_admin_channel_no_approval_any_user(self) -> None:
        config = PermissionsConfig(
            channels={
                "admin": ChannelPermissions(
                    allowed_tools=["git", "obsidian", "deploy", "git_ops"],
                    override=True,
                    approval_required=False,
                ),
            },
        )
        assert requires_approval(config, "admin", "unknown-user") is False

    def test_admin_channel_tools_resolved(self) -> None:
        config = PermissionsConfig(
            channels={
                "admin": ChannelPermissions(
                    allowed_tools=["git", "obsidian", "deploy", "git_ops"],
                    override=True,
                    approval_required=False,
                ),
            },
        )
        tools = resolve_permissions(config, "admin", "unknown-user")
        assert tools == frozenset({"git", "obsidian", "deploy", "git_ops"})

    def test_admin_channel_user_tools_ignored(self) -> None:
        """User permissions are ignored on admin channel since override=True."""
        config = PermissionsConfig(
            channels={
                "admin": ChannelPermissions(
                    allowed_tools=["git", "obsidian", "deploy", "git_ops"],
                    override=True,
                    approval_required=False,
                ),
            },
            users={
                "dan": UserPermissions(additional_tools=["extra_tool"]),
            },
        )
        tools = resolve_permissions(config, "admin", "dan")
        assert "extra_tool" not in tools
        assert "git" in tools

    def test_admin_channel_user_approval_ignored(self) -> None:
        """User approval_required is ignored on admin channel (override=True).

        Even if a user has approval_required=True, the admin channel's
        override flag ensures only the channel's approval_required (False)
        is considered — so no approval is ever needed on this channel.
        """
        config = PermissionsConfig(
            channels={
                "admin": ChannelPermissions(
                    allowed_tools=["git"],
                    override=True,
                    approval_required=False,
                ),
            },
            users={
                "someone": UserPermissions(
                    additional_tools=[],
                    approval_required=True,
                ),
            },
        )
        # User has approval_required=True, but admin override ignores it
        assert requires_approval(config, "admin", "someone") is False

    def test_admin_channel_loaded_from_config(self, tmp_project) -> None:
        path = tmp_project.write_config(_admin_config())
        cfg = load_config(
            path,
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        admin_ch = cfg.permissions.channels.get("admin")
        assert admin_ch is not None
        assert admin_ch.approval_required is False
        assert admin_ch.override is True
        assert set(admin_ch.allowed_tools) == {"git", "obsidian", "deploy", "git_ops"}


# ── Non-admin channels still require approval ──────────────────────────


class TestNonAdminStillRestricted:
    """Existing channels retain their approval gates."""

    def test_slack_still_requires_approval(self) -> None:
        config = PermissionsConfig(
            channels={
                "slack": ChannelPermissions(
                    allowed_tools=["obsidian"],
                    override=True,
                    approval_required=True,
                ),
                "admin": ChannelPermissions(
                    allowed_tools=["git", "obsidian", "deploy", "git_ops"],
                    override=True,
                    approval_required=False,
                ),
            },
        )
        assert requires_approval(config, "slack", "dan") is True
        assert requires_approval(config, "admin", "dan") is False

    def test_slack_tools_still_restricted(self) -> None:
        config = PermissionsConfig(
            channels={
                "slack": ChannelPermissions(
                    allowed_tools=["obsidian"],
                    override=True,
                ),
                "admin": ChannelPermissions(
                    allowed_tools=["git", "obsidian", "deploy", "git_ops"],
                    override=True,
                ),
            },
            users={
                "dan": UserPermissions(additional_tools=["deploy"]),
            },
        )
        slack_tools = resolve_permissions(config, "slack", "dan")
        admin_tools = resolve_permissions(config, "admin", "dan")
        # Slack override=True: user tools excluded
        assert slack_tools == frozenset({"obsidian"})
        # Admin override=True: channel tools only (user tools excluded)
        assert admin_tools == frozenset({"git", "obsidian", "deploy", "git_ops"})


# ── Integration: load real project config ───────────────────────────────


class TestRealConfigAdminAgent:
    """Smoke tests against the real project config file."""

    def test_real_config_has_admin_agent(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        admin = cfg.get_agent("admin")
        assert admin is not None
        assert admin.name == "admin"
        assert admin.persona == "admin"
        assert "git_ops" in admin.allowed_tools
        assert "deploy" in admin.allowed_tools

    def test_real_config_admin_channel_no_approval(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        admin_ch = cfg.permissions.channels.get("admin")
        assert admin_ch is not None
        assert admin_ch.approval_required is False
        assert admin_ch.override is True

    def test_real_config_admin_no_approval_resolved(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        assert requires_approval(cfg.permissions, "admin", "dan") is False

    def test_real_config_admin_ignores_user_approval(self) -> None:
        """Admin channel override ensures user approval flags are ignored."""
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        # Even with a hypothetical user that has approval_required, override
        # ensures the admin channel never requires approval
        admin_ch = cfg.permissions.channels["admin"]
        assert admin_ch.override is True
        assert admin_ch.approval_required is False


# ── Integration: admin dispatches git ops ─────────────────────────────


def _admin_dispatch_config() -> DanClawConfig:
    """Config with admin agent that has git_ops and a restricted channel."""
    return DanClawConfig(
        agents=[
            AgentConfig(
                name="default",
                persona="default",
                backend_preference=["claude"],
            ),
            AgentConfig(
                name="admin",
                persona="admin",
                backend_preference=["claude"],
                allowed_tools=["git_ops", "deploy"],
            ),
        ],
        permissions=PermissionsConfig(
            channels={
                "admin": ChannelPermissions(
                    allowed_tools=["git_ops", "deploy"],
                    override=True,
                    approval_required=False,
                ),
                "general": ChannelPermissions(
                    allowed_tools=["obsidian"],
                    override=True,
                    approval_required=True,
                ),
            },
        ),
    )


class TestAdminAgentDispatchGitOps:
    """Admin agent can dispatch messages with git_ops tool access."""

    @pytest_asyncio.fixture
    async def db(self):
        async with aiosqlite.connect(":memory:") as conn:
            await conn.executescript(_SCHEMA_SQL)
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.commit()
            yield conn

    @pytest_asyncio.fixture
    async def repo(self, db):
        return Repository(db)

    @pytest_asyncio.fixture
    async def mgr(self, repo):
        return SessionManager(repo)

    @pytest.fixture
    def personas_dir(self, tmp_path):
        return make_personas_dir(tmp_path, {
            "default": "Default test persona.",
            "admin": "Admin persona with full tool access.",
        })

    @pytest_asyncio.fixture
    async def admin_dispatcher(self, mgr, repo, personas_dir):
        return Dispatcher(
            mgr, repo, MockExecutor(),
            config=_admin_dispatch_config(),
            personas_dir=personas_dir,
        )

    @pytest.mark.asyncio
    async def test_admin_channel_no_approval_gate(self, admin_dispatcher) -> None:
        """Admin channel dispatches directly without approval gate."""
        msg = StandardMessage(
            source="terminal", channel_ref="admin",
            user_id="dan", content="run git add",
        )
        result = await admin_dispatcher.dispatch(msg)
        assert result.backend != "system"  # not blocked by approval
        assert "approval" not in result.response.lower()

    @pytest.mark.asyncio
    async def test_admin_channel_resolves_git_ops_tool(self, admin_dispatcher) -> None:
        """Admin channel permission includes git_ops tool."""
        config = _admin_dispatch_config()
        tools = resolve_permissions(config.permissions, "admin", "dan")
        assert "git_ops" in tools

    @pytest.mark.asyncio
    async def test_general_channel_requires_approval(self, admin_dispatcher) -> None:
        """Non-admin channel hits the approval gate.

        The dispatcher resolves permissions using ``message.source``,
        so source must match a configured channel name.
        """
        msg = StandardMessage(
            source="general", channel_ref="general-thread",
            user_id="someone", content="run git push",
        )
        result = await admin_dispatcher.dispatch(msg)
        assert "approval" in result.response.lower()

    @pytest.mark.asyncio
    async def test_general_channel_cannot_access_git_ops(self) -> None:
        """Non-admin channel does not resolve git_ops tool."""
        config = _admin_dispatch_config()
        tools = resolve_permissions(config.permissions, "general", "someone")
        assert "git_ops" not in tools

    @pytest.mark.asyncio
    async def test_admin_dispatch_uses_admin_agent(self, admin_dispatcher) -> None:
        """After switching to admin agent, dispatch uses it."""
        # Create session via first message
        msg1 = StandardMessage(
            source="terminal", channel_ref="admin",
            user_id="dan", content="/switch admin",
        )
        r1 = await admin_dispatcher.dispatch(msg1)
        assert "admin" in r1.response.lower()

        # Subsequent message dispatches through admin agent
        msg2 = StandardMessage(
            source="terminal", channel_ref="admin",
            user_id="dan", content="git add .",
            session_id=r1.session_id,
        )
        r2 = await admin_dispatcher.dispatch(msg2)
        assert r2.agent_name == "admin"

    @pytest.mark.asyncio
    async def test_admin_dispatch_returns_response(self, admin_dispatcher) -> None:
        """Admin agent returns a response (mock executor echo)."""
        msg = StandardMessage(
            source="terminal", channel_ref="admin",
            user_id="dan", content="git commit -m 'test'",
        )
        result = await admin_dispatcher.dispatch(msg)
        assert result.response  # non-empty response
        assert result.session_id  # session was created


class TestAdminGitOpsToolAccess:
    """Verify git_ops tool functions work end-to-end with real git repos."""

    def test_admin_agent_add_commit_push(self, tmp_path: Path) -> None:
        """Admin agent's tools can perform the full git workflow."""
        from tools.git_ops import git_add, git_commit, git_push

        # Set up repo with bare remote
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "admin@danclaw.dev"],
            cwd=repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Admin Agent"],
            cwd=repo, capture_output=True, check=True,
        )
        (repo / "init.txt").write_text("init")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo, capture_output=True, check=True,
        )

        remote = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote)],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote)],
            cwd=repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "master"],
            cwd=repo, capture_output=True, check=True,
        )

        # Simulate admin agent performing git operations
        (repo / "update.py").write_text("# admin change")
        git_add(["update.py"], cwd=repo)
        git_commit("admin: automated update", cwd=repo)
        git_push(cwd=repo)

        # Verify remote received the commit
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=remote, capture_output=True, text=True,
        )
        assert "admin: automated update" in log.stdout

    def test_admin_tools_with_telemetry(self, tmp_path: Path) -> None:
        """Admin git ops emit telemetry events."""
        from dispatcher.telemetry import TelemetryCollector
        from tools.instrumented import git_add, git_commit

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "admin@danclaw.dev"],
            cwd=repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Admin"],
            cwd=repo, capture_output=True, check=True,
        )
        (repo / "init.txt").write_text("init")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo, capture_output=True, check=True,
        )

        collector = TelemetryCollector()
        (repo / "telemetry_test.py").write_text("# test")
        git_add(["telemetry_test.py"], cwd=repo, telemetry=collector)
        git_commit("admin: telemetry test", cwd=repo, telemetry=collector)

        assert len(collector.events) == 2
        assert collector.events[0].payload["tool"] == "git_add"
        assert collector.events[1].payload["tool"] == "git_commit"
        assert all(e.payload["success"] for e in collector.events)
