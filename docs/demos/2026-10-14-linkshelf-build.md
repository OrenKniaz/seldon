# Linkshelf: recorded planning, build recovery and proxy acceptance

## Evidence and product

Evidence root: `C:/Users/oren/claude-worktrees/`. Sources: `_assignments/linkshelf.md`, `linkshelf-replan.md`; final worker reports and command events in `_assignments/logs/linkshelf-222017/events.jsonl` and `linkshelf-replan-224419/events.jsonl`; read-only planner projections for `_runs/linkshelf/project`; and `_runs/linkshelf/runs/linkshelf-20261014T193858850174/evidence/run.json` plus `bead-B3/.harness/blocker.json`.

Linkshelf is a household bookmark service for 2–6 people: private tagged shelves, substring search, read-only sharing, a JSON API and a server-rendered HTML shelf. Unlike the earlier tiny single-user water-cli dogfood, this brief requires a long-running HTTP server, SQLite persistence and several isolated users. Detailed water-cli build figures are **not recorded** in these sources.

Clean first use provisions Alice with `linkshelf adduser alice`, starts the loopback server, creates a link through the API, lists it, then shows it on `/` after token-cookie login. Python standard-library implementation and automatic database creation are required.

## Planner results

The initial planner recorded 10 clarifications, authored protected tests, implementation stubs, an oracle and executable acceptance scenarios, then published manifest `d4502282707695ed0a40bdae5517c8c08eff7a758572b6e698600900378c8b6c`.

| Bead | Owner files | Initial prerequisites |
|---|---|---|
| B1: storage/migrations | `storage.py` | None |
| B2: token primitives | `auth.py` | None |
| B3: HTTP/API/login | `server.py` | B1, B2 |
| B4: HTML renderer | `page.py` | None |
| B5: CLI/integration | `linkshelf.py`, `linkshelf`, `linkshelf.cmd` | B1–B4 |

B1/B2/B4 could start independently; B3 followed storage/auth, and B5 integrated the service. Initial oracle audit passed, including HTTP integration. Quality was 95/100: 10 passes, one warning, no failures. Two live invocations per reviewer produced 19 findings: 13 resolved, five rejected with evidence, one deferred. The deferred incomplete-client stall remained a B3 follow-up.

Oracle acceptance dry run `179c33d6a601436595c3983c07b9e165` passed 5/5 scenarios, including the then-current 13-test suite. This was reference-implementation evidence; the initial planner did not run `accept`.

## First build and planner replan

The real provider run `linkshelf-20261014T193858850174` first validated B1 (Codex), B2 (Claude) and B4 (Claude). B3 (Codex) stopped without source edits: actual B1 `Storage.user_digests()` returned `{digest: user_id}`, while B2 iterated `(user_id, digest)` pairs, raising `ValueError: too many values to unpack`. B5 was blocked behind B3. Passing isolated bead proofs had missed a real producer/consumer mismatch.

The blocker suggested changing B2, but the planner retained the already-published list contract: `Storage.user_digests() -> list[(user_id: int, digest: str)]`, empty result `[]`, no ordering guarantee. D2 recorded the decision; R1/R2 and protected tests pinned the shape, persistence and real Storage→authenticate success/failure cases. B2 became dependent on B1 and owned the cross-bead proof. The oracle passed unchanged.

Impact prediction: B1, B2, B3, B5. Actual pre-publication `p impact`: B1/B2 affected, B3/B4/B5 untouched. That projection excluded downstream dependency closure: B3 still needed to resume and B5 still needed implementation; B4 could be reused.

Re-audit passed. Each reviewer received one more live invocation, reaching three total each. The replan's 24 findings were dispositioned as four resolved, two rejected and 18 deferred; none remained open. Quality stayed 95/100 with deferred-gap warnings. Manifest `cedb5d82250f31a208eeed0845317e863c7fdc48f03800595330b14247c61538` was republished with proxy approval.

## Resume outcomes

- **Resume 1:** B4 was reused. B1 repaired the dict return and username grammar. B2 passed the strengthened real-storage proof without a source change. B3 implemented the server and reported all API tests passing, but integrated validation failed on oversized-body handling: the client saw `ConnectionAbortedError: [WinError 10053]` instead of the expected JSON 400. B5 remained unavailable behind that failure.
- **Resume 2:** B3 was redispatched at high effort; B1/B2/B4 were reused. B3 and then B5 passed integrated bead validation. Final run status was `plan-validated`, with the integrated three-test end-to-end module passing. `resumeSummary` records reused B1/B4/B2, redispatched B3/B5 and no invalidations. Worker self-reports alone did not establish either outcome.

## Acceptance results

