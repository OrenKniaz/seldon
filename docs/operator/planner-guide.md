# Planner guide

CHARTER: serves C1-C4/C8/C16/C18/C20/C23/C25; DV5 remains open pending authorized real-run proof.

The planner has three mutation tools: `harness_plan_start`, `harness_loop_answer`,
and `harness_steer`. The run controller owns continuation. Read-only schema, status,
node, packet, judgment, review, wave and mail inspection remain available.

Start with `harness_plan_start(name, idea)` in the intended project. Every mutating
wire request must include the independently known absolute `expected_workspace`.
The server never retargets; the caller assertion does not authenticate host context.
`harness_plan_status` reads the canonical `run` baton without advancing anything.
Reattach an existing authorized run with `harness_plan_start`; paused/stopped runs
remain paused/stopped. No worker polling, recovery command or resume tool is exposed.

The returned baton contains the run ID, canonical phase/revision, controller state,
control generation, pending question or user request, retained effect, next action
and reason. The CLI and cockpit read this same authority. CLI commands are
`p --workspace DIR start`, `answer`, `steer`, `status`, `schema`, `nodes`, `show`, `mail`.
`run_plan` enters the same CLI. JSON values can be passed inline or as `@FILE`.

Answer only the current question with `harness_loop_answer(request_id, answer, source)`.
It supplies the actual `answerSchema`, identities, candidate/evidence binding and context:

- Author meaning: `draft`, `ready`, `research`, `ask_user`, with canonical content and rationale.
- Assess a bound subject: the existing 1j positive/negative/inconclusive/unsupported contract.
- Choose direction: continue/repair/replan/research/ask_user/pause/stop, with rationale/content.
- Interpret retained user words: the 1j user-decision contract for the exact displayed subject.

Canonical content includes nodes, reviewer selection/catalog/policy, scenarios, finding
resolutions, clarifications, assumptions and structured mail. `harness_schema kind=K`
returns node examples. Product fields/research/rationale are domain-open; schemas constrain
shape and links. Workers write all code/tests. Planner source-edit routes are removed.
Compilation, review preparation, publication, dispatch, observation and assessment
preparation are internal controller steps. Do not supply phase/compile/start instructions.
Malformed answers get two exact fixes through the same 1j owner, then steering.
Inconclusive/unsupported meaning stays pending without holding a process or reservation.

Explicit planning/judge/reviewer invocation permission is separate from product-plan B.
Only exact authorized role/provider/model/effort tuples can launch; offerings and draft
selections grant no permission. Native host policy remains in force. With authorized
planner/judge role tuples, the controller calls provider-normal semantic invocations;
MCP does not initiate host-model completions. Without a role grant, the prerequisite
or outstanding typed question remains visible to the native chat.

A locks understanding and vision. The plan is reviewed before B. B approves the plan,
workers and execution envelope; same-scope changes reuse existing authorization only
with the existing bound semantic judgment. There are no product call/review budgets.
C requires current integrated validation, delivery, review and actual owner words after
walkthrough/try-it guidance. Source goldens and fake-provider replies are not real-run acceptance.

For a displayed `userRequest`, retain the actual separate reply with `harness_steer`:
`direction="continue", rationale, user_words={request_id,expected_revision,quoted_message,
quote_sha256,actor_label}`. The hash binds exact UTF-8 bytes. This remains caller-mediated;
source, recency and human identity are unverified. Only its bound 1j interpretation
can approve the subject. Decline suspends the action; changes return to planning;
unclear meaning remains pending. Silence and transport loss never approve or cancel.
For negotiated native elicitation, use `harness_steer native_confirmation=true`;
the durable subject survives reconnect with a fresh wire ID and distinct native provenance.

`harness_steer` also carries unsolicited direction and actual user pause/stop/control.
An interrupt commits its generation before cleanup; late owners cannot integrate,
deliver or gain credit. Pause blocks admissions; explicit `resume=true` reconciles
before continuation. Stop revokes continuation. Dead/expired owners release logical
ownership independently of verified process quiescence; uncertain namespaces stay
quarantined. Wake reconciliation consumes receipts before dispatch and never resets
deadlines or the per-bead recovery lineage. After two automatic recoveries, the
controller asks for planner direction. Genuine permission denials are not retried.

Semantic-v1 retains its current execution limits pending later restoration stages.
Evidence stays truthful: awaiting, failed, stale, integrated and validated live are
separate. Browser-unavailable means web UNVERIFIED; CC's normal host browser gate
owns web proof. No desktop/window control or user-browser automation is permitted.
The existing stdio JSON-RPC transport is retained; MCP SDK adoption is deferred to 2c-SDK.