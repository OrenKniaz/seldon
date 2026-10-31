"""Source-only regressions for protected publication bytes and partial integration."""

import json
import shutil
from pathlib import Path

import pytest

from codex_harness.core import dispatch
from codex_harness.core.project_memory import ProjectMemoryStore
from codex_harness.core.spine import compile_manifest, load_spine

from .test_dispatch import FIXTURE, _tiny_plan


class SimulatedCrash(BaseException):
    """Bypass dispatch's ordinary worker-exception handling like process death."""


def test_compiled_protected_substitution_refused_before_worker(tmp_path):
    root = tmp_path / "published"
    shutil.copytree(FIXTURE, root)
    manifest = compile_manifest(load_spine(root / "spine.json"), root / "template")
    (root / "sample.json").write_text(json.dumps(manifest))
    proof = root / "template/tests/test_money.py"
    proof.write_text(
        proof.read_text().replace("self.assertEqual(value, cents)", "self.assertEqual(value, -1)")
    )
    called = []
    with pytest.raises(ValueError, match="requirement.*revision"):
        dispatch.run_plan(root, work_root=tmp_path / "runs", fake_workers={"A": called.append})
    assert not called
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize(
    "stage", ["candidate", "receipt", "fast-forward", "promotion", "confirmation"]
)
def test_partial_integration_resume_same_attempt(tmp_path, monkeypatch, stage):
    root = _tiny_plan(tmp_path)
    path = root / "sample.json"
    manifest = json.loads(path.read_text())
    del manifest["beads"]["B"]
    manifest["beads"]["A"]["lessons"] = [{"text": "Use integer cents", "decisionId": "D1"}]
    path.write_text(json.dumps(manifest))
    calls = []

    def worker(worktree):
        calls.append(str(worktree))
        (worktree / "A.py").write_text("VALUE = 1\n")

    original = {
        "candidate": dispatch._validate,
        "receipt": dispatch._write_record,
        "fast-forward": dispatch._git,
        "promotion": dispatch.promote_lesson,
        "confirmation": dispatch.confirm_lesson,
    }[stage]
    fired = []

    def interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        trigger = (
            (
                stage == "candidate"
                and dispatch._git(args[0], "branch", "--show-current").startswith("candidate/")
            )
            or (stage == "receipt" and Path(args[0]).name == "integrated.json")
            or (stage == "fast-forward" and "--ff-only" in args)
            or stage in {"promotion", "confirmation"}
        )
        if trigger and not fired:
            fired.append(True)
            raise SimulatedCrash("simulated crash")
        return result

    target = {
        "candidate": "_validate",
        "receipt": "_write_record",
        "fast-forward": "_git",
        "promotion": "promote_lesson",
        "confirmation": "confirm_lesson",
    }[stage]
    monkeypatch.setattr(dispatch, target, interrupt)
    with pytest.raises(SimulatedCrash, match="simulated crash"):
        dispatch.run_plan(root, work_root=tmp_path / "runs", fake_workers={"A": worker})
    monkeypatch.setattr(dispatch, target, original)
    run = next((tmp_path / "runs").iterdir())
    result = dispatch.run_plan(
        root, work_root=tmp_path / "runs", resume_run_dir=run, fake_workers={"A": worker}
    )
    assert result["status"] == dispatch.STATUS_MOCK_ONLY
    assert len(calls) == 1
    assert result["beads"]["A"]["attempt"]["cwd"] == calls[0]
    repo = Path(result["repo"])
    assert len(dispatch._git(repo, "rev-list", "--merges", "HEAD").splitlines()) == 1
    assert (
        len(dispatch._git(repo, "log", "--all", "--format=%s", "--grep=worker change").splitlines())
        == 1
    )
    assert len(ProjectMemoryStore(result["memoryWorkspace"]).confirmed_slice()) == 1


def _compiled_fixture(tmp_path):
    root = tmp_path / "published"
    shutil.copytree(FIXTURE, root)
    manifest = compile_manifest(load_spine(root / "spine.json"), root / "template")
    (root / "sample.json").write_text(json.dumps(manifest))
    return root, manifest


def test_compiled_unchanged_and_lf_normalized_control(tmp_path):
    root, manifest = _compiled_fixture(tmp_path)
    for path in (root / "template").rglob("*"):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    first = dispatch.run_plan(root, work_root=tmp_path / "runs", mock_provider="reference")
    assert first["status"] == dispatch.STATUS_MOCK_ONLY
    assert first["planRevision"] == manifest["planRevision"]
    resumed = dispatch.run_plan(
        root,
        work_root=tmp_path / "runs",
        mock_provider="reference",
        resume_run_dir=Path(first["repo"]).parent,
    )
    assert resumed["status"] == dispatch.STATUS_MOCK_ONLY
    assert all(bead["reused"] for bead in resumed["beads"].values())


def test_compiled_postcopy_bytes_checked_before_baseline_or_worker(tmp_path, monkeypatch):
    root, _ = _compiled_fixture(tmp_path)
    copy = shutil.copytree
    called = []

    def substitute(source, destination, *args, **kwargs):
        result = copy(source, destination, *args, **kwargs)
        if Path(source) == root / "template":
            proof = Path(destination) / "tests/test_money.py"
            proof.write_text(proof.read_text() + "\n# copy substitution\n")
        return result

    monkeypatch.setattr(shutil, "copytree", substitute)
    monkeypatch.setattr(dispatch, "_validate", lambda *args: pytest.fail("baseline validation ran"))
    with pytest.raises(ValueError, match="requirement.*revision"):
        dispatch.run_plan(
            root, work_root=tmp_path / "runs", fake_workers={"A": called.append, "B": called.append}
        )
    assert not called


