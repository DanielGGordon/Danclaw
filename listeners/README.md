# Listeners

Channel-specific adapters that translate external messages (terminal, Slack, Twilio, etc.) into `StandardMessage` objects and forward them to the dispatcher.

## Public Interface

Each listener is a standalone process (or Docker container) that:
- Connects to the dispatcher via Unix domain socket (local) or HTTP (webhook-based)
- Translates inbound channel messages to `StandardMessage`
- Delivers dispatcher responses back to the originating channel

## Relationship to Other Modules

- **Depends on**: `dispatcher` (sends StandardMessages to it)
- **Uses**: `config` (for listener-specific settings)
- **Independent of**: `tools`, `personas`

## Implemented Listeners

- **slack/**: Slack Socket Mode listener using `slack-bolt`. See `listeners/slack/README.md`.
