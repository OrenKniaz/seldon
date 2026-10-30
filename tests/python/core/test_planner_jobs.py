"""Job mechanics with explicit store/process/dispatch doubles; no provider proof.

Real manifest validation, template snapshots and durable record I/O are exercised.
The store double does NOT establish review-backed publication or human approval.
"""

import copy
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.core.execution_support import publish_source_store, source_grant

from codex_harness.core import planner_jobs as jobs
from codex_harness.core.plan_store import PlanStore
from codex_harness.core.record_io import read_record_json, write_record_json_once

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"


class SourceOnlyStore(PlanStore):
    """Real canonical DB plus explicit negative publication-input test mutations."""

    def __init__(self, workspace):
        canonical, revision = publish_source_store(workspace, spine_file=FIXTURE / "spine.json")
        self.__dict__.update(canonical.__dict__)
        self.revision = revision
        self.manifest = PlanStore.published_manifest(self, revision)
        self.phase_override = None
        self.pending_gate = None
        self.current = True

    def status(self):
        status = super().status()
        status["published_revision"] = self.revision
        if self.phase_override is not None:
            status["phase"] = self.phase_override
        if self.pending_gate is not None:
            status["pending_gate"] = self.pending_gate
        return status

    def publication_current(self, revision):
        return self.current and super().publication_current(revision)

    def published_manifest(self, revision):
        if hasattr(self, "manifest") and revision == self.revision:
            return copy.deepcopy(self.manifest)
        return super().published_manifest(revision)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    store = SourceOnlyStore(tmp_path)
    launches = []

    def process(command, **kwargs):
        launches.append((command, kwargs))
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(jobs.subprocess, "Popen", process)
    monkeypatch.setattr(jobs, "open_plan_store", lambda path: nullcontext(store))
    workers = [
        dict(bead=bead, provider="codex", model="gpt-6.1-sol", effort="medium")
        for bead in store.manifest["beads"]
    ]
    source_grant(store, store.revision, workers)
    yield store, workers, launches
    store.close()


def admit(fixture, **kwargs):
    store, workers, _ = fixture
    if not jobs._directory(store.workspace, store.revision).exists():
        source_grant(
            store,
            store.revision,
            workers,
            max_parallel=kwargs.get("max_parallel", 2),
            timeout=kwargs.get("timeout", 900),
        )
    return jobs.start_worker_job(store, store.revision, workers, call_cap=16, **kwargs)


def _thread_start(workspace, revision, workers):
    with PlanStore(workspace) as canonical:
        return jobs.start_worker_job(canonical, revision, workers, call_cap=16)


def test_reconnect_and_concurrent_start_do_not_duplicate_launch(fixture):
    store, workers, launches = fixture
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: _thread_start(store.workspace, store.revision, workers), range(4)
            )
        )
    assert len(launches) == 1
    assert {result["jobId"] for result in results} == {store.revision}
    reconnected = jobs.worker_job_status(store.workspace, store.revision)
    assert reconnected["status"] == reconnected["liveness"] == "unknown"
    assert reconnected["harnessDeliveryProven"] is False
    assert reconnected["didRunProvider"] is None
    assert admit(fixture) == reconnected
    assert len(launches) == 1


