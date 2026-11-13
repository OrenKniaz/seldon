"""R7/R8/R9/C18/C23 journeys: real worker processes, Git integration and durable reopen."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from seldon import fixups, plan, proof
from seldon.run import Run
from test_journey import Scripted, approve, copy_final, finish, prepare


def blocked_owner_journeys(root):
    """A real blocked WORK must reach its sibling owner; final-judge gaps never exercise WORK routing.

    The own-scope twin catches wrongly waiving local failures, which the sibling success cannot expose.
    """
    for own_scope in (False, True):
        slot = root / ("own" if own_scope else "sibling")
        run, provider = prepare(slot, "final-seed", 2)

        def scopes(answer):
            # The discovered check belongs to u001 through checkPaths, outside its explicit scope.
            answer["units"][0]["scope"] = ["unit-001/value.txt", "unit-001/interaction.txt"]

        provider.plan_fault = scopes
        native = slot / "native.py"
        target = "docs/try-it.md" if own_scope else "unit-001/check.py"
        correction = "Require a ready interaction in the value check and make the interaction ready."
        blocked = ("if guide and " + ("True" if own_scope else
                   "Path('unit-001/interaction.txt').read_text() != 'ready'") + ":\n"
                   "    answer['answer'].update(outcome='blocked', discoveries=["
                   + repr({"target": target, "correction": correction}) + "])\n")
        native.write_text(native.read_text("utf-8").replace(
            'print(json.dumps({"type":"item.completed"', blocked
            + 'print(json.dumps({"type":"item.completed"'), encoding="utf-8")
        try:
            if own_scope:
                cursor = run.run()
                assert cursor.state == "INCOMPLETE"
                assert run.store.unit("guide").state == "FAILED"
                assert run.store.unit("guide").recoveries == 2
                assert not run.store.records("unit_fixup")
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


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    slot = tmp_path_factory.mktemp("fixup-seed")
    run, provider = prepare(slot / "delivery", "final-seed", 3)
    slot = slot / "delivery"

    def shared_check(answer):
        answer["edits"].append({"kind": "check", "targetHandle": "new:interaction", "typedBody": {
            "purpose": "A user can request either interaction or compose both",
            "mode": "preserves", "command": [sys.executable, "-S", "-B", "-c",
                "from pathlib import Path; "
                f"request=Path({(slot / 'request').as_posix()!r}); "
                "action=request.read_text() if request.exists() else 'values'; "
                "owners=(1,2) if action=='compose' else (1,) if action=='one' else (); "
                "assert all(Path(f'unit-{i:03}/interaction.txt').read_text()=='ready' for i in owners); "
                "print('requested interactions ready')"],
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
    try:
        while run.store.cursor().state != "PRODUCT-JUDGE":
            assert run.store.cursor().state != "INCOMPLETE", run.store.records("outcome")
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
        assert cursor.state == "INCOMPLETE"
        assert run.store.unit("u001").state == "FAILED"
        assert run.store.unit("guide").state == "BLOCKED"
        assert run.store.unit("u002") == before
        outcome = run.store.records("outcome")[-1][2]
        assert "u001" in outcome["failed"] and "guide" in outcome["blocked"]
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
