"""C12/C13/C18/C23: schedule durable scoped attempts and publish finished patches."""

import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from . import evidence, plan, work
from .store import encode


class Execution:
    def _sync_units(self, document, invalid=()):
        retained = self.store.units()
        for ref, contract in plan.select(document, "bead").items():
            unit = retained.get(ref, {"results": {}, "attempts": []})
            if ref in invalid:
                self.store.record("invalidation", {"unit": ref, "results": unit["results"], "reason": "Changed contract or upstream obligation"})
                unit["results"] = {}
                for value in unit.get("evidence", {}).values():
                    value["stale"] = True
            unit["contract"] = contract
            self.store.unit(ref, unit)

    def _finished(self, phase="build"):
        document = self.store.plan(self.store.cursor()["revision"])
        return {r for r, u in self.store.units().items()
                if r in plan.select(document, "bead") and phase in u["results"]}

    def _assign(self, cursor, ref, phase):
        unit = self.store.units()[ref]
        contract = unit["contract"]
        owned = contract["testPaths"] if phase == "tests" else [p for p in contract["ownedPaths"] if p not in contract["testPaths"]]
        if cursor.get("repairPaths"):
            owned = sorted(set(owned).intersection(cursor["repairPaths"])) or owned
        assignment = work.admit(self.project, self.scratch, owned)
        document = self.store.plan(cursor["revision"])
        checks = {r: t for r, t in plan.select(document, "test").items()
                  if r in contract["tests"] and (t["stage"] == "walkthrough" if owned == ["TRY.txt"] else phase == "build" or t["stage"] == "verification")}
        with self.store.transaction():
            attempt = self._attempt("work", unit=ref, phase=phase, startedAt=time.time(), authorization=cursor["B"])
            assignment_ref = self.store.record("assignment", {**assignment, "unit": ref, "phase": phase,
                "attempt": attempt, "checks": checks,
                "fileOwners": plan.file_owners(document),
                "checkPaths": sorted({p for r in plan.prerequisites(document, ref)
                                      for p in plan.select(document, "bead")[r]["ownedPaths"]}),
                "scratch": str(self.scratch), "authorization": cursor["B"]})
            unit.pop("waiting", None)
            unit["active"] = {"assignment": assignment_ref, "attempt": attempt, "phase": phase, "status": "running"}
            unit["attempts"].append(attempt)
            self.store.unit(ref, unit)
        return assignment_ref

    def _perform(self, cursor, ref):
        unit = self.store.units()[ref]
        active = unit["active"]
        assignment_ref = active["assignment"]
        assignment = self.store.read(assignment_ref)
        root, phase = Path(assignment["worktree"]), active["phase"]
        document = self.store.plan(cursor["revision"])
        feedback = []
        for local_attempt in range(3):
            if active.get("report"):
                invocation = self.store.read(active["report"])
                answer, result = invocation["answer"], active.pop("report")
            else:
                call = self._call(cursor, "WORK", [cursor["plan"], assignment_ref, *feedback], task=phase, unit=ref,
                                  goal=("Write automated checks of the accepted obligations; execute them against the baseline. Product failures are expected feedback."
                                        if phase == "tests" else "Make the checks pass and the product beautiful."))
                answer = call["answer"]
                with self.store.transaction():
                    active["invocation"] = call["attempt"]
                    result = self._answer(call)
                    active.update(report=result, status="checking")
                    unit["active"] = active
                    self.store.unit(ref, unit)
                    self.store.record("writer_end", {"attempt": active["attempt"], "report": result, "finishedAt": time.time()})
            if answer["outcome"] != "done":
                missing = {owner for gap in answer["discoveries"]
                           if gap["cause"] == "plan" and gap["target"] not in assignment["checkPaths"]
                           for owner in assignment["fileOwners"].get(gap["target"], [])
                           if owner not in plan.prerequisites(document, ref)}
                if missing:
                    with self.store.transaction():
                        defect = self.store.record("plan_defect", {"unit": ref, "report": result,
                            "missingDependencies": sorted(missing), "discoveries": answer["discoveries"]})
                        self.store.record("attempt_end", {"attempt": active["attempt"], "result": defect})
                    return defect
                raise ValueError(encode(answer))
            local_candidate = self._candidate(root)
            retained = self._green_facts(cursor, local_candidate)
            checks = {r: t for r, t in assignment["checks"].items()
                      if not any(o["test"] == r and o["applies"] and o["passed"] for o in retained.values())}
            local = evidence.observe(root, checks, self.scratch) if phase == "build" else []
            if any(not o["cleanupVerified"] for o in local):
                raise ValueError("Local command cleanup unverified; stop before further effects")
            with self.store.transaction():
                feedback = evidence.save(self.store, active["attempt"], "local:" + phase, local_candidate, local, document)
            facts = evidence.facts(self.store, feedback, local_candidate, document)
            if all(o["executionUsable"] and (phase == "tests" or o["passed"]) for o in facts.values()):
                break
            if local_attempt == 2:
                raise ValueError("Local checks unresolved after two refinements: " + encode(facts))
            active.pop("report", None)
            with self.store.transaction():
                active["status"] = "running"
                unit["active"] = active
                self.store.unit(ref, unit)
        changed = work.changes(assignment, answer["artifactPaths"])
        with self.store.transaction():
            result_ref = self.store.record("work", {"answer": result, "assignment": assignment_ref,
                "localObservations": feedback, "changedPaths": sorted(changed), "diff": {
                    name: {"before": assignment["baseline"]["files"].get(name),
                           "after": self.store.artifact(data, active["attempt"])} for name, data in changed.items()}})
            active.update(work=result_ref, status="ready")
            unit["active"] = active
            self.store.unit(ref, unit)
        return result_ref

    def _publish_result(self, cursor, ref, result_ref):
        unit = self.store.units()[ref]
        active = unit["active"]
        result = self.store.read(result_ref)
        assignment = self.store.read(result["assignment"])
        changed = {p: (self.store.root / "artifacts" / patch["after"]).read_bytes() for p, patch in result["diff"].items()}
        integration = work.prepare(self.project, self.scratch, assignment, changed)
        with self.store.transaction():
            cursor["publication"] = self.store.record("publication", {**integration,
                "unit": ref, "phase": active["phase"], "work": result_ref, "diff": result["diff"]})
            self.store.cursor(cursor)
        self._finish_publication(cursor)

    def _finish_publication(self, cursor):
        publication_ref = cursor["publication"]
        publication = self.store.read(publication_ref)
        work.publish(self.project, self.store, publication)
        with self.store.transaction():
            ref = publication["unit"]
            unit = self.store.units()[ref]
            active = unit.pop("active")
            integration = self.store.record("integration", {"publication": publication_ref, **publication})
            unit["results"][publication["phase"]] = {"work": publication["work"], "integration": integration}
            self.store.unit(ref, unit)
            cursor["candidate"] = self._candidate()
            self.store.record("attempt_end", {"attempt": active["attempt"], "result": integration, "finishedAt": time.time()})
            for gap in self.store.read(self.store.read(publication["work"])["answer"])["answer"]["discoveries"]:
                discovery = self.store.record("discovery", {"work": publication["work"], **gap})
                cursor.setdefault("discoveries", []).append(discovery)
                self.store.record("disposition", {"discovery": discovery, "disposition": "pending check-in"})
            cursor.pop("publication")
            self.store.cursor(cursor)

    def _execute_units(self, cursor, document):
        phase = cursor["state"][5:-1]
        approved = self.store.read(cursor["B"])["subject"]
        if (approved["revision"] != cursor["revision"] or approved["plan"] != document) and cursor.get("authorizedRevision") != cursor["revision"]:
            raise ValueError("Work requires B or a recorded impact/scope authorization")
        if cursor.get("candidate") and not self._current_candidate(cursor["candidate"]):
            raise ValueError("Delivered candidate changed outside integration")
        contracts = plan.select(document, "bead")
        running = {}
        defects = []
        capacity = min(4, os.cpu_count() or 1)
        # All SQLite transitions have one writer lock; provider calls and real checks overlap.
        with ThreadPoolExecutor(max_workers=capacity) as pool:
            while running or (not defects and set(contracts) - self._finished(phase)):
                busy = {p for r in running.values() for p in self.store.read(self.store.units()[r]["active"]["assignment"])["ownedPaths"]}
                for ref, contract in contracts.items():
                    if defects:
                        break  # Drain existing writers before changing their plan.
                    if ref in self._finished(phase) or ref in running.values():
                        continue
                    unit = self.store.units()[ref]
                    if unit.get("active", {}).get("status") == "uncertain":
                        raise ValueError(f"Writer {ref} is uncertain; reconcile its terminal receipt before resuming")
                    unmet = set(contract["dependencies"]) - self._finished(phase)
                    owned = contract["testPaths"] if phase == "tests" else [p for p in contract["ownedPaths"] if p not in contract["testPaths"]]
                    conflict = sorted(set(owned) & busy)
                    reason = {"dependencies": sorted(unmet)} if unmet else {"files": conflict} if conflict else {"pool": capacity} if len(running) >= capacity else None
                    if reason:
                        if unit.get("waiting") != reason:
                            with self.store.transaction():
                                self.store.record("unit_wait", {"unit": ref, "phase": phase, "reason": reason})
                                unit["waiting"] = reason
                                self.store.unit(ref, unit)
                        continue
                    if not unit.get("active"):
                        self._assign(cursor, ref, phase)
                    active = self.store.units()[ref]["active"]
                    if active.get("work"):
                        self._publish_result(cursor, ref, active["work"])
                        continue
                    running[pool.submit(self._perform, dict(cursor), ref)] = ref
                    busy.update(owned)
                if not running:
                    if set(contracts) <= self._finished(phase):
                        break
                    raise ValueError("No dependency-ready unit")
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    ref = running.pop(future)
                    result = future.result()
                    if "missingDependencies" in self.store.read(result):
                        with self.store.transaction():
                            unit = self.store.units()[ref]
                            unit.pop("active")
                            self.store.unit(ref, unit)
                        defects.append(result)
                    else:
                        self._publish_result(cursor, ref, result)
        if defects:
            edits = {}
            for defect in defects:
                body = self.store.read(defect)
                ref = body["unit"]
                contract = edits.setdefault(ref, {**contracts[ref], "dependencies": list(contracts[ref]["dependencies"])})
                contract["dependencies"] = sorted(set(contract["dependencies"]) | set(body["missingDependencies"]))
            with self.store.transaction():
                cause = self.store.record("dependency_repair", {"defects": defects, "dependencyEdits": [
                    {"kind": "bead", "targetHandle": ref, "typedBody": body} for ref, body in edits.items()]})
                self._repair(cursor, cause, "dependencies")
            return
        cursor["purpose"] = "RED" if phase == "tests" else "GREEN"
        if phase == "build" and self._green_ready(cursor):
            cursor["purpose"] = "demo" if cursor.get("checkin") else "GREEN"
        with self.store.transaction():
            self._next(cursor, f"OBSERVE({cursor['purpose']})", cursor["candidate"])
