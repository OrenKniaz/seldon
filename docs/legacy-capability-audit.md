# Legacy capability audit for the local Seldon extraction

STAGE-SPEED1 (C14/C18/C24/C26, I4/I5/I6/I9): TRIAGE DESIGN — the CLI golden
serialized independent legacy HTTP, ShelfLife and CLI journeys. Baseline gate:
18 CLI/guard cases 637.14s + isolated web 216.14s; 19/19 GREEN. The golden
fixture now overlaps the two unchanged HTTP journeys with CLI in separate
interpreters/sibling scratch workspaces, using the existing Windows Job owner and requiring
successful exits plus verified quiescence. Cold-start entries, fault twins,
assertions, product deadlines, production code and isolated web are unchanged.
Owner: test_golden_path.py. Exit: two consecutive complete gates <=540s GREEN;
exact results in ../stage-speed1-report.md. ShelfLife real-provider activity is
unknown: command-line inspection was denied; pre-existing provider processes
were visible. This is dev-gate evidence only, with no product-acceptance claim.
The initial concurrent candidate nested mutable helper workspaces under the
main product and correctly failed acceptance-revision-changed (473.43s, RED).
Sibling roots supersede that isolation error without relaxing freshness checks.
R2 supersedes any stability inference from the earlier passing gate: CC's
confirmation passed 19/19 in 444s, then its loaded-machine publish gate failed
the ShelfLife child with exit 1 and pytest-truncated output. Failed children now
retain complete stdout/stderr in their own journey-failure.log; the assertion
names that file and prints its last 60 lines. The failed publish workspace
denies this session access; a readable retained copy is requested. Its cause
remains unestablished, so passing diagnostic gates do not clear that defect.
R2 exit: diagnose/fix that failure and three consecutive full GREEN gates <=540s;
no retry, deadline change, assertion weakening or production change.

HOT1 (C14/C20/C24/C26, I6/I9): TRIAGE DESIGN — shared provider context
data belonged to roundtable orchestration, closing the approvals → planner_jobs
→ dispatch → worker_packet → roundtable cycle during ShelfLife r10 init.
Siblings: every installed console entry, seldon_cli, seldon_mcp, mcp_planner
and plan_cli cold starts. Owner: the existing dependency-free worker_packet_budget
module now owns PROVIDER_TOKEN_BUDGETS; all data consumers import that owner.
The redundant maximum-of-all-providers clamp is deleted; each provider's packet
limit is unchanged. No local-import workaround or judgment/consent change.
This supersedes STAGE-INT5's deferred BUG classification for this import cycle;
its retained observation and previous import-order workaround remain history.
The existing fast golden builds/installs the candidate wheel under lane scratch,
starts all six installed entries and the four named modules in fresh -I Python,
then observes init persistence, registered stdio initialization, check and removal
while preserving existing project instructions and retained permission policy.
Prior provider-host preimports hid the regression. Observed RED: 1 failed/5.29s
on the cold installed p entry with the circular ImportError. The first repaired
run reached removal in 11.65s, then exposed a fixture expectation that incorrectly
required deleting retained policy; only that expectation was corrected.
Focused golden: 2 passed/629.06s (cold-start segment 11.05s); this exceeds the
600s fast target and establishes no timing credit. Owner: shared golden runtime;
exit: the final complete gate passes within 600s without reducing behavioral coverage.
PlanStore reads the leaf through its existing module import, preserving its size ratchet.
Case count remains 19 (golden 2); no unit test or new test file. Final gate
results, runtime, patch and hash are in ../stage-hot1-report.md. Exit: cold installed
init/check/MCP/removal and the complete gate GREEN. No actual-provider, host-app,
ladder or full-vision acceptance is established by this scripted suite.

STAGE-2B candidate (C8/C18/C20, I4-I6; DV8 OPEN): DESIGN — quota and
launch authorization shared an owner. Siblings: worker starts, plan/acceptance
review starts, execution assessments and readiness reviews. The candidate deletes
provider_call_budget and approval_migration, B call/review allowances, renewals,
review round/total caps, readiness call caps and their public plumbing. Retained
budget events/tables/files are untouched and grant no converted admission.
The existing jobs owner verifies the native request/controller/protected snapshot;
attempt_provenance verifies run/attempt linkage and current B at launch. Review
owners call the same approvals.require_approval directly. Attempt deadlines,
provider context capacity and development efficiency metrics remain. The job
monitor derives its bound from admitted worker count plus its existing judgment
wait, instead of a removed call ceiling. PlanStore.finding_state remains unchanged.
Readiness exposed a sibling authority gap: its separate launcher had a call cap
but no current-B check. The candidate uses the same B owner at that launch boundary.
Golden changes retain 19 cases, remove budget inputs, change the stale worker
proposal regression to an unauthorized timeout, and add readiness B refusal and
success. The ShelfLife worker double now starts a real scripted child through the
ordinary dispatcher; immutable process/run/attempt originals, exit and root delivery
are observed. Former fake_workers skipped the migrated authority checks entirely.
CLI golden: initial readiness reply fixture selected the wrong envelope and failed
in 11.64s. The corrected scripted selector passed the CLI journey in 210.14s,
including quota-free B/review/root delivery/C and crash/stall recovery. No source
rule, deadline or assertion was relaxed for that fixture error. The original
scratch-root denial is resolved by CC's task-specific --add-dir correction.
Full gate 1 passed 19/19 in 555.17s (18/291.31s + web 1/261.53s), within
600s; its log is C:/seldon/tests/stage-2b/full-1.log. Subsequent source inspection
found the replacement status view passed raw SQLite packetBound integers to a
boolean renderer. It now uses the existing roundtable binding projection; the
same golden consumes the rendered full status and checks recorded packet scope.
This final projection edit is covered only by the final gate reported below.
The final full-gate outcome/runtime and exact candidate are recorded in
../stage-2b-report.md; this audit entry alone grants no full-suite or ladder credit.
2d integration handoff: provider_call_budget.verify_job now lives in
planner_jobs.verify_job; bind_run and claim_call's authority-only part now live
in attempt_provenance.bind_run/verify_attempt. Initial controller identity and
immutable admission/run/snapshot/attempt checks remain; 2b does not own 2d's
pending-controller wait removal. Port 2d's continuation changes to these owners;
do not restore the deleted budget module or drop initial launch authority. Bounded
recovery/continuation acceptance remains 2d's outcome. L0/DV8 remain OPEN until
actual authorized runs; scripted-provider suite results never establish them.

STAGE-2B independent review R1 (C8/C18/C20, I5/I6): REJECT of patch
7273a26bf913ba9bc8a93d320e962fc0b0766bcd97b09a5ddaed65041e9ba0ee
identified the removed all-selected consent preflight. The previous 19/19 suite
and quota deletion did not cover an approved reviewer followed by an unapproved
one. A new segment in the existing CLI golden reproduced BOTH sibling starts in
15.45s: readiness launched once; plan review launched once and left an orphan
review_attempts row under a required slot. Per-launch checks alone were insufficient.
The existing approvals owner now supplies require_reviewers: both batch owners
check every selected tuple before packet/launch preparation; their per-launch
checks remain. No quota counter, reservation or semantic authority is added.
The same golden declares required roles through harness_adopt_review_policy,
checks one B remedy, zero provider starts and no orphan attempts on both refusals,
then runs the valid selection and requires an eligible, complete policy before
continuing through publication/delivery/C. Prior all-unapproved and positive-only
selections missed the approved-prefix failure.
The first repaired CLI reached an inherited consumer mismatch: required-policy
status still indexed removed closureFindings, replacing the B remedy with generic
unknown status (17.35s). Removed that obsolete view branch; current policy reasons
remain canonical, without a fallback field or weaker hold. Corrected R1 CLI
golden passed in 248.44s: both mixed selections refused before any start, no orphan
attempt remained, and the valid required-slot rerun completed through C. The one
authorized final full-gate result, exact patch and any runtime limitation are in
../stage-2b-report.md. No independent re-review or
actual-provider/DV8/L0 acceptance is implied. The earlier 604.97s fast-ceiling miss
remains retained history, not fresh evidence for this repair.

STAGE-2A Round 5 (C4/C10/C11/C14/C18, I5/I6): the Round 4 request
identity remains invocation/reviewer/shard or member. Binding currentness now
precedes terminal-attempt guidance: a stale exhausted request requires refreshed
facts, and cannot steal the current worker continuation. The retained stale-A
journey catches that admission regression; the CLI golden also checks stale
terminal actions alongside both independent inconclusive shards.
The new complete-review read also exposed repeated evaluation of unrelated
completed judgment history. The one finding-state owner now evaluates current
bindings for pending requests and obligation-linked judgments, retaining all raw
history, member links, equivalence and disposition judgments. A retained-data
profile attributed 13.54s of a 16.50s preparation to binding reads; narrowing
that work preserves their authority. Convergence credits now share that same
per-read observation, as evidence and candidate bindings already do; writers and
subsequent reads still observe fresh facts. The public 30s deadline is unchanged.
Manifest revision and convergence credits share the existing per-read snapshot;
the next read/writer still reconstructs current bindings. The repeated table
existence and nodesDigest checks are subsumed by the required-column check.
The isolated browser path executes no Round 4 request/list consumers. Full run 1
passed readiness but exposed the same deadline shape in integrated S4: every
completed action had fallen back from normal launch to single-process, unlike
CC3's normal-mode run. Re-probing the failed mode on each action spent the shared
30s scenario budget. The runner now carries the observed fallback only to later
actions in that bounded scenario; sandboxing, assertions and deadlines are unchanged.
Full run 1: 18 passed/679.31s, web failed/234.67s; final outcome is in the report.
Round 4 findings 1/2 remain deferred to 2a2. L0/DV7 remain open.

STAGE-2A (C2-C4/C10/C11/C14/C16, I1-I9): DESIGN — review structure
previously granted semantic authority. Siblings: Jaccard clustering, cluster-wide
closure, revision-location reuse, empty/minor-only approval and semantic grades.
Owner: PlanStore's existing 1j prepare/submit/read; roundtable executes independent
reviewers. Candidate deletes semantic grades/AST heuristics and reconstruction
prompts, retains exact commands and deterministic evidence hashes, and requires
explicit bound verdicts/rationales/depth. Conflicting scope classifications remain
pending; equivalence groups grant display membership only; each member's closure
and actual-delta reuse require their own bound judgment. Three rounds trigger a
convergence assessment, not approval. Requested provider/model and observed runtime
remain distinct; same-model reviewers receive that lower label. DV7 remains OPEN
until authorized L0 proves actual reviews and labeling; no ladder credit here.
WEB-STALL-1: DESIGN — host sleep exposed waits without wall-clock/progress bounds;
siblings: RPC writes/replies, HTTP startup/readback/teardown and cockpit fetch/body.
Owner: golden/cockpit harness. Retained root cause is host sleep, not 2p. Candidate
uses wall-clock polling/deadlines and held-request browser recovery; it adds no OS
sleep-inhibition or host power changes. Receipt-less cleanup never grants success.
ShelfLife verifier: assertion-only RED was observed in the admitted job (47.71s);
previous verifier accepted empty HTTP success. Candidate now imports real CSV into
private SQLite, asserts saved rows and HTTP status/body, and bounds its lifecycle.
Gate 2 retained 17 non-browser passes and web through C, then found two fixture
mistakes: malformed review correctly returned isError, and a cancelled browser route
was aborted twice. The same fixture owners now expect the failure and observe client
cancellation without repeating it. Validation: see stage-2a-report.md for final gate and exact
patch. Existing 19-case marked fast gate remains; actual-provider/full-dream proof
is unperformed. Native sealed malformed acceptance receipts remain pending rather
than being relabeled as a different invocation; public 1j retains its two fixes.
Exit: final golden <=600s GREEN, then authorized L0 after integrated 2e; verify
independent plan reviews, evidence meaningfulness and same-model labeling there.

