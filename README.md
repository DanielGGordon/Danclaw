# DanClaw

Multi-agent communication and automation platform. Routes messages from multiple channels (terminal, Slack, phone) through a central dispatcher to AI agents with configurable personas, tools, and permissions.

## Architecture

- **Listeners** (`listeners/`): Channel adapters that translate external messages to a standard internal format
- **Dispatcher** (`dispatcher/`): Core routing, session management, permission resolution, and AI execution
- **Tools** (`tools/`): Standalone scripts invokable by agents (e.g., Obsidian vault access, git operations)
- **Config** (`config/`): JSON configuration for agents, permissions, and listeners; config loading and validation
- **Personas** (`personas/`): Markdown system prompts defining agent behavior

## Tech Stack

- Python 3.11+, asyncio
- SQLite via aiosqlite
- aiohttp for HTTP-based IPC
- Docker Compose for deployment
- Tailscale for networking

## Getting Started

```bash
# Copy environment template
cp .env.example .env
# Edit .env with your actual secrets

# Run with Docker Compose
docker-compose up
```

## Status

Phase 1: Project scaffold in progress.
