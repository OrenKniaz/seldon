# Seldon

Seldon wraps supported coding providers through durable planning, review, parallel work, serial integration and evidence. This local extraction keeps the Python package `codex_harness.core`.

The complete [accepted North Star v3](<docs/architecture/October 12 Source of truth/harness-north-star-v3.md>) remains the product goal. Source tests, mocks and clean imports establish bounded implementation evidence; they do not establish actual-worker success or full-vision completion. [Retained obligations](docs/legacy-capability-audit.md) explicitly record remaining capability gaps.

## Retained open obligations

These requirements remain open; this extraction does not waive them.

- [ ] Bind each actual thin attempt to its genuine live assignment and runtime.
- [ ] Validate authorization and mutate dynamic file claims atomically, including release/reacquire.
- [ ] Bind attributable mail to sender, recipient and run; establish revocation across stores.
- [ ] Authenticate localhost view-only access.
- [ ] Expose supported cancellation with truthful terminal state and recovery.
- [ ] Prove live stall/crash/interleaving recovery and claim-expiry behavior.
- [ ] Provide minimal health and supported-provider readiness diagnostics.
- [ ] Produce authorized actual-worker, integrated and locked-vision acceptance evidence.

## Core entry points

From an installed package, `p` and `plan_cli` run the planner, `run_plan` runs dispatch, and `status_server` serves the view-only cockpit. Equivalent module targets are `python -m codex_harness.core.plan_cli`, `python -m codex_harness.core.dispatch_cli`, and `python -m codex_harness.core.status_server`. For source use, put this repository's `src` directory on `PYTHONPATH`. Provider launches use the user's existing configuration and authentication; this extraction changes neither.

Start with the [planner guide](docs/operator/planner-guide.md), [handoff contract](docs/operator/handoff-contract.md) and [verification runners](docs/architecture/verification-runners.md). The runtime uses the Python standard library. Development tooling versions are pinned in `pyproject.toml`; the Windows source gate uses the existing prepared interpreter without installing packages.

## Verification and provenance

Run `python -B -m pytest tests/python/core -q` with fresh short test/temp directories, then `python -B -m ruff check src/codex_harness/core tests/python/core scripts/run_plan.py` and `python -B -m ruff format --check src/codex_harness/core tests/python/core scripts/run_plan.py`. Windows symlink privilege skips must remain explicit.

[PROVENANCE.md](PROVENANCE.md) and [extraction-manifest.json](extraction-manifest.json) identify the exact source objects and exclusions. Committed fixture reference code and sample planning/publication snapshots are static test inputs, never evidence of Seldon completion. Historical demo documents retain their source-era artifact paths; those runtime artifacts are not included here. Packaging/install behavior has not been certified by the source-only import/entry tests.

Publication is held pending the extracted-repository gate and exact-candidate approval. No real provider run is part of this extraction.
