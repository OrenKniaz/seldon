"""C18/C23; R7/R8/R9: recorded gaps reopen only their approved owning units."""

from . import gitwork, recovery
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
            if (not planning.contract_descendant(store, body["revision"])
                    or body["approval"] != store.cursor().B):
                raise ValueError("Fixup requires its approved plan and owner")
            return body
    return None


def handoff(store, ref, report, answer, *, key=None, charged=False):
    """A blocked worker waits for a named DONE owner; repeated handoffs spend recovery."""
    if answer["outcome"] != "blocked" or not answer["discoveries"]:
        return False
    # Serialize candidate selection with integration, and owner admission with other handoffs.
    with store.integration_lock, store.transaction():
        cursor = store.cursor()
        document = store.plan(cursor.revision)
        owners = planning.select(document, "unit")
        contracts = planning.select(document, "contract")
        contract_change = all(d["owner"] in contracts for d in answer["discoveries"])
        row = store.unit(ref)
        key = key or recovery.lineage(store, ref)
        gaps = []
        for discovery in answer["discoveries"]:
            owner = discovery["owner"]
            if contract_change:
                owner = contracts[owner]["provider"]
                if store.unit(owner).state != "DONE":
                    continue
            if owner not in owners or owner == ref:
                return False
            named = store.unit(owner)
            if (named.state != "DONE" or not named.integrated
                    or not contract_change and not gitwork.contains(
                        store.root.parent, named.integrated, row.base)):
                raise ValueError(f"{owner}'s files are not in this unit's base by design; "
                                 "write the wave check, run unit checks, return done")
            gaps.append({"subject": owner, "kind": "blocked", "commit": cursor.candidate,
                         "units": [owner], "correction": discovery["correction"], "source": report})
        if not charged:
            recovery.failed(store, report, ref, key=key)
        for discovery in answer["discoveries"]:
            store.record("discovery", {"unit": ref, "report": report,
                                      "disposition": "owner fixup", **discovery})
        if not gaps:
            advance(store, "reopen", report, unit=ref, report=None, callFolder=None, callAttempt=None)
            return True
        advance(store, "wait-owner", report, unit=ref,
                waitingFor=list(dict.fromkeys(r for g in gaps for r in g["units"])),
                report=None, callFolder=None, callAttempt=None, worktree=None,
                T=None, P=None, observations=[], judgment=None,
                lineage=key)
        apply(store, gaps, key=key, charged=True)
    return True


def apply(store, gaps, *, key=None, charged=False):
    """Atomically admit local rework; filesystem preparation happens in the resumable unit loop."""
    assignments = plan(store, gaps)
    if not assignments:
        return []
    with store.transaction():
        refs, seen = [], set()
        for ref, assignment in assignments.items():
            cause = store.record("unit_fixup", assignment)
            refs.append(cause)
            failure = "\n".join(g["correction"] for g in assignment["gaps"])
            gap = assignment["gaps"][0]
            acceptance = "check" if gap["kind"] == "check" else store.cursor().state.split("-")[0]
            origin = key or "gap:" + acceptance + ":" + gap["subject"]
            recovery.recover(store, ref, failure, key=origin, cause=cause, charged=charged or origin in seen,
                             base=assignment["base"], worktree=None, callFolder=None, callAttempt=None)
            seen.add(origin)
        cursor = store.cursor()
        if cursor.state != "EXECUTE":
            event = "rework" if cursor.state == "WAVE-VERIFY" else "gap"
            advance(store, event, refs, pendingJudge=None, finalCommit=None, finalObservations=[],
                    productVerdict=None, checkin=None)
    return refs
