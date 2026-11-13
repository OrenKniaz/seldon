"""C11; R2/R3: plan approval requires bounded, independently combined area reviews."""

import json
import threading
import time
from copy import deepcopy
from pathlib import Path

from seldon import plan
from seldon.run import Run
from test_architect import AreaProvider, start
from test_journey import Scripted, approve, finish, prepare


class ReviewProvider(AreaProvider):
    def __init__(self, slot, mode):
        super().__init__(slot, 3, "contracts")
        self.barrier, self.interrupted = None, True
        self.review_mode = mode
        self.review_calls = []
        self.review_barrier = threading.Barrier(3)

    def __call__(self, question):
        kind, facts = question["kind"], question["facts"]
        if kind == "REVIEW":
            subject = facts["Review subject"]
            with self.lock:
                first = not any(q["facts"]["Review subject"] == subject for q in self.review_calls)
                self.review_calls.append(deepcopy(question))
            if first and subject != "architecture":
                self.review_barrier.wait(timeout=10)
            gap = None
            if (self.review_mode in ("area", "area-records", "persistent") and subject == "area-002"
                    and not any("restart" in c["purpose"] for c in
                                plan.select(facts["Plan"], "check").values())):
                gap = "Plan a check that reads the persisted value after restart"
            if self.review_mode == "cross" and subject == "architecture":
                contract = facts["Architecture outline"]["contracts"][0]
                if "restart" not in contract["text"]:
                    gap = "The first boundary must specify how consumers read a value after restart"
            answer = {"verdict": "gap" if gap else "adequate",
                      "gaps": [{"target": "persistence", "correction": gap}] if gap else [],
                      "why": "The supplied records cover the responsibility and its agreements"}
            return json.dumps({"answer": answer})
        answer = json.loads(super().__call__(question))
        if kind == "ARCHITECT" and "Architecture correction" in facts:
            answer["answer"]["contracts"][0]["text"] += " Consumers can read the value after restart."
        if (kind == "DETAIL" and "Merge correction" in facts
                and self.review_mode in ("area", "area-records")
                and facts["Area"]["handle"] == "area-002"):
            next(e for e in answer["answer"]["edits"] if e["kind"] == "check")[
                "typedBody"]["purpose"] += " after restart"
            if self.review_mode == "area-records":
                body = answer["answer"]
                requirement = deepcopy(next(e for e in body["edits"] if e["kind"] == "requirement"))
                requirement["targetHandle"] = "restart-requirement"
                requirement["typedBody"]["obligation"] += " after restart"
                check = deepcopy(next(e for e in body["edits"] if e["kind"] == "check"))
                check["targetHandle"] = "restart-check"
                check["typedBody"]["requirements"] = ["restart-requirement"]
                body["edits"].extend([requirement, check])
                body["units"][0]["requirements"].append("restart-requirement")
        return json.dumps(answer)


