# Legacy capability audit for the local Seldon extraction

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
