<!-- Generated from spine.json; document revision 5962ec53491b -->

# Plan

Validation argv prefix: `-B -m unittest discover -s tests -p`

Append the bead's proof module to this prefix; do not run it by itself.

Requirement files: VISION.md, PLAN.md, REQUIREMENTS.md, TRACE.md, tests

## A: units implementation (74f47ce86bab)

Implement only watercli/units.py against the fixed public stubs and linked requirements. Acceptance: python -B -m unittest discover -s tests -p test_units.py passes; do not modify planner-owned tests/docs or __init__.py. Oracle is planner-only and absent from worker packets.

- Owned files: watercli/units.py
- Dependencies: (none)
- Requirement refs: R1@89ffad59b741, R2@de7ae47376ab
- Proof: `test_units.py`
- Provider: codex
- Parents: DES_UNITS

### D_CONTRACT: Freeze simple interfaces and storage errors (f41d53b5825b)

Planner detail choices within accepted scope: HALF_UP integer ml; explicit setup file; current goal for history; sequential invocations; atomic replace without backups. No new init command or automatic reset.

Harness validation is an argv PREFIX: core.dispatch._validate constructs [sys.executable, *validation, proofModule]. The terminal -p receives the selected module pattern. Standalone full suite: python -B -m unittest discover -s tests. This prefix must not be run by itself.

### D_STACK: Which stack keeps this personal CLI small? (b57a7dc9b5f8)

Decision D_STACK: Which stack keeps this personal CLI small?
Linked to: V1
- Python 3.11+ standard library
  Pros: Matches proxy preference; Decimal, argparse, JSON, tempfile, unittest; no runtime dependencies.
  Cons: Requires installed Python; no third-party command framework.
- Python + Click
  Pros: Convenient command framework.
  Cons: Adds a dependency and violates stdlib-only preference.
Recommendation: Python 3.11+ standard library
Reply with an option name (Python 3.11+ standard library / Python + Click), or explain the change you want.
Decision: choose standard library, per the supplied user-proxy preference; unittest is the requirement proof runner.

### R1: Units and exact rounding (89ffad59b741)

parse_amount(amount: str, unit='ml') -> int. Decimal arithmetic, 1 oz=29.5735 ml; nearest integer, ties half up. Examples: 8 oz=237 ml; 1 oz=30; 1.5 ml=2; omitted unit=ml.

Use a local Decimal context with precision max(28, coefficient digit count + 8), enough to multiply the exact five-digit oz coefficient and round long decimal ties correctly. Inputs whose integer-ml conversion exceeds this documented Decimal context capacity are invalid; normalize every DecimalException to ValueError.

### R2: Reject invalid amounts and units (de7ae47376ab)

Raise ValueError for zero, negative, unknown units, garbage, nonfinite values and positive amounts rounding to zero. Unit tokens are exactly ml and oz.

Reject unrepresentable Decimal conversions and arithmetic overflow as ValueError; no traceback. Long exact ml ties are tested independently of the global Decimal precision.

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

### D_CONTRACT: Freeze simple interfaces and storage errors (f41d53b5825b)

Planner detail choices within accepted scope: HALF_UP integer ml; explicit setup file; current goal for history; sequential invocations; atomic replace without backups. No new init command or automatic reset.

Harness validation is an argv PREFIX: core.dispatch._validate constructs [sys.executable, *validation, proofModule]. The terminal -p receives the selected module pattern. Standalone full suite: python -B -m unittest discover -s tests. This prefix must not be run by itself.

### D_STACK: Which stack keeps this personal CLI small? (b57a7dc9b5f8)

Decision D_STACK: Which stack keeps this personal CLI small?
Linked to: V1
- Python 3.11+ standard library
  Pros: Matches proxy preference; Decimal, argparse, JSON, tempfile, unittest; no runtime dependencies.
  Cons: Requires installed Python; no third-party command framework.
- Python + Click
  Pros: Convenient command framework.
  Cons: Adds a dependency and violates stdlib-only preference.
Recommendation: Python 3.11+ standard library
Reply with an option name (Python 3.11+ standard library / Python + Click), or explain the change you want.
Decision: choose standard library, per the supplied user-proxy preference; unittest is the requirement proof runner.

### R3: Local JSON persistence (8b9fa3b0355a)

load(Path)->dict; save(Path,dict)->None; add_entry(data,day,ml)->new dict. Exact schema: {goal_ml:positive int, entries:[{day:YYYY-MM-DD,ml:positive int}]}; default seed goal=2000. Appends preserve order and inputs; save/load roundtrip exact values.

### R4: Missing corrupt and failed-write errors (29adb2fc6e6c)

