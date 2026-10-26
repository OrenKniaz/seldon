# Planner guide

The full agreed vision must reach working product with real proof. Trace vision → requirements → beads → code → tests; generated docs reflect the structured plan. Workers should build without guessing, and final acceptance checks the locked vision. Efficiency never replaces depth, actual user decisions or meaningful failure cases.
Use this guide, public schema/help and the user's project; do not inspect Harness source to discover planning contracts. Detailed [handoff declarations](handoff-contract.md), [verification runners](../architecture/verification-runners.md) and [resume contract](resume-plan.md) expand the relevant steps.

**Start and recover.** Inspect `harness_plan_status` or `p status`; follow `Next:`, then command `--help`, targeted diagnostics and only then a bounded search. Status resumes without changing plan truth. Missing-plan reads create nothing; broken reads and unavailable telemetry remain unknown.
In PowerShell define `function p { uv run --no-sync --frozen --offline python -m codex_harness.core.plan_cli --workspace "$W" @args }`; set `$W` to the existing project. Replace angle-bracket placeholders.
Global `--json` returns `{text,status,data,next,isError}`; text ends with `Next:` and creations expose `data.id`. For node/decision input, use global `--json` before the subcommand or `--machine-json` after it. Success exits 0; input/gate/audit/quality refusal exits 2.
`p status --brief` shows phase, gate and quality. `p nodes --kind K` lists IDs/titles; `p show ID --body --test NAME` reads node detail and named protected test bodies (8 KB cap). A clean diagnostic is not proof of product behavior.

**Project identity.** MCP stays bound to its startup workspace; names or changed config cannot retarget it. Check `workspaceBinding` first. Except schema/preflight/status, supply `expected_workspace` as an independently known absolute project path. Missing/invalid/conflicting assertions refuse before store/provider/native-card access; explicit mismatch also refuses discovery. Never copy an unexpected server path to bypass a hold.
Caller workspace assertions are unauthenticated, not host identity or approval. Host cwd and connection reload remain unverified; use the correctly bound connection. CLI keeps explicit `--workspace`.

**Caller-mediated chat confirmation.** A declined, cancelled, missing or unavailable native confirmation grants nothing and offers a fresh `chatConfirmation` question. Alternatively call `harness_prepare_chat_confirmation gate=align|vision-lock`; execution uses `gate=execution` with an explicit nested `execution` proposal containing `expected_revision`, every exact worker tuple and `call_cap` (optional `max_parallel`/`timeout`). Wait for a separate actual external chat reply to that displayed question: `Approve`, `No`, `Cancel`, or `Changes: <text>`. Record its literal bytes through `harness_record_chat_confirmation` with `gate`, `request_id`, `expected_revision`, `quoted_message`, its UTF-8 `quote_sha256`, and an `actor_label`. No separate approve or origin field is accepted. Never turn the preceding refusal or silence into a new answer. Provenance is `chat-confirmation`, caller-mediated: source, recency and human identity are unverified; the hash binds the supplied quote only. Recording a decision launches nothing. Use a separate advance or exact matching worker-start/resume after approval; negatives, conditions, stale/replayed or mismatched replies grant no credit. Native confirmation remains available with its existing provenance. Final acceptance and all other gates remain unchanged.
`p preflight --provider codex --provider claude` / `harness_preflight providers=[...]` observes executables and workspace/plan/template existence without database/config/credential reads, creation or provider launch. Default providers: Codex/Claude/Copilot. Missing tools can still return diagnostic exit 0; invalid input exits 2. Authentication, protections, network, plan validity and worker success remain unproved; no planning gates clear.

