# Demo: real failure and retry under candidate isolation (2026-10-14)

**Scope:** local run with real Codex workers (Sol 6.1, `gpt-6.1-sol`, medium effort) on the code with candidate-bound integration (f8882ca5 and later). Driver script: `C:\Users\oren\claude-worktrees\_runs\demo_retry.sh`. Live or served acceptance is not claimed.

## Steps and results
| Step | What | Outcome | Time |
|---|---|---|---|
| 1 | Fresh run of tip-split (A and B on Codex) | `plan-validated` | 196 s |
| 2 | Plan revised (D3 becomes "tip always rounds up") with the revised tests DELIBERATELY left half-updated (one expectation changed, two stale), as a simulated planner error | `plan-failed: A rejected: no source change`. Integration received only the plan-update commit (`638e425`); no worker change and no failed merge landed. B was blocked | 65 s |
| 3 | Tests fully corrected; resume | `plan-validated`. A was re-dispatched from the clean integration head and really changed `tipsplit/money.py` (candidate `87666d94`, validated, then fast-forwarded). B was reused and re-validated. The integration proof was re-run | 68 s |

## The worker's reasoning in step 2 (verbatim)
> No source files changed: the rounding contract is contradictory.
> - `PLAN.md` specifies half-up; `VISION.md` specifies always-up.
> - Tests require both rules, so neither can pass all tests.
> … Implementation is blocked pending your rounding choice and resolution of the conflicting test expectation.

The worker also found a second planner error that the driver script had left in: `PLAN.md` was not updated.

## What this shows
- A contradictory revision is not papered over. The integration branch carries no failed attempt, and the retry starts from a clean head and makes a real change.
- The worker-side signal is valuable. Today the dispatcher records only `rejected: no source change` and **drops the worker's explanation**. It should be forwarded to the planner as a blocker message with its spec refs (follow-up queued).
- The real-run path in which a worker changes code, its candidate fails and integration stays unchanged is covered by stand-in tests. It did not occur in this real run, because the worker declined to change the code.

## Verification by Opus
- Integration history is free of failed merges.
- All tests pass on the final head.
- `tipsplit 10.01 2 --tip 15` prints `$5.76` each.
- A's final attempt has `changedPaths ['tipsplit/money.py']`.
