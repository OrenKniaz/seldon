# Seldon UI/UX audit (2026-10-26)

How the evidence was collected:
- Real card text came from copies of two published stores: Tally (`seldon-runs/slot-01`) and Pocketboard (`2026-10-25/P2-fresh-…`). Each store was copied with `sqlite3.backup()` from a read-only URI; the originals were never touched.
- Commands run against the copies:
  - `plan_cli status`, `resume`, `findings`, `readiness` and `nodes`;
  - a stale-review publish attempt, made on a third copy;
  - a scripted `PlannerServer` session that produced the start, status, understanding, chat-confirmation, decision, refusal and resume cards.
- The cockpit was served by `status_server` from the Pocketboard copy and inspected in a browser.

Assessment lens:
- Can a non-expert read it?
- Is the user's decision first?
- Is there exactly one obvious next step?
- Does it render as plain host-neutral markdown? (Codex, Claude Code and Copilot Chat all handle `**bold**`, `-` bullets, tables and code spans.)

## Top 10 problems

### 1. The status card is a 64–106 line wall
- Tally status runs to 106 lines; Pocketboard to 64.
- Every call contains:
  - three full 64-character hashes;
  - the whole product brief (the "Complete vision details");
  - provenance prose such as "caller-mediated; message source, recency and actor identity unverified";
  - a historical approval record;
  - an "Outcome planning" block made of "not recorded" rows;
  - a paragraph-long "Connection recovery".
- The answer to "where are we and what's next?" is buried.

Before (excerpt):
```
**Plan status**
Full details in machine output.
- Editable spine_revision: 4998c1f65f6662b8c550094807d0d2ae4b96dc5a9e16a40926e8e64d6a43a108
- Compiled manifest_revision: c9eadbbb06ee5217a0cae3a23ccdfe3aa464ffd39347f2661fe69a6f93f26e07
- Frozen published_revision: bf1f27b2835ae3e11b956622fdb293b1483e3148b78c742b86965062f041da1a
- Acceptance: not accepted: acceptance-scenarios-required
- Proof: 0/9 scenarios proved by current recorded runs; review reports are recorded evidence, not independent live proof
...(the full PRODUCT-BRIEF, ~60 lines)...
Current vision-lock approval: granted; origin: chat-confirmation (caller-mediated; message source, recency and actor identity unverified)
Historical vision-lock record (not current approval credit): recorded by chat-confirmation for 582d1157…
**Outcome planning · draft / lock unconfirmed**
- Intake: not recorded
...
```
After (real Pocketboard copy output; 64 lines become 27):
```
Seldon · project: pocket · phase 9/11: publish
Plan **Pocketboard** · nothing is waiting on you
Progress: align ✓ · expand ✓ · … · roundtable ✓ · **publish** · execute · accept
**Where things stand** · Publish (9 of 11)
- Vision lock: ✓ approved
- Test audit: ✓ passing (Prospective plan · tests not oracle-verified)
- Plan review: ✓ current · Sol6.1-medium: round 1/1 for this revision · 8 total · ⚠ Only one provider …
- Published: ✓ up to date (8134368b1f96)
- Open: 0 questions · 0 open findings · 3 assumptions
- Product acceptance: not yet · 0/19 scenarios proved by recorded runs
- Worker readiness (advisory): not-yet
**Vision:** Pocketboard is a small local dashboard for one person's weekly priorities, …

**Details** (for the agent; full data in structured output)
- Revisions: spine 63fe4834ff5e · manifest 3705b1e5fc5a · published 8134368b1f96
- Current vision-lock approval: granted; origin: mcp-client-elicitation (…)
- Historical vision-lock record (not current approval credit): … for c66cd87c5529
- Outcome planning: reviewer choice RS1 (draft) · full map: harness_waves
- Connection recovery: if this chat loses Seldon, open a new chat in <project> and call harness_plan_status …
Next: …
```
The full vision is still shown during Align, Expand and Vision Lock, and whenever the vision is not locked.

### 2. Every reply opens with three redundant header lines
- Every reply starts with the project line.
- Then comes a 300-character workspace-binding sentence ("Effective server workspace: … Caller assertion is unauthenticated; host workspace and connection reload are unverified. The server never retargets.").
- Then a second "Harness Plan Mode" banner that repeats the phase.

Before:
```
Seldon · project: tally · phase 9/11: publish
Effective server workspace: C:\…\tally; expected caller workspace: C:\…\tally; match: matched. Caller assertion is unauthenticated; …
Harness Plan Mode: Tally · Phase 9/11: Publish · pending: none
```
After:
```
Seldon · project: tally · phase 9/11: publish
Workspace: matched (caller-stated; unverified)
Plan **Tally** · nothing is waiting on you
```
The full wording is still shown on a mismatch. The structured `workspaceBinding` is unchanged.

### 3. The understanding card invites the agent to summarise it
Real runs show the agents paraphrasing the card instead of showing it before Align. Two things invite this:
- the card has no instruction to show it as written;
- the `Next:` line asks for confirmation "in your own words".

Before: `Next: Your call: confirm this understanding in your own words, or tell me what to change.`

After:
- The card heading becomes **Here's what I understood: is this right?**
- It ends with **Your call:** *confirm, or tell me what's wrong.*
- `Next: Show the user this card exactly as written (do not summarise), then ask them to confirm it or say what to change.`

### 4. The chat confirmation question buries the decision
- The question the user must answer is at the bottom, after the whole vision.
- It is mixed into a paragraph about SHA-256 hashes, actor labels and unauthenticated quotes, which is instructions for the agent, not the user.
- A workspace-binding sentence was also spliced into line 2 of the card.

