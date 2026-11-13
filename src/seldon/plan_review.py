"""C8/C11; R2/R3: bounded area verdicts combine into one plan approval."""

from concurrent.futures import ThreadPoolExecutor

from . import architecture, inference, plan, questions
from .states import advance


def briefs(document):
    outline = document["architecture"]
    vision = document["nodes"]["vision"]["body"]
    units = {r: u for r, u in plan.select(document, "unit").items() if r != "guide"}
    coverage = plan.coverage(document)
    result, counts = {}, {}
    for area, area_ref in zip(outline["areas"], plan.select(document, "area"), strict=True):
        owned = {r: u for r, u in units.items() if u["area"] == area_ref}
        requirements = {r for u in owned.values() for r in u["requirements"]}
        features = {f for r in requirements for f in document["nodes"][r]["body"]["features"]}
        checks = {r for r, c in plan.select(document, "check").items()
                  if set(c["requirements"]) <= requirements
                  or any(plan.matches(arg, u["scope"]) for arg in c["command"] for u in owned.values())}
        refs = set(owned) | requirements | features | checks
        refs.update(r for r, o in plan.select(document, "obligation").items() if o["owner"] in owned)
        nodes = {r: n for r, n in document["nodes"].items() if r in refs}
        for r, c in plan.select(document, "criterion").items():
            nodes[r] = {"kind": "criterion", "body": {
                **c, "checks": [ref for ref in c["checks"] if ref in checks]}}
        trace = {r: {"checks": [c for c in row["checks"] if c in checks],
                     "units": [u for u in row["units"] if u in owned]}
                 for r, row in coverage.items() if r in requirements or r.startswith("quality:")}
        participants = [area["handle"], *area.get("ancestors", [])]
        contracts = [c for c in outline["contracts"]
                     if any(architecture.endpoint(a, c) for a in participants)]
        result[area["handle"]] = questions.build("REVIEW", {
            "Review subject": area["handle"], "Approved vision": vision, "Area": area,
            "Wave goal": outline["waves"][0]["goal"], "Contracts": contracts,
            "Decisions": [d for d in outline["decisions"] if set(d["areas"]) & set(participants)],
            "Plan": {"nodes": nodes}, "Trace coverage": trace})
        counts[area["handle"]] = {"features": len(features), "requirements": len(requirements),
            "checks": len(checks), "units": len(owned),
            "requirementsWithChecks": sum(bool(trace[r]["checks"]) for r in requirements),
            "qualityCriteriaWithChecks": sum(bool(trace[r]["checks"]) for r in trace
                                              if r.startswith("quality:"))}
    result["cross-area"] = questions.build("REVIEW", {
        "Review subject": "cross-area", "Approved vision": vision,
        "Architecture outline": {"areas": [{k: a[k] for k in ("handle", "responsibility", "commitments")}
                                           for a in outline["areas"]],
                                 "contracts": outline["contracts"], "waves": outline["waves"]},
        "Per-area coverage counts": counts})
    return result


def step(store, provider, capacity):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    pending = briefs(document)
    saved = [b for _, _, b in store.records("area_review")]

    def ask(subject):
        try:
            question = pending[subject]
            # Canonical DETAIL records survive global handle renumbering when another area grows.
            basis = (question if subject == "cross-area" else {
                "detail": document["areaDetails"][subject],
                "facts": {k: v for k, v in question["facts"].items() if k not in ("Plan", "Trace coverage")}})
            previous = next((b for b in reversed(saved)
                             if b["subject"] == subject and b["basis"] == basis), None)
            if previous:
                return previous["answer"]

            def check(answer):
                if (answer["verdict"] == "adequate") != (not answer["gaps"]):
                    raise ValueError("An adequate review has no gaps; a gap needs a concrete correction")

            ref, _ = inference.call(store, provider, question, check=check)
            store.record("area_review", {"revision": cursor.revision, "subject": subject,
                                         "basis": basis, "answer": ref})
            return ref
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=capacity) as pool:
        refs = dict(zip(pending, pool.map(ask, pending), strict=True))
    answers = {s: store.read(r)["answer"] for s, r in refs.items()}
    gaps = [{**g, "area": s} for s, a in answers.items() for g in a["gaps"]]
    adequate = all(a["verdict"] == "adequate" for a in answers.values())
    with store.transaction():
        result = store.record("plan_review", {"revision": cursor.revision, "subjects": refs,
            "answer": {"verdict": "adequate" if adequate else "gap", "gaps": gaps}})
        advance(store, "adequate" if adequate else "gap", result, review=result, failure=str(gaps))


def repair(store):
    cursor = store.cursor()
    if cursor.B:
        raise ValueError("Post-B plan steering is deferred to M3")
    if cursor.repairs >= 3:
        raise ValueError("Three pre-B plan repairs exhausted: " + cursor.failure)
    gaps = store.read(cursor.review)["answer"]["gaps"] if cursor.review else []
    corrections = {}
    for gap in gaps:
        corrections.setdefault(gap["area"], []).append({"error": gap["correction"],
                                                       "target": gap["target"], "otherSide": {}})
    if cursor.requestedChange:
        words = store.read(cursor.requestedChange)["words"]
        corrections = {a["handle"]: [{"error": words, "otherSide": {}}]
                       for a in store.plan(cursor.revision)["architecture"]["areas"]}
    with store.transaction():
        ref = store.record("review_repair", {"revision": cursor.revision, "review": cursor.review,
                                            "corrections": corrections})
        if "cross-area" in corrections:
            store.record("architecture_gap", {"vision": cursor.vision,
                                               "gap": str(corrections), "repair": ref})
            event = "architecture-gap"
        else:
            event = "area-gap"
        advance(store, event, ref, repairs=cursor.repairs + 1, decision=None, requestedChange=None)