@pytest.mark.parametrize("complete_request", [False, True])
def test_concurrent_start_after_grant_consumption_reattaches_original(
    fixture, monkeypatch, complete_request
):
    from codex_harness.core import planner_execution

    store, workers, launches = fixture
    delayed_at_grant, release_delayed = threading.Event(), threading.Event()
    winner_consumed, release_winner = threading.Event(), threading.Event()
    role = threading.local()
    require = planner_execution.require_execution
    consume = planner_execution.consume_execution

    def interleaved_require(canonical, scope):
        if role.name == "delayed":
            delayed_at_grant.set()
            assert release_delayed.wait(10), "winner did not reach the controlled interleaving"
        return require(canonical, scope)

    def observed_consume(canonical, scope, job_id):
        request_id = consume(canonical, scope, job_id)
        if role.name == "winner":
            winner_consumed.set()
            if not complete_request:
                assert release_winner.wait(10), (
                    "delayed start did not return from partial admission"
                )
        return request_id

    def start(name):
        role.name = name
        return _thread_start(store.workspace, store.revision, workers)

    monkeypatch.setattr(planner_execution, "require_execution", interleaved_require)
    monkeypatch.setattr(planner_execution, "consume_execution", observed_consume)
    directory = jobs._directory(store.workspace, store.revision)
    with ThreadPoolExecutor(max_workers=2) as executor:
        delayed = executor.submit(start, "delayed")
        try:
            assert delayed_at_grant.wait(10), "delayed start did not pass the absent-job check"
            winner = executor.submit(start, "winner")
            if complete_request:
                admitted = winner.result(timeout=10)
                request_bytes = (directory / "request.json").read_bytes()
            else:
                assert winner_consumed.wait(10), "winner did not consume its native grant"
                assert not (directory / "request.json").exists()
                assert launches == []
            release_delayed.set()
            reattached = delayed.result(timeout=10)
            assert reattached["jobId"] == store.revision
            assert reattached["status"] == reattached["liveness"] == "unknown"
            if complete_request:
                assert reattached == admitted
                assert (directory / "request.json").read_bytes() == request_bytes
            else:
                assert reattached["reason"] == "Admission incomplete; do not relaunch"
                assert launches == []
                assert list(directory.iterdir()) == []
        finally:
            release_delayed.set()
            release_winner.set()
        admitted = winner.result(timeout=10)
    assert len(launches) == 1
    assert admitted == jobs.worker_job_status(store.workspace, store.revision)
    consumed = planner_execution._records(store, planner_execution.CONSUMED)
    assert len(consumed) == 1
    assert consumed[0]["jobId"] == store.revision


@pytest.mark.parametrize("case", ["unpublished", "stale", "phase", "gate", "manifest", "template"])
def test_unpublished_stale_and_mismatched_inputs_refuse_before_launch(fixture, case):
    store, workers, launches = fixture
    expected = store.revision
    if case == "unpublished":
        store.revision = None
    elif case == "stale":
        store.current = False
    elif case == "phase":
        store.phase_override = "roundtable"
    elif case == "gate":
        store.pending_gate = "approval:vision-lock"
    elif case == "manifest":
        store.manifest["beads"][workers[0]["bead"]]["title"] = "Altered publication"
    else:
        (store.workspace / "template/PLAN.md").write_text("Altered protected plan")
    with pytest.raises(ValueError, match="worker-job-"):
        jobs.start_worker_job(store, expected, workers, call_cap=16)
    assert launches == []
    assert not (store.workspace / ".codex-harness/worker-jobs").exists()


def test_exact_publication_snapshot_options_and_ordinary_dispatch(fixture, monkeypatch):
    store, workers, launches = fixture
    workers[-1].update(provider="claude", model="claude-sonnet-5-5", effort="high")
    result = admit(fixture, max_parallel=1, timeout=73)
    directory = Path(result["evidenceDir"])
    request = read_record_json(directory / "request.json")
    assert read_record_json(directory / "plan/sample.json") == store.manifest
    assert jobs._files(directory / "plan/template") == jobs._files(store.workspace / "template")
    assert request["options"] == dict(
        workers=sorted(workers, key=lambda row: row["bead"]), maxParallel=1, timeout=73, callCap=16
    )
    command, launch = launches[0]
    assert command[1:5] == ["-P", "-B", "-m", "codex_harness.core.planner_jobs"]
    assert command[-2:] == ["--job-id", store.revision]
    assert launch["cwd"] == store.workspace and launch["stdin"] == jobs.subprocess.DEVNULL
    assert launch["env"] == {
        **os.environ,
        "PYTHONPATH": str(Path(jobs.__file__).resolve().parents[2]),
    }
    called = []

    def source_dispatch(plan, **kwargs):
        called.append((plan, kwargs))
        return dict(runId="source-only-fixture", status=jobs.dispatch.STATUS_MOCK_ONLY)

    monkeypatch.setattr(jobs.dispatch, "run_plan", source_dispatch)
    terminal = jobs.execute_worker_job(store.workspace, store.revision)
    assert terminal["status"] == "completed"
    assert terminal["dispatchStatus"] == jobs.dispatch.STATUS_MOCK_ONLY
    assert called == [
        (
            directory / "plan",
            dict(
                work_root=directory / "runs",
                provider_for={row["bead"]: row["provider"] for row in workers},
                model_for={row["bead"]: row["model"] for row in workers},
                effort_for={row["bead"]: row["effort"] for row in workers},
                max_parallel=1,
                timeout=73,
                max_provider_calls=16,
                execution_job=directory,
                setup={},
            ),
        )
    ]
    assert jobs.worker_job_status(store.workspace, store.revision)["harnessDeliveryProven"] is False
    with pytest.raises(FileExistsError):
        jobs.execute_worker_job(store.workspace, store.revision)
    assert len(called) == 1


