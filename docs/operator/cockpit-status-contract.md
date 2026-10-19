# Optional read-only cockpit status

`GET /api/status` retains every existing key and adds optional `cockpit` with
`source`, `vision`, `answers`, `areas`, `capabilities`, `waves`, `crew` and
`chronicle` and `recordedReviews`. Consumers must tolerate missing/null sections and child fields.
Null means unavailable; it is not zero, agreement, readiness or permission.
The projection creates no files/stores and launches no providers. Plan reads
reuse the existing SQLite in-memory snapshot. Mutable run and attempt reads
retain the participating `record_io` protocol and bounded activity observer.
The active-confirmation fixture permits only SQLite WAL-index reader bookkeeping
at SHM offsets 100–119, the five documented read-mark slots in the
[SQLite WAL format](https://www.sqlite.org/walformat.html). It still requires exact
file keys, every mtime, SHM length and all other SHM bytes, plus exact DB/WAL and
ordinary-file bytes. This fixture-specific exception allows no logical plan,
approval, database or WAL mutation; existing read-only tests stay unchanged.

| Fact | Source and limit |
| --- | --- |
| `source` | Exact editable/published plan revisions. `runBinding` distinguishes current versus historical/unbound fallback runs. A current run requires a fresh publication and matching original run revision. |
| `vision.title/promise` | Canonical vision node title and vision/text, not a generated product promise. |
| `vision.acceptance` | Unchanged canonical `accepted`, `recordedUserAccepted`, `isProxy`, `approvedBy`, `approvalOrigin`, `humanOriginVerified`, `invalidReason`. Recorded-unverified user approval is not verified personal acceptance; nothing is copied as capability acceptance. |
| `answers.where` | Recorded plan phase, label “Current”, no inferred execution wave ID/count. |
| `answers.blocking` | Explicit open major/blocker findings and blocked/held/failure work records from a current bound run. Missing owner/action/agreement facts are omitted. Suggestions, prose and arbitrary run fields do not become agreed repairs. |
| `answers.needsYou` | Recorded clarification questions and actual approval/acceptance-approval gates, answered in chat. Empty questions do not imply review readiness; `reviewReadiness` retains the separate existing advisory projection. |
| `answers.rightNow` | Finite `code`, plain `text`, editable-plan `sourceRevision` and public `refs`. Priority: recorded questions, confirmation/acceptance prerequisite, current recorded review error/points, other bound blockers/build, then qualified recorded/unknown status. No phase or reservation implies present activity. |
| `recordedReviews` | Allowlisted completed roundtable observations from the same in-memory read copy. Envelope: `availability` (available/partial/unavailable), editable `sourceRevision`, `currentManifestRevision`, fixed `reason`, `rounds` and `unboundRowCount`. Null/partial is not known zero. No review-attempt reservations, packets, paths, private correlation IDs or raw event/shard bodies are exported. |
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

Optional `vision.proofAccounting` adds a revision-aware requirement/evidence map
from original published membership, canonical claims and receipts. See
[proof accounting](proof-accounting.md) for exact identity, invalidation and
missing-producer semantics. Matching content at an earlier claim and a later
candidate is reported separately from admitted execution. Genuine assignment/
runtime admission is still unavailable, so this map grants no work-item credit
or whole-vision acceptance. Existing scenario and approval fields stay intact.

Global scenario checks include every stored canonical scenario. Per-capability
checks join an actual scenario ID to an acceptance node with exactly one feature
parent. Unlinked or ambiguous scenarios remain in `vision.scenarioScope` and the
global denominator; they are never reassigned heuristically. Zero linked checks
does not become a passing capability. No mixed scenario/work-item percentage is
defined. Source refs point to existing acceptance run/scenario identities.

This is a presentation view, not a new evidence adapter, store or approval gate.
Recorded rounds group exact round/manifest revision identities. `relevance` is
current only for an exact current **manifest** match, historical for a known older
manifest, otherwise unknown. Conflicting revisions for one round remain unknown.
Round completion requires agreeing completed-event reviewer/provider identities
and completed stored results; errors remain errors and missing agreement is unknown.
Reviewer results retain original counts, timestamps, exit/invalid outcomes and
mock/provider/packet-binding flags. `requestedModel` means **Model requested**,
never observed effective model. Missing optional legacy fields remain null.

Clusters preserve recorded canonical membership, all member text/attribution,
suggestions and distinct open/resolved/rejected/deferred dispositions and reasons.
`runLinkage` is recorded only for an exact round/revision/reviewer/provider join;
otherwise unknown. No model attribution, theme, agreement or resolution author is
inferred. Broken canonical membership yields unavailable details. Known zero
findings, source-only simulations and completed observations grant no acceptance.
Old consumers may omit these additive fields; the UI uses a qualified recorded
status fallback rather than rebuilding an activity classifier.
An already-existing align/vision-lock approval item may additionally contain
`recordedUnderstanding`: title, public gate/editable revision, known plain-text
understanding sections, `chatConnection: unknown` and the connection-unknown
notice. The latest global recorded request must match current gate, phase,
revision, canonical vision and deterministic card; any response or later phase
transition suppresses it. No item is created and acceptance cannot borrow this
card. Private request keys/raw cards, arbitrary vision metadata and response
content are never projected. This excerpt is not the complete approval card and
does not establish an active prompt or grant any approval credit.
Original whole-vision/live-wave binding, verified personal acceptance and actual
UI usability remain separate obligations. Tests using injected publication,
review or approval projections are synthetic contract fixtures, never live proof.
