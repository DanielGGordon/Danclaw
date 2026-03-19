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
    allowed_tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DanClawConfig:
    """Top-level configuration object."""

    agents: list[AgentConfig]
    listeners: dict = field(default_factory=dict)

    @property
    def default_agent(self) -> AgentConfig:
        """Return the first agent in the list as the default.

        Raises
        ------
        ConfigError
            If there are no agents configured.
        """
        if not self.agents:
            raise ConfigError("No agents configured")
        return self.agents[0]

    def get_agent(self, name: str) -> AgentConfig | None:
        """Look up an agent by name.

        Returns ``None`` if no agent with that name exists.
        """
        for agent in self.agents:
            if agent.name == name:
                return agent
        return None


class ConfigError(Exception):
    """Raised when the config file is invalid or cannot be loaded."""


_REQUIRED_AGENT_FIELDS = ("name", "persona", "backend_preference")


def validate_config(
    config: DanClawConfig,
    *,
    personas_dir: str | Path,
    tools_dir: str | Path,
) -> None:
    """Validate that all agent references resolve to real files.

    Checks every agent's persona file exists in *personas_dir* and every
    tool listed in ``allowed_tools`` has a matching script in *tools_dir*.
    All errors are collected and reported together so the caller can fix
    everything in one pass.

    Args:
        config: A loaded :class:`DanClawConfig` to validate.
        personas_dir: Directory containing persona markdown files.
        tools_dir: Directory containing tool scripts.

    Raises:
        ConfigError: If any referenced persona files or tool scripts are missing.
            The message lists every missing item.
    """
    personas_dir = Path(personas_dir)
    tools_dir = Path(tools_dir)
    errors: list[str] = []

    for idx, agent in enumerate(config.agents):
        # Check persona file
        persona_file = personas_dir / f"{agent.persona}.md"
        if not persona_file.exists():
            errors.append(
                f"agents[{idx}] ({agent.name}): persona file not found: {persona_file}"
            )

        # Check tool scripts
        for tool_name in agent.allowed_tools:
            # Look for any file matching the tool name (with or without extension)
            matches = list(tools_dir.glob(f"{tool_name}*"))
            # Filter to actual tool scripts (not directories, not __pycache__, etc.)
            tool_files = [
                m for m in matches
                if m.is_file() and m.stem == tool_name
            ]
            if not tool_files:
                errors.append(
                    f"agents[{idx}] ({agent.name}): tool script not found "
                    f"for '{tool_name}' in {tools_dir}"
                )

    if errors:
        detail = "; ".join(errors)
        raise ConfigError(f"Config validation failed: {detail}")


def load_config(
    config_path: str | Path,
    *,
    personas_dir: str | Path | None = None,
    tools_dir: str | Path | None = None,
) -> DanClawConfig:
    """Load and validate the JSON config file.

    Args:
        config_path: Path to the JSON config file.
        personas_dir: Path to the personas directory. Defaults to
            ``<config_path>/../personas``.
        tools_dir: Path to the tools directory. Defaults to
            ``<config_path>/../tools``.

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
    if tools_dir is None:
        tools_dir = config_path.parent.parent / "tools"
    else:
        tools_dir = Path(tools_dir)

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
    seen_names: set[str] = set()
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
        allowed_tools = agent_data.get("allowed_tools", [])

        # Type checks
        if not isinstance(name, str) or not name:
            raise ConfigError(f"agents[{idx}]: 'name' must be a non-empty string")
        if name in seen_names:
            raise ConfigError(f"agents[{idx}]: duplicate agent name '{name}'")
        seen_names.add(name)
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
        if not isinstance(allowed_tools, list):
            raise ConfigError(f"agents[{idx}] ({name}): 'allowed_tools' must be a list")
        for tool in allowed_tools:
            if not isinstance(tool, str) or not tool:
                raise ConfigError(
                    f"agents[{idx}] ({name}): 'allowed_tools' entries must be non-empty strings"
                )

        agents.append(
            AgentConfig(
                name=name,
                persona=persona,
                backend_preference=list(backend_preference),
                allowed_tools=list(allowed_tools),
            )
        )

    # --- Validate listeners (loose for now) ---
    listeners = data.get("listeners", {})
    if not isinstance(listeners, dict):
        raise ConfigError("'listeners' must be a JSON object")

    config = DanClawConfig(agents=agents, listeners=listeners)

    # Validate that all referenced persona files and tool scripts exist.
    validate_config(config, personas_dir=personas_dir, tools_dir=tools_dir)

    return config
