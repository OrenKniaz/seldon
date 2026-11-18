"""C9/C14/C15: thin acceptance through actual worker processes, Git and final reviews."""

import json
import sys
import time
from copy import deepcopy
from pathlib import Path
from threading import Barrier

import pytest

from seldon import gitwork, plan, questions
from seldon.providers import ROLES, Codex, ProviderFailure
from seldon.run import Run


@pytest.mark.fast
def test_grow_align_receipt_and_lock_journey(tmp_path, monkeypatch):
    """A partial reply and a replayed mixed done/change cannot lock an unseen vision.

    Scalar approval tests miss the interruption between receiving several choices and
    folding them, and cannot catch a draft reaching ARCHITECT or B as the accepted vision.
    """
    from seldon import architecture, decisions, frontdoor, schema

    monkeypatch.delenv("SELDON_OFF", raising=False)
    # Verbatim excerpts from Quiet Bench d56dc7b call-f5f75800. At r10 the recorded
    # simulator said "Build this". The grouped planner and owner replies are counterfactual.
    launch = "python server.py --port PORT --data DIR"
    dream = (f"Every session is saved on a small local storage server (`{launch}`) with its "
             "settings and results, and a web page on that server lets me start a run, browse "
             "saved sessions, reload one and compare two.")
    stopped = ("Report stopped and limited runs truthfully, including how many samples were processed; "
               "do not present a partial run as completed.")
    first = questions.consequential_round_example()
    first["vision"]["size"] = "large"  # The recorded three-wave bench was rated large.
    first["vision"]["stories"][1].update(outcome=stopped, sources=[])
    first["questions"].append({"affects": "scopeIn:inspect", "question": "How can a colleague inspect it?",
        "options": [{"label": "Reopen a saved run", "means": "Open it on this PC"},
                    {"label": "Export a copy", "means": "Open it on another bench"}]})
    mixed = "This is it, except I need a printable result"
    changed = "Compare only the shared measured interval and label the printable result partial."
    revised = deepcopy(first)
    revised.update(changed=changed, questions=[], recommendation="done")
    display = {}
    architect = []
    seen = []

    def provider(q):
        strict_schema(q["schema"])
        kind, facts = q["kind"], q["facts"]
        seen.append(kind)
        if kind == "ALIGN":
            if seen.count("ALIGN") == 1:
                assert facts["Dream"] == dream
                answer = first
            else:
                assert facts["Previous proposal"]["vision"] == first["vision"]
                assert facts["Owner size override"] == "medium"
                assert mixed in facts["Sources"].values()
                replies = {s["label"]: s["source"] for s in facts["Selected choices"]}
                interval = replies["Compare shared interval"]
                printing = replies["Other"]
                assert "Compare shared interval" in facts["Sources"][interval]
                assert facts["Sources"][printing] == mixed
                revised["vision"].update(size="medium", sizeWhy="The owner chose a medium split")
                revised["vision"]["stories"][0].update(outcome=changed, sources=[interval, printing])
                answer = revised
        elif kind == "DECIDE":
            assert facts["User's verbatim words"] == mixed
            picks = {g["id"]: {"optionId": "unclear", "text": ""} for g in facts["Displayed groups"]}
            picks[display["handoff"]] = {"optionId": "other", "text": "printable result"}
            picks["round"] = {"optionId": "done", "text": ""}
            answer = {"selections": picks, "why": "Explicit done and a changed handoff"}
        elif kind == "ARCHITECT":
            architect.append(facts["Approved vision"])
            assert facts["Approved vision"] == revised["vision"]
            goals = list(facts["Commitment handles"])
            answer = {"areas": [{"handle": "bench", "responsibility": "Compare and print",
                                "scope": ["bench/**"], "commitments": goals}],
                      "contracts": [], "decisions": [],
                      "waves": [{"goal": "A printable partial comparison", "commitments": goals}]}
        elif kind == "DETAIL":
            goal = facts["Accepted commitments"]["scopeIn:compare"]
            assert goal["outcome"] == changed
            answer = {"verdict": "planned", "edits": [
                {"kind": "feature", "targetHandle": "f", "typedBody": {
                    "story": goal["story"], "acceptance": goal["outcome"],
                    "commitments": ["scopeIn:compare"]}},
                {"kind": "requirement", "targetHandle": "r", "typedBody": {
                    "acceptance": goal["outcome"], "features": ["f"]}}],
                "units": [{"scope": ["bench/**"], "requirements": ["r"], "dependencies": [],
                           "instructions": "Show and print the shared measured interval"}]}
        else:
            assert kind == "REVIEW"
            answer = {"verdict": "adequate", "gaps": []}
        response = {"answer": {**answer, "why": answer.get("why", "Trace the accepted bench outcome")}}
        schema.validate(response, q["schema"])
        return json.dumps(response)

    product = tmp_path / "product"
    setup = {"userOrigin": "counterfactual owner"}
    run = Run(product, tmp_path / "workers", dream, setup, provider, lambda _: None)
    try:
        assert run.step() == "A"
        initial = frontdoor.state(product)["waitingFor"]
        assert initial and initial["subject"]["vision"] == first["vision"]
        assert run.store.cursor().vision is None and not architect
        comparison, handoff = initial["ask"]["groups"][:2]
        display["handoff"] = handoff["id"]
        run.user = lambda _: {"request": initial["request"], "replyId": "invalid",
                              "selections": [{"groupId": comparison["id"], "optionId": "missing"}]}
        with pytest.raises(decisions.Waiting):
            run.step()
        invalid = frontdoor.state(product)
        assert invalid["waitingFor"] == initial
        assert all(part in invalid["reason"] for part in
                   ("Expected one displayed option", "'missing'", f"Use answer request {initial['request']}"))
        run.user = lambda _: {"request": initial["request"], "replyId": "interval",
            "selections": [{"groupId": comparison["id"], "optionId": comparison["options"][1]["id"]},
                           {"groupId": "size", "optionId": "medium"}]}
        with pytest.raises(decisions.Waiting):
            run.step()
        partial = frontdoor.state(product)["waitingFor"]
        assert comparison["id"] not in [g["id"] for g in partial["ask"]["groups"]]
        assert partial["ask"]["retained"][0]["label"] == "Compare shared interval"

        reply = {"request": partial["request"], "replyId": "print", "words": mixed}
        run.user = lambda _: reply
        record = run.store.record

        def interrupt_after_receipt(kind, body):
            ref = record(kind, body)
            if kind == "user_words" and body.get("replyId") == "print":
                raise KeyboardInterrupt
            return ref

        run.store.record = interrupt_after_receipt
        with pytest.raises(KeyboardInterrupt):
            run.step()
        run.close()
        monkeypatch.setenv("SELDON_OFF", "growAlign")  # An open round keeps its stored shape.
        run = Run.reopen(product, provider, lambda _: reply)
        assert frontdoor.state(product)["waitingFor"] == partial
        assert run.step() == "ALIGN"
        assert run.step() == "A"
        shown = frontdoor.state(product)["waitingFor"]
        assert shown["subject"]["vision"] == revised["vision"]
        with pytest.raises(decisions.Waiting):
            run.step()  # Transport redelivery of the superseded request.
        assert shown["request"] in frontdoor.state(product)["reason"]
        assert run.store.cursor().A is None and run.store.cursor().vision is None and not architect
        receipts = [r for _, _, r in run.store.records("user_words") if r.get("replyId") == "print"]
        assert len(receipts) == 1 and receipts[0]["words"] == mixed and receipts[0]["origin"] == setup["userOrigin"]

        run.user = lambda _: {"request": shown["request"], "replyId": "lock",
                              "selections": [{"groupId": "round", "optionId": "done"}]}
        for phase in ("ARCHITECT", "DETAIL", "REVIEW", "B"):
            assert run.step() == phase
        run.user = lambda _: (_ for _ in ()).throw(decisions.Waiting())
        with pytest.raises(decisions.Waiting):
            run.step()
        b = frontdoor.state(product)["waitingFor"]["subject"]["plan"]
        locked = b["nodes"]["vision"]["body"]
        assert architect == [locked] == [shown["subject"]["vision"]]
        feature = next(f for f in plan.select(b, "feature").values() if f["acceptance"] == changed)
        story = architecture.commitments(locked)[feature["commitments"][0]]
        assert run.store.read(story["sources"][1]) == receipts[0]
        approval = run.store.read(run.store.cursor().A)
        assert approval["request"] == shown["request"] and approval["subject"]["vision"] == locked
        assert seen.count("ALIGN") == 2
    finally:
        run.close()


@pytest.mark.fast
def test_grow_align_tiny_first_done_journey(tmp_path, monkeypatch):
    """A complete tiny proposal locks immediately; revision-only journeys miss forced extra turns."""
    from seldon import decisions, frontdoor, schema

    monkeypatch.delenv("SELDON_OFF", raising=False)
    calls = []

    def provider(q):
        strict_schema(q["schema"])
        calls.append(q["kind"])
        response = {"answer": questions.tiny_round_example()}
        schema.validate(response, q["schema"])
        return json.dumps(response)

    def user(item):
        return {"request": item["request"], "replyId": "tiny-lock",
                "selections": [{"groupId": "round", "optionId": "done"}]}

    product = tmp_path / "greeting"
    run = Run(product, tmp_path / "workers", "Greet a visitor by name", {}, provider, user)
    try:
        assert run.step() == "A"
        shown = frontdoor.state(product)["waitingFor"]
        assert shown["subject"]["vision"]["size"] == "tiny"
        run.user = lambda item: (_ for _ in ()).throw(decisions.Waiting())
        with pytest.raises(decisions.Waiting):
            run.step()
        run.user = user
        assert run.step() == "ARCHITECT"
        assert calls == ["ALIGN"]
        assert run.store.read(run.store.cursor().A)["request"] == shown["request"]
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("repeat", [False, True])
def test_quietbench_skeleton_commitments_journey(tmp_path, repeat):
    """Replay the real skeleton with a goal but no work through approval and into EXECUTE.

    Unlike duplicate-handle cases, every handle in the saved answer is valid. The bug
    is that DETAIL would ask nobody. The repeated reply must reach planner and user.
    """
    from seldon import decisions

    recorded = json.loads((Path(__file__).parent / "fixtures/quietbench-a01-planning.json")
                          .read_text(encoding="utf-8"))
    scopes = {"simulation": ("simulation/**", "scopeIn:2", "Run a tone through a fixed filter"),
              "service": ("server.py", "scopeIn:10", "Save the session and reopen it after restart"),
              "experience": ("web/**", "scopeIn:5", "Show the saved tone run in the page")}
    slot = tmp_path / "quietbench"
    run, _ = prepare(slot)
    corrections, detailed, recovery = [], {}, []

    class QuietBench(Scripted):
        def __call__(self, q):
            strict_schema(q["schema"])
            kind, facts = q["kind"], q["facts"]
            if kind == "ALIGN":
                return json.dumps(recorded[kind])
            if kind == "ARCHITECT":
                body = deepcopy(recorded[kind])
                if "Exact corrections" in facts:
                    corrections.append(facts["Exact corrections"][0]["Errors"][0])
                    if not repeat:
                        body["answer"]["waves"][0]["commitments"] = [c for _, c, _ in scopes.values()]
                return json.dumps(body)
            if kind == "DETAIL":
                area = facts["Area"]["handle"]
                scope, _, behavior = scopes[area]
                detailed[area] = list(facts["Accepted commitments"])
                return json.dumps({"answer": {"verdict": "planned", "edits": [
                    {"kind": "feature", "targetHandle": "slice",
                     "typedBody": {"story": behavior, "acceptance": behavior}},
                    {"kind": "requirement", "targetHandle": "proof",
                     "typedBody": {"features": ["slice"], "acceptance": behavior}}],
                    "units": [{"scope": [scope], "requirements": ["proof"], "dependencies": [],
                               "instructions": behavior}], "why": "Deliver the fixed-tone skeleton."}})
            if kind == "COACH":
                recovery.append(str(facts["Unresolved work"]))
            return super().__call__(q)

    def user(item):
        if "step" in item["subject"]:
            recovery.append(item["ask"]["question"])
            assert item["ask"]["options"][0]["effect"] == "pause"
            raise decisions.Waiting()
        return approve(item)

    run.provider, run.user = QuietBench(slot), user
    try:
        if repeat:
            assert run.run().state == "ARCHITECT"
            assert len(corrections) == 2
            assert len(recovery) == 2 and all(corrections[0] in message for message in recovery)
            assert not detailed
        else:
            for _ in range(12):
                if run.step() == "EXECUTE":
                    break
            assert run.store.cursor().state == "EXECUTE"
            assert plan.approved(run.store) and not run.store.records("step_failure")
            assert detailed == {area: [commitment] for area, (_, commitment, _) in scopes.items()}
            assert len(corrections) == 1 and not recovery
        reason = corrections[0]
        assert all(word in reason for word in ("wave 1", "wave 2", "wave 3", "expected", "got []",
                                               "Send", "scopeIn:2", *scopes))
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("repeat", [False, True])
def test_answer_consumption_repair_golden(tmp_path, repeat):
    """An unusable but schema-valid architecture is corrected before asking the user.

    Duplicate handles used to spend schema retries with no consumption record or useful
    expected/got context. The repeat twin protects against endless exact-fix retries and
    proves the real COACH/user route recommends a resumable pause. Schema-only twins
    cannot exercise this producer-to-plan boundary.
    """
    from seldon import decisions

    slot = tmp_path / "answer-repair"
    run, _ = prepare(slot)
    questions_seen, user_questions = [], []

    class Mismatch(Scripted):
        def __call__(self, q):
            raw = super().__call__(q)
            if q["kind"] == "ARCHITECT":
                questions_seen.append(q)
                if repeat or "Exact corrections" not in q["facts"]:
                    body = json.loads(raw)
                    conflict = {**body["answer"]["areas"][0], "responsibility": "A conflicting responsibility"}
                    body["answer"]["areas"].append(conflict)
                    return json.dumps(body)
                # An identical repeated entry has one sensible meaning; normalise it.
                body = json.loads(raw)
                body["answer"]["areas"].append(deepcopy(body["answer"]["areas"][0]))
                return json.dumps(body)
            if q["kind"] == "COACH":
                assert "ARCHITECT" in str(q["facts"]["Unresolved work"])
                assert "areas" in str(q["facts"]["Unresolved work"])
                return json.dumps({"answer": {"guidance": "Send distinct area handles",
                                               "why": "The area list repeats a handle"}})
            return raw

    run.provider = Mismatch(slot)
    def user(item):
        if "step" in item["subject"]:
            user_questions.append(item)
            raise decisions.Waiting()
        return approve(item)
    run.user = user
    try:
        run.run()
        failures = [b for _, _, b in run.store.records("consumption_failure")]
        assert failures and all(b["kind"] == "ARCHITECT" and b["field"] == "areas"
                                for b in failures)
        reason = failures[0]["reason"]
        assert "expected" in reason and "got" in reason and "Send" in reason
        assert reason in str(questions_seen[1]["facts"]["Exact corrections"])
        if repeat:
            assert len(questions_seen) == 4 and len(failures) == 4
            assert run.store.records("planner_answer") and len(user_questions) == 1
            item = user_questions[0]
            assert reason in str(item["subject"])
            assert reason in item["ask"]["question"]
            assert "Seldon's fault" in item["ask"]["question"]
            assert "pause" in item["ask"]["question"]
            assert item["ask"]["options"][0]["effect"] == "pause"
        else:
            assert len(questions_seen) == 2 and len(failures) == 1
            assert not user_questions and not run.store.records("step_failure")
            assert run.store.cursor().state == "SCRIPTED_C"
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("off", [False, True])
def test_consumption_crash_rollback_and_switch_journey(tmp_path, monkeypatch, off):
    """A crash after plan insertion rolls back and teaches the same question its context.

    A schema-valid answer can fail inside Seldon's projection after writes have begun.
    Existing input-error twins fail before writing and miss partial plan publication.
    The off twin proves the cohort switch routes directly to existing planner recovery.
    """
    monkeypatch.setenv("SELDON_OFF", "growAlign,answerRepair" if off else "growAlign")
    slot = tmp_path / "consumption-crash"
    run, _ = prepare(slot)
    original = run.store.revision
    failed, seen = [], []
    def interrupted(document, parent=None):
        revision = original(document, parent)
        if not failed:
            failed.append(revision)
            raise IndexError("list index out of range")
        return revision
    monkeypatch.setattr(run.store, "revision", interrupted)

    class Planner(Scripted):
        def __call__(self, q):
            if q["kind"] == "ARCHITECT":
                seen.append(q)
            if failed and q["kind"] in ("ARCHITECT", "COACH"):
                assert run.store.cursor().revision is None
                assert run.store.db.execute("SELECT count(*) FROM plan_revision").fetchone()[0] == 0
                assert not [b for _, _, b in run.store.records("answer") if b["kind"] == "ARCHITECT"]
            return super().__call__(q)
    run.provider = Planner(slot)
    try:
        run.run()
        assert run.store.cursor().state == "SCRIPTED_C"
        failures = [b for _, _, b in run.store.records("consumption_failure")]
        assert len(failures) == 1 and failures[0]["exceptionType"] == "IndexError"
        reason = failures[0]["reason"]
        assert "ARCHITECT" in reason and "waves" in reason and "Send" in reason
        assert "list index out of range" not in reason
        assert len(seen) == 2
        assert bool(run.store.records("planner_answer")) == off
        assert (reason in str(seen[1]["facts"].get("Exact corrections"))) == (not off)
    finally:
        run.close()


