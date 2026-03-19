# Plan: DanClaw — Multi-Agent Communication & Automation Platform

> Source PRD: PRD.md

## Architectural decisions

Durable decisions that apply across all phases:

- **Language & runtime**: Python 3.11+, asyncio for concurrency. Single async event loop in the dispatcher process.
- **Internal message format**: All listeners translate to/from a `StandardMessage` model (source, channel_ref, user_id, content, session_id). The dispatcher never sees channel-specific data.
- **IPC**: Unix domain sockets for local listeners (terminal, cron). HTTP (aiohttp) for webhook-based listeners (Twilio, future WhatsApp/Chrome). Both accept StandardMessage.
- **Database**: SQLite via `aiosqlite`. All DB access goes through a repository abstraction layer (not an ORM). Tables: `sessions`, `messages`, `channel_bindings`, `telemetry_events`.
- **Config**: Single JSON config file defining agents (name, persona, tools, backend preference, permissions). Separate sections for listeners. Personas are markdown files in `personas/` referenced by name.
- **Secrets**: `.env` file loaded by Docker Compose or systemd EnvironmentFile. Never in config, prompts, or logs.
- **AI executor**: Subprocess calls to `claude -p` (primary) and `codex` (fallback). Backend preference is an ordered list per agent. Session persistence via `--resume`.
- **Tools**: Standalone scripts in `tools/`, registered per-agent in config.
- **Permissions**: Three-layer model — channel permissions (baseline), user permissions (additive), channel override flag (locks to channel-only). Resolved before every agent invocation.
- **Session state**: `ACTIVE`, `WAITING_FOR_HUMAN`, `DONE`, `ERROR`. Sessions have channel bindings for multi-channel fanout.
- **Telemetry**: Structured JSONL events (event_type, session_id, source, status, payload, timestamp) appended to file and stored in DB.
- **Deployment**: Docker Compose (one container per service, SQLite on volume mount). Native systemd as alternative for dev.
- **Network**: Tailscale only. No public ports except Tailscale Funnel for inbound webhooks where required.

---

## Phase 1: Project Scaffold & Docker

**User stories**: 25, 28

### What to build

Set up the project structure, dependency management, Docker Compose configuration, and config/secrets loading. The result is a running Docker Compose stack with a dispatcher container that starts up, loads config, loads secrets from `.env`, connects to SQLite, and logs that it's ready. No real functionality yet — just proof that the infrastructure works.

### Acceptance criteria

- [x] Project has a defined directory structure with clear separation of concerns (listeners, dispatcher, tools, config, personas)
- [x] `pyproject.toml` (or equivalent) with initial dependencies: `aiosqlite`, `aiohttp`
- [x] Docker Compose file with a dispatcher service, SQLite volume mount, and `.env` file for secrets
- [x] `.env.example` with placeholder keys (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `CLAUDE_API_KEY`)
- [x] JSON config file with a minimal agent definition (name, persona, backend preference)
- [x] Config loader that reads and validates the JSON config at startup
- [x] Dispatcher process starts, loads config, logs readiness, and exits cleanly on shutdown
- [x] `docker-compose up` builds and runs successfully
- [x] `.gitignore` excludes `.env`, SQLite files, `__pycache__`, etc.

---

## Phase 2: Dispatcher & Sessions (Mocked AI)

**User stories**: 11, 12, 13, 30

### What to build

The core dispatcher loop: accept a StandardMessage, find or create a session, route to an agent, execute (with a mocked AI backend that returns canned responses), store the message and response in SQLite, update session state, and return the response. This phase establishes the full internal pipeline without any real listeners or AI backends. Testable via a Python script that programmatically sends StandardMessages to the dispatcher.

### Acceptance criteria

- [x] `StandardMessage` data model defined (source, channel_ref, user_id, content, session_id)
- [x] SQLite schema created on first run: sessions, messages, channel_bindings tables
- [x] Repository abstraction layer for all DB access (no direct SQL outside the repository)
- [x] Session manager: create session, find by ID, update state (ACTIVE, WAITING_FOR_HUMAN, DONE, ERROR)
- [x] Channel binding: associate a session with one or more channel references
- [x] Mocked executor that returns a canned response (e.g., echoes the input or returns a fixed string)
- [x] Dispatcher accepts a StandardMessage, routes through session manager → executor → stores result → returns response
- [x] Messages persist across process restarts (SQLite on disk)
- [x] Sessions persist across process restarts and resume correctly
- [x] Integration test: send a message, get a response, send a follow-up, verify session continuity

