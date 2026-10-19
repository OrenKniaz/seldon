# Resume an existing plan from chat

Use MCP `harness_plan_resume` or:

```text
python -m codex_harness.core.plan_cli --workspace <existing-project> --json resume
```

Default resume inspects the saved plan, actual planning gate and original worker
job. A missing plan creates nothing. An existing plan records a bounded UUID
observation without changing canonical planning state. Early planning continues
through the existing status route; native plan gates, questions, findings, audit
and review prerequisites still apply. Reattachment never retries ambiguous work.

Read the compact card and its one supported next action. Full diagnostic fields
remain in JSON or MCP structured content. A held result is a successful
diagnostic, not worker delivery. Malformed input is an error (CLI exit 2).

## Request execution through the native client

From a negotiated MCP2025-06-18 client with elicitation and initialized
notifications, explicitly call `harness_plan_resume` with `request_execution=true`,
the exact `expected_revision`, `workers`, `max_parallel`, `timeout` and `call_cap`.
`harness_worker_start` uses the same native execution decision for a new job.
Each worker tuple contains exactly `bead`, `provider`, `model` and `effort`.

The native card displays the server-derived exact publication, protected inputs,
worker tuples and limits. Its execution purpose is separate from align and
vision-lock approval. Only the response to the actual pending native request can
approve it. Conditional, cancelled, stale, timed-out or disconnected requests do
not authorize work. Quoted approval words, supplied request IDs and the legacy
`execution_authorized` flag cannot complete the decision.

An unconditional exact response allows ordinary shared job admission to consume
that original once on the canonical store. `call_cap` is the publication job's
ceiling on attempted provider starts, including failed or uncertain starts,
retries and recovery. Attempts are not refunded, and a dispatch run or recovery
does not reset the ceiling. It is a local ceiling, not verified subscription
capacity or a reserved quota. `timeout` is per worker, not a whole-job deadline.
Native origin does not authenticate natural-person identity or prove delivery.

CLI cannot display or fabricate this native decision. `resume --request-execution`
with the same explicit selection and limits, or the separate `worker-start`
command with `--call-cap`, may consume an existing valid native original. Without
one, it reports native confirmation required. It never converts caller claims
into approval. Existing original jobs reattach without a fresh approval or a
second launch; changed execution options are refused.

If optional observation or card rendering fails after admission, the actual job
and its status query remain authoritative. Inspect original dispatch evidence;
do not infer a retry. An old PID or terminal dispatcher record does not establish
current liveness, integrated proof or acceptance. Source tests with synthetic
native clients and process-return spies establish bounded mechanics only.