@pytest.mark.fast
def test_coach_consumption_failure_parks_golden(tmp_path, monkeypatch):
    """If Seldon cannot consume COACH either, its saved question recommends pause.

    Ordinary step-fault twins assume coaching works; a bug applying its guidance used
    to escape the recovery handler itself, leaving no helpful question to resume from.
    """
    from seldon import decisions
    slot = tmp_path / "coach-crash"
    run, _ = prepare(slot)
    record = run.store.record
    def broken(kind, body):
        ref = record(kind, body)
        if kind == "planner_answer":
            raise IndexError("list index out of range")
        return ref
    monkeypatch.setattr(run.store, "record", broken)
    class Planner(Scripted):
        def __call__(self, q):
            raw = super().__call__(q)
            if q["kind"] == "ARCHITECT":
                body = json.loads(raw)
                body["answer"]["waves"] = []
                return json.dumps(body)
            return raw
    run.provider = Planner(slot)
    def user(item):
        if "step" in item["subject"]:
            raise decisions.Waiting()
        return approve(item)
    run.user = user
    try:
        run.run()
        request = run.store.read(run.store.cursor().decision)
        assert "COACH.guidance/ask" in request["ask"]["question"]
        assert "IndexError" in request["ask"]["question"]
        assert "list index out of range" not in request["ask"]["question"]
        assert request["ask"]["options"][0]["effect"] == "pause"
        assert not run.store.records("planner_answer")
        assert len([b for _, _, b in run.store.records("consumption_failure") if b["kind"] == "COACH"]) == 2
        assert len(run.store.records("step_failure")) == 2
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("fault", [False, True])
def test_wave_loop_golden(tmp_path, fault):
    """An unanswered check-in allows planning but holds new work; its answer grows the product.

    The fault twin breaks an old reader with a new format and repairs its owner, then steers
    at C. One-wave and planning-only journeys miss stale bases, reused sessions and check-in
    consent lost as the cursor advances. The strict provider rejects STEER before execution
    if any nested object makes fields optional. Recorded remaining-only STEER answers must
    preserve completed waves; empty-delta stubs missed the real wave-2 IndexError.
    """
    from wave_loop_journey import journey

    journey(tmp_path / "waves", fault)


@pytest.mark.fast
@pytest.mark.parametrize("missing_object", [False, True])
def test_recorded_shelflife_repair_coaching_journey(tmp_path, missing_object):
    """A reopened repair without a worktree reaches its UNIT question on the recorded base.

    ShelfLife r439 failed before COACH because cwd=None queried Seldon's repo. Existing
    repair journeys kept a worktree and missed this recorded ASKED state. A lost Git object
    twin must park with the actual execution_facts caller, rather than git()'s generic raise.
    """
    import base64
    import zlib
    from seldon import decisions
    from seldon.store import Store

    fixture = json.loads((Path(__file__).parent / "fixtures/shelflife_not_tree.json").read_text())
    project = tmp_path / "product"
    project.mkdir()
    gitwork.git(project, "init", "--quiet")
    for oid, kind, packed in fixture["objects"]:
        assert gitwork.git(project, "hash-object", "-t", kind, "-w", "--stdin",
                           data=zlib.decompress(base64.b64decode(packed))) == oid
    candidate = fixture["cursor"]["candidate"]
    gitwork.git(project, "update-ref", "HEAD", candidate)
    gitwork.checkout(project, candidate)
    store = Store(project)
    # Rehydrate the recorded snapshot; only local filesystem addresses and the paused
    # controller decision change. No units, patches or recovery outcomes are invented.
    def relocate(value):
        if isinstance(value, str):
            old = "C:/seldon/tests/m1/shelflife-main-15682aa-s01"
            for spelling in (old, old.replace("/", "\\"), old.replace("/", "\\\\")):
                value = value.replace(spelling, str(tmp_path))
            return value
        if isinstance(value, list):
            return [relocate(v) for v in value]
        if isinstance(value, dict):
            return {k: relocate(v) for k, v in value.items()}
        return value
    for ref, kind, body in fixture["records"]:
        store.db.execute("INSERT INTO record VALUES (?,?,?)", (ref, kind, json.dumps(relocate(body))))
    for table, rows in (("unit", fixture["units"]), ("recovery", fixture["recoveries"])):
        for ref, body in rows:
            store.db.execute(f"INSERT INTO {table} VALUES (?,?)", (ref, json.dumps(relocate(body))))
    cursor = relocate(fixture["cursor"])
    cursor["decision"] = None
    store.db.execute("INSERT INTO run VALUES (1,?)", (json.dumps(cursor),))
    store.db.execute("INSERT INTO plan_revision VALUES (?,NULL,?)",
                     (cursor["revision"], json.dumps(fixture["document"])))
    store.close()
    if missing_object:
        obj = project / ".git/objects" / candidate[:2] / candidate[2:]
        obj.chmod(0o666)
        obj.unlink()
    calls = []
    def provider(q):
        assert q["kind"] == "COACH"
        calls.append(q)
        return json.dumps({"answer": {"ask": {"question": "How should this repair continue?",
            "options": [{"label": "Repair the owner", "means": "Continue with the recorded correction"},
                        {"label": "Clarify", "means": "Explain the outstanding result"}]},
            "why": "The unresolved repair needs direction"}})
    def user(item):
        raise decisions.Waiting()
    run = Run.reopen(project, provider, user)
    try:
        run.run()
        request = run.store.read(run.store.cursor().decision)
        failures = run.store.records("step_failure")
        if missing_object:
            assert request["gate"] == "EXECUTE" and failures
            body = failures[-1][2]
            assert "not a tree object" in body["message"]
            assert body["location"].startswith("src/seldon/questions.py:")
            assert body["location"].endswith(" execution_facts")
        else:
            assert request["gate"] == "UNIT" and not failures
            facts = calls[0]["facts"]["Execution facts"]
            assert facts["Base"] == candidate and facts["Worktree"] is None
            assert facts["Paths present"] == ["README.md", "checks/integration/fresh-folder.cjs"]
            assert request["subject"]["unit"] == "1.import-integration.1"
        assert len(calls) == (0 if missing_object else 1)
        assert run.store.cursor().candidate == candidate
        assert run.store.unit("1.backend.1").state == "DONE"
    finally:
        run.close()


@pytest.mark.fast
def test_architect_scope_returns_to_vision_journey(tmp_path):
    """An architect's scope discovery reaches ALIGN and A before any worker can start.

    Ordinary wave journeys keep the vision fixed and miss scope text lost at the ALIGN boundary.
    """
    from seldon.decisions import Waiting

    run, _ = prepare(tmp_path / "scope")

    class Scope(Scripted):
        def __call__(self, q):
            if q["kind"] == "ARCHITECT":
                return json.dumps({"answer": {"scope": "Read exported values", "why": "Needs acceptance"}})
            answer = json.loads(super().__call__(q))["answer"]
            if q["kind"] == "ALIGN" and "Requested change" in q["facts"]:
                answer["scopeIn"].append(q["facts"]["Requested change"]["answer"]["scope"])
            return json.dumps({"answer": answer})

    run.provider = Scope(tmp_path / "scope")
    def user(item):
        if item["gate"] == "A" and "Read exported values" in item["subject"]["vision"]["scopeIn"]:
            raise Waiting()
        return approve(item)

    run.user = user
    try:
        with pytest.raises(Waiting):
            while True:
                run.step()
        request = run.store.read(run.store.cursor().decision)
        assert run.store.cursor().state == request["gate"] == "A"
        assert "Read exported values" in request["subject"]["vision"]["scopeIn"]
        assert not run.store.units()
    finally:
        run.close()

VISION = {
    "size": "small",
    "understanding": "Read named values",
    "users": ["CLI user"],
    "scopeIn": ["Read each named persistent value"],
    "scopeOut": [],
    "improvements": [],
    "quality": [{"criterion": "Clear output", "verification": "Read the real command output"}],
    "alternatives": [{"label": "Show all values", "means": "List values without naming one"}],
}

NATIVE = r"""
import json, os, sys, time, uuid
from pathlib import Path
slot = Path(sys.argv[1])
args = sys.argv[2:]
schema = Path(args[args.index('--output-schema')+1])
q = json.loads(schema.with_name('question.json').read_text('utf-8'))
facts = q['facts']
name = facts['Write scope'][0].split('/')[0]
mode = (slot/'mode').read_text()
def owner(number):
    scope = 'unit-'+str(number).zfill(3)+'/**'
    return next(r for r, paths in facts['Correction owners'].items() if scope in paths)
context = slot/('context-'+name+'.json')
if q['execution'].get('session') and 'Exact failure' in facts:
    facts = {**json.loads(context.read_text()), **facts}
else:
    context.write_text(json.dumps(facts))
if mode == 'parallel-contract':
    assert ('Design guidance used by UI workers' in facts) == (name == 'unit-002')
counter = slot/('turn-'+name)
turn = int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(turn))
thread = args[args.index('resume')+1] if 'resume' in args else str(uuid.uuid4())
print(json.dumps({'type':'thread.started','thread_id':thread}), flush=True)
with (slot/('launch-'+name+'.jsonl')).open('a') as out:
    out.write(json.dumps({'thread':thread,'resumed':'resume' in args,'turn':turn})+'\n')
if mode == 'scale-five' and name == 'unit-013':
    (slot/'peer-running').write_text(str(os.getpid()))
    deadline = time.monotonic() + 60
    while not (slot/'peer-answered').exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert (slot/'peer-answered').exists(), 'no answer while peer process ran'
    (slot/'peer-finished').write_text(str(os.getpid()))
if mode == 'scale-five' and name == 'unit-012' and turn == 1:
    deadline = time.monotonic() + 60
    while not (slot/'peer-running').exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert (slot/'peer-running').exists(), 'peer process never launched'
if mode == 'scale-five' and name == 'unit-022' and not (slot/'resumed').exists():
    (slot/'kill-wave3').write_text(str(os.getpid()))
    time.sleep(240)
if mode == 'kill-work' and name == 'unit-002' and not (slot/'resume').exists():
    (slot/'kill-ready').write_text(str(os.getpid()))
    time.sleep(30)
answer = {'outcome':'done','summary':'Wrote and exercised the acceptance',
          'checks':[], 'checkPaths':[], 'discoveries':[], 'why':'Ready for independent observation'}
if mode == 'schema-fix' and name == 'unit-001' and turn == 1:
    answer = json.loads((slot/'recorded-work.json').read_text())['answer']
elif mode == 'scale-five' and name == 'unit-012' and turn == 1:
    answer.update(outcome='question', summary='Should the reader preserve the exact label?')
elif mode == 'repeat-question' and name == 'unit-001':
    answer.update(outcome='question', summary='May I hand off with acceptance pending WAVE-VERIFY?')
elif mode == 'question' and name == 'unit-001' and turn == 1:
    answer.update(outcome='question', summary='How can I check persistence in this scope?')
elif mode == 'coach-facts' and name == 'unit-003' and turn == 1:
    answer.update(outcome='question', summary="Coordinator: supply u001's existing task/thread ID and evidence location")
elif mode == 'coach-facts' and name == 'unit-001' and turn == 1:
    answer.update(outcome='question', summary="Where are u002's files and execution evidence?")
elif name == 'docs':
    command = [sys.executable, '-B', 'docs/try-it/check.py']
    Path('docs/try-it').mkdir(parents=True, exist_ok=True)
    Path(facts['Guide path']).write_text('Read the saved value in unit-001/value.txt. Observe value-001.\n')
    Path('docs/try-it/check.py').write_text("from pathlib import Path\n"
        "assert Path('unit-001/value.txt').read_text() == 'value-001'\nprint('observed value-001')\n")
    answer['checkPaths'] = ['docs/try-it/check.py']
    answer['checks'] = [{'purpose':'Follow the reader path', 'command':command,
                         'expectedEvidence':'Observed exact saved value'}]
else:
    Path(name).mkdir(exist_ok=True)
    number = int(name.split('-')[1])
    execution = facts['Execution facts']
    for peer, state in execution['Units'].items():
        if peer != owner(int(name.split('-')[1])) and state['state'] == 'DONE':
            paths = [p for p in execution['Paths present'] if p.startswith('unit-') and p.endswith('value.txt')]
            assert paths and all(Path(p).read_text().startswith('value-') for p in paths)
    if mode == 'coach-facts' and name == 'unit-003':
        guidance = facts['Exact failure']
        assert 'WAVE-VERIFY' in guidance
        assert 'Worktree' in guidance and 'Runtime' in guidance
        assert execution['Proof owner'] == 'WAVE-VERIFY'
    if mode == 'coach-facts' and name == 'unit-001':
        guidance = facts['Exact failure']
        assert 'Worktree' in guidance and 'Runtime' in guidance
    value = 'value-'+str(number).zfill(3)
    source = ('from pathlib import Path\nimport os\n'
        + f"assert Path('{name}/value.txt').read_text() == '{value}'\n"
        + f"print('observed {value}')\n")
    if mode == 'contracts':
        if number == 1:
            Path(name, 'items.json').write_text(json.dumps({'items': ['value-001']}))
        elif any(c['provider'] == owner(1) for c in facts.get('Contracts consumed', {}).values()):
            agreements = {c['text'] for label in ('Contracts consumed', 'Area contracts')
                          for c in facts.get(label, {}).values()}
            assert 'JSON response: {"items": ["value-001"]}.' in agreements
            source += ("import json\n"
                "assert json.loads(Path('unit-001/items.json').read_text())['items'] == ['value-001']\n")
        else:
            assert not any(c['provider'] == owner(1) for label in
                           ('Contracts provided', 'Contracts consumed', 'Area contracts')
                           for c in facts.get(label, {}).values())
    if mode == 'contract-fanout' and number >= 3:
        assert 'UTF-8 text: value-001.' in str(facts['Contracts consumed'])
        source += "assert Path('unit-001/value.txt').read_text() == 'value-001'\n"
    if mode == 'coach-facts' and name == 'unit-003':
        source += "assert Path('unit-001/value.txt').read_text() == 'value-001'\n"
    source += "if os.environ.get('SELDON_EVIDENCE_DIR'):\n"
    source += "    evidence = Path(os.environ['SELDON_EVIDENCE_DIR'])\n"
    source += "    evidence.joinpath('value.json').write_text('observed persistence')\n"
    if mode == 'see-proof-gap' and name == 'unit-002' and turn == 1:
        source = source.replace('observed value-002', 'unlabelled value-002')
    if ((mode == 'proof-failure' and name == 'unit-001' and turn == 1)
            or (mode == 'scale-five' and name == 'unit-011' and turn <= 2)):
        source += 'raise AssertionError("actual failed product assertion")\n'
    if mode == 'wave-fault' and name == 'unit-001' and turn == 1:
        source += "assert not Path('unit-002/value.txt').exists(), 'integrated mismatch'\n"
    if mode == 'wave-gap' and name == 'unit-002' and turn == 1:
        source += "assert not Path('unit-001/value.txt').exists(), 'integrated mismatch'\n"
    if mode.startswith('defect-'):
        if name == 'unit-001':
            source += "assert Path('unit-002/peer.txt').read_text() == 'correct', 'peer defect'\n"
        else:
            Path(name, 'peer.txt').write_text('correct' if turn > 1 and mode != 'defect-fails' else 'broken')
    Path(name, 'check.py').write_text(source)
    Path(name, 'value.txt').write_text(value)
    if mode == 'contract-gap' and name == 'unit-001' and 'today.txt' in facts.get('Exact failure', ''):
        Path(name, 'today.txt').write_text('2026-11-18')
    if mode == 'scope' and turn == 1 and name == 'unit-001':
        Path('unowned.txt').write_text('outside scope')
    if mode == 'scope' and turn > 1:
        Path('unowned.txt').unlink(missing_ok=True)
    answer['discoveries'] = [{'owner': owner(int(name.split('-')[1])), 'correction': 'Keep the saved values visible'}]
    if mode.startswith('defect-') and name == 'unit-001':
        answer['discoveries'] = [{'owner': owner(2), 'correction': 'Write correct into unit-002/peer.txt'}]
        if mode == 'defect-question':
            answer.update(outcome='question', summary='May u002 fix the peer defect?')
    answer['checkPaths'] = [name+'/check.py']
    answer['checks'] = [{'purpose':'Read the actual persistent value',
        'command':[sys.executable,'-B',name+'/check.py'],
        'expectedEvidence':'Observed exact saved value'}]
if 'Existing checks' in facts and not (mode == 'schema-fix' and name == 'unit-001' and turn == 1):
    requirements = facts['Requirements']
    latest = max((int(r.split('.')[0]) for r in requirements), default=0)
    for check in answer['checks']:
        links = [r for r in requirements if r.startswith(str(latest) + '.')]
        check['requirements'] = links
        for handle, prior in facts['Existing checks'].items():
            if check['command'] == prior['command'] and set(links) - set(prior['requirements']):
                check.update(update=handle, requirements=list(dict.fromkeys(prior['requirements'] + links)))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'answer':answer})}}), flush=True)
print(json.dumps({'type':'turn.completed'}), flush=True)
"""