STAGE-2A follow-up (C2-C4/C10/C11/C14/C16/C18, I1/I5/I6): the
classification regression now consumes the equivalence judgment's bound member
facts. The presentation projection is unchanged and supplies no semantic authority.
This supersedes the initial projection blocker; review -> repair -> reuse -> B
and ShelfLife through C reached execution in the renewed CLI run.
The newly reached verifier exposed SQLite lifetime errors: connection context
managers commit/rollback but do not close. The fixture importer, server and verifier
now close their own connections explicitly before HTTP completion/temp cleanup.
No cleanup exception is suppressed and no retry masks it. T_HTTP has observed
assertion RED -> exit-0 Rice,Beans GREEN with empty stderr and verified quiescence.
The same flow exposed orphan semantic requests. PlanStore now asks equivalence only
for multiple members and prepares reuse only for positive original reviews; the
existing submit owner alone materializes membership. Publication keeps its normal
next action without fabricated verdicts. Final candidate/gate outcome supersedes the
initial BLOCKED report only as recorded in stage-2a-report.md; L0/DV7 remain open.
Renewed CLI golden: 1 passed in 183.91s; review/grouping/member closure/reuse,
ShelfLife through C and the main fault/recovery journey are GREEN. Final full-gate
result and exact patch remain in stage-2a-report.md; suite evidence is not L0.

STAGE-2A independent review fixes (C2-C4/C10/C11/C14, I1/I2/I4/I6):
REJECT of be14e839 is retained as source history; the five findings share one cause:
review authority outlived or omitted part of its subject. The existing 1j read now
checks one captured binding against all named members, exact planning evidence,
revision, and the canonical current acceptance run/product revision where applicable.
Review 1: removed normalized-text merging from both plan and acceptance ingestion;
every source finding and its opposing suggestion/scope rationale survives to judgment.
Review 2: acceptance closure and product-review bindings include the current run;
the canonical reader checks product revision before reconstructing semantic inputs.
A positive A disposition loses credit after changing only product bytes to B, and
closure stays pending/open. Returning to the exact A restores only A's bound credit.
Review 3: each admitted review invocation gets new judgment IDs; exact fixes and
sealed recovery retain only that invocation's IDs. Two unchanged malformed dossiers
exhaust both fix allowances; a fresh identical dossier accepts new, distinct replies.
Review 4: cluster views choose an open/deferred representative across every member;
closing A alone leaves B visible and continuation recommends reconciling B.
Review 5: convergence binds current review/reuse credit as well as revision; reuse
submissions and resolution expose the current request after three rounds and repair.
Continuation consumes current pending judgments rather than recommending advancement.
Golden CLI proves all five through canonical producers and scripted judge replies,
then reaches ShelfLife C and the main recovery journey: 1 passed in 229.20s.
One status read reuses its observed metadata snapshot (275.46s before reuse);
submissions and closure writes always validate live bindings, including candidate
changes between calls. No new judge store, retry counter, provider launch or unit test.
Final candidate, 19-case gate and exact patch identity: stage-2a-report.md. Actual
provider/L0/full-vision acceptance and DV7 closure remain unperformed.

STAGE-2A final gate blocker: the first full review-fix run passed 19/19 in
561.62s; the second/final authorized run passed 18 non-browser cases in 310.04s,
then failed web in 306.38s (combined 616.42s, above the 600s fast-gate ceiling).
The cockpit displayed its stale warning but checks.js:49 timed out after 8s
waiting for refreshed "Last received:" status. Cause is unresolved; this is not
evidence of host sleep or permission failure. Owner: cockpit refresh/check harness.
Exit: establish the recovery failure cause, then a newly authorized final full
gate must pass within 600s. Both full-run allowances are used; candidate is BLOCKED.

STAGE-2A ROUND 3 (C2-C4/C10/C11/C14/C18, I2/I4/I5/I6): DESIGN --
the 66f70ae0 re-review rejected two owners for one finding's authority. This
supersedes the prior closure/convergence completion claims above; history is retained.
Owner: PlanStore.finding_state computes every effective member and outstanding
request from immutable source facts and current 1j judgments. New plan, acceptance
and readiness source facts use append-only events; archived mutable status/resolution
columns are ignored. Dispositions write no closure status. Current positive member
credit closes; a current concrete negative opens; unavailable/stale/inconclusive
judgment stays pending and never supplies a concrete-failure blocker.
The same binding covers immutable members, revision and canonical acceptance run/
product revision. Applying closure preserves its judgment; changing product bytes
removes effective closure, and returning to the exact bound candidate restores it.
Every negative review gap is retained even when meaning.findings is empty; later
positive packets carry the member IDs. A fresh positive review alone cannot clear
those obligations: the real C gate still refuses until explicit current disposition.
An inconclusive major finding stays pending. Default groups retain open members.
Requests are keyed by reviewer/member/subject identity; both reuse requests remain
visible, and one positive reviewer plus convergence cannot satisfy complete review.
Publication, continuation and quality/cards share review_prerequisite; independent
counts, mutable closure reads/writes and legacy closure-credit projections are gone.
One read reuses captured metadata and latest event records only within that call;
the cache is cleared before returning, and submit/disposition writers bind live facts.
The cockpit recovery failure above has a concrete owner race: an automatic retry
can hold refresh when a manual click arrives, and the boolean guard drops the click.
Manual refresh now replaces/aborts that owner; retired replies cannot render or clear
the new owner. Browser coverage deliberately holds a second automatic retry before
manual recovery and keeps the same 8s check. No timeout increase or host power change.
All five prior regressions remain in the same CLI golden; no new test cases/files,
unit tests, judge store or retry counter. Exact final gate/runtime and patch identity
are in stage-2a-report.md; provider/L0/full-vision and DV7 closure remain unperformed.

Round 3 full run 1: 17 passed and the new CLI link-history assertion failed in
135.75s; web then failed its initial freshness read at checks.js:29 in 265.24s.
The link assertion incorrectly expected A to reference a judgment naming only B
and the gap. The fixture now explicitly includes A in that later proposal; the
same assertion remains and the owner never invents a link to an unbound member.
The initial browser read failed before the controlled retry scenario. Read-only
profiling of its retained workspace found repeated record copying and two current-
run validations in one snapshot (2.015s, 328527 response bytes). Immutable record
reads and the validated run observation are now reused within that single read;
the measured snapshot is 1.453s, with one validation. This narrows the performance
diagnosis; final browser outcome remains in the report, not inferred from a profile.
All timers remain unchanged; stale pending requests are exposed as stale, and all
historical member judgment links are retained independently of effective closure.

Round 3 allocated run 2: all 18 non-browser cases passed in 333.98s; web failed
in 265.97s at recovery, showing the 5s request deadline inside the 8s DOM wait
(combined 599.95s). This is NOT a green full gate. Both full-run allowances are used.
Local Playwright _unrouteInternal removes future handlers without settling paused
invocations. The harness removed its interceptor with requests still paused; that
was not a controlled end to fault injection. It now switches the same interceptor
to real route.continue() before manual refresh and removes it only after readback.
The second automatic held request and the original deadlines remain. This final
harness repair is syntax-checked only; a new authorized full gate is required.

STAGE-2A Round 4 (C4/C10/C11/C18; I2/I5/I6): DESIGN -- request
supersession used purpose/whole-spine identity instead of its bound invocation and
shard/member subject. The canonical PlanStore list now keys captured invocation
(including native request correlation), reviewer and actual packet/member subject.
It exposes submitReady and submit/fresh-invocation/refresh actions; terminal steer
attempts cannot advertise submission. Cards and continuation share one presentation
of this list; publication requires all live plan-review requests (product review
is distinguished by its canonical acceptance-run binding). No new judge plumbing.
Golden consumes two inconclusive canonical shards from one reviewer: both visible;
one positive leaves the other required and publication pending; both positives clear
the request prerequisite. Exhausted malformed attempts offer fresh invocation,
never submission. Previous coverage used one shard/reviewer and checked only errors.
The first CLI rerun reached acceptance but exceeded its 30s public review bound;
the request gate had included the active product review. Run-bound scope is now read
from the same list. Exact final gate and patch are recorded in stage-2a-report.md.
Re-review 3 findings 1 and 2 are OUT OF SCOPE and unchanged: pending-obligation gate
semantics and resharded negative-gap retention belong to follow-up lane 2a2. No claim
of their closure, actual-provider/L0 acceptance or DV7 closure is made here.

STAGE-2P (C3/C4/C13/C14; uses DV4/DV9, preserves I1-I9): DESIGN — the
worker-packet producer still requested deleted stub semantics. Siblings: web DOM
markers, CLI exit 3 and Build/legacy unit-test instructions. Owner: worker_packet.
Disposition: replaced with semantic integration/E2E tests, observed behavioral
RED, baseline preservation and the same tests GREEN; web keeps real preview.
The golden now consumes generated packets before scripting worker-authored files,
runs the compiled ordinary verifier, rejects startup-error RED via canonical 1j,
then admits behavioral RED and persisted GREEN with verified process cleanup.
The shared test-only 1j provider accepts any question/evidence bindings and explicit
review, equivalence/reuse, loop and acceptance meanings; existing schema, pending
and two exact-fix retries remain canonical. This proves harness readiness, not the
later consumers. Main CLI/web and ShelfLife worker replies no longer fabricate
marker RED. The approval support's retained marker fixture remains outside this
producer repair; it is not the required L0 packet/worker path. DV4 stays partial.
Exit: final <=19-case marked fast/golden gate, then Stage 2a; L0 must use this
actual packet/worker path after 2e under task-specific provider authorization.
No actual providers, L0-L3 or full-vision acceptance claimed. Final gate outcome,
runtime and exact patch identity: work/lanes/stage-2p-report.md.
Final gate: 18 passed in 188.55s; browser golden FAILED in 6548.33s (wall
6739.67s), waiting for #freshness containing "Last received:" at cockpit/checks.js:21.
Status-server replies also logged WinError 10053; cause is unproven. Owner:
cockpit/status_server; exit: bounded actual browser readback with current status.
The 75-minute lane timebox is exhausted; no retry or out-of-lane repair attempted.
BLOCKED, not ready for 2a; fast-gate <=600s acceptance is not met.
Receipt inspection also found BUG — T_HTTP prints an empty HTTP body and exits 0
while its server logs a SQLite error; siblings: none established. Owner: golden's
worker-authored verifier; unresolved. Exit: self-contained importer/SQLite/HTTP
assertion shows ordinary behavioral RED then GREEN through the admitted job.
The readback packet/worker/1j path passed, but this weaker ShelfLife verifier must
not be credited as that integrated semantic test. No ladder credit is granted.

STAGE-1I (C2-C4/C8-C10/C14/C24/C26, I1-I6/I8/I9): DESIGN — the
golden/journey provider had no bound semantic judge. Siblings: keyword A/B/C
consent, AST audit tiers, marker RED, qualitative FAIL and Contracts admission.
Owner: existing golden harness scripts one public 1j prepare/submit/read path;
process exits/output/cleanup remain real. Dispatch retains awaiting judgment,
and the original worker PID consumes each verdict once and continues judged
Contracts to Build under the same B. Post-exit scheduling remains Stage 2c;
no PID, provider-call, provenance or quiescence guard is bypassed.
Delivered-root assessments resolve through verified delivery to the recorded
validation worktree; fixture tools/logs and checkout line endings cannot rebind it.
Verdicts finish before acceptance capture; replacing a judgment during capture
correctly stales that attempt and grants no credit.
The generated scenario transport now reads its full owned JSON payload; the
131072-byte evidence-reader cap had misreported full 1j packets as product ERROR.
Consumers retain judgment IDs, binding hashes and current verdict fields; full
fact packets stay in 1j, avoiding duplicate review shards and spurious cap exhaustion.
Runtime friction: one web-file run took 745.17s, with the extra gap around preview;
the instrumented repeat measured preview at 20.12s. Cause remains unreproduced
(preview/audit_process owner); retain timing on recurrence, without guessing a fix.
Intake now uses baselineRevisions; B binds the complete current plan revision.
Both evidence-relevance packet owners consume execution_witnesses; actual
provider finding-closure execution is not established by the scripted journeys.
Disposition: integration candidate; final counts, exact patch and gate outcome
are in work/lanes/stage-1i-report.md. Exit: the final 19-case golden gate proves
real RED/GREEN, refusal/change/replay/reconnect, stale credit and 1a impact/history.
This supersedes the 1b intake namespace blocker and 1b/1c harness diagnosis;
SCEN-COV-1's old unknown-adapter ERROR and AST-tier conclusions are superseded.
actual-provider/full-vision acceptance, L0-L3 and later review/loop rows stay open.

