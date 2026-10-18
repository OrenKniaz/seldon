"""Job mechanics with explicit store/process/dispatch doubles; no provider proof.

Real manifest validation, template snapshots and durable record I/O are exercised.
The store double does NOT establish review-backed publication or human approval.
"""

import copy
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_harness.core import planner_jobs as jobs
from codex_harness.core.plan_store import PlanStore
from codex_harness.core.record_io import read_record_json, write_record_json_once
from codex_harness.core.spine import compile_manifest, load_spine

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"


class SourceOnlyStore:
    """Only supply the published store contract; never label synthetic evidence real."""

    def __init__(self, workspace):
        self.workspace = workspace
        shutil.copytree(FIXTURE / "template", workspace / "template")
        self.graph = load_spine(FIXTURE / "spine.json")
        self.manifest = compile_manifest(self.graph, workspace / "template")
        self.revision = self.manifest["planRevision"]
        self.phase = "publish"
        self.pending_gate = None
        self.current = True
        (workspace / ".codex-harness").mkdir()
        (workspace / ".codex-harness/plan.sqlite3").touch()

    def status(self):
        return dict(
            published_revision=self.revision, phase=self.phase, pending_gate=self.pending_gate
        )

    def publication_current(self, revision):
        return self.current and revision == self.manifest_revision()

    def manifest_revision(self):
        return compile_manifest(self.graph, self.workspace / "template")["planRevision"]

    def published_manifest(self, revision):
        assert revision == self.revision
        return copy.deepcopy(self.manifest)

    def spine(self):
        return self.graph

    check_requirement_files = staticmethod(PlanStore.check_requirement_files)


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
    return store, workers, launches


def admit(fixture, **kwargs):
    store, workers, _ = fixture
    return jobs.start_worker_job(store, store.revision, workers, **kwargs)


def test_reconnect_and_concurrent_start_do_not_duplicate_launch(fixture):
    store, workers, launches = fixture
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(lambda _: jobs.start_worker_job(store, store.revision, workers), range(4))
        )
    assert len(launches) == 1
    assert {result["jobId"] for result in results} == {store.revision}
    reconnected = jobs.worker_job_status(store.workspace, store.revision)
    assert reconnected["status"] == reconnected["liveness"] == "unknown"
    assert reconnected["harnessDeliveryProven"] is False
    assert reconnected["didRunProvider"] is None
    assert admit(fixture) == reconnected
    assert len(launches) == 1


@pytest.mark.parametrize("case", ["unpublished", "stale", "phase", "gate", "manifest", "template"])
def test_unpublished_stale_and_mismatched_inputs_refuse_before_launch(fixture, case):
    store, workers, launches = fixture
    expected = store.revision
    if case == "unpublished":
        store.revision = None
    elif case == "stale":
        store.current = False
    elif case == "phase":
        store.phase = "roundtable"
    elif case == "gate":
        store.pending_gate = "approval:vision-lock"
    elif case == "manifest":
        store.manifest["beads"][workers[0]["bead"]]["title"] = "Altered publication"
    else:
        (store.workspace / "template/PLAN.md").write_text("Altered protected plan")
    with pytest.raises(ValueError, match="worker-job-"):
        jobs.start_worker_job(store, expected, workers)
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
        workers=sorted(workers, key=lambda row: row["bead"]), maxParallel=1, timeout=73
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
        jobs.start_worker_job(store, store.revision, workers, timeout=11)
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
        child = original_popen(command, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(jobs.subprocess, "Popen", observe_launch)
    admitted = jobs.start_worker_job(store, store.revision, workers)
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
        jobs.start_worker_job(store, store.revision, workers)
    assert launches == []