def strict_schema(schema, path="schema"):
    """Codex's pre-invocation rejection, shared by scripted planning and native WORK."""
    if schema.get("type") == "object":
        missing = schema["properties"].keys() - set(schema.get("required", []))
        reason = (f"'{path}.required' is required to include every key in properties. "
                  f"Missing '{next(iter(sorted(missing)))}'." if missing else
                  f"'{path}.additionalProperties' is required to be false."
                  if schema.get("additionalProperties") is not False else None)
        if reason:
            raise RuntimeError("Codex 400 Invalid schema for response_format 'codex_output_schema': " + reason)
        for name, child in schema["properties"].items():
            strict_schema(child, f"{path}.properties.{name}")
    if "items" in schema:
        strict_schema(schema["items"], path + ".items")
    for index, child in enumerate(schema.get("anyOf", [])):
        strict_schema(child, f"{path}.anyOf.{index}")


def judge_owners(q):
    """The owners a JUDGE gap may name (the judged unit first, then its contracts); [] before 11-18."""
    gap = q["schema"]["properties"]["answer"]["anyOf"][0]["properties"]["gaps"]["items"]
    return gap["properties"]["owners"]["items"]["enum"] if "owners" in gap["properties"] else []


class Scripted:
    def __init__(self, slot, mode="normal", count=1):
        self.slot, self.mode, self.count = Path(slot), mode, count
        self.setup = {
            "providers": deepcopy(ROLES),
            "scriptedUser": True,
            "userOrigin": "scripted journey",
            "codexCommand": [sys.executable, "-B", str(self.slot / "native.py"), str(self.slot)],
            "python": {"path": sys.executable},
        }
        self.native = Codex(self.slot / "calls", self.setup)
        self.calls = []
        self.gaps = set()
        self.parallel = Barrier(3) if mode == "parallel-contract" else None

    def timeout_for(self, kind, context):
        return 30

    def invoke(self, q, started):
        strict_schema(q["schema"])
        if q["kind"] == "WORK":
            if self.mode == "wave-fault" and "Exact failure" in q["facts"]:
                assert q["execution"]["session"]
                assert "Correction observations" not in q["facts"]
                assert "integrated mismatch" in q["facts"]["Exact failure"]
                assert "Requirements" not in q["facts"]
            if self.parallel:
                self.parallel.wait(timeout=60)  # Hang guard, not a timing assertion.
                if plan.GUIDE_PATH in q["facts"]["Write scope"]:
                    assert q["facts"]["This wave's commitments"] == {"scopeIn:1": VISION["scopeIn"][0]}
                    assert "value-001" in str(q["facts"]["Contracts consumed"])
            return self.native.invoke(q, started)
        return self(q), None

    def __call__(self, q):
        strict_schema(q["schema"])
        kind, facts = q["kind"], q["facts"]
        self.calls.append(kind)
        if kind == "ALIGN":
            answer = deepcopy(VISION)
            if self.mode == "parallel-contract":
                answer["quality"][0]["criterion"] = "Visual direction: a clear, deliberate reader page"
        elif kind == "ARCHITECT":
            groups = (
                [[n] for n in range(1, self.count + 1)] if self.mode == "contracts" else
                [[1], [2], [3]] if self.mode == "coach-facts" else
                [[1, 2], [3, 4]] if self.mode == "contract-fanout" else [[1], [2]]
                if self.mode in ("contracts", "parallel-contract", "wave-gap", "contract-gap", "see-proof", "see-proof-gap", "contract-fanout")
                else [list(range(i, min(i + 10, self.count + 1))) for i in range(1, self.count + 1, 10)]
            )
            self.refs = {n: f"1.area-{i}.{j}" for i, group in enumerate(groups, 1)
                         for j, n in enumerate(group, 1)}
            answer = {
                "areas": [
                    {
                        "handle": f"area-{i}",
                        "responsibility": "Deliver named values",
                        "scope": [f"unit-{n:03}/**" for n in group],
                        "commitments": (["scopeIn:1"] if i == 1 else ["quality:1"])
                        if self.mode == "parallel-contract" else ["scopeIn:1", "quality:1"],
                    }
                    for i, group in enumerate(groups, 1)
                ],
                "contracts": [],
                "decisions": [],
                "waves": [{"goal": "Deliver the complete values",
                           "commitments": ["scopeIn:1", "quality:1"]}],
            }
            if self.mode in ("contracts", "parallel-contract", "coach-facts", "wave-gap", "contract-gap", "see-proof", "see-proof-gap", "contract-fanout"):
                answer["contracts"] = [
                    {
                        "handle": "values",
                        "kind": "data rule",
                        "text": "UTF-8 text: value-001.".ljust(3000, " "),
                        "provider": "area-1",
                        "consumers": (["area-1", "area-2", "area-2"]
                                      if self.mode == "parallel-contract" else
                                      ["area-3"] if self.mode == "coach-facts" else ["area-2"]),
                    }
                ]
                if self.mode == "contracts":
                    answer["contracts"].append({
                        "handle": "items", "kind": "api",
                        "text": 'JSON response: {"items": ["value-001"]}.',
                        "provider": "area-1", "consumers": [],
                    })
        elif kind == "DETAIL":
            numbers = [int(scope.split("/")[0].split("-")[1]) for scope in facts["Area"]["scope"]]
            edits, units = [], []
            for n in numbers:
                f, r = f"f{n}", f"r{n}"
                edits += [
                    {
                        "kind": "feature",
                        "targetHandle": f,
                        "typedBody": {"story": f"Read value {n:03}", "acceptance": f"Observe value-{n:03}"},
                    },
                    {
                        "kind": "requirement",
                        "targetHandle": r,
                        "typedBody": {
                            "acceptance": f"Running the reader displays saved value-{n:03}.",
                            "features": [f],
                        },
                    },
                ]
                units.append(
                    {
                        "scope": [f"unit-{n:03}/**"],
                        "requirements": [r],
                        "dependencies": [],
                        "instructions": f"Deliver and check value-{n:03}",
                    }
                )
            if self.mode == "contract-fanout" and facts["Area"]["handle"] == "area-1":
                units[0]["provides"] = ["values"]
            if self.mode in ("kill-work", "wave-fault") and len(units) > 1:
                units[1]["dependencies"] = [1]
            if self.mode.startswith("defect-"):
                units[0]["dependencies"] = [2]
            answer = {"verdict": "planned", "edits": edits, "units": units}
        elif kind == "REVIEW":
            answer = {"verdict": "adequate", "gaps": []}
            if self.mode == "review-gap":
                answer = {
                    "verdict": "gap",
                    "gaps": [
                        {"severity": "must-fix",
                            "target": "plan",
                            "correction": "Keep the accepted persistent value in the requirement sentence.",
                        }
                    ],
                }
        elif kind == "COACH":
            answer = {"guidance": "Yes. Write a scoped Python check that reads the saved value "
                      "and asserts its contents."}
            if self.mode == "coach-facts":
                answer["guidance"] = questions.labelled(facts["Execution facts"])
        elif kind == "DECIDE":
            words = facts["User's words"]
            answer = {"choice": "Waive" if words == "Go on and do not stop again" and "Waive" in q["targets"]
                      else "Other" if words.startswith(("Revise", "Try again", "again"))
                      else "Stop" if words.startswith("No") else q["targets"][0]}
        elif kind in ("JUDGE", "FLOW", "PRODUCT"):
            answer = {
                "verdict": "satisfied",
                "gaps": [],
            }
            if kind == "JUDGE":
                observations = facts["Observations"]
                assert any(o["purpose"] in ("P", "final") and o["exitCode"] == 0 for o in observations)
                for r in q["targets"]:
                    assert any(
                        r in o["requirements"] and o["purpose"] in ("T", "wave-start") and o["exitCode"] != 0
                        for o in observations
                    )
                if (self.mode in ("unit-gap", "unit-rework") and "value-001" in str(facts["Requirements"])
                        or self.mode == "wave-gap" and "value-002" in str(facts["Requirements"])):
                    name = "unit-002" if self.mode == "wave-gap" else "unit-001"
                    turn = int((self.slot / ("turn-" + name)).read_text())
                    if self.mode == "unit-gap" or turn == 1:
                        r = q["targets"][0]
                        answer.update(
                            verdict="gap",
                            gaps=[
                                {"severity": "must-fix",
                                    "target": r, "owners": judge_owners(q)[:1],
                                    "correction": facts["Requirements"][r]["acceptance"]
                                    + (" The observed RED does not demonstrate this requirement."
                                       if self.mode == "unit-rework" else " The check is incomplete."),
                                }
                            ],
                        )
                if self.mode in ("see-proof", "see-proof-gap") and "value-002" in str(facts["Requirements"]):
                    assert any(o["purpose"] == "final" and o["exitCode"] == 0 for o in observations)
                    if any("unlabelled value-002" in o["stdout"] for o in observations
                           if o["purpose"] == "final"):
                        answer.update(verdict="gap", gaps=[{"severity": "must-fix", "target": q["targets"][0],
                            "owners": judge_owners(q)[:1], "correction": "The reader output omits the saved value label"}])
            else:
                obs = facts["Final observations"]
                assert obs and all(o["exitCode"] == 0 for o in obs)
                if self.mode in ("see-proof", "see-proof-gap"):
                    for verdict in facts["Source judgment"]:
                        assert verdict["verdict"] == "satisfied" and verdict["why"]
                        assert verdict["gaps"] == []
                    if "value-002" in str(facts):
                        assert any(v["unit"] == self.refs[2] for v in facts["Source judgment"])
                if self.mode == "unit-gap":
                    for verdict in facts["Source judgment"]:
                        if verdict["unit"] == self.refs[1]:
                            assert verdict["verdict"] == "gap" and verdict["why"]
                            assert "The check is incomplete" in str(verdict["gaps"])
                if kind == "FLOW" and self.mode == "flow-fault" and "value-001" in str(facts["Requirements"]):
                    if kind not in self.gaps:
                        self.gaps.add(kind)
                        r = q["targets"][0]
                        answer.update(
                            verdict="gap",
                            gaps=[
                                {"severity": "must-fix",
                                    "target": r,
                                    "correction": "Exercise the promised reader",
                                    "owners": list(facts["Correction owners"]),
                                }
                            ],
                        )
                if (kind == "PRODUCT" and self.mode == "product-fault"
                        and any(c["criterion"] == VISION["quality"][0]["criterion"]
                                for c in facts["Criteria"].values())
                        and kind not in self.gaps):
                    self.gaps.add(kind)
                    answer.update(
                        verdict="gap",
                        gaps=[{"severity": "must-fix", "target": q["targets"][0], "correction": "Make the accepted output clear",
                               "owners": [self.refs[1], self.refs[2]]}],
                    )
        else:
            raise AssertionError(kind)
        answer["why"] = "The observed acceptance supports this answer"
        return json.dumps({"answer": answer})


def approve(request):
    from seldon.decisions import Waiting

    if request["gate"] not in ("A", "B", "CHECK-IN", "C"):
        raise Waiting()
    return {"request": request["request"], "subject": request["subject"], "choice": "approve"}


def unit_ref(run, number):
    scope = f"unit-{number:03}/**"
    return next(r for r, u in plan.select(run.store.plan(run.store.cursor().revision), "unit").items()
                if scope in u["scope"])


def prepare(slot, mode="normal", count=1, capacity=6):
    slot.mkdir(parents=True)
    (slot / "mode").write_text(mode)
    (slot / "native.py").write_text(NATIVE, encoding="utf-8")
    if mode == "schema-fix":
        import shutil

        shutil.copyfile(Path(__file__).with_name("recorded_work.json"), slot / "recorded-work.json")
    provider = Scripted(slot, mode, count)
    return Run(
        slot / "product",
        slot / "workers",
        "Read named values",
        provider.setup,
        provider,
        approve,
        capacity=capacity,
    ), provider


def finish(run):
    while run.store.cursor().state not in ("SCRIPTED_C", "ACCEPTED", "STOPPED"):
        run.step()
    cursor = run.store.cursor()
    assert cursor.state == "SCRIPTED_C"
    assert all(u.state in ("DONE", "DROPPED") for u in run.store.units().values())
    document = plan.execution(run.store)
    obs = [run.store.read(r) for r in cursor.finalObservations]
    assert all(o["commit"] == cursor.candidate and o["exitCode"] == 0 for o in obs)
    requirements = set(plan.select(document, "requirement"))
    assert requirements == {run.store.read(r)["subject"] for r in cursor.requirementVerdicts}
    gitwork.unchanged(run.project, cursor.candidate)
    assert not run.store.records("outcome")[-1][2]["accepted"]
    return cursor, run.store.records(), run.store.units()


