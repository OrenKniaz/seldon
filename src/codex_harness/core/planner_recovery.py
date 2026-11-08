"""Bounded worker recovery through the existing process owner, within B."""

import os
import sys
import time

from codex_harness.core import approvals
from codex_harness.core import planner_jobs as jobs
from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
from codex_harness.core.plan_types import _now
from codex_harness.core.planner_execution import _canonical_store
from codex_harness.core.record_io import (
    _record_guard,
    read_record_json,
    write_record_json,
    write_record_json_once,
)

RESOLVED = "worker-recovery-resolved"


def end_attempt(workspace, job_id):
    """Ask the held Job owner to stop; a lost owner quarantines the old namespace."""
    directory = jobs._directory(workspace, job_id)
    request = jobs._request(directory)
    with open_plan_store_readonly(workspace) as store:
        segment = jobs.latest_segment(store, job_id)
    write_record_json(directory / "stop.json", {"requestDigest": request["requestDigest"]})
    deadline = time.monotonic() + 12
    while not (segment / "quiescence.json").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    proof = inspect(workspace, job_id)
    path = directory / "recovery-verdict.json"
    with _record_guard(path):
        if not path.exists():
            write_record_json_once(
                path,
                dict(
                    requestDigest=request["requestDigest"],
                    verdict="VERIFIED-QUIESCENT"
                    if proof["verdict"] == "VERIFIED-QUIESCENT"
                    else "ABANDONED",
                    quarantine=True,
                ),
            )


def recovery_verdict(directory, request):
    path = directory / "recovery-verdict.json"
    record = read_record_json(path) if path.exists() else {}
    if record and record.get("requestDigest") != request["requestDigest"]:
        raise ValueError("worker-recovery-verdict-identity-mismatch")
    return record


def disposition(store, job_id):
    return next((r for r in approvals.records(store, RESOLVED) if r["jobId"] == job_id), None)


def attempt_proof(store, job_id):
    """Existing admission records own non-worker lifetimes too; no second store."""
    kind, identifier = job_id.split(":", 1)
    if kind == "review":
        from codex_harness.core.planner_review import status

        attempt = status(store, identifier)
        if attempt is None:
            from codex_harness.core.acceptance_recovery import STARTED

            header = next(
                (r for r in approvals.records(store, STARTED) if r["attemptId"] == identifier), None
            )
            if header:
                completed = store.connection.execute(
                    "SELECT 1 FROM acceptance_reviews WHERE id=?", (identifier,)
                ).fetchone()
                attempt = dict(id=identifier, state="completed" if completed else "running")
    elif kind in {"audit", "acceptance", "plan-review"}:
        table = "review" if kind == "plan-review" else kind
        row = store.connection.execute(
            f"SELECT id,state,error FROM {table}_attempts WHERE id=?", (identifier,)
        ).fetchone()
        attempt = dict(row) if row else None
    else:
        raise ValueError("recovery-attempt-kind-unknown")
    if attempt is None:
        raise ValueError("recovery-attempt-unknown")
    return dict(
        jobId=job_id,
        attempt=attempt,
        verdict="UNKNOWN",
        ownershipReleased=bool(disposition(store, job_id)),
        quiescenceVerified=False,
        options=["abandon"],
    )


def release_attempt(store, job_id, reason):
    """Release logical ownership, quarantine late results, never attest process exit."""
    kind, identifier = job_id.split(":", 1)
    proof = attempt_proof(store, job_id)
    if previous := disposition(store, job_id):
        return previous
    if proof["attempt"]["state"] in {"completed", "stale", "stopped", "superseded"}:
        raise ValueError("recovery-attempt-already-terminal")
    if kind == "review":
        from codex_harness.core.acceptance_recovery import ABANDONED
        from codex_harness.core.planner_review import UPDATED

        store._event(ABANDONED, dict(attemptId=identifier, reason=reason))
        store._event(
            UPDATED, dict(attemptId=identifier, state="abandoned", result=None, error=reason)
        )
    else:
        if kind == "acceptance":
            (store.workspace / ".codex-harness" / f"acceptance-stop-{identifier}").touch()
        table = "review" if kind == "plan-review" else kind
        store.connection.execute(
            f"UPDATE {table}_attempts SET state='abandoned',error=? WHERE id=?",
            (reason, identifier),
        )
    resolved = dict(
        jobId=job_id,
        choice="abandon",
        reason=reason,
        proof=proof,
        quarantine=True,
        ownershipReleased=True,
        quiescenceVerified=False,
        successor=None,
    )
    store._event(RESOLVED, resolved)
    return resolved


