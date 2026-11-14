"""T-140: consumer-owned integrated checks, through planning, proof and scoped repair."""

import json
import time
from copy import deepcopy

import pytest

from seldon import plan
from seldon.run import Run
from test_journey import NATIVE, Scripted, approve, finish, planned


class ContractProvider(Scripted):
    def __init__(self, slot, misplaced=False):
        super().__init__(slot, "rule1", 4)
        self.misplaced = misplaced

    def __call__(self, question):
        kind, facts = question["kind"], question["facts"]
        if kind == "ARCHITECT":
            answer = {"areas": [{"handle": name, "responsibility": name,
                "scope": [f"unit-{i:03}/**"],
                "commitments": ["scopeIn:1", "quality:1", "quality:2"]}
                for i, name in enumerate(("data", "server", "ui", "delivery"), 1)],
                "contracts": [{"handle": name, "kind": "file-format", "text": text,
                    "provider": owner, "consumers": consumers}
                    for name, text, owner, consumers in (
                        ("store", "The stored interaction is ready.", "data", ["server", "delivery"]),
                        ("http", "The server returns HTTP 200.", "server", ["ui", "delivery"]),
                        ("assets", "The UI supplies offline assets.", "ui", ["delivery"]))],
                "decisions": [], "waves": [{"goal": "Run the complete product"}]}
        elif kind == "DETAIL":
            name = facts["Area"]["handle"]
            i = ("data", "server", "ui", "delivery").index(name) + 1
            full = planned(4, "rule1")
            unit = {k: v for k, v in full["units"][i-1].items() if k != "area"}
            unit.update(checkPaths=[f"unit-{i:03}/*.py"],
                provides=[r for r, c in facts["Contracts"].items() if c["side"] == "provider"],
                consumes={"data": [], "server": ["store"], "ui": ["http"],
                          "delivery": ["store", "http", "assets"]}[name])
            edits = deepcopy(full["edits"][3*(i-1):3*i])
            if name == "data" and self.misplaced and "Exact corrections" not in facts:
                # The obsolete provider-owned contract reference must get an ordinary DETAIL correction.
                edits.append({"kind": "check", "targetHandle": "new:wrong", "typedBody": {
                    **edits[2]["typedBody"], "level": "wave", "requirements": ["new:r001", "store"],
                    "command": [edits[2]["typedBody"]["command"][0], "unit-001/wave.py"]}})
            if name == "delivery":
                edits.append({"kind": "check", "targetHandle": "new:wave", "typedBody": {
                    **edits[2]["typedBody"], "level": "wave",
                    "purpose": "Read the integrated store and HTTP response",
                    "command": [edits[2]["typedBody"]["command"][0], "-B", "unit-004/wave.py"],
                    "requirements": ["new:r004", "store", "http", "assets"]}})
            answer = {"verdict": "planned", "edits": edits, "units": [unit],
                      "quality": full["quality"] if i == 1 else []}
        elif kind == "JUDGE":
            # The c03 judge rejects exact response assertions absent from this unit's agreements.
            agreements = " ".join(c["text"] for label in ("Contracts provided", "Contracts consumed")
                                  for c in facts.get(label, {}).values())
            if any("HTTP 200" in source for source in facts["Check sources"].values()) \
                    and "HTTP 200" not in agreements:
                return json.dumps({"answer": {"verdict": "gap", "gaps": [{
                    "target": question["targets"][0], "correction": "HTTP response agreement missing"}],
                    "assessments": [], "why": "This unit cannot assert an unseen agreement"}})
            return super().__call__(question)
        else:
            return super().__call__(question)
        return json.dumps({"answer": {**answer, "why": "Checks stay within their owner's agreements"}})


