"""C1/C9/C12/C23; R3/R4: bounded area corrections preserve valid siblings and one approval plan."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace

from . import architecture, inference, plan, questions, recovery
from .answers import recorder
from .states import advance


def gap(store, corrections, source):
    cursor = store.cursor()
    outline = store.plan(cursor.revision)["architecture"]
    ref = store.record("architecture_gap", {"vision": cursor.vision, "outline": outline,
                       "source": source, "gap": str(architecture.DetailCorrection(corrections))})
    forwarded = isinstance(source, str) and "corrections" in store.read(source)
    recovery.planning(store, source if forwarded else ref, corrections, "contract-gap")


def step(store, provider, capacity):
    cursor = store.cursor()
    previous = store.plan(cursor.revision)
    outline = previous["architecture"]
    architects = [b for _, _, b in store.records("answer") if b["kind"] == "ARCHITECT"]
    question = store.read(store.read(architects[-1]["attempt"])["question"])
    baseline = question["facts"].get("Previous architecture", outline)
    pending = architecture.detail_areas(outline, cursor.wave, baseline)
    # Make newly written contract text canonical before any consumer plans against it.
    updated = deepcopy(previous)
    contracts = architecture.index(outline["contracts"])
    for ref, contract in plan.select(updated, "contract").items():
        if ref in contracts:
            contract.update(kind=contracts[ref]["kind"], text=contracts[ref]["text"])
        else:
            del updated["nodes"][ref]  # A runtime assumption has no unit endpoint.
    if updated != previous:
        with store.transaction():
            revision = store.revision(updated, cursor.revision)
            cursor = replace(store.cursor(), revision=revision)
            store.cursor(cursor)
        previous = updated
    vision = store.read(cursor.vision)["answer"]
    setup = store.read(cursor.kickoff)["setup"]
    fold_guide = setup.get("sizeFirst") is True and setup.get("split") == "auto"
    guide = plan.guide_owner(previous)
    guide_area = pending[0]["handle"] if pending and guide in (None, "guide") else None
    resumed = next((b["words"] for _, _, b in reversed(store.records("approval"))
                    if "step" in b["subject"] and b["choice"] in ("approve", "revise")), None)
    done, refs = {}, []
    repairs = [(r, b["corrections"]) for r, k, b in store.records()
               if k in ("review_repair", "detail_correction") and b["revision"] == cursor.revision]
    review_correction = repairs[-1] if repairs else None

    def ask(area, correction=None):
        try:
            question = questions.detail_brief(store, outline, area,
                guide_brief=area["handle"] == guide_area,
                may_fold_guide=fold_guide and len(outline["areas"]) == len(outline["waves"]) == 1)
            if existing := question["facts"]["What exists here"].get(guide):
                existing["scope"] = [p for p in existing["scope"] if p not in plan.GUIDE_SCOPE]
            local = {r: {**c,
                "provider": area["handle"] if c["provider"] in area.get("ancestors", []) else c["provider"],
                "consumers": [area["handle"] if a in area.get("ancestors", []) else a
                              for a in c["consumers"]]}
                for r, c in architecture.index(outline["contracts"]).items()
                if c["provider"] in [area["handle"], *area.get("ancestors", [])]
                or set(c["consumers"]).intersection([area["handle"], *area.get("ancestors", [])])}
            basis = {"vision": cursor.vision, "commit": cursor.candidate, "area": area,
                     "contracts": local, "facts": {k: v for k, v in question["facts"].items()
                                                 if k not in ("Peer checks", "User's words")}}
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
                    architecture.expand(outline, area, answer, record=recorder(store))
                elif answer.get("verdict") == "planned":
                    architecture.local_plan(area, answer, vision, record=recorder(store))

            body = {"revision": cursor.revision, "area": area["handle"], "basis": basis, "resume": resumed,
                    "correction": correction[0] if correction else None}
            try:
                result, answer = inference.call(store, provider, question, check=validate,
                                                field="edits/units/subareas")
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
        nonlocal outline, guide_area
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
                            if area["handle"] == guide_area:
                                guide_area = answer["subareas"][0]["handle"]
                            pending.extend(a for a in outline["areas"]
                                           if a["handle"] in architecture.index(answer["subareas"]))
                        else:
                            leaves.append((area, answer))
        if gaps:
            gap(store, gaps, correction[0] if correction and "architecture" in correction[1] else refs)
            return None, failures
        return leaves, failures

    leaves, failures = collect(pending, review_correction)
    if leaves is None:
        return
    rows = store.units()
    retain_trace = any(unit.wave == cursor.wave for unit in rows.values())
    completed = {ref for ref, unit in rows.items() if unit.state == "DONE" and unit.wave < cursor.wave}
    normalised = []
    while True:
        normalised.clear()
        try:
            if failures:
                raise architecture.DetailCorrection(failures)
            compiled = plan.merge(vision, outline, leaves, previous, cursor.wave, fold_guide=fold_guide,
                retain_trace=retain_trace, completed=completed,
                record=lambda kind, body: normalised.append((kind, body)))
            compiled["areaDetails"] = {**previous.get("areaDetails", {}),
                **{a["handle"]: done[a["handle"]]["answer"] for a, _ in leaves}}
            break
        except architecture.ContractGap as error:
            gap(store, {a: [{"error": str(error), "otherSide": {}}] for a in error.areas}, refs)
            return
        except architecture.DetailCorrection as error:
            question = next((row["question"] for rows in error.corrections.values()
                             for row in rows if "question" in row), None)
            correction = (store.record("detail_correction", {"revision": cursor.revision,
                "sources": list(refs), "corrections": error.corrections,
                **({"question": question} if question else {})}), error.corrections)
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
        for kind, body in normalised:
            store.record(kind, {**body, "wave": cursor.wave, "revision": revision})
        advance(store, "detailed", refs, revision=revision)
