"""Persona loader: reads markdown persona files by name."""

from __future__ import annotations

from pathlib import Path


class PersonaError(Exception):
    """Raised when a persona file cannot be loaded."""


_DEFAULT_PERSONAS_DIR = Path(__file__).resolve().parent


def load_persona(
    name: str,
    *,
    personas_dir: str | Path | None = None,
) -> str:
    """Load a persona markdown file by name.

    Args:
        name: The persona name (e.g., "default" loads ``default.md``).
        personas_dir: Directory containing persona markdown files.
            Defaults to the ``personas/`` directory in this package.

    Returns:
        The markdown content of the persona file as a string.

    Raises:
        PersonaError: If the name is invalid or the file cannot be read.
    """
    if not name or not isinstance(name, str):
        raise PersonaError("Persona name must be a non-empty string")

    if any(c in name for c in ("/", "\\", "\0")) or ".." in name:
        raise PersonaError("Persona name contains invalid characters")

    if personas_dir is None:
        personas_dir = _DEFAULT_PERSONAS_DIR
    else:
        personas_dir = Path(personas_dir)

    persona_file = (personas_dir / f"{name}.md").resolve()

    if not persona_file.is_relative_to(personas_dir.resolve()):
        raise PersonaError("Persona name contains invalid path components")

    if not persona_file.exists():
        raise PersonaError(f"Persona file not found: {persona_file}")

    try:
        return persona_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise PersonaError(f"Cannot read persona file: {exc}") from exc
