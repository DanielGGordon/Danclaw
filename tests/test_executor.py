"""Tests for dispatcher.executor — mocked AI executor."""

from __future__ import annotations

import pytest

from dispatcher.executor import ExecutorResult, MockExecutor
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


# ── Protocol conformance ─────────────────────────────────────────────

class TestProtocol:
    def test_mock_executor_satisfies_protocol(self):
        """MockExecutor must be structurally compatible with the Executor
        protocol (duck-typed — no explicit subclass needed)."""
        from dispatcher.executor import Executor

        executor: Executor = MockExecutor()
        assert hasattr(executor, "execute")
