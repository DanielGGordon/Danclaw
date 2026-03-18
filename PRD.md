# PRD: DanClaw — Multi-Agent Communication & Automation Platform

## Problem Statement

I want a personal AI agent system running on my Raspberry Pi 5 that I can talk to from any communication channel — Slack, terminal (SSH), phone calls, SMS, WhatsApp, a Chrome extension, ChatGPT via MCP, and eventually a web/mobile GUI. The system should support bidirectional, multi-turn conversations where the agent can ask clarifying questions before taking action. Different users (me, friends, collaborators) should be able to interact with the system through various channels, each with scoped permissions and capabilities. The architecture must be extensible so that adding a new communication channel requires only writing a listener — no changes to the dispatcher, agents, or core logic. Slack → Obsidian is use case #1, not the entire product. I need this to be transparent, cost-effective, reproducible by others, and fully under my control — not a black box SaaS product.

## Solution

A session-centric multi-agent platform that runs on a dedicated Raspberry Pi 5 (8GB), accessible via Tailscale. The system is composed of:

- **Listeners** that receive messages from external channels (Slack, terminal, phone/SMS, WhatsApp, Chrome extension, MCP, and any future channel) — all implementing a common listener interface
- **A dispatcher** that routes messages to the correct session and agent, completely channel-agnostic
- **Agents** defined in config, each with their own persona, tools, and permission boundaries
- **A session manager** that maintains multi-turn conversation state, supports channel bridging (start in Slack, pick up on terminal), and persists across reboots
- **An executor layer** that runs Claude Code (`claude -p`) as the primary AI backend with OpenAI Codex CLI as a configurable fallback
- **Tool scripts** that give agents real-world capabilities (Obsidian, Slack messaging, phone calls via Twilio + ElevenLabs, etc.)

```
┌──────────────────────────────────────────────────────────────────────┐
│                          LISTENERS                                   │
│       All listeners implement the same interface:                    │
│       receive() → StandardMessage, send(response) → channel         │
│                                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │
│  │ Slack    │ │Terminal/ │ │ Twilio   │ │ WhatsApp │ │ Future:   │ │
│  │ (Socket  │ │ SSH      │ │ Phone/   │ │ (Webhook)│ │ Chrome,   │ │
│  │  Mode)   │ │ (Unix    │ │ SMS      │ │          │ │ MCP,      │ │
│  │          │ │  Socket) │ │ (Webhook)│ │          │ │ Email,    │ │
│  │          │ │          │ │          │ │          │ │ Telegram..│ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬─────┘ │
│       │            │            │            │              │       │
└───────┼────────────┼────────────┼────────────┼──────────────┼───────┘
        │            │            │            │              │
        ▼            ▼            ▼            ▼              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    INTERNAL API (Channel-Agnostic)                    │
│         Unix Socket (local) + HTTP (webhook-based listeners)         │
│         Accepts/returns StandardMessage format only                   │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          DISPATCHER                                  │
│                                                                      │
│  ┌──────────────┐  ┌───────────────┐  ┌───────────────────────────┐ │
│  │ Session       │  │ Permission    │  │ Agent                     │ │
│  │ Manager       │  │ Resolver      │  │ Router                    │ │
│  │               │  │               │  │                           │ │
│  │ - find/create │  │ - channel     │  │ - match request to agent  │ │
│  │   sessions    │  │   perms       │  │   definition              │ │
│  │ - track state │  │ - user perms  │  │ - spawn/resume agent      │ │
│  │ - bridge      │  │ - override    │  │   process                 │ │
│  │   channels    │  │   flags       │  │                           │ │
│  └──────┬───────┘  └──────┬────────┘  └────────────┬──────────────┘ │
│         │                 │                         │                │
└─────────┼─────────────────┼─────────────────────────┼────────────────┘
          │                 │                         │
          ▼                 ▼                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     SESSION STORE (SQLite)                            │
│                                                                      │
│  Sessions | Messages | Channel Bindings | Event Log                  │
│                                                                      │
│  (Local-first, swappable backend, archival-ready)                    │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       EXECUTOR LAYER                                 │
│                                                                      │
│  ┌──────────────────────┐  ┌───────────────────────────────────────┐ │
│  │ claude -p --resume    │  │ codex (fallback)                     │ │
│  │                       │  │                                      │ │
│  │ Primary backend       │◄─┤ Activated on credit exhaust,         │ │
│  │ Session persistence   │  │ timeout, or config preference        │ │
│  └──────────┬────────────┘  └───────────────────────────────────────┘ │
│             │                                                        │
└─────────────┼────────────────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                           TOOLS                                      │
│                                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │
│  │ Obsidian │ │ Slack    │ │ Twilio + │ │ Git/     │ │ Future:   │ │
│  │ (vault   │ │ Send     │ │ Eleven   │ │ Deploy   │ │ Calendar, │ │
│  │  r/w)    │ │          │ │ Labs     │ │          │ │ Email,    │ │
│  │          │ │          │ │          │ │          │ │ Any API...│ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └───────────┘ │
│                                                                      │
│  Each tool is a standalone script, registered per-agent              │
└──────────────────────────────────────────────────────────────────────┘
```

