# Real Codex / Harness plan-mode check — 2026-10-14

**Result: NONPASS / blocked at the real Codex client approval boundary.**
The real server initializes and lists its tools, but no planner call reaches
the server. All three permitted real runs were used. A zero CLI exit code does
not mean plan mode worked. There is no returned banner or understanding card.

Assignment: `C:\Users\oren\claude-worktrees\_assignments\livemcp.md`.
Worktree: `C:\Users\oren\claude-worktrees\v3-livemcp`. Branch: `codex/claude-v3-livemcp`.
Owner surface: the planner MCP boundary and this demo. Driver Claude Opus owns
assignment review. No commits, pushes, user configuration edits, worker launches,
confirmation or phase advancement. No scope expansion into client implementation.

## Method and provenance

CLI: `C:\Users\oren\AppData\Local\OpenAI\Codex\bin\9614f1d9b40d9c4f\codex.exe`,
version `codex-cli 0.162.0-alpha.17.2`, newest bundled binary available here.
Model `gpt-6.1-sol`, reasoning effort `high`. Help was checked with
`codex.exe exec --help` and `codex.exe mcp --help` before launching.

Per-invocation overrides configured the prepared worktree Python environment.
Each temporary `mcp_tap.py` imports the real
`codex_harness.core.mcp_planner.main` in that interpreter. Its stdin/stdout taps
copy parsed wire messages to an evidence file and delegate stream operations
and UTF-8 reconfiguration to the original streams. The server's JSON-RPC
payloads and dispatcher were unchanged. This is temporary diagnostic
instrumentation under ignored `state/`, not a product adapter or simulated server.
The launcher is `C:\Users\oren\claude-worktrees\v3-livemcp\state\runs\2026-10-14-livemcp\run_live.py` (its final version runs attempt 3;
do not invoke it again under this assignment).

`cli-events.jsonl` is unmodified Codex stdout. `mcp-wire.jsonl` contains actual
server/client JSON-RPC traffic tagged `diagnostic.mcp_wire` and
`source=real-server-stdio`. Each `events.jsonl` concatenates those two files in
that order, with diagnostic records explicitly labelled. It is not claimed to
be a chronological CLI-only stream. `run.json` holds argv, workspace, version,
exit status and before/after configuration hashes; stderr is in `stderr.log`.
No `tools/list` response was inferred from model prose.

