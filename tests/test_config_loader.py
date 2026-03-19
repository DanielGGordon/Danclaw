"""Tests for config.loader — config loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.loader import (
    AgentConfig,
    ConfigError,
    DanClawConfig,
    ObsidianToolConfig,
    TelemetryConfig,
    ToolsConfig,
    load_config,
    validate_config,
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
            """Create a stub tool script in the tools directory."""
            (self.tools / f"{name}{ext}").write_text(f"# stub tool: {name}")

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
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)

    assert isinstance(cfg, DanClawConfig)
    assert len(cfg.agents) == 1
    assert cfg.agents[0].name == "default"
    assert cfg.agents[0].persona == "default"
    assert cfg.agents[0].backend_preference == ["claude", "codex"]
    assert cfg.agents[0].allowed_tools == []
    assert cfg.listeners == {}


def test_load_multiple_agents(tmp_project):
    tmp_project.add_persona("coder")
    tmp_project.add_tool("git")
    tmp_project.add_tool("obsidian")
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
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
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
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
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
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
    assert cfg.listeners == {}


# ── File-level errors ───────────────────────────────────────────────────


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path / "nonexistent.json")


def test_invalid_json(tmp_project):
    path = tmp_project.config / "danclaw.json"
    path.write_text("{bad json")
    with pytest.raises(ConfigError, match="Invalid JSON"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_top_level_not_object(tmp_project):
    path = tmp_project.config / "danclaw.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ConfigError, match="JSON object at the top level"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


# ── agents validation ──────────────────────────────────────────────────


def test_missing_agents_key(tmp_project):
    path = tmp_project.write_config({"listeners": {}})
    with pytest.raises(ConfigError, match="missing required key: 'agents'"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_agents_not_a_list(tmp_project):
    path = tmp_project.write_config({"agents": "not a list"})
    with pytest.raises(ConfigError, match="'agents' must be a list"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_agents_empty_list(tmp_project):
    path = tmp_project.write_config({"agents": []})
    with pytest.raises(ConfigError, match="must not be empty"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_agent_not_a_dict(tmp_project):
    path = tmp_project.write_config({"agents": ["not a dict"]})
    with pytest.raises(ConfigError, match="must be a JSON object"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


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
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


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
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_agent_backend_preference_empty(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {"name": "a", "persona": "default", "backend_preference": []}
            ]
        }
    )
    with pytest.raises(ConfigError, match="'backend_preference' must be a non-empty list"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


def test_agent_backend_preference_not_strings(tmp_project):
    path = tmp_project.write_config(
        {
            "agents": [
                {"name": "a", "persona": "default", "backend_preference": [123]}
            ]
        }
    )
    with pytest.raises(ConfigError, match="entries must be strings"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


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
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


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
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


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
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


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
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


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
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


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
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


# ── Config validation (personas + tools) ─────────────────────────────


def test_validate_config_valid(tmp_project):
    """Valid config with existing persona and tools passes validation."""
    tmp_project.add_tool("git")
    tmp_project.add_tool("obsidian")
    config = DanClawConfig(
        agents=[
            AgentConfig(
                name="default",
                persona="default",
                backend_preference=["claude"],
                allowed_tools=["git", "obsidian"],
            ),
        ],
    )
    # Should not raise
    validate_config(
        config, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools
    )


def test_validate_config_missing_persona(tmp_project):
    """Missing persona file produces a clear error."""
    config = DanClawConfig(
        agents=[
            AgentConfig(
                name="ghost",
                persona="nonexistent",
                backend_preference=["claude"],
            ),
        ],
    )
    with pytest.raises(ConfigError, match="persona file not found.*nonexistent"):
        validate_config(
            config, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools
        )


def test_validate_config_missing_tool(tmp_project):
    """Missing tool script produces a clear error."""
    config = DanClawConfig(
        agents=[
            AgentConfig(
                name="default",
                persona="default",
                backend_preference=["claude"],
                allowed_tools=["no_such_tool"],
            ),
        ],
    )
    with pytest.raises(ConfigError, match="tool script not found.*no_such_tool"):
        validate_config(
            config, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools
        )


def test_validate_config_multiple_missing_items(tmp_project):
    """All missing personas and tools are reported in a single error."""
    config = DanClawConfig(
        agents=[
            AgentConfig(
                name="agent_a",
                persona="missing_persona_a",
                backend_preference=["claude"],
                allowed_tools=["missing_tool_x"],
            ),
            AgentConfig(
                name="agent_b",
                persona="missing_persona_b",
                backend_preference=["claude"],
                allowed_tools=["missing_tool_y"],
            ),
        ],
    )
    with pytest.raises(ConfigError) as exc_info:
        validate_config(
            config, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools
        )
    msg = str(exc_info.value)
    assert "missing_persona_a" in msg
    assert "missing_persona_b" in msg
    assert "missing_tool_x" in msg
    assert "missing_tool_y" in msg


def test_validate_config_tool_with_extension(tmp_project):
    """Tool scripts with various extensions are found correctly."""
    tmp_project.add_tool("git", ext=".sh")
    tmp_project.add_tool("obsidian", ext=".py")
    config = DanClawConfig(
        agents=[
            AgentConfig(
                name="default",
                persona="default",
                backend_preference=["claude"],
                allowed_tools=["git", "obsidian"],
            ),
        ],
    )
    # Should not raise
    validate_config(
        config, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools
    )


def test_load_config_rejects_missing_tool(tmp_project):
    """load_config also catches missing tools via validate_config."""
    path = tmp_project.write_config(
        {
            "agents": [
                {
                    "name": "default",
                    "persona": "default",
                    "backend_preference": ["claude"],
                    "allowed_tools": ["nonexistent_tool"],
                }
            ]
        }
    )
    with pytest.raises(ConfigError, match="tool script not found.*nonexistent_tool"):
        load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)


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
    cfg = load_config(path, personas_dir=tmp_project.personas, tools_dir=tmp_project.tools)
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


# ── AgentConfig timeout field ────────────────────────────────────────


class TestAgentConfigTimeout:
    def test_default_timeout_is_120(self):
        agent = AgentConfig(
            name="a", persona="default", backend_preference=["claude"],
        )
        assert agent.timeout == 120

    def test_custom_timeout(self):
        agent = AgentConfig(
            name="a", persona="default", backend_preference=["claude"],
            timeout=60,
        )
        assert agent.timeout == 60

    def test_timeout_loaded_from_json(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "fast",
                "persona": "default",
                "backend_preference": ["claude"],
                "timeout": 30,
            }],
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.agents[0].timeout == 30

    def test_timeout_defaults_to_120_when_omitted(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.agents[0].timeout == 120

    def test_timeout_zero_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "bad",
                "persona": "default",
                "backend_preference": ["claude"],
                "timeout": 0,
            }],
        })
        with pytest.raises(ConfigError, match="timeout.*positive integer"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_timeout_negative_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "bad",
                "persona": "default",
                "backend_preference": ["claude"],
                "timeout": -10,
            }],
        })
        with pytest.raises(ConfigError, match="timeout.*positive integer"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_timeout_string_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "bad",
                "persona": "default",
                "backend_preference": ["claude"],
                "timeout": "fast",
            }],
        })
        with pytest.raises(ConfigError, match="timeout.*positive integer"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )


# ── AgentConfig fallback_notification field ──────────────────────────


class TestAgentConfigFallbackNotification:
    def test_default_is_silent(self):
        agent = AgentConfig(
            name="a", persona="default", backend_preference=["claude"],
        )
        assert agent.fallback_notification == "silent"

    def test_notify_mode(self):
        agent = AgentConfig(
            name="a", persona="default", backend_preference=["claude"],
            fallback_notification="notify",
        )
        assert agent.fallback_notification == "notify"

    def test_custom_string(self):
        agent = AgentConfig(
            name="a", persona="default", backend_preference=["claude"],
            fallback_notification="Using backup model.",
        )
        assert agent.fallback_notification == "Using backup model."

    def test_loaded_from_json(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
                "fallback_notification": "notify",
            }],
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.agents[0].fallback_notification == "notify"

    def test_defaults_to_silent_when_omitted(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.agents[0].fallback_notification == "silent"

    def test_custom_string_loaded_from_json(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
                "fallback_notification": "Backup AI is responding.",
            }],
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.agents[0].fallback_notification == "Backup AI is responding."

    def test_empty_string_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "bad",
                "persona": "default",
                "backend_preference": ["claude"],
                "fallback_notification": "",
            }],
        })
        with pytest.raises(ConfigError, match="fallback_notification.*non-empty string"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_non_string_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "bad",
                "persona": "default",
                "backend_preference": ["claude"],
                "fallback_notification": 42,
            }],
        })
        with pytest.raises(ConfigError, match="fallback_notification.*non-empty string"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )


# ── Tools config ─────────────────────────────────────────────────────


class TestToolsConfig:
    """Tests for the tools configuration section."""

    def test_tools_defaults_to_empty_when_omitted(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.tools == ToolsConfig()
        assert cfg.tools.obsidian is None

    def test_tools_empty_object_is_valid(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "tools": {},
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.tools.obsidian is None

    def test_tools_not_a_dict_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "tools": "bad",
        })
        with pytest.raises(ConfigError, match="'tools' must be a JSON object"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_obsidian_vault_path_loaded(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "tools": {
                "obsidian": {"vault_path": "/home/user/vault"},
            },
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.tools.obsidian is not None
        assert cfg.tools.obsidian.vault_path == "/home/user/vault"

    def test_obsidian_not_a_dict_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "tools": {"obsidian": "bad"},
        })
        with pytest.raises(ConfigError, match="'tools.obsidian' must be a JSON object"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_obsidian_missing_vault_path_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "tools": {"obsidian": {}},
        })
        with pytest.raises(ConfigError, match="missing required field 'vault_path'"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_obsidian_vault_path_empty_string_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "tools": {"obsidian": {"vault_path": ""}},
        })
        with pytest.raises(ConfigError, match="vault_path.*non-empty string"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_obsidian_vault_path_not_string_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "tools": {"obsidian": {"vault_path": 123}},
        })
        with pytest.raises(ConfigError, match="vault_path.*non-empty string"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_obsidian_tool_config_is_frozen(self):
        oc = ObsidianToolConfig(vault_path="/some/path")
        with pytest.raises(AttributeError):
            oc.vault_path = "/other"

    def test_tools_config_is_frozen(self):
        tc = ToolsConfig()
        with pytest.raises(AttributeError):
            tc.obsidian = None

    def test_tools_config_on_danclaw_config_is_frozen(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "tools": {
                "obsidian": {"vault_path": "/vault"},
            },
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        with pytest.raises(AttributeError):
            cfg.tools = ToolsConfig()


# ── Telemetry config ─────────────────────────────────────────────────


class TestTelemetryConfig:
    """Tests for the telemetry configuration section."""

    def test_telemetry_defaults_when_omitted(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.telemetry == TelemetryConfig()
        assert cfg.telemetry.slack_log_channel is None

    def test_telemetry_empty_object_is_valid(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "telemetry": {},
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.telemetry.slack_log_channel is None

    def test_telemetry_slack_log_channel_loaded(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "telemetry": {
                "slack_log_channel": "C0123456789",
            },
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.telemetry.slack_log_channel == "C0123456789"

    def test_telemetry_not_a_dict_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "telemetry": "bad",
        })
        with pytest.raises(ConfigError, match="'telemetry' must be a JSON object"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_slack_log_channel_empty_string_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "telemetry": {"slack_log_channel": ""},
        })
        with pytest.raises(ConfigError, match="slack_log_channel.*non-empty string"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_slack_log_channel_non_string_raises_config_error(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "telemetry": {"slack_log_channel": 12345},
        })
        with pytest.raises(ConfigError, match="slack_log_channel.*non-empty string"):
            load_config(
                tmp_project.config / "danclaw.json",
                personas_dir=tmp_project.personas,
                tools_dir=tmp_project.tools,
            )

    def test_telemetry_config_is_frozen(self):
        tc = TelemetryConfig(slack_log_channel="C123")
        with pytest.raises(AttributeError):
            tc.slack_log_channel = "C456"

    def test_telemetry_config_on_danclaw_config_is_frozen(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "telemetry": {"slack_log_channel": "C123"},
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        with pytest.raises(AttributeError):
            cfg.telemetry = TelemetryConfig()

    def test_slack_log_channel_null_is_valid(self, tmp_project):
        tmp_project.write_config({
            "agents": [{
                "name": "default",
                "persona": "default",
                "backend_preference": ["claude"],
            }],
            "telemetry": {"slack_log_channel": None},
        })
        cfg = load_config(
            tmp_project.config / "danclaw.json",
            personas_dir=tmp_project.personas,
            tools_dir=tmp_project.tools,
        )
        assert cfg.telemetry.slack_log_channel is None