### Listener Interface Contract

Every listener — current and future — implements the same contract. The dispatcher and agents know nothing about Slack, Twilio, or any specific channel. All channel-specific logic lives in the listener.

```
┌──────────────────────────────────────────────────────────────┐
│                   LISTENER CONTRACT                           │
│                                                              │
│  Every listener must:                                        │
│                                                              │
│  1. Receive a message from the outside world                 │
│  2. Translate it to a StandardMessage:                       │
│     - source (e.g., "slack", "terminal", "phone")            │
│     - channel_ref (e.g., Slack thread_ts, terminal tty)      │
│     - user_id                                                │
│     - content (text, or transcribed audio)                   │
│     - session_id (if resuming an existing conversation)      │
│  3. Send it to the dispatcher via Unix socket or HTTP        │
│  4. Receive agent responses and translate back to the        │
│     channel's native format                                  │
│                                                              │
│  Adding a new channel = write a listener + register in       │
│  config. No dispatcher or agent changes needed.              │
│                                                              │
│  Known future listeners:                                     │
│  Slack, Terminal, Phone, SMS, WhatsApp, Chrome Extension,    │
│  ChatGPT (MCP server), Email, Telegram, Calendar, Webhooks  │
└──────────────────────────────────────────────────────────────┘
```

### Permission Model

```
┌──────────────────────────────────────────────────────┐
│                  PERMISSION RESOLUTION                │
│                                                      │
│  Channel Permissions (baseline)                      │
│       │                                              │
│       ├── override_flag = false ──┐                  │
│       │                          ▼                   │
│       │              Channel Perms + User Perms      │
│       │              (user perms are additive)       │
│       │                                              │
│       └── override_flag = true ──┐                   │
│                                  ▼                   │
│                      Channel Perms ONLY              │
│                      (user perms ignored)            │
│                                                      │
│  Examples:                                           │
│  - #email-only: override=true, tools=[email]         │
│    → Even admin can only do email here               │
│                                                      │
│  - #dan-tasks: override=false, tools=[tasks]         │
│    → Friend gets tasks only                          │
│    → Dan's user perms add admin tools                │
│                                                      │
│  - #admin: override=false, tools=[all]               │
│    → No approval gates, full system access           │
│    → Can modify agent code and trigger deploy        │
└──────────────────────────────────────────────────────┘
```

### Bidirectional Conversation Flow

```
┌─────────┐         ┌────────────┐        ┌───────┐
│  Human  │         │ Dispatcher │        │ Agent │
└────┬────┘         └─────┬──────┘        └───┬───┘
     │                    │                   │
     │  "Add dark mode"   │                   │
     │───────────────────►│                   │
     │                    │  create session   │
     │                    │  resolve perms    │
     │                    │  spawn agent      │
     │                    │──────────────────►│
     │                    │                   │
     │                    │  "Which dashboard?"│
     │                    │◄──────────────────│
     │                    │  state=WAITING    │
     │  "Customer-facing" │                   │
     │◄───────────────────│                   │
     │                    │                   │
     │  "Customer-facing" │                   │
     │───────────────────►│                   │
     │                    │  resume session   │
     │                    │──────────────────►│
     │                    │                   │
     │                    │  "Done. PR #42"   │
     │                    │◄──────────────────│
     │                    │  state=DONE       │
     │  "Done. PR #42"   │                   │
     │◄───────────────────│                   │
     │                    │                   │

  Channel bridging: at any point, another listener
  (e.g., terminal) can attach to the same session.
  Messages flow to ALL bound channels.
```

