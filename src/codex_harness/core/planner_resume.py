"""Resume reads canonical guidance and job originals; only jobs admits execution."""

from pathlib import Path

from codex_harness.core import planner_jobs as jobs
from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
from codex_harness.core.planner_recovery import admission_id


def resume_plan(
    workspace,
    *,
    expected_revision=None,
    workers=None,
    execution_authorized=False,
    deadline=None,
    max_parallel=2,
    timeout=900,
    request_execution=False,
):
    workspace = Path(workspace)
    if workspace.is_symlink():
        raise ValueError("resume-workspace-symlink")
    workspace = workspace.resolve()
    result = dict(
        schema="seldon/planner-resume-v1",
        state="held",
        job=None,
        reasonCode="canonical-state-unavailable",
        canonicalStatus=None,
        publishedRevision=None,
        nextAction=dict(kind="unavailable_refresh"),
        liveness="unknown",
        harnessDeliveryProven=False,
    )
    if not jobs._inside(workspace, workspace / ".codex-harness/plan.sqlite3").is_file():
        return dict(result, reasonCode="plan-missing", nextAction=dict(kind="start_plan"))

    def attach(job):
        result.update(
            state="reattached",
            reasonCode="original-job-reattached",
            job=job,
            nextAction=dict(kind="inspect_worker_job", jobId=job["jobId"]),
        )

    # An existing original wins even when current planning guidance is unavailable.
    # Follow the recovery owner's chain, rather than reattaching the abandoned root.
    with open_plan_store_readonly(workspace) as store:
        revision = expected_revision or store._meta()["published_revision"]
        if revision:
            selected = admission_id(store, revision)
            try:
                original = jobs.worker_job_status(workspace, selected)
            except ValueError as error:
                if str(error) != "worker-job-not-found":
                    raise
            else:
                if request_execution:
                    original = jobs.start_worker_job(
                        store,
                        revision,
                        workers,
                        max_parallel=max_parallel,
                        timeout=timeout,
                    )
                attach(original)
                return result
        status = store.status()
        result.update(
            canonicalStatus=status,
            planRevision=status["plan_revision"],
            manifestRevision=status["manifest_revision"],
            publishedRevision=status["published_revision"],
            nextAction=status["reviewResolution"]["nextAction"],
        )
        # Guidance only: the execution owner checks all admission prerequisites
        # and exact consent again before any launch.
        if (
            status["phase"] not in {"publish", "execute"}
            or status["pending_gate"]
            or not status["publication"]["up_to_date"]
        ):
            result["reasonCode"] = "canonical-prerequisite"
            return result
        result.update(
            reasonCode="native-execution-confirmation-required",
            nextAction=dict(
                kind="request_native_execution", publishedRevision=status["published_revision"]
            ),
        )
    if request_execution:
        with open_plan_store(workspace) as store:
            attach(
                jobs.start_worker_job(
                    store,
                    expected_revision,
                    workers,
                    max_parallel=max_parallel,
                    timeout=timeout,
                )
            )
            result.update(state="handoff_requested", reasonCode="worker-handoff-requested")
    return result
