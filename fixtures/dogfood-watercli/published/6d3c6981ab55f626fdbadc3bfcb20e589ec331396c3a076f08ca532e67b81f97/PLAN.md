<!-- Generated from spine.json; document revision b328176351ca -->

# Plan

Validation: `-B -m unittest discover -s tests -p`

Requirement files: VISION.md, PLAN.md, REQUIREMENTS.md, TRACE.md, tests

## A: units implementation (74f47ce86bab)

Implement only watercli/units.py against the fixed public stubs and linked requirements. Acceptance: python -B -m unittest discover -s tests -p test_units.py passes; do not modify planner-owned tests/docs or __init__.py. Oracle is planner-only and absent from worker packets.

- Owned files: watercli/units.py
- Dependencies: (none)
- Requirement refs: R1@89ffad59b741, R2@de7ae47376ab
- Proof: `test_units.py`
- Provider: codex
- Parents: DES_UNITS

Lesson after validation (D_CONTRACT): Planner detail choices within accepted scope: HALF_UP integer ml; explicit setup file; current goal for history; sequential invocations; atomic replace without backups. No new init command or automatic reset.

Harness validation is an argv PREFIX: core.dispatch._validate constructs [sys.executable, *validation, proofModule]. The terminal -p receives the selected module pattern. Standalone full suite: python -B -m unittest discover -s tests. This prefix must not be run by itself.

## B: store implementation (7b259b65a5cd)

Implement only watercli/store.py against the fixed public stubs and linked requirements. Acceptance: python -B -m unittest discover -s tests -p test_store.py passes; do not modify planner-owned tests/docs or __init__.py. Oracle is planner-only and absent from worker packets.

- Owned files: watercli/store.py
- Dependencies: (none)
- Requirement refs: R3@8b9fa3b0355a, R4@29adb2fc6e6c, R5@1651a05430cc, R6@63c4aebc6543
- Proof: `test_store.py`
- Provider: claude
- Parents: DES_STORE

Lesson after validation (D_CONTRACT): Planner detail choices within accepted scope: HALF_UP integer ml; explicit setup file; current goal for history; sequential invocations; atomic replace without backups. No new init command or automatic reset.

Harness validation is an argv PREFIX: core.dispatch._validate constructs [sys.executable, *validation, proofModule]. The terminal -p receives the selected module pattern. Standalone full suite: python -B -m unittest discover -s tests. This prefix must not be run by itself.

## C: report implementation (6965b357c564)

Implement only watercli/report.py against the fixed public stubs and linked requirements. Acceptance: python -B -m unittest discover -s tests -p test_report.py passes; do not modify planner-owned tests/docs or __init__.py. Oracle is planner-only and absent from worker packets.

- Owned files: watercli/report.py
- Dependencies: (none)
- Requirement refs: R7@46c22a6e8041, R8@05a301860860
- Proof: `test_report.py`
- Provider: codex
- Parents: DES_REPORT

Lesson after validation (D_CONTRACT): Planner detail choices within accepted scope: HALF_UP integer ml; explicit setup file; current goal for history; sequential invocations; atomic replace without backups. No new init command or automatic reset.

Harness validation is an argv PREFIX: core.dispatch._validate constructs [sys.executable, *validation, proofModule]. The terminal -p receives the selected module pattern. Standalone full suite: python -B -m unittest discover -s tests. This prefix must not be run by itself.

## D: cli implementation (6c38f595a9cf)

Implement only watercli/cli.py against the fixed public stubs and linked requirements. Acceptance: python -B -m unittest discover -s tests -p test_cli.py passes; do not modify planner-owned tests/docs or __init__.py. Oracle is planner-only and absent from worker packets.

Integration ownership: after A/B/C are integrated, D must also run python -B -m unittest discover -s tests -p test_end_to_end.py using the real modules and separate subprocesses. The controller integration gate uses sample.integration.proofModule; no product completion claim is made by this planning fixture.

- Owned files: watercli/cli.py
- Dependencies: A, B, C
- Requirement refs: R10@957cacf56c87, R11@83750d3d0197, R9@c07889060eda
- Proof: `test_cli.py`
- Provider: claude
- Parents: DES_CLI

Lesson after validation (D_CONTRACT): Planner detail choices within accepted scope: HALF_UP integer ml; explicit setup file; current goal for history; sequential invocations; atomic replace without backups. No new init command or automatic reset.

Harness validation is an argv PREFIX: core.dispatch._validate constructs [sys.executable, *validation, proofModule]. The terminal -p receives the selected module pattern. Standalone full suite: python -B -m unittest discover -s tests. This prefix must not be run by itself.

## Designs

### DES_CLI: CLI data flow (ee73e60a7ae9)

argv/env -> argparse -> local date -> load -> units/pure transforms or report -> save on mutations -> stdout. ValueError/DataError/OSError -> clear stderr and exit2. Canonical imported names are patched only in CLI unit proofs; e2e uses real modules and processes.

Parents: D_CONTRACT, D_STACK, R10, R11, R9

### DES_REPORT: Reporting boundary (1f87fb7500c4)

watercli/report.py contains pure today/history functions; caller supplies local day, data and current goal. No storage or units import.

Parents: D_CONTRACT, D_STACK, R7, R8

### DES_STORE: Persistence boundary (1392fb746c81)

watercli/store.py owns DataError, load/save and pure add/undo/goal transforms. Exact schema, insertion order, copy-on-update and atomic replacement. Explicit seed docs precede first add.

Parents: D_CONTRACT, D_STACK, R3, R4, R5, R6

### DES_UNITS: Units boundary (c2ed299dc046)

watercli/units.py exposes parse_amount; Decimal avoids conversion drift. Raises ValueError for caller presentation.

Parents: D_CONTRACT, D_STACK, R1, R2
