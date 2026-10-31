"""One transactional delivery ledger for attempted or uncertain real starts.

SQLite arbitrates participating concurrent callers. Claims precede possible
launch and are never refunded, including uncertain failures.
This is not provider quota verification, authentication or current liveness.
"""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from codex_harness.core.record_io import open_record_read, read_record_json, write_record_json_once

DEFAULT_REVIEW_ROUNDS = 2


def verify_job(directory, record=None):
    from codex_harness.core import planner_jobs as jobs
    from codex_harness.core.plan_store import open_plan_store_readonly
    from codex_harness.core.planner_execution import admitted_execution

    if directory is None:
        raise ValueError("provider-call-owned-native-job-required")
    directory = Path(directory).resolve()
    workspace = directory.parent.parent.parent
    request = jobs._request(directory)
    if jobs._directory(workspace, request["jobId"]) != directory:
        raise ValueError("provider-call-job-path-invalid")
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
        if not admitted_execution(
            store, request.get("executionRequestId"), scope, request["jobId"]
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
            or record.get("planRevision") != request["publicationRevision"]
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
    from codex_harness.core.plan_store import open_plan_store

    with open_plan_store(directory.parent.parent.parent) as store:
        with store._transaction():
            from codex_harness.core.approvals import current, executable_candidate, require_approval

            approved = current(store, "B")
            scope = dict(request["executionScope"])
            if approved:
                scope["callCap"] = approved["subject"]["execution"]["callCap"]
            manifest = read_record_json(directory / "plan/sample.json")
            authority = require_approval(
                store,
                "B",
                dict(
                    execution=scope,
                    candidate=executable_candidate(manifest, request["templateFiles"]),
                ),
            )
            return _reserve(store, "worker", authority["subject"]["execution"]["callCap"], record)


def usage(store, kind):
    return store.connection.execute(
        "SELECT count(*) FROM usage_ledger WHERE kind=?", (kind,)
    ).fetchone()[0]


def _reserve(store, kind, total, payload):
    from uuid import uuid4

    if not store.connection.in_transaction:
        raise ValueError("usage-writer-transaction-required")
    used = usage(store, kind)
    if used >= total:
        from codex_harness.core import approvals
        from codex_harness.core.plan_store import PlanStoreError

        previous = approvals.current(store, "B")["subject"]
        action = {
            "reviewAllowance": used + max(1, len(previous["reviewers"])),
            "allowanceReason": "Additional review round after the cumulative allowance was spent",
        }
        if kind == "worker":
            action = {
                "execution": {
                    **previous["execution"],
                    "callCap": used + len(previous["execution"]["workers"]),
                }
            }
        proposed = approvals.proposal(store, "B", action)
        error = PlanStoreError(
            "plan-b-consent-required", f"{kind}-call-cap-exhausted: " + proposed["card"]
        )
        error.approval = proposed
        raise error
    store.connection.execute(
        "INSERT INTO usage_ledger (kind, source, payload_json) VALUES (?, ?, ?)",
        (kind, uuid4().hex, json.dumps(payload, sort_keys=True)),
    )
    return used + 1


def planning_review_budget(store):
    from codex_harness.core.approvals import current

    record = current(store, "B")
    return dict(
        maxStarts=record["subject"]["reviewAllowance"] if record else None,
        spentAdmissions=usage(store, "review"),
    )


def claim_planning_review_start(store, reviewer, *, limits=None):
    """Reserve at the real launch boundary. Refusal rolls back every admission row."""
    from contextlib import nullcontext

    from codex_harness.core.approvals import require_approval

    with nullcontext() if store.connection.in_transaction else store._transaction():
        record = require_approval(store, "B", dict(reviewer=reviewer, reviewLimits=limits or {}))
        return _reserve(store, "review", record["subject"]["reviewAllowance"], reviewer)


def review_capacity(store, reviewer, revision, max_rounds, max_total):
    from codex_harness.core import roundtable
    from codex_harness.core.plan_store import PlanStoreError

    totals = next(
        (
            t
            for t in roundtable.review_totals(store, revision)
            if t["reviewer"] == reviewer["name"] and t["provider"] == reviewer["provider"]
        ),
        dict(round=0, total=0),
    )
    if max_total is not None and totals["total"] >= max_total:
        raise PlanStoreError(
            "roundtable-total-budget-exhausted",
            roundtable._total_exhaustion_advice(
                store, reviewer, revision, totals["total"], max_total
            ),
        )
    if max_rounds is not None and totals["round"] >= max_rounds:
        raise PlanStoreError(
            "roundtable-budget-exhausted",
            "Revise the plan or raise the round limit through B",
        )
    return totals


def admit_review(
    store, reviewer, run_id, revision, max_rounds, max_total, timeout, *, mocked=False
):
    from codex_harness.core import roundtable
    from codex_harness.core.plan_store import PlanStoreError

    with store._transaction():
        if store.manifest_revision() != revision:
            raise PlanStoreError("roundtable-plan-changed", "Review the new plan revision")
        if not mocked:
            # Usage and the attempt reservation commit together, immediately
            # before the child call. Any refusal rolls both back.
            claim_planning_review_start(
                store,
                reviewer,
                limits=dict(maxRounds=max_rounds, maxTotal=max_total, timeout=timeout),
            )
        roundtable._table(store)
        store.connection.execute(
            "CREATE TABLE IF NOT EXISTS review_attempts (id TEXT PRIMARY KEY, "
            "plan_revision TEXT, reviewer TEXT, provider TEXT)"
        )
        exists = store.connection.execute(
            "SELECT 1 FROM review_attempts WHERE id=?", (run_id,)
        ).fetchone()
        totals = review_capacity(
            store, reviewer, revision, None if exists else max_rounds, None if exists else max_total
        )
        if not exists:
            store.connection.execute(
                "INSERT INTO review_attempts VALUES (?, ?, ?, ?)",
                (run_id, revision, reviewer["name"], reviewer["provider"]),
            )
            return totals["round"] + 1, totals["total"] + 1
        return totals["round"], totals["total"]


def admit_execution_assessment(store, header):
    from codex_harness.core import acceptance_recovery as recovery
    from codex_harness.core import roundtable

    request, context = header["request"], header["context"]
    with store._transaction():
        if roundtable._execution_context(store, request) != context:
            raise ValueError("execution-selection-raced-before-reservation")
        if any(
            old["request"] == request and old["context"] == context
            for _, old in roundtable._execution_events(store, "execution-assessment-reserved")
        ):
            return False
        used = next(
            (
                row
                for row in roundtable.review_totals(store, request["expected_manifest_revision"])
                if row["reviewer"] == request["reviewer"]["name"]
                and row["provider"] == request["reviewer"]["provider"]
            ),
            dict(round=0, total=0),
        )
        if used["round"] >= request["max_rounds"] or used["total"] >= request["max_total"]:
            raise ValueError("execution-shared-review-budget-exhausted")
        claim_planning_review_start(
            store,
            request["reviewer"],
            limits=dict(
                maxRounds=request["max_rounds"],
                maxTotal=request["max_total"],
                timeout=request["timeout"],
            ),
        )
        root = roundtable._execution_directory(store, header["registrationId"])
        root.mkdir(parents=True, exist_ok=False)
        recovery._write(root, "packet", header["packet"].encode("utf-8"))
        store._event("execution-assessment-reserved", header)
    return True
