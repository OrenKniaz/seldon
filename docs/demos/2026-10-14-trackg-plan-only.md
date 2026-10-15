# Track G: two complex plans, with the oracle boundary exposed

## Evidence and scope

These are **plans only**: no product build, product acceptance or build-quality claim. Evidence root: `C:/Users/oren/claude-worktrees/`. Briefs: `_assignments/trackg-editor.md`, `trackg-platform.md`, `_trackg_common.md`. Reports: final `agent_message` in `_assignments/logs/trackg-editor-222013/events.jsonl` and `trackg-platform-222015/events.jsonl`. Plan sources: `_runs/trackg-{editor,platform}/project/manifest.json` and `commands.jsonl`; editor `final-nodes.json`, `final-facts.json`, `change-comparison.json`; platform `impact-prediction.json`, `impact-actual.json`.

Node counts were checked against the logged final `nodes` projections, bead counts and dependency paths against manifests, and quality/gate outcomes against command results. Reviewer failures and weakest-area assessments below are attributed to the planners' recorded reports. All approvals used the supplied proxy; owner approval is not established.

## Plan inventory

| Recorded item | Collaborative editor | Data platform |
|---|---:|---:|
| Vision nodes | 1 | 1 |
| Feature nodes | 10 | 10 |
| Story nodes | 10 | 10 |
| Requirement nodes | 40 | 48 |
| Decision nodes | 11 | 12 |
| Design nodes | 6 | 6 |
| Bead nodes | 40 | 48 |
| Test nodes | 40 | 49 |
| Total nodes | 158 | 184 |
| Disjoint owned paths | 157 | 71 |
| Acceptance scenarios authored | 14 | 11 |
| Critical path, beads / edges | 15 / 14 | 21 / 20 |
| Final planner quality | 72/100 | 72/100 |

Each latest quality result records six passes, one warning, two failures and two not-yet checks. Its first-moment “proved” label reflects linked planned coverage, not executed product evidence. The platform planner reported 122 protected test methods and a critical-path estimate of 30.5 owner-hours; these are authored coverage and planning estimates.

Editor critical path: `B01 → B06 → B26 → B07 → B08 → B09 → B10 → B17 → B19 → B20 → B21 → B24 → B37 → B39 → B40`.

Platform critical path: `B01 → B02 → B07 → B11 → B13 → B14 → B15 → B16 → B17 → B20 → B22 → B23 → B24 → B27 → B25 → B34 → B35 → B36 → B43 → B47 → B48`.

## Collaborative editor

The vision is browser-based rich-text co-editing with presence, comments, version restore, hours of offline editing, role/document authorization, OIDC and durability. The brief specifies up to 50 concurrent editors per document and same-region remote-edit p95 below 200 ms. These thresholds are requirements, not measured results.

Two invocations per specified reviewer were used: Codex `gpt-6.1-sol`, medium effort; Claude `claude-haiku-4-5-20251001`. Codex returned 14 then 12 findings. Final dispositions: 21 resolved planning defects and five deferred findings. Post-second-round corrections received no independent re-review.

Claude failed both invocations. The planner reported approximately 234,745 and 208,667 prompt tokens against a 200,000-token limit; the CLI surfaced a temporary-directory lock error instead of the primary size failure. These diagnostic figures come from the worker report, not a fresh provider run.

Injected change: revoked offline users' edits must never merge; preserve them privately for copy-out and notify the document owner that rejected edits exist. Prediction named 10 directly affected beads. Immediate actual comparison found 13 directly changed beads and 49 nodes, missing B01 protocol ownership, B24 privacy verification and B27 offline recovery routing. The explicit conservative dependency cone contains all 40 beads.

The plan added private local recovery, content-free owner notification after reconnect, permanent quarantine of old authorization epochs, and revoke→regrant→first-reconnect handling. It distinguishes bytes already in flight from transmission after observed denial and requires zero unauthorized shared commits. Later review corrections affected 16 beads substantively and all 40 through schedule cleanup; these are separate from the initial injected-change comparison.

`impact` had no published baseline. `change-comparison.json` uses saved canonical-node inputs plus an explicit dependency closure; it is **not** a published Harness impact report.

Furthest gate attempted: **publication**. `advance --to publish` returned success, but `publish` refused with `plan-publication-evidence-required`: no passing current-manifest audit and no successful current-revision review. Audit returned `spine-template-missing` for the absent oracle. No fake oracle was supplied; no publication or implementation followed.

Planner's weakest three areas: proof independence; durability/retained-CRDT-state feasibility; bead sizing/integration readiness. Regional durability and bounded tombstone recovery remain unproved, while several estimates were judged optimistic.

## Data platform

The vision combines operational databases, billing REST and CSV ingestion; tested SQL modeling with column lineage; schema-impact forecasting; idempotent historical backfills; role-controlled metrics and drill-down; PII masking and access audit. The brief's cloud budget is below $2,000/month; the planner explicitly did not establish compliance with that budget.

Two invocations per the same specified reviewers were used. Initial review: Codex 12 findings and Claude 16. Final review: Codex 10 findings; Claude failed with a reported temporary-directory `WinError 32`. Final dispositions across 38 findings: 26 resolved, 11 deferred, one rejected, with reasons, owners and exit checks.

