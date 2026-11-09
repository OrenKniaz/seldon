"""C1/C8/C16/C23: CODE owns the day-one A-to-C loop and its canonical baton."""

import time
from pathlib import Path

from . import evidence, plan, work
from .inference import Inference
from .decisions import Decisions
from .verification import Verification
from .execution import Execution
from .store import Store, digest, encode


class Loop(Inference, Decisions, Verification, Execution):
    def __init__(self, project, scratch, dream, setup, provider, user):
        self.project, self.scratch = Path(project), Path(scratch)
        self.project.mkdir()
        self.scratch.mkdir()
        work.git(self.project, "init", "--quiet")
        self.store = Store(self.project)
        self._lease = self.store.claim_controller()
        work.git(self.project, "update-ref", "HEAD", work.revision(self.project))
        self.provider, self.user, self.setup = provider, user, setup
        with self.store.transaction():
            kickoff = self.store.record("kickoff", {"words": dream, "setup": setup})
            source = Path(__file__).resolve().parent
            self.store.cursor({"state": "ALIGN", "kickoff": kickoff, "revision": None,
                               "sourceCommit": work.git(source, "rev-parse", "HEAD"),
                               "sourceDigest": digest({p.name: p.read_text() for p in sorted(source.glob("*.py"))}),
                               "questionContractDigest": digest({p: (source / p).read_text() for p in ("questions.py", "schema.py")}),
                               "scratch": str(self.scratch), "judgments": []})

    def _attempt(self, operation, **bindings):
        with self.store.transaction():
            return self.store.record("attempt", {"operation": operation, **bindings,
                                                 "deadline": time.time() + 30})

    def _next(self, cursor, state, cause):
        self.store.record("transition", {"input": cursor["state"], "result": cause, "next": state})
        cursor["state"] = state
        self.store.cursor(cursor)

    def _request(self, cursor, gate, binding):
        cursor["decision"] = self.store.record("decision_request", {"subject": binding,
            "gate": gate, "revision": cursor["revision"], "origin": self.setup["userOrigin"]})
        self._next(cursor, f"DECIDE({gate})", cursor["decision"])

    def _candidate(self, root=None):
        content = work.files(root or self.project)
        body = work.candidate(content)
        ref = self.store.record("candidate", body)
        for data in content.values():
            self.store.artifact(data, ref, "application/octet-stream")
        return ref

    def _current_candidate(self, ref):
        return self.store.read(ref) == work.candidate(work.files(self.project))

    def _incomplete(self, cursor, cause):
        detail = self.store.read(cause) if isinstance(cause, str) and cause[0:1] == "r" and cause[1:].isdigit() else cause
        if isinstance(detail, dict) and isinstance(detail.get("answer"), str):
            detail = {**detail, "answerDetail": self.store.read(detail["answer"])}
        if isinstance(detail, dict) and "observations" in detail:
            detail = {**detail, "observationDetails": [self.store.read(ref) for ref in detail["observations"]]}
        reason = f"{cursor['state']}: {encode(detail)}"
        proven = [ref for ref in cursor["judgments"] if self.store.read(ref)["satisfied"]]
        outcome = self.store.record("outcome", {"accepted": False, "verification_complete": False,
            "reason": reason, "lastProvenResult": proven[-1:],
            "nextAction": "Supply the named missing fact or resolve the unresolved gap before resuming"})
        self._next(cursor, "INCOMPLETE", outcome)

    def _green_facts(self, cursor, candidate=None):
        facts = evidence.facts(self.store, [r for r, _, b in self.store.records("observation") if b["purpose"] == "GREEN"],
                              candidate or cursor["candidate"], self.store.plan(cursor["revision"]))
        latest = {b["test"]: (r, b) for r, b in facts.items() if b["applies"]}
        return {r: b for r, b in latest.values() if b["passed"]}

    def _green_ready(self, cursor, candidate=None):
        required = {r for r, t in plan.select(self.store.plan(cursor["revision"]), "test").items() if t["stage"] == "verification"}
        facts = self._green_facts(cursor, candidate)
        return required <= {o["test"] for o in facts.values()} and evidence.ready(facts, "GREEN")

    def _judgments(self, purpose):
        return sorted({u["evidence"][purpose]["judgment"] for u in self.store.units().values()
                       if purpose in u.get("evidence", {}) and not u["evidence"][purpose].get("stale")})

    def _red_tests(self, document):
        covered = set()
        for unit in self.store.units().values():
            red = unit.get("evidence", {}).get("RED", {})
            if red.get("stale"):
                continue
            for ref in red.get("observations", []):
                body = self.store.read(ref)
                fact = evidence.facts(self.store, [ref], body["candidate"], document)[ref]
                if fact["applies"] and self.store.read(red["judgment"])["satisfied"]:
                    covered.add(body["test"])
        return covered

    @classmethod
    def reopen(cls, project, provider, user):
        self = cls.__new__(cls)
        self.project = Path(project)
        self.store = Store(self.project)
        self._lease = self.store.claim_controller()
        cursor = self.store.cursor()
        self.scratch = Path(cursor["scratch"])
        self.setup = self.store.read(cursor["kickoff"])["setup"]
        self.provider, self.user = provider, user
        if cursor.get("publication"):
            self._finish_publication(cursor)
        with self.store.transaction():
            for ref, unit in self.store.units().items():
                active = unit.get("active")
                if active and not (active.get("report") or active.get("work")) and active.get("invocation"):
                    terminal = self._recover_call(active["invocation"])
                    if terminal:
                        report, finished = terminal
                        active.update(report=report, status="checking")
                        self.store.unit(ref, unit)
                        self.store.record("writer_end", {"attempt": active["attempt"], "report": report, "finishedAt": finished})
                        self.store.record("recovery", {"unit": ref, "attempt": active["attempt"],
                            "report": report, "action": "reconcile recorded terminal reply; resume checks"})
                if active and not (active.get("report") or active.get("work")):
                    active["status"] = "uncertain"
                    self.store.unit(ref, unit)
                    self.store.record("recovery", {"unit": ref, "attempt": active["attempt"],
                        "action": "retain ownership; reconcile terminal report; never relaunch uncertain writer"})
        return self

    def _eligible(self, cursor):
        document = self.store.plan(cursor["revision"])
        required = set(plan.select(document, "bead"))
        return (required <= self._finished() and bool(cursor.get("checkin"))
                and bool(self._judgments("GREEN")) and bool(cursor.get("demoJudgment"))
                and cursor.get("demoCandidate") == cursor.get("candidate")
                and self.store.read(cursor["executionFacts"])["traceComplete"]
                and self._green_ready(cursor)
                and self._current_candidate(cursor["candidate"]))

    def step(self):
        cursor = self.store.cursor()
        state = cursor["state"]
        if state in ("ACCEPTED", "SCRIPTED_C", "INCOMPLETE"):
            return state
        document = self.store.plan(cursor["revision"]) if cursor["revision"] else None
        if state == "ALIGN":
            call = self._call(cursor, "ALIGN", [])
            with self.store.transaction():
                cursor["vision"] = self._answer(call)
                self._request(cursor, "A", plan.subject("A", vision=call["answer"]))
        elif state.startswith("DECIDE("):
            self._decide(cursor)
        elif state == "PLAN":
            call = self._call(cursor, "PLAN", [cursor["vision"]])
            vision = self.store.read(cursor["vision"])["answer"]
            proposed = plan.create(vision, call["answer"])
            with self.store.transaction():
                source = self._answer(call)
                cursor["revision"] = self.store.revision(proposed)
                cursor["plan"] = self.store.record("plan", {"revision": cursor["revision"],
                    "document": proposed, "source": source, "visionApproval": cursor["A"]})
                self._sync_units(proposed)
                self._next(cursor, "REVIEW", cursor["plan"])
        elif state == "REPAIR":
            cause, origin = cursor["repairCause"], cursor["repairOrigin"]
            refs = [cursor["vision"], cause] + ([cursor["plan"]] if cursor.get("plan") else [])
            call = self._call(cursor, "REPAIR", refs)
            before = document or {"nodes": {"vision": {"kind": "vision",
                "body": plan.vision_body(self.store.read(cursor["vision"])["answer"])}}}
            after = plan.apply(before, call["answer"]["edits"])
            with self.store.transaction():
                source = self._answer(call)
                proposal = self.store.record("repair_proposal", {"cause": cause,
                    "preimage": cursor["revision"], "source": source,
                    "before": before, "after": after, "origin": origin})
                cursor["pendingRepair"] = proposal
                self.store.cursor(cursor)
            vision_changed = before["nodes"]["vision"] != after["nodes"]["vision"]
            impact = None
            if cursor.get("B") and before != after:
                call = self._call(cursor, "IMPACT", [proposal])
                with self.store.transaction():
                    impact = self._answer(call)
            with self.store.transaction():
                if (vision_changed and cursor.get("A")) or (impact and call["answer"]["verdict"] == "scope_change"):
                    self._request(cursor, "scope", plan.subject("scope", proposal=proposal,
                        before=before, after=after, impact=impact))
                else:
                    self._apply_repair(cursor, proposal, impact)
        elif state == "REVIEW":
            call = self._call(cursor, "REVIEW", [cursor["vision"], cursor["plan"]])
            answer = call["answer"]
            with self.store.transaction():
                result = self._answer(call)
                cursor["review"] = self.store.record("review", {"answer": result,
                    "revision": cursor["revision"], "planDigest": digest(document),
                    "reviewer": call["provider"]})
                if answer["verdict"] == "adequate" and not answer["gaps"]:
                    if cursor.get("resumeRepair") and cursor.get("B"):
                        self._resume_repair(cursor, cursor["review"])
                    else:
                        self._request(cursor, "B", plan.subject("B", revision=cursor["revision"],
                            plan=document, review=cursor["review"]))
                else:
                    self._repair(cursor, result, "REVIEW")
        elif state.startswith("WORK("):
            self._execute_units(cursor, document)
        elif state.startswith("OBSERVE(") or state == "JUDGE":
            self._verify(cursor, document)
        elif state == "STEER":
            waves = plan.select(document, "wave")
            if any(not set(wave["beads"]) <= self._finished() for wave in waves.values()):
                raise ValueError("Wave check-in requires every member to be integrated")
            with self.store.transaction():
                self._request(cursor, "check-in", plan.subject("check-in", waves=waves,
                    candidate=cursor["candidate"], judgments=cursor["judgments"],
                    proposedDeferrals={r: self.store.read(r) for r in cursor.get("discoveries", [])},
                    continuation="Approve these discovery deferrals and proceed to final walkthrough and acceptance"))
        else:
            raise ValueError(f"Unknown state: {state}")
        return self.store.cursor()["state"]

    def run(self):
        try:
            while self.store.cursor()["state"] not in ("ACCEPTED", "SCRIPTED_C", "INCOMPLETE"):
                self.step()
        except (ValueError, KeyError, RuntimeError, OSError) as error:
            with self.store.transaction():
                self._incomplete(self.store.cursor(), f"{type(error).__name__}: {error}")
        finally:
            self._lease.close()
        return self.store.cursor()
