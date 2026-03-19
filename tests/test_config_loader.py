"""Tests for config.loader — config loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.loader import AgentConfig, ConfigError, DanClawConfig, load_config


@pytest.fixture()
def tmp_project(tmp_path: Path):
    """Create a minimal project layout with config and personas."""
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir()
    (personas_dir / "default.md").write_text("You are the default agent.")

    config_dir = tmp_path / "config"
    config_dir.mkdir()

    class _Project:
        root = tmp_path
        personas = personas_dir
        config = config_dir

        def write_config(self, data: dict) -> Path:
            p = self.config / "danclaw.json"
            p.write_text(json.dumps(data))
            return p

        def add_persona(self, name: str, content: str = "persona text") -> None:
            (self.personas / f"{name}.md").write_text(content)

    return _Project()


# ── Happy path ──────────────────────────────────────────────────────────


def test_load_valid_config(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {
                    "name": "default",
                    "persona": "default",
                    "backend_preference": ["claude", "codex"],
                    "allowed_tools": [],
                }
            ],
            "listeners": {},
        }
    )
    cfg = load_config(path, personas_dir=tmp_project.personas)

    assert isinstance(cfg, DanClawConfig)
    assert len(cfg.agents) == 1
    assert cfg.agents[0].name == "default"
    assert cfg.agents[0].persona == "default"
    assert cfg.agents[0].backend_preference == ["claude", "codex"]
    assert cfg.agents[0].allowed_tools == []
    assert cfg.listeners == {}


def test_load_multiple_agents(tmp_project):
    tmp_project.add_persona("coder")
    path = tmp_project.write_config(
        {
            "agents": [
                {
                    "name": "default",
                    "persona": "default",
                    "backend_preference": ["claude"],
                },
                {
                    "name": "coder",
                    "persona": "coder",
                    "backend_preference": ["codex", "claude"],
                    "allowed_tools": ["git", "obsidian"],
                },
            ],
        }
    )
    cfg = load_config(path, personas_dir=tmp_project.personas)
    assert len(cfg.agents) == 2
    assert cfg.agents[1].name == "coder"
    assert cfg.agents[1].allowed_tools == ["git", "obsidian"]


def test_allowed_tools_default_to_empty(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {
                    "name": "default",
                    "persona": "default",
                    "backend_preference": ["claude"],
                }
            ],
        }
    )
    cfg = load_config(path, personas_dir=tmp_project.personas)
    assert cfg.agents[0].allowed_tools == []


def test_listeners_default_to_empty(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {
                    "name": "default",
                    "persona": "default",
                    "backend_preference": ["claude"],
                }
            ],
        }
    )
    cfg = load_config(path, personas_dir=tmp_project.personas)
    assert cfg.listeners == {}


# ── File-level errors ───────────────────────────────────────────────────


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path / "nonexistent.json")


def test_invalid_json(tmp_project):
    path = tmp_project.config / "danclaw.json"
    path.write_text("{bad json")
    with pytest.raises(ConfigError, match="Invalid JSON"):
        load_config(path, personas_dir=tmp_project.personas)


def test_top_level_not_object(tmp_project):
    path = tmp_project.config / "danclaw.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ConfigError, match="JSON object at the top level"):
        load_config(path, personas_dir=tmp_project.personas)


# ── agents validation ──────────────────────────────────────────────────


def test_missing_agents_key(tmp_project):
    path = tmp_project.write_config({"listeners": {}})
    with pytest.raises(ConfigError, match="missing required key: 'agents'"):
        load_config(path, personas_dir=tmp_project.personas)


def test_agents_not_a_list(tmp_project):
    path = tmp_project.write_config({"agents": "not a list"})
    with pytest.raises(ConfigError, match="'agents' must be a list"):
        load_config(path, personas_dir=tmp_project.personas)


def test_agents_empty_list(tmp_project):
    path = tmp_project.write_config({"agents": []})
    with pytest.raises(ConfigError, match="must not be empty"):
        load_config(path, personas_dir=tmp_project.personas)


def test_agent_not_a_dict(tmp_project):
    path = tmp_project.write_config({"agents": ["not a dict"]})
    with pytest.raises(ConfigError, match="must be a JSON object"):
        load_config(path, personas_dir=tmp_project.personas)


# ── Missing required agent fields ──────────────────────────────────────


@pytest.mark.parametrize("missing_field", ["name", "persona", "backend_preference"])
def test_agent_missing_required_field(tmp_project, missing_field):
    agent = {
        "name": "default",
        "persona": "default",
        "backend_preference": ["claude"],
    }
    del agent[missing_field]
    path = tmp_project.write_config({"agents": [agent]})
    with pytest.raises(ConfigError, match=f"missing required field '{missing_field}'"):
        load_config(path, personas_dir=tmp_project.personas)


# ── Type validation ────────────────────────────────────────────────────


def test_agent_name_empty_string(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {"name": "", "persona": "default", "backend_preference": ["claude"]}
            ]
        }
    )
    with pytest.raises(ConfigError, match="'name' must be a non-empty string"):
        load_config(path, personas_dir=tmp_project.personas)


def test_agent_backend_preference_empty(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {"name": "a", "persona": "default", "backend_preference": []}
            ]
        }
    )
    with pytest.raises(ConfigError, match="'backend_preference' must be a non-empty list"):
        load_config(path, personas_dir=tmp_project.personas)


def test_agent_backend_preference_not_strings(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {"name": "a", "persona": "default", "backend_preference": [123]}
            ]
        }
    )
    with pytest.raises(ConfigError, match="entries must be strings"):
        load_config(path, personas_dir=tmp_project.personas)


def test_agent_allowed_tools_not_a_list(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {
                    "name": "a",
                    "persona": "default",
                    "backend_preference": ["claude"],
                    "allowed_tools": "not-a-list",
                }
            ]
        }
    )
    with pytest.raises(ConfigError, match="'allowed_tools' must be a list"):
        load_config(path, personas_dir=tmp_project.personas)


def test_agent_allowed_tools_entries_must_be_strings(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {
                    "name": "a",
                    "persona": "default",
                    "backend_preference": ["claude"],
                    "allowed_tools": [123],
                }
            ]
        }
    )
    with pytest.raises(ConfigError, match="'allowed_tools' entries must be non-empty strings"):
        load_config(path, personas_dir=tmp_project.personas)


def test_agent_allowed_tools_empty_string_rejected(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {
                    "name": "a",
                    "persona": "default",
                    "backend_preference": ["claude"],
                    "allowed_tools": [""],
                }
            ]
        }
    )
    with pytest.raises(ConfigError, match="'allowed_tools' entries must be non-empty strings"):
        load_config(path, personas_dir=tmp_project.personas)


def test_agent_duplicate_names_rejected(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {
                    "name": "default",
                    "persona": "default",
                    "backend_preference": ["claude"],
                },
                {
                    "name": "default",
                    "persona": "default",
                    "backend_preference": ["codex"],
                },
            ]
        }
    )
    with pytest.raises(ConfigError, match="duplicate agent name"):
        load_config(path, personas_dir=tmp_project.personas)


def test_listeners_not_a_dict(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {"name": "default", "persona": "default", "backend_preference": ["claude"]}
            ],
            "listeners": "bad",
        }
    )
    with pytest.raises(ConfigError, match="'listeners' must be a JSON object"):
        load_config(path, personas_dir=tmp_project.personas)


# ── Persona file validation ────────────────────────────────────────────


def test_missing_persona_file(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {
                    "name": "ghost",
                    "persona": "nonexistent",
                    "backend_preference": ["claude"],
                }
            ]
        }
    )
    with pytest.raises(ConfigError, match="persona file not found"):
        load_config(path, personas_dir=tmp_project.personas)


# ── Default personas_dir resolution ────────────────────────────────────


def test_personas_dir_defaults_to_sibling(tmp_project):
    """When personas_dir is not given, it defaults to ../personas relative to config."""
    path = tmp_project.write_config(
        {
            "agents": [
                {"name": "default", "persona": "default", "backend_preference": ["claude"]}
            ]
        }
    )
    # personas/ is at tmp_project.root / "personas", config is at tmp_project.root / "config"
    cfg = load_config(path)
    assert cfg.agents[0].persona == "default"


# ── Frozen dataclasses ──────────────────────────────────────────────────


def test_config_is_immutable(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {"name": "default", "persona": "default", "backend_preference": ["claude"]}
            ]
        }
    )
    cfg = load_config(path, personas_dir=tmp_project.personas)
    with pytest.raises(AttributeError):
        cfg.agents = []
    with pytest.raises(AttributeError):
        cfg.agents[0].name = "other"


# ── Integration: load real project config ──────────────────────────────


def test_load_real_config():
    """Smoke test: load the actual project config file."""
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config" / "danclaw.json"
    personas_dir = project_root / "personas"
    cfg = load_config(config_path, personas_dir=personas_dir)
    assert len(cfg.agents) >= 1
    assert cfg.agents[0].name == "default"
