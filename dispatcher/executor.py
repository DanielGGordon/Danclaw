"""AI executor abstraction with mock and Claude implementations.

Defines the executor interface, a MockExecutor that returns canned
responses, and a ClaudeExecutor that calls ``claude -p`` as an async
subprocess with ``--resume`` for session persistence and
``--system-prompt`` for persona injection.
"""

from __future__ import annotations

import asyncio
import json
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

    Runs ``claude -p "<message>" --output-format json`` and optionally
    passes the agent persona via ``--system-prompt``.  JSON output is
    parsed to extract the response text and Claude's own session ID,
    which is tracked internally for ``--resume`` on follow-up calls.

    Parameters
    ----------
    claude_bin:
        Path or name of the ``claude`` CLI binary.  Defaults to
        ``"claude"``.
    """

    def __init__(
        self, claude_bin: str = "claude", timeout: float = 300.0,
    ) -> None:
        self._claude_bin = claude_bin
        self._timeout = timeout
        self._sessions_by_id: dict[str, str] = {}
        self._sessions_by_channel: dict[str, tuple[str | None, str]] = {}

    async def execute(
        self,
        message: StandardMessage,
        *,
        persona: str | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> ExecutorResult:
        """Execute *message* via the ``claude`` CLI subprocess.

        Claude's own session ID (returned in its JSON output) is tracked
        internally.  On follow-up messages the executor maps the
        dispatcher session ID or channel reference to the correct Claude
        session ID for ``--resume``.

        Raises
        ------
        RuntimeError
            If the subprocess exits with a non-zero return code.
        """
        cmd = [self._claude_bin, "-p", message.content,
               "--output-format", "json"]

        claude_session_id = None
        if message.session_id:
            claude_session_id = self._sessions_by_id.get(message.session_id)
        if not claude_session_id:
            entry = self._sessions_by_channel.get(message.channel_ref)
            if entry is not None:
                stored_sid, stored_claude_sid = entry
                if not (stored_sid and message.session_id
                        and stored_sid != message.session_id):
                    claude_session_id = stored_claude_sid
        if claude_session_id:
            cmd.extend(["--resume", claude_session_id])

        if persona:
            cmd.extend(["--system-prompt", persona])

        if allowed_tools is not None:
            cmd.extend(["--allowedTools", ",".join(sorted(allowed_tools))])

        logger.info("Running claude subprocess: %s", cmd)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"claude subprocess timed out after {self._timeout}s"
            )

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"claude exited with code {proc.returncode}: {stderr_text}"
            )

        raw = stdout.decode(errors="replace").strip()
        try:
            data = json.loads(raw)
            content = data.get("result", "") if isinstance(data, dict) else raw
        except (json.JSONDecodeError, TypeError):
            content = raw
            data = {}

        new_claude_sid = data.get("session_id") if isinstance(data, dict) else None
        if new_claude_sid:
            self._sessions_by_channel[message.channel_ref] = (
                message.session_id, new_claude_sid,
            )
            if message.session_id:
                self._sessions_by_id[message.session_id] = new_claude_sid

        return ExecutorResult(content=content, backend="claude")