---

## Phase 3: Terminal Listener

**User stories**: 4, 14

### What to build

A terminal-based listener that connects to the dispatcher via Unix domain socket. A CLI command (`agent`) that lets you send messages and see responses interactively. This is the first real way to interact with the system end-to-end. Still uses the mocked AI backend.

### Acceptance criteria

- [x] Dispatcher listens on a Unix domain socket, accepts StandardMessage JSON, returns response JSON
- [x] `agent chat` command starts an interactive session — type a message, see the response, repeat
- [x] `agent chat` creates a new session on first message, reuses it for subsequent messages
- [x] `agent list` command shows active sessions with their IDs and states
- [x] `agent attach <session-id>` attaches to an existing session and shows its message history
- [x] Terminal listener runs as its own process (or Docker container), communicating with the dispatcher over the Unix socket
- [x] Clean shutdown on Ctrl+C

---

## Phase 4: Config-Driven Agents & Personas

**User stories**: 1, 2, 19, 20

### What to build

Agents become real entities loaded from config. Each agent has a name, a persona (markdown system prompt), an ordered backend preference, and a list of allowed tools. The dispatcher routes messages to the correct agent based on config. Persona switching within a session via a command (e.g., "switch to code-review mode").

### Acceptance criteria

- [x] Agent definitions in JSON config: name, persona reference, backend preference list, allowed tools
- [x] Personas stored as markdown files in `personas/` directory, loaded by name
- [x] Dispatcher resolves which agent to use for a given message based on config
- [x] Agent's persona is injected as context when invoking the executor
- [x] Persona switching: a user can request a different persona within a session, and subsequent messages use the new persona
- [x] Config validation: startup fails with a clear error if an agent references a missing persona or tool
- [x] Default agent configured for when no specific agent is matched

---

## Phase 5: Permission Model

**User stories**: 6, 7, 8, 9

### What to build

The three-layer permission resolver. Channel permissions define the baseline. User permissions are additive. The channel override flag locks to channel-only when set. Approval gates are configurable per channel/persona/user. Permissions are checked before every agent invocation.

### Acceptance criteria

- [x] Permission definitions in JSON config: per-channel (tools, override flag), per-user (additional tools)
- [x] Permission resolver: given a channel + user, compute the effective permission set
- [x] Override flag: when true, user permissions are ignored — only channel permissions apply
- [x] Approval gates: configurable per channel/persona/user — when enabled, high-impact actions require confirmation
- [x] Dispatcher checks permissions before invoking an agent — blocked requests return an error message to the user
- [x] Unit tests covering all permission combinations: channel-only, channel+user, override=true, override=false, approval required vs not

---

## Phase 6: AI Executor (Real Backends)

**User stories**: 2, 3

### What to build

Replace the mocked executor with real `claude -p` and `codex` invocations. The executor reads the ordered backend preference from the agent config and tries each in order. On failure (timeout, credit exhaustion, error), it falls back to the next backend. Fallback notification is configurable per persona.

### Acceptance criteria

- [x] Executor calls `claude -p --resume <session-id>` with the agent's persona as system prompt
- [x] Executor calls `codex` as fallback when claude fails (timeout, non-zero exit, credit error)
- [x] Backend preference order read from agent config (default: `["claude", "codex"]`)
- [x] Timeout configurable per agent
- [x] Fallback notification: configurable per persona — silent, notify user, or custom message
- [x] Telemetry event emitted on fallback (which backend failed, which succeeded)
- [x] End-to-end test via terminal listener: send a real message, get a real AI response

---

## Phase 7: Slack Listener

**User stories**: 17, 18, 23, 29

### What to build

Slack Socket Mode bot that listens for messages (channel messages, DMs, @mentions). Each Slack thread maps to a session. The bot responds in-thread. Bidirectional multi-turn conversations work — the bot can ask clarifying questions (WAITING_FOR_HUMAN), and the user's next message in the thread resumes the session.

