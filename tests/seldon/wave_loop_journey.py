"""C8/C17/C23: real Git products grow while check-in answers gate only work."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from copy import deepcopy

from seldon import gitwork, plan
from seldon.decisions import Waiting
from seldon.run import Run
from test_journey import Scripted, approve, finish, prepare, unit_ref


class Waves(Scripted):
    def invoke(self, q, started):
        result = super().invoke(q, started)
        marker = self.slot / "interrupt"
        if q["kind"] == "WORK" and q["facts"]["Write scope"] == ["unit-003/**"] and marker.exists():
            marker.unlink()
            os._exit(71)  # Kill the controller after the native reply, before it records the report.
        return result

    def __call__(self, q):
        kind, facts = q["kind"], q["facts"]
        if kind == "ARCHITECT" and "Finished wave" in facts:
            previous = facts["Previous architecture"]
            if not facts["Remaining waves"]:
                previous = deepcopy(previous)
                previous["waves"].append({"goal": "Polish the extension", "commitments": ["scopeIn:2"]})
                return json.dumps({"answer": {"areas": [], "contracts": [], "decisions": [],
                    "waves": previous["waves"], "why": "Apply the C direction"}})
            # Terra returned the remaining horizon, with full area/contract records.
            # Translate its domain handles to this executable product; preserve the answer shape.
            recorded = json.loads((Path(__file__).parent / "fixtures" /
                                   "shelflife-wave2-steer.json").read_text(encoding="utf-8"))
            answer = recorded["answer"]
            for field in ("areas", "contracts", "decisions"):
                answer[field] = deepcopy(previous[field])
            answer["waves"] = deepcopy(facts["Remaining waves"])
            return json.dumps(recorded)
        if kind == "JUDGE":
            for wave in (3, 2):
                sentence = f"Read extension-{wave}.txt."
                if any(sentence in b["acceptance"] for b in facts["Requirements"].values()):
                    assert all(sentence in b["acceptance"] for b in facts["Requirements"].values())
                    break
        answer = json.loads(super().__call__(q))["answer"]
        if kind == "ALIGN":
            answer["scopeIn"].append("Extend the saved values")
        elif kind == "ARCHITECT":
            answer["areas"][0]["commitments"].append("scopeIn:2")
            answer["waves"].append({"goal": "Extend saved values", "commitments": ["scopeIn:2"]})
        elif kind == "DETAIL":
            later = facts["Current wave"]["goal"] != "Deliver the complete values"
            polish = facts["Current wave"]["goal"] == "Polish the extension"
            keep = (1,) if polish else (1, 3) if later else (1, 2)
            answer["units"] = [u for u in answer["units"]
                               if int(u["scope"][0].split("/")[0].split("-")[1]) in keep]
            answer["edits"] = [e for e in answer["edits"] if int(e["targetHandle"][1:]) in keep]
            if later:
                wave = 3 if facts["Current wave"]["goal"] == "Polish the extension" else 2
                for e in answer["edits"]:
                    if e["targetHandle"] == "r1":
                        e["typedBody"]["acceptance"] += f" Read extension-{wave}.txt."
                if not polish:
                    answer["units"][1]["dependencies"] = [1]
            else:
                answer["units"][1]["dependencies"] = [1]
            if "Merge correction" in facts:
                for unit in answer["units"]:
                    unit["instructions"] += " Preserve the existing saved values."
        elif kind == "REVIEW" and self.mode == "review":
            wave = max(int(r.split(".")[0]) for u in facts["Units"].values()
                       for r in u["requirements"])
            if wave not in self.gaps:
                self.gaps.add(wave)
                answer.update(verdict="gap", gaps=[{
                    "target": "plan", "correction": "Preserve the existing saved values."}])
        return json.dumps({"answer": answer})


def prepared(slot, fault):
    run, _ = prepare(slot, "normal", 3)
    provider = Waves(slot, "review" if fault else "normal", 3)
    run.provider = provider
    native = slot / "native.py"
    source = native.read_text()
    source = source.replace("    Path(name, 'check.py').write_text(source)", """
    if number == 1:
        wave = 3 if 'extension-3.txt' in str(facts['Requirements']) else 2 if 'extension-2.txt' in str(facts['Requirements']) else 1
        Path(name, 'format.txt').write_text('1' if wave == 1 else '2')
        if wave > 1:
            path = name + '/extension-' + str(wave) + '.txt'
            Path(path).write_text('extended')
            source += f"assert Path({path!r}).read_text() == 'extended'\\n"
    if number == 2:
        Path(name, 'max-format.txt').write_text('1' if FAULT and turn == 1 else '2')
        source += "assert int(Path('unit-001/format.txt').read_text()) <= int(Path('unit-002/max-format.txt').read_text()), 'reader cannot read the new format'\\n"
    Path(name, 'check.py').write_text(source)
