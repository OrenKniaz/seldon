"""Job mechanics with explicit store/process/dispatch doubles; no provider proof.

Real manifest validation, template snapshots and durable record I/O are exercised.
The store double does NOT establish review-backed publication or human approval.
"""

import copy
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.core.execution_support import publish_source_store, source_grant

from codex_harness.core import planner_jobs as jobs
from codex_harness.core.plan_store import PlanStore
from codex_harness.core.record_io import read_record_json

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
