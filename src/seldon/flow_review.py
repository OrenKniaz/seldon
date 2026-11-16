"""C3/C9/C15; R2/R4/R7/R12: one feature's behaviour across its contracts, without repair."""

from dataclasses import replace

from . import architecture, fixups, inference, judgment, plan, proof, questions
from .schema import answer_schema
from .states import advance


def trace(document, feature):
    requirements = {r: b for r, b in plan.select(document, "requirement").items()
                    if feature in b["features"]}
    checks = {r: c for r, c in plan.select(document, "check").items()
              if set(requirements).intersection(c["requirements"])}
    return requirements, checks


def evidence(store, feature):
    cursor = store.cursor()
    document = plan.execution(store)
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
        verdicts.append({"unit": ref, "commit": verdict["commit"], **verdict["answer"]})
    return requirements, checks, refs, trees, verdicts


def row(body):
    return {k: body[k] for k in ("check", "requirements", "exitCode", "stdout", "stderr",
                                 "executionStarted", "cleanupVerified", "timedOut")}


def brief(store, feature):
    document = plan.execution(store)
    requirements, checks, refs, _, verdicts = evidence(store, feature)
    facts = {
        "Feature": {k: document["nodes"][feature]["body"][k] for k in ("story", "acceptance")},
        "Requirements": {r: {k: b[k] for k in ("acceptance",)}
                         for r, b in requirements.items()},
        "Checks": {r: {k: c[k] for k in ("purpose", "expectedEvidence", "requirements")}
                   for r, c in checks.items()},
        "Final observations": [row(store.read(r)) for r in refs],
        # Same fields as final rows: commands can carry check source, which is judged once at the unit.
        "Wave-start observations": [row(b) for _, _, b in store.records("observation")
            if b["purpose"] == "wave-start" and b["check"] in checks],
        "Crossed contracts": architecture.crossed_contracts(document, requirements),
        "Correction owners": {u: {k: plan.select(document, "unit")[u][k]
                                   for k in ("instructions", "scope", "requirements")}
                              for u in plan.traced_units(document, checks)},
        "Source judgment": verdicts}
    images = questions.images(store, refs)
    question = {
        "kind": "FLOW",
        "prompt": "Does this feature's observed user flow meet each requirement across its contracts? "
                  "Name only responsible correction owners for each gap; name a crossed contract "
                  "when its agreement needs correction. Judge the observations and screenshots. "
                  + questions.JUDGMENT_LEEWAY,
        "facts": facts, "targets": list(requirements), "schema": answer_schema(
            "FLOW", requirements, [*facts["Correction owners"], *facts["Crossed contracts"]]),
        "examples": [{"answer": {"verdict": "satisfied", "gaps": [],
                     "why": "The observed flow meets its acceptance across the supplied contracts"}}],
        "execution": {}, "images": images}
    return question


def review(store, feature, source, answer):
    """Consume one recorded FLOW answer; the caller owns dispatch, cursor updates and fixups."""
    cursor = store.cursor()
    requirements, _, refs, trees, _ = evidence(store, feature)
    if answer["verdict"] == "uncertain":
        answer = {**answer, "verdict": "not judged", "gaps": []}
    else:
        answer = {**answer, "verdict": "gap" if answer["gaps"] else "satisfied"}
    with store.transaction():
        verdict = store.record("feature_verdict", {
            "feature": feature, "commit": cursor.candidate, "answer": answer,
            "source": source, "scopeTrees": trees, "observations": refs})
        corrections = {}
        for gap in answer["gaps"]:
            for owner in gap["owners"]:
                corrections.setdefault(owner, []).append(gap["correction"])
        document = plan.execution(store)
        contracts = architecture.crossed_contracts(document, requirements)
        gaps = [{"subject": owner if owner in contracts else feature, "kind": "flow",
                 "commit": cursor.candidate,
                 "units": [contracts[owner]["provider"] if owner in contracts else owner],
                 "correction": "; ".join(items), "source": verdict}
                for owner, items in corrections.items()]
    return {"verdict": verdict, "gaps": gaps}


def requirement_verdicts(store, verdicts):
    """AND all flows reaching a requirement, once, for canonical closure and C eligibility."""
    cursor = store.cursor()
    document = plan.execution(store)
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
                answers.append({"answer": {**answer,
                    "verdict": ("not judged" if answer["verdict"] == "not judged" else
                                "gap" if any(g["target"] == subject for g in answer["gaps"])
                                else "satisfied"),
                    "gaps": [g for g in answer["gaps"] if g["target"] == subject]}})
            owned = plan.traced_units(document, plan.traced_checks(document, subject))
            body = {
                "subject": subject, "kind": "FLOW", "commit": cursor.candidate, "sources": sources,
                "scopeTrees": {u: trees[u] for u in owned}, "answer": judgment.combined(answers)}
            retained = next((r for r in cursor.requirementVerdicts if store.read(r) == body), None)
            rows.append(retained or store.record("subject_verdict", body))
    return rows


def step(store, provider):
    cursor = store.cursor()
    document = plan.execution(store)
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
    done = {store.read(r)["feature"] for r in cursor.demoVerdicts}
    selected = [f for f in plan.select(document, "feature") if f not in done]
    batch = {f: brief(store, f) for f in selected}
    replies = inference.batch(store, provider, batch)
    gaps, verdicts = [], list(cursor.demoVerdicts)
    with store.transaction():
        for feature, (result, answer) in replies.items():
            reviewed = review(store, feature, result, answer)
            gaps.extend(reviewed["gaps"])
            if not reviewed["gaps"]:
                verdicts.append(reviewed["verdict"])
        store.cursor(replace(store.cursor(), demoVerdicts=verdicts))
        if gaps:
            fixups.apply(store, gaps)
        else:
            closures = requirement_verdicts(store, verdicts)
            advance(store, "satisfied", verdicts, requirementVerdicts=closures)