def journey(slot, mode="normal", count=1):
    run, provider = prepare(slot, mode, count)
    try:
        if count == 50:
            while run.store.cursor().state != "WAVE-VERIFY":
                run.step()
            candidate = run.store.cursor().candidate
            run.close()
            run = Run.reopen(slot / "product", provider, approve)
        if mode == "see-proof-gap":
            from seldon import fixups

            while run.store.cursor().state != "WAVE-VERIFY":
                run.step()
            unaffected = run.store.unit(unit_ref(run, 1))

            class Interrupted(BaseException):
                pass

            def interrupt(store, gaps):
                assert "saved value label" in str(gaps)
                raise Interrupted()
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(fixups, "apply", interrupt)
                with pytest.raises(Interrupted):
                    run.step()
            run.close()
            run = Run.reopen(slot / "product", provider, approve)
        if mode == "wave-gap":
            while run.store.cursor().state != "WAVE-VERIFY":
                run.step()
            assert all(u.state in ("DONE", "DROPPED") for u in run.store.units().values())
            assert run.store.unit(unit_ref(run, 2)).judgment is None
            assert not any(b.get("unit") == unit_ref(run, 2) and b["next"] == "REWORK"
                           for _, _, b in run.store.records("transition"))
            run.step()
            assert run.store.records("unit_fixup")
        finish(run)
        if mode == "normal":
            assert any(b["discoveries"] for _, _, b in run.store.records("worker_checks"))
            from run_report import report
            output, row = report(slot)
            assert row[3] == "SCRIPTED_C" and "accepted=False" in output
            shown = json.loads(next(line.split(": ",1)[1] for line in output.splitlines()
                                    if line.startswith("User questions:")))
            assert all(q["answered"] for q in shown)
            assert any(q["gate"] == "A" and q["subject"]["vision"]["understanding"] == VISION["understanding"]
                       for q in shown)
            usage = json.loads(next(line.split(": ",1)[1] for line in output.splitlines()
                                    if line.startswith("Calls and fresh tokens per wave:")))
            assert usage["1"]["freshTokens"] is None, "missing native usage became zero tokens"

        if mode == "parallel-contract":
            assert provider.calls.count("ARCHITECT") == 1
            assert not run.store.records("detail_correction")
            assert not run.store.records("architecture_gap")
            assert not any("Dependencies" in b["reason"] for _, _, b in run.store.records("unit_wait"))
            transitions = [b for _, _, b in run.store.records("transition")
                           if b.get("unit") in (unit_ref(run, 1), unit_ref(run, 2), "guide")]
            working = {b["unit"]: i for i, b in enumerate(transitions) if b["next"] == "WORKING"}
            assert set(working) == {unit_ref(run, 1), unit_ref(run, 2), "guide"}
            assert all(b["next"] == "WORKING" for b in transitions[min(working.values()):max(working.values())+1])
            walkthrough = [b for _, _, b in run.store.records("observation") if b["unit"] == "guide"]
            assert any(b["purpose"] == "T" and b["exitCode"] != 0
                       and "unit-001/value.txt" in b["stderr"] for b in walkthrough)
            assert any(b["purpose"] == "final" and b["exitCode"] == 0
                       and b["commit"] == run.store.cursor().candidate for b in walkthrough)
            assert not any(b["purpose"] == "P" for b in walkthrough)
            agreement, = plan.select(plan.execution(run.store), "contract").values()
            assert len(agreement["text"]) == 3000 and agreement["kind"] == "data rule"
            assert agreement["consumers"] == [unit_ref(run, 2), "guide"]
        if count == 50:
            assert run.store.cursor().candidate == candidate
            assert len(run.store.units()) == 51
            assert all((slot / f"turn-unit-{i:03}").read_text() == "1" for i in range(1, 51))
        assert (run.project / "unit-001/value.txt").read_text() == "value-001"
        records = run.store.records()
        gates = [b["gate"] for _, _, b in run.store.records("decision_request")]
        assert set(gates) == {"A", "B", "CHECK-IN", "C"}
        if mode == "question":
            from run_report import report
            output, _ = report(slot)
            coached = json.loads(next(line.split(": ", 1)[1] for line in output.splitlines()
                                      if line.startswith("Planner-answered questions:")))
            assert any(c["question"]["Unresolved work"]["failure"] == "How can I check persistence in this scope?"
                       and "scoped Python check" in c["answer"]["guidance"] for c in coached)
            assert provider.calls.count("COACH") == 1
            assert (slot / "turn-unit-001").read_text() == "2"
            assert not run.store.records("unit_failure")
        if mode == "coach-facts":
            assert any(b["unit"] == unit_ref(run, 3) and b["next"] == "PROVING"
                       for _, _, b in run.store.records("transition"))
            observations = [b for _, _, b in run.store.records("observation") if b["unit"] == unit_ref(run, 3)]
            assert {b["purpose"] for b in observations} == {"T", "final"}
            assert any(b["purpose"] == "final" and b["exitCode"] == 0 for b in observations)
        if mode in ("unit-gap", "unit-rework"):
            assert (slot / "turn-unit-001").read_text() == "2"
            assert len(run.store.records("unit_gap")) == (2 if mode == "unit-gap" else 1)
            assert not any(b["errors"] for _, _, b in run.store.records("provider_reply"))
            checkin = next(b for _, _, b in run.store.records("decision_request") if b["gate"] == "CHECK-IN")
            assert bool(checkin["subject"]["open gaps"]) == (mode == "unit-gap")
        if mode == "see-proof-gap":
            assignment = next(b for _, _, b in run.store.records("unit_fixup") if b["owner"] == unit_ref(run, 2))
            assert "saved value label" in str(assignment["gaps"])
            assert run.store.unit(unit_ref(run, 1)) == unaffected
            assert any("observed value-002" in run.store.read(r)["stdout"]
                       for r in run.store.cursor().finalObservations)
        if mode in ("wave-fault", "flow-fault", "product-fault"):
            assert run.store.records("unit_fixup")
            assert (slot / "turn-unit-002").read_text() == ("2" if mode == "product-fault" else "1")
        if mode == "schema-fix":
            assert (slot / "turn-unit-001").read_text() == "2"
            assert any(b["errors"] for _, _, b in run.store.records("provider_reply"))
        if mode == "proof-failure":
            assert (slot / "turn-unit-001").read_text() == "2"
            assert any("actual failed product assertion" in b["stderr"]
                       for _, _, b in run.store.records("observation") if b["purpose"] == "P")
        if mode == "contract-fanout":
            document = plan.execution(run.store)
            agreement, = plan.select(document, "contract").values()
            assert agreement["provider"] == unit_ref(run, 1)
            assert agreement["consumers"] == [unit_ref(run, 3), unit_ref(run, 4), "guide"]
            assert all(run.store.unit(ref).recoveries == 0 for ref in (unit_ref(run, 3), unit_ref(run, 4)))
        if mode == "contracts":
            assert not run.store.records("unit_fixup")
            assert not any(b.get("next") == "REWORK" for _, _, b in run.store.records("transition"))
            import re
            from seldon.store import encode

            for _, _, question in run.store.records("question"):
                folder, _ = provider.native.prepare(question)
                prompt = (folder / "prompt.txt").read_text("utf-8")
                instructions = (folder / "instructions.txt").read_text("utf-8")
                facts = question["facts"]
                headings = ["Answer schema", "Example", *facts]
                sections = re.split(r"\n\n(?=(?:" + "|".join(map(re.escape, headings)) + r")\n)", prompt)
                stable = [instructions + "\n" + question["prompt"],
                          "Answer schema\n" + encode(question["schema"]),
                          "Example\n" + encode(question["examples"][0])]
                assert sections[:3] == stable
                assert set(sections) == {*stable, *(questions.labelled({k: v}) for k, v in facts.items())}
                if question["kind"] == "JUDGE" and "Contracts consumed" in facts:
                    assert prompt.index("\n\nContracts consumed\n") < prompt.index("\n\nObservations\n")
                if question["kind"] == "FLOW":
                    assert prompt.index("\n\nCrossed contracts\n") < prompt.index("\n\nFinal observations\n")
                assert json.loads((folder / "question.json").read_text("utf-8")) == question
        if mode == "review-gap":
            assert provider.calls.count("REVIEW") == 2
            b = next(b for _, _, b in run.store.records("decision_request") if b["gate"] == "B")
            assert b["subject"]["review"]["answer"]["gaps"]
        return records
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize(
    "mode,count",
    [
        ("normal", 1),
        ("question", 1),
        ("coach-facts", 3),
        ("unit-gap", 1),
        ("unit-rework", 1),
        ("review-gap", 1),
        ("schema-fix", 1),
        ("contracts", 3),
        ("contract-fanout", 4),
        ("parallel-contract", 2),
        ("wave-fault", 2),
        ("wave-gap", 2),
        ("see-proof", 2),
        ("see-proof-gap", 2),
        ("flow-fault", 2),
        ("product-fault", 2),
        ("proof-failure", 2),
        ("scope", 2),
        ("scale50", 50),
    ],
)
def test_thin_plan_golden(tmp_path, mode, count):
    """Real worker checks reach C through thin intent, local recovery and strict integrated reviews.

    The parallel-contract twin catches a guide waiting on product work or proving its
    walkthrough before integration; value-only sequential journeys miss that barrier.

    Success alone misses worker questions, persistent unit/plan gaps and final-only failures.
    The two-area contract journey catches serialized contract peers and rejected long agreements;
    ordinary journeys do not exercise provider-in-consumers normalization or simultaneous WORKING.
    Multi-unit areas catch consumer briefs lost when DETAIL omits redundant endpoints,
    and preserve one explicit provider when its area has multiple units. Single-unit cases miss this.
    Recorded contract questions protect stable-prefix ordering and lossless section rendering;
    scripted answers alone never inspect the prompt supplied to the native provider.
    A consumer's real JSON check needs its provider's sibling API agreement to reach C without
    repair; one-contract journeys miss that blind spot and unrelated units must not receive it.
    Backend/page briefs catch craft guidance leaking to peers without the visual commitment.
    Factual questions require COACH with execution facts; wave-owned gaps must integrate before repair.
    A real sibling read from an early unit catches running integrated acceptance before integration.
    Integrated JUDGE evidence and its verdict reaching FLOW/PRODUCT catch blind source judgment;
    a semantic gap on GREEN proof must rework its owner, even after interruption before fixup.
    Check-failure twins miss semantic gaps and replay of an already-recorded semantic reply.
    The persistent local-gap twin also requires final judges to see the actual negative finding.
    The report follows canonical requests and COACH guidance; provider-only assertions miss
    an unanswered question being reported as planner-answered or unknown usage becoming zero.
    Fifty units and reopen catch a hidden size ceiling or replay of already integrated work.
    """
    began = time.monotonic()
    journey(tmp_path / mode, mode, count)
    print(f"{mode}: {time.monotonic() - began:.2f}s")


@pytest.mark.fast
@pytest.mark.parametrize("route", ["brief", "coach", "gap"])
def test_quietbench_peer_agreements_journey(tmp_path, route, monkeypatch):
    """Replay Quiet Bench through B and its first backend WORK and COACH.

    The approved outline had the agreements but DETAIL had no peer units yet. Existing
    contract journeys already bind their providers, so they miss an outline-only agreement.
    """
    from seldon import decisions

    monkeypatch.delenv("SELDON_OFF", raising=False)
    fixture = json.loads((Path(__file__).parent / "fixtures/quietbench-c01-agreements.json").read_text(
        encoding="utf-8"))
    slot = tmp_path / "quietbench-agreements"
    seen, user_questions = [], []
    expected = {c["handle"]: c["text"] for c in fixture["r23"]["contracts"]
                if c["handle"] in ("simulation-run", "page-assets")}

    class ReceivedAgreement(BaseException):
        pass

    def agreements(facts):
        return {ref: contract["text"] for name, contracts in facts.items()
                if name in ("Contracts consumed", "Planned contracts")
                for ref, contract in contracts.items() if ref in expected}

    class QuietBench(Scripted):
        def invoke(self, q, started):
            strict_schema(q["schema"])
            kind, facts = q["kind"], q["facts"]
            if kind == "ARCHITECT" and "Architecture correction" in facts:
                message = facts["Architecture correction"]
                assert all(part in message for part in ("Expected", "got", "simulation-run",
                                                        "Define the fixed-filter settings", "Wave 1"))
                seen.append("architecture")
            if kind in ("ALIGN", "ARCHITECT", "DETAIL"):
                answer = fixture[{"ALIGN": "r12", "ARCHITECT": "r23", "DETAIL": "r60"}[kind]]
            elif kind == "WORK":
                if plan.GUIDE_PATH in facts["Write scope"]:
                    return super().invoke(q, started)
                seen.append("work")
                if route == "brief" and agreements(facts) == expected:
                    raise ReceivedAgreement
                if route == "coach" and "Direction: " + guidance in facts.get("Exact failure", ""):
                    raise ReceivedAgreement
                answer = fixture["r103"]
            elif kind == "COACH":
                seen.append("coach")
                answer = fixture["r111"]
                if agreements(facts) == expected:
                    answer = {"contract": {"resolution": "gap" if route == "gap" else "answer",
                              "text": "Define the fixed-filter settings in simulation-run." if route == "gap"
                                      else guidance}, "why": "Resolve the peer agreement in the plan."}
            else:
                return super().invoke(q, started)
            return json.dumps({"answer": answer}), None

    guidance = "Use " + expected["simulation-run"] + " " + expected["page-assets"]

    def user(item):
        if item["gate"] == "A":
            return {"request": item["request"], "replyId": "approved-skeleton", "selections": [
                {"groupId": "size", "optionId": "large"}, {"groupId": "round", "optionId": "done"}]}
        if item["gate"] == "UNIT":
            user_questions.append(item["ask"]["question"])
            raise decisions.Waiting()
        return approve(item)

    slot.mkdir()
    (slot / "mode").write_text("normal")
    (slot / "native.py").write_text(NATIVE, encoding="utf-8")
    provider = QuietBench(slot)
    run = Run(slot / "product", slot / "workers", fixture["dream"],
              provider.setup, provider, user, capacity=1)
    try:
        try:
            run.run()
        except ReceivedAgreement:
            pass
        assert not user_questions, user_questions
        assert not [b for _, _, b in run.store.records("decision_request") if b["gate"] == "UNIT"]
        assert seen == {"brief": ["work"], "coach": ["work", "coach", "work"],
                        "gap": ["work", "coach", "architecture", "work", "coach"]}[route]
        if route == "gap":
            assert run.store.cursor().state == "ARCHITECT"
            assert "The planner must settle it" in run.store.cursor().failure
    finally:
        run.close()


@pytest.mark.fast
def test_repeating_worker_question_journey(tmp_path):
    """Repeated handoff questions reach one user wait while siblings finish, then resume.

    The finite question journey misses the uncounted loop that caused T-184; the
    wave-gap journey never asks a question and cannot protect counted COACH returns.
    """
    from seldon.decisions import Waiting

    slot = tmp_path / "repeat-question"
    run, provider = prepare(slot, "repeat-question", 3)
    try:
        with pytest.raises(Waiting):
            while True:
                run.step()
        requests = [b for _, _, b in run.store.records("decision_request") if b["gate"] == "UNIT"]
        assert len(requests) == 1
        assert run.store.unit(unit_ref(run, 1)).state == "ASKED"
        assert all(run.store.unit(ref).state == "DONE" for ref in (unit_ref(run, 2), unit_ref(run, 3)))
        candidate = run.store.cursor().candidate
        run.close()
        run = Run.reopen(slot / "product", provider, approve)
        with pytest.raises(Waiting):
            run.step()
        assert run.store.cursor().candidate == candidate
        assert len([b for _, _, b in run.store.records("decision_request") if b["gate"] == "UNIT"]) == 1
        (slot / "mode").write_text("normal")
        run.user = lambda request: ("Try again with the saved-value check"
                                    if request["gate"] == "UNIT" else approve(request))
        finish(run)
        assert all((slot / f"turn-unit-{n:03}").read_text() == "1" for n in (2, 3))
    finally:
        run.close()


@pytest.mark.fast
def test_hermetic_golden(tmp_path):
    """Runtime writes cannot corrupt final source; read-only checks never exercise this isolation."""
    from test_proof import hermetic_journey

    hermetic_journey(tmp_path / "hermetic")


