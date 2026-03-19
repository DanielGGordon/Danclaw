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
        """SlackListener registers message and app_mention event handlers."""
        with patch("listeners.slack.listener.App") as MockApp:
            from listeners.slack.listener import SlackListener

            mock_app = MockApp.return_value
            SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test-token",
                app_token="xapp-test-token",
            )
            event_calls = [c[0][0] for c in mock_app.event.call_args_list]
            assert "message" in event_calls
            assert "app_mention" in event_calls


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


# ── Mention stripping ─────────────────────────────────────────────────


class TestStripMention:
    """Tests for SlackListener.strip_mention."""

    def test_strip_specific_bot_mention(self):
        """Strips a specific bot's <@BOT_ID> mention from text."""
        from listeners.slack.listener import SlackListener

        result = SlackListener.strip_mention("<@U123BOT> hello world", "U123BOT")
        assert result == "hello world"

    def test_strip_generic_mention_without_bot_id(self):
        """Strips any leading <@...> mention when bot_user_id is None."""
        from listeners.slack.listener import SlackListener

        result = SlackListener.strip_mention("<@U999XYZ> do something", None)
        assert result == "do something"

    def test_no_mention_returns_unchanged(self):
        """Text without a mention is returned unchanged."""
        from listeners.slack.listener import SlackListener

        result = SlackListener.strip_mention("just plain text", "U123BOT")
        assert result == "just plain text"

    def test_strip_mention_with_leading_whitespace(self):
        """Leading whitespace before the mention is handled."""
        from listeners.slack.listener import SlackListener

        result = SlackListener.strip_mention("  <@U123BOT>  hello", "U123BOT")
        assert result == "hello"

    def test_strip_does_not_remove_wrong_bot_id(self):
        """Only the specified bot_user_id mention is stripped."""
        from listeners.slack.listener import SlackListener

        result = SlackListener.strip_mention("<@UOTHER> hello", "U123BOT")
        assert result == "<@UOTHER> hello"

    def test_strip_only_leading_mention(self):
        """Only the leading mention is stripped, not inline mentions."""
        from listeners.slack.listener import SlackListener

        result = SlackListener.strip_mention(
            "<@U123BOT> hey <@U456OTHER> check this", "U123BOT"
        )
        assert result == "hey <@U456OTHER> check this"

    def test_mention_only_returns_empty(self):
        """A message that is just a mention results in empty string."""
        from listeners.slack.listener import SlackListener

        result = SlackListener.strip_mention("<@U123BOT>", "U123BOT")
        assert result == ""


# ── App mention handling ──────────────────────────────────────────────


