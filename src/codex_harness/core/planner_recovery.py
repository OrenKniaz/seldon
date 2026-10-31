"""Explicit worker disposition; missing containment originals remain UNKNOWN."""

import json
import os
import sys
from uuid import uuid4

from codex_harness.core import approvals
from codex_harness.core import planner_jobs as jobs
from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
from codex_harness.core.planner_execution import _canonical_store
from codex_harness.core.provider_call_budget import usage
from codex_harness.core.record_io import read_record_json, write_record_json_once

RESOLVED = "worker-recovery-resolved"
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
            result["reason"] = (
                "Retained candidate revalidation "
                + resolved.get("validation", {}).get("state", "started")
                + "; inspect recovery.validation for results and delivery path. No worker launched."
            )
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
    observations = {}
    for path in [
        directory / "launch.json",
        directory / "started.json",
        *directory.glob("runs/*/evidence/**/attempt/request.json"),
    ]:
        if path.is_file():
            record = read_record_json(jobs._inside(directory, path))
            observations[str(path.relative_to(directory))] = record.get(
                "pid", record.get("processId")
            )
    verified = (
        request.get("containment") == "windows-job-monitor-v1"
        and receipt.get("requestDigest") == request["requestDigest"]
        and control.get("backend") == "windows-job"
        and control.get("membershipVerifiedBeforePayload") is True
        and control.get("quiescenceVerified") is True
        and control.get("activeProcessesAfterTeardown") == 0
    )
    roots = [directory / "temp", *sorted((directory / "runs").glob("*/*"))]
    owned = []
    for root in roots:
        try:
            path = jobs._inside(directory, root)
            owned.append(dict(path=str(path), state="retained" if path.exists() else "absent"))
        except (OSError, ValueError) as error:
            owned.append(dict(path=str(root), state="inaccessible", reason=str(error)))
    with open_plan_store_readonly(workspace) as store:
        spent = usage(store, "worker")
    proof = dict(
        jobId=job_id,
        publicationRevision=request["publicationRevision"],
        requestDigest=request["requestDigest"],
        verdict="VERIFIED-QUIESCENT" if verified else "UNKNOWN",
        reasons=[]
        if verified
        else ["No bound, complete controller/descendant Job teardown receipt"],
        controllerAndChildren=control or {"liveness": "UNKNOWN"},
        recordedPids=observations,
        ownedDirectories=owned,
        tempOwnership="job-local" if receipt else "legacy temp roots unknown",
        spentCalls=spent,
        remainingCalls=max(0, request["options"]["callCap"] - spent),
    )
    from codex_harness.core.planner_candidate import retained

    try:
        proof["revalidation"] = dict(available=True, **retained(workspace, job_id))
    except (ValueError, OSError, KeyError) as error:
        proof["revalidation"] = dict(
            available=False,
            reason=str(error),
            nextAction="Inspect run evidence; finish Build before readmission.",
        )
    proof["options"] = (
        ["abandon"]
        + (["readmit"] if verified else [])
        + (["revalidate"] if verified and proof["revalidation"]["available"] else [])
    )
    with open_plan_store_readonly(workspace) as store:
        proof["disposition"] = disposition(store, job_id)
    if resolved := proof["disposition"]:
        retryable = (
            resolved["choice"] == "readmit"
            and not jobs._directory(workspace, resolved["successor"]).exists()
        ) or resolved.get("validation", {}).get("state") in {"FAIL", "ERROR"}
        proof["options"] = ["revalidate"] if retryable and "revalidate" in proof["options"] else []
    proof["proofDigest"] = jobs._digest(proof)
    return proof


def resolve(store, job_id, choice, reason):
    """Same-work recovery uses current A/B; UNKNOWN never permits a retry."""
    _canonical_store(store)
    if (
        choice not in {"abandon", "readmit", "revalidate"}
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValueError("recovery-choice-and-reason-required")
    with store._transaction():
        proof = inspect(store.workspace, job_id)
        if proof["disposition"] and not (
            choice == "revalidate" and "revalidate" in proof["options"]
        ):
            raise ValueError(
                "recovery-already-resolved: inspect disposition and original run evidence"
            )
        if choice != "abandon":
            if proof["verdict"] != "VERIFIED-QUIESCENT":
                raise ValueError("recovery-readmit-requires-VERIFIED-QUIESCENT; UNKNOWN stays held")
            approvals.require_approval(store, "B")
        if choice == "revalidate":
            if not proof["revalidation"]["available"]:
                raise ValueError(proof["revalidation"]["reason"])
            prior = proof["disposition"]
            if (
                prior
                and prior.get("successor")
                and jobs._directory(store.workspace, prior["successor"]).exists()
            ):
                raise ValueError(
                    "revalidate: successor already admitted; inspect successor instead"
                )
        resolved = dict(
            jobId=job_id,
            choice=choice,
            reason=reason,
            proof=proof,
            quarantine=proof["verdict"] == "UNKNOWN",
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
    try:
        result = run_audit_process(
            command,
            cwd=workspace,
            capture_output=False,
            timeout=request["options"]["timeout"] * request["options"]["callCap"] + 600,
            env={**os.environ, **dict.fromkeys(("TEMP", "TMP", "TMPDIR"), str(temp))},
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
