# Project-local activation (experimental host integrations)

Install the Seldon wheel into a persistent Python environment, then run its
`seldon` command from the product folder:

```text
seldon init --host all --dry-run
seldon init --host all --approval-mode approve
seldon init --host codex --approval-mode prompt
seldon init --host claude --approval-mode prompt
seldon init --host copilot-vscode --approval-mode prompt
seldon init --check
seldon init --remove
```

An optional path selects an existing project folder. Without `--host`, init
offers detected hosts; `--yes` selects all detected hosts without prompting.
Detection observes executable/app existence only. `--name` sets the heading in
the generated `SELDON.md`. Existing Seldon registrations pointing to another
workspace are refused unless `--yes` explicitly authorizes replacing that entry.
Init never changes global configuration, workspace trust or authentication.

The default project permission mode is `approve`, after a scope disclosure and
interactive consent. For unattended setup, `--approval-mode approve` is the
explicit consent choice; plain `--yes` does not grant permissions. Use
`--approval-mode prompt` to request tool prompts. The choice covers **all current
and future Seldon MCP tools**, including worker, acceptance and mail invocations.
It does not change application Align/Vision, execution, acceptance, audit, review
or publication gates. This is a host permission choice, not approval to execute
a product run. Existing explicit server modes survive ordinary re-init; explicit
mode flags change the server default while preserving stricter per-tool rules.

The cockpit shows saved project settings, not active chat permissions or pending
prompt state. Seldon's server tool-approval setting does not govern Codex app
chat-creation or browser requests; those use separate host controls.

The absolute interpreter from the wheel installation starts the canonical MCP
server with `--workspace` bound to the project's absolute path. Keep this Python
installation in place. Source-only and editable installations are refused. Moving
a project requires reinitialization; `--check` detects the old workspace binding.

The host writers use documented project configuration formats:

| Host | Project file | Next host action |
| --- | --- | --- |
| [Codex](https://learn.chatgpt.com/docs/extend/mcp#connect-codex-to-an-mcp-server) | `.codex/config.toml` | Open this folder and choose Trust |
| [Claude Code](https://code.claude.com/docs/en/mcp#project-scope) | `.mcp.json` and `.claude/settings.local.json` | Trust the folder; verify the saved server permission in a native chat |
| [Copilot in VS Code](https://code.visualstudio.com/docs/agents/run/approvals) | `.vscode/mcp.json` | Run **Chat: Manage Tool Approval**; use the Seldon server-group checkbox to approve, or leave it unchecked for prompts |

Permission formats were checked against primary documentation on 2026-10-25. Local
fixture checks establish generated registration and server behavior. Each host
integration remains experimental until its actual chat-to-Align journey is
observed. Claude Desktop chat and Copilot CLI are not covered by these writers.

Codex saves `default_tools_approval_mode` on the project Seldon server; existing
per-tool `approval_mode` overrides retain precedence. See the
[Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
New Codex registrations use `tool_timeout_sec = 900` as the per-request budget,
separate from permission approval and the server's 600-second confirmation wait.
Re-init preserves explicit user timeouts, including smaller budgets; those can
expire sooner. Removal retains saved budgets with unowned policy for manual cleanup.
The [documented key](https://developers.openai.com/codex/config-reference/) does
not establish that a native client actually waits ten minutes. The cause of the
observed 120-second cutoff remains unverified. Equivalent Claude Code and VS Code
timeout settings are **UNKNOWN** here; these writers emit no timeout overrides.
Claude approve mode merges `enabledMcpjsonServers: ["seldon"]` and the
[server-scoped rule](https://code.claude.com/docs/en/permissions) `mcp__seldon__*`
into `permissions.allow`; prompt mode uses `permissions.ask`. Existing narrower
ask/deny rules and disabled-server settings remain in place. Folder trust and
managed host policy can still prevent connection or require approval.
VS Code has no documented workspace-and-server-scoped autoapproval setting
established here: init writes no autoapproval setting. Its manual step and
effective policy remain **UNVERIFIED** until exercised in that host.

Existing JSON values are preserved; JSON formatting can change during a merge.
Strict JSON is required: JSONC/comments and duplicate keys are refused.
Codex tool policy tables are preserved; unsupported inline or unowned complex
layouts require manual merge rather than being rewritten.
Codex settings outside the owned block retain their text. Repeated init does not
rewrite unchanged files. Ownership uses `seldon-init v1` comments and an ordinary
server environment marker. Removal deletes only marked registrations, generated
guidance and ignore entries, preserving plan data and unrelated settings.
Permission policy ownership cannot be established, so remove retains Claude
settings and Codex policy as unowned configuration for manual cleanup. It does
not restore a prior permission mode. Remaining host configuration files are retained.

`--dry-run` prints full proposed files and writes nothing. Those files may contain
existing configuration, so review its output locally. Normal output does not dump
unrelated host settings. Preparation validates every target before writing;
individual file replacements are atomic. An I/O failure during multi-file writing
can leave a partial setup: fix that failure and rerun the same command.

`--check` reports saved permission modes, compares registrations, ownership markers
and workspace bindings, then
performs a bounded initialize handshake with the installed server. It does not
execute an edited registration, create a plan database or call a model. Provider
discovery reuses the canonical preflight and reports executables as offered,
not verified. No provider runs during init. A successful registration/initialize
check does not establish active host permissions: effective policy is **UNKNOWN**.

The read-only cockpit can be started separately with:

```text
status_server --workspace /absolute/project --runs /absolute/project/.codex-harness/runs
```

Its default URL is `http://127.0.0.1:7480`. Init does not start that service.
To begin planning, open a chat in the initialized folder and say
"Let's plan my idea with Seldon".
