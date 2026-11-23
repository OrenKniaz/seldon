"""C8/R18 scripted user journeys through the real development runner."""

import json
from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace

import pytest

import real_hello
from seldon import decisions, schema
from seldon.store import Store
from test_journey import Scripted, VISION, strict_schema


HANDOFF = "Show the last repair attempt and the next safe step at shift change."
OTHER = "Please show the last repair attempt and the next safe step at shift change."
DREAM = "Help the next volunteer pick up where I left off at the repair café."
PERSONA = {"goal": "Find the right item", "priorities": ["Keep the queue moving"],
           "preference": HANDOFF, "exclusion": "No visitor phone numbers"}


@pytest.mark.fast
def test_persona_requests_handoff_once_then_finishes_at_b(tmp_path, monkeypatch):
    """A recorded callback can be retried across reopen, and a proposal must earn scripted A.

    A fixed first-option runner approves the omitted handoff. Existing golden journeys use
    predetermined gate consent and do not exercise the development runner's reply carrier.
    """
    monkeypatch.setenv("SELDON_OFF", "growAlign,sizeFirst")
    monkeypatch.setenv("SELDON_STOP_AT_B", "1")
    for key in ("TEMP", "TMP", "TMPDIR", "SELDON_EVIDENCE_DIR"):
        monkeypatch.setenv(key, str(tmp_path))
    slot = tmp_path / "cohort"
    slot.mkdir()
    judgments = []
    alternatives = [
        {"label": "Item lookup only", "means": "Find a tagged repair on the shared laptop."},
        {"label": "Add a shift list", "means": "Show unfinished item IDs at changeover."},
    ]

    class Planner(Scripted):
        def __call__(self, q):
            strict_schema(q["schema"])
            assert HANDOFF not in json.dumps(q["facts"], ensure_ascii=False)
            assert PERSONA["exclusion"] not in json.dumps(q["facts"], ensure_ascii=False)
            if q["kind"] == "ALIGN":
                assert q["facts"]["Dream"] == DREAM
                covering = OTHER in str(q["facts"].get("Requested change", ""))
                scope = "Find a tagged repair."
                answer = {**deepcopy(VISION), "alternatives": alternatives, "why": "Scripted planning",
                          "scopeIn": [f"{scope} {OTHER}" if covering else scope]}
            elif q["kind"] == "DECIDE":
                assert q["facts"]["User's words"] == OTHER
                answer = {"choice": "Other", "why": "Counterfactual scripted revision"}
            else:
                return super().__call__(q)
            payload = {"answer": answer}
            schema.validate(payload, q["schema"])
            return json.dumps(payload)

    class CounterfactualVolunteer:
        def __call__(self, q):
            strict_schema(q["schema"])
            assert q["facts"]["Private persona"] == PERSONA
            ask = q["facts"]["Displayed request"]
            assert [o["label"] for o in ask["ask"]["options"]] == [
                "Build this", "Item lookup only", "Add a shift list", "Other"]
            covering = OTHER in ask["subject"]["vision"]["scopeIn"][0]
            words = "Build this" if covering else OTHER
            judgments.append(words)
            payload = {"answer": {"words": words}}
            schema.validate(payload, q["schema"])
            return json.dumps(payload)

    planner = Planner(slot)
    monkeypatch.setattr(real_hello, "setup_facts", lambda: planner.setup)
    monkeypatch.setattr(real_hello, "Provider", lambda root, setup: (
        planner if root.name == "calls" else CounterfactualVolunteer()))
    record = Store.record
    interrupted = True

    def lose_delivery(self, kind, body):
        if kind == "user_words" and interrupted:
            raise decisions.Waiting()
        return record(self, kind, body)

    monkeypatch.setattr(Store, "record", lose_delivery)
    assert real_hello.run(slot, False, DREAM, script={"persona": PERSONA}) == 0
    with closing(Store(slot / "product")) as store:
        assert store.records("user_words") == []
    assert judgments == [OTHER]
    interrupted = False
    assert real_hello.run(slot, False, reopen=True) == 0
    with closing(Store(slot / "product")) as store:
        assert store.cursor().state == "B"
        assert store.cursor().B is None
        assert [b["words"] for _, _, b in store.records("user_words")] == [OTHER, "Build this"]
        assert all(b["origin"] == "scripted journey" for _, _, b in store.records("user_words"))
    assert judgments == [OTHER, "Build this"]


