# Project-local activation (experimental host integrations)

Install the Seldon wheel into a persistent Python environment, then run its
`seldon` command from the product folder:

```text
seldon init --host all --dry-run
seldon init --host codex
seldon init --host claude
seldon init --host copilot-vscode
seldon init --check
seldon init --remove
```

An optional path selects an existing project folder. Without `--host`, init
offers detected hosts; `--yes` selects all detected hosts without prompting.
Detection observes executable/app existence only. `--name` sets the heading in
the generated `SELDON.md`. Existing Seldon registrations pointing to another
workspace are refused unless `--yes` explicitly authorizes replacing that entry.
Init never changes global configuration, trust, authentication or permissions.

The absolute interpreter from the wheel installation starts the canonical MCP
server with `--workspace` bound to the project's absolute path. Keep this Python
installation in place. Source-only and editable installations are refused. Moving
a project requires reinitialization; `--check` detects the old workspace binding.

The host writers use documented project configuration formats:

| Host | Project file | Next host action |
| --- | --- | --- |
| [Codex](https://learn.chatgpt.com/docs/extend/mcp#connect-codex-to-an-mcp-server) | `.codex/config.toml` | Open this folder and choose Trust |
| [Claude Code](https://code.claude.com/docs/en/mcp#project-scope) | `.mcp.json` | Open a chat here; approve seldon when prompted |
| [Copilot in VS Code](https://code.visualstudio.com/docs/agents/reference/mcp-configuration) | `.vscode/mcp.json` | Review/approve the workspace MCP server |

These formats were checked against primary documentation on 2026-10-22. Local
fixture checks establish generated registration and server behavior. Each host
integration remains experimental until its actual chat-to-Align journey is
observed. Claude Desktop chat and Copilot CLI are not covered by these writers.

Existing JSON values are preserved; JSON formatting can change during a merge.
Strict JSON is required: JSONC/comments, duplicate keys and complex existing
Codex `seldon` tables are refused for manual merge rather than rewritten.
Codex settings outside the owned block retain their text. Repeated init does not
rewrite unchanged files. Ownership uses `seldon-init v1` comments and an ordinary
server environment marker. Removal deletes only marked registrations, generated
guidance and ignore entries, preserving plan data and unrelated settings. The
remaining empty host configuration files are retained.

`--dry-run` prints full proposed files and writes nothing. Those files may contain
existing configuration, so review its output locally. Normal output does not dump
unrelated host settings. Preparation validates every target before writing;
individual file replacements are atomic. An I/O failure during multi-file writing
can leave a partial setup: fix that failure and rerun the same command.

`--check` compares registrations, ownership markers and workspace bindings, then
performs a bounded initialize handshake with the installed server. It does not
execute an edited registration, create a plan database or call a model. Provider
discovery reuses the canonical preflight and reports executables as offered,
not verified. No provider runs during init.

The read-only cockpit can be started separately with:

```text
status_server --workspace /absolute/project --runs /absolute/project/.codex-harness/runs
```

Its default URL is `http://127.0.0.1:7480`. Init does not start that service.
To begin planning, open a chat in the initialized folder and say
"Let's plan my idea with Seldon".
