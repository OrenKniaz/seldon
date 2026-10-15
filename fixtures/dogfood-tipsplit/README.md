# Dogfood sample: tip-split

The sample requirement for North Star v3's first outcome: a documented requirement goes to a real provider worker, the worker produces a source change, and the change passes meaningful requirement-linked validation on the integrated result.

## Folders
- **`template/`** is the workspace a run copies into a fresh git repo. It holds:
  - `VISION.md` (decisions D1–D5) and `PLAN.md` (Bead A, Bead B and the integration requirements R1–R6, with file ownership);
  - stub sources that raise `NotImplementedError`;
  - requirement-linked tests (standard library `unittest`, no dependencies).
- **`reference/`** holds solutions that prove the requirements are satisfiable. Never copy it into a worker's workspace.

## Validation
Run from the workspace root:

```
python -m unittest discover -s tests -v
```

| Workspace state | `test_money` | `test_cli` | `test_end_to_end` |
| --- | --- | --- | --- |
| Template | fails (`NotImplementedError`) | fails | fails |
| Bead A only | passes | fails | fails |
| Bead B only | fails | passes (money stubbed) | fails |
| A and B integrated | passes | passes | passes |

`tests/python/fixtures/test_dogfood_tipsplit_fixture.py` in this repo checks every row of that table.

## Used for
- **First outcome:** Bead A (Codex), with its validation run on the integrated result.
- **Then:** Bead B (Claude Code) in parallel, with disjoint ownership.
- **Memory step:** Bead B relies on the memory fact "amounts are integer cents everywhere; never floats (D1)", recorded when Bead A completes.
- **Recovery step:** inject a failure (for example a float-based `split`) and check that it is caught by integrated validation, then re-planned without lost work.
