"""Tests for tools.git_ops — git add, commit, push operations."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.git_ops import git_add, git_commit, git_push


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "master"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    # Initial commit so we have a HEAD
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
    # Push initial commit so the remote has a matching branch
    subprocess.run(
        ["git", "push", "-u", "origin", "master"],
        cwd=git_repo, capture_output=True, check=True,
    )
    return remote


# ══════════════════════════════════════════════════════════════════════
# git_add
# ══════════════════════════════════════════════════════════════════════


class TestGitAdd:
    """Unit tests for tools.git_ops.git_add."""

    def test_add_single_file(self, git_repo: Path) -> None:
        (git_repo / "a.txt").write_text("hello")
        git_add(["a.txt"], cwd=git_repo)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=git_repo, capture_output=True, text=True,
        )
        assert "A  a.txt" in status.stdout

    def test_add_multiple_files(self, git_repo: Path) -> None:
        (git_repo / "b.txt").write_text("b")
        (git_repo / "c.txt").write_text("c")
        git_add(["b.txt", "c.txt"], cwd=git_repo)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=git_repo, capture_output=True, text=True,
        )
        assert "A  b.txt" in status.stdout
        assert "A  c.txt" in status.stdout

    def test_add_nested_file(self, git_repo: Path) -> None:
        sub = git_repo / "sub"
        sub.mkdir()
        (sub / "nested.txt").write_text("nested")
        git_add(["sub/nested.txt"], cwd=git_repo)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=git_repo, capture_output=True, text=True,
        )
        assert "A  sub/nested.txt" in status.stdout

    def test_add_nonexistent_file_raises(self, git_repo: Path) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            git_add(["nonexistent.txt"], cwd=git_repo)

    def test_add_returns_string(self, git_repo: Path) -> None:
        (git_repo / "d.txt").write_text("d")
        result = git_add(["d.txt"], cwd=git_repo)
        assert isinstance(result, str)


# ══════════════════════════════════════════════════════════════════════
# git_commit
# ══════════════════════════════════════════════════════════════════════


class TestGitCommit:
    """Unit tests for tools.git_ops.git_commit."""

    def test_commit_staged_changes(self, git_repo: Path) -> None:
        (git_repo / "e.txt").write_text("e")
        subprocess.run(["git", "add", "e.txt"], cwd=git_repo, capture_output=True, check=True)
        result = git_commit("add e", cwd=git_repo)
        assert "add e" in result

    def test_commit_message_in_log(self, git_repo: Path) -> None:
        (git_repo / "f.txt").write_text("f")
        subprocess.run(["git", "add", "f.txt"], cwd=git_repo, capture_output=True, check=True)
        git_commit("test message xyz", cwd=git_repo)
        log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=git_repo, capture_output=True, text=True,
        )
        assert "test message xyz" in log.stdout

    def test_commit_nothing_staged_raises(self, git_repo: Path) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            git_commit("empty", cwd=git_repo)

    def test_commit_returns_string(self, git_repo: Path) -> None:
        (git_repo / "g.txt").write_text("g")
        subprocess.run(["git", "add", "g.txt"], cwd=git_repo, capture_output=True, check=True)
        result = git_commit("msg", cwd=git_repo)
        assert isinstance(result, str)


# ══════════════════════════════════════════════════════════════════════
# git_push
# ══════════════════════════════════════════════════════════════════════


class TestGitPush:
    """Unit tests for tools.git_ops.git_push."""

    def test_push_to_bare_remote(self, git_repo: Path, bare_remote: Path) -> None:
        (git_repo / "h.txt").write_text("h")
        subprocess.run(["git", "add", "h.txt"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add h"],
            cwd=git_repo, capture_output=True, check=True,
        )
        result = git_push(cwd=git_repo)
        assert isinstance(result, str)
        # Verify the remote received the commit
        remote_log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=bare_remote, capture_output=True, text=True,
        )
        assert "add h" in remote_log.stdout

    def test_push_with_explicit_branch(self, git_repo: Path, bare_remote: Path) -> None:
        (git_repo / "i.txt").write_text("i")
        subprocess.run(["git", "add", "i.txt"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add i"],
            cwd=git_repo, capture_output=True, check=True,
        )
        result = git_push(branch="master", cwd=git_repo)
        assert isinstance(result, str)

    def test_push_no_remote_raises(self, git_repo: Path) -> None:
        """Push fails when no remote is configured."""
        (git_repo / "j.txt").write_text("j")
        subprocess.run(["git", "add", "j.txt"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add j"],
            cwd=git_repo, capture_output=True, check=True,
        )
        with pytest.raises(subprocess.CalledProcessError):
            git_push(cwd=git_repo)


# ══════════════════════════════════════════════════════════════════════
# End-to-end: add → commit → push
# ══════════════════════════════════════════════════════════════════════


class TestGitOpsEndToEnd:
    """Full add → commit → push sequence through the tool functions."""

    def test_add_commit_push_sequence(self, git_repo: Path, bare_remote: Path) -> None:
        (git_repo / "feature.py").write_text("print('feature')")
        git_add(["feature.py"], cwd=git_repo)
        git_commit("add feature", cwd=git_repo)
        git_push(cwd=git_repo)

        # Verify the remote has the commit
        remote_log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=bare_remote, capture_output=True, text=True,
        )
        assert "add feature" in remote_log.stdout

    def test_multiple_commits_then_push(self, git_repo: Path, bare_remote: Path) -> None:
        (git_repo / "one.txt").write_text("1")
        git_add(["one.txt"], cwd=git_repo)
        git_commit("first", cwd=git_repo)

        (git_repo / "two.txt").write_text("2")
        git_add(["two.txt"], cwd=git_repo)
        git_commit("second", cwd=git_repo)

        git_push(cwd=git_repo)

        remote_log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=bare_remote, capture_output=True, text=True,
        )
        assert "first" in remote_log.stdout
        assert "second" in remote_log.stdout


# ══════════════════════════════════════════════════════════════════════
# CLI interface
# ══════════════════════════════════════════════════════════════════════


class TestGitOpsCli:
    """Tests for the git_ops CLI entry point."""

    def test_cli_add(self, git_repo: Path) -> None:
        (git_repo / "cli.txt").write_text("cli")
        result = subprocess.run(
            [sys.executable, "-m", "tools.git_ops", "add", "--cwd", str(git_repo), "cli.txt"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0

    def test_cli_commit(self, git_repo: Path) -> None:
        (git_repo / "cli2.txt").write_text("cli2")
        subprocess.run(["git", "add", "cli2.txt"], cwd=git_repo, capture_output=True, check=True)
        result = subprocess.run(
            [sys.executable, "-m", "tools.git_ops", "commit",
             "--cwd", str(git_repo), "-m", "cli commit"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0

    def test_cli_push(self, git_repo: Path, bare_remote: Path) -> None:
        (git_repo / "cli3.txt").write_text("cli3")
        subprocess.run(["git", "add", "cli3.txt"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "cli push test"],
            cwd=git_repo, capture_output=True, check=True,
        )
        result = subprocess.run(
            [sys.executable, "-m", "tools.git_ops", "push", "--cwd", str(git_repo)],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0

    def test_cli_commit_failure_exits_nonzero(self, git_repo: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.git_ops", "commit",
             "--cwd", str(git_repo), "-m", "nothing staged"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode != 0
        assert "failed" in result.stderr.lower()
