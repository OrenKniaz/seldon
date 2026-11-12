"""C3/C15/I2/I10; R2/R9/R11: one final subject, traced facts, local fixup, code ANDs."""

from dataclasses import replace
from pathlib import Path

from . import gitwork, guide, inference, plan, proof, questions, units
from .states import advance


def subjects(document, kind):
    if kind == "DEMO":
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
    scopes = [p for u in owned.values() for p in u["scope"]]
    trees = trees if trees is not None else scope_trees(store, owned)
    verdicts = []
    for ref in owned:
        verdict = store.read(store.unit(ref).judgment)
        commit = verdict["commit"]
        unchanged = verdict["scopeTree"] == trees[ref]
        if not unchanged:
            raise ValueError(f"{ref} needs its unit JUDGE on the changed scope before final judgment")
        verdicts.append({"unit": ref, "verdict": verdict.get("answer", {}).get("verdict", "guide written"),
                         "commit": commit, "scopeFilesUnchangedSinceVerdict": unchanged})
    final = questions.observed(store, refs)
    for item, ref in zip(final, refs):
        body = store.read(ref)
        item.update(workingDirectory=body["cwd"], commit=body["commit"], ranOnDeliveredRevision=True)
    body = subjects(document, kind)[subject]
    obligation = ({k: v for k, v in body.items() if k != "features"} if kind == "DEMO" else
                  {k: body[k] for k in ("criterion", "verification")})
    facts = {"Subject": obligation, "Checks": checks, "Final observations": final,
             "Unit verdicts": verdicts,
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
                    artifacts[path]["text"] = text
            except UnicodeDecodeError:
                pass
    if artifacts:
        facts["Check artifacts"] = artifacts
    if subject == "tryItGuide":
        facts["Guide file"] = {"path": plan.GUIDE_PATH, "commit": cursor.finalCommit,
                               "text": gitwork.git(store.root.parent, "show",
                                                   f"{cursor.finalCommit}:{plan.GUIDE_PATH}")}
    if resolve is not None:
        # Rehydrate authoritative records through the same subject trace, never semantic keyword matching.
        facts["Requested facts"] = resolve
        facts["Code-established execution facts"] = [
            {k: v for k, v in store.read(r).items() if k not in ("stdout", "stderr", "scopeTree")}
            for r in refs]
        facts["Code-established coverage"] = {
            "commit": cursor.finalCommit, "checksPassed": not proof.failures(store, refs),
            "scopes": scopes}
    return questions.build(kind, facts, [subject], images=questions.images(store, refs))


def combined(verdicts):
    passed = all(questions.satisfied(v["answer"], final=True) for v in verdicts)
    return {"verdict": "satisfied" if passed else "gap",
            "gaps": [g for v in verdicts for g in v["answer"]["gaps"]],
            "assessments": [a for v in verdicts for a in v["answer"]["assessments"]],
            "why": "AND of the recorded subject verdicts"}


def fixup(store, kind, subject, result, answer, affected=None):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    affected = owners(document, kind, subject) if affected is None else affected
    failure = str(answer)
    with store.transaction():
        cause = store.record("fixup", {"subject": subject, "kind": kind, "source": result,
                                      "units": list(affected), "commit": cursor.candidate,
                                      "correction": failure})
        for ref in affected:
            row = store.unit(ref)
            gitwork.checkout(row.worktree, cursor.candidate)
            store.unit(ref, replace(row, base=cursor.candidate))
            units.recover(store, ref, failure)
        advance(store, "gap", cause, pendingJudge=None, finalCommit=None, finalObservations=[],
                demoVerdicts=[], productVerdict=None,
                checkin=None, decision=None)


def save(store, kind, subject, result, answer, trees):
    field = "requirementVerdicts" if kind == "DEMO" else "qualityVerdicts"
    with store.transaction():
        cursor = store.cursor()
        ref = store.record("subject_verdict", {"subject": subject, "kind": kind,
            "commit": cursor.candidate, "answer": answer, "source": result,
            "scopeTrees": {r: trees[r] for r in owners(store.plan(cursor.revision), kind, subject)}})
        store.cursor(replace(cursor, **{field: getattr(cursor, field) + [ref]}, pendingJudge=None))


def step(store, provider):
    cursor = store.cursor()
    kind = "DEMO" if cursor.state.startswith("DEMO") else "PRODUCT"
    field = "requirementVerdicts" if kind == "DEMO" else "qualityVerdicts"
    document = store.plan(cursor.revision)
    approved = store.read(cursor.B)["subject"]
    if (approved["revision"] != cursor.revision or approved["plan"] != document
            or approved["review"] != store.read(cursor.review)):
        raise ValueError("Final judgment requires B for this exact plan and review")
    trees = scope_trees(store, plan.select(document, "unit"))
    for ref, unit in plan.select(document, "unit").items():
        row = store.unit(ref)
        verdict = store.read(row.judgment)
        if verdict["scopeTree"] == trees[ref]:
            continue
        refs = [r for r in cursor.finalObservations if store.read(r)["unit"] == ref]
        evidence = [r for r in row.observations if store.read(r)["purpose"] != "P"] + refs
        result, answer = inference.call(store, provider, questions.judge_brief(
            store, ref, cursor.candidate, evidence))
        if not questions.satisfied(answer):
            fixup(store, kind, ref, result, answer, {ref: unit})
            return
        result = store.record("unit_verdict", {"commit": cursor.candidate, "scopeTree": trees[ref],
                                               "answer": answer, "source": result})
        with store.transaction():
            store.unit(ref, replace(row, judgment=result))
    with store.transaction():
        cursor = replace(cursor, **{f: [r for r in getattr(cursor, f) if current(store.read(r), trees)]
                                    for f in ("requirementVerdicts", "qualityVerdicts")})
        store.cursor(cursor)
    if cursor.pendingJudge:
        pending = store.read(cursor.pendingJudge)
        subject = pending["subject"]
        question = brief(store, kind, subject, resolve=pending["answer"]["neededFacts"], trees=trees)
        result, answer = inference.call(store, provider, question)
        if not questions.satisfied(answer, final=True):
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
                    "commit": cursor.candidate, "source": result, "answer": answer})
                advance(store, "uncertain", result, pendingJudge=pending)
            return
        if not questions.satisfied(answer, final=True):
            fixup(store, kind, subject, result, answer)
            return
        save(store, kind, subject, result, answer, trees)
    cursor = store.cursor()
    verdicts = [store.read(r) for r in getattr(cursor, field)]
    with store.transaction():
        if kind == "DEMO":
            refs = []
            for feature in plan.select(document, "feature"):
                requirements = {r for r, b in plan.select(document, "requirement").items()
                                if feature in b["features"]}
                selected = [v for v in verdicts if v["subject"] in requirements]
                refs.append(store.record("feature_verdict", {"feature": feature,
                    "commit": cursor.candidate, "answer": combined(selected),
                    "sources": [r for r in cursor.requirementVerdicts
                                if store.read(r)["subject"] in requirements]}))
            advance(store, "satisfied", refs, demoVerdicts=refs)
        else:
            verdict = store.record("product_verdict", {"commit": cursor.candidate,
                "answer": combined(verdicts), "sources": cursor.qualityVerdicts})
            advance(store, "satisfied", verdict, productVerdict=verdict)
