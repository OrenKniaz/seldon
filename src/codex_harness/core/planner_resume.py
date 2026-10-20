"""Same-chat continuation observations, never an autonomous planner or admission lease.

Authorization, capacity and deadline declarations remain caller reports. Default
inspection uses a read-only store. An explicit execution request can consume an
exact native original only through shared jobs admission on the canonical store;
it cannot mint permission or retry an ambiguous job.
"""

from pathlib import Path
from uuid import uuid4

from codex_harness.core import planner_jobs
from codex_harness.core.plan_quality import quality_report
from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
from codex_harness.core.record_io import write_record_json_once

SCHEMA = "seldon/planner-resume-v1"


def _job_summary(job, current):
    def text(value, limit=80):
        return value[:limit] if isinstance(value, str) else None

    return {
        **{
            key: job.get(key)
            for key in ("jobId", "publicationRevision", "status", "didRunProvider")
        },
        "liveness": "unknown",
        "harnessDeliveryProven": False,
        "relation": "current" if current else "historical",
        "dispatchStatus": text(job.get("terminal", {}).get("dispatchStatus")),
        "observedRunStatus": text(job.get("run", {}).get("status")),
        "reason": text(job.get("reason"), 512),
        "reasonTruncated": isinstance(job.get("reason"), str) and len(job["reason"]) > 512,
        "statusRoute": {
            "mcpTool": "harness_worker_status",
            "cliCommand": "worker-status",
            "jobId": job["jobId"],
        },
    }


def _original(workspace, revision):
    try:
        return planner_jobs.worker_job_status(workspace, revision)
    except ValueError as error:
        if str(error) == "worker-job-not-found":
            return None
        raise


