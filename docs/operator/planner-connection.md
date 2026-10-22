# Project identity and connection recovery

The planner connection is fixed to one project root. Its identity projection
starts with `Seldon · project: tally · phase 2/11: expand`. The folder name is
the project identity; a separately chosen plan title does not rename the folder.
The canonical phase and revision come from the current status observation.
When no status was read, such as an early routing refusal, the phase is `unknown`.

If transport closes, open a new chat in the **same project** using that project's
local Seldon connection. Call `harness_plan_status` with `expected_workspace`
set to the intended absolute project root. Continue from the saved phase and
revision. Inspect any interrupted call's result and running job before retrying.
An interrupted native confirmation is not approval; follow the current gate.

Editing a registration file does not retarget an existing connection. A
wrong-project refusal must remain ahead of store access and provider admission.
Discover the fixed server binding read-only, then use the connection for the
intended project. The caller's workspace assertion is not verified host context.

`planner_connection.connection_reply` is a display-only consumer of an existing
status snapshot. It preserves tool payloads, routing bindings, native requested
schemas and protocol errors. It never opens a store, launches a job, retries a
mutation, changes configuration or records confirmation.

The MCP and CLI adapters use this projection on ordinary tool replies, routing
refusals and native confirmation responses. A new server reads the same durable
phase and revision. Host reconnect support and live client binding still require
a host journey. No last-contact time or job liveness is invented from a saved snapshot.
