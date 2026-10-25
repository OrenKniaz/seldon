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
