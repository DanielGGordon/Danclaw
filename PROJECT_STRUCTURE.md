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
├── logging_config/
│   ├── __init__.py           # Re-exports setup_logging
│   ├── setup.py              # JSONFormatter and setup_logging: structured JSON logging for all components
│   └── README.md             # Module documentation
├── cli/
│   ├── __init__.py           # Python package marker
│   ├── agent.py              # CLI entry point: `agent chat`, `agent list`, and `agent attach` subcommands over Unix socket
│   └── README.md             # Module documentation
├── config/
│   ├── __init__.py           # Re-exports load_config, validate_config, DanClawConfig, AgentConfig, ConfigError
│   ├── loader.py             # Config loader: reads, validates, returns structured config
│   ├── danclaw.json          # Main config: agent definitions, listener settings
│   └── README.md             # Module documentation
├── dispatcher/
│   ├── __init__.py           # Re-exports StandardMessage, init_db, Dispatcher, DispatchResult, etc.
│   ├── __main__.py           # Entry point: config loading, DB init, SocketServer startup, signal handling
│   ├── database.py           # SQLite schema init (sessions, messages, channel_bindings)
│   ├── dispatcher.py         # Core Dispatcher class: routes messages through session → executor → storage pipeline
│   ├── executor.py           # AI executor protocol, ExecutorResult, MockExecutor (canned responses), ClaudeExecutor (claude -p subprocess), CodexExecutor (codex -q subprocess), and FallbackExecutor (ordered fallback chain)
│   ├── models.py             # StandardMessage frozen dataclass with serialization helpers
│   ├── permissions.py        # Permission resolver: computes effective tool sets for channel + user pairs
│   ├── repository.py         # Async repository abstraction for all DB access (CRUD on sessions, messages, channel_bindings)
│   ├── session_manager.py    # High-level session lifecycle manager (get-or-create, state transitions, active listing)
│   ├── socket_server.py      # Asyncio Unix domain socket server with client registry and fanout push
│   ├── telemetry.py          # In-memory telemetry event recording (TelemetryEvent, TelemetryCollector)
│   └── README.md             # Module documentation
├── listeners/
│   ├── __init__.py           # Python package marker
│   ├── README.md             # Module documentation
│   └── slack/
│       ├── __init__.py       # Re-exports SlackListener
│       ├── listener.py       # SlackListener: Socket Mode listener using slack-bolt
│       ├── __main__.py       # Entry point for running as standalone process
│       └── README.md         # Module documentation
├── personas/
│   ├── __init__.py           # Re-exports load_persona, PersonaError
│   ├── loader.py             # Persona loader: reads markdown files by name
│   ├── default.md            # Default agent persona (system prompt)
│   ├── admin.md              # Admin agent persona (full access, no approval gates)
│   └── README.md             # Module documentation
├── tools/
│   ├── __init__.py           # Python package marker
│   ├── obsidian_read.py      # Read a file from an Obsidian vault (subprocess tool)
│   ├── obsidian_write.py     # Create or update a file in an Obsidian vault (subprocess tool)
│   ├── obsidian_search.py    # Search/list files in an Obsidian vault by name or content (subprocess tool)
│   ├── git_ops.py            # Git add, commit, push operations (admin tool)
│   ├── deploy.py             # Deploy tool: pull, rebuild, restart services (admin tool)
│   ├── trigger_deploy.py     # Agent-callable deploy entry point with auto project root detection
│   ├── instrumented.py       # Telemetry-instrumented wrappers for all tool functions
│   └── README.md             # Module documentation
├── scripts/
│   └── e2e_test.py           # Standalone end-to-end smoke test (requires claude CLI)
└── tests/                    # Test suite
    ├── conftest.py           # Shared test helpers (make_config)
    ├── test_admin_agent.py   # Tests for admin agent config, permissions, dispatch, and git ops integration
    ├── test_e2e_claude.py    # End-to-end integration test via ClaudeExecutor (manual marker)
    ├── test_git_ops.py       # Tests for git add/commit/push tool functions and CLI
    ├── test_git_ops_telemetry.py # Tests for telemetry-instrumented git operation wrappers
    ├── test_deploy.py          # Tests for deploy tool: pull, build, restart sequence
    ├── test_deploy_restriction.py # Tests for deploy restriction: non-admin channels/users cannot trigger deploy
    ├── test_deploy_telemetry.py # Tests for telemetry-instrumented deploy wrapper
    ├── test_trigger_deploy.py   # Tests for agent-triggered deploy: tool, telemetry, permissions, and dispatcher integration
    └── test_telemetry_query.py # Tests for telemetry query/filter/pagination methods
```

## Module Descriptions

- **logging_config/**: Shared structured JSON logging configuration. Provides `setup_logging()` which configures the root logger to emit single-line JSON objects (with `timestamp`, `level`, `logger`, `message`, and optional context fields) to stderr. Used by all entry points (dispatcher, Slack listener, CLI).
- **cli/**: Command-line interface. `agent chat` starts an interactive session over the dispatcher's Unix domain socket. `agent list` displays all sessions in a formatted table. `agent attach <session-id>` attaches to an existing session, displays its history, then enters a chat loop.
- **config/**: Configuration loading and validation. Reads JSON config defining agents (name, persona, backend_preference, allowed_tools), listeners, and permissions (per-channel tools/override, per-user additional tools).
- **dispatcher/**: Core message routing, session management, permission checks, AI executor invocation.
- **listeners/**: Channel adapters that translate to/from StandardMessage. Contains sub-modules per channel.
  - **listeners/slack/**: Slack Socket Mode listener using `slack-bolt`. Connects via `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`, converts Slack events to StandardMessage, and forwards to dispatcher via Unix socket. Maps thread_ts to channel_ref for session grouping.
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