Read-only planner status records acceptance run `b1fbafc5894440d288304b92d30e74c7`: **5/5 passing**, covering `first-moment`, `isolation`, `invalid-input`, `scope-1` and `extended-proofs`. Output records clean-state CLI/API/HTML/search/restart, owner-only mutation and read-only shares, JSON 400 without partial writes, and server cleanup. The scope scenario ran the final 15-test suite successfully.

Acceptance review records **zero findings**. Acceptance is recorded by `opus-user-proxy`: `accepted=true`, `isProxy=true`, `approvalOrigin=recorded-unverified`, `humanOriginVerified=false`, `realUserAccepted=false`. This separate acceptance record extends the dispatcher result; `run.json` itself retains `isLiveProductProof=false` and explicitly does not claim a live acceptance step.

## Recorded efficiency

Figures below come from `codex_harness.core.efficiency.summarize_events`, invoked through `uv run --no-sync --frozen --offline python -c`, on each recorded attempt's `events.jsonl`; they match the attempt efficiency envelopes. Commands count logged command events, including batched shells, rather than individual planner CLI calls. Token columns preserve the helper's provider-specific fields; do not treat them as comparable billing totals or add cached input to input indiscriminately.

| Bead / attempt | Provider / model / effort | Commands | Input tokens | Cached input | Output tokens | Seconds |
|---|---|---:|---:|---:|---:|---:|
| B1 initial | Codex / gpt-6.1-sol / medium | 5 | 126,461 | 98,176 | 3,270 | 146.556 |
| B1 resume 1 | Codex / gpt-6.1-sol / medium | 4 | 107,359 | 78,848 | 885 | 69.273 |
| B2 initial | Claude / claude-haiku-4-5-20251001 / not recorded | 5 | 50 | 236,732 | 2,104 | 36.944 |
| B2 resume 1 | Claude / claude-haiku-4-5-20251001 / not recorded | 3 | 34 | 159,040 | 2,198 | 43.580 |
| B3 initial blocker | Codex / gpt-6.1-sol / medium | 4 | 139,156 | 108,416 | 880 | 119.201 |
| B3 resume 1 | Codex / gpt-6.1-sol / medium | 4 | 149,907 | 66,560 | 4,023 | 193.319 |
| B3 resume 2 | Codex / gpt-6.1-sol / high | 4 | 144,639 | 110,464 | 5,145 | 159.377 |
| B4 initial, later reused | Claude / claude-haiku-4-5-20251001 / not recorded | 7 | 58 | 288,068 | 3,815 | 55.578 |
| B5 completed attempt | Codex / gpt-6.1-sol / medium | 3 | 113,164 | 95,232 | 1,602 | 91.225 |

No B5 provider attempt is recorded for its earlier blocked states. The run envelope totals 39 commands, 780,828 input tokens, 1,241,536 cached-input tokens and 23,922 output tokens; these are recorded worker-attempt aggregates, not end-to-end project cost.

The planner logs summarize to 25 commands / 3,113,299 input / 2,961,408 cached input / 35,388 output tokens initially, and 20 / 2,134,930 / 1,994,368 / 14,172 for the replan. Both planner durations are **not recorded**. Those log summaries do not establish a complete reviewer-inclusive cost.

## Lessons

- Interface prose and isolated fake-store proofs did not enforce the storage/auth contract. The proposed quality check consumes coupled interface requirements, bead ownership/dependencies and protected proof source; it flags consumers that can close without testing the real producer and advises assigning the cross-bead proof before downstream work. Lane `planfix5` owns this check.
- Reuse preserved unaffected validated work; downstream scheduling must be distinguished from the CLI's directly affected set. Integrated Windows validation caught behavior the worker's successful local report missed.
- Spine edits cleared stored acceptance scenarios. The assignment records the driver re-loading the planner's own `scenarios.json`; lane `planfix5` is fixing preservation. That driver action's exact timestamp and command log are **not recorded** in the permitted evidence.
- Guide gaps included misleading audit exit status, omitted helper/scenario context in review packets, ambiguous `show --body` syntax, verbose status and impact semantics. Deferred findings remain known gaps; disposition alone does not fix them.

## Honest limits

Acceptance is proxy-only, with unverified origin; no actual household user's approval or long-term deployment evidence is recorded. The planner accidentally confirmed a routine assumption with actor `user`; its rationale explicitly said planner-owned, and a later re-attribution attempt could not change the closed record. Treat that actor as a boundary deviation, not human approval.

The planner also authored template/oracle/input files through shell/Python writes rather than exclusively through the brief's “CLI only” wording. Logs show these project-local writes and permitted guide/brief reads; this report does not certify literal CLI-only compliance. No forbidden Harness-source read was observed in the inspected command events. Cost, long-term reliability and real-user acceptance are **not recorded**.