JUDGMENT-1 (Stage 1j, C2/C3/C4, I1/I2/I4/I5/I6/I9): DESIGN — no shared
typed owner retains absent/inconclusive semantic judgments and bounded exact-fix
disposition. Sibling consumers: intent/consent/expansion (1b), execution/test
meaningfulness (1c), later review/steering/acceptance. Owner: PlanStore's
prepare_judgment/submit_judgment/judgment, using existing append-only events.
Disposition: one pending-judgment status, canonical subject/evidence revisions and
source events, explicit candidate binding, LLM verdict/rationale/open meaning/gaps;
two exact fixes after the initial malformed reply, then steering with owner released.
Exit: public submission → reload → shared consumer preserves pending/verdict,
invalidates changed bindings, and closes exhausted retries in the CLI golden journey.
Schema validation and read-only node inspection moved within the owned files to
preserve frozen module ceilings; no new store or engine. Existing consumer migration
belongs to 1b/1c; no actual provider execution, consent or ladder promotion claimed.

SCEN-COV-1: ShelfLife round 3 (20261104T131037-acc663-001-shelflife) stalled
on missing vision.fields.scenarios, then R1/R2/R3/R5 uncovered after saving
S_IMPORT_MIXED/S_IMPORT_UNREADABLE (CLI argv/path fixtures and exact stream/exit
observations). No HTTP/UI scenario object was submitted in the retained turns.
Owner: semantic_spec / semantic_schema.validate. Disposition: unsupported
scenario vocabulary and uncovered requirements retain scenario: unvalidated in
the canonical planning projection, exported SCENARIOS.json and acceptance view;
planning proceeds, with no execution or acceptance credit. Runtime requires a
supported executable replacement. Setup confinement and reference integrity
remain enforced. Exit: golden replays both importer submissions, compiles HTTP
intent at the labelled tier, refuses it before any child launch, then executes
worker proof from CSV importer through SQLite to real HTTP and reaches C in
three decisions. Original ShelfLife delivery and actual-provider acceptance
remain unproven. First gate: 16 batch checks and web passed; the new fixture
needed exist_ok for the compiler-created template and the size ceiling needed
to ratchet down by two. Final gate: 17 batch checks passed, CLI failed on a
fixture assertion that incorrectly demanded passed=true from the expected RED
Contracts row; web passed (350.16s). Retained dispatch shows Contracts
contracts-red and Build passed=true, including importer -> SQLite -> HTTP.
The assertion now checks those distinct outcomes; corrected fixture is UNRUN
because the two actual gate runs are exhausted. C on this new journey is
unverified. A separate tag attempt was denied before tests because it selected
a scratch directory outside the assigned root; that denied path was not retried.

SCEN-COV-1 C follow-up supersedes the earlier unverified-C disposition above.
The exact delivery owner error was "delivery: one retained candidate required
before C/judgment": this fixture dispatched into workspace/runs instead of an
admitted worker job, so no canonical root delivery existed. Fixed the fixture
to use public worker admission, the existing controller/Windows Job, ordinary
dispatch and root delivery; only provider execution is doubled. Kept the C
integrity gate intact. Pin fixture child imports for changed cwd. First new
CLI run stopped on a Contracts runner error (17.86s). Second reached ShelfLife C with
three decisions and owner-verified root delivery, then failed the existing UX-5
probe because acceptance_status dropped tier metadata (32.07s). Owner fix now
preserves executable/evidenceTier/validationMessage and reuses the already-read
latest run, keeping acceptance.py at 1918 lines. Read-only projection of that
actual saved UX-5 producer now exposes S2/S3 as scenario: unvalidated and
executable=false, including S2's exact unknown value-field reason. Both allowed
CLI runs are exhausted; the projection fix has no fresh complete golden pass.
CC's prior web pass (three decisions, 231.23s) is retained, not rerun. This is
source-only fixture evidence; original ShelfLife/provider acceptance is unproven.

SCEN-COV-1 / RECOVERY-1 regression investigation: CC's clean-main full gate
reported a failed successor with VERIFIED-QUIESCENT but no stopReason, while
ShelfLife and web reached C. Its retained runtime folder is access-denied to
this lane; the failed CC terminal/dispatch records are requested, not inferred.
Added inspect-proof and job-record prints at the exact existing stall assertion;
the worker-stalled assertion, zero-process check and poison/quarantine checks
are unchanged. The authorized local CLI golden then passed in 128.32s. Actual
retained proof showed worker-stalled, seven processes before teardown, verified
quiescence and zero processes afterward; the next readmission completed delivery
and C. inspect returns the quiescence control unchanged; graph/spine do not
filter that field. This does not establish the cause of CC's earlier failure.
Disposition: unreproduced, original failed-job evidence needed before a causal
repair; no speculative recovery-rule change or retry added. Exit: inspect CC's
failed successor terminal/dispatch record and repair its actual owning failure,
then retain a complete CLI pass after the final edit.

AUDIT-HTTP-1: ShelfLife fight round 2 (20261101T163156-9ed2f4-001-shelflife,
turns02/03) stalled on `prospective-no-interface-assertion` and
`passing-audit-required`. Owner: test_audit.audit_prospective_tests. Disposition:
prospective classification is data-only; unknown interfaces are info findings
labelled `audit: unrecognized interface`. Collection and RED→GREEN stay at
execution; captured-input binding, review and acceptance retain their owners.
Dead prospective subprocess/discovery/refusal paths are removed. Exit: the HTTP
golden journey recognizes subprocess/urllib status/body assertions, admits an
opaque concurrency helper at the lower tier, publishes, proves real RED→GREEN
and reaches C in three decisions. Unproven: the single gate run failed before
HTTP planning on a fixture argument collision (corrected afterward); web failed
browser readiness with a scenario deadline error and verified zero-process
cleanup. The other 17 checks passed. No baseline reproduction or retry was run;
the corrected HTTP journey and actual-provider acceptance remain unverified.

AUDIT-HTTP-1 follow-up supersedes the HTTP and web validation blockers above:
CC's retained web golden passed with three decisions (254.34s); the owner
identified the earlier readiness failure as load. The HTTP fixture mistakenly
treated legacy understanding's node ID as an A card and assumed acceptance ran
from the product directory. It now explicitly prepares the combined A subject
and uses product-bound paths from acceptance's isolated working directory.
No product repair was needed for these fixture defects. The first authorized
CLI run reached dispatch RED→GREEN but exposed the cwd assumption (12.50s);
the second passed (97.97s), including HTTP C in three decisions and the existing
CLI recovery/delivery journey. Subprocess/urllib assertions are recognized;
the opaque concurrency helper remains informational and does not block
publication or C. CC's other 17 checks are retained, not rerun here. Actual
provider/product acceptance remains unproven by these deterministic workers.

RECOVERY-1: ShelfLife turn04 refused readmission with
`recovery-readmit-requires-VERIFIED-QUIESCENT; UNKNOWN stays held` after stalled
Build calls (fight-1, 20261101T145704-2c0e74-001-shelflife, turns03-07).
Owner: planner_recovery monitor/resolve and the existing audit_process Job owner.
Disposition: bounded no-progress stop and recovery signal terminate the owned Job;
missing teardown evidence becomes ABANDONED after 12 seconds. The old attempt's
workspace is quarantined in its original namespace and excluded from delivery and
revalidation; fresh readmission retains B. Exit: CLI golden loses an owner's
receipt, then hangs a real Build descendant, verifies zero Job members, readmits
and reaches C in three decisions without poisoned files. Final CLI/structural gate:
18 passed in 105.31s, suite count unchanged (19), core net zero, no new tools.
Actual-provider recovery acceptance remains unproven.

RECOVERY-1 validation blocker: first web golden passed in 300.27s; final web
golden failed at acceptance in 262.58s on missing Chromium profile `lockfile`
(`WinError 2`), after verified Job teardown with zero active processes. Owner:
unchanged semantic_runner._cleanup. Disposition: retained outside this recovery
repair; both permitted gate runs consumed, no further retry or cleanup edits.
Exit: cleanup owner handles a concurrently removed browser-profile entry without
hiding other failures, and an authorized golden gate reaches web C. This is an
observed cleanup race, not a demonstrated baseline failure or a green final gate.

CATALOG-1: 47/150 farm runs stalled before B when executable discovery could not
produce a model catalog (20261101T100128Z-d4f633-001-hashslip and
20261101T111738Z-af1b82-002-linecount, turn02.md). Owner: intake selection and B
admission. Disposition: missing discovery now retains exact proposed reviewer
tuples as catalog: unverified in B and the acceptance packet; plain yes authorizes
them. Exit: no-catalog CLI/web golden journeys reach C in three decisions.
Actual-provider launch/recovery acceptance remains unproven by these stub runs.

Read-only source audit of qualified safety `8fd5e902` and planning `0c94f540`; no runtime proof. The later user-approved standalone repository decision supersedes the earlier in-repository packaging preference only. North Star v3 remains the product obligation.

The [October 16 port decision](decisions/2026-10-16-basic-security-port-scope.md)
also sets the current implementation scope. It approves minimal health, supported
cancellation and basic stall/crash recovery. Assignment/admission binding beyond
current claim binding, validated leases/tokens, server-bound mail and authenticated
localhost are backlog items. They remain recorded below without blocking the
basic local-provenance lane.

| Disposition | Capability to retain | Existing thin-core route or remaining question |
| --- | --- | --- |
| Preserve | Durable confirmed planning and publication | PlanStore, intake/waves, phase gates and immutable publication |
| Preserve | Readable planning docs, chat cards and diagrams | planner_views, spine_render and revision-stamped exports |
| Preserve | Traceability and bead quality | spine, graph and plan_quality; selective legacy reuse only for demonstrated gaps |
| Preserve | Cross-provider review | roundtable through provider-normal builders |
| Preserve | Mid-flight steering and invalidation | impact_since, steering and finding resolution |
| Preserve | Parallel provider work and separate worktrees | dispatch attempts and serial integration; worktrees are not security boundaries |
| Preserve | Confirmed bounded shared project memory | Relocated existing storage, confirmation, provenance and prompt reader |
| Preserve | Candidate-bound integrated evidence | dispatch integration and current commit/tree claims |
| Preserve | Meaningful tests and locked-vision acceptance | test_audit and acceptance; extraction tests do not certify product completion |
| Preserve | Truthful recovery and retries | Existing resume records and notifications; additional gaps below |
| Preserve | View-only cockpit and unknown usage | status_api/status_server; unknown usage remains unknown |
| Drift | Harness-owned OS confinement | Superseded by supported provider protections; do not import its framework |
| Drift | Bypass-bearing legacy launch routes | Keep normal-provider policy and truthful enforcement limits |
| Drift | Cockpit mutation controls | Accepted execution control remains in provider chat/CLI/MCP |
| Current basic obligation | Exact local process provenance | Bind controller-observed launches to the exact plan, packet, run, bead and attempt originals; refuse missing, foreign or mismatched recovery evidence. This is local provenance, not authentication or current liveness. |
| Backlog, October 16 decision | Assignment/admission beyond current claim binding | Retain the stronger identity-binding goal; no new host-authority service, token registry or legacy runtime import is required for the current local lane. |
| Backlog, October 16 decision | Authorized dynamic claims | Validated caller leases/tokens and atomic dynamic release/reacquire remain deferred. |
| Backlog, October 16 decision | Attributable scoped mail | Server-side sender/recipient/run binding and revocation remain deferred. |
| Backlog, October 16 decision | Authenticated localhost | Loopback plus read-only GET does not establish authentication; authentication remains deferred. |
| Open obligation | Supported cancellation | Select and stop a thin attempt while preserving truthful terminal state and recovery |
| Open obligation | Basic stall/crash recovery | Timeouts, no-progress detection and clean resume require live evidence. Failed-job status/resume explains inspection-only reattachment; replacement-controller continuation remains unsupported. Terminal records do not prove controller exit or current child liveness. Stronger lease-expiry/interleaving guarantees remain deferred. |
| Open obligation | Minimal health/preflight | Report actual supported launch readiness without importing the legacy operator/session runtime |
| Open obligation | Actual worker and full-vision evidence | Provider/model runs and integrated acceptance need separate authorization and execution evidence |

