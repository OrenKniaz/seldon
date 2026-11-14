"""C12/C13/C14/C18/C23; R7/R8/R9/R11: a whole worker session owns each unit loop."""

import json
import time
from dataclasses import replace
from pathlib import Path

from . import decisions, fixups, gitwork, inference, plan, proof, questions, recovery
from .providers import Deadline, ProviderFailure, ResumeUnavailable, session_id, terminal_answer
from .schema import validate
from .states import advance


def recovered_report(store, ref):
    unit = store.unit(ref)
    if unit.report:
        return unit.report
    if unit.callAttempt:
        replies = [b for _, _, b in store.records("provider_reply")
                   if b["attempt"] == unit.callAttempt and not b["errors"]]
        if replies:
            return store.record("answer", {"unit": ref, "attempt": unit.callAttempt, "kind": "WORK",
                "answer": json.loads(replies[-1]["rawAnswer"])["answer"], "recovered": True})
    if unit.callFolder:
        folder = Path(unit.callFolder)
        receipt = folder / "result.json"
        if receipt.is_file():
            result = json.loads(receipt.read_text("utf-8"))
            if result.get("cleanupVerified") and not result.get("timedOut"):
                raw = terminal_answer((folder / "stdout.bin").read_text("utf-8", errors="replace"))
                question = json.loads((folder / "question.json").read_text("utf-8"))
                validate(json.loads(raw), question["schema"])
                questions.validate_discoveries(store.plan(store.cursor().revision), json.loads(raw)["answer"])
                return store.record("answer", {"unit": ref, "attempt": unit.callAttempt, "kind": "WORK",
                    "answer": json.loads(raw)["answer"], "recovered": True})
    return None


def thread_from_recording(store, ref):
    unit = store.unit(ref)
    if unit.callFolder:
        stream = Path(unit.callFolder) / "stdout.bin"
        if stream.is_file():
            thread = session_id(stream.read_text("utf-8", errors="replace"))
            if thread:
                with store.transaction():
                    store.unit(ref, replace(store.unit(ref), thread=thread))


def work(store, provider, ref):
    report = recovered_report(store, ref)
    if report is None:
        thread_from_recording(store, ref)
        question = questions.work_brief(store, ref)
        store.record("writer_start", {"unit": ref, "session": store.unit(ref).thread,
                                      "startedAt": time.time()})
        try:
            report, answer = inference.call(store, provider, question, unit=ref)
        except ResumeUnavailable as error:
            if not error.cleanup_verified:
                raise
            store.record("resume_unavailable", {"unit": ref, "reason": str(error)})
            with store.transaction():
                row = store.unit(ref)
                store.unit(ref, replace(row, thread=None,
                                        failure=row.failure + "\nResume unavailable: " + str(error)))
            question = questions.work_brief(store, ref)
            question["execution"]["session"] = None
            question["facts"]["Session recovery"] = "Resume unavailable; use this brief, patch and failure."
            report, answer = inference.call(store, provider, question, unit=ref)
    else:
        answer = store.read(report)["answer"]
    store.record("writer_end", {"unit": ref, "report": report, "finishedAt": time.time()})
    document = store.plan(store.cursor().revision)
    contracts = plan.select(document, "unit")[ref]
    if discoveries := [d for d in answer["discoveries"]
                       if d["owner"] in contracts["consumes"] + contracts["provides"]]:
        with store.integration_lock:
            result, correction = inference.call(store, provider,
                questions.contract_brief(store, ref, discoveries))
            with store.transaction():
                if correction["verdict"] == "user":
                    recovery.recover(store, ref, str(discoveries) + "\n" + correction["why"],
                                     cause=result, direct=True)
                else:
                    key = recovery.lineage(store, ref)
                    shape = plan.contract_texts(store.plan(store.cursor().revision), correction["changes"])
                    texts = plan.select(shape, "contract")
                    current = plan.select(store.plan(store.cursor().revision), "contract")
                    if recovery.failed(store, result, ref, shape=texts, direct=texts == current):
                        advance(store, "asked", result, unit=ref, failure=str(discoveries))
                        decisions.ask_unit(store, correction["why"])
                        return
                    plan.correct_contracts(store, result, correction)
                    fixups.handoff(store, ref, report, {"outcome": "blocked", "discoveries": [
                        {"owner": c["contract"], "correction": c["text"]} for c in correction["changes"]]},
                        key=key, charged=True)
        return
    foreign = [d for d in answer["discoveries"] if d["owner"] != ref]
    if foreign and fixups.handoff(store, ref, report, {**answer, "discoveries": foreign}):
        if not any(d["owner"] == ref for d in answer["discoveries"]):
            return
    if answer["outcome"] == "blocked" and any(d["owner"] == ref for d in answer["discoveries"]):
        recovery.recover(store, ref, str(answer["discoveries"]), cause=report, direct=True)
        return
    if answer.get("outcome") != "done":
        raise ValueError("Worker could not complete this unit: " + str(answer))
    with store.transaction():
        for discovery in answer["discoveries"]:
            store.record("discovery", {"unit": ref, "report": report, "disposition": "pending check-in",
                                        **discovery})
        advance(store, "returned", report, unit=ref, report=report)


