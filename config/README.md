# Config

Configuration loading and validation. Reads the JSON config file that defines agents, listeners, permissions, and tool registrations. Also handles secrets via environment variables.

## Public Interface

- `load_config(path)`: Reads and validates the JSON config file, returns a structured config object
- Config schema defines: agents (name, persona, backend preference, tools, permissions), listener settings, channel permissions

## Relationship to Other Modules

- **Used by**: `dispatcher` (agent definitions, permissions), `listeners` (listener-specific settings)
- **References**: `personas/` directory (persona markdown files referenced by name)
- **Independent of**: `tools` (tools are registered by name in config but executed by dispatcher)

## Status

Scaffold only. No config loader implemented yet.