def contract_owner_journeys(root):
    """A c03 four-area twin reaches B/DONE; a repeated final cause asks at its repair owner before continuing.

    Earlier journeys let a leaf provider drive unseen HTTP agreements and never use a consumed
    contract reference to carry provider requirements through merge into wave proof and handoff.
    The misplaced-reference twin catches accepting a provider's own contract instead of correcting it.
    These scripted semantic answers establish structure and recovery, not real-model acceptance.
    """
    began = time.monotonic()
    for broken in (False, True):
        slot = root / ("broken" if broken else "c03-twin")
        slot.mkdir(parents=True)
        (slot / "mode").write_text("rule1")
        wave = ("from pathlib import Path\n"
                "assert Path('unit-001/interaction.txt').read_text() == 'ready'\n"
                "assert Path('unit-002/http.txt').read_text() == 'HTTP 200'\n"
                "assert Path('unit-003/value.txt').read_text() == 'value-003'\n"
                "print('integrated store and HTTP response')\n")
        injection = ("if not guide and name == 'unit-001':\n"
                     f"    Path(name, 'interaction.txt').write_text('ready' if turn > 1 or {not broken!r} "
                     "else 'blocked')\n"
                     "if not guide and name == 'unit-002':\n"
                     "    Path(name, 'http.txt').write_text('HTTP 200')\n"
                     "if not guide and name == 'unit-004':\n"
                     f"    Path(name, 'wave.py').write_text({wave!r})\n"
                     "    if turn > 1 and Path('unit-001/interaction.txt').read_text() == 'blocked':\n"
                     "        answer['answer'].update(outcome='blocked', discoveries=[{\n"
                     "            'owner': 'u001', 'correction': 'Make the stored interaction ready'}])\n")
        (slot / "native.py").write_text(NATIVE.replace(
            'print(json.dumps({"type":"item.completed"',
            injection + 'print(json.dumps({"type":"item.completed"'), encoding="utf-8")
        provider = ContractProvider(slot, misplaced=broken)
        run = Run(slot / "product", slot / "workers", "Read named values", provider.setup,
                  provider, approve, capacity=4)
        try:
            while run.store.cursor().state != "B":
                assert not run.store.cursor().decision
                run.step()
            assert not run.store.records("architecture_gap")
            document = run.store.plan(run.store.cursor().revision)
            wave_ref, = plan.assigned(document)[None]
            assert plan.owner(document, wave_ref) == "u004"
            assert set(plan.traced_units(document, plan.assigned(document)[None])) == {
                "u001", "u002", "u003", "u004"}
            if broken:
                from seldon.decisions import Waiting
                assert run.run().decision
                request = run.store.read(run.store.cursor().decision)
                assert request["gate"] == "UNIT" and request["subject"]["unit"] == "u001"
                answered = []
                def user(item, answered=answered):
                    if item["gate"] != "UNIT":
                        return approve(item)
                    if answered:
                        raise Waiting()
                    answered.append(item["request"])
                    return "Repair the stored interaction through its data owner"
                run.user = user
            _, rows, units = finish(run)
            assert all(u.state == "DONE" for u in units.values())
            assert not [b for _, k, b in rows if k == "unit_failure" and b["unit"] == "u004"]
            corrections = [q for _, k, q in rows if k == "question" and q["kind"] == "DETAIL"
                           and "Exact corrections" in q["facts"]]
            assert bool(corrections) == broken
            if broken:
                assignments = [b for _, k, b in rows if k == "unit_fixup"]
                assert [b["owner"] for b in assignments] == ["u004", "u001"]
                failure = run.store.read(assignments[0]["gaps"][0]["source"])
                assert failure["check"] == wave_ref and failure["exitCode"] != 0
                assert run.store.read(assignments[1]["gaps"][0]["source"])["answer"]["outcome"] == "blocked"
                assert (slot / "turn-unit-001").read_text() == "2"
                assert (run.project / "unit-001/interaction.txt").read_text() == "ready"
            else:
                assert not run.store.records("unit_fixup")
            assert any(b["check"] == wave_ref and b["exitCode"] == 0
                       and b["commit"] == run.store.cursor().candidate
                       for _, k, b in rows if k == "observation")
        finally:
            run.close()
    print(f"Rule 1 contract-owner journeys: {time.monotonic()-began:.2f}s", flush=True)


@pytest.mark.fast
def test_contract_owner_journeys(tmp_path):
    contract_owner_journeys(tmp_path / "rule1")
