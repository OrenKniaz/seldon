"""R7/R8/R9/C18/C23 journeys: real worker processes, Git integration and durable reopen."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import pytest

from seldon import fixups, plan, proof
from seldon.frontdoor import state as run_state
from seldon.run import Run
from test_journey import Scripted, approve, copy_final, finish, prepare


def blocked_owner_journeys(root):
    """A real blocked WORK must reach its sibling owner; final-judge gaps never exercise WORK routing.

    The own-scope twin catches wrongly waiving local failures, which the sibling success cannot expose.
    The missing-base twin catches reopening a DONE sibling for absent integration; guide repair
    always starts with that sibling's files and cannot expose this false blocker.
    """
    for mode in ("sibling", "own", "repeated"):
        own_scope = mode == "own"
        slot = root / mode
        run, provider = prepare(slot, "final-seed", 2)

        def scopes(answer):
            # The discovered check belongs to u001 through checkPaths, outside its explicit scope.
            answer["units"][0]["scope"] = ["unit-001/value.txt", "unit-001/interaction.txt"]

        provider.plan_fault = scopes
        native = slot / "native.py"
        owner = "guide" if own_scope else "u001"
        correction = "Require a ready interaction in the value check and make the interaction ready."
        blocked = ("if guide and " + ("True" if mode != "sibling" else
                   "Path('unit-001/interaction.txt').read_text() != 'ready'") + ":\n"
                   "    answer['answer'].update(outcome='blocked', discoveries=["
                   + repr({"owner": owner, "correction": correction}) + "])\n")
        native.write_text(native.read_text("utf-8").replace(
            'print(json.dumps({"type":"item.completed"', blocked
            + 'print(json.dumps({"type":"item.completed"'), encoding="utf-8")
        try:
            if mode != "sibling":
                cursor = run.run()
                assert cursor.decision and run.store.unit("guide").state == "ASKED"
                assert run.store.unit("guide").recoveries == 2
                assert len(run.store.records("unit_fixup")) == (0 if own_scope else 3)
            else:
                finish(run)
                assert run.store.unit("guide").recoveries == 0
                assert not [b for _, _, b in run.store.records("unit_failure") if b["unit"] == "guide"]
                assignments = [b for _, _, b in run.store.records("unit_fixup")]
                assert [a["owner"] for a in assignments] == ["u001"]
                assignment, = assignments
                assert assignment["scope"] == [
                    "unit-001/value.txt", "unit-001/interaction.txt", "unit-001/check.py"]
                assert assignment["gaps"][0]["correction"] == correction
                assert run.store.read(assignment["gaps"][0]["source"])["answer"]["outcome"] == "blocked"
                owner = run.store.unit("u001")
                assert run.store.unit("guide").base == owner.integrated
                assert (run.project / "unit-001/interaction.txt").read_text() == "ready"
                assert (slot / "turn-docs").read_text() == "2"
            assert run.store.unit("u002").state == "DONE"
            assert run.store.unit("u002").recoveries == 0
            assert (slot / "turn-unit-002").read_text() == "1"
        finally:
            run.close()

    slot = root / "missing-base"
    run, provider = prepare(slot, "base-only", 2)

    def wave(answer):
        answer["units"][1]["checkPaths"] = ["unit-002/*.py"]
        answer["units"][0]["provides"] = ["new:page"]
        answer["units"][1]["consumes"] = ["new:page"]
        answer["edits"].extend([
            {"kind": "contract", "targetHandle": "new:page", "typedBody": {
                "kind": "ui-structure", "text": "The page exposes a named content slot.",
                "provider": 1, "consumers": [2]}},
            {"kind": "check", "targetHandle": "new:wave", "typedBody": {
                "purpose": "Read the integrated page and consumer value", "level": "wave",
                "mode": "establishes", "command": [sys.executable, "-S", "-B", "unit-002/wave.py"],
                "requiredEnvironment": ["Python"], "expectedEvidence": "Page and consumer compose",
                "requirements": ["new:r001", "new:r002"]}}])

    provider.plan_fault = wave
    native = slot / "native.py"
    source = ("from pathlib import Path\n"
              "assert Path('unit-001/page.txt').read_text() == 'content slot'\n"
              "assert Path('unit-002/value.txt').read_text() == 'value-002'\n"
              "print('integrated page and consumer')\n")
    injection = ("if name == 'unit-001':\n"
                 "    Path(name, 'page.txt').write_text('content slot')\n"
                 "if name == 'unit-002':\n"
                 "    assert not Path('unit-001/page.txt').exists()\n"
                 f"    Path(name, 'wave.py').write_text({source!r})\n"
                 "    if turn == 1:\n"
                 "        answer['answer'].update(outcome='blocked', discoveries=[{\n"
                 "            'owner': 'u001', 'correction': 'lacks integrated page'}])\n")
    native.write_text(native.read_text("utf-8").replace(
        'print(json.dumps({"type":"item.completed"', injection
        + 'print(json.dumps({"type":"item.completed"'), encoding="utf-8")
    invoke = provider.invoke
    consumer_started = Event()

    def coordinated(question, started):
        if question["kind"] == "WORK":
            scope = question["facts"]["Write scope"]
            if scope == ["unit-001/**"]:
                assert consumer_started.wait(20), "Consumer must start before page integration"
            elif scope == ["unit-002/**"]:
                consumer_started.set()
                deadline = time.monotonic() + 20
                while run.store.unit("u001").state != "DONE" and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert run.store.unit("u001").state == "DONE", "Page owner must be DONE at handoff"
        return invoke(question, started)

    provider.invoke = coordinated
    try:
        cursor, rows, _ = finish(run)
        assert not run.store.records("unit_fixup")
        transitions = [b for _, k, b in rows if k == "transition" and b["unit"] == "u001"]
        assert not any(b["input"] == "DONE" for b in transitions)
        failure, = [b["failure"] for _, k, b in rows if k == "unit_failure" and b["unit"] == "u002"]
        assert failure == ("u001's files are not in this unit's base by design; "
                           "write the wave check, run unit checks, return done")
        wave_ref, = plan.assigned(run.store.plan(cursor.revision))[None]
        assert any(b["check"] == wave_ref and b["purpose"] == "final"
                   and b["commit"] == cursor.candidate and b["exitCode"] == 0
                   and "integrated page and consumer" in b["stdout"]
                   for _, k, b in rows if k == "observation")
        questions = [b for _, k, b in rows if k == "question" and b["kind"] == "WORK"
                     and b["facts"]["Write scope"] == ["unit-002/**"]]
        assert len(questions) == 2
        assert all("Wave checks to write, not run here" in q["facts"] for q in questions)
    finally:
        run.close()


@pytest.mark.fast
def test_blocked_owner_journeys(tmp_path):
    """Exercise WORK handoffs through C, including an owner absent from the consumer's base."""
    blocked_owner_journeys(tmp_path / "blocked-owner")


