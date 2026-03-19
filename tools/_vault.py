"""Shared vault validation helpers for Obsidian tools."""

from __future__ import annotations

from pathlib import Path


class VaultError(Exception):
    """Raised when a vault operation fails."""


def resolve_vault(vault: str | Path) -> Path:
    """Resolve and validate that *vault* is an existing directory."""
    vault = Path(vault).resolve()
    if not vault.is_dir():
        raise VaultError(f"Vault directory does not exist: {vault}")
    return vault


def is_path_within_vault(resolved: Path, vault: Path) -> bool:
    """Return ``True`` if *resolved* is inside (or equal to) *vault*."""
    return str(resolved).startswith(str(vault) + "/") or resolved == vault


def resolve_path_in_vault(vault: Path, file_path: str) -> Path:
    """Resolve *file_path* within *vault*, raising on traversal attempts."""
    target = (vault / file_path).resolve()
    if not is_path_within_vault(target, vault):
        raise VaultError(f"Path escapes vault: {file_path}")
    return target
