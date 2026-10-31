# Seldon repository instructions

Read README.md and the accepted North Star v3 before relevant product work. Preserve its full product promise; the later user-approved standalone repository decision changes packaging only. Preserve unrelated owning contracts and constraints. Scope current changes to their concrete assignment.

Keep one writer per owned source surface. Do not copy the legacy runtime merely to satisfy imports, introduce bypass flags or a new Harness-owned confinement framework, weaken assertions or acceptance, treat mocks as completion, or add cockpit mutation controls. Keep durable plan truth, provenance, confirmation and candidate-bound evidence intact.

Review the retained capability audit before declaring a capability complete. Source tests alone do not establish actual provider execution or full-vision acceptance. Report Windows privilege-dependent skips and unknown usage truthfully.

Don't write tautological tests. A test must verify observable behavior a user or caller depends on, not re-assert the implementation (copied constants, mirrored logic, call sequences, private field shapes). If a test could only fail when the code is edited, not when behavior breaks, delete it. One behavior is proven once; prefer fewer, end-to-end-ish tests over many unit-level restatements.

Keep it simple. Build the smallest design that meets the assignment; no speculative abstractions, flags, or hardening beyond BASIC security (local single-user tool). Many edge cases usually mean the design is wrong: find one rule that covers them instead of adding checks. Reports are short: what changed, test counts vs base, risks, next step. Do not produce evidence files nobody asked for.

Do not commit runtime state, logs, generated proof artifacts, secrets, local configuration or caches. Preserve original repositories and history. No publication, provider launches, new credentials, global settings, trust/ACL/security changes or denied-action retries are authorized by these instructions. Obtain task-specific authorization for those actions.
