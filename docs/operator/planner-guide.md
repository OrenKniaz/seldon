# Planner guide

Turn the full agreed vision into working product with real proof. The structured plan is
truth; trace vision → requirements → scenarios/contracts → beads → code → tests.
Read [handoff declarations](handoff-contract.md), [verification runners](../architecture/verification-runners.md)
and the [resume contract](resume-plan.md) only when the current step needs them.

**Start and recover.** Read `harness_plan_status` / `p status`, follow `Next:`, then use
command `--help` or `harness_schema`. Refusals include the reason and exact recovery route.
Status never changes plan truth. Missing data and
unavailable telemetry stay unknown. Do not inspect Seldon source to discover contracts.
Use `p` with explicit `--workspace DIR`; global `--json` returns `{text,status,data,next,isError}`.
Node/decision inputs also accept `--machine-json`. Success exits 0; refusals exit 2.
`p nodes --kind K` lists IDs; `p show ID --body --test NAME` inspects named protected tests.

**Workspace.** MCP is bound to its startup workspace. Check `workspaceBinding`; except
schema/preflight/status, supply independently known absolute `expected_workspace`.
A mismatch refuses before store/provider access. Assertions do not authenticate the host;
never copy an unexpected server path to bypass a hold. `p preflight --provider codex`
observes installed tools and paths without launching providers or clearing planning gates.

**Semantic-v1 is the default for new plans.** Start with
`p start --plan-mode semantic-v1 --idea "..." --name "..."` or `harness_plan_start`.
Existing modes are immutable. The planner writes semantics; workers write code.
Put closed `seldon/scenarios-v1` data in `V1.fields.scenarios` and explicit entry/argv
bindings in `V1.fields.targets`. Every requirement needs a scenario; design nodes hold
complete interface contracts. Use schema-declared actions/observations; setup is data only.
Declare exactly two beads: `semanticPhase:"contracts"`, then `semanticPhase:"build"`
depending on Contracts. Each references all requirements/contracts and owns the CLI entries.
Omit test nodes and `proofModule`. Compile freezes scenarios/contracts as protected inputs.
Contracts produces stubs: exit 3 and stderr `SELDON-STUB <operation>` for every operation.
The controller must observe every scenario FAIL with its marker before Build; launch/import
errors earn ERROR. Final Validation requires every scenario PASS without markers.
Use `max_parallel=1`; HTTP, GUI and multiple/parallel Build beads are unsupported here.
`harness_worker_status` exposes `run.semanticContracts` and `run.semanticValidation`.
RED history, source tests and scenario passes alone do not establish full-vision acceptance.

**Three decisions.** Default to chat; use native forms only when the user requests them.
Prepare a displayed request, wait for a separate actual reply, then record its literal words.
Never invent consent or turn silence, a cancellation or an earlier refusal into approval.

- **A: understanding and Vision Lock.** At Align, prepare users, first moment, scope,
  expansion accept/reject/defer choices, quality and Done. `harness_understanding_card`
  creates combined `align+vision-lock` consent when complete. Approval covers both gates;
  advance through Expand and Vision Lock without another question.
- **B: plan and execution.** Compile first; record exact reviewer proposals and the shared
  campaign ceiling in B (up to N reviews, not a promise to use them all). Prepare
  `harness_prepare_chat_confirmation gate=plan-execution` with `reviewer_node_id` and exact
  `execution={expected_revision,workers,call_cap,max_parallel,timeout}`. Display plan, waves,
  files, reviewer/worker provider-model-effort tuples, review and worker caps and timeouts.
  Approval covers review, eligible repairs, publication after passing review, execution and
  validation within those limits. Failed/uncertain starts consume caps; changes need fresh
  affected consent. Inspect schema before amending a shared budget; spent starts never reset.
- **C: owner acceptance.** Run product scenarios, complete current acceptance review and
  resolve findings within B. Ask whether delivery meets the locked vision; record actual
  owner words through `harness_accept`. C names the delivered repository and reviewed product
  run. Permission to run scenarios is not acceptance.

