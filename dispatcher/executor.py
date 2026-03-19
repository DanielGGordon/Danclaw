"""AI executor abstraction with mock and Claude implementations.

Defines the executor interface, a MockExecutor that returns canned
responses, and a ClaudeExecutor that calls ``claude -p`` as an async
subprocess with ``--resume`` for session persistence and
``--system-prompt`` for persona injection.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from dispatcher.models import StandardMessage

logger = logging.getLogger(__name__)


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

    async def execute(
        self,
        message: StandardMessage,
        *,
        persona: str | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> ExecutorResult:
        """Process *message* and return an ExecutorResult.

        Parameters
        ----------
        message:
            The incoming message to process.
        persona:
            Optional persona content (markdown) to guide the executor's
            behaviour.  Loaded from the agent's persona file by the
            dispatcher before calling execute.
        allowed_tools:
            The resolved set of tools the user is allowed to use on this
            channel.  The executor should restrict tool access to this set.
        """
        ...  # pragma: no cover


class MockExecutor:
    """Executor that returns a deterministic canned response.

    By default the response echoes the input content prefixed with
    ``"mock response: "``.  A custom fixed response can be supplied at
    construction time instead.

    The most recently received persona is stored in :attr:`last_persona`
    and the most recently received allowed_tools in :attr:`last_allowed_tools`
    so tests can verify they were passed through correctly.

    Parameters:
        fixed_response: If provided, every call returns this exact string
            instead of echoing the input.
    """

    def __init__(self, fixed_response: str | None = None) -> None:
        self._fixed_response = fixed_response
        self.last_persona: str | None = None
        self.last_allowed_tools: frozenset[str] | None = None

    async def execute(
        self,
        message: StandardMessage,
        *,
        persona: str | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> ExecutorResult:
        """Return a canned response for *message*."""
        self.last_persona = persona
        self.last_allowed_tools = allowed_tools
        if self._fixed_response is not None:
            content = self._fixed_response
        else:
            content = f"mock response: {message.content}"
        return ExecutorResult(content=content, backend="mock")


class ClaudeExecutor:
    """Executor that calls ``claude -p`` as an async subprocess.

    Runs ``claude -p "<message>" --resume <session_id>`` and optionally
    passes the agent persona via ``--system-prompt``.  Stdout is captured
    as the response content.

    Parameters
    ----------
    claude_bin:
        Path or name of the ``claude`` CLI binary.  Defaults to
        ``"claude"``.
    """

    def __init__(self, claude_bin: str = "claude") -> None:
        self._claude_bin = claude_bin

    async def execute(
        self,
        message: StandardMessage,
        *,
        persona: str | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> ExecutorResult:
        """Execute *message* via the ``claude`` CLI subprocess.

        The session ID from *message* is used with ``--resume`` to
        maintain conversation context across calls.  If *persona* is
        provided, it is passed as ``--system-prompt``.

        Raises
        ------
        RuntimeError
            If the subprocess exits with a non-zero return code.
        """
        cmd = [self._claude_bin, "-p", message.content]

        if message.session_id:
            cmd.extend(["--resume", message.session_id])

        if persona:
            cmd.extend(["--system-prompt", persona])

        logger.info("Running claude subprocess: %s", cmd)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"claude exited with code {proc.returncode}: {stderr_text}"
            )

        content = stdout.decode(errors="replace").strip()
        return ExecutorResult(content=content, backend="claude")
