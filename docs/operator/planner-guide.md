# Planner guide (v9, 2026-10-18)
**What a finished plan leads to.** The user's full vision is delivered for real, never a mocked or basic version. Every requirement traces from vision to code to a passing proof. Docs are generated from the plan and never stale. Workers can verify their own work from the plan alone. A final review confirms the product achieves the locked vision, while the user supervises as little as possible. Efficiency never replaces quality: ask the questions the vision genuinely needs, and never invent the user's intent.
**Read only this guide and the user's project.** Never read Harness source or Harness design docs.
**Recovery ladder when stuck:**
1. Re-run `p status`. Its `Next:` line is authoritative.
2. `p <command> --help`.
3. Targeted diagnostics on the reported error.
4. Only then a bounded search.

In PowerShell define `function p { uv run --no-sync --frozen --offline python -m codex_harness.core.plan_cli --workspace "$W" @args }`.
Set `$W` to an existing project directory. Every text reply ends with the next action; replace angle-bracket values.
`--json` returns one JSON object (`text`, `status`, `data`, `next`, `isError`); its **text** ends with `Next:`. Creations return `data.id`.
For `node`/`decision`, use global `--json` before the subcommand or `--machine-json` after it: their local `--json` is input.
`p show <node-id> [--body --test <name>]` reads node detail, links and protected test functions; named source is capped at 8 KB.
`p nodes [--kind K]` reads `id · kind · title` listings. `p status --brief` shows phase, gate, quality, open counts and `Next:`. `p preflight [--provider codex --provider claude]` (or MCP `harness_preflight` with optional `providers`) reports executable discovery and workspace/plan/template existence; default selection is Codex, Claude and Copilot. It creates nothing, even for a missing workspace, and does not open the plan database. Exit 0 means the diagnostic returned, including missing tools; invalid input exits 2. Use global `--json` for structured observations. Authentication, network, effective protections, plan validity and actual worker success remain unknown/not proven. It reads no credentials/config, launches no provider, and neither changes nor satisfies quality/readiness/publication gates.
Re-disposition a closed finding with `resolve` again: resolved / deferred (real gap not fixed now) / rejected (finding is wrong, cite evidence). `p readiness [--beads B1,B2 | --sample N --seed S] --reviewer @reviewer.json ...` reviews exact worker packets (default: all ≤8 beads, otherwise 5). Readiness is advisory in quality/status/render/publish; blockers use `findings`/`resolve`. Above 12 beads × 2 reviewers requires `--max-calls N`.
Scope alone is never a reason to reject.
Success exits 0; input/gate/audit refusal or any failing quality check exits 2. `p status` resumes without changing truth.
| Phase | Commands in order (`p` prefix); exit gate |
| --- | --- |
| align | `start --idea "..." --name "..."`; `card --vision "..." --users "..." --first-moment "..." --scope-in "..." --scope-out "..." [--assumption "..."]`; native MCP `harness_record_confirmation gate=align`; `advance --to expand`. Fresh displayed-gate/revision response. |
| expand | `ask --question "..." [--node V1]`; `answer --id Q --text "..."`; `assume --text "..."`; `resolve-assumption --id A --confirmed --rationale "reason" --by planner` (or `--rejected`); `--by` is required: `user`, `planner` or `proxy:<name>`. `node --json "@uplifts.jsonl"`; `advance --to vision-lock`. Capture accepted/rejected/deferred ideas. |
| vision-lock | `diagram --kind user-journey`; native MCP `harness_record_confirmation gate=vision-lock`; `advance --to features`. Fresh displayed-gate/revision response. |
| features | `node --json "@features.jsonl"`; `diagram --kind feature-map`; `advance --to requirements`. Features/stories mapped; no additional store gate. |
| requirements | `node --json "@requirements.jsonl"`; `advance --to tech-stack`. Every requirement has a test descendant. |
| tech-stack | `decision --json "@decision.json"`; `decision --id D1 --choose "<option>" --rationale "<reason>"`; `diagram --kind decision-map`; `advance --to plan`. Decisions recorded; no additional store gate. |
| plan | `node --json "@beads.jsonl"`; `compile --template template --out manifest.json`; `diagram --kind bead-dag`; `advance --to roundtable`. Compile renders spine docs into the template and checks every listed requirement file; requirement-to-bead coverage and valid manifest. |
| roundtable | `roundtable [--reviewer "@reviewer.json" ...] [--max-rounds 2]`; `findings`; `resolve --id F --resolution "fix/reason" [--resolved | --deferred | --rejected]` (`--deferred` for real gaps not fixed now; `--rejected` only for wrong findings); `audit --oracle oracle`; `quality`; `advance --to publish`. Open questions block exit; audit/review must match the manifest before publication. |
| publish | `render [--out plan/docs]`; `publish`; `worker-start --expected-revision <published_revision> --workers @workers.json`. Matching template, audit, reviews and publication required. Workers implement; the planner steers. |
| execute | `worker-status --job-id <job-id>`; `status`; `impact` after edits; `advance --to accept` only after actual workers/integrated proofs are assessed. Product checks alone do not prove worker delivery. |
| accept | `accept-scenarios --derive`; `accept-run --subject product --repo <dir>`; `accept-review --repo <dir>`; resolve findings; `accept --statement "actual words" --by user`. All scenarios pass at the current integrated revision, no open acceptance findings. |

