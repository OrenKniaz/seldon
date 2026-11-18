"""Strict recorded Quiet Bench planning twins with executed Git-backed probes."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from seldon import architecture, gitwork, plan
from seldon.run import Run
from test_journey import NATIVE, Scripted, approve

STAGING = (
    "Build it in three waves and show me each one. Wave 1: the thinnest thing that runs end to end, "
    "one tone through a fixed filter,\nsaved and shown. Wave 2: the adaptive filter, attenuation per "
    "band, browse and reload. Wave 3: compare runs, limiter and stop\nswitch, WAV import.\n"
)
DISCOVERY = "The saved session probe is repeatable. A separate reader must verify compatibility from disk."
AGREEMENT = "The reader gets the original saved value from unit-001/value.txt as UTF-8."


class Interrupted(BaseException):
    pass


class QuietBench(Scripted):
    def __init__(self, slot, recording):
        super().__init__(slot)
        self.recording = recording
        self.interrupt = False

    def __call__(self, q):
        kind, facts = q["kind"], q["facts"]
        if kind == "ALIGN":
            answer = deepcopy(self.recording["r12"])
            answer["vision"].update(size="tiny", sizeWhy="Measured plain run fits in minutes.")
        elif kind == "ARCHITECT":
            answer = self.outline(facts)
        elif kind == "DETAIL":
            area = facts["Area"]
            if area["handle"] == "reader" and self.interrupt:
                self.interrupt = False
                raise Interrupted()
            edits = []
            requirements = []
            for i, (ref, commitment) in enumerate(facts["Accepted commitments"].items(), 1):
                acceptance = commitment.get("outcome", commitment.get("verification"))
                requirements.append(f"r{i}")
                edits.extend([
                    {"kind": "feature", "targetHandle": f"f{i}", "typedBody": {
                        "story": commitment.get("story", acceptance), "acceptance": acceptance,
                        "commitments": [ref]}},
                    {"kind": "requirement", "targetHandle": f"r{i}", "typedBody": {
                        "acceptance": acceptance, "features": [f"f{i}"]}},
                ])
            answer = {"verdict": "planned", "edits": edits, "units": [{
                "scope": area["scope"], "requirements": requirements, "dependencies": [],
                "instructions": "Exercise the accepted behaviour from the owned paths."}],
                "why": "Every feature is traced to an owned requirement."}
        elif kind == "JUDGE" and "findings" in q["schema"]["properties"]["answer"].get("properties", {}):
            observations = facts["Observations"]
            assert any(o["purpose"] == "T" and o["exitCode"] != 0 for o in observations)
            assert any(o["purpose"] == "final" and o["exitCode"] == 0
                       and o["check"] in facts["Walkthrough checks"] for o in observations)
            finding = {"verdict": "satisfied", "gaps": [],
                       "why": "The staged probe executed on the delivered candidate."}
            answer = {"findings": dict.fromkeys(q["targets"], finding),
                      "why": "The probe and walkthrough ran against the same revision."}
        else:
            return super().__call__(q)
        return json.dumps({"answer": answer})

    def outline(self, facts):
        recorded = deepcopy(self.recording["r23"])
        for area, number in zip(recorded["areas"], (2, 1, 3), strict=True):
            area["scope"] = [f"unit-{number:03}/**"]
        previous = facts.get("Previous architecture")
        if previous and facts["Remaining waves"]:
            return {"areas": [], "contracts": [], "decisions": [], "waves": [],
                    "why": "Keep the recorded staging."}
        if previous:
            assert DISCOVERY in str(facts["Finished wave"])
            assert DISCOVERY in str(facts["User's words"])
            return {"areas": [{"handle": "reader", "responsibility": "Independent session reader",
                               "scope": ["unit-002/**"], "commitments": ["scopeIn:first-run"]}],
                    "contracts": [{"handle": "saved-session", "kind": "file", "provider": "bench",
                                   "consumers": ["reader"], "text": AGREEMENT}],
                    "decisions": [], "waves": [{"goal": "Prove saved-session compatibility",
                                                "commitments": ["scopeIn:first-run"]}],
                    "why": DISCOVERY}
        # A recorded response gives the counterfactual when the planner receives no size policy.
        approach = facts.get("Planning approach")
        if not approach or approach["split"] == "force" or STAGING in facts["User's words"]["Dream"]:
            return recorded
        handles = list(facts["Commitment handles"])
        return {"areas": [{"handle": "bench", "responsibility": "Deliver the accepted instrument",
                           "scope": ["unit-001/**"], "commitments": handles}],
                "contracts": [], "decisions": [],
                "waves": [{"goal": "Deliver the complete accepted bench", "commitments": handles}],
                "why": "One session can deliver the accepted instrument."}


@pytest.mark.fast
@pytest.mark.parametrize("staged", [False, True], ids=["one-owner-escalates", "forced-recorded-waves"])
def test_quietbench_size_route_journey(tmp_path, monkeypatch, staged):
    """Catch lost kickoff policy and ownership on a real next-wave transition.

    The existing peer-agreement replay starts with three owners. It misses a micro plan
    reaching B with one builder and promoting the same run without reopening its first wave.
    The native script observes routing and files on Git revisions using small product probes.
    """
    monkeypatch.delenv("SELDON_OFF", raising=False)
    monkeypatch.setenv("SELDON_SPLIT", "force" if staged else "auto")
    recording = json.loads((Path(__file__).parent / "fixtures/quietbench-c01-agreements.json")
                           .read_text(encoding="utf-8"))
    dream = recording["dream"]
    if not staged:
        assert STAGING in dream
        dream = dream.replace(STAGING, "", 1)
    (tmp_path / "mode").write_text("normal", encoding="utf-8")
    native = NATIVE.replace(
        "    Path(name, 'check.py').write_text(source)",
        f"""    later = any(r.startswith('2.') for r in facts['Requirements'])
    discovery = {DISCOVERY!r}
    if later:
        if name == 'unit-002':
            agreements = facts['Contracts consumed']
            if 'saved-session' in agreements:
                assert agreements['saved-session']['text'] == {AGREEMENT!r}
            else:
                assert 'session-json' in agreements
            source += "assert Path('unit-001/value.txt').read_text() == 'value-001'\\n"
        source += f"assert Path('{{name}}/wave-two.txt').read_text() == 'compatible'\\n"
        Path(name, 'wave-two.txt').write_text('compatible')
    check_path = name + ('/wave-two.py' if later else '/check.py')
    Path(check_path).write_text(source)""",
    ).replace("name+'/check.py'", "check_path").replace(
        "'correction': 'Keep the saved values visible'", "'correction': discovery",
    ).replace(
        "if 'Existing checks' in facts and not",
        r"""if name != 'docs' and 'docs/try-it.md' in facts['Write scope'] and not later:
    Path('docs/try-it').mkdir(parents=True, exist_ok=True)
    Path('docs/try-it.md').write_text('Run python docs/try-it/check.py from the product folder.')
    Path('docs/try-it/check.py').write_text(
        "from pathlib import Path\nassert Path('unit-001/value.txt').read_text() == 'value-001'\n")
    answer['checkPaths'].append('docs/try-it/check.py')
    answer['checks'].append({'purpose': 'Run the documented walkthrough',
        'command': [sys.executable, '-B', 'docs/try-it/check.py'],
        'expectedEvidence': 'Read the saved value'})
