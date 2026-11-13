"""C1/C4/C18/I10; R8/R12/R13: recorded strict calls and two bounded transport recoveries."""

import time
from dataclasses import replace

from . import questions
from .providers import ProviderFailure, role_for
from .store import encode


def call(store, provider, question, unit=None, check=lambda answer: None, max_bytes=None, retries=2):
    setup = store.read(store.cursor().kickoff)["setup"]
    kind = question["kind"]
    role = role_for(kind, {})
    attempt = None

    def invoke(current):
        nonlocal attempt
        if max_bytes is not None and len(encode(current).encode("utf-8")) > max_bytes:
            raise questions.MissingFacts("FACTS corrections exceed twice the original subject brief")
        if unit:
            current = {**current, "execution": {**current["execution"], "session": store.unit(unit).thread}}
        previous = None
        for recovery in range(3):
            timeout = provider.timeout_for(kind, {}) if hasattr(provider, "timeout_for") else 30
            with store.transaction():
                qref = store.record("question", current)
                attempt = store.record("attempt", {"operation": kind, "question": qref,
                    "unit": unit, "provider": setup.get("providers", {}).get(role, {}),
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
                # Worker transport recovery is local to its unit, sharing its C18 recovery allowance.
                if unit or not error.transient or not error.cleanup_verified or recovery == 2:
                    raise
                previous = attempt

    def recorded(body):
        return store.record("provider_reply", {"attempt": attempt, "finishedAt": time.time(), **body})

    def validate(answer):
        if kind in ("JUDGE", "DEMO", "PRODUCT"):
            if sorted(a["target"] for a in answer["assessments"]) != sorted(question["targets"]):
                raise ValueError("Assess the exact requested subjects, each once")
        if (kind != "FLOW" and answer.get("verdict") == "satisfied"
                and not questions.satisfied(answer, final=kind != "JUDGE")):
            raise ValueError("A negative assessment requires a gap verdict and correction: " + str(answer))
        check(answer)

    answer = questions.ask(invoke, question, recorded, validate, retries=retries)
    with store.transaction():
        result = store.record("answer", {"attempt": attempt, "kind": kind, "unit": unit, "answer": answer})
        store.record("attempt_end", {"attempt": attempt, "result": result, "finishedAt": time.time()})
    if answer.get("verdict") == "uncertain" and kind == "JUDGE":
        if "Requested facts" not in question["facts"]:
            limit = 2 * len(encode(question).encode("utf-8"))
            reask = {**question, "facts": {**question["facts"], "Requested facts": answer["neededFacts"]}}
            try:
                return call(store, provider, reask, unit, check, limit)
            except (questions.MissingFacts, questions.UnitTooLarge) as error:
                answer = {**answer, "why": answer["why"] + "; " + str(error)}
        answer = {**answer, "verdict": "not judged", "assessments": [], "gaps": []}
    elif answer.get("verdict") == "uncertain" and kind not in ("REVIEW", "FLOW", "PRODUCT"):
        raise questions.MissingFacts("Unresolved facts: " + "; ".join(answer["neededFacts"]))
    return result, answer
