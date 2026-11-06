"""Executable inputs and durable job admission; A/B consent belongs to approvals."""

from pathlib import Path

from codex_harness.core import approvals, planner_jobs as jobs
from codex_harness.core.plan_quality import quality_report
from codex_harness.core.planner_views import review_resolution_state
from codex_harness.core.spine import Spine

ADMITTED = "worker-job-admitted"


def _canonical_store(store):
    databases = store.connection.execute("PRAGMA database_list").fetchall()
    if store.connection.execute("PRAGMA query_only").fetchone()[0] or not any(
        row[1] == "main" and row[2] and Path(row[2]).resolve() == store.path.resolve()
        for row in databases
    ):
        raise ValueError("worker-execution-canonical-store-required")
    if store.path.resolve() != (store.workspace / ".codex-harness/plan.sqlite3").resolve():
        raise ValueError("worker-execution-canonical-store-required")


def execution_scope(
    store, expected_revision, workers, *, max_parallel=2, timeout=900, _candidate=False
):
    """Read canonical scope; no event, approval, launch or provider query."""
    if _candidate:
        manifest, template = store._manifest(Spine(store._data())), store.workspace / "template"
        if not expected_revision or expected_revision not in {
            manifest["planRevision"],
            store._meta()["published_revision"],
        }:
            raise ValueError("worker-execution-frozen-candidate-required")
    else:
        manifest, template = jobs._publication(store, expected_revision)
    semantic = manifest.get("planMode") == "semantic-v1"
    if semantic and max_parallel != 1:
        raise ValueError("Parallel Build unsupported in semantic-v1; use max_parallel=1")
    selected = jobs._workers(manifest, workers)
    if type(max_parallel) is not int or max_parallel < 1 or not jobs._valid_timeout(timeout):
        raise ValueError("worker-execution-limits-invalid: use positive max_parallel and a finite positive timeout")
    if not _candidate:
        status = store.status()
        resolution = review_resolution_state(store, status)
        check = next(c for c in quality_report(store)["checks"] if c["id"] == "roundtable")
        if (
            (status["vision_lock_stale"] if "vision_lock_stale" in status else False)
            or status["open_clarifications"]
            or resolution.get("findingDispositionPrerequisite", {}).get("complete") is not True
            or (
                (not semantic and resolution["audit"]["current"] is not True)
                or resolution["review"]["current"] is not True
                or check["status"] not in {"pass", "warn"}
            )
        ):
            raise ValueError("worker-execution-canonical-prerequisite")
    scope = dict(
        workspace=str(store.workspace.resolve()),
        publicationRevision=expected_revision,
        spineRevision=store._revision(),
        sourceManifestRevision=manifest.get("sourceManifestRevision", expected_revision),
        manifestDigest=jobs._digest(manifest),
        templateFiles=jobs._files(template),
        inputs=jobs.execution_inputs(store),
        workers=selected,
        maxParallel=max_parallel,
        timeout=timeout,
    )
    return scope


def require_execution(store, scope):
    _canonical_store(store)
    approvals.scope_assessment(store, prepare=True)
    if scope["inputs"] != jobs.execution_inputs(store):
        raise ValueError("worker-execution-inputs-stale")
    return approvals.require_approval(
        store,
        "B",
        dict(execution=scope),
    )


def admit_execution(store, scope, job_id):
    """Record job provenance once the existing namespace owner has won admission."""
    with store._transaction():
        record = require_execution(store, scope)
        store._event(
            ADMITTED, dict(id=record["id"], jobId=job_id, scope=scope, channel=record["channel"])
        )
    return record["id"]


def admitted_execution(store, request_id, scope, job_id):
    return next((r for r in approvals.records(store, ADMITTED)
                 if r["id"] == request_id and r["jobId"] == job_id and r["scope"] == scope), None)
