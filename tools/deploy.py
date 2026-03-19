"""Deploy tool: pull latest code, rebuild, and restart services.

Provides a deploy function that runs the standard deploy sequence:
1. git pull
2. docker compose build (if needed)
3. docker compose up -d (restart affected services)
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str], *, cwd: str | Path) -> str:
    """Run a command and return combined output.

    Raises
    ------
    subprocess.CalledProcessError
        If the command exits non-zero.
    """
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return (result.stdout + result.stderr).strip()


def deploy(*, cwd: str | Path, rebuild: bool = True) -> str:
    """Execute the deploy sequence.

    Args:
        cwd: Project root directory.
        rebuild: Whether to rebuild Docker images before restarting.

    Returns:
        Combined output from all deploy steps.
    """
    outputs: list[str] = []

    # 1. Pull latest
    out = _run(["git", "pull", "--ff-only"], cwd=cwd)
    outputs.append(f"git pull: {out}")

    # 2. Rebuild if requested
    if rebuild:
        out = _run(["docker", "compose", "build"], cwd=cwd)
        outputs.append(f"docker compose build: {out}")

    # 3. Restart services
    out = _run(["docker", "compose", "up", "-d"], cwd=cwd)
    outputs.append(f"docker compose up -d: {out}")

    return "\n".join(outputs)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Deploy tool")
    parser.add_argument("--cwd", required=True, help="Project root directory")
    parser.add_argument(
        "--no-rebuild", action="store_true",
        help="Skip Docker image rebuild",
    )
    args = parser.parse_args()

    try:
        print(deploy(cwd=args.cwd, rebuild=not args.no_rebuild))
    except subprocess.CalledProcessError as exc:
        print(f"Deploy failed: {exc.stderr or exc.stdout}", file=sys.stderr)
        sys.exit(exc.returncode)
