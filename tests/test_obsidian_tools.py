"""Tests for tools.obsidian_read, tools.obsidian_write, and tools.obsidian_search."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.obsidian_read import VaultError as ReadVaultError
from tools.obsidian_read import main as read_main
from tools.obsidian_read import read_file
from tools.obsidian_search import VaultError as SearchVaultError
from tools.obsidian_search import main as search_main
from tools.obsidian_search import search_files
from tools.obsidian_write import VaultError as WriteVaultError
from tools.obsidian_write import main as write_main
from tools.obsidian_write import write_file


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """Create a temporary vault with sample markdown files."""
    v = tmp_path / "vault"
    v.mkdir()
    (v / "readme.md").write_text("# Welcome\n\nThis is the vault root.")
    notes = v / "notes"
    notes.mkdir()
    (notes / "todo.md").write_text("# TODO\n\n- Buy milk\n- Fix bug")
    (notes / "meeting.md").write_text("# Meeting Notes\n\nDiscussed roadmap.")
    daily = v / "daily"
    daily.mkdir()
    (daily / "2024-01-01.md").write_text("# Jan 1\n\nHappy new year!")
    # Hidden dir should be ignored by search.
    hidden = v / ".obsidian"
    hidden.mkdir()
    (hidden / "config.json").write_text("{}")
    return v


# ══════════════════════════════════════════════════════════════════════
# obsidian_read
# ══════════════════════════════════════════════════════════════════════


class TestReadFile:
    """Unit tests for tools.obsidian_read.read_file."""

    def test_read_root_file(self, vault: Path) -> None:
        content = read_file(vault, "readme.md")
        assert "# Welcome" in content

    def test_read_nested_file(self, vault: Path) -> None:
        content = read_file(vault, "notes/todo.md")
        assert "Buy milk" in content

    def test_read_preserves_content(self, vault: Path) -> None:
        content = read_file(vault, "daily/2024-01-01.md")
        assert content == "# Jan 1\n\nHappy new year!"

    def test_read_nonexistent_file(self, vault: Path) -> None:
        with pytest.raises(ReadVaultError, match="File not found"):
            read_file(vault, "nope.md")

    def test_read_nonexistent_vault(self, tmp_path: Path) -> None:
        with pytest.raises(ReadVaultError, match="Vault directory does not exist"):
            read_file(tmp_path / "missing", "readme.md")

    def test_read_path_traversal_blocked(self, vault: Path) -> None:
        with pytest.raises(ReadVaultError, match="Path escapes vault"):
            read_file(vault, "../../../etc/passwd")

    def test_read_directory_not_file(self, vault: Path) -> None:
        with pytest.raises(ReadVaultError, match="File not found"):
            read_file(vault, "notes")


class TestReadMain:
    """Tests for the obsidian_read CLI entry point."""

    def test_main_stdout(self, vault: Path, capsys) -> None:
        read_main(["--vault", str(vault), "--file", "readme.md"])
        captured = capsys.readouterr()
        assert "# Welcome" in captured.out

    def test_main_error_exits(self, vault: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            read_main(["--vault", str(vault), "--file", "nope.md"])
        assert exc_info.value.code == 1

    def test_subprocess_invocation(self, vault: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.obsidian_read",
             "--vault", str(vault), "--file", "readme.md"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "# Welcome" in result.stdout


# ══════════════════════════════════════════════════════════════════════
# obsidian_write
# ══════════════════════════════════════════════════════════════════════


class TestWriteFile:
    """Unit tests for tools.obsidian_write.write_file."""

    def test_create_new_file(self, vault: Path) -> None:
        msg = write_file(vault, "new.md", "# New File")
        assert "Created" in msg
        assert (vault / "new.md").read_text() == "# New File"

    def test_update_existing_file(self, vault: Path) -> None:
        msg = write_file(vault, "readme.md", "# Updated")
        assert "Updated" in msg
        assert (vault / "readme.md").read_text() == "# Updated"

    def test_create_nested_file(self, vault: Path) -> None:
        msg = write_file(vault, "projects/alpha/plan.md", "# Alpha Plan")
        assert "Created" in msg
        assert (vault / "projects" / "alpha" / "plan.md").read_text() == "# Alpha Plan"

    def test_write_nonexistent_vault(self, tmp_path: Path) -> None:
        with pytest.raises(WriteVaultError, match="Vault directory does not exist"):
            write_file(tmp_path / "missing", "file.md", "content")

    def test_write_path_traversal_blocked(self, vault: Path) -> None:
        with pytest.raises(WriteVaultError, match="Path escapes vault"):
            write_file(vault, "../escape.md", "evil")

    def test_write_preserves_unicode(self, vault: Path) -> None:
        text = "Caf\u00e9 \u2603 \U0001f31f"
        write_file(vault, "unicode.md", text)
        assert (vault / "unicode.md").read_text(encoding="utf-8") == text

    def test_write_empty_content(self, vault: Path) -> None:
        msg = write_file(vault, "empty.md", "")
        assert "Created" in msg
        assert (vault / "empty.md").read_text() == ""


class TestWriteMain:
    """Tests for the obsidian_write CLI entry point."""

    def test_main_stdout(self, vault: Path, capsys) -> None:
        write_main(["--vault", str(vault), "--file", "cli.md", "--content", "hello"])
        captured = capsys.readouterr()
        assert "Created cli.md" in captured.out

    def test_main_error_exits(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            write_main([
                "--vault", str(tmp_path / "missing"),
                "--file", "f.md",
                "--content", "x",
            ])
        assert exc_info.value.code == 1

    def test_subprocess_invocation(self, vault: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.obsidian_write",
             "--vault", str(vault), "--file", "sub.md", "--content", "subprocess test"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "Created" in result.stdout
        assert (vault / "sub.md").read_text() == "subprocess test"


# ══════════════════════════════════════════════════════════════════════
# obsidian_search
# ══════════════════════════════════════════════════════════════════════


class TestSearchFiles:
    """Unit tests for tools.obsidian_search.search_files."""

    def test_list_all_files(self, vault: Path) -> None:
        results = search_files(vault)
        assert "readme.md" in results
        assert "notes/todo.md" in results
        assert "daily/2024-01-01.md" in results

    def test_hidden_dirs_excluded(self, vault: Path) -> None:
        results = search_files(vault)
        hidden = [r for r in results if ".obsidian" in r]
        assert hidden == []

    def test_filter_by_name_glob(self, vault: Path) -> None:
        results = search_files(vault, name="*.md")
        assert len(results) >= 4
        assert all(r.endswith(".md") for r in results)

    def test_filter_by_name_specific(self, vault: Path) -> None:
        results = search_files(vault, name="todo.md")
        assert results == ["notes/todo.md"]

    def test_filter_by_content(self, vault: Path) -> None:
        results = search_files(vault, query="Buy milk")
        assert results == ["notes/todo.md"]

    def test_content_search_case_insensitive(self, vault: Path) -> None:
        results = search_files(vault, query="buy MILK")
        assert "notes/todo.md" in results

    def test_filter_by_name_and_content(self, vault: Path) -> None:
        results = search_files(vault, name="*.md", query="roadmap")
        assert results == ["notes/meeting.md"]

    def test_no_matches(self, vault: Path) -> None:
        results = search_files(vault, query="zzzznotfound")
        assert results == []

    def test_results_sorted(self, vault: Path) -> None:
        results = search_files(vault)
        assert results == sorted(results)

    def test_nonexistent_vault(self, tmp_path: Path) -> None:
        with pytest.raises(SearchVaultError, match="Vault directory does not exist"):
            search_files(tmp_path / "missing")

    def test_name_pattern_no_match(self, vault: Path) -> None:
        results = search_files(vault, name="*.txt")
        assert results == []

    def test_binary_file_skipped_on_content_search(self, vault: Path) -> None:
        (vault / "binary.bin").write_bytes(b"\x00\x01\x02\xff")
        results = search_files(vault, query="something")
        assert "binary.bin" not in results


class TestSearchMain:
    """Tests for the obsidian_search CLI entry point."""

    def test_main_list_all(self, vault: Path, capsys) -> None:
        search_main(["--vault", str(vault)])
        captured = capsys.readouterr()
        assert "readme.md" in captured.out

    def test_main_with_name(self, vault: Path, capsys) -> None:
        search_main(["--vault", str(vault), "--name", "todo.md"])
        captured = capsys.readouterr()
        assert "notes/todo.md" in captured.out
        assert "readme.md" not in captured.out

    def test_main_with_query(self, vault: Path, capsys) -> None:
        search_main(["--vault", str(vault), "--query", "roadmap"])
        captured = capsys.readouterr()
        assert "meeting.md" in captured.out

    def test_main_error_exits(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            search_main(["--vault", str(tmp_path / "missing")])
        assert exc_info.value.code == 1

    def test_subprocess_invocation(self, vault: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.obsidian_search",
             "--vault", str(vault), "--query", "Happy new year"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "2024-01-01.md" in result.stdout
