"""Tests for telemetry-instrumented git operation wrappers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dispatcher.telemetry import TelemetryCollector
from tools.instrumented import git_add, git_commit, git_push


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    (repo / "init.txt").write_text("init")
    subprocess.run(["git", "add", "init.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


@pytest.fixture()
def bare_remote(tmp_path: Path, git_repo: Path) -> Path:
    """Create a bare remote and configure it as origin for *git_repo*."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=git_repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "master"],
        cwd=git_repo, capture_output=True, check=True,
    )
    return remote


@pytest.fixture()
def collector() -> TelemetryCollector:
    return TelemetryCollector()


# ══════════════════════════════════════════════════════════════════════
# git_add telemetry
# ══════════════════════════════════════════════════════════════════════


class TestGitAddTelemetry:
    """Instrumented git_add emits correct telemetry events."""

    def test_success_emits_event(self, git_repo: Path, collector: TelemetryCollector) -> None:
        (git_repo / "a.txt").write_text("a")
        git_add(["a.txt"], cwd=git_repo, telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "tool_execution"
        assert event.payload["tool"] == "git_add"
        assert event.payload["success"] is True
        assert event.payload["duration"] >= 0
        assert event.payload["args"]["paths"] == ["a.txt"]
        assert event.payload["args"]["cwd"] == str(git_repo)
        assert "error" not in event.payload

    def test_failure_emits_event(self, git_repo: Path, collector: TelemetryCollector) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            git_add(["nonexistent.txt"], cwd=git_repo, telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.payload["tool"] == "git_add"
        assert event.payload["success"] is False
        assert "error" in event.payload

    def test_timestamp_is_set(self, git_repo: Path, collector: TelemetryCollector) -> None:
        (git_repo / "b.txt").write_text("b")
        git_add(["b.txt"], cwd=git_repo, telemetry=collector)
        assert collector.events[0].timestamp > 0


# ══════════════════════════════════════════════════════════════════════
# git_commit telemetry
# ══════════════════════════════════════════════════════════════════════


class TestGitCommitTelemetry:
    """Instrumented git_commit emits correct telemetry events."""

    def test_success_emits_event(self, git_repo: Path, collector: TelemetryCollector) -> None:
        (git_repo / "c.txt").write_text("c")
        subprocess.run(["git", "add", "c.txt"], cwd=git_repo, capture_output=True, check=True)
        git_commit("test commit", cwd=git_repo, telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "tool_execution"
        assert event.payload["tool"] == "git_commit"
        assert event.payload["success"] is True
        assert event.payload["args"]["message"] == "test commit"
        assert "error" not in event.payload

    def test_failure_emits_event(self, git_repo: Path, collector: TelemetryCollector) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            git_commit("nothing staged", cwd=git_repo, telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.payload["tool"] == "git_commit"
        assert event.payload["success"] is False
        assert "error" in event.payload

    def test_timestamp_is_set(self, git_repo: Path, collector: TelemetryCollector) -> None:
        (git_repo / "d.txt").write_text("d")
        subprocess.run(["git", "add", "d.txt"], cwd=git_repo, capture_output=True, check=True)
        git_commit("ts test", cwd=git_repo, telemetry=collector)
        assert collector.events[0].timestamp > 0


# ══════════════════════════════════════════════════════════════════════
# git_push telemetry
# ══════════════════════════════════════════════════════════════════════


class TestGitPushTelemetry:
    """Instrumented git_push emits correct telemetry events."""

    def test_success_emits_event(
        self, git_repo: Path, bare_remote: Path, collector: TelemetryCollector,
    ) -> None:
        (git_repo / "e.txt").write_text("e")
        subprocess.run(["git", "add", "e.txt"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "push test"],
            cwd=git_repo, capture_output=True, check=True,
        )
        git_push(cwd=git_repo, telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "tool_execution"
        assert event.payload["tool"] == "git_push"
        assert event.payload["success"] is True
        assert event.payload["args"]["remote"] == "origin"
        assert "error" not in event.payload

    def test_failure_emits_event(self, git_repo: Path, collector: TelemetryCollector) -> None:
        (git_repo / "f.txt").write_text("f")
        subprocess.run(["git", "add", "f.txt"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "no remote"],
            cwd=git_repo, capture_output=True, check=True,
        )
        with pytest.raises(subprocess.CalledProcessError):
            git_push(cwd=git_repo, telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.payload["tool"] == "git_push"
        assert event.payload["success"] is False
        assert "error" in event.payload

    def test_branch_arg_included_when_set(
        self, git_repo: Path, bare_remote: Path, collector: TelemetryCollector,
    ) -> None:
        (git_repo / "g.txt").write_text("g")
        subprocess.run(["git", "add", "g.txt"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "branch arg"],
            cwd=git_repo, capture_output=True, check=True,
        )
        git_push(branch="master", cwd=git_repo, telemetry=collector)
        assert collector.events[0].payload["args"]["branch"] == "master"

    def test_branch_arg_omitted_when_none(
        self, git_repo: Path, bare_remote: Path, collector: TelemetryCollector,
    ) -> None:
        (git_repo / "h.txt").write_text("h")
        subprocess.run(["git", "add", "h.txt"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "no branch"],
            cwd=git_repo, capture_output=True, check=True,
        )
        git_push(cwd=git_repo, telemetry=collector)
        assert "branch" not in collector.events[0].payload["args"]

    def test_timestamp_is_set(
        self, git_repo: Path, bare_remote: Path, collector: TelemetryCollector,
    ) -> None:
        (git_repo / "i.txt").write_text("i")
        subprocess.run(["git", "add", "i.txt"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "ts"],
            cwd=git_repo, capture_output=True, check=True,
        )
        git_push(cwd=git_repo, telemetry=collector)
        assert collector.events[0].timestamp > 0


# ══════════════════════════════════════════════════════════════════════
# End-to-end: add → commit → push with telemetry
# ══════════════════════════════════════════════════════════════════════


class TestGitOpsInstrumentedE2E:
    """Full add → commit → push through instrumented wrappers."""

    def test_full_sequence_emits_three_events(
        self, git_repo: Path, bare_remote: Path, collector: TelemetryCollector,
    ) -> None:
        (git_repo / "feature.py").write_text("print('feature')")
        git_add(["feature.py"], cwd=git_repo, telemetry=collector)
        git_commit("add feature", cwd=git_repo, telemetry=collector)
        git_push(cwd=git_repo, telemetry=collector)

        assert len(collector.events) == 3
        tools = [e.payload["tool"] for e in collector.events]
        assert tools == ["git_add", "git_commit", "git_push"]
        assert all(e.payload["success"] for e in collector.events)