@pytest.mark.fast
def test_guide_walkthrough_journey(tmp_path):
    """A reader's never-exiting server fence cannot block C; value-only guides miss prose execution.

    The real kit walk also checks ordering, delivered sources and observations in PRODUCT.
    """
    from kit_journey import kit_journey

    kit_journey(tmp_path / "walkthrough", "server")


@pytest.mark.fast
@pytest.mark.parametrize("surface", ["browser", "broken-guide"])
def test_check_kit_golden(tmp_path, surface, monkeypatch):
    """Worker-authored real browser/server checks retain baseline helpers and clean descendants.

    Value-file journeys cannot expose absent browser skills, screenshots or server leaks.
    Unlinked checks and module commands catch declaration refusals hidden by explicit file commands.
    Guide observations catch local proof of a walkthrough that belongs to the integrated product.
    """
    from kit_journey import kit_journey

    if surface == "broken-guide":
        monkeypatch.setenv("SELDON_TEST_GUIDE_FAULT", str(tmp_path / "guide-fault"))
    kit_journey(tmp_path / "kit", surface)


@pytest.mark.fast
@pytest.mark.parametrize("outcome", ["done", "question", "fails"])
def test_defect_reaches_owner_journey(tmp_path, outcome):
    """A real failing peer check delivers its writer's correction to the named file owner.

    Existing wave faults repair their writer and miss lost discoveries, premature local
    proof and forced writer routing. The failed-fix twin protects the owner's recovery
    and durable user wait: an ineffective fix must never publish or loop forever.
    """
    from seldon.decisions import Waiting
    from seldon.frontdoor import state

    slot = tmp_path / "defect"
    run, provider = prepare(slot, "defect-" + outcome, 2)
    try:
        while run.store.cursor().state != "WAVE-VERIFY":
            run.step()
        assert run.store.unit(unit_ref(run, 1)).state == "DONE"
        original = gitwork.blobs(run.project, run.store.cursor().candidate, ["unit-001/**"])
        assert (run.project / "unit-002/peer.txt").read_text() == "broken"
        run.step()
        fixup, = [b for _, _, b in run.store.records("unit_fixup")]
        assert fixup["owner"] == unit_ref(run, 2)
        gap, = fixup["gaps"]
        assert gap["subject"] == unit_ref(run, 1) + ":check:1" and gap["units"] == [unit_ref(run, 2)]
        assert "Write correct into unit-002/peer.txt" in gap["correction"]
        assert "peer defect" in run.store.read(gap["source"])["stderr"]
        if outcome == "fails":
            with pytest.raises(Waiting):
                while True:
                    run.step()
            assert state(str(run.project))["waitingFor"]["gate"] == "UNIT"
            request, = [b for _, _, b in run.store.records("decision_request") if b["gate"] == "UNIT"]
            assert request["subject"]["unit"] == unit_ref(run, 2)
            assert run.store.unit(unit_ref(run, 1)).state == "DONE"
            candidate = run.store.cursor().candidate
            run.close()
            run = Run.reopen(slot / "product", provider, approve)
            with pytest.raises(Waiting):
                run.step()
            assert run.store.cursor().candidate == candidate
            assert run.store.read(run.store.cursor().decision) == request
            (slot / "mode").write_text("defect-done")
            run.user = lambda item: ("Try again: correct peer.txt" if item["gate"] == "UNIT" else approve(item))
        finish(run)
        assert (run.project / "unit-002/peer.txt").read_text() == "correct"
        assert gitwork.blobs(run.project, run.store.cursor().candidate, ["unit-001/**"]) == original
        if outcome != "fails":
            assert "COACH" not in provider.calls
            assert not any(b["gate"] == "UNIT" for _, _, b in run.store.records("decision_request"))
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("retire", [False, True])
def test_shortened_work_report_keeps_checks_at_c(tmp_path, retire):
    """ShelfLife's import-only repair must still run the previously passing interface check.

    Other repair journeys resend their whole check list and miss lost proofs, shifted check
    identities and incorrect FLOW links. The retirement twin confirms that omission and
    naming the check for retirement have different effects on real integrated execution.
    """
    slot = tmp_path / "check-identity"
    run, _ = prepare(slot, "wave-gap", 2)

    class ShelfLife(Scripted):
        def __call__(self, q):
            answer = json.loads(super().__call__(q))["answer"]
            if q["kind"] == "DETAIL" and q["facts"]["Area"]["handle"] == "area-2":
                answer["edits"] += [
                    {"kind": "feature", "targetHandle": "interface", "typedBody": {
                        "story": "Read the interface", "acceptance": "See the saved label"}},
                    {"kind": "requirement", "targetHandle": "label", "typedBody": {
                        "acceptance": "Show the saved label.", "features": ["interface"]}},
                ]
                answer["units"][0]["requirements"].append("label")
            return json.dumps({"answer": answer})

    run.provider = ShelfLife(slot, "wave-gap", 2)
    native = slot / "native.py"
    source = native.read_text()
    script = r"""
if name == 'unit-002':
    path = name + '/interface.py'
    if 'Existing checks' in facts:
        imported = answer['checks'][0]
        imported.pop('update', None)
        imported['requirements'] = [r for r, b in facts['Requirements'].items()
                                    if b['acceptance'] != 'Show the saved label.']
    if turn == 1:
        Path(name, 'label.txt').write_text('Saved items')
        Path(path).write_text("from pathlib import Path\n"
            "assert Path('unit-002/label.txt').read_text() == 'Saved items'\n"
            "print('observed interface label')\n")
        interface = {'purpose': 'Read the interface label', 'command': [sys.executable, '-B', path],
                     'expectedEvidence': 'The saved label is visible'}
        if 'Existing checks' in facts:
            interface['requirements'] = [r for r, b in facts['Requirements'].items()
                                         if b['acceptance'] == 'Show the saved label.']
        answer['checkPaths'].append(path)
        answer['checks'].insert(0, interface)
    elif RETIRE:
        handle = next(r for r, c in facts['Existing checks'].items()
                      if c['purpose'] == 'Read the interface label')
        imported['update'] = next(r for r, c in facts['Existing checks'].items()
                                  if c['command'] == imported['command'])
        imported['requirements'] = list(facts['Requirements'])
        check_path = Path(name, 'check.py')
        check_path.write_text(check_path.read_text()
                              + "assert Path('unit-002/label.txt').read_text() == 'Saved items'\n")
        if 'Exact corrections' not in facts:
            handle = 'interface.py'
        else:
            correction = str(facts['Exact corrections'])
            assert "Check 'interface.py' is not owned" in correction
            assert 'Use an Existing checks handle' in correction
        answer['checks'].append({'retire': handle})
    elif 'Existing checks' in facts:
        if 'Exact corrections' not in facts:
            imported['requirements'] = ['label.txt']
        else:
            correction = str(facts['Exact corrections'])
            assert "unassigned requirements ['label.txt']" in correction
            assert 'Use the handles under Requirements' in correction
"""
    native.write_text(source.replace("print(json.dumps({'type':'item.completed'",
        script.replace("RETIRE", repr(retire)) + "\nprint(json.dumps({'type':'item.completed'"), encoding="utf-8")
    try:
        while run.store.cursor().state != "WAVE-VERIFY":
            run.step()
        run.step()
        gap, = [b["gaps"][0] for _, _, b in run.store.records("unit_fixup")]
        first_commit = run.store.read(gap["source"])["commit"]
        first_interface, = [b for _, _, b in run.store.records("observation")
                            if b["commit"] == first_commit and b["purpose"] == "final"
                            and "observed interface label" in b["stdout"]]
        finish(run)
        final = [run.store.read(r) for r in run.store.cursor().finalObservations]
        imported, = [b for b in final if "unit-002/check.py" in b["command"]]
        interface = [b for b in final if "observed interface label" in b["stdout"]]
        assert bool(interface) is not retire
        assert imported["check"] == gap["subject"]
        if interface:
            assert interface[0]["check"] == first_interface["check"]
            label, = interface[0]["requirements"]
            saved, = imported["requirements"]
            assert label != saved
            declared = plan.select(run.store.plan(run.store.cursor().revision), "requirement")
            assert declared[label]["acceptance"] == "Show the saved label."
            assert declared[saved]["acceptance"] == "Running the reader displays saved value-002."
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("fault", ["criterion-gap", "area-uncertain", "outside-unit",
                                   "outside-contract", "resume-unavailable"])
def test_judge_once_golden(tmp_path, fault):
    """Protect area-to-criterion truth and real correction recovery through C.

    Single-criterion golden misses a lost second verdict or uncertainty promoted to success;
    traced-owner gaps miss routing to DONE peers outside the feature. Resume-only repairs
    miss a new session receiving a delta without the original contracts and requirements.
    """
    from seldon.providers import ResumeUnavailable
    from seldon import judgment

    slot = tmp_path / fault
    run, provider = prepare(slot, "contracts", 2)
    planted, resumed, fresh = [], [], []
    schemas = {}

    class Fault:
        def invoke(self, q, started):
            kind, facts = q["kind"], q["facts"]
            if kind == "WORK":
                if "Exact failure" in facts:
                    if q["execution"].get("session"):
                        assert {"Exact failure", "Write scope", "Correction owners",
                                "Correction observations", "Existing checks"} <= facts.keys()
                        assert ("values" in facts.get("Contracts provided", {})
                                or "values" in facts.get("Contracts consumed", {}))
                        assert facts["Correction observations"]
                        resumed.append(q)
                        if fault == "resume-unavailable":
                            raise ResumeUnavailable("Recorded session no longer available", False, True)
                    else:
                        assert facts["Requirements"] and facts["Execution facts"]
                        assert facts["Contract authority"]
                        fresh.append(q)
                return provider.invoke(q, started)
            answer = json.loads(provider(q))["answer"]
            if kind == "ALIGN":
                answer["quality"].append({"criterion": "Readable saved label",
                                          "verification": "Observe the saved value label"})
            if kind == "ARCHITECT":
                for area in answer["areas"]:
                    area["commitments"].append("quality:2")
                if fault == "outside-contract":
                    answer["areas"].append({"handle": "area-3", "responsibility": "Read third value",
                        "scope": ["unit-003/**"], "commitments": ["scopeIn:1", "quality:1", "quality:2"]})
                    answer["contracts"][0]["consumers"] = ["area-3"]
            if kind in ("FLOW", "PRODUCT"):
                if kind in schemas:
                    assert schemas[kind] == q["schema"]
                schemas[kind] = q["schema"]
                document = plan.execution(run.store)
                if not planted:
                    if kind == "PRODUCT" and fault in ("criterion-gap", "area-uncertain", "resume-unavailable") and all(t in plan.select(document, "criterion") for t in q["targets"]) and unit_ref(run, 1) in judgment.owners(document, kind, q["targets"][0]):
                        area = plan.select(document, "criterion")[q["targets"][0]]["area"]
                        criteria = {r for r,b in plan.select(document, "criterion").items() if b["area"] == area}
                        assert set(facts["Criteria"]) == set(q["targets"]) == criteria
                        assert len(criteria) == 2
                        target = q["targets"][-1]
                        owner = unit_ref(run, 1)
                    elif kind == "FLOW" and fault in ("outside-unit", "outside-contract") and all("value-002" in b["acceptance"] for b in facts["Requirements"].values()):
                        target = q["targets"][0]
                        assert unit_ref(run, 1) not in facts["Correction owners"]
                        owner = (next(iter(plan.select(document, "contract")))
                                 if fault == "outside-contract" else unit_ref(run, 1))
                        if fault == "outside-contract":
                            assert owner not in facts["Crossed contracts"]
                    else:
                        return json.dumps({"answer": answer}), None
                    planted.append((kind, target, owner))
                    if fault == "area-uncertain":
                        answer = {"verdict": "uncertain", "neededFacts": ["Visual observation unavailable"], "why": "Need the missing observation"}
                    else:
                        answer.update(verdict="gap", gaps=[{"severity": "must-fix", "target": target, "owners": [owner],
                                      "correction": "Exercise the saved value label"}])
            return json.dumps({"answer": answer}), None

    run.provider = Fault()
    try:
        finish(run)
        assert planted
        document = plan.execution(run.store)
        rows = {run.store.read(r)["subject"]: run.store.read(r) for r in run.store.cursor().qualityVerdicts}
        assert set(rows) == set(judgment.subjects(document, "PRODUCT"))
        if fault == "area-uncertain":
            target = planted[0][1]
            area = plan.select(document, "criterion")[target]["area"]
            for r,b in plan.select(document, "criterion").items():
                if b["area"] == area:
                    assert rows[r]["answer"]["verdict"] == "not judged"
                    assert "Visual observation unavailable" in judgment.not_judged(run.store)[r]
            assert not run.store.records("outcome")[-1][2]["verification_complete"]
        elif fault == "outside-contract":
            # A contract owner is the planner's agreement to correct, never its provider's fixup.
            gaps = [b["gap"] for _, _, b in run.store.records("architecture_gap")]
            assert len(gaps) == 1 and f"contract {planted[0][2]} disagree" in gaps[0]
            assert not run.store.records("unit_fixup")
            assert all(r["answer"]["verdict"] == "satisfied" for r in rows.values())
        else:
            assignments = [b for _,_,b in run.store.records("unit_fixup")]
            assert {b["owner"] for b in assignments} == {planted[0][2]}
            assert resumed
            assert all(r["answer"]["verdict"] == "satisfied" for r in rows.values())
            if fault == "resume-unavailable":
                assert fresh and run.store.records("resume_unavailable")
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("steering", ["partial", "resent"])
def test_progressive_wave_plan_journey(tmp_path, steering):
    """Preserve built ownership while detailing new commitments and changed consumers.

    One-wave journeys miss renumbered worker rows, lost requirements, full-horizon DETAIL,
    stale contract handles and B expiring when the approved vision has not changed.
    This planning journey supplies the lead-owned next-wave transition at its boundary.
    """
    from dataclasses import replace
    from seldon import architecture, detail, inference

    slot = tmp_path / steering
    run, provider = prepare(slot, "contracts", 3)
    original = provider.__call__
    detailed = []
    interruptions = []

    class Interrupted(BaseException):
        pass

    def invoke(q, started):
        if q["kind"] == "WORK":
            return provider.invoke(q, started)
        answer = json.loads(original(q))["answer"]
        if q["kind"] == "ALIGN":
            answer["scopeIn"].append("Export saved values")
        elif q["kind"] == "ARCHITECT":
            answer["areas"][0]["commitments"].append("scopeIn:2")
            answer["areas"][1]["scope"].append("unit-004/**")
            answer["contracts"][0]["text"] = "Saved values transport; export shape comes in wave 2."
            answer["contracts"].append({"handle": "quiet", "kind": "data rule", "provider": "area-3",
                                        "consumers": [], "text": "Third value remains UTF-8."})
            answer["waves"].append({"goal": "Export saved values", "commitments": []})
            # Code assigns the omitted commitment to the last wave.
        elif q["kind"] == "DETAIL":
            detailed.append(q)
            if q["facts"]["Current wave"]["goal"] == "Export saved values":
                for edit in answer["edits"]:
                    if edit["kind"] == "requirement":
                        edit["typedBody"]["acceptance"] += " Export preserves the saved value."
                if q["facts"]["Area"]["handle"] == "area-2":
                    assert "export: UTF-8 CSV" in q["facts"]["Contracts"]["values"]["text"]
                    canonical = run.store.plan(run.store.cursor().revision)
                    assert "export: UTF-8 CSV" in plan.select(canonical, "contract")["values"]["text"]
                    assert "items" not in q["facts"]["Contracts"]
                    assert "quiet" not in q["facts"]["Contracts"]
                    exists, = q["facts"]["What exists here"].values()
                    assert exists["scope"] == ["unit-002/**"]
                    assert "saved value-002" in str(exists["acceptance"])
                    assert exists["last worker summary"] == "Wrote and exercised the acceptance"
                    if steering == "partial" and not interruptions:
                        interruptions.append(True)
                        raise Interrupted()
            else:
                answer["edits"] = [e for e in answer["edits"] if e["targetHandle"] not in ("f4", "r4")]
                answer["units"] = [u for u in answer["units"] if u["scope"] != ["unit-004/**"]]
        return json.dumps({"answer": answer}), None

    from types import SimpleNamespace
    run.provider = SimpleNamespace(invoke=invoke)
    try:
        while run.store.cursor().state != "CHECK-IN":
            run.step()
        run.step()  # Post the check-in and allow planning to proceed.
        run.step()  # Consume the real check-in words before the planning-only boundary below.
        cursor = run.store.cursor()
        before = run.store.plan(cursor.revision)
        names = {r: deepcopy(u) for r, u in plan.select(before, "unit").items()}
        assert before["architecture"]["waves"][1]["commitments"] == ["scopeIn:2"]
        built = plan.built(run.store)
        assert set(built["Finished wave"]) == set(names)
        assert built["User check-in"] == run.store.read(run.store.read(cursor.checkin)["words"])["words"]
        assert all("summary" in row and "discoveries" in row and "open gaps" in row
                   for row in built["Finished wave"].values())
        previous = before["architecture"]
        changed = {**previous["contracts"][0], "text": "Saved values and export: UTF-8 CSV, name,value header."}
        partial = {"areas": [], "contracts": [changed], "decisions": [], "waves": [],
                   "why": "Export the built values"}
        resent = {**deepcopy(previous), "contracts": [changed, *deepcopy(previous["contracts"][1:])],
                  "why": partial["why"]}
        merged = architecture.merge(previous, partial)
        assert merged == architecture.merge(previous, resent)
        architecture.outline(merged, before["nodes"]["vision"]["body"])
        scope = {"scope": "Include live hardware", "why": "Needs user agreement"}
        assert architecture.merge(previous, scope) is scope
        brief = questions.architect_brief(before["nodes"]["vision"]["body"],
                                           questions.user_words(run.store), built=built)
        _, answer = inference.call(run.store, lambda q: json.dumps({"answer":
            partial if steering == "partial" else resent}), brief,
            check=lambda a: architecture.outline(architecture.merge(previous, a),
                                                 before["nodes"]["vision"]["body"]))
        document = {**deepcopy(before), "architecture": architecture.merge(previous, answer)}
        revision = run.store.revision(document, cursor.revision)
        run.store.cursor(replace(cursor, state="DETAIL", wave=2, revision=revision))
        detailed.clear()
        try:
            detail.step(run.store, run.provider, run.capacity)
        except Interrupted:
            planner = run.provider
            run.close()
            run = Run.reopen(slot / "product", planner, approve)
            detail.step(run.store, run.provider, run.capacity)
        after = run.store.plan(run.store.cursor().revision)
        assert {q["facts"]["Area"]["handle"] for q in detailed} == {"area-1", "area-2"}
        assert plan.approved(run.store)
        units = plan.select(after, "unit")
        assert set(names) < set(units)
        for ref, old in names.items():
            assert units[ref]["scope"] == old["scope"]
            assert set(old["requirements"]) <= set(units[ref]["requirements"])
            for req in old["requirements"]:
                assert after["nodes"][req] == before["nodes"][req]
        assert units[unit_ref(run, 3)] == names[unit_ref(run, 3)]
        assert len(units[unit_ref(run, 1)]["requirements"]) > len(names[unit_ref(run, 1)]["requirements"])
        assert unit_ref(run, 4).startswith("2.area-2.")
        assert "quiet" not in architecture.contracts_for(after, "guide")
        assert plan.select(after, "contract")["values"]["text"] == changed["text"]
        vision = deepcopy(before["nodes"]["vision"]["body"])
        vision["scopeIn"].append("Include live hardware")
        vref = run.store.record("answer", {"answer": vision})
        run.store.cursor(replace(run.store.cursor(), vision=vref))
        assert not plan.approved(run.store)
    finally:
        run.close()



