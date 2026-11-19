"""Provider preparation journeys for mission-first briefs and the pinned control."""

import json
import sqlite3
from pathlib import Path

import pytest

from seldon import features, providers, questions
from seldon.store import encode
from test_journey import prepare, strict_schema


def ordered(text, *sections):
    cursor = 0
    for section in sections:
        cursor = text.index(section, cursor) + len(section)


def receipt_at_end(prompt, question):
    marker = "\n\nAnswer schema\n"
    receipt, schema = prompt.rsplit(marker, 1)
    if question["kind"] == "WORK":
        from seldon.desktop import WRITE_RULE
        assert schema.endswith("\n\n" + WRITE_RULE)
        schema = schema.removesuffix("\n\n" + WRITE_RULE)
    assert schema == encode(question["schema"])
    strict_schema(json.loads(schema))
    return json.loads(receipt.splitlines()[-1])["answer"]


def test_first_work_reaches_native_preparation_in_both_arms(tmp_path, monkeypatch):
    """First WORK must reach native preparation with trial coaching confined to the trial arm."""
    monkeypatch.setattr(features, "planning_setup", lambda: {"sizeFirst": True, "split": "auto"})
    monkeypatch.setattr(features, "enabled", lambda name: name not in ("growAlign", "plannerBrief"))
    run, scripted = prepare(tmp_path / "journey")
    invoke = scripted.invoke

    class CaptureFirstWork(BaseException):
        pass

    observed = []

    def capture(question, started):
        if question["kind"] == "WORK":
            assert not question["execution"]["session"]
            observed.append(question)
            raise CaptureFirstWork
        return invoke(question, started)

    monkeypatch.setattr(scripted, "invoke", capture)
    try:
        with pytest.raises(CaptureFirstWork):
            for _ in range(30):
                run.step()
    finally:
        run.close()
    question, = observed
    assert question["renderContext"]["soleOwner"]
    assert question["renderContext"]["guideRole"] == "owned"
    assert question["renderContext"]["audience"] == ["CLI user"]
    for name in ("codex", "claude"):
        setup = {"providers": providers.roles(name), name + "Command": ["unused-native-executable"]}
        provider = providers.Provider(tmp_path / name, setup)
        for arm in (False, True):
            monkeypatch.setattr(features, "enabled", lambda feature: arm if feature == "prompts2" else True)
            folder, invocation = provider.prepare(question)
            prompt = (folder / "prompt.txt").read_text("utf-8")
            instructions = (folder / "instructions.txt").read_text("utf-8")
            recorded = json.loads((folder / "question.json").read_text("utf-8"))
            assert recorded == question
            assert providers.render_question(recorded, name, prompts2=arm) == (prompt, instructions)
            assert invocation["provider"]["provider"] == name
            if name == "claude":
                assert invocation["command"][invocation["command"].index("--json-schema") + 1] == encode(
                    question["schema"])
            else:
                assert "--output-schema" in invocation["command"]
                assert any("model_instructions_file=" in arg for arg in invocation["command"])
            if arm:
                ordered(prompt, "Build the current wave", "Accepted audience",
                        questions.labelled({"User's words": question["facts"]["User's words"]}),
                        "Exact product wording", "words, labels, commands, field names and numbers verbatim",
                        "Apply craft to everything around them", "Current wave", "Write code a senior engineer",
                        "Prove the failure paths and edge cases the requirements imply",
                        "invalid input, empty states, limits and conflicts", "docs/try-it.md", "Assignment facts",
                        "After the work", "Illustration from another assignment", "Answer schema")
                assert prompt.count(instructions) == (1 if name == "claude" else 0)
                if name == "claude":
                    ordered(prompt, "Build the current wave", "Call instruction", "Accepted audience")
                assert "Your peers are building" not in prompt
                assert receipt_at_end(prompt, question)["checks"][0]["command"] == [
                    "python", "checks/preferences.py"]
            else:
                ordered(prompt, instructions, question["prompt"], "Answer schema", "Example", "User's words")
                assert "Accepted audience\n" not in prompt
                assert "Write code a senior engineer" not in prompt
                assert "Exact product wording\n" not in prompt
                assert "Prove the failure paths and edge cases" not in prompt
            for key in ("Requirements", "Write scope", "Existing checks", "Correction owners", "Runtime"):
                assert questions.labelled({key: question["facts"][key]}) in prompt


RECORDINGS = Path("C:/seldon/tests/m1/qbu-dbc5db9-s01/calls")


