"""C12/C13/C14/C18/C23; R7/R8/R9/R11: a whole worker session owns each unit loop."""

import json
import time
from dataclasses import replace
from pathlib import Path

from . import decisions, features, fixups, gitwork, inference, judgment, plan, proof, questions, recovery
from . import unit_questions
from .providers import Deadline, ProviderFailure, ResumeUnavailable, recorded
from .schema import validate
from .states import advance


def affected(store, ref):
    contracts = plan.select(plan.execution(store), "unit")
    refs = {ref}
    while added := {r for r, u in contracts.items() if r not in refs
                    and refs.intersection(u["dependencies"])}:
        refs.update(added)
    return refs


def again(store, subject, source, direction):
    ref = subject["unit"]
    for owner, unit in store.units().items():
        if unit.state == "ASKED" and recovery.lineage(store, owner) == recovery.lineage(store, ref):
            advance(store, "again", source, unit=owner, thread=None,
                    base=unit.base if unit.worktree else store.cursor().candidate,
                    callFolder=None, callAttempt=None,
                    failure=unit.failure + "\nDirection: " + direction)


def contract_gap(store, ref):
    return store.recovery(recovery.lineage(store, ref)).get("contractGap")


def ask_unit(store, provider, ref):
    try:
        row = store.unit(ref)
        tried = [b["failure"] for _, _, b in store.records("unit_failure") if b["unit"] == ref]
        subject = {"unit": ref, "tried": tried, "failure": row.failure,
                   "affects": sorted(affected(store, ref) - {ref}), "recommendation": row.failure}
        attempts = store.recovery(recovery.lineage(store, ref))["attempts"]
        if attempts:
            report = attempts[-1]
            source = store.read(report)
            if source.get("kind") == "WORK" and source["unit"] == ref:
                receipt = source["answer"]
                subject.update(report=report, receipt=receipt, tried=[*tried, row.failure])
                if worker_question := receipt.get("question"):
                    subject["workerQuestion"] = worker_question
        checks = recovery.repeated(store, ref)
        if checks and (missing := judged(store, ref)):
            # Builders could not fix what a judge found missing; the owner chooses the outcome.
            subject["question"] = (f"The builders could not fix what the judge found missing in {ref}, "
                f"even after the approach you chose: {missing} Tried: {len(attempts)} rounds of corrections. "
                "How should we continue?")
        elif checks and features.enabled("checkContract") and check_failure(store, ref):
            subject.update(checkFailure=True, question=(
                f"The builders could not make these checks pass for {ref} after trying the chosen approach: "
                f"{', '.join(checks)}. Expected them to pass. The same checks failed again. "
                "What should we change so the builders can continue?"), recommendation=(
                "Offer a concrete change to the agreement or approach based on the observed failure."))
        elif checks:
            subject["ask"] = {"question": f"This is Seldon's fault: {ref} failed the same checks again "
                f"({', '.join(checks)}) after the approach you chose. Expected them to pass; got the "
                "failure shown. We recommend Pause until Seldon is fixed.",
                "options": [decisions.option("Pause", "Pause here; your work is saved and can resume", "pause"),
                            decisions.option("Try again", "Retry this unit if its cause has been fixed", "direct"),
                            decisions.option("Leave this out", "Leave out this unit and its dependents", "drop")]}
        if "ask" not in subject:
            subject = decisions.coach(store, provider, subject, frame=bool(checks))
        if subject:
            decisions.request(store, "UNIT", subject)
    finally:
        store.close()


def judged(store, ref):
    """What a judge found missing in the unit's latest correction; None for a check or code failure."""
    attempts = store.recovery(recovery.lineage(store, ref))["attempts"]
    body = store.read(attempts[-1]) if attempts else {}
    sources = [store.read(g["source"]) for g in body.get("gaps", []) if g["kind"] != "check"]
    return " ".join(f"{g['target']}: {g['correction']}" for b in sources
                    for g in questions.must_fix(b.get("answer", {"gaps": []}))) or None


def check_failure(store, ref):
    """Repeated proof failures carry check IDs or a check gap, unlike controller faults."""
    attempt = store.recovery(recovery.lineage(store, ref))["attempts"][-1]
    body = store.read(attempt)
    return body.get("checks") or any(g["kind"] == "check" for g in body.get("gaps", []))


def drop(store, ref, approval):
    for owner in sorted(affected(store, ref)):
        if store.unit(owner).state == "WAITING":
            advance(store, "asked", approval, unit=owner)
        if store.unit(owner).state == "ASKED":
            advance(store, "drop", approval, unit=owner, waiting="")


