# Dispatcher

The core routing and orchestration process. Accepts `StandardMessage` objects from listeners, manages sessions, resolves permissions, selects the appropriate agent, invokes the AI executor, stores results, and returns responses.

## Public Interface

- `StandardMessage` — frozen dataclass representing the universal internal message format. Fields: `source`, `channel_ref`, `user_id`, `content`, `session_id` (optional). Includes `to_dict()` and `from_dict()` serialization helpers for JSON transport.
- `init_db(db_path)` — async function that creates the SQLite schema (sessions, messages, channel_bindings tables) using `CREATE TABLE IF NOT EXISTS`. Safe to call on every startup.
- `Repository(db)` — async repository abstraction layer for all database access. Takes an `aiosqlite.Connection`. Methods:
  - Sessions: `create_session`, `get_session`, `update_session_state`, `list_sessions`
  - Messages: `save_message`, `get_messages_for_session`
  - Channel bindings: `add_channel_binding`, `get_bindings_for_session`, `find_session_by_channel`
- Row dataclasses: `SessionRow`, `MessageRow`, `ChannelBindingRow` — frozen dataclasses for type-safe query results.
- Accepts `StandardMessage` via Unix domain socket or HTTP
- Returns response messages to the calling listener
- Manages session lifecycle (create, resume, close)

## Relationship to Other Modules

- **Receives from**: `listeners` (StandardMessage input)
- **Uses**: `config` (agent definitions, permissions), `personas` (system prompts), `tools` (per-agent tool access)
- **Stores to**: SQLite database (sessions, messages, telemetry)

## Entry Point

Run with `python -m dispatcher`. The `__main__.py` module:

1. Sets up Python logging (INFO level, stderr)
2. Loads the JSON config via `config.load_config()`
3. Logs readiness with agent count
4. Installs signal handlers for SIGTERM and SIGINT
5. Runs an asyncio event loop that waits for a shutdown signal
6. Logs clean shutdown on exit

## Status

Dispatcher starts, loads config, logs readiness, and shuts down cleanly on signal. SQLite schema initialisation (`init_db`) and repository abstraction layer (`Repository`) are available. No routing or session logic yet.