Before (tail):
```
Chat confirmation 311b6216… for revision edb77c65….
Reply exactly Approve, No, Cancel, or Changes: <text>. No response grants nothing. The caller must quote your latest actual external chat message verbatim, with its SHA-256 and an actor label. Actor identity and message source are unverified; …
```
After:
```
**Your decision: approve the Align understanding below?**
Reply with exactly one of: **Approve** · **No** · **Cancel** · **Changes: <what to change>**

…understanding…

---
For the agent: chat confirmation bc849b43… for revision edb77c65…(full). Show this question to the user exactly as written and wait for their reply. Record it with harness_record_chat_confirmation: quote their latest actual chat message verbatim, with its SHA-256 and an actor label. Actor identity and message source are unverified; the hash binds the quote, not its author. No reply grants nothing.
```
The refusal that carries the question now says `Next: Show the user the chat question below exactly as written and wait for their reply.` Before, it said "retry native confirmation when ready". The workspace line is no longer spliced into the question.

### 5. Refusals name a code but don't teach the fix
Before:
```
plan-approval-required: align needs approval of 53046e92a40edd291c3aa61bf26c20b7f515c8c9f0b133d278a9b2e489bc6c0b
pending_gate: approval:align
Next: harness_plan_status read-only; inspect the reported blocker, primary result and saved IDs. Do not repeat …
```
After: the same lines (the code is kept), followed by:
```
How to fix: the user has not approved align for this plan version yet. Show them the understanding card exactly as written, then call harness_record_confirmation gate=align. If the client cannot show the form, show the chat question Seldon offers instead and wait for the user's reply.
```
- There is one "How to fix" line for each common planning refusal: approval required/stale, native form unavailable, open questions, open findings, missing audit/review evidence, phase order, invalid phase, gate mismatch and unknown node id.
- Async-audit and acceptance refusals belong to other lanes and are untouched.

### 6. When publish needs another review, there is no question for the user, only boilerplate
- This happened in the Pocketboard run, twice: `plan-publication-evidence-required: Missing successful review`.
- After it came an 8-line "Author repair" block, although there were zero findings.
- The session log records: "No decision question/request id/allowed-answer set returned."

After (with no open findings):
```
**Decision needed: run one more plan review?**
The plan changed after its last successful review, so publishing needs a fresh review of this exact version.
Reply **Run review** to review it now (uses one review call from your budget), or **Stop** to keep the last published plan (21269710aa6f).
Next: Ask the user the question above and wait; on Run review: …roundtable --reviewer @<reviewer.json> …
```
- The "Author repair" instructions now appear only when there are open findings to repair, in both MCP and the CLI.
- `structuredContent.authorHandoff` is unchanged.

### 7. Jargon throughout the user-facing text
Examples:
- "Phases (position, not proof)"
- "Full details in machine output."
- "(attempt only; canonical gates recheck)"
- "locked vision (current recorded chat-confirmation credit)"
- "Recorded decision; this is not user approval or delivery proof."

These are mostly internal truth labels shown to the user. They now use plain words or move to the details footer. The ones the tests depend on are kept verbatim in the footer.

### 8. Decision and understanding cards say "Your call" twice
- The card ends with "Your call: reply with an option name…".
- The `Next:` line then repeats "Your call: choose an option…".

The `Next:` line now tells the agent what to do ("Show this decision to the user and wait for their choice"). The user-facing ask appears once.

### 9. The status card shows provenance blobs from records nobody asked about
- On Pocketboard, "Reviewer choice" printed a raw dictionary: `choiceId: b89950d0…; effort: kind: named; …`.
- After it came a 500-character "Supplied confirmation provenance" statement.

After: the outcome-planning section collapses to a single line, and only when a map, intake or reviewer choice exists. The full map stays available through `harness_waves`.

### 10. The cockpit doesn't answer "where is my plan?"
- The page opens on themed jargon: "Prime Radiant", "The Vault · Checkpoint", and the banner "Runtime current binding unavailable · independent plan facts retained · earlier runs excluded".
- It also shows a vision → capability graph.
- It shows nothing about the plan phase, the gates (vision lock, audit, review, publish), the bead/worker list or the review status, even though `/api/status` already provides all of them.
- The text is mostly muted 15px type on a dark green background.

After:
- A new **Plan overview** panel at the top shows:
  - an 11-step phase stepper;
  - gate chips (Vision lock, Test audit, Plan review, Published, Accepted);
  - the next step;
  - beads with their worker state;
  - review status: reviewer, round, open/resolved/deferred findings and quality warnings.
- Headings are plainer: "Product map", "Checkpoint", and "No build run is linked to this plan yet · showing plan facts only".
- The palette tokens are a cleaner neutral graphite (light/navy variants kept), with soft card shadows. Fonts stay at 15px or larger, and body text at 18px, per the browser checks.
- The existing map, board and chronicle views stay below the panel unchanged, so their tested behaviour is preserved.

## Status of the 10 problems
Problems 1–10 are all addressed in this branch (see commits). They are only partly addressed in these places:
- #7: The CLI `Next:` lines still repeat the full `python -m … --workspace "<path>"` prefix.
- #7: `render_vision` still uses `Label:
value` lines. Tests pin these lines, and markdown hosts fold them into one paragraph.
- #10: The older sticky "Where / Blocking / Needs you" strip and the map views were restyled but not restructured.

## Out of scope / not changed
- MCP tool argument and result structures, `structuredContent`, and all proof, approval and gate semantics.
- Owned by other lanes: async acceptance, audit helper attribution, worker packets, and dispatch.