**Short turns and schema first.** Work one feature through requirements → tests → bead; finish each turn with the stored-count status. Read `p schema --kind K` / `harness_schema kind=K` at first use. `node --json @feature-nodes.jsonl` and `harness_upsert_nodes nodes=[...]` share the atomic mixed-node writer; validation refusal saves none. In MCP, a completed write with `statusAvailable=false` needs read-only inspection, not replay. Discover batch help with `harness_schema tool=harness_upsert_nodes`; single edits use `harness_upsert_node`. ≤8 calls per stage is a measurement target, never a cap or quality verdict.
Every node has `id,kind,title,text,parents,fields`; parents are unique existing IDs (batch order is independent), and vision is unique. Preserve accepted intent and current IDs. Schema supplies allowed parent kinds, prerequisites and examples; examples are planned content, not proof or approval.
Vision schema names users, first moment, scope, quality and Done. Primary Done is `done_feels_like` (aliases `doneFeelsLike`, `done`); `done_means` retains additional detail. The canonical request, full node detail and cockpit preserve full words; the native card is a bounded summary linking to harness_show_node: absent is `Not recorded`, null `Not specified`, and false/empty values do not fall back to other text.
Before compile, vision fields need `validation:["-B","-m","unittest","discover","-s","tests","-p"]` and `requirementFiles:[]` (or extra protected relative file/directory paths). These are not required at initial vision authoring. Paths are relative to the product/template checkout: `tests`, not `template/tests`. Read vision/compile schema, merge metadata into the existing complete node and retain all other fields.
Test nodes link requirements/beads via parents and declare `module` plus `level:unit|e2e`; one e2e selects the integration suite, whose actual execution is still required for proof. Beads declare disjoint `ownedFiles`, acyclic `dependsOn`, `proofModule`, and `lessons` decision IDs (empty allowed). For cross-bead contracts, consumer proofs should import the producer's owned module or declare `fields.contractTest`; pin the interface and dependency.
At first test/proof authoring, prepare executable protected tests and importable failing stubs in workspace/template before compile/review; node records do not create files. A module CLI needs its planned entry: for `python -m tally`, provide `template/tally/__init__.py` and `template/tally/__main__.py` with a failing entry stub. A simple `template/app.py` containing `raise NotImplementedError('planned CLI')` supports a direct subprocess proof invoking `[sys.executable, '-m', 'app']` and asserting the real result; no imported `main()` API is required. This example does not promise arbitrary helper/control-flow support. Stubs are planning skeletons, not product implementation. Zero review findings do not replace audit collection, attributable red-stub failures or interface assertions.

| Phase | Public commands (`p` prefix unless MCP named); exit condition |
| --- | --- |
| align | `start --idea "..." --name "..."`; `card --vision "..." --users "..." --first-moment "..." --scope-in "..." --scope-out "..."`; native `harness_record_confirmation gate=align`; `advance --to expand`. Fresh displayed-revision response. |
| expand | `ask --question "..." --node V1`; `answer --id Q --text "..."`; `assume --text "..."`; `resolve-assumption --id A --confirmed --rationale "..." --by planner` (or `--rejected`; by user/planner/proxy:name); record accepted/rejected/deferred suggestions; advance to vision-lock. |
| vision-lock | `diagram --kind user-journey`; native `harness_record_confirmation gate=vision-lock`; advance to features after fresh approval. |
| features | `node --json @features.jsonl`; `diagram --kind feature-map`; advance to requirements with mapped features/stories. |
| requirements | `node --json @requirements.jsonl`; advance to tech-stack only with test descendants for every requirement. |
| tech-stack | `decision --json @decision.json --choose Python --rationale "..."`; `diagram --kind decision-map`; advance to plan. Record material choices/trade-offs. |
| plan | `node --json @beads.jsonl`; `compile --template template --out manifest.json`; `diagram --kind bead-dag`; advance to roundtable with requirement-to-bead coverage and valid manifest. |
| roundtable | `roundtable --reviewer @reviewer.json ...`; `findings`; `resolve --id F --resolution "fix/reason" --resolved` (or deferred/rejected); `audit --oracle oracle`; `quality`; advance to publish. Current matching review/audit and resolved questions required. |
| publish | `render --out plan/docs`; `publish`; explicit `worker-start --expected-revision REV --workers @workers.json` subject to native execution authorization. Matching protected template, audit and reviews required. |
| execute | `worker-status --job-id ID`; `status`; `impact` after edits; advance to accept only after actual worker/integrated evidence is assessed. |
| accept | `accept-scenarios --derive`; `accept-run --subject product --repo DIR`; `accept-review --repo DIR`; resolve findings; `accept --statement "actual words" --by user`. All current scenarios, review and approval required. |

