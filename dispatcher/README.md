# Dispatcher

The core routing and orchestration process. Accepts `StandardMessage` objects from listeners, manages sessions, resolves permissions, selects the appropriate agent, invokes the AI executor, stores results, and returns responses.

## Public Interface

- Accepts `StandardMessage` via Unix domain socket or HTTP
- Returns response messages to the calling listener
- Manages session lifecycle (create, resume, close)

## Relationship to Other Modules

- **Receives from**: `listeners` (StandardMessage input)
- **Uses**: `config` (agent definitions, permissions), `personas` (system prompts), `tools` (per-agent tool access)
- **Stores to**: SQLite database (sessions, messages, telemetry)

## Status

Scaffold only. No dispatcher logic implemented yet.
