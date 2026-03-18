# cli/ — Command-Line Interface

CLI entry points for interacting with the DanClaw dispatcher.

## Subcommands

### `agent chat`

Start an interactive chat session over the dispatcher's Unix domain socket.

```bash
python -m cli.agent chat [--socket /path/to/socket]
```

- Connects to the dispatcher's Unix domain socket (default: `/tmp/danclaw.sock`, override with `--socket` or `DANCLAW_SOCKET` env var).
- Sends each user message as a `StandardMessage` JSON line.
- Displays the agent's response.
- Creates a new session on the first message and reuses it for subsequent messages.
- Type `exit` or press Ctrl+C to quit.

### `agent list`

List all sessions from the dispatcher in a formatted table.

```bash
python -m cli.agent list [--socket /path/to/socket]
```

- Connects to the dispatcher's Unix domain socket and sends a `list_sessions` request.
- Displays session IDs, agent names, states, and creation times in a formatted table.
- Shows "No sessions found." when no sessions exist.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DANCLAW_SOCKET` | `/tmp/danclaw.sock` | Path to the dispatcher Unix domain socket |
| `USER` | `terminal-user` | User ID sent in messages |

## Module Layout

- `__init__.py` — Package marker.
- `agent.py` — `agent` command with `chat` and `list` subcommands. Contains `chat()`, `list_sessions()`, `_format_sessions_table()`, `_connect()`, `_send_recv()`, `_build_message()`, and `main()`.
