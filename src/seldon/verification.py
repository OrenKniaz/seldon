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
                cursor.pop("judging", None)
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
            units = plan.select(document, "bead")
            tests = plan.select(document, "test")
            groups = {r: {"requirements": b["requirements"], "tests": [t for t in tests if plan.test_units(document, t) == {r}]}
                      for r, b in units.items()} if purpose != "demo" else {}
            groups["integration"] = {"requirements": [], "tests": [t for t in tests if
                (tests[t]["stage"] == "walkthrough" if purpose == "demo" else len(plan.test_units(document, t)) > 1)]}
            subjects = []
            for unit, scope in groups.items():
                targets = ([*scope["requirements"], "noStubsMocksMissing"] if purpose == "GREEN" and unit != "integration" else
                           [t for t in scope["tests"] if any(o["test"] == t for o in observed.values())])
                if purpose == "demo":
                    targets = ["tryItGuide", *[f"quality:{i}" for i in range(1, len(document["nodes"]["vision"]["body"]["quality"]) + 1)]]
                if targets:
                    subjects.append((unit, scope, targets))
            completed = cursor.setdefault("judging", [])
            unit, scope, targets = subjects[len(completed)]
            local = {r: o for r, o in observed.items() if o["test"] in scope["tests"] or
                     (purpose == "GREEN" and set(o["requirements"]).intersection(scope["requirements"]))}
            scope = {"tests": sorted({o["test"] for o in local.values()}), "requirements": scope["requirements"] or
                     sorted({r for o in local.values() for r in o["requirements"]})}
            if purpose == "demo":
                packet_body = self.store.read(packet)
                packet_body["artifactRefs"] = sorted({a for o in observed.values() if o["purpose"] == "demo" for a in o["artifactRefs"]})
                packet = self.store.record("execution_facts", packet_body)
            refs = [cursor["plan"], cursor["candidate"], packet, *local,
                    *(completed if unit == "integration" else []),
                    *(self._judgments("GREEN") if purpose == "demo" else [])]
            call = self._call(cursor, "JUDGE", refs, purpose=purpose, assessments=targets,
                              coverage={"unit": unit, "obligations": targets}, **scope)
            answer = call["answer"]
            satisfied = (evidence.ready(observed, purpose) and (purpose == "RED" or evidence.ready(local, purpose))
                         and answer["verdict"] == "satisfied" and not answer["gaps"] and all(a["satisfied"] for a in answer["assessments"]))
            with self.store.transaction():
                judgment = self.store.record("judgment", {"answer": self._answer(call), "purpose": purpose,
                    "coverage": {"unit": unit, "obligations": targets},
                    "candidate": cursor["candidate"], "observations": list(local), "satisfied": satisfied})
                cursor["judgments"].append(judgment)
                completed.append(judgment)
                owner = next(reversed(units)) if unit == "integration" else unit
                record = self.store.units()[owner]
                prior = record.get("evidence", {}).get(purpose, {}).get("observations", []) if unit == "integration" else []
                record.setdefault("evidence", {})[purpose] = {"judgment": judgment, "observations": list(dict.fromkeys([*prior, *local]))}
                self.store.unit(owner, record)
                if satisfied and len(completed) < len(subjects):
                    self._next(cursor, "JUDGE", judgment)
                    return
                cursor.pop("judging")
            with self.store.transaction():
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
