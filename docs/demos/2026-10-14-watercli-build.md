# Demo: water-cli built from its published plan by Codex and Claude workers (2026-10-14)

**Scope:** local dispatcher run (`scripts/run_plan.py`) of the published water-cli plan (`fixtures/dogfood-watercli`), with worker packets v1, efficiency telemetry and candidate-bound integration. The plan was made with Opus as a labelled user proxy, so real-user acceptance is NOT claimed, and live or served acceptance is not claimed.

## Result
**`plan-validated`, all workers real.** Final integration head: `cf93b43b4058`.

Earlier worker-efficiency figures used the old units: PowerShell calls were omitted, and `failedCommands` mixed denials, red test runs and other errors. The table below uses the corrected units, re-summarized from the real A–D event logs. Tokens are exact input (cached input) / output counts.

| Bead | Provider | Outcome | Commands | Denied | Wrong-selector/other failed | Red test runs | Exploration | Tokens: input (cached) / output | Time |
|---|---|---|---|---|---|---|---|---|---|
| A units | Codex Sol 6.1 | validated (reused on resume) | 2 | 0 | 0 | 0 | 1 | 69,932 (45,696) / 776 | 54 s |
| B storage | Claude Haiku 4.5 | validated | 5 | 2 | 0 | 0 | 2 | 66 (348,445) / 5,275 | 65 s |
| C report | Codex Sol 6.1 | validated (reused on resume) | 2 | 0 | 0 | 0 | 1 | 71,439 (53,888) / 561 | 58 s |
| D CLI | Claude Haiku 4.5 | validated | 17 | 5 | 1 | 2 | 7 | 202 (1,694,684) / 34,112 | 298 s |

A, B and C ran in parallel. D ran after them. The integration proof (`test_end_to_end`) passed on the final head.

## Harness bug found and fixed during the run
The first attempt failed bead B inside Harness: the efficiency parser assumed Claude `stream-json` messages are always objects, but real `system` events carry plain strings. Fixed in 08031ab8, and observation errors no longer fail a bead. Resume then kept A and C and ran B and D.

## Opus's independent check
- All tests pass on the final head.
- With a seeded data file, the product behaves as specified:
  - `add 250` and `add 8 oz` (237 ml);
  - `today` shows 487 ml / 2000 ml (24.3%);
  - `history 2` includes empty days;
  - `goal 1000` gives 48.7%;
  - `undo` twice, then "Nothing to undo today.";
  - `add 0` and `add 5 cups` give a clear error with exit code 2.

## Vision gap the tests did not catch
**The first user moment fails.** On a fresh machine, `water-cli add 250` prints `Error: File not found …`.
- The planner read the edge-case answer ("a missing data file gives a clear error, never silent data loss") literally, as "require a pre-seeded file". It never reconciled that with the locked first user moment ("log a glass of water and immediately see the total").
- The roundtable did not flag it, every test passes, and the product does not achieve its own vision on first use.
- This is what the planned final "did we achieve the vision" acceptance review (A8) must catch. It needs a scenario check of the first user moment against the real product, not just unit tests.

## Worker efficiency notes (D, Claude Haiku)
Of D's 17 command calls (3 PowerShell, 10 Bash and 4 Read), eight errored: five were guard/permission denials, two were red unittest runs during implementation, and one was a wrong selector. `deniedCommands=5`, `redTestRuns=2`, `failedCommands=1`, and `errorCommands=8` (the sum of those three categories). Exploration is an overlapping count of read/search calls, not another error category.

The five denials were:
- three PowerShell calls: one directory-changing compound command and two commands reported as containing multiple operations requiring approval;
- one multi-line `python -c` script reported as able to hide arguments from path validation;
- one `git -C … status --short` call requiring approval.

The wrong selector used pytest-style `::` syntax with unittest and exited 5 with `NO TESTS RAN`; it is an ordinary failed command, not a red test run. The packet should name the exact tool (Bash) and the accepted command forms, including the single-test form `python -B -m unittest tests.test_cli.Class.test`. On Windows, Claude should also be allowed the same command through its PowerShell tool. These are packet/tool-policy follow-ups; the telemetry repair does not change permissions.

Claude reports uncached input and cache-read input separately; Codex reports input including cached input. The token columns preserve each provider's reported units and are not directly comparable total-input measures.

B also had two PowerShell guard/approval denials omitted by the old units: five command calls, `deniedCommands=2` and `errorCommands=2`, with no red test runs or other failed commands. A and C each had two commands and no errors. Errors from editing tools are outside these command counts.
