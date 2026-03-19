# Config

Configuration loading and validation. Reads the JSON config file that defines agents, listeners, permissions, and tool registrations. Also handles secrets via environment variables.

## Files

- `danclaw.json` — Main JSON config defining agents and listener settings.
- `loader.py` — Config loader module: reads, validates, and returns structured config objects.
- `__init__.py` — Re-exports `load_config`, `DanClawConfig`, `AgentConfig`, `ConfigError`.

## Public Interface

- `load_config(path, *, personas_dir=None, tools_dir=None)` — Reads and validates the JSON config file, returns a `DanClawConfig` instance. Raises `ConfigError` on any validation failure. Validates that all referenced persona files and tool scripts exist.
- `validate_config(config, *, personas_dir, tools_dir)` — Validates that all agent persona files and tool scripts exist on disk. Collects all errors and reports them together in a single `ConfigError`. Can be called independently of `load_config`.
- `DanClawConfig` — Frozen dataclass: `agents: list[AgentConfig]`, `listeners: dict`.
  - `default_agent` — Property returning the first agent in the list. Raises `ConfigError` if no agents are configured.
  - `get_agent(name)` — Looks up an agent by name. Returns `None` if not found.
- `AgentConfig` — Frozen dataclass: `name`, `persona`, `backend_preference`, `allowed_tools`.
- `ConfigError` — Exception raised for invalid or missing config.

## Validation Rules

- Config must be a JSON object with an `agents` list (non-empty).
- Each agent must have `name` (non-empty string), `persona` (non-empty string referencing a markdown file in `personas/`), and `backend_preference` (non-empty ordered list of strings like `["claude", "codex"]`).
- `allowed_tools` defaults to an empty list if omitted. Entries must be non-empty strings.
- Agent names must be unique across the config.
- Each agent's `persona` must correspond to an existing `<persona>.md` file in `personas/`.
- Each agent's `allowed_tools` entries must correspond to existing scripts in `tools/` (matched by stem, any extension).
- All missing personas and tools are collected and reported in a single error message.
- `listeners` must be a dict (defaults to `{}` if omitted).

## Relationship to Other Modules

- **Used by**: `dispatcher` (agent definitions, permissions), `listeners` (listener-specific settings)
- **References**: `personas/` directory (persona markdown files referenced by name)
- **Independent of**: `tools` (tools are registered by name in config but executed by dispatcher)

## Status

Config loader implemented and tested (32 tests).
