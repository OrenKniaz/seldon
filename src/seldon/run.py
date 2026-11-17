"""C1/C8/C15/C17/C23; R10/R11: plan each wave from the integrated product, then final proof."""

import os
from dataclasses import replace
from pathlib import Path

from . import (
    architecture,
    decisions,
    detail,
    fixups,
    flow_review,
    gitwork,
    inference,
    judgment,
    plan,
    plan_review,
    proof,
    questions,
    scheduler,
    units,
    wave_verify,
)
from .states import advance
from .store import RunRow, Store, UnitRow

TERMINAL = {"ACCEPTED", "SCRIPTED_C", "STOPPED"}


def eligible(store):
    cursor = store.cursor()
    document = plan.execution(store)
    checkin = store.read(cursor.checkin)["subject"] if cursor.checkin else {}
    if (not cursor.B or checkin.get("commit") != cursor.candidate
            or checkin.get("wave") != cursor.wave or cursor.finalCommit != cursor.candidate
            or any(u.state not in ("DONE", "DROPPED") for u in store.units().values())):
        raise ValueError("C requires every unit DONE or DROPPED and final proof after check-in")
    observations = [store.read(r) for r in cursor.finalObservations]
    checks = set(plan.select(document, "check"))
    green = {o["check"] for o in observations if o["commit"] == cursor.candidate and o["exitCode"] == 0}
    if checks != green or proof.failures(store, cursor.finalObservations):
        raise ValueError("Every declared check needs passing execution on the final commit")
    if any(not row.judgment for row in store.units().values() if row.state != "DROPPED"):
        raise ValueError("C requires current unit judgments after contract changes")
    verdicts = [store.read(r) for r in cursor.demoVerdicts]
    trees = judgment.scope_trees(store, plan.select(document, "unit"))
    for kind, refs in (("FLOW", cursor.requirementVerdicts), ("PRODUCT", cursor.qualityVerdicts)):
        rows = [store.read(r) for r in refs]
        if ({r["subject"] for r in rows} != set(judgment.subjects(document, kind))
                or len(rows) != len(judgment.subjects(document, kind))
                or not all(judgment.current(r, trees)
                           and judgment.ready(r["answer"]) for r in rows)):
            raise ValueError("C requires each final subject satisfied or explicitly not judged")
    if ({v["feature"] for v in verdicts} != set(plan.select(document, "feature"))
            or len(verdicts) != len(plan.select(document, "feature"))
            or not all(v["commit"] == cursor.candidate and judgment.current(v, trees)
                       and judgment.ready(v["answer"])
                       for v in verdicts)):
        raise ValueError("C requires a satisfied final verdict for every feature")
    product = store.read(cursor.productVerdict) if cursor.productVerdict else None
    if not product or product["commit"] != cursor.candidate or not judgment.ready(product["answer"]):
        raise ValueError("C requires satisfied product quality and try-it guide on the final commit")
    gitwork.unchanged(store.root.parent, cursor.candidate)


