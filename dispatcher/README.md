# Dispatcher

The core routing and orchestration process. Accepts `StandardMessage` objects from listeners, manages sessions, resolves permissions, selects the appropriate agent, invokes the AI executor, stores results, and returns responses.

## Public Interface

- `StandardMessage` — frozen dataclass representing the universal internal message format. Fields: `source`, `channel_ref`, `user_id`, `content`, `session_id` (optional). Includes `to_dict()` and `from_dict()` serialization helpers for JSON transport.
- `init_db(db_path)` — async function that creates the SQLite schema (sessions, messages, channel_bindings tables) using `CREATE TABLE IF NOT EXISTS`. Safe to call on every startup.
- `Repository(db)` — async repository abstraction layer for all database access. Takes an `aiosqlite.Connection`. Methods:
  - Sessions: `create_session`, `get_session`, `update_session_state`, `update_session_agent`, `list_sessions`
  - Messages: `save_message`, `get_messages_for_session`
  - Channel bindings: `add_channel_binding`, `get_bindings_for_session`, `find_session_by_channel`
- Row dataclasses: `SessionRow`, `MessageRow`, `ChannelBindingRow` — frozen dataclasses for type-safe query results.
- `SessionManager(repo)` — high-level session lifecycle manager wrapping the repository. Methods:
  - `get_or_create_session(message, agent_name)` — finds a live session by explicit ID or channel binding, or creates a new one
  - `get_session(session_id)` — retrieves a session by ID
  - `update_agent(session_id, agent_name)` — changes the agent assigned to a session
  - `update_state(session_id, new_state)` — transitions session state with validation of allowed transitions
  - `list_active_sessions()` — returns all ACTIVE and WAITING_FOR_HUMAN sessions
- `Dispatcher(session_manager, repo, executor, config, *, personas_dir=None)` — core routing class that accepts a `StandardMessage` and runs the full pipeline. Uses the loaded `DanClawConfig` to resolve which agent handles the message (currently selects the default/first agent; per-channel routing is planned for later). Loads the agent's persona via `load_persona` and passes it to the executor on each dispatch:
  - `dispatch(message) -> DispatchResult` — resolves the agent from config, resolves effective permissions for the message's channel + user via `resolve_permissions`, checks `requires_approval`, loads the agent's persona content, finds or creates a session, stores the inbound message, and proceeds to execution. If approval is required, the session is set to `WAITING_FOR_HUMAN` and an approval message is returned without calling the executor. Otherwise, calls the executor with the persona and resolved `allowed_tools`, stores the response, and returns a `DispatchResult`. Detects persona switch commands (`/switch <agent>` or `switch to <agent>`) and updates the session's agent accordingly. After a switch, subsequent messages use the new agent's persona.
  - On executor failure, sets session state to `ERROR` and re-raises the exception.
  - If the persona file cannot be loaded, logs a warning and proceeds with `persona=None`.
  - The most recently resolved permissions are stored in `_last_resolved_permissions` for inspection.
- `DispatchResult` — frozen dataclass with `session_id`, `response` (text), `backend` (name of the backend that produced it), and `agent_name` (name of the agent that handled the message).
- `Executor` — protocol (typing.Protocol) defining the async `execute(message, *, persona=None, allowed_tools=None) -> ExecutorResult` interface that all executor implementations must satisfy. The `persona` keyword argument receives the agent's persona content (markdown string) loaded by the dispatcher. The `allowed_tools` keyword argument receives the resolved set of tools (frozenset) the user is allowed to use on the channel.
- `ExecutorResult` — frozen dataclass with `content` (response text) and `backend` (name of the backend that produced it).
- `MockExecutor(fixed_response=None)` — executor that returns canned responses. Echoes input by default (`"mock response: <content>"`); returns a fixed string when `fixed_response` is provided. Stores the most recently received persona in `last_persona` and the most recently received allowed_tools in `last_allowed_tools` for test verification.
- `ClaudeExecutor(claude_bin="claude")` — executor that calls `claude -p "<message>"` as an async subprocess. Uses `--resume <session_id>` when the message has a `session_id` for session persistence. Passes the agent persona via `--system-prompt` when provided. Captures stdout as response content. Raises `RuntimeError` on non-zero exit code (includes stderr in the error message). The `claude_bin` parameter allows overriding the CLI binary path.
- `CodexExecutor(codex_bin="codex")` — executor that calls `codex -q "<message>"` as an async subprocess. Runs in quiet mode. Does not support `--resume` or `--system-prompt` flags. Captures stdout as response content. Raises `RuntimeError` on non-zero exit code (includes stderr in the error message). The `codex_bin` parameter allows overriding the CLI binary path.
- `FallbackExecutor(executors)` — meta-executor that takes an ordered list of executors and tries each in sequence. If an executor raises any exception, it logs the failure and moves to the next executor. If all executors fail, the last exception is re-raised. Requires at least one executor. Useful for configuring primary/fallback AI backends (e.g., Claude primary with Codex fallback).
- `build_executor(backend_preference)` — factory function that maps a list of backend names (e.g. `["claude", "codex"]`) to executor instances and wraps them in a `FallbackExecutor`. Known backends: `"claude"` (ClaudeExecutor), `"codex"` (CodexExecutor), `"mock"` (MockExecutor). Raises `ValueError` for empty lists or unknown backend names. This is the primary way to construct an executor from an agent's `backend_preference` config field.
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
- `requires_approval(config, channel, user_id)` — returns ``True`` if any applicable permission layer (channel or user) has ``approval_required=True``. When the channel has ``override=True``, only the channel's flag is considered. This is a resolved boolean checkpoint; the actual "wait for approval" flow will be built when the executor is real.
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

Dispatcher starts as a standalone process, loads config, initialises the database, starts the SocketServer on a Unix domain socket, and shuts down cleanly on signal. The CLI (`cli/agent.py`) connects to the dispatcher as a separate process over the Unix socket. SQLite schema initialisation (`init_db`), repository abstraction layer (`Repository`), session lifecycle manager (`SessionManager`), mocked executor (`MockExecutor`), the core `Dispatcher` routing class, and the `SocketServer` Unix domain socket interface are available. The full pipeline (message in -> session -> executor -> store -> response) works end-to-end with the mock executor, accessible via Unix domain socket. The dispatcher loads each agent's persona from the personas directory and injects it into the executor on every dispatch call. Persona switching is supported: users can send `/switch <agent>` or `switch to <agent>` to change the active agent within a session, with subsequent messages using the new agent's persona. Permission resolution is integrated into the dispatch pipeline: on each dispatch, the dispatcher resolves the effective tool set for the message's channel + user and passes it to the executor as `allowed_tools`. If `requires_approval` is True for the channel or user, the session is set to `WAITING_FOR_HUMAN` with an approval message, and the executor is not called until a human approves. The `ClaudeExecutor` is available as a real AI backend that calls `claude -p` as an async subprocess with `--resume` for session persistence and `--system-prompt` for persona injection.
