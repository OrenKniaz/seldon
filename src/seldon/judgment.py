"""C3/C15/I2/I10; R2/R9/R11: one final subject, traced facts, local fixup, code ANDs."""

from dataclasses import replace
from pathlib import Path

from . import fixups, gitwork, guide, inference, plan, proof, questions
from .states import advance
from .store import encode


def subjects(document, kind):
    if kind == "FLOW":
        return plan.select(document, "requirement")
    return {**plan.select(document, "criterion"), "tryItGuide": {
        "criterion": "The try-it guide is usable from the delivered directory",
        "verification": plan.GUIDE_WORK, "checks": []}}


def trace(document, kind, subject, store=None):
    return (guide.checks(store.root.parent) if store else {}) if subject == "tryItGuide" else \
        plan.traced_checks(document, subject)


def owners(document, kind, subject):
    if subject == "tryItGuide":
        return {"guide": plan.select(document, "unit")["guide"]}
    return plan.traced_units(document, trace(document, kind, subject))


def scope_trees(store, owned):
    cursor = store.cursor()
    trees = {b["unit"]: b["scopeTree"] for r in cursor.finalObservations for b in [store.read(r)]
             if b["unit"] in owned and b["commit"] == cursor.candidate}
    if set(trees) != set(owned):
        raise ValueError("Every reached unit needs its scope tree observed on the final commit")
    return trees


def current(verdict, trees):
    return verdict["scopeTrees"] == {r: trees[r] for r in verdict["scopeTrees"]}


def brief(store, kind, subject, *, resolve=None, trees=None):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    checks = trace(document, kind, subject, store)
    refs = [r for r in cursor.finalObservations if store.read(r)["check"] in checks]
    if (cursor.finalCommit != cursor.candidate or {store.read(r)["check"] for r in refs} != set(checks)
            or any(store.read(r)["commit"] != cursor.finalCommit for r in refs)):
        raise ValueError("Judge evidence requires every traced check on the final commit")
    owned = owners(document, kind, subject)
    trees = trees if trees is not None else scope_trees(store, owned)
    satisfied = 0
    for ref in owned:
        verdict = store.read(store.unit(ref).judgment)
        unchanged = verdict["scopeTree"] == trees[ref]
        if not unchanged:
            raise ValueError(f"{ref} needs its unit JUDGE on the changed scope before final judgment")
        satisfied += int(verdict.get("answer", {}).get("verdict") == "satisfied")
    final = questions.observed(store, refs)
    for item, ref in zip(final, refs):
        body = store.read(ref)
        item.update(workingDirectory=body["cwd"], commit=body["commit"], ranOnDeliveredRevision=True,
                    **{k: body[k] for k in ("executionStarted", "cleanupVerified", "timedOut")})
    body = subjects(document, kind)[subject]
    obligation = {k: body[k] for k in ("criterion", "verification")}
    facts = {"Subject": obligation, "Checks": checks, "Final observations": final,
             "Source judgment": ("source judged at every reached unit: "
                                 f"{satisfied} of {len(set(owned) - {'guide'})} satisfied, "
                                 "scope trees unchanged since those verdicts"),
             "Source availability": "Source is judged at the unit; source text is unavailable "
                                    "after integration.",
             "Reader's starting directory": str(store.root.parent.resolve())}
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
    images = questions.images(store, refs)
    question = questions.build(kind, facts, [subject], images=images)
    if resolve is None:
        return question
    # Requests are semantic text. Code supplies only named facts already owned by this subject,
    # without guessing their meaning or duplicating outputs, artifacts or verdict rows (T-79).
    facts = {**facts, "Requested facts": [{"request": item, "observations": list(checks),
                                  "screenshots": images} for item in resolve]}
    reask = questions.build(kind, facts, [subject], images=images)
    if len(encode(reask).encode("utf-8")) > 2 * len(encode(question).encode("utf-8")):
        raise questions.MissingFacts("Requested facts exceed twice the original subject brief")
    return reask


def ready(answer):
    # DV6: deferred judgment is visible at C; it is never satisfaction.
    return questions.satisfied(answer, final=True) or answer["verdict"] == "not judged"


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


def combined(verdicts):
    answers = [v["answer"] for v in verdicts]
    return {"verdict": ("gap" if not all(ready(a) for a in answers) else
                        "not judged" if any(a["verdict"] == "not judged" for a in answers) else "satisfied"),
            "gaps": [g for a in answers for g in a["gaps"]],
            "assessments": [item for a in answers for item in a["assessments"]],
            "neededFacts": [fact for a in answers for fact in a.get("neededFacts", [])],
            "why": "Combined recorded subject verdicts; unresolved facts remain not judged (DV6)"}


def fixup(store, kind, subject, result, answer, affected=None):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    affected = owners(document, kind, subject) if affected is None else affected
    return fixups.apply(store, [{"subject": subject, "kind": "flow", "source": result,
                                 "units": list(affected), "commit": cursor.candidate,
                                 "correction": str(answer)}])


