# Planner charter and drift guard (binding, 2026-10-27)
Why: we drifted from the planner vision several times. Refocusing on a thin core, then drifting back, is the pattern to break. This page is short on purpose. If a change can't be justified here in one line, it doesn't get built.

## The charter: what Seldon's planner IS (10 rules)
1. **The heart is expansion.** It turns a rough idea into the best version of itself (category best practice, upgrades, pushback, taste) without inventing scope.
2. **Integrity:** nothing the user wants is lost, diluted, mocked or contradicted on the way down.
3. **Semantics only.** The planner writes meaning (requirements, scenarios, contracts, decisions, beads, waves). **Never code.**
4. **The spine is traceable:** vision → requirements → scenarios → contracts → beads → waves, so an agent can execute without the planner's memory.
5. **The user decides; the planner proposes.** Real decisions are asked and recorded; everything else flows.
6. **Plan review = expansion, integrity, spine.** Code and tests are reviewed in the waves.
7. **Workers build in parallel waves;** Seldon proves the result with the scenarios.
8. **Flows like water:** minimal touches (Align, Lock, real decisions), no prompts, every refusal teaches the fix.
9. **Provider-neutral thin core.** Wrap Codex / Claude Code / Copilot; never re-implement them.
10. **Real runs are the signal.** A feature that doesn't improve a measured run doesn't ship.

## Charter clarifications (Oren + CC, 2026-10-27)
- **Expansion always runs, and is critical.** For a rough idea it expands the product (category best practice, upgrades, pushback). For a precise spec it expands *quality within scope* (missed edge cases, quality bar, done definition, delight, risks). Never skipped, never silently thin.
- **Waves: ship Contracts → Build → Validation (+ Integration for multiple beads) first.**
- **Exploration loop (Oren, 10-27, e.g. a "personal sound bubble" algorithm):** when the plan has open technical questions that can't be decided without evidence, the planner:
  (1) marks them as unknowns;
  (2) defines evaluation criteria first (metric scenarios with targets);
  (3) runs an exploration wave of generative research (survey + invent new or merged candidates by reasoning), prototypes, measurement on one shared harness, and tuning;
  (4) brings a results table + recommendation to a user decision gate;
  (5) RE-PLANS with the chosen approach (bidirectional planning).
  Products with no unknowns never trigger it. Build it after the semantic spike, measured on a real R&D-type product.
- **Validation = scenarios + a light qualitative acceptance** against the Vision Lock's quality bar and feeling (reviewer or user), so "passes every check but is basic" can't pass.
- **Coordination:** DOT runs code-fix lanes only. Runs, measurement and publishing stay with CC; fix lanes may run as directly launched Sol/Terra sessions.
- **No perpetual redesign:** the semantic spike is measured within ~48 h. If it wins: migrate and **delete the old planner-test path on a fixed date** (no permanent flag). Complete A–Z on the new design rather than patching the old one.

## The drift guard: 3 checks on EVERY slice before it starts (in the proposal note, 3 lines)
- **Charter rule:** which rule (1–10) does this serve, and which user-visible outcome in a real run improves?
- **Core or mechanics?** Is the bug a symptom of an idea that contradicts the charter? If yes, fix the idea, not the mechanics.
- **Simplest version:** what is the smallest thing that proves it? State a **net-lines budget** and a **tool-count delta**.

## Complexity budget (tracked by CC at every publish)
- Core lines must trend **down** week over week (baseline 2026-10-26: ~34k lines in `core/`, 52 MCP tools).
- New slices state net lines; anything over +400 net lines needs CC sign-off with a reason.
- MCP tool count must not grow; merges are preferred. Target ≤ 30 tools after the semantic migration.
- Spike rule: build the minimal version first, measure on a real run, then harden only what the run proved necessary.

## The drift review (CC, every 48 h, ≤ 1 page to Oren)
Over the last 48 h of publishes and runs, report:
- each change → its charter rule;
- net lines and tools;
- the run metrics trend (planner time, user touches, review rounds, v3 gates);
- any slice with no charter rule or a growing core → flagged and stopped.

## Dogfooding
The planner's own review job 2 (integrity) should catch drift in *user* plans. We apply the same discipline to Seldon itself: Seldon work is planned against this charter as its vision.
