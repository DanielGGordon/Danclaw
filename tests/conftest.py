"""Shared test helpers and fixtures."""

from __future__ import annotations

from pathlib import Path

from config import AgentConfig, DanClawConfig


def make_config(agent_name: str = "default", persona: str = "default") -> DanClawConfig:
    """Build a minimal DanClawConfig with a single agent.

    Useful for constructing a :class:`Dispatcher` in tests without
    needing a real JSON config file or persona files on disk.
    """
    return DanClawConfig(
        agents=[
            AgentConfig(
                name=agent_name,
                persona=persona,
                backend_preference=["claude"],
            ),
        ],
    )


def make_personas_dir(
    tmp_path: Path,
    personas: dict[str, str] | None = None,
) -> Path:
    """Create a temporary personas directory with markdown files.

    Args:
        tmp_path: A temporary directory (typically from pytest's tmp_path).
        personas: Mapping of persona name to content.  Defaults to
            ``{"default": "You are the default test persona."}``.

    Returns:
        Path to the created personas directory.
    """
    if personas is None:
        personas = {"default": "You are the default test persona."}
    personas_dir = tmp_path / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)
    for name, content in personas.items():
        (personas_dir / f"{name}.md").write_text(content, encoding="utf-8")
    return personas_dir