def worker_status(workspace, result):
    with open_plan_store_readonly(workspace) as store:
        resolved = disposition(store, result["jobId"])
    if not resolved:
        directory = jobs._directory(workspace, result["jobId"])
        if recovery_verdict(directory, jobs._request(directory)):
            result.update(
                status="failed",
                reason="Worker ended; the run loop owns readmission within the same B.",
            )
    if resolved:
        state = {
            "abandon": "abandoned",
            "readmit": "readmitted",
            "revalidate": "revalidated",
            "steer": "steering-required",
        }[resolved["choice"]]
        if resolved["choice"] == "revalidate":
            validation = resolved.get("validation", {})
            state = (
                "completed"
                if validation.get("state") == "PASS"
                else validation["state"]
                if validation.get("state") in {"pending-judgment", "pending-build"}
                else "failed"
                if validation
                else "revalidating"
            )
        result.update(status=state, recovery=resolved, ownershipReleased=resolved["ownershipReleased"])
        if resolved["choice"] == "revalidate":
            result["reason"] = "Inspect recovery.validation for retained revalidation and delivery."
    return result


def admission_id(store, publication):
    job_id = publication
    while resolved := disposition(store, job_id):
        if resolved["choice"] == "abandon":
            raise ValueError("recovery-abandoned-terminal: evidence and quarantine retained")
        if resolved["choice"] in {"revalidate", "steer"}:
            return job_id
        job_id = resolved["successor"]
    return job_id


def inspect(workspace, job_id):
    if ":" in job_id:
        with open_plan_store_readonly(workspace) as store:
            return attempt_proof(store, job_id)
    directory = jobs._directory(workspace, job_id)
    request = jobs._request(directory)
    with open_plan_store_readonly(workspace) as store:
        segment = jobs.latest_segment(store, job_id)
    receipt_path = jobs._inside(directory, segment / "quiescence.json")
    receipt = read_record_json(receipt_path) if receipt_path.is_file() else {}
    control = receipt.get("control", {})
    terminal_path = segment / "terminal.json"
    terminal = read_record_json(terminal_path) if terminal_path.is_file() else {}
    verified = jobs.verified_segment_exit(request, segment.name, terminal, receipt)

    verdict = recovery_verdict(directory, request)
    proof = dict(
        jobId=job_id,
        publicationRevision=request["publicationRevision"],
        requestDigest=request["requestDigest"],
        verdict=verdict.get("verdict", "VERIFIED-QUIESCENT" if verified else "UNKNOWN"),
        controllerAndChildren=control or {"liveness": "UNKNOWN"},
        ownedDirectories=[
            dict(
                path=str(directory),
                state="quarantined" if (directory / "stop.json").exists() else "retained",
            )
        ],
    )
    admitted = read_record_json(segment / "request.json")
    outcomes = terminal.get("outcomes") or jobs.segment_outcomes(directory, admitted)
    proof["faultBeads"] = [key for key, outcome in outcomes.items() if outcome["fault"]]
    with open_plan_store_readonly(workspace) as store:
        repair = next(
            (r for r in approvals.records(store, "run-repair-required") if r["jobId"] == job_id),
            None,
        )
    manifest = read_record_json(directory / "plan/sample.json")
    if repair:
        proof["faultBeads"] = sorted(
            set(proof["faultBeads"])
            | {
                key
                for key, bead in manifest["beads"].items()
                if bead.get("semanticPhase") == "build"
            }
        )
        proof["repairObservation"] = repair
    proof["faultOutcomes"] = {key: outcomes[key]["workStatus"] for key in proof["faultBeads"]}
    proof["processOutcomes"] = {key: outcome["status"] for key, outcome in outcomes.items()}
    proof["faultWork"] = {key: outcomes[key]["workId"] for key in proof["faultBeads"]}
    held = jobs.dispatch.DENIAL_HELD_STATUSES | {"ambiguous launch"}
    proof["held"] = not proof["faultBeads"] or any(
        not outcomes[key]["launchCertain"] or outcomes[key]["status"] in held
        or outcomes[key].get("workStatus") == jobs.dispatch.STATUS_PROVENANCE_HELD
        or any(
            outcomes[key].get("permissionGuard", {}).get(flag)
            for flag in ("requiresUserDisposition", "requiresAuditHold"))
        for key in outcomes
    )
    from codex_harness.core.planner_candidate import retained

    try:
        proof["revalidation"] = dict(available=True, **retained(workspace, job_id))
    except (ValueError, OSError, KeyError) as error:
        proof["revalidation"] = dict(
            available=False,
            reason=str(error),
            nextAction="Readmit a fresh attempt within B.",
        )
    proof["options"] = ["abandon", "readmit"] + (
        ["revalidate"] if verified and proof["revalidation"]["available"] else []
    )
    with open_plan_store_readonly(workspace) as store:
        proof["disposition"] = disposition(store, job_id)
        from codex_harness.core.planner_candidate import current_job_run

        refresh_required = not current_job_run(store)[0]["complete"]
    if resolved := proof["disposition"]:
        retryable = refresh_required or resolved.get("validation", {}).get("state") in {
            "FAIL",
            "ERROR",
            "pending-judgment",
        }
        proof["options"] = ["revalidate"] if retryable and "revalidate" in proof["options"] else []
    proof["proofDigest"] = jobs._digest(proof)
    return proof


