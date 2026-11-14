"""C8/C11; R2/R3: bounded area verdicts combine into one plan approval."""

from concurrent.futures import ThreadPoolExecutor

from . import architecture, detail, inference, plan, questions, recovery
from .states import advance


def briefs(store, document, words):
    outline = document["architecture"]
    vision = document["nodes"]["vision"]["body"]
    units = {r: u for r, u in plan.select(document, "unit").items() if r != "guide"}
    agreements = dict(zip(plan.select(document, "contract"),
                          (c["handle"] for c in outline["contracts"]), strict=True))
    writers = plan.owners(document)
    areas = dict(zip(plan.select(document, "area"), outline["areas"], strict=True))
    result = {}
    for area_ref, area in areas.items():
        owned = {r: u for r, u in units.items() if u["area"] == area_ref}
        requirements = {r for u in owned.values() for r in u["requirements"]}
        features = {f for r in requirements for f in document["nodes"][r]["body"]["features"]}
        checks = {r for r, w in writers.items() if w in owned}
        refs = set(owned) | requirements | features | checks
        refs.update(r for r, o in plan.select(document, "obligation").items() if o["owner"] in owned)
        nodes = {r: n for r, n in document["nodes"].items() if r in refs}
        participants = [area["handle"], *area.get("ancestors", [])]
        contracts = [c for c in outline["contracts"]
                     if any(architecture.endpoint(a, c) for a in participants)]
        for r in checks:
            check = nodes[r]["body"]
            nodes[r] = {**nodes[r], "body": {**check,
                "requirements": [ref for ref in check["requirements"] if ref in requirements],
                "integrates": [agreements[ref] for ref in check["integrates"]]}}
        for r, c in plan.select(document, "criterion").items():
            nodes[r] = {"kind": "criterion", "body": {k: v for k, v in c.items() if k != "checks"}}
        result[area["handle"]] = questions.build("REVIEW", {
            "Review subject": area["handle"], "Approved vision": vision, "Area": area,
            "User's words": words, "Peer checks": questions.peer_checks(store, outline, area),
            "Wave goal": outline["waves"][0]["goal"], "Contracts": contracts,
            "Decisions": [d for d in outline["decisions"] if set(d["areas"]) & set(participants)],
            "Plan": {"nodes": nodes}, "Proof map": plan.proof_map(
                document, [*sorted(requirements), *plan.select(document, "criterion")])})
    return result


def architecture_brief(document, words):
    outline = document["architecture"]
    question = questions.build("REVIEW", {
        "Review subject": "architecture",
        "Approved vision": {k: v for k, v in document["nodes"]["vision"]["body"].items() if k != "why"},
        "User's words": words,
        "Architecture outline": {
            "areas": [{k: a[k] for k in ("handle", "responsibility", "commitments")}
                      for a in outline["areas"]],
            **{k: outline[k] for k in ("contracts", "decisions", "waves")}}})
    question["prompt"] = ("Can these responsibilities, agreements and waves deliver the accepted vision? "
                          "Is an accepted promise or a cross-area agreement missing?")
    return question


def review(store, provider, question, basis):
    subject = question["facts"]["Review subject"]
    previous = next((b for _, _, b in reversed(store.records("area_review"))
                     if b["subject"] == subject and b["basis"] == basis), None)
    if previous:
        return previous["answer"]

    def check(answer):
        if (answer["verdict"] == "adequate") != (not answer["gaps"]):
            raise ValueError("An adequate review has no gaps; a gap needs a concrete correction")

    ref, answer = inference.call(store, provider, question, check=check)
    sources = [ref]
    if answer["verdict"] == "uncertain":
        cursor = store.cursor()
        document = store.plan(cursor.revision)
        setup = store.read(cursor.kickoff)["setup"]
        available = {"Runtime": questions.runtime(setup)}
        if subject == "architecture":
            available["Accepted commitments"] = architecture.commitments(
                document["nodes"]["vision"]["body"])
            available["Planning stage"] = (
                "Area details are recorded and reviewed separately; this subject is the outline."
                if "areaDetails" in document else
                "Architecture outline before DETAIL; requirements, checks and unit plans "
                "have not been written for this outline.")
        else:
            area = next(a for a in document["architecture"]["areas"] if a["handle"] == subject)
            available["Recorded area plan"] = store.read(document["areaDetails"][subject])["answer"]
            available["Product in this area"] = questions.detail_brief(
                store, document["architecture"], area)["facts"]["Product in this area"]
            available["Planning stage"] = "Before B; these are planned checks, not execution evidence."
        reask = {**question, "facts": {**question["facts"], "Requested facts": {
            "Needs": answer["neededFacts"], "Available subject facts": available}}}
        ref, answer = inference.call(store, provider, reask, check=check)
        sources.append(ref)
        if answer["verdict"] == "uncertain":
            answer = {**answer, "verdict": "not judged", "gaps": []}
    with store.transaction():
        result = store.record("review_verdict", {"subject": subject, "answer": answer, "sources": sources})
        store.record("area_review", {"revision": store.cursor().revision, "subject": subject,
                                     "basis": basis, "answer": result})
    return result


