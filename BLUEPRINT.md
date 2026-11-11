# SELDON BLUEPRINT v1 — how Seldon builds large dreams

> **Status: RATIFIED v1 by Oren, 2026-11-11 ("Ratify it"). Written by Fable at Oren's request. Binding on every builder (Fable, Opus, Codex, any other agent).**
> **Sprint rule (Oren, 2026-11-11):** *we are a one-person startup; we sprint all the time and do not work like a dinosaur with endless ceremonies.* Every rule below exists because a real run failed without it. A rule that slows work without preventing a failure gets deleted.
> **Place:** directly under `CHARTER.md`. The charter says *what* Seldon must do. This says *what shape the machine has* so that it holds at size. If they conflict, the charter wins and the conflict goes to Oren the same day. It waives no C# or I#; a piece not built yet is still a registered Deviation (charter §8).
> **Only Oren changes this document.** Builders propose edits with run evidence.
> **Supersedes** `CLEAN-CORE-DESIGN.md` where they differ. `CLEAN-CORE-DECISION.md` stays in force (clean core, question rules, taste, leeway, hygiene).

## 1. The target

**The goal, in Oren's plain words (charter §1): Seldon is the best option for using agents to create dreams.** Every dream we run, from hello-world to the ANC system, is confirmation that Seldon works. None of them is the goal, and no rule here may be bent to make one particular dream pass.

The design target is the **large dream**: many features across several areas, built over several waves by many workers, in a run that lasts hours or days.
Oren, 2026-11-11: *hello-world is only the minimal task for testing our code; the real test is a very complicated app built in one shot, where a single worker does not give a good result.*

- Small dreams run on the same machine with one area, one wave and one unit. There is no small-app mode (I9).
- **The design test for every decision: does this hold at 100 units, 10 waves and three days?** If not, the shape is wrong, even when hello-world passes.

## 2. Why one session fails at size, and what Seldon supplies

| A single long session… | Seldon supplies | Charter |
|---|---|---|
| forgets requirements and contradicts its own early decisions | **Durable intent**: vision, decisions and trace live in the store; every call gets a fresh slice of them | C9, C10, C23 |
| builds parts that do not fit together | **Contracts first**: what two parts must agree on is decided once, before parallel work | C9, C12 |
| drowns in context | **Bounded slices**: each call sees one subject, never the product | I10 |
| says "done" with stubs, weak tests and missing features | **Independent proof** on the integrated product, by code and a separate judge | C14, C15, C26 |
| compounds early mistakes; its plan goes stale | **Waves**: integrate, resync with the vision, replan from what was actually built | C16, C17 |
| loses everything on a crash | **Durable state** and local recovery | C18, C23 |

These six are the load-bearing parts. Work that serves none of them waits.

## 3. The shape

### 3.1 Records: one canonical plan (C9)

