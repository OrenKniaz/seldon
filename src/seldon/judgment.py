"""C3/C15/I2/I10; R2/R9/R11: one area question, criterion verdicts, local fixup, code ANDs."""

from dataclasses import replace
from pathlib import Path

from . import architecture, fixups, gitwork, inference, plan, questions
from .schema import answer_schema
from .states import advance
from . import features


def subjects(document, kind):
    if kind == "FLOW":
        return plan.select(document, "requirement")
    areas = dict(zip((a["handle"] for a in document["architecture"]["areas"]),
                     plan.select(document, "area"), strict=True))
    commitments = {ref: {"criterion": text,
        "verification": "Trace the accepted experience end to end across its areas",
        "areas": [areas[a["handle"]] for a in document["architecture"]["areas"]
                  if ref in a["commitments"]]}
        for ref, text in architecture.commitments(document["nodes"]["vision"]["body"]).items()
        if ref.startswith(("scopeIn:", "improvements:"))}
    return {**plan.select(document, "criterion"), **commitments, "tryItGuide": {
        "criterion": "The try-it guide is usable from the delivered directory",
        "verification": plan.GUIDE_WORK, "checks": []}}


def trace(document, kind, subject):
    body = subjects(document, kind)[subject]
    if "areas" in body:
        return {r: c for r, c in plan.select(document, "check").items()
                if c["owner"] == "guide" or
                plan.select(document, "unit")[c["owner"]]["area"] in body["areas"]}
    return (plan.assigned(document)["guide"] if subject == "tryItGuide" else
            plan.traced_checks(document, subject))


def owners(document, kind, subject):
    body = subjects(document, kind)[subject]
    if "areas" in body:
        return {r: u for r, u in plan.select(document, "unit").items()
                if r == "guide" or u["area"] in body["areas"]}
    if kind == "FLOW":
        return {r: u for r, u in plan.select(document, "unit").items()
                if subject in u["requirements"]}
    if "area" in body:
        return {r: u for r, u in plan.select(document, "unit").items() if u["area"] == body["area"]}
    return plan.traced_units(document, trace(document, kind, subject))


def scope_trees(store, owned):
    cursor = store.cursor()
    trees = {b["unit"]: b["scopeTree"] for r in cursor.finalObservations for b in [store.read(r)]
             if b["unit"] in owned and b["commit"] == cursor.candidate}
    if missing := set(owned) - trees.keys():
        units = plan.select(plan.execution(store), "unit")
        for ref in missing:
            trees[ref] = gitwork.scope_tree(store.root.parent, cursor.candidate,
                                           units[ref]["scope"], cursor.scratch)
    return trees


def current(verdict, trees):
    return verdict["scopeTrees"] == {r: trees[r] for r in verdict["scopeTrees"]}


