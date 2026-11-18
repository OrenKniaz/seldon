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
            if body["approval"] != store.cursor().B:
                raise ValueError("Fixup requires its approved plan and owner")
            return body
    return None


def apply(store, gaps, *, agreement=False):
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
            updates = dict(base=assignment["base"], wave=store.cursor().wave,
                           worktree=None, callFolder=None, callAttempt=None)
            if agreement:
                # The correction is new work from the planner, not another failed worker attempt.
                advance(store, "recover", cause, unit=ref, failure=failure, thread=None,
                        report=None, T=None, P=None, observations=[], judgment=None, **updates)
            else:
                gap = assignment["gaps"][0]
                state = store.cursor().state
                acceptance = "check" if gap["kind"] == "check" or state == "WAVE-VERIFY" else state.split("-")[0]
                origin = "gap:" + acceptance + ":" + gap["subject"] + ":" + ref
                recovery.recover(store, ref, failure, key=origin, cause=cause, **updates)
        cursor = store.cursor()
        if cursor.state != "EXECUTE":
            event = "rework" if cursor.state == "WAVE-VERIFY" else "gap"
            advance(store, event, refs, finalCommit=None, finalObservations=[],
                    productVerdict=None, checkin=None)
    return refs


def replan(store, gaps):
    """A fix no unit of this wave can hold alone is a plan gap: the planner gives it an owner,
    or corrects the agreement two sides disagree on (R4)."""
    cursor = store.cursor()
    units = store.units()

    def explain(g):
        if g["kind"] != "contract":
            return (f"Wave {cursor.wave} check {g['subject']} failed on the integrated product. Expected: "
                    f"a unit of this wave owns the work it needs; got: its writer hands that work to "
                    f"{', '.join(g['units'])}, which no unit of this wave holds. Give the work an owner in "
                    "this wave: bring the commitment that owns it into this wave so a unit builds it. "
                    "Writer's report and failure: " + g["correction"])
        if store.read(g["source"]).get("purpose") == "final":
            return (f"Wave {cursor.wave} check {g['subject']} failed on the integrated product. "
                    f"The worker handoffs point to the provider and consumer of {', '.join(g['units'])}. "
                    "Identify and correct only the disputed agreement within the accepted scope. "
                    "Both sides will build against the corrected text. Handoffs and failure:\n"
                    + g["correction"])
        row = units.get(g["subject"])
        handed = store.read(row.report)["answer"]["discoveries"] if row and row.report else []
        return (f"Wave {cursor.wave}: the judge found {g['subject']} and contract {', '.join(g['units'])} "
                f"disagree on the integrated product, so no single worker can fix it in its scope. "
                "Correct or extend that contract text so its provider and consumers agree; name only the "
                "changed contract. Seldon then checks the provider and consumers against the corrected "
                f"text and reworks the side that differs. Judged gap: {g['correction']}"
                + (f" Worker's discoveries: {handed}" if handed else ""))

    text = "\n".join(explain(g) for g in gaps)
    checks = [g for g in gaps if g["kind"] == "contract"
              and store.read(g["source"]).get("purpose") == "final"]
    ref = store.record("architecture_gap", {"vision": cursor.vision, "source": [g["source"] for g in gaps],
                                            "outline": store.plan(cursor.revision)["architecture"],
                                            "gap": text, **({"checks": checks} if checks else {})})
    contracted = any(g["kind"] == "contract" for g in gaps)
    # A contract correction changes the outline, so its repeat is the same subject and agreement.
    recovery.planning(store, ref, sorted([g["kind"], g["subject"], *g["units"]] for g in gaps), "plan-gap",
                      outline=not contracted, finalCommit=None)


def corrected_checks(store):
    """Rework both sides of a corrected check agreement once, when its revised plan is reviewed."""
    from .architecture import index

    for _, _, transition in reversed(store.records("transition")):
        if transition["unit"] is not None:
            continue
        if transition["next"] == "EXECUTE":
            return []
        if transition["input"] == "WAVE-VERIFY" and transition["event"] == "plan-gap":
            correction = store.read(transition["cause"])
            break
    else:
        return []
    if not correction.get("checks"):
        return []
    document = planning.execution(store)
    before = index(correction["outline"]["contracts"])
    contracts = planning.select(document, "contract")
    rows = store.units()
    gaps = []
    for gap in correction["checks"]:
        for ref in gap["units"]:
            if ref not in contracts or before.get(ref, {}).get("text") == contracts[ref]["text"]:
                continue
            contract = contracts[ref]
            endpoints = [r for r in [contract["provider"], *contract["consumers"]]
                         if r in rows and rows[r].state == "DONE"]
            if endpoints:
                gaps.append({**gap, "kind": "flow", "units": endpoints,
                             "correction": f"Implement the corrected {ref} agreement.\n" + gap["correction"]})
    return gaps
