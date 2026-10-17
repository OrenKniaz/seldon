# Wave refinement

A multi-wave publication keeps the complete product Spine and obligation register.
Its ordinary execution manifest contains the real beads in the contiguous detailed
prefix W1 through WN. Later waves contain sketches. There are no executable sketch
placeholders. Every real bead must belong to the prefix, including its prerequisites.
The canonical compiler derives `waveScope`; callers cannot select arbitrary beads.
Non-wave and complete single-wave manifests retain their existing identities.

Each boundary has one protected acceptance node in its `exitScenarioIds` with
`fields.waveBoundaryIntegrationTestId` and `fields.waveBoundaryIntegrationModule`.
The ID resolves to a real test with that exact module and cumulative requirement
trace when the boundary is detailed. Its protected template file must exist. The
compiler selects that test for `manifest.integration`. Keep the unique global e2e
test in the full Spine; a scoped pass does not claim full-product acceptance.

Confirm the displayed map-containing plan at the existing plan-to-roundtable step:

```powershell
python -m codex_harness.core.plan_cli --workspace PROJECT advance --to roundtable --by user --statement "The actual approval words"
```

This event records the original Spine, protected product digest, map revision and
exact supplied identity/statement. Explicit proxies use `--by proxy:NAME`; their
identity, `confirmationOrigin=proxy` and `isProxy=true` remain visible in the lock
and `waves --json` projection. This preserves supplied provenance, without proving
human identity. A bare or planner-only transition, earlier vision confirmation, or
historical event without the map binding cannot source a wave-product lock.
The vision lock retains its existing meaning. Normal compile, oracle audit,
roundtable and publication gates still apply to the current scoped revision.
Repeating confirmation of unchanged product intent keeps the current immutable
publication's still-valid original sourced lock. The new confirmation remains a
separate history event, including its own user/proxy label. Protected product
changes still invalidate that old lock; snapshots are never rewritten or relabelled.

After the unchanged dispatch path produces its canonical integration verdict:

```powershell
python -m codex_harness.core.plan_cli --workspace PROJECT wave-exit --run RUN --through W1 --json
python -m codex_harness.core.plan_cli --workspace PROJECT refine-wave W2 --run RUN --prepare --json
```

`RUN/evidence/run.json` must describe exactly `RUN/repo`, the current publication,
selected integration proof and a clean current commit/tree. The existing
integration owner's claim-input comparison is reused once for that verdict.
The exit route runs the stored, executable, revision-bound scenarios for W1..WN
using the existing isolated scenario runner. It rechecks all bindings afterward.
It writes a separate append-only cumulative-exit event; full acceptance records
and human/proxy acceptance remain separate. Every current boundary needs fresh
cumulative exits. Earlier publication results stay history.

Prepare extracts pending producers' owned Python/JSON interfaces from regular
Git blobs on that candidate. Python AST signatures, class methods and annotated
fields are read statically. Product modules are never imported. JSON is parsed
strictly and no remote schema references are fetched. Unsupported shapes remain
unknown. Contexts bind the exact publication, lock, map, nodes, exits and blobs.
Dataclass decorators/defaults and TypedDict optionality remain literal AST syntax,
including `total`, `Required` and `NotRequired`; they are not runtime evaluations.
JSON root references use `blob-oid:#`. Required unknown shapes and unresolved JSON
references remain material questions. Original sketch producer dependencies and
expected contracts must survive as real dependencies and producer-owned blob bindings.
Local Git reads have a 30-second timeout. Static selection refuses blobs over 1 MiB,
selections over 4 MiB, and contexts over 8 MiB with an unavailable diagnostic;
essential interfaces and product intent are never truncated to fit.

Capture a response as UTF-8 bytes with exactly these top-level fields:

```json
{
  "schema": "dot-wave-refinement-response-v1",
  "contextDigest": "digest from prepare",
  "refinements": [{
    "sketchId": "SK2",
    "nodes": [],
    "interfaceRefs": ["blob-oid:public_symbol"],
    "reason": "Observed interface and resulting change",
    "replacedAssumptions": ["Original assumption replaced"]
  }],
  "contradictions": [],
  "questions": []
}
```

`nodes` contains complete new bead/test Spine nodes, including requirement trace,
file ownership, dependencies and proof modules. Every target sketch must have
complete bead and test coverage before apply. Contradictions use `sourceId`,
`interfaceRefs`, `resolution` and `classification` (`detail`, `locked` or
`unresolved`). Known locked object-versus-observed-list conflicts always produce
an unapplied card. Required questions and incomplete batches block the whole apply.

```powershell
python -m codex_harness.core.plan_cli --workspace PROJECT refine-wave W2 --run RUN --response @response.json --expected-spine REV --json
python -m codex_harness.core.plan_cli --workspace PROJECT refine-wave W2 --apply PROPOSAL --expected-spine REV --json
```

The CLI reads at most 1 MiB once in binary mode. BOMs, duplicate keys, nonfinite or
overflowing numbers, trailing JSON and authority booleans refuse. Proposal identity
binds the exact captured bytes: whitespace/newline changes produce another ID.
Record prints a reviewable projected delta/cards/impact receipt with proposal/source
references and the raw digest. The exact captured UTF-8 stays in the canonical
append-only event, without being echoed into normal CLI text or JSON. Record changes
no nodes. Apply checks current sources again, validates the complete
candidate with existing compiler/graph owners, preserves the protected digest, and
writes all target nodes/detail in one SQLite transaction or rolls everything back.
Identical re-apply is idempotent while that applied draft remains current.

Apply produces a draft with readiness not run, review/publication required and zero
completed-real credit. The impact report includes old/new dependency closure,
contracts, downstream tests/requirements/waves and retired beads; downstream body
content is unchanged. Existing review gates remain. After publication P2, obtain a
current P2 integration verdict via dispatch/resume and rerun X1+X2 before W3.
Mock-reference execution remains `source-test-only`. Neither refinement nor these
local scenarios certify live providers, global acceptance or the full vision.