def owner_repair_journeys(root):
    """A real three-unit wave failure reopens its file owner, even after two earlier recoveries.

    The passing twin catches repair on coverage alone. The failing twin strengthens a local check
    and must prove it on P, then rerun the wave check at integration; local-only gaps miss x09.
    """
    for broken in (False, True):
        slot = root / ("broken" if broken else "passing")
        run, provider = prepare(slot, "owner-repair", 3)

        def wave(answer):
            answer["units"][0]["checkPaths"] = ["unit-001/*.py"]
            answer["edits"].append({"kind": "check", "targetHandle": "new:wave", "typedBody": {
                "purpose": "Compose three values and request the first interaction", "level": "wave",
                "mode": "establishes", "command": [sys.executable, "-S", "-B", "unit-001/wave.py"],
                "requiredEnvironment": ["Python"], "expectedEvidence": "Three values and ready interaction",
                "requirements": ["new:r001", "new:r002", "new:r003"]}})

        provider.plan_fault = wave
        native = slot / "native.py"
        injection = ("if not guide and name == 'unit-001':\n"
                     f"    ready = turn > 3 or {not broken!r}\n"
                     "    Path(name, 'interaction.txt').write_text('ready' if ready else 'blocked')\n"
                     "    Path(name, 'wave.py').write_text(\"from pathlib import Path\\n\"\n"
                     "        + \"assert all(Path(f'unit-{i:03}/value.txt').exists() for i in (1,2,3))\\n\"\n"
                     "        + \"assert Path('unit-001/interaction.txt').read_text() == 'ready'\\n\"\n"
                     "        + \"print('three values and ready interaction')\\n\")\n"
                     "    if turn > 3:\n"
                     "        with Path(name, 'check.py').open('a') as check:\n"
                     "            check.write(\"assert Path('unit-001/interaction.txt').read_text() == 'ready'\\n\")\n")
        native.write_text(native.read_text("utf-8").replace(
            'answer = {"answer":{"outcome":"done"', injection + 'answer = {"answer":{"outcome":"done"'),
            encoding="utf-8")
        invoke = provider.invoke
        rejected = 0

        def judged(question, started):
            nonlocal rejected
            raw, thread = invoke(question, started)
            if (broken and question["kind"] == "JUDGE"
                    and "unit-001/check.py" in question["facts"]["Check sources"] and rejected < 2):
                reply = json.loads(raw)
                reply["answer"].update(verdict="gap", gaps=[{
                    "target": question["targets"][0], "correction": "Clarify the local interaction evidence"}])
                rejected += 1
                return json.dumps(reply), thread
            return raw, thread

        provider.invoke = judged
        try:
            while run.store.cursor().state != "WAVE-VERIFY":
                assert not run.store.cursor().decision, run.store.records("unit_failure")
                run.step()
            before = run.store.units()
            assert before["u001"].recoveries == (2 if broken else 0)
            wave_ref, = plan.assigned(run.store.plan(run.store.cursor().revision))[None]
            run.step()
            if broken:
                assert run.store.unit("u001").state == "REWORK"
                assert run.store.unit("u001").recoveries == 0
                for ref in ("u002", "u003", "guide"):
                    assert run.store.unit(ref) == before[ref]
                assignment, = [b for _, _, b in run.store.records("unit_fixup")]
                assert assignment["owner"] == "u001"
                source = run.store.read(assignment["gaps"][0]["source"])
                assert source["check"] == wave_ref and source["exitCode"] != 0
            else:
                assert not run.store.records("unit_fixup")
            _, rows, after = finish(run)
            assert after["u001"].recoveries == 0
            for ref in ("u002", "u003", "guide"):
                assert after[ref] == before[ref]
            if broken:
                patch = after["u001"].P
                repaired = [b for _, k, b in rows if k == "observation" and b["commit"] == patch]
                assert any(b["purpose"] == "P" and b["exitCode"] == 0 for b in repaired)
                assert not any(b["purpose"] in ("T", "base") for b in repaired)
                assert any(b["check"] == wave_ref and b["purpose"] == "integrated"
                           and b["exitCode"] == 0 for _, k, b in rows if k == "observation")
                assert (slot / "turn-unit-001").read_text() == "4"
        finally:
            run.close()


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    slot = tmp_path_factory.mktemp("fixup-seed")
    run, provider = prepare(slot / "delivery", "final-seed", 3)
    slot = slot / "delivery"

    def shared_check(answer):
        answer["units"][0]["checkPaths"] = ["unit-001/*.py"]
        answer["edits"].append({"kind": "check", "targetHandle": "new:interaction", "typedBody": {
            "purpose": "A user can request either interaction or compose both",
            "mode": "preserves", "level": "wave", "command": [sys.executable, "-S", "-B",
                "unit-001/interactions.py", str(slot / "request")],
            "requiredEnvironment": ["Python"], "expectedEvidence": "Requested interactions are ready",
            "requirements": ["new:r001", "new:r002"]}})

    provider.plan_fault = shared_check
    native = slot / "native.py"
    text = native.read_text("utf-8").replace("and number == 1:", "and number in (1, 2):")
    # The shared check owns this regression assertion; local persistence checks remain preservation checks.
    text = text.replace('        if turn > 1:\n            source += f"assert Path('
                        "'{name}/interaction.txt')"
                        '.read_text() == \'ready\'\\n"\n', "")
    native.write_text(text, encoding="utf-8")
    source = ("from pathlib import Path\nimport sys\nrequest=Path(sys.argv[1])\n"
              "action=request.read_text() if request.exists() else 'values'\n"
              "owners=(1,2) if action=='compose' else (1,) if action=='one' else ()\n"
              "assert all(Path(f'unit-{i:03}/interaction.txt').read_text()=='ready' for i in owners)\n"
              "print('requested interactions ready')\n")
    text = text.replace('answer = {"answer":{"outcome":"done"',
                        "if not guide and name == 'unit-001':\n"
                        f"    Path(name, 'interactions.py').write_text({source!r})\n"
                        + 'answer = {"answer":{"outcome":"done"')
    native.write_text(text, encoding="utf-8")
    try:
        while run.store.cursor().state != "PRODUCT-JUDGE":
            assert not run.store.cursor().decision, run.store.records("unit_failure")
            run.step()
    finally:
        run.close()
    return slot