All attempts retained the `read-only` shell sandbox. Run 1 used the default
MCP approval policy. Run 2 tried per-tool `approve`; run 3 tried per-tool `auto`
only for the two requested operations. Both settings are described in the
[official MCP configuration reference](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
Those overrides did not resolve the observed refusal in this environment.
The exact client policy precedence/root cause remains unproved. The read-only
sandbox was a diagnostic choice, not an assignment requirement, and may have
contributed to the refusal of mutating planner operations. Default or
workspace-write sandbox behavior was not verified within the three-run budget.
Attempt 1 stopped after the rejected start call; attempts 2 and 3 attempted both
calls, and both were rejected.

## Acceptance checks from captured events

| Check | Result | Actual evidence |
| --- | --- | --- |
| 1. Server started and tools listed | PASS | Every wire capture contains real initialize request/reply, notifications/initialized and tools/list request/reply with all 20 tool definitions. |
| 2. Start and understanding card called | NONPASS | Run 3 records both client attempts, each status failed and result null. Zero tools/call requests reached the server in any run. |
| 3. Banner, status and confirmation prompt returned | NONPASS | No successful tool response; no server-returned banner, status line or card. |
| 4. No confirmation or advance | PASS for absence only | Neither the CLI completed-call records nor the server requests contain either operation. Canonical gate enforcement was not exercised in this live session. |
| 5. Plan store exists | NONPASS | No temporary project contains .codex-harness/plan.sqlite3. |

The precise client error, quoted verbatim from each failed call's
`item.error.message`, was:

```text
MCP tool call requires approval, but approval policy is never
```

**Exact banner and card text: unavailable.** Neither was returned by a tool.
Quoting source templates, arguments, a synthetic store reply, or the model's
last message as real tool output would fabricate the requested proof. Run 3's
last agent message formats the client error as a tool-style JSON object, but
the actual CLI events have `result: null`; that model-authored object is not
a server result.

## Fixes and next action

No changes to `mcp_planner.py`, `planner_views.py`, or their tests. No handshake,
JSON Schema, UTF-8 decoding, stdout-flushing or store error was observed.
The server accepted the client's initialization and returned its tool
definitions without a reported transport error. Emoji banner transport
was not exercised. Changing mutating tools to claim read-only behavior would
misrepresent their contract and is not an acceptable repair.

This demo is the compact discovery/handoff surface permitted by the assignment:
client approval refusal is blocking; likely owner is the Codex invocation/policy
boundary. The driver should inspect effective MCP approvals and sandbox policy,
then authorize further live usage if appropriate. Exit check: both real calls
succeed, exact banner/card are captured, store remains in align with zero
approvals, and no confirmation/advance is called. No fourth run was made.

Python 3.12.7 was prepared using `uv --frozen --offline`. Onboarding health
passed interpreter/tool detection but warned repo wiring was absent; wiring
was not changed for this isolated direct-MCP check. Canonical memory recall
returned `no_evidence`, with stale CASS and no relevant hits; historical memory
was not used to establish any live result.

## Repository validation

Required command:

```powershell
uv run --frozen --offline python -m pytest tests/python/core -q -p no:cacheprovider
```

Result: PASS, **333 passed in 215.13s (0:03:35)**, exit code 0.

```powershell
uv run --frozen --offline ruff check src/codex_harness/core/mcp_planner.py src/codex_harness/core/planner_views.py tests/python/core/test_mcp_planner.py
```

Result: PASS, `All checks passed!`. No Python files changed.

User configuration SHA-256 before and after each run:
`d66ca3c128b5084712fba7790c587c907200e53557a70cca0c15cab04e04482d`.
Only the new demo is a source-controlled worktree change; raw evidence stays
under ignored `state/runs/2026-10-14-livemcp`.

## Run 1

Workspace: `C:\Users\oren\AppData\Local\Temp\livemcp-water-20261014-ypqj779l`. CLI exit: `0`.

Exact argument vector passed to `subprocess.run` (JSON strings preserve TOML
quoting and paths; stdin was `subprocess.DEVNULL`, cwd was `C:\Users\oren\claude-worktrees\v3-livemcp`):

```json
[
  "C:\\Users\\oren\\AppData\\Local\\OpenAI\\Codex\\bin\\9614f1d9b40d9c4f\\codex.exe",
  "exec",
  "--json",
  "--color",
  "never",
  "--ignore-user-config",
  "--ephemeral",
  "--skip-git-repo-check",
  "-s",
  "read-only",
  "-m",
  "gpt-6.1-sol",
  "-c",
  "model_reasoning_effort=\"high\"",
  "-c",
  "mcp_servers.harness_planner.command=\"C:/Users/oren/claude-worktrees/v3-livemcp/.venv/Scripts/python.exe\"",
  "-c",
  "mcp_servers.harness_planner.args=[\"C:/Users/oren/claude-worktrees/v3-livemcp/state/runs/2026-10-14-livemcp/mcp_tap.py\", \"--workspace\", \"C:/Users/oren/AppData/Local/Temp/livemcp-water-20261014-ypqj779l\"]",
  "--cd",
  "C:\\Users\\oren\\AppData\\Local\\Temp\\livemcp-water-20261014-ypqj779l",
  "Use only the harness_planner tools. Start Harness plan mode for this idea: 'a tiny CLI that tracks daily water intake'. Call harness_plan_start, then harness_understanding_card with your understanding, then STOP and show me the complete outputs of both tools exactly as returned, including status lines, phase trackers, banner and card. Do not record any confirmation. Do not call harness_advance. Do not implement anything, run shell commands, or change configuration."
]
```

Observed: {"run": 1, "toolsListed": 20, "attempts": ["harness_plan_start"], "successfulCalls": 0, "wireToolCalls": 0, "confirmationOrAdvance": 0, "storeExists": false, "configUnchanged": true}

Evidence directory: `C:\Users\oren\claude-worktrees\v3-livemcp\state\runs\2026-10-14-livemcp\run1`.

- `events.jsonl` SHA-256: `8f7e91feb2e08add50a564537442c978ff1650394f936f8013764534001a18f6`
- `cli-events.jsonl` SHA-256: `c1e3065b43f657abefb681ecaf750959224eb36663216fd5e66f59d4db2b810c`
- `mcp-wire.jsonl` SHA-256: `b73f6bb5cfa7effc12559e1e13c18f86569ec8e5d2e4903e50f30ce66863cdb9`

## Run 2

Workspace: `C:\Users\oren\AppData\Local\Temp\livemcp-water-20261014-5zdcmksn`. CLI exit: `0`.

Exact argument vector passed to `subprocess.run` (JSON strings preserve TOML
quoting and paths; stdin was `subprocess.DEVNULL`, cwd was `C:\Users\oren\claude-worktrees\v3-livemcp`):

```json
[
  "C:\\Users\\oren\\AppData\\Local\\OpenAI\\Codex\\bin\\9614f1d9b40d9c4f\\codex.exe",
  "-c",
  "mcp_servers.harness_planner.tools.harness_plan_start.approval_mode=\"approve\"",
  "-c",
  "mcp_servers.harness_planner.tools.harness_understanding_card.approval_mode=\"approve\"",
  "exec",
  "--json",
  "--color",
  "never",
  "--ignore-user-config",
  "--ephemeral",
  "--skip-git-repo-check",
  "-s",
  "read-only",
  "-m",
  "gpt-6.1-sol",
  "-c",
  "model_reasoning_effort=\"high\"",
  "-c",
  "mcp_servers.harness_planner.command=\"C:/Users/oren/claude-worktrees/v3-livemcp/.venv/Scripts/python.exe\"",
  "-c",
  "mcp_servers.harness_planner.args=[\"C:/Users/oren/claude-worktrees/v3-livemcp/state/runs/2026-10-14-livemcp/run2/mcp_tap.py\", \"--workspace\", \"C:/Users/oren/AppData/Local/Temp/livemcp-water-20261014-5zdcmksn\"]",
  "--cd",
  "C:\\Users\\oren\\AppData\\Local\\Temp\\livemcp-water-20261014-5zdcmksn",
  "Use only the harness_planner tools. Start Harness plan mode for this idea: 'a tiny CLI that tracks daily water intake'. Call harness_plan_start, then harness_understanding_card with your understanding, then STOP and show me the complete outputs of both tools exactly as returned, including status lines, phase trackers, banner and card. Do not record any confirmation. Do not call harness_advance. Do not implement anything, run shell commands, or change configuration."
]
```

Observed: {"run": 2, "toolsListed": 20, "attempts": ["harness_plan_start", "harness_understanding_card"], "successfulCalls": 0, "wireToolCalls": 0, "confirmationOrAdvance": 0, "storeExists": false, "configUnchanged": true}

Evidence directory: `C:\Users\oren\claude-worktrees\v3-livemcp\state\runs\2026-10-14-livemcp\run2`.

- `events.jsonl` SHA-256: `7215cf6ba555671e4b84e79caf2c86d0e4b4a86b5ce5503a6aa74ce325f79356`
- `cli-events.jsonl` SHA-256: `92f35e4cd81ef01eb6962407ee3b6db97f4518f1317a3c15f4b7696efa8705a9`
- `mcp-wire.jsonl` SHA-256: `ffd44240ab896936e1f38787c562e9b8d775e586cec1da658b6a4122e1e230f7`

## Run 3

Workspace: `C:\Users\oren\AppData\Local\Temp\livemcp-water-20261014-o_azo4bq`. CLI exit: `0`.

Exact argument vector passed to `subprocess.run` (JSON strings preserve TOML
quoting and paths; stdin was `subprocess.DEVNULL`, cwd was `C:\Users\oren\claude-worktrees\v3-livemcp`):

```json
[
  "C:\\Users\\oren\\AppData\\Local\\OpenAI\\Codex\\bin\\9614f1d9b40d9c4f\\codex.exe",
  "exec",
  "--json",
  "--color",
  "never",
  "--ignore-user-config",
  "--ephemeral",
  "--skip-git-repo-check",
  "-s",
  "read-only",
  "-m",
  "gpt-6.1-sol",
  "-c",
  "model_reasoning_effort=\"high\"",
  "-c",
  "mcp_servers.harness_planner.command=\"C:/Users/oren/claude-worktrees/v3-livemcp/.venv/Scripts/python.exe\"",
  "-c",
  "mcp_servers.harness_planner.args=[\"C:/Users/oren/claude-worktrees/v3-livemcp/state/runs/2026-10-14-livemcp/run3/mcp_tap.py\", \"--workspace\", \"C:/Users/oren/AppData/Local/Temp/livemcp-water-20261014-o_azo4bq\"]",
  "-c",
  "mcp_servers.harness_planner.tools.harness_plan_start.approval_mode=\"auto\"",
  "-c",
  "mcp_servers.harness_planner.tools.harness_understanding_card.approval_mode=\"auto\"",
  "--cd",
  "C:\\Users\\oren\\AppData\\Local\\Temp\\livemcp-water-20261014-o_azo4bq",
  "Use only the harness_planner tools. Start Harness plan mode for this idea: 'a tiny CLI that tracks daily water intake'. Call harness_plan_start, then harness_understanding_card with your understanding, then STOP and show me the complete outputs of both tools exactly as returned, including status lines, phase trackers, banner and card. Do not record any confirmation. Do not call harness_advance. Do not implement anything, run shell commands, or change configuration."
]
```

Observed: {"run": 3, "toolsListed": 20, "attempts": ["harness_plan_start", "harness_understanding_card"], "successfulCalls": 0, "wireToolCalls": 0, "confirmationOrAdvance": 0, "storeExists": false, "configUnchanged": true}

Evidence directory: `C:\Users\oren\claude-worktrees\v3-livemcp\state\runs\2026-10-14-livemcp\run3`.

- `events.jsonl` SHA-256: `c0e067cf08715c4f60b858f4195ed93f59f58ec53d622285fdecdfc5f986dcbd`
- `cli-events.jsonl` SHA-256: `9b8f8691c1c97a74510cd07f93ab4baabd8dc8b1774b42af03e5d1ebcc9175b4`
- `mcp-wire.jsonl` SHA-256: `f0d190f4734f1de16832aa8f1aa17428ec6f5935357abc3d1c17904ac802e802`

## Follow-up run by Opus: PASS
The blocker above was Codex's MCP tool approval, not Harness. With `approval_policy = never`, a headless run refuses any MCP tool that is not pre-approved. Codex supports per-tool `approval_mode = "approve"`; Oren's own config already uses it for the `cm` server. A single real run used per-invocation `-c` overrides only (no saved configuration), pre-approving ONLY the safe tools `harness_plan_start`, `harness_plan_status` and `harness_understanding_card`:

```
codex.exe exec --json --color never -m gpt-6.1-sol --skip-git-repo-check \
  -c "mcp_servers.harness_planner.command='<harness>\.venv\Scripts\python.exe'" \
  -c "mcp_servers.harness_planner.args=['-m','codex_harness.core.mcp_planner','--workspace','<project>']" \
  -c "mcp_servers.harness_planner.cwd='<harness>'" \
  -c 'mcp_servers.harness_planner.tools.harness_plan_start.approval_mode="approve"' \
  -c 'mcp_servers.harness_planner.tools.harness_plan_status.approval_mode="approve"' \
  -c 'mcp_servers.harness_planner.tools.harness_understanding_card.approval_mode="approve"' \
  --cd <project> "<start plan mode for 'a tiny CLI that tracks daily water intake'>"
```

**Results:**
1. The server started, and both tools were called and completed.
2. The output carried the status line `[plan … · phase align · open questions 0 · pending: approval:align]`, the phase tracker, and the banner `🧭 Harness Plan Mode: water-cli · Phase 1/11: Align`.
3. Codex wrote a real understanding card: vision, users, first user moment, scope in and out, and four assumptions, ending "Confirm, or tell me what's wrong."
4. `<project>/.codex-harness/plan.sqlite3` was created.
5. No confirmation was recorded and no phase advanced, so the gates held. `harness_record_confirmation`, `harness_advance` and `harness_publish` remained unapproved, which means a human must approve them in an interactive session.
