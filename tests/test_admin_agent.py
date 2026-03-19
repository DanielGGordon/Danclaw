"""Tests for admin agent configuration, permissions, and dispatch behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import (
    AgentConfig,
    ChannelPermissions,
    DanClawConfig,
    PermissionsConfig,
    UserPermissions,
    load_config,
)
from dispatcher.permissions import requires_approval, resolve_permissions


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

    def test_admin_channel_user_tools_not_additive(self) -> None:
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
        """Even if user has approval_required=True, admin channel ignores it.

        Because override=True, user approval flags are not considered.
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
                "someone": UserPermissions(approval_required=True),
            },
        )
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
        # Admin override=True: only channel tools, but deploy is already listed
        assert "deploy" in admin_tools


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

    def test_real_config_admin_no_approval_resolved(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        assert requires_approval(cfg.permissions, "admin", "dan") is False
