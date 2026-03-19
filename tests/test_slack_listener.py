"""Tests for listeners.slack — Slack Socket Mode listener."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from dispatcher.models import StandardMessage


# ── SlackListener construction ────────────────────────────────────────


class TestSlackListenerInit:
    """Tests for SlackListener.__init__ and token validation."""

    def test_init_with_explicit_tokens(self):
        """SlackListener accepts explicit bot and app tokens."""
        with patch("listeners.slack.listener.App"):
            from listeners.slack.listener import SlackListener

            listener = SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test-token",
                app_token="xapp-test-token",
            )
            assert listener._bot_token == "xoxb-test-token"
            assert listener._app_token == "xapp-test-token"

    def test_init_from_env_vars(self, monkeypatch):
        """SlackListener falls back to environment variables."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env-token")
        monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-env-token")

        with patch("listeners.slack.listener.App"):
            from listeners.slack.listener import SlackListener

            listener = SlackListener(socket_path="/tmp/test.sock")
            assert listener._bot_token == "xoxb-env-token"
            assert listener._app_token == "xapp-env-token"

    def test_init_missing_bot_token_raises(self, monkeypatch):
        """SlackListener raises ValueError when bot token is missing."""
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

        with patch("listeners.slack.listener.App"):
            from listeners.slack.listener import SlackListener

            with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
                SlackListener(socket_path="/tmp/test.sock")

    def test_init_missing_app_token_raises(self, monkeypatch):
        """SlackListener raises ValueError when app token is missing."""
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

        with patch("listeners.slack.listener.App"):
            from listeners.slack.listener import SlackListener

            with pytest.raises(ValueError, match="SLACK_APP_TOKEN"):
                SlackListener(
                    socket_path="/tmp/test.sock",
                    bot_token="xoxb-test-token",
                )

    def test_init_creates_bolt_app_with_token(self):
        """SlackListener creates a slack-bolt App with the bot token."""
        with patch("listeners.slack.listener.App") as MockApp:
            from listeners.slack.listener import SlackListener

            SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test-token",
                app_token="xapp-test-token",
            )
            MockApp.assert_called_once_with(token="xoxb-test-token")

    def test_init_registers_message_event_handler(self):
        """SlackListener registers a message event handler on the App."""
        with patch("listeners.slack.listener.App") as MockApp:
            from listeners.slack.listener import SlackListener

            mock_app = MockApp.return_value
            SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test-token",
                app_token="xapp-test-token",
            )
            mock_app.event.assert_called_once_with("message")


# ── Channel ref building ─────────────────────────────────────────────


class TestBuildChannelRef:
    """Tests for SlackListener._build_channel_ref."""

    @pytest.fixture
    def listener(self):
        with patch("listeners.slack.listener.App"):
            from listeners.slack.listener import SlackListener

            return SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )

    def test_channel_ref_with_thread_ts(self, listener):
        """Thread messages use thread_ts in channel_ref."""
        ref = listener._build_channel_ref("C123", "1234567890.123456", "1234567891.000000")
        assert ref == "C123:1234567890.123456"

    def test_channel_ref_without_thread_ts(self, listener):
        """Top-level messages use message ts in channel_ref."""
        ref = listener._build_channel_ref("C123", None, "1234567890.123456")
        assert ref == "C123:1234567890.123456"


# ── Message conversion ───────────────────────────────────────────────


