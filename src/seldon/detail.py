"""C1/C9/C12/C23; R3/R4: bounded area corrections preserve valid siblings and one approval plan."""

from concurrent.futures import ThreadPoolExecutor

from . import architecture, inference, plan, plan_review, questions, recovery
from .states import advance


def gap(store, corrections, source):
    cursor = store.cursor()
    outline = store.plan(cursor.revision)["architecture"]
    with store.transaction():
        ref = store.record("architecture_gap", {"vision": cursor.vision, "outline": outline,
                           "source": source, "gap": str(architecture.DetailCorrection(corrections))})
    recovery.planning(store, ref, outline, "contract-gap")


def step(store, provider, capacity):
    cursor = store.cursor()
    outline = store.plan(cursor.revision)["architecture"]
    vision = store.read(cursor.vision)["answer"]
    resumed = next((b["words"] for _, _, b in reversed(store.records("approval"))
                    if "step" in b["subject"] and b["choice"] in ("approve", "revise")), None)
    done, refs = {}, []
    repairs = [(r, b["corrections"]) for r, k, b in store.records()
               if k in ("review_repair", "detail_correction") and b["revision"] == cursor.revision]
    review_correction = repairs[-1] if repairs else None

    def ask(area, correction=None):
        try:
            question = questions.detail_brief(store, outline, area)
            local = {r: {**c,
                "provider": area["handle"] if c["provider"] in area.get("ancestors", []) else c["provider"],
                "consumers": [area["handle"] if a in area.get("ancestors", []) else a
                              for a in c["consumers"]]}
                for r, c in architecture.index(outline["contracts"]).items()}
            basis = {"vision": cursor.vision, "commit": cursor.candidate, "area": area,
                     "contracts": local, "facts": {**question["facts"]},
                     "singleArea": len(outline["areas"]) == 1}
            prior = [(r, b) for r, _, b in store.records("area_detail") if b.get("basis") == basis
                     and (not b.get("failure") and not b.get("architecture") or
                          b["revision"] == cursor.revision)]
            previous = prior[-1] if prior else None
            if previous and (not correction or previous[1].get("correction") == correction[0]
                             and previous[1].get("resume") == resumed) and (
                    previous[1].get("resume") == resumed or not previous[1].get("architecture")):
                done[area["handle"]] = previous[1]
                return previous[0]
            if correction:
                question["facts"]["Merge correction"] = correction[1][area["handle"]]
                if previous:
                    body = previous[1]
                    question["facts"]["Previous area plan"] = (body.get("rejected") if body.get("failure")
                        else store.read(body["answer"])["answer"])

            def validate(answer):
                if answer.get("verdict") == "subdivide":
                    architecture.subdivision(area, answer, architecture.index(outline["contracts"]))
                elif answer.get("verdict") == "planned":
                    architecture.local_plan(area, local, answer, vision, basis["singleArea"])

            body = {"revision": cursor.revision, "area": area["handle"], "basis": basis, "resume": resumed,
                    "correction": correction[0] if correction else None}
            try:
                result, answer = inference.call(store, provider, question, check=validate)
                body["answer"] = result
                if answer.get("verdict") == "contract_gap":
                    body["architecture"] = answer["need"]
            except architecture.ContractGap as error:
                body["architecture"] = str(error)
            except questions.AnswerRejected as error:
                body.update(failure=str(error), rejected=error.answer)
            ref = store.record("area_detail", body)
            done[area["handle"]] = body
            return ref
        finally:
            store.close()

    def collect(pending, correction=None):
        nonlocal outline
        leaves, failures, gaps = [], {}, {}
        if correction and "architecture" in correction[1]:
            pending, gaps = [], correction[1]
            refs.append(correction[0])
        with ThreadPoolExecutor(max_workers=capacity) as pool:
            while pending:
                batch, pending = pending, []
                results = list(pool.map(lambda a, c=correction: ask(
                    a, c if c and a["handle"] in c[1] else None), batch))
                correction = None
                for area, ref in zip(batch, results, strict=True):
                    refs.append(ref)
                    body = store.read(ref)
                    if body.get("architecture"):
                        gaps[area["handle"]] = [{"error": body["architecture"], "otherSide": {}}]
                    elif body.get("failure"):
                        failures[area["handle"]] = [{"error": body["failure"], "otherSide": {}}]
                    else:
                        answer = store.read(body["answer"])["answer"]
                        if answer["verdict"] == "subdivide":
                            recovery.planning(store, ref, answer)
                            outline = architecture.expand(outline, area, answer)
                            pending.extend(a for a in outline["areas"]
                                           if a["handle"] in architecture.index(answer["subareas"]))
                        else:
                            leaves.append((area, answer))
        if gaps:
            gap(store, gaps, refs)
            return None, failures
        return leaves, failures

    leaves, failures = collect(outline["areas"], review_correction)
    if leaves is None:
        return
    while True:
        try:
            if failures:
                raise architecture.DetailCorrection(failures)
            compiled = plan.add_guide(plan.merge(vision, outline, leaves))
            compiled["areaDetails"] = {a["handle"]: done[a["handle"]]["answer"] for a in outline["areas"]}
            break
        except architecture.ContractGap as error:
            gap(store, {a: [{"error": str(error), "otherSide": {}}] for a in error.areas}, refs)
            return
        except architecture.DetailCorrection as error:
            correction = (store.record("detail_correction", {"revision": cursor.revision,
                "sources": list(refs), "corrections": error.corrections}), error.corrections)
            recovery.planning(store, correction[0], error.corrections)
            replacement, failures = collect([a for a in outline["areas"]
                                             if a["handle"] in correction[1]], correction)
            if replacement is None:
                return
            replacements = {a["handle"]: (a, b) for a, b in replacement}
            leaves = [replacements.pop(a["handle"], (a, b)) for a, b in leaves]
            leaves.extend(replacements.values())
    with store.transaction():
        revision = store.revision(compiled, cursor.revision)
        reviewed = plan_review.architecture_brief(compiled, questions.user_words(store))
        has_review = any(b["subject"] == "architecture" and b["basis"] == reviewed
                         for _, _, b in store.records("area_review"))
        advance(store, "detailed" if has_review else "outline-changed", refs, revision=revision)
