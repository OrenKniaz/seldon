"""C9/C14/C15: thin acceptance through actual worker processes, Git and final reviews."""

import json
import sys
import time
from copy import deepcopy
from pathlib import Path
from threading import Barrier

import pytest

from seldon import gitwork, plan, questions
from seldon.providers import ROLES, Codex
from seldon.run import Run

VISION = {
    "size": "small",
    "understanding": "Read named values",
    "users": ["CLI user"],
    "scopeIn": ["Read each named persistent value"],
    "scopeOut": [],
    "improvements": [],
    "quality": [{"criterion": "Clear output", "verification": "Read the real command output"}],
    "unknowns": [],
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
if mode == 'parallel-contract':
    assert ('Design guidance used by UI workers' in facts) == (name == 'unit-002')
counter = slot/('turn-'+name)
turn = int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(turn))
thread = args[args.index('resume')+1] if 'resume' in args else str(uuid.uuid4())
print(json.dumps({'type':'thread.started','thread_id':thread}), flush=True)
with (slot/('launch-'+name+'.jsonl')).open('a') as out:
    out.write(json.dumps({'thread':thread,'resumed':'resume' in args,'turn':turn})+'\n')
if mode == 'kill-work' and name == 'unit-002' and not (slot/'resume').exists():
    (slot/'kill-ready').write_text(str(os.getpid()))
    time.sleep(30)
answer = {'outcome':'done','summary':'Wrote and exercised the acceptance',
          'checks':[], 'checkPaths':[], 'discoveries':[], 'why':'Ready for independent observation'}