class TestMessageToStandard:
    """Tests for SlackListener.message_to_standard."""

    @pytest.fixture
    def listener(self):
        with patch("listeners.slack.listener.App"):
            from listeners.slack.listener import SlackListener

            return SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )

    def test_basic_message_conversion(self, listener):
        """A normal Slack message converts to a StandardMessage."""
        event = {
            "channel": "C123ABC",
            "user": "U456DEF",
            "text": "hello world",
            "ts": "1234567890.123456",
        }
        msg = listener.message_to_standard(event)
        assert msg is not None
        assert msg.source == "slack"
        assert msg.channel_ref == "C123ABC:1234567890.123456"
        assert msg.user_id == "U456DEF"
        assert msg.content == "hello world"
        assert msg.session_id is None

    def test_threaded_message_uses_thread_ts(self, listener):
        """A threaded message uses thread_ts in channel_ref."""
        event = {
            "channel": "C123ABC",
            "user": "U456DEF",
            "text": "reply in thread",
            "ts": "1234567891.000000",
            "thread_ts": "1234567890.123456",
        }
        msg = listener.message_to_standard(event)
        assert msg is not None
        assert msg.channel_ref == "C123ABC:1234567890.123456"

    def test_bot_message_returns_none(self, listener):
        """Bot messages are ignored (returns None)."""
        event = {
            "channel": "C123ABC",
            "user": "U456DEF",
            "text": "I am a bot",
            "ts": "1234567890.123456",
            "bot_id": "B789GHI",
        }
        msg = listener.message_to_standard(event)
        assert msg is None

    def test_bot_subtype_returns_none(self, listener):
        """Messages with subtype 'bot_message' are ignored."""
        event = {
            "channel": "C123ABC",
            "text": "bot says hi",
            "ts": "1234567890.123456",
            "subtype": "bot_message",
        }
        msg = listener.message_to_standard(event)
        assert msg is None

    def test_message_changed_subtype_returns_none(self, listener):
        """Message edits (subtype 'message_changed') are ignored."""
        event = {
            "channel": "C123ABC",
            "user": "U456DEF",
            "text": "edited message",
            "ts": "1234567890.123456",
            "subtype": "message_changed",
        }
        msg = listener.message_to_standard(event)
        assert msg is None

    def test_message_deleted_subtype_returns_none(self, listener):
        """Message deletions are ignored."""
        event = {
            "channel": "C123ABC",
            "user": "U456DEF",
            "text": "deleted message",
            "ts": "1234567890.123456",
            "subtype": "message_deleted",
        }
        msg = listener.message_to_standard(event)
        assert msg is None

    def test_missing_channel_returns_none(self, listener):
        """Messages without a channel are ignored."""
        event = {
            "user": "U456DEF",
            "text": "no channel",
            "ts": "1234567890.123456",
        }
        msg = listener.message_to_standard(event)
        assert msg is None

    def test_missing_user_returns_none(self, listener):
        """Messages without a user are ignored."""
        event = {
            "channel": "C123ABC",
            "text": "no user",
            "ts": "1234567890.123456",
        }
        msg = listener.message_to_standard(event)
        assert msg is None

    def test_missing_text_returns_none(self, listener):
        """Messages without text are ignored."""
        event = {
            "channel": "C123ABC",
            "user": "U456DEF",
            "ts": "1234567890.123456",
        }
        msg = listener.message_to_standard(event)
        assert msg is None

    def test_empty_text_returns_none(self, listener):
        """Messages with empty text are ignored."""
        event = {
            "channel": "C123ABC",
            "user": "U456DEF",
            "text": "",
            "ts": "1234567890.123456",
        }
        msg = listener.message_to_standard(event)
        assert msg is None


# ── Message handling and dispatch ─────────────────────────────────────


