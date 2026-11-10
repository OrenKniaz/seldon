"""C14/C15/C26: observe integrated products and judge their semantic obligations."""

from . import evidence, plan


class Verification:
    def _verify(self, cursor, document):
        state = cursor["state"]
        if state.startswith("OBSERVE("):
            purpose = cursor["purpose"]
            stage = "walkthrough" if purpose == "demo" else "verification"
            declarations = {ref: body for ref, body in plan.select(document, "test").items()
                            if body["stage"] == stage}
            if not self._current_candidate(cursor["candidate"]):
                raise ValueError("Observation requires declared commands and the exact candidate")
            attempt = self._attempt("observe", purpose=purpose,
                                    candidate=cursor["candidate"], tests=list(declarations))
            retained = self._green_facts(cursor) if purpose != "RED" else {}
            covered = self._red_tests(document) if purpose == "RED" else {o["test"] for o in retained.values()}
            declarations = {r: t for r, t in declarations.items() if r not in covered}
            if purpose == "RED" and not declarations:
                with self.store.transaction():
                    self._next(cursor, "WORK(build)", self._judgments("RED"))
                return
            results = evidence.observe(self.project, declarations, self.scratch)
            if not self._current_candidate(cursor["candidate"]):
                raise ValueError("Observation changed the delivered candidate")
            with self.store.transaction():
                cursor["observationScope"] = evidence.save(
                    self.store, attempt, purpose, cursor["candidate"], results, document)
                self.store.record("attempt_end", {
                    "attempt": attempt, "results": cursor["observationScope"]})
                facts = evidence.facts(self.store, [*retained, *cursor["observationScope"]], cursor["candidate"], document)
                if any(not o["executionUsable"] for o in facts.values()):
                    fault = self.store.record("seldon_fault", {"fault": "VERIFICATION_NOT_EXECUTED",
                        "observations": cursor["observationScope"]})
                    self._incomplete(cursor, fault)
                else:
                    self._next(cursor, "JUDGE", cursor["observationScope"])
        elif state == "JUDGE":
            purpose = cursor["purpose"]
            quality = document["nodes"]["vision"]["body"]["quality"]
            inventory = ([ref for ref, body in plan.select(document, "test").items()
                          if body["stage"] == "verification" and (purpose != "RED" or ref in {self.store.read(r)["test"] for r in cursor["observationScope"]})] if purpose != "demo" else
                         [*plan.select(document, "requirement"),
                          "noStubsMocksMissing",
                          *[f"quality:{i}" for i, _ in enumerate(quality, 1)]])
            if purpose == "demo":
                inventory += ["tryItGuide"]
            facts = self._green_facts(cursor) if purpose != "RED" else {}
            retained = list(facts)
            if purpose == "demo" and not self._green_ready(cursor):
                with self.store.transaction():
                    cursor["purpose"] = "GREEN"
                    self._next(cursor, "OBSERVE(GREEN)", cursor["candidate"])
                return
            observed = evidence.facts(self.store, [*retained, *cursor["observationScope"]], cursor["candidate"], document)
            coverage = sorted({r for o in observed.values() if o["applies"] and o["passed"] for r in o["requirements"]})
            packet = self.store.record("execution_facts", {"candidate": cursor["candidate"],
                "reusedEvidence": retained, "coverage": coverage,
                "traceComplete": purpose == "demo" and set(plan.select(document, "requirement")) <= set(coverage)})
            refs = [cursor["plan"], cursor["candidate"], packet, *cursor["observationScope"], *retained,
                    *(self._judgments("GREEN") if purpose == "demo" else [])]
            call = self._call(cursor, "JUDGE", refs, purpose=purpose, assessments=inventory)
            answer = call["answer"]
            assessments = answer["assessments"]
            satisfied = (evidence.ready(observed, purpose) and answer["verdict"] == "satisfied" and not answer["gaps"]
                         and all(a["satisfied"] for a in assessments))
            with self.store.transaction():
                result = self._answer(call)
                judgment = self.store.record("judgment", {"answer": result, "purpose": purpose,
                    "candidate": cursor["candidate"], "observations": cursor["observationScope"],
                    "satisfied": satisfied})
                cursor["judgments"].append(judgment)
                for ref, unit in self.store.units().items():
                    local = [r for r in cursor["observationScope"] if self.store.read(r)["test"] in unit["contract"]["tests"]]
                    if local:
                        unit.setdefault("evidence", {})[purpose] = {"judgment": judgment, "observations": local}
                        self.store.unit(ref, unit)
                if not satisfied:
                    self._repair(cursor, judgment, purpose)
                elif purpose == "RED":
                    self._next(cursor, "WORK(build)", judgment)
                elif purpose == "GREEN":
                    self._next(cursor, "STEER", judgment)
                else:
                    cursor["demoJudgment"], cursor["demoCandidate"] = judgment, cursor["candidate"]
                    cursor["executionFacts"] = packet
                    if not self._eligible(cursor):
                        raise ValueError("C requires all authorized work and final evidence")
                    self._request(cursor, "C", plan.subject("C", scope=document["nodes"]["vision"],
                        revision=cursor["revision"], candidate=cursor["candidate"],
                        evidence=[*retained, *cursor["observationScope"]],
                        judgments=[*self._judgments("RED"), *self._judgments("GREEN"), cursor["demoJudgment"]],
                        executionFacts=packet, units={r: u["results"] for r, u in self.store.units().items()}))
