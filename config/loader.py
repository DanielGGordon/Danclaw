"""Config loader: reads and validates the DanClaw JSON config file."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for a single agent."""

    name: str
    persona: str
    backend_preference: list[str]
    tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DanClawConfig:
    """Top-level configuration object."""

    agents: list[AgentConfig]
    listeners: dict = field(default_factory=dict)


class ConfigError(Exception):
    """Raised when the config file is invalid or cannot be loaded."""


_REQUIRED_AGENT_FIELDS = ("name", "persona", "backend_preference")


def load_config(
    config_path: str | Path,
    *,
    personas_dir: str | Path | None = None,
) -> DanClawConfig:
    """Load and validate the JSON config file.

    Args:
        config_path: Path to the JSON config file.
        personas_dir: Path to the personas directory. Defaults to
            ``<config_path>/../personas``.

    Returns:
        A validated :class:`DanClawConfig` instance.

    Raises:
        ConfigError: If the file is missing, malformed, or fails validation.
    """
    config_path = Path(config_path)
    if personas_dir is None:
        personas_dir = config_path.parent.parent / "personas"
    else:
        personas_dir = Path(personas_dir)

    # --- Read file ---
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read config file: {exc}") from exc

    # --- Parse JSON ---
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in config file: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("Config file must contain a JSON object at the top level")

    # --- Validate agents ---
    if "agents" not in data:
        raise ConfigError("Config is missing required key: 'agents'")

    agents_raw = data["agents"]
    if not isinstance(agents_raw, list):
        raise ConfigError("'agents' must be a list")
    if len(agents_raw) == 0:
        raise ConfigError("'agents' list must not be empty")

    agents: list[AgentConfig] = []
    for idx, agent_data in enumerate(agents_raw):
        if not isinstance(agent_data, dict):
            raise ConfigError(f"agents[{idx}]: must be a JSON object")

        for field_name in _REQUIRED_AGENT_FIELDS:
            if field_name not in agent_data:
                raise ConfigError(
                    f"agents[{idx}] ({agent_data.get('name', '?')}): "
                    f"missing required field '{field_name}'"
                )

        name = agent_data["name"]
        persona = agent_data["persona"]
        backend_preference = agent_data["backend_preference"]
        tools = agent_data.get("tools", [])

        # Type checks
        if not isinstance(name, str) or not name:
            raise ConfigError(f"agents[{idx}]: 'name' must be a non-empty string")
        if not isinstance(persona, str) or not persona:
            raise ConfigError(f"agents[{idx}] ({name}): 'persona' must be a non-empty string")
        if not isinstance(backend_preference, list) or len(backend_preference) == 0:
            raise ConfigError(
                f"agents[{idx}] ({name}): 'backend_preference' must be a non-empty list"
            )
        for bp in backend_preference:
            if not isinstance(bp, str):
                raise ConfigError(
                    f"agents[{idx}] ({name}): 'backend_preference' entries must be strings"
                )
        if not isinstance(tools, list):
            raise ConfigError(f"agents[{idx}] ({name}): 'tools' must be a list")
        for t in tools:
            if not isinstance(t, str):
                raise ConfigError(
                    f"agents[{idx}] ({name}): 'tools' entries must be strings"
                )

        # Validate persona file exists
        persona_file = personas_dir / f"{persona}.md"
        if not persona_file.exists():
            raise ConfigError(
                f"agents[{idx}] ({name}): persona file not found: {persona_file}"
            )

        agents.append(
            AgentConfig(
                name=name,
                persona=persona,
                backend_preference=list(backend_preference),
                tools=list(tools),
            )
        )

    # --- Validate listeners (loose for now) ---
    listeners = data.get("listeners", {})
    if not isinstance(listeners, dict):
        raise ConfigError("'listeners' must be a JSON object")

    return DanClawConfig(agents=agents, listeners=listeners)
