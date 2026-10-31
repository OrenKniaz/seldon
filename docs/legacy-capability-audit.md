# Legacy capability audit for the local Seldon extraction

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