Open and backlog obligations remain in the product plan with the explicit scope
above. Clean imports do not establish completion, and this decision does not
authorize copying the legacy runtime merely to satisfy imports. The current
extraction cuts only the actual provider and memory dependency leaves.

Extraction excludes legacy modules outside core, original Git metadata/config/history, credential and agent-config directories, runtime databases/state/logs, generated evidence, environments/caches, hidden evaluators and unrelated fixtures. Required tracked mock reference fixtures remain explicitly labeled test inputs.

DELETE-1 projection duplication: status_api previously recomputed headline,
activity and proof observations alongside canonical plan status. Owner: status_api
and cockpit. Disposition: removed in deletion lanes 1–2; one GET-only view consumes
canonical status and receipts. Exit check: saved MCP plan → HTTP → browser retains
revision, blockers, next step, ownership and unknown usage through refresh failure.
Actual-provider and full-vision obligations above remain open.

DELETE-1 validation findings: the first full run exposed an empty saved-plan read
regression; corrected at status_api, with the real PlanStore → HTTP read-only check
passing. The untouched `test_web_hung_renderer_timeout_reaps_browser_children`
once reported WinError 32 while deleting a Chromium profile instead of its timeout
reason. Disposition: unresolved intermittent cleanup issue, owner semantic_runner;
the exact selector passed on baseline 3e213699 and this candidate. Exit: retain the
timeout reason and verified child teardown without a profile-sharing error.

DELETE-1 cleanup follow-up: a second full run again had 467 passes and two
WinError 32 browser-profile failures (reduced-motion attribution and hung-renderer
timeout). Isolated baseline/candidate selectors and the baseline 15-case browser
group passed; this supersedes isolated nonreproduction as clearance of the gate.
Required gate repair: semantic_runner retries only sharing violation 32 during
temporary-root deletion, only after verified teardown, at most three attempts
(0.5s and 1s delays). Exhaustion and all other errors still propagate. The browser
assertions and Job quiescence contract are unchanged. Validation is pending.

Cleanup repair validation update: the real Windows file-lock probe retained an
unverified root and recovered after handle release in 0.5s. All 15 existing browser
failure checks passed; all 173 fast tests passed in 228.9s wall time, under 600s.
The exact full-suite rerun remains pending; prior failures are retained above.

Cleanup repair gate closure: the unchanged full-suite command completed with
469 passes, no skips, exit 0 in 1340.01s. This closes the local deletion/cleanup
validation blocker for this candidate; it does not establish actual-provider or
full-vision acceptance. The raw golden MCP call count still exceeds the audit's
25-call target and remains a separate planning-workflow obligation.

DIAG-TRANSPORT: UX-5's exact upsert on a slot-39 copy returned exit 0, empty
stderr on both source and registered runtime; historical disconnect cause remains
unknown. Owner: planner stdio. A malformed tool name did kill the baseline with
`TypeError: unhashable type: 'list'` (exit 1). Boundary isolation now returns
isError, reserves stdout for frames and sets UTF-8. Exit check: captured upsert,
failing call, raw/printed diagnostics and continued golden delivery all pass.
The stable full gate passed 469 tests, exit 0, in 1169.72s (baseline: 469 tests).
The copied original V1 exactly matches the captured repair: its save persisted;
the historical disconnect remains unattributed without original host/server logs.
Repository-wide Ruff also reports 42 inherited errors in dogfood-watercli's
author_plan.py and one formatting difference in its template/watercli/cli.py;
both match HEAD. Owner: retained fixtures; deferred outside transport. Exit:
fixture-owner cleanup passes repository-wide Ruff; src/tests are already clean.


## Contract alignment discovery, November 1 (source evidence)

CONTRACT-ALIGNMENT owns this batch in `work/sprint-20261029/lane-contract-align`, base `193aeea` (local import `5c39c38`). Source gates do not certify a real provider cohort; the sibling `contract-align-report.md` records final check results and the patch identity.

| Observation and evidence | Owner and disposition | Practical exit |
| --- | --- | --- |
| STAGE-EXEC1 prior candidate, acceptance conclusion SUPERSEDED by independent review: ShelfLife publication `328e924f` reached B/execute, then indexed an empty dependency tuple before any provider launch; readmission repeated the crash. | The positional lookup was removed and the scripted gate passed, but review rejected multi-build completion/final-HEAD assumptions and the excerpt-based test-source identity. | Prior multi-build fixture and completion claims grant no acceptance. The corrected scope and complete-bundle rule are recorded below; the real-provider rerun remains unperformed. |
| STAGE-EXEC1 review revision (C9/C14/C18, I6/I8, uses DV2): two execution findings concern unsupported multi-build plans; the single-build finding shows a newly imported helper missing from RED baseline copying and changed assertion helpers retaining old RED credit. | Graph admission, shared by public compile/publish and dispatch, permits one build plus an optional preceding contracts supplier. Multiple builds receive the exact Stage 3a/DV2 merge remedy; inverted contracts receive one explicit dependency repair before execution. Each executable trace test declares complete `execution.testRoots`; their union is one full-content-hashed bundle, including helpers/fixtures. Review 2 superseded the additive-copy equivalence claim: the baseline now restores declared roots from the committed candidate tree, including tracked deletions, and verifies the complete content hash before observing RED. RED reuse uses the same hash; bounded source bodies serve judge context only. Contracts-only plans receive the add-one-build-dependent-on-contracts remedy, rather than a merge instruction. Existing 1j preparation/submission/read and retries remain the only semantic path. | Existing golden refuses multiple builds and inverted contracts before any worker starts, replans to contracts→single build→C, and runs a dependency-free build importing a new helper/fixture through assertion RED→GREEN→C. The helper enumerates fixtures and the worker deletes an old tracked fixture: baseline and GREEN must both exclude it before assertion RED can be admitted. A strengthened assertion helper with unchanged module/argv invalidates prior RED; a positive verdict cannot manufacture missing RED. Contracts-only refusal is followed by public replan and C. Cases remain 19. Final gate, patch identity and real-run limitations are in `work/lanes/stage-exec1-report.md`; multi-build execution/final-HEAD support stays with Stage 3a. |
| STAGE-EXEC1 full gate 1 (500.94s): approval journeys exposed a removed `linkedRed` receipt field; the web journey reached C and then failed the untouched cockpit check at golden line 2341 with `Status unavailable: Status request exceeded 5s wall-clock deadline`. | Candidate owner restores the existing RED hash field. Cockpit/status owner retains the separate shared-machine timing observation; no deadline or cockpit changes in this lane. | Final gate result is in the lane report. On recurrence, diagnose the existing status request boundary; the browser must receive canonical status within its existing deadline. No claim that the timing cause is reproduced or repaired. |
| Stage-2a flake (C14/C18/C23/C25, I6): original production + held publication reproduced valid pending Build evidence with running run/unknown MCP status (112.57s). Retained successor 30090394f15b reached verifier S10 13.70s after the last monitored evidence write, then hit the 15s no-progress clock. | Dispatch/semantic runner/job status owners publish pending status with the evidence snapshot and completed-scenario progress in existing monitored evidence. PlanStore.finding_state remains sole finding/request owner. Repair targets: web 3/3 and CLI 3/3 GREEN. Historical S4 deadline did not recur. One original CLI repetition stopped at the preliminary HTTP 5s startup deadline; subsequent repetitions passed, cause unproven. | Required two consecutive full golden gates are recorded in stage-2a-report.md, Flake section. At any future HTTP startup recurrence capture the existing child startup/error boundary; no deadline increase. Suite evidence only; actual-provider/full-vision acceptance remains unperformed. Superseding final outcome: full gate 1 GREEN/490.18s, full gate 2 RED/381.79s (S3 scenario deadline, S4 unperformed). The owner now retains the observed fallback across the same bounded run; its targeted web Build reached PASS, then harness_accept_status timed out (1638.89s total). No full gate on this final mode-cache edit. Timebox expired; acceptance cleanup remains unverified after server exit, process inventory denied. Fresh owner-status diagnosis and a renewed full-gate/time budget are required; no completion claimed. |
| Stage-2a Round 6 (C11/C14/C18, I2/I5/I6): review4 #1 and rereview3 #2 share an undefined dossier replacement lifecycle. Golden RED/42.27s observed A's concrete gap missing from the replacement packet. | PlanStore retains accepted plan-review members/gaps in the judgment transaction and retires prior pending requests for the same reviewer and current binding when a new invocation replaces them. Distinct live shard identities remain; immutable accepted obligations survive positive replacements. The dossier runner carries the original logical round identity. Producer-side deferred writes and implicit read-time request dedup are removed. Rereview3 #1 remains out of scope for 2a2. | New scripted golden reaches publication only after disposing A's retained finding/gap, without waiting on retired B; CLI targeted GREEN. Two final full-gate results and exact candidate are in stage-2a-report.md Round 6. No actual-provider/full-vision credit claimed. |
| Captured F3/UX5 writes, Run25 S8/HC evidence, four-scenario web validation and UX4 click-first RED did not compose with their consumers. | PlanStore/semantic schema, acceptance, roundtable and consent owners: repaired together; retained fixtures and owning behavior tests cover the required seams. Final audit also extended unchanged-subject B reuse from chat to native; the same owner test checks no persistence change before consuming the request. | Exact candidate passes the source gates, then an authorized pinned CLI/web cohort proves root delivery, formal review and C; no real cohort credit here. |
| Stdio retained-candidate recovery blocked in Git reads that inherited the RPC input stream; real golden web traces identified the calls. | Candidate/dispatch/steering subprocess owners: closed stdin explicitly; the real browser/stdio journey now consumes candidate-bound contrast judgment and reaches C. | Retained-failure recovery completes without losing the RPC connection; scenario children have explicit cleanup receipts. Missing receipts remain UNKNOWN. The single broad gate exposed an outer TimeoutExpired diagnostic lacking the runner timeout vocabulary despite verified teardown; normalization is repaired at the runner, with the existing hung-renderer check as its exit. |
| The review's eight optional seams remain: approved-roster rediscovery, shard/start allowance, durable review owner recovery, C negative vocabulary, long-scenario budget parity, atomic same-work delivery repair, asset/packet capacity, typed failure/delivery status. | Existing approvals/review/budget/candidate/runner/status owners; deferred from this bounded batch, without weakening their current holds. | Run each ranked row's decisive owner check before claiming closure; preserve failed/assisted runs in any real cohort. |

