"""Trigger deploy tool: agent-callable entry point for deployments.

Designed to be invoked by the admin agent as a tool call. Resolves the
project root automatically (defaults to the danclaw project directory)
and delegates to :func:`tools.deploy.deploy`.

Usage::

    python -m tools.trigger_deploy [--no-rebuild] [--cwd PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _default_project_root() -> Path:
    """Return the danclaw project root (parent of the ``tools/`` package)."""
    return Path(__file__).resolve().parent.parent


def trigger_deploy(
    *,
    cwd: str | Path | None = None,
    rebuild: bool = True,
) -> str:
    """Trigger a deploy, defaulting to the danclaw project root.

    Args:
        cwd: Project root directory. Defaults to the danclaw repo root.
        rebuild: Whether to rebuild Docker images before restarting.

    Returns:
        Combined output from all deploy steps.
    """
    from tools.deploy import deploy

    if cwd is None:
        cwd = _default_project_root()
    return deploy(cwd=cwd, rebuild=rebuild)


if __name__ == "__main__":
    import subprocess

    parser = argparse.ArgumentParser(
        description="Trigger a deploy (agent tool entry point)",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Project root directory (defaults to danclaw repo root)",
    )
    parser.add_argument(
        "--no-rebuild",
        action="store_true",
        help="Skip Docker image rebuild",
    )
    args = parser.parse_args()

    try:
        print(trigger_deploy(cwd=args.cwd, rebuild=not args.no_rebuild))
    except subprocess.CalledProcessError as exc:
        print(f"Deploy failed: {exc.stderr or exc.stdout}", file=sys.stderr)
        sys.exit(exc.returncode)