def review_journeys(root):
    """Architecture is reviewed before three concurrent area verdicts and one B; gaps replan their owners.

    DETAIL success cannot catch a missing review, serialized fan-out, a false adequate aggregate,
    or lost sibling verdicts after repair/reopen, including when new records renumber sibling handles.
    A cross-area persistence gap must reach ARCHITECT,
    while repeated unchanged answers must exhaust three repairs rather than approve or loop.
    """
    began = time.monotonic()
    for mode in ("adequate", "area", "area-records", "cross", "persistent"):
        slot = root / mode
        run, base = start(slot)
        provider = ReviewProvider(base.slot, mode)
        run.provider = provider
        try:
            while run.store.cursor().state not in ("B", "REPAIR", "ARCHITECT") or not provider.arch_calls:
                run.step()
            if mode == "cross":
                assert run.store.cursor().state == "ARCHITECT" and not provider.detail_calls
                assert provider.review_calls[0]["facts"]["Review subject"] == "architecture"
                run.close()
                run = Run.reopen(slot / "product", provider, approve)
                while run.store.cursor().state != "B":
                    run.step()
                assert provider.arch_calls == 2
                assert len(provider.review_calls) == 5
                assert all("restart" in q["facts"]["Contracts"]["link-1"]["text"]
                           for q in provider.detail_calls if "link-1" in q["facts"]["Contracts"])
                assert run.step() == "EXECUTE"
                continue
            assert len(provider.review_calls) == 4
            initial = run.store.read(run.store.cursor().review)
            assert set(initial["subjects"]) == {"area-001", "area-002", "area-003", "architecture"}
            for q in provider.review_calls:
                facts = q["facts"]
                if facts["Review subject"] == "architecture":
                    assert "Plan" not in facts and "unit-001" not in json.dumps(facts)
                    assert "Per-area coverage counts" not in facts
                    assert "How the plan executes" not in facts
                    assert "check sources" not in json.dumps(q)
                else:
                    units = plan.select(facts["Plan"], "unit")
                    assert len(units) == 1
                    assert facts["Trace coverage"] and facts["Contracts"]
            if mode != "adequate":
                assert run.store.cursor().state == "REPAIR" and not run.store.cursor().B
                assert initial["answer"]["gaps"][0]["area"] == (
                    "area-002")
                run.close()
                run = Run.reopen(slot / "product", provider, approve)
                if mode == "persistent":
                    assert run.run().state == "INCOMPLETE"
                    assert run.store.cursor().repairs == 3
                    reason = run.store.records("outcome")[-1][2]["reason"]
                    assert "Three pre-B plan repairs exhausted" in reason
                    assert not run.store.cursor().B
                    continue
                while run.store.cursor().state != "B":
                    run.step()
                final = run.store.read(run.store.cursor().review)
                assert initial["subjects"]["area-003"] == final["subjects"]["area-003"]
                if mode in ("area", "area-records"):
                    assert provider.arch_calls == 1
                    assert [q["facts"]["Area"]["handle"] for q in provider.detail_calls[3:]] == ["area-002"]
                    assert {q["facts"]["Review subject"] for q in provider.review_calls[4:]} == (
                        {"area-002"})
                    assert initial["subjects"]["architecture"] == final["subjects"]["architecture"]
                    assert initial["subjects"]["area-001"] == final["subjects"]["area-001"]
            assert run.step() == "EXECUTE"
            assert len([b for _, _, b in run.store.records("decision_request") if b["gate"] == "B"]) == 1
            assert run.store.cursor().repairs == (0 if mode == "adequate" else 1)
        finally:
            run.close()
    print(f"Area REVIEW adequate/local/cross/three-repair limit: {time.monotonic()-began:.2f}s", flush=True)


def uncertain_review_journey(root):
    """Unknown review facts remain visible through B, reopen and C; an area can resolve from real facts.

    Adequate/gap journeys miss fatal MissingFacts, repeated empty briefs, and a falsely adequate
    aggregate. The user-facing B subject and terminal outcome must retain the missing judgment.
    """
    run, base = prepare(root, "review-uncertain", 1)

    class UncertainProvider(Scripted):
        def __call__(self, question):
            if question["kind"] != "REVIEW":
                return super().__call__(question)
            facts = question["facts"]
            if facts["Review subject"] == "architecture":
                if "Requested facts" in facts:
                    supplied = facts["Requested facts"]["Available subject facts"]
                    assert supplied["Accepted commitments"] and supplied["Runtime"]
                    assert "have not been written" in supplied["Planning stage"]
                return json.dumps({"answer": {"verdict": "uncertain",
                    "neededFacts": ["A user study confirming that the responsibilities meet the audience needs"],
                    "why": "The outline has no user-study results"}})
            if "Requested facts" not in facts:
                return json.dumps({"answer": {"verdict": "uncertain",
                    "neededFacts": ["The original area plan and the product currently in that area"],
                    "why": "Need the recorded area answer and current product inventory"}})
            supplied = facts["Requested facts"]["Available subject facts"]
            assert supplied["Recorded area plan"]["units"]
            assert supplied["Product in this area"] == {"files": [], "additionalFiles": 0}
            return json.dumps({"answer": {"verdict": "adequate", "gaps": [],
                                          "why": "The recorded plan covers this area"}})

    run.provider = provider = UncertainProvider(base.slot, base.mode, 1)
    try:
        while run.store.cursor().state != "B":
            run.step()
        revision = run.store.cursor().revision
        # B is an actual user request, then reopen the run: pending judgment is durable.
        requests = []
        def user(request):
            if request["gate"] == "B":
                requests.append(request)
            return approve(request)
        run.user = user
        run.step()
        review = requests[0]["subject"]["review"]
        assert review["answer"] == {"verdict": "not judged", "gaps": []}
        assert "not judged" in review["reviewTier"]
        assert review["notJudged"]["architecture"]["reason"] == "The outline has no user-study results"
        assert "user study" in review["notJudged"]["architecture"]["neededFacts"][0]
        area = next(s for s in review["subjects"] if s != "architecture")
        assert run.store.read(review["subjects"][area])["answer"]["verdict"] == "adequate"
        run.close()
        run = Run.reopen(root / "product", provider, approve)
        assert run.store.cursor().revision == revision
        finish(run)
        outcome = run.store.records("outcome")[-1][2]
        assert outcome["verification_complete"] is False
        assert outcome["notJudged"]["review:architecture"] == "not judged: The outline has no user-study results"
        qs = [q for _, _, q in run.store.records("question") if q["kind"] == "REVIEW"]
        assert len(qs) == 4
        assert all(qs[i]["facts"] != qs[i+1]["facts"] for i in (0, 2))
    finally:
        run.close()


