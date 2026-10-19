"""Conservative local provider-attempt claims in the original publication job.

Create-once ordinal originals arbitrate participating concurrent callers. Claims
precede possible launch and are never refunded, including uncertain failures.
This is not provider quota verification, authentication or current liveness.
"""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from codex_harness.core.record_io import open_record_read, read_record_json, write_record_json_once


def verify_job(directory, record=None):
    from codex_harness.core import planner_jobs as jobs
    from codex_harness.core.plan_store import open_plan_store_readonly
    from codex_harness.core.planner_execution import consumed_execution, execution_scope

    if directory is None:
        raise ValueError("provider-call-owned-native-job-required")
    directory = Path(directory).resolve()
    workspace = directory.parent.parent.parent
    if jobs._directory(workspace, directory.name) != directory:
        raise ValueError("provider-call-job-path-invalid")
    request = jobs._request(directory)
    scope = request.get("executionScope")
    if not isinstance(scope, dict) or request["options"].get("callCap") != scope.get("callCap"):
        raise ValueError("provider-call-native-scope-required")
    started = read_record_json(jobs._inside(directory, directory / "started.json"))
    if (
        started.get("requestDigest") != request["requestDigest"]
        or started.get("pid") != os.getpid()
    ):
        raise ValueError("provider-call-owned-controller-required")
    with open_plan_store_readonly(workspace) as store:
        current = execution_scope(
            store,
            directory.name,
            scope["workers"],
            max_parallel=scope["maxParallel"],
            timeout=scope["timeout"],
            call_cap=scope["callCap"],
        )
        if current != scope or not consumed_execution(
            store, request.get("executionRequestId"), scope, directory.name
        ):
            raise ValueError("provider-call-execution-binding-stale")
    snapshot = read_record_json(jobs._inside(directory, directory / "plan/sample.json"))
    if (
        jobs._digest(snapshot) != request["manifestDigest"]
        or jobs._files(directory / "plan/template") != request["templateFiles"]
    ):
        raise ValueError("provider-call-protected-snapshot-changed")
    if record is not None:
        from codex_harness.core.attempt_provenance import normalized_owned

        selected = next((w for w in scope["workers"] if w["bead"] == record.get("bead")), None)
        bead = snapshot["beads"].get(record.get("bead"), {})
        if (
            selected is None
            or any(record.get(k) != selected[k] for k in ("provider", "model", "effort"))
            or record.get("planRevision") != directory.name
            or normalized_owned(record.get("ownedFiles", []))
            != normalized_owned(bead.get("ownedFiles", []))
            or record.get("requirementRefs") != bead.get("requirementRefs")
            or record.get("specRefs") != bead.get("specRefs")
        ):
            raise ValueError("provider-call-worker-scope-mismatch")
    return request


def bind_run(directory, plan_dir, run_dir, *, max_provider_calls, resume=False, retry_options=None):
    from codex_harness.core import planner_jobs as jobs
    from codex_harness.core.dispatch import (
        STATUS_MOCK_ONLY,
        STATUS_VALIDATED,
        STATUS_VALIDATED_NO_INTEGRATION,
    )

    directory = Path(directory).resolve()
    request = verify_job(directory)
    run_dir, plan_dir = Path(run_dir).resolve(), Path(plan_dir).resolve()
    if (
        plan_dir != directory / "plan"
        or run_dir.parent != directory / "runs"
        or max_provider_calls != request["options"]["callCap"]
    ):
        raise ValueError("provider-call-fixed-job-run-required")
    jobs._inside(directory, run_dir)
    if resume:
        original = read_record_json(jobs._inside(directory, directory / "dispatch-run.json"))
        if original != {
            "requestDigest": request["requestDigest"],
            "runDir": str(run_dir),
            "pid": os.getpid(),
        }:
            raise ValueError("provider-call-original-resume-required")
        with open_record_read(jobs._inside(directory, run_dir / "evidence/run.json")) as stream:
            raw = stream.read()
        previous = json.loads(raw.decode("utf-8"))
        status = previous.get("status")
        terminal = isinstance(status, str) and (
            status in {STATUS_MOCK_ONLY, STATUS_VALIDATED, STATUS_VALIDATED_NO_INTEGRATION}
            or (status.startswith("plan-failed: ") and bool(status[len("plan-failed: ") :].strip()))
        )
        try:
            finished = datetime.fromisoformat(previous.get("finishedAt"))
            timestamp_valid = finished.tzinfo is not None and finished.utcoffset() is not None
        except (TypeError, ValueError):
            timestamp_valid = False
        if not terminal or not timestamp_valid:
            raise ValueError("provider-call-terminal-original-required")
        digest = hashlib.sha256(raw).hexdigest()
        claims = jobs._inside(directory, directory / "dispatch-resume")
        claims.mkdir(exist_ok=True)
        write_record_json_once(
            claims / (digest + ".json"),
            {
                **original,
                "priorRunSha256": digest,
                "retryOptions": retry_options or {},
            },
        )
        return
    write_record_json_once(
        directory / "dispatch-run.json",
        {
            "requestDigest": request["requestDigest"],
            "runDir": str(run_dir),
            "pid": os.getpid(),
        },
    )


def claim_call(directory, record, attempt_dir, *, timeout=None):
    from codex_harness.core import planner_jobs as jobs

    directory = Path(directory).resolve()
    request = verify_job(directory, record)
    if timeout is not None and (
        not jobs._valid_timeout(timeout) or timeout != request["options"]["timeout"]
    ):
        raise ValueError("provider-call-timeout-scope-mismatch")
    original = read_record_json(jobs._inside(directory, directory / "dispatch-run.json"))
    attempt_dir = Path(attempt_dir).resolve()
    run_dir = jobs._inside(directory, Path(original["runDir"]))
    if (
        original.get("requestDigest") != request["requestDigest"]
        or not attempt_dir.is_relative_to(run_dir)
        or (
            run_dir.parent != directory / "runs"
            or original.get("pid") != os.getpid()
            or record.get("runId") != run_dir.name
        )
    ):
        raise ValueError("provider-call-run-binding-mismatch")
    claims = jobs._inside(directory, directory / "provider-call-budget")
    claims.mkdir(exist_ok=True)
    # Atomic create-once collisions advance within the same approved finite
    # range; no read-count/write race, refund, fresh-root reset or unlimited retry.
    for ordinal in range(1, request["options"]["callCap"] + 1):
        path = jobs._inside(directory, claims / f"{ordinal:08d}.json")
        try:
            write_record_json_once(
                path,
                {
                    "requestDigest": request["requestDigest"],
                    "ordinal": ordinal,
                    "bead": record["bead"],
                    "provider": record["provider"],
                    "model": record["model"],
                    "effort": record["effort"],
                    "attemptDir": str(attempt_dir),
                },
            )
            return ordinal
        except FileExistsError:
            previous = read_record_json(path)
            if (
                previous.get("requestDigest") != request["requestDigest"]
                or previous.get("ordinal") != ordinal
            ):
                raise ValueError("provider-call-claim-original-invalid") from None
            continue
    raise ValueError("provider-call-cap-exhausted")
