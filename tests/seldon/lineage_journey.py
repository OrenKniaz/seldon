"""Forever-fault twins at canonical planning, worker and final-proof boundaries."""

import json
from copy import deepcopy

from seldon.decisions import Waiting
from seldon.frontdoor import state
from seldon.run import Run
from test_journey import approve, prepare


def waiting(run):
    run.run()
    shown = state(str(run.project))
    assert shown["status"] == "WAITING" and not run.store.records("outcome"), shown
    return shown["waitingFor"]


def reopen(run, provider, user=approve):
    project = run.project
    run.close()
    return Run.reopen(project, provider, user)


def recurrence(run, provider, first):
    """One answer allows one continuation, and reopening never replays the accepted words."""
    answered = []

    def user(item):
        if item["gate"] in ("A", "B", "CHECK-IN", "C"):
            return approve(item)
        if answered:
            raise Waiting()
        answered.append(item["request"])
        return "again, keep the accepted promise"

    run = reopen(run, provider, user)
    class Interrupted(BaseException):
        pass
    step = run.step
    def interrupt():
        result = step()
        if answered and run.store.cursor().decision is None:
            raise Interrupted()
        return result
    run.step = interrupt
    try:
        waiting(run)
    except Interrupted:
        assert run.store.cursor().decision is None
        run = reopen(run, provider, user)
    assert answered
    second = waiting(run)
    assert second["request"] != first["request"]
    subject = second["subject"]
    assert subject["recurrence"] == "This is the same unresolved issue after your previous answer"
    answer = run.store.read(subject["previousAnswer"])
    assert answer["request"] == first["request"] and "again" in answer["words"]
    assert len(subject["attempts"]) > len(first["subject"]["attempts"])
    assert subject["lineage"] == first["subject"]["lineage"]
    assert subject["recommendation"]
    run = reopen(run, provider)
    before = run.store.records()
    assert waiting(run) == second and run.store.records() == before
    return run


def unit_lineage(slot):
    """Infinite own failures ask after two rounds and one answered retry; finite repair misses resets."""
    run, provider = prepare(slot, "ask", 4)
    invoke = provider.invoke
    def answer(q, started):
        raw, session = invoke(q, started)
        if q["kind"] == "JUDGE" and q["targets"][0] == "n002":
            body = json.loads(raw)
            body["answer"].update(verdict="gap", gaps=[{
                "target": "n002", "correction": "The historical baseline cannot be demonstrated"}])
            return json.dumps(body), session
        return raw, session
    provider.invoke = answer
    try:
        first = waiting(run)
        assert len(first["subject"]["tried"]) == 2
        assert all(run.store.unit(r).state == "DONE" for r in ("u002", "u003", "u004"))
        run = recurrence(run, provider, first)
        assert (slot / "turn-unit-001").read_text() == "3"
    finally:
        run.close()


def final_lineage(slot, kind):
    """Local successes cannot erase a final cause, including alternating owners and guide failures.

    Existing final-rework twins repair once; they cannot catch unlimited new lives at DONE.
    """
    run, provider = prepare(slot, "ask", 2)
    invoke = provider.invoke
    rounds = []
    if kind == "transfer":
        def shared(body):
            body["edits"][4]["typedBody"]["features"] = ["new:f001"]
            del body["edits"][3]
        provider.plan_fault = shared
    native = slot / "native.py"
    injection = ("if not guide:\n"
                 "    Path(name, 'revision.txt').write_text(str(turn))\n"
                 "else:\n"
                 "    with Path('docs/try-it.md').open('a') as guide_file:\n"
                 "        guide_file.write('\\nRevision ' + str(turn))\n")
    native.write_text(native.read_text("utf-8").replace('print(json.dumps({"type":"item.completed"',
        injection + 'print(json.dumps({"type":"item.completed"'), encoding="utf-8")

    def answer(q, started):
        raw, session = invoke(q, started)
        selected = (q["kind"] == "FLOW" and "n002" in q["targets"] if kind == "transfer" else
                    q["kind"] == "PRODUCT" and q["targets"] == [
                        "tryItGuide" if kind == "guide" else "quality:001"])
        if selected:
            body = json.loads(raw)
            rounds.append(q)
            gap = {"target": q["targets"][0], "correction": "The accepted final interaction still fails"}
            if kind == "transfer":
                gap["owners"] = ["u001" if len(rounds) % 2 else "u002"]
            body["answer"].update(verdict="gap", gaps=[gap])
            return json.dumps(body), session
        return raw, session

    provider.invoke = answer
    try:
        first = waiting(run)
        assert len(rounds) == 2
        expected_owner = "u002" if kind == "transfer" else "guide" if kind == "guide" else "u001"
        assert first["subject"]["unit"] == expected_owner
        run = recurrence(run, provider, first)
        assert len(rounds) == 3
    finally:
        run.close()


