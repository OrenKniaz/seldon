"""Fresh WORK coaching is exercised at the worker's actual input boundary."""

import json

import pytest

from test_journey import Scripted, finish, prepare


@pytest.mark.fast
def test_fresh_product_work_coaches_a_real_path(tmp_path, monkeypatch):
    """Fresh product owners get path coaching with their assignment; retries keep the failure brief.

    The planner-brief journey checks that prose survives compilation. It would miss a
    coaching instruction dropped before provider input, or one added to a guide or retry.
    """
    monkeypatch.setenv("SELDON_ON", "plannerBrief")
    monkeypatch.setenv("SELDON_OFF", "growAlign,sizeFirst")
    assignment = "Help the reader find a value saved by a previous session."
    scripted_answer = Scripted.__call__

    def answer(self, question):
        raw = scripted_answer(self, question)
        if question["kind"] != "DETAIL":
            return raw
        response = json.loads(raw)
        for unit in response["answer"]["units"]:
            unit["brief"] = assignment
        if "Separate walkthrough" in question["facts"]:
            response["answer"]["guideBrief"] = "Show a new reader how to find the saved values."
        return json.dumps(response)

    monkeypatch.setattr(Scripted, "__call__", answer)
    slot = tmp_path / "paths"
    run, _ = prepare(slot, "wave-fault", count=2)
    try:
        finish(run)
    finally:
        run.close()

    fresh, retained, guides = [], [], []
    for folder in sorted((slot / "calls").glob("call-*")):
        question = json.loads((folder / "question.json").read_text(encoding="utf-8"))
        if question["kind"] != "WORK":
            continue
        prompt = (folder / "prompt.txt").read_text(encoding="utf-8")
        if question["execution"]["session"]:
            retained.append(prompt)
        elif "Guide path" in question["facts"]:
            guides.append(prompt)
        else:
            fresh.append(prompt)

    assert len(fresh) == 2 and len(retained) == len(guides) == 1
    for number, prompt in enumerate(sorted(fresh), 1):
        assert prompt.startswith(assignment + "\n\nWrite scope\n")
        assert f"Running the reader displays saved value-{number:03}." in prompt
        assert "Existing checks" in prompt
        coaching = prompt.partition("Exercise an acceptance path\n")[2].split("\n\n", 1)[0]
        assert "real entry point" in coaching and "before returning" in coaching
        assert coaching.index("observe the operation running") < coaching.index("assert its terminal state")
    assert "integrated mismatch" in retained[0]
    assert all("Exercise an acceptance path" not in prompt for prompt in retained + guides)
