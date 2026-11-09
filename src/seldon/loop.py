"""C1/C8/C16/C23: CODE owns the day-one A-to-C loop and its canonical baton."""

import time
from pathlib import Path

from . import evidence, plan, questions, work
from .store import Store, digest


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

    def _call(self, cursor, kind, refs, **context):
        role = {"REVIEW": "reviewer", "WORK": "worker", "JUDGE": "judge"}.get(kind, "planner")
        refs = list(dict.fromkeys([cursor["kickoff"], *refs]))
        facts = {ref: {"sourceRecord": ref, "revision": cursor["revision"],
                       "boundedContent": self.store.read(ref)} for ref in refs}
        for ref in refs:
            body = facts[ref]["boundedContent"]
            artifacts = body.get("outputRefs", []) + list(body.get("files", {}).values())
            for key in artifacts:
                content = (self.store.root / "artifacts" / key).read_bytes()
                facts[f"artifact:{key}"] = {"sourceRecord": ref, "revision": cursor["revision"],
                    "boundedContent": content.decode("utf-8", errors="replace")}
        question = questions.build(kind, facts, context)
        with self.store.transaction():
            question_ref = self.store.record("question", question)
            attempt = self.store.record("attempt", {"operation": kind, "question": question_ref,
                                                     "provider": self.setup["providers"][role],
                                                     "deadline": time.time() + 30})
        answer = questions.ask(self.provider, question)
        return {"question": question_ref, "attempt": attempt, "answer": answer,
                "provider": self.setup["providers"][role]}

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

    def _candidate(self):
        content = work.files(self.project)
        body = work.candidate(content)
        ref = self.store.record("candidate", body)
        for data in content.values():
            self.store.artifact(data, ref, "application/octet-stream")
        return ref

    def _current_candidate(self, ref):
        return self.store.read(ref) == work.candidate(work.files(self.project))

    def _incomplete(self, cursor, cause):
        outcome = self.store.record("outcome", {"accepted": False, "verification_complete": False,
            "reason": cause, "lastProvenResult": cursor["judgments"][-1:],
            "nextAction": "Resolve this cause through milestone M1a before continuing"})
        self._next(cursor, "INCOMPLETE", outcome)

    def _eligible(self, cursor):
        document = self.store.plan(cursor["revision"])
        required = set(plan.select(document, "bead"))
        green = self.store.read(self.store.read(cursor["greenJudgment"])["candidate"])["files"]
        delivered = self.store.read(cursor["candidate"])["files"]
        return (required <= set(cursor["completed"]) and bool(cursor.get("checkin"))
                and bool(cursor.get("greenJudgment")) and bool(cursor.get("demoJudgment"))
                and cursor.get("demoCandidate") == cursor.get("candidate")
                and all(delivered.get(path) == value for path, value in green.items())
                and self._current_candidate(cursor["candidate"]))

    def step(self):
        cursor = self.store.cursor()
        state = cursor["state"]
        if state in ("ACCEPTED", "INCOMPLETE"):
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
                    "subject": request["subject"], "words": words, "origin": request["origin"]})
            call = self._call(cursor, "DECIDE", [request_ref, words_ref])
            with self.store.transaction():
                answer_ref = self._answer(call)
                if (self.store.cursor()["decision"] != request_ref
                        or call["answer"]["choice"] != "approve"):
                    self._incomplete(cursor, answer_ref)
                else:
                    approval = self.store.record("approval", {"request": request_ref,
                        "subject": request["subject"], "words": words_ref,
                        "interpretation": answer_ref})
                    gate = request["gate"]
                    if gate == "A":
                        cursor["A"] = approval
                        self._next(cursor, "PLAN", approval)
                    elif gate == "B":
                        cursor["B"] = approval
                        cursor["task"] = "tests"
                        self._next(cursor, "WORK(tests)", approval)
                    elif gate == "check-in":
                        cursor["checkin"], cursor["task"] = approval, "guide"
                        self._next(cursor, "WORK(guide)", approval)
                    elif gate == "C" and self._eligible(cursor):
                        outcome = self.store.record("outcome", {"accepted": True,
                            "verification_complete": True, "candidate": cursor["candidate"],
                            "approval": approval, "lastProvenResult": cursor["demoJudgment"],
                            "reason": "User accepted the demonstrated candidate",
                            "nextAction": None})
                        self._next(cursor, "ACCEPTED", outcome)
                    else:
                        self._incomplete(cursor, "C requires the current demonstrated candidate")
        elif state == "PLAN":
            call = self._call(cursor, "PLAN", [cursor["vision"], cursor["A"]])
            vision = self.store.read(cursor["vision"])["answer"]
            proposed = plan.create(vision, call["answer"]["edits"])
            with self.store.transaction():
                source = self._answer(call)
                cursor["revision"] = self.store.revision(proposed)
                cursor["plan"] = self.store.record("plan", {"revision": cursor["revision"],
                    "document": proposed, "source": source, "visionApproval": cursor["A"]})
                self._next(cursor, "REVIEW", cursor["plan"])
        elif state == "REVIEW":
            call = self._call(cursor, "REVIEW", [cursor["vision"], cursor["plan"]])
            answer = call["answer"]
            with self.store.transaction():
                result = self._answer(call)
                cursor["review"] = self.store.record("review", {"answer": result,
                    "revision": cursor["revision"], "planDigest": digest(document),
                    "reviewer": call["provider"]})
                if (answer["verdict"] == "adequate" and not answer["gaps"]
                        and not answer["neededFacts"]):
                    self._request(cursor, "B", plan.subject("B", revision=cursor["revision"],
                        plan=document, review=cursor["review"]))
                else:
                    self._incomplete(cursor, result)
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
            if approved["revision"] != cursor["revision"] or approved["plan"] != document:
                raise ValueError("Work requires B authorization for this canonical plan")
            if cursor.get("candidate") and not self._current_candidate(cursor["candidate"]):
                raise ValueError("Delivered candidate changed outside integration")
            attempt = self._attempt("worktree", bead=ref, authorization=cursor["B"])
            self.assignment = work.admit(self.project, self.scratch, bead["ownedPaths"])
            with self.store.transaction():
                assignment = self.store.record("assignment", {"attempt": attempt, "bead": ref,
                    "worktree": str(self.assignment["root"]), "ownedPaths": bead["ownedPaths"],
                    "baseline": work.candidate(self.assignment["baseline"]),
                    "authorization": cursor["B"]})
            call = self._call(cursor, "WORK", [cursor["plan"], cursor["B"], assignment], task=task)
            answer = call["answer"]
            self.changed = work.changes(self.assignment, answer["artifactPaths"])
            with self.store.transaction():
                result = self._answer(call)
                baseline = self.assignment["baseline"]
                cursor["work"] = self.store.record("work", {
                    "answer": result, "assignment": assignment,
                    "changedPaths": sorted(self.changed), "diff": {
                        name: {"before": self.store.artifact(baseline.get(name, b""), attempt),
                               "after": self.store.artifact(data, attempt)}
                        for name, data in self.changed.items()}})
                cursor["bead"], cursor["workAttempt"] = ref, attempt
                if answer["outcome"] != "done" or answer["discoveries"]:
                    self._incomplete(cursor, result)
                elif task == "build":
                    self._next(cursor, "INTEGRATE", cursor["work"])
                else:
                    self._integrate(cursor)
        elif state == "INTEGRATE":
            with self.store.transaction():
                self._integrate(cursor)
        elif state.startswith("OBSERVE("):
            purpose = cursor["purpose"]
            stage = "walkthrough" if purpose == "demo" else "verification"
            declarations = {ref: body for ref, body in plan.select(document, "test").items()
                            if body["stage"] == stage}
            if not declarations or not self._current_candidate(cursor["candidate"]):
                raise ValueError("Observation requires declared commands and the exact candidate")
            attempt = self._attempt("observe", purpose=purpose,
                                    candidate=cursor["candidate"], tests=list(declarations))
            results = evidence.observe(self.project, declarations)
            if not self._current_candidate(cursor["candidate"]):
                raise ValueError("Observation changed the delivered candidate")
            with self.store.transaction():
                cursor["latestObservations"] = evidence.save(
                    self.store, attempt, purpose, cursor["candidate"], results)
                cursor["observations"].extend(cursor["latestObservations"])
                self.store.record("attempt_end", {
                    "attempt": attempt, "results": cursor["latestObservations"]})
                self._next(cursor, "JUDGE", cursor["latestObservations"])
        elif state == "JUDGE":
            purpose = cursor["purpose"]
            quality = document["nodes"]["vision"]["body"]["quality"]
            inventory = ([ref for ref, body in plan.select(document, "test").items()
                          if body["stage"] == "verification"] if purpose == "RED" else
                         [*plan.select(document, "requirement"),
                          "traceComplete", "noStubsMocksMissing",
                          *[f"quality:{i}" for i, _ in enumerate(quality, 1)]])
            if purpose == "demo":
                inventory += ["walkthrough", "tryItGuide"]
            refs = [cursor["plan"], cursor["candidate"],
                    *cursor["latestObservations"], *cursor["judgments"]]
            call = self._call(cursor, "JUDGE", refs, purpose=purpose, assessments=inventory)
            answer = call["answer"]
            assessments = answer["assessments"]
            if sorted(a["target"] for a in assessments) != sorted(inventory):
                raise ValueError("Judge must assess the exact requested inventory once")
            observations = [self.store.read(ref) for ref in cursor["latestObservations"]]
            observed = all(o["executionStarted"] and not o["timedOut"] and o["exitCode"] is not None
                           and (o["exitCode"] != 0 if purpose == "RED" else o["exitCode"] == 0)
                           for o in observations)
            satisfied = (observed and answer["verdict"] == "satisfied" and not answer["gaps"]
                         and not answer["neededFacts"] and all(a["satisfied"] for a in assessments))
            with self.store.transaction():
                result = self._answer(call)
                judgment = self.store.record("judgment", {"answer": result, "purpose": purpose,
                    "candidate": cursor["candidate"], "observations": cursor["latestObservations"],
                    "satisfied": satisfied})
                cursor["judgments"].append(judgment)
                if not satisfied:
                    self._incomplete(cursor, judgment)
                elif purpose == "RED":
                    cursor["task"] = "build"
                    self._next(cursor, "WORK(build)", judgment)
                elif purpose == "GREEN":
                    cursor["greenJudgment"] = judgment
                    self._next(cursor, "STEER", judgment)
                else:
                    cursor["demoJudgment"], cursor["demoCandidate"] = judgment, cursor["candidate"]
                    if not self._eligible(cursor):
                        raise ValueError("C requires all authorized work and final evidence")
                    self._request(cursor, "C", plan.subject("C", scope=document["nodes"]["vision"],
                        revision=cursor["revision"], candidate=cursor["candidate"],
                        evidence=cursor["observations"], judgments=cursor["judgments"],
                        guide=cursor["work"]))
        elif state == "STEER":
            waves = plan.select(document, "wave")
            if any(not set(wave["beads"]) <= set(cursor["completed"]) for wave in waves.values()):
                raise ValueError("Wave check-in requires every member to be integrated")
            call = self._call(cursor, "STEER", [
                cursor["plan"], cursor["greenJudgment"], cursor["candidate"]])
            with self.store.transaction():
                result = self._answer(call)
                self._request(cursor, "check-in", plan.subject("check-in",
                    waves=waves,
                    candidate=cursor["candidate"], judgment=cursor["greenJudgment"],
                    continuation="Final walkthrough and try-it-yourself guide", direction=result))
        else:
            raise ValueError(f"Unknown state: {state}")
        return self.store.cursor()["state"]

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
            cursor["purpose"] = {"tests": "RED", "build": "GREEN", "guide": "demo"}[task]
            self._next(cursor, f"OBSERVE({cursor['purpose']})", ref)

    def run(self):
        while self.store.cursor()["state"] not in ("ACCEPTED", "INCOMPLETE"):
            self.step()
        return self.store.cursor()

    # C17 extension: wave-end continuation plans successive waves from integrated results.
