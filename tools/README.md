# Tools

Standalone scripts that agents can invoke during execution. Each tool is registered per-agent in the JSON config and gated by the permission model.

## Public Interface

Each tool is a script (Python or shell) that:
- Accepts structured input (arguments or stdin)
- Performs a specific action (e.g., read/write Obsidian vault, git operations)
- Returns structured output

## Relationship to Other Modules

- **Invoked by**: `dispatcher` (via the AI executor, on behalf of an agent)
- **Gated by**: permission model in `config`
- **Independent of**: `listeners`, `personas`

## Status

Scaffold only. No tools implemented yet.
