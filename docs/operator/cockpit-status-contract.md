# Optional read-only cockpit status

`GET /api/status` retains every existing key and adds optional `cockpit` with
`source`, `vision`, `answers`, `areas`, `capabilities`, `waves`, `crew` and
`chronicle`. Consumers must tolerate missing/null sections and child fields.
Null means unavailable; it is not zero, agreement, readiness or permission.
The projection creates no files/stores and launches no providers. Plan reads
reuse the existing SQLite in-memory snapshot. Mutable run and attempt reads
retain the participating `record_io` protocol and bounded activity observer.

| Fact | Source and limit |
| --- | --- |
| `source` | Exact editable/published plan revisions. `runBinding` distinguishes current versus historical/unbound fallback runs. A current run requires a fresh publication and matching original run revision. |
| `vision.title/promise` | Canonical vision node title and vision/text, not a generated product promise. |
| `vision.acceptance` | Unchanged canonical `accepted`, `recordedUserAccepted`, `isProxy`, `approvedBy`, `approvalOrigin`, `humanOriginVerified`, `invalidReason`. Recorded-unverified user approval is not verified personal acceptance; nothing is copied as capability acceptance. |
| `answers.where` | Recorded plan phase, label “Current”, no inferred execution wave ID/count. |
| `answers.blocking` | Explicit open major/blocker findings and blocked/held/failure work records from a current bound run. Missing owner/action/agreement facts are omitted. Suggestions, prose and arbitrary run fields do not become agreed repairs. |
| `answers.needsYou` | Recorded clarification questions and actual approval/acceptance-approval gates, answered in chat. Empty questions do not imply review readiness; `reviewReadiness` retains the separate existing advisory projection. |
| `areas` | Ungrouped: the current canonical spine has no area node or contracted grouping metadata. No presentation taxonomy is invented. |
| `capabilities` | Canonical feature IDs/titles/state. Descendant bead IDs define branch scope. Shared beads count once in a branch and once globally, not once per visible story/requirement/test card. |
| `waves` | Existing `PlanStore.outcome_waves()` read on the same in-memory copy after prior read transactions end. `projection` preserves the original draft/stale reasons, unconfirmed lock and unavailable runtime binding. Declared order never means current/done. Unvalidated stale carriers do not supply item lists. |
| `crew` | Existing `currentAgents`, copied only for a current bound run. Identities are recorded; activity quietness is advisory and process liveness remains unknown. Terminal cancellation does not establish descendant cleanup. |
| `chronicle` | Actual phase history and projected run events with source refs. Unbound run events are explicitly historical; absent timestamps are not invented. |

`vision.items` counts unique canonical beads globally. Capability `items` counts
only its trace-linked beads. `done/active/blocked/later` describe recorded work
state only and remain null for stale, missing or ambiguous membership/state.
They are not execution or proof scores. Draft wave `itemCounts.total` counts
detailed bead IDs only; sketches are not executable beads. Wave progress stays
null because this slice establishes no live-wave runtime binding.

Proof units are separate:

- `productScenarios`: **product scenario checks**, using the original latest
  acceptance run row, matching record identity, explicit product subject,
  current canonical plan/scenario/build binding and current scenario results.
  The canonical acceptance reader establishes freshness before presentation.
  Explicit mocked, oracle, missing, stale or mismatched evidence yields null.
  These command checks do not independently prove real use/unmocked execution.
- `requirementValidation`: **work items with requirement validation**, null in
  this slice with `proofUnavailableReason`. The original current claim/spec/
  publication and unmocked integrated-execution bindings are not established
  by the existing projected run. A completed/validated status or claim string
  cannot populate this unit.

Global scenario checks include every stored canonical scenario. Per-capability
checks join an actual scenario ID to an acceptance node with exactly one feature
parent. Unlinked or ambiguous scenarios remain in `vision.scenarioScope` and the
global denominator; they are never reassigned heuristically. Zero linked checks
does not become a passing capability. No mixed scenario/work-item percentage is
defined. Source refs point to existing acceptance run/scenario identities.

This is a presentation view, not a new evidence adapter, store or approval gate.
Original whole-vision/live-wave binding, verified personal acceptance and actual
UI usability remain separate obligations. Tests using injected publication,
review or approval projections are synthetic contract fixtures, never live proof.
