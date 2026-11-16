"""C1/C4/C18/I10; R8/R12/R13: recorded strict calls and two bounded transport recoveries."""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from . import questions
from .providers import Deadline, ProviderFailure, ResumeUnavailable, role_for


def call(store, provider, question, unit=None, check=lambda answer: None, retries=2, binding=None):
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

    answer = questions.ask(invoke, question, recorded, check, retries=retries)
    with store.transaction():
        result = store.record("answer", {"attempt": attempt, "kind": kind, "unit": unit,
                                         "answer": answer, "binding": binding})
        store.record("attempt_end", {"attempt": attempt, "result": result, "finishedAt": time.time()})
    if answer.get("verdict") == "uncertain" and kind == "JUDGE":
        answer = {**answer, "verdict": "not judged", "gaps": []}
    return result, answer


def batch(store, provider, pending, check=lambda answer, question: None):
    """Record independent replies on one candidate; only the controller applies their outcomes."""
    cursor = store.cursor()
    bindings = {s: [cursor.candidate, cursor.revision, q["kind"], s] for s, q in pending.items()}
    recorded = {tuple(b["binding"]): (r, b["answer"]) for r, _, b in store.records("answer")
                if b.get("binding")}

    def ask(subject):
        try:
            question = pending[subject]
            return recorded.get(tuple(bindings[subject])) or call(store, provider, question,
                check=lambda a: check(a, question), binding=bindings[subject])
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=store.read(cursor.kickoff)["capacity"]) as pool:
        replies = dict(zip(pending, pool.map(ask, pending), strict=True))
    latest = store.cursor()
    if (latest.candidate, latest.revision) != (cursor.candidate, cursor.revision):
        raise ValueError("Final replies require their original candidate and plan")
    return replies
