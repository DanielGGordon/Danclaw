"""Tests for telemetry-instrumented deploy wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dispatcher.telemetry import TelemetryCollector
from tools.instrumented import deploy


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def collector() -> TelemetryCollector:
    return TelemetryCollector()


@pytest.fixture()
def mock_subprocess() -> MagicMock:
    """Patch subprocess.run inside tools.deploy to succeed."""
    with patch("tools.deploy.subprocess.run") as mock:
        mock.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        yield mock


# ══════════════════════════════════════════════════════════════════════
# Success events
# ══════════════════════════════════════════════════════════════════════


class TestDeployTelemetrySuccess:
    """Deploy telemetry on successful execution."""

    def test_success_emits_event(
        self, tmp_path: Path, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        deploy(cwd=tmp_path, telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "tool_execution"
        assert event.payload["tool"] == "deploy"
        assert event.payload["success"] is True
        assert event.payload["duration"] >= 0
        assert "error" not in event.payload

    def test_args_include_cwd(
        self, tmp_path: Path, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        deploy(cwd=tmp_path, telemetry=collector)
        assert collector.events[0].payload["args"]["cwd"] == str(tmp_path)

    def test_args_include_rebuild_true(
        self, tmp_path: Path, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        deploy(cwd=tmp_path, rebuild=True, telemetry=collector)
        assert collector.events[0].payload["args"]["rebuild"] is True

    def test_args_include_rebuild_false(
        self, tmp_path: Path, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        deploy(cwd=tmp_path, rebuild=False, telemetry=collector)
        assert collector.events[0].payload["args"]["rebuild"] is False

    def test_timestamp_is_set(
        self, tmp_path: Path, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        deploy(cwd=tmp_path, telemetry=collector)
        assert collector.events[0].timestamp > 0

    def test_returns_deploy_output(
        self, tmp_path: Path, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        result = deploy(cwd=tmp_path, telemetry=collector)
        assert isinstance(result, str)
        assert "git pull" in result


# ══════════════════════════════════════════════════════════════════════
# Failure events
# ══════════════════════════════════════════════════════════════════════


class TestDeployTelemetryFailure:
    """Deploy telemetry on failed execution."""

    def test_failure_emits_event(
        self, tmp_path: Path, collector: TelemetryCollector,
    ) -> None:
        with patch("tools.deploy.subprocess.run") as mock:
            mock.side_effect = subprocess.CalledProcessError(1, "git pull")
            with pytest.raises(subprocess.CalledProcessError):
                deploy(cwd=tmp_path, telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.payload["tool"] == "deploy"
        assert event.payload["success"] is False
        assert "error" in event.payload

    def test_failure_includes_duration(
        self, tmp_path: Path, collector: TelemetryCollector,
    ) -> None:
        with patch("tools.deploy.subprocess.run") as mock:
            mock.side_effect = subprocess.CalledProcessError(1, "git pull")
            with pytest.raises(subprocess.CalledProcessError):
                deploy(cwd=tmp_path, telemetry=collector)
        assert collector.events[0].payload["duration"] >= 0

    def test_failure_reraises_exception(
        self, tmp_path: Path, collector: TelemetryCollector,
    ) -> None:
        with patch("tools.deploy.subprocess.run") as mock:
            mock.side_effect = subprocess.CalledProcessError(128, "git pull")
            with pytest.raises(subprocess.CalledProcessError) as exc_info:
                deploy(cwd=tmp_path, telemetry=collector)
            assert exc_info.value.returncode == 128

    def test_failure_timestamp_is_set(
        self, tmp_path: Path, collector: TelemetryCollector,
    ) -> None:
        with patch("tools.deploy.subprocess.run") as mock:
            mock.side_effect = subprocess.CalledProcessError(1, "git pull")
            with pytest.raises(subprocess.CalledProcessError):
                deploy(cwd=tmp_path, telemetry=collector)
        assert collector.events[0].timestamp > 0


# ══════════════════════════════════════════════════════════════════════
# Integration: deploy in admin pipeline
# ══════════════════════════════════════════════════════════════════════


class TestDeployTelemetryIntegration:
    """Deploy telemetry combined with other instrumented tools."""

    def test_deploy_event_distinct_from_git_ops(
        self, tmp_path: Path, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        """Deploy events use 'deploy' tool name, not 'git_pull'."""
        deploy(cwd=tmp_path, telemetry=collector)
        assert collector.events[0].payload["tool"] == "deploy"

    def test_multiple_deploys_emit_multiple_events(
        self, tmp_path: Path, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        deploy(cwd=tmp_path, telemetry=collector)
        deploy(cwd=tmp_path, rebuild=False, telemetry=collector)
        assert len(collector.events) == 2
        assert all(e.payload["tool"] == "deploy" for e in collector.events)
        assert collector.events[0].payload["args"]["rebuild"] is True
        assert collector.events[1].payload["args"]["rebuild"] is False