Native confirmation requires MCP2025-06-18, client `elicitation` capability and initialization. The server displays the full stored card and collects explicit approval, complete words and conditions; conditions block credit. Decline, cancel, timeout, disconnect or changed revision grant none. Client support is conditional, not established by source tests. CLI quotes/`--by` labels refuse with `plan-confirmation-origin-unavailable`; they cannot substitute for the native response. Plan-to-roundtable confirmation remains unchanged.
`workers.json` is an array of exact `{bead,provider,model,effort}` objects, one per published bead, using approved existing capacity and canonical provider names. MCP equivalents are `harness_worker_start expected_revision=<revision> workers=<array>` and `harness_worker_status job_id=<id>`. Starts return one durable job per publication; reconnect/status never relaunches. Missing terminal evidence and current liveness remain unknown. No retry, install or permission expansion is implied.
Nodes require `id`, `kind`, `title`, `text`, `parents` (id array), and `fields` (object).
`accept-run` requires `--subject product|oracle`: only product runs prove delivery, satisfy acceptance, or permit rebind; oracle runs are scenarios verified against the reference implementation (dry run). Legacy runs without a subject are product only when `<repo>/../evidence/run.json` exists; otherwise oracle.
Acceptance: `accept-scenarios` lists; `--input @scenarios.json` saves `{id,title,given,steps,expect}` for `first-moment` and every `scope-N`; optional `nodeIds` bind other spine nodes. Steps are shell strings or `{command,expect}`; `expect` supports `stdout`/`stderr`, `stdoutRegex`/`stderrRegex`, and `exit` (default 0). Prose-only `--derive` results are `draft`, `executable: false`: supply steps and expectations. Edits retain scenarios and mark changed node references `stale`; save reviewed scenarios to refresh them.
Use `{repo}`, `{python}`, `{home}`, `{data}` in commands. Each scenario starts in an empty cwd with fresh HOME/data, never a seeded fixture. Vision fields can supply structured `first_user_moment`/`scope_in` items or `acceptance` entries with these ids.
`accept-review --reviewer @codex-reviewer.json --reviewer @claude-reviewer.json` uses normal subscriptions; its findings appear in `findings` and use `resolve`. A code/scenario change requires a fresh run and review. `accept-rebind --repo <dir>` reruns all scenarios for `revision-algorithm-upgraded` or `legacy-unbound` (matching raw hash; audited node-binding back-fill); passing unchanged v1 products retain reviews/approval by references, preserving approver and proxy identity. Product, plan, scenario or vision changes require fresh acceptance.

Interrupted ordinary acceptance reviews retain their registered attempt and sealed original packets/returns under `.codex-harness/acceptance-review-attempts/`. Find attempt IDs in the interruption error or status JSON's `acceptance.reviewAttempts`; stored-return counts do not imply eligibility. Recover the existing aggregate without launching providers with `accept-review --repo <same-dir> --resume <id>` (MCP: `harness_accept_review` with `repo` and `resume`). Every original slot must be complete, valid, nonmocked and bound to the same source, candidate, context and observed native model/session.

Unknown telemetry, tool/error events, missing protected bodies, overflow reshards, stale or changed receipts refuse recovery. Missing work is never automatically relaunched; do not move or edit originals. Repeated successful resume returns the same review/findings and preserves dispositions. External saved reviews without original Harness registration cannot be imported. Questions, findings, phase and user approval gates remain in force.
Delegated acceptance uses `--by proxy:<name>` (or `opus-user-proxy`); status records `isProxy: true`, never real-user acceptance. Only record actual user words with `--by user`.
Quality distinguishes **planned vs proved** with `evidence: planned|executed`: a clean-state first-moment scenario or linked e2e (`firstMoment: true`, `cleanState: true`) is planned; only a passing current acceptance result is proved. Draft/stale scenarios are not planned. Missing-file cases must not defeat first use.
Explicit `acceptance` spine nodes need a schema change by the spine owner; until then use vision fields or the scenario table through `accept-scenarios --input`.
`node --json` accepts a literal object, `"@file.json"`, or `"@file.jsonl"` (atomic batch; order independent). Parents must exist and have these kinds; a vision is unique. Nonvision nodes should retain an intent chain.

