"""Unix domain socket server for the dispatcher.

Runs an asyncio Unix domain socket server that accepts newline-delimited
JSON messages in :class:`StandardMessage` format, passes them to the
:class:`Dispatcher`, and writes back the response as JSON.

Protocol
--------
Each request is a single line of JSON (newline-terminated) representing a
:class:`StandardMessage`.  The server responds with a single line of JSON
containing either a successful result or an error::

    # Success response
    {"ok": true, "session_id": "...", "response": "...", "backend": "..."}

    # Error response
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
        """
        peer = writer.get_extra_info("peername") or "unknown"
        logger.debug("Client connected: %s", peer)

        try:
            while True:
                try:
                    line = await reader.readline()
                except ValueError:
                    error_resp = json.dumps({
                        "ok": False,
                        "error": "Request line too long",
                    })
                    writer.write(error_resp.encode("utf-8") + b"\n")
                    await writer.drain()
                    break
                if not line:
                    # Client disconnected
                    break

                response = await self._process_line(line)
                writer.write(response.encode("utf-8") + b"\n")
                await writer.drain()
        except ConnectionError:
            logger.debug("Client disconnected abruptly: %s", peer)
        finally:
            writer.close()
            await writer.wait_closed()
            logger.debug("Client connection closed: %s", peer)

    async def _process_line(self, line: bytes) -> str:
        """Parse a JSON line, dispatch it, and return the JSON response."""
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return json.dumps({"ok": False, "error": f"Invalid JSON: {exc}"})

        try:
            message = StandardMessage.from_dict(data)
        except (TypeError, ValueError) as exc:
            return json.dumps({"ok": False, "error": f"Invalid message: {exc}"})

        try:
            result = await self._dispatcher.dispatch(message)
        except Exception as exc:
            logger.exception("Dispatch failed")
            return json.dumps({"ok": False, "error": f"Dispatch error: {exc}"})

        return json.dumps({
            "ok": True,
            "session_id": result.session_id,
            "response": result.response,
            "backend": result.backend,
        })
