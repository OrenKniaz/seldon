"""C18/C23; R15: unresolved accepted work has one durable recovery and question history."""

from dataclasses import replace

from . import decisions
from .states import advance


def lineage(store, unit=None):
    row = store.unit(unit) if unit else store.cursor()
    return row.lineage or ("unit:" + unit if unit else "planning:" + str(row.vision))


def failed(store, cause, unit=None, *, key=None, shape=None, direct=False):
    """Charge a failed round once; structural changes route twice, exact fixed points ask now."""
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
    asked = direct or repeated or bool(state["answer"]) or state["stage"] >= (2 if shape is not None else 1)
    state["waiting"] = asked
    store.recovery(key, state)
    row = store.unit(unit) if unit else store.cursor()
    if unit:
        store.unit(unit, replace(row, lineage=key, recoveries=state["rounds"]))
    else:
        store.cursor(replace(row, lineage=key))
    return asked


def recover(store, ref, failure, *, key=None, cause=None, direct=False, charged=False, **updates):
    with store.transaction():
        row = store.unit(ref)
        cause = cause or store.record("unit_failure", {
            "unit": ref, "state": row.state, "failure": failure,
            "evidence": row.observations, "attempt": row.callAttempt})
        key = key or lineage(store, ref)
        asked = store.recovery(key).get("waiting", False) if charged else failed(
            store, cause, ref, key=key, direct=direct)
        advance(store, "asked" if asked else "recover", cause, unit=ref, failure=failure,
                lineage=key, recoveries=store.recovery(key)["rounds"],
                report=None, T=None, P=None, observations=[], judgment=None, **updates)
        if asked:
            decisions.ask_unit(store, "Change the approach: " + failure)
        return asked


def planning(store, cause, shape, event=None, **updates):
    with store.transaction():
        if failed(store, cause, shape=shape):
            body = store.read(cause)
            decisions.request(store, store.cursor().state, {"step": store.cursor().state,
                "tried": [body.get("gap") or str(body.get("corrections", cause))],
                "recommendation": "Change the approach to the unresolved plan",
                "options": ["Try again", "Give direction", "Stop"]})
            waiting = True
        else:
            waiting = False
            if event:
                advance(store, event, cause, **updates)
    if waiting:
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
        state.update(answer=answer, question=None, waiting=False)
        store.recovery(key, state)