@pytest.mark.fast
@pytest.mark.parametrize("fail_through", [2, 3, "live"])
def test_planner_first_recovery_journey(tmp_path, fail_through):
    """Real failing checks get planner direction before spending the user's time.

    A persistent failure keeps its request across restart; an independent reader finishes
    while a dependent waits. Picking the proposal must resume without DECIDE. The existing
    worker-question twin misses failed checks, planner-first repair and label selection.
    """
    from seldon.decisions import Waiting

    slot = tmp_path / "planner-first"
    run, original = prepare(slot, count=3)
    native = slot / "native.py"
    failures = 3 if fail_through == "live" else fail_through
    native.write_text(native.read_text().replace("    Path(name, 'check.py').write_text(source)",
        f"    if number == 1 and turn <= {failures}:\n"
        "        source += \"raise AssertionError('reader unavailable')\\n\"\n"
        "    Path(name, 'check.py').write_text(source)"))

    if fail_through == "live":
        native.write_text(native.read_text().replace("    value = 'value-'",
            "    if number == 2:\n"
            "        while not (slot/'question-shown').exists(): time.sleep(0.05)\n"
            "    value = 'value-'"))

    class Planner(Scripted):
        def __call__(self, q):
            if q["kind"] == "DECIDE":
                raise AssertionError("A displayed label needs no interpreter")
            if q["kind"] == "FLOW" and not q["facts"]["Final observations"]:
                assert q["facts"]["Left out by the user"]
                assert all(v["why"] == "left out by the user" for v in q["facts"]["Source judgment"])
                return json.dumps({"answer": {"verdict": "satisfied", "gaps": [], "why": "left out by the user"}})
            raw = super().__call__(q)
            if q["kind"] == "DETAIL":
                body = json.loads(raw)
                body["answer"]["units"][2]["dependencies"] = [1]
                return json.dumps(body)
            if q["kind"] == "COACH":
                assert run.store.integration_lock.acquire(blocking=False), "COACH holds integration lock"
                run.store.integration_lock.release()
                facts = q["facts"]
                assert "reader unavailable" in str(facts["Unresolved work"])
                assert facts["Requirements"] and facts["Vision"]
                return json.dumps({"answer": {"guidance": "Read the saved value and repair the reader",
                                              "why": "Preserve the accepted persistence"}})
            return raw

    provider = Planner(slot, count=3)
    run.provider = provider
    if fail_through == "live":
        def answer_when_ready(item):
            if item["gate"] != "UNIT":
                return approve(item)
            assert run.store.unit(unit_ref(run, 3)).state == "WAITING"
            (slot / "question-shown").touch()
            if run.store.unit(unit_ref(run, 2)).state != "DONE":
                raise Waiting()
            return item["ask"]["options"][0]["label"]
        run.user = answer_when_ready
    try:
        if fail_through == 3:
            with pytest.raises(Waiting):
                while True:
                    run.step()
            request = run.store.cursor().decision
            item = run.store.read(request)
            assert item["ask"]["options"][0]["means"] == "Read the saved value and repair the reader"
            assert run.store.unit(unit_ref(run, 2)).state == "DONE"
            assert run.store.unit(unit_ref(run, 3)).state == "WAITING"
            run.close()
            run = Run.reopen(slot / "product", provider, approve)
            with pytest.raises(Waiting):
                run.step()
            assert run.store.cursor().decision == request and run.store.read(request) == item
            run.user = lambda item: ("  TRY THE PLANNER'S NEW APPROACH  "
                                    if item["gate"] == "UNIT" else approve(item))
        finish(run)
        assert run.store.records("planner_answer")
        if fail_through == 2:
            assert not any(b["gate"] == "UNIT" for _, _, b in run.store.records("decision_request"))
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("steer_gate", ["CHECK-IN", "C"])
def test_options_and_unclear_golden(tmp_path, steer_gate):
    """Unclear words cannot approve A or C; a visible option can, without a model.

    Scripted approvals never exercise interpretation, alternatives or acceptance ambiguity.
    The scripted dict path is retained while option labels and own words use real requests.
    """
    from seldon import decisions

    slot = tmp_path / "options"
    from wave_loop_journey import prepared, Waves
    run, original = prepared(slot, False)

    class Interpreter(Waves):
        def __call__(self, q):
            if q["kind"] == "DECIDE":
                if q["facts"]["User's words"] == "Keep the value visible":
                    return json.dumps({"answer": {"choice": "Other", "why": "A requested improvement"}})
                return json.dumps({"answer": {"choice": "Unclear", "why": "Please choose an option or give a direction"}})
            return super().__call__(q)

    provider = Interpreter(slot, "normal", 3)
    run.provider = provider
    try:
        run.step()
        run.user = lambda item: "perhaps, what?"
        with pytest.raises(decisions.Waiting):
            run.step()
        ref = run.store.cursor().decision
        assert not run.store.records("approval")
        assert run.store.cursor().failure and run.store.cursor().state == "A"
        item = run.store.read(ref)
        assert item["ask"]["options"][1]["label"] == VISION["alternatives"][0]["label"]
        run.close()
        run = Run.reopen(slot / "product", provider, lambda item: "Show all values")
        assert run.store.cursor().decision == ref and run.store.read(ref) == item
        run.step()
        assert run.store.cursor().state == "ALIGN"
        assert VISION["alternatives"][0]["means"] in run.store.read(run.store.cursor().requestedChange)["words"]
        run.user = lambda item: item["ask"]["options"][0]["label"]
        if steer_gate == "CHECK-IN":
            while run.store.cursor().state != "CHECK-IN":
                run.step()
            run.user = lambda item: "Keep the value visible"
            run.step()  # Post the nonblocking check-in.
            run.step()  # Interpret its answer while planning can proceed.
            assert run.store.cursor().state == "ARCHITECT"
            assert run.store.read(run.store.cursor().requestedChange)["words"] == "Keep the value visible"
            run.user = lambda item: item["ask"]["options"][0]["label"]
        while run.store.cursor().state != "C":
            run.step()
        assert run.store.cursor().state == "C"
        run.user = lambda item: "perhaps, what?"
        prior = list(run.store.records("approval"))
        with pytest.raises(decisions.Waiting):
            run.step()
        ref = run.store.cursor().decision
        assert run.store.records("approval") == prior and not run.store.records("outcome")
        with pytest.raises(decisions.Waiting):
            run.step()
        assert run.store.cursor().decision == ref
        if steer_gate == "C":
            run.user = lambda item: "Keep the value visible"
            run.step()
            assert run.store.cursor().state == "ARCHITECT" and not run.store.records("outcome")
            assert run.store.read(run.store.cursor().requestedChange)["words"] == "Keep the value visible"
        run.user = lambda item: item["ask"]["options"][0]["label"]
        finish(run)
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("kept_gap", [False, True])
def test_planner_asks_and_drop_journey(tmp_path, kept_gap):
    """A planner's open choice can omit a blocked branch while independent work finishes.

    Repair twins never drop a unit, so they miss transitive dependents being stranded or
    starting after omission. A real missing output label still repairs its kept owner;
    omission must not erase accepted work or bypass C judgments.
    """
    from seldon.decisions import Waiting

    slot = tmp_path / "drop"
    run, _ = prepare(slot, "repeat-question", 3)

    class Planner(Scripted):
        def __call__(self, q):
            if q["kind"] == "DECIDE":
                raise AssertionError("Displayed choices need no interpretation")
            if q["kind"] == "COACH":
                return json.dumps({"answer": {"ask": {"question": "Which reader should we deliver?",
                    "options": [{"label": "Keep the named reader", "means": "Repair named reads"},
                                {"label": "Use a list", "means": "Deliver a list instead"}]},
                    "why": "The user must choose the scope"}})
            if q["kind"] == "PRODUCT" and any("unlabelled value-002" in o["stdout"]
                    for o in q["facts"]["Final observations"]):
                return json.dumps({"answer": {"verdict": "gap", "gaps": [{"severity": "must-fix",
                    "target": q["targets"][0], "owners": [unit_ref(run, 2)],
                    "correction": "Restore the saved value label"}], "why": "Kept output loses its label"}})
            if q["kind"] == "FLOW" and not q["facts"]["Final observations"]:
                assert q["facts"]["Left out by the user"]
                assert all(v["why"] == "left out by the user" for v in q["facts"]["Source judgment"])
                return json.dumps({"answer": {"verdict": "satisfied", "gaps": [], "why": "left out by the user"}})
            raw = super().__call__(q)
            if q["kind"] == "DETAIL":
                body = json.loads(raw)
                body["answer"]["units"][2]["dependencies"] = [1]
                return json.dumps(body)
            return raw

    provider = Planner(slot, "repeat-question", 3)
    run.provider = provider
    native = (slot / "native.py").read_text()
    start, end = native.index("elif name == 'docs':"), native.index("else:\n    Path(name).mkdir", native.index("elif name == 'docs':"))
    native = native[:start] + native[start:end].replace("unit-001", "unit-002").replace("value-001", "value-002") + native[end:]
    if kept_gap:
        native = native.replace("    Path(name, 'check.py').write_text(source)",
            "    if name == 'unit-002' and turn == 1: source = source.replace('observed value-002', 'unlabelled value-002')\n"
            "    Path(name, 'check.py').write_text(source)")
    (slot / "native.py").write_text(native)
    try:
        with pytest.raises(Waiting):
            while True:
                run.step()
        request = run.store.cursor().decision
        item = run.store.read(request)
        assert item["ask"]["question"] == "Which reader should we deliver?"
        assert item["ask"]["options"][0]["label"] == "Keep the named reader"
        assert run.store.unit(unit_ref(run, 2)).state == "DONE"
        run.user = lambda item: "Leave this out"
        run.step()
        assert run.store.cursor().state == "WAVE-VERIFY"
        assert all(run.store.unit(r).state == "DROPPED" for r in (unit_ref(run, 1), unit_ref(run, 3)))
        assert run.store.unit(unit_ref(run, 2)).state == "DONE"
        assert not (slot / "turn-unit-003").exists()
        assert any(b["request"] == request and b["effect"] == "drop"
                   for _, _, b in run.store.records("approval"))
        run.user = approve
        finish(run)
        c = [b for _, _, b in run.store.records("decision_request") if b["gate"] == "C"][-1]
        assert set(c["subject"]["leftOut"]) == {unit_ref(run, 1), unit_ref(run, 3)}
        assert all(b["reason"] == "left out by the user" for b in c["subject"]["leftOut"].values())
        assert (slot / "turn-unit-002").read_text() == ("2" if kept_gap else "1")
    finally:
        run.close()


