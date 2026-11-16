"""C18/C23; R7/R8/R9: recorded gaps reopen only their approved owning units."""

from . import plan as planning
from . import recovery
from .states import advance


def plan(store, gaps):
    """Assign corrections by the producer's owners, without another semantic question."""
    cursor = store.cursor()
    document = planning.execution(store)
    owners = planning.select(document, "unit")
    assignments = {}
    for gap in gaps:
        if (set(gap) != {"subject", "kind", "commit", "units", "correction", "source"}
                or gap["kind"] not in ("flow", "check") or gap["commit"] != cursor.candidate
                or not isinstance(gap["correction"], str) or not gap["correction"].strip()
                or not isinstance(gap["units"], list) or not gap["units"]):
            raise ValueError("Fixup requires a concrete current-candidate Gap and its owning units")
        store.read(gap["source"])
        for ref in dict.fromkeys(gap["units"]):
            if ref not in owners or store.unit(ref).state != "DONE":
                raise ValueError(f"{ref}: a Gap must name its DONE approved owner")
            history = (active(store, ref) or {}).get("observations", [])
            assignment = assignments.setdefault(ref, {
                "owner": ref, "scope": owners[ref]["scope"], "base": cursor.candidate,
                "revision": cursor.revision, "approval": cursor.B,
                "observations": list(dict.fromkeys(history + store.unit(ref).observations)), "gaps": []})
            assignment["gaps"].append(gap)
    return assignments


def active(store, ref):
    """The latest immutable assignment belongs to this unit's current repair base."""
    row = store.unit(ref)
    for _, _, body in reversed(store.records("unit_fixup")):
        if body["owner"] == ref:
            if body["base"] != row.base:
                return None
            if (body["revision"] != store.cursor().revision
                    or body["approval"] != store.cursor().B):
                raise ValueError("Fixup requires its approved plan and owner")
            return body
    return None


def apply(store, gaps):
    """Atomically admit local rework; filesystem preparation happens in the resumable unit loop."""
    assignments = plan(store, gaps)
    if not assignments:
        return []
    with store.transaction():
        refs = []
        for ref, assignment in assignments.items():
            cause = store.record("unit_fixup", assignment)
            refs.append(cause)
            failure = "\n".join(g["correction"] for g in assignment["gaps"])
            gap = assignment["gaps"][0]
            acceptance = "check" if gap["kind"] == "check" else store.cursor().state.split("-")[0]
            origin = "gap:" + acceptance + ":" + gap["subject"] + ":" + ref
            recovery.recover(store, ref, failure, key=origin, cause=cause,
                             base=assignment["base"], worktree=None, callFolder=None, callAttempt=None)
        cursor = store.cursor()
        if cursor.state != "EXECUTE":
            event = "rework" if cursor.state == "WAVE-VERIFY" else "gap"
            advance(store, event, refs, finalCommit=None, finalObservations=[],
                    productVerdict=None, checkin=None)
    return refs
