"""C3/C9/C15; R2/R4/R7/R12: one feature's behaviour across its contracts, without repair."""

from dataclasses import replace

from . import architecture, fixups, inference, judgment, plan, proof, questions
from .schema import answer_schema
from .states import advance
from .store import encode


def trace(document, feature):
    requirements = {r: b for r, b in plan.select(document, "requirement").items()
                    if feature in b["features"]}
    checks = {r: c for r, c in plan.select(document, "check").items()
              if set(requirements).intersection(c["requirements"])}
    return requirements, checks


def evidence(store, feature):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    requirements, checks = trace(document, feature)
    refs = [r for r in cursor.finalObservations if store.read(r)["check"] in checks]
    if (not requirements or cursor.finalCommit != cursor.candidate
            or {store.read(r)["check"] for r in refs} != set(checks)
            or any(store.read(r)["commit"] != cursor.candidate for r in refs)):
        raise ValueError("Flow review needs every traced check on the final candidate")
    if proof.failures(store, refs):
        raise ValueError("Return failed checks as check Gaps before flow review")
    owned = plan.traced_units(document, checks)
    trees = judgment.scope_trees(store, owned)
    verdicts = []
    for ref in owned:
        row = store.unit(ref)
        verdict = store.read(row.judgment) if row.judgment else None
        if not verdict or verdict["scopeTree"] != trees[ref]:
            raise ValueError(f"{ref} needs its own unit JUDGE before flow review")
        verdicts.append(verdict["answer"]["verdict"])
    return requirements, checks, refs, trees, verdicts


def brief(store, feature, *, resolve=None):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    requirements, checks, refs, _, verdicts = evidence(store, feature)
    observations = []
    for ref in refs:
        body = store.read(ref)
        observations.append({k: body[k] for k in (
            "check", "requirements", "exitCode", "stdout", "stderr", "executionStarted",
            "cleanupVerified", "timedOut")})
        observations[-1]["screenshots"] = questions.images(store, [ref])
    facts = {
        "Feature": {k: document["nodes"][feature]["body"][k] for k in ("story", "acceptance")},
        "Requirements": {r: {k: b[k] for k in ("obligation", "design", "contract")}
                         for r, b in requirements.items()},
        "Checks": {r: {k: c[k] for k in ("purpose", "expectedEvidence", "requirements")}
                   for r, c in checks.items()},
        "Final observations": observations,
        "Crossed contracts": [{"kind": c["kind"], "text": c["text"]}
                              for c in architecture.crossed_contracts(document, requirements).values()],
        "Source judgment": {"allSatisfied": all(v == "satisfied" for v in verdicts),
                            "anyNotJudged": "not judged" in verdicts, "scopeTreesUnchanged": True}}
    images = questions.images(store, refs)
    question = {
        "kind": "FLOW",
        "prompt": "Does this feature's observed user flow meet each requirement across its contracts? "
                  "Judge the final observations and screenshots. " + questions.JUDGMENT_LEEWAY,
        "facts": facts, "targets": list(requirements), "schema": answer_schema("DEMO", requirements),
        "examples": [{"answer": {"verdict": "satisfied", "gaps": [],
                     "assessments": questions.assessments(requirements, final=True),
                     "why": "The observed flow meets its acceptance across the supplied contracts"}}],
        "execution": {}, "images": images}
    if resolve is not None:
        original_bytes = len(encode(question).encode("utf-8"))
        facts["Requested facts"] = [{"request": item, "observations": list(checks), "screenshots": images}
                                    for item in resolve]
        if len(encode(question).encode("utf-8")) > 2 * original_bytes:
            raise questions.MissingFacts("Requested facts exceed twice the original flow brief")
    return question


def validate(answer, targets):
    if answer["verdict"] != "uncertain":
        if sorted(a["target"] for a in answer["assessments"]) != sorted(targets):
            raise ValueError("Assess every requirement of this feature exactly once")
        if any(g["target"] not in targets for g in answer["gaps"]):
            raise ValueError("A flow correction must name a requirement of this feature")