**Native decisions and reviewer limits.** Align/vision-lock need initialized MCP2025-06-18 with client `elicitation` capability. Review the full displayed gate/revision, then supply explicit approval, complete words and conditions. Nonempty conditions block credit; decline/cancel, timeout/disconnect or stale revisions also give no credit. CLI quotes/by labels return `plan-confirmation-origin-unavailable`, never native approval; client-reported responses do not authenticate a person. Plan-to-roundtable confirmation remains unchanged.
Edits limited to vision validation/requirementFiles retain the lock; edits to text/understanding require renewed native vision-lock confirmation. Read the current node before editing; never fabricate approvals, reviewer records or user words.
Record reviewer proposals with `harness_reviewer_choice`; inspect `harness_schema tool=harness_confirm_reviewer_choice`, the stored proposal and fresh status. Bind actual node ID/current `spine_revision`/known workspace, `approve=true`, complete selection statement and `conditions=""` only for the caller's actual action. Template nulls are unbound; do not supply by/origin/human flags or invented approval/event/source IDs.
Ordinary choice is a caller-declared tester-as-user action (`human_identity_verified=false`, `approvalGranted=false`), clears only the reviewer-confirmation hold and launches no provider. Availability/authentication/independence/cross-provider/review-path/budget/native-final holds remain. Replaced catalogs, changed proposals and replay refuse. Missing genuine catalog observations report `reviewer-catalog-producer-unavailable`; intake separately reports `typed-confirmation-producer-unavailable`. Gate confirmation only serves align/vision-lock; offered models do not prove entitlement.
Real roundtables use existing approved capacity, exact authorized models/effort and normal provider protections. Default budget: 2 attempts/reviewer/manifest; `--max-rounds` changes that, `--max-total` caps totals across revisions. Preserve existing caps/deadlines; unknown capacity is not permission to retry or enlarge them. Failed reviewers expose diagnostic artifacts; retry only affected failed work. No benchmark launch is authorized by this guide.
`p readiness --beads B1,B2 --reviewer @reviewer.json ...` (or sample/seed) reviews exact packets; default all ≤8 beads, otherwise 5. Advisory status never substitutes for proof; findings use resolve. Above 12 beads ×2 reviewers requires `--max-calls`. Scope alone is not a rejection reason.

**Compile, audit and review recovery.** Prepare executable protected tests and importable failing implementation stubs in workspace/template; workers own implementation, never proofs. MCP `harness_compile` uses that fixed template and known workspace, renders canonical docs and records protected-file/manifest binding; then `harness_packet_view id=B provider=codex` is read-only. Recompile after plan/protected-file edits; missing tests or invalid manifests refuse, never fabricate proof.

