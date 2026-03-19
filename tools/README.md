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

### Instrumented Wrappers (`instrumented.py`)

`tools.instrumented` provides telemetry-emitting wrappers around the Obsidian tool functions (`read_file`, `write_file`, `search_files`). Each wrapper accepts a `telemetry` keyword argument (a `TelemetryCollector` instance) and records a `"tool_execution"` event with payload fields:

- `tool` — tool name (e.g. `"obsidian_read"`)
- `args` — dict of arguments passed to the tool function
- `success` — boolean indicating success or failure
- `duration` — wall-clock seconds the call took
- `error` — error message (only present on failure)

Events are emitted on both success and failure (exceptions are re-raised after recording).

## Relationship to Other Modules

- **Invoked by**: `dispatcher` (via the AI executor, on behalf of an agent)
- **Gated by**: permission model in `config`
- **Independent of**: `listeners`, `personas`