def gap(run, action, kind, owners):
    """The canonical check observes a user's newly exercised failing interaction on the candidate."""
    store = run.store
    (run.project.parent / "request").write_text(action)
    document = store.plan(store.cursor().revision)
    ref, check = next((r, c) for r, c in plan.select(document, "check").items()
                      if c["purpose"].startswith("A user can request"))
    observations = proof.observe(store, run.project, store.cursor().candidate, {ref: check}, "wave")
    assert proof.failures(store, observations), "The requested interaction must really fail before repair"
    source = store.record("check_gap" if kind == "check" else "flow_gap", {
        "commit": store.cursor().candidate, "observations": observations})
    return {"subject": ref if kind == "check" else "compose-interactions", "kind": kind,
            "commit": store.cursor().candidate, "units": owners,
            "correction": "Make the requested interaction ready inside your own write scope.",
            "source": source}


@pytest.mark.fast
def test_check_gap_preserves_other_units_and_verdicts_to_c(seed, tmp_path):
    """Catch global rework/verdict resets; the old golden faults do not accept the M2 Gap contract."""
    run, _ = copy_final(seed, tmp_path / "check", "final-seed")
    try:
        before = run.store.units()
        verdicts = run.store.cursor().requirementVerdicts
        flows = run.store.cursor().demoVerdicts
        correction = gap(run, "one", "check", ["u001"])
        fixups.apply(run.store, [correction])
        assert run.store.cursor().requirementVerdicts == verdicts
        assert run.store.cursor().demoVerdicts == flows
        assert run.store.unit("u002") == before["u002"]
        assert run.store.unit("guide") == before["guide"]
        _, _, after = finish(run)
        assert after["u002"] == before["u002"] and after["guide"] == before["guide"]
        unaffected = [r for r in verdicts if "u001" not in run.store.read(r)["scopeTrees"]]
        assert unaffected
        for ref in unaffected:
            old = run.store.read(ref)
            current, = [run.store.read(r) for r in run.store.cursor().requirementVerdicts
                        if run.store.read(r)["subject"] == old["subject"]]
            assert current["answer"] == old["answer"] and current["scopeTrees"] == old["scopeTrees"]
            assert sum(q["kind"] == "FLOW" and q["targets"] == [old["subject"]]
                       for _, _, q in run.store.records("question")) == 1
        launches = [json.loads(s) for s in
                    (run.project.parent / "launch-unit-001.jsonl").read_text().splitlines()]
        assert len(launches) == 2 and launches[1]["resumed"]
        assert launches[1]["thread"] == before["u001"].thread
        assert launches[1]["facts"]["Scoped corrections"]["corrections"] == [correction["correction"]]
    finally:
        run.close()