def test_compiled_preintegration_rechecks_template_bytes(tmp_path):
    root, _ = _compiled_fixture(tmp_path)
    called = []

    def worker(worktree):
        shutil.copy2(root / "reference/tipsplit/money.py", worktree / "tipsplit/money.py")
        proof = root / "template/tests/test_money.py"
        proof.write_text(proof.read_text() + "\n# publication changed during attempt\n")

    result = dispatch.run_plan(
        root, work_root=tmp_path / "runs", fake_workers={"A": worker, "B": called.append}
    )
    assert "requirement files revision mismatch" in result["beads"]["A"]["status"]
    assert not called
    assert result["beads"]["A"]["integrated"] is None
    assert dispatch._git(Path(result["repo"]), "rev-list", "--merges", "HEAD") == ""


@pytest.mark.parametrize("change", ["template", "repo", "addition", "deletion", "missing-digest"])
def test_compiled_unchanged_revision_resume_refuses_protected_tamper(tmp_path, change):
    root, manifest = _compiled_fixture(tmp_path)
    first = dispatch.run_plan(root, work_root=tmp_path / "runs", mock_provider="reference")
    revision = manifest["planRevision"]
    target = root / "template/tests/test_money.py"
    if change == "repo":
        repo = Path(first["repo"])
        target = repo / "tests/test_money.py"
        target.write_text(target.read_text() + "\n# substituted\n")
        dispatch._git(repo, "add", "tests/test_money.py")
        dispatch._commit(repo, "substituted protected file")
    elif change == "addition":
        (target.parent / "test_added.py").write_text("# newly protected member\n")
    elif change == "deletion":
        target.unlink()
    elif change == "missing-digest":
        del manifest["requirementFilesRevision"]
        (root / "sample.json").write_text(json.dumps(manifest))
    else:
        target.write_text(target.read_text() + "\n# substituted\n")
    with pytest.raises(ValueError, match="requirement.*revision"):
        dispatch.run_plan(
            root,
            work_root=tmp_path / "runs",
            mock_provider="reference",
            resume_run_dir=Path(first["repo"]).parent,
        )
    assert json.loads((root / "sample.json").read_text())["planRevision"] == revision


@pytest.mark.parametrize("tamper", ["worker", "candidate", "attempt", "receipt", "orphan"])
def test_candidate_bound_state_mismatch_refuses_resume(tmp_path, monkeypatch, tamper):
    root = _tiny_plan(tmp_path)
    path = root / "sample.json"
    manifest = json.loads(path.read_text())
    del manifest["beads"]["B"]
    path.write_text(json.dumps(manifest))
    original = dispatch._write_record
    calls = []

    def worker(worktree):
        calls.append(str(worktree))
        (worktree / "A.py").write_text("VALUE = 1\n")

    def crash(path, *args):
        result = original(path, *args)
        if path.name == "integrated.json":
            raise SimulatedCrash()
        return result

    monkeypatch.setattr(dispatch, "_write_record", crash)
    with pytest.raises(SimulatedCrash):
        dispatch.run_plan(root, work_root=tmp_path / "runs", fake_workers={"A": worker})
    monkeypatch.setattr(dispatch, "_write_record", original)
    run = next((tmp_path / "runs").iterdir())
    state_path = run / "evidence/A/integration-state.json"
    state = json.loads(state_path.read_text())
    if tamper == "orphan":
        state_path.unlink()
    elif tamper == "receipt":
        receipt = state_path.with_name("integrated.json")
        payload = json.loads(receipt.read_text())
        payload["recoveryBinding"]["workerCommit"] = state["attemptBase"]
        receipt.write_text(json.dumps(payload))
    else:
        field = {"worker": "workerCommit", "candidate": "candidateCommit", "attempt": "runId"}[
            tamper
        ]
        state[field] = "foreign"
        state_path.write_text(json.dumps(state))
    result = dispatch.run_plan(
        root, work_root=tmp_path / "runs", resume_run_dir=run, fake_workers={"A": worker}
    )
    assert "integration recovery" in result["status"]
    assert len(calls) == 1
    assert dispatch._git(Path(result["repo"]), "rev-list", "--merges", "HEAD") == ""


def test_caught_candidate_interruption_recovers_existing_branch(tmp_path, monkeypatch):
    root = _tiny_plan(tmp_path)
    path = root / "sample.json"
    manifest = json.loads(path.read_text())
    del manifest["beads"]["B"]
    path.write_text(json.dumps(manifest))
    calls = []

    def worker(worktree):
        calls.append(str(worktree))
        (worktree / "A.py").write_text("VALUE = 1\n")

    validate = dispatch._validate

    def interrupted(cwd, *args, **kwargs):
        if dispatch._git(cwd, "branch", "--show-current").startswith("candidate/"):
            raise RuntimeError("interrupted candidate validation")
        return validate(cwd, *args, **kwargs)

    monkeypatch.setattr(dispatch, "_validate", interrupted)
    first = dispatch.run_plan(root, work_root=tmp_path / "runs", fake_workers={"A": worker})
    assert "interrupted candidate validation" in first["status"]
    monkeypatch.setattr(dispatch, "_validate", validate)
    result = dispatch.run_plan(
        root,
        work_root=tmp_path / "runs",
        resume_run_dir=Path(first["repo"]).parent,
        fake_workers={"A": worker},
    )
    assert result["status"] == dispatch.STATUS_MOCK_ONLY
    assert len(calls) == 1
    assert (
        len(dispatch._git(Path(result["repo"]), "rev-list", "--merges", "HEAD").splitlines()) == 1
    )
