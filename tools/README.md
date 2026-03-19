# Tools

Standalone scripts that agents can invoke during execution. Each tool is registered per-agent in the JSON config and gated by the permission model.

## Public Interface

Each tool is a script (Python or shell) that:
- Accepts structured input (arguments or stdin)
- Performs a specific action (e.g., read/write Obsidian vault, git operations)
- Returns structured output

### Obsidian Vault Tools

Three scripts provide Obsidian vault operations. All accept `--vault` (path to vault directory) and enforce path-traversal protection.

- **`obsidian_read.py`** — Read a file from the vault.
  - Args: `--vault PATH --file RELPATH`
  - Outputs file content to stdout.
  - `python -m tools.obsidian_read --vault /path/to/vault --file notes/todo.md`

- **`obsidian_write.py`** — Create or update a file in the vault.
  - Args: `--vault PATH --file RELPATH --content TEXT`
  - Creates intermediate directories automatically. Prints confirmation to stdout.
  - `python -m tools.obsidian_write --vault /path/to/vault --file notes/todo.md --content "# TODO"`

- **`obsidian_search.py`** — Search/list files by name pattern and/or content.
  - Args: `--vault PATH [--name GLOB] [--query TEXT]`
  - Prints matching relative paths to stdout (one per line). Skips hidden directories.
  - `python -m tools.obsidian_search --vault /path/to/vault --name "*.md" --query "TODO"`

### Git Operations (`git_ops.py`)

Git add, commit, and push operations via subprocess. Used by the admin agent for self-update workflows.

- **`git_add(paths, cwd)`** — Stage files for commit.
- **`git_commit(message, cwd)`** — Create a commit with the given message.
- **`git_push(remote, branch, cwd)`** — Push commits to the remote.

Also runnable as a CLI: `python -m tools.git_ops add --cwd /path --file1 file2`

### Deploy (`deploy.py`)

Deploy tool that pulls latest code, optionally rebuilds Docker images, and restarts services.

- **`deploy(cwd, rebuild)`** — Execute the full deploy sequence (git pull, docker compose build, docker compose up -d).

Also runnable as a CLI: `python -m tools.deploy --cwd /path [--no-rebuild]`

### Trigger Deploy (`trigger_deploy.py`)

Agent-callable entry point for deployments. Unlike `deploy.py` (which requires an explicit `--cwd`), `trigger_deploy` automatically resolves the danclaw project root, making it suitable for direct invocation by the admin agent.

- **`trigger_deploy(cwd, rebuild)`** — Trigger a deploy, defaulting to the danclaw project root.

Also runnable as a CLI: `python -m tools.trigger_deploy [--cwd /path] [--no-rebuild]`

### Instrumented Wrappers (`instrumented.py`)

`tools.instrumented` provides telemetry-emitting wrappers around tool functions. Each wrapper accepts a `telemetry` keyword argument (a `TelemetryCollector` instance) and records a `"tool_execution"` event with payload fields:

- `tool` — tool name (e.g. `"obsidian_read"`, `"git_add"`, `"git_commit"`, `"git_push"`)
- `args` — dict of arguments passed to the tool function
- `success` — boolean indicating success or failure
- `duration` — wall-clock seconds the call took
- `error` — error message (only present on failure)

Wrapped tools:
- **Obsidian**: `read_file`, `write_file`, `search_files`
- **Git operations**: `git_add`, `git_commit`, `git_push`
- **Deploy**: `deploy`, `trigger_deploy`

Events are emitted on both success and failure (exceptions are re-raised after recording).

## Relationship to Other Modules

- **Invoked by**: `dispatcher` (via the AI executor, on behalf of an agent)
- **Gated by**: permission model in `config`
- **Independent of**: `listeners`, `personas`
