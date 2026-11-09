"""C8/C10/C16: bound user decisions and cause-directed authorized repair."""

from . import plan


class Decisions:
    def _decide(self, cursor):
        request_ref = cursor["decision"]
        request = self.store.read(request_ref)
        words = self.user({"request": request_ref, **request})
        with self.store.transaction():
            words_ref = self.store.record("user_words", {"request": request_ref,
                "subject": request["subject"]["digest"], "words": words, "origin": request["origin"]})
        explicit = isinstance(words, dict) and set(words) == {"request", "subject", "choice"}
        if isinstance(words, dict):
            if (words.get("request") != request_ref or words.get("subject") != request["subject"]["digest"]
                    or (explicit and words.get("choice") not in ("approve", "revise", "decline"))):
                raise ValueError("Decision response must bind to the displayed request and subject")
        if explicit:
            answer = words
        else:
            call = self._call(cursor, "DECIDE", [request_ref, words_ref], gate=request["gate"])
            answer = call["answer"]
        with self.store.transaction():
            answer_ref = words_ref if explicit else self._answer(call)
            if self.store.cursor()["decision"] != request_ref:
                self._incomplete(cursor, "Decision subject changed; obtain a fresh reply")
            elif answer["choice"] in ("revise", "decline"):
                self._repair(cursor, answer_ref, "decision:" + request["gate"])
            elif answer["choice"] != "approve":
                self._incomplete(cursor, answer_ref)
            else:
                approval = self.store.record("approval", {"request": request_ref,
                    "subject": request["subject"], "words": words_ref,
                    "interpretation": None if explicit else answer_ref, "scripted": self.setup.get("scriptedUser", False)})
                gate = request["gate"]
                if gate == "scope":
                    cursor["scopeApproval"] = approval
                    self._apply_repair(cursor, cursor["pendingRepair"], approval)
                elif gate == "A":
                    cursor["A"] = approval
                    cursor["lockedVisionDigest"] = request["subject"]["digest"]
                    self._next(cursor, "PLAN", approval)
                elif gate == "B":
                    cursor["B"] = approval
                    cursor.pop("resumeRepair", None)
                    self._next(cursor, "WORK(tests)", approval)
                elif gate == "check-in":
                    cursor["checkin"], cursor["purpose"] = approval, "demo"
                    for discovery in cursor.pop("discoveries", []):
                        self.store.record("disposition", {"discovery": discovery, "disposition": "deferred", "authority": approval})
                    self._next(cursor, "OBSERVE(demo)", approval)
                elif gate == "C" and self._eligible(cursor):
                    scripted = self.setup.get("scriptedUser", False)
                    outcome = self.store.record("outcome", {"accepted": not scripted, "scriptedUser": scripted,
                        "verification_complete": True, "candidate": cursor["candidate"],
                        "approval": approval, "lastProvenResult": cursor["demoJudgment"],
                        "reason": "scripted C, not owner acceptance" if scripted else "User accepted the demonstrated candidate",
                        "nextAction": None})
                    self._next(cursor, "SCRIPTED_C" if scripted else "ACCEPTED", outcome)
                else:
                    self._incomplete(cursor, "C requires the current demonstrated candidate")

    def _repair(self, cursor, cause, origin, task=None):
        counts = cursor.setdefault("repairCounts", {})
        counts[origin] = counts.get(origin, 0) + 1
        if counts[origin] > 3:
            gap = self.store.read(cause)
            if isinstance(gap.get("answer"), str):
                gap = {**gap, "answerDetail": self.store.read(gap["answer"])}
            self._incomplete(cursor, {"unresolvedGap": gap,
                                      "reason": "Three repairs exhausted for " + origin})
            return
        cursor["repairCause"], cursor["repairOrigin"] = cause, origin
        body = self.store.read(cause)
        answer = self.store.read(body["answer"])["answer"] if "candidate" in body and "answer" in body else {}
        gaps = answer.get("gaps", [])
        cursor.pop("repairPaths", None)
        if gaps and all(g["cause"] == "implementation" for g in gaps):
            paths = {g["target"] for g in gaps}
            cursor["repairPaths"] = sorted(paths)
            task = "tests" if all(p in b["testPaths"] for b in plan.select(self.store.plan(cursor["revision"]), "bead").values() for p in paths.intersection(b["ownedPaths"])) else "build"
        if task:
            units = self.store.units()
            owners = {ref for ref, unit in units.items() if not cursor.get("repairPaths")
                      or set(cursor["repairPaths"]).intersection(unit["contract"]["ownedPaths"])}
            document = self.store.plan(cursor["revision"])
            invalid = plan.affected(document, document, owners) if task == "tests" else owners
            for ref in invalid.intersection(units):
                unit = units[ref]
                unit["results"].pop(task, None)
                if task == "tests":
                    unit["results"].pop("build", None)
                    unit["checkGeneration"] = unit.get("checkGeneration", 0) + 1
                    for value in unit.get("evidence", {}).values():
                        value["stale"] = True
                self.store.unit(ref, unit)
        self.store.record("repair_route", {"cause": cause, "owner": task or "plan"})
        self._next(cursor, f"WORK({task})" if task else "REPAIR", cause)

    def _apply_repair(self, cursor, proposal, authority):
        body = self.store.read(proposal)
        if body["preimage"] != cursor["revision"]:
            raise ValueError("Repair preimage changed; proposal is stale")
        before, after = body["before"], body["after"]
        affected = plan.affected(before, after)
        cursor["revision"] = self.store.revision(after, cursor["revision"])
        cursor["plan"] = self.store.record("plan", {"revision": cursor["revision"],
            "document": after, "source": proposal, "authority": authority,
            "visionApproval": cursor.get("A"), "staleTargets": sorted(affected)})
        if before["nodes"]["vision"] != after["nodes"]["vision"]:
            cursor["vision"] = self.store.record("vision", {"answer": after["nodes"]["vision"]["body"],
                "proposal": proposal, "approval": authority})
        self._sync_units(after, affected)
        cursor.setdefault("resumeRepair", body["origin"])
        if cursor.get("B"):
            cursor["authorizedRevision"] = cursor["revision"]
            self.store.record("authorization", {"revision": cursor["revision"],
                "B": cursor["B"], "impactOrApproval": authority, "proposal": proposal})
            for key in ("demoJudgment", "demoCandidate", *(("checkin",)
                        if affected.intersection(plan.select(after, "bead")) else ())):
                cursor.pop(key, None)
        if not cursor.get("A"):
            self._request(cursor, "A", plan.subject("A", vision=after["nodes"]["vision"]["body"]))
        else:
            self._next(cursor, "PLAN" if set(after["nodes"]) == {"vision"} else "REVIEW", proposal)

    def _resume_repair(self, cursor, review):
        document = self.store.plan(cursor["revision"])
        beads = plan.select(document, "bead")
        pending_tests = set(beads) - self._finished("tests")
        pending_build = set(beads) - self._finished("build")
        origin = cursor.pop("resumeRepair")
        if pending_tests or pending_build:
            self._next(cursor, "WORK(tests)" if pending_tests else "WORK(build)", review)
        else:
            cursor["purpose"] = origin if origin in ("RED", "GREEN", "demo") else "GREEN"
            self._next(cursor, f"OBSERVE({cursor['purpose']})", review)
