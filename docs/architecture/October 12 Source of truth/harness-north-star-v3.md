# Harness North Star v3

Status: ACCEPTED by Oren on 2026-10-12 ("I accept our new direction of north star v3, resume all implementation efforts"). Joint DOT/Opus recommendation, signed by DOT/Astra (with edits applied) and Opus. Earlier North Stars (April 25, August 11) remain historical context. Accepting the direction does not by itself authorize migration or deletion; each step still follows normal delivery ownership.

## One sentence
Harness turns an idea into a shipped product by running the AI coding tools the user already has (Codex, Claude Code, Copilot; later Pi, Devin, others) through one workflow: shape the idea, document it beautifully, review it, split it into beads, execute concurrently, prove the work, and re-plan. Harness wraps those tools and never re-implements them.

## The problem it solves
Today the operator keeps reconstructing accepted intent, assignments, integration state and proof by hand whenever work crosses tools, subscriptions or sessions. Harness exists to remove that reconstruction and supervision.

## Success (user-directed)
Harness succeeds when all three outcomes hold:
1. **The idea gets better.** Planning challenges and expands the user's idea into a stronger product, not a transcription of it.
2. **The full agreed vision is delivered for real.** Every accepted requirement reaches working product with real evidence. Mocks and stubs are explicit stages only, never the delivered result.
3. **Supervision goes down.** This is measured in operator interventions and time, compared with coordinating the same work manually in the native apps.

## Planning and documentation are the heart
- The durable, readable plan is the authority for what is being built: vision, decisions, spec and beads. Chats, provider sessions and agent memory are not.
- It is written for humans first: clear, beautiful and current. Execution, review and proof trace back to it.

## The planner flow (Oren, 2026-10-14, with agreed refinements)
The planner is the heart of Harness. It takes an idea through these phases. Each phase is a gated state in the plan store, not a habit of the agent.

1. **Align.** Understand the user's vision. The planner restates it in its own words ("here is what I think you want, and why") and the user confirms or corrects it.
2. **Expand.** Improve the vision with product-category best practices. Each suggestion is accepted, rejected or deferred by the user, never silently added.
3. **Approve: vision lock.** One short summary for the user to approve: the one-sentence vision, users, the first user moment, scope in and out, the quality bar, and what "done" feels like.
4. **Features, user stories, edge cases.** Plus an assumptions and open-questions register. Its size is the measure of guesswork.
5. **Requirement specs.** Each requirement has acceptance criteria and a planned verification (unit, end-to-end or visual). Every requirement says how it will be proven.
6. **Tech stack.** Options with reasoning and trade-offs, recorded as decision records. The user is asked only when the choice is material.
7. **Plan.** Beads with file ownership and dependencies, shaped for maximum concurrency, each linked to its requirements.
8. **Roundtable.** A cross-subscription review using the user's existing Codex and Claude capacity, proportional to consequence. Findings link to the exact plan items they concern, and fixes are tracked.
9. **Publish.** A frozen plan revision that workers execute against. Any later change creates a new revision.
10. **Execute with oversight.** Workers take over. The planner steers (see Steering).
11. **Change mid-flight.** A vision edit runs impact analysis. Only affected work, proof and docs are redone. It returns to the user only if scope changes.
12. **Accept.** A final review of whether the product achieves the locked vision, not just whether tests pass.

**Traceability spine (A-SPICE idea).** Vision → features → user stories and usage → requirements → decisions and design (architecture, tech stack) → beads → code → unit tests → end-to-end tests are all linked, with stable ids and content revisions. You can see why everything exists and how it serves the vision. Nothing is waste.

**Structured state, not markdown.** The plan lives in a structured per-project store (SQLite). Each published revision is exported as an immutable snapshot into git for history and review. Markdown documents are generated views stamped with their plan revision, so docs are never stale.

