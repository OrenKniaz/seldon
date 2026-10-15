<!-- Generated from spine.json; document revision 659ff66fb833 -->

# Requirements

## F1: Record water (3bb0aec799d4)

Record a positive amount in ml or oz with exact integer ml conversion.

### S1: Record water story (f3ce8432f881)

As the sole user, I quickly record a drink in my preferred supported unit.

#### R1: Units and exact rounding (89ffad59b741)

parse_amount(amount: str, unit='ml') -> int. Decimal arithmetic, 1 oz=29.5735 ml; nearest integer, ties half up. Examples: 8 oz=237 ml; 1 oz=30; 1.5 ml=2; omitted unit=ml.

Use a local Decimal context with precision max(28, coefficient digit count + 8), enough to multiply the exact five-digit oz coefficient and round long decimal ties correctly. Inputs whose integer-ml conversion exceeds this documented Decimal context capacity are invalid; normalize every DecimalException to ValueError.

#### R2: Reject invalid amounts and units (de7ae47376ab)

Raise ValueError for zero, negative, unknown units, garbage, nonfinite values and positive amounts rounding to zero. Unit tokens are exactly ml and oz.

Reject unrepresentable Decimal conversions and arithmetic overflow as ValueError; no traceback. Long exact ml ties are tested independently of the global Decimal precision.

## F2: Trust local data (575fb07906ac)

Persist the goal and ordered day entries safely, and undo only today's last entry.

### S2: Trust local data story (aa9327f59700)

As the sole user, I trust errors to preserve my history and can undo a mistaken drink.

#### R3: Local JSON persistence (8b9fa3b0355a)

load(Path)->dict; save(Path,dict)->None; add_entry(data,day,ml)->new dict. Exact schema: {goal_ml:positive int, entries:[{day:YYYY-MM-DD,ml:positive int}]}; default seed goal=2000. Appends preserve order and inputs; save/load roundtrip exact values.

#### R4: Missing corrupt and failed-write errors (29adb2fc6e6c)

load rejects missing/unreadable/invalid UTF-8/invalid JSON/schema with DataError, without creating or resetting files. Validate positive integers (not bool) and actual ISO calendar dates. save validates then writes a sibling temporary file, flushes/fsyncs, atomically os.replace; replacement failure preserves original bytes and cleans the temp. CLI returns 2 with clear stderr, never a traceback. No concurrent-process guarantee; single sequential user commands only.

#### R5: Undo today's last entry gracefully (1651a05430cc)

undo_entry(data,day)->(new dict,removed ml or None). Remove the last matching day in insertion order, preserving all other entries. No matching entry: unchanged state, CLI prints Nothing to undo today., exits 0 and performs no write.

#### R6: Configure a positive integer goal (63c4aebc6543)

set_goal(data,ml)->new dict. Reject noninteger/zero/negative/bool goals. goal shows current ml; goal 2500 persists 2500 without changing entries. Initial documented seed=2000; history uses the current goal, not historical goal snapshots.

## F3: See progress (b41db9be9f25)

Read today's uncapped percentage and a chronological calendar-day history.

### S3: See progress story (0e55a7dca340)

As the sole user, I see how far I am toward my current goal, including empty days.

#### R7: Today's local-day percentage (46c22a6e8041)

today_report(data,day)->str: YYYY-MM-DD: N ml / G ml (P.P%). Sum only the requested local calendar day. Zero intake is 0.0%; over-goal is uncapped (e.g.125.0%); format one decimal using Python's standard fixed-point formatting.

#### R8: Calendar history and invalid windows (05a301860860)

history_report(data,day,days=7)->list[str]. Positive integer days, oldest to newest, includes today and zero-intake dates across month/year boundaries. history defaults to 7; reject days <=0. Current goal applies to all rows.

Reject history windows extending before date.min with ValueError before constructing timedeltas; real CLI returns2, empty stdout, one-line stderr and unchanged bytes even for a huge positive days integer.

## F4: Simple terminal workflow (5ea427dcbe11)

Five commands, local calendar boundary, configured data location, concise success/error outputs.

### S4: Simple terminal workflow story (ca8c9a4dcad5)

As the sole user, I use the CLI across processes without tracing implementation details.

#### R10: CLI clear errors without writes (957cacf56c87)

Unknown command, missing args, noninteger goal/history, invalid units/amounts and storage failures return 2, empty stdout, one-line Error: ... stderr without traceback. Missing file errors never create files. Successful read-only commands never save; no-op undo never saves. --help may use argparse's conventional help exit.

#### R11: Integrated daily water journey (83750d3d0197)

Separate real CLI processes use a temp WATER_CLI_DATA seed: goal=2000; add 250; add 8 oz; today=487 ml,24.3%; history default 7/custom2; goal1000 persists,48.7%; undo237 leaves250; undo again; empty undo. Invalid inputs preserve bytes; corrupt JSON and missing files return2 without traceback or reset.

Additional subprocess scenario: pre-seed an intake on local day minus two, leave day minus one empty, add 200 ml today, and require history 3 to show 100 ml / 0 ml / 200 ml in chronological order. R8 report unit proof also checks Dec 31 to Jan 1.

#### R9: Five CLI commands and outputs (c07889060eda)

main(argv=None)->int dispatches add <amount> [unit], today, history [days], goal [ml], undo. Module entry point python -m watercli.cli. WATER_CLI_DATA overrides ./water.json. Capture date.today().isoformat() once per command (local day). Outputs: Added N ml.; report line(s); Goal: N ml.; Removed N ml.; Nothing to undo today. Success=0. Imports public dependency names into cli namespace for isolated orchestration tests.

All configured WATER_CLI_DATA paths follow the same R4 missing/unreadable/corrupt-file error rules as the default path. Both goal (show) and goal <ml> (set) print Goal: N ml.; add prints Added N ml.; today/history print report rows; undo prints Removed N ml. or Nothing to undo today.
