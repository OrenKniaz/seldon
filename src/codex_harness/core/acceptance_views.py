"""Read-only acceptance status projection from canonical run and review owners."""
from __future__ import annotations

import json

from codex_harness.core import acceptance_recovery as recovery
from codex_harness.core.acceptance_runs import INVALID_MESSAGES, _latest_run, acceptance_next, require_c_evidence, run_invalid_reason, run_subject
from codex_harness.core.plan_store import PlanStoreError
from codex_harness.core.planner_candidate import current_job_run

def acceptance_status(store) -> dict:
    """Read acceptance and its candidate prerequisite without turning a view into a gate."""
    data = store._data()
    candidate = current_job_run(store)[0] if data["nodes"] and store.spine().semantic else dict(complete=True)
    previous_run = _latest_run(store)
    invalid_reason = run_invalid_reason(store, previous_run)
    run = previous_run if invalid_reason is None else None
    snapshot = getattr(store, "_review_read_snapshot", None)
    if snapshot is not None:
        snapshot["acceptanceRun"] = run
    findings = store.acceptance_findings()
    disposition = store.finding_state()["dispositionPrerequisite"]
    approval = store.connection.execute(
        "SELECT result_json FROM acceptance_approvals ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    approval = json.loads(approval[0]) if approval else None
    reviews = [
        json.loads(row[0])
        for row in store.connection.execute(
            "SELECT result_json FROM acceptance_reviews ORDER BY rowid"
        )
    ]
    invalid_reason = invalid_reason if candidate["complete"] else "retained-candidate-required"
    if invalid_reason is None:
        if run_subject(run) != "product":
            invalid_reason = "acceptance-product-required"
        else:
            try:
                require_c_evidence(store, run)
            except PlanStoreError as error:
                invalid_reason = error.code
            if invalid_reason is None:
                consent = store.judgment(approval["judgmentId"]) if approval and approval.get("judgmentId") else {}
                if (not approval or approval["runId"] != run["id"] or not consent.get("positiveCredit")
                    or not any(ref["payload"].get("facts", {}).get("subject", {}).get("run", {}).get("id") == run["id"]
                               for ref in consent.get("sourceRefs", []))):
                    invalid_reason = "acceptance-approval-required"
    valid = invalid_reason is None
    results = {item["id"]: item for item in run["results"]} if run else {}
    scenarios = [
        dict(
            **{k: s[k] for k in s if k in "executable evidenceTier validationMessage".split()},
            id=s["id"],
            title=s["title"],
            steps=s["steps"],
            lastResult=("pass" if results[s["id"]]["passed"] else "fail")
            if s["id"] in results
            else "not-run (stale)"
            if previous_run
            else "not-run",
            lastResultSubject=run_subject(run) if s["id"] in results else None,
            lastResultNote=(
                "scenarios verified against the reference implementation (dry run)"
                if run_subject(run) == "oracle" and results[s["id"]]["passed"]
                else f"{run_subject(run)} scenario result"
            )
            if s["id"] in results
            else None,
            runId=run["id"] if s["id"] in results else None,
            stepResults=results[s["id"]]["steps"] if s["id"] in results else [],
            error=results[s["id"]].get("error") if s["id"] in results else None,
        )
        for s in store.acceptance_scenarios()
    ]
    return {
        "candidatePrerequisite": candidate,
        "scenarios": scenarios,
        "lastRunSubject": run_subject(previous_run) if previous_run else None,
        "findings": findings,
        "approvedBy": approval["by"] if valid else None,
        "isProxy": approval["isProxy"] if valid else None,
        "accepted": valid,
        "invalidReason": invalid_reason,
        "invalidMessage": (candidate["reason"] if not candidate["complete"] else disposition["nextAction"]["message"]
                           if invalid_reason == "pending-judgment" and not disposition["complete"]
                           else INVALID_MESSAGES.get(invalid_reason)),
        "repo": previous_run["repo"] if previous_run else None,
        "next": candidate["next"] if not candidate["complete"] else (disposition["nextAction"]["message"]
                 if disposition["nextAction"] is not None
                 else acceptance_next(invalid_reason, previous_run["repo"] if previous_run else None))
        if invalid_reason
        else "harness_plan_status",
        "approval": approval,
        "reviews": reviews,
        "reviewAttempts": recovery.attempt_summaries(store),
        "executionAssessments": store.execution_assessment_state(),
        "approvalOrigin": "recorded-unverified" if approval else None,
        "humanOriginVerified": False,
        "recordedUserAccepted": valid and not approval["isProxy"],
        # Deprecated alias for one release; this does not verify human origin.
        "realUserAccepted": valid and not approval["isProxy"],
    }
