# SELDON CHARTER v1.0 — the hard goal

> **Status: RATIFIED v1.1 by Oren, 2026-11-04 ("I ratify"). Binding; supersedes v1.0 (ratified the same day).** `AGENTS.md` line 1 points here; every brief cites C#/I#.
> **Only Oren changes this document.** CC/DOT may propose edits with reasons. North Stars, plans, specs, spikes and briefs sit *below* it, must trace to it, and never replace it.
> Sources: Oren's statements (March → 11-04) and his 67 answers (`SELDON-CHARTER-ANSWERS-2026-11-04.md`), North Stars v3/v4, ASTRA-DESIGN-1/2.

## 0. Why this exists
North Stars v1→v4 were each rewritten around the latest direction change, and each rewrite silently lost parts of the core. The 10-27 semantic-v1 spike cut test nodes, parallel beads and waves "temporarily"; it became the main path and nobody restored the cuts. **CC approved those limits and missed the regression.** This charter is **additive and stable**. New direction is added here, or registered as a time-boxed Deviation (§8). A core piece is never dropped implicitly.

## 1. The goal
**Seldon turns a person's dream into a real, working product.** It serves both non-technical people and developers/teams, on new and existing codebases. A dream is any software product of any size, including software for hardware control, DSP, audio sensors, auto-tuning music, ANC and testing audio algorithms.

Seldon is the user's **cofounder, friend and competitor**: it improves the dream (what to build *and* how), breaks it down the way a high-quality software team does (V-model traceability), gets the user's acceptance at the points that matter, and builds concurrently with AI workers. It proves at the end that **the actual dream was built**: not stubs, not mocks, not missing features.

**Quality and one-shot results come first.** Slower or more expensive than plain Codex is acceptable if it buys that, but Seldon must be as efficient as it can be.
*Planning is slow and high-end. Execution is fast and parallel.*

**Success** = a real dream built, with focus that never breaks: Seldon always knows what to do, where it is heading and why. The planner brain thinks on its feet, steers, and tells the user "we need something different" when that's true.