@pytest.mark.parametrize(("prefix", "sections"), [
    ("87e7843d", ("Who would use this", "Sources", "Rank stories by value")),
    ("8dcbb25b", ("Who would use this", "Sources", "Exact corrections", "rejected value and its source")),
    ("0876e396", ("What must change", "Finished wave", "User check-in", "Preserve completed waves")),
    ("60506a4c", ("What should the user", "Current wave", "Applicable constraints", "one-based unit positions")),
    ("315f0928", ("If the builders delivered", "Accepted commitments", "Judge the supplied plan")),
    ("903e66fa", ("What can this worker", "Unresolved work", "Node 20 executable", "Answer yes or no")),
    ("0f21e094", ("Would the intended user", "Code quality", "Decide whether this unit", "Evidence")),
    ("6a5e00ae", ("Would the intended user", "Code quality", "sourceAcceptance", "fresh-checkout", "Evidence")),
    ("5819ec02", ("Follow the feature story", "Feature", "What do the observations", "Crossed contracts")),
    ("c757d11a", ("Does this delivered experience", "Criteria", "Use final observations")),
    ("715a9190", ("Does this delivered experience", "Criteria", "observed clean-start journey", "Guide file")),
    ("1067a45a", ("Which displayed option", "User's words", "Displayed options", "Waive requires")),
    ("316c807f", ("Continue the existing work", "Exact failure", "Write scope", "Assignment facts")),
])
def test_recorded_decision_keeps_subject_and_native_receipt(prefix, sections):
    """Specialized builders must keep their actual subject and schema when replacing the opening."""
    if not RECORDINGS.is_dir():
        pytest.skip("Quiet Bench as-of-call evidence is available on the ladder rig")
    folder, = RECORDINGS.glob(f"call-{prefix}*")
    question = json.loads((folder / "question.json").read_text(encoding="utf-8"))
    prompt, instructions = providers.render_question(
        question, "claude", [Path("image-0.png")] if question["images"] else [], prompts2=True)
    ordered(prompt, *sections, "Illustrative receipt" if question["kind"] != "WORK" else "Illustration",
            "Answer schema")
    assert prompt.count(instructions) == 1
    if question["images"]:
        ordered(prompt, "Assignment facts" if question["kind"] == "WORK" else "Evidence",
                "Images: open each file", "Illustration" if question["kind"] == "WORK" else
                "Illustrative receipt", "Answer schema")
    answer = receipt_at_end(prompt, question)
    if prefix == "6a5e00ae":
        assert set(answer["findings"]) == set(question["targets"])
        assert answer["findings"]["tryItGuide"]["verdict"] == "uncertain"
        assert "Settings.load" in answer["findings"]["sourceAcceptance"]["why"]
    if prefix == "316c807f":
        assert "User's words\n" not in prompt


@pytest.mark.parametrize("prefix", ["620aaddb", "77d99488", "2338f24d"])
def test_recorded_split_work_keeps_contracts_and_write_scope(prefix):
    """Disjoint owners can have no visible dependency rows; their briefs must still retain peer agreements."""
    calls = RECORDINGS.parent.parent / "shelflife-main-ab2ae1f-s01/calls"
    if not calls.is_dir():
        pytest.skip("ShelfLife as-of-call evidence is available on the ladder rig")
    folder, = calls.glob(f"call-{prefix}*")
    question = json.loads((folder / "question.json").read_text(encoding="utf-8"))
    prompt, _ = providers.render_question(question, prompts2=True)
    if prefix == "2338f24d":
        ordered(prompt, "Help the reader", "this wave's public agreements", "integrated WAVE-VERIFY",
                "one runnable example per CLI command", "Assignment facts")
    else:
        ordered(prompt, "Deliver the assigned part", "senior engineer can trace", "integrated run",
                "Assignment facts")
        assert "You own the complete experience" not in prompt
    for label in ("Write scope", "Contract authority", "Contracts provided", "Contracts consumed"):
        if label in question["facts"]:
            assert questions.labelled({label: question["facts"][label]}) in prompt
    receipt_at_end(prompt, question)


def test_grouped_decide_keeps_the_displayed_record_and_exact_reply():
    """The scalar DECIDE brief could lose unanswered groups or replace request-bound option IDs."""
    state = RECORDINGS.parent / "product/.seldon/state.sqlite3"
    if not state.is_file():
        pytest.skip("Quiet Bench as-of-call evidence is available on the ladder rig")
    with sqlite3.connect(state.as_uri() + "?mode=ro", uri=True) as db:
        request, reply = (json.loads(row[0]) for row in db.execute(
            "SELECT body FROM record WHERE id IN (14,16) ORDER BY id"))
    question = questions.batch_decide_brief(request["ask"]["groups"], reply["words"])
    prompt, _ = providers.render_question(question, prompts2=True)
    ordered(prompt, "User's verbatim words", reply["words"], "Displayed groups", "option ID",
            "Mark unanswered", "only on an explicit round decision", "Answer schema")
    assert set(receipt_at_end(prompt, question)["selections"]) == {"size", "round"}
