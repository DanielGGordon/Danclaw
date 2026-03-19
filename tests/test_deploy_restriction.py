"""Tests for deploy restriction: non-admin users/channels cannot trigger deploy.

Verifies that the restricted_tools mechanism prevents deploy and trigger_deploy
from being granted via user permissions, while the admin channel retains full
deploy access.
"""

from __future__ import annotations

import json
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
from dispatcher.dispatcher import Dispatcher
from dispatcher.executor import MockExecutor
from dispatcher.models import StandardMessage
from dispatcher.permissions import resolve_permissions
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from dispatcher.telemetry import TelemetryCollector
from tests.conftest import make_personas_dir


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_project(tmp_path: Path):
    """Create a project layout with personas and tool stubs."""
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir()
    (personas_dir / "default.md").write_text("Default persona.")
    (personas_dir / "admin.md").write_text("Admin persona.")

    config_dir = tmp_path / "config"
    config_dir.mkdir()

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    for tool in ("obsidian_read", "obsidian_write", "obsidian_search",
                 "git_ops", "deploy", "trigger_deploy"):
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


def _deploy_config(**overrides) -> dict:
    """Config with admin agent, restricted_tools, and multiple channels."""
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
                    "git_ops", "deploy", "trigger_deploy",
                ],
            },
        ],
        "permissions": {
            "channels": {
                "terminal": {
                    "allowed_tools": ["git", "obsidian"],
                    "override": False,
                },
                "slack": {
                    "allowed_tools": ["obsidian"],
                    "override": True,
                    "approval_required": True,
                },
                "admin": {
                    "allowed_tools": ["git", "obsidian", "deploy", "git_ops", "trigger_deploy"],
                    "override": True,
                    "approval_required": False,
                },
            },
            "users": {
                "dan": {"additional_tools": ["git"]},
            },
            "restricted_tools": ["deploy", "trigger_deploy"],
        },
    }
    cfg.update(overrides)
    return cfg


def _permissions_config_with_restrictions() -> PermissionsConfig:
    """Build a PermissionsConfig with restricted deploy tools."""
    return PermissionsConfig(
        channels={
            "admin": ChannelPermissions(
                allowed_tools=["git", "obsidian", "deploy", "git_ops", "trigger_deploy"],
                override=True,
                approval_required=False,
            ),
            "slack": ChannelPermissions(
                allowed_tools=["obsidian"],
                override=True,
                approval_required=True,
            ),
            "terminal": ChannelPermissions(
                allowed_tools=["git", "obsidian"],
                override=False,
            ),
        },
        users={
            "dan": UserPermissions(additional_tools=["git", "deploy", "trigger_deploy"]),
        },
        restricted_tools=frozenset({"deploy", "trigger_deploy"}),
    )


# ══════════════════════════════════════════════════════════════════════
# Permission resolver: restricted_tools filtering
# ══════════════════════════════════════════════════════════════════════


class TestRestrictedToolsPermissionResolver:
    """restricted_tools are stripped from user permissions."""

    def test_user_deploy_stripped_on_terminal(self) -> None:
        """User with deploy in additional_tools does not get it on terminal."""
        config = _permissions_config_with_restrictions()
        tools = resolve_permissions(config, "terminal", "dan")
        assert "deploy" not in tools
        assert "trigger_deploy" not in tools

    def test_user_deploy_stripped_on_unknown_channel(self) -> None:
        """User deploy tools are stripped even on unknown channels."""
        config = _permissions_config_with_restrictions()
        tools = resolve_permissions(config, "unknown-channel", "dan")
        assert "deploy" not in tools
        assert "trigger_deploy" not in tools

    def test_user_non_restricted_tools_preserved(self) -> None:
        """Non-restricted user tools are still granted."""
        config = _permissions_config_with_restrictions()
        tools = resolve_permissions(config, "terminal", "dan")
        assert "git" in tools

    def test_admin_channel_still_has_deploy(self) -> None:
        """Admin channel retains deploy tools (granted by channel, not user)."""
        config = _permissions_config_with_restrictions()
        tools = resolve_permissions(config, "admin", "dan")
        assert "deploy" in tools
        assert "trigger_deploy" in tools

    def test_admin_channel_any_user_has_deploy(self) -> None:
        """Any user on admin channel gets deploy (channel grants it)."""
        config = _permissions_config_with_restrictions()
        tools = resolve_permissions(config, "admin", "unknown-user")
        assert "deploy" in tools
        assert "trigger_deploy" in tools

    def test_slack_channel_no_deploy(self) -> None:
        """Slack channel has override=True and no deploy in channel tools."""
        config = _permissions_config_with_restrictions()
        tools = resolve_permissions(config, "slack", "dan")
        assert "deploy" not in tools
        assert "trigger_deploy" not in tools

    def test_no_restricted_tools_user_gets_deploy(self) -> None:
        """Without restricted_tools, user can add deploy on terminal."""
        config = PermissionsConfig(
            channels={
                "terminal": ChannelPermissions(
                    allowed_tools=["git", "obsidian"],
                    override=False,
                ),
            },
            users={
                "dan": UserPermissions(additional_tools=["deploy"]),
            },
            # No restricted_tools
        )
        tools = resolve_permissions(config, "terminal", "dan")
        assert "deploy" in tools

    def test_restricted_tools_empty_frozenset_no_filtering(self) -> None:
        """Empty restricted_tools does not filter anything."""
        config = PermissionsConfig(
            channels={
                "terminal": ChannelPermissions(
                    allowed_tools=["git"],
                    override=False,
                ),
            },
            users={
                "dan": UserPermissions(additional_tools=["deploy"]),
            },
            restricted_tools=frozenset(),
        )
        tools = resolve_permissions(config, "terminal", "dan")
        assert "deploy" in tools

    def test_channel_deploy_not_affected_by_restriction(self) -> None:
        """Channel-granted deploy tools are never stripped by restricted_tools."""
        config = PermissionsConfig(
            channels={
                "special": ChannelPermissions(
                    allowed_tools=["deploy", "trigger_deploy"],
                    override=False,
                ),
            },
            restricted_tools=frozenset({"deploy", "trigger_deploy"}),
        )
        tools = resolve_permissions(config, "special", "someone")
        assert "deploy" in tools
        assert "trigger_deploy" in tools


