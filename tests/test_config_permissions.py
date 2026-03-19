"""Tests for permission definitions in config: per-channel and per-user."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.loader import (
    ChannelPermissions,
    ConfigError,
    DanClawConfig,
    PermissionsConfig,
    UserPermissions,
    load_config,
)


@pytest.fixture()
def tmp_project(tmp_path: Path):
    """Create a minimal project layout with config and personas."""
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir()
    (personas_dir / "default.md").write_text("You are the default agent.")

    config_dir = tmp_path / "config"
    config_dir.mkdir()

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()

    class _Project:
        root = tmp_path
        personas = personas_dir
        config = config_dir
        tools = tools_dir

        def write_config(self, data: dict) -> Path:
            p = self.config / "danclaw.json"
            p.write_text(json.dumps(data))
            return p

        def add_persona(self, name: str, content: str = "persona text") -> None:
            (self.personas / f"{name}.md").write_text(content)

        def add_tool(self, name: str, ext: str = ".py") -> None:
            (self.tools / f"{name}{ext}").write_text(f"# stub tool: {name}")

    return _Project()


def _base_config(**overrides):
    """Return a minimal valid config dict, with optional overrides."""
    cfg = {
        "agents": [
            {
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }
        ],
    }
    cfg.update(overrides)
    return cfg


# ── Happy path: permissions omitted ────────────────────────────────────


def test_permissions_default_when_omitted(tmp_project):
    """Permissions default to empty channels and users when not provided."""
    path = tmp_project.write_config(_base_config())
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    assert isinstance(cfg.permissions, PermissionsConfig)
    assert cfg.permissions.channels == {}
    assert cfg.permissions.users == {}


def test_permissions_default_with_empty_object(tmp_project):
    """An explicit empty permissions object is valid."""
    path = tmp_project.write_config(_base_config(permissions={}))
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    assert cfg.permissions.channels == {}
    assert cfg.permissions.users == {}


# ── Happy path: channel permissions ────────────────────────────────────


def test_channel_permissions_parsed(tmp_project):
    """Channel permissions with allowed_tools and override are parsed."""
    path = tmp_project.write_config(
        _base_config(
            permissions={
                "channels": {
                    "terminal": {
                        "allowed_tools": ["git", "obsidian"],
                        "override": False,
                    },
                    "slack": {
                        "allowed_tools": ["obsidian"],
                        "override": True,
                    },
                },
            }
        )
    )
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    assert len(cfg.permissions.channels) == 2

    terminal = cfg.permissions.channels["terminal"]
    assert isinstance(terminal, ChannelPermissions)
    assert terminal.allowed_tools == ["git", "obsidian"]
    assert terminal.override is False

    slack = cfg.permissions.channels["slack"]
    assert slack.allowed_tools == ["obsidian"]
    assert slack.override is True


def test_channel_permissions_defaults(tmp_project):
    """Channel with no allowed_tools or override gets defaults."""
    path = tmp_project.write_config(
        _base_config(permissions={"channels": {"terminal": {}}})
    )
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    terminal = cfg.permissions.channels["terminal"]
    assert terminal.allowed_tools == []
    assert terminal.override is False


# ── Happy path: user permissions ───────────────────────────────────────


def test_user_permissions_parsed(tmp_project):
    """User permissions with additional_tools are parsed."""
    path = tmp_project.write_config(
        _base_config(
            permissions={
                "users": {
                    "dan": {"additional_tools": ["git", "deploy"]},
                    "alice": {"additional_tools": ["obsidian"]},
                },
            }
        )
    )
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    assert len(cfg.permissions.users) == 2

    dan = cfg.permissions.users["dan"]
    assert isinstance(dan, UserPermissions)
    assert dan.additional_tools == ["git", "deploy"]

    alice = cfg.permissions.users["alice"]
    assert alice.additional_tools == ["obsidian"]


def test_user_permissions_defaults(tmp_project):
    """User with empty object gets default empty additional_tools."""
    path = tmp_project.write_config(
        _base_config(permissions={"users": {"dan": {}}})
    )
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    dan = cfg.permissions.users["dan"]
    assert dan.additional_tools == []


# ── Happy path: full permissions ───────────────────────────────────────


def test_full_permissions_config(tmp_project):
    """Both channels and users together are parsed correctly."""
    path = tmp_project.write_config(
        _base_config(
            permissions={
                "channels": {
                    "terminal": {"allowed_tools": ["git"], "override": False},
                },
                "users": {
                    "dan": {"additional_tools": ["deploy"]},
                },
            }
        )
    )
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    assert "terminal" in cfg.permissions.channels
    assert "dan" in cfg.permissions.users


# ── Error handling: permissions not an object ──────────────────────────


def test_permissions_not_a_dict(tmp_project):
    path = tmp_project.write_config(_base_config(permissions="bad"))
    with pytest.raises(ConfigError, match="'permissions' must be a JSON object"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_permissions_channels_not_a_dict(tmp_project):
    path = tmp_project.write_config(_base_config(permissions={"channels": "bad"}))
    with pytest.raises(ConfigError, match="'permissions.channels' must be a JSON object"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_permissions_users_not_a_dict(tmp_project):
    path = tmp_project.write_config(_base_config(permissions={"users": "bad"}))
    with pytest.raises(ConfigError, match="'permissions.users' must be a JSON object"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


# ── Error handling: channel permission entries ─────────────────────────


def test_channel_entry_not_a_dict(tmp_project):
    path = tmp_project.write_config(
        _base_config(permissions={"channels": {"terminal": "bad"}})
    )
    with pytest.raises(ConfigError, match="permissions.channels\\['terminal'\\].*must be a JSON object"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_channel_allowed_tools_not_a_list(tmp_project):
    path = tmp_project.write_config(
        _base_config(permissions={"channels": {"terminal": {"allowed_tools": "bad"}}})
    )
    with pytest.raises(ConfigError, match="'allowed_tools' must be a list"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_channel_allowed_tools_entry_not_string(tmp_project):
    path = tmp_project.write_config(
        _base_config(permissions={"channels": {"terminal": {"allowed_tools": [123]}}})
    )
    with pytest.raises(ConfigError, match="'allowed_tools' entries must be non-empty strings"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_channel_allowed_tools_entry_empty_string(tmp_project):
    path = tmp_project.write_config(
        _base_config(permissions={"channels": {"terminal": {"allowed_tools": [""]}}})
    )
    with pytest.raises(ConfigError, match="'allowed_tools' entries must be non-empty strings"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_channel_override_not_bool(tmp_project):
    path = tmp_project.write_config(
        _base_config(permissions={"channels": {"terminal": {"override": "yes"}}})
    )
    with pytest.raises(ConfigError, match="'override' must be a boolean"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


# ── Error handling: user permission entries ────────────────────────────


def test_user_entry_not_a_dict(tmp_project):
    path = tmp_project.write_config(
        _base_config(permissions={"users": {"dan": "bad"}})
    )
    with pytest.raises(ConfigError, match="permissions.users\\['dan'\\].*must be a JSON object"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_user_additional_tools_not_a_list(tmp_project):
    path = tmp_project.write_config(
        _base_config(permissions={"users": {"dan": {"additional_tools": "bad"}}})
    )
    with pytest.raises(ConfigError, match="'additional_tools' must be a list"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_user_additional_tools_entry_not_string(tmp_project):
    path = tmp_project.write_config(
        _base_config(permissions={"users": {"dan": {"additional_tools": [42]}}})
    )
    with pytest.raises(ConfigError, match="'additional_tools' entries must be non-empty strings"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_user_additional_tools_entry_empty_string(tmp_project):
    path = tmp_project.write_config(
        _base_config(permissions={"users": {"dan": {"additional_tools": [""]}}})
    )
    with pytest.raises(ConfigError, match="'additional_tools' entries must be non-empty strings"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


# ── Frozen dataclasses ─────────────────────────────────────────────────


def test_channel_permissions_immutable():
    cp = ChannelPermissions(allowed_tools=["git"], override=False)
    with pytest.raises(AttributeError):
        cp.allowed_tools = []
    with pytest.raises(AttributeError):
        cp.override = True


def test_user_permissions_immutable():
    up = UserPermissions(additional_tools=["deploy"])
    with pytest.raises(AttributeError):
        up.additional_tools = []


def test_permissions_config_immutable():
    pc = PermissionsConfig()
    with pytest.raises(AttributeError):
        pc.channels = {}
    with pytest.raises(AttributeError):
        pc.users = {}


# ── Integration: load real project config ──────────────────────────────


def test_load_real_config_has_permissions():
    """Smoke test: the real config file includes permissions."""
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config" / "danclaw.json"
    personas_dir = project_root / "personas"
    cfg = load_config(config_path, personas_dir=personas_dir)
    assert isinstance(cfg.permissions, PermissionsConfig)
    assert "terminal" in cfg.permissions.channels
    assert "slack" in cfg.permissions.channels
    assert cfg.permissions.channels["slack"].override is True
    assert cfg.permissions.channels["slack"].approval_required is True
    assert "dan" in cfg.permissions.users


# ── Approval required: channel ────────────────────────────────────────


def test_channel_approval_required_parsed(tmp_project):
    """Channel approval_required is parsed correctly."""
    path = tmp_project.write_config(
        _base_config(
            permissions={
                "channels": {
                    "slack": {
                        "allowed_tools": ["git"],
                        "approval_required": True,
                    },
                },
            }
        )
    )
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    assert cfg.permissions.channels["slack"].approval_required is True


def test_channel_approval_required_defaults_false(tmp_project):
    """Channel approval_required defaults to False when omitted."""
    path = tmp_project.write_config(
        _base_config(permissions={"channels": {"slack": {}}})
    )
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    assert cfg.permissions.channels["slack"].approval_required is False


def test_channel_approval_required_not_bool(tmp_project):
    """Non-boolean approval_required on channel raises ConfigError."""
    path = tmp_project.write_config(
        _base_config(
            permissions={"channels": {"slack": {"approval_required": "yes"}}}
        )
    )
    with pytest.raises(ConfigError, match="'approval_required' must be a boolean"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


# ── Approval required: user ───────────────────────────────────────────


def test_user_approval_required_parsed(tmp_project):
    """User approval_required is parsed correctly."""
    path = tmp_project.write_config(
        _base_config(
            permissions={
                "users": {
                    "dan": {
                        "additional_tools": ["deploy"],
                        "approval_required": True,
                    },
                },
            }
        )
    )
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    assert cfg.permissions.users["dan"].approval_required is True


def test_user_approval_required_defaults_false(tmp_project):
    """User approval_required defaults to False when omitted."""
    path = tmp_project.write_config(
        _base_config(permissions={"users": {"dan": {}}})
    )
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    assert cfg.permissions.users["dan"].approval_required is False


def test_user_approval_required_not_bool(tmp_project):
    """Non-boolean approval_required on user raises ConfigError."""
    path = tmp_project.write_config(
        _base_config(
            permissions={"users": {"dan": {"approval_required": 1}}}
        )
    )
    with pytest.raises(ConfigError, match="'approval_required' must be a boolean"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