def contract_lineage(slot, pending, oscillate):
    """An unchanged/oscillating CONTRACT cannot renew DONE or unstarted owners forever.

    The useful single-correction twin misses WORKING->WORKING loops and revision-based escapes.
    """
    run, provider = prepare(slot, "ask", 2)
    run.capacity = 1
    original = "The provider exposes the accepted value"

    def contracts(body):
        body["edits"].append({"kind": "contract", "targetHandle": "new:agreement", "typedBody": {
            "kind": "api", "text": original, "provider": 1, "consumers": [2]}})
        body["units"][0]["provides"] = ["new:agreement"]
        body["units"][1]["consumes"] = ["new:agreement"]
        body["units"][0 if pending else 1]["dependencies"] = [2 if pending else 1]
        body["units"][0 if pending else 1]["reads"] = [
            "unit-002/value.txt" if pending else "unit-001/value.txt"]

    provider.plan_fault = contracts
    native = slot / "native.py"
    injection = ("if name == 'unit-002':\n"
                 "    answer['answer'].update(outcome='blocked', discoveries=[{\n"
                 "        'owner': next(iter(facts['Contracts consumed'])),\n"
                 "        'correction': 'The accepted value is still unavailable'}])\n")
    native.write_text(native.read_text("utf-8").replace('print(json.dumps({"type":"item.completed"',
        injection + 'print(json.dumps({"type":"item.completed"'), encoding="utf-8")
    invoke = provider.invoke
    corrections = []

    def answer(q, started):
        if q["kind"] == "CONTRACT":
            corrections.append(q)
            ref = next(iter(q["facts"]["Contracts consumed"]))
            text = original + (" differently" if oscillate and len(corrections) % 2 else "")
            return json.dumps({"answer": {"verdict": "corrected",
                "changes": [{"contract": ref, "text": text}],
                "decision": "Preserve the accepted value", "why": "Another agreement correction"}}), None
        return invoke(q, started)

    provider.invoke = answer
    try:
        first = waiting(run)
        assert "accepted value" in str(first["subject"])
        assert not run.store.cursor().finalCommit
        run = recurrence(run, provider, first)
    finally:
        run.close()


def planning_lineage(slot, split):
    """New names and a subdivision remain repairable; only the same unresolved outline asks again."""
    run, provider = prepare(slot, "ask", 2)
    invoke = provider.invoke
    turns = []
    names = iter(("first", "second"))
    subdivisions = iter((True,))

    def answer(q, started):
        if q["kind"] == "DETAIL":
            turns.append(q)
            if split and next(subdivisions, False):
                area = q["facts"]["Area"]
                children = [{**deepcopy(area), "handle": area["handle"] + suffix,
                             "commitments": list(q["facts"]["Accepted commitments"]),
                             "scope": [s.removesuffix("**") + suffix + "/**" for s in area["scope"]]}
                            for suffix in ("a", "b")]
                return json.dumps({"answer": {"verdict": "subdivide", "subareas": children,
                    "contracts": [], "why": "The same commitments need more subdivision"}}), None
            if split:
                return json.dumps({"answer": {"verdict": "contract_gap",
                    "need": "The accepted values still need an agreed format",
                    "why": "Unresolved agreement"}}), None
            raw, session = invoke(q, started)
            body = json.loads(raw)
            path = f"unassigned-{next(names, 'unresolved')}/value.txt"
            body["answer"]["units"][0]["reads"] = [path]
            body["answer"]["units"][0]["dependencies"] = [path]
            return json.dumps(body), session
        return invoke(q, started)

    provider.invoke = answer
    try:
        first = waiting(run)
        assert first["gate"] == "DETAIL" and not run.store.cursor().B
        assert first["subject"]["tried"]
        run = recurrence(run, provider, first)
    finally:
        run.close()


