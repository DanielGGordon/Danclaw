# Personas

Markdown files containing system prompts for agents. Each persona defines the agent's behavior, tone, and capabilities. Referenced by name in the JSON config.

## Public Interface

Each persona is a `.md` file that serves as the system prompt for an agent. The dispatcher loads the file content and injects it as context when invoking the AI executor.

## Relationship to Other Modules

- **Referenced by**: `config` (agent definitions point to persona names)
- **Loaded by**: `dispatcher` (reads file content at agent invocation time)
- **Independent of**: `listeners`, `tools`

## Personas

- **`default.md`** — Default assistant persona. Helpful, concise, and direct.
- **`admin.md`** — Admin agent persona with full tool access. Executes commands directly without approval gates.

## Status

Two personas defined: `default` and `admin`.