def arch_review(store, provider):
    document = store.plan(store.cursor().revision)
    question = architecture_brief(document, questions.user_words(store))
    ref = review(store, provider, question, question)
    answer = store.read(ref)["answer"]
    if answer["verdict"] == "gap":
        detail.gap(store, {"architecture": [{"error": g["correction"], "target": g["target"],
                                            "otherSide": {}} for g in answer["gaps"]]}, ref)
    else:
        advance(store, "reviewed-details" if "areaDetails" in document else "reviewed", ref)


def step(store, provider, capacity):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    pending = briefs(store, document, questions.user_words(store))
    area_handles = dict(zip(plan.select(document, "area"), pending, strict=True))
    targets = {ref: area_handles[u["area"]] for unit, u in plan.select(document, "unit").items()
               for ref in [unit, *u["requirements"]]}
    targets.update({c: targets[owner] for c, owner in plan.owners(document).items()})
    targets.update(area_handles)
    for subject, question in pending.items():
        targets.update({p["check"]: p["owner"] for p in question["facts"]["Peer checks"]})
        question["facts"]["Correction owners"] = {r: a for r, a in targets.items()
            if a == subject or a in {p["owner"] for p in question["facts"]["Peer checks"]}}

    def ask(subject):
        try:
            question = pending[subject]
            # Canonical DETAIL records survive global handle renumbering when another area grows.
            basis = {"detail": document["areaDetails"][subject], "peers": {
                        p["owner"]: document["areaDetails"][p["owner"]]
                        for p in question["facts"]["Peer checks"]},
                     "facts": {k: v for k, v in question["facts"].items()
                               if k not in ("Plan", "Proof map", "Correction owners")}}
            return review(store, provider, question, basis)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=capacity) as pool:
        refs = dict(zip(pending, pool.map(ask, pending), strict=True))
    outline_question = architecture_brief(document, questions.user_words(store))
    refs["architecture"] = next(b["answer"] for _, _, b in reversed(store.records("area_review"))
                                if b["subject"] == "architecture" and b["basis"] == outline_question)
    answers = {s: store.read(r)["answer"] for s, r in refs.items()}
    gaps = [{**g, "area": "architecture" if g["target"].lower().startswith("architecture")
             else targets.get(g["target"], g["target"] if g["target"] in pending else s)}
            for s, a in answers.items() for g in a["gaps"]]
    not_judged = {s: {"reason": a["why"], "neededFacts": a["neededFacts"]}
                  for s, a in answers.items() if a["verdict"] == "not judged"}
    verdict = "gap" if gaps else "not judged" if not_judged else "adequate"
    with store.transaction():
        result = store.record("plan_review", {"revision": cursor.revision, "subjects": refs,
            "answer": {"verdict": verdict, "gaps": gaps}, "notJudged": not_judged,
            "reviewTier": "partial review: not judged" if not_judged else "same-provider review"})
        advance(store, "gap" if gaps else "reviewed", result,
                review=result, failure=str(gaps))


def repair(store):
    cursor = store.cursor()
    corrections = {}
    for gap in store.read(cursor.review)["answer"]["gaps"] if cursor.review else []:
        corrections.setdefault(gap["area"], []).append({"error": gap["correction"],
                                                       "target": gap["target"], "otherSide": {}})
    if cursor.requestedChange:
        words = store.read(cursor.requestedChange)["words"]
        for area in store.plan(cursor.revision)["architecture"]["areas"]:
            corrections.setdefault(area["handle"], []).append({"error": words, "otherSide": {}})
    ref = store.record("review_repair", {"revision": cursor.revision, "review": cursor.review,
                                        "corrections": corrections})
    recovery.planning(store, ref, corrections, "area-gap", repairs=cursor.repairs + 1,
                      decision=None, requestedChange=None)
