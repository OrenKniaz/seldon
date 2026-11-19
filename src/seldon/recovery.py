"""C18/C23; R15: unresolved accepted work has one durable recovery and question history."""

from dataclasses import replace

from . import decisions
from .states import advance


def lineage(store, unit=None):
    row = store.unit(unit) if unit else store.cursor()
    return row.lineage or ("unit:" + unit if unit else "planning:" + str(row.vision))


def failed(store, cause, unit=None, *, key=None, shape=None, direct=False):
    key = key or lineage(store, unit)
    state = store.recovery(key)
    if cause in state["attempts"]:
        state["waiting"] = direct or state.get("waiting", False)
        store.recovery(key, state)
        return state["waiting"]
    repeated = shape is not None and shape in state["seen"]
    state["rounds"] += 1
    state["stage"] = max(state["stage"], state["rounds"] // 2)
    state["attempts"].append(cause)
    if shape is not None:
        state["seen"].append(shape)
    asked = direct or (repeated if shape is not None else bool(state["answer"]) or state["stage"] >= 1)
    state["waiting"] = asked
    store.recovery(key, state)
    row = store.unit(unit) if unit else store.cursor()
    if unit:
        store.unit(unit, replace(row, lineage=key, recoveries=state["rounds"]))
    else:
        store.cursor(replace(row, lineage=key))
    return asked


def recover(store, ref, failure, *, key=None, cause=None, direct=False, charged=False, checks=None,
            **updates):
    with store.transaction():
        row = store.unit(ref)
        cause = cause or store.record("unit_failure", {
            "unit": ref, "state": row.state, "failure": failure,
            "evidence": row.observations, "attempt": row.callAttempt, "checks": checks})
        key = key or lineage(store, ref)
        asked = store.recovery(key).get("waiting", False) if charged else failed(
            store, cause, ref, key=key, direct=direct)
        advance(store, "asked" if asked else "recover", cause, unit=ref, failure=failure,
                lineage=key, recoveries=store.recovery(key)["rounds"],
                report=None, T=None, P=None, observations=[], judgment=None, **updates)
        return asked


def repeated(store, unit):
    """The checks that failed again after the user chose an approach; None for a new failure."""
    state = store.recovery(lineage(store, unit))
    if not state["answer"] or not state["attempts"]:
        return None

    def failed_checks(ref):
        body = store.read(ref)
        return body.get("checks") or sorted(g["subject"] for g in body.get("gaps", [])) or None

    answered = int(state["answer"][1:])
    last = failed_checks(state["attempts"][-1])
    earlier = [failed_checks(r) for r in state["attempts"][:-1] if int(r[1:]) < answered]
    return last if last and last in earlier else None


def planning(store, cause, shape, event=None, outline=True, **updates):
    with store.transaction():
        cursor = store.cursor()
        shape = {"subject": shape, "outline": store.plan(cursor.revision).get("architecture") if outline
                 else None, "answer": store.recovery(lineage(store))["answer"]}
        if not failed(store, cause, shape=shape):
            if event:
                advance(store, event, cause, **updates)
            return
        body = store.read(cause)
        question = body.get("question")
        decisions.request(store, store.cursor().state, {"step": store.cursor().state,
            **({"question": question} if question else {}),
            "tried": [body.get("gap") or str(body.get("corrections", cause))],
            "recommendation": "Change the approach to the unresolved plan"})
    raise decisions.Waiting()


def subject(store, gate, body):
    key = lineage(store, body.get("unit"))
    state = store.recovery(key)
    if state["answer"]:
        body = {**body, "recurrence": "This is the same unresolved issue after your previous answer",
                "previousAnswer": state["answer"]}
    return key, {**body, "lineage": key, "attempts": state["attempts"], "routingStage": state["stage"]}


def answered(store, subject, answer):
    if key := subject.get("lineage"):
        state = store.recovery(key)
        state.update(answer=answer, question=None, waiting=False, coached=False)
        store.recovery(key, state)