load rejects missing/unreadable/invalid UTF-8/invalid JSON/schema with DataError, without creating or resetting files. Validate positive integers (not bool) and actual ISO calendar dates. save validates then writes a sibling temporary file, flushes/fsyncs, atomically os.replace; replacement failure preserves original bytes and cleans the temp. CLI returns 2 with clear stderr, never a traceback. No concurrent-process guarantee; single sequential user commands only.

### R5: Undo today's last entry gracefully (1651a05430cc)

undo_entry(data,day)->(new dict,removed ml or None). Remove the last matching day in insertion order, preserving all other entries. No matching entry: unchanged state, CLI prints Nothing to undo today., exits 0 and performs no write.

### R6: Configure a positive integer goal (63c4aebc6543)

set_goal(data,ml)->new dict. Reject noninteger/zero/negative/bool goals. goal shows current ml; goal 2500 persists 2500 without changing entries. Initial documented seed=2000; history uses the current goal, not historical goal snapshots.

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

### D_CONTRACT: Freeze simple interfaces and storage errors (f41d53b5825b)

Planner detail choices within accepted scope: HALF_UP integer ml; explicit setup file; current goal for history; sequential invocations; atomic replace without backups. No new init command or automatic reset.

Harness validation is an argv PREFIX: core.dispatch._validate constructs [sys.executable, *validation, proofModule]. The terminal -p receives the selected module pattern. Standalone full suite: python -B -m unittest discover -s tests. This prefix must not be run by itself.

### D_STACK: Which stack keeps this personal CLI small? (b57a7dc9b5f8)

Decision D_STACK: Which stack keeps this personal CLI small?
Linked to: V1
- Python 3.11+ standard library
  Pros: Matches proxy preference; Decimal, argparse, JSON, tempfile, unittest; no runtime dependencies.
  Cons: Requires installed Python; no third-party command framework.
- Python + Click
  Pros: Convenient command framework.
  Cons: Adds a dependency and violates stdlib-only preference.
Recommendation: Python 3.11+ standard library
Reply with an option name (Python 3.11+ standard library / Python + Click), or explain the change you want.
Decision: choose standard library, per the supplied user-proxy preference; unittest is the requirement proof runner.

### R7: Today's local-day percentage (46c22a6e8041)

today_report(data,day)->str: YYYY-MM-DD: N ml / G ml (P.P%). Sum only the requested local calendar day. Zero intake is 0.0%; over-goal is uncapped (e.g.125.0%); format one decimal using Python's standard fixed-point formatting.

### R8: Calendar history and invalid windows (05a301860860)

history_report(data,day,days=7)->list[str]. Positive integer days, oldest to newest, includes today and zero-intake dates across month/year boundaries. history defaults to 7; reject days <=0. Current goal applies to all rows.

Reject history windows extending before date.min with ValueError before constructing timedeltas; real CLI returns2, empty stdout, one-line stderr and unchanged bytes even for a huge positive days integer.

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

### D_CONTRACT: Freeze simple interfaces and storage errors (f41d53b5825b)

Planner detail choices within accepted scope: HALF_UP integer ml; explicit setup file; current goal for history; sequential invocations; atomic replace without backups. No new init command or automatic reset.

Harness validation is an argv PREFIX: core.dispatch._validate constructs [sys.executable, *validation, proofModule]. The terminal -p receives the selected module pattern. Standalone full suite: python -B -m unittest discover -s tests. This prefix must not be run by itself.

### D_STACK: Which stack keeps this personal CLI small? (b57a7dc9b5f8)

Decision D_STACK: Which stack keeps this personal CLI small?
Linked to: V1
- Python 3.11+ standard library
  Pros: Matches proxy preference; Decimal, argparse, JSON, tempfile, unittest; no runtime dependencies.
  Cons: Requires installed Python; no third-party command framework.
- Python + Click
  Pros: Convenient command framework.
  Cons: Adds a dependency and violates stdlib-only preference.
Recommendation: Python 3.11+ standard library
Reply with an option name (Python 3.11+ standard library / Python + Click), or explain the change you want.
Decision: choose standard library, per the supplied user-proxy preference; unittest is the requirement proof runner.

### R10: CLI clear errors without writes (957cacf56c87)

Unknown command, missing args, noninteger goal/history, invalid units/amounts and storage failures return 2, empty stdout, one-line Error: ... stderr without traceback. Missing file errors never create files. Successful read-only commands never save; no-op undo never saves. --help may use argparse's conventional help exit.

### R11: Integrated daily water journey (83750d3d0197)

Separate real CLI processes use a temp WATER_CLI_DATA seed: goal=2000; add 250; add 8 oz; today=487 ml,24.3%; history default 7/custom2; goal1000 persists,48.7%; undo237 leaves250; undo again; empty undo. Invalid inputs preserve bytes; corrupt JSON and missing files return2 without traceback or reset.

