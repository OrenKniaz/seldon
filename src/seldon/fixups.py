"""C18/C23; R7/R8/R9: recorded gaps reopen only their approved owning units."""

from . import gitwork
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
                or gap["kind"] not in ("flow", "check", "blocked") or gap["commit"] != cursor.candidate
                or not isinstance(gap["correction"], str) or not gap["correction"].strip()
                or not isinstance(gap["units"], list) or not gap["units"]):
            raise ValueError("Fixup requires a concrete current-candidate Gap and its owning units")
        store.read(gap["source"])
        if gap["kind"] == "check" and gap["subject"] in checks:
            gap = {**gap, "units": [planning.owner(document, gap["subject"])]}
        # Guide walkthrough/format checks are produced at verification, outside the planned check nodes.
        for ref in dict.fromkeys(gap["units"]):
            if ref not in owners or store.unit(ref).state != "DONE":
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


def handoff(store, ref, report, answer):
    """A blocked worker waits for a named DONE owner; repeated handoffs spend recovery."""
    if answer["outcome"] != "blocked" or not answer["discoveries"]:
        return False
    # Serialize candidate selection with integration, and owner admission with other handoffs.
    with store.integration_lock, store.transaction():
        cursor = store.cursor()
        document = store.plan(cursor.revision)
        owners = planning.select(document, "unit")
        repeated = any(b["unit"] == ref and b["disposition"] == "owner fixup"
                       for _, _, b in store.records("discovery"))
        row = store.unit(ref)
        if repeated and row.recoveries >= 2:
            return False
        gaps = []
        for discovery in answer["discoveries"]:
            owner = discovery["owner"]
            if owner not in owners or owner == ref:
                return False
            named = store.unit(owner)
            if (named.state != "DONE" or not named.integrated
                    or not gitwork.contains(store.root.parent, named.integrated, row.base)):
                raise ValueError(f"{owner}'s files are not in this unit's base by design; "
                                 "write the wave check, run unit checks, return done")
            gaps.append({"subject": owner, "kind": "blocked", "commit": cursor.candidate,
                         "units": [owner], "correction": discovery["correction"], "source": report})
        if repeated:
            store.record("unit_failure", {"unit": ref, "state": row.state, "failure": str(answer),
                         "recovery": row.recoveries + 1, "exhausted": False})
        for discovery in answer["discoveries"]:
            store.record("discovery", {"unit": ref, "report": report,
                                      "disposition": "owner fixup", **discovery})
        advance(store, "wait-owner", report, unit=ref,
                waitingFor=list(dict.fromkeys(r for g in gaps for r in g["units"])),
                report=None, callFolder=None, callAttempt=None, worktree=None,
                T=None, P=None, observations=[], judgment=None,
                recoveries=row.recoveries + int(repeated))
        apply(store, gaps)
    return True


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
            failure = (f"Fixup inside {ref}'s write scope {assignment['scope']} only:\n"
                       + "\n".join(g["correction"] for g in assignment["gaps"]))
            # A new life preserves the session, but not the previous life's recovery count or receipts.
            advance(store, "recover", cause, unit=ref, base=assignment["base"], worktree=None,
                    callFolder=None, callAttempt=None, recoveries=0, failure=failure, report=None,
                    T=None, P=None, observations=[], judgment=None)
        cursor = store.cursor()
        if cursor.state != "EXECUTE":
            event = "rework" if cursor.state == "WAVE-VERIFY" else "gap"
            advance(store, event, refs, pendingJudge=None, finalCommit=None, finalObservations=[],
                    productVerdict=None, checkin=None, decision=None)
    return refs
