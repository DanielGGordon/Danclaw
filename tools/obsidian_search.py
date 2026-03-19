"""Obsidian vault file search.

Searches an Obsidian vault by filename pattern and/or file content, printing
matching relative paths to stdout (one per line).  Designed to be invoked as
a subprocess by the executor on behalf of an agent.

Usage::

    python -m tools.obsidian_search --vault /path/to/vault --name "*.md"
    python -m tools.obsidian_search --vault /path/to/vault --query "TODO"
    python -m tools.obsidian_search --vault /path/to/vault --name "*.md" --query "TODO"
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path


class VaultError(Exception):
    """Raised when a vault operation fails."""


def search_files(
    vault: str | Path,
    *,
    name: str | None = None,
    query: str | None = None,
) -> list[str]:
    """Search for files in an Obsidian vault.

    When both *name* and *query* are provided, a file must match both to be
    included.  When neither is provided, all files in the vault are listed.

    Args:
        vault: Absolute path to the vault root directory.
        name: Optional filename glob pattern (e.g. ``"*.md"``, ``"daily-*"``).
            Matched against the file name only, not the full relative path.
        query: Optional case-insensitive substring to search for in file
            content.

    Returns:
        A sorted list of relative file paths (using forward slashes) that
        match the criteria.

    Raises:
        VaultError: If the vault doesn't exist.
    """
    vault = Path(vault).resolve()
    if not vault.is_dir():
        raise VaultError(f"Vault directory does not exist: {vault}")

    results: list[str] = []

    for path in sorted(vault.rglob("*")):
        if not path.is_file():
            continue

        # Skip hidden files/directories (e.g., .obsidian/, .git/).
        relative = path.relative_to(vault)
        if any(part.startswith(".") for part in relative.parts):
            continue

        # Name filter.
        if name is not None and not fnmatch.fnmatch(path.name, name):
            continue

        # Content filter.
        if query is not None:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if query.lower() not in text.lower():
                continue

        results.append(str(relative))

    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Search files in an Obsidian vault"
    )
    parser.add_argument("--vault", required=True, help="Path to the vault directory")
    parser.add_argument("--name", default=None, help="Filename glob pattern")
    parser.add_argument("--query", default=None, help="Content search string")
    args = parser.parse_args(argv)

    try:
        matches = search_files(args.vault, name=args.name, query=args.query)
        for m in matches:
            print(m)
    except VaultError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
