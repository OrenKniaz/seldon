"""C11/R2: one bounded review of each wave, one repair, remaining gaps visible in its review."""

from . import architecture, inference, plan, questions
from .states import advance
from . import features


def step(store, provider, capacity):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    vision = document["nodes"]["vision"]["body"]
    outline = document["architecture"]
    goals = architecture.commitments(vision)
    pending = {}
    for area_ref, area in zip(plan.select(document, "area"), outline["areas"], strict=True):
        units = {r: u for r, u in plan.select(document, "unit").items()
                 if u["area"] == area_ref and r != "guide"}
        requirements = {r for u in units.values() for r in u["requirements"]}
        features = {f for r in requirements for f in document["nodes"][r]["body"]["features"]}
        pending[area["handle"]] = questions.build("REVIEW", {
            "Review subject": area["handle"], "User's words": questions.user_words(store),
            "Accepted commitments": {r: goals[r] for r in area["commitments"]},
            "Area": area, "Units": units,
            "Plan": {r: document["nodes"][r] for r in requirements | features},
            "Contracts": {r: c for u in units for r, c in architecture.contracts_for(document, u).items()}})
    projections = {}
    def consume(area, source, answer):
        projections[area] = ([{**g, "area": area} for g in answer.get("gaps", [])],
            {"reason": answer["why"], "neededFacts": answer["neededFacts"]}
            if answer["verdict"] == "uncertain" else None)
    replies = inference.batch(store, provider, pending, consume=consume, field="verdict/gaps")
    gaps = [g for gs, _ in projections.values() for g in gs]
    unknown = {area: unknown for area, (_, unknown) in projections.items() if unknown}
    ref = store.record("plan_review", {"revision": cursor.revision,
        "subjects": {s: r for s, (r, _) in replies.items()},
        "answer": {"verdict": "gap" if questions.must_fix({"gaps": gaps}) else "not judged" if unknown else "adequate", "gaps": gaps},
        "notJudged": unknown, "reviewTier": "same-provider review"})
    event = "gap" if questions.must_fix({"gaps": gaps}) and not cursor.repairs else "reviewed" if cursor.wave == 1 else "planned"
    advance(store, event, ref, review=ref)


def repair(store):
    cursor = store.cursor()
    corrections = {}
    for gap in store.read(cursor.review)["answer"]["gaps"] if cursor.review else []:
        if not features.blocking(gap):
            continue
        corrections.setdefault(gap["area"], []).append({"error": gap["correction"], "otherSide": {}})
    if cursor.requestedChange:
        words = store.read(cursor.requestedChange)["words"]
        corrections = {a["handle"]: [{"error": words, "otherSide": {}}]
                       for a in store.plan(cursor.revision)["architecture"]["areas"]}
    ref = store.record("review_repair", {"revision": cursor.revision, "corrections": corrections})
    advance(store, "area-gap", ref, repairs=cursor.repairs + 1, requestedChange=None)