def integrate(store, ref, checks):
    with store.integration_lock:
        unit = store.unit(ref)
        cursor = store.cursor()
        project = store.root.parent
        gitwork.unchanged(project, cursor.candidate)
        commit = gitwork.integrated(project, cursor.scratch, unit.base, unit.P, cursor.candidate)
        try:
            gitwork.checkout(unit.worktree, commit)
            observations = proof.observe(store, unit.worktree, commit, checks, "integrated", ref)
            bad = proof.failures(store, observations)
            if bad:
                raise ValueError("Integrated checks failed: " + proof.explain(bad))
        except proof.UncertainCleanup:
            raise
        except (ValueError, RuntimeError, OSError):
            gitwork.checkout(unit.worktree, unit.P)
            raise
        with store.transaction():
            intent = store.record("publication", {"unit": ref, "before": cursor.candidate,
                "commit": commit, "patch": unit.P, "observations": observations})
            store.cursor(replace(store.cursor(), publication=intent))
        gitwork.publish(project, store)


def run_unit(store, provider, ref):
    document = store.plan(store.cursor().revision)
    contract = plan.select(document, "unit")[ref]
    checks = plan.assigned(document)[ref]
    assignment = fixups.active(store, ref)
    integrated_checks = dict(checks)
    if assignment:
        all_checks = plan.select(document, "check")
        integrated_checks.update({g["subject"]: all_checks[g["subject"]] for g in assignment["gaps"]
                                  if g["kind"] == "check" and g["subject"] in all_checks})
    try:
        while (unit := store.unit(ref)).state not in ("DONE", "ASKED", "DROPPED", "WAITING"):
            try:
                if unit.state == "READY":
                    base = store.cursor().candidate
                    root = gitwork.admit(store.root.parent, store.cursor().scratch, base)
                    advance(store, "start", "Isolated worktree admitted", unit=ref,
                            base=base, worktree=root, waiting="")
                elif unit.state == "REWORK":
                    root = unit.worktree or gitwork.admit(
                        store.root.parent, store.cursor().scratch, unit.base)
                    advance(store, "retry", unit.failure, unit=ref, worktree=root,
                            callFolder=None, callAttempt=None)
                elif unit.state == "WORKING":
                    work(store, provider, ref)
                elif unit.state == "PROVING":
                    observations = proof.prove(store, ref, contract, checks)
                    advance(store, "proven", observations, unit=ref)
                elif unit.state == "JUDGING":
                    if ref == "guide":
                        if not gitwork.git(unit.worktree, "show", f"{unit.P}:{plan.GUIDE_PATH}").strip():
                            raise ValueError("Write the try-it guide for the integrated product")
                        result = store.record("guide_written", {"commit": unit.P, "path": plan.GUIDE_PATH,
                            "scopeTree": gitwork.scope_tree(unit.worktree, unit.P, contract["scope"],
                                                            store.cursor().scratch)})
                    else:
                        result, answer = inference.call(store, provider, questions.judge_brief(store, ref))
                        if answer["verdict"] != "not judged" and not questions.satisfied(answer):
                            raise ValueError("Judgment gap: " + str(answer))
                        result = store.record("unit_verdict", {"commit": unit.P, "answer": answer,
                            "source": result, "scopeTree": gitwork.scope_tree(
                                unit.worktree, unit.P, contract["scope"], store.cursor().scratch)})
                    advance(store, "approved", result, unit=ref, judgment=result)
                elif unit.state == "INTEGRATING":
                    integrate(store, ref, integrated_checks)
                else:
                    raise RuntimeError(f"Unscheduled unit state: {unit.state}")
            except ProviderFailure as error:
                thread_from_recording(store, ref)
                if isinstance(error, Deadline) and error.cleanup_verified:
                    recovery.recover(store, ref, str(error))
                else:
                    if error.cleanup_verified:
                        with store.transaction():
                            store.unit(ref, replace(store.unit(ref), callFolder=None, callAttempt=None))
                    raise
            except proof.UncertainCleanup:
                raise
            except (ValueError, RuntimeError, OSError) as error:
                recovery.recover(store, ref, str(error))
    finally:
        store.close()