class TestHandleMessage:
    """Tests for SlackListener._handle_message and _send_to_dispatcher."""

    @pytest.fixture
    def listener(self):
        with patch("listeners.slack.listener.App"):
            from listeners.slack.listener import SlackListener

            return SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )

    def test_handle_message_calls_send_to_dispatcher(self, listener):
        """_handle_message converts and sends valid messages."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "hello",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(listener, "_send_to_dispatcher") as mock_send:
            listener._handle_message(event, say)
            mock_send.assert_called_once()
            sent_msg = mock_send.call_args[0][0]
            assert isinstance(sent_msg, StandardMessage)
            assert sent_msg.content == "hello"

    def test_handle_message_ignores_bot_messages(self, listener):
        """_handle_message skips bot messages without sending."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "bot message",
            "ts": "1234567890.123456",
            "bot_id": "B789",
        }
        say = MagicMock()

        with patch.object(listener, "_send_to_dispatcher") as mock_send:
            listener._handle_message(event, say)
            mock_send.assert_not_called()

    def test_handle_message_logs_send_failure(self, listener):
        """_handle_message catches and logs dispatcher send errors."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "hello",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(
            listener, "_send_to_dispatcher", side_effect=ConnectionRefusedError
        ):
            # Should not raise — error is caught and logged
            listener._handle_message(event, say)

    def test_send_to_dispatcher_uses_unix_socket(self, listener):
        """_send_to_dispatcher connects to Unix socket and sends JSON."""
        msg = StandardMessage(
            source="slack",
            channel_ref="C123:1234567890.123456",
            user_id="U456",
            content="hello",
        )
        expected_payload = json.dumps(msg.to_dict()) + "\n"

        mock_socket = MagicMock()
        mock_socket.recv.return_value = b'{"ok": true}\n'

        with patch("listeners.slack.listener.sock_mod") as mock_sock_mod:
            mock_sock_mod.AF_UNIX = 1
            mock_sock_mod.SOCK_STREAM = 1
            mock_sock_mod.socket.return_value.__enter__ = MagicMock(
                return_value=mock_socket
            )
            mock_sock_mod.socket.return_value.__exit__ = MagicMock(return_value=False)
            listener._send_to_dispatcher(msg)

            mock_socket.connect.assert_called_once_with("/tmp/test.sock")
            mock_socket.sendall.assert_called_once_with(
                expected_payload.encode("utf-8")
            )


# ── Connection setup (start/stop) ────────────────────────────────────


class TestConnectionSetup:
    """Tests for SlackListener.start and .stop."""

    def test_start_creates_socket_mode_handler(self):
        """start() creates a SocketModeHandler and calls start()."""
        with patch("listeners.slack.listener.App") as MockApp, \
             patch("listeners.slack.listener.SocketModeHandler") as MockHandler:
            from listeners.slack.listener import SlackListener

            listener = SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )

            mock_handler_instance = MockHandler.return_value
            listener.start()

            MockHandler.assert_called_once_with(MockApp.return_value, "xapp-test")
            mock_handler_instance.start.assert_called_once()

    def test_stop_closes_handler(self):
        """stop() calls close() on the SocketModeHandler."""
        with patch("listeners.slack.listener.App"), \
             patch("listeners.slack.listener.SocketModeHandler") as MockHandler:
            from listeners.slack.listener import SlackListener

            listener = SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )

            mock_handler_instance = MockHandler.return_value
            listener.start()
            listener.stop()

            mock_handler_instance.close.assert_called_once()
            assert listener._handler is None

    def test_stop_without_start_is_noop(self):
        """stop() does nothing if start() was never called."""
        with patch("listeners.slack.listener.App"):
            from listeners.slack.listener import SlackListener

            listener = SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )
            # Should not raise
            listener.stop()

    def test_app_property_returns_bolt_app(self):
        """The app property returns the underlying slack-bolt App."""
        with patch("listeners.slack.listener.App") as MockApp:
            from listeners.slack.listener import SlackListener

            listener = SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )
            assert listener.app is MockApp.return_value


# ── __main__ entry point ──────────────────────────────────────────────


class TestMainEntryPoint:
    """Tests for listeners.slack.__main__."""

    def test_main_creates_listener_and_starts(self, monkeypatch):
        """main() creates a SlackListener with parsed args and starts it."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")

        with patch("listeners.slack.__main__.SlackListener") as MockListener, \
             patch("sys.argv", ["prog", "--socket-path", "/tmp/custom.sock"]):
            from listeners.slack.__main__ import main

            mock_instance = MockListener.return_value
            main()

            MockListener.assert_called_once_with(socket_path="/tmp/custom.sock")
            mock_instance.start.assert_called_once()

    def test_main_uses_default_socket_path(self, monkeypatch):
        """main() uses default socket path when not specified."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")

        with patch("listeners.slack.__main__.SlackListener") as MockListener, \
             patch("sys.argv", ["prog"]):
            from listeners.slack.__main__ import main

            mock_instance = MockListener.return_value
            main()

            MockListener.assert_called_once_with(
                socket_path="/tmp/danclaw-dispatcher.sock"
            )
