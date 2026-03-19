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
from typing import Callable, Protocol

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
    timeout:
        Maximum seconds to wait for the subprocess.  ``None`` means
        no timeout.  Defaults to ``None``.
    """

    def __init__(
        self, claude_bin: str = "claude", *, timeout: int | None = None,
    ) -> None:
        self._claude_bin = claude_bin
        self._timeout = timeout

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
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(
                f"claude subprocess timed out after {self._timeout}s"
            )

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"claude exited with code {proc.returncode}: {stderr_text}"
            )

        content = stdout.decode(errors="replace").strip()
        return ExecutorResult(content=content, backend="claude")


class CodexExecutor:
    """Executor that calls ``codex`` as an async subprocess.

    Runs ``codex -q "<message>"`` in quiet mode and captures stdout as
    the response.  Unlike ``claude``, Codex does not support
    ``--resume`` or ``--system-prompt`` flags, so those are ignored.

    Parameters
    ----------
    codex_bin:
        Path or name of the ``codex`` CLI binary.  Defaults to
        ``"codex"``.
    timeout:
        Maximum seconds to wait for the subprocess.  ``None`` means
        no timeout.  Defaults to ``None``.
    """

    def __init__(
        self, codex_bin: str = "codex", *, timeout: int | None = None,
    ) -> None:
        self._codex_bin = codex_bin
        self._timeout = timeout

    async def execute(
        self,
        message: StandardMessage,
        *,
        persona: str | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> ExecutorResult:
        """Execute *message* via the ``codex`` CLI subprocess.

        Raises
        ------
        RuntimeError
            If the subprocess exits with a non-zero return code.
        """
        cmd = [self._codex_bin, "-q", message.content]

        logger.info("Running codex subprocess: %s", cmd)

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
            raise TimeoutError(
                f"codex subprocess timed out after {self._timeout}s"
            )

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"codex exited with code {proc.returncode}: {stderr_text}"
            )

        content = stdout.decode(errors="replace").strip()
        return ExecutorResult(content=content, backend="codex")


# ── Backend registry and factory ─────────────────────────────────────

_BACKEND_REGISTRY: dict[str, type] = {
    "claude": ClaudeExecutor,
    "codex": CodexExecutor,
    "mock": MockExecutor,
}


def build_executor(
    backend_preference: list[str],
    *,
    timeout: int | None = None,
    fallback_notification: str = "silent",
    notification_callback: Callable[[str], None] | None = None,
) -> FallbackExecutor:
    """Build a :class:`FallbackExecutor` from a list of backend names.

    Maps each name in *backend_preference* to the corresponding executor
    class, instantiates it with default arguments, and wraps them all in
    a :class:`FallbackExecutor`.

    Parameters
    ----------
    backend_preference:
        Ordered list of backend names (e.g. ``["claude", "codex"]``).
        Must be non-empty.  Each name must be a key in the backend
        registry (``"claude"``, ``"codex"``, ``"mock"``).
    timeout:
        Maximum seconds each executor may run.  Passed through to
        :class:`ClaudeExecutor` and :class:`CodexExecutor`.  ``None``
        means no timeout.  Defaults to ``None``.
    fallback_notification:
        Controls notification on fallback.  ``"silent"`` (default)
        suppresses notification, ``"notify"`` uses a standard message,
        or a custom string.  Passed through to :class:`FallbackExecutor`.
    notification_callback:
        Optional callable invoked with notification text on fallback.
        Passed through to :class:`FallbackExecutor`.

    Returns
    -------
    FallbackExecutor
        An executor that tries each backend in the given order.

    Raises
    ------
    ValueError
        If *backend_preference* is empty or contains an unknown backend
        name.
    """
    if not backend_preference:
        raise ValueError("backend_preference must be a non-empty list")

    # Backends that accept a timeout keyword
    _TIMEOUT_BACKENDS = {"claude", "codex"}

    executors = []
    for name in backend_preference:
        cls = _BACKEND_REGISTRY.get(name)
        if cls is None:
            known = ", ".join(sorted(_BACKEND_REGISTRY))
            raise ValueError(
                f"Unknown backend '{name}'. "
                f"Known backends: {known}"
            )
        if name in _TIMEOUT_BACKENDS and timeout is not None:
            executors.append(cls(timeout=timeout))
        else:
            executors.append(cls())
    return FallbackExecutor(
        executors,
        fallback_notification=fallback_notification,
        notification_callback=notification_callback,
    )


_FALLBACK_NOTIFY_TEXT = "[Switched to backup AI]"


class FallbackExecutor:
    """Executor that tries multiple executors in sequence.

    Accepts an ordered list of executors and calls each in turn.  If an
    executor raises any exception, it is logged and the next executor is
    tried.  If all executors fail, the last exception is re-raised.

    When a fallback occurs (i.e. a non-primary executor succeeds),
    the optional *notification_callback* is invoked with the
    notification text.  The notification text is then prepended to
    the response content.

    Parameters
    ----------
    executors:
        Ordered list of executor instances to try.  Must contain at
        least one executor.
    fallback_notification:
        Controls notification behaviour on fallback.  ``"silent"``
        (default) suppresses notification, ``"notify"`` uses a
        standard message, or a custom string to use as the
        notification text.
    notification_callback:
        Optional callable invoked with the notification text when a
        fallback occurs (and the mode is not ``"silent"``).
    """

    def __init__(
        self,
        executors: list,
        *,
        fallback_notification: str = "silent",
        notification_callback: Callable[[str], None] | None = None,
    ) -> None:
        if not executors:
            raise ValueError("FallbackExecutor requires at least one executor")
        self._executors = list(executors)
        self._fallback_notification = fallback_notification
        self._notification_callback = notification_callback

    def _resolve_notification(self) -> str | None:
        """Return the notification text, or ``None`` for silent mode."""
        if self._fallback_notification == "silent":
            return None
        if self._fallback_notification == "notify":
            return _FALLBACK_NOTIFY_TEXT
        return self._fallback_notification

    async def execute(
        self,
        message: StandardMessage,
        *,
        persona: str | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> ExecutorResult:
        """Try each executor in order, falling back on failure.

        Returns the result from the first executor that succeeds.
        If all executors fail, raises the exception from the last one.
        If a fallback executor succeeds, the configured notification
        text is prepended to the response content.
        """
        last_exc: Exception | None = None
        for idx, executor in enumerate(self._executors):
            try:
                result = await executor.execute(
                    message, persona=persona, allowed_tools=allowed_tools,
                )
            except Exception as exc:
                logger.warning(
                    "Executor %s failed: %s; trying next fallback",
                    type(executor).__name__,
                    exc,
                )
                last_exc = exc
                continue
            if idx > 0:
                # A fallback executor succeeded
                note = self._resolve_notification()
                if note is not None:
                    if self._notification_callback is not None:
                        self._notification_callback(note)
                    result = ExecutorResult(
                        content=f"{note}\n\n{result.content}",
                        backend=result.backend,
                    )
            return result
        raise last_exc  # type: ignore[misc]
