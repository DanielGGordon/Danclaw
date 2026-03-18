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
├── config/
│   ├── __init__.py           # Re-exports load_config, DanClawConfig, AgentConfig, ConfigError
│   ├── loader.py             # Config loader: reads, validates, returns structured config
│   ├── danclaw.json          # Main config: agent definitions, listener settings
│   └── README.md             # Module documentation
├── dispatcher/
│   ├── __init__.py           # Re-exports StandardMessage, init_db
│   ├── __main__.py           # Entry point: config loading, signal handling, async loop
│   ├── database.py           # SQLite schema init (sessions, messages, channel_bindings)
│   ├── models.py             # StandardMessage frozen dataclass with serialization helpers
│   └── README.md             # Module documentation
├── listeners/
│   ├── __init__.py           # Python package marker
│   └── README.md             # Module documentation
├── personas/
│   ├── default.md            # Default agent persona (system prompt)
│   └── README.md             # Module documentation
├── tools/
│   ├── __init__.py           # Python package marker
│   └── README.md             # Module documentation
└── tests/                    # Test suite
```

## Module Descriptions

- **config/**: Configuration loading and validation. Reads JSON config defining agents, listeners, permissions.
- **dispatcher/**: Core message routing, session management, permission checks, AI executor invocation.
- **listeners/**: Channel adapters (terminal, Slack, Twilio) that translate to/from StandardMessage.
- **personas/**: Markdown files used as system prompts for agents. Referenced by name in config.
- **tools/**: Standalone scripts invokable by agents, registered per-agent in config.
- **tests/**: All test files. Mirrors the source module structure.
- **plans/**: Implementation plans and task tracking.

## Data Flow

```
External Channel → Listener → StandardMessage → Dispatcher → Agent (AI Executor) → Response
                                                    ↕
                                              SQLite (sessions, messages)
```
