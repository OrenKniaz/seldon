"""R7: a failing requirement reopens its owners, preserving its feature's other units."""

import json

import pytest

from seldon import plan

from .test_journey import Scripted, finish, prepare


class RequirementFlow(Scripted):
    def __call__(self, question):
        reply = json.loads(super().__call__(question))
        if question["kind"] == "FLOW" and any(
                "interaction blocked" in row["stdout"]
                for row in question["facts"]["Final observations"]):
            target = question["targets"][0]
            reply["answer"].update(verdict="gap", gaps=[
                {"target": target, "correction": "Make the blocked interaction ready"},
                {"target": target, "correction": "Assert that the interaction is ready"}])
        return json.dumps(reply)


@pytest.mark.fast
def test_requirement_gap_preserves_other_feature_owner_to_c(tmp_path):
    """Catch feature-wide repair from a real FLOW answer through fixups and worker integration.

    Existing final-gap journeys have one requirement per feature; the shared-page journey's
    composition check legitimately reaches both units, so neither exposes over-broad ownership.
    """
    slot = tmp_path / "delivery"
    run, _ = prepare(slot, "demo-gap", 2)
    provider = RequirementFlow(slot, "demo-gap", 2)

    def one_feature(answer):
        answer["edits"] = [e for e in answer["edits"] if e["targetHandle"] != "new:f002"]
        requirement = next(e for e in answer["edits"] if e["targetHandle"] == "new:r002")
        requirement["typedBody"]["features"] = ["new:f001"]

    provider.plan_fault = one_feature
    run.provider = provider
    try:
        while run.store.cursor().state != "FLOW-JUDGE":
            assert run.store.cursor().state != "INCOMPLETE", run.store.records("outcome")
            run.step()
        store = run.store
        document = store.plan(store.cursor().revision)
        assert len(plan.select(document, "feature")) == 1
        assert len(plan.select(document, "requirement")) == 2
        before = store.units()
        assert before["u001"].state == before["u002"].state == "DONE"
        verdict = before["u002"].judgment
        assert store.read(verdict)["answer"]["verdict"] == "satisfied"

        assert run.step() == "EXECUTE"
        assert store.unit("u001").state == "REWORK"
        assert store.unit("u002") == before["u002"]
        assert store.unit("guide") == before["guide"]

        _, rows, after = finish(run)
        assert after["u002"] == before["u002"]
        assert after["u002"].judgment == verdict
        assert (run.project / "unit-001/interaction.txt").read_text() == "ready"
        launches = [json.loads(line) for line in
                    (slot / "launch-unit-001.jsonl").read_text().splitlines()]
        assert len(launches) == 2 and launches[-1]["resumed"]
        correction = launches[-1]["facts"]["Scoped corrections"]["corrections"]
        assert len(correction) == 1
        assert "Make the blocked interaction ready" in correction[0]
        assert "Assert that the interaction is ready" in correction[0]
        assert len((slot / "launch-unit-002.jsonl").read_text().splitlines()) == 1
        judgments = [q for _, kind, q in rows if kind == "question" and q["kind"] == "JUDGE"]
        other = plan.select(document, "unit")["u002"]["requirements"]
        assert sum(set(other).intersection(q["targets"]) != set() for q in judgments) == 1
    finally:
        run.close()
