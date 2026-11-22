"""C1/C8/C15/C17/C23; R10/R11: plan each wave from the integrated product, then final proof."""

import os
from dataclasses import replace
from pathlib import Path

from . import decisions, features, gitwork, inference, lead, proof, questions
from .states import TERMINAL, advance
from .store import RunRow, Store


def eligible(store):
    if lead.enabled(store):
        return lead.eligible(store)
    from . import scheduler
    return scheduler.eligible(store)


class Run:
    """The front door owns only its dependencies; modules receive the store and subjects explicitly."""
    def __init__(self, project, scratch, dream, setup, provider, user, capacity=None, *, mode=None):
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
            kickoff = self.store.record("kickoff", {"words": dream,
                                                    "setup": {**setup, **features.planning_setup()},
                                                    "mode": lead.kickoff_mode(mode),
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
        if self.store.cursor().state not in TERMINAL:
            gitwork.publish(self.project, self.store)
            if not lead.enabled(self.store):
                from . import units
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
        if state not in ("ALIGN", "A"):
            if lead.enabled(store):
                return lead.step(store, self.provider, self.user)
            from . import scheduler
        if state in ("A", "B", "C") and not cursor.decision:
            if state == "A":
                subject = {"vision": store.read(cursor.vision)["answer"]}
            elif state == "B":
                subject = {"revision": cursor.revision, "vision": cursor.vision,
                           "plan": store.plan(cursor.revision), "review": store.read(cursor.review)}
            else:
                from . import judgment, plan
                subject = {"commit": cursor.candidate, "units": {r: u.state for r, u in store.units().items()},
                           "notes": judgment.notes(store), "notJudged": judgment.not_judged(store),
                           "leftOut": plan.left_out(store), "deviation": "DV6"}
            decisions.request(store, state, subject)
            cursor = store.cursor()
        if cursor.decision and (state != "EXECUTE" or store.read(cursor.decision)["gate"] != "UNIT"):
            try:
                decisions.decide(store, self.provider, self.user)
            except decisions.Waiting:
                if decisions.holds(store):
                    raise
            latest = store.cursor()
            if latest.state != state or not latest.decision:
                return latest.state
        if state == "ALIGN":
            self.align()
        elif state != "A" and scheduler.step(store, self.provider, self.user, self.capacity, store.cursor()):
            return store.cursor().state
        cursor = store.cursor()
        if cursor.failure:
            with store.transaction():
                store.cursor(replace(cursor, failure=""))
        return cursor.state

    def align(self):
        store = self.store
        cursor = store.cursor()
        kickoff = store.read(cursor.kickoff)
        setup = kickoff["setup"]
        size_first = setup.get("sizeFirst", False)
        facts = {**questions.user_words(store), "Runtime": questions.runtime(setup)}
        if cursor.requestedChange:
            facts["Requested change"] = store.read(cursor.requestedChange)
        # The question is recorded before the provider runs. Its shape survives an interrupted
        # call as well as a switch change between a displayed round and its revision.
        last = next((q for _, _, q in reversed(store.records("question")) if q["kind"] == "ALIGN"), None)
        grown = ("vision" in last["schema"]["properties"]["answer"].get("properties", {})
                 if last else features.enabled("growAlign"))
        if not grown:
            inference.call(store, self.provider, questions.build("ALIGN", facts),
                consume=lambda ref, _: advance(store, "aligned", ref, vision=ref, requestedChange=None))
            return

        micro = size_first and lead.enabled(store) and features.enabled("leadPlanOne")
        sources = {"kickoff": kickoff["words"], **{ref: row["words"]
            for ref, _, row in store.records("user_words") if isinstance(row["words"], str)
            and not row.get("rejected")}}
        previous = next((r for _, _, r in reversed(store.records("decision_request"))
                         if "groups" in r.get("ask", {})), None)
        size = None
        if previous:
            draft = previous["subject"]["round"]
            facts["Previous proposal"] = {key: value for key, value in store.read(draft)["answer"].items()
                                          if key != "microSpec"}
            approvals = [a for _, _, a in reversed(store.records("approval")) if "selections" in a]
            revision = next((a for a in approvals
                             if a["subject"].get("round") == draft and a["choice"] == "revise"), None)
            if revision:
                facts["Selected choices"] = revision["selections"]
            size = next((s["optionId"] for a in approvals for s in a["selections"]
                         if s["groupId"] == "size"), None)
            if size and size != "other":
                facts["Owner size override"] = size

        def display(ref, answer):
            advance(store, "aligned", ref, requestedChange=None)
            decisions.request(store, "A", {"round": ref, "vision": questions.vision_body(answer)},
                              ask=decisions.round_ask(answer, size_first=size_first))

        inference.call(store, self.provider, questions.align_round_brief(
                           facts, sources=sources, size_first=size_first, micro=micro, lead_mode=lead.enabled(store)),
                       check=lambda answer: questions.check_align_round(answer, sources, size),
                       consume=display, field="vision/questions")

    def run(self, wait=None):
        import time

        from .providers import InputRejected, ProviderFailure

        wait = wait or time.sleep
        delay = 60
        try:
            cursor = self.store.cursor()
            while cursor.state not in TERMINAL:
                try:
                    self.step()
                except decisions.Waiting:
                    break
                except InputRejected as error:
                    if self.store.cursor().decision:
                        break
                    decisions.step_fault(self.store, self.provider, error)
                    cursor = self.store.cursor()
                except (ProviderFailure, proof.UncertainCleanup) as error:
                    receipt = getattr(error, "receipt", None)
                    reason = str(error) + (f"; receipt: {receipt}" if receipt else "")
                    with self.store.transaction():
                        cursor = self.store.cursor(replace(self.store.cursor(), failure=reason))
                    if isinstance(error, proof.UncertainCleanup) and cursor.state == "PROVE":
                        decisions.owner_question(self.store, "Seldon", reason)
                        break
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