Additional subprocess scenario: pre-seed an intake on local day minus two, leave day minus one empty, add 200 ml today, and require history 3 to show 100 ml / 0 ml / 200 ml in chronological order. R8 report unit proof also checks Dec 31 to Jan 1.

### R9: Five CLI commands and outputs (c07889060eda)

main(argv=None)->int dispatches add <amount> [unit], today, history [days], goal [ml], undo. Module entry point python -m watercli.cli. WATER_CLI_DATA overrides ./water.json. Capture date.today().isoformat() once per command (local day). Outputs: Added N ml.; report line(s); Goal: N ml.; Removed N ml.; Nothing to undo today. Success=0. Imports public dependency names into cli namespace for isolated orchestration tests.

All configured WATER_CLI_DATA paths follow the same R4 missing/unreadable/corrupt-file error rules as the default path. Both goal (show) and goal <ml> (set) print Goal: N ml.; add prints Added N ml.; today/history print report rows; undo prints Removed N ml. or Nothing to undo today.

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

## Integration

### T_E2E: T_E2E requirement proof (237380709929)

Planner-owned tests; worker must not edit tests.

Proof: `test_end_to_end.py`

#### D_CONTRACT: Freeze simple interfaces and storage errors (f41d53b5825b)

Planner detail choices within accepted scope: HALF_UP integer ml; explicit setup file; current goal for history; sequential invocations; atomic replace without backups. No new init command or automatic reset.

Harness validation is an argv PREFIX: core.dispatch._validate constructs [sys.executable, *validation, proofModule]. The terminal -p receives the selected module pattern. Standalone full suite: python -B -m unittest discover -s tests. This prefix must not be run by itself.

#### D_STACK: Which stack keeps this personal CLI small? (b57a7dc9b5f8)

Decision D_STACK: Which stack keeps this personal CLI small?
Linked to: V1
- Python 3.11+ standard library
  Pros: Matches proxy preference; Decimal, argparse, JSON, tempfile, unittest; no runtime dependencies.
  Cons: Requires installed Python; no third-party command framework.
- Python + Click
  Pros: Convenient command framework.
  Cons: Adds a dependency and violates stdlib-only preference.
Recommendation: Python 3.11+ standard library
Reply with an option name (Python 3.11+ standard library / Python + Click), or explain the change you want.
Decision: choose standard library, per the supplied user-proxy preference; unittest is the requirement proof runner.

#### R10: CLI clear errors without writes (957cacf56c87)

Unknown command, missing args, noninteger goal/history, invalid units/amounts and storage failures return 2, empty stdout, one-line Error: ... stderr without traceback. Missing file errors never create files. Successful read-only commands never save; no-op undo never saves. --help may use argparse's conventional help exit.

#### R11: Integrated daily water journey (83750d3d0197)

Separate real CLI processes use a temp WATER_CLI_DATA seed: goal=2000; add 250; add 8 oz; today=487 ml,24.3%; history default 7/custom2; goal1000 persists,48.7%; undo237 leaves250; undo again; empty undo. Invalid inputs preserve bytes; corrupt JSON and missing files return2 without traceback or reset.

Additional subprocess scenario: pre-seed an intake on local day minus two, leave day minus one empty, add 200 ml today, and require history 3 to show 100 ml / 0 ml / 200 ml in chronological order. R8 report unit proof also checks Dec 31 to Jan 1.

#### R8: Calendar history and invalid windows (05a301860860)

history_report(data,day,days=7)->list[str]. Positive integer days, oldest to newest, includes today and zero-intake dates across month/year boundaries. history defaults to 7; reject days <=0. Current goal applies to all rows.

Reject history windows extending before date.min with ValueError before constructing timedeltas; real CLI returns2, empty stdout, one-line stderr and unchanged bytes even for a huge positive days integer.

#### R9: Five CLI commands and outputs (c07889060eda)

main(argv=None)->int dispatches add <amount> [unit], today, history [days], goal [ml], undo. Module entry point python -m watercli.cli. WATER_CLI_DATA overrides ./water.json. Capture date.today().isoformat() once per command (local day). Outputs: Added N ml.; report line(s); Goal: N ml.; Removed N ml.; Nothing to undo today. Success=0. Imports public dependency names into cli namespace for isolated orchestration tests.

All configured WATER_CLI_DATA paths follow the same R4 missing/unreadable/corrupt-file error rules as the default path. Both goal (show) and goal <ml> (set) print Goal: N ml.; add prints Added N ml.; today/history print report rows; undo prints Removed N ml. or Nothing to undo today.
