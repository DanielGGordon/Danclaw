"""Obsidian vault file reader.

Reads a file from an Obsidian vault (a directory of markdown files) and
prints its content to stdout.  Designed to be invoked as a subprocess by
the executor on behalf of an agent.

Usage::

    python -m tools.obsidian_read --vault /path/to/vault --file notes/todo.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools._vault import VaultError, resolve_path_in_vault, resolve_vault


def read_file(vault: str | Path, file_path: str) -> str:
    """Read a file from an Obsidian vault.

    Args:
        vault: Absolute path to the vault root directory.
        file_path: Relative path within the vault (e.g. ``"notes/todo.md"``).

    Returns:
        The file content as a string.

    Raises:
        VaultError: If the vault doesn't exist, the file is outside the vault,
            or the file cannot be read.
    """
    vault = resolve_vault(vault)
    target = resolve_path_in_vault(vault, file_path)

    if not target.is_file():
        raise VaultError(f"File not found in vault: {file_path}")

    try:
        return target.read_text(encoding="utf-8")
    except OSError as exc:
        raise VaultError(f"Cannot read file: {exc}") from exc


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Read a file from an Obsidian vault")
    parser.add_argument("--vault", required=True, help="Path to the vault directory")
    parser.add_argument("--file", required=True, help="Relative file path within the vault")
    args = parser.parse_args(argv)

    try:
        content = read_file(args.vault, args.file)
        sys.stdout.write(content)
    except VaultError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
