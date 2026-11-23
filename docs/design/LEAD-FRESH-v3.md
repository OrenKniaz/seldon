# Seldon Lead, fresh implementation: design v3 (Claude + Astra merged, awaiting Oren)
23 Nov 2026. Sources: lead-fresh-v2 (Claude, with Oren's four corrections) and lead-fresh-v2-astra (Astra round 2). Claude accepts all of Astra's amendments below. **No development until Oren agrees.**

## The idea in one paragraph
Seldon is a planner with a living spec. The spec is one typed graph saved in the run's SQLite DB: the person's words, people, multi-user stories, promises, design with reasons, checks and walk scenarios, all linked down the V. Observations from the real product link back up. The planner grows the spec with the person until they say aligned. One plain Codex/Claude builder builds the whole thing and is kept going. The person can talk to the planner at any time, and the planner edits the spec. At the end the planner walks the multi-user stories, a fresh judge reads the evidence, and the planner steers until the person accepts at C.

## Flow
| Stage | Who | What |
|---|---|---|
| Grow and align | Person + planner, unlimited rounds | Expand the dream, research unknowns, ask every question that matters (grouped, options + Other, a recommendation) until the person says aligned. Each answer is a spec edit. |
| **A** | Person | Locks the vision: a spec snapshot. |
| Spec | Planner, then a fresh reviewer | Fill the V: stories (with roles, multi-user), promises (stable IDs; action, observable result, "not met if"), design with reasons, checks, walk scenarios. The reviewer traces every promise to the person's words before B. Example: all three M2 reconciliation reports must be traced to their required outputs. |
| **B** | Person | Approves the spec, the build order, and which deliveries they want demonstrated. No code before B. |
| Build | One builder session | The brief is rendered from the spec (person's words first). The builder owns code, tests and guide, and returns done / partial / blocked with a short note. Code resumes the same session. |
| Talk while building | Person ↔ planner | Discuss, add, remove, pause or redirect at any time. Scope changes show their impact and wait for the person's yes. At each builder return (including "done"), code compares the spec revision the builder last acknowledged with the accepted one, sends one cumulative delta, and records the acknowledgement. Chat shows edits still waiting. If a change invalidates active work, pause promptly, keep the partial work, and confirm the writer stopped before any restart. An explicit "stop" stops immediately. |
| Demonstrations (only those chosen at B) | Planner walk + fresh judge | Verify all promises delivered so far, on the current version. |
| Final walk | Planner walks; code records; fresh judge assesses | The planner plays the multi-user stories on the real product (separate logins, interruptions, the tricky ones, e.g. marking ready without the kit). Code captures actions, outcomes and artifacts. A fresh read-only judge gets the person's words, the spec, the candidate and the raw evidence. It can ask for a probe the planner missed, and it judges coverage and quality. |
| Steer | Planner | From the verdict: demonstrated defects go back to the same builder, together. A missing promise is the planner's to plan. A scope question goes to the person. Repeat until every core promise has current proof, or the person decides. |
| **C** | Person | Every promise's status on this version, UX feedback, live demo, verified fresh-copy guide, remaining limits. Accept or steer. |

## The spec graph (storage and editing)
- **Storage.** Existing SQLite; one writer; one transaction per edit. Each edit saves the whole typed spec (with links) as a snapshot recording parent revision, author and reason. A and B pin snapshots. Observations are stored separately, tied to the promise, the check or walk, the exact code revision and the builder session. The run cursor, pending decisions and active session live beside it. No graph database and no per-edge versions.
- **Editing (agents).** Read a story and its links, then submit one typed batch: add / amend / retire / link / unlink. New items can be named inside the batch. Code assigns IDs, checks references and commits all or nothing against the revision read. On conflict it returns what changed, and the agent retries. Proposed scope edits stay separate from the accepted revision until the person decides.
- **Fresh, not stale.** After each substantive edit, code follows the links in both snapshots and reopens affected proof: a changed check reopens every promise it covered, and removing the last proof link leaves a promise unproved. The planner can mark a wording-only edit with its reason. A new code version needs current proof even if the spec did not change. Coverage and freshness are computed by code, never typed in.
- **Trace both ways.** From any observed result you can reach its promise, check or walk, code revision, builder session and the person's original words and decision, and back.

## Fresh vs reused
- **Write fresh:** the Lead flow, spec storage and editing, brief rendering, all prompts.
- **Reuse where it works on its own:** DB transactions, bound chat replies, provider launch/resume/cleanup (including resume after a kill), Git capture, real-path browser/API observation.
- **Leave out:** unit scheduling, receipts with check bindings, receipt repair, separate coaching, witness bookkeeping, scope/owner concepts.

## Charter / blueprint changes for Oren to ratify (Lead only; exact wording)
1. **C5/C8/C12/C17/C28, journey.** "B approves the reviewed spec, build order and working deliveries the person wants demonstrated. Build order, progress updates and session changes need no proof gate. One accountable writer continues between deliveries. Show and record each demonstration. The person may waive its reply."
2. **C9/C10/C19.** "The editable DB spec traces the person's words through stories, promises, design and checks to candidate code and observed results. Preserve accepted revisions and decisions. Show affected work and missing or outdated proof after each edit. Render handoffs from the store."
3. **C16/C25.** "During the build the person may discuss, add, remove, pause or redirect work in chat. Show the impact and obtain their decision before changing accepted scope. Hold affected work. Record which revision the builder received."
4. **C13/C15/C16, Blueprint §2.** "At every agreed delivery independently verify all promises delivered so far on the integrated candidate. The planner walks the final multi-user stories. A fresh judge assesses coverage and quality from the accepted trace and real observations. The planner then orders repair, seeks a scope decision or offers C. C requires current core proof, source completeness, the Vision Lock quality bar, a live demo, a verified fresh-copy guide and the person's acceptance."
5. **Blueprint §§1–2a.** "At B bind each promise to its action, observable result and 'not met if'. The builder returns done, partial or blocked with a note. Code records the candidate. Keep typed-answer repair (C4) and meaningful RED/GREEN (C14). Record what felt awkward during the walk. A must-fix cites the approved promise and observation. When the walk proves a promise despite a broken check, carry the check repair in the next brief and show it at handoff. Send reproduced defects together to the same builder and recheck affected paths."

## How we would know it is right
- Fresh Lead vs plain-continue on an unfamiliar dream, with the same owner access and tools.
- It includes a live change mid-build followed by a restart.
- Check:
  - which instruction the builder actually followed;
  - whether existing data survived;
  - whether every critical user journey works.
- The owner picks blind and weighs the gain against elapsed time, tokens and their own minutes.
- Before that, a quick regression on M2 (reconciliation reports, kit rule) and on DoorCount/ShelfLife.

## Still open for Oren
- Anything else he has in mind (he said his list was partial).
- Ratify the five wordings, or change them.
- Who plays the owner in the proof trial: Oren himself, or a simulated owner first.