def brief(store, kind, selected, *, trees=None):
    subject = selected[0]
    cursor = store.cursor()
    document = plan.execution(store)
    checks = trace(document, kind, subject)
    refs = [r for r in cursor.finalObservations if store.read(r)["check"] in checks]
    if (cursor.finalCommit != cursor.candidate or {store.read(r)["check"] for r in refs} != set(checks)
            or any(store.read(r)["commit"] != cursor.finalCommit for r in refs)):
        raise ValueError("Judge evidence requires every traced check on the final commit")
    owned = owners(document, kind, subject)
    trees = trees if trees is not None else scope_trees(store, owned)
    verdicts = []
    for ref in owned:
        if store.unit(ref).state == "DROPPED":
            verdicts.append({"unit": ref, "why": "left out by the user"})
            continue
        verdict = store.read(store.unit(ref).judgment)
        if verdict["scopeTree"] != trees[ref]:
            raise ValueError(f"{ref} needs its unit JUDGE on the changed scope before final judgment")
        if "answer" in verdict:
            verdicts.append({"unit": ref, "commit": verdict["commit"], **verdict["answer"]})
    final = questions.observed(store, refs)
    for item, ref in zip(final, refs):
        body = store.read(ref)
        item.update(workingDirectory=body["cwd"], commit=body["commit"], ranOnDeliveredRevision=True,
                    **{k: body[k] for k in ("executionStarted", "cleanupVerified", "timedOut")})
    criteria = {s: {k: subjects(document, kind)[s][k] for k in ("criterion", "verification")}
                for s in selected}
    facts = {"Left out by the user": plan.left_out(store), "Criteria": criteria, "Checks": checks, "Final observations": final,
             "Source judgment": verdicts,
             "Source availability": "Source is judged at the unit; source text is unavailable "
                                    "after integration.",
             "Reader's starting directory": str(store.root.parent.resolve())}
    body = subjects(document, kind)[subject]
    if "areas" in body:
        features = {f for u in plan.select(document, "unit").values() if u["area"] in body["areas"]
                    for r in u["requirements"]
                    for f in plan.select(document, "requirement")[r]["features"]}
        facts["Feature verdicts"] = [{"feature": row["feature"],
            **plan.select(document, "feature")[row["feature"]], "answer": row["answer"]}
            for ref in cursor.demoVerdicts for row in [store.read(ref)] if row["feature"] in features]
    artifacts = {}
    outputs = [item[key] for item in final for key in ("stdout", "stderr")]
    for ref in refs:
        for path in store.read(ref)["evidenceFiles"]:
            content = Path(path).read_bytes()
            artifacts[path] = {"bytes": len(content), "commit": cursor.finalCommit}
            try:
                text = content.decode("utf-8")
                if not any(text in output for output in outputs):
                    artifacts[path]["text"] = content[-4096:].decode("utf-8", errors="replace")
            except UnicodeDecodeError:
                pass
    if artifacts:
        facts["Check artifacts"] = artifacts
    if subject == "tryItGuide":
        facts["Guide file"] = {"path": plan.GUIDE_PATH, "commit": cursor.finalCommit,
                               "text": gitwork.git(store.root.parent, "show",
                                                   f"{cursor.finalCommit}:{plan.GUIDE_PATH}")}
        facts["Walkthrough check sources"] = {p: b.decode("utf-8", errors="replace") for p, b in
            gitwork.blobs(store.root.parent, cursor.finalCommit,
                          plan.select(document, "unit")["guide"]["checkPaths"]).items()}
    question = questions.build(kind, facts, selected, images=questions.images(store, refs))
    if "areas" in body:
        question["prompt"] = ("Was this accepted commitment delivered end to end across its areas? "
            "Passing features alone may leave a gap between them. Use the feature verdicts and guide "
            "walkthrough evidence; name the responsible correction owners for any behaviour gap. "
            + questions.JUDGMENT_LEEWAY)
    return {**question, "schema": review_schema(document, kind)}


def review_schema(document, kind):
    return answer_schema(kind, sorted(subjects(document, kind)),
                         sorted([*plan.select(document, "unit"), *plan.select(document, "contract")]))


def corrections(store, subject, source, answer):
    contracts = plan.select(plan.execution(store), "contract")
    owners = {}
    for gap in questions.must_fix(answer):
        for owner in gap["owners"]:
            owners.setdefault(owner, []).append(gap["correction"])
    return [{"subject": owner if owner in contracts else subject, "kind": "flow",
             "commit": store.cursor().candidate,
             "units": [contracts[owner]["provider"] if owner in contracts else owner],
             "correction": "; ".join(items), "source": source} for owner, items in owners.items()]


def ready(answer):
    # DV6: deferred judgment is visible at C; it is never satisfaction.
    return questions.satisfied(answer) or answer["verdict"] == "not judged"


