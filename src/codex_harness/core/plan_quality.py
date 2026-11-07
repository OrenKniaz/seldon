"""Read-only structural observations; semantic quality belongs to bound 1j reviews."""

from codex_harness.core.spine import SpineError, compile_manifest
from codex_harness.core.reviewer_evidence import review_tier


def quality_report(store):
    """Project canonical links and recorded judgments without checklist grades."""
    revision = store.manifest_revision()
    checks = []
    try:
        spine = store.spine()
        manifest = compile_manifest(spine)
        gaps = spine.coverage_gaps()
        checks.append(dict(id="spine", question="Are canonical trace links complete?",
            status="fail" if gaps else "pass", evidence=dict(gaps=gaps),
            advice="Repair the named trace links." if gaps else ""))
    except (SpineError, ValueError) as error:
        manifest = None
        checks.append(dict(id="spine", question="Is the plan structurally compilable?",
            status="fail", evidence=dict(error=str(error)), advice=str(error)))
    prerequisite = store.review_prerequisite(revision)
    reviews = prerequisite["reviews"]
    checks.append(dict(id="roundtable", question="Are current independent plan verdicts positive?",
        status="pass" if prerequisite["complete"] else "warn",
        evidence=dict(reviewIds=[row["id"] for row in reviews], reviewTier=review_tier(reviews) if reviews else None),
        advice="" if prerequisite["complete"] else "Obtain explicit bound verdicts from the selected reviewers."))
    checks.append(dict(id="semantic-quality", question="Does the plan deliver the whole dream?",
        status="not-yet", evidence=dict(judgmentStatus="pending-judgment" if not reviews else "recorded-review-verdicts",
            manifestRevision=revision, scenarioIds=[row["id"] for row in store.acceptance_scenarios()] if manifest else []),
        advice="Read each reviewer's rationale and concrete gaps; structure grants no semantic grade."))
    for check in checks:
        check.update(claim="structural observation" if check["id"] == "spine" else "recorded judgment",
                     evidenceStatus="planned")
    return dict(planRevision=store._revision(), manifestRevision=revision, score=None,
                checks=checks, summary="Structural observations and explicit semantic verdicts; no quality score")


def render_quality_card(report):
    lines = [report["summary"]]
    lines.extend(f"{check['question']}: {check['status']}. {check['advice']}" for check in report["checks"])
    return "\n".join(lines) + "\n"
