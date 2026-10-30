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


def _planning_review_originals(store):
    """Cross-check sticky project originals; absence alone retains legacy behavior."""
    root = store.path.parent / "planning-review-budget"
    registrations = store.connection.execute(
        "SELECT payload_json FROM events WHERE kind='planning-review-budget-registered' ORDER BY id"
    ).fetchall()
    claims = store.connection.execute(
        "SELECT payload_json FROM events WHERE kind='planning-review-start-claimed' ORDER BY id"
    ).fetchall()
    if not registrations and not claims and not root.exists():
        return None, []
    try:
        if len(registrations) != 1 or root.is_symlink():
            raise ValueError("registration required")
        original = json.loads(registrations[0][0])
        if (
            not isinstance(original, dict)
            or set(original) != {"schema", "workspace", "campaign", "maxStarts"}
            or original["schema"] != "seldon/planning-review-budget-v1"
            or original["workspace"] != str(store.workspace)
            or not isinstance(original["campaign"], str)
            or not original["campaign"].strip()
            or type(original["maxStarts"]) is not int
            or not 1 <= original["maxStarts"] <= 10
            or read_record_json(root / "registration.json") != original
        ):
            raise ValueError("registration changed")
        digest = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
        records = [json.loads(row[0]) for row in claims]
        if len(records) > original["maxStarts"]:
            raise ValueError("claims exceed original cap")
        expected = {f"{ordinal:08d}.json" for ordinal in range(1, len(records) + 1)}
        directory = root / "claims"
        if directory.is_symlink() or {path.name for path in directory.iterdir()} != expected:
            raise ValueError("claim originals missing or orphaned")
        for ordinal, record in enumerate(records, 1):
            if (
                not isinstance(record, dict)
                or set(record) != {"registrationDigest", "ordinal", "reviewer", "pid"}
                or record["registrationDigest"] != digest
                or type(record["ordinal"]) is not int
                or record["ordinal"] != ordinal
                or not isinstance(record["reviewer"], dict)
                or type(record["pid"]) is not int
                or read_record_json(directory / f"{ordinal:08d}.json") != record
            ):
                raise ValueError("claim original changed")
        return original, records
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise ValueError("planning-review-budget-invalid-original") from error


def planning_review_budget(store):
    """Read the configured review ceiling; no quota or host-call claim."""
    original, claims = _planning_review_originals(store)
    return None if original is None else {**original, "spentAdmissions": len(claims)}


def register_planning_review_budget(store, campaign, max_starts):
    """Prepare once through the public CLI, before any child; never reset a cap."""
    if (
        not isinstance(campaign, str)
        or not campaign.strip()
        or len(campaign) > 128
        or type(max_starts) is not int
        or not 1 <= max_starts <= 10
    ):
        raise ValueError("planning-review-budget-invalid-scope")
    proposed = dict(
        schema="seldon/planning-review-budget-v1",
        workspace=str(store.workspace),
        campaign=campaign,
        maxStarts=max_starts,
    )
    with store._transaction():
        original, claims = _planning_review_originals(store)
        if original is not None:
            if proposed != original:
                raise ValueError("planning-review-budget-immutable-scope")
        else:
            root = store.path.parent / "planning-review-budget"
            root.mkdir()
            (root / "claims").mkdir()
            write_record_json_once(root / "registration.json", proposed)
            store._event("planning-review-budget-registered", proposed)
    return {**proposed, "spentAdmissions": len(claims)}


def claim_planning_review_start(store, reviewer):
    """Reserve one durable review admission before a possible real child start.

    Existing SQLite writer reservation serializes participating processes. File
    originals precede their events: interruption leaves an orphan and holds,
    never a refundable launch or fresh allowance. A spent admission is not proof
    the process started. Worker job guards stay separate.
    """
    with store._transaction():
        original, claims = _planning_review_originals(store)
        if original is None:
            return None
        from codex_harness.core.planner_confirmations import combined_credit

        authority = combined_credit(store, "review-budget")
        if authority:
            scope = authority["scopes"]["review-budget"]
            if {k: v for k, v in scope.items() if k != "reviewers"} != original:
                raise ValueError("planning-review-budget-consent-required")
            if {
                k: reviewer.get(k) for k in ("name", "provider", "model", "effort")
            } not in scope.get("reviewers", []):
                raise ValueError("planning-reviewer-consent-required")
        if len(claims) >= original["maxStarts"]:
            raise ValueError("planning-review-start-cap-exhausted")
        ordinal = len(claims) + 1
        record = dict(
            registrationDigest=hashlib.sha256(
                json.dumps(original, sort_keys=True).encode()
            ).hexdigest(),
            ordinal=ordinal,
            reviewer=reviewer,
            pid=os.getpid(),
        )
        write_record_json_once(
            store.path.parent / "planning-review-budget" / "claims" / f"{ordinal:08d}.json",
            record,
        )
        store._event("planning-review-start-claimed", record)
    return ordinal
