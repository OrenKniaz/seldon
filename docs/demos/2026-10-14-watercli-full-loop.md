# Demo: water-cli, the full 11-phase loop with a guide-only planner (2026-10-14)

**Scope:**
- A Sol 6.1 planner briefed to use only `docs/operator/planner-guide.md` and the `p` CLI. **Not strictly guide-only:** the replay3 and replay3c planners admitted reading `AGENTS.md`/`README.md` and querying memory before they loaded the reading boundary. No Harness source, architecture docs, tests or fixtures were read.
- real Codex workers;
- clean-state acceptance on the integrated build;
- a cross-provider acceptance review;
- a **proxy** acceptance.

The user answers and approvals came from Opus as a labelled user proxy (`opus-user-proxy`). **No real-user acceptance is claimed.** Status reports the approval origin as `recorded-unverified`.

Plan workspace: `_runs/watercli-replay3/project`. Integrated run: `water-cli-20261014T164206782744`.

**Evidence identities:**

| Item | Identity |
|---|---|
| Build source (Harness) | `d3378a33c6272937472e04576b83f36a612f0cc3` |
| Plan revision | `ac9575b841eb7acefd0ae06d859f33c2587e364ccd1055664119891811fb91df` |
| Product commit | `d68eba3b7011aceec6d673d45d010abb2fba1ef0` |
| Product tree | `3e143e029f62aa32def9ea99b16a625d1927d424` |
| Acceptance run | `89e876fa0aab4ebfa2a02b790c7aa92d` |
| Acceptance review | `0f282793073f4a518d8284f2cfda7fcd` |

This report makes no claim about the full Harness vision and no claim about savings.

## Outcome
**All 11 phases are complete.** Every phase from Align to Accept is ✓, with acceptance recorded by the proxy and `humanOriginVerified: false`.

| Step | Result | Planner or worker effort |
|---|---|---|
| Plan (replay3) | Quality 100, `first-moment-proof` passed, roundtable with Codex and Claude, published | 17 commands, 0 errors, 5 exploration |
| Repair after a dispatch failure (replay3b) | Docs packaged into the template, re-reviewed (`--max-total 3`), re-published | 16 commands, 0 errors |
| Build (Codex Sol 6.1 ×4) | `plan-validated`; integration passed | 8 commands, 0 errors, 197 s in total |
| Acceptance run on the build | 9/9 scenarios from a clean cwd, HOME and data | n/a |
| Acceptance review | Codex: 2 major gaps (R2 storage safety, R3 prior-day data). Claude Haiku: none. | n/a |
| Close the findings (replay3c) | 3 new scenarios. Acceptance **12/12 on the real build**; both findings resolved with evidence; clean re-review | 12 commands, 0 errors |
| Accept | Recorded as `opus-user-proxy`, `isProxy: true`, origin unverified | n/a |

**First user moment on the real build, from a clean install:**
1. `today` prints `0 ml (0%)`;
2. `add 250 ml` prints `Added 250 ml`;
3. `today` then prints `250 ml (12.5%)`.

This closes the water-cli gap where every test passed but the first use failed on a fresh machine.

## What dogfooding found: fixed, each now gated at plan time
1. **Rendered docs missing from the template.**
   - *Symptom:* dispatch crashed on `git rev-parse HEAD:PLAN.md` before any worker launched. Compile, audit, quality, review and publish had all passed.
   - *Fix (950fdb9f):* compile renders the spine docs into the template; compile, audit and publish refuse missing requirement files.
2. **`tests/` was hard-coded.**
   - *Symptom (confirmed):* the planner chose `tests_cli/`. The worker packet and prompt hard-coded `discover -s tests`, so all three parallel workers got a non-runnable command and correctly reported `blocked`.
   - *Correction (DOT source review):* in this run `tests_cli` was listed in `requirementFiles`. It was therefore included in the verification identity (`_claim_inputs` → `_content_identity` expands tracked blobs) and in the protected paths. An earlier claim here that the identity "bound nothing" was wrong for this run.
   - *Fix (d3378a33):* the test directory is derived from the manifest for commands, packets, identity and protected paths. Beyond the confirmed command defect, this hardens plans whose test directory is **not** listed in `requirementFiles`; no such plan has been observed. `tests/` plans keep byte-identical packets.
3. **"Rejected" overstated completeness.**
   - *Symptom:* the packaging-only repair closed 7 roundtable findings as `rejected`, several of them real coverage gaps.
   - *Fix (e4b0e829):* a `deferred` status plus a `deferred-findings` quality warning.
   - **This does not reclassify the 7 existing records.** Each one is being reconciled individually against the executed scenarios and review (lane `replay3d`).
4. **Acceptance status shape drift.**
   - *Symptom:* the workspace showed every scenario as "Not run" on an accepted plan.
   - *Fix (aa21a34f):* status carries the executed step records, and the page reads `lastResult`.

## Honest limits
- Both the old fixture flow and the guide-only flow passed every gate while carrying gaps 1 and 2. **Only a real dispatch exposed them.** Plan-time gates now cover both.
- The first-use clarification was **offered in the brief** (a proxy question), so this run proves the plan-time check and the clean-state scenario mechanism, not unprompted discovery.
- Workers were Codex-only. Claude dispatch beads are on hold pending the owner's decision on the PowerShell tool allowance (see DOT's hold).
- The Claude Haiku acceptance reviewer returned no findings where Codex found two real gaps. That is one sample, not a basis for a new default. A bounded reviewer comparison is proposed. Default settings are unchanged until the owner approves any change.
- `p accept` takes a free-form `--by`. The record never proves human origin.
