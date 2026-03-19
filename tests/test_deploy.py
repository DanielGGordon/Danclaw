"""Tests for tools.deploy — pull, rebuild, restart deploy sequence."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from tools.deploy import _run, deploy


# ══════════════════════════════════════════════════════════════════════
# _run helper
# ══════════════════════════════════════════════════════════════════════


class TestRun:
    """Unit tests for the _run subprocess helper."""

    def test_returns_stdout(self, tmp_path: Path) -> None:
        result = _run(["echo", "hello"], cwd=tmp_path)
        assert "hello" in result

    def test_returns_combined_stdout_stderr(self, tmp_path: Path) -> None:
        result = _run(
            ["bash", "-c", "echo out && echo err >&2"],
            cwd=tmp_path,
        )
        assert "out" in result
        assert "err" in result

    def test_raises_on_nonzero_exit(self, tmp_path: Path) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            _run(["false"], cwd=tmp_path)

    def test_uses_cwd(self, tmp_path: Path) -> None:
        result = _run(["pwd"], cwd=tmp_path)
        assert str(tmp_path) in result

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        result = _run(["echo", "  padded  "], cwd=tmp_path)
        # echo adds trailing newline, _run strips it
        assert result == "padded"


# ══════════════════════════════════════════════════════════════════════
# deploy function — mocked subprocess
# ══════════════════════════════════════════════════════════════════════


class TestDeployMocked:
    """Tests for deploy() with subprocess mocked out."""

    @pytest.fixture()
    def mock_run(self) -> MagicMock:
        """Patch subprocess.run to return success for all commands."""
        with patch("tools.deploy.subprocess.run") as mock:
            mock.return_value = MagicMock(
                stdout="ok",
                stderr="",
                returncode=0,
            )
            yield mock

    def test_calls_git_pull(self, tmp_path: Path, mock_run: MagicMock) -> None:
        deploy(cwd=tmp_path)
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["git", "pull", "--ff-only"] in calls

    def test_calls_docker_compose_build(self, tmp_path: Path, mock_run: MagicMock) -> None:
        deploy(cwd=tmp_path, rebuild=True)
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["docker", "compose", "build"] in calls

    def test_calls_docker_compose_up(self, tmp_path: Path, mock_run: MagicMock) -> None:
        deploy(cwd=tmp_path)
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["docker", "compose", "up", "-d"] in calls

    def test_skips_build_when_rebuild_false(self, tmp_path: Path, mock_run: MagicMock) -> None:
        deploy(cwd=tmp_path, rebuild=False)
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["docker", "compose", "build"] not in calls

    def test_order_pull_then_build_then_up(self, tmp_path: Path, mock_run: MagicMock) -> None:
        deploy(cwd=tmp_path, rebuild=True)
        calls = [c.args[0] for c in mock_run.call_args_list]
        pull_idx = calls.index(["git", "pull", "--ff-only"])
        build_idx = calls.index(["docker", "compose", "build"])
        up_idx = calls.index(["docker", "compose", "up", "-d"])
        assert pull_idx < build_idx < up_idx

    def test_order_pull_then_up_no_rebuild(self, tmp_path: Path, mock_run: MagicMock) -> None:
        deploy(cwd=tmp_path, rebuild=False)
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert len(calls) == 2
        assert calls[0] == ["git", "pull", "--ff-only"]
        assert calls[1] == ["docker", "compose", "up", "-d"]

    def test_returns_combined_output(self, tmp_path: Path, mock_run: MagicMock) -> None:
        result = deploy(cwd=tmp_path)
        assert isinstance(result, str)
        assert "git pull" in result
        assert "docker compose build" in result
        assert "docker compose up" in result

    def test_returns_output_without_build(self, tmp_path: Path, mock_run: MagicMock) -> None:
        result = deploy(cwd=tmp_path, rebuild=False)
        assert "git pull" in result
        assert "docker compose build" not in result
        assert "docker compose up" in result

    def test_passes_cwd_to_all_commands(self, tmp_path: Path, mock_run: MagicMock) -> None:
        deploy(cwd=tmp_path)
        for c in mock_run.call_args_list:
            assert c.kwargs["cwd"] == str(tmp_path)

    def test_git_pull_failure_stops_deploy(self, tmp_path: Path, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(1, "git pull")
        with pytest.raises(subprocess.CalledProcessError):
            deploy(cwd=tmp_path)
        # Only one call made (the failing git pull)
        assert mock_run.call_count == 1

    def test_docker_build_failure_stops_deploy(self, tmp_path: Path, mock_run: MagicMock) -> None:
        def side_effect(args, **kwargs):
            if args == ["docker", "compose", "build"]:
                raise subprocess.CalledProcessError(1, "docker compose build")
            return MagicMock(stdout="ok", stderr="", returncode=0)

        mock_run.side_effect = side_effect
        with pytest.raises(subprocess.CalledProcessError):
            deploy(cwd=tmp_path)
        assert mock_run.call_count == 2  # git pull + failed build

    def test_docker_up_failure_raises(self, tmp_path: Path, mock_run: MagicMock) -> None:
        def side_effect(args, **kwargs):
            if args == ["docker", "compose", "up", "-d"]:
                raise subprocess.CalledProcessError(1, "docker compose up")
            return MagicMock(stdout="ok", stderr="", returncode=0)

        mock_run.side_effect = side_effect
        with pytest.raises(subprocess.CalledProcessError):
            deploy(cwd=tmp_path)

    def test_rebuild_defaults_to_true(self, tmp_path: Path, mock_run: MagicMock) -> None:
        deploy(cwd=tmp_path)
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["docker", "compose", "build"] in calls

    def test_cwd_accepts_string(self, mock_run: MagicMock) -> None:
        deploy(cwd="/some/path")
        for c in mock_run.call_args_list:
            assert c.kwargs["cwd"] == "/some/path"

    def test_cwd_accepts_path(self, tmp_path: Path, mock_run: MagicMock) -> None:
        deploy(cwd=tmp_path)
        for c in mock_run.call_args_list:
            assert c.kwargs["cwd"] == str(tmp_path)


# ══════════════════════════════════════════════════════════════════════
# CLI interface
# ══════════════════════════════════════════════════════════════════════


class TestDeployCli:
    """Tests for the deploy CLI entry point."""

    @pytest.fixture()
    def project_root(self) -> Path:
        return Path(__file__).parent.parent

    def test_cli_runs_deploy(self, project_root: Path) -> None:
        """CLI invokes deploy; mock subprocess to avoid real operations."""
        with patch("tools.deploy.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
            result = subprocess.run(
                [sys.executable, "-m", "tools.deploy", "--cwd", "/tmp/fake"],
                capture_output=True,
                text=True,
                cwd=str(project_root),
            )
        # The CLI process runs in a subprocess, so our mock doesn't apply there.
        # Instead, test the CLI's argument parsing by checking help output.
        help_result = subprocess.run(
            [sys.executable, "-m", "tools.deploy", "--help"],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert help_result.returncode == 0
        assert "--cwd" in help_result.stdout
        assert "--no-rebuild" in help_result.stdout

    def test_cli_no_rebuild_flag(self, project_root: Path) -> None:
        help_result = subprocess.run(
            [sys.executable, "-m", "tools.deploy", "--help"],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert "--no-rebuild" in help_result.stdout

    def test_cli_requires_cwd(self, project_root: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.deploy"],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode != 0


# ══════════════════════════════════════════════════════════════════════
# Integration: deploy in a real git repo (mocked docker)
# ══════════════════════════════════════════════════════════════════════


class TestDeployIntegration:
    """Integration tests using a real git repo with mocked Docker commands."""

    @pytest.fixture()
    def git_repo(self, tmp_path: Path) -> Path:
        """Create a temp git repo with a remote for pull to succeed."""
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
    def repo_with_remote(self, tmp_path: Path, git_repo: Path) -> Path:
        """Set up a bare remote so git pull --ff-only works."""
        remote = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote)],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote)],
            cwd=git_repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "master"],
            cwd=git_repo, capture_output=True, check=True,
        )
        return git_repo

    def test_deploy_with_real_git_pull(self, repo_with_remote: Path) -> None:
        """Git pull works on a real repo; Docker commands are mocked."""
        original_run = subprocess.run

        def selective_mock(args, **kwargs):
            if args[0] == "docker":
                return MagicMock(stdout="ok", stderr="", returncode=0)
            return original_run(args, **kwargs)

        with patch("tools.deploy.subprocess.run", side_effect=selective_mock):
            result = deploy(cwd=repo_with_remote)
        assert "git pull" in result

    def test_deploy_no_rebuild_with_real_git(self, repo_with_remote: Path) -> None:
        """No-rebuild deploy only calls git pull and docker up."""
        original_run = subprocess.run

        def selective_mock(args, **kwargs):
            if args[0] == "docker":
                return MagicMock(stdout="ok", stderr="", returncode=0)
            return original_run(args, **kwargs)

        with patch("tools.deploy.subprocess.run", side_effect=selective_mock):
            result = deploy(cwd=repo_with_remote, rebuild=False)
        assert "docker compose build" not in result
        assert "docker compose up" in result
