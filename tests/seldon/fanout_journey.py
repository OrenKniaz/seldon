"""T-155: real planning, workers and integrated evidence through final subject dispatch."""

import json
import threading
import time
from collections import Counter

import pytest

from seldon import gitwork, plan
from seldon.run import Run
from test_journey import approve, finish, prepare


def final_fanout_journey(slot, stage, case):
    mode = ("demo-gap" if stage == "FLOW" else "product-gap") if case == "gaps" else "fanout"
    run, provider = prepare(slot, mode, 3)
    try:
        while run.store.cursor().state != stage + "-JUDGE":
            assert not run.store.cursor().decision
            run.step()
        cursor = run.store.cursor()
        document = run.store.plan(cursor.revision)
        subjects = list(plan.select(document, "feature") if stage == "FLOW" else
                        {**plan.select(document, "criterion"), "tryItGuide": {}})
        targets = [tuple(r for r, b in plan.select(document, "requirement").items()
                         if f in b["features"]) for f in subjects] if stage == "FLOW" else [
                             (s,) for s in subjects]
        barrier = threading.Barrier(len(subjects))
        calls = Counter()
        lock = threading.Lock()
        candidate = cursor.candidate
        before = run.store.units()

        class Interrupted(BaseException):
            pass

        def answer(question):
            if question["kind"] != stage:
                return provider(question)
            target = tuple(question["targets"])
            with lock:
                calls[target] += 1
                number = len(calls)
            barrier.wait(timeout=15)  # Hang guard; overlap is proven by rendezvous, never elapsed time.
            assert run.store.cursor().candidate == candidate
            assert run.store.units() == before
            assert gitwork.git(run.project, "rev-parse", "HEAD") == candidate
            if case == "restart" and number == len(subjects):
                deadline = time.monotonic() + 15
                while len([b for _, _, b in run.store.records("answer")
                           if b["kind"] == stage]) < len(subjects) - 1:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Other subject replies were not recorded")
                    time.sleep(.01)
                raise Interrupted()
            reply = json.loads(provider(question))
            if case == "uncertain":
                return json.dumps({"answer": {"verdict": "uncertain", "neededFacts": [
                    "The visual observation remains unavailable"], "why": "Need more facts"}})
            if case == "gaps" and target in targets[:2]:
                reply["answer"].update(verdict="gap", gaps=[{
                    **({"owners": [f"u{targets.index(target)+1:03}"]} if stage == "FLOW" else {}),
                    "target": question["targets"][0], "correction": "Correct " + str(target)}])
            return json.dumps(reply)

        run.provider = answer
        began = time.monotonic()
        if case == "restart":
            with pytest.raises(Interrupted):
                run.step()
            recorded = [b for _, _, b in run.store.records("answer") if b["kind"] == stage]
            assert len(recorded) == len(subjects) - 1
            completed = set(calls)
            # The interrupted subject has no answer; retain counts across a fresh controller.
            run.close()
            def resumed(question):
                if question["kind"] == stage:
                    calls[tuple(question["targets"])] += 1
                return provider(question)
            run = Run.reopen(slot / "product", resumed, approve)
            finish(run)
            assert sorted(calls.values()) == [1] * (len(completed) - 1) + [2]
        else:
            state = run.step()
            assert len(calls) == len(subjects) and set(calls.values()) == {1}
            if case == "gaps":
                assert state == "EXECUTE"
                gaps = [g for _, _, b in run.store.records("unit_fixup") for g in b["gaps"]]
                assert len({g["correction"] for g in gaps}) == 2
                assert all(g["commit"] == candidate for g in gaps)
                assert run.store.cursor().candidate == candidate
            class Remaining:
                def invoke(self, question, started):
                    if case == "uncertain" and question["kind"] == stage:
                        assert "Requested facts" in question["facts"]
                        calls[tuple(question["targets"])] += 1
                        return json.dumps({"answer": {"verdict": "uncertain", "neededFacts": [
                            "The visual observation remains unavailable"],
                            "why": "Still not observed"}}), None
                    return provider.invoke(question, started)
            run.provider = Remaining()
            finish(run)
            if case == "uncertain":
                assert set(calls.values()) == {2}
                outcome = run.store.records("outcome")[-1][2]
                assert not outcome["verification_complete"]
                field = "demoVerdicts" if stage == "FLOW" else "qualityVerdicts"
                assert all(run.store.read(r)["answer"]["verdict"] == "not judged"
                           for r in getattr(run.store.cursor(), field))
        parallel = {field: {run.store.read(r).get("feature", run.store.read(r).get("subject")):
                            run.store.read(r)["answer"] for r in getattr(run.store.cursor(), field)}
                    for field in ("demoVerdicts", "qualityVerdicts")}
        print(f"{stage} {case} final-stage seconds: {time.monotonic() - began:.3f}", flush=True)
        if case == "clean":
            serial, _ = prepare(slot / "serial", "fanout", 3, capacity=1)
            try:
                finish(serial)
                assert parallel == {field: {serial.store.read(r).get("feature",
                                            serial.store.read(r).get("subject")):
                                            serial.store.read(r)["answer"]
                                            for r in getattr(serial.store.cursor(), field)}
                                    for field in parallel}
            finally:
                serial.close()
    finally:
        run.close()

