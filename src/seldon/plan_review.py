"""C11/R2: one bounded review of each wave, one repair, remaining gaps visible in its review."""

from . import architecture, fixups, plan, questions, unit_questions
from .states import advance
from . import features


def step(store, provider, capacity):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    vision = document["nodes"]["vision"]["body"]
    outline = document["architecture"]
    goals = questions.commitments(vision)
    pending = {}
    for area in outline["areas"]:
        area_ref = area["handle"]
        units = {r: u for r, u in plan.select(document, "unit").items()
                 if u["area"] == area_ref and r != "guide"}
        requirements = {r for u in units.values() for r in u["requirements"]}
        feature_refs = {f for r in requirements for f in document["nodes"][r]["body"]["features"]}
        pending[area_ref] = unit_questions.build("REVIEW", {
            "Review subject": area_ref, "User's words": questions.user_words(store),
            "Accepted commitments": {r: goals[r] for r in area["commitments"]},
            "Area": area, "Units": units,
            "Plan": {r: document["nodes"][r] for r in requirements | feature_refs},
            "Contracts": {r: c for u in units for r, c in architecture.contracts_for(document, u).items()}})
    projections = {}
    def consume(area, source, answer):
        projections[area] = ([{**g, "area": area} for g in answer.get("gaps", [])],
            {"reason": answer["why"], "neededFacts": answer["neededFacts"]}
            if answer["verdict"] == "uncertain" else None)
    replies = unit_questions.batch(store, provider, pending, consume=consume, field="verdict/gaps")
    gaps = [g for gs, _ in projections.values() for g in gs]
    unknown = {area: unknown for area, (_, unknown) in projections.items() if unknown}
    ref = store.record("plan_review", {"revision": cursor.revision,
        "subjects": {s: r for s, (r, _) in replies.items()},
        "answer": {"verdict": "gap" if questions.must_fix({"gaps": gaps}) else "not judged" if unknown else "adequate", "gaps": gaps},
        "notJudged": unknown, "reviewTier": "same-provider review"})
    event = "gap" if questions.must_fix({"gaps": gaps}) and not cursor.repairs else "reviewed" if not cursor.B else "planned"
    with store.transaction():
        corrections = (fixups.corrected_checks(store)
                       if event == "planned" and features.enabled("checkContract") else [])
        advance(store, event, ref, review=ref)
        fixups.apply(store, corrections, agreement=True)


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