def recovered_report(store, ref):
    unit = store.unit(ref)
    if unit.report:
        return unit.report
    replies = [b for _, _, b in store.records("provider_reply")
               if b["attempt"] == unit.callAttempt and not b["errors"]] if unit.callAttempt else []
    if replies:
        raw = replies[-1]["rawAnswer"]
    elif unit.callFolder:
        folder = Path(unit.callFolder)
        receipt = folder / "result.json"
        result = json.loads(receipt.read_text("utf-8")) if receipt.is_file() else {}
        if not result.get("cleanupVerified") or result.get("timedOut"):
            return None
        raw = recorded(folder).answer((folder / "stdout.bin").read_text("utf-8", errors="replace"))
        question = json.loads((folder / "question.json").read_text("utf-8"))
        validate(json.loads(raw), question["schema"])
    else:
        return None
    return store.record("answer", {"unit": ref, "attempt": unit.callAttempt, "kind": "WORK",
        "answer": json.loads(raw)["answer"], "recovered": True})


def thread_from_recording(store, ref):
    unit = store.unit(ref)
    stream = Path(unit.callFolder) / "stdout.bin" if unit.callFolder else None
    thread = recorded(unit.callFolder).session(stream.read_text("utf-8", errors="replace")) if stream and stream.is_file() else None
    if thread:
        with store.transaction():
            store.unit(ref, replace(store.unit(ref), thread=thread))


def work(store, provider, ref):
    def needs_answer(answer):
        # Old receipts overloaded question with handoffs to a named correction owner.
        return answer["outcome"] == "question" and ("question" in answer or
            not any(d["owner"] != ref for d in answer["discoveries"]))

    def consume(report, answer):
        store.record("writer_end", {"unit": ref, "report": report, "finishedAt": time.time()})
        if needs_answer(answer):
            return
        plan.work_checks(store, ref, report, answer)
        advance(store, "returned", report, unit=ref, report=report)

    report = recovered_report(store, ref)
    if report is None:
        thread_from_recording(store, ref)
        question = unit_questions.work_brief(store, ref)
        store.record("writer_start", {"unit": ref, "session": store.unit(ref).thread,
                                      "startedAt": time.time()})
        try:
            report, answer = inference.call(store, provider, question, unit=ref, consume=consume,
                                           field="outcome/checkPaths/checks/discoveries")
        except ResumeUnavailable as error:
            if not error.cleanup_verified:
                raise
            store.record("resume_unavailable", {"unit": ref, "reason": str(error)})
            with store.transaction():
                row = store.unit(ref)
                store.unit(ref, replace(row, thread=None,
                                        failure=row.failure + "\nResume unavailable: " + str(error)))
            report, answer = inference.call(store, provider, unit_questions.work_brief(store, ref), unit=ref,
                consume=consume, field="outcome/checkPaths/checks/discoveries")
    else:
        report, answer = inference.call(store, provider, unit_questions.work_brief(store, ref), unit=ref,
            consume=consume, field="outcome/checkPaths/checks/discoveries", retained=report)
    if needs_answer(answer):
        recovery.recover(store, ref, answer.get("question", answer["summary"]), cause=report, direct=True)


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
                raise proof.Failed("Integrated checks failed: ", bad)
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
    assignment = fixups.active(store, ref)
    try:
        while (unit := store.unit(ref)).state not in ("DONE", "ASKED", "DROPPED", "WAITING"):
            try:
                document = plan.execution(store)
                contract = plan.select(document, "unit")[ref]
                checks = plan.assigned(document, local=True)[ref]
                integrated_checks = dict(checks)
                if assignment and not contract["handed"]:
                    all_checks = plan.select(document, "check")
                    integrated_checks.update({g["subject"]: all_checks[g["subject"]]
                        for g in assignment["gaps"]
                        if g["kind"] == "check" and g["subject"] in all_checks})
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
                        if (not gitwork.blobs(unit.worktree, unit.P, [questions.GUIDE_PATH]).get(questions.GUIDE_PATH, b"").strip()
                                or not plan.assigned(document).get("guide")):
                            raise ValueError("Write docs/try-it.md and declare its walkthrough check")
                        result = store.record("guide_written", {"commit": unit.P, "path": questions.GUIDE_PATH,
                            "scopeTree": gitwork.scope_tree(unit.worktree, unit.P, contract["scope"],
                                                            store.cursor().scratch)})
                    elif (judgment.folded_owner(store, document) == ref
                          or plan.wave_requirements(document, ref) or contract["handed"]):
                        advance(store, "approved", unit.P, unit=ref, judgment=None)
                        continue
                    else:
                        tree = gitwork.scope_tree(unit.worktree, unit.P, contract["scope"], store.cursor().scratch)
                        def consume(result, answer):
                            if not questions.satisfied(answer):
                                prior = [b for _, _, b in store.records("unit_gap")
                                         if b["unit"] == ref and b["base"] == unit.base]
                                gap = store.record("unit_gap", {"unit": ref, "base": unit.base,
                                    "commit": unit.P, "answer": answer, "source": result})
                                if not prior:
                                    advance(store, "recover", gap, unit=ref, failure=str(answer),
                                            report=None, T=None, P=None, observations=[], judgment=None)
                                    return
                                store.record("open_gap", {"unit": ref, "commit": unit.P,
                                    "label": "open gap", "answer": answer, "source": result})
                            result = store.record("unit_verdict", {"commit": unit.P, "answer": answer,
                                "source": result, "scopeTree": tree})
                            advance(store, "approved", result, unit=ref, judgment=result)
                        inference.call(store, provider, unit_questions.judge_brief(store, ref),
                                       consume=consume, field="verdict/gaps")
                        continue
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
            except Exception as error:  # a code fault recovers or asks; it never ends the run (R15)
                recovery.recover(store, ref, str(error), checks=getattr(error, "checks", None))
    finally:
        store.close()