Deferred defects include deployment authorization tied to the candidate, same-revision representation equivalence, retrieval-history sealing, pooled-session audit identity, persisted quality-rule settings, stale dependency prose, payload version versus effective date, early schema ownership and mixed known/UNKNOWN net aggregation. Other deferred findings concern misleading proof wording and truncated review packets. Disposition does not make the candidate execution-ready.

Injected change: billing `amount` becomes `amount_net` plus `tax`, and `currency` becomes an object, effective from a date. Prediction: 37 affected beads. Local actual comparison: 36 directly edited and 37 affected through dependencies, matching the predicted affected set; B29 was dependency-affected.

Affected set: `B03, B04, B08, B11–B30, B32, B34–B37, B40–B48`. The plan added versioned gross/net/tax normalization and nested-currency lineage, breaking-change detection/impact, semantic admission, migration/replay, reconciliation/rollback and digest-bound consumer acknowledgements. Assertions cover duplicate replay and historical net remaining UNKNOWN. Effective date and vendor semantics remain assumptions.

Canonical `impact` refused because there was no published revision. `impact-prediction.json` and `impact-actual.json` record local manifest snapshots and dependency closure, **not** canonical published impact evidence.

Furthest phase reached: **roundtable**. Audit returned `spine-template-missing` for the absent oracle. `publish` refused with `plan-publish-phase-required`, because the planner preserved that stop rather than advancing past it. No passing audit, final Claude review, publication, implementation or acceptance is recorded.

Planner's weakest three areas: contract consistency; verification precision; feasibility/sizing. Executable examples and dependency prose still conflict, generic assertions and packet truncation weaken verification, and vendor semantics, audit support, costs and spike durations remain unproved. The planner explicitly judged the result below the requested “world's best engineering teams” bar.

## Merged guide gaps

The following merges repeated symptoms across the planners while preserving case-specific evidence.

| Gap or confusion | Recorded cases |
|---|---|
| Mandatory full oracle blocks honest publication of a large prospective plan; no alternative documented | Both |
| Gate display can advance to publish without required audit/review; platform's publish attempt instead fails on phase | Both |
| Quality exits successfully with missing audit/review and calls planned first use “proved” | Both |
| Unpublished revisions cannot be compared by canonical `impact`; local comparisons required | Both |
| Python module and unit/e2e verification shape forces an extra TypeScript bridge; worker-controlled bridge summaries lack independent proof integrity | Editor |
| Review packets exceed context limits or truncate protected assertions; reducing duplicated context was insufficient | Both |
| Temporary-directory cleanup/lock errors hide or replace provider failures | Both; editor identified prompt-size cause |
| `show --body` requires `--test`, contrary to the guide's apparent optional syntax | Both |
| Spine metadata edits clear stored acceptance scenarios, requiring resubmission | Editor |
| Recovery recommends more review after the assignment's invocation budget is exhausted | Both |
| `Next:` audit priority conflicts with the phase table's roundtable-first order | Platform |
| Status says `zero-open-clarifications` despite no open questions and different actual blockers | Platform |
| `accept-scenarios --derive` produces empty steps/expectations; executable scenarios must be supplied manually | Platform |
| Generated journey diagrams become story-title summaries instead of detailed interactions | Editor |
| Prescribed `uv` invocation creates a worktree `.venv` | Both |
| Windows CP1252 printing failed in the planner journal wrapper; corrected without duplicate clarification | Editor |

## Product implications

These are lane assignments from the reports brief, not verified claims that repairs have shipped.

| Gap / required improvement | Fix lane or disposition |
|---|---|
| Honest proof labels, quality/error exit codes, phase gates, accurate pending/next recovery, executable scenario guidance and scenario preservation | `planfix5` |
| Compare unpublished changes through `impact --since`, with direct impact distinguished from dependency closure | `planfix5` |
| Linkshelf's missing real producer/consumer cross-bead contract check | `planfix5` |
| Oversized/truncated dossiers, sharded packets, and surfacing primary provider errors instead of cleanup failures | `reviewscale` |
| Language-neutral verification, avoiding an ad hoc Python→TypeScript summary bridge | `polyglot` |
| Prospective publication for plans without an oracle, while retaining truthful evidence gates | **Still open** |
| Diagram fidelity, `.venv` side effects and journal encoding guidance | Owner **not recorded** |

Independent proof integrity and deferred product contracts also require execution-owner work; assigning a tooling lane does not discharge those requirements. A prospective publication mode must distinguish a reviewable plan from a proven build without fabricating oracle evidence.

## Recorded planner efficiency and limits

`summarize_events` through frozen/offline `uv` on the worker logs records editor: 31 commands, 4,219,265 input tokens, 4,042,240 cached-input tokens, 50,645 output tokens; platform: 40 commands, 4,328,028 input, 4,161,152 cached input, 60,549 output. Commands are shell-event counts, potentially batching many CLI calls. Duration and complete reviewer-inclusive cost are **not recorded**; token fields are not a billing comparison.

Native product checks, achieved latency/durability, actual cloud spend, user acceptance and build quality are **not recorded**. Quality scores and planning coverage cannot establish them. This evidence supports substantial planning and concrete tooling limitations, with both candidates still blocked before honest publication and execution.
