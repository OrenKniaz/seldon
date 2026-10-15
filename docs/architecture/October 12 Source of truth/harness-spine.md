# Planning spine v1

`spine.json` is the single structured planning source. Markdown is generated,
never an independent writer. This P1 core slice adds no planner workflow,
dispatch, persistence migration or completion authority.

## Source and validation

The envelope is `{schema: "codex-harness/spine-v1", name, nodes}`. Every node has
`id`, `kind`, `title`, `text`, `parents` (ids), and `fields` (JSON object).
There is exactly one parentless vision. Allowed child ← parent kinds are:
feature ← vision; story ← feature; requirement ← story/feature;
decision ← vision/feature/requirement; design ← requirement/decision;
bead ← requirement/design/decision; test ← requirement/bead.

Beads require `ownedFiles`, `proofModule`, `dependsOn` (bead ids), and `lessons`
(decision ids); `provider` is optional. Tests require `module` and `level`
(`unit` or `e2e`). Dependency and lesson references are checked. Execution
dependencies are separate from planning ancestry and do not propagate impact.
Orphans are admitted so incomplete plans can report gaps without blocking edits.

`SpineError.code` identifies duplicate ids, unknown parents, forbidden parent
kinds, cycles, vision count and invalid bead fields with the assignment's codes.
Malformed JSON/schema and test fields additionally use `spine-schema-invalid`
and `spine-test-fields-invalid`. Cycles are checked before parent kinds.

## Revisions and navigation

`Spine(data)` creates a snapshot; `node(id)` and `to_dict()` return copies.
Edit a copy and construct a new spine. `parents`/`children` return sorted ids;
`descendants` and `ancestors` exclude self. `trace_up` returns every id path
starting at the requested node and ending at the vision.

Local revisions hash UTF-8 canonical JSON: sorted keys, compact separators,
literal Unicode, and sorted parents. Only the six node fields participate.
`plan_revision` hashes sorted `id:revision` lines joined by `\n`, with no final
newline. Node and parent order do not affect revisions or rendered output.
`save_spine` writes sorted nodes/parents/keys, two-space indentation and LF.

## Coverage, impact and compilation

`coverage_gaps()` returns `{id, gap}` entries for requirements lacking descendant
beads/tests, beads without vision ancestry, and tests without bead/requirement
ancestry. Gap names are `requirement-without-bead`, `requirement-without-test`,
`bead-without-vision`, and `test-covering-nothing`. Gaps are advisory: R6 in
tip-split has an integration test but no bead and is reported honestly.

`diff(old, new)` returns sorted added/removed/changed ids. `impact` walks the
changed nodes and new descendants, plus removed nodes' old descendants, once.
Affected beads/tests include retired ids; untouched beads are surviving new ids.
Each rendered file carries its own document revision (a hash of its body), so an
edit marks stale only the documents that actually render the changed nodes.
Impact derives `staleDocs` from the kinds of the changed nodes without rendering
(for example, a decision maps to `VISION.md`, plus `PLAN.md` when it is a bead
lesson, and a requirement maps to `REQUIREMENTS.md`, `PLAN.md` and `TRACE.md`).
Tests check this against real old/new renders. A name-only edit does not alter node revisions
or Markdown. In tip-split, D3 affects A and `test_money`, B remains untouched, and only `VISION.md` is stale.

`compile_manifest` projects the existing provider-sample shape, copying root
`validation` and `requirementFiles`, adding `planRevision` (which also folds in the
template requirement files' content when a template is given), requirement
ancestor `requirementRefs` and all decision/design/requirement `specRefs`, and expanding lesson decision text. It does not
confirm lessons: the integrator must validate before publishing them. The
single e2e node supplies integration module/requirements; multiple e2e nodes
raise `spine-integration-invalid` rather than silently dropping proof. Existing
`graph.load_plan` remains the manifest ownership/path consumer and validator.

## Generated views and CLI

`render_markdown` deterministically produces `VISION.md` (vision/decisions),
`REQUIREMENTS.md` (features/stories/requirements), `PLAN.md` (beads/designs,
ownership, dependencies, refs and proof), and `TRACE.md` (coverage matrix/gaps).
Each begins `<!-- Generated from spine.json; document revision <12 hex> -->`.

Use `python -m codex_harness.core.spine render <spine> <outdir>`,
`compile <spine> <out.json>`, or `impact <old> <new>` (JSON on stdout).
Generated views and compiled manifests are projections; edit the spine source.
