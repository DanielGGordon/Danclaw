"""Config package: loading and validation of the DanClaw JSON config.

Re-exports load_config, validate_config, DanClawConfig, AgentConfig,
ChannelPermissions, UserPermissions, PermissionsConfig, ConfigError.
"""

from config.loader import (
    AgentConfig,
    ChannelPermissions,
    ConfigError,
    DanClawConfig,
    ObsidianToolConfig,
    PermissionsConfig,
    TelemetryConfig,
    ToolsConfig,
    UserPermissions,
    load_config,
    validate_config,
)

__all__ = [
    "AgentConfig",
    "ChannelPermissions",
    "ConfigError",
    "DanClawConfig",
    "ObsidianToolConfig",
    "PermissionsConfig",
    "TelemetryConfig",
    "ToolsConfig",
    "UserPermissions",
    "load_config",
    "validate_config",
]
