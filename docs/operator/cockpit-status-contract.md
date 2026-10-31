# Cockpit status contract

`GET /api/status` returns `plan`, `runs`, `savedPermissions` and `usage`.
Missing sources are `unavailable`; reading never initializes a plan store.

`plan` uses the same canonical status enrichment and next-step guidance as
`harness_plan_status`, plus full spine nodes, open questions and assumptions.
Revision, pending gate, open findings, acceptance and next step share that authority.
The existing read-only store snapshots SQLite without migrating the source.

`runs` contains controller-recorded run IDs, revisions, timestamps, bead providers,
models and receipts. These are displayed observations, never proof revalidation or
process-liveness claims. Canonical validated proof comes from plan status.
No headline, Gantt, activity-file observation, efficiency trend or extra proof
accounting is computed. Unknown usage remains unknown.

Saved permissions expose only allowlisted project-local settings. They do not
establish active host permissions, connection state or authenticated identity.
The loopback server serves only the one cockpit and named CSS/JS assets and rejects
mutating HTTP methods. Failed refresh retains explicitly stale data.