| STAGE-EXEC2 (C26/C20/C14, I1/I5/I6/I7): ShelfLife R5 reviews lacked a supported served UI/API observation path; offline file pages were the only browser adapter. | Semantic runner owns one adapter: declared server.argv uses allocated loopback SELDON_HOST/SELDON_PORT (or {host}/{port}), bounded readyPath/readyWithin, Chromium UI steps and actual HTTP request/response, with native browser context policy and existing Windows Job cleanup. Static contrast/heading/motion and marker verdicts are removed: rendered snapshots/screenshots remain evidence, and explicit named qualitative answers come from current shared 1j execution judgments. Planner schema/guidance, reviewer and worker packets advertise the owner. Adapter bytes join canonical execution identity for API-only evidence too. | Extended existing web golden authors served bindings through the store and ordinary workers/dispatch: UI form→disk write→HTTP readback→server restart→persisted readback, alongside retained offline journeys. Readiness timeout names the failed origin and repair and records cleanup; API-only observation needs no browser installation. Final gate and exact patch identity: stage-exec2-report.md. No authorized real-provider ShelfLife/ladder run performed. |
| STAGE-EXEC2 iteration discoveries: repeating rendered text/styles per step exceeded the existing capture bound; immediate visible assertions raced real asynchronous UI writes; acceptance consumed only named contrast credit and left other explicitly answered checks pending. | Same observation owner retains one bounded final rendered snapshot, reports capture overflow explicitly, waits at most one second for visible/hidden state, and consumes only explicit qualitativeChecks answers from the existing current 1j execution record. No extra judge, retry store or inferred approval. | Existing served golden exercises each regression through the canonical consumer; source journey results remain suite evidence. |
| STAGE-EXEC2 focused iteration: untouched offline S2 hit the existing scenario deadline after S1 had executed; cleanup receipt verified zero remaining processes. | Existing runner/process-start timing owner; retained as a shared-machine timing observation, not diagnosed or repaired by this lane. The following candidate observed all four scenarios and integration PASS; any final gate recurrence must be reported. | No timeout relaxation or OS/provider settings change; future exit check is completion within the existing bound with verified cleanup. |
| STAGE-EXEC2 first full gate: 17 passed + web passed (through C), CLI stopped on its legacy flags metadata assertion; 306.89s overall. | Golden owner replaces that assertion with the existing observable baseline PASS obligation: diagnostic marker prose never decides a semantic gap. The original recursive equality and timeout caps remain intact; unused adapter phase plumbing and one-use wrappers are removed. | Final second gate is recorded in stage-exec2-report.md; no test requirement is waived and no third full run is authorized. |
| STAGE-EXEC2 second full gate: 17 passed + served/offline web passed through C (211.88s); CLI stopped because the UX-5 malformed assertHash/value diagnostic no longer identified its value operand. Total 393.61s; full gate RED, 18/19 passed, within ten minutes. | Web schema owner restores the offending step and supported operand shapes in its existing pending-judgment teaching message; the golden assertion is retained. This is a diagnostic correction after the last permitted full run. | Final focused CLI result is in stage-exec2-report.md. No final full GREEN is claimed; the two-run full-gate allowance is exhausted, and integration requires renewed authorization for a final full gate. |
| Stage-2a2 / DV10 (C10/C11/C14/C15, I2/I4/I6/I8): after plan repair and changed bindings, fresh positive reviews let the original golden advance to publish with retained obligations pending (RED/37.96s). Sibling gates: C, legacy acceptance rebind and continuation. Earlier stale-binding assertions never attempted publication/C after fresh reviews. | PlanStore.finding_state owns one current-disposition prerequisite over retained obligations; readiness stays advisory. Publication, C/rebind, worker admission and continuation consume it. Pending is a judgment wait naming exact obligation IDs, not a new observed failure. Gate-local open-only rules, obsolete unconsumed closure projections and the unused duplicate review-advice renderer are removed; legacy closure input validation is retained. | Extended existing golden: pending plan obligations survive fresh positive reviews until bound disposition; HTTP product changes after positive closure execute successfully, get fresh reviews, remain held at C until fresh bound disposition, then reach C. Exact final gate result/patch in stage-2a2-report.md. Scripted suite evidence only; DV10 register ratification and actual-provider/ladder acceptance remain outside this lane. |
| Stage-2a2 replay friction: moving the candidate-change replay to HTTP initially exhausted its unchanged review-call cap with a redundant intermediate positive review. | Golden fixture owner reuses the final fresh positive review for the retention assertion. No new approval, cap, retry, timeout or production-budget change. | Both expanded regression paths finish under existing B; final result is in stage-2a2-report.md. |
| Stage-2a2 Round 2 supersedes Round 1's completeness conclusion: independent review rejected the disposition-before-run ordering (I5/I8). Product/scenario changes, interrupted acceptance and both legacy migrations share the producer dependency. Product-change guidance RED/28.51s requested dispositions with no current run. | FindingState orders canonical run recovery before bound disposition and then C. Rebind records original inputs/approval/reviews before execution and resumes from a refreshed canonical run; approval is not carried until current dispositions succeed. Legacy scenario binding uses the canonical scenario writer; its first replay caught a nested transaction, so that writer now participates in the owning transaction, keeping backfill and resume provenance atomic. Verified same-input rebind lineage carries the original explicit positive review; ordinary reviews still require current bindings. Changed migration inputs invalidate restoration guidance. | Existing golden retains plan-change publication coverage, follows run-first product guidance, observes queued-owner guidance by exact attempt ID, replays persisted legacy-unbound and revision-algorithm inputs, holds C/original approval before disposition, binds the refreshed run, resumes through a new server without rerunning, preserves actor/words/provenance and reaches C. Final result/patch are in stage-2a2-report.md Round 2; scripted suite evidence only. |
| Stage-2a2 Round 3 supersedes Round 2's lineage-credit conclusion: re-review found that historical credit bypassed current template bindings (I6). Siblings: template change before restoration and after completed rebind. Golden review-gate RED/52.53s returned credit after changed template bytes; a C-only check had been masked by retained-finding holds. | Deleted historical review traversal, approval/review copying and the restoration context/stage. Rebind refreshes execution only; retained records confer history, not credit. Ordinary reviews use current 1j verdicts. C's producer binds current review inputs/run, and its consumer requires a current positive verdict for that exact run. The existing run-first ordering remains. | Both legacy/revision replays obtain fresh reviews, the owner's complete retained-obligation set and fresh C through existing 1j, within unchanged allowances. They deny review/C credit on template drift before fresh review and after completed migration/C. Plan-change and product recovery journeys remain. Final gate, production reduction and exact candidate are in stage-2a2-report.md Round 3; no actual-provider/ladder/full-vision claim. |

| Stage-2a2 Round 4 supersedes Round 3's C-consumer completeness claim (C14/C15/C23, I2/I6): upgraded stores retain positive C judgments lacking reviewBinding; after template drift plus fresh same-run reviews/dispositions, golden RED/42.65s observed accepted=True without fresh consent. Siblings: template additions, edits and deletions. Earlier replays created only newly bound C. | PlanStore.judgment now requires explicit current review/run binding for C source facts; absent or stale bindings confer no credit. The C gate and ordered continuation consume the same judgment currency; historical words/actors remain intact. | Existing HTTP golden seeds historical unbound C from a real 1j positive reply/current run, changes template input, obtains ordinary fresh reviews and all bound dispositions, observes acceptance-approval-required/fresh C guidance, then submits bound C and reaches acceptance. Existing plan/product migration and stale-bound-C journeys remain. Final gate and exact patch are in stage-2a2-report.md Round 4; scripted suite evidence only. |

