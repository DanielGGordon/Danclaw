# Project Structure

```
danclaw/
├── README.md                 # Project overview, architecture, getting started
├── PROJECT_STRUCTURE.md      # This file — directory layout and module descriptions
├── PRD.md                    # Product requirements document
├── plans/
│   └── danclaw.md            # Implementation plan with phases and acceptance criteria
├── .env.example              # Environment variable template (secrets placeholder)
├── .env                      # Actual secrets (git-ignored)
├── .dockerignore             # Docker build context exclusions
├── .gitignore                # Git ignore rules
├── pyproject.toml            # Project metadata and dependencies
├── Dockerfile                # Container image for the dispatcher service
├── docker-compose.yml        # Multi-service orchestration with SQLite volume and .env
├── cli/
│   ├── __init__.py           # Python package marker
│   ├── agent.py              # CLI entry point: `agent chat`, `agent list`, and `agent attach` subcommands over Unix socket
│   └── README.md             # Module documentation
├── config/
│   ├── __init__.py           # Re-exports load_config, DanClawConfig, AgentConfig, ConfigError
│   ├── loader.py             # Config loader: reads, validates, returns structured config
│   ├── danclaw.json          # Main config: agent definitions, listener settings
│   └── README.md             # Module documentation
├── dispatcher/
│   ├── __init__.py           # Re-exports StandardMessage, init_db, Dispatcher, DispatchResult, etc.
│   ├── __main__.py           # Entry point: config loading, DB init, SocketServer startup, signal handling
│   ├── database.py           # SQLite schema init (sessions, messages, channel_bindings)
│   ├── dispatcher.py         # Core Dispatcher class: routes messages through session → executor → storage pipeline
│   ├── executor.py           # AI executor protocol, ExecutorResult, and MockExecutor (canned responses)
│   ├── models.py             # StandardMessage frozen dataclass with serialization helpers
│   ├── repository.py         # Async repository abstraction for all DB access (CRUD on sessions, messages, channel_bindings)
│   ├── session_manager.py    # High-level session lifecycle manager (get-or-create, state transitions, active listing)
│   ├── socket_server.py      # Asyncio Unix domain socket server accepting newline-delimited JSON (StandardMessage)
│   └── README.md             # Module documentation
├── listeners/
│   ├── __init__.py           # Python package marker
│   └── README.md             # Module documentation
├── personas/
│   ├── __init__.py           # Re-exports load_persona, PersonaError
│   ├── loader.py             # Persona loader: reads markdown files by name
│   ├── default.md            # Default agent persona (system prompt)
│   └── README.md             # Module documentation
├── tools/
│   ├── __init__.py           # Python package marker
│   └── README.md             # Module documentation
└── tests/                    # Test suite
    └── conftest.py           # Shared test helpers (make_config)
```

## Module Descriptions

- **cli/**: Command-line interface. `agent chat` starts an interactive session over the dispatcher's Unix domain socket. `agent list` displays all sessions in a formatted table. `agent attach <session-id>` attaches to an existing session, displays its history, then enters a chat loop.
- **config/**: Configuration loading and validation. Reads JSON config defining agents (name, persona, backend_preference, allowed_tools), listeners, and permissions.
- **dispatcher/**: Core message routing, session management, permission checks, AI executor invocation.
- **listeners/**: Channel adapters (terminal, Slack, Twilio) that translate to/from StandardMessage.
- **personas/**: Markdown files used as system prompts for agents. Referenced by name in config. Includes a loader module (`load_persona`) that reads persona files by name and returns their content as strings.
- **tools/**: Standalone scripts invokable by agents, registered per-agent in config.
- **tests/**: All test files. Mirrors the source module structure.
- **plans/**: Implementation plans and task tracking.

## Data Flow

```
External Channel → Listener → StandardMessage → Dispatcher → Agent (AI Executor) → Response
                                                    ↕
                                              SQLite (sessions, messages)
```
