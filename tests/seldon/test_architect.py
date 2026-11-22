"""C23: planning resumes recorded area answers without replaying completed work."""
from copy import deepcopy
import json

import pytest
from seldon import inference, plan, questions
from seldon.run import Run
from seldon.states import advance
from test_journey import Scripted, approve, finish, prepare, strict_schema


@pytest.mark.fast
def test_quiet_bench_choice_trace_at_b(tmp_path, scalar_align):
    """Protect choice lineage at B after reorder; fixed-vision journeys miss crossed or lost sources."""
    # Quiet Bench d56dc7b: exact launch literal and verbatim excerpt from the saved ALIGN reply.
    launch = "python server.py --port PORT --data DIR"
    dream = (f"Every session is saved on a small local storage server (`{launch}`) with its settings "
             "and results, and a web page on that server lets me start a run, browse saved sessions, "
             "reload one and compare two.")
    stopped = ("Report stopped and limited runs truthfully, including how many samples were processed; "
               "do not present a partial run as completed.")
    # The replies are counterfactual. Recorded r10 and r170 are scripted approvals of the scalar path.
    compare_words = "compare the shared interval"
    inspect_words = "Keep the measured samples when I reopen a stopped run"
    groups = [{"id": "compare", "options": [{"id": "complete", "label": "Complete runs"},
                                           {"id": "shared", "label": "Compare the shared interval"}]},
              {"id": "inspect", "options": [{"id": "settings", "label": "Keep settings"},
                                           {"id": "samples", "label": "Keep settings and samples"}]}]
    slot = tmp_path / "quiet-bench"
    slot.mkdir()
    scripted = Scripted(slot)
    launch_fact = {"handle": "launch", "value": launch, "meaning": "Start the saved-session service",
                   "source": "kickoff"}

    def planning(q):
        strict_schema(q["schema"])
        kind, facts = q["kind"], q["facts"]
        if kind == "ARCHITECT":
            assert facts["Approved vision"] == locked
            assert facts["Commitment handles"]["scopeIn:compare"]["sources"] == [reply]
            answer = {"areas": [
                {"handle": "browser", "responsibility": "Compare runs", "scope": ["browser/**"],
                 "commitments": ["scopeIn:compare", "quality:trust"]},
                {"handle": "saved", "responsibility": "Reopen stopped runs", "scope": ["saved/**"],
                 "commitments": ["scopeIn:inspect"]}],
                "contracts": [], "decisions": [], "waves": [{
                    "goal": "Open a recorded run and compare the measured bands",
                    "commitments": ["scopeIn:compare", "scopeIn:inspect", "quality:trust"]}]}
        elif kind == "DETAIL":
            area = facts["Area"]["handle"]
            key = {"browser": "scopeIn:compare", "saved": "scopeIn:inspect"}[area]
            assert facts["Applicable constraints"]["launch"] == launch_fact
            goal = facts["Accepted commitments"][key]
            if area == "browser":
                corrections = facts.get("Exact corrections")
                if corrections:
                    error = corrections[0]["Errors"][0]
                    assert all(text in error for text in ("scopeIn:inspect", "scopeIn:compare", "Use this area"))
                else:
                    key = "scopeIn:inspect"  # A schema-valid but wrong area's reference must be repaired.
            answer = {"verdict": "planned", "edits": [
                {"kind": "feature", "targetHandle": "f", "typedBody": {
                    "story": goal["story"], "acceptance": goal["outcome"], "commitments": [key]}},
                {"kind": "requirement", "targetHandle": "r", "typedBody": {
                    "acceptance": goal["outcome"], "features": ["f"]}}],
                "units": [{"scope": facts["Area"]["scope"], "requirements": ["r"], "dependencies": [],
                           "instructions": "Deliver the accepted outcome using the launch agreement"}]}
        else:
            assert kind == "REVIEW", kind  # Stop at B, before any worker calls.
            answer = {"verdict": "adequate", "gaps": []}
        return json.dumps({"answer": {**answer, "why": "Trace the accepted bench outcomes"}})

    run = Run(slot / "product", slot / "workers", dream, scripted.setup, planning, approve)
    store = run.store
    try:
        def call(brief, answer, check, consume=lambda *_: None):
            def provider(q):
                strict_schema(q["schema"])
                return json.dumps({"answer": answer})
            return inference.call(store, provider, brief, check=check, consume=consume)[0]

        initial = {"vision": {"size": "large", "sizeWhy": "Bench simulation and saved comparisons",
            "understanding": "Repeat a comparison using two saved settings", "users": ["Audio engineer"],
            "constraints": [launch_fact], "scopeOut": [],
            "stories": [
                {"handle": "compare", "feature": "Compare runs", "story": "Compare two saved runs",
                 "outcome": "The engineer can see two runs' band differences", "constraints": ["launch"],
                 "sources": []},
                {"handle": "inspect", "feature": "Reopen a stopped run", "story": "Understand a stopped run",
                 "outcome": stopped, "constraints": ["launch"], "sources": []}],
            "quality": [{"handle": "trust", "criterion": "Trustworthy values",
                         "verification": "Compare the bands in both orders",
                         "constraints": [], "sources": []}]},
            "changed": "Expose measured samples on a stopped run.",
            "questions": [{"affects": "scopeIn:compare", "question": "Which measurements should count?",
                           "options": [{"label": "Complete runs", "means": "Compare completed runs only"},
                                       {"label": "Shared interval", "means": "Compare overlapping data"}]}],
            "recommendation": "revise", "why": "A choice affects trust in the comparison"}
        facts = {"Dream": dream, "Runtime": {"python": {"path": "machine-inventory.exe"}}}
        sources = {"kickoff": dream}
        bad_launch = deepcopy(initial)
        bad_launch["vision"]["constraints"][0]["value"] = "machine-inventory.exe"
        first = call(questions.align_round_brief(facts, sources=sources), bad_launch,
                     lambda a: questions.check_align_round(a, sources))
        assert any(b["answer"] == first and b["action"] == "paraphrased"
                   for _, _, b in store.records("answer_normalisation"))
        displayed = store.record("decision_request", {"gate": "A", "subject": {"round": first},
                                   "ask": {"groups": groups}, "origin": "counterfactual"})
        reply = store.record("user_words", {"request": displayed, "words": compare_words,
                                           "origin": "counterfactual"})
        inspect = store.record("user_words", {"request": displayed, "words": inspect_words,
                                             "origin": "counterfactual"})
        interpreted = {}
        call(questions.batch_decide_brief(groups, compare_words),
             {"selections": {"compare": {"optionId": "shared", "text": ""},
                             "inspect": {"optionId": "unclear", "text": ""}},
              "why": "Only the comparison choice is expressed"},
             lambda a: questions.check_batch_decide(a, compare_words),
             lambda _, a: interpreted.update(a["selections"]))
        assert interpreted["compare"]["optionId"] == "shared"
        sources.update({reply: compare_words, inspect: inspect_words})
        revised = deepcopy(initial)
        comparison, inspection = revised["vision"]["stories"]
        comparison.update(outcome="Compare the shared measured interval and label the result partial.",
                          sources=[reply])
        inspection["sources"] = [inspect]
        revised["vision"]["quality"][0]["sources"] = ["kickoff"]
        revised["vision"]["stories"] = [inspection, comparison]
        revised.update(changed="Use the shared interval for comparisons.",
                       questions=[], recommendation="done")

        # Exercise the actual answer consumer and planning states. The growLoop lane owns A's user lock.
        def lock(ref, answer):
            assert questions.vision_body(answer)["stories"][0]["handle"] == "inspect"
            advance(store, "aligned", ref, vision=ref)
            advance(store, "approve", ref)
        locked = deepcopy(questions.vision_body(revised))
        revised["vision"]["quality"][0]["sources"].append("Design guidance used by UI workers")
        call(questions.align_round_brief({**facts, "Previous proposal": store.read(first)["answer"],
                                        "Selected choices": interpreted}, sources=sources), revised,
             lambda a: questions.check_align_round(a, sources), lock)
        for state in ("DETAIL", "REVIEW", "B", "EXECUTE"):
            assert run.step() == state
        assert plan.approved(store)
        at_b_subject = store.read(store.cursor().B)["subject"]
        assert any("trust" in note for note in at_b_subject["normalisations"])
        at_b = at_b_subject["plan"]
        vision = at_b["nodes"]["vision"]["body"]
        assert vision == locked and "machine-inventory.exe" not in json.dumps(vision)
        commitments = questions.commitments(vision)
        traces = {f["story"]: commitments[f["commitments"][0]] for f in plan.select(at_b, "feature").values()}
        compare = traces["Compare two saved runs"]
        assert store.read(compare["sources"][0]) == {
            "request": displayed, "words": compare_words, "origin": "counterfactual"}
        assert [c for c in vision["constraints"] if c["handle"] in compare["constraints"]] == [launch_fact]
        assert store.read(traces["Understand a stopped run"]["sources"][0])["words"] == inspect_words
        assert any(v["field"] == "edits.typedBody.commitments"
                   for _, _, v in store.records("consumption_failure"))
    finally:
        run.close()

@pytest.mark.fast
def test_area_product_journey(tmp_path, scalar_align):
    """Protect interrupted DETAIL; worker-only reopen misses lost completed planning answers."""
    slot = tmp_path/'areas'
    run, _ = prepare(slot, 'contracts', 2)
    class Interrupted(Scripted):
        interrupt = True
        def __call__(self, q):
            if q['kind'] == 'DETAIL' and q['facts']['Area']['handle'] == 'area-2' and self.interrupt:
                self.interrupt = False
                raise RuntimeError('controller interruption')
            return super().__call__(q)
    provider = Interrupted(slot, 'contracts', 2)
    run.provider = provider
    try:
        while run.store.cursor().state != 'DETAIL':
            run.step()
        with pytest.raises(RuntimeError, match='controller interruption'):
            run.step()
        assert any(b['area'] == 'area-1' for _, _, b in run.store.records('area_detail'))
        run.close()
        class Resumed(Interrupted):
            interrupt = False
            def __call__(self, q):
                assert not (q['kind'] == 'DETAIL' and q['facts']['Area']['handle'] == 'area-1')
                return super().__call__(q)
        run = Run.reopen(slot/'product', Resumed(slot,'contracts',2), approve)
        finish(run)
    finally:
        run.close()
