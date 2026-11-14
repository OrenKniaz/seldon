"""S2: canonical proof locations guide semantic review and local judgment through delivery."""

import json
from copy import deepcopy

from seldon import plan
from test_journey import NATIVE, Scripted, finish, planned, prepare


def proven_once_journey(root):
    """Repair peer re-testing, then reach C without demanding integrated proof locally.

    Catches REVIEW losing foreign proof owners and JUDGE asking for a peer's local evidence.
    Existing ownership journeys exercise placement, but their semantic stubs accept duplicate cases.
    """
    class Provider(Scripted):
        def __call__(self, question):
            kind, facts = question["kind"], question["facts"]
            if kind == "ARCHITECT":
                answer = {"areas": [
                    {"handle": f"area-{n}", "responsibility": f"Deliver value {n}",
                     "scope": [f"unit-{n:03}/**"],
                     "commitments": ["scopeIn:1", "quality:1", "quality:2"]} for n in (1, 2)],
                    "contracts": [{"handle": "reading", "kind": "file-format",
                        "text": "UTF-8 value-001 in unit-001/value.txt; display as Reading: value-001.",
                        "provider": "area-1", "consumers": ["area-2"]}],
                    "decisions": [], "waves": [{"goal": "Read the provider's value in the consumer"}]}
            elif kind == "DETAIL":
                n = int(facts["Area"]["handle"][-1])
                full = planned(2, "architect")
                edits = deepcopy(full["edits"][3*(n-1):3*n])
                unit = {k: v for k, v in full["units"][n-1].items() if k != "area"}
                unit.update(provides=["reading"] if n == 1 else [],
                            consumes=["reading"] if n == 2 else [],
                            checkPaths=[f"unit-{n:03}/check.py", f"unit-{n:03}/join.py"])
                if n == 2:
                    join = deepcopy(edits[-1])
                    join.update(targetHandle="join")
                    join["typedBody"].update(level="wave", purpose="Display the real provider value: agreement only",
                        command=[*join["typedBody"]["command"][:-1], "unit-002/join.py"],
                        requirements=["new:r002", "reading"])
                    edits.append(join)
                    if "Merge correction" not in facts:
                        duplicate = deepcopy(edits[2])
                        duplicate.update(targetHandle="duplicate-peer")
                        duplicate["typedBody"].update(level="unit", purpose="Repeat provider persistence states")
                        edits.append(duplicate)
                answer = {"verdict": "planned", "edits": edits, "units": [unit],
                          "quality": full["quality"] if n == 1 else []}
            elif kind == "REVIEW" and facts["Review subject"] != "architecture":
                duplicate = next((c for row in facts["Proof map"].values() for c in row
                                  if c["purpose"] == "Repeat provider persistence states"), None)
                answer = {"verdict": "gap" if duplicate else "adequate", "gaps": [
                    {"target": duplicate["check"], "correction": "Remove the consumer's repeated provider cases"}]
                    if duplicate else []}
            elif kind == "JUDGE":
                mapping = facts["Proof map"]
                # This judge accepts a cross-unit obligation only when its actual proof location is supplied.
                if "unit-001/check.py" in facts["Check sources"]:
                    row = mapping[question["targets"][0]]
                    if not any(c["owner"] == "u002" and c["level"] == "wave" for c in row):
                        answer = {"verdict": "gap", "assessments": [], "gaps": [{
                            "target": question["targets"][0], "correction": "Where is the consumer agreement proven?"}]}
                        return json.dumps({"answer": {**answer, "why": "Missing proof location"}})
                return super().__call__(question)
            else:
                return super().__call__(question)
            return json.dumps({"answer": {**answer, "why": "Use the declared proof locations"}})

    run, _ = prepare(root, "proof-once", 2)
    run.provider = Provider(root, "proof-once", 2)
    product = "def display(value):\n    return 'Reading: ' + value\n"
    local = ("from display import display\n"
             "assert display('value-001') == 'Reading: value-001'\n")
    join = ("from pathlib import Path\nfrom display import display\n"
            "value = Path('unit-001/value.txt').read_text()\n"
            "assert display(value) == 'Reading: ' + value\n")
    injection = ("if name == 'unit-002':\n"
                 f"    Path(name, 'display.py').write_text({product!r})\n"
                 f"    with Path(name, 'check.py').open('a') as check: check.write({local!r})\n"
                 f"    Path(name, 'join.py').write_text({join!r})\n")
    (root / "native.py").write_text(NATIVE.replace('    for check in facts["Checks"].values():',
        '\n'.join('    ' + line for line in injection.splitlines())
        + '\n    for check in facts["Checks"].values():'), encoding="utf-8")
    try:
        _, rows, _ = finish(run)
        assert len(run.store.records("review_repair")) == 1
        document = run.store.plan(run.store.cursor().revision)
        assert len(plan.select(document, "check")) == 3  # two local behaviours and their join
        assert not run.store.records("unit_failure")
        judge = next(q for _, k, q in rows if k == "question" and q["kind"] == "JUDGE"
                     and "unit-001/check.py" in q["facts"]["Check sources"])
        assert {c["owner"] for row in judge["facts"]["Proof map"].values() for c in row} == {"u001", "u002"}
        final = [b for _, k, b in rows if k == "observation" and b["purpose"] == "final"]
        assert any(o["command"][-1] == "unit-002/join.py" for o in final)
        check_bytes = sum(len((run.project / f"unit-002/{p}").read_bytes()) for p in ("check.py", "join.py"))
        print(f"S2 scripted C; consumer check/product bytes: {check_bytes}/{len(product.encode())}", flush=True)
    finally:
        run.close()
