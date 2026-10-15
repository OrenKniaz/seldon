# Planner → worker handoff

The published manifest is the worker contract. Generated docs are views of the
canonical spine; provider reports alone are not proof of completion.

## Planner declarations

- Vision `fields.validation`: Python arguments preceding the proof module.
  The current worker command/tool contract is Python stdlib unittest discovery;
  use `-B -m unittest discover -s <relative-test-dir> -p`.
- Vision `fields.requirementFiles`: extra protected files/directories (possibly
  empty). The test directory need not be repeated here.
- Each bead: exact relative `ownedFiles`, prerequisite bead ids in `dependsOn`,
  a discovery filename/pattern in `proofModule`, and decision ids in `lessons`.
  Ownership may be nested; parallel beads must have disjoint files.
- Test nodes: requirement/bead parents, `module`, and `level` (`unit` or `e2e`).
  One e2e module becomes the manifest integration proof.
- Put importable stubs and executable proofs in the template. Supply every
  affected owned file in the oracle for audit, or in `reference/` for mock dispatch.

## Harness derivations

- Compile derives requirements/spec revisions, dependencies, lessons and the
  integration selection from spine ancestry and bead/test fields.
- `compile --template` renders the four docs before hashing protected content.
  Compile adds those docs and the validation test directory to `requirementFiles`.
  Protected content, with normalized line endings, binds the manifest revision.
- The discovery directory comes from validation `-s`; the compatibility default
  without `-s` is `tests`. It must be safe and relative to the checkout.
- Packets use owned-file bodies, prerequisite Python interfaces, served spec text,
  the derived discovery directory, proof pattern and actual discovered test names.
  Commands run from the worker checkout; package layout need not be top-level.
- Workers cannot change protected inputs or files outside their exact ownership.
- Integration validates a candidate before advancing the integration branch.
  Claims bind candidate commit/tree, owned-source blobs, the whole test directory,
  docs, requirement files, proof selection and prerequisite identities.
- Resume updates protected files for changed revisions and reuses only passing
  claims whose relevant inputs still match; final-head assertions are rechecked.

## Fixed names and boundaries

- Rendered docs: `VISION.md`, `REQUIREMENTS.md`, `PLAN.md`, `TRACE.md`.
- Dispatcher package: manifest file plus sibling `template/`; mock mode also
  needs sibling `reference/`. A directory argument selects `sample.json`;
  an explicit manifest path may use any filename.
- PlanStore knows `<workspace>/template`; compile accepts an explicit template.
  Publication exports manifest snapshots and docs, not a self-contained code
  template: dispatch needs the sibling template prepared by the caller.
- Runtime names (`.codex-harness/plan.sqlite3`, mail/review stores, run/evidence
  files, integration/candidate branches) belong to Harness, not the product.
- `HEAD` means the actual Git candidate/integration head, not a product entrypoint.
  Python `.py` interface extraction and unittest selectors are current contracts.
- First-use audit uses the template and requires an oracle. Existing-data audit
  uses an explicit Git ref or automatic HEAD; dirty working code is not its baseline.
  Product first-use behavior still needs a meaningful clean-state proof.
- Reviews bind the current manifest. Open findings remain blockers; resolved and
  reasoned rejected findings are addressed; deferred findings remain known gaps.
- Reference mock dispatch runs real local proofs but reports
  `source-test-only (mocked provider)`, never `plan-validated` or live acceptance.
  Passing source proofs do not demonstrate real provider execution or delivery.

## Changing how evidence is bound
- Any change to a revision, hash or binding rule (integrated revision, scenario
  node revisions, manifest identity) must version the algorithm and say how old
  records are read. Old evidence must surface a precise reason (for example
  `revision-algorithm-upgraded`, `legacy-unbound`), never a generic "stale".
- Recovery re-runs the proof on the unchanged product and carries prior reviews
  and approvals forward only as audited rebound records that keep the original
  approver and proxy flag (`p accept-rebind`). A changed product never inherits.