class TestAppMentionHandling:
    """Tests for SlackListener._handle_app_mention."""

    @pytest.fixture
    def listener(self):
        with patch("listeners.slack.listener.App"):
            from listeners.slack.listener import SlackListener

            listener = SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )
            listener._bot_user_id = "U123BOT"
            return listener

    def test_app_mention_strips_mention_and_dispatches(self, listener):
        """app_mention events have the <@BOT_ID> stripped from content."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "<@U123BOT> do something",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(listener, "_send_to_dispatcher") as mock_send:
            listener._handle_app_mention(event, say)
            mock_send.assert_called_once()
            sent_msg = mock_send.call_args[0][0]
            assert sent_msg.content == "do something"

    def test_app_mention_builds_correct_channel_ref(self, listener):
        """app_mention events produce the correct channel_ref."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "<@U123BOT> hello",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(listener, "_send_to_dispatcher") as mock_send:
            listener._handle_app_mention(event, say)
            sent_msg = mock_send.call_args[0][0]
            assert sent_msg.channel_ref == "C123:1234567890.123456"
            assert sent_msg.source == "slack"

    def test_app_mention_threaded_uses_thread_ts(self, listener):
        """Threaded app_mention events use thread_ts in channel_ref."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "<@U123BOT> reply",
            "ts": "1234567891.000000",
            "thread_ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(listener, "_send_to_dispatcher") as mock_send:
            listener._handle_app_mention(event, say)
            sent_msg = mock_send.call_args[0][0]
            assert sent_msg.channel_ref == "C123:1234567890.123456"

    def test_app_mention_only_mention_returns_none(self, listener):
        """An app_mention with only the mention and no text is ignored."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "<@U123BOT>",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(listener, "_send_to_dispatcher") as mock_send:
            listener._handle_app_mention(event, say)
            mock_send.assert_not_called()

    def test_app_mention_logs_send_failure(self, listener):
        """_handle_app_mention catches and logs dispatcher send errors."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "<@U123BOT> hello",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(
            listener, "_send_to_dispatcher", side_effect=ConnectionRefusedError
        ):
            # Should not raise
            listener._handle_app_mention(event, say)


# ── DM handling ───────────────────────────────────────────────────────


class TestDMHandling:
    """Tests for DM (direct message) handling via the message event."""

    @pytest.fixture
    def listener(self):
        with patch("listeners.slack.listener.App"):
            from listeners.slack.listener import SlackListener

            return SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )

    def test_dm_message_is_processed(self, listener):
        """DM messages (channel_type=im) are forwarded to the dispatcher."""
        event = {
            "channel": "D123DM",
            "channel_type": "im",
            "user": "U456",
            "text": "hello from DM",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(listener, "_send_to_dispatcher") as mock_send:
            listener._handle_message(event, say)
            mock_send.assert_called_once()
            sent_msg = mock_send.call_args[0][0]
            assert sent_msg.content == "hello from DM"
            assert sent_msg.channel_ref == "D123DM:1234567890.123456"

    def test_dm_does_not_strip_mention(self, listener):
        """DM messages do not have mentions stripped (no should_strip_mention)."""
        event = {
            "channel": "D123DM",
            "channel_type": "im",
            "user": "U456",
            "text": "<@U123BOT> hey bot",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(listener, "_send_to_dispatcher") as mock_send:
            listener._handle_message(event, say)
            sent_msg = mock_send.call_args[0][0]
            # DM messages go through _handle_message which does NOT strip
            assert sent_msg.content == "<@U123BOT> hey bot"


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


# ── Threaded reply behaviour ──────────────────────────────────────────


class TestThreadedReply:
    """Tests for in-thread reply behaviour (_reply_in_thread and helpers)."""

    @pytest.fixture
    def listener(self):
        with patch("listeners.slack.listener.App"):
            from listeners.slack.listener import SlackListener

            return SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )

    def test_thread_ts_for_reply_uses_thread_ts_when_present(self, listener):
        """_thread_ts_for_reply returns thread_ts for threaded messages."""
        event = {"ts": "1111.000", "thread_ts": "1000.000"}
        assert listener._thread_ts_for_reply(event) == "1000.000"

    def test_thread_ts_for_reply_uses_ts_for_top_level(self, listener):
        """_thread_ts_for_reply returns ts for top-level (non-threaded) messages."""
        event = {"ts": "1111.000"}
        assert listener._thread_ts_for_reply(event) == "1111.000"

    def test_reply_in_thread_calls_say_with_thread_ts(self, listener):
        """_reply_in_thread calls say() with text and thread_ts."""
        say = MagicMock()
        event = {"ts": "1111.000"}
        response = {"content": "Hello!"}

        listener._reply_in_thread(response, event, say)

        say.assert_called_once_with(text="Hello!", thread_ts="1111.000")

    def test_reply_in_thread_none_response_is_noop(self, listener):
        """_reply_in_thread does nothing when response is None."""
        say = MagicMock()
        listener._reply_in_thread(None, {"ts": "1111.000"}, say)
        say.assert_not_called()

    def test_reply_in_thread_empty_content_is_noop(self, listener):
        """_reply_in_thread does nothing when response has no content."""
        say = MagicMock()
        listener._reply_in_thread({"status": "ok"}, {"ts": "1111.000"}, say)
        say.assert_not_called()

    def test_reply_in_thread_empty_string_content_is_noop(self, listener):
        """_reply_in_thread does nothing when content is empty string."""
        say = MagicMock()
        listener._reply_in_thread({"content": ""}, {"ts": "1111.000"}, say)
        say.assert_not_called()

    def test_handle_message_replies_in_thread_for_top_level(self, listener):
        """_handle_message replies in a new thread for top-level messages."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "hello",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(
            listener,
            "_send_to_dispatcher",
            return_value={"content": "Hi there!"},
        ):
            listener._handle_message(event, say)

        say.assert_called_once_with(
            text="Hi there!", thread_ts="1234567890.123456"
        )

    def test_handle_message_replies_in_existing_thread(self, listener):
        """_handle_message replies in the existing thread when thread_ts is set."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "follow-up",
            "ts": "1234567891.000000",
            "thread_ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(
            listener,
            "_send_to_dispatcher",
            return_value={"content": "Got it!"},
        ):
            listener._handle_message(event, say)

        say.assert_called_once_with(
            text="Got it!", thread_ts="1234567890.123456"
        )

    def test_handle_message_no_reply_when_dispatcher_returns_none(self, listener):
        """_handle_message does not call say when dispatcher returns None."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "hello",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(listener, "_send_to_dispatcher", return_value=None):
            listener._handle_message(event, say)

        say.assert_not_called()

    def test_handle_message_no_reply_on_dispatcher_error(self, listener):
        """_handle_message does not call say when dispatcher raises."""
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "hello",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(
            listener,
            "_send_to_dispatcher",
            side_effect=ConnectionRefusedError,
        ):
            listener._handle_message(event, say)

        say.assert_not_called()

    def test_handle_app_mention_replies_in_thread(self, listener):
        """_handle_app_mention replies in-thread for a top-level mention."""
        listener._bot_user_id = "U123BOT"
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "<@U123BOT> do something",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(
            listener,
            "_send_to_dispatcher",
            return_value={"content": "Done!"},
        ):
            listener._handle_app_mention(event, say)

        say.assert_called_once_with(
            text="Done!", thread_ts="1234567890.123456"
        )

    def test_handle_app_mention_replies_in_existing_thread(self, listener):
        """_handle_app_mention replies in existing thread for threaded mention."""
        listener._bot_user_id = "U123BOT"
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "<@U123BOT> follow up",
            "ts": "1234567891.000000",
            "thread_ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(
            listener,
            "_send_to_dispatcher",
            return_value={"content": "Here you go!"},
        ):
            listener._handle_app_mention(event, say)

        say.assert_called_once_with(
            text="Here you go!", thread_ts="1234567890.123456"
        )

    def test_handle_app_mention_no_reply_on_error(self, listener):
        """_handle_app_mention does not reply when dispatcher raises."""
        listener._bot_user_id = "U123BOT"
        event = {
            "channel": "C123",
            "user": "U456",
            "text": "<@U123BOT> hello",
            "ts": "1234567890.123456",
        }
        say = MagicMock()

        with patch.object(
            listener,
            "_send_to_dispatcher",
            side_effect=ConnectionRefusedError,
        ):
            listener._handle_app_mention(event, say)

        say.assert_not_called()


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
            # Mock auth_test to return a bot user ID
            MockApp.return_value.client.auth_test.return_value = {
                "user_id": "U123BOT"
            }
            listener.start()

            MockHandler.assert_called_once_with(MockApp.return_value, "xapp-test")
            mock_handler_instance.start.assert_called_once()

    def test_start_resolves_bot_user_id(self):
        """start() resolves the bot user ID via auth.test."""
        with patch("listeners.slack.listener.App") as MockApp, \
             patch("listeners.slack.listener.SocketModeHandler"):
            from listeners.slack.listener import SlackListener

            listener = SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )

            MockApp.return_value.client.auth_test.return_value = {
                "user_id": "U999BOT"
            }
            listener.start()

            assert listener._bot_user_id == "U999BOT"

    def test_start_continues_if_auth_test_fails(self):
        """start() still works if auth.test fails (generic pattern fallback)."""
        with patch("listeners.slack.listener.App") as MockApp, \
             patch("listeners.slack.listener.SocketModeHandler") as MockHandler:
            from listeners.slack.listener import SlackListener

            listener = SlackListener(
                socket_path="/tmp/test.sock",
                bot_token="xoxb-test",
                app_token="xapp-test",
            )

            MockApp.return_value.client.auth_test.side_effect = Exception("API error")
            listener.start()

            assert listener._bot_user_id is None
            MockHandler.return_value.start.assert_called_once()

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
