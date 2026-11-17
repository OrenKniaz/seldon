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
@pytest.mark.parametrize("fault", [False, True])
def test_wave_loop_golden(tmp_path, fault):
    """An unanswered check-in allows planning but holds new work; its answer grows the product.

    The fault twin breaks an old reader with a new format and repairs its owner, then steers
    at C. One-wave and planning-only journeys miss stale bases, reused sessions and check-in
    consent lost as the cursor advances. The strict provider rejects STEER before execution
    if any nested object makes fields optional. Empty changes must preserve the built product.
    """
    from wave_loop_journey import journey

    journey(tmp_path / "waves", fault)


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
                    assert q["facts"]["What the reader came to do"] == VISION["understanding"]
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
                if self.mode in ("contracts", "parallel-contract", "wave-gap", "see-proof", "see-proof-gap", "contract-fanout")
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
            if self.mode in ("contracts", "parallel-contract", "coach-facts", "wave-gap", "see-proof", "see-proof-gap", "contract-fanout"):
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
                        {
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
                                {
                                    "target": r,
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
                        answer.update(verdict="gap", gaps=[{"target": q["targets"][0],
                            "correction": "The reader output omits the saved value label"}])
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
                                {
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
                        gaps=[{"target": q["targets"][0], "correction": "Make the accepted output clear",
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
                        assert set(facts) == {"Exact failure", "Write scope", "Correction owners",
                                              "Correction observations"}
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
                        answer.update(verdict="gap", gaps=[{"target": target, "owners": [owner],
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
        else:
            assignments = [b for _,_,b in run.store.records("unit_fixup")]
            owner = planted[0][2]
            expected = plan.select(document, "contract")[owner]["provider"] if fault == "outside-contract" else owner
            assert {b["owner"] for b in assignments} == {expected}
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
                return json.dumps({"answer": {"verdict": "gap", "gaps": [{
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

    Worker-failure twins miss controller faults that encourage endless user retries.
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
                    answer.update(verdict="gap", gaps=[{"target": q["targets"][0],
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
