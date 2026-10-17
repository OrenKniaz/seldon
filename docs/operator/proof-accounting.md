# Revision-aware proof accounting

The optional `cockpit.vision.proofAccounting` map reports original evidence and
its current bindings. It is read-only, not a completion gate or acceptance
authority. It creates no stores, launches no workers, runs no validation and
does not change dispatch, reuse or impact policy.

`source` contains `planRevision` (editable Spine), `publicationRevision`
(published manifest, including protected inputs), `runId`, `candidateCommit`,
`candidateTree` and `evidenceRefs`. The two plan revision domains need not be
equal. Candidate identities refer to the product integration repository, never
the Harness implementation checkout. Missing identities remain null.

`requirements` is null without original published requirement membership.
Otherwise each published requirement has its original full node `revision`,
unique trace-linked `beadIds`, deduplicated `claimIds`, `evidenceRefs`, `reasons`
and a `bindingState`: `unavailable`, `invalidated` or `bound-evidence`. An editable
requirement change or stale publication invalidates current use; it does not
erase the published requirement or shrink the denominator.

Per-row `claimObservations` retain each selected claim's `id`,
`sourceCandidateCommit`, `sourceCandidateTree`, `currentInputsState`,
`evidenceRefs` and `reasons`. `currentInputsState=current` means the original
claim hash, source commit/tree, canonical original receipt, candidate and spec
references agree, and canonical source/verification/dependency identities still
match the current product commit. It does **not** mean admitted execution.
An earlier claim survives an unrelated later commit. Source, dependency,
protected-test, proof-module, requirement or spec changes cannot retain it.
The latest recorded claim for a bead is authoritative for this observation;
an earlier passing history entry never substitutes for a newer failed claim.
An explicit `claimSummary.impacted` disposition invalidates the exact selected
claim ID even when re-observed content matches. Its original changed-input reason
and run-record reference remain visible, including that contradiction. A distinct
fresh claim with its own matching original receipt does not inherit an older
claim's disposition. A latest failed claim retains its recorded validation-failure
reason and cannot resurrect an older passing claim.
On a canonical resume, `sourcePublicationRevision` keeps the original bead
revision and `attemptPublicationRevision` keeps the matching request/result
revision. `acceptedPublicationRevision` reports the bead's recorded accepted
publication label separately. An old bead may reach claim checking through that
current accepted label, but the label grants no credit: current use requires
the actual current-publication revalidation receipt, selected claim, spec refs,
candidate and current input bindings to agree. Missing or wrong revalidation
stays unavailable or invalidated. This does not rewrite original attempt facts.
Identical repeated claims and shared beads do not create extra work items.

The reader uses the status API's existing in-memory SQLite snapshot and the
canonical dispatch claim-input comparison. Run, attempt and receipt reads use
participating `record_io` reads, bounded records and canonical-root containment.
The product repository must be the selected run's own clean integration repo;
foreign evidence paths and external Git directories are unavailable. Before
any Git access, the reader makes a confined bounded participating read of any `commondir`
indirection. Both the indirection file and resolved common metadata must remain
inside the canonical run root before any Git access. Missing/unreadable targets,
malformed indirection and external common metadata leave candidate identity
unknown with an explicit reason. Normal repositories need no `commondir` file.
Git status uses `--no-optional-locks` to avoid refreshing the index. Read failures are
reported, not retried through another path.

`missingDependencies` gives explicit `code`, `description` and `refs`, including
missing publication, run/candidate, original claim/receipt, attempt originals and
stale bindings. `reason` explains unavailable overall observation.

For provider-normal attempts, `localProcessProvenance` compares the controller's
create-once `attempt/process-provenance.json` with the selected attempt and its
request/result originals. It binds the original plan, packet bytes, ownership,
launch configuration, owned child PID and locally observed start facts.
Missing, foreign, changed or incomplete originals cannot establish consistent
local provenance; legacy provider records are not sealed retroactively.
Consistent mock records retain their source-only labels.

This is participating local provenance, not same-user authentication, current
PID liveness or sandbox attestation. The [October 16 scope decision](../decisions/2026-10-16-basic-security-port-scope.md)
backlogs stronger assignment/admission identity. No external host service or
lease/token registry is a prerequisite for this local observation.

Local provenance and claim correspondence alone do not establish actual-worker
validation. The reader reports `actual-worker-proof-unavailable`, never emits a
`bound-evidence` row, and keeps `requirementValidation` null. A supplied
`didExecuteRealProvider`, validated status, matching local receipt, mock, oracle
or unmocked-shaped fixture cannot supply actual worker or whole-vision proof.

Requirement rows, unique work-item validation, product scenario checks and
acceptance are separate units. All unlinked/ambiguous scenarios remain in the
existing global scenario denominator. Existing accepted/proxy/origin fields
pass through unchanged; nothing here verifies human identity or supplies a
whole-vision percentage, completion bit or supervision-savings claim.

Regression evidence uses disposable canonical SQLite publication, Git,
`record_io`, fake workers and local test commands. Those fixtures establish
reader semantics only; they do not establish actual worker or full-vision proof.