class Run:
    """The front door owns only its dependencies; modules receive the store and subjects explicitly."""
    def __init__(self, project, scratch, dream, setup, provider, user, capacity=None):
        self.project = Path(project)
        self.project.mkdir()
        Path(scratch).mkdir()
        self.store = Store(self.project)
        self._lease = self.store.claim_controller()
        self.provider, self.user = provider, user
        self.capacity = capacity or max(1, min(32, os.cpu_count() or 1))
        if type(self.capacity) is not int or self.capacity < 1:
            raise ValueError("Capacity must be a positive integer")
        candidate = gitwork.init(self.project)
        with self.store.transaction():
            kickoff = self.store.record("kickoff", {"words": dream, "setup": setup,
                                                    "capacity": self.capacity})
            self.store.cursor(RunRow(kickoff=kickoff, scratch=str(Path(scratch).resolve()),
                                     candidate=candidate, waveStart=candidate))
            advance(self.store, "start", kickoff)

    @classmethod
    def reopen(cls, project, provider, user):
        self = cls.__new__(cls)
        self.project = Path(project)
        self.store = Store(self.project)
        self._lease = self.store.claim_controller()
        self.provider, self.user = provider, user
        self.capacity = self.store.read(self.store.cursor().kickoff)["capacity"]
        if self.store.cursor().state in TERMINAL:
            return self
        gitwork.publish(self.project, self.store)
        for ref, row in self.store.units().items():
            if row.state == "WORKING":
                units.thread_from_recording(self.store, ref)
                reason = "Controller interrupted WORKING; reconcile its report or resume its partial patch"
                advance(self.store, "reopen", reason, unit=ref, failure=row.failure + "\n" + reason)
        return self

    def step(self):
        store = self.store
        cursor = store.cursor()
        state = cursor.state
        if state in TERMINAL:
            return state
        if state in ("A", "B", "C") and not cursor.decision:
            subject = ({"vision": store.read(cursor.vision)["answer"]} if state == "A" else
                       {"revision": cursor.revision, "vision": cursor.vision,
                        "plan": store.plan(cursor.revision), "review": store.read(cursor.review)}
                       if state == "B" else
                       {"commit": cursor.candidate,
                        "units": {r: u.state for r, u in store.units().items()},
                        "notJudged": judgment.not_judged(store), "leftOut": plan.left_out(store), "deviation": "DV6"})
            decisions.request(store, state, subject)
            cursor = store.cursor()
        if cursor.decision and (state != "EXECUTE" or store.read(cursor.decision)["gate"] != "UNIT"):
            try:
                decisions.decide(store, self.provider, self.user)
            except decisions.Waiting:
                if decisions.holds(store):
                    raise
            if store.cursor().state != state or not store.cursor().decision:
                return store.cursor().state
        cursor = store.cursor()
        if state == "ALIGN":
            kickoff = store.read(cursor.kickoff)
            facts = {**questions.user_words(store), "Runtime": questions.runtime(kickoff["setup"])}
            if cursor.requestedChange:
                facts["Requested change"] = store.read(cursor.requestedChange)
            ref, answer = inference.call(store, self.provider, questions.build("ALIGN", facts))
            advance(store, "aligned", ref, vision=ref, requestedChange=None)
        elif state == "CHECK-IN":
            waves = plan.execution(store)["architecture"]["waves"]
            more = cursor.wave < len(waves)
            with store.transaction():
                decisions.request(store, state, {
                    "commit": cursor.candidate, "wave": cursor.wave,
                    "built": plan.built(store)["Finished wave"],
                    "next wave goal": waves[cursor.wave]["goal"] if more else None})
                advance(store, "next" if more else "last", cursor.candidate,
                        **({"wave": cursor.wave + 1, "waveStart": cursor.candidate, "repairs": 0}
                           if more else {}))
        elif state == "ARCHITECT":
            finished = max((u.wave for u in store.units().values()), default=0)
            if cursor.wave == finished:
                cursor = advance(store, "steer", cursor.requestedChange or cursor.candidate,
                                 wave=finished + 1, waveStart=cursor.candidate, repairs=0)
            vision = store.read(cursor.vision)["answer"]
            previous = store.plan(cursor.revision) if cursor.revision else None
            built = plan.built(store) if cursor.wave > 1 else None
            outline = previous["architecture"] if previous else None
            gaps = [b for _, _, b in store.records("architecture_gap") if b["vision"] == cursor.vision]
            result, answer = inference.call(store, self.provider,
                questions.architect_brief(vision, questions.user_words(store),
                                         gaps[-1]["gap"] if gaps else None,
                                         outline if gaps or built else None, built=built),
                check=lambda a: architecture.outline(architecture.merge(outline, a)
                                                     if built else a, vision))
            answer = architecture.merge(outline, answer) if built else answer
            if "scope" in answer:
                advance(store, "scope", result, requestedChange=result)
                return store.cursor().state
            document = dict(previous) if built else {
                "nodes": {"vision": {"kind": "vision", "body": vision}}}
            document["architecture"] = {k: v for k, v in answer.items() if k != "why"}
            with store.transaction():
                revision = store.revision(document, cursor.revision)
                advance(store, "outlined", result, revision=revision)
        elif state == "DETAIL":
            detail.step(store, self.provider, self.capacity)
        elif state == "REVIEW":
            plan_review.step(store, self.provider, self.capacity)
        elif state == "REPAIR":
            plan_review.repair(store)
        elif state == "EXECUTE":
            if decisions.holds(store):
                raise decisions.Waiting()
            if not plan.approved(store):
                raise ValueError("Execution requires B for the accepted vision")
            with store.transaction():
                for ref, body in plan.select(plan.execution(store), "unit").items():
                    row = store.unit(ref)
                    if row is None:
                        store.unit(ref, UnitRow(wave=cursor.wave))
                        advance(store, "create", cursor.B, unit=ref)
                    elif row.state == "DONE" and row.wave < cursor.wave and (
                            ref == "guide" or any(r.startswith(f"{cursor.wave}.")
                                                  for r in body["requirements"])):
                        advance(store, "extend", cursor.revision, unit=ref,
                                **{k: v for k, v in vars(UnitRow(wave=cursor.wave,
                                    base=cursor.candidate)).items() if k != "state"})
            scheduler.execute(store, self.provider, self.capacity, self.user)
            if store.cursor().state not in TERMINAL:
                advance(store, "finished", "Every unit integrated or dropped")
        elif state == "WAVE-VERIFY":
            observations = wave_verify.verify(store, cursor.candidate)
            gaps = proof.gaps(store, cursor.candidate, observations)
            if not gaps:
                gaps = units.judge_wave(store, self.provider, observations)
            if gaps:
                fixups.apply(store, gaps)
            else:
                advance(store, "passed", observations, finalCommit=cursor.candidate,
                        finalObservations=observations)
        elif state == "FLOW-JUDGE":
            flow_review.step(store, self.provider)
        elif state == "PRODUCT-JUDGE":
            judgment.step(store, self.provider)
        cursor = store.cursor()
        if cursor.failure:
            with store.transaction():
                store.cursor(replace(cursor, failure=""))
        return cursor.state

    def run(self, wait=None):
        import time

        from .providers import ProviderFailure

        wait = wait or time.sleep
        delay = 60
        try:
            cursor = self.store.cursor()
            while cursor.state not in TERMINAL:
                try:
                    self.step()
                except decisions.Waiting:
                    break
                except (ProviderFailure, proof.UncertainCleanup) as error:
                    receipt = getattr(error, "receipt", None)
                    reason = str(error) + (f"; receipt: {receipt}" if receipt else "")
                    with self.store.transaction():
                        cursor = self.store.cursor(replace(self.store.cursor(), failure=reason))
                    if isinstance(error, proof.UncertainCleanup) or not error.cleanup_verified:
                        break
                    wait(delay)
                    delay = min(900, delay * 2)
                except Exception as error:  # any fault parks as a question; the store stays resumable (R15)
                    if self.store.cursor().decision:
                        break
                    decisions.step_fault(self.store, self.provider, error)
                    cursor = self.store.cursor()
                else:
                    cursor = self.store.cursor()
                    delay = 60
        finally:
            self._lease.close()
        return self.store.cursor()

    def close(self):
        self.store.close()
        self._lease.close()
