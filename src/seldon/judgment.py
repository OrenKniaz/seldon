"""C3/C15/I2/I10; R2/R9/R11: one final subject, traced facts, local fixup, code ANDs."""

from dataclasses import replace
from pathlib import Path

from . import fixups, gitwork, inference, plan, proof, questions
from .states import advance


def subjects(document, kind):
    if kind == "FLOW":
        return plan.select(document, "requirement")
    return {**plan.select(document, "criterion"), "tryItGuide": {
        "criterion": "The try-it guide is usable from the delivered directory",
        "verification": plan.GUIDE_WORK, "checks": []}}


def trace(document, kind, subject):
    return (plan.assigned(document)["guide"] if subject == "tryItGuide" else
            plan.traced_checks(document, subject))


def owners(document, kind, subject):
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


def brief(store, kind, subject, *, trees=None):
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
        verdict = store.read(store.unit(ref).judgment)
        unchanged = verdict["scopeTree"] == trees[ref]
        if not unchanged:
            raise ValueError(f"{ref} needs its unit JUDGE on the changed scope before final judgment")
        if "answer" in verdict:
            verdicts.append({"unit": ref, "commit": verdict["commit"], **verdict["answer"]})
    final = questions.observed(store, refs)
    for item, ref in zip(final, refs):
        body = store.read(ref)
        item.update(workingDirectory=body["cwd"], commit=body["commit"], ranOnDeliveredRevision=True,
                    **{k: body[k] for k in ("executionStarted", "cleanupVerified", "timedOut")})
    body = subjects(document, kind)[subject]
    obligation = {k: body[k] for k in ("criterion", "verification")}
    facts = {"Subject": obligation, "Checks": checks, "Final observations": final,
             "Source judgment": verdicts,
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
        facts["Walkthrough check sources"] = {p: b.decode("utf-8", errors="replace") for p, b in
            gitwork.blobs(store.root.parent, cursor.finalCommit,
                          plan.select(document, "unit")["guide"]["checkPaths"]).items()}
    images = questions.images(store, refs)
    return questions.build(kind, facts, [subject], images=images)


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
    selected = [s for s in subjects(document, kind) if s not in done]
    batch, replies = {}, {}
    for subject in selected:
        question = brief(store, kind, subject, trees=trees)
        refs = [r for r in cursor.finalObservations
                if store.read(r)["check"] in trace(document, kind, subject)]
        failed = proof.failures(store, refs)
        if failed:
            answer = {"verdict": "gap", "gaps": [{"target": subject, "correction": proof.explain(failed)}],
                      "why": "A traced check failed on the final commit"}
            result = store.record("check_gap", {"subject": subject, "observations": refs,
                                              "commit": cursor.candidate, "answer": answer})
            replies[subject] = result, answer
        else:
            batch[subject] = question
    replies.update(inference.batch(store, provider, batch))
    gaps = []
    with store.transaction():
        for subject in selected:
            result, answer = replies[subject]
            if answer["verdict"] == "uncertain":
                answer = {**answer, "verdict": "not judged", "gaps": []}
            if not ready(answer):
                gaps.append({"subject": subject, "kind": "flow", "source": result,
                             "units": list(owners(document, kind, subject)), "commit": cursor.candidate,
                             "correction": str(answer)})
            else:
                ref = store.record("subject_verdict", {"subject": subject, "kind": kind,
                    "commit": cursor.candidate, "answer": answer, "source": result,
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
