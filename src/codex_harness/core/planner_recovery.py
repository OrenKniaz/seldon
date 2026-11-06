"""Bounded worker recovery through the existing process owner, within B."""

import json
import os
import sys
import time
from uuid import uuid4

from codex_harness.core import approvals
from codex_harness.core import planner_jobs as jobs
from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
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
    write_record_json(directory / "stop.json", {"requestDigest": request["requestDigest"]})
    deadline = time.monotonic() + 12
    while not (directory / "quiescence.json").exists() and time.monotonic() < deadline:
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


TOOL = dict(
    name="harness_recovery",
    description="Inspect workers or review:/audit:/acceptance:<attemptId>; release quarantines uncertain owners without claiming process exit.",
    inputSchema=dict(
        type="object",
        properties={
            "mode": {"type": "string", "enum": ["inspect", "resolve"]},
            "job_id": {"type": "string"},
            "choice": {"type": "string", "enum": ["abandon", "readmit", "revalidate"]},
            "reason": {"type": "string"},
        },
        required=["mode", "job_id"],
        additionalProperties=False,
    ),
)


def disposition(store, job_id):
    return next((r for r in approvals.records(store, RESOLVED) if r["jobId"] == job_id), None)


def attempt_proof(store, job_id):
    """Existing admission records own non-worker lifetimes too; no second store."""
    kind, identifier = job_id.split(":", 1)
    if kind == "review":
        from codex_harness.core.planner_review import status

        attempt = status(store, identifier)
    elif kind in {"audit", "acceptance"}:
        row = store.connection.execute(
            f"SELECT id,state,error FROM {kind}_attempts WHERE id=?", (identifier,)
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
        store.connection.execute(
            f"UPDATE {kind}_attempts SET state='abandoned',error=? WHERE id=?",
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
                reason="Worker ended; readmit within the same B via harness_recovery.",
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
        result.update(status=state, recovery=resolved, ownershipReleased=True)
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
    receipt_path = jobs._inside(directory, directory / "quiescence.json")
    receipt = read_record_json(receipt_path) if receipt_path.is_file() else {}
    control = receipt.get("control", {})
    verified = (
        request.get("containment") == "windows-job-monitor-v1"
        and receipt.get("requestDigest") == request["requestDigest"]
        and control.get("backend") == "windows-job"
        and control.get("membershipVerifiedBeforePayload") is True
        and control.get("quiescenceVerified") is True
        and control.get("activeProcessesAfterTeardown") == 0
    )
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
    paths = list(directory.glob("runs/*/evidence/run.json"))
    run = read_record_json(jobs._inside(directory, paths[0])) if len(paths) == 1 else {}
    proof["faultBeads"] = [
        key
        for key, bead in run.get("beads", {}).items()
        if bead.get("attempt")
        and bead["status"]
        not in {
            "contracts-red",
            "requirement-validated",
            jobs.dispatch.STATUS_MOCK_ONLY,
            "already-satisfied",
            "reused",
        }
    ]
    proof["faultOutcomes"] = {key: run["beads"][key]["status"] for key in proof["faultBeads"]}
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
    if resolved := proof["disposition"]:
        retryable = resolved.get("validation", {}).get("state") in {
            "FAIL",
            "ERROR",
            "pending-judgment",
        }
        proof["options"] = ["revalidate"] if retryable and "revalidate" in proof["options"] else []
    proof["proofDigest"] = jobs._digest(proof)
    return proof


def resolve(store, job_id, choice, reason):
    """Same-work recovery ends or quarantines the old attempt under current B."""
    _canonical_store(store)
    if (
        choice not in {"abandon", "readmit", "revalidate"}
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValueError("recovery-choice-and-reason-required")
    with store._transaction():
        if ":" in job_id:
            if choice != "abandon":
                raise ValueError("uncertain-attempt-requires-abandon: no automatic replay")
            return release_attempt(store, job_id, reason)
        if choice != "abandon":
            approvals.require_approval(store, "B")
        if choice in {"readmit", "abandon"} and not disposition(store, job_id):
            end_attempt(store.workspace, job_id)
        proof = inspect(store.workspace, job_id)
        previous = proof["disposition"]
        steered = previous and previous["choice"] == "steer" and choice == "readmit"
        if steered:
            judgment = store.judgment(previous["judgment"]["id"], candidate=proof["requestDigest"])
            if not judgment["positiveCredit"] or judgment["meaning"].get("action") != "readmit":
                raise ValueError("recovery-steering-pending: submit the bound 1j direction first")
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
        cursor = jobs._request(jobs._directory(store.workspace, job_id))["publicationRevision"]
        recoveries = dict.fromkeys(proof["faultBeads"] or [None], 0)
        while cursor != job_id:
            prior = disposition(store, cursor)
            if not prior or prior["choice"] != "readmit":
                raise ValueError("recovery-job-not-in-publication-chain")
            for bead in recoveries.keys() & set(prior["proof"].get("faultBeads", []) or [None]):
                recoveries[bead] += 1
            cursor = prior["successor"]
        if choice == "readmit" and any(count >= 2 for count in recoveries.values()) and not steered:
            choice = "steer"
        resolved = dict(
            jobId=job_id,
            choice=choice,
            reason=reason,
            proof=proof,
            quarantine=choice != "revalidate",
            successor=jobs._digest(dict(jobId=job_id, proof=proof["proofDigest"], choice=choice))
            if choice == "readmit"
            else None,
        )
        if steered:
            resolved["steeringJudgmentId"] = judgment["id"]
        if choice == "steer":
            store._event("worker-recovery-exhausted", dict(jobId=job_id, proof=proof))
            event_id = store.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            resolved["judgment"] = store.prepare_judgment(
                question="Two automatic bead recoveries failed. What is the next safe direction within the accepted vision? Record steering; scope changes require the user.",
                subject_ids=store.spine().ids("vision"),
                source_event_ids=[event_id],
                candidate=proof["requestDigest"],
            )
        store._event(RESOLVED, resolved)
    if choice == "revalidate":
        from codex_harness.core.planner_candidate import revalidate

        try:
            resolved["validation"] = revalidate(store, job_id, uuid4().hex)
        except (ValueError, OSError, jobs.subprocess.SubprocessError) as error:
            resolved["validation"] = dict(
                state="ERROR", results=[dict(reason=str(error))], delivery=None
            )
        store._event(RESOLVED, resolved)
    return resolved


def monitor(workspace, job_id):
    """Existing Job owner gates the controller and covers its ordinary descendants."""
    from codex_harness.core.audit_process import run_audit_process

    directory = jobs._directory(workspace, job_id)
    request = jobs._request(directory)
    if request.get("containment") != "windows-job-monitor-v1":
        raise ValueError("recovery-legacy-job-cannot-acquire-retrospective-containment")
    write_record_json_once(directory / "monitor.json", {"requestDigest": request["requestDigest"]})
    temp = directory / "temp"
    temp.mkdir()
    payload = (
        "import sys\n"
        "from codex_harness.core.planner_jobs import execute_worker_job\n"
        "result = execute_worker_job(sys.argv[1], sys.argv[2])\n"
        "raise SystemExit(1 if result['status'] == 'failed' else 0)\n"
    )
    command = [sys.executable, "-P", "-B", "-c", payload, str(workspace), job_id]
    try:
        result = run_audit_process(
            command,
            cwd=workspace,
            capture_output=False,
            timeout=request["options"]["timeout"] * (len(request["options"]["workers"]) + 1) + 600,
            env={**os.environ, **dict.fromkeys(("TEMP", "TMP", "TMPDIR"), str(temp))},
            stop_when=lambda: "recovery-requested" if (directory / "stop.json").exists() else None,
            stall_seconds=None,  # Each provider owns its idle/deadline clock; this is the batch supervisor.
        )
        control = getattr(result, "audit_control", {})
        code = result.returncode
    except (OSError, jobs.subprocess.SubprocessError) as error:
        control = {**getattr(error, "audit_control", {}), "error": str(error)}
        code = 1
    write_record_json_once(
        directory / "quiescence.json",
        {"requestDigest": request["requestDigest"], "control": control},
    )
    if control.get("stopReason") or not control.get("quiescenceVerified"):
        end_attempt(workspace, job_id)
    return code


def reply(workspace, arguments):
    """Bypass status/drain/close paths, including invalid inspection calls."""
    try:
        args = dict(arguments)
        mode = args.pop("mode")
        if mode == "inspect" and set(args) == {"job_id"}:
            data = inspect(workspace, **args)
        elif mode == "resolve" and set(args) == {"job_id", "choice", "reason"}:
            with open_plan_store(workspace) as store:
                data = resolve(store, **args)
        else:
            raise ValueError("recovery-requires-inspect-or-resolve-with-job-choice-reason")
        return dict(
            isError=False,
            structuredContent=data,
            content=[dict(type="text", text=data.get("card", json.dumps(data)))],
        )
    except (ValueError, OSError, KeyError) as error:
        return dict(isError=True, content=[dict(type="text", text=str(error))])
