"""Shared test helpers and fixtures."""

from __future__ import annotations

from config import AgentConfig, DanClawConfig


def make_config(agent_name: str = "default") -> DanClawConfig:
    """Build a minimal DanClawConfig with a single agent.

    Useful for constructing a :class:`Dispatcher` in tests without
    needing a real JSON config file or persona files on disk.
    """
    return DanClawConfig(
        agents=[
            AgentConfig(
                name=agent_name,
                persona="default",
                backend_preference=["claude"],
            ),
        ],
    )