@pytest.mark.fast
@pytest.mark.parametrize("control", [False, True])
def test_grouped_runner_emits_addressed_choices(tmp_path, monkeypatch, capsys, control):
    """Exercise the runner over the grouped chat contract seam, which is absent on the lane base.

    Scalar replay cannot catch dropping Other's group binding or choosing the round's first option.
    """
    for key in ("TEMP", "TMP", "TMPDIR", "SELDON_EVIDENCE_DIR"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setenv("SELDON_OFF", "growPersona" if control else "")
    monkeypatch.setenv("SELDON_STOP_AT_B", "1")
    handoff = {"id": "handoff", "question": "What can the next shift see?", "default": "list",
               "options": [{"id": "list", "label": "Unfinished items", "means": "Show item IDs"},
                           {"id": "later", "label": "Decide later", "means": "Leave handoff open"}]}
    round_group = {"id": "round", "question": "Is alignment done?", "default": "revise",
                   "options": [{"id": "revise", "label": "Keep shaping", "means": "See another draft"},
                               {"id": "done", "label": "This is it", "means": "Accept these defaults"}]}
    replies = []
    requests = []

    class CounterfactualVolunteer:
        def __call__(self, q):
            assert not control
            strict_schema(q["schema"])
            request = q["facts"]["Displayed request"]
            assert request["ask"]["groups"] == [handoff, round_group]
            covering = request["subject"]["vision"] == OTHER
            custom = {"groupId": "handoff", "optionId": "other", "text": OTHER}
            answer = {"decision": {"groupId": "round", "optionId": "done" if covering else "revise"},
                      "choices": [] if covering else [custom]}
            payload = {"answer": answer}
            schema.validate(payload, q["schema"])
            return json.dumps(payload)

    class GroupedDecisionContract:
        """Recorded-display twin for the integration-owned decision consumer."""

        def __init__(self, project, scratch, dream, setup, provider, user):
            self.project = project
            self.user = user
            self.store = SimpleNamespace(records=lambda kind: [(row["request"], kind, row) for row in
                {"user_words": replies, "decision_request": requests}.get(kind, [])])

        def run(self):
            for index, vision in enumerate(["Find unfinished items", OTHER], 10):
                rid = f"r{index}"
                request = {"request": rid, "gate": "A", "subject": {"vision": vision},
                           "ask": {"groups": [handoff, round_group]}}
                requests.append(request)
                monkeypatch.setattr(real_hello, "state", lambda run: {"waitingFor": request})
                replies.append(self.user(request))
                if replies[-1]["selections"][-1]["optionId"] == "done":
                    break
            assert self.user({"request": "r12", "gate": "CHECK-IN", "ask": {"options": ["approve"]}}) == "approve"
            return SimpleNamespace(decision=None, state="A")

        def close(self):
            pass

    monkeypatch.setattr(real_hello, "Run", GroupedDecisionContract)
    monkeypatch.setattr(real_hello, "setup_facts", lambda: {"providers": {}})
    monkeypatch.setattr(real_hello, "Provider", lambda root, setup: CounterfactualVolunteer())
    assert real_hello.run(tmp_path, False, DREAM, script={"persona": PERSONA}) == 0
    transcript = [json.loads(line) for line in capsys.readouterr().out.splitlines()
                  if line.startswith('{"simUserAnswer"')]
    assert [line["mode"] for line in transcript if line["gate"] == "A"] == (
        ["scripted transport control"] if control else ["scripted persona"] * 2)
    assert replies == ([{"request": "r10", "replyId": "simulated-r10", "selections": [
        {"groupId": "handoff", "optionId": "list"}, {"groupId": "round", "optionId": "done"}]}]
        if control else [
            {"request": "r10", "replyId": "simulated-r10", "selections": [
                {"groupId": "handoff", "optionId": "other", "text": OTHER},
                {"groupId": "round", "optionId": "revise"}]},
            {"request": "r11", "replyId": "simulated-r11", "selections": [
                {"groupId": "round", "optionId": "done"}]},
        ])