| Record | Meaning |
|---|---|
| **Vision** | The Vision Lock: understanding, scope, quality bar (C6, C21). |
| **Area** | A subsystem or feature area with one stated responsibility. The architecture level. |
| **Contract** | One agreement between areas or units: an API, schema, file format, shared type, UI structure, command line. Exactly one provider; named consumers. |
| **Decision** | A recorded choice and its reason (architecture, research, user). |
| **Feature / Requirement** | The story and acceptance; the observable obligation. |
| **Check** | An ordinary command and what it proves. Declares `establishes` (must fail before the work, pass after) or `preserves` (must pass before and after). Levels: unit, contract, wave, walkthrough. |
| **Wave** | A goal and its units. Ends in an integrated, running, proven product. |
| **Unit** (the charter's bead) | One agent session's worth of work: a path scope, its requirements and checks, the contracts it provides and consumes, its dependencies. |

Execution adds: Attempt, Patch (a commit), Observation, Judgment, Integration, Discovery, Approval. The walk dream → feature → requirement → check → unit → commit → result must work in both directions.

### 3.2 Three nested loops

**Run.** `ALIGN → A → ARCHITECT (areas, contracts, decisions, wave outline) → DETAIL wave 1 → REVIEW → B →` per wave `[EXECUTE → WAVE-VERIFY → STEER → CHECK-IN → DETAIL next wave] → VALIDATE (whole dream, feature by feature) → DEMO → C`.
B approves the architecture, the wave outline and wave 1. Later waves are Seldon's to detail; a change to accepted scope goes to the user (C16).

**Wave.** Units run concurrently as the graph, scopes and machine allow, each waiting with a shown reason (C12). The wave ends when its units are integrated and WAVE-VERIFY passes on one commit: every check in the product so far, the wave's cross-unit checks, and real-path proof for each surface touched (C26).

**Unit.**

| State | Who | What |
|---|---|---|
| READY | code | Dependencies integrated, consumed contracts available, scope free. |
| WORKING | one worker session | Writes checks and product inside its scope, runs them, inspects its own results. |
| PROVING | code | Diff stays in scope. `establishes` checks fail on the unit's base without its product changes and pass with them. `preserves` checks pass both times. |
| JUDGING | LLM, this unit only | Are the checks meaningful for these requirements? Any stub, mock or missing behaviour? |
| INTEGRATING | code | Merge into the wave candidate; affected checks rerun on the new commit. |
| DONE | | |

A failure in PROVING, JUDGING or INTEGRATING returns the unit to its own worker with the exact failure. Two recoveries (C18), then **unit steering**: the planner re-splits it, changes approach, corrects a contract, or raises scope to the user. Other units keep running.

### 3.3 Planning is progressive

Plan the horizon, detail the next wave. ARCHITECT fixes areas, contracts and an outline of waves. DETAIL writes the units of one wave only, reading the product as it exists at that moment. Wave 1 of any dream with more than one unit is the **skeleton**: the thinnest product that runs end to end, with the contracts made real and the test harness in place.

## 4. Binding rules

Each rule names its check and the real failure it prevents. A change that breaks a rule does not land.

**R1. One machine, sized by data.** No size branches in structure; tiny is 1 area, 1 wave, 1 unit. *Check:* hello-world and the 50-unit synthetic journey take the same code path. *Prevents:* the tiny-only plan shape that hid concurrency (core8).

**R2. No call is sized by the product.** A question's facts are bounded by its subject: one unit, one feature, one contract, one wave summary. Product-wide conclusions are computed by code from per-item verdicts. *Check:* the largest question is the same size at 1 and at 50 units. *Prevents:* the 87–97 KB judge questions (f7, f9) and the 1.5M-character request (round 33).

**R3. No single-call plan.** Any list an LLM must write is small and bounded; code splits a larger subject into more questions with recorded coverage. *Prevents:* the 32-slot plan ceiling; five PLAN calls and 519 s for three buttons (f7).

**R4. Contracts before parallel work.** Anything two units must agree on is a Contract with one provider. Consumers receive its text in their brief. A unit never depends on another unit's files without a contract or a dependency edge. A contract change stale-marks its consumers (C10). *Prevents:* three button units each needing a page nobody had built (f3).

**R5. Skeleton first.** Any placeholder in the skeleton is a recorded obligation; C cannot pass while one is open (C15). *Prevents:* parts that work alone and fail together.

**R6. Scopes, not file lists.** A unit owns path scopes and may create files inside them. A shared file has one named owner. Code enforces scope from the observed diff. *Prevents:* plans rejected over exact file lists (f8); a planner cannot know every file of a large app in advance.

**R7. Failure is local.** No global phases. One unit's failed proof or doubtful verdict never stops an unrelated unit. *Check:* in a journey with one failing unit of N, the other N−1 integrate. *Prevents:* one RED verdict ending the whole run (rounds 31–33; runs 9, 24).

**R8. A worker is a whole agent session for a whole unit.** Seldon never steps inside it. Rework resumes the same session when the provider allows; otherwise a fresh session gets the brief, the patch and the failure. A separate test worker is used only when Seldon decides (C13). *Prevents:* two cold sessions per unit, which doubled cost and lost context.

**R9. Code proves, the judge interprets.** RED and GREEN are established by code running ordinary commands on exact commits. The judge answers only what code cannot: meaning and quality. Evidence binds to a commit id; when the commit changes, the checks run again. No evidence-reuse identity schemes. *Prevents:* judges refusing valid evidence (run 12), evidence split across questions (runs 34, 35), unschedulable steps (f11, f12).

**R10. Every wave ends integrated, running and proven, and the next wave is planned from the product as it exists.** Wave N+1 is brownfield work on our own product, so mapping existing code (C24) is this same capability, built once.

**R11. Explicit state.** Run, wave and unit states and their legal transitions are declared in one table. State records are typed; no free-form keys. Every state has an exit for success, failure, uncertainty and crash (I5). Killing the process in any state and reopening continues without redoing finished units (C23). *Prevents:* a 30-key cursor dictionary; the implicit state machines found on 11-07.

**R12. Questions are briefs.** Plain language, one subject, labelled facts, one decision, a strict schema, one realistic example. No internal ids beyond the handles the answer must use; no digests or receipts. Guides state principles. A fix for one observed answer goes into the schema, the facts or the code, never into a new prompt sentence. *Prevents:* judges asking for "the remaining r17 content"; prompts that are a history of patches.

**R13. Roles from one table.** Strongest model for architecture, planning and judgment; cross-provider review where available, honestly labelled (C11); provider failures recover twice, then steer (C18).

**R14. Clarity over line count.** One job per module with real boundaries; no mixins sharing one object; lines of at most 110 characters; no god files. Line totals are reported, never targeted.

## 5. The present core (`e4024b6`): keep, change, drop

| | Modules |
|---|---|
| **Keep** | `store.py` (add typed state records), schema validator, `providers.py` (add session resume and a second provider), `processes.py`, worktrees and atomic publication in `work.py`, recording of every real call, `run_report.py`, the recorded-answer replay test. |
| **Change** | `plan.py` → the records of §3.1, progressive. `execution.py` → the unit loop of §3.2 on Git commits. `verification.py` → proof by code per unit, then WAVE-VERIFY. `inference.py` and `questions.py` → one brief builder per subject (R12). |
| **Drop** | Global `WORK(tests)` and `WORK(build)` phases; evidence applicability hashing and `checkGeneration`; exact-file ownership; the 64 KB error as a limit; repair counts by origin; hard-coded `TRY.txt`; required citations nobody reads; the staged `core10b` paging change (keep its recordings and replay test). |

## 6. Build order

Each milestone lands only with its exit met. Hello-world stays green throughout as a smoke test.

| Milestone | Builds | Exit |
|---|---|---|
| **M1 Unit loop** | State tables (R11); unit lifecycle; RED and GREEN by code on commits (R9); scopes (R6); Git integration; deletions of §5. Still one wave. | 50-unit synthetic journey green, with one failing unit isolated and a kill-and-reopen. Real Foundation with concurrent workers: 8 clean of 10. |
| **M2 Contracts and skeleton** | Area, Contract, Decision; ARCHITECT; skeleton wave; WAVE-VERIFY; briefs (R12). | Foundation with a shared page contract: 8 of 10. ShelfLife reaches C in at least 3 of 5, scoring at least as high as a plain agent on the same oracle. |
| **M3 Waves** | Progressive DETAIL; STEER; check-in; next wave from the existing product; unit steering. | The L3 dream reaches C with three or more waves; a mid-wave kill resumes. |
| **M4 Front door** | Chat front door and run control (C20, C25); cross-provider review (C11); size shown to the user (C5). | Oren runs L3 himself and gives a real C. |

Later pieces attach at one named place each: research (C7) as a unit kind; open-loop work (C22) as a wave whose exit is a metric; memory and mail (C19) as records; onboarding a foreign codebase (C24) as R10's mapping on a repository we did not build.

## 7. Proof and working rules for builders

**P1. Rungs are confirmation, not goals.** L0 hello-world is a smoke test and proves nothing about the design. L1 Foundation: three or more concurrent units with a shared contract. L2 ShelfLife. L3 a named large dream needing at least 3 areas, 3 waves and 12 units. L4 Oren's real project. No promotion with blockers (charter §6).

**P2. Scale is tested without an LLM on every change:** a synthetic scripted journey of 50 units in 5 waves proves scheduling, isolation, bounded questions and reopen. Scripted journeys are never evidence about LLM behaviour.

**P3. Recorded real answers replay** as regression on every change.

**P4. Numbers, not adjectives.** A claim states clean runs of N. Promotion to the next rung needs 8 clean of 10. No self-scored percentages.

**P5. The fight at the right rung.** From L2 up, each milestone also runs the same dream through one plain agent, scored by the same black-box oracle. Seldon's claim is higher quality at size; L3 is where it must be shown.

**P6. One owner.** One named lead reads every line that lands in `src/seldon`. One change per landing. Nothing lands over a REJECT. No patch silently drops earlier behaviour.

**P7. A stable rig.** Real runs execute on a machine that cannot sleep, and every run is resumable (R11).

**P8. No ceremony.** Briefs and reports are short and say only what changed, what was proven and what is not. No boilerplate lines, no status documents nobody reads, no review rounds without a named risk.

## 8. Ratified decisions (Oren, 2026-11-11)

1. **R9's reading of C14 is confirmed:** code proves, per unit, that the tests fail without the work and pass with it. Writing tests in a separate session is Seldon's choice (C13).
2. **The L3 confirmation dream is the ANC system:** active noise cancellation on this PC with the Andiamo interface, accelerometer and microphone inputs, hardware driver control, performance simulation, data recording with a local storage server, real-time ANC, and code for the ADSP-21569 evaluation board if PC latency is not good enough. Brief: `work/ladder/L3-DREAM-ANC.md` in the dev workspace.
3. **Who builds Seldon:**

| Role | Who |
|---|---|
| Owner (P6) | Fable while it has usage; otherwise Opus 5.5 |
| Orchestrator | Fable at medium effort while it has usage; otherwise Opus 5.5 at medium |
| Executors | Sol 6.1 or Terra 6.1, effort chosen by the orchestrator per task |
