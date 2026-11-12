"""C18/C23; R7/R8/R9: recorded gaps reopen only their approved owning units."""

from dataclasses import replace

from . import plan as planning
from .states import advance


def plan(store, gaps):
    """Assign corrections by the producer's owners, without another semantic question."""
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    owners = planning.select(document, "unit")
    checks = planning.select(document, "check")
    assignments = {}
    for gap in gaps:
        if (set(gap) != {"subject", "kind", "commit", "units", "correction", "source"}
                or gap["kind"] not in ("flow", "check") or gap["commit"] != cursor.candidate
                or not isinstance(gap["correction"], str) or not gap["correction"].strip()
                or not isinstance(gap["units"], list) or not gap["units"]):
            raise ValueError("Fixup requires a concrete current-candidate Gap and its owning units")
        store.read(gap["source"])
        eligible = (planning.traced_units(document, {gap["subject"]: checks[gap["subject"]]})
                    if gap["kind"] == "check" and gap["subject"] in checks else owners)
        # Guide walkthrough/format checks are produced at verification, outside the planned check nodes.
        for ref in dict.fromkeys(gap["units"]):
            if ref not in eligible or ref not in owners or store.unit(ref).state != "DONE":
                raise ValueError(f"{ref}: a Gap must name its DONE approved owner")
            assignment = assignments.setdefault(ref, {
                "owner": ref, "scope": owners[ref]["scope"], "base": cursor.candidate,
                "revision": cursor.revision, "approval": cursor.B, "gaps": []})
            assignment["gaps"].append(gap)
    return assignments


def active(store, ref):
    """The latest immutable assignment belongs to this unit's current repair base."""
    row = store.unit(ref)
    for _, _, body in reversed(store.records("unit_fixup")):
        if body["owner"] == ref:
            if body["base"] != row.base:
                return None
            if (body["revision"] != store.cursor().revision or body["approval"] != store.cursor().B):
                raise ValueError("Fixup requires its approved plan and owner")
            return body
    return None


def checks_for(store, document, ref):
    """Preserve existing behaviour; re-prove failed local checks, leaving cross-unit proof to the wave."""
    local = planning.assigned(document)[ref]
    fixup = active(store, ref)
    if fixup is None:
        return local
    failed = {g["subject"] for g in fixup["gaps"] if g["kind"] == "check"}
    flow = any(g["kind"] == "flow" for g in fixup["gaps"])
    return {r: dict(c, mode="establishes" if r in failed or flow and c["mode"] == "establishes"
                   else "preserves")
            for r, c in local.items()}


def apply(store, gaps):
    """Atomically admit local rework; filesystem preparation happens in the resumable unit loop."""
    from . import units

    assignments = plan(store, gaps)
    if not assignments:
        return []
    with store.transaction():
        refs = []
        for ref, assignment in assignments.items():
            cause = store.record("unit_fixup", assignment)
            refs.append(cause)
            row = store.unit(ref)
            if row.state == "BLOCKED":
                continue
            # Preserve the worker session; a new isolated checkout starts at the current candidate.
            store.unit(ref, replace(row, base=assignment["base"], worktree=None,
                                    callFolder=None, callAttempt=None))
            failure = (f"Fixup inside {ref}'s write scope {assignment['scope']} only:\n"
                       + "\n".join(g["correction"] for g in assignment["gaps"]))
            units.recover(store, ref, failure)
        cursor = store.cursor()
        event = "rework" if cursor.state == "WAVE-VERIFY" else "gap"
        advance(store, event, refs, pendingJudge=None, finalCommit=None, finalObservations=[],
                productVerdict=None, checkin=None, decision=None)
    return refs