| Kind | Allowed parent kinds |
| --- | --- |
| vision | none |
| feature | vision |
| story | feature |
| requirement | story, feature |
| decision | vision, feature, requirement |
| design | requirement, decision |
| bead | requirement, design, decision |
| test | requirement, bead |

Bead fields: disjoint `ownedFiles`, `dependsOn` bead ids (acyclic), `proofModule`, and `lessons` decision ids (possibly empty). The cross-bead contract check warns unless a consumer proof imports the producer's owned module or the requirement declares `fields.contractTest`; pin the interface and make the consumer depend on the producer.
Link tests to requirement ids through `parents`; test fields are `module` and `level` (`unit` or one `e2e` integration test).
Vision fields must include `validation` (Python arguments preceding the module) and protected `requirementFiles` paths.
TypeScript command runner example: `"verification":{"runner":"command","argv":["node","--test","{proof}"],"testDir":"tests","proofGlob":"*.test.ts"}`; see [verification runners](../architecture/verification-runners.md).
Put executable requirement tests in `<workspace>/template`; assert observable results, including meaningful failures. Include importable implementation stubs failing with `NotImplementedError`; workers own implementation, never protected tests.
`audit --prospective` checks unittest collection, red stubs, assertions/imports and requirement traceability without an oracle; it does not prove satisfiability. Unsupported command collection contracts refuse honestly.
Publish that choice explicitly: `publish --proof-level prospective --reason "why no oracle"`; the prospective label remains visible. Workers must block impossible protected tests with evidence.
Prospective quality warns: integrated worker validation and clean-state acceptance are the first real proof. A later oracle audit upgrades the proof level; republish the stronger snapshot.
`audit --oracle oracle` defaults to Git HEAD when available; otherwise it copies the template and checks every oracle bead plus integration. Use `--baseline template` explicitly or `--baseline-ref <git-ref>`; output records the baseline. Repair the reported proof, then retry audit.
The oracle directory supplies every affected owned file; audit overlays protected tests and oracle into temporary copies.
After publication, affected beads come from the published-to-current spine impact; HEAD must be the intended integration baseline.
An integration test proves the complete user path; absent integration proof is reported explicitly. Failing modules show the first 40 output lines and a full-output artifact under `state/runs`.
Use `remove-node --id X` only without children/references. `impact --since <manifest-revision|file>` predicts before publication; bare `impact` uses publication or the last compiled revision. After edits run `impact`, `compile`, `audit`, `quality`; return to an earlier phase and reapprove when needed.
After vision lock, V1 edits limited to `fields.validation` and `fields.requirementFiles` keep the lock. Edits to V1 text or understanding-card fields stale it; native `harness_record_confirmation gate=vision-lock` renews the displayed understanding before further advance. A client-reported response is not natural-person authentication; never relabel proxy or quoted words as owner approval.
Worked mini example: **26 CLI calls**, two beads (B depends on A). Save these files in the checkout (CLI JSON inputs use cwd).
Create project `template/tests_cli` with `test_one.py`, `test_two.py`, `test_end_to_end.py` using unittest assertions:
`from mini_one import inc; assert inc(2) == 3`; `from mini_two import twice; assert twice(2) == 6`; integration asserts `twice(inc(1)) == 6`.
Wrap each assertion in a `unittest.TestCase` test method. Stubs: `inc(n)`/`twice(n)` raise `NotImplementedError`.
Create project `oracle/mini_one.py`: `def inc(n): return n + 1`; `oracle/mini_two.py`: `from mini_one import inc` then `def twice(n): return 2 * inc(n)`.
A new project needs no Git commit: the template baseline runs the oracle against protected proofs in temporary copies.
Save `features.jsonl` (V1 keeps the card fields and adds compilation metadata):
```jsonl
{"id":"V1","kind":"vision","title":"Mini","text":"Increment then double","parents":[],"fields":{"vision":"Increment then double","users":"CLI user","first_user_moment":"Get 6","scope_in":"Two functions","scope_out":"GUI","assumptions":[],"validation":["-m","unittest","discover","-s","tests_cli","-p"],"requirementFiles":["tests_cli"]}}
{"id":"F1","kind":"feature","title":"Arithmetic","text":"Increment then double","parents":["V1"],"fields":{}}
{"id":"S1","kind":"story","title":"Calculate","text":"As a CLI user I get 6","parents":["F1"],"fields":{}}
```
Save `requirements.jsonl`:
```jsonl
{"id":"R1","kind":"requirement","title":"Increment","text":"inc(2) returns 3","parents":["S1"],"fields":{}}
{"id":"R2","kind":"requirement","title":"Double increment","text":"twice(2) returns 6","parents":["S1"],"fields":{}}
{"id":"T1","kind":"test","title":"Increment proof","text":"Observed increment","parents":["R1"],"fields":{"module":"test_one.py","level":"unit"}}
{"id":"T2","kind":"test","title":"Double proof","text":"Observed composed result","parents":["R2"],"fields":{"module":"test_two.py","level":"unit"}}
{"id":"TE","kind":"test","title":"Integration","text":"Complete arithmetic path","parents":["R1","R2"],"fields":{"module":"test_end_to_end.py","level":"e2e","firstMoment":true,"cleanState":true}}
```
Save `beads.jsonl`:
```jsonl
{"id":"A","kind":"bead","title":"Increment","text":"Implement inc","parents":["R1"],"fields":{"ownedFiles":["mini_one.py"],"dependsOn":[],"proofModule":"test_one.py","lessons":[]}}
{"id":"B","kind":"bead","title":"Double","text":"Implement twice via inc","parents":["R2"],"fields":{"ownedFiles":["mini_two.py"],"dependsOn":["A"],"proofModule":"test_two.py","lessons":[]}}
```
Decisions may include `chosen` and `rationale`, or record them with `decision --id D1 --choose Python --rationale "Stdlib meets the scope"`.
Save `decision.json`: `{"id":"D1","question":"Runtime?","options":[{"name":"Python","pros":"Stdlib","cons":"Requires Python"}],"recommendation":"Python","parents":["V1"]}`.
Save `codex-reviewer.json`: `{"name":"codex-reviewer","provider":"codex","model":"gpt-6.1-sol","effort":"medium"}` and `claude-reviewer.json`: `{"name":"claude-reviewer","provider":"claude"}`.
Run in order; replace `<Q>` with the id printed by `ask`. At each native step, review and answer the full displayed card; no quoted CLI approval is available.
```powershell
p start --idea "Increment then double" --name Mini
p card --vision "Increment then double" --users "CLI user" --first-moment "Get 6" --scope-in "Two functions" --scope-out GUI
# Native MCP: harness_record_confirmation gate=align; answer the displayed card.
p advance --to expand
p ask --question "Use Python stdlib?" --node V1
p answer --id "<Q>" --text "Yes, per the supplied mini scope."
p advance --to vision-lock
p diagram --kind user-journey
# Native MCP: harness_record_confirmation gate=vision-lock; answer the displayed card.
p advance --to features
p node --json "@features.jsonl"
p diagram --kind feature-map
p advance --to requirements
p node --json "@requirements.jsonl"
p advance --to tech-stack
p decision --json "@decision.json" --choose Python --rationale "Stdlib meets the scope"
p advance --to plan
p node --json "@beads.jsonl"
p compile --template template --out manifest.json
p diagram --kind bead-dag
p advance --to roundtable
p roundtable --reviewer "@codex-reviewer.json" --reviewer "@claude-reviewer.json"
p audit --oracle oracle
p quality
p advance --to publish
p publish
```
Real roundtables launch providers normally. **Test only:** add `--test-only-review-output response.json` with explicit reviewers to inject a finding array (e.g. `[]`) instead; never treat injected reviews as live evidence.
Review budget defaults to 2 attempts per reviewer per manifest revision; `--max-rounds N` changes it. Totals span revisions; optional `--max-total N` caps total invocations per reviewer.
A failed reviewer writes a diagnostic artifact with command, stdout, stderr and exit code; text/JSON show its path and first stderr lines.
Inspect that artifact, fix the reported failure, then retry only its `--reviewer`; other providers' latest successful same-revision runs remain valid.
Windows reviewer discovery prefers the newest desktop-bundled Codex executable; explicit provider overrides are preserved.
Quality fails without a passing audit or successful review for the current manifest; one successful provider warns about missing cross-subscription evidence.
`advance --to publish` and `publish` both refuse missing/stale audit or review with recovery; no `--force`. A score alone does not authorize publication.
`spine_revision` (`plan_revision` alias) hashes editable nodes; `manifest_revision` includes protected requirement files and is unavailable until compilable.
`published_revision` identifies the frozen worker manifest; `up_to_date` compares it to `manifest_revision`, so test edits invalidate evidence.
At vision-lock, the journey uses `first_user_moment` and `scope_in` until stories exist. An unchosen decision's `Next:` records a choice before advancing.
