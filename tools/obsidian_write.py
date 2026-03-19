"""Obsidian vault file writer.

Creates or updates a file in an Obsidian vault and prints a confirmation
message to stdout.  Designed to be invoked as a subprocess by the executor
on behalf of an agent.

Usage::

    python -m tools.obsidian_write --vault /path/to/vault --file notes/todo.md --content "# TODO"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


class VaultError(Exception):
    """Raised when a vault operation fails."""


def write_file(vault: str | Path, file_path: str, content: str) -> str:
    """Create or update a file in an Obsidian vault.

    Intermediate directories are created automatically.

    Args:
        vault: Absolute path to the vault root directory.
        file_path: Relative path within the vault (e.g. ``"notes/todo.md"``).
        content: The text content to write.

    Returns:
        A confirmation message describing what was done.

    Raises:
        VaultError: If the vault doesn't exist, the path escapes the vault,
            or the file cannot be written.
    """
    vault = Path(vault).resolve()
    if not vault.is_dir():
        raise VaultError(f"Vault directory does not exist: {vault}")

    target = (vault / file_path).resolve()

    # Prevent path traversal outside the vault.
    if not str(target).startswith(str(vault) + "/") and target != vault:
        raise VaultError(f"Path escapes vault: {file_path}")

    existed = target.is_file()

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise VaultError(f"Cannot write file: {exc}") from exc

    action = "Updated" if existed else "Created"
    return f"{action} {file_path}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Create or update a file in an Obsidian vault"
    )
    parser.add_argument("--vault", required=True, help="Path to the vault directory")
    parser.add_argument(
        "--file", required=True, help="Relative file path within the vault"
    )
    parser.add_argument("--content", required=True, help="Content to write")
    args = parser.parse_args(argv)

    try:
        result = write_file(args.vault, args.file, args.content)
        print(result)
    except VaultError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