@pytest.mark.fast
def test_flow_gap_repairs_two_scopes_without_replanning(seed, tmp_path):
    """Catch single-owner routing or a planner call; retained final-gap faults reach only one owner."""
    run, _ = copy_final(seed, tmp_path / "flow", "final-seed")
    try:
        before = run.store.units()
        planning = [(r, k) for r, k, b in run.store.records("question")
                    if b["kind"] in ("ARCHITECT", "DETAIL", "REVIEW")]
        revision = run.store.cursor().revision
        fixups.apply(run.store, [gap(run, "compose", "flow", ["u001", "u002"])])
        assert run.store.unit("guide") == before["guide"]
        finish(run)
        assert run.store.cursor().revision == revision
        assert planning == [(r, k) for r, k, b in run.store.records("question")
                            if b["kind"] in ("ARCHITECT", "DETAIL", "REVIEW")]
        for ref in ("u001", "u002"):
            directory = "unit-" + ref[1:]
            assert (run.project / directory / "interaction.txt").read_text() == "ready"
            launches = [json.loads(s) for s in (run.project.parent / f"launch-{directory}.jsonl")
                        .read_text().splitlines()]
            assert len(launches) == 2 and launches[-1]["thread"] == before[ref].thread
            assert launches[-1]["facts"]["Scoped corrections"]["writeScope"] == [directory + "/**"]
        assert run.store.unit("guide") == before["guide"]
    finally:
        run.close()