For chat, use `harness_prepare_chat_confirmation` and `harness_record_chat_confirmation`:
`gate,request_id,expected_revision,quoted_message,quote_sha256,actor_label`. Responses are
`Approve`, `No`, `Cancel`, or `Changes: <text>`. Hash the exact UTF-8 quote. Caller-mediated
provenance leaves source, recency and identity unverified. Recording launches nothing;
advance or start matching workers separately. Negatives, conditions, stale/replayed replies
and mismatched scope grant nothing. A qualified reply records changes-requested and returns
a fresh card for the changed decision. Recovery refusals do not automatically re-ask.

**Author and review.** Read `p schema --kind K` / `harness_schema kind=K` at first use.
Nodes have `id,kind,title,text,parents,fields`; preserve IDs and accepted intent.
`p node --json @nodes.jsonl` / `harness_upsert_nodes nodes=[...]` writes mixed batches
atomically. After a completed write with unavailable status, inspect rather than replay.
Work features → requirements/scenarios → contracts → beads; record material decisions,
unknowns and research choices. Done uses `done_feels_like`; `done_means` preserves extra detail.
Compile, inspect packets, review expansion/integrity/spine, resolve findings, audit and publish.
All open non-readiness findings block. Resolve with evidence, defer gaps with reasons, reject
only disproven findings. `p readiness --beads B1,B2 --reviewer @reviewer.json` is advisory.
`p impact --since REV` identifies affected work. Recompile after plan/protected-file edits;
stale evidence earns no credit. Coverage is planned until proved on the actual candidate.

**Execution and acceptance.** Start exact published worker tuples within consent limits;
reattach existing jobs without relaunch. Unknown ownership or cleanup holds new work.
`harness_accept_run repo=DIR subject=product|oracle` returns an `attemptId`; poll that same
`harness_accept_status id=ID`, never resubmit to poll. Queued work and historical captured
PASS do not grant current acceptance. Product acceptance needs accepted dispatch evidence,
current passing scenarios, review and owner acceptance. Status never executes work.
Use `accept-scenarios --derive` to prepare drafts; fill executable steps and expectations.

Explicit `acceptance` spine nodes allow parents acceptance vision/feature. Read
`p schema --kind acceptance` / `harness_schema kind=acceptance`; save its example as
acceptance.json, then `p node --json @acceptance.json` / `harness_upsert_node node=<node>`.
These are drafts, not automatic executable coverage or proof.

Only actual owner words use `--by user`; proxy acceptance stays labeled as proxy.
See [proof accounting](proof-accounting.md).

**Legacy plans.** Choose `plan_mode=legacy` explicitly. The
[worked CLI example](planner-legacy-example.md) retains the protected-test workflow.
Provide executable protected tests and importable failing stubs under `template` before
compile/review; workers own implementation, not protected proofs. Tests declare module/level;
beads declare disjoint owned files, dependencies, proof module and lessons. Pin cross-bead
interfaces in consumer proofs. `audit --oracle oracle` proves satisfiability; prospective
audit proves collection/red stubs only and requires explicit prospective publication.
For asynchronous `harness_audit`, poll the same `harness_audit_status` attempt.
Native `harness_record_confirmation gate=align` and
`harness_record_confirmation gate=vision-lock` need initialized MCP with client `elicitation` capability.
Review full displayed revisions: conditions block credit; decline, cancellation and stale
responses grant nothing. CLI quotes return `plan-confirmation-origin-unavailable`.
Reviewer proposals use `harness_reviewer_choice`, then the complete B card through
`harness_prepare_chat_confirmation gate=plan-execution`. The separate reviewer-confirmation
tool is retired; offered models do not prove access.
Use only authorized actual providers/models/effort and existing capacity; never enlarge
budgets or retry uncertain work implicitly. Injected/mock reviews cannot authorize publication.