## User Stories

1. As the system owner, I want to define agents in a central JSON config file with their tools, persona, and permissions, so that I can manage the entire system's behavior from one place.
2. As the system owner, I want the AI backend preference (Claude first, then Codex) to be configurable per agent in the JSON config, so that I can optimize cost and capability per use case.
3. As the system owner, I want to be notified (or not) when the system falls back from Claude to Codex, configurable per persona, so that I'm aware of capability changes when it matters.
4. As the system owner, I want to SSH into my Pi via Tailscale from anywhere and attach to any active session from the terminal, so that I can monitor or take over conversations.
5. As the system owner, I want to start a conversation on terminal and have it continue in a Slack channel (or vice versa), so that I'm not locked to one interface.
6. As the system owner, I want channel-level permissions that define the baseline tools and scope available, so that each channel has a clear purpose and boundary.
7. As the system owner, I want user-level permissions that are additive on top of channel permissions, so that I have more access than my friends in the same channel.
8. As the system owner, I want a channel override flag that locks permissions to channel-only (ignoring user permissions), so that purpose-specific channels like "email only" stay locked down.
9. As the system owner, I want approval requirements to be configurable per channel/persona/user, so that my #admin channel has no gates while shared channels require approval for destructive actions.
10. As the system owner, I want the #admin channel to allow the agent to modify its own code, push to git, and trigger a deploy/restart script, so that I can update the system through conversation.
11. As the system owner, I want sessions to persist across Pi reboots by default (configurable per session/channel), so that long-running conversations aren't lost.
12. As the system owner, I want the data layer designed so the storage backend is swappable from SQLite to an external DB later, so that I can scale without rearchitecting.
13. As the system owner, I want every event (message, agent action, tool call, error) stored in the DB, so that a future GUI can display conversation history and activity summaries.
14. As the system owner, I want each listener (Slack, terminal, phone) to be its own systemd service, so that they can be independently started, stopped, and debugged.
15. As the system owner, I want a Slack log channel where the agent posts activity summaries, so that I have quick visibility into what's happening without SSH.
16. As the system owner, I want systemd journal logging for all components, so that I can debug issues via `journalctl`.
17. As a Slack user (friend), I want to DM the bot or message it in a shared channel and have a multi-turn conversation where the agent asks me clarifying questions before acting, so that the agent understands what I need.
18. As a Slack user (friend), I want my conversation to happen in a Slack thread, so that the channel stays clean and my session is contained.
19. As a Slack user (friend), I want the bot to respond in the bot's voice by default, with configurable message formatting/attribution per session, so that responses feel natural.
20. As a Slack user, I want to be able to switch the active persona in a channel (e.g., "switch to code-review mode"), so that I can access different agent behaviors without changing channels.
21. As a phone caller, I want to call a Twilio number and speak to an agent using ElevenLabs voice synthesis, so that I can interact with the system hands-free.
22. As the system owner, I want to add a new listener (e.g., email, webhook) by creating a new systemd service and registering it in config, without modifying the dispatcher, so that the system scales easily.
23. As the system owner, I want to add a new tool by dropping a script into the tools directory and registering it in an agent's config, so that I can extend capabilities incrementally.
24. As the system owner, I want all secrets (API keys, tokens) loaded from a secure env file by systemd and never exposed in prompts or logs, so that credentials stay safe.
25. As the system owner, I want nothing exposed to the public internet — all access goes through Tailscale, so that the attack surface is minimal.
26. As the system owner, I want sessions to start as 1 human + 1 agent, with the architecture designed to support multi-human sessions later, so that I don't have to rearchitect for collaboration.
27. As someone reproducing this setup, I want to clone the repo and run `docker-compose up` to get the full system running, so that I don't need to manually install dependencies or configure systemd.
28. As the system owner, I want to add a new communication channel (e.g., WhatsApp, Telegram, email) by writing a listener that implements the standard interface and registering it in config, without modifying the dispatcher or agents.
29. As the system owner, I want the dispatcher to be completely channel-agnostic — it only sees StandardMessage objects, never Slack threads or Twilio SIDs — so that the core logic never needs to change when channels are added.
30. As a ChatGPT user, I want to interact with DanClaw through an MCP server so that I can use ChatGPT as a frontend while DanClaw handles execution (future listener).
31. As a Chrome extension user, I want to send requests to DanClaw from my browser and receive responses, so that I can trigger agent actions from any webpage (future listener).

