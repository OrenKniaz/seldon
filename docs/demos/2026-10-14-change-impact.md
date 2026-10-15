# Demo: a vision change re-runs only the affected work (2026-10-14)

**Scope:** local dispatcher run with real Codex workers (Sol 6.1, `gpt-6.1-sol`, medium effort), on the shared tip at the time (`06be9003`). Live or served acceptance is **not claimed**.

## Scenario
1. The tip-split plan was compiled from `spine.json`. Bead A (money core) carries spec refs D1–D4 and R1–R3. Bead B (CLI) carries D1, D5, R4 and R5.
2. A real run was dispatched; both beads went to Codex.
3. As the planner, Opus revised decision **D3**, from "the tip is rounded half up" to "the tip always rounds UP to the next cent". It also revised requirement R2, the vision and plan docs, and the tests that prove R2.
4. `spine impact` reported: changed D3 and R2; affected beads **A only**; untouched **B**; affected test `test_money`.
5. The same run was resumed against the revised plan.

## Commands
```
python -m codex_harness.core.spine compile plan/spine.json plan/sample.json
python scripts/run_plan.py --plan plan --work-root runs --provider A=codex --provider B=codex \
  --executable A=<codex.exe> --executable B=<codex.exe> --model A=gpt-6.1-sol --model B=gpt-6.1-sol
python -m codex_harness.core.spine impact plan/spine.json plan2/spine.json
python scripts/run_plan.py --plan plan2 --work-root runs --resume runs/<run> ...same provider flags...
```

## Results
| Step | Outcome | Time |
|---|---|---|
| Initial run | `plan-validated`; A and B real Codex; integrated proofs passed | 114 s |
| Resume #1 | `plan-failed: A integrated validation failed`; B blocked | 50 s |
| Resume #2 | `plan-failed: A integrated validation failed` | 42 s |
| Resume #3 | **`plan-validated`**: A re-dispatched, **B reused** and re-validated, integration proof re-run | 43 s |

**The two failed resumes were planner errors, not worker errors.** The revised `tests/test_money.py` still contained two older half-up expectations: a property formula and one table row. Codex implemented "round up" correctly every time. Harness refused to call the result validated and blocked B, which depends on A. Opus fixed the tests, checked them first against a correct round-up implementation, and resumed.

**Final candidate:**
- integration commit `db0b01b2dc2272eaba13a588c680891880da83f3`, tree `a3d2aaaf5245ee9fdb74ca522e2eac2411d6d050`;
- plan update commit `40633665` ("Plan revision bd3da4c7c2a8: update requirement files"; paths `tests/test_money.py`, `VISION.md`, `PLAN.md`);
- A's new spec refs include D3@77b74701 and R2@74c6d2f8;
- B's `tipsplit/cli.py` is unchanged since its original integration (`9503c98`).

**Independent check by Opus:**
- all tests pass on the integrated repo;
- `tipsplit 10.01 2 --tip 15` prints `$5.76` each. The tip of 150.15 rounds up to 151, so the total is 1152;
- no `reference/` or memory store is present in the repo;
- `didExecuteRealProvider: true`; `liveAcceptance.claimed: false`.

**Supervision:** zero manual edits by a human. Three driver-side resumes were caused by an incomplete planner revision. Elapsed worker time was about 4 minutes.

## Qualification (added after review)
This demo ran before candidate-bound integration. A failed post-merge validation left A's code on `integration`, so the final retry met code that was already correct (an empty change that still passed the guard). It therefore does **not** prove safe failure and retry. The repair is f8882ca5 (candidate-bound validation). The fresh demonstration is `docs/demos/2026-10-14-failure-retry.md`. The history above is preserved as recorded.

## Gaps found (queued)
1. **A test-only revision doesn't bump the plan revision.** `planRevision` comes from the spine only. Editing a test file without editing its spine node is not detected on resume; here it was worked around by also revising the `test_money` node. Fix: include the content of the requirement files in the plan revision.
2. **Planner "test the tests".** Before publishing a revision, check that the revised tests are internally consistent (for example against a reference or oracle where one exists), and flag tests that fail for beads outside the impact set. This would have caught the stale expectations before any worker ran.
