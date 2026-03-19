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
- `Dispatcher(session_manager, repo, executor, config)` — core routing class that accepts a `StandardMessage` and runs the full pipeline. Uses the loaded `DanClawConfig` to resolve which agent handles the message (currently selects the default/first agent; per-channel routing is planned for later):
  - `dispatch(message) -> DispatchResult` — resolves the agent from config, finds or creates a session, stores the inbound message, calls the executor, stores the response, and returns a `DispatchResult`.
  - On executor failure, sets session state to `ERROR` and re-raises the exception.
- `DispatchResult` — frozen dataclass with `session_id`, `response` (text), `backend` (name of the backend that produced it), and `agent_name` (name of the agent that handled the message).
- `Executor` — protocol (typing.Protocol) defining the async `execute(message) -> ExecutorResult` interface that all executor implementations must satisfy.
- `ExecutorResult` — frozen dataclass with `content` (response text) and `backend` (name of the backend that produced it).
- `MockExecutor(fixed_response=None)` — executor that returns canned responses. Echoes input by default (`"mock response: <content>"`); returns a fixed string when `fixed_response` is provided.
- `SocketServer(dispatcher, socket_path)` — asyncio Unix domain socket server that fronts the Dispatcher. Accepts newline-delimited JSON and writes back JSON responses. Supports two request types:
  - **StandardMessage dispatch** — JSON with `source`, `channel_ref`, `user_id`, `content`. Response: `{"ok": true, "session_id": "...", "response": "...", "backend": "...", "agent_name": "..."}`
  - **list_sessions** — `{"type": "list_sessions"}`. Response: `{"ok": true, "sessions": [{"id": "...", "agent_name": "...", "state": "...", "created_at": "..."}]}`
  - **get_history** — `{"type": "get_history", "session_id": "..."}`. Response: `{"ok": true, "session_id": "...", "messages": [{"role": "...", "content": "...", "source": "...", "user_id": "...", "created_at": "..."}]}`
  - Error response (any type): `{"ok": false, "error": "..."}`
  - Methods:
    - `start()` — begin listening on the Unix domain socket (removes stale socket file first)
    - `stop()` — stop accepting connections and remove the socket file
    - `socket_path` — property returning the configured socket path
    - `is_serving` — property returning whether the server is currently active
- Returns response messages to the calling listener

## Relationship to Other Modules

- **Receives from**: `listeners` (StandardMessage input)
- **Uses**: `config` (agent definitions, permissions), `personas` (system prompts), `tools` (per-agent tool access)
- **Stores to**: SQLite database (sessions, messages, telemetry)

## Entry Point

Run with `python -m dispatcher [config_path]`. The `__main__.py` module:

1. Sets up Python logging (INFO level, stderr)
2. Loads the JSON config via `config.load_config()`
3. Initialises the SQLite database schema via `init_db()`
4. Wires up Repository, SessionManager, MockExecutor, and Dispatcher
5. Starts the SocketServer on a Unix domain socket
6. Logs readiness with agent count
7. Installs signal handlers for SIGTERM and SIGINT
8. Waits for a shutdown signal, then stops the SocketServer and exits cleanly

Environment variables:
- `DANCLAW_SOCKET` — Unix domain socket path (default: `/tmp/danclaw.sock`)
- `DANCLAW_DB` — SQLite database path (default: `<project_root>/danclaw.db`)

## Status

Dispatcher starts as a standalone process, loads config, initialises the database, starts the SocketServer on a Unix domain socket, and shuts down cleanly on signal. The CLI (`cli/agent.py`) connects to the dispatcher as a separate process over the Unix socket. SQLite schema initialisation (`init_db`), repository abstraction layer (`Repository`), session lifecycle manager (`SessionManager`), mocked executor (`MockExecutor`), the core `Dispatcher` routing class, and the `SocketServer` Unix domain socket interface are available. The full pipeline (message in -> session -> executor -> store -> response) works end-to-end with the mock executor, accessible via Unix domain socket. The real AI executor backends (claude, codex) are planned for Phase 6.
