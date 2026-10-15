# Planner efficiency: three matched water-cli runs (2026-10-14)

All three runs used the same brief and the same user-proxy answers (Opus as a labelled proxy, never the owner), a Codex Sol 6.1 planner at high effort, and real reviewers (Codex Sol 6.1 and Claude Haiku). Metrics come from each session's `events.jsonl`.

| | Run 1: drives the Python API, reads source | Run 2: guide v1 + `plan_cli` | Run 3: guide v2 + evidence gates |
|---|---|---|---|
| Wall time | 22.5 min | 11.1 min | 17.2 min (two real review rounds plus an oracle repair) |
| Commands | 53 | 19 | 22 |
| Exploration commands | 23 | 6 | 5 |
| Harness source/doc reads | 12 | 1 (accidental) | 1 (accidental `AGENTS.md`, self-reported) |
| Failed commands | 8 | 3 | 0 |
| Glue code | 617-line script | none | none |
| Input / output tokens | 4.08M / 38k | 1.45M / 16k | 2.12M / 22k |
| Test audit | pass | **blocked** (no git baseline) | pass (template baseline) |
| Cross-provider review | ran | **Codex invalid** (bare `codex` hit the npm `.CMD`: WinError 2) | 2 rounds; 10 findings, 7 resolved and 3 rejected with reasons |
| Quality / publish | 100 / published | 94 / **published despite missing evidence** | 100 / publish gated on audit and current-revision review |

## What changed between runs
- **Run 1 → 2:** `plan_cli` and the one-page planner guide (ffd1458b). The planner stops reading Harness source and stops writing glue code.
- **Run 2 → 3:** `planfix` (9627169b):
  - template-baseline audits;
  - reviewer failure artifacts and desktop `codex.exe` resolution;
  - keeping per-provider runs;
  - a round budget;
  - failure-specific `Next` hints;
  - quality and the publish gate requiring a passing audit and a successful current-revision review.

## Remaining rough edges (run 3 report; queued)
- An audit FAIL exits 0.
- The `--baseline` value is ambiguous (a directory versus `template` versus a git ref).
- Audit failures lack the underlying unittest errors.
- `ask --json` does not return the question id in `data`.
- `resolve-assumption` has no rationale field.
- The guide doesn't say which V1 edits need renewed approval.
- The start text names the MCP tool instead of the CLI command.
- Planners in this repo still read `AGENTS.md` first. A planner working in a user's project would not.

## Limits
These are local, single-plan measurements with a proxy user. They do not establish supervision savings against a human baseline or real-user alignment quality.
