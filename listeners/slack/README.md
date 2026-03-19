# Slack Listener

Connects to Slack via Socket Mode using `slack-bolt`. Translates incoming Slack messages into `StandardMessage` objects and forwards them to the dispatcher over a Unix domain socket.

## Requirements

- `SLACK_BOT_TOKEN` environment variable (Bot User OAuth Token, `xoxb-...`)
- `SLACK_APP_TOKEN` environment variable (App-Level Token, `xapp-...`, with `connections:write` scope)

## Usage

```bash
# Run as a standalone process
python -m listeners.slack --socket-path /tmp/danclaw-dispatcher.sock

# Or with custom log level
python -m listeners.slack --log-level DEBUG
```

## Public Interface

- `SlackListener(socket_path, bot_token=None, app_token=None)` — main listener class
- `SlackListener.start()` — start listening (blocking)
- `SlackListener.stop()` — stop the listener
- `SlackListener.message_to_standard(event)` — convert a Slack event dict to StandardMessage
- `SlackFanoutPoster(client)` — posts fanout messages to Slack threads on behalf of the bot. Used as the `fanout_poster` callback for the SocketServer to forward terminal user messages and agent responses to Slack channels in bridged sessions. By default (session attribution=`"bot"`), terminal messages appear as the bot with no attribution prefix. When attribution is set to a non-bot value, messages are prefixed with `[via <source>]`. The `post(channel_ref, text)` method parses the `channel_ref` (format `<channel_id>:<thread_ts>`) and calls `chat_postMessage`.

## Channel Reference Mapping

Slack thread semantics are mapped to `channel_ref` as `<channel_id>:<thread_ts>`. For top-level messages (no thread), `channel_ref` uses `<channel_id>:<message_ts>`. This allows the dispatcher to group threaded replies into a single session.

## Threaded Replies

Bot responses are always posted as threaded replies to keep channels clean. When the dispatcher returns a response (via the `response` or `content` field), the listener calls `say(text=content, thread_ts=thread_ts)`. For top-level messages, this creates a new thread anchored at the original message. For messages already in a thread, the reply is posted in the same thread.

## Relationship to Other Modules

- **Depends on**: `dispatcher` (sends StandardMessages via Unix socket)
- **Independent of**: `config`, `tools`, `personas`
