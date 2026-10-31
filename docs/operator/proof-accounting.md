# Revision-aware proof accounting

Worker proofs use shell/Python and stdlib HTTP checks (for example `urllib.request`),
without optional host tools Seldon did not configure. Keep exact published proof
commands, protected assertions and requirements unchanged. `BROWSER-CHECKS.md` and
other browser procedures remain acceptance or human checks unless the plan explicitly
requires a worker browser tool and init configured it. Current worker packets do not
attest configured browser capabilities; `capabilityRefs`, discovery and parent-session
settings do not establish that exception. A required worker browser proof without
configuration is a blocker. Report deferred browser checks as unexecuted; HTTP evidence
does not establish rendering, JavaScript, keyboard, focus or browser acceptance.

The cockpit consumes canonical plan status and its validated acceptance receipts.
Its former independent proof-accounting reader is retired. It does not reopen
attempt originals, run Git claim comparisons, calculate proof percentages or
establish process liveness. See the [status contract](cockpit-status-contract.md).

Canonical dispatch and acceptance owners retain candidate, receipt and approval
binding checks. The view shows their recorded results and publication freshness;
raw run receipts remain labelled as recorded rather than revalidated by the UI.
Unknown usage and unproven actual-worker or full-vision acceptance stay unknown.
A stale refresh cannot establish current proof or human acceptance.
