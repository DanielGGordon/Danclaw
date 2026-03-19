"""Config package: loading and validation of the DanClaw JSON config.

Re-exports load_config, validate_config, DanClawConfig, AgentConfig, ConfigError.
"""

from config.loader import AgentConfig, ConfigError, DanClawConfig, load_config, validate_config

__all__ = ["AgentConfig", "ConfigError", "DanClawConfig", "load_config", "validate_config"]