@pytest.mark.fast
def test_step_fault_and_waiver_journey(tmp_path):
    """An identical invocation fault after planner guidance recommends a resumable Stop.

    Worker-failure twins miss controller faults that encourage endless user retries,
    and message-only records hide the owning source location from store triage.
    Reopen and a later successful call must retain the decision and allow waived check-ins.
    """
    from seldon import decisions

    slot = tmp_path / "fault"
    run, _ = prepare(slot)

    class Fault(Scripted):
        fixed = False
        def __call__(self, q):
            if q["kind"] == "ARCHITECT" and not self.fixed:
                receipt = slot / f"failure-{time.time_ns()}.json"
                receipt.write_text('{"cleanupVerified": true}')
                raise ProviderFailure("Codex invocation failed: 400 Invalid schema for response_format "
                    "'codex_output_schema': 'schema.properties.answer.anyOf.0.required' is required "
                    "to include every key in properties. Missing 'areas'.", True, True, str(receipt))
            assert q["kind"] != "DECIDE"
            return super().__call__(q)

    provider = Fault(slot)
    run.provider = provider
    def user(item):
        if "step" in item["subject"]:
            raise decisions.Waiting()
        return approve(item)
    run.user = user
    try:
        run.run()
        item = run.store.read(run.store.cursor().decision)
        assert item["subject"]["step"] == "ARCHITECT"
        failures = [body for _, _, body in run.store.records("step_failure")]
        assert failures and all(body["exceptionType"] == "ProviderFailure" for body in failures)
        assert all(body["failure"] == "ProviderFailure: " + body["message"] for body in failures)
        assert all(body["location"].startswith("src/seldon/inference.py:") and
                   int(body["location"].rsplit(":", 1)[1].split()[0]) > 0 for body in failures)
        assert run.store.records("planner_answer")
        assert "Seldon's fault" in item["ask"]["question"]
        assert "recommend Stop" in item["ask"]["question"]
        assert "400 Invalid schema" in str(item["subject"]["tried"])
        assert [o["label"] for o in item["ask"]["options"]] == ["Stop", "Try again"]
        assert decisions.holds(run.store)
        request = run.store.cursor().decision
        run.close()
        run = Run.reopen(slot / "product", provider, lambda item: "Stop")
        run.run()
        assert run.store.cursor().state == "ARCHITECT" and run.store.cursor().decision == request
        run.close()
        provider.fixed = True
        run = Run.reopen(slot / "product", provider,
            lambda item: "Try again" if "step" in item["subject"] else approve(item))
        while run.store.cursor().state != "B":
            run.step()
        run.user = lambda item: "Build it and do not stop at check-ins"
        run.step()
        while run.store.cursor().state != "CHECK-IN":
            run.step()
        decisions.request(run.store, "CHECK-IN", {"commit": run.store.cursor().candidate})
        assert run.store.cursor().waived and run.store.cursor().decision is None
        approval = run.store.read(run.store.cursor().checkin)
        assert run.store.read(approval["words"])["words"] == "waived by the user"
        assert run.store.read(approval["request"])["ask"]["options"]
        assert not decisions.holds(run.store)
    finally:
        run.close()


@pytest.mark.fast
def test_planning_gap_coaching_journey(tmp_path):
    """A real repeated DETAIL gap consumes planner guidance, then resumes on a user choice.

    Worker coaching cannot catch lost planning guidance or reuse of the rejected area answer.
    The success path goes through recovery.planning, the recorded approval and DETAIL's consumer.
    """
    from seldon import decisions

    slot = tmp_path / "planning-gap"
    run, _ = prepare(slot)

    class Planner(Scripted):
        def __call__(self, q):
            if q["kind"] == "DECIDE":
                raise AssertionError("A displayed choice needs no DECIDE")
            if q["kind"] == "COACH":
                return json.dumps({"answer": {"guidance": "Use the saved text contract",
                                              "why": "Keep the agreed reader"}})
            if q["kind"] == "DETAIL":
                words = str(q["facts"]["User's words"])
                if "Try the planner's new approach" not in words:
                    return json.dumps({"answer": {"verdict": "contract_gap",
                        "need": "Which representation should the reader consume?", "why": "Need a direction"}})
                assert "Use the saved text contract" in words
            return super().__call__(q)

    provider = Planner(slot)
    run.provider = provider
    try:
        while True:
            try:
                run.step()
            except decisions.Waiting:
                if run.store.cursor().decision and "ask" in run.store.read(run.store.cursor().decision):
                    break
        assert run.store.records("planner_answer")
        run.user = lambda item: (item["ask"]["options"][0]["label"]
                                if "step" in item["subject"] else approve(item))
        finish(run)
    finally:
        run.close()



@pytest.mark.fast
@pytest.mark.parametrize("missing", [False, True])
def test_cross_area_commitment_golden(tmp_path, missing):
    """Catch a promise lost between passing features, absent from area-only journeys.

    The real guide reads the carried path; only its consumer owner repairs a missing path.
    The clean twin reaches C without rework and C retains each accepted commitment once.
    """
    run, provider = prepare(tmp_path / "commitment", "contracts", 2)
    native = NATIVE.replace(
        "Path(name, 'value.txt').write_text(value)",
        "Path(name, 'value.txt').write_text(value)\n"
        "    if name == 'unit-002':\n"
        + ("        if turn > 1: Path(name, 'carried.txt').write_text('carried value-001')\n"
           if missing else "        Path(name, 'carried.txt').write_text('carried value-001')\n"))
    native = native.replace(
        "print('observed value-001')\\n",
        "print('observed value-001')\\nprint(Path('unit-002/carried.txt').read_text() "
        "if Path('unit-002/carried.txt').exists() else 'path missing')\\n")
    (provider.slot / "native.py").write_text(native, encoding="utf-8")
    checked, repairs = [], []

    class Commitment:
        def invoke(self, q, started):
            kind, facts = q["kind"], q["facts"]
            if kind == "WORK":
                if "Exact failure" in facts:
                    repairs.append(facts["Write scope"])
                return provider.invoke(q, started)
            answer = json.loads(provider(q))["answer"]
            if kind == "ALIGN":
                answer["scopeIn"] = ["Carry the saved value from producer through consumer"]
                answer["improvements"] = ["Keep the carried value visible in the walkthrough"]
            elif kind == "ARCHITECT":
                for area in answer["areas"]:
                    area["commitments"].append("improvements:1")
                answer["waves"][0]["commitments"].append("improvements:1")
            elif kind == "PRODUCT" and "Feature verdicts" in facts:
                assert {row["acceptance"] for row in facts["Feature verdicts"]} == {
                    "Observe value-001", "Observe value-002"}
                assert all(row["answer"]["verdict"] == "satisfied" for row in facts["Feature verdicts"])
                assert "Product files" not in facts and "Check sources" not in facts
                guide = [o for o in facts["Final observations"] if o["check"].startswith("guide:")]
                assert guide
                carried = any("carried value-001" in o["stdout"] for o in guide)
                checked.append((q["targets"][0], carried))
                if not carried:
                    answer.update(verdict="gap", gaps=[{"severity": "must-fix", "target": q["targets"][0],
                        "owners": [unit_ref(run, 2)], "correction": "Carry value-001 through unit-002/carried.txt"}])
            return json.dumps({"answer": answer}), None

    run.provider = Commitment()
    try:
        finish(run)
        assert {s for s, ok in checked if ok} == {"scopeIn:1", "improvements:1"}
        assert any(not ok for _, ok in checked) == missing
        assert bool(repairs) == missing
        assert all(scope == ["unit-002/**"] for scope in repairs)
        rows = [run.store.read(r) for r in run.store.cursor().qualityVerdicts]
        assert [r["subject"] for r in rows].count("scopeIn:1") == 1
        assert [r["subject"] for r in rows].count("improvements:1") == 1
        assert all(r["answer"]["verdict"] == "satisfied" for r in rows)
    finally:
        run.close()


@pytest.mark.fast
def test_saved_area_order_does_not_reassign_review_or_validation_journey(tmp_path):
    """A reopened ShelfLife plan must brief the named owner after outline reordering.

    Ordered scripted plans and promises spanning every area can hide an owner swap.
    Replay u01's saved plan and worker declarations through both question producers.
    """
    from seldon import judgment, plan_review
    from seldon.store import RunRow, Store, UnitRow

    fixture = json.loads((Path(__file__).parent / "fixtures/shelflife-u01-area-join.json").read_text())
    areas = fixture["architecture"]["areas"]
    expected_subjects = {
        "backend": ("scopeIn:1 scopeIn:2 scopeIn:3 scopeIn:4 scopeIn:5 scopeIn:6 scopeIn:11", "quality:3"),
        "interface": ("scopeIn:7 scopeIn:8 improvements:1", "quality:1 quality:2 quality:5"),
        "import": ("scopeIn:9 scopeIn:10 improvements:2", "quality:4"),
    }
    declarations = {row["unit"]: row for row in fixture["workerChecks"]}
    expected_review, expected_validation = {}, {}
    guide_checks = {"guide:check:1"}
    for area, (commitments, quality) in expected_subjects.items():
        unit = f"1.{area}.1"
        local_checks = {f"{unit}:check:{i}" for i, _ in enumerate(declarations[unit]["checks"], 1)}
        expected_review[area] = ({unit}, set((commitments + " " + quality).split()))
        for commitment in commitments.split():
            checks = local_checks | guide_checks
            expected_validation[commitment] = ({unit}, checks, checks)
        for criterion in quality.split():
            check_ids = local_checks | (guide_checks if area == "backend" else set())
            expected_validation[f"quality:{int(criterion.split(':')[1]):03}:{area}"] = (
                {unit}, check_ids, check_ids)
    expected_validation["tryItGuide"] = (set(), guide_checks, guide_checks)

    actual = {}
    for name, order in (("recorded", areas), ("permuted", areas[1:] + areas[:1])):
        project = tmp_path / name
        project.mkdir()
        gitwork.git(project, "init", "--quiet")
        guide = gitwork.git(project, "hash-object", "-w", "--stdin", data=b"Open the ShelfLife walkthrough.\n")
        candidate, = gitwork.import_commits(project, None, [[(plan.GUIDE_PATH, "100644", guide)]])
        document = {"architecture": {"areas": order}, "nodes": fixture["nodes"]}
        store = Store(project)
        try:
            revision = store.revision(document)
            kickoff = store.record("kickoff", {"setup": {}, "capacity": 3, "words": "Build ShelfLife"})
            cursor = RunRow(state="REVIEW", revision=revision, kickoff=kickoff, candidate=candidate)
            store.cursor(cursor)
            store.close()
            store = Store(project)
            provider = Scripted(project)
            plan_review.step(store, provider, 3)
            assert store.cursor().state == "B"

            # u01 stopped before final validation. Supply candidate-bound observations for
            # its saved check declarations so this replay tests routing, not product proof.
            for ref, declaration in declarations.items():
                store.record("worker_checks", declaration)
                evidence = {"scopeTree": candidate, "commit": candidate}
                if ref != "guide":
                    evidence["answer"] = {"verdict": "satisfied", "gaps": []}
                verdict = store.record("unit_verdict", evidence)
                store.unit(ref, UnitRow(state="DONE", judgment=verdict))
            observations = [store.record("observation", {
                "commit": candidate, "scopeTree": candidate, "unit": check["owner"], "check": ref,
                "requirements": check["requirements"], "purpose": "final", "command": check["command"],
                "exitCode": 0, "stdout": ref, "stderr": "", "cwd": str(project), "evidenceFiles": [],
                "executionStarted": True, "cleanupVerified": True, "timedOut": False,
            }) for ref, check in plan.select(plan.execution(store), "check").items()]
            from dataclasses import replace
            store.cursor(replace(cursor, state="PRODUCT-JUDGE", finalCommit=candidate, finalObservations=observations))
            judgment.step(store, provider)
            assert store.cursor().state == "C"

            review, validation = {}, {}
            for _, _, question in store.records("question"):
                facts = question["facts"]
                if question["kind"] == "REVIEW":
                    review[facts["Review subject"]] = (set(facts["Units"]), set(facts["Accepted commitments"]))
                elif question["kind"] == "PRODUCT":
                    for subject in facts["Criteria"]:
                        validation[subject] = ({row["unit"] for row in facts["Source judgment"]},
                                               set(facts["Checks"]),
                                               {row["check"] for row in facts["Final observations"]})
            actual[name] = {"REVIEW": review, "PRODUCT": validation}
        finally:
            store.close()
    expected = {"REVIEW": expected_review, "PRODUCT": expected_validation}
    assert actual == {"recorded": expected, "permuted": expected}


@pytest.mark.fast
def test_no_check_final_judgment_journey(tmp_path):
    """Retain a source-judged unit without final observations through the C presentation.

    Existing workers always declare checks, hiding scope-tree lookup and lost FLOW evidence.
    Full C eligibility remains the protected run.py owner's integration responsibility.
    """
    from seldon import judgment
    run, provider = prepare(tmp_path / "no-check", count=2)
    native = NATIVE.replace(
        "print(json.dumps({'type':'item.completed'",
        "if name == 'unit-002': answer.update(checks=[], checkPaths=[])\n"
        "print(json.dumps({'type':'item.completed'")
    (provider.slot / "native.py").write_text(native, encoding="utf-8")
    source, seen = [], []

    class NoCheck:
        def invoke(self, q, started):
            if q["kind"] == "WORK":
                return provider.invoke(q, started)
            if q["kind"] == "JUDGE" and not q["facts"]["Observations"]:
                assert "value-002" in str(q["facts"]["Product files"])
                source.append(q)
                return json.dumps({"answer": {"verdict": "satisfied", "gaps": [],
                    "why": "The unit writes the accepted saved value; no executable check declared"}}), None
            if q["kind"] == "FLOW" and not q["facts"]["Final observations"]:
                assert any(v["unit"] == unit_ref(run, 2) and v["verdict"] == "satisfied"
                           for v in q["facts"]["Source judgment"])
                seen.append(q)
                return json.dumps({"answer": {"verdict": "satisfied", "gaps": [],
                    "why": "The retained local source judgment covers the saved value"}}), None
            return provider.invoke(q, started)

    run.provider = NoCheck()
    try:
        while run.store.cursor().state != "C":
            run.step()
        assert source and seen
        local = run.store.read(run.store.unit(unit_ref(run, 2)).judgment)
        assert local["answer"]["verdict"] == "satisfied"
        rows = [run.store.read(r) for r in run.store.cursor().requirementVerdicts]
        assert any(r["scopeTrees"].get(unit_ref(run, 2)) == local["scopeTree"] for r in rows)
        assert all(judgment.ready(r["answer"]) for r in rows)
        finish(run)
    finally:
        run.close()



