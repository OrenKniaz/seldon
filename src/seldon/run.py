"""C1/C8/C15/C23/I6; R7/R9/R11: one wave of independent unit lifecycles, then final proof."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from . import (
    decisions, gitwork, guide, inference, judgment, obligations, plan, proof, questions, scheduler, units,
)
from .states import advance
from .store import RunRow, Store, UnitRow

TERMINAL = {"ACCEPTED", "SCRIPTED_C", "INCOMPLETE"}


def eligible(store):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    if (not cursor.B or not cursor.checkin or cursor.finalCommit != cursor.candidate
            or any(u.state != "DONE" for u in store.units().values())):
        raise ValueError("C requires every unit DONE and final integrated proof after check-in")
    observations = [store.read(r) for r in cursor.finalObservations]
    checks = set(plan.select(document, "check")) | set(guide.checks(store.root.parent))
    green = {o["check"] for o in observations if o["commit"] == cursor.candidate and o["exitCode"] == 0}
    covered = {r for o in observations if o["check"] in green for r in o["requirements"]}
    if (checks != green or set(plan.select(document, "requirement")) - covered
            or proof.failures(store, cursor.finalObservations)):
        raise ValueError("Every requirement and check needs passing execution on the final commit")
    if pending := obligations.remaining(store):
        raise ValueError(f"Close open skeleton obligations with final-commit requirement verdicts: {pending}")
    if any(not row.judgment for row in store.units().values()):
        raise ValueError("C requires current unit judgments after contract changes")
    verdicts = [store.read(r) for r in cursor.demoVerdicts]
    trees = judgment.scope_trees(store, plan.select(document, "unit"))
    for kind, refs in (("DEMO", cursor.requirementVerdicts), ("PRODUCT", cursor.qualityVerdicts)):
        rows = [store.read(r) for r in refs]
        if ({r["subject"] for r in rows} != set(judgment.subjects(document, kind))
                or len(rows) != len(judgment.subjects(document, kind))
                or not all(judgment.current(r, trees)
                           and questions.satisfied(r["answer"], final=True) for r in rows)):
            raise ValueError("C requires every final subject's satisfied verdict")
    if ({v["feature"] for v in verdicts} != set(plan.select(document, "feature"))
            or not all(v["commit"] == cursor.candidate and questions.satisfied(v["answer"], final=True)
                       for v in verdicts)):
        raise ValueError("C requires a satisfied final verdict for every feature")
    product = store.read(cursor.productVerdict) if cursor.productVerdict else None
    if not product or product["commit"] != cursor.candidate or not questions.satisfied(product["answer"],
                                                                                      final=True):
        raise ValueError("C requires satisfied product quality and try-it guide on the final commit")
    gitwork.unchanged(store.root.parent, cursor.candidate)


def incomplete(store, reason):
    failed = {r: u.failure for r, u in store.units().items() if u.state == "FAILED"}
    blocked = {r: u.failure for r, u in store.units().items() if u.state == "BLOCKED"}
    outcome = store.record("outcome", {"accepted": False, "verification_complete": False,
        "reason": reason, "failed": failed, "blocked": blocked,
        "nextAction": "Resolve the named failure, then start a fresh run."})
    advance(store, "fail", outcome)


def wave_verify(store, capacity):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    assignments = plan.assigned(document)
    gitwork.unchanged(store.root.parent, cursor.candidate)

    def observe_unit(ref):
        try:
            checks, root = assignments[ref], store.root.parent
            if ref == "guide":
                # A user follows the guide in a fresh copy of the delivery; its steps never touch the candidate.
                row = store.unit(ref)
                gitwork.checkout(row.worktree, cursor.candidate)
                gitwork.git(row.worktree, "clean", "-fdx")
                root = row.worktree
                try:
                    checks = guide.checks(root)
                except ValueError as error:
                    store.unit(ref, replace(row, base=cursor.candidate))
                    units.recover(store, ref, str(error))
                    return ref, []
            tree = gitwork.scope_tree(store.root.parent, cursor.candidate,
                                      plan.select(document, "unit")[ref]["scope"], cursor.scratch)
            return ref, proof.observe(store, root, cursor.candidate, checks, "final", ref, tree,
                                      disposable=ref == "guide")
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=capacity) as pool:
        results = list(pool.map(observe_unit, store.units()))
    wave = proof.observe(store, store.root.parent, cursor.candidate, assignments[None], "final")
    observations = [r for _, refs in results for r in refs] + wave
    rework = False
    for ref, refs in results:
        if store.unit(ref).state != "DONE":
            rework = True
            continue
        bad = proof.failures(store, refs)
        if bad:
            row = store.unit(ref)
            gitwork.checkout(row.worktree, cursor.candidate)
            with store.transaction():
                store.unit(ref, replace(row, base=cursor.candidate))
            units.recover(store, ref, "Final unit checks failed: " + proof.explain(bad))
            rework = True
    if proof.failures(store, wave):
        raise ValueError("Final cross-unit wave checks failed: " + proof.explain(proof.failures(store, wave)))
    with store.transaction():
        advance(store, "rework" if rework else "passed", observations,
                finalCommit=None if rework else cursor.candidate,
                finalObservations=[] if rework else observations, demoVerdicts=[], productVerdict=None,
                pendingJudge=None)


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
                advance(self.store, "exhausted" if row.recoveries >= 2 else "reopen", reason, unit=ref,
                        failure=reason, recoveries=min(2, row.recoveries + 1))
        return self

    def step(self):
        store = self.store
        cursor = store.cursor()
        state = cursor.state
        if state in TERMINAL:
            return state
        kickoff = store.read(cursor.kickoff)
        if state == "ALIGN":
            facts = {"Dream": kickoff["words"], "Runtime": questions.runtime(kickoff["setup"])}
            if cursor.requestedChange:
                facts["Requested change"] = store.read(cursor.requestedChange)["words"]
            ref, answer = inference.call(store, self.provider, questions.build("ALIGN", facts))
            advance(store, "aligned", ref, vision=ref, requestedChange=None)
        elif state in ("A", "B", "CHECK-IN", "C"):
            if not cursor.decision:
                subject = ({"vision": store.read(cursor.vision)["answer"]} if state == "A" else
                           {"revision": cursor.revision, "plan": store.plan(cursor.revision),
                            "review": store.read(cursor.review)} if state == "B" else
                           {"commit": cursor.candidate,
                            "units": {r: u.state for r, u in store.units().items()}})
                decisions.request(store, state, subject)
            decisions.decide(store, self.provider, self.user)
        elif state == "PLAN":
            vision = store.read(cursor.vision)["answer"]
            result, answer = inference.call(store, self.provider, questions.build("PLAN", {
                "Original request": kickoff["words"], "Approved vision": vision,
                "Runtime": questions.runtime(kickoff["setup"])}),
                check=lambda answer: plan.create(vision, answer))
            document = plan.add_guide(plan.create(vision, answer))
            with store.transaction():
                revision = store.revision(document)
                advance(store, "planned", result, revision=revision)
        elif state == "REVIEW":
            result, answer = inference.call(store, self.provider, questions.build("REVIEW", {
                "Original request": kickoff["words"], "Approved vision": store.read(cursor.vision)["answer"],
                "Plan": store.plan(cursor.revision)}))
            adequate = answer["verdict"] == "adequate" and not answer["gaps"]
            advance(store, "adequate" if adequate else "gap", result, review=result, failure=str(answer))
        elif state == "REPAIR":
            decisions.repair(store, self.provider)
        elif state == "EXECUTE":
            document = store.plan(cursor.revision)
            approval = store.read(cursor.B)
            if (approval["subject"]["revision"] != cursor.revision
                    or approval["subject"]["plan"] != document
                    or approval["subject"]["review"] != store.read(cursor.review)):
                raise ValueError("Execution requires B for this exact plan and review")
            with store.transaction():
                for ref in plan.select(document, "unit"):
                    if store.unit(ref) is None:
                        store.unit(ref, UnitRow())
                        advance(store, "create", cursor.B, unit=ref)
            scheduler.execute(store, self.provider, self.capacity)
            failures = {r: {"state": u.state, "reason": u.failure} for r, u in store.units().items()
                        if u.state != "DONE"}
            if failures:
                incomplete(store, "Units incomplete: " + str(failures))
            else:
                advance(store, "finished", "Every unit integrated")
        elif state == "WAVE-VERIFY":
            wave_verify(store, self.capacity)
        elif state in ("DEMO-JUDGE", "PRODUCT-JUDGE", "DEMO-FACTS", "PRODUCT-FACTS"):
            judgment.step(store, self.provider)
        return store.cursor().state

    def run(self):
        try:
            while self.store.cursor().state not in TERMINAL:
                self.step()
        except (ValueError, RuntimeError, OSError) as error:
            incomplete(self.store, f"{type(error).__name__}: {error}")
        finally:
            self._lease.close()
        return self.store.cursor()

    def close(self):
        self.store.close()
        self._lease.close()