For a review repair to an existing Python proof, read `harness_show_node id=<test-node> include_source=true` and current `harness_plan_status.manifest_revision`. Submit `harness_revise_test` with `node_id`, the returned template-relative `path`, `expected_file_sha256` from `testFiles.sha256`, `expected_manifest_revision`, complete replacement `source`, and a concrete `reason`. It atomically replaces only an existing linked file under the configured test directory (256 KiB maximum); stale preimages, unlinked files and reparse/escape paths refuse. This does not create initial test files or edit implementation stubs. After an interrupted call, inspect current bytes/status before retrying: replacement can have completed without a returned receipt. Old evidence and published snapshots remain history. Compile, audit and obtain an authorized current review; record finding disposition separately after verification. Source editing grants no approval or proof credit.
`audit --oracle oracle` uses Git HEAD when available, otherwise template; explicit `--baseline template` / `--baseline-ref REF` records the choice. Oracle supplies every affected owned file. Without oracle, authorized `audit --prospective` checks collection/red stubs/assertions/imports/traceability, not satisfiability; unsupported runner collection refuses. Publish explicitly with `--proof-level prospective --reason "..."`; workers must report impossible protected proofs, never weaken them. A later oracle audit permits republishing stronger proof.
For native prospective validation, inspect `harness_schema tool=harness_audit`, then call `harness_audit mode=prospective` with the independently known `expected_workspace`. Admission returns an `attemptId` before long validation; pending admission grants no proof or publication credit. Read `harness_audit_status id=<attemptId>` with the same workspace assertion to consume the committed result, audit ID and findings. A duplicate may already be complete: read its stored result without replaying validation. Unknown ownership remains held. Failure is recorded as failure; collection/red-stub checks do not prove oracle satisfiability, product execution, review, approval or acceptance. Stale or failing results do not grant publication.
TypeScript/command runner shapes and collection requirements are in [verification runners](../architecture/verification-runners.md). Current matching audit/real-review evidence is required; mocks, stale reviews and changed-only checks cannot substitute. Prospective publication/worker validation alone do not prove the locked vision.
`reviewResolution` is revision-stamped advice, not approval/publication/dispatch credit. Host owns repairs/dispositions; questions/native approval come first, and all open non-readiness findings block regardless of severity. Resolve fixed issues; defer real gaps with reason; reject only disproven findings with evidence. Re-disposition closed findings explicitly. Retain member/blocker IDs; `detailRoute` exposes complete review beyond bounded excerpts.
Refresh status/compile after edits, then use normal audit/review/advance/publication. Manifest/spine equality alone does not prove compilation. `spine_revision` (plan_revision alias) hashes editable nodes; manifest_revision binds protected content; published_revision is frozen worker authority. Test edits stale evidence; there is no force bypass. Use `impact --since REV|FILE` (default publication/last compilation); remove nodes only without children/references. Report obligations verified/failed/unverified and preserve unresolved gaps.

**Execution and acceptance.** Workers implement while planner steers. `workers.json` is exact `{bead,provider,model,effort}` per published bead; MCP start/status mirror CLI. Durable jobs reattach without relaunch; missing terminal evidence/liveness remain unknown. Native execution authorization, finite limits and uncertain-work recovery are detailed in the [resume contract](resume-plan.md); no install, retry or permission expansion is implied.
Scenario schema/derive return canonical first-moment and every scope-N ID. Derive replaces with drafts: list existing work first. Preserve IDs/intent and submit the complete array `{id,title,given,steps,expect}`; optional nodeIds bind other nodes. Steps are commands or `{command,expect}`; expectations support stdout/stderr, regex variants and exit (default0). Fill actual executable steps/expectations; prose drafts or saving alone prove nothing.
Use `{repo}`, `{python}`, `{home}`, `{data}`; each scenario starts in fresh empty cwd/HOME/data. Changes stale referenced coverage; resave reviewed scenarios. Planned clean-state first-moment/e2e (`firstMoment:true,cleanState:true`) is not proved until current product runs pass. Missing-file behavior must support first use.
Explicit `acceptance` spine nodes are supported. Allowed parents: acceptance vision/feature. Read `p schema --kind acceptance` (MCP: `harness_schema kind=acceptance`); apply its prerequisiteNodes, save example.node as acceptance.json, then use `p node --json @acceptance.json` (MCP: `harness_upsert_node node=<node>`). Use current parent IDs and preserve intent. These are draft planning records, not automatic executable coverage or proof: empty fields need executable steps, and current product runs, review and approval remain required.
`accept-run --subject product|oracle` distinguishes product delivery from reference dry runs. Legacy unspecified subject is product only with sibling evidence/run.json, otherwise oracle. Acceptance review uses distinct existing Codex/Claude capacity and findings/resolve; changed product/plan/scenario/vision needs fresh acceptance. `accept-rebind --repo DIR` reruns legacy-unbound/revision-algorithm-upgraded cases, preserving original approver/proxy references only for unchanged passing products.