@pytest.mark.parametrize("mutation", ["snapshot", "publication", "failure"])
def test_background_failure_is_retained_without_retry(fixture, monkeypatch, mutation):
    store, _, launches = fixture
    result = admit(fixture)
    directory = Path(result["evidenceDir"])
    calls = []

    def fail_dispatch(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("source-only dispatcher failure")

    monkeypatch.setattr(jobs.dispatch, "run_plan", fail_dispatch)
    if mutation == "snapshot":
        (directory / "plan/template/PLAN.md").write_text("Changed after admission")
    elif mutation == "publication":
        store.current = False
    terminal = jobs.execute_worker_job(store.workspace, store.revision)
    assert terminal["status"] == "failed"
    assert len(calls) == (1 if mutation == "failure" else 0)
    saved = jobs.worker_job_status(store.workspace, store.revision)
    assert saved["terminal"] == terminal
    assert saved["status"] == "failed" and saved["didRunProvider"] is None
    assert len(launches) == 1


def test_options_cannot_be_replaced_or_partial_admission_seized(fixture):
    store, workers, launches = fixture
    directory = jobs._directory(store.workspace, store.revision)
    directory.mkdir(parents=True)
    assert admit(fixture)["status"] == "unknown"
    assert launches == []
    # Existing partial admission is retained, never cleaned up/relaunched.
    assert list(directory.iterdir()) == []
    directory.rmdir()
    admit(fixture)
    with pytest.raises(ValueError, match="already-admitted-with-other-options"):
        jobs.start_worker_job(store, store.revision, workers, timeout=11, call_cap=16)
    assert len(launches) == 1


def test_real_child_uses_harness_entrypoint_despite_colliding_product_package(
    tmp_path, monkeypatch
):
    """Real harmless child; actual empty store refuses before dispatcher/providers.

    The store double admits only in the parent. The real child must enter the
    Harness and retain that refusal, rather than run the workspace's exit-0 trap.
    """
    store = SourceOnlyStore(tmp_path)
    workers = [
        dict(bead=bead, provider="codex", model="gpt-6.1-sol", effort="medium")
        for bead in store.manifest["beads"]
    ]
    collision = tmp_path / "codex_harness"
    (collision / "core").mkdir(parents=True)
    trap = "from pathlib import Path\nPath('wrong-entrypoint.txt').write_text('wrong')\n"
    (collision / "__init__.py").write_text(trap)
    (collision / "core/__init__.py").write_text("")
    (collision / "core/planner_jobs.py").write_text(trap + "raise SystemExit(0)\n")
    # Even an inherited explicit product path must not win over the loaded
    # Harness package. Unrelated provider/account environment remains intact.
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("SELDON_TEST_PRESERVE_PROVIDER_ENV", "unchanged")
    original_popen, children = jobs.subprocess.Popen, []

    def observe_launch(command, **kwargs):
        assert kwargs["env"]["SELDON_TEST_PRESERVE_PROVIDER_ENV"] == "unchanged"
        store.connection.execute("UPDATE meta SET published_revision=NULL")
        child = original_popen(command, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(jobs.subprocess, "Popen", observe_launch)
    source_grant(store, store.revision, workers)
    admitted = jobs.start_worker_job(store, store.revision, workers, call_cap=16)
    assert len(children) == 1
    assert children[0].wait(timeout=30) == 1
    directory = Path(admitted["evidenceDir"])
    terminal = read_record_json(directory / "terminal.json")
    assert terminal["status"] == "failed"
    assert "worker-job-current-publication-required" in terminal["error"]["message"]
    assert not (tmp_path / "wrong-entrypoint.txt").exists()
    assert not (directory / "runs").exists()


def test_popen_failure_remains_unknown_and_never_relaunched(fixture, monkeypatch):
    store, _, _ = fixture
    calls = []

    def denied(*args, **kwargs):
        calls.append(True)
        raise PermissionError("source-only launch refusal")

    monkeypatch.setattr(jobs.subprocess, "Popen", denied)
    with pytest.raises(PermissionError):
        admit(fixture)
    result = admit(fixture)
    directory = Path(result["evidenceDir"])
    assert read_record_json(directory / "admission-error.json")["type"] == "PermissionError"
    assert result["admissionError"]["message"] == "source-only launch refusal"
    assert result["status"] == "unknown" and not (directory / "terminal.json").exists()
    assert calls == [True]


def test_dispatch_snapshot_is_observed_without_claiming_liveness_or_delivery(fixture):
    store, workers, _ = fixture
    result = admit(fixture)
    run_dir = Path(result["evidenceDir"]) / "runs/source-only-run"
    (run_dir / "evidence").mkdir(parents=True)
    run = dict(
        runId="source-only-run",
        planRevision=store.revision,
        repo=str(run_dir / "repo"),
        isMocked=True,
        didExecuteRealProvider=False,
        status="running",
        beads={row["bead"]: dict(status="pending", **row) for row in workers},
    )
    write_record_json_once(run_dir / "evidence/run.json", run)
    observed = jobs.worker_job_status(store.workspace, store.revision)
    assert observed["run"]["status"] == "running"
    assert observed["status"] == observed["liveness"] == "unknown"
    assert observed["didRunProvider"] is False and not observed["harnessDeliveryProven"]
    # Foreign candidate provenance must never become a progress display.
    path = run_dir / "evidence/run.json"
    run["planRevision"] = "f" * 64
    path.write_text(json.dumps(run))
    with pytest.raises(ValueError, match="dispatch-identity-mismatch"):
        jobs.worker_job_status(store.workspace, store.revision)


def test_worker_status_wait_returns_early_on_dispatch_change(fixture):
    """Protect waiting on progress even while top-level status stays unknown.

    A loop comparing only status would miss bead progress; snapshot tests never wait.
    Real durable records pass through the MCP adapter and existing job-status reader.
    """
    from codex_harness.core.mcp_planner import PlannerServer

    store, _, _ = fixture
    directory = Path(admit(fixture)["evidenceDir"])
    run_dir = directory / "runs/source-only-run"
    (run_dir / "evidence").mkdir(parents=True)
    run = dict(
        runId="source-only-run",
        planRevision=store.revision,
        repo=str(run_dir / "repo"),
        isMocked=True,
        didExecuteRealProvider=False,
        status="running",
        beads={},
    )
    server = PlannerServer(store.workspace)
    with ThreadPoolExecutor(max_workers=1) as executor:

        def update():
            time.sleep(0.15)
            write_record_json_once(run_dir / "evidence/run.json", run)

        future = executor.submit(update)
        started = time.monotonic()
        try:
            reply = server.call_tool(
                "harness_worker_status",
                dict(
                    job_id=store.revision,
                    wait_seconds=5,
                    include_detail=True,
                ),
            )
        finally:
            server.close()
        elapsed = time.monotonic() - started
        future.result(timeout=5)
    assert not reply["isError"], reply
    assert elapsed < 4
    data = reply["structuredContent"]
    assert data["status"] == "unknown" and data["run"]["status"] == "running"
    assert not data["harnessDeliveryProven"] and data["didRunProvider"] is False
    assert "wait_seconds=120" in reply["content"][0]["text"]


def test_worker_status_wait_times_out_without_inventing_progress(fixture):
    """Catch immediate return or oversleep on unchanged jobs; snapshot tests miss timing."""
    store, _, _ = fixture
    initial = admit(fixture)
    started = time.monotonic()
    result = jobs.worker_job_status(store.workspace, store.revision, wait_seconds=0.15)
    assert 0.15 <= time.monotonic() - started < 2
    assert result == initial


@pytest.mark.parametrize("wait_seconds", [-1, 601])
def test_worker_status_rejects_unbounded_wait_at_public_boundary(fixture, wait_seconds):
    """Bound server occupancy; ordinary snapshot tests never supply wait arguments."""
    from codex_harness.core.mcp_planner import PlannerServer

    store, _, _ = fixture
    admit(fixture)
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool(
            "harness_worker_status",
            dict(
                job_id=store.revision,
                wait_seconds=wait_seconds,
            ),
        )
        assert reply["isError"] and "between 0 and 600" in str(reply)
    finally:
        server.close()


@pytest.mark.parametrize(
    "invalid", ["duplicate", "missing", "unsupported", "empty", "noncanonical"]
)
def test_worker_selection_requires_explicit_supported_tuple(fixture, invalid):
    store, workers, launches = fixture
    if invalid == "duplicate":
        workers[-1] = workers[0]
    elif invalid == "missing":
        workers.pop()
    elif invalid == "unsupported":
        workers[0]["provider"] = "invented-provider"
    elif invalid == "noncanonical":
        workers[0]["provider"] = " Codex "
    else:
        workers[0]["effort"] = ""
    with pytest.raises(
        ValueError, match="provider-must-use-canonical-name" if invalid == "noncanonical" else None
    ):
        jobs.start_worker_job(store, store.revision, workers, call_cap=16)
    assert launches == []


def test_job_that_ended_before_any_provider_launch_readmits_on_fresh_grant(fixture, monkeypatch):
    """Protect stranded publications; catches a Seldon prelaunch bug burning the only job."""
    store, workers, launches = fixture
    directory = Path(admit(fixture)["evidenceDir"])
    beads = {
        bead: {"attempt": None, "didRunProvider": False, "status": "prelaunch failed: X"}
        for bead in store.manifest["beads"]
    }

    def prelaunch_failure(plan, *, work_root, **kwargs):
        record = {"runId": "r1", "status": "plan-failed: prelaunch failed", "beads": beads}
        record.update(didExecuteRealProvider=False, isMocked=False, planRevision=store.revision)
        record["repo"] = str(work_root / "r1/repo")
        (work_root / "r1/evidence").mkdir(parents=True)
        write_record_json_once(work_root / "r1/evidence/run.json", record)
        return record

    monkeypatch.setattr(jobs.dispatch, "run_plan", prelaunch_failure)
    jobs.execute_worker_job(store.workspace, store.revision)
    status = jobs.worker_job_status(store.workspace, store.revision)
    assert "fresh worker execution approval" in status["reason"]
    # The consumed grant alone only reattaches; no relaunch without a new approval.
    assert jobs.start_worker_job(store, store.revision, workers, call_cap=16) == status
    assert len(launches) == 1
    source_grant(store, store.revision, workers)
    readmitted = jobs.start_worker_job(store, store.revision, workers, call_cap=16)
    assert readmitted["status"] == "unknown" and len(launches) == 2
    assert (directory.parent / f"{directory.name}.prelaunch-failed-1/terminal.json").is_file()


def test_job_whose_provider_ran_is_never_readmitted(fixture, monkeypatch):
    store, workers, launches = fixture
    admit(fixture)
    beads = {bead: {"attempt": {}, "didRunProvider": True} for bead in store.manifest["beads"]}

    def provider_failure(plan, *, work_root, **kwargs):
        record = {"runId": "r1", "status": "plan-failed: worker failed", "beads": beads}
        record.update(didExecuteRealProvider=True, isMocked=False, planRevision=store.revision)
        record["repo"] = str(work_root / "r1/repo")
        (work_root / "r1/evidence").mkdir(parents=True)
        write_record_json_once(work_root / "r1/evidence/run.json", record)
        return record

    monkeypatch.setattr(jobs.dispatch, "run_plan", provider_failure)
    jobs.execute_worker_job(store.workspace, store.revision)
    status = jobs.worker_job_status(store.workspace, store.revision)
    assert "fresh worker execution approval" not in status["reason"]
    source_grant(store, store.revision, workers)
    assert jobs.start_worker_job(store, store.revision, workers, call_cap=16) == status
    assert len(launches) == 1