## Implementation Decisions

### Architecture
- **Session-centric dispatcher** with an event log pattern: every message and action is an append-only event, making the DB the single source of truth for all activity.
- **Internal API**: Unix sockets for local IPC (terminal, cron) + lightweight HTTP server (aiohttp) for webhook-based listeners (Twilio, WhatsApp, Chrome extension, future MCP). Both accept the same StandardMessage format.
- **Systemd services** for all long-running processes (listeners, dispatcher). Auto-restart, logging, boot survival.
- **Docker Compose** for reproducible deployment. Anyone can clone the repo and `docker-compose up` to run the full system. Development can be done natively on the Pi for faster iteration; Docker is the deployment and distribution mechanism.
- **Tailscale only** for network access. No ports exposed to the public internet. Tailscale Funnel used selectively for inbound webhooks (Twilio, WhatsApp) that require a public URL.

### Tech Stack
- **Language**: Python 3.11+ — async-first, rich ecosystem for Slack/Twilio/ElevenLabs SDKs, stdlib SQLite.
- **Async runtime**: `asyncio` — single-process cooperative concurrency for the dispatcher. Handles multiple concurrent sessions without threading overhead.
- **Slack**: `slack-bolt` in Socket Mode (no inbound webhooks needed, works behind NAT/Tailscale).
- **Phone/SMS**: `twilio` Python SDK for telephony and SMS.
- **Voice synthesis**: `elevenlabs` Python SDK for generating speech on phone calls.
- **Database**: `sqlite3` (stdlib) with `aiosqlite` for async access. Repository pattern abstraction for future backend swapability — no ORM.
- **Internal HTTP**: `aiohttp` — lightweight async HTTP server for webhook-based listeners only. Not a full web framework.
- **AI executor**: `subprocess` shelling out to `claude -p` and `codex` CLI. No embedded SDKs — keeps the AI layer swappable and transparent.
- **Config**: JSON files loaded at startup.
- **Secrets**: systemd EnvironmentFile (native) or Docker secrets (containerized). Never in config or prompts.
- **Obsidian**: Direct file read/write via `pathlib` (exact approach TBD during implementation).
- **Containerization**: Docker Compose with one container per service (dispatcher, each listener). SQLite on a volume mount for persistence.
- **Process management**: systemd for native deployment, Docker Compose for containerized deployment.
- **Terminal bridge**: `socat` + a small Python CLI (`agent` command) for attaching to sessions via SSH.

### Agent Configuration
- Agents are first-class entities defined in a central JSON config. Each agent definition includes: name, persona (system prompt), allowed tools, AI backend preference (ordered list, e.g., `["claude", "codex"]`), and permission boundaries.
- Listeners are configured in their own section of the config, mapping to which agents they can invoke. Each listener type (Slack, Twilio, terminal, etc.) has its own config block with channel-specific settings.
- Personas are markdown files in a `personas/` directory, referenced by name in agent config. A channel can switch between personas at runtime.

### Permission Model (Three Layers)
1. **Channel permissions** — baseline tools and scope for that channel/listener.
2. **User permissions** — additive on top of channel permissions (e.g., Dan gets admin tools in a shared channel).
3. **Channel override flag** — when set to true, channel permissions are the ceiling. User permissions are ignored. Used for purpose-locked channels (e.g., email-only).

Approval gates are configurable per channel/persona/user combination.

### AI Backend
- Primary: `claude -p` with `--resume` for session persistence.
- Fallback: Codex CLI, activated on credit exhaustion, timeout, or error.
- Backend preference is an ordered list in the agent config JSON. Default: `["claude", "codex"]`.
- Fallback notification behavior is configurable per persona (silent, notify user, or custom message).