if mode == 'schema-fix' and name == 'unit-001' and turn == 1:
    answer = json.loads((slot/'recorded-work.json').read_text())['answer']
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
    execution = facts['Execution facts']
    peers = {ref: row for ref, row in execution['Units'].items() if ref != 'guide'}
    assert peers and all(row['state'] == 'DONE' for row in peers.values())
    values = [p for p in execution['Paths present'] if p.endswith('/value.txt')]
    assert len(values) == len(peers) and all(Path(p).read_text().startswith('value-') for p in values)
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
        if peer != 'u'+name.split('-')[1] and state['state'] == 'DONE':
            paths = [p for p in execution['Paths present'] if p.startswith('unit-') and p.endswith('value.txt')]
            assert paths and all(Path(p).read_text().startswith('value-') for p in paths)
    if mode == 'coach-facts' and name == 'unit-003':
        guidance = facts['Exact failure']
        assert 'WAVE-VERIFY' in guidance
        assert 'Evidence\n[]' in guidance
        assert execution['Proof owner'] == 'WAVE-VERIFY'
    if mode == 'coach-facts' and name == 'unit-001':
        guidance = facts['Exact failure']
        assert 'Evidence\n[]' in guidance
    value = 'value-'+str(number).zfill(3)
    source = ('from pathlib import Path\nimport os\n'
        + f"assert Path('{name}/value.txt').read_text() == '{value}'\n"
        + f"print('observed {value}')\n")
    if mode == 'contract-fanout' and number >= 3:
        assert 'UTF-8 text: value-001.' in str(facts['Contracts consumed'])
        source += "assert Path('unit-001/value.txt').read_text() == 'value-001'\n"
    if mode == 'coach-facts' and name == 'unit-003':
        source += "assert Path('unit-001/value.txt').read_text() == 'value-001'\n"
    source += "if os.environ.get('SELDON_EVIDENCE_DIR'):\n"
    source += "    evidence = Path(os.environ['SELDON_EVIDENCE_DIR'])\n"
    source += "    evidence.joinpath('value.json').write_text('observed persistence')\n"
    if mode == 'proof-failure' and name == 'unit-001' and turn == 1:
        source += 'raise AssertionError("actual failed product assertion")\n'
    if mode == 'wave-fault' and name == 'unit-001' and turn == 1:
        source += "assert not Path('unit-002/value.txt').exists(), 'integrated mismatch'\n"
    if mode == 'wave-gap' and name == 'unit-002' and turn == 1:
        source += "assert not Path('unit-001/value.txt').exists(), 'integrated mismatch'\n"
    Path(name, 'check.py').write_text(source)
    Path(name, 'value.txt').write_text(value)
    if mode == 'scope' and turn == 1 and name == 'unit-001':
        Path('unowned.txt').write_text('outside scope')
    if mode == 'scope' and turn > 1:
        Path('unowned.txt').unlink(missing_ok=True)
    answer['discoveries'] = [{'owner': 'future check-in', 'correction': 'Keep the saved values visible'}]
    answer['checkPaths'] = [name+'/check.py']
    answer['checks'] = [{'purpose':'Read the actual persistent value',
        'command':[sys.executable,'-B',name+'/check.py'],
        'expectedEvidence':'Observed exact saved value'}]
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'answer':answer})}}), flush=True)
print(json.dumps({'type':'turn.completed'}), flush=True)
"""


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
        self.parallel = Barrier(2) if mode == "parallel-contract" else None

    def timeout_for(self, kind, context):
        return 30

    def invoke(self, q, started):
        if q["kind"] == "WORK":
            if self.parallel and plan.GUIDE_PATH not in q["facts"]["Write scope"]:
                self.parallel.wait(timeout=60)  # Hang guard, not a timing assertion.
            return self.native.invoke(q, started)
        return self(q), None

    def __call__(self, q):
        kind, facts = q["kind"], q["facts"]
        self.calls.append(kind)
        if kind == "ALIGN":
            answer = deepcopy(VISION)
            if self.mode == "parallel-contract":
                answer["quality"][0]["criterion"] = "Visual direction: a clear, deliberate reader page"
        elif kind == "ARCHITECT":
            groups = (
                [[1], [2], [3]] if self.mode == "coach-facts" else
                [[1, 2], [3, 4]] if self.mode == "contract-fanout" else [[1], [2]]
                if self.mode in ("contracts", "parallel-contract", "wave-gap", "contract-fanout")
                else [list(range(i, min(i + 10, self.count + 1))) for i in range(1, self.count + 1, 10)]
            )
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
                "waves": [{"goal": "Deliver the complete values"}],
            }
            if self.mode in ("contracts", "parallel-contract", "coach-facts", "wave-gap", "contract-fanout"):
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
            answer = {
                "choice": "revise"
                if words.startswith("Revise")
                else "decline"
                if words.startswith("No")
                else "approve"
            }
        elif kind in ("JUDGE", "FLOW", "PRODUCT"):
            answer = {
                "verdict": "satisfied",
                "gaps": [],
            }
            if kind == "JUDGE":
                observations = facts["Observations"]
                if facts["Proof execution"]["Proof owner"] != "WAVE-VERIFY":
                    assert any(o["purpose"] == "P" and o["exitCode"] == 0 for o in observations)
                for r in q["targets"]:
                    assert any(
                        r in o["requirements"] and o["purpose"] == "T" and o["exitCode"] != 0
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
            else:
                obs = facts["Final observations"]
                assert obs and all(o["exitCode"] == 0 for o in obs)
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
                        and facts["Subject"]["criterion"] == VISION["quality"][0]["criterion"]
                        and kind not in self.gaps):
                    self.gaps.add(kind)
                    answer.update(
                        verdict="gap",
                        gaps=[{"target": q["targets"][0], "correction": "Make the accepted output clear"}],
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
    assert all(u.state == "DONE" for u in run.store.units().values())
    document = plan.execution(run.store)
    obs = [run.store.read(r) for r in cursor.finalObservations]
    assert all(o["commit"] == cursor.candidate and o["exitCode"] == 0 for o in obs)
    requirements = set(plan.select(document, "requirement"))
    assert requirements == {r for o in obs for r in o["requirements"]}
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
        if mode == "wave-gap":
            while run.store.cursor().state != "WAVE-VERIFY":
                run.step()
            assert all(u.state == "DONE" for u in run.store.units().values())
            gap = run.store.records("unit_gap")[0][2]
            assert gap["commit"] == run.store.unit("u002").P
            assert any(b["patch"] == gap["commit"] for _, _, b in run.store.records("publication"))
            assert not any(b.get("unit") == "u002" and b["next"] == "REWORK"
                           for _, _, b in run.store.records("transition"))
            run.step()
            assert run.store.records("unit_fixup")
        finish(run)
        if mode == "normal":
            assert any(b["owner"] == "future check-in" for _, _, b in run.store.records("discovery"))
        if mode == "parallel-contract":
            assert provider.calls.count("ARCHITECT") == 1
            assert not run.store.records("detail_correction")
            assert not run.store.records("architecture_gap")
            assert not any("Dependencies" in b["reason"] for _, _, b in run.store.records("unit_wait")
                           if b["unit"] != "guide")
            transitions = [b for _, _, b in run.store.records("transition") if b.get("unit") in ("u001", "u002")]
            working = {b["unit"]: i for i, b in enumerate(transitions) if b["next"] == "WORKING"}
            assert set(working) == {"u001", "u002"}
            assert all(b["next"] == "WORKING" for b in transitions[min(working.values()):max(working.values())+1])
            agreement, = plan.select(plan.execution(run.store), "contract").values()
            assert len(agreement["text"]) == 3000 and agreement["kind"] == "data rule"
            assert agreement["consumers"] == ["u002"]
        if count == 50:
            assert run.store.cursor().candidate == candidate
            assert len(run.store.units()) == 51
            assert all((slot / f"turn-unit-{i:03}").read_text() == "1" for i in range(1, 51))
        assert (run.project / "unit-001/value.txt").read_text() == "value-001"
        records = run.store.records()
        gates = [b["gate"] for _, _, b in run.store.records("decision_request")]
        assert set(gates) == {"A", "B", "CHECK-IN", "C"}
        if mode == "question":
            assert provider.calls.count("COACH") == 1
            assert (slot / "turn-unit-001").read_text() == "2"
            assert not run.store.records("unit_failure")
        if mode == "coach-facts":
            assert any(b["unit"] == "u003" and b["next"] == "PROVING"
                       for _, _, b in run.store.records("transition"))
            observations = [b for _, _, b in run.store.records("observation") if b["unit"] == "u003"]
            assert {b["purpose"] for b in observations} == {"T", "final"}
            assert any(b["purpose"] == "final" and b["exitCode"] == 0 for b in observations)
        if mode in ("unit-gap", "unit-rework"):
            assert (slot / "turn-unit-001").read_text() == "2"
            assert len(run.store.records("unit_gap")) == (2 if mode == "unit-gap" else 1)
            assert not any(b["errors"] for _, _, b in run.store.records("provider_reply"))
            checkin = next(b for _, _, b in run.store.records("decision_request") if b["gate"] == "CHECK-IN")
            assert bool(checkin["subject"]["open gaps"]) == (mode == "unit-gap")
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
            assert agreement["provider"] == "u001"
            assert agreement["consumers"] == ["u003", "u004"]
            assert all(run.store.unit(ref).recoveries == 0 for ref in ("u003", "u004"))
        if mode == "contracts":
            assert len(plan.select(plan.execution(run.store), "contract")) == 1
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
        ("contracts", 2),
        ("contract-fanout", 4),
        ("parallel-contract", 2),
        ("wave-fault", 2),
        ("wave-gap", 2),
        ("flow-fault", 2),
        ("product-fault", 2),
        ("proof-failure", 2),
        ("scope", 2),
        ("scale50", 50),
    ],
)
def test_thin_plan_golden(tmp_path, mode, count):
    """Real worker checks reach C through thin intent, local recovery and strict integrated reviews.

    Success alone misses worker questions, persistent unit/plan gaps and final-only failures.
    The two-area contract journey catches serialized contract peers and rejected long agreements;
    ordinary journeys do not exercise provider-in-consumers normalization or simultaneous WORKING.
    Multi-unit areas catch consumer briefs lost when DETAIL omits redundant endpoints,
    and preserve one explicit provider when its area has multiple units. Single-unit cases miss this.
    Backend/page briefs catch craft guidance leaking to peers without the visual commitment.
    Factual questions require COACH with execution facts; wave-owned gaps must integrate before repair.
    A real sibling read from an early unit catches running integrated acceptance before integration.
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
        assert run.store.unit("u001").state == "ASKED"
        assert all(run.store.unit(ref).state == "DONE" for ref in ("u002", "u003"))
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