def review(store, feature, source, answer, *, reasked=False):
    """Consume one recorded FLOW answer; the caller owns dispatch, cursor updates and fixups."""
    cursor = store.cursor()
    requirements, _, _, trees, _ = evidence(store, feature)
    validate(answer, requirements)
    if answer["verdict"] == "uncertain":
        if not reasked:
            pending = store.record("judge_uncertain", {
                "subject": feature, "kind": "FLOW", "commit": cursor.candidate,
                "source": source, "answer": answer, "scopeTrees": trees,
                "questionBytes": len(encode(brief(store, feature)).encode("utf-8"))})
            return {"verdict": None, "pending": pending, "gaps": []}
        answer = {**answer, "verdict": "not judged", "assessments": [], "gaps": []}
    else:
        negatives = [a for a in answer["assessments"] if not (a["meaningful"]
                                                              and a["noStubsMocksMissing"])]
        corrections = answer["gaps"] + [{"target": a["target"], "correction": a["why"]}
                                         for a in negatives if a["target"] not in
                                         {g["target"] for g in answer["gaps"]}]
        answer = {**answer, "verdict": "gap" if corrections else "satisfied", "gaps": corrections}
    with store.transaction():
        verdict = store.record("feature_verdict", {
            "feature": feature, "commit": cursor.candidate, "answer": answer,
            "source": source, "scopeTrees": trees})
        gaps = ([{"subject": feature, "kind": "flow", "commit": cursor.candidate,
                  "units": list(trees), "correction": "; ".join(g["correction"] for g in answer["gaps"]),
                  "source": verdict}]
                if answer["verdict"] == "gap" else [])
    return {"verdict": verdict, "pending": None, "gaps": gaps}


def requirement_verdicts(store, verdicts):
    """AND all flows reaching a requirement, once, for canonical closure and C eligibility."""
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    features = {store.read(r)["feature"]: r for r in verdicts}
    if len(features) != len(verdicts) or set(features) != set(plan.select(document, "feature")):
        raise ValueError("Requirement closure needs one verdict for every feature")
    rows = []
    with store.transaction():
        for subject, requirement in plan.select(document, "requirement").items():
            sources = [features[f] for f in requirement["features"]]
            selected = [store.read(r) for r in sources]
            _, _, _, trees, _ = evidence(store, requirement["features"][0])
            answers = []
            for row in selected:
                if row["commit"] != cursor.candidate or not judgment.ready(row["answer"]):
                    raise ValueError("Requirement closure needs current satisfied or not judged flows")
                answer = row["answer"]
                answers.append({"answer": {**answer, "assessments": [
                    a for a in answer["assessments"] if a["target"] == subject]}})
            owned = plan.traced_units(document, plan.traced_checks(document, subject))
            body = {
                "subject": subject, "kind": "FLOW", "commit": cursor.candidate, "sources": sources,
                "scopeTrees": {u: trees[u] for u in owned}, "answer": judgment.combined(answers)}
            retained = next((r for r in cursor.requirementVerdicts if store.read(r) == body), None)
            rows.append(retained or store.record("subject_verdict", body))
    return rows


def step(store, provider):
    if not judgment.refresh_units(store, provider):
        return
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    trees = judgment.scope_trees(store, plan.select(document, "unit"))
    with store.transaction():
        refs = []
        for ref in cursor.demoVerdicts:
            row = store.read(ref)
            if judgment.current(row, trees):
                refs.append(ref if row["commit"] == cursor.candidate else store.record(
                    "feature_verdict", {**row, "commit": cursor.candidate, "reusedFrom": ref}))
        store.cursor(replace(cursor, demoVerdicts=refs))
    cursor = store.cursor()
    if cursor.pendingJudge:
        pending = store.read(cursor.pendingJudge)
        if (pending["kind"] != "FLOW" or pending["commit"] != cursor.candidate
                or not judgment.current(pending, trees)):
            raise ValueError("Pending FLOW facts require their original candidate and scope trees")
        feature = pending["subject"]
        try:
            question = brief(store, feature, resolve=pending["answer"]["neededFacts"])
            result, answer = inference.call(store, provider, question,
                check=lambda a: validate(a, question["targets"]), max_bytes=2 * pending["questionBytes"])
        except questions.MissingFacts as error:
            result = pending["source"]
            answer = {**pending["answer"], "why": pending["answer"]["why"] + "; " + str(error)}
        reviewed = review(store, feature, result, answer, reasked=True)
        if reviewed["gaps"]:
            fixups.apply(store, reviewed["gaps"])
            return
        advance(store, "resolved", result, demoVerdicts=cursor.demoVerdicts + [reviewed["verdict"]],
                pendingJudge=None)
        return
    done = {store.read(r)["feature"] for r in cursor.demoVerdicts}
    for feature in plan.select(document, "feature"):
        if feature in done:
            continue
        question = brief(store, feature)
        result, answer = inference.call(store, provider, question,
                                       check=lambda a: validate(a, question["targets"]))
        reviewed = review(store, feature, result, answer)
        if reviewed["gaps"]:
            fixups.apply(store, reviewed["gaps"])
            return
        if reviewed["pending"]:
            advance(store, "uncertain", result, pendingJudge=reviewed["pending"])
            return
        with store.transaction():
            cursor = store.cursor()
            store.cursor(replace(cursor, demoVerdicts=cursor.demoVerdicts + [reviewed["verdict"]]))
    cursor = store.cursor()
    closures = requirement_verdicts(store, cursor.demoVerdicts)
    advance(store, "satisfied", cursor.demoVerdicts, requirementVerdicts=closures)