**MCP acceptance is asynchronous.** `harness_accept_run repo=<DIR> subject=product|oracle`
checks the subject and canonical planner state and returns a durable `attemptId`
with `productCaptured=false`. This is queued intent, not product validation or
accepted-candidate credit. The owner captures actual product revision and dispatch
evidence off the protocol thread, and the serialized writer acknowledges those
metadata before any scenario child. Missing accepted candidates terminate with a
retained refusal before scenario launch. Follow
`harness_accept_status id=<attemptId>` on the same workspace connection; do not
resubmit to poll. Plan status and other tools remain available during execution.
The canonical serialized writer commits each scenario result as it finishes,
even without another client call. Pending admission and partial progress grant
no acceptance. A terminal current result is recorded in the ordinary acceptance
run store for review and approval; changed captured inputs retain evidence but
receive no current run credit. Product runs with zero accepted dispatch beads
are refused before launch; legitimate accepted partial and reused candidates
remain available for diagnostics.

An attempt's result describes the real initial/terminal captured product snapshot,
not filesystem immutability until the later database commit. Canonical current-run,
review, approval and proof gates check the actual product again. A later file edit
can leave a historical captured PASS while refusing all current acceptance credit;
attempt status explicitly labels that captured-result scope.

Each step retains its 60-second wait limit. A scenario has an enforced execution
deadline of `60 * declared step count + 1.7` seconds, including its existing
cleanup allowance; later waits shrink to the remaining budget. Progress records
the actual scenario budget, deadline, timeouts and unfinished work. Initial,
prelaunch and terminal product metadata run off the protocol thread without a
canonical write transaction. Metadata Git waits and tree hashing use the remaining
whole-run budget; an owner deadline wake persists UNKNOWN even while a metadata
operation is stalled, and late results grant no credit or further child launch.
Child stdin
is EOF; explicit shell redirections still supply declared input. The captured
`maxExecutionSeconds` is the sum of these scenario budgets, not a guarantee about
metadata or filesystem latency. No 900-second client timeout is used as a product
budget. The whole run also enforces that summed execution deadline from dispatch:
no new child starts after it expires, and waits use the lesser of the step,
scenario and whole-run remaining budgets. Admission immediately holds prior
review/approval credit; a failed new attempt cannot revive the superseded run.
One attempt owns the workspace at a time; duplicate admission while work
is pending reuses its ID. After known termination, a separate explicit run is
fresh execution with a new ID and invalidates earlier review/approval bindings.
Status calls never execute work. An unobserved owner or uncertain child cleanup holds additional child
admission with `unknown-interrupted`; restart/status never silently replays work
or attests quiescence. Old synchronous calls have no retained acceptance child
identity and cannot be adopted or declared stopped by this new status route.
The local CLI and legacy rebind remain synchronous.

Interrupted acceptance reviews retain registered attempt originals under .codex-harness/acceptance-review-attempts. Inspect acceptance.reviewAttempts and use `accept-review --repo SAME --resume ID` / MCP resume: no provider launch. Every slot must be complete, valid, nonmocked and match source/candidate/context/native session/model. Missing/unknown telemetry, error events, overflow reshards, stale/changed originals refuse; never edit/move originals or import unregistered external returns. Successful repeat resume is idempotent and preserves dispositions; all gates remain.
Only record actual user words as `--by user`; delegated acceptance uses `proxy:NAME` (or opus-user-proxy), remains isProxy:true and never real-user identity. Product checks do not prove actual worker delivery; final acceptance must assess the full locked vision.

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
Real roundtables launch providers normally. Test-only `--test-only-review-output response.json` injects an explicit finding array with supplied reviewers; injected/mock reviews never count as live evidence. The example's source test stops at the real review gate.