def planning_direction(slot, architecture, monkeypatch, capsys):
    """Owner direction reaches the repairing planner before another question, then B.

    Forever-fault twins cannot catch a useful answer being discarded before a real repair try.
    REVIEW retains the missing promise alongside the words; ARCH-REVIEW routes back to ARCHITECT.
    """
    run, provider = prepare(slot, "ask", 1)
    invoke = provider.invoke
    calls, verdicts, requests, repaired = [], [], [], []
    words = "Revise the plan: keep the exact stored value and make its read visible"
    gap = "The accepted stored value needs a visible read"

    def answer(q, started):
        calls.append(q)
        if q["kind"] == ("ARCHITECT" if architecture else "DETAIL") and words in json.dumps(q["facts"]):
            repaired.append(q)
        if q["kind"] == "REVIEW" and (q["facts"]["Review subject"] == "architecture") == architecture:
            verdicts.append(q)
            if not repaired:
                correction = gap if len(verdicts) != 2 else gap + " after restart"
                return json.dumps({"answer": {"verdict": "gap", "gaps": [{
                    "target": "architecture: missing promise" if architecture else "n002",
                    "correction": correction}], "why": "An accepted promise is missing"}}), None
        return invoke(q, started)

    def user(item):
        if item["gate"] in ("A", "B", "CHECK-IN", "C"):
            return approve(item)
        requests.append((item, len(calls)))
        assert len(requests) == 1, "Owner was asked again before their direction was tried"
        return words

    provider.invoke, run.user = answer, user
    try:
        while run.store.cursor().state != "B":
            try:
                run.step()
            except Waiting:
                pass
        assert requests and not run.store.units()
        following = [q for q in calls[requests[0][1]:] if q["kind"] != "DECIDE"]
        assert following[0]["kind"] == ("ARCHITECT" if architecture else "DETAIL")
        assert words in json.dumps(following[0]["facts"])
        assert gap in json.dumps(following[0]["facts"])
        if not architecture:
            assert len(verdicts) == 4  # Three gap verdicts followed by the repaired plan.
        assert len([b for _, _, b in run.store.records("decision_request")
                    if "step" in b["subject"]]) == 1
        if not architecture:
            import real_hello
            (slot / "setup.json").write_text(json.dumps(provider.setup), encoding="utf-8")
            run.close()
            monkeypatch.setattr(real_hello, "Codex", lambda *_: provider)
            monkeypatch.setenv("SELDON_STOP_AT_B", "1")
            monkeypatch.delenv("SELDON_SIM_USER", raising=False)
            assert real_hello.run(slot, False, reopen=True) == 0
            output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
            summary = next(row["planSummary"] for row in output if "planSummary" in row)
            assert summary["units"] and summary["checkCount"]
            assert summary["reviewRounds"] == 3 and len(summary["ownerQuestions"]) == 1
            assert output[-1]["state"] == "B" and not output[-1]["ownerAccepted"]
            run = Run.reopen(slot / "product", provider, approve)
            assert run.store.cursor().state == "B" and not run.store.units()
            assert not any(q["kind"] == "WORK" for q in calls)
    finally:
        run.close()


def executor_lineage(slot, guide):
    """A real command fails only at final observation, even when local/integrated checks pass.

    Semantic-gap twins miss check-owner routing and an external guide executor's repeated failure.
    """
    run, provider = prepare(slot, "ask", 2)
    marker = slot / "executor-down"
    native = slot / "native.py"
    source = ("from pathlib import Path\n"
              f"assert not Path({str(marker)!r}).exists(), 'external executor unavailable'\n"
              "assert Path('unit-001/value.txt').read_text() == 'value-001'\n"
              "print('observed value-001')\n")
    import sys
    walkthrough = source if guide else (
        "from pathlib import Path; print('observed', Path('unit-001/value.txt').read_text())")
    command = json.dumps([sys.executable, "-S", "-B", "-c", walkthrough])
    document = f'Try it yourself [value](../unit-001/value.txt)\n```argv\n{command}\n```\n'
    injection = ("if guide:\n"
                 f"    Path('docs/try-it.md').write_text({document!r})\n")
    if not guide:
        injection += ("if name == 'unit-001':\n"
                      f"    with Path(name, 'check.py').open('a') as check: check.write({source!r})\n")
    native.write_text(native.read_text("utf-8").replace('print(json.dumps({"type":"item.completed"',
        injection + 'print(json.dumps({"type":"item.completed"'), encoding="utf-8")
    step = run.step
    def fault():
        if run.store.cursor().state == "WAVE-VERIFY":
            marker.write_text("offline")
        else:
            marker.unlink(missing_ok=True)
        return step()
    run.step = fault
    try:
        first = waiting(run)
        assert "external executor unavailable" in str(first["subject"])
        assert first["subject"]["unit"] == ("guide" if guide else "u001")
        # Reopen retains the product history; only the external fault injector is reattached.
        answered = []
        def user(item):
            if item["gate"] != "UNIT":
                return approve(item)
            if answered:
                raise Waiting()
            answered.append(item["request"])
            return "again, keep the accepted promise"
        run = reopen(run, provider, user)
        step = run.step
        run.step = fault
        second = waiting(run)
        assert second["subject"]["recurrence"] == (
            "This is the same unresolved issue after your previous answer")
        assert run.store.read(second["subject"]["previousAnswer"])["request"] == first["request"]
        assert second["subject"]["lineage"] == first["subject"]["lineage"]
        run = reopen(run, provider)
        assert waiting(run) == second
    finally:
        run.close()
