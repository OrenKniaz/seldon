"""C1/C8/C16/C23: CODE owns the day-one A-to-C loop and its canonical baton."""

import time
from pathlib import Path

from . import evidence, plan, questions, work
from .providers import role_for
from .store import Store, digest, encode


class Loop:
    def __init__(self, project, scratch, dream, setup, provider, user):
        self.project, self.scratch = Path(project), Path(scratch)
        self.project.mkdir()
        self.scratch.mkdir()
        work.git(self.project, "init", "--quiet")
        self.store = Store(self.project)
        self.provider, self.user, self.setup = provider, user, setup
        self.assignment = None
        self.changed = None
        with self.store.transaction():
            kickoff = self.store.record("kickoff", {"words": dream, "setup": setup})
            self.store.cursor({"state": "ALIGN", "kickoff": kickoff, "revision": None,
                               "completed": [], "judgments": [], "observations": []})

    def _attempt(self, operation, **bindings):
        with self.store.transaction():
            return self.store.record("attempt", {"operation": operation, **bindings,
                                                 "deadline": time.time() + 30})

    def _call(self, cursor, kind, refs, _facts=None, **context):
        role = role_for(kind, context)
        refs = list(dict.fromkeys(([cursor["kickoff"]] if kind in ("ALIGN", "PLAN", "WORK") else []) + refs))
        facts = {ref: self.store.read(ref) for ref in refs}
        facts.update({b["request"]: self.store.read(b["request"]) for b in list(facts.values()) if "words" in b and "request" in b})
        if kind == "JUDGE":
            facts.update(evidence.facts(self.store, [r for r, b in facts.items() if "outputRefs" in b],
                                        cursor["candidate"], self.store.plan(cursor["revision"])))
        if cursor.get("plan") in facts:
            nodes = self.store.plan(cursor["revision"])["nodes"]
            facts[cursor["plan"]] = {"document": {"nodes": {r: n for r, n in nodes.items()
                if (n["kind"] not in ("bead", "wave") and (kind != "JUDGE" or n["kind"] != "test" or
                    context["purpose"] == "demo" or n["body"]["stage"] == "verification")) or
                (kind == "WORK" and n["kind"] == "bead" and n["body"]["task"] in (context["task"], "build"))}}}
            facts.pop(cursor["vision"], None)
        if cursor["kickoff"] in facts:
            facts[cursor["kickoff"]] = ({"words": facts[cursor["kickoff"]]["words"], "capabilities": self.setup} if kind == "ALIGN" else
                {k: v for k, v in self.setup.items() if k not in ("providers", "codexCommand", "factFiles", "userOrigin", "evidenceDirectory")})
        if kind == "REPAIR":
            facts.update({r: self.store.read(r) for r in cursor.get("latestObservations", [])})
        if kind == "WORK" and cursor.get("repairCause"):
            facts[cursor["repairCause"]] = self.store.read(cursor["repairCause"])
        if kind in ("PLAN", "REPAIR", "REVIEW", "JUDGE", "WORK"):
            context["handles"] = [r for r, n in self.store.plan(cursor["revision"])["nodes"].items()
                                  if n["kind"] not in ("bead", "wave")] if cursor["revision"] else ["vision"]
            context["paths"] = [p for b in plan.select(self.store.plan(cursor["revision"]), "bead").values()
                                for p in b["ownedPaths"]] if cursor["revision"] else []
        sources = {ref: ref for ref in facts}
        for handle, path in (self.setup.get("factFiles", {}) if kind not in ("ALIGN", "DECIDE") else {}).items():
            facts[handle], sources[handle] = {"path": path}, cursor["kickoff"]
        for ref, body in list(facts.items()):
            if kind in ("DECIDE", "REPAIR") and "words" in body:
                body.pop("subject", None)
            if isinstance(body.get("answer"), str):
                body["answer"] = self.store.read(body["answer"])["answer"]
            for key in body.get("artifactRefs", []) + list(body.get("files", {}).values()):
                content = (self.store.root / "artifacts" / key).read_bytes()
                facts[f"artifact:{key}"] = ({"imagePath": str(self.store.root / "artifacts" / key),
                    "instruction": "This actual browser screenshot is attached to the question; look at it to judge visual quality"}
                    if content.startswith((b"\x89PNG", b"\xff\xd8")) else content[:8192].decode("utf-8", errors="replace"))
                sources[f"artifact:{key}"] = ref
        facts = _facts if _facts is not None else {ref: {"sourceRecord": sources[ref], "revision": cursor["revision"],
                "boundedContent": body} for ref, body in facts.items()}
        question = questions.build(kind, facts, context)
        with self.store.transaction():
            question_ref = self.store.record("question", question)
            attempt = self.store.record("attempt", {"operation": kind, "question": question_ref,
                                                     "provider": self.setup["providers"][role],
                                                     "deadline": time.time() + getattr(self.provider, "timeout", 30)})
        def record(body):
            with self.store.transaction():
                return self.store.record("provider_reply", body)
        def check(answer):
            if kind == "PLAN":
                plan.create(self.store.read(cursor["vision"])["answer"], answer)
            elif kind == "REPAIR":
                plan.apply(self.store.plan(cursor["revision"]) if cursor["revision"] else {"nodes": {"vision": {
                    "kind": "vision", "body": plan.vision_body(self.store.read(cursor["vision"])["answer"])}}}, answer["edits"])
            if kind == "JUDGE" and sorted(a["target"] for a in answer["assessments"]) != sorted(context["assessments"]):
                raise ValueError("Judge must assess the exact requested inventory once")
        answer = questions.ask(self.provider, question, record, check)
        invocation = {"question": question_ref, "attempt": attempt, "answer": answer,
                      "provider": self.setup["providers"][role]}
        needs = answer.get("neededFacts", []) if answer.get("verdict") == "uncertain" else [
            {"target": g["target"], "question": g["correction"]} for g in answer.get("gaps", []) if g.get("cause") == "evidence"]
        if needs:
            with self.store.transaction():
                uncertain = self._answer(invocation)
            additions = {}
            for need in needs:
                target = need["target"]
                if target in self.setup.get("factFiles", {}):
                    content = Path(self.setup["factFiles"][target]).read_text(encoding="utf-8")
                    if content != facts[target]["boundedContent"]:
                        additions[target] = {**facts[target], "boundedContent": content}
            if _facts is not None or not additions:
                raise ValueError("Unresolved question: " + "; ".join(n["question"] for n in needs))
            self.store.record("fact_gather", {"cause": uncertain, "supplied": list(additions)})
            return self._call(cursor, kind, refs, _facts={**facts, **additions}, **context)
        return invocation

    def _answer(self, invocation):
        ref = self.store.record("answer", invocation)
        self.store.record("attempt_end", {"attempt": invocation["attempt"], "result": ref})
        return ref

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
        return evidence.facts(self.store, self.store.read(cursor["greenJudgment"])["observations"] if cursor.get("greenJudgment") else [],
                              candidate or cursor["candidate"], self.store.plan(cursor["revision"]))

    def _eligible(self, cursor):
        document = self.store.plan(cursor["revision"])
        required = set(plan.select(document, "bead"))
        return (required <= set(cursor["completed"]) and bool(cursor.get("checkin"))
                and bool(cursor.get("greenJudgment")) and bool(cursor.get("demoJudgment"))
                and cursor.get("demoCandidate") == cursor.get("candidate")
                and self.store.read(cursor["executionFacts"])["traceComplete"]
                and evidence.ready(self._green_facts(cursor), "GREEN")
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
                        self._next(cursor, "PLAN", approval)
                    elif gate == "B":
                        cursor["B"] = approval
                        cursor.pop("resumeRepair", None)
                        cursor["task"] = "tests"
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
        elif state == "PLAN":
            call = self._call(cursor, "PLAN", [cursor["vision"]])
            vision = self.store.read(cursor["vision"])["answer"]
            proposed = plan.create(vision, call["answer"])
            with self.store.transaction():
                source = self._answer(call)
                cursor["revision"] = self.store.revision(proposed)
                cursor["plan"] = self.store.record("plan", {"revision": cursor["revision"],
                    "document": proposed, "source": source, "visionApproval": cursor["A"]})
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
            task = cursor["task"]
            beads = plan.select(document, "bead")
            ready = [(ref, b) for ref, b in beads.items() if b["task"] == task
                     and ref not in cursor["completed"]
                     and set(b["dependencies"]) <= set(cursor["completed"])]
            if not ready:
                raise ValueError(f"No dependency-ready {task} bead")
            ref, bead = ready[0]
            approved = self.store.read(cursor["B"])["subject"]
            if (approved["revision"] != cursor["revision"] or approved["plan"] != document) and cursor.get("authorizedRevision") != cursor["revision"]:
                raise ValueError("Work requires B or a recorded impact/scope authorization for this plan")
            if cursor.get("candidate") and not self._current_candidate(cursor["candidate"]):
                raise ValueError("Delivered candidate changed outside integration")
            attempt = self._attempt("worktree", bead=ref, authorization=cursor["B"])
            owned = sorted(set(bead["ownedPaths"]) & set(cursor.get("repairPaths", bead["ownedPaths"]))) or bead["ownedPaths"]
            self.assignment = work.admit(self.project, self.scratch, owned)
            with self.store.transaction():
                assignment = self.store.record("assignment", {"attempt": attempt, "bead": ref,
                    "worktree": str(self.assignment["root"]), "ownedPaths": owned,
                    "baseline": work.candidate(self.assignment["baseline"]),
                    "checks": {r: t for r, t in plan.select(document, "test").items()
                               if (t["stage"] == "walkthrough" if owned == ["TRY.txt"] else task == "build" or t["stage"] == "verification")},
                    "executionReport": evidence.REPORT,
                    "scratch": str(self.scratch),
                    "authorization": cursor["B"]})
            feedback = []
            for local_attempt in range(3):
                call = self._call(cursor, "WORK", [cursor["plan"], assignment, *feedback], task=task)
                answer = call["answer"]
                result = self._answer(call)
                if answer["outcome"] != "done":
                    raise ValueError(encode(answer))
                local_candidate = self._candidate(self.assignment["root"])
                retained = self._green_facts(cursor, local_candidate)
                checks = {r: t for r, t in self.store.read(assignment)["checks"].items()
                          if not any(o["test"] == r and o["applies"] and o["passed"] for o in retained.values())}
                local = evidence.observe(self.assignment["root"], checks, self.scratch)
                if any(not o["cleanupVerified"] for o in local):
                    raise ValueError("Local command cleanup unverified; stop before further effects")
                feedback = evidence.save(self.store, attempt, "local:" + task, local_candidate, local, document)
                facts = evidence.facts(self.store, feedback, local_candidate, document)
                if all(o["executionUsable"] and (task == "tests" or o["passed"]) for o in facts.values()):
                    break
                if local_attempt == 2:
                    raise ValueError("Local checks unresolved after two refinements: " + encode(facts))
            self.changed = work.changes(self.assignment, answer["artifactPaths"])
            with self.store.transaction():
                baseline = self.assignment["baseline"]
                cursor["work"] = self.store.record("work", {
                    "answer": result, "assignment": assignment, "localObservations": feedback,
                    "changedPaths": sorted(self.changed), "diff": {
                        name: {"before": self.store.artifact(baseline.get(name, b""), attempt),
                               "after": self.store.artifact(data, attempt)}
                        for name, data in self.changed.items()}})
                cursor["bead"], cursor["workAttempt"] = ref, attempt
                for gap in answer["discoveries"]:
                    discovery = self.store.record("discovery", {"work": cursor["work"], **gap})
                    cursor.setdefault("discoveries", []).append(discovery)
                    self.store.record("disposition", {"discovery": discovery, "disposition": "pending check-in"})
                self._integrate(cursor)
        elif state.startswith("OBSERVE("):
            purpose = cursor["purpose"]
            stage = "walkthrough" if purpose == "demo" else "verification"
            declarations = {ref: body for ref, body in plan.select(document, "test").items()
                            if body["stage"] == stage}
            if not self._current_candidate(cursor["candidate"]):
                raise ValueError("Observation requires declared commands and the exact candidate")
            attempt = self._attempt("observe", purpose=purpose,
                                    candidate=cursor["candidate"], tests=list(declarations))
            results = evidence.observe(self.project, declarations, self.scratch)
            if not self._current_candidate(cursor["candidate"]):
                raise ValueError("Observation changed the delivered candidate")
            with self.store.transaction():
                cursor["latestObservations"] = evidence.save(
                    self.store, attempt, purpose, cursor["candidate"], results, document)
                cursor["observations"].extend(cursor["latestObservations"])
                self.store.record("attempt_end", {
                    "attempt": attempt, "results": cursor["latestObservations"]})
                facts = evidence.facts(self.store, cursor["latestObservations"], cursor["candidate"], document)
                if any(not o["executionUsable"] for o in facts.values()):
                    fault = self.store.record("seldon_fault", {"fault": "VERIFICATION_NOT_EXECUTED",
                        "observations": cursor["latestObservations"]})
                    self._incomplete(cursor, fault)
                elif not evidence.ready(facts, purpose):
                    fault = self.store.record("execution_failure", {"observations": cursor["latestObservations"]})
                    self._repair(cursor, fault, purpose, task="tests" if purpose == "RED" else "build")
                else:
                    self._next(cursor, "JUDGE", cursor["latestObservations"])
        elif state == "JUDGE":
            purpose = cursor["purpose"]
            quality = document["nodes"]["vision"]["body"]["quality"]
            inventory = ([ref for ref, body in plan.select(document, "test").items()
                          if body["stage"] == "verification"] if purpose != "demo" else
                         [*plan.select(document, "requirement"),
                          "noStubsMocksMissing",
                          *[f"quality:{i}" for i, _ in enumerate(quality, 1)]])
            if purpose == "demo":
                inventory += ["tryItGuide"]
            facts = self._green_facts(cursor) if purpose == "demo" else {}
            retained = list(facts)
            if purpose == "demo" and not evidence.ready(facts, "GREEN"):
                with self.store.transaction():
                    cursor["purpose"] = "GREEN"
                    self._next(cursor, "OBSERVE(GREEN)", cursor["candidate"])
                return self.store.cursor()["state"]
            observed = evidence.facts(self.store, [*retained, *cursor["latestObservations"]], cursor["candidate"], document)
            coverage = sorted({r for o in observed.values() if o["applies"] and o["passed"] for r in o["requirements"]})
            packet = self.store.record("execution_facts", {"candidate": cursor["candidate"],
                "reusedEvidence": retained, "coverage": coverage,
                "traceComplete": purpose == "demo" and set(plan.select(document, "requirement")) <= set(coverage)})
            refs = [cursor["plan"], cursor["candidate"], packet, *cursor["latestObservations"], *retained,
                    *([cursor["greenJudgment"]] if purpose == "demo" else [])]
            call = self._call(cursor, "JUDGE", refs, purpose=purpose, assessments=inventory)
            answer = call["answer"]
            assessments = answer["assessments"]
            satisfied = (answer["verdict"] == "satisfied" and not answer["gaps"]
                         and all(a["satisfied"] for a in assessments))
            with self.store.transaction():
                result = self._answer(call)
                judgment = self.store.record("judgment", {"answer": result, "purpose": purpose,
                    "candidate": cursor["candidate"], "observations": cursor["latestObservations"],
                    "satisfied": satisfied})
                cursor["judgments"].append(judgment)
                if not satisfied:
                    self._repair(cursor, judgment, purpose)
                elif purpose == "RED":
                    cursor["redJudgment"] = judgment
                    cursor["task"] = "build"
                    self._next(cursor, "WORK(build)", judgment)
                elif purpose == "GREEN":
                    cursor["greenJudgment"] = judgment
                    self._next(cursor, "STEER", judgment)
                else:
                    cursor["demoJudgment"], cursor["demoCandidate"] = judgment, cursor["candidate"]
                    cursor["executionFacts"] = packet
                    if not self._eligible(cursor):
                        raise ValueError("C requires all authorized work and final evidence")
                    self._request(cursor, "C", plan.subject("C", scope=document["nodes"]["vision"],
                        revision=cursor["revision"], candidate=cursor["candidate"],
                        evidence=[*retained, *cursor["latestObservations"]],
                        judgments=[cursor[k] for k in ("redJudgment", "greenJudgment", "demoJudgment")],
                        executionFacts=packet, guide=cursor["work"]))
        elif state == "STEER":
            waves = plan.select(document, "wave")
            if any(not set(wave["beads"]) <= set(cursor["completed"]) for wave in waves.values()):
                raise ValueError("Wave check-in requires every member to be integrated")
            with self.store.transaction():
                self._request(cursor, "check-in", plan.subject("check-in", waves=waves,
                    candidate=cursor["candidate"], judgment=cursor["greenJudgment"],
                    proposedDeferrals={r: self.store.read(r) for r in cursor.get("discoveries", [])},
                    continuation="Approve these discovery deferrals and proceed to final walkthrough and acceptance"))
        else:
            raise ValueError(f"Unknown state: {state}")
        return self.store.cursor()["state"]

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
            owners = [b["task"] for b in plan.select(self.store.plan(cursor["revision"]), "bead").values()
                      if paths.intersection(b["ownedPaths"])]
            task = owners[0] if owners else "tests" if origin == "RED" else "build"
        if task:
            cursor["task"] = task
            cursor["completed"] = [r for r in cursor["completed"] if
                self.store.plan(cursor["revision"])["nodes"][r]["body"]["task"] != task]
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
        cursor["completed"] = [ref for ref in cursor["completed"]
                                if ref in after["nodes"] and ref not in affected]
        cursor.setdefault("resumeRepair", body["origin"])
        if cursor.get("B"):
            cursor["authorizedRevision"] = cursor["revision"]
            self.store.record("authorization", {"revision": cursor["revision"],
                "B": cursor["B"], "impactOrApproval": authority, "proposal": proposal})
            for key in ("demoJudgment", "demoCandidate", *(("redJudgment", "greenJudgment", "checkin")
                        if affected.intersection(plan.select(after, "bead")) else ())):
                cursor.pop(key, None)
        if not cursor.get("A"):
            self._request(cursor, "A", plan.subject("A", vision=after["nodes"]["vision"]["body"]))
        else:
            self._next(cursor, "PLAN" if set(after["nodes"]) == {"vision"} else "REVIEW", proposal)

    def _resume_repair(self, cursor, review):
        document = self.store.plan(cursor["revision"])
        beads = plan.select(document, "bead")
        pending = [b["task"] for ref, b in beads.items() if ref not in cursor["completed"]]
        origin = cursor.pop("resumeRepair")
        cursor["task"] = "tests" if "tests" in pending else "build"
        if pending:
            self._next(cursor, f"WORK({cursor['task']})", review)
        else:
            cursor["purpose"] = origin if origin in ("RED", "GREEN", "demo") else "GREEN"
            self._next(cursor, f"OBSERVE({cursor['purpose']})", review)

    def _integrate(self, cursor):
        integration = work.integrate(self.project, self.assignment["baseline"], self.changed)
        ref = self.store.record("integration", {"work": cursor["work"], **integration})
        cursor["candidate"] = self._candidate()
        cursor["completed"].append(cursor["bead"])
        self.store.record("attempt_end", {"attempt": cursor["workAttempt"], "result": ref})
        self.assignment = self.changed = None
        task = cursor["task"]
        remaining = [b for r, b in plan.select(self.store.plan(cursor["revision"]), "bead").items()
                     if b["task"] == task and r not in cursor["completed"]]
        if remaining:
            self._next(cursor, f"WORK({task})", ref)
        else:
            cursor["purpose"] = {"tests": "RED", "build": "GREEN"}[task]
            if task == "build" and cursor.get("greenJudgment"):
                if evidence.ready(self._green_facts(cursor), "GREEN"):
                    cursor["purpose"] = "demo"
            self._next(cursor, f"OBSERVE({cursor['purpose']})", ref)

    def run(self):
        try:
            while self.store.cursor()["state"] not in ("ACCEPTED", "SCRIPTED_C", "INCOMPLETE"):
                self.step()
        except (ValueError, KeyError, RuntimeError, OSError) as error:
            with self.store.transaction():
                self._incomplete(self.store.cursor(), f"{type(error).__name__}: {error}")
        return self.store.cursor()