def not_judged(store):
    cursor = store.cursor()
    deferred = {"review:" + subject: "not judged: " + row["reason"]
                for subject, row in (store.read(cursor.review)["notJudged"].items()
                                     if cursor.review else [])}
    deferred.update({v["subject"]: "not judged: " + "; ".join(dict.fromkeys(v["answer"]["neededFacts"]))
                     for v in ([store.read(r) for r in [*cursor.requirementVerdicts, *cursor.qualityVerdicts]]
                               + [{"subject": ref, "answer": store.read(row.judgment).get("answer", {})}
                                  for ref, row in store.units().items() if row.judgment])
                     if v["answer"].get("verdict") == "not judged"})
    return deferred


def notes(store):
    """Show recorded nonblocking findings alongside the verdict at C."""
    cursor = store.cursor()
    refs = [*cursor.demoVerdicts, *cursor.qualityVerdicts,
            *(u.judgment for u in store.units().values() if u.judgment)]
    if cursor.review:
        refs.append(cursor.review)
    return [{"source": ref, **gap} for ref in refs
            for gap in store.read(ref).get("answer", {}).get("gaps", [])
            if not features.blocking(gap)]


def combined(verdicts):
    answers = [v["answer"] for v in verdicts]
    return {"verdict": ("gap" if not all(ready(a) for a in answers) else
                        "not judged" if any(a["verdict"] == "not judged" for a in answers) else "satisfied"),
            "gaps": [g for a in answers for g in a["gaps"]],
            "neededFacts": [fact for a in answers for fact in a.get("neededFacts", [])],
            "why": "Combined recorded subject verdicts; unresolved facts remain not judged (DV6)"}


def step(store, provider):
    cursor = store.cursor()
    kind = "PRODUCT"
    document = plan.execution(store)
    trees = scope_trees(store, plan.select(document, "unit"))
    with store.transaction():
        cursor = replace(cursor, **{f: [r for r in getattr(cursor, f) if current(store.read(r), trees)]
                                    for f in ("requirementVerdicts", "qualityVerdicts")})
        store.cursor(cursor)
    done = {store.read(r)["subject"] for r in cursor.qualityVerdicts}
    groups = {}
    for subject, body in subjects(document, kind).items():
        groups.setdefault(body.get("area", subject), []).append(subject)
    selected = {area: ss for area, ss in groups.items() if set(ss) - done}
    replies = inference.batch(store, provider, {
        area: brief(store, kind, ss, trees=trees) for area, ss in selected.items()})
    gaps = []
    with store.transaction():
        for area, ss in selected.items():
            result, answer = replies[area]
            if answer["verdict"] == "uncertain":
                answer = {**answer, "verdict": "not judged", "gaps": []}
            result = store.record("area_verdict", {"area": area, "source": result, "answer": answer,
                "commit": cursor.candidate, "observations": [r for r in cursor.finalObservations
                                 if store.read(r)["check"] in trace(document, kind, ss[0])]})
            gaps.extend(corrections(store, area, result, answer))
            for subject in ss:
                if subject in done:
                    continue
                targeted = [g for g in answer["gaps"] if g["target"] == subject]
                individual = {**answer, "gaps": targeted, "verdict": (
                    "not judged" if answer["verdict"] == "not judged" else
                    "gap" if questions.must_fix({"gaps": targeted}) else "satisfied")}
                if not ready(individual):
                    continue
                ref = store.record("subject_verdict", {"subject": subject, "kind": kind,
                    "commit": cursor.candidate, "answer": individual, "source": result,
                    "scopeTrees": {r: trees[r] for r in owners(document, kind, subject)}})
                row = store.cursor()
                store.cursor(replace(row, qualityVerdicts=row.qualityVerdicts + [ref]))
        if gaps:
            fixups.apply(store, gaps)
        else:
            cursor = store.cursor()
            verdict = store.record("product_verdict", {"commit": cursor.candidate,
                "answer": combined([store.read(r) for r in cursor.qualityVerdicts]),
                "sources": cursor.qualityVerdicts})
            advance(store, "satisfied", verdict, productVerdict=verdict)
