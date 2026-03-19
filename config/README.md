# Config

Configuration loading and validation. Reads the JSON config file that defines agents, listeners, permissions, and tool registrations. Also handles secrets via environment variables.

## Files

- `danclaw.json` — Main JSON config defining agents and listener settings.
- `loader.py` — Config loader module: reads, validates, and returns structured config objects.
- `__init__.py` — Re-exports `load_config`, `validate_config`, `DanClawConfig`, `AgentConfig`, `ChannelPermissions`, `UserPermissions`, `PermissionsConfig`, `TelemetryConfig`, `ToolsConfig`, `ObsidianToolConfig`, `ConfigError`.

## Public Interface

- `load_config(path, *, personas_dir=None, tools_dir=None)` — Reads and validates the JSON config file, returns a `DanClawConfig` instance. Raises `ConfigError` on any validation failure. Validates that all referenced persona files and tool scripts exist.
- `validate_config(config, *, personas_dir, tools_dir)` — Validates that all agent persona files and tool scripts exist on disk. Collects all errors and reports them together in a single `ConfigError`. Can be called independently of `load_config`.
- `DanClawConfig` — Frozen dataclass: `agents: list[AgentConfig]`, `listeners: dict`, `permissions: PermissionsConfig`, `tools: ToolsConfig`, `telemetry: TelemetryConfig`.
  - `default_agent` — Property returning the first agent in the list. Raises `ConfigError` if no agents are configured.
  - `get_agent(name)` — Looks up an agent by name. Returns `None` if not found.
- `AgentConfig` — Frozen dataclass: `name`, `persona`, `backend_preference`, `allowed_tools`, `timeout` (int, seconds, default 120), `fallback_notification` (string, default `"silent"`).
- `PermissionsConfig` — Frozen dataclass: `channels: dict[str, ChannelPermissions]`, `users: dict[str, UserPermissions]`. Defaults to empty dicts when omitted from config.
- `ChannelPermissions` — Frozen dataclass: `allowed_tools: list[str]`, `override: bool`, `approval_required: bool`. The `override` flag, when True, locks the channel to channel-only permissions (user permissions are ignored). The `approval_required` flag, when True, requires confirmation before high-impact actions on this channel.
- `UserPermissions` — Frozen dataclass: `additional_tools: list[str]`, `approval_required: bool`. Extra tools granted to a user, additive on top of the channel baseline. The `approval_required` flag, when True, requires confirmation before high-impact actions by this user.
- `ToolsConfig` — Frozen dataclass: `obsidian: ObsidianToolConfig | None`. Container for tool-specific settings. Defaults to all-`None` when `tools` section is omitted from config.
- `ObsidianToolConfig` — Frozen dataclass: `vault_path: str`. Configuration for the Obsidian tool, specifying the absolute path to the vault directory.
- `TelemetryConfig` — Frozen dataclass: `slack_log_channel: str | None`. Telemetry/logging configuration. When `slack_log_channel` is set to a Slack channel ID string, the `SlackLogSink` is constructed at startup. Defaults to `None` (no Slack logging).
- `ConfigError` — Exception raised for invalid or missing config.

## Validation Rules

- Config must be a JSON object with an `agents` list (non-empty).
- Each agent must have `name` (non-empty string), `persona` (non-empty string referencing a markdown file in `personas/`), and `backend_preference` (non-empty ordered list of strings like `["claude", "codex"]`).
- `timeout` is optional (positive integer, defaults to 120). Controls the maximum seconds an executor may run before being cancelled.
- `fallback_notification` is optional (non-empty string, defaults to `"silent"`). Controls user notification when a fallback backend is used: `"silent"` suppresses notification, `"notify"` prepends a standard message (`"[Switched to backup AI]"`), or a custom string to use as the notification text.
- `allowed_tools` defaults to an empty list if omitted. Entries must be non-empty strings.
- Agent names must be unique across the config.
- Each agent's `persona` must correspond to an existing `<persona>.md` file in `personas/`.
- Each agent's `allowed_tools` entries must correspond to existing scripts in `tools/` (matched by stem, any extension).
- All missing personas and tools are collected and reported in a single error message.
- `listeners` must be a dict (defaults to `{}` if omitted).
- `permissions` is optional (defaults to empty channels/users). When present:
  - `permissions.channels` maps channel names to objects with `allowed_tools` (list of strings, default `[]`), `override` (boolean, default `false`), and `approval_required` (boolean, default `false`).
  - `permissions.users` maps user identifiers to objects with `additional_tools` (list of strings, default `[]`) and `approval_required` (boolean, default `false`).
- `tools` is optional (defaults to empty). When present, must be a JSON object with tool-specific settings:
  - `tools.obsidian` (optional): object with `vault_path` (required non-empty string) specifying the absolute path to the Obsidian vault directory.
- `telemetry` is optional (defaults to empty). When present, must be a JSON object:
  - `telemetry.slack_log_channel` (optional): non-empty string specifying the Slack channel ID where telemetry summaries are posted. When set, the `SlackLogSink` is constructed at startup using `SLACK_BOT_TOKEN` from the environment.

## Relationship to Other Modules

- **Used by**: `dispatcher` (agent definitions, permissions), `listeners` (listener-specific settings)
- **References**: `personas/` directory (persona markdown files referenced by name)
- **Independent of**: `tools` (tools are registered by name in config but executed by dispatcher)

## Status

Config loader implemented and tested (84 tests).
