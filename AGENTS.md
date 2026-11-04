# Seldon repository instructions

**CHARTER.md is the binding goal (ratified by Oren 2026-11-04) and outranks every other document.** Read it before any work. Every brief and report cites the C#/I# it serves; reports open with `CHARTER: serves C# — violates no I# (or uses DV#)` and blockers with `TRIAGE: BUG|DESIGN — why — sibling cases`. Never drop or weaken a core piece without a ratified charter edit or a registered Deviation. North Stars and older plans are background only. Preserve unrelated owning contracts and constraints. Scope current changes to their concrete assignment.

Keep one writer per owned source surface. Do not copy the legacy runtime merely to satisfy imports, introduce bypass flags or a new Harness-owned confinement framework, weaken assertions or acceptance, treat mocks as completion, or add cockpit mutation controls. Keep durable plan truth, provenance, confirmation and candidate-bound evidence intact.

Review the retained capability audit before declaring a capability complete. Source tests alone do not establish actual provider execution or full-vision acceptance. Report Windows privilege-dependent skips and unknown usage truthfully.

Don't write tautological tests. A test must verify observable behavior a user or caller depends on, not re-assert the implementation (copied constants, mirrored logic, call sequences, private field shapes). If a test could only fail when the code is edited, not when behavior breaks, delete it. One behavior is proven once; prefer fewer, end-to-end-ish tests over many unit-level restatements.

Proof is semantic only (CHARTER C14): no unit tests. Fake-provider golden/E2E journeys prove complete flows; the ladder (L0→L3) and authorized real runs establish product acceptance outside the suite. The suite-count and fast-gate ceilings in tests/python/test_suite_budget.py only go down; mark the fast gate and measure its runtime (at most ten minutes).

Keep it simple. Build the smallest design that meets the assignment; no speculative abstractions, flags, or hardening beyond BASIC security (local single-user tool). Many edge cases usually mean the design is wrong: find one rule that covers them instead of adding checks. Reports are short: what changed, test counts vs base, risks, next step. Do not produce evidence files nobody asked for.

Do not commit runtime state, logs, generated proof artifacts, secrets, local configuration or caches. Preserve original repositories and history. No publication, provider launches, new credentials, global settings, trust/ACL/security changes or denied-action retries are authorized by these instructions. Obtain task-specific authorization for those actions.

The golden-path E2E (tests/python/core/test_golden_path.py) is the first gate.
Every user-visible change extends it in the same patch; a feature it does not exercise is not done.