**Planner quality report.** A deterministic check against Oren's checklist:
- Did we expand the idea, and ask enough questions to align?
- Is the idea mapped correctly into features, stories and requirements?
- Is there a clear spine from vision to tasks to proof?
- Are beads spread for concurrency and ownership?
- Did the cross-subscription roundtable run, and was it helpful?
- Is the process efficient, or full of guesswork?
- Is it repeatable and deterministic?
- Is the plan published with correct context for each worker?

## Planner chat experience
The planning conversation happens in the user's provider chat (Codex, Claude Code, Copilot). Harness makes it clear and deterministic through what its tools return, not through the agent's goodwill.

- **Starting plan mode.**
  - One explicit trigger per provider: a Harness skill or slash command such as `/harness-plan <idea>`, or the phrase "plan with Harness: …".
  - On entry, the planner shows a fixed **plan-mode banner**: project, current phase (for example "Phase 1/12: Align"), what will happen next, and how to pause or exit.
  - The banner comes from a Harness tool, so the user can see that the agent really entered plan mode.
- **Always-visible state.** Every planner reply starts with a one-line status: phase, plan revision, open questions, pending approvals. It also carries a phase tracker (✅ Align · ⏳ Expand · ○ Approve · …).
- **Proof of understanding.** At Align and at the vision lock, an "Here's what I understood" card: the one-sentence vision, users, the first user moment, scope in and out, and the top assumptions. Then an explicit "Confirm, or tell me what's wrong."
- **Required diagrams.** At key gates the planner must show a Mermaid diagram generated from the plan store, not drawn freehand, so the picture matches the plan:
  - the user journey or app flow at the vision lock;
  - the feature map at features;
  - the data flow or architecture at tech stack;
  - the bead dependency graph, with concurrency visible, at plan;
  - the trace view at publish.

  Where a chat can't render Mermaid, a text fallback is shown.
- **Decision cards.** Choices are shown as options with a recommendation and the trade-offs, using the provider's native picker where one exists. Approval gates say exactly what to reply.
- **Change digests.** After each revision, a compact "what changed" summary listing affected features, requirements and beads.
- **During execution.** Periodic digest cards (beads done, running and blocked; steering catches; proof status), plus a link to the read-only board for detail.
- **Enforcement.** Gate tools refuse to advance a phase without the recorded confirmation. The banner, status line and diagrams are part of the tool responses. That makes the experience deterministic and repeatable, not dependent on prompting.

## Steering
- The planner steers continuously against the plan. It catches:
  - design drift;
  - missing requirements;
  - mocked or stubbed work presented as done;
  - gamed or self-asserted evidence.
- On each catch, it corrects the plan or reassigns the work.
- Re-planning never silently lowers acceptance. Narrowing scope, weakening a requirement or accepting lesser proof is recorded explicitly.
- Genuine changes to the vision go back to the user for a decision.

## Principles (kept from August 11, Oren-confirmed)
- **"Best durable product, not shortest credible route."** Durable means good architecture, design and code.
- **"Simplicity means the least architecture that fully supports the durable product."**
- **Wrap before build.** Reuse supported provider capabilities first. Add only the durable, cross-provider behavior or integration that those capabilities do not provide together.
- **Keep the full vision; keep the mechanisms small.** A first slice is sequencing, never a permanent cut.

## What the providers own (wrapped, never rebuilt)
- Models, tool execution and code editing.
- Sandbox and permissions.
- Chat, which is also where the planning conversation happens.
- In-session subagents and agent teams.
- Their instruction and memory files.
- Session resume.

Harness reports each provider's actual configured boundary truthfully:
- **Codex:** `workspace-write` is not repo-only.
- **Claude Code:**
  - The shell sandbox is unavailable on native Windows; permission rules still apply.
  - A Git worktree separates working files but is not a security boundary.
  - Harness records the actual effective protections, and reports unsupported requirements rather than claiming equivalent isolation.
  - None of this is permission to install WSL or containers, or to alter OS security.
