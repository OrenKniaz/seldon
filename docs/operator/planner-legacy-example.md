# Legacy planner CLI example

Return to the [planner guide](planner-guide.md). This example uses explicit legacy mode;
new plans default to semantic-v1. It demonstrates source mechanics, not live review.

Worked mini example: **26 CLI calls**, two beads (B depends on A). Save these files in the checkout (CLI JSON inputs use cwd).
Create project `template/tests_cli` with `test_one.py`, `test_two.py`, `test_end_to_end.py` using unittest assertions:
`from mini_one import inc; assert inc(2) == 3`; `from mini_two import twice; assert twice(2) == 6`; integration asserts `twice(inc(1)) == 6`.
Wrap each assertion in a `unittest.TestCase` test method. Stubs: `inc(n)`/`twice(n)` raise `NotImplementedError`.
Create project `oracle/mini_one.py`: `def inc(n): return n + 1`; `oracle/mini_two.py`: `from mini_one import inc` then `def twice(n): return 2 * inc(n)`.
A new project needs no Git commit: the template baseline runs the oracle against protected proofs in temporary copies.
Save `features.jsonl` (V1 keeps the card fields and adds compilation metadata):
```jsonl
{"id":"V1","kind":"vision","title":"Mini","text":"Increment then double","parents":[],"fields":{"vision":"Increment then double","users":"CLI user","first_user_moment":"Get 6","scope_in":"Two functions","scope_out":"GUI","assumptions":[],"validation":["-m","unittest","discover","-s","tests_cli","-p"],"requirementFiles":["tests_cli"],"quality_bar":"Deterministic arithmetic through both functions","done_feels_like":"Get 6 from the complete increment-then-double path","expansion":{"mode":"quality","reason":"Precise two-function CLI scope","upgrades":[{"id":"composed","text":"Verify the composed first moment","kind":"risk","impact":"Catch integration mistakes","disposition":"accept"},{"id":"negative","text":"Add negative-input examples","kind":"quality","impact":"Clarify arithmetic edges","disposition":"defer"},{"id":"history","text":"Keep calculation history","kind":"delight","impact":"Recall earlier results","disposition":"reject"}]}}}
{"id":"F1","kind":"feature","title":"Arithmetic","text":"Increment then double","parents":["V1"],"fields":{}}
{"id":"S1","kind":"story","title":"Calculate","text":"As a CLI user I get 6","parents":["F1"],"fields":{}}
```
Save `requirements.jsonl`:
```jsonl
{"id":"R1","kind":"requirement","title":"Increment","text":"inc(2) returns 3","parents":["S1"],"fields":{"expansionRefs":["composed"]}}
{"id":"R2","kind":"requirement","title":"Double increment","text":"twice(2) returns 6","parents":["S1"],"fields":{}}
{"id":"T1","kind":"test","title":"Increment proof","text":"Observed increment","parents":["R1"],"fields":{"module":"test_one.py","level":"unit"}}
{"id":"T2","kind":"test","title":"Double proof","text":"Observed composed result","parents":["R2"],"fields":{"module":"test_two.py","level":"unit"}}
{"id":"TE","kind":"test","title":"Integration","text":"Complete arithmetic path","parents":["R1","R2"],"fields":{"module":"test_end_to_end.py","level":"e2e","firstMoment":true,"cleanState":true}}
```
Save `beads.jsonl`:
```jsonl
{"id":"A","kind":"bead","title":"Increment","text":"Implement inc","parents":["R1"],"fields":{"ownedFiles":["mini_one.py"],"dependsOn":[],"proofModule":"test_one.py","lessons":[]}}
{"id":"B","kind":"bead","title":"Double","text":"Implement twice via inc","parents":["R2"],"fields":{"ownedFiles":["mini_two.py"],"dependsOn":["A"],"proofModule":"test_two.py","lessons":[]}}
```
Decisions may include `chosen` and `rationale`, or record them with `decision --id D1 --choose Python --rationale "Stdlib meets the scope"`.
Save `decision.json`: `{"id":"D1","question":"Runtime?","options":[{"name":"Python","pros":"Stdlib","cons":"Requires Python"}],"recommendation":"Python","parents":["V1"]}`.
Save `codex-reviewer.json`: `{"name":"codex-reviewer","provider":"codex","model":"gpt-6.1-sol","effort":"medium"}` and `claude-reviewer.json`: `{"name":"claude-reviewer","provider":"claude"}`.
Run in order; replace `<Q>` with the id printed by `ask`. At each native step, review and answer the full displayed card; no quoted CLI approval is available.
```powershell
p start --plan-mode legacy --idea "Increment then double" --name Mini
p card --vision "Increment then double" --users "CLI user" --first-moment "Get 6" --scope-in "Two functions" --scope-out GUI
# Native MCP: harness_record_confirmation gate=align; answer the displayed card.
p advance --to expand
p ask --question "Use Python stdlib?" --node V1
p answer --id "<Q>" --text "Yes, per the supplied mini scope."
p node --json "@features.jsonl"
p advance --to vision-lock
p diagram --kind user-journey
# Native MCP: harness_record_confirmation gate=vision-lock; answer the displayed card.
p advance --to features
p diagram --kind feature-map
p advance --to requirements
p node --json "@requirements.jsonl"
p advance --to tech-stack
p decision --json "@decision.json" --choose Python --rationale "Stdlib meets the scope"
p advance --to plan
p node --json "@beads.jsonl"
p compile --template template --out manifest.json
p diagram --kind bead-dag
p advance --to roundtable
p roundtable --reviewer "@codex-reviewer.json" --reviewer "@claude-reviewer.json"
p audit --oracle oracle
p quality
p advance --to publish
p publish
```
Real roundtables launch providers normally. Test-only `--test-only-review-output response.json` injects an explicit finding array with supplied reviewers; injected/mock reviews never count as live evidence. The example's source test stops at the real review gate.
