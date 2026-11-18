"""Grouped R19 replay. Run explicitly with pytest tests/seldon/size_choice_journey.py."""

import json
from pathlib import Path

import pytest

from seldon import frontdoor, gitwork, plan, schema
from seldon.run import Run
from test_journey import strict_schema


@pytest.mark.fast
def test_quiet_bench_effort_override_keeps_requested_waves(tmp_path, monkeypatch):
    """A small effort estimate can coexist with staged delivery across a restart.

    The mixed-reply golden journey proves approval durability, but does not check the
    effort menu or whether a size override changes the requested execution shape.
    """
    monkeypatch.delenv("SELDON_OFF", raising=False)
    monkeypatch.delenv("SELDON_SPLIT", raising=False)
    fixture = Path(__file__).with_name("fixtures") / "quietbench-a01-planning.json"
    recorded = json.loads(fixture.read_text(encoding="utf-8"))
    old = recorded["ALIGN"]["answer"]
    staging = next(value for value in old["scopeIn"] if value.startswith("Build and show three waves"))
    dream = "\n".join(old["scopeIn"])
    effort = "The comparable plain-agent run completed in about three minutes in one session."
    vision = {
        "size": old["size"], "sizeWhy": old["why"], "understanding": old["understanding"],
        "users": old["users"],
        "stories": [{"handle": f"bench-{i}", "feature": "Quiet Bench", "story": old["users"][0],
                     "outcome": value, "constraints": ["staging"], "sources": ["kickoff"]}
                    for i, value in enumerate(old["scopeIn"]) if value != staging],
        "constraints": [{"handle": "staging", "value": staging, "meaning": "Requested checkpoints",
                         "source": "kickoff"}],
        "quality": [{**q, "handle": f"q{i}", "sources": ["kickoff"], "constraints": []}
                    for i, q in enumerate(old["quality"])],
        "scopeOut": [{"outcome": value, "sources": ["kickoff"]} for value in old["scopeOut"]],
    }
    seen = {}

    def provider(q):
        strict_schema(q["schema"])
        kind, facts = q["kind"], q["facts"]
        seen[kind] = q
        if kind == "ALIGN":
            override = facts.get("Owner size override")
            revised = {**vision, "size": override, "sizeWhy": effort} if override else vision
            answer = {"vision": revised, "changed": old["understanding"], "questions": [],
                      "recommendation": "done", "why": old["why"]}
        elif kind == "ARCHITECT":
            goals = list(facts["Commitment handles"])
            answer = {"areas": [{"handle": "bench", "responsibility": "Deliver Quiet Bench",
                                "scope": ["bench/**"], "commitments": goals}],
                      "waves": [{**w, "commitments": goals} for w in recorded[kind]["answer"]["waves"]],
                      "contracts": [], "decisions": [], "why": "One owner with three requested checkpoints."}
        elif kind == "DETAIL":
            answer = {"verdict": "planned", "edits": [
                {"kind": "feature", "targetHandle": "bench", "typedBody": {
                    "story": "Try the fixed-filter bench", "acceptance": "Run and save a fixed-filter tone",
                    "commitments": list(facts["Accepted commitments"])}},
                {"kind": "requirement", "targetHandle": "fixed", "typedBody": {
                    "acceptance": "Reopen the same saved tone", "features": ["bench"]}}],
                "units": [{"scope": ["bench/**"], "requirements": ["fixed"], "dependencies": [],
                           "instructions": "Run and save a fixed-filter tone"}], "why": "First working slice."}
        else:
            pytest.fail(f"Unexpected {kind} question")
        schema.validate({"answer": answer}, q["schema"])
        return json.dumps({"answer": answer})

    def choose(item, group, option):
        return {"request": item["request"], "replyId": f"{group}-{option}",
                "selections": [{"groupId": group, "optionId": option}]}

    product = tmp_path / "quiet-bench"
    run = Run(product, tmp_path / "workers", dream, {}, provider, lambda _: None)
    try:
        assert run.step() == "A"
        shown = frontdoor.state(product)["waitingFor"]
        size = next(g for g in shown["ask"]["groups"] if g["id"] == "size")
        assert [o["label"] for o in size["options"]] == ["Big", "Micro", "Small", "Medium", "Other"]
        assert size["default"] == old["size"]
        scale = {o["id"]: o["means"] for o in size["options"] if "id" in o}
        for option, time in zip(("tiny", "small", "medium", "large"), ("minutes", "days", "weeks", "months")):
            assert time in scale[option].lower()
        assert "one builder" in scale["tiny"]
        run.user = lambda item: choose(item, "size", "tiny")
        assert run.step() == "ALIGN"
        run.close()
        monkeypatch.setenv("SELDON_OFF", "sizeFirst")
        monkeypatch.setenv("SELDON_SPLIT", "force")
        run = Run.reopen(product, provider, lambda _: None)
        assert run.step() == "A"
        revised = frontdoor.state(product)["waitingFor"]
        assert revised["subject"]["vision"]["size"] == "tiny"
        assert revised["subject"]["vision"]["constraints"] == vision["constraints"]
        size = next(g for g in revised["ask"]["groups"] if g["id"] == "size")
        assert size["options"][0]["label"] == "Micro"
        assert effort in size["options"][0]["means"]
        assert run.store.cursor().A is None and "ARCHITECT" not in seen
        run.user = lambda item: choose(item, "round", "done")
        assert run.step() == "ARCHITECT"
        assert run.step() == "DETAIL"
        assert run.step() == "REVIEW"
        arch = seen["ARCHITECT"]["facts"]
        detail = seen["DETAIL"]["facts"]
        assert arch["Approved vision"]["size"] == "tiny"
        assert arch["Approved vision"]["constraints"][0]["value"] == staging
        assert "auto" == arch["Planning approach"]["split"] == detail["Planning approach"]["split"]
        assert "requested" in seen["ARCHITECT"]["prompt"] and "requested" in seen["DETAIL"]["prompt"]
        current = run.store.cursor()
        assert run.store.read(current.A)["request"] == revised["request"]
        compiled = run.store.plan(current.revision)
        assert len(compiled["architecture"]["waves"]) == 3
        requirement, = plan.select(compiled, "requirement").values()
        feature, = requirement["features"]
        assert compiled["nodes"][feature]["body"]["commitments"] == list(detail["Accepted commitments"])
        assert detail["Applicable constraints"]["staging"]["value"] == staging
        assert gitwork.git(product, "rev-parse", "HEAD").strip() == current.candidate
    finally:
        run.close()
