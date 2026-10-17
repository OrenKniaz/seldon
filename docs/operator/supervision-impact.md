# Supervision and impact observations

Run a read-only reconciliation against original run evidence:

```text
python -B -m codex_harness.core.plan_cli --workspace <planner-project> supervision-report --run <original-run-directory> --baseline-run <original-baseline-run-directory> --forecast <product-repo>/impact-forecast.json --interventions <explicit-log.json> --machine-json
```

The result is JSON with `isError` and `data`. Missing evidence is represented by
nullable observations and `missingDependencies`, rather than a fabricated zero
or a successful prediction. Omitting optional forecast or intervention inputs
is supported. This command does not start planning, initialize/migrate a store,
write run records, change dispatch/reuse policy, launch providers or certify
completion. Input JSON files are limited to 1 MiB. Inferred evidence and product
repository paths must remain under their canonical workspace/run roots.
The product repository must have its own contained `.git` directory. Linked Git
metadata files and escaping metadata are unsupported and refused before Git
reads; there is no alternate-path, trust or configuration repair.
If a contained Git directory has `commondir`, the bounded indirection record
must remain inside that metadata directory and its resolved common directory
must remain in the canonical workspace/run roots. Missing, malformed or escaped
common metadata stays unknown before any Git read.

`source.planRevision` is the editable canonical spine identity.
`source.publicationRevision` also binds protected inputs; the original dispatcher
record's `planRevision` is reported as `runPublicationRevision`. These domains
can legitimately differ. The implementation base `2466c259...` is reported
separately from the product's current Git commit/tree. Each original claim keeps
its own source candidate, including a valid older candidate. Actual changes use
the original baseline/outcome candidate pair, not an unrelated later HEAD.
Unavailable original integrated candidate/tree facts stay unknown.

`impact` retains the canonical `PlanStore.impact_since` result. Its reporting
breakdown separates changed spine nodes, their semantic trace, dependency
consumers and conservative protected-input proof effects. These are overlapping
dimensions, not a summed metric. Generated planning documents are protected
inputs too: editing a requirement can correctly dirty every bead's proof.
The actual Git range separately reports changed source owners, dependency proof
consumers, untouched source owners and document tree entries. File changes alone
do not establish semantic progress.

`actual` keeps the original resume and claim summaries with evidence references.
Redispatch bookkeeping includes mocked source attempts; it is not verified real
worker execution. `actualWorkerRedispatchedBeads` remains null because local
process provenance and bookkeeping alone do not establish actual-worker
redispatch. A consumer can be reused while a
fresh claim records its proof revalidation. Revalidation is an attempt, not a
claim of success. Historical revalidation rows without a fresh claim are not
counted again. Retained IDs are deduplicated, checked against original Git
identity and rejected as unknown when originals are absent, conflicting,
invalidated or contradicted by a current failure. These observations do not
resurrect an older retained ID when the authoritative latest same-bead claim is
explicitly impacted. A distinct fresh claim does not inherit an older ID's
invalidation. These observations do not
certify original receipt/current input validity or full-vision delivery.

## Frozen forecast input

A forecast is an existing JSON artifact inside the original product repository.
Its exact bytes must exist in the Git commit recorded by the original run's
`baseline.commit`, and that commit must precede the original outcome in Git
ancestry. The report never creates or retrofits that artifact. A caller-supplied
timestamp alone is insufficient. `createdAt` must predate original run start,
and baseline/target identities must match their canonical source records.

```json
{
  "createdAt": "2026-10-16T00:00:00Z",
  "baseline": {
    "runId": "original-baseline-run",
    "planRevision": "<baseline-spine-revision>",
    "publicationRevision": "<baseline-publication-revision>",
    "candidateCommit": "<original-product-commit>",
    "candidateTree": "<original-product-tree>"
  },
  "target": {
    "planRevision": "<target-spine-revision>",
    "publicationRevision": "<target-publication-revision>"
  },
  "predicted": {
    "redispatchedBeads": ["A"],
    "revalidatedBeads": ["B"],
    "reusedBeads": ["B"]
  }
}
```

Other optional prediction dimensions are `changedPaths`, `invalidatedClaimIds`,
`retainedClaimIds` and `docPaths`. An absent dimension stays null. For each
available dimension, misses are original observations absent from the forecast;
extras are forecast items absent from those observations. There is no whole
vision score or perfect-prediction fallback. Matching local process provenance
does not itself prove actual worker completion or a supervision improvement.
Stronger authenticated assignment identity is [backlogged](../decisions/2026-10-16-basic-security-port-scope.md),
not a prerequisite for the current local observation. Current producers do not automatically freeze
forecasts, and resumed-run evidence may lack a suitable pre-outcome baseline
artifact; that is an explicit producer dependency.

## Explicit intervention observations

An optional original JSON log supplies `runId`, timezone-qualified `windowStart`
and `windowEnd`, declared `coverage` (`complete` or partial), and `events`.
Each event has a stable `id`, `at`, and recorded `origin`: `human`, `proxy`,
`agent-driver` or unknown. Unknown labels remain unknown. Caller labels never
establish independently verified human origin.

Known event/duration subtotals remain separate from complete totals. Identical
aliased event IDs count once; conflicting aliases make completeness unavailable.
`activeMinutes` requires finite nonnegative durations plus compatible
`activeStart`/`activeEnd` intervals inside a complete observation window.
Overlapping or absent intervals keep total active minutes null. Duration sums
by origin are recorded workload observations, not inferred operator elapsed
time. Missing logs/window/duration/coverage stays unknown. Idle time is never
invented by subtracting activity from wall time. Model tokens, file quietness
and run wall time do not become human minutes or semantic progress.

There is no executed matched native detailed-prompt baseline. Consequently
`supervisionSavings` remains null. Disposable SQLite/Git and synthetic records
test these reader semantics; they establish no actual provider, personal
acceptance or full-vision achievement.