## 2. Core pieces — all required (C1–C26)
| # | Core piece | Means | Verify by |
|---|---|---|---|
| C1 | **Seldon drives the loop** | A deterministic loop (plan → build → observe → judge → repair/replan → next) calls planner, judge and worker LLMs where semantics are needed. The LLM is not the scheduler. | runs advance with no planner-choreographed transitions |
| C2 | **Determinism first** | Whatever can be a tool, function, class, schema or store is one, not LLM reasoning. | no LLM call does a function's job |
| C3 | **The LLM owns semantics** | It creates and judges meaning: intent, quality, test meaningfulness, "is this the dream". | every semantic decision has a recorded verdict |
| C4 | **Structured LLM output** | Typed records with IDs and links, plus a free-text rationale (the WHY). Never open text as the store. The store is whatever is most efficient in time and tokens. Malformed structure: 2 exact-fix retries, then judge/steer. | outputs are schema-valid records |
| C5 | **Size router** | The planner judges dream size at Align (tiny/small/medium/large) and shows it to the user, who may override. It collapses trace levels, reviews and waves for small dreams. | a hello-world uses a slim process |
| C6 | **Improve the dream (the heart)** | Expansion in micro (quality within scope) and macro (what to build). Pushes back like a cofounder. Ends in a **Vision Lock** with a quality bar covering, where relevant: UX/visual, performance, maintainability, architecture, observability and failure behavior. "It works" is not enough. | accepted upgrades visible in the trace |
| C7 | **Research when unknown** | On Seldon's own judgment, showing why. Criteria → explore/invent → measure → the user decides → replan. Pre-B prototypes only with user approval and a clear reason; they never build the product. | research decisions carry evidence |
| C8 | **User decisions** | **A** = understanding + Vision Lock · **B** = plan approval · **per-wave check-in** · **C** = acceptance, after a live demo/walkthrough + a try-it-yourself guide. Seldon recommends; the user decides in their own words. **No budget in v1** (budgets are a dev-process metric only). | exactly these touches, plus real research choices |
| C9 | **V-model traceability** | dream → features → user stories → requirements → architecture/design → interface contracts → tests → beads → code → results, with decisions/research as side nodes. Each breakdown level has its matching verification level. Tests are graph nodes: planned semantically, written by workers, results linked back. | walk from any test/code to the dream and back |
| C10 | **Living trace** | The vision can be re-opened any time; impact is shown through the whole graph. Downstream items are stale-marked deterministically and only the affected parts are replanned, without confusion or ceremony. Discoveries flow upward as typed amendments (never a silent patch to a later artifact); scope-affecting ones go to the user (C16). | a vision change stale-marks the exact downstream set |
| C11 | **Multi-model plan review** | Reviewers check the PLAN: vision/expansion, and "does this plan lead to the whole built dream, or is something missing". Different providers/models preferred; same-model instances get a lower label. Depth scales with consequence (Seldon decides); ~3 rounds, then Seldon decides. | review records with independent verdicts |
| C12 | **Beads for concurrency** | Beads with ownership, contracts and dependencies; as much concurrency as the graph allows, governed elastically by machine/provider limits and file conflicts, with the reason for any waiting shown; no arbitrary caps. Best-fit provider/model per bead; the user may pin providers at the start. | parallel beads when the graph allows |
| C13 | **Workers write all code** | The planner writes semantics only. Workers write code and tests; Seldon decides whether a separate test worker is used. | no planner-authored code |
| C14 | **Semantic tests only** | Tests test semantics (integration/E2E). Tests-first RED for the right reason (observed), then GREEN. No unit tests that do nothing; no Seldon stub magic. This applies to the products Seldon builds and to Seldon itself. | tests map to requirements/stories |
| C15 | **Real-dream validation** | C requires: **trace complete** (every accepted feature → passing semantic tests + evidence), an explicit **no stubs/mocks/missing check**, and the **full quality bar vs the Vision Lock** (C6). Proof runs on the *integrated* result. A gap found → an auto-repair wave, reported at the next check-in. | a removed feature is caught even with green tests |
| C16 | **The planner steers** | On failures/stalls, wave ends and new discoveries. Free to re-split beads and change approach; changes to accepted features/scope go to the user. | events → recorded steering decisions |
| C17 | **Waves** | The planner splits waves (tools assist); the user may dictate ("10 waves, first…"). After each wave: integrate → resync with the vision → check in with the user → plan the next wave from what was built. | wave N+1 beads cite wave N results |
| C18 | **Recovery, never stuck** | Crashes/stalls/transport loss: 2 automatic recoveries of a bead, then steer. | fault twins reach the end |
| C19 | **Memory, mail, worktrees** | Project memory + lessons across projects, with provenance. Memory is advisory: it informs but never overrides the plan/trace, and any memory that influences a decision is cited. A small cross-provider user-settings record (provider pins, quality bar); no general user memory (providers own that). Structured mail between agents (blocker/question/discovery/handoff), recorded in the trace. Headless workers in isolated worktrees with file ownership; Seldon integrates. | memory items cite their source |
| C20 | **Wrap providers, thin core** | Codex, Claude Code, GitHub Copilot. **Absolute:** never re-implement provider sandbox, chat, permissions or security. Seldon is not a security product. Windows first. The user stays in the provider app; the cockpit is a **read-only** view (trace, progress, demo) that shouldn't need opening much. | no host re-implementation |
| C21 | **The user's dream charter** | The Vision Lock IS the user's charter. Any narrowing during the build is a visible deviation the user approves, so the user's project can't drift either. | narrowing → a user-visible deviation |
| C22 | **Open-loop work** | Besides building a known answer (closed loop), Seldon runs open-loop dreams: tune/search toward a metric (e.g., audio/DSP tuning, ANC, algorithm quality) with a defined metric, recorded progress per iteration, and explicit stop conditions agreed at Vision Lock. | open-loop runs report metric history and stop for a stated reason |
| C23 | **Continuity, never lose the thread** | Every loop leaves a trusted baton: decisions, assumptions, open questions, current state, next safe action and why. Any restart, context compaction or agent replacement resumes from canonical state with the same direction. | a killed/restarted run resumes at the right step with the same reasoning |
| C24 | **Brownfield onboarding** | For existing codebases, Seldon first maps the existing code, tests and behavior into the trace (what exists, why, how it's verified), then plans changes. Existing GREEN behavior is preserved and proven. | a brownfield run's trace shows existing features + their evidence before new work |
| C25 | **User run control** | The user can pause, resume, stop or re-prioritize a run at any time via chat; every worker is inspectable (what it's doing, why, its evidence). The cockpit stays read-only. | pause/resume/stop work from chat mid-wave |
| C26 | **Real-path proof + evidence states** | Proof uses the real user path per surface: browser for UI, request/response for APIs, real data state for persistence, simulator/hardware-in-the-loop/signal measurement for HW/DSP/audio. Every item has a truthful evidence state: awaiting, failed, stale, integrated, validated live. | evidence states visible per requirement; UI proof is a real browser run |

## 3. Invariants — boundary rules (each checkable)
- **I1 Structure vs semantics.** Deterministic code owns structure: trace links + coverage, IDs, phases, bead/wave scheduling, running things, observed RED/GREEN, receipts, stale-marking. The LLM owns semantics. Deterministic code never judges meaning by pattern-matching.
- **I2 Unsure → judge, never refuse.** Code that can't decide something semantic routes it to an LLM judge and records the verdict. "Couldn't check" ≠ broken and ≠ passed: it stays pending judgment.
- **I3 Schemas constrain shape and links, never the domain.** Strict on fields, IDs and referential integrity. Closed lists of what a product may be or do are forbidden. Test: *would a valid but unforeseen product (HTTP, UI, mobile, pipeline, library, HW/DSP/audio) fail this field?* If yes, make it open.
- **I4 Only observed failure blocks.** Tests ran and failed; RED→GREEN not shown; validation failed; a judge named a concrete gap; the user said no.
- **I5 Every wait ends.** Every attempt/claim/hold has a bounded end that releases ownership, without inventing success.
- **I6 One owner per truth.** Each fact is decided in one place; views and gates read it and never recompute it.
- **I7 Product-neutral.** No assumption of CLI, Python, static web, a language, or N beads.
- **I8 Every refusal teaches.** It names the one concrete fix.
- **I9 Thin and deleting.** One shared rule beats a special case. Every blocker gets `TRIAGE: BUG|DESIGN — why — sibling cases`; DESIGN = fix the shared rule, never patch. No god files.

## 4. The journey (the size router collapses steps for small dreams)
Align (size routed) → Expand → **A: understanding + Vision Lock** → Research (if unknowns) → Spec (features, stories, requirements, architecture, contracts, planned tests) → Wave + bead plan → multi-model plan review → **B: plan approval** → per wave: tests-first RED → parallel beads in worktrees → integrate → semantic tests GREEN → steer → **check-in** → next wave → real-dream validation (trace complete, no stubs, UX bar) → repair waves if needed → live demo + try-it guide → **C: acceptance**.

## 5. Non-goals
Re-implementing provider sandbox/chat/permissions/security · being a security product · budgets in v1 · unit tests · planner-written code · fixed ceremony counts as rules (counts are defaults) · general user memory · a cockpit with decisions.

## 6. Seldon's own development (dev process, not product)
Retros and the improvement pipeline (amendments to Seldon itself, lessons from its own runs) are dev-process tools for Oren while building Seldon, not end-user features. Planning friction, time and cost are measured here as dev metrics, never as product machinery. Proof = the ladder L0→L3 (no promotion with blockers) + the ShelfLife fight vs plain Codex + Oren's own real project. Semantic tests only (golden/E2E/ladder). Budgets and efficiency metrics are measured here, not in the product. Legacy mode: harvest its good parts (graph, waves, concurrency) into the one model, then delete it. The reset plan is re-derived from this charter and approved by Oren.

## 7. How everyone uses this
- **Briefs** cite the C#/I# they serve.
- **Reports** open with `CHARTER: serves C# — violates no I# (or uses DV#)`, and `TRIAGE:` for blockers.
- **Reviewers** reject anything that drops or weakens a C# or breaks an I# without a ratified edit or a live Deviation.
- **CC** sends Oren a weekly 1-page audit: C1–C26 × present/partial/missing + evidence, open Deviations, drift alerts.
- **Deviations:** CC may open one (owner, expiry ≤ 7 days, restore plan) and notifies Oren the same day; Oren may veto. At expiry: restore, or Oren ratifies a charter change.

## 8. Deviation register (registered 2026-11-04 by CC, Oren notified the same day; expiry 7 days; DV2/3/5/6/8/9 re-dated by Oren 2026-11-05 to 2026-11-18, conditional on 2a landing + a real ShelfLife run within 24h and 2b/2d running in parallel; restore stages from the Oren-approved reset plan `work/ladder/design-review/astra-reset-1.md`)
| ID | Breaks | What | Owner | Expires | Restore by |
|---|---|---|---|---|---|
| DV1 | C9, I7 | semantic-v1 has no test nodes in the trace graph | CC | 2026-11-11 | Stage 1a |
| DV2 | C12, I7 | semantic-v1: exactly 2 serial beads, max_parallel=1 | CC | 2026-11-18 | Stage 3a |
| DV3 | C17 | semantic-v1 rejects waves; waves only in legacy mode | CC | 2026-11-18 | Stage 3b |
| DV4 | I1, I3, C14 | closed scenario vocabulary; stub-marker RED; AST test audit | CC | 2026-11-11 | Stages 1c+2a |
| DV5 | C1, C16 | the planner choreographs transitions; steering is advisory heuristics | CC | 2026-11-18 | Stages 2c+3b |
| DV6 | C15 | no full-dream completeness check (finite scenarios only) | CC | 2026-11-18 | Stage 4a |
| DV7 | C11 | "two reviewers" can be the same model tuple, with no label | CC | 2026-11-11 | Stage 2a |
| DV8 | C8 | B/review/call budgets exist in the product (v1 has no budget) | CC | 2026-11-18 | Stage 2b |
| DV9 | — | two execution models (legacy + semantic) coexist | CC | 2026-11-18 | Stage 3d |
| DV10 | I4, I6, I2, C11 | pending (not open) findings do not hold publication/C (pre-existing at efc4a25; registered by CC 2026-11-05, Oren notified; re-split gap half restored by Stage 2a) | CC | 2026-11-08 | Lane 2a2 |
