"""Unix domain socket server for the dispatcher.

Runs an asyncio Unix domain socket server that accepts newline-delimited
JSON messages, passes them to the :class:`Dispatcher`, and writes back
the response as JSON.

Protocol
--------
Each request is a single line of JSON (newline-terminated).  Three request
types are supported:

1. **StandardMessage dispatch** — a JSON object with ``source``,
   ``channel_ref``, ``user_id``, and ``content`` fields (as defined by
   :class:`StandardMessage`).  Response::

       {"ok": true, "session_id": "...", "response": "...", "backend": "...",
        "fanout_channels": ["..."]}

2. **List sessions** — ``{"type": "list_sessions"}``  Response::

       {"ok": true, "sessions": [{"id": "...", "agent_name": "...",
           "state": "...", "created_at": "..."}]}

3. **Get history** — ``{"type": "get_history", "session_id": "..."}``
   Response::

       {"ok": true, "session_id": "...", "messages": [
           {"role": "...", "content": "...", "source": "...",
            "user_id": "...", "created_at": "..."}]}

4. **Detach** — ``{"type": "detach", "session_id": "...", "channel_ref": "..."}``
   Removes the channel binding.  The session and other bindings remain
   intact.  Response::

       {"ok": true, "removed": true}

5. **Fanout push** (server → client, unsolicited) — pushed to connected
   clients when a dispatch from another channel produces a response for a
   session they are bound to::

       {"type": "fanout", "session_id": "...", "response": "...",
        "source": "...", "agent_name": "..."}

Error response (for any type)::

    {"ok": false, "error": "description of the error"}
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from dispatcher.dispatcher import Dispatcher
from dispatcher.models import StandardMessage

logger = logging.getLogger(__name__)


class SocketServer:
    """Asyncio Unix domain socket server fronting a :class:`Dispatcher`.

    Parameters
    ----------
    dispatcher:
        The dispatcher instance that will process incoming messages.
    socket_path:
        Filesystem path for the Unix domain socket.
    """

    def __init__(self, dispatcher: Dispatcher, socket_path: str | Path) -> None:
        self._dispatcher = dispatcher
        self._socket_path = Path(socket_path)
        self._server: Optional[asyncio.Server] = None
        # Registry of connected clients: channel_ref → StreamWriter.
        # Used for pushing fanout messages to terminal clients that are
        # attached to bridged sessions.
        self._connected_clients: dict[str, asyncio.StreamWriter] = {}

    @property
    def socket_path(self) -> Path:
        """Return the path of the Unix domain socket."""
        return self._socket_path

    @property
    def is_serving(self) -> bool:
        """Return True if the server is currently serving connections."""
        return self._server is not None and self._server.is_serving()

    async def start(self) -> None:
        """Start listening on the Unix domain socket.

        Removes any stale socket file before binding.
        """
        # Remove stale socket file if it exists
        if self._socket_path.exists():
            self._socket_path.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self._socket_path),
        )
        logger.info("Socket server listening on %s", self._socket_path)

    async def stop(self) -> None:
        """Stop the server and clean up the socket file."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("Socket server stopped")

        if self._socket_path.exists():
            self._socket_path.unlink()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection.

        Reads newline-delimited JSON messages, dispatches each one, and
        writes back the response.  Continues until the client disconnects.

        When a client sends a StandardMessage, its ``channel_ref`` is
        registered so the server can push fanout messages from other
        channels to this client.
        """
        peer = writer.get_extra_info("peername") or "unknown"
        logger.debug("Client connected: %s", peer)
        registered_refs: list[str] = []

        try:
            while True:
                line = await reader.readline()
                if not line:
                    # Client disconnected
                    break

                response, channel_ref = await self._process_line(line, writer)
                if channel_ref and channel_ref not in self._connected_clients:
                    self._connected_clients[channel_ref] = writer
                    registered_refs.append(channel_ref)
                    logger.debug(
                        "Registered client channel_ref: %s", channel_ref,
                    )
                writer.write(response.encode("utf-8") + b"\n")
                await writer.drain()
        except ConnectionResetError:
            logger.debug("Client disconnected abruptly: %s", peer)
        finally:
            for ref in registered_refs:
                self._connected_clients.pop(ref, None)
            writer.close()
            await writer.wait_closed()
            logger.debug("Client connection closed: %s", peer)

    async def _process_line(
        self,
        line: bytes,
        writer: Optional[asyncio.StreamWriter] = None,
    ) -> tuple[str, Optional[str]]:
        """Parse a JSON line, dispatch it, and return ``(response, channel_ref)``.

        The second element is the ``channel_ref`` from the message (if it
        was a StandardMessage dispatch), which ``_handle_client`` uses to
        register the writer for fanout.

        Handles control request types and StandardMessage dispatch.
        """
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return json.dumps({"ok": False, "error": f"Invalid JSON: {exc}"}), None

        # ── Control requests (keyed by "type") ───────────────────────
        if isinstance(data, dict) and data.get("type") == "list_sessions":
            return await self._handle_list_sessions(), None

        if isinstance(data, dict) and data.get("type") == "detach":
            session_id = data.get("session_id")
            channel_ref = data.get("channel_ref")
            if not session_id or not isinstance(session_id, str):
                return json.dumps({
                    "ok": False,
                    "error": "detach requires a string 'session_id' field",
                }), None
            if not channel_ref or not isinstance(channel_ref, str):
                return json.dumps({
                    "ok": False,
                    "error": "detach requires a string 'channel_ref' field",
                }), None
            result = await self._handle_detach(session_id, channel_ref)
            # Unregister the client for this channel_ref on detach
            self._connected_clients.pop(channel_ref, None)
            return result, None

        if isinstance(data, dict) and data.get("type") == "get_history":
            session_id = data.get("session_id")
            if not session_id or not isinstance(session_id, str):
                return json.dumps({
                    "ok": False,
                    "error": "get_history requires a string 'session_id' field",
                }), None
            return await self._handle_get_history(session_id), None

        # ── Standard message dispatch ────────────────────────────────
        try:
            message = StandardMessage.from_dict(data)
        except (TypeError, ValueError) as exc:
            return json.dumps({"ok": False, "error": f"Invalid message: {exc}"}), None

        try:
            result = await self._dispatcher.dispatch(message)
        except Exception as exc:
            logger.exception("Dispatch failed")
            return json.dumps({"ok": False, "error": f"Dispatch error: {exc}"}), None

        # Push fanout to connected clients
        await self._push_fanout(result, message.source)

        return json.dumps({
            "ok": True,
            "session_id": result.session_id,
            "response": result.response,
            "backend": result.backend,
            "agent_name": result.agent_name,
            "fanout_channels": list(result.fanout_channels),
        }), message.channel_ref

    async def _push_fanout(self, result, source: str) -> None:
        """Push a fanout message to connected clients in the fanout list.

        For each channel_ref in ``result.fanout_channels``, if there is a
        registered connected client, send it a JSON fanout push message.
        """
        if not result.fanout_channels:
            return

        fanout_msg = json.dumps({
            "type": "fanout",
            "session_id": result.session_id,
            "response": result.response,
            "source": source,
            "agent_name": result.agent_name,
        })
        payload = fanout_msg.encode("utf-8") + b"\n"

        stale_refs: list[str] = []
        for ref in result.fanout_channels:
            writer = self._connected_clients.get(ref)
            if writer is None:
                continue
            try:
                writer.write(payload)
                await writer.drain()
                logger.debug("Pushed fanout to %s", ref)
            except (ConnectionResetError, BrokenPipeError, OSError):
                logger.debug("Failed to push fanout to %s (stale)", ref)
                stale_refs.append(ref)

        for ref in stale_refs:
            self._connected_clients.pop(ref, None)

    async def _handle_detach(
        self, session_id: str, channel_ref: str,
    ) -> str:
        """Remove a channel binding from a session."""
        try:
            mgr = self._dispatcher._session_manager
            removed = await mgr.remove_binding(session_id, channel_ref)
        except KeyError as exc:
            return json.dumps({"ok": False, "error": str(exc)})
        except Exception as exc:
            logger.exception("Failed to detach")
            return json.dumps({"ok": False, "error": f"Detach error: {exc}"})

        return json.dumps({"ok": True, "removed": removed})

    async def _handle_get_history(self, session_id: str) -> str:
        """Return message history for a session as a JSON response."""
        try:
            repo = self._dispatcher._repo
            session = await repo.get_session(session_id)
            if session is None:
                return json.dumps({
                    "ok": False,
                    "error": f"Session not found: {session_id}",
                })
            messages = await repo.get_messages_for_session(session_id)
        except Exception as exc:
            logger.exception("Failed to get history")
            return json.dumps({"ok": False, "error": f"History error: {exc}"})

        return json.dumps({
            "ok": True,
            "session_id": session_id,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "source": m.source,
                    "user_id": m.user_id,
                    "created_at": m.created_at,
                }
                for m in messages
            ],
        })

    async def _handle_list_sessions(self) -> str:
        """Return all sessions as a JSON response."""
        try:
            repo = self._dispatcher._repo
            sessions = await repo.list_sessions()
        except Exception as exc:
            logger.exception("Failed to list sessions")
            return json.dumps({"ok": False, "error": f"List error: {exc}"})

        return json.dumps({
            "ok": True,
            "sessions": [
                {
                    "id": s.id,
                    "agent_name": s.agent_name,
                    "state": s.state,
                    "created_at": s.created_at,
                }
                for s in sessions
            ],
        })
