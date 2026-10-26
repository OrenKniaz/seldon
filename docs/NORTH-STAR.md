# Seldon North Star v4: the lean, smooth planner (Oren + CC, 2026-10-27)
Supersedes the planner parts of v3 where they conflict. Keeps v3's frame: wrap Codex / Claude Code / Copilot; a thin, provider-neutral core; never re-implement the host.

## One sentence
**Seldon helps a not-very-technical person build their dream.** It expands the idea, researches what's unknown, records their decisions, plans multi-lane work for fast delivery, then builds it in waves with design, integration and validation, so the result truly achieves the vision instead of something basic, mocked or missing.

## The journey
1. **Align:** understand the real intent. 1–3 sharp questions; the user approves the understanding.
2. **Expand (the heart):** product mode for rough ideas, quality-within-scope mode for precise specs. 3–7 upgrades with impact, plus quality bar and done; the user accepts/rejects/defers → **Vision Lock**.
3. **Research (when there are unknowns):** evaluation criteria first → exploration wave (survey, *invent* new or merged approaches, prototype, measure, tune) → results table → the user decides → re-plan.
4. **Spec:** requirements, structured acceptance scenarios (data, not code), recorded decisions, shared interface contracts.
5. **Multi-lane plan:** beads with contracts, ownership and dependencies, built for parallel workers; it must pass the "no hidden planner memory" check.
6. **Review:** exactly three jobs: expansion, integrity, spine.
7. **Waves:** Contracts (stubs + red proof) → Build (parallel lanes, design + code + unit tests, reviewed per bead) → Integration → Validation (the scenarios pass + qualitative acceptance against the Vision Lock's quality bar).
8. **Accept:** the user's own words.

## Principles
- **The planner writes semantics, never code.** Workers write all code.
- **Lean and smooth:** user touches are only Align, Vision Lock, real decisions and research choices. No approval prompts. Every refusal teaches.
- **Conditional machinery:** research and extra waves exist only when the plan needs them.
- **Real runs are the signal;** the complexity budget trends down; the drift guard applies to every slice (`PLANNER-CHARTER-AND-DRIFT-GUARD.md`).

## Measures (scorecard v3)
Semantic plan published with real expansion (≥1 accepted upgrade visibly in the spine); every requirement → scenarios; every shared interface → one contract; review clean on the 3 jobs; Contracts wave proves all scenarios red; small product planned in ≤ 15 min; zero prompts; then waves deliver a product that passes the scenarios and the qualitative check.
**Planner Satisfied** = 2 consecutive v3 passes on different products → Oren tests weekly.

## Companion documents
`PLANNER-CHARTER-AND-DRIFT-GUARD.md` (rules + guard), `SEMANTIC-PLANNER-WAVES-2026-10-27.md` (design), `PLANNER-PROCESS-REVIEW-2026-10-27.md` (gap analysis), `EXPANSION-LOOP-SPEC-2026-10-27.md`, `SEMANTIC-SCENARIO-SPIKE-PROPOSAL-2026-10-27.md`.

Repository companion: [Planner charter and drift guard](PLANNER-CHARTER-AND-DRIFT-GUARD.md).
