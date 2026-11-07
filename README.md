# Seldon

Seldon wraps supported coding providers through durable planning, review, parallel work, serial integration and evidence. This local extraction keeps the Python package `codex_harness.core`.

The ratified [CHARTER.md](CHARTER.md) is binding and outranks historical North Stars and plans. Source goldens, mocks and clean imports establish bounded implementation evidence; they do not establish actual-provider success or full-vision completion. [Retained obligations](docs/legacy-capability-audit.md) record remaining gaps.

## Retained open obligations

The [October 16 port decision](docs/decisions/2026-10-16-basic-security-port-scope.md)
sets the current basic-security scope. Deferred items remain visible in the
capability audit; they are not gates for local implementation.

- [ ] Bind controller-observed launches to exact local plan, packet and attempt originals; refuse mismatched recovery.
- [ ] Expose supported cancellation with truthful terminal state and recovery.
- [ ] Prove basic live stall/crash recovery without lost work.
- [ ] Provide minimal health and supported-provider readiness diagnostics.
- [ ] Produce authorized actual-worker, integrated and locked-vision acceptance evidence.

## Core entry points

Installed project activation: `seldon init --host all --dry-run`, then
`seldon init --host <host>` from your product folder. See the
[project-local setup guide](docs/operator/project-init.md) for check/remove,
preservation rules and experimental host support status.

`p`, `plan_cli` and `run_plan` enter the same durable run controller; `status_server` serves the read-only cockpit. Module targets are `python -m codex_harness.core.plan_cli` and `python -m codex_harness.core.status_server`. For source use, put this repository's `src` directory on `PYTHONPATH`. Provider launches retain the user's configuration, authentication and host protections.

Start with the [planner guide](docs/operator/planner-guide.md), [handoff contract](docs/operator/handoff-contract.md) and [verification runners](docs/architecture/verification-runners.md). The runtime uses the Python standard library. Development tooling versions are pinned in `pyproject.toml`; the Windows source gate uses the existing prepared interpreter without installing packages.

## Verification and provenance

The user-approved October 19 release rule uses changed owning tests, all new regressions, a finite reviewed smoke set, an actual offline wheel build and the complete Ruff check/format scopes before private publication. Bind actual collection, completion, command results and independent meaningful review to the exact candidate. Use fresh isolated roots and preserve explicit Windows privilege skips. The target is ten minutes, measured; never omit or weaken checks to meet it.

The authorized lane gate is `bash ../golden-gate.sh . stage-2c`: golden/approval journeys and the retained structural guards only, at most 19 cases and ten minutes. No unit tests. Browser-unavailable is web UNVERIFIED; CC's unsandboxed normal-browser gate is the web proof authority. A source gate does not establish actual-provider or product acceptance.

[PROVENANCE.md](PROVENANCE.md) and [extraction-manifest.json](extraction-manifest.json) identify the exact source objects and exclusions. Committed fixture reference code and sample planning/publication snapshots are static test inputs, never evidence of Seldon completion. Historical demo documents retain their source-era artifact paths; those runtime artifacts are not included here. Packaging/install behavior has not been certified by the source-only import/entry tests.

Private publication requires the exact-candidate fast gate and independent review above; the complete suite follows asynchronously. Publication authority, private-target/account checks and source provenance remain required. No real provider run is part of this extraction.