def judge_wave(store, provider, observations):
    """Judge deferred source with its integrated proof; route concrete gaps to its owner."""
    cursor, document = store.cursor(), plan.execution(store)
    if owner := judgment.folded_owner(store, document):
        tree = judgment.folded_evidence(store, document, owner, observations)
        question = judgment.folded_brief(store, document, owner, observations)

        gaps = []

        def consume(ref, source, answer):
            result, combined = judgment.record_folded(
                store, ref, source, answer, tree, observations, question)
            gaps.extend(judgment.corrections(store, ref, result, combined))

        unit_questions.batch(store, provider, {owner: question}, consume=consume, field="findings")
        return gaps
    batch, trees = {}, {}
    for ref, contract in plan.select(document, "unit").items():
        # The guide has no source verdict: PRODUCT compares its walkthrough with the product.
        # A unit judged alone is judged again here once a contract change cleared its verdict.
        row = store.unit(ref)
        if row.state == "DROPPED" or ref == "guide":
            continue
        previous = store.read(row.judgment) if row.judgment else None
        reusable = previous and "findings" not in previous
        if reusable and not (plan.wave_requirements(document, ref) or contract["handed"]):
            continue
        tree = gitwork.scope_tree(store.root.parent, cursor.candidate, contract["scope"], cursor.scratch)
        if reusable and previous["scopeTree"] == tree and previous["answer"]["verdict"] != "gap":
            continue
        trees[ref] = tree
        refs = [r for r in observations if store.read(r)["unit"] == ref]
        batch[ref] = unit_questions.judge_brief(store, ref, cursor.candidate, refs)
    gaps = []
    def consume(ref, source, answer):
        if answer["verdict"] == "uncertain":
            answer = {**answer, "verdict": "not judged", "gaps": []}
        result = store.record("unit_verdict", {"commit": cursor.candidate, "answer": answer,
            "source": source, "scopeTree": trees[ref],
            "observations": [r for r in observations if store.read(r)["unit"] == ref]})
        with store.transaction():
            store.unit(ref, replace(store.unit(ref), judgment=result))
        if not questions.satisfied(answer) and answer["verdict"] != "not judged":
            # A disagreement with an agreement is the planner's to correct, never one worker's (R4).
            agreements = [o for g in questions.must_fix(answer) for o in g["owners"] if o != ref]
            contracted = bool(agreements) and features.enabled("contractGap")
            gaps.append({"subject": ref, "kind": "contract" if contracted else "flow",
                         "commit": cursor.candidate, "units": list(dict.fromkeys(agreements)) if contracted
                         else [ref], "source": result, "correction": "; ".join(
                             f"{g['target']}: {g['correction']}" for g in questions.must_fix(answer))
                         + " Judge: " + answer["why"] if contracted else str(answer)})
    unit_questions.batch(store, provider, batch, consume=consume, field="verdict/gaps")
    return gaps