def resolve(store, job_id, choice, reason, *, direction_judgment_id=None):
    """Same-work recovery ends or quarantines the old attempt under current B."""
    _canonical_store(store)
    if (
        choice not in {"abandon", "readmit", "revalidate", "steer"}
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValueError("recovery-choice-and-reason-required")
    with store._transaction():
        if choice == "revalidate":
            from codex_harness.core.run_ownership import ensure_effect

            ensure_effect(store)
        if ":" in job_id:
            if choice != "abandon":
                raise ValueError("uncertain-attempt-requires-abandon: no automatic replay")
            return release_attempt(store, job_id, reason)
        if choice in {"readmit", "revalidate"}:
            approvals.require_approval(store, "B")
        if choice in {"readmit", "abandon", "steer"} and not disposition(store, job_id):
            end_attempt(store.workspace, job_id)
        proof = inspect(store.workspace, job_id)
        previous = proof["disposition"]
        steered = previous and previous["choice"] == "steer" and choice == "readmit"
        if steered:
            judgment = store.judgment(direction_judgment_id) if direction_judgment_id else {}
            captured = next(
                (
                    r["payload"]
                    for r in judgment.get("sourceRefs", [])
                    if r["kind"] == "loop-question"
                ),
                {},
            )
            if (
                not judgment.get("positiveCredit")
                or judgment["meaning"].get("option") not in {"continue", "repair", "replan"}
                or captured.get("context", {}).get("jobId") != job_id
            ):
                raise ValueError(
                    "recovery-steering-pending: answer the run loop's bound direction question"
                )
        if proof["disposition"] and not (
            steered or choice == "revalidate" and "revalidate" in proof["options"]
        ):
            raise ValueError(
                "recovery-already-resolved: inspect disposition and original run evidence"
            )
        if choice == "revalidate":
            if proof["verdict"] != "VERIFIED-QUIESCENT":
                raise ValueError(
                    "revalidate requires a quiescent retained candidate; readmit instead"
                )
            if not proof["revalidation"]["available"]:
                raise ValueError(proof["revalidation"]["reason"])
        # Derive the bound from the existing successor chain, never a competing
        # retry counter. The third fault requests semantics through 1j.
        prior_recoveries = {
            r["jobId"]: r
            for r in reversed(approvals.records(store, RESOLVED))
            if r["choice"] == "readmit" and not r.get("steeringJudgmentId")
        }
        recoveries = {
            work: sum(
                work in prior["proof"].get("faultWork", {}).values()
                for prior in prior_recoveries.values()
            )
            for work in proof["faultWork"].values()
        }
        if choice == "readmit" and proof["held"]:
            choice = "abandon"
        elif choice == "readmit" and not steered and any(c >= 2 for c in recoveries.values()):
            choice = "steer"
        resolved = dict(
            jobId=job_id,
            choice=choice,
            reason=reason,
            proof=proof,
            quarantine=choice != "revalidate",
            ownershipReleased=choice != "revalidate",
            quiescenceVerified=proof["verdict"] == "VERIFIED-QUIESCENT",
            successor=jobs._digest(dict(jobId=job_id, proof=proof["proofDigest"], choice=choice))
            if choice == "readmit"
            else None,
        )
        if steered:
            resolved["steeringJudgmentId"] = judgment["id"]
        if choice == "steer":
            store._event(
                "worker-recovery-steering-required",
                dict(
                    jobId=job_id,
                    proof=proof,
                    automaticRecoveriesExhausted=any(count >= 2 for count in recoveries.values()),
                ),
            )
        store._event(RESOLVED, resolved)
    return resolved


def record_validation(store, resolved, validation):
    from codex_harness.core.run_ownership import ensure_effect

    with store._transaction():
        ensure_effect(store)
        resolved["validation"] = validation
        store._event(RESOLVED, resolved)


def monitor(workspace, job_id, segment_id):
    """Existing bounded Job owner; the run loop alone consumes its terminal event."""
    from datetime import UTC, datetime
    from codex_harness.core.audit_process import run_audit_process
    from codex_harness.core.run_loop import run_loop

    directory = jobs._directory(workspace, job_id)
    path = jobs.segment_directory(workspace, job_id, segment_id)
    request, segment = jobs._request(directory), read_record_json(path / "request.json")
    if request.get("containment") != "windows-job-monitor-v1":
        raise ValueError("recovery-legacy-job-cannot-acquire-retrospective-containment")
    temp = path / "temp"
    temp.mkdir()
    payload = (
        "import sys\nfrom codex_harness.core.planner_jobs import execute_worker_job\n"
        "result = execute_worker_job(sys.argv[1], sys.argv[2], sys.argv[3])\n"
        "raise SystemExit(1 if result['status'] == 'failed' else 0)\n"
    )
    command = [sys.executable, "-P", "-B", "-c", payload, str(workspace), job_id, segment_id]
    remaining = max(
        0.01, (datetime.fromisoformat(segment["deadlineAt"]) - datetime.now(UTC)).total_seconds()
    )
    try:
        result = run_audit_process(
            command,
            cwd=workspace,
            capture_output=False,
            timeout=remaining,
            env={**os.environ, **dict.fromkeys(("TEMP", "TMP", "TMPDIR"), str(temp))},
            stop_when=lambda: "recovery-requested" if (directory / "stop.json").exists() else None,
        )
        control, code = getattr(result, "audit_control", {}), result.returncode
    except (OSError, jobs.subprocess.SubprocessError) as error:
        control, code = {**getattr(error, "audit_control", {}), "error": str(error)}, 1
    receipt = dict(requestDigest=request["requestDigest"], segmentId=segment_id, control=control)
    if not (path / "terminal.json").exists():
        write_record_json_once(
            path / "terminal.json",
            dict(
                status="failed",
                reason="Payload exited without a terminal observation",
                exitCode=code,
                requestDigest=request["requestDigest"],
                segmentId=segment_id,
                at=_now(),
                outcomes=jobs.segment_outcomes(directory, segment),
            ),
        )
    # Quiescence releases recovery only after its canonical outcome is available.
    write_record_json_once(path / "quiescence.json", receipt)
    write_record_json(directory / "quiescence.json", receipt)
    if (path / "terminal.json").exists():
        write_record_json(directory / "terminal.json", read_record_json(path / "terminal.json"))
    with open_plan_store(workspace) as store:
        run_loop(store)
    return code