# ══════════════════════════════════════════════════════════════════════
# Config parsing: restricted_tools
# ══════════════════════════════════════════════════════════════════════


class TestRestrictedToolsConfigParsing:
    """restricted_tools are parsed from JSON config correctly."""

    def test_restricted_tools_parsed(self, tmp_project) -> None:
        path = tmp_project.write_config(_deploy_config())
        cfg = load_config(
            path,
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.permissions.restricted_tools == frozenset({"deploy", "trigger_deploy"})

    def test_restricted_tools_default_empty(self, tmp_project) -> None:
        """restricted_tools defaults to empty when omitted."""
        config_data = _deploy_config()
        del config_data["permissions"]["restricted_tools"]
        path = tmp_project.write_config(config_data)
        cfg = load_config(
            path,
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.permissions.restricted_tools == frozenset()

    def test_restricted_tools_not_a_list_raises(self, tmp_project) -> None:
        config_data = _deploy_config()
        config_data["permissions"]["restricted_tools"] = "deploy"
        path = tmp_project.write_config(config_data)
        from config.loader import ConfigError
        with pytest.raises(ConfigError, match="'permissions.restricted_tools' must be a list"):
            load_config(
                path,
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_restricted_tools_entry_not_string_raises(self, tmp_project) -> None:
        config_data = _deploy_config()
        config_data["permissions"]["restricted_tools"] = [123]
        path = tmp_project.write_config(config_data)
        from config.loader import ConfigError
        with pytest.raises(ConfigError, match="'permissions.restricted_tools' entries must be non-empty strings"):
            load_config(
                path,
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_restricted_tools_empty_string_raises(self, tmp_project) -> None:
        config_data = _deploy_config()
        config_data["permissions"]["restricted_tools"] = [""]
        path = tmp_project.write_config(config_data)
        from config.loader import ConfigError
        with pytest.raises(ConfigError, match="'permissions.restricted_tools' entries must be non-empty strings"):
            load_config(
                path,
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_restricted_tools_is_frozenset(self, tmp_project) -> None:
        path = tmp_project.write_config(_deploy_config())
        cfg = load_config(
            path,
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert isinstance(cfg.permissions.restricted_tools, frozenset)


# ══════════════════════════════════════════════════════════════════════
# Integration: real config file
# ══════════════════════════════════════════════════════════════════════


class TestRealConfigDeployRestriction:
    """Smoke tests against the real project config."""

    def test_real_config_has_restricted_tools(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        assert "deploy" in cfg.permissions.restricted_tools
        assert "trigger_deploy" in cfg.permissions.restricted_tools

    def test_real_config_admin_has_deploy(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        tools = resolve_permissions(cfg.permissions, "admin", "dan")
        assert "deploy" in tools
        assert "trigger_deploy" in tools

    def test_real_config_terminal_no_deploy(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        tools = resolve_permissions(cfg.permissions, "terminal", "dan")
        assert "deploy" not in tools
        assert "trigger_deploy" not in tools

    def test_real_config_slack_no_deploy(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        tools = resolve_permissions(cfg.permissions, "slack", "dan")
        assert "deploy" not in tools
        assert "trigger_deploy" not in tools

    def test_real_config_unknown_channel_no_deploy(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        tools = resolve_permissions(cfg.permissions, "random-channel", "dan")
        assert "deploy" not in tools
        assert "trigger_deploy" not in tools

    def test_real_config_dan_no_deploy_in_additional_tools(self) -> None:
        """User dan should not have deploy in additional_tools."""
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        dan = cfg.permissions.users.get("dan")
        assert dan is not None
        assert "deploy" not in dan.additional_tools
        assert "trigger_deploy" not in dan.additional_tools


# ══════════════════════════════════════════════════════════════════════
# Integration: dispatcher blocks deploy on non-admin channels
# ══════════════════════════════════════════════════════════════════════


def _dispatch_config_with_restrictions() -> DanClawConfig:
    """Config for dispatcher integration tests with deploy restrictions."""
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
                allowed_tools=["git_ops", "deploy", "trigger_deploy"],
            ),
        ],
        permissions=PermissionsConfig(
            channels={
                "admin": ChannelPermissions(
                    allowed_tools=["git_ops", "deploy", "trigger_deploy"],
                    override=True,
                    approval_required=False,
                ),
                "terminal": ChannelPermissions(
                    allowed_tools=["git", "obsidian"],
                    override=False,
                ),
                "slack": ChannelPermissions(
                    allowed_tools=["obsidian"],
                    override=True,
                    approval_required=True,
                ),
            },
            users={
                "dan": UserPermissions(additional_tools=["git", "deploy"]),
            },
            restricted_tools=frozenset({"deploy", "trigger_deploy"}),
        ),
    )


class TestDispatcherDeployRestriction:
    """Dispatcher passes correct allowed_tools, excluding deploy for non-admin."""

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
            "admin": "Admin persona.",
        })

    @pytest.fixture
    def executor(self):
        return MockExecutor()

    @pytest.fixture
    def telemetry(self):
        return TelemetryCollector()

    @pytest_asyncio.fixture
    async def dispatcher(self, mgr, repo, executor, personas_dir, telemetry):
        return Dispatcher(
            mgr, repo, executor,
            config=_dispatch_config_with_restrictions(),
            personas_dir=personas_dir,
            telemetry=telemetry,
        )

    @pytest.mark.asyncio
    async def test_admin_channel_executor_gets_deploy(
        self, dispatcher, executor,
    ) -> None:
        """Executor receives deploy in allowed_tools on admin channel."""
        msg = StandardMessage(
            source="admin", channel_ref="admin-thread",
            user_id="dan", content="deploy now",
        )
        await dispatcher.dispatch(msg)
        assert "deploy" in executor.last_allowed_tools
        assert "trigger_deploy" in executor.last_allowed_tools

    @pytest.mark.asyncio
    async def test_terminal_executor_no_deploy(
        self, dispatcher, executor,
    ) -> None:
        """Executor does NOT receive deploy in allowed_tools on terminal."""
        msg = StandardMessage(
            source="terminal", channel_ref="terminal-1",
            user_id="dan", content="deploy now",
        )
        await dispatcher.dispatch(msg)
        assert "deploy" not in executor.last_allowed_tools
        assert "trigger_deploy" not in executor.last_allowed_tools

    @pytest.mark.asyncio
    async def test_slack_executor_no_deploy(
        self, dispatcher, executor,
    ) -> None:
        """Executor does NOT receive deploy in allowed_tools on slack.

        Since slack has approval_required=True, the request hits the approval
        gate before reaching the executor. Verify via permissions instead.
        """
        config = _dispatch_config_with_restrictions()
        tools = resolve_permissions(config.permissions, "slack", "dan")
        assert "deploy" not in tools
        assert "trigger_deploy" not in tools

    @pytest.mark.asyncio
    async def test_terminal_user_deploy_stripped_by_restriction(
        self, dispatcher, executor,
    ) -> None:
        """Even though user 'dan' has deploy in additional_tools,
        restricted_tools strips it on the terminal channel."""
        msg = StandardMessage(
            source="terminal", channel_ref="terminal-1",
            user_id="dan", content="please deploy",
        )
        await dispatcher.dispatch(msg)
        # User's deploy was stripped, but git was kept
        assert "git" in executor.last_allowed_tools
        assert "deploy" not in executor.last_allowed_tools

    @pytest.mark.asyncio
    async def test_telemetry_permission_resolved_no_deploy(
        self, dispatcher, telemetry,
    ) -> None:
        """Telemetry records the restricted permission set on non-admin."""
        msg = StandardMessage(
            source="terminal", channel_ref="terminal-1",
            user_id="dan", content="deploy this",
        )
        await dispatcher.dispatch(msg)
        perm_events = [
            e for e in telemetry.events
            if e.event_type == "permission_resolved"
        ]
        assert len(perm_events) == 1
        # Deploy is not in allowed tools, so count reflects that
        allowed_count = perm_events[0].payload["allowed_tools_count"]
        config = _dispatch_config_with_restrictions()
        expected_tools = resolve_permissions(
            config.permissions, "terminal", "dan",
        )
        assert allowed_count == len(expected_tools)