- **No bypass flags.** No `--bare` subscription bypass.

## What Harness owns (the product)
1. **Plan:** idea expansion and a beautifully documented, durable plan (vision, spec, beads). Planning depth follows ambiguity, and the planning runs inside provider chat through Harness MCP tools.
2. **Review:** proportional to consequence. A second provider reviews the plan when it matters; there are no blanket ceremonies.
3. **Work graph:** beads with dependencies and file ownership, enforced across vendors at dispatch and at merge.
4. **Dispatch:** each bead starts automatically through the provider's supported headless interface, in its own git worktree, with status, cancel and log.
5. **Mail:** provider-neutral, attributable messages between agents and the planner.
6. **Memory:**
   - A small shared project memory with provenance and confirmation status.
   - Relevant, bounded context goes in through supported per-run input or instruction options.
   - Existing provider instruction files remain user-owned. Changing them is a deliberate integration choice, not a default side effect.
7. **Integration and proof:** one integrator merges in DAG order. The bead's validation runs on the integrated result, and the evidence is bound to the candidate commit. An agent's claim is never proof.
8. **Re-planning and recovery:** triggered by events (failed validation, merge conflict, stall, steering catch, operator request). Durable minimal task and attempt state, idempotent retries, no lost work. Re-planning never silently lowers acceptance (see Steering).
9. **Cockpit:**
   - A read-only view of the plan, graph, owners and provider/model, proof, blockers and available usage.
   - User actions stay in their provider chat. Agents carry them out through supported Harness CLI or MCP tools, and mail supports coordination.
   - Unknown usage remains explicitly unknown.

## Basic security requirements within existing authorizations and platform restrictions
- No secrets in the repo, logs or memory.
- Data integrity.
- Simple path containment for untrusted content.
- The provider-normal sandbox.
- Authenticated localhost.
- Respect the existing scoped authorization for routine private integration.
- Escalate destructive actions, public releases, new spending, credentials or privacy, OS or security changes, and other platform-required decisions.
- Harness-owned confinement is not on the path.

This section changes no permissions and reopens no denied action.

## Path from today
- **Sequencing:** the current repository, Python and Windows come first; macOS follows.
- **Repository:** prefer a thin core in this repository.
  - Define and verify the smallest dependency boundary using current code before choosing a package layout, replacement store or exact import bans.
  - Preserve working behavior and existing candidates.
  - Consider a new repository only if measured extraction or coupling costs justify a separate decision. There is no automatic two-slice escape.
- **Reuse candidates (clean leaves):**
  - the bead schema and quality contract;
  - the planning leaf core;
  - the worktree manager;
  - the proof models;
  - the project memory candidate;
  - agent mail and the event bus;
  - the provider adapters, after a narrow policy change from bypass to provider-normal, with bypass rejected;
  - the view-only board UI;
  - preflight and health.
  - These are reuse candidates, not newly admitted by this document.
- **Candidate gaps to verify against existing modules** (not an approved list of new frameworks):
  - MCP planning and mail tools;
  - a small dispatcher;
  - state that is independent of the trust spine;
  - cross-vendor ownership checks;
  - the integrator;
  - re-plan triggers;
  - a read-only API.
- **Old code is frozen and preserved, not deleted.**

## First proof (acceptance target)
The target scenario:
- In a fresh dogfood repo, an idea is shaped in provider chat into a 2-bead plan.
- Codex and Claude Code each take a bead automatically.
- The workers report via mail, and worker B uses a memory fact confirmed by A.
- The integrator merges and runs the integrated tests.
- An injected failure is re-planned without lost work.
- The board shows all of it truthfully.

The evidence must show actual worker completion, memory use, integrated tests and recovery, not just successful process exit. Interventions and time are compared against manual coordination. Delivery is sequenced into demonstrable steps without cutting capabilities; the first useful demonstration need not meet every criterion.
