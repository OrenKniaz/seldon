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
    assert set(first["subject"]["attempts"]) < set(subject["attempts"])
    assert subject["lineage"] == first["subject"]["lineage"]
    assert subject["recommendation"]
    run = reopen(run, provider)
    before = run.store.records()
    assert waiting(run) == second and run.store.records() == before
    return run

def unit_lineage(slot):
    """Persistent real check failure stays resumable after user direction; finite repair misses reset loops."""
    run, provider = prepare(slot, "ask", 4)
    native = slot / "native.py"
    text = native.read_text()
    text = text.replace("    Path(name, 'check.py').write_text(source)",
        "    if number == 1:\n"
        "        source += \"raise AssertionError('reader unavailable')\\n\"\n"
        "    Path(name, 'check.py').write_text(source)")
    native.write_text(text)
    try:
        first = waiting(run)
        assert all(run.store.unit(r).state == "DONE" for r in ("u002", "u003", "u004"))
        run = recurrence(run, provider, first)
    finally:
        run.close()