if 'Existing checks' in facts and not""",
    )
    (tmp_path / "native.py").write_text(native, encoding="utf-8")
    provider = QuietBench(tmp_path, recording)

    def user(request):
        if request["gate"] == "A":
            return {"request": request["request"], "replyId": "micro-approved", "selections": [
                {"groupId": "size", "optionId": "tiny"}, {"groupId": "round", "optionId": "done"}]}
        if request["gate"] == "CHECK-IN" and not staged and request["subject"]["wave"] == 1:
            return "Revise the remaining plan inside the accepted scope. " + DISCOVERY
        return approve(request)

    project = tmp_path / "product"
    run = Run(project, tmp_path / "workers", dream, provider.setup, provider, user, capacity=1)
    try:
        run.step()  # Display A, then resume under opposite experiment settings.
        run.close()
        monkeypatch.setenv("SELDON_OFF", "sizeFirst")
        monkeypatch.setenv("SELDON_SPLIT", "auto" if staged else "force")
        run = Run.reopen(project, provider, user)
        while run.step() != "B":
            pass
        before = run.store.plan(run.store.cursor().revision)
        outline = before["architecture"]
        assert before["nodes"]["vision"]["body"]["size"] == "tiny"
        approved_handles = set(architecture.commitments(before["nodes"]["vision"]["body"]))
        assert approved_handles == {r for w in outline["waves"] for r in w["commitments"]}
        if staged:
            assert outline["waves"] == recording["r23"]["waves"]
        else:
            assert (len(outline["areas"]), len(outline["waves"]), outline["contracts"]) == (1, 1, [])
            features = plan.select(before, "feature")
            assert {r for f in features.values() for r in f["commitments"]} == approved_handles
            builder, = [u for ref, u in plan.select(before, "unit").items() if ref != "guide"]
            assert set(builder["requirements"]) == set(plan.select(before, "requirement"))
        architect, = [q for _, _, q in run.store.records("question") if q["kind"] == "ARCHITECT"]
        assert architect["facts"]["Planning approach"]["split"] == ("force" if staged else "auto")
        while run.step() != "CHECK-IN":
            pass
        first = run.store.cursor()
        assert first.finalCommit == first.candidate
        earlier = plan.execution(run.store)
        assert {r for c in plan.select(earlier, "check").values() for r in c["requirements"]} == set(
            plan.select(earlier, "requirement"))
        run.step()  # Publish wave one's check-in.
        run.step()  # Approve the staged horizon or give evidence for in-scope steering.
        if not staged:
            provider.interrupt = True
            assert run.step() == "DETAIL"
            with pytest.raises(Interrupted):
                run.step()
            run.close()
            run = Run.reopen(project, provider, user)
        while run.step() != "CHECK-IN":
            pass
        last = run.store.cursor()
        assert last.wave == 2 and last.B == first.B
        assert last.waveStart == first.candidate
        assert last.candidate != first.candidate
        gitwork.git(project, "merge-base", "--is-ancestor", first.candidate, last.candidate)
        after = plan.execution(run.store)
        assert after["architecture"]["waves"][0] == outline["waves"][0]
        if not staged:
            def guide_writer(document):
                return next((ref, u["area"]) for ref, u in plan.select(document, "unit").items()
                            if plan.GUIDE_PATH in u["scope"])

            assert guide_writer(after) == guide_writer(earlier)
        assert all(after["nodes"][r] == record for r, record in earlier["nodes"].items()
                   if record["kind"] in ("feature", "requirement"))
        observed = [run.store.read(ref) for ref in last.finalObservations]
        assert any(o["check"].startswith("2.") and o["exitCode"] == 0 for o in observed)
        for ref, check in plan.select(earlier, "check").items():
            assert any(o["check"] == ref and o["requirements"] == check["requirements"]
                       and o["exitCode"] == 0 for o in observed)
        peer, = [ref for ref, u in plan.select(after, "unit").items() if u["scope"] == ["unit-002/**"]]
        agreement = plan.select(after, "contract")["session-json" if staged else "saved-session"]
        assert peer in agreement["consumers"]
        assert not any(row["gate"] == "UNIT" for _, _, row in run.store.records("decision_request"))
    finally:
        run.close()