### Session Management
- Each conversation is a session with a unique ID.
- Sessions track state: `ACTIVE`, `WAITING_FOR_HUMAN`, `DONE`, `ERROR`.
- Sessions have channel bindings — one session can be bound to multiple channels simultaneously (bridging).
- Terminal bridging: an `agent attach <session-id>` command binds the terminal to an existing session. Messages flow to all bound channels.
- Messages from terminal appear as the bot by default. Attribution formatting is configurable per session.
- Sessions persist across reboots by default (configurable per session/channel).

### Data Layer
- SQLite for local storage. Tables: sessions, messages, channel_bindings, events.
- Storage access goes through an abstraction layer so the backend is swappable to an external DB later.
- Event log is append-only — every human message, agent response, tool call, and error is an event. This powers future GUI, daily digests, and audit.

### Tools
- Each tool is a standalone script (shell or Python) in a `tools/` directory.
- Tools are registered per-agent in config. An agent can only call tools it's been assigned.
- Initial tools: Obsidian vault access (read/write markdown files — exact CLI approach TBD during implementation), Slack message sending, phone calls (Twilio for telephony + ElevenLabs for voice synthesis), git operations, deploy/restart script.

### Self-Update Mechanism
- The #admin channel (or equivalent) agent can edit the system's own code, commit, push, and trigger a deploy script.
- Deploy script: pull latest, restart affected systemd services.
- No remote CI/CD. Code is assumed tested before push.

### Hosting
- Raspberry Pi 5, 8GB RAM, dedicated to this system.
- All services managed by systemd.

## Testing Decisions

A good test for this system verifies **external behavior through the interfaces** — does a message in, produce the right message/action out? Tests should not depend on internal implementation details like specific function signatures or data structures.

### Modules to test:
- **Dispatcher routing** — given a message from a specific channel/user, does it reach the correct agent with the correct permissions resolved?
- **Permission resolver** — given channel perms, user perms, and override flags, does it produce the correct effective permission set?
- **Session lifecycle** — create, resume, bridge, persist, and recover sessions correctly across states.
- **AI backend fallback** — does the executor correctly fall back from Claude to Codex on failure/exhaustion, and respect per-agent config?
- **Listener message parsing** — does each listener correctly extract session context (e.g., Slack thread_ts → session ID mapping)?
- **Tool execution** — do tool scripts produce expected outputs given known inputs?

### Testing approach:
- Integration tests that send a message through the Unix socket and verify the dispatcher's behavior end-to-end.
- Unit tests for the permission resolver (combinatorial logic with three layers).
- Mock the AI backend for session lifecycle tests (stub `claude -p` and `codex` responses).
- Real Slack API tests in a dedicated test channel for the Slack listener.

## Out of Scope

- **Web or mobile GUI** — the system will be designed to support a future GUI (all events in the DB), but building the GUI is not part of this PRD.
- **Multi-human sessions** — architecture will support this later, but initial implementation is 1 human + 1 agent per session.
- **MCP server listener** — ChatGPT-as-frontend via MCP is a future listener, not initial scope.
- **Chrome extension listener** — future listener, not initial scope.
- **WhatsApp/Telegram/Email listeners** — future listeners, not initial scope. Architecture supports adding them.
- **Remote CI/CD pipeline** — deploys are triggered locally via script.
- **External database migration** — SQLite is the initial backend; migration to an external DB is a future effort.

## Stretch Goals (Low Priority)

- **Daily digest** — agent sends a summary of all sessions/actions from the past 24 hours via Slack or email.
- **Alert thresholds** — notifications for unusual activity (error spikes, credit usage, stuck agents).

## Further Notes

- The project name is **DanClaw** (a play on the "OpenClaw" alternative mentioned in the source video).
- The system philosophy is "glass box, not black box" — every component is readable, debuggable, and replaceable.
- The config-driven design means most changes (adding agents, tools, channels, permissions) require zero code changes — just config edits and service restarts.
- Docker Compose is the deployment and distribution mechanism. Native systemd is an alternative for direct Pi development.
- The listener interface is the primary extensibility point. Adding a new communication channel (WhatsApp, Telegram, email, Chrome, MCP, etc.) requires only writing a listener that translates to/from StandardMessage — zero changes to dispatcher, agents, or tools.
- Slack → Obsidian is use case #1, chosen to prove the full architecture end-to-end. The system is designed for many listeners and many tools from the start.
