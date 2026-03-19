"""Tests for dispatcher.executor — mock and Claude AI executors."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from dispatcher.executor import (
    ClaudeExecutor,
    CodexExecutor,
    ExecutorResult,
    FallbackExecutor,
    MockExecutor,
    build_executor,
)
from dispatcher.models import StandardMessage


# ── Helpers ──────────────────────────────────────────────────────────

def _make_message(content: str = "hello world") -> StandardMessage:
    return StandardMessage(
        source="terminal",
        channel_ref="test-ref",
        user_id="user-1",
        content=content,
    )


# ── ExecutorResult tests ─────────────────────────────────────────────

class TestExecutorResult:
    def test_fields(self):
        result = ExecutorResult(content="hi", backend="mock")
        assert result.content == "hi"
        assert result.backend == "mock"

    def test_frozen(self):
        result = ExecutorResult(content="hi", backend="mock")
        with pytest.raises(AttributeError):
            result.content = "changed"  # type: ignore[misc]


# ── MockExecutor echo mode tests ─────────────────────────────────────

class TestMockExecutorEcho:
    @pytest.mark.asyncio
    async def test_echoes_input(self):
        executor = MockExecutor()
        msg = _make_message("ping")
        result = await executor.execute(msg)
        assert result.content == "mock response: ping"
        assert result.backend == "mock"

    @pytest.mark.asyncio
    async def test_echoes_different_content(self):
        executor = MockExecutor()
        result = await executor.execute(_make_message("other text"))
        assert result.content == "mock response: other text"

    @pytest.mark.asyncio
    async def test_echoes_empty_content(self):
        executor = MockExecutor()
        result = await executor.execute(_make_message(""))
        assert result.content == "mock response: "


# ── MockExecutor fixed response mode tests ───────────────────────────

class TestMockExecutorFixed:
    @pytest.mark.asyncio
    async def test_returns_fixed_response(self):
        executor = MockExecutor(fixed_response="I am a bot.")
        result = await executor.execute(_make_message("anything"))
        assert result.content == "I am a bot."
        assert result.backend == "mock"

    @pytest.mark.asyncio
    async def test_fixed_ignores_input_content(self):
        executor = MockExecutor(fixed_response="constant")
        r1 = await executor.execute(_make_message("aaa"))
        r2 = await executor.execute(_make_message("bbb"))
        assert r1.content == r2.content == "constant"

    @pytest.mark.asyncio
    async def test_fixed_empty_string(self):
        executor = MockExecutor(fixed_response="")
        result = await executor.execute(_make_message("ignored"))
        assert result.content == ""


# ── Persona support ──────────────────────────────────────────────────

class TestMockExecutorPersona:
    @pytest.mark.asyncio
    async def test_stores_persona(self):
        executor = MockExecutor()
        await executor.execute(_make_message("hi"), persona="Be concise.")
        assert executor.last_persona == "Be concise."

    @pytest.mark.asyncio
    async def test_persona_defaults_to_none(self):
        executor = MockExecutor()
        await executor.execute(_make_message("hi"))
        assert executor.last_persona is None

    @pytest.mark.asyncio
    async def test_last_persona_updated_each_call(self):
        executor = MockExecutor()
        await executor.execute(_make_message("a"), persona="first")
        assert executor.last_persona == "first"
        await executor.execute(_make_message("b"), persona="second")
        assert executor.last_persona == "second"


# ── ClaudeExecutor tests ─────────────────────────────────────────────

def _make_proc_mock(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Create a mock asyncio.Process with the given outputs."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


class TestClaudeExecutorCommand:
    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_basic_command_construction(self, mock_exec):
        proc = _make_proc_mock(stdout=b"Hello from Claude")
        mock_exec.return_value = proc

        executor = ClaudeExecutor()
        msg = _make_message("What is 2+2?")
        msg = StandardMessage(
            source="terminal", channel_ref="ref", user_id="u1",
            content="What is 2+2?", session_id="sess-abc",
        )
        result = await executor.execute(msg)

        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "claude"
        assert args[1] == "-p"
        assert args[2] == "What is 2+2?"
        assert "--resume" in args
        assert "sess-abc" in args

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_session_id_used_for_resume(self, mock_exec):
        proc = _make_proc_mock(stdout=b"response")
        mock_exec.return_value = proc

        executor = ClaudeExecutor()
        msg = StandardMessage(
            source="terminal", channel_ref="ref", user_id="u1",
            content="hi", session_id="my-session-123",
        )
        await executor.execute(msg)

        args = mock_exec.call_args[0]
        resume_idx = list(args).index("--resume")
        assert args[resume_idx + 1] == "my-session-123"

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_no_resume_without_session_id(self, mock_exec):
        proc = _make_proc_mock(stdout=b"response")
        mock_exec.return_value = proc

        executor = ClaudeExecutor()
        msg = _make_message("hi")  # no session_id
        await executor.execute(msg)

        args = mock_exec.call_args[0]
        assert "--resume" not in args


class TestClaudeExecutorPersona:
    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_persona_passed_as_system_prompt(self, mock_exec):
        proc = _make_proc_mock(stdout=b"ok")
        mock_exec.return_value = proc

        executor = ClaudeExecutor()
        msg = _make_message("hello")
        await executor.execute(msg, persona="You are a helpful assistant.")

        args = mock_exec.call_args[0]
        assert "--system-prompt" in args
        sp_idx = list(args).index("--system-prompt")
        assert args[sp_idx + 1] == "You are a helpful assistant."

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_no_system_prompt_without_persona(self, mock_exec):
        proc = _make_proc_mock(stdout=b"ok")
        mock_exec.return_value = proc

        executor = ClaudeExecutor()
        msg = _make_message("hello")
        await executor.execute(msg)

        args = mock_exec.call_args[0]
        assert "--system-prompt" not in args

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_empty_persona_not_passed(self, mock_exec):
        proc = _make_proc_mock(stdout=b"ok")
        mock_exec.return_value = proc

        executor = ClaudeExecutor()
        msg = _make_message("hello")
        await executor.execute(msg, persona="")

        args = mock_exec.call_args[0]
        assert "--system-prompt" not in args


class TestClaudeExecutorOutput:
    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_stdout_captured_as_content(self, mock_exec):
        proc = _make_proc_mock(stdout=b"The answer is 4.\n")
        mock_exec.return_value = proc

        executor = ClaudeExecutor()
        result = await executor.execute(_make_message("2+2"))

        assert result.content == "The answer is 4."
        assert result.backend == "claude"

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_empty_stdout(self, mock_exec):
        proc = _make_proc_mock(stdout=b"")
        mock_exec.return_value = proc

        executor = ClaudeExecutor()
        result = await executor.execute(_make_message("hi"))
        assert result.content == ""

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_nonzero_exit_raises_runtime_error(self, mock_exec):
        proc = _make_proc_mock(returncode=1, stderr=b"something broke")
        mock_exec.return_value = proc

        executor = ClaudeExecutor()
        with pytest.raises(RuntimeError, match="claude exited with code 1"):
            await executor.execute(_make_message("fail"))

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_nonzero_exit_includes_stderr(self, mock_exec):
        proc = _make_proc_mock(returncode=2, stderr=b"detailed error msg")
        mock_exec.return_value = proc

        executor = ClaudeExecutor()
        with pytest.raises(RuntimeError, match="detailed error msg"):
            await executor.execute(_make_message("fail"))


class TestClaudeExecutorCustomBin:
    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_custom_binary(self, mock_exec):
        proc = _make_proc_mock(stdout=b"ok")
        mock_exec.return_value = proc

        executor = ClaudeExecutor(claude_bin="/usr/local/bin/claude")
        await executor.execute(_make_message("hi"))

        args = mock_exec.call_args[0]
        assert args[0] == "/usr/local/bin/claude"


# ── Protocol conformance ─────────────────────────────────────────────

class TestProtocol:
    def test_mock_executor_satisfies_protocol(self):
        """MockExecutor must be structurally compatible with the Executor
        protocol (duck-typed — no explicit subclass needed)."""
        from dispatcher.executor import Executor

        executor: Executor = MockExecutor()
        assert hasattr(executor, "execute")

    def test_claude_executor_satisfies_protocol(self):
        """ClaudeExecutor must be structurally compatible with the Executor
        protocol (duck-typed — no explicit subclass needed)."""
        from dispatcher.executor import Executor

        executor: Executor = ClaudeExecutor()
        assert hasattr(executor, "execute")

    def test_codex_executor_satisfies_protocol(self):
        """CodexExecutor must be structurally compatible with the Executor
        protocol (duck-typed — no explicit subclass needed)."""
        from dispatcher.executor import Executor

        executor: Executor = CodexExecutor()
        assert hasattr(executor, "execute")

    def test_fallback_executor_satisfies_protocol(self):
        """FallbackExecutor must be structurally compatible with the Executor
        protocol (duck-typed — no explicit subclass needed)."""
        from dispatcher.executor import Executor

        executor: Executor = FallbackExecutor([MockExecutor()])
        assert hasattr(executor, "execute")


# ── CodexExecutor tests ─────────────────────────────────────────────

class TestCodexExecutorCommand:
    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_basic_command_construction(self, mock_exec):
        proc = _make_proc_mock(stdout=b"Hello from Codex")
        mock_exec.return_value = proc

        executor = CodexExecutor()
        msg = _make_message("What is 2+2?")
        result = await executor.execute(msg)

        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "codex"
        assert args[1] == "-q"
        assert args[2] == "What is 2+2?"

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_custom_binary(self, mock_exec):
        proc = _make_proc_mock(stdout=b"ok")
        mock_exec.return_value = proc

        executor = CodexExecutor(codex_bin="/usr/local/bin/codex")
        await executor.execute(_make_message("hi"))

        args = mock_exec.call_args[0]
        assert args[0] == "/usr/local/bin/codex"

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_no_resume_or_system_prompt_flags(self, mock_exec):
        proc = _make_proc_mock(stdout=b"ok")
        mock_exec.return_value = proc

        executor = CodexExecutor()
        msg = StandardMessage(
            source="terminal", channel_ref="ref", user_id="u1",
            content="hi", session_id="sess-abc",
        )
        await executor.execute(msg, persona="You are helpful.")

        args = mock_exec.call_args[0]
        assert "--resume" not in args
        assert "--system-prompt" not in args


class TestCodexExecutorOutput:
    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_stdout_captured_as_content(self, mock_exec):
        proc = _make_proc_mock(stdout=b"The answer is 4.\n")
        mock_exec.return_value = proc

        executor = CodexExecutor()
        result = await executor.execute(_make_message("2+2"))

        assert result.content == "The answer is 4."
        assert result.backend == "codex"

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_nonzero_exit_raises_runtime_error(self, mock_exec):
        proc = _make_proc_mock(returncode=1, stderr=b"something broke")
        mock_exec.return_value = proc

        executor = CodexExecutor()
        with pytest.raises(RuntimeError, match="codex exited with code 1"):
            await executor.execute(_make_message("fail"))

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_nonzero_exit_includes_stderr(self, mock_exec):
        proc = _make_proc_mock(returncode=2, stderr=b"detailed error msg")
        mock_exec.return_value = proc

        executor = CodexExecutor()
        with pytest.raises(RuntimeError, match="detailed error msg"):
            await executor.execute(_make_message("fail"))


# ── FallbackExecutor tests ──────────────────────────────────────────

class TestFallbackExecutorInit:
    def test_requires_at_least_one_executor(self):
        with pytest.raises(ValueError, match="at least one"):
            FallbackExecutor([])


class TestFallbackExecutorSuccess:
    @pytest.mark.asyncio
    async def test_returns_first_executor_result_on_success(self):
        executor = FallbackExecutor([
            MockExecutor(fixed_response="first"),
            MockExecutor(fixed_response="second"),
        ])
        result = await executor.execute(_make_message("hi"))
        assert result.content == "first"
        assert result.backend == "mock"


class TestFallbackExecutorFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_second_on_first_failure(self):
        failing = MockExecutor()
        # Monkey-patch execute to raise
        async def _raise(*a, **kw):
            raise RuntimeError("primary failed")
        failing.execute = _raise

        backup = MockExecutor(fixed_response="backup response")

        executor = FallbackExecutor([failing, backup])
        result = await executor.execute(_make_message("hi"))
        assert result.content == "backup response"

    @pytest.mark.asyncio
    async def test_passes_persona_and_tools_to_fallback(self):
        failing = MockExecutor()
        async def _raise(*a, **kw):
            raise RuntimeError("primary failed")
        failing.execute = _raise

        backup = MockExecutor(fixed_response="ok")

        executor = FallbackExecutor([failing, backup])
        tools = frozenset(["tool_a"])
        await executor.execute(
            _make_message("hi"), persona="Be nice.", allowed_tools=tools,
        )
        assert backup.last_persona == "Be nice."
        assert backup.last_allowed_tools == tools


class TestFallbackExecutorAllFail:
    @pytest.mark.asyncio
    async def test_raises_last_exception_when_all_fail(self):
        fail1 = MockExecutor()
        async def _raise1(*a, **kw):
            raise RuntimeError("first failed")
        fail1.execute = _raise1

        fail2 = MockExecutor()
        async def _raise2(*a, **kw):
            raise ValueError("second failed")
        fail2.execute = _raise2

        executor = FallbackExecutor([fail1, fail2])
        with pytest.raises(ValueError, match="second failed"):
            await executor.execute(_make_message("hi"))


# ── build_executor factory tests ─────────────────────────────────────

class TestBuildExecutorDefault:
    def test_default_preference_creates_claude_then_codex(self):
        executor = build_executor(["claude", "codex"])
        assert isinstance(executor, FallbackExecutor)
        assert len(executor._executors) == 2
        assert isinstance(executor._executors[0], ClaudeExecutor)
        assert isinstance(executor._executors[1], CodexExecutor)

    def test_single_backend(self):
        executor = build_executor(["claude"])
        assert len(executor._executors) == 1
        assert isinstance(executor._executors[0], ClaudeExecutor)

    def test_mock_backend(self):
        executor = build_executor(["mock"])
        assert len(executor._executors) == 1
        assert isinstance(executor._executors[0], MockExecutor)


class TestBuildExecutorCustomOrder:
    def test_codex_before_claude(self):
        executor = build_executor(["codex", "claude"])
        assert isinstance(executor._executors[0], CodexExecutor)
        assert isinstance(executor._executors[1], ClaudeExecutor)

    def test_three_backends(self):
        executor = build_executor(["codex", "claude", "mock"])
        assert len(executor._executors) == 3
        assert isinstance(executor._executors[0], CodexExecutor)
        assert isinstance(executor._executors[1], ClaudeExecutor)
        assert isinstance(executor._executors[2], MockExecutor)


class TestBuildExecutorErrors:
    def test_unknown_backend_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown backend 'gpt4'"):
            build_executor(["gpt4"])

    def test_unknown_backend_lists_known(self):
        with pytest.raises(ValueError, match="Known backends:"):
            build_executor(["nonexistent"])

    def test_empty_list_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            build_executor([])

    def test_mixed_valid_and_invalid_raises(self):
        with pytest.raises(ValueError, match="Unknown backend 'bad'"):
            build_executor(["claude", "bad"])


# ── Timeout tests ────────────────────────────────────────────────────

class TestClaudeExecutorTimeout:
    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_timeout_raises_timeout_error(self, mock_exec):
        from unittest.mock import MagicMock

        proc = AsyncMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        mock_exec.return_value = proc

        executor = ClaudeExecutor(timeout=5)
        with patch(
            "dispatcher.executor.asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError(),
        ):
            with pytest.raises(TimeoutError, match="claude subprocess timed out"):
                await executor.execute(_make_message("slow request"))

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_no_timeout_by_default(self, mock_exec):
        """When timeout is None, wait_for should pass timeout=None."""
        proc = _make_proc_mock(stdout=b"ok")
        mock_exec.return_value = proc

        executor = ClaudeExecutor()
        assert executor._timeout is None

        with patch("dispatcher.executor.asyncio.wait_for", new_callable=AsyncMock) as mock_wf:
            mock_wf.return_value = (b"ok", b"")
            await executor.execute(_make_message("hi"))
            mock_wf.assert_called_once()
            _, kwargs = mock_wf.call_args
            assert kwargs["timeout"] is None

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_timeout_value_passed_to_wait_for(self, mock_exec):
        proc = _make_proc_mock(stdout=b"ok")
        mock_exec.return_value = proc

        executor = ClaudeExecutor(timeout=30)

        with patch("dispatcher.executor.asyncio.wait_for", new_callable=AsyncMock) as mock_wf:
            mock_wf.return_value = (b"ok", b"")
            await executor.execute(_make_message("hi"))
            _, kwargs = mock_wf.call_args
            assert kwargs["timeout"] == 30


class TestCodexExecutorTimeout:
    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_timeout_raises_timeout_error(self, mock_exec):
        from unittest.mock import MagicMock

        proc = AsyncMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        mock_exec.return_value = proc

        executor = CodexExecutor(timeout=5)
        with patch(
            "dispatcher.executor.asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError(),
        ):
            with pytest.raises(TimeoutError, match="codex subprocess timed out"):
                await executor.execute(_make_message("slow request"))

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    @patch("dispatcher.executor.asyncio.create_subprocess_exec")
    async def test_no_timeout_by_default(self, mock_exec):
        proc = _make_proc_mock(stdout=b"ok")
        mock_exec.return_value = proc

        executor = CodexExecutor()
        assert executor._timeout is None


class TestFallbackExecutorTimeoutFallback:
    @pytest.mark.asyncio
    async def test_timeout_error_triggers_fallback(self):
        """TimeoutError from the first executor should cause fallback."""
        failing = MockExecutor()

        async def _timeout(*a, **kw):
            raise TimeoutError("timed out")

        failing.execute = _timeout

        backup = MockExecutor(fixed_response="backup response")
        executor = FallbackExecutor([failing, backup])
        result = await executor.execute(_make_message("hi"))
        assert result.content == "backup response"


class TestBuildExecutorTimeout:
    def test_timeout_passed_to_claude_executor(self):
        executor = build_executor(["claude"], timeout=60)
        assert isinstance(executor._executors[0], ClaudeExecutor)
        assert executor._executors[0]._timeout == 60

    def test_timeout_passed_to_codex_executor(self):
        executor = build_executor(["codex"], timeout=45)
        assert isinstance(executor._executors[0], CodexExecutor)
        assert executor._executors[0]._timeout == 45

    def test_timeout_not_passed_to_mock(self):
        executor = build_executor(["mock"], timeout=60)
        assert isinstance(executor._executors[0], MockExecutor)
        # MockExecutor has no _timeout attribute
        assert not hasattr(executor._executors[0], "_timeout")

    def test_default_timeout_is_none(self):
        executor = build_executor(["claude"])
        assert executor._executors[0]._timeout is None

    def test_mixed_backends_with_timeout(self):
        executor = build_executor(["claude", "codex", "mock"], timeout=90)
        assert executor._executors[0]._timeout == 90
        assert executor._executors[1]._timeout == 90
        assert not hasattr(executor._executors[2], "_timeout")


# ── Fallback notification tests ──────────────────────────────────────

class TestFallbackNotificationSilent:
    """Silent mode: no notification text prepended, no callback called."""

    @pytest.mark.asyncio
    async def test_silent_is_default(self):
        failing = MockExecutor()
        async def _raise(*a, **kw):
            raise RuntimeError("primary failed")
        failing.execute = _raise

        backup = MockExecutor(fixed_response="backup response")
        executor = FallbackExecutor([failing, backup])
        result = await executor.execute(_make_message("hi"))
        assert result.content == "backup response"

    @pytest.mark.asyncio
    async def test_silent_explicit(self):
        failing = MockExecutor()
        async def _raise(*a, **kw):
            raise RuntimeError("primary failed")
        failing.execute = _raise

        backup = MockExecutor(fixed_response="backup response")
        executor = FallbackExecutor(
            [failing, backup], fallback_notification="silent",
        )
        result = await executor.execute(_make_message("hi"))
        assert result.content == "backup response"

    @pytest.mark.asyncio
    async def test_silent_no_callback(self):
        failing = MockExecutor()
        async def _raise(*a, **kw):
            raise RuntimeError("primary failed")
        failing.execute = _raise

        calls = []
        backup = MockExecutor(fixed_response="backup")
        executor = FallbackExecutor(
            [failing, backup],
            fallback_notification="silent",
            notification_callback=lambda text: calls.append(text),
        )
        await executor.execute(_make_message("hi"))
        assert calls == []


class TestFallbackNotificationNotify:
    """Notify mode: standard message prepended, callback called."""

    @pytest.mark.asyncio
    async def test_notify_prepends_standard_message(self):
        failing = MockExecutor()
        async def _raise(*a, **kw):
            raise RuntimeError("primary failed")
        failing.execute = _raise

        backup = MockExecutor(fixed_response="backup response")
        executor = FallbackExecutor(
            [failing, backup], fallback_notification="notify",
        )
        result = await executor.execute(_make_message("hi"))
        assert result.content == "[Switched to backup AI]\n\nbackup response"

    @pytest.mark.asyncio
    async def test_notify_calls_callback(self):
        failing = MockExecutor()
        async def _raise(*a, **kw):
            raise RuntimeError("primary failed")
        failing.execute = _raise

        calls = []
        backup = MockExecutor(fixed_response="ok")
        executor = FallbackExecutor(
            [failing, backup],
            fallback_notification="notify",
            notification_callback=lambda text: calls.append(text),
        )
        await executor.execute(_make_message("hi"))
        assert calls == ["[Switched to backup AI]"]

    @pytest.mark.asyncio
    async def test_notify_preserves_backend(self):
        failing = MockExecutor()
        async def _raise(*a, **kw):
            raise RuntimeError("primary failed")
        failing.execute = _raise

        backup = MockExecutor(fixed_response="ok")
        executor = FallbackExecutor(
            [failing, backup], fallback_notification="notify",
        )
        result = await executor.execute(_make_message("hi"))
        assert result.backend == "mock"


class TestFallbackNotificationCustom:
    """Custom string mode: custom text prepended, callback called."""

    @pytest.mark.asyncio
    async def test_custom_text_prepended(self):
        failing = MockExecutor()
        async def _raise(*a, **kw):
            raise RuntimeError("primary failed")
        failing.execute = _raise

        backup = MockExecutor(fixed_response="fallback answer")
        executor = FallbackExecutor(
            [failing, backup],
            fallback_notification="Using alternate model due to outage.",
        )
        result = await executor.execute(_make_message("hi"))
        assert result.content == "Using alternate model due to outage.\n\nfallback answer"

    @pytest.mark.asyncio
    async def test_custom_text_calls_callback(self):
        failing = MockExecutor()
        async def _raise(*a, **kw):
            raise RuntimeError("primary failed")
        failing.execute = _raise

        calls = []
        backup = MockExecutor(fixed_response="ok")
        executor = FallbackExecutor(
            [failing, backup],
            fallback_notification="Custom notice",
            notification_callback=lambda text: calls.append(text),
        )
        await executor.execute(_make_message("hi"))
        assert calls == ["Custom notice"]


class TestFallbackNotificationPrimarySuccess:
    """When the primary executor succeeds, no notification regardless of mode."""

    @pytest.mark.asyncio
    async def test_no_notification_on_primary_success_notify(self):
        primary = MockExecutor(fixed_response="primary response")
        backup = MockExecutor(fixed_response="backup response")
        calls = []
        executor = FallbackExecutor(
            [primary, backup],
            fallback_notification="notify",
            notification_callback=lambda text: calls.append(text),
        )
        result = await executor.execute(_make_message("hi"))
        assert result.content == "primary response"
        assert calls == []

    @pytest.mark.asyncio
    async def test_no_notification_on_primary_success_custom(self):
        primary = MockExecutor(fixed_response="primary")
        backup = MockExecutor(fixed_response="backup")
        executor = FallbackExecutor(
            [primary, backup],
            fallback_notification="Custom notice",
        )
        result = await executor.execute(_make_message("hi"))
        assert result.content == "primary"


class TestBuildExecutorFallbackNotification:
    """build_executor passes fallback_notification through."""

    def test_default_is_silent(self):
        executor = build_executor(["claude", "codex"])
        assert executor._fallback_notification == "silent"

    def test_notify_mode(self):
        executor = build_executor(
            ["claude", "codex"], fallback_notification="notify",
        )
        assert executor._fallback_notification == "notify"

    def test_custom_mode(self):
        executor = build_executor(
            ["claude", "codex"],
            fallback_notification="Backup AI is responding.",
        )
        assert executor._fallback_notification == "Backup AI is responding."

    def test_callback_passed_through(self):
        calls = []
        executor = build_executor(
            ["claude", "codex"],
            fallback_notification="notify",
            notification_callback=lambda text: calls.append(text),
        )
        assert executor._notification_callback is not None