""".replace("FAULT", repr(fault)))
    native.write_text(source)
    return run, provider


def journey(slot, fault):
    run, provider = prepared(slot, fault)
    answered, steered = False, False

    def user(item):
        nonlocal steered
        if item["gate"] == "CHECK-IN":
            if not answered:
                raise Waiting()
            return approve(item) if fault else "Go on and do not stop again"
        if item["gate"] == "C" and not steered:
            steered = True
            return {"request": item["request"], "subject": item["subject"], "choice": "revise"}
        return approve(item)

    run.user = user
    try:
        with pytest.raises(Waiting):
            while True:
                run.step()
        cursor = run.store.cursor()
        assert cursor.wave == 2 and cursor.state == "EXECUTE"
        document = run.store.plan(cursor.revision)
        owner, unchanged, added = (unit_ref(run, n) for n in (1, 2, 3))
        old = run.store.units()
        assert old[owner].state == old[unchanged].state == "DONE"
        assert added not in old and old[owner].wave == 1
        assert any(r.startswith("2.") for r in plan.select(document, "unit")[owner]["requirements"])
        if fault:
            assert "Preserve the existing saved values." in plan.select(document, "unit")[owner]["instructions"]
            subject = run.store.read(cursor.B)["subject"]
            assert subject["vision"] == cursor.vision
            assert "Preserve the existing saved values." in plan.select(subject["plan"], "unit")[owner]["instructions"]
        assert cursor.waveStart == cursor.candidate
        request = run.store.read(cursor.decision)
        assert request["gate"] == "CHECK-IN" and request["subject"]["wave"] == 1
        assert request["subject"]["commit"] == cursor.candidate
        assert request["subject"]["built"][owner]["summary"]
        assert request["subject"]["next wave goal"] == "Extend saved values"
        assert not (slot / "turn-unit-003").exists()
        answered = True
        run.step()  # Consume the real check-in answer before any worker starts.
        assert run.store.cursor().waived == (not fault) and run.store.cursor().decision is None
        if fault:
            run.step()
        else:
            (slot / "interrupt").write_text("Kill after the new unit's native reply")
            run.close()
            script = """
import sys
from pathlib import Path
from wave_loop_journey import Waves
from seldon.run import Run
from test_journey import approve
slot = Path(sys.argv[1])
run = Run.reopen(slot / 'product', Waves(slot, 'normal', 3), approve)
run.step()
"""
            result = subprocess.run([sys.executable, "-B", "-c", script, str(slot)],
                                    capture_output=True, text=True, timeout=90)
            assert result.returncode == 71, result.stdout + result.stderr
            run = Run.reopen(slot / "product", provider, user)
            assert run.store.unit(unchanged) == old[unchanged]
            assert run.store.unit(added).state == "WORKING"
            run.step()
        assert run.store.cursor().state == "WAVE-VERIFY"
        assert run.store.unit(unchanged) == old[unchanged]
        assert run.store.unit(owner).wave == run.store.unit(added).wave == 2
        launches = [json.loads(s) for s in (slot / "launch-unit-001.jsonl").read_text().splitlines()]
        assert launches[-1]["thread"] != launches[0]["thread"] and not launches[-1]["resumed"]
        if fault:
            run.step()
            assignment, = [b for _, _, b in run.store.records("unit_fixup")]
            assert assignment["owner"] == unchanged
            assert "reader cannot read the new format" in str(assignment["gaps"])
            assert run.store.unit(unchanged).state == "REWORK"
        finish(run)
        assert (run.project / "unit-003/value.txt").read_text() == "value-003"
        assert (run.project / "unit-001/extension-2.txt").read_text() == "extended"
        if fault:
            assert steered and run.store.cursor().wave == 3
            assert (run.project / "unit-001/extension-3.txt").read_text() == "extended"
            from seldon import fixups

            assert fixups.active(run.store, unchanged) == assignment
            assert run.store.unit(unchanged).wave == 2
        else:
            assert steered and run.store.cursor().wave == 3
            assert (run.project / "unit-001/extension-3.txt").read_text() == "extended"
            assert run.store.unit(unchanged) == old[unchanged]
        requests = {r for r, _, b in run.store.records("decision_request") if b["gate"] == "CHECK-IN"}
        approvals = {b["request"] for _, _, b in run.store.records("approval")}
        assert requests <= approvals
        if not fault:
            assert any(run.store.read(b["words"])["words"] == "waived by the user"
                       for _, _, b in run.store.records("approval"))
        gitwork.unchanged(run.project, run.store.cursor().candidate)
    finally:
        run.close()
