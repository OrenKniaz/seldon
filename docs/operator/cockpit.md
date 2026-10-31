# Read-only plan cockpit

Run `status_server --workspace <project> --runs <runs-root>` and open the printed loopback URL.
The single view shows the canonical plan, bead ownership, pending gate, questions,
findings, next step, validated acceptance receipts and recorded providers.
Chat and cockpit share canonical status enrichment and next-step guidance.

Expand plan nodes and receipts for full recorded details. The view makes only GET
requests; decisions and operational actions remain in chat. Usage is unknown.
Recorded timestamps and terminal results do not establish current process liveness.

Publication freshness comes from canonical status. Runs are labelled as matching
that publication only when it is current and their revision matches. Other receipts
remain historical or unconfirmed. The cockpit does not reopen proof originals,
calculate proof percentages or grant acceptance. Refresh failure retains the last
received snapshot and explicitly marks freshness unconfirmed until a successful refresh.

The former `/board` and `/legacy` views and fixture-source selector are retired.