def recorded_review_journey(root):
    """Replay y01's literal planning/review answers through B, including its concrete keyboard gap.

    Scripted uncertainty misses the real model's requests for details at the wrong review boundary;
    retaining the real area gap catches a fix that reclassifies a concrete defect as not judged.
    Only new fact/correction questions receive scripted replies. No product execution is claimed.
    """
    recording = json.loads(Path(__file__).with_name("recorded_review.json").read_text("utf-8"))
    root.mkdir(parents=True)

    class Replay(Scripted):
        def __init__(self):
            super().__init__(root, "recorded-review", 1)
            self.used = set()
            self.corrected = False

        def __call__(self, question):
            kind, facts = question["kind"], question["facts"]
            subject = facts.get("Review subject", facts.get("Area", {}).get("handle", ""))
            if kind == "REVIEW" and "Requested facts" in facts:
                return json.dumps({"answer": {"verdict": "uncertain",
                    "neededFacts": ["Detailed requirements and checks are not available before DETAIL"],
                    "why": "Not judged: detailed plans are outside the outline review"}})
            if kind == "DETAIL" and "Merge correction" in facts:
                assert subject == "seldon"
                assert "Tab navigation" in facts["Merge correction"][0]["error"]
                answer = deepcopy(facts["Previous area plan"])
                for edit in answer["edits"]:
                    if edit["kind"] == "requirement":
                        edit["typedBody"]["obligation"] += (
                            " Reach the Seldon button with Tab navigation, show focus, activate with Enter/Space.")
                    if edit["kind"] == "check":
                        edit["typedBody"]["purpose"] += (
                            " Reach the Seldon button by Tab, verify visible focus and Enter/Space activation.")
                self.corrected = True
                return json.dumps({"answer": answer})
            if kind == "REVIEW" and subject == "seldon" and self.corrected:
                assert any("by Tab" in c["purpose"] for c in plan.select(facts["Plan"], "check").values())
                return json.dumps({"answer": {"verdict": "adequate", "gaps": [],
                    "why": "The corrected plan includes keyboard reachability and activation"}})
            for i, reply in enumerate(recording["replies"]):
                original_subject = "cross-area" if subject == "architecture" else subject
                if i not in self.used and reply["kind"] == kind and reply["subject"] == original_subject:
                    self.used.add(i)
                    return reply["raw"]
            raise AssertionError((kind, subject, list(facts)))

    provider = Replay()
    run = Run(root / "product", root / "workers", recording["dream"], provider.setup,
              provider, approve, capacity=6)
    try:
        for _ in range(20):
            if run.store.cursor().state == "EXECUTE":
                break
            run.step()
        assert run.store.cursor().state == "EXECUTE" and run.store.cursor().B
        assert provider.corrected and len(provider.used) == len(recording["replies"])
        replies = [b["rawAnswer"] for _, _, b in run.store.records("provider_reply")]
        assert all(r["raw"] in replies for r in recording["replies"])
        reviews = [b for _, _, b in run.store.records("plan_review")]
        assert reviews[0]["answer"]["verdict"] == "gap"
        assert reviews[0]["answer"]["gaps"][0]["area"] == "seldon"
        assert reviews[-1]["answer"] == {"verdict": "not judged", "gaps": []}
        assert not run.store.units()
        print("Recorded y01 ARCHITECT/DETAIL/area REVIEW -> corrected B; architecture not judged", flush=True)
    finally:
        run.close()
