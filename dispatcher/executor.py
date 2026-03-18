"""AI executor abstraction and mocked implementation.

Defines the executor interface and a MockExecutor that returns canned
responses.  The real executor (Phase 6) will call ``claude -p`` and
``codex`` as subprocesses; the mock allows the full dispatcher pipeline
to be developed and tested without external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dispatcher.models import StandardMessage


@dataclass(frozen=True)
class ExecutorResult:
    """Value object returned by an executor after processing a message.

    Attributes:
        content: The response text produced by the executor.
        backend: Name of the backend that generated the response
            (e.g. ``"mock"``, ``"claude"``, ``"codex"``).
    """

    content: str
    backend: str


class Executor(Protocol):
    """Protocol that all executor implementations must satisfy."""

    async def execute(self, message: StandardMessage) -> ExecutorResult:
        """Process *message* and return an ExecutorResult."""
        ...  # pragma: no cover


class MockExecutor:
    """Executor that returns a deterministic canned response.

    By default the response echoes the input content prefixed with
    ``"mock response: "``.  A custom fixed response can be supplied at
    construction time instead.

    Parameters:
        fixed_response: If provided, every call returns this exact string
            instead of echoing the input.
    """

    def __init__(self, fixed_response: str | None = None) -> None:
        self._fixed_response = fixed_response

    async def execute(self, message: StandardMessage) -> ExecutorResult:
        """Return a canned response for *message*."""
        if self._fixed_response is not None:
            content = self._fixed_response
        else:
            content = f"mock response: {message.content}"
        return ExecutorResult(content=content, backend="mock")