| STAGE-2d (C4/C18/C23/C25; I2/I4/I5/I6): review recovery advertised a `review:` ID but resolved through the worker-only directory parser; interrupted acceptance held its unique slot indefinitely; audit shutdown joined without a deadline; dispatch and resume owned separate retry/hold rules. | Candidate in `work/lanes/stage-2d`, base `be232aff29d970608bcc4d9c9675001f3f58c683`: existing recovery events now release/quarantine review, audit and acceptance admissions; late credit is excluded. Worker recovery derives each bead's allowance from canonical successor records and prepares a bound 1j steering question after two recoveries. Duplicate dispatch retry/import paths and the advisory resume store were removed. Stage 2a's finding owner is unchanged. Historical pre-review candidate (subsequently REJECTED for MED 1/2 below): targeted CLI GREEN/307.21s after correcting a test marker that changed the captured product; full gate 1 passed all 19 cases but missed the 600s fast budget (669.26s wall). See `work/lanes/stage-2d-report.md`. | Extended existing CLI golden: supported queued-audit cleanup; real worker crash/stall observations; per-bead exhaustion and judged direction; actual stdio server crash/reconnect during acceptance and review; fresh admission through C. No new cases or test files. All named CLI journeys passed, with three user decisions, 24 happy-path calls and explicit UNKNOWN cleanup after server loss; this is scripted-provider suite evidence only. |
| STAGE-2d validation/efficiency: initial CLI launch could not open `C:/seldon/tests/stage-2d/cli-1.log` (access denied), so Python/pytest never started. Session write roots contained the literal `C:/seldon/tests$L`. | Superseded: CC restored the exact scratch write root and authorized continuation for 75 minutes. First executed CLI was RED/227.36s: the fault fixture wrote its review-started marker into the product after acceptance, correctly invalidating that run. The marker now lives under `.codex-harness`; failing evidence is retained in `C:/seldon/tests/stage-2d/cli-1-failure`, and corrected CLI passed/307.21s. Candidate removes 373 nonblank production lines versus the original 400 target; CC allows the 27-line shortfall only when further removal would be cosmetic. No remaining duplicate lifetime rule was identified for safe deletion within this lane; preserved continuation belongs to 2c. Full gate 1: 18 passed/379.27s + web 1 passed/287.22s = 19/19, wall 669.26s, fast budget MISSED. This was a shared-machine run; the cause of the slowdown is not established. Retained log: `C:/seldon/tests/stage-2d/full-gate-1.log`. One remaining full run checks the unchanged source/test candidate after this audit update; final outcome belongs to the report. | Final permitted full gate after the last edit must be GREEN; retain the 19-case ceiling and measure the 600s fast budget. No deadline increase, removed scenario, alternate scratch, unit test or actual-provider launch. |
| STAGE-2d continuation boundary: `planner_jobs.execute_worker_job` waits in the original controller for judgment; `planner_candidate.revalidate` continues Build only in that PID, and the base `provider_call_budget.bind_run` rejects a replacement owner. Simply deleting that wait would strand the existing browser journey after Contracts. | CC explicitly assigned this handoff to Stage 2c on November 5: retain the working base observer wait here, with no expanded dependency on the budget module being deleted by parallel lane 2b. No fabricated ownership-release or process-quiescence claim. | Stage 2c proves a persisted pending-judgment handoff with no running attempt, consumes the same bound judgment after restart and finishes Build/C without repeating paid effects or resetting authority. Interrupted revalidation remains part of that unproven continuation boundary. No standalone deviation or ladder closure. |
| STAGE-2d independent-review MED 1/2 (C4/C18/C23; TRIAGE BUG): the removed prelaunch product comparison allowed scenarios to run on changed bytes; recovery history pooled independent beads when the current fault set contained both. The earlier serial fault journey and terminal-only freshness checks missed these regressions. | Same owners/candidate repaired: `planner_acceptance` compares actual product identity after acknowledgement and before scenarios, refusing with one rerun remedy. `planner_recovery` derives each current bead's count separately from the existing successor history; no persisted counter or new judge path. Existing HTTP golden now uses two independently owned HTTP services through real Job-controlled worker failures: `{A}`, `{B}`, `{A,B}` readmit, then each bead's third fault steers. The prior duplicate serial exhaustion check was removed; crash/stall/transport-loss journeys remain. | Observed RED: review-cli-2.log/57.79s (`steer` instead of `readmit`); review-cli-4.log/111.23s (changed product's scenario actually executed). Both repairs GREEN in review-cli-5.log/334.50s, including refusal before the scenario launch marker and fresh C. Setup-only failures retained: review-cli-1/23.99s consumed the compile ID instead of the returned prospective publication ID; review-cli-3/77.14s assumed an undeclared integration row had a `passed` field. Tests now consume canonical publication and actual A/B validation rows; production contracts were preserved. Final full-gate result belongs to work/lanes/stage-2d-report.md; no source/test/audit edits follow that gate. |

| STAGE-SCHEMA1 (C2/C4/C14, I8/I9): r5-diag item 3 burned turns inferring scenarios/setup/targets, choosing the writer and omitting execution.testRoots; prior golden plans supplied already-valid shapes. | Planner schema owns one checked node-example generator: Spine plus semantic_spec validate its examples; the minimal build links an executable test. Shared nested shape diagnostics name paths/allowed keys and the writer; unknown-tool discovery retains the valid tool list. Redundant two-bead/sample-error teaching is replaced, with no validator-rule, 1j, consent or retry changes. | Existing stdio CLI golden reads schema, saves examples first try, refuses malformed nested writes without persistence, names upsert_node for the wrong writer, and carries the returned execution shape through real worker-process RED/GREEN, persistence, teardown and C. Targeted CLI passed 273.12s before the final diagnostic adjustment; final gate/patch are recorded in ../stage-schema1-report.md. Scripted-provider suite evidence only; ladder/live-provider acceptance unperformed. Initial iteration failed acceptance-revision-changed while source was being edited; that run grants no candidate evidence. |

| STAGE-INT3 (C26/C14/C24, I1/I5/I6/I9): approved EXEC2 used older main, overlapping SCHEMA1 diagnostics and the expanded golden worker fixture. | Existing schema owners retain SCHEMA1 examples, writer guidance and nested paths alongside served UI/HTTP support; worker fixtures retain main's ShelfLife/recovery and EXEC2's server behavior. All audit rows survive; module ceilings match actual sizes. Review P2 remains deferred: canonical API-only preflight still requires browser dependencies; the adapter's browser-free HTTP support does not establish that end-to-end capability. | Single-file web GREEN/240.40s and CLI GREEN/329.79s preserve served/offline observation, persistence, teardown and main journeys. Final integrated gate and exact patch are in ../stage-int3-report.md. Scripted-provider suite evidence only; real-provider/ladder acceptance remains unperformed. P2 exit: canonical preflight follows executed actions' browser requirement. |

| STAGE-EXEC3 (C18/C26/C14, I6/I8/I9; TRIAGE DESIGN): ShelfLife r7 omitted a declared verifier; null source hashes reached fallback RED's Git restore and surfaced a controller exception. Siblings: omitted modules/helpers, empty/partial declared bundles and local argv inputs; HTTP products also received static-root preview guidance. Earlier golden workers always supplied complete bundles. | Execution sources now enumerate actual declared files and local argv inputs, naming all absent/empty roots before validation/materialization; one incomplete worker outcome enters the existing per-bead two-recovery/1j chain. The existing recovery event carries that outcome into the successor's bounded packet. Candidate identity is consolidated at its existing owner. Served preview reads the canonical SCENARIOS target and uses exec2's server/browser/cleanup adapter; offline products retain file preview. No new judge, counter or store. | Extended ShelfLife golden: first Build omits verify_http.py, persisted outcome names it, readmission packet teaches the exact fix, second Build restores it and reaches observed RED/GREEN, delivery and fresh C. Existing web journey executes the served packet command and checks HTTP origin/readiness plus Job cleanup. Focused CLI GREEN/414.49s; first iteration RED/163.21s was the scripted judge assuming every result was judgment evidence (KeyError candidate), corrected to pass structural outcomes through. Final gate/patch: ../stage-exec3-report.md. Scripted-provider suite evidence only; no authorized real-provider/ladder acceptance. |
| STAGE-EXEC3 preview replay (C26/I6/I8): focused web RED/118.17s ran the worker's command from the delivered product root, where protected SCENARIOS.json is absent. Inspection also found the shared server adapter would leave an unowned .server.log in a worker checkout; the earlier static preview did not launch a server. | The golden consumes guidance in the actual worker checkout. The preview CLI supplies temporary evidence storage to the existing server adapter, preserving source ownership; normal interactive close cleans it, and outer Job containment still owns timeout cleanup. No product-root binding copy or ownership bypass. | Focused web GREEN/201.04s: canonical served command opens HTTP with ready=true, outer cleanup verifies zero processes, git status is unchanged, persistence/readback and fresh C complete. Offline journeys remain. Final full gate and exact candidate are recorded in ../stage-exec3-report.md; no actual-provider/full-vision claim. |
| STAGE-EXEC3 independent-review correction P1/P2 (C14/C18/C26, I4/I5/I6/I8; TRIAGE BUG, sibling cases: none): REJECT supersedes the initial argv-completeness and preview-cleanup conclusions above. Arbitrary argv operands are not declared bundle inputs: a later Build's missing product must still permit Contracts RED, and present product code must never be restored into fallback RED. Outer Job cleanup also masked raw preview completion leaving launcher descendants. | runners uses ONLY declared testRoots for completeness, hashing and restoration. Preview uses the existing bounded audit-process owner and reports its actual cleanup receipt. Normal page close or browser disconnection ends preview through ordinary browser cleanup. No new store, retry counter, judgment or confinement implementation. | Existing compiled golden passes the later-built channels.dat in Contracts argv, observes absent-product RED, then Build GREEN while fallback RED remains RED. Web uses a real launcher/child server, normal WM_CLOSE and an uncontained caller: own Job receipt reports zero processes, TCP port is closed and checkout unchanged. Focused web RED/245.06s exposed page close being distinct from browser disconnection; repaired web GREEN/193.45s, normal preview completion 7.51s. Original full suite passed 19/19 but missed 600s at 629.46s; review-corrected CLI/full-gate result and exact patch are in ../stage-exec3-report.md. Scripted-provider evidence only. |
| STAGE-EXEC3 boundary correction (C14/C18/C20/C26, I5/I6): CC stopped round 2 because its desktop window enumeration/title matching/close handler violated NO SCREEN/WINDOW CONTROL. The 193.45s result above is invalid for acceptance and is not reused; its CLI run was interrupted. This supersedes the interactive-preview conclusion and exit check in the preceding row. | Removed the handler and title fixture entirely. Preview launches only headless Chromium, observes the declared served/offline URL and completes automatically inside the existing bounded process owner. Worker guidance names that headless observation; no visible window, desktop interaction or user-browser attachment. P1 remains testRoots-only. | Existing golden uses an uncontained caller and real launcher/child server; it consumes the headless browser's actual user agent and the inner owner's Job membership/quiescence/zero-process receipt, verifies the server port is closed and the checkout unchanged. No outer teardown supplies success. Fresh headless web GREEN/203.80s (preview 6.35s), CLI GREEN/346.54s including legitimate Contracts RED and unchanged fallback RED after Build. Final full result and exact patch are recorded in ../stage-exec3-report.md; previous window-controlled evidence confers no credit. |

| STAGE-RVSHAPE1 (C4/C11/C14; I6/I8/I9; TRIAGE DESIGN): ShelfLife r9 spent ten exact-fix replies on missing meaning.depth, including an exhausted review. Siblings: review verdicts, finding disposition/equivalence, reuse, convergence and scope WHAT/HOW. Prior golden providers invented valid depth without reading packet guidance. | The existing prepare_judgment owner adds one validator-checked, bound response example plus field guidance to the same durable record; examples are inconclusive and confer no approval. Depth remains an open nonblank explanation of chosen depth and consequence, with no new enum or validator rules. Shared packet guidance replaces duplicate per-review shape instructions; bindings, pending state and the two fixes are unchanged. | Existing golden providers copy the packet shape, submit sibling examples, read persisted results and prove first-try review acceptance. Blank depth still exhausts exactly two exact fixes; ordinary execution, persistence and cleanup journeys remain. Observed initial RED: missing responseExample, 27.21s. Final gate, counts and patch are in ../stage-rvshape1-report.md; scripted-provider evidence only, no real-provider/ladder acceptance. |
| STAGE-CONSENT1 (C8/C14, I1/I6/I9): ShelfLife round 6 (`work/fight-1/fight-seldon.json`, `run-seldon-r6.log`) refused the repaired plan twice with plan-b-consent-required solely because its revision changed. DESIGN siblings: review/test/command repairs, metadata corrections, same-work recovery and wave re-splits preserving scope/limits. | The existing approvals owner binds B to feature/requirement IDs and Vision Lock revision, the worker tuple multiset/count, parallelism/deadline and exact delivery reviewers/stages. Catalog observations are display context. Obsolete executable-consent digest and revision-parameter approval wrapper are deleted; canonical admission/proof digests and all-selected/per-launch review checks remain. The read-only status owner reports canonical mutations since B, without compiling the draft or adding a consent/judge store. | Existing golden repairs a reviewed test's command and actual worker-authored verifier body, records matching reviewer catalog metadata, reviews/executes/persists/cleans up and reaches C with A/B/C only. A worker-model change offers one reused diff-card request; pending B survives same-subject repair/reconnect and a real reviewer change stales the reply. First CLI iteration passed 324.54s; final candidate gate/patch are in ../stage-consent1-report.md. Scripted-provider suite evidence only; no real ShelfLife, ladder or prepatch-consent migration acceptance. |
| STAGE-CONSENT1 iteration friction: the reconnect fixture retained the pre-repair publication revision after B's pending interpretation correctly resumed; its worker admission refused (2 selected journeys passed, reconnect failed; 37.59s). Inspection also found that using impact_since only for status reporting would couple readable draft status to compilation. | Same owner/candidate: the fixture consumes the canonical compile result; status reads existing mutation events after the exact B request. No executable freshness check, refusal, deadline, 1j retry or acceptance requirement was relaxed. | Corrected reconnect journey passed in 19.66s; stale-subject and reviewer-renewal journeys retain their valid earlier results until the final gate. Exit for actual product credit remains an authorized real run; final suite evidence is reported separately. |
| STAGE-CONSENT1 focused CLI 2 (138.51s): the strengthened worker verifier passed T_HTTP but printed readback twice; the existing S_IMPORT_MIXED exact stdout check correctly failed. | Fixture owner emits its original single-line readback only after both server lifecycles have asserted the persisted rows. Both assertions, the exact expected stdout and cleanup checks remain. | Failure retained under C:/seldon/tests/stage-consent1/cli-2-failure; corrected result and final gate are in the lane report. This was a fixture-output repair, not timing failure or a relaxed product requirement. |
| STAGE-CONSENT1 full gate 1: 18/19 passed (all 17 E2E journeys); the only failure was planner_views.py at 1577 lines against its frozen 1575 ceiling. Wall 627.68s also missed the 600s fast ceiling (first phase 410.09s; isolated web 214.90s). No timing cause is established. | The existing consent-view owner formats the new continuation line; the generic view consumes it without growing. Catalog display enrichment is read only for B. The 1575 view ceiling is unchanged; final production total and PlanStore ceilings are below lane base, with no new exemption. | Prior patch 13723bd27d9f2597d5faea4504f366736d391cb12eab84c18d1bba0fc086df21 and detailed log retained under C:/seldon/tests/stage-consent1/full-1.patch and full-1-detail.log. One final allowed full gate follows these edits; its result/runtime/patch identity are in ../stage-consent1-report.md. No actual-provider or ladder credit. |
| STAGE-CONSENT1 CC Round 2 (C8/C14/C16, I1/I6/I9), ShelfLife R7 r7-diag.md item 3: DESIGN — B's worker multiset/count still made a tests-first re-split a new consent subject. This supersedes Round 1's worker-count binding, not its retained receipts. Siblings: bead number/order/ownership, contracts-before-build, tuple subsets and lower operating limits inside the same approved scope. | The same approvals owner uses unique allowed provider/model/effort tuples, parallel-worker and per-attempt timeout ceilings, and the canonical delivery reviewer/stage selection. Actual bead assignments remain in execution admission and proof. Existing approved permissions/ceilings are retained for in-envelope requests; a new tuple or higher ceiling yields a bound diff. Envelope content comes from the latest recorded decision, while the existing current/pending gate still controls admission; no second store or inferred consent. | Extended the existing golden from one approved build to test-only contracts C -> product build B after review, reports both bead changes, executes real assertion RED -> GREEN with persisted evidence/cleanup, and reaches C with three decisions. Focused CLI GREEN 359.80s. The new-model permission reply initially reproduced stale binding (RED 3.46s); the same owner's repair passed renewal, reconnect and stale-subject journeys (3/3, 50.64s), including one worker/reviewer diff approved through C. Duplicate changes-requested coverage is removed there; change-b retains it. Final full result and exact patch are in ../stage-consent1-report.md; 19-case ceiling unchanged. |
| STAGE-CONSENT1 Round 1 final full gate: 18/19 in 585.67s; CLI S10's unchanged five-second action timed out, leaving RED ERROR and T_RECEIPT unperformed. Cleanup verified zero remaining processes; the browser journey, all approval journeys and both structural guards passed. | Semantic observation/test-fixture owner; timing cause unestablished, disposition retained/not chased. CC Round 2 explicitly leaves this limit unchanged and authorizes one fresh full gate; a quiet-machine check must complete S10 and RED -> GREEN -> C without changing its deadline. | Prior report/patch retained as C:/seldon/tests/stage-consent1/r1-final-report.md and r1-final.patch; prior p tree moved within scratch to r1-full-2-p, preserving the db536ae7a40b run; full-2-detail.log retains the original receipt. Round 2's final result is reported separately; no earlier GREEN or composite evidence substitutes for that gate. |

| STAGE-CONSENT1 CC Round 3 (C8/C14/C16/C23, I1/I2/I6): independent review rejected ID-only scope identity: an obligation could narrow under the same requirement ID. This supersedes Round 1/2 scope-content claims, preserving the reviewed worker envelope. Siblings: feature/requirement wording, acceptance fields, additions/removals and in-flight replies. | Approvals records full feature/requirement/acceptance nodes and their canonical content digest with the Vision Lock revision. Canonical node transactions prepare the existing 1j scope-intent question; matching digests need no judgment, added/removed nodes or unavailable historical scope require a fresh B, and content edits wait for meaning. Current positive preserved credit reuses B; changed scope shows the full diff. Explicit new B retires obsolete pending scope questions through existing judgment supersession. No new judge store/counter or semantic word matching. Execution confirmation adapters are deleted in favor of the canonical confirmation owner. | Existing golden holds readiness before any provider start on pending/inconclusive scope, then reports preserved wording and reaches C with A/B/C. Existing journeys stale an in-flight same-ID narrowing, approve a changed requirement plus model/reviewer in one reused diff card through C, and explicitly approve an unsure scope without leaving a pending prompt. Approval focus GREEN 3/3, 72.58s; CLI GREEN 399.45s. Final gate/patch are in ../stage-consent1-report.md; 19 cases versus 19 base, scripted-provider suite evidence only. Old records without scope content fail closed to a fresh card; historical-consent migration acceptance remains unperformed. |
| STAGE-CONSENT1 Round 3 P2: continuation reporting omitted wave-refinement-applied. The new canonical wave exercise also found a producer/consumer identity mismatch: publication_current and the original wave lock both passed, but the raw manifest differed from the prospective publication only by planRevision, proofLevel, proofLevelReason and sourceManifestRevision (r3-cli-6.log RED 136.10s). Sibling consumers: cumulative exits and refinement prepare/record/apply, sharing wave_boundary_context. | Status reads the existing receipt's editedNodeIds and affected beads/tests/waves/requirements. The boundary consumes the frozen published manifest after checking its canonical source revision and current proof level/lock; candidate cleanliness, integration claim inputs and executable scenario bindings remain required. No historical evidence is relabelled. | The existing HTTP golden uses real worker integration and cumulative HTTP exits, rejects stale publication, reads the actual committed interface, records/applies a scripted planner refinement, and sees NEXT/T_NEXT/WD2 plus the impacted later wave in chat/status under the original B. No future-wave execution or full-vision/ladder acceptance is claimed. Default explicit acceptance-node derivation omits its own nodeIds: the fixture supplies them through the public writer; a producer default is deferred to the acceptance/schema owner, with an exit check that derived wave exits bind their source acceptance node automatically. |
| STAGE-CONSENT1 Round 3 fixture friction: wave acceptance ancestry (r3-cli-2, 20.98s), a glob where one protected boundary file is required (cli-3, 18.64s), the newly observed passing integration row (cli-4, 95.85s), retained migration template bytes (cli-5, 151.60s), and missing explicit exit nodeIds (cli-7, 155.41s) were corrected through existing producers. cli-8 passed applied-wave reporting but failed real SQLite/HTTP readback because its scripted rewrite still matched the former literal response body (163.11s). Approval focus initially retained an obsolete requirement pin (r3-approval-1, 1.85s). | Fixture owners retain actual readiness/concurrency checks in one protected integration file, restore captured original template/product bytes for current wave evidence, reuse the first-moment exit and supply its node binding, and update the worker rewrite. Pinned requirement references are refreshed through the vision writer and independently judged as preserved metadata; the narrowed requirement still needs B. No assertion, timeout, retry allowance or acceptance requirement is weakened. | cli-9 GREEN 399.45s includes persistence, process recovery/cleanup and C; approval-2 GREEN 3/3, 72.58s. cli-8 evidence is retained in C:/seldon/tests/stage-consent1/r3-cli8-p; cli-9 in r3-cli9-p. Round 2 report/patch/SHA are retained as r2-final-* under the same scratch root. Final full gate follows the last source/test/audit edit; no actual-provider launch or push. |

| STAGE-CONSENT1 CC Round 4 (C8/C9/C14/C16/C23, I1/I2/I6): independent review confirmed content judgments/P2 but rejected the hand-listed scope kinds. An ancestor story could change from local export to third-party disclosure while generic feature/requirement text and the preserved verdict stayed unchanged; REQUIREMENTS.md still delivered the story to workers. Siblings: stories and future scope-bearing kinds. This supersedes Round 3's scope-selector completeness claim. | Spine's one kind-metadata table now owns V-model levels and parent kinds. Its through-requirement selector captures vision fields, features, stories, requirements, acceptance criteria and declared metric/stop obligations; technical levels and side nodes retain their distinct roles. Approval snapshots/digests, 1j subject references and diff cards use that same selected node map. No approval-owned kind list, second judge store, retry counter or semantic inference. A redundant vision-count check is removed because Spine already validates that invariant; production line growth versus the Round 3 candidate is zero. | Existing golden retains the prior preserved verdict, changes only the ancestor story to third-party upload, observes that verdict becoming stale and readiness waiting without launches, scripts changed, offers one reused B request with the story diff, approves it and observes the next readiness calls. Generic feature/requirement nodes remain byte-equivalent across the story edit. Original local-product C/cleanup/persistence remain covered; the newly approved third-party work stays unbuilt/unaccepted. Focused CLI GREEN 324.08s; final full result and exact patch are in ../stage-consent1-report.md. 19 cases versus 19 base; no actual provider/ShelfLife/ladder acceptance. |

| STAGE-CONSENT1 CC Round 5 (C8/C9/C14/C16/C23, I1/I2/I6/I9): DESIGN — selecting scope by V-model rank omitted execution-consumed design, architecture, contract and decision obligations. An unchanged generic requirement could accompany a design switch from local export to third-party disclosure. Siblings: every current/future semantic kind in the execution trace. This supersedes Round 4's selector-completeness claim; its receipts remain retained as r4-final-* under C:/seldon/tests/stage-consent1. | B now snapshots the unfiltered Spine.to_dict()[nodes] projection used by the compiled execution trace and locked worker inputs. That one map supplies its digest, 1j subject references and card diffs. One scope-intent question judges WHAT versus HOW with full before/after digests: HOW continues/reports; WHAT asks once; unsure holds/offers B. Added/removed feature/story/requirement/acceptance nodes are structurally WHAT; technical additions/removals use the same question. Rank metadata/query and duplicate positive-meaning validation are deleted; A's separate Vision Lock contract remains. Pending B replies use this same comparison against their exact displayed snapshot, preserving HOW and staling WHAT without another store/counter. | Existing golden edits only design text for a module refactor (HOW, no card, reported), then third-party upload (WHAT, one reused diff card, approve/readiness), and retains the ancestor-story WHAT case with all other nodes unchanged. Canonical packets carry the edited text; original real process/SQLite/HTTP persistence, cleanup and C are retained; new third-party work remains unbuilt/unaccepted. Scripted catalog, tests-first re-split and wave-refinement HOW verdicts preserve their original B. CLI focus passed 353.86s; an earlier 113.20s focus correctly rejected concurrent source edits at acceptance review, so verification now freezes source. Pending-reply focus and the ONE final gate are reported in ../stage-consent1-report.md; 19 cases versus 19 base, no provider/ShelfLife/ladder claim. |

| STAGE-CONSENT1 CC Round 6 (C8/C9/C14/C16/C23, I1/I2/I6/I9): DESIGN — B owned a node-only projection while admission separately hashed file-backed execution inputs. harness_revise_test could weaken a protected assertion with no node mutation; siblings are protected tests, referenced fixtures/contracts, scenario files and future file inputs. This supersedes Round 5's execution-input completeness claim; its exact candidate/report/SHA are retained as r5-final-* under C:/seldon/tests/stage-consent1. | planner_jobs now owns ONE execution_inputs snapshot: the full spine nodes and the existing confined template file set, with exact raw-byte SHA-256 and content evidence from one file read. B consumes it directly; execution_scope records that same manifest, and require_execution compares the captured inputs with current canonical inputs before consuming B/HOW. Frozen-copy hash guards remain unchanged. Every canonical transaction can prepare the same 1j WHAT/HOW question for node or file changes; 1j currentness also checks the execution-input digest, preventing old node-current HOW credit after file changes. Continuation reporting reads canonical file differences and retains wave impact events. Duplicate admission/provenance readers and B's private node snapshot are removed; no new judge store, retry counter or inferred meaning. | Existing golden uses harness_revise_test to replace an exact HTTP body assertion with a nonempty check without changing nodes: stale captured admission is rejected, current admission waits, WHAT yields one reused B diff card, explicit approval proceeds. Renaming the test with identical approved assertions yields HOW, no card, and reports the node/file. The question carries exact before/after content; prior design/story, re-split, process/persistence/cleanup and C cases remain. First focus reached the file gate but a fixture compared escaped repr text (205.05s); the corrected run exposed old HOW currentness after compilation changed VISION.md (167.96s), repaired in the shared binding. Final focus and ONE full-gate result are in ../stage-consent1-report.md; 19 cases versus 19 base, scripted providers only, no actual-provider/ShelfLife/ladder acceptance. |

| STAGE-INT4 (C8/C14/C18/C23/C24, I1/I2/I6/I9; TRIAGE DESIGN): approved CONSENT1 overlaps main EXEC3 recovery and RVSHAPE1 judgment examples. | Existing owners retain the shared execution-input manifest and WHAT/HOW consent, all main golden assertions, and all audit rows. Scope-intent uses the same validator-checked example generator. The tests-first re-split keeps ordered Build ownership of its verifier so the omitted-input fault and successor repair remain real. Ceilings match actual sizes. | Focused CLI/web and the final integrated gate are recorded in ../stage-int4-report.md. Scripted-provider suite evidence only; actual-provider/ladder acceptance remains unperformed. |

| STAGE-INT4 focused web stop (C14/C18/C24/C26, I4/I5; TRIAGE BUG): after CLI GREEN/464.21s, focused web RED/171.63s at unchanged UX6 S4 with "Runner failed or scenario deadline expired"; S1-S3 passed. | Semantic runner/timing owner; cause unestablished. S4 Windows Job receipt verifies membership, quiescence and zero remaining processes; the outer acceptance snapshot still reports quiescence UNVERIFIED, so it is not promoted. No deadline, scenario or assertion changes and no retry. | User stop-on-untouched-timing-failure instruction applied: full gate was not started. Retained C:/seldon/tests/stage-int4/web.log; runtime-owner diagnosis and an authorized quiet-machine final gate remain before integration acceptance. Source/patch and exact validation limits are in ../stage-int4-report.md. |

| STAGE-EXEC4 (C14/C18/C26, I1/I2/I4/I5/I6/I9; TRIAGE DESIGN): ShelfLife r9 rejected disposable preview DB ownership, treated missing-product baseline errors as infrastructure, and counted intentional 409 responses as console failures. Siblings: missing modules/server startup, browser negative paths and disposable observation data. Earlier golden workers supplied the server during Contracts, asserted file absence explicitly, and previewed read-only pages. | Runner/candidate owners supply SELDON_DATA_DIR and server argv {dataDir}; preview uses its existing temporary root and process owner. Baseline records retain raw FAIL/ERROR, exits and stderr/reasons; only the existing bound 1j verdict admits RED. Verified-clean scenario errors no longer prevent remaining observations. Web actions may declare expectedErrors by method/path/statusEquals; native resource diagnostics are deduplicated against observed responses, while undeclared responses, network failures and application errors still fail. No new judge, store, retry, desktop control or provider launch. | Existing golden file passed both cases in 798.84s: real missing-module baseline, pending/right-reason judgment, GREEN/C; missing served baseline admitted through 1j; declared browser 409 passes, undeclared 500 fails; preview creates SQLite data, reaps its launcher/child, removes the owned directory and leaves checkout unchanged. Final input-shape checks preserve this contract. Final full gate, exact patch and independent authority review are recorded in ../stage-exec4-report.md; this is scripted-provider evidence, with actual-provider/ladder/full-vision acceptance unperformed. |
| STAGE-EXEC4 independent-review correction (C14/C18/C26, I5/I6; TRIAGE BUG, sibling cases: none): the first patch's positive RED branch could override another row's uncertain cleanup. The earlier clean-process journeys never lost a scenario receipt. | The same candidate owner now requires verified zero-process cleanup for every performed observation before either RED or GREEN admission; a semantic verdict cannot supply process truth. The existing compiled golden persists a real observed failure with one cleanup receipt removed, submits a positive reply through 1j and refuses admission, then uses the intact real receipts to admit RED/GREEN. This is deliberate receipt loss, not a claim that Windows teardown failed. | First review REJECT superseded; corrected exact-patch review and final full-gate results belong to ../stage-exec4-report.md. No ownership, retry, process-control or test-case expansion. |
| STAGE-EXEC4 Round 2 / HIGH review (C14/C18/C26, I4/I5/I6/I9; TRIAGE DESIGN): supersedes the first EXEC4 row's preview-ownership and error-allowance conclusions. Journey-wide allowances hid failures on happy calls sharing the same endpoint; cwd=checkout still allowed relative DB/cache writes when products ignored the supplied environment. RED-admission via current bound 1j was confirmed sound. | Deleted cooperative SELDON_DATA_DIR/{dataDir} handling and the separate JavaScript preview path. Preview uses the ordinary semantic runner, its existing disposable candidate snapshot and bounded process owner. Expected errors now belong to one uniquely named step; requests retain their originating step, each declared response is consumed once, and late responses cannot borrow another step's allowance. The original approval/RED judgment path is unchanged. | Extended the existing web golden with declared negative 409/500 and happy 409/500 on the same endpoints; relative scenario.sqlite3 ignores runner environment. Focused web GREEN/276.07s: byte-identical candidate, removed snapshot, closed server port and verified zero owned processes. Final opening-step metadata check is also exercised by preview. Exact final full gate, runtime, patch/hash and review belong to ../stage-exec4-report.md; 19-case ceiling retained, no actual-provider/ladder/full-vision claim or desktop/window control. |

| STAGE-STALL1 (C18/C16/C14, I5/I6/I9; TRIAGE DESIGN): ShelfLife r9 attempt 1 was stopped for evidence silence while the provider was active. Siblings: long reasoning, installs/builds, review and acceptance work. | Existing Windows Job process owner computes one idle clock from pinned member CPU counters, process creation, captured output and supplied evidence/stream changes; its bootstrap and dispatch supervisor are excluded. Aggregate Job CPU was rejected because dispatcher housekeeping falsely credited silent providers. Duplicate pre-payload teardown/quiescence handling is consolidated without changing stop authority. Approved provider deadlines and canonical two-recovery/1j steering remain. | Baseline RED: active Contracts killed as worker-stalled, seven owned processes reaped/zero remaining (266.40s, golden line 1577). Existing ShelfLife golden now scripts output-only then CPU-only work across a 5s accelerated silence window, a canonical silent provider, an active provider exceeding approved B timeout, persisted failure, readmission and bound 1j steering to completion. A 323.33s fixture failure exposed the existing denial-audit inconclusive status after TimeoutExpired; assertions now consume its actual failure/cleanup. A native ctypes spelling error failed at 6.19s and is corrected. No new cases/files. Second independent Sol review rejects production net +22; the aggregate-CPU defect is corrected and no further material functional blocker was identified. A nonblocking native-failure handle gap remains: if IsProcessInJob raises after OpenProcess, the new handle is not yet cached for final cleanup (audit_process owner; deferred exit: close it on membership-query failure). Candidate remains NOT READY; no safe related deletion identified and no further extension after the second review. Final focused run RED/329.34s at golden line 1602: the approved 25s deadline reached TimeoutExpired, but existing dispatch taskkill /T returned Access denied and its subsequent 15s wait timed out (controllerProcessExited=false). The outer Windows Job later verified zero remaining processes. Deadline stop and recovery-to-completion remain unproved; no final full gate was run because repeating the denied teardown is not authorized. Owner: dispatch/provider deadline teardown and existing Job process owner; next action: reassess supported per-attempt teardown without weakening parallel-worker ownership or retrying taskkill. Final checks, runtime, exact patch and disposition: ../stage-stall1-report.md. No actual-provider, ladder or full-vision acceptance claim. |

| STAGE-STALL1 round 2 (C18/C16/C14/C20, I5/I6/I9; TRIAGE DESIGN): HIGH review exposed split deadline/termination ownership; denied taskkill left the provider alive until batch cleanup. Siblings: parallel worker, review and acceptance attempts. | Each provider/review/acceptance command now uses its own existing held Windows Job owner, which owns its idle clock, fixed deadline, output and verified teardown. Dispatch publishes the actual private-bootstrap child PID through existing provenance; cancellation is validated first and committed only after a zero-process Job receipt. The outer batch retains only its overall bound and explicit stop. All taskkill paths and duplicate root-kill/cancellation/deadline loops are deleted. Private Jobs remove supervisor CPU from each activity clock, superseding round 1 pinned-member accounting and its optional handle gap. | First CLI iteration reached per-attempt deadline teardown (seven members to zero) while the sibling kept writing and then validated; canonical cancellation also verified zero. It stopped at the old mock-only fixture label (116.37s), now corrected because scripted provider replies execute through actual dispatch. Existing ShelfLife keeps output-only/CPU-only work (3s each across a 2s accelerated window) and true silence; the duplicate serial deadline case is removed in favor of the stronger parallel twin; recovery and 1j steering remain canonical. One review reply per journey executes through the real Job owner; unchanged semantic replies reuse their existing doubles; legacy/semantic acceptance share it. No new cases/files. Final exact patch, review, gate and runtime: ../stage-stall1-report.md. Windows suite evidence only; no actual-provider, ladder or full-vision acceptance claim. |

| STAGE-STALL1 round 2 independent review R1 (C14/C18/C25, I4/I6): REJECT of 1fdad95b1db28fb696a25ecafa2e05c539a91a140005e327159141fc359beee3 found interrupted acceptance could pass if its expected exit matched forced-stop exit 19. The earlier complete CLI run passed in 450.26s but did not contain that counterexample. | Acceptance now requires the canonical Job receipt to verify zero and contain no stop reason before matching an expectation. The persisted-scenario writer and real command runner reproduced false credit at golden line 1762 (RED/120.37s), then the same owner is repaired; no new test case or alternate semantic verdict. | The next focused run and final exact-candidate gate/second review are recorded in ../stage-stall1-report.md. Deadline/cancellation/steering findings above are retained; this source check does not establish actual-provider acceptance. |

| STAGE-STALL1 round 2 single full gate: CLI failed at golden line 1645 with KeyError evidenceDir while B was still awaiting canonical admission; the other 17 first-phase cases passed (155.39s). The browser phase remains separate. | Golden consumer now waits for both canonical evidenceDir bindings before reading either attempt. No product timeout, termination or release assertion changes; a missing binding cannot produce a passing twin. Owner: golden parallel-admission fixture; the direct CLI had passed 427.41s before this full-load interleaving was observed. | Final focused verification and the full-gate browser outcome are recorded in ../stage-stall1-report.md. The one authorized full gate was consumed; no fresh full-gate GREEN is claimed after this fixture correction. Further full execution requires renewed authorization. |

| STAGE-STALL1 round 3 (C18/C14/C20/C25, I5/I6; TRIAGE BUG, sibling cases: none): HIGH re-review confirmed Windows per-attempt Job ownership but found the retained POSIX subprocess.run fallback omitted launch publication and audit_control, breaking dispatch/acceptance consumers. Earlier golden coverage ran the Windows owner only. | Shared portable boundary now launches a new POSIX session/group, publishes the real payload PID, tracks native Linux kernel ticks/macOS libproc CPU counters plus output under a fixed deadline, performs one SIGKILL of only that group and requires kernel-observed group absence before zero-process credit. Callback/observation failures still stop the group; denied operations are not retried. Windows owner and consumers are unchanged; no dependencies or platform selectors added. Draft review caught ps failure gating teardown and rounded ps CPU hiding quiet work; both were corrected before the final gate. | Existing canonical provider golden now binds persisted provenance to the owner payload PID and requires its verified zero-member receipt. This Windows host has no os.killpg, so native portable execution is unavailable without OS changes: syntax/impact checks only, no Linux/macOS runtime claim. Final Windows gate, exact patch and evidence limitations are in ../stage-stall1-report.md; no actual-provider/ladder acceptance. |

| STAGE-INT5 P1 integration (C14/C18/C20/C25, I5/I6; TRIAGE BUG): portable poll() reaped the leader before the final group signal, permitting PID/PGID reuse; sibling paths are normal exit and exit near deadline. | The shared portable owner observes native ps live/zombie state without reaping, retains the leader through its one SIGKILL, verifies no live group members remain, then reaps; no group lookup or signal follows reaping. Zombies retain identity but cannot execute; Windows source is unchanged from STALL1. Existing EXEC4/STALL1/main golden behavior and audit history are merged, with actual size ceilings ratcheted downward. | Native POSIX runtime is unavailable on this Windows host; portable evidence remains static. Initial focused CLI 63.54s failed successor publication after integration edited loaded engine bytes during execution; this is invalid mixed-candidate evidence and is superseded only by the frozen-candidate runs in ../stage-int5-report.md. No denied operation, desktop control, actual provider or acceptance claim. |
| STAGE-INT5 discovery (C20/I9; TRIAGE BUG): direct cold imports of plan_store.status or mcp_planner encountered the existing approvals/planner_jobs/dispatch/worker_packet/roundtable circular import. | Owner: core import dependencies; deferred as outside the lane integration. Importing dispatch first follows the working golden initialization path; no production fallback added. | Exit: a clean interpreter imports mcp_planner and reads canonical status without an import-order workaround. This observation is separate from the mixed-candidate focused failure. |
| STAGE-INT5 golden merge correction (C14/C24, I4/I6; TRIAGE BUG): frozen CLI 111.60s proved deadline/sibling isolation, cancellation, independent recovery bounds and 1j steering, then rejected the real integrated result as no-integration. STALL1 predates main's concurrent HTTP integration declaration. | The golden now requires plan-validated from both semantic and legacy real scripted providers, preserving main's concurrent proof rather than weakening it to the older lane label. Existing requirement/integration result assertions remain. Owner: golden integration expectation. | Final frozen focused and full-gate results are recorded in ../stage-int5-report.md; initial failed runs are retained under C:/seldon/tests/stage-int5. |
