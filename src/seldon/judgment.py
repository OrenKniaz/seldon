"""C3/C15/I2/I10; R2/R9/R11: one final subject, traced facts, local fixup, code ANDs."""

from dataclasses import replace
from pathlib import Path

from . import gitwork, inference, plan, proof, questions, units
from .states import advance


def subjects(document, kind):
    if kind == "DEMO":
        return plan.select(document, "requirement")
    return {**plan.select(document, "criterion"), "tryItGuide": {
        "criterion": "The try-it guide is usable from the delivered directory",
        "verification": plan.GUIDE_WORK, "checks": []}}


def trace(document, kind, subject):
    return {} if subject == "tryItGuide" else plan.traced_checks(document, subject)


def owners(document, kind, subject):
    if subject == "tryItGuide":
        return {"guide": plan.select(document, "unit")["guide"]}
    return plan.traced_units(document, trace(document, kind, subject))


def brief(store, kind, subject, *, resolve=None):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    checks = trace(document, kind, subject)
    refs = [r for r in cursor.finalObservations if store.read(r)["check"] in checks]
    if (cursor.finalCommit != cursor.candidate or {store.read(r)["check"] for r in refs} != set(checks)
            or any(store.read(r)["commit"] != cursor.finalCommit for r in refs)):
        raise ValueError("Judge evidence requires every traced check on the final commit")
    owned = owners(document, kind, subject)
    scopes = [p for u in owned.values() for p in u["scope"]]
    patterns = [p for u in owned.values() for p in u["checkPaths"]]
    # Scope ownership is the existing file trace; include helpers, not just command arguments.
    files = gitwork.sources(store.root.parent, cursor.finalCommit, scopes, details=True)
    sources = [p for p in files if plan.matches(p, patterns)]
    final = questions.observed(store, refs)
    for item, ref in zip(final, refs):
        body = store.read(ref)
        item.update(workingDirectory=body["cwd"], commit=body["commit"], ranOnDeliveredRevision=True)
    body = subjects(document, kind)[subject]
    obligation = ({k: v for k, v in body.items() if k != "features"} if kind == "DEMO" else
                  {k: body[k] for k in ("criterion", "verification")})
    facts = {"Subject": obligation, "Checks": checks, "Final observations": final,
             "Check source paths": sources, "Files on the delivered revision": files,
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
                               **{k: v for k, v in files.get(
                                   plan.GUIDE_PATH, {"present": False, "bytes": 0}).items() if k != "text"}}
    if resolve is not None:
        # Rehydrate authoritative records through the same subject trace, never semantic keyword matching.
        facts["Requested facts"] = resolve
        facts["Code-established execution facts"] = [
            {k: v for k, v in store.read(r).items() if k not in ("stdout", "stderr")} for r in refs]
        facts["Code-established coverage"] = {
            "commit": cursor.finalCommit, "checksPassed": not proof.failures(store, refs),
            "scopes": scopes, "trackedFiles": list(files)}
    return questions.build(kind, facts, [subject], images=questions.images(store, refs))


def combined(verdicts):
    passed = all(questions.satisfied(v["answer"], final=True) for v in verdicts)
    return {"verdict": "satisfied" if passed else "gap",
            "gaps": [g for v in verdicts for g in v["answer"]["gaps"]],
            "assessments": [a for v in verdicts for a in v["answer"]["assessments"]],
            "why": "AND of the recorded subject verdicts"}


def fixup(store, kind, subject, result, answer):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    affected = owners(document, kind, subject)
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
                requirementVerdicts=[], qualityVerdicts=[], demoVerdicts=[], productVerdict=None,
                checkin=None, decision=None)


def save(store, kind, subject, result, answer):
    field = "requirementVerdicts" if kind == "DEMO" else "qualityVerdicts"
    with store.transaction():
        cursor = store.cursor()
        ref = store.record("subject_verdict", {"subject": subject, "kind": kind,
            "commit": cursor.candidate, "answer": answer, "source": result})
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
    if cursor.pendingJudge:
        pending = store.read(cursor.pendingJudge)
        subject = pending["subject"]
        question = brief(store, kind, subject, resolve=pending["answer"]["neededFacts"])
        result, answer = inference.call(store, provider, question)
        if not questions.satisfied(answer, final=True):
            fixup(store, kind, subject, result, answer)
            return
        with store.transaction():
            save(store, kind, subject, result, answer)
            advance(store, "resolved", result)
        return
    done = {store.read(r)["subject"] for r in getattr(cursor, field)}
    for subject in subjects(document, kind):
        if subject in done:
            continue
        question = brief(store, kind, subject)
        refs = [r for r in cursor.finalObservations
                if store.read(r)["check"] in trace(document, kind, subject)]
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
        save(store, kind, subject, result, answer)
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
