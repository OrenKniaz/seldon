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
STALL_SECONDS = 120


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
    description="Inspect workers or review:<attemptId>; resolve checks machine prerequisites.",
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
        state = {"abandon": "abandoned", "readmit": "readmitted", "revalidate": "revalidated"}[
            resolved["choice"]
        ]
        if resolved["choice"] == "revalidate":
            validation = resolved.get("validation", {})
            state = (
                "completed"
                if validation.get("state") == "PASS"
                else "failed"
                if validation
                else "revalidating"
            )
        result.update(status=state, recovery=resolved)
        if resolved["choice"] == "revalidate":
            result["reason"] = "Inspect recovery.validation for retained revalidation and delivery."
    return result


def admission_id(store, publication):
    job_id = publication
    while resolved := disposition(store, job_id):
        if resolved["choice"] == "abandon":
            raise ValueError("recovery-abandoned-terminal: evidence and quarantine retained")
        if resolved["choice"] == "revalidate":
            return job_id
        job_id = resolved["successor"]
    return job_id


def inspect(workspace, job_id):
    if job_id.startswith("review:"):
        from codex_harness.core.planner_review import recovery_proof

        return recovery_proof(workspace, job_id)
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
        retryable = resolved.get("validation", {}).get("state") in {"FAIL", "ERROR"}
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
        if choice != "abandon":
            approvals.require_approval(store, "B")
        if choice in {"readmit", "abandon"} and not disposition(store, job_id):
            end_attempt(store.workspace, job_id)
        proof = inspect(store.workspace, job_id)
        if proof["disposition"] and not (
            choice == "revalidate" and "revalidate" in proof["options"]
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
        "raise SystemExit(0 if result['status'] == 'completed' else 1)\n"
    )
    command = [sys.executable, "-P", "-B", "-c", payload, str(workspace), job_id]
    last_progress = next_check = time.monotonic()
    previous = None

    def stop_when():
        nonlocal last_progress, next_check, previous
        now = time.monotonic()
        if now < next_check:
            return None
        next_check = now + 0.2
        if (directory / "stop.json").exists():
            return "recovery-requested"
        # Worker events and verification results count as progress; status reads do not.
        paths = directory.glob("runs/*/evidence/**/*.json*")
        observed = [(str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(paths)]
        if observed != previous:
            previous, last_progress = observed, now
        if now - last_progress >= min(STALL_SECONDS, request["options"]["timeout"]):
            return "worker-stalled"
        return None

    try:
        result = run_audit_process(
            command,
            cwd=workspace,
            capture_output=False,
            timeout=request["options"]["timeout"] * request["options"]["callCap"] + 600,
            env={**os.environ, **dict.fromkeys(("TEMP", "TMP", "TMPDIR"), str(temp))},
            stop_when=stop_when,
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