@pytest.mark.fast
def test_fixup_exhaustion_blocks_done_dependants(seed, tmp_path):
    """Catch a DONE consumer surviving exhausted rework; ordinary exhaustion covers only waiting consumers."""
    run, _ = copy_final(seed, tmp_path / "exhaust", "final-seed")
    try:
        before = run.store.unit("u002")
        native = run.project.parent / "native.py"
        native.write_text(native.read_text("utf-8").replace(
            'answer = {"answer":{"outcome":"done"',
            'answer = {"answer":{"outcome":"blocked" if name == "unit-001" and turn > 1 else "done"'),
            encoding="utf-8")
        fixups.apply(run.store, [gap(run, "one", "check", ["u001"])])
        cursor = run.run()
        assert cursor.decision and run.store.unit("u001").state == "ASKED"
        assert run.store.unit("u002") == before and not run.store.records("outcome")
        assert "guide" in run_state(str(run.project))["waitingFor"]["subject"]["affects"]
    finally:
        run.close()


@pytest.mark.fast
def test_kill_during_fixup_reopens_without_redoing_done_units(seed, tmp_path):
    """Catch reusing an old WORK receipt or losing fixup admission; retained crashes precede final fixups."""
    slot = tmp_path / "kill"
    run, _ = copy_final(seed, slot, "final-seed")
    try:
        before = run.store.units()
        fixups.apply(run.store, [gap(run, "one", "check", ["u001"])])
    finally:
        run.close()
    child = subprocess.run([sys.executable, "-B", "-m", "test_fixups", "--kill", str(slot)],
                           cwd=Path(__file__).resolve().parents[2], capture_output=True, timeout=40)
    assert child.returncode == 77, child.stdout.decode() + child.stderr.decode()
    run = Run.reopen(slot / "product", Scripted(slot, "final-seed", 2), approve)
    try:
        finish(run)
        assert run.store.unit("u002") == before["u002"]
        assert run.store.unit("guide") == before["guide"]
        assert (slot / "turn-unit-001").read_text() == "2"
        assert (slot / "turn-unit-002").read_text() == "1"
    finally:
        run.close()


if __name__ == "__main__" and sys.argv[1:2] == ["--kill"]:
    slot = Path(sys.argv[2])
    provider = Scripted(slot, "final-seed", 2)
    invoke = provider.invoke

    def interrupted(question, started):
        result = invoke(question, started)
        if question["kind"] == "WORK":
            os._exit(77)  # Native worker has finished; its receipt is durable, the controller reply is not.
        return result

    provider.invoke = interrupted
    Run.reopen(slot / "product", provider, approve).run()
