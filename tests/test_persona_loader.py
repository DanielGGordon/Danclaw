"""Tests for personas.loader — persona loading by name."""

from __future__ import annotations

from pathlib import Path

import pytest

from personas.loader import PersonaError, load_persona


@pytest.fixture()
def personas_dir(tmp_path: Path):
    """Create a temporary personas directory with sample files."""
    d = tmp_path / "personas"
    d.mkdir()
    (d / "default.md").write_text("You are the default agent.")
    (d / "coder.md").write_text(
        "# Coder Persona\n\nYou are a coding assistant. Write clean, tested code."
    )
    return d


# ── Happy path ──────────────────────────────────────────────────────────


def test_load_persona_by_name(personas_dir):
    content = load_persona("default", personas_dir=personas_dir)
    assert content == "You are the default agent."


def test_load_persona_returns_full_content(personas_dir):
    content = load_persona("coder", personas_dir=personas_dir)
    assert "# Coder Persona" in content
    assert "Write clean, tested code." in content


def test_load_persona_preserves_whitespace(personas_dir):
    (personas_dir / "spaced.md").write_text("line one\n\n  indented\n")
    content = load_persona("spaced", personas_dir=personas_dir)
    assert content == "line one\n\n  indented\n"


# ── File not found ─────────────────────────────────────────────────────


def test_load_persona_not_found(personas_dir):
    with pytest.raises(PersonaError, match="Persona file not found"):
        load_persona("nonexistent", personas_dir=personas_dir)


def test_load_persona_not_found_includes_path(personas_dir):
    with pytest.raises(PersonaError, match="nonexistent.md"):
        load_persona("nonexistent", personas_dir=personas_dir)


# ── Invalid name ───────────────────────────────────────────────────────


def test_load_persona_empty_name(personas_dir):
    with pytest.raises(PersonaError, match="non-empty string"):
        load_persona("", personas_dir=personas_dir)


def test_load_persona_none_name(personas_dir):
    with pytest.raises(PersonaError, match="non-empty string"):
        load_persona(None, personas_dir=personas_dir)


# ── Default directory ──────────────────────────────────────────────────


def test_load_persona_default_dir():
    """Load the actual default persona from the project's personas/ directory."""
    content = load_persona("default")
    assert len(content) > 0
    assert "default" in content.lower()


# ── Integration with real project file ─────────────────────────────────


def test_load_real_default_persona():
    """The project ships a default.md persona that is non-empty."""
    project_personas = Path(__file__).resolve().parent.parent / "personas"
    content = load_persona("default", personas_dir=project_personas)
    assert len(content.strip()) > 0
    assert "default" in content.lower() or "assistant" in content.lower()