@pytest.mark.fast
def test_scale_five_wave_golden(tmp_path):
    """Fifty values survive progressive steering, local questions and a wave-3 process kill.

    Catches one-wave early C, failure escalation before the planner, answers that kill
    unrelated workers and replay of DONE work. The existing single-wave scale case
    never crosses CHECK-IN into a later wave or answers while another worker runs.
    """
    import os
    import sqlite3
    import subprocess

    slot = tmp_path / "scale-five"
    command = [sys.executable, "-B", str(Path(__file__).with_name("scale_journey.py")), str(slot)]
    deadline = time.monotonic() + 290  # requested hang guard, never a product assertion
    log = tmp_path / "scale.log"

    def start(extra=()):
        return subprocess.Popen([*command, *extra], stdout=output, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def stop(child):
        if child.poll() is None:
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
            child.wait(timeout=10)

    with log.open("wb") as output:
        child = start()
        try:
            while not (slot / "kill-wave3").exists():
                if child.poll() is not None:
                    pytest.fail(log.read_text("utf-8"))
                if time.monotonic() >= deadline:
                    pytest.fail("STOP: scale journey exceeded five-minute hang guard")
                time.sleep(.05)
            db = sqlite3.connect((slot / "product/.seldon/state.sqlite3").as_uri()+"?mode=ro",uri=True)
            try:
                before = {r:json.loads(b) for r,b in db.execute("SELECT id,body FROM unit")
                          if json.loads(b)["state"] == "DONE" and r != "guide"}
                cursor = json.loads(db.execute("SELECT cursor FROM run").fetchone()[0])
                assert cursor["wave"] == 3
            finally:
                db.close()
            stop(child)
            child = start(["--resume"])
            child.wait(timeout=max(1, deadline-time.monotonic()))
            assert child.returncode == 0, log.read_text("utf-8")
            db = sqlite3.connect((slot / "product/.seldon/state.sqlite3").as_uri()+"?mode=ro",uri=True)
            try:
                after = {r:json.loads(b) for r,b in db.execute("SELECT id,body FROM unit")}
                assert before and all(after[r]["integrated"] == b["integrated"] for r,b in before.items())
            finally:
                db.close()
        finally:
            stop(child)


@pytest.mark.fast
@pytest.mark.parametrize("severity", ["nit", "pre-existing"])
def test_judge_notes_reach_c_without_rework(tmp_path, severity):
    """Nonblocking findings survive REVIEW, source, flow and product to C with no repairs.

    Catches polish or a peer's old defect reopening a healthy owner; existing must-fix
    recovery journeys prove blocking gaps but never carry a note through final closure.
    """
    run, original = prepare(tmp_path / "notes", count=2)

    class Notes(Scripted):
        def __call__(self, q):
            answer = json.loads(super().__call__(q))["answer"]
            if q["kind"] in ("REVIEW", "JUDGE", "FLOW", "PRODUCT"):
                gap = {"target": next(iter(q["targets"]), "plan"), "severity": severity,
                       "correction": q["kind"] + ": " + (
                           "A small requirement remains unproven in this delivered app"
                           if severity == "nit" else
                           "The peer's old unit-002 file has a pre-existing formatting issue")}
                if q["kind"] in ("FLOW", "PRODUCT"):
                    gap["owners"] = [self.refs[2]]
                elif q["kind"] == "JUDGE":
                    gap["owners"] = judge_owners(q)[:1]
                answer.update(verdict="gap", gaps=[gap])
            return json.dumps({"answer": answer})

    run.provider = Notes(original.slot, count=2)
    shown = []
    def user(item):
        if item["gate"] == "C":
            shown.extend(item["subject"]["notes"])
        return approve(item)
    run.user = user
    try:
        finish(run)
        assert {n["correction"].split(":")[0] for n in shown} == {"REVIEW", "JUDGE", "FLOW", "PRODUCT"}
        assert all(n["severity"] == severity for n in shown)
        assert not run.store.records("unit_fixup") and not run.store.records("unit_gap")
        assert all(int((original.slot / ("turn-unit-" + str(n).zfill(3))).read_text()) == 1
                   for n in (1, 2))
    finally:
        run.close()


LATER_GUIDE = r"""
    walk_import = mode == 'later-forced' or 'Import values' in json.dumps(facts)
    Path('docs/try-it/check.py').write_text("from pathlib import Path\n"
        "assert Path('unit-001/value.txt').read_text() == 'value-001'\nprint('observed value-001')\n"
        + ("assert Path('unit-002/value.txt').exists(), 'Expected unit-002/value.txt from the importer'\n"
           "print('observed import')\n" if walk_import else ""))
    if 'importer' in facts.get('Exact failure', ''):
        later = next((r for r, v in facts['Correction owners'].items()
                      if isinstance(v, dict) and 'unit-002/**' in v['scope']), None)
        # Terra named the closest listed owner when the real owner was not listed (t01).
        answer['discoveries'] = [{'owner': later or owner(1), 'correction': 'Build unit-002/value.txt, the importer'}]
"""


class LaterWave(Scripted):
    """ShelfLife t01 shape: wave 1 builds the reader, the importer area waits for wave 2."""

    def __call__(self, q):
        kind, facts = q["kind"], q["facts"]
        if kind == "ARCHITECT":
            first = {"goal": "Read the saved values", "commitments": ["scopeIn:1", "quality:1"]}
            later = {"goal": "Import values", "commitments": ["scopeIn:2"]}
            previous = facts.get("Previous architecture")
            if "Finished wave" in facts:
                answer = {**{k: previous[k] for k in ("areas", "contracts", "decisions")},
                          "waves": facts["Remaining waves"]}
            elif "Architecture correction" in facts:
                assert "no unit of this wave holds" in facts["Architecture correction"]
                assert "area-2" in facts["Architecture correction"]
                answer = {**{k: previous[k] for k in ("areas", "contracts", "decisions")},
                          "waves": [{**first, "commitments": first["commitments"] + later["commitments"]}]}
            else:
                answer = {"areas": [
                    {"handle": "area-1", "responsibility": "Read saved values", "scope": ["unit-001/**"],
                     "commitments": ["scopeIn:1", "quality:1"]},
                    {"handle": "area-2", "responsibility": "Import values", "scope": ["unit-002/**"],
                     "commitments": ["scopeIn:2"]}],
                    "contracts": [{"handle": "values-file", "kind": "file", "provider": "area-1",
                                   "consumers": ["area-2"],
                                   "text": "Wave 2 agreement: Import values reuse the saved value format."}],
                    "decisions": [], "waves": [first, later]}
            answer["why"] = "The importer follows the reader"
            return json.dumps({"answer": answer})
        answer = json.loads(super().__call__(q))["answer"]
        if kind == "ALIGN":
            answer["scopeIn"].append("Import values from a file")
            answer["understanding"] = "Read named values and Import values from a file"
        return json.dumps({"answer": answer})


@pytest.mark.fast
@pytest.mark.parametrize("forced", [False, True])
def test_later_wave_piece_journey(tmp_path, forced):
    """A wave's walkthrough proves that wave; work no unit of the wave owns goes to the planner.

    ShelfLife t01: the wave-1 guide walked the wave-2 importer, its check was routed to the
    backend, whose scope cannot hold the importer, and 10 COACH calls looped. The clean twin
    keeps the importer out of the wave-1 walkthrough and adds it in wave 2. The forced twin
    walks it anyway: the planner brings the importer into wave 1 and no wrong owner is assigned.
    """
    slot = tmp_path / "later"
    run, _ = prepare(slot, "later-forced" if forced else "later", 2)
    native = slot / "native.py"
    source = native.read_text()
    start = source.index("    Path('docs/try-it/check.py').write_text(")
    end = source.index("    answer['checkPaths'] = ['docs/try-it/check.py']")
    native.write_text(source[:start] + LATER_GUIDE + source[end:], encoding="utf-8")
    run.provider = LaterWave(slot, "later", 2)
    briefs = []
    original = run.provider.invoke

    def invoke(q, started):
        if q["kind"] == "WORK" and plan.GUIDE_PATH in q["facts"]["Write scope"] and "Readers" in q["facts"]:
            briefs.append(q["facts"])
        return original(q, started)

    run.provider.invoke = invoke
    try:
        finish(run)
        store = run.store
        reader = unit_ref(run, 1)
        assert not any(b["owner"] == reader for _, _, b in store.records("unit_fixup"))
        first = briefs[0]
        assert "scopeIn:2" not in first["This wave's commitments"]
        assert "User's words" not in first and "Import values" not in str(first.get("Contracts consumed"))
        importer = unit_ref(run, 2)
        guide = [store.read(r) for r in store.cursor().finalObservations
                 if store.read(r)["check"].startswith("guide:")]
        assert guide and all("observed import" in o["stdout"] for o in guide)
        gaps = store.records("architecture_gap")
        if forced:
            # One plan gap, then the planner gave the importer a wave-1 unit.
            assert len(gaps) == 1 and "Expected: a unit of this wave owns" in gaps[0][2]["gap"]
            assert importer.startswith("1.") and store.unit(importer).wave == 1
            assert store.cursor().wave == 1
        else:
            assert not gaps and not store.records("unit_fixup")
            assert importer.startswith("2.") and store.cursor().wave == 2
            assert "scopeIn:2" in briefs[-1]["This wave's commitments"]
    finally:
        run.close()


@pytest.mark.fast
def test_repeated_unit_failure_pauses_journey(tmp_path):
    """A repeated product assertion reaches the planner for a plain user question.

    ShelfLife t01 offered "Try the planner's new approach" five times for one unchanged failure;
    the step-fault twin covers planning steps only.
    """
    from seldon.decisions import Waiting

    slot = tmp_path / "repeat"
    run, provider = prepare(slot)
    native = slot / "native.py"
    native.write_text(native.read_text().replace("    Path(name, 'check.py').write_text(source)",
        "    source += \"raise AssertionError('reader unavailable')\\n\"\n"
        "    Path(name, 'check.py').write_text(source)"))
    shown = []

    def user(item):
        if item["gate"] != "UNIT":
            return approve(item)
        shown.append(item)
        if len(shown) == 1:
            return item["ask"]["options"][0]["label"]
        raise Waiting()

    run.user = user
    try:
        with pytest.raises(Waiting):
            while True:
                run.step()
        first, again = shown[0], shown[-1]
        assert first["ask"]["options"][0]["label"] == "Try the planner's new approach"
        ask = again["ask"]
        assert "could not make" in ask["question"] and ":check:1" in ask["question"]
        assert "Seldon's fault" not in ask["question"]
        assert "reader unavailable" in again["subject"]["failure"]
        assert ask["options"][0]["label"] == "Change the agreement or approach"
        assert [o["effect"] for o in ask["options"]] == ["direct", "drop", "pause"]
        assert provider.calls.count("COACH") == 3
        assert run.store.cursor().decision == again["request"]
    finally:
        run.close()


class ContractGap(Scripted):
    """Recorded ShelfLife u01 shape: the consumer relies on what the agreed contract does not define.

    The page required an X-Pantry-Today header that the pantry API contract never named; the
    interface worker could not fix that alone and the run paused on "This is Seldon's fault".
    """
    def __init__(self, slot, case):
        super().__init__(slot, "contract-gap", 1)
        self.case = case

    def __call__(self, q):
        if q["kind"] == "ARCHITECT" and "Architecture correction" in q["facts"]:
            assert "values" in q["facts"]["Architecture correction"]
            answer = json.loads(super().__call__(q))["answer"]
            answer["contracts"][0]["text"] += " The provider also writes the date to unit-001/today.txt."
            return json.dumps({"answer": answer})
        if q["kind"] != "JUDGE":
            return super().__call__(q)
        answer = json.loads(super().__call__(q))["answer"]
        facts, owners = q["facts"], judge_owners(q)
        agreed = str(facts.get("Contracts consumed", {})) + str(facts.get("Contracts provided", {}))
        consumer = "value-002" in str(facts["Requirements"])
        gap = None
        if consumer and (self.case == "unfixable" or "today.txt" not in agreed):
            gap = {"correction": "Make the reader work with the agreed values contract without "
                   "requiring a date the contract does not define", "owners": (
                       ["values"] if self.case == "contract" and "values" in owners else owners[:1])}
        elif not consumer and "today.txt" in agreed and "unit-001/today.txt" not in facts["Product files"]:
            gap = {"correction": "Write unit-001/today.txt as the corrected values contract says",
                   "owners": owners[:1]}
        if gap:
            gap = {"severity": "must-fix", "target": q["targets"][0], **gap}
            if not owners:
                del gap["owners"]  # The base schema had no JUDGE owners.
            answer.update(verdict="gap", gaps=[gap])
        return json.dumps({"answer": answer})


@pytest.mark.fast
def test_check_contract_journey(tmp_path):
    """Quiet Bench r1025/r1158: a check writer sends a failure to service, which needs a
    guarantee from engine. A worker-only fix repeats; the judge-gap twin never exercises
    a failing executable check or requires both endpoint patches to pass it.
    """
    from seldon.decisions import Waiting

    slot = tmp_path / "check-contract"
    run, _ = prepare(slot, "contracts", 3)
    native = slot / "native.py"
    native.write_text(native.read_text(encoding="utf-8").replace(
        "    if mode == 'contracts':", "    if mode == 'check-contract':\n"
        "        if number in (1, 2):\n"
        "            side = 'Contracts provided' if number == 1 else 'Contracts consumed'\n"
        "            corrected = 'Publish ready during processing' in str(facts.get(side, {}))\n"
        "            Path(name, 'callback.txt').write_text(('ready' if corrected else 'terminal')\n"
        "                if number == 1 else ('ready' if corrected else 'progress'))\n"
        "        if number == 3:\n"
        "            source += \"assert Path('unit-001/callback.txt').read_text() == \"\n"
        "            source += \"Path('unit-002/callback.txt').read_text() == 'ready', \"\n"
        "            source += \"'POST /api/runs could not return an active session'\\n\"\n"
        "    if mode == 'contracts':").replace(
        "    answer['checkPaths'] = [name+'/check.py']",
        "    if mode == 'check-contract' and number == 3:\n"
        "        answer['discoveries'] = [{'owner': owner(2), 'correction': 'Return an active session'}]\n"
        "    if mode == 'check-contract' and number == 2 and not corrected:\n"
        "        answer['discoveries'] = [{'owner': owner(1), 'correction': 'Guarantee when callbacks fire'}]\n"
        "    answer['checkPaths'] = [name+'/check.py']"), encoding="utf-8")
    (slot / "mode").write_text("check-contract")

    class CheckContract(Scripted):
        def __call__(self, q):
            answer = json.loads(super().__call__(q))["answer"]
            if q["kind"] == "ARCHITECT" and "Architecture correction" in q["facts"]:
                correction = q["facts"]["Architecture correction"]
                assert "POST /api/runs could not return an active session" in correction
                assert "Guarantee when callbacks fire" in correction and "Return an active session" in correction
                answer["contracts"][0]["text"] += " Publish ready during processing."
            return json.dumps({"answer": answer})

    run.provider = CheckContract(slot, "contracts", 3)
    shown = []

    def user(item):
        if item["gate"] != "UNIT":
            return approve(item)
        shown.append(item)
        if len(shown) == 1:
            return item["ask"]["options"][0]["label"]
        raise Waiting()

    run.user = user
    store = run.store
    try:
        try:
            finish(run)
        except Waiting:
            owners = [b["owner"] for _, _, b in store.records("unit_fixup")]
            pytest.fail(f"Contract disagreement still asks the user after fixups to {owners}: "
                        f"{[item['ask']['question'] for item in shown]}")
        assert not shown
        assert len(store.records("architecture_gap")) == 1
        assert "Publish ready during processing" in plan.select(plan.execution(store), "contract")["values"]["text"]
        assert (slot / "product" / "unit-001" / "callback.txt").read_text() == "ready"
        assert (slot / "product" / "unit-002" / "callback.txt").read_text() == "ready"
        assert any(store.read(r)["check"] == f"{unit_ref(run, 3)}:check:1"
                   for r in store.cursor().finalObservations)
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("case", ["contract", "unfixable"])
def test_contract_gap_journey(tmp_path, case):
    """A judged disagreement with a contract becomes one planner correction, not a worker loop.

    Recorded u01 (2026-11-17) looped one worker on a cross-unit gap and then blamed Seldon; the
    repeated-check twin covers executable failures. contract: the planner extends the contract,
    the provider is reworked and the wave verifies with no user question. unfixable: a judged gap
    no worker fixes reaches the user in plain words with planner-framed options.
    """
    from seldon.decisions import Waiting

    slot = tmp_path / case
    run, _ = prepare(slot, "contract-gap")
    run.provider = provider = ContractGap(slot, case)
    shown = []

    def user(item):
        if item["gate"] != "UNIT":
            return approve(item)
        shown.append(item)
        if len(shown) == 1 and case == "unfixable":
            return item["ask"]["options"][0]["label"]
        raise Waiting()

    run.user = user
    store = run.store
    try:
        if case == "contract":
            finish(run)
            assert not shown and not [b for _, _, b in store.records("decision_request") if b["gate"] == "UNIT"]
            gaps = [b["gap"] for _, _, b in store.records("architecture_gap")]
            assert len(gaps) == 1 and "contract values disagree" in gaps[0]
            assert "requiring a date the contract does not define" in gaps[0]
            assert "today.txt" in plan.select(plan.execution(store), "contract")["values"]["text"]
            fixed = {b["owner"] for _, _, b in store.records("unit_fixup")}
            assert fixed == {unit_ref(run, 1)}  # the provider was reworked; the consumer was never looped
            assert (slot / "product" / "unit-001" / "today.txt").read_text() == "2026-11-18"
            assert provider.calls.count("COACH") == 0
        else:
            with pytest.raises(Waiting):
                while True:
                    run.step()
            first, last = shown[0], shown[-1]
            assert first is not last and first["ask"]["options"][0]["label"] == "Try the planner's new approach"
            ask = last["ask"]
            assert "Seldon's fault" not in ask["question"]
            assert "could not fix what the judge found missing" in ask["question"]
            assert "requiring a date the contract does not define" in ask["question"]
            labels = [o["label"] for o in ask["options"]]
            assert labels == ["Try the planner's new approach", "Leave this out", "Pause"]
            assert provider.calls.count("COACH") == 3  # coached once, asked once, then framed the options
            assert not store.records("architecture_gap")
    finally:
        run.close()
