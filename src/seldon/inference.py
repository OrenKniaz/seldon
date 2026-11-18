"""C1/C4/C18/I10; R8/R12/R13: recorded strict calls and two bounded transport recoveries."""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from . import architecture, features, questions
from .answers import ConsumptionFailure, Mismatch
from .providers import Deadline, ProviderFailure, ResumeUnavailable, role_for


def call(store, provider, question, unit=None, check=lambda answer: None, retries=2, binding=None,
         consume=lambda source, answer: None, field="answer", retained=None):
    setup = store.read(store.cursor().kickoff)["setup"]
    kind = question["kind"]
    attempt = None

    def invoke(current):
        nonlocal attempt
        if unit:
            current = {**current, "execution": {**current["execution"], "session": store.unit(unit).thread}}
        previous = None
        for recovery in range(3):
            timeout = provider.timeout_for(kind, {}) if hasattr(provider, "timeout_for") else 30
            with store.transaction():
                qref = store.record("question", current)
                attempt = store.record("attempt", {"operation": kind, "question": qref,
                    "unit": unit, "provider": setup.get("providers", {}).get(role_for(kind, {}), {}),
                    "deadline": time.time() + timeout, "retryOf": previous, "startedAt": time.time()})
                if unit:
                    store.unit(unit, replace(store.unit(unit), callAttempt=attempt))

            def started(folder):
                if unit:
                    with store.transaction():
                        store.unit(unit, replace(store.unit(unit), callFolder=str(folder)))
                store.record("call_started", {"attempt": attempt, "folder": str(folder)})

            try:
                if hasattr(provider, "invoke"):
                    raw, thread = provider.invoke(current, started)
                    if unit and thread:
                        with store.transaction():
                            store.unit(unit, replace(store.unit(unit), thread=thread))
                    return raw
                return provider(current)
            except ProviderFailure as error:
                store.record("provider_failure", {"attempt": attempt, "reason": str(error),
                    "transient": error.transient, "cleanupVerified": error.cleanup_verified,
                    "receipt": error.receipt})
                if (isinstance(error, ResumeUnavailable) or not error.transient
                        or not error.cleanup_verified or unit and isinstance(error, Deadline)):
                    raise
                if recovery == 2:
                    raise RuntimeError(f"{error}; receipt: {error.receipt}") from error
                previous = attempt

    def recorded(body):
        return store.record("provider_reply", {"attempt": attempt, "finishedAt": time.time(), **body})

    original, correction = question, None
    while True:
        if retained:
            body = store.read(retained)
            attempt, answer = body.get("attempt"), body["answer"]
        else:
            answer = questions.ask(invoke, question, recorded, retries=retries)
        try:
            # Only answer-to-record work belongs here; provider calls and execution stay outside.
            # Roll back partial records so an exact correction never applies an answer twice.
            with store.transaction():
                if kind == "ARCHITECT" and original["facts"].get("Previous architecture") and "scope" not in answer:
                    facts = original["facts"]
                    previous = facts["Previous architecture"]
                    finished = len(previous["waves"]) - len(facts.get("Remaining waves", previous["waves"]))
                    answer = {**architecture.merge(previous, answer, completed=previous["waves"][:finished]),
                              "why": answer["why"]}
                    current = store.cursor().wave
                    if len(answer["waves"]) < current:
                        # Steering after closed waves needs a next wave. Normalise when the answer
                        # carries commitments no wave holds; otherwise ask once in plain words.
                        planned = {c for w in answer["waves"] for c in w["commitments"]}
                        open_ = sorted({c for a in answer["areas"] for c in a["commitments"]} - planned)
                        if open_:
                            answer["waves"].append({"goal": "Deliver the requested change", "commitments": open_})
                        else:
                            raise Mismatch("waves", f"Seldon has closed {current - 1} wave(s) and is planning "
                                f"wave {current}, but your waves list has {len(answer['waves'])}. Add the "
                                "next wave: its goal and the commitments it delivers for the requested change.")
                if answer.get("verdict") != "uncertain":
                    if answer.get("verdict") == "gap" and not answer["gaps"]:
                        raise Mismatch("gaps", "Seldon expected the corrections behind a gap verdict; "
                            "got an empty gaps list. Send the gaps, or a satisfied verdict.")
                    if "gaps" in answer:
                        answer = {**answer, "verdict": ("gap" if questions.must_fix(answer) else
                                  "adequate" if kind == "REVIEW" else "satisfied")}
                    check(answer)
                if answer.get("verdict") == "uncertain" and kind == "JUDGE":
                    answer = {**answer, "verdict": "not judged", "gaps": []}
                result = retained or store.record("answer", {"attempt": attempt, "kind": kind, "unit": unit,
                    "answer": answer, "binding": binding})
                consume(result, answer)
                if not retained:
                    store.record("attempt_end", {"attempt": attempt, "result": result, "finishedAt": time.time()})
            return result, answer
        except architecture.ContractGap:
            raise  # A missing agreement is a planner decision, not an unusable answer.
        except Exception as error:
            failed_field = error.field if isinstance(error, Mismatch) else field
            reason = (error.reason if isinstance(error, Mismatch) else
                f"Seldon could not use {kind}.{failed_field}: "
                + (str(error) if isinstance(error, ValueError) else
                   f"unexpected {type(error).__name__} while turning this field into records. "
                   "Send this field using the supplied handles and facts; if it repeats, pause for a Seldon fix."))
            store.record("consumption_failure", {"attempt": attempt, "kind": kind,
                "field": failed_field, "reason": reason, "exceptionType": type(error).__name__,
                "answer": answer})
            if correction is not None or not features.enabled("answerRepair"):
                raise ConsumptionFailure(f"Seldon's fault using {kind}.{failed_field}: {reason}") from error
            correction = {"Rejected answer": answer, "Errors": [reason]}
            retained = None
            question = {**original, "facts": {**original["facts"], "Exact corrections": [correction]}}


def batch(store, provider, pending, check=lambda answer, question: None,
          consume=lambda subject, source, answer: None, field="answer"):
    """Record independent replies on one candidate; only the controller applies their outcomes."""
    cursor = store.cursor()
    bindings = {s: [cursor.candidate, cursor.revision, q["kind"], s] for s, q in pending.items()}
    recorded = {tuple(b["binding"]): (r, b["answer"]) for r, _, b in store.records("answer")
                if b.get("binding")}

    def ask(subject):
        try:
            question = pending[subject]
            retained = recorded.get(tuple(bindings[subject]))
            return call(store, provider, question, check=lambda a: check(a, question),
                binding=bindings[subject], consume=lambda r, a: consume(subject, r, a), field=field,
                retained=retained[0] if retained else None)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=store.read(cursor.kickoff)["capacity"]) as pool:
        replies = dict(zip(pending, pool.map(ask, pending), strict=True))
    latest = store.cursor()
    if (latest.candidate, latest.revision) != (cursor.candidate, cursor.revision):
        raise ValueError("Final replies require their original candidate and plan")
    return replies