### Acceptance criteria

- [x] Slack listener connects via Socket Mode using `slack-bolt` (no public webhook needed)
- [x] Bot responds to @mentions and DMs
- [x] Each Slack thread maps to a unique session (thread_ts → session ID)
- [x] Bot replies in-thread, keeping the channel clean
- [x] Multi-turn: bot asks a question → state=WAITING_FOR_HUMAN → user replies in thread → session resumes
- [x] Bot ignores its own messages (no loops)
- [x] Slack listener runs as its own Docker container/service
- [x] Slack tokens loaded from `.env`, never logged

---

## Phase 8: Session Bridging

**User stories**: 5, 27

### What to build

A session can be bound to multiple channels simultaneously. When you `agent attach <session-id>` from terminal, you join the same session that's active in a Slack thread. Messages from any bound channel are delivered to the agent, and agent responses fan out to all bound channels. Terminal messages appear as the bot in Slack by default (configurable).

### Acceptance criteria

- [x] A session can have multiple channel bindings (e.g., both a Slack thread and a terminal)
- [x] `agent attach <session-id>` adds a terminal binding to an existing session
- [x] Messages sent from terminal are delivered to the agent and the response appears in both terminal and Slack thread
- [x] Messages sent from Slack are delivered to the agent and the response appears in both Slack thread and terminal
- [x] Attribution: terminal messages appear as the bot in Slack by default
- [x] Attribution formatting is configurable per session
- [x] Detaching from terminal removes the binding without affecting the Slack session

---

## Phase 9: Obsidian Tool

**User stories**: 24

### What to build

The first real tool: read and write files in an Obsidian vault. Registered per-agent in config. The agent can search, read, and modify markdown files in the vault directory. This completes use case #1: a user messages in Slack, the agent reads/writes Obsidian notes, and responds.

### Acceptance criteria

- [x] Obsidian tool script(s) in `tools/`: read file, write file, search/list files in vault
- [x] Vault path configurable in JSON config
- [x] Tool registered in agent config and only available to agents that have it in their allowed tools list
- [x] Permission-gated: a user without Obsidian tool access cannot trigger it
- [x] End-to-end: Slack message → agent reads an Obsidian note → responds with content in Slack thread
- [x] End-to-end: Slack message → agent creates/updates an Obsidian note → confirms in Slack thread
- [x] Tool execution emits telemetry events

---

## Phase 10: Telemetry & Observability

**User stories**: 15, 16, 33

### What to build

Structured JSONL telemetry for every significant system action. A Slack log channel where the bot posts activity summaries. All components log to systemd journal (or Docker logs) for debugging.

### Acceptance criteria

- [x] Every dispatcher action emits a telemetry event: permission checks, routing decisions, agent spawns, tool calls, session state changes, errors, fallbacks
- [x] Events written to JSONL file (append-only) and stored in `telemetry_events` DB table
- [x] Each event has: event_type, session_id, source, status, payload, timestamp
- [x] Slack log channel: bot posts a summary when sessions complete or errors occur
- [x] Log channel is configurable in JSON config (channel ID)
- [x] All components produce structured logs compatible with `journalctl` / `docker logs`
- [x] Telemetry data is queryable from the DB (supports future GUI)

---

## Phase 11: Self-Update & Deploy

**User stories**: 10

### What to build

An admin channel/agent that can modify the system's own code, commit, push to git, and trigger a deploy script that pulls the latest and restarts services. No approval gates in the admin channel.

### Acceptance criteria

- [x] Admin agent defined in config with full tool access and no approval gates
- [x] Admin agent can execute git operations (add, commit, push)
- [x] Deploy script: pulls latest from git, rebuilds Docker images if needed, restarts affected services
- [x] Deploy triggered by the agent via a tool script
- [x] Admin channel configured with no approval override
- [ ] Telemetry events emitted for deploy actions
- [ ] Non-admin users/channels cannot trigger deploy

---

## Future Work

- **Real-time voice calls** (user stories 21, 22): Twilio Media Streams + ElevenLabs real-time STT/TTS with cloned voice. Deferred — revisit once the core platform is stable and latency benchmarks can be run against real infrastructure.
