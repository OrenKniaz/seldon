"""Source-only regressions for protected publication bytes and partial integration."""

import json
from pathlib import Path

import pytest

from codex_harness.core import dispatch
from codex_harness.core.project_memory import ProjectMemoryStore

from .test_dispatch import _tiny_plan


class SimulatedCrash(BaseException):
    """Bypass dispatch's ordinary worker-exception handling like process death."""


@pytest.mark.parametrize("stage", ["receipt", "fast-forward"])
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