def save(store, kind, subject, result, answer, trees):
    field = "qualityVerdicts"
    with store.transaction():
        cursor = store.cursor()
        ref = store.record("subject_verdict", {"subject": subject, "kind": kind,
            "commit": cursor.candidate, "answer": answer, "source": result,
            "scopeTrees": {r: trees[r] for r in owners(store.plan(cursor.revision), kind, subject)}})
        store.cursor(replace(cursor, **{field: getattr(cursor, field) + [ref]}, pendingJudge=None))


def refresh_units(store, provider):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    approved = store.read(cursor.B)["subject"]
    if (approved["revision"] != cursor.revision or approved["plan"] != document
            or approved["review"] != store.read(cursor.review)):
        raise ValueError("Final judgment requires B for this exact plan and review")
    trees = scope_trees(store, plan.select(document, "unit"))
    for ref, unit in plan.select(document, "unit").items():
        row = store.unit(ref)
        verdict = store.read(row.judgment) if row.judgment else None
        targets = set(unit["requirements"]) | (set(questions.SOURCE_TARGETS) if ref != "guide" else set())
        assessed = ({a["target"] for a in verdict.get("answer", {}).get("assessments", [])}
                    if verdict else set())
        if (verdict and verdict["scopeTree"] == trees[ref]
                and (targets <= assessed or verdict.get("answer", {}).get("verdict") == "not judged")):
            continue
        refs = [r for r in cursor.finalObservations if store.read(r)["unit"] == ref]
        evidence = [r for r in row.observations if store.read(r)["purpose"] != "P"] + refs
        result, answer = inference.call(store, provider, questions.judge_brief(
            store, ref, cursor.candidate, evidence))
        if answer["verdict"] != "not judged" and not questions.satisfied(answer):
            fixup(store, "FLOW", ref, result, answer, {ref: unit})
            return False
        result = store.record("unit_verdict", {"commit": cursor.candidate, "scopeTree": trees[ref],
                                               "answer": answer, "source": result})
        with store.transaction():
            store.unit(ref, replace(row, judgment=result))
    return True


def step(store, provider):
    if not refresh_units(store, provider):
        return
    cursor = store.cursor()
    kind, field = "PRODUCT", "qualityVerdicts"
    document = store.plan(cursor.revision)
    trees = scope_trees(store, plan.select(document, "unit"))
    with store.transaction():
        cursor = replace(cursor, **{f: [r for r in getattr(cursor, f) if current(store.read(r), trees)]
                                    for f in ("requirementVerdicts", "qualityVerdicts")})
        store.cursor(cursor)
    if cursor.pendingJudge:
        pending = store.read(cursor.pendingJudge)
        subject = pending["subject"]
        try:
            question = brief(store, kind, subject, resolve=pending["answer"]["neededFacts"], trees=trees)
            result, answer = inference.call(store, provider, question, max_bytes=2 * pending["questionBytes"])
        except questions.MissingFacts as error:
            result = pending["source"]
            answer = {**pending["answer"], "why": pending["answer"]["why"] + "; " + str(error)}
        if answer["verdict"] == "uncertain":
            answer = {**answer, "verdict": "not judged", "assessments": [], "gaps": []}
        if not ready(answer):
            fixup(store, kind, subject, result, answer)
            return
        with store.transaction():
            save(store, kind, subject, result, answer, trees)
            advance(store, "resolved", result)
        return
    done = {store.read(r)["subject"] for r in getattr(cursor, field)}
    for subject in subjects(document, kind):
        if subject in done:
            continue
        question = brief(store, kind, subject, trees=trees)
        refs = [r for r in cursor.finalObservations
                if store.read(r)["check"] in trace(document, kind, subject, store)]
        failed = proof.failures(store, refs)
        if failed:
            answer = {"verdict": "gap", "gaps": [{"target": subject, "correction": proof.explain(failed)}],
                      "assessments": [], "why": "A traced check failed on the final commit"}
            result = store.record("check_gap", {"subject": subject, "observations": refs,
                                              "commit": cursor.candidate, "answer": answer})
        else:
            result, answer = inference.call(store, provider, question)
        if answer["verdict"] == "uncertain":
            with store.transaction():
                pending = store.record("judge_uncertain", {"subject": subject, "kind": kind,
                    "commit": cursor.candidate, "source": result, "answer": answer,
                    "questionBytes": len(encode(question).encode("utf-8"))})
                advance(store, "uncertain", result, pendingJudge=pending)
            return
        if not questions.satisfied(answer, final=True):
            fixup(store, kind, subject, result, answer)
            return
        save(store, kind, subject, result, answer, trees)
    cursor = store.cursor()
    verdicts = [store.read(r) for r in getattr(cursor, field)]
    with store.transaction():
        verdict = store.record("product_verdict", {"commit": cursor.candidate,
            "answer": combined(verdicts), "sources": cursor.qualityVerdicts})
        advance(store, "satisfied", verdict, productVerdict=verdict)