def resume_plan(
    workspace,
    *,
    expected_revision=None,
    workers=None,
    execution_authorized=False,
    reserved_worker_calls=None,
    deadline=None,
    max_parallel=2,
    timeout=900,
    request_execution=False,
    call_cap=None,
):
    """Inspect by default; explicitly requested admission requires a native original.

    Observations are create-once advisory records. Missing stores create nothing.
    Existing/incomplete/corrupt job originals can never become absence or retries.
    """
    workspace = Path(workspace)
    result = {
        "schema": SCHEMA,
        "state": "held",
        "reasonCode": "canonical-state-unavailable",
        "nextAction": {"kind": "unavailable_refresh"},
        "planRevision": None,
        "manifestRevision": None,
        "publishedRevision": None,
        "requestedRevision": expected_revision if isinstance(expected_revision, str) else None,
        "canonicalStatus": None,
        "job": None,
        "execution": {
            "authorizationReported": execution_authorized is True,
            "reservedWorkerCalls": reserved_worker_calls
            if type(reserved_worker_calls) is int
            else None,
            "deadline": deadline if isinstance(deadline, str) else None,
            "capacityVerified": False,
            "authorizationVerified": False,
            "requestExecution": request_execution is True,
            "callCap": call_cap if type(call_cap) is int else None,
        },
        "observationId": None,
        "observationPath": None,
        "observationPersisted": False,
        "observationError": None,
        "liveness": "unknown",
        "harnessDeliveryProven": False,
    }

    def hold(code, kind, reason, fields=(), **context):
        result.update(
            reasonCode=code,
            nextAction={
                "kind": kind,
                "reason": reason,
                "requiredFields": list(fields),
                "mcpTool": "harness_plan_resume",
                "cliCommand": "resume",
                **context,
            },
        )

    try:
        if workspace.is_symlink():
            raise ValueError("resume-workspace-symlink")
        workspace = workspace.resolve()
        database = planner_jobs._inside(workspace, workspace / ".codex-harness/plan.sqlite3")
        if not database.is_file():
            hold(
                "plan-missing",
                "start_plan",
                "Start a plan before resuming it.",
                mcpTool="harness_plan_start",
                cliCommand="start",
            )
            return result
    except (OSError, ValueError):
        result["reasonCode"] = "workspace-unavailable"
        return result

    def finish():
        identifier = uuid4().hex
        result.update(observationId=identifier)
        try:
            directory = planner_jobs._inside(workspace, workspace / ".codex-harness/planner-resume")
            directory.mkdir(exist_ok=True)
            path = planner_jobs._inside(workspace, directory / (identifier + ".json"))
            result.update(observationPath=str(path), observationPersisted=True)
            write_record_json_once(path, result)
        except (OSError, ValueError):
            # A saved original remains authoritative even if this observation
            # cannot be published. Never turn failed reporting into a new launch.
            result.update(observationError="observation-write-failed", observationPersisted=False)
        return result

    def attach(job):
        current = job["jobId"] == result["publishedRevision"] and bool(
            (result["canonicalStatus"] or {}).get("publication", {}).get("up_to_date")
        )
        result.update(
            state="reattached",
            reasonCode="original-job-reattached",
            job=_job_summary(job, current),
            nextAction={"kind": "inspect_worker_job", "jobId": job["jobId"]},
        )

    original = None
    admission = None
    try:
        original = (
            _original(workspace, expected_revision) if expected_revision is not None else None
        )
        with open_plan_store_readonly(workspace) as store:
            # Explicit jobs must still be inspectable if canonical state is
            # temporarily unreadable or has moved on from their publication.
            try:
                status = store.status()
                resolution = status["reviewResolution"]
                result.update(
                    canonicalStatus=status,
                    planRevision=status["plan_revision"],
                    manifestRevision=status["manifest_revision"],
                    publishedRevision=status["published_revision"],
                    nextAction=resolution["nextAction"],
                )
            except Exception:
                if original is not None:
                    attach(original)
                    result["job"]["relation"] = "unknown"
                return finish()
            selected = (
                expected_revision if expected_revision is not None else status["published_revision"]
            )
            if original is None and selected is not None:
                original = _original(workspace, selected)
            if original is not None:
                if request_execution is True:
                    # Validate explicit execution options through the same shared
                    # existing-job path; default historical inspection stays read-only.
                    try:
                        planner_jobs.start_worker_job(
                            store,
                            expected_revision,
                            workers,
                            max_parallel=max_parallel,
                            timeout=timeout,
                            call_cap=call_cap,
                        )
                    except Exception:
                        attach(original)
                        result.update(state="held", reasonCode="existing-job-options-refused")
                        return finish()
                attach(original)
                return finish()

            # The shared view deliberately scopes its action to planning phases.
            # Resume must retain the same evidence prerequisites in execute too.
            if resolution["nextAction"]["kind"] == "existing_canonical_blocker":
                result["reasonCode"] = "canonical-prerequisite"
                return finish()
            blockers = resolution["blockingOpenFindings"]
            if blockers is None:
                return finish()
            if blockers:
                result.update(
                    reasonCode="canonical-prerequisite",
                    nextAction={
                        **resolution["nextAction"],
                        "kind": "reconcile_findings",
                        "findingIds": [row["id"] for row in blockers],
                        "manifestRevision": resolution["manifestRevision"],
                        "auditMode": resolution["audit"]["proofLevel"] or "unknown",
                        "findingDetails": [
                            {"id": row["id"], "detailRoute": row["detailRoute"]} for row in blockers
                        ],
                    },
                )
                return finish()
            # Publication continuation uses its configured prerequisites. Worker
            # admission below still owns the separate quality/native checks.
            if (
                status["phase"] in {"roundtable", "publish"}
                and resolution["nextAction"]["kind"] != "not_applicable"
            ):
                result["reasonCode"] = "canonical-prerequisite"
                return finish()
            if status["phase"] not in {"roundtable", "publish", "execute"}:
                result.update(
                    reasonCode="planning-phase-required",
                    nextAction={
                        "kind": "inspect_plan_phase",
                        "phase": status["phase"],
                        "mcpTool": "harness_plan_status",
                        "cliCommand": "status",
                    },
                )
                return finish()
            if resolution["audit"]["current"] is None:
                return finish()
            if not resolution["audit"]["current"]:
                result.update(
                    reasonCode="canonical-prerequisite",
                    nextAction={
                        **resolution["nextAction"],
                        "kind": "audit_required",
                        "manifestRevision": resolution["manifestRevision"],
                        "auditMode": resolution["audit"]["proofLevel"] or "unknown",
                    },
                )
                return finish()
            check = next(
                check for check in quality_report(store)["checks"] if check["id"] == "roundtable"
            )
            if not resolution["review"]["current"] or check["status"] == "fail":
                result.update(
                    reasonCode="canonical-prerequisite",
                    nextAction={
                        **resolution["nextAction"],
                        "kind": "review_required",
                        "manifestRevision": resolution["manifestRevision"],
                        "auditMode": resolution["audit"]["proofLevel"] or "unknown",
                        "detailRoute": {
                            "roundId": check["evidence"].get("roundId"),
                            "mcpTool": "harness_review",
                            "cliCommand": "review",
                        },
                        "advice": check["advice"][:512],
                        "adviceTruncated": len(check["advice"]) > 512,
                    },
                )
                return finish()
            if check["status"] not in {"pass", "warn"}:
                return finish()
            if (
                status["pending_gate"]
                or status["phase"] not in {"publish", "execute"}
                or not status["publication"]["up_to_date"]
            ):
                result["reasonCode"] = "canonical-prerequisite"
                return finish()
            if request_execution is not True:
                hold(
                    "native-execution-confirmation-required",
                    "request_native_execution",
                    "Execution needs an explicit request and an exact native client decision; "
                    "caller declarations cannot grant permission.",
                    publishedRevision=status["published_revision"],
                )
                return finish()
            admission = dict(
                expected_revision=expected_revision,
                workers=workers,
                max_parallel=max_parallel,
                timeout=timeout,
                call_cap=call_cap,
            )
        # Read-only projection cannot be the durable grant writer/consumer.
        # Shared jobs admission owns canonical validation and one-time consumption.
        if admission is not None:
            with open_plan_store(workspace) as canonical:
                try:
                    job = planner_jobs.start_worker_job(canonical, **admission)
                except Exception as error:
                    original = _original(workspace, expected_revision)
                    if original is not None:
                        attach(original)
                    else:
                        hold(
                            "native-execution-confirmation-required"
                            if getattr(error, "code", str(error))
                            == "worker-execution-grant-required"
                            else "worker-admission-refused",
                            "request_native_execution",
                            "An exact native execution original is required; inspect the refusal "
                            "before another explicit request.",
                            publishedRevision=result["publishedRevision"],
                        )
                    return finish()
                original = job  # Retain actual admission even if later reporting fails.
                attach(job)
                result.update(state="handoff_requested", reasonCode="worker-handoff-requested")
    except Exception:
        # Malformed/foreign/unreadable originals are never absence. Keep this
        # bounded diagnostic free of raw exception/provider/private content.
        if original is not None:
            attach(original)
            result["job"]["relation"] = "unknown"
        else:
            result.update(state="held", reasonCode="canonical-or-job-unavailable")
    return finish()
