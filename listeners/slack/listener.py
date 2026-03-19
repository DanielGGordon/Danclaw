"""SlackListener — translates Slack messages to StandardMessage and forwards
them to the dispatcher via Unix domain socket.

Uses ``slack-bolt`` in Socket Mode so no public webhook endpoint is needed.
Requires two environment variables:

- ``SLACK_BOT_TOKEN`` — the Bot User OAuth Token (``xoxb-...``)
- ``SLACK_APP_TOKEN`` — the App-Level Token (``xapp-...``) with
  ``connections:write`` scope

Slack thread semantics are mapped to ``channel_ref`` as
``<channel_id>:<thread_ts>`` (or ``<channel_id>:<message_ts>`` for
top-level messages).  This allows the dispatcher's session manager to
group threaded replies into a single session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket as sock_mod
from pathlib import Path
from typing import Optional

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from dispatcher.models import StandardMessage

logger = logging.getLogger(__name__)


class SlackListener:
    """Slack Socket Mode listener that forwards messages to the dispatcher.

    Parameters
    ----------
    bot_token:
        Slack Bot User OAuth Token.  Falls back to ``SLACK_BOT_TOKEN`` env var.
    app_token:
        Slack App-Level Token.  Falls back to ``SLACK_APP_TOKEN`` env var.
    socket_path:
        Path to the dispatcher's Unix domain socket.
    """

    def __init__(
        self,
        socket_path: str | Path,
        bot_token: Optional[str] = None,
        app_token: Optional[str] = None,
    ) -> None:
        self._bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        self._app_token = app_token or os.environ.get("SLACK_APP_TOKEN", "")
        self._socket_path = Path(socket_path)

        if not self._bot_token:
            raise ValueError(
                "SLACK_BOT_TOKEN must be set via argument or environment variable"
            )
        if not self._app_token:
            raise ValueError(
                "SLACK_APP_TOKEN must be set via argument or environment variable"
            )

        self._app = App(token=self._bot_token)
        self._handler: Optional[SocketModeHandler] = None

        # Register the message listener
        self._app.event("message")(self._handle_message)

    @property
    def app(self) -> App:
        """Return the underlying slack-bolt App instance."""
        return self._app

    def _build_channel_ref(self, channel: str, thread_ts: Optional[str], ts: str) -> str:
        """Build a channel_ref from Slack event fields.

        Uses ``thread_ts`` if the message is part of a thread, otherwise
        falls back to the message's own ``ts`` as the thread root.
        """
        ref_ts = thread_ts if thread_ts else ts
        return f"{channel}:{ref_ts}"

    def message_to_standard(
        self,
        event: dict,
    ) -> Optional[StandardMessage]:
        """Convert a Slack message event dict to a StandardMessage.

        Returns None for events that should be ignored (bot messages,
        message subtypes like edits/deletes, etc.).
        """
        # Ignore bot messages to prevent loops
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            logger.debug("Ignoring bot message: %s", event.get("bot_id"))
            return None

        # Ignore message subtypes (edits, deletes, joins, etc.)
        subtype = event.get("subtype")
        if subtype is not None:
            logger.debug("Ignoring message subtype: %s", subtype)
            return None

        channel = event.get("channel", "")
        user = event.get("user", "")
        text = event.get("text", "")
        ts = event.get("ts", "")
        thread_ts = event.get("thread_ts")

        if not channel or not user or not text:
            logger.debug("Ignoring incomplete message event: %s", event)
            return None

        channel_ref = self._build_channel_ref(channel, thread_ts, ts)

        return StandardMessage(
            source="slack",
            channel_ref=channel_ref,
            user_id=user,
            content=text,
        )

    def _handle_message(self, event: dict, say) -> None:
        """Handle a Slack message event.

        Converts to StandardMessage and sends to dispatcher via Unix socket.
        """
        msg = self.message_to_standard(event)
        if msg is None:
            return

        try:
            self._send_to_dispatcher(msg)
        except Exception:
            logger.exception("Failed to send message to dispatcher")

    def _send_to_dispatcher(self, message: StandardMessage) -> None:
        """Send a StandardMessage to the dispatcher over the Unix socket.

        Uses a synchronous socket connection since slack-bolt's event
        handlers run in threads.
        """
        payload = json.dumps(message.to_dict()) + "\n"

        with sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM) as s:
            s.connect(str(self._socket_path))
            s.sendall(payload.encode("utf-8"))
            # Read response (newline-delimited JSON)
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            if data:
                response = json.loads(data.decode("utf-8").strip())
                logger.debug("Dispatcher response: %s", response)

    def start(self) -> None:
        """Start the Slack listener in Socket Mode (blocking)."""
        logger.info("Starting Slack listener (Socket Mode)")
        self._handler = SocketModeHandler(self._app, self._app_token)
        self._handler.start()

    def stop(self) -> None:
        """Stop the Slack listener."""
        if self._handler is not None:
            logger.info("Stopping Slack listener")
            self._handler.close()
            self._handler = None
