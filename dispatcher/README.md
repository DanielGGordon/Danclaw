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
- `SessionManager(repo)` — high-level session lifecycle manager wrapping the repository. Methods:
  - `get_or_create_session(message, agent_name)` — finds a live session by explicit ID or channel binding, or creates a new one
  - `get_session(session_id)` — retrieves a session by ID
  - `update_state(session_id, new_state)` — transitions session state with validation of allowed transitions
  - `list_active_sessions()` — returns all ACTIVE and WAITING_FOR_HUMAN sessions
- `Executor` — protocol (typing.Protocol) defining the async `execute(message) -> ExecutorResult` interface that all executor implementations must satisfy.
- `ExecutorResult` — frozen dataclass with `content` (response text) and `backend` (name of the backend that produced it).
- `MockExecutor(fixed_response=None)` — executor that returns canned responses. Echoes input by default (`"mock response: <content>"`); returns a fixed string when `fixed_response` is provided.
- Accepts `StandardMessage` via Unix domain socket or HTTP
- Returns response messages to the calling listener

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

Dispatcher starts, loads config, logs readiness, and shuts down cleanly on signal. SQLite schema initialisation (`init_db`), repository abstraction layer (`Repository`), session lifecycle manager (`SessionManager`), and mocked executor (`MockExecutor`) are available. No routing logic yet; the real AI executor backends (claude, codex) are planned for Phase 6.
