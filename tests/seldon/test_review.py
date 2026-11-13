"""C11; R2/R3: plan approval requires bounded, independently combined area reviews."""

import json
import threading
import time
from copy import deepcopy

from seldon import plan
from seldon.run import Run
from test_architect import AreaProvider, start
from test_journey import approve


class ReviewProvider(AreaProvider):
    def __init__(self, slot, mode):
        super().__init__(slot, 3, "contracts")
        self.barrier, self.interrupted = None, True
        self.review_mode = mode
        self.review_calls = []
        self.review_barrier = threading.Barrier(4)

    def __call__(self, question):
        kind, facts = question["kind"], question["facts"]
        if kind == "REVIEW":
            subject = facts["Review subject"]
            with self.lock:
                first = not any(q["facts"]["Review subject"] == subject for q in self.review_calls)
                self.review_calls.append(deepcopy(question))
            if first:
                self.review_barrier.wait(timeout=10)
            gap = None
            if (self.review_mode in ("area", "area-records", "persistent") and subject == "area-002"
                    and not any("restart" in c["purpose"] for c in
                                plan.select(facts["Plan"], "check").values())):
                gap = "Plan a check that reads the persisted value after restart"
            if self.review_mode == "cross" and subject == "cross-area":
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
    """Three areas need four concurrent verdicts and one B; gaps replan only their owners.

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
            while run.store.cursor().state not in ("B", "REPAIR"):
                run.step()
            assert len(provider.review_calls) == 4
            initial = run.store.read(run.store.cursor().review)
            assert set(initial["subjects"]) == {"area-001", "area-002", "area-003", "cross-area"}
            for q in provider.review_calls:
                facts = q["facts"]
                if facts["Review subject"] == "cross-area":
                    assert "Plan" not in facts and "unit-001" not in json.dumps(facts)
                    assert set(facts["Per-area coverage counts"]) == {
                        "area-001", "area-002", "area-003"}
                else:
                    units = plan.select(facts["Plan"], "unit")
                    assert len(units) == 1
                    assert facts["Trace coverage"] and facts["Contracts"]
            if mode != "adequate":
                assert run.store.cursor().state == "REPAIR" and not run.store.cursor().B
                assert initial["answer"]["gaps"][0]["area"] == (
                    "cross-area" if mode == "cross" else "area-002")
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
                        {"area-002"} if mode == "area" else {"area-002", "cross-area"})
                    assert initial["subjects"]["area-001"] == final["subjects"]["area-001"]
                else:
                    assert provider.arch_calls == 2
                    assert {q["facts"]["Area"]["handle"] for q in provider.detail_calls[3:]} == {
                        "area-001", "area-002"}
                    assert {q["facts"]["Review subject"] for q in provider.review_calls[4:]} == {
                        "area-001", "area-002", "cross-area"}
            assert run.step() == "EXECUTE"
            assert len([b for _, _, b in run.store.records("decision_request") if b["gate"] == "B"]) == 1
            assert run.store.cursor().repairs == (0 if mode == "adequate" else 1)
        finally:
            run.close()
    print(f"Area REVIEW adequate/local/cross/three-repair limit: {time.monotonic()-began:.2f}s", flush=True)
