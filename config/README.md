# Config

Configuration loading and validation. Reads the JSON config file that defines agents, listeners, permissions, and tool registrations. Also handles secrets via environment variables.

## Files

- `danclaw.json` — Main JSON config defining agents and listener settings.
- `loader.py` — Config loader module: reads, validates, and returns structured config objects.
- `__init__.py` — Re-exports `load_config`, `DanClawConfig`, `AgentConfig`, `ConfigError`.

## Public Interface

- `load_config(path, *, personas_dir=None)` — Reads and validates the JSON config file, returns a `DanClawConfig` instance. Raises `ConfigError` on any validation failure.
- `DanClawConfig` — Frozen dataclass: `agents: list[AgentConfig]`, `listeners: dict`.
- `AgentConfig` — Frozen dataclass: `name`, `persona`, `backend_preference`, `tools`.
- `ConfigError` — Exception raised for invalid or missing config.

## Validation Rules

- Config must be a JSON object with an `agents` list (non-empty).
- Each agent must have `name` (non-empty string), `persona` (non-empty string), and `backend_preference` (non-empty list of strings).
- `tools` defaults to an empty list if omitted.
- Each agent's `persona` must correspond to an existing `<persona>.md` file in `personas/`.
- `listeners` must be a dict (defaults to `{}` if omitted).

## Relationship to Other Modules

- **Used by**: `dispatcher` (agent definitions, permissions), `listeners` (listener-specific settings)
- **References**: `personas/` directory (persona markdown files referenced by name)
- **Independent of**: `tools` (tools are registered by name in config but executed by dispatcher)

## Status

Config loader implemented and tested (22 tests).
