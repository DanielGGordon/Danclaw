"""Tests for tools.instrumented — telemetry-emitting tool wrappers."""

from __future__ import annotations

from pathlib import Path

import pytest

from dispatcher.telemetry import TelemetryCollector
from tools.instrumented import read_file, search_files, write_file
from tools.obsidian_read import VaultError as ReadVaultError
from tools.obsidian_search import VaultError as SearchVaultError
from tools.obsidian_write import VaultError as WriteVaultError


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """Create a temporary vault with sample files."""
    v = tmp_path / "vault"
    v.mkdir()
    (v / "readme.md").write_text("# Welcome\n\nThis is the vault root.")
    notes = v / "notes"
    notes.mkdir()
    (notes / "todo.md").write_text("# TODO\n\n- Buy milk\n- Fix bug")
    return v


@pytest.fixture()
def collector() -> TelemetryCollector:
    return TelemetryCollector()


# ══════════════════════════════════════════════════════════════════════
# read_file telemetry
# ══════════════════════════════════════════════════════════════════════


class TestReadFileTelemetry:
    """Instrumented read_file emits correct telemetry events."""

    def test_success_emits_event(self, vault: Path, collector: TelemetryCollector) -> None:
        result = read_file(vault, "readme.md", telemetry=collector)
        assert "# Welcome" in result
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "tool_execution"
        assert event.payload["tool"] == "obsidian_read"
        assert event.payload["success"] is True
        assert event.payload["duration"] >= 0
        assert event.payload["args"]["file_path"] == "readme.md"
        assert "error" not in event.payload

    def test_failure_emits_event(self, vault: Path, collector: TelemetryCollector) -> None:
        with pytest.raises(ReadVaultError):
            read_file(vault, "nonexistent.md", telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "tool_execution"
        assert event.payload["tool"] == "obsidian_read"
        assert event.payload["success"] is False
        assert event.payload["duration"] >= 0
        assert "error" in event.payload
        assert "File not found" in event.payload["error"]

    def test_payload_contains_vault_arg(self, vault: Path, collector: TelemetryCollector) -> None:
        read_file(vault, "readme.md", telemetry=collector)
        assert collector.events[0].payload["args"]["vault"] == str(vault)

    def test_timestamp_is_set(self, vault: Path, collector: TelemetryCollector) -> None:
        read_file(vault, "readme.md", telemetry=collector)
        assert collector.events[0].timestamp > 0


# ══════════════════════════════════════════════════════════════════════
# write_file telemetry
# ══════════════════════════════════════════════════════════════════════


class TestWriteFileTelemetry:
    """Instrumented write_file emits correct telemetry events."""

    def test_success_emits_event(self, vault: Path, collector: TelemetryCollector) -> None:
        result = write_file(vault, "new.md", "# New", telemetry=collector)
        assert "Created" in result
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "tool_execution"
        assert event.payload["tool"] == "obsidian_write"
        assert event.payload["success"] is True
        assert event.payload["duration"] >= 0
        assert event.payload["args"]["file_path"] == "new.md"
        assert event.payload["args"]["content"] == "# New"
        assert "error" not in event.payload

    def test_failure_emits_event(self, vault: Path, collector: TelemetryCollector) -> None:
        with pytest.raises(WriteVaultError):
            write_file(vault, "../escape.md", "evil", telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "tool_execution"
        assert event.payload["tool"] == "obsidian_write"
        assert event.payload["success"] is False
        assert "error" in event.payload

    def test_payload_contains_vault_arg(self, vault: Path, collector: TelemetryCollector) -> None:
        write_file(vault, "x.md", "text", telemetry=collector)
        assert collector.events[0].payload["args"]["vault"] == str(vault)

    def test_timestamp_is_set(self, vault: Path, collector: TelemetryCollector) -> None:
        write_file(vault, "x.md", "text", telemetry=collector)
        assert collector.events[0].timestamp > 0


# ══════════════════════════════════════════════════════════════════════
# search_files telemetry
# ══════════════════════════════════════════════════════════════════════


class TestSearchFileTelemetry:
    """Instrumented search_files emits correct telemetry events."""

    def test_success_emits_event(self, vault: Path, collector: TelemetryCollector) -> None:
        results = search_files(vault, telemetry=collector)
        assert len(results) >= 1
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "tool_execution"
        assert event.payload["tool"] == "obsidian_search"
        assert event.payload["success"] is True
        assert event.payload["duration"] >= 0
        assert "error" not in event.payload

    def test_success_with_name_and_query(self, vault: Path, collector: TelemetryCollector) -> None:
        results = search_files(vault, name="*.md", query="Buy milk", telemetry=collector)
        assert "notes/todo.md" in results
        event = collector.events[0]
        assert event.payload["args"]["name"] == "*.md"
        assert event.payload["args"]["query"] == "Buy milk"

    def test_failure_emits_event(self, tmp_path: Path, collector: TelemetryCollector) -> None:
        with pytest.raises(SearchVaultError):
            search_files(tmp_path / "missing", telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "tool_execution"
        assert event.payload["tool"] == "obsidian_search"
        assert event.payload["success"] is False
        assert "error" in event.payload
        assert "Vault directory does not exist" in event.payload["error"]

    def test_payload_contains_vault_arg(self, vault: Path, collector: TelemetryCollector) -> None:
        search_files(vault, telemetry=collector)
        assert collector.events[0].payload["args"]["vault"] == str(vault)

    def test_optional_args_omitted_when_none(self, vault: Path, collector: TelemetryCollector) -> None:
        search_files(vault, telemetry=collector)
        args = collector.events[0].payload["args"]
        assert "name" not in args
        assert "query" not in args

    def test_timestamp_is_set(self, vault: Path, collector: TelemetryCollector) -> None:
        search_files(vault, telemetry=collector)
        assert collector.events[0].timestamp > 0
