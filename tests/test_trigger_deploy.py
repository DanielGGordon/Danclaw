"""Tests for tools.trigger_deploy — agent-callable deploy entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import aiosqlite
import pytest
import pytest_asyncio

from config import (
    AgentConfig,
    ChannelPermissions,
    DanClawConfig,
    PermissionsConfig,
    UserPermissions,
)
from dispatcher.database import _SCHEMA_SQL
from dispatcher.dispatcher import Dispatcher
from dispatcher.executor import MockExecutor
from dispatcher.models import StandardMessage
from dispatcher.permissions import resolve_permissions
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from dispatcher.telemetry import TelemetryCollector
from tests.conftest import make_personas_dir
from tools.trigger_deploy import _default_project_root, trigger_deploy


# ══════════════════════════════════════════════════════════════════════
# _default_project_root
# ══════════════════════════════════════════════════════════════════════


class TestDefaultProjectRoot:
    """Tests for automatic project root detection."""

    def test_returns_path_object(self) -> None:
        root = _default_project_root()
        assert isinstance(root, Path)

    def test_root_is_repo_root(self) -> None:
        root = _default_project_root()
        # The project root should contain pyproject.toml
        assert (root / "pyproject.toml").exists()

    def test_root_contains_tools_dir(self) -> None:
        root = _default_project_root()
        assert (root / "tools").is_dir()

    def test_root_contains_config(self) -> None:
        root = _default_project_root()
        assert (root / "config" / "danclaw.json").exists()


# ══════════════════════════════════════════════════════════════════════
# trigger_deploy — mocked subprocess
# ══════════════════════════════════════════════════════════════════════


class TestTriggerDeployMocked:
    """Tests for trigger_deploy with subprocess mocked out."""

    @pytest.fixture()
    def mock_run(self) -> MagicMock:
        """Patch subprocess.run inside tools.deploy to succeed."""
        with patch("tools.deploy.subprocess.run") as mock:
            mock.return_value = MagicMock(
                stdout="ok", stderr="", returncode=0,
            )
            yield mock

    def test_calls_deploy(self, mock_run: MagicMock, tmp_path: Path) -> None:
        result = trigger_deploy(cwd=tmp_path)
        assert isinstance(result, str)
        assert "git pull" in result

    def test_defaults_to_project_root(self, mock_run: MagicMock) -> None:
        """When cwd is not specified, defaults to the project root."""
        trigger_deploy()
        # All subprocess calls should use the project root as cwd
        expected_root = str(_default_project_root())
        for c in mock_run.call_args_list:
            assert c.kwargs["cwd"] == expected_root

    def test_explicit_cwd_overrides_default(self, mock_run: MagicMock, tmp_path: Path) -> None:
        trigger_deploy(cwd=tmp_path)
        for c in mock_run.call_args_list:
            assert c.kwargs["cwd"] == str(tmp_path)

    def test_rebuild_true_by_default(self, mock_run: MagicMock, tmp_path: Path) -> None:
        trigger_deploy(cwd=tmp_path)
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["docker", "compose", "build"] in calls

    def test_rebuild_false_skips_build(self, mock_run: MagicMock, tmp_path: Path) -> None:
        trigger_deploy(cwd=tmp_path, rebuild=False)
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["docker", "compose", "build"] not in calls

    def test_returns_combined_output(self, mock_run: MagicMock, tmp_path: Path) -> None:
        result = trigger_deploy(cwd=tmp_path)
        assert "git pull" in result
        assert "docker compose up" in result

    def test_failure_propagates(self, tmp_path: Path) -> None:
        with patch("tools.deploy.subprocess.run") as mock:
            mock.side_effect = subprocess.CalledProcessError(1, "git pull")
            with pytest.raises(subprocess.CalledProcessError):
                trigger_deploy(cwd=tmp_path)


# ══════════════════════════════════════════════════════════════════════
# Telemetry-instrumented trigger_deploy
# ══════════════════════════════════════════════════════════════════════


class TestTriggerDeployTelemetry:
    """Tests for telemetry-instrumented trigger_deploy wrapper."""

    @pytest.fixture()
    def collector(self) -> TelemetryCollector:
        return TelemetryCollector()

    @pytest.fixture()
    def mock_subprocess(self) -> MagicMock:
        with patch("tools.deploy.subprocess.run") as mock:
            mock.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
            yield mock

    def test_success_emits_event(
        self, tmp_path: Path, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        from tools.instrumented import trigger_deploy as instr_trigger

        instr_trigger(cwd=tmp_path, telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "tool_execution"
        assert event.payload["tool"] == "trigger_deploy"
        assert event.payload["success"] is True
        assert event.payload["duration"] >= 0
        assert "error" not in event.payload

    def test_args_include_rebuild(
        self, tmp_path: Path, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        from tools.instrumented import trigger_deploy as instr_trigger

        instr_trigger(cwd=tmp_path, rebuild=False, telemetry=collector)
        assert collector.events[0].payload["args"]["rebuild"] is False

    def test_args_include_cwd_when_specified(
        self, tmp_path: Path, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        from tools.instrumented import trigger_deploy as instr_trigger

        instr_trigger(cwd=tmp_path, telemetry=collector)
        assert collector.events[0].payload["args"]["cwd"] == str(tmp_path)

    def test_args_omit_cwd_when_default(
        self, collector: TelemetryCollector, mock_subprocess: MagicMock,
    ) -> None:
        from tools.instrumented import trigger_deploy as instr_trigger

        instr_trigger(telemetry=collector)
        assert "cwd" not in collector.events[0].payload["args"]

    def test_failure_emits_event(
        self, tmp_path: Path, collector: TelemetryCollector,
    ) -> None:
        from tools.instrumented import trigger_deploy as instr_trigger

        with patch("tools.deploy.subprocess.run") as mock:
            mock.side_effect = subprocess.CalledProcessError(1, "git pull")
            with pytest.raises(subprocess.CalledProcessError):
                instr_trigger(cwd=tmp_path, telemetry=collector)
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.payload["tool"] == "trigger_deploy"
        assert event.payload["success"] is False
        assert "error" in event.payload

    def test_failure_reraises_exception(
        self, tmp_path: Path, collector: TelemetryCollector,
    ) -> None:
        from tools.instrumented import trigger_deploy as instr_trigger

        with patch("tools.deploy.subprocess.run") as mock:
            mock.side_effect = subprocess.CalledProcessError(128, "git pull")
            with pytest.raises(subprocess.CalledProcessError) as exc_info:
                instr_trigger(cwd=tmp_path, telemetry=collector)
            assert exc_info.value.returncode == 128


# ══════════════════════════════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════════════════════════════


class TestTriggerDeployCli:
    """Tests for the trigger_deploy CLI entry point."""

    @pytest.fixture()
    def project_root(self) -> Path:
        return Path(__file__).parent.parent

    def test_cli_help(self, project_root: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.trigger_deploy", "--help"],
            capture_output=True, text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0
        assert "--cwd" in result.stdout
        assert "--no-rebuild" in result.stdout

    def test_cli_cwd_is_optional(self, project_root: Path) -> None:
        """CLI should not require --cwd (defaults to project root)."""
        result = subprocess.run(
            [sys.executable, "-m", "tools.trigger_deploy", "--help"],
            capture_output=True, text=True,
            cwd=str(project_root),
        )
        # --cwd is optional, not required
        assert result.returncode == 0


# ══════════════════════════════════════════════════════════════════════
# Integration: admin agent triggers deploy through dispatcher
# ══════════════════════════════════════════════════════════════════════


def _deploy_dispatch_config() -> DanClawConfig:
    """Config with admin agent that has trigger_deploy access."""
    return DanClawConfig(
        agents=[
            AgentConfig(
                name="default",
                persona="default",
                backend_preference=["claude"],
            ),
            AgentConfig(
                name="admin",
                persona="admin",
                backend_preference=["claude"],
                allowed_tools=["git_ops", "deploy", "trigger_deploy"],
            ),
        ],
        permissions=PermissionsConfig(
            channels={
                "admin": ChannelPermissions(
                    allowed_tools=["git_ops", "deploy", "trigger_deploy"],
                    override=False,
                    approval_required=False,
                ),
                "general": ChannelPermissions(
                    allowed_tools=["obsidian"],
                    override=True,
                    approval_required=True,
                ),
            },
        ),
    )


class TestAdminAgentTriggersDeploy:
    """Integration: admin agent can trigger deploy through the dispatcher."""

    @pytest_asyncio.fixture
    async def db(self):
        async with aiosqlite.connect(":memory:") as conn:
            await conn.executescript(_SCHEMA_SQL)
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.commit()
            yield conn

    @pytest_asyncio.fixture
    async def repo(self, db):
        return Repository(db)

    @pytest_asyncio.fixture
    async def mgr(self, repo):
        return SessionManager(repo)

    @pytest.fixture
    def personas_dir(self, tmp_path):
        return make_personas_dir(tmp_path, {
            "default": "Default persona.",
            "admin": "Admin persona with full tool access including deploy.",
        })

    @pytest.fixture
    def collector(self) -> TelemetryCollector:
        return TelemetryCollector()

    @pytest_asyncio.fixture
    async def admin_dispatcher(self, mgr, repo, personas_dir, collector):
        return Dispatcher(
            mgr, repo, MockExecutor(),
            config=_deploy_dispatch_config(),
            personas_dir=personas_dir,
            telemetry=collector,
        )

    @pytest.mark.asyncio
    async def test_admin_channel_has_trigger_deploy_permission(self) -> None:
        config = _deploy_dispatch_config()
        tools = resolve_permissions(config.permissions, "admin", "dan")
        assert "trigger_deploy" in tools

    @pytest.mark.asyncio
    async def test_general_channel_no_trigger_deploy(self) -> None:
        config = _deploy_dispatch_config()
        tools = resolve_permissions(config.permissions, "general", "someone")
        assert "trigger_deploy" not in tools

    @pytest.mark.asyncio
    async def test_admin_dispatch_deploy_no_approval(self, admin_dispatcher) -> None:
        """Admin channel dispatches deploy requests without approval."""
        msg = StandardMessage(
            source="admin", channel_ref="admin",
            user_id="dan", content="trigger deploy",
        )
        result = await admin_dispatcher.dispatch(msg)
        assert result.backend != "system"
        assert "approval" not in result.response.lower()

    @pytest.mark.asyncio
    async def test_general_channel_blocked_from_deploy(self, admin_dispatcher) -> None:
        """Non-admin channel hits approval gate."""
        msg = StandardMessage(
            source="general", channel_ref="general-thread",
            user_id="someone", content="trigger deploy",
        )
        result = await admin_dispatcher.dispatch(msg)
        assert "approval" in result.response.lower()

    @pytest.mark.asyncio
    async def test_admin_switch_then_deploy(self, admin_dispatcher) -> None:
        """Switch to admin agent, then request deploy."""
        msg1 = StandardMessage(
            source="admin", channel_ref="admin",
            user_id="dan", content="/switch admin",
        )
        r1 = await admin_dispatcher.dispatch(msg1)
        assert "admin" in r1.response.lower()

        msg2 = StandardMessage(
            source="admin", channel_ref="admin",
            user_id="dan", content="deploy the latest changes",
            session_id=r1.session_id,
        )
        r2 = await admin_dispatcher.dispatch(msg2)
        assert r2.agent_name == "admin"
        assert r2.session_id == r1.session_id

    @pytest.mark.asyncio
    async def test_dispatch_emits_telemetry(self, admin_dispatcher, collector) -> None:
        """Deploy request through admin channel emits telemetry events."""
        msg = StandardMessage(
            source="admin", channel_ref="admin",
            user_id="dan", content="trigger deploy",
        )
        await admin_dispatcher.dispatch(msg)
        event_types = [e.event_type for e in collector.events]
        assert "message_received" in event_types
        assert "executor_invoked" in event_types

    def test_trigger_deploy_tool_function_with_telemetry(self, tmp_path: Path) -> None:
        """The trigger_deploy instrumented wrapper emits deploy telemetry."""
        from tools.instrumented import trigger_deploy as instr_trigger

        collector = TelemetryCollector()
        with patch("tools.deploy.subprocess.run") as mock:
            mock.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
            instr_trigger(cwd=tmp_path, telemetry=collector)

        assert len(collector.events) == 1
        assert collector.events[0].payload["tool"] == "trigger_deploy"
        assert collector.events[0].payload["success"] is True


# ══════════════════════════════════════════════════════════════════════
# Integration: real config includes trigger_deploy
# ══════════════════════════════════════════════════════════════════════


class TestRealConfigTriggerDeploy:
    """Smoke tests against the real project config file."""

    def test_real_config_admin_has_trigger_deploy(self) -> None:
        from config import load_config

        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        admin = cfg.get_agent("admin")
        assert "trigger_deploy" in admin.allowed_tools

    def test_real_config_admin_channel_has_trigger_deploy(self) -> None:
        from config import load_config

        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        admin_ch = cfg.permissions.channels.get("admin")
        assert "trigger_deploy" in admin_ch.allowed_tools

    def test_real_config_non_admin_no_trigger_deploy(self) -> None:
        from config import load_config

        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / "config" / "danclaw.json"
        cfg = load_config(config_path, personas_dir=project_root / "personas")
        slack_ch = cfg.permissions.channels.get("slack")
        assert "trigger_deploy" not in slack_ch.allowed_tools
