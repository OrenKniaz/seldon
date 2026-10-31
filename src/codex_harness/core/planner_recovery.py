"""Explicit worker disposition; missing containment originals remain UNKNOWN."""

import json
import os
import sys
from uuid import uuid4

from codex_harness.core import approvals
from codex_harness.core import planner_jobs as jobs
from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
from codex_harness.core.planner_confirmations import _chat_evidence
from codex_harness.core.planner_execution import _canonical_store
from codex_harness.core.provider_call_budget import usage
from codex_harness.core.record_io import read_record_json, write_record_json_once

REQUEST = "worker-recovery-requested"
RESOLVED = "worker-recovery-resolved"
TOOL = dict(
    name="harness_recovery",
    description="Inspect workers; resolve offers abandon/readmit/revalidate consent.",
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


def recovery_subject(store, action):
    return dict(
        version=2,
        kind="recovery",
        workspace=str(store.workspace.resolve()),
        effect=approvals.EFFECTS["recovery"],
        **action,
        proof=inspect(store.workspace, action["jobId"]),
    )


def prepare(store, job_id, choice, reason):
    _canonical_store(store)
    if (
        choice not in {"abandon", "readmit", "revalidate"}
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValueError("recovery-choice-and-reason-required")
    with store._transaction():
        action = dict(jobId=job_id, choice=choice, reason=reason)
        proposal = approvals.proposal(store, "recovery", action)
        proof = proposal["subject"]["proof"]
        if proof["disposition"] and not (
            choice == "revalidate" and "revalidate" in proof["options"]
        ):
            raise ValueError(
                "recovery-already-resolved: inspect disposition and original run evidence"
            )
        if choice == "revalidate":
            if proof["verdict"] != "VERIFIED-QUIESCENT":
                raise ValueError(
                    "revalidate requires VERIFIED-QUIESCENT; inspect original job teardown"
                )
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
        if choice == "readmit" and proof["verdict"] != "VERIFIED-QUIESCENT":
            raise ValueError("recovery-readmit-requires-VERIFIED-QUIESCENT")
        request = dict(
            **proposal,
            request_id=uuid4().hex,
            gate="recovery",
            plan_revision=store._revision(),
            jobId=job_id,
            choice=choice,
            reason=reason,
            proof=proof,
            workspace=str(store.workspace.resolve()),
            proposedAction=action,
        )
        if choice == "revalidate":
            request["card"] += (
                "\nRevalidate retained candidate; PASS integrates and delivers at workspace root. "
                "No worker start or extra provider spend."
            )
        request["card"] += (
            f"\nReply binding: expected_revision={request['plan_revision']}"
            "\nEvidence and spent calls stay charged. UNKNOWN abandonment retains quarantine. "
            "Readmission releases a hold; fresh execution consent and worker-start are required."
        )
        store._event(REQUEST, request)
    return request


def complete(store, request_id, **answer):
    _canonical_store(store)
    action, content, chat = _chat_evidence(request_id, **answer)
    with store._transaction():
        request = next(iter(approvals.records(store, REQUEST)), {})
        if request.get("request_id") != request_id:
            raise ValueError("recovery-request-unknown-or-superseded")
        if any(
            r["request_id"] == request_id
            for r in approvals.records(store, "worker-recovery-responded")
        ):
            raise ValueError("recovery-already-responded")
        subject = approvals.subject(store, "recovery", request["proposedAction"])
        proof = subject["proof"]
        if answer["expected_revision"] != request["plan_revision"] or subject != request["subject"]:
            raise ValueError("recovery-proof-stale: inspect and ask again")
        record = approvals.record_approval(
            store,
            request,
            words=content["statement"],
            actor=chat["actor_label"],
            channel="chat-confirmation",
            affirmative=content["approve"],
            state=action,
            conditions=content["conditions"],
        )
        result = dict(
            request_id=request_id,
            state=record["state"],
            provenance="chat-confirmation",
            chat=chat,
            action=action,
            content=content,
        )
        store._event("worker-recovery-responded", result)
        if record["state"] == "approved":
            approvals.require_approval(store, "recovery", request["proposedAction"])
            resolved = dict(
                jobId=request["jobId"],
                request_id=request_id,
                choice=request["choice"],
                reason=request["reason"],
                proof=proof,
                quarantine=proof["verdict"] == "UNKNOWN",
                successor=jobs._digest(request) if request["choice"] == "readmit" else None,
            )
            store._event(RESOLVED, resolved)
            result["disposition"] = resolved
    if record["state"] == "approved" and request["choice"] == "revalidate":
        from codex_harness.core.planner_candidate import revalidate

        try:
            resolved["validation"] = revalidate(store, request["jobId"], request_id)
        except (ValueError, OSError, jobs.subprocess.SubprocessError) as error:
            resolved["validation"] = dict(
                state="ERROR", results=[dict(reason=str(error))], delivery=None
            )
        store._event(RESOLVED, resolved)
    return result


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
                data = prepare(store, **args)
        else:
            raise ValueError("recovery-requires-inspect-or-resolve-with-job-choice-reason")
        return dict(
            isError=False,
            structuredContent=data,
            content=[dict(type="text", text=data.get("card", json.dumps(data)))],
        )
    except (ValueError, OSError, KeyError) as error:
        return dict(isError=True, content=[dict(type="text", text=str(error))])
