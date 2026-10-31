"""Canonical plan/mail readers, dispatcher receipts and read-only HTTP behavior."""

from pathlib import Path

from codex_harness.core.status_api import snapshot

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit" / "spine.json"


def _mtimes(root):
    return {str(path): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}


def test_missing_sections_do_not_initialize_storage(tmp_path):
    missing = tmp_path / "missing"
    result = snapshot(workspace=missing, runs_root=missing)
    assert result["plan"] == result["runs"] == "unavailable"
    assert not missing.exists()
    assert snapshot()["plan"] == "unavailable"
    result = snapshot(workspace=tmp_path, runs_root=tmp_path)
    assert result["plan"] == "unavailable" and result["runs"] == []
    assert not (tmp_path / ".codex-harness").exists()


def test_real_dispatcher_record_and_resume_reach_snapshot(tmp_path, monkeypatch):
    from codex_harness.core.dispatch import run_plan

    def no_provider(*args):
        raise AssertionError("No real providers are authorized")

    monkeypatch.setattr("codex_harness.core.dispatch.get_adapter", no_provider)
    record = run_plan(FIXTURE.parent, work_root=tmp_path, mock_provider="reference")
    before = _mtimes(tmp_path)
    viewed = snapshot(runs_root=tmp_path)["runs"][0]
    assert _mtimes(tmp_path) == before
    assert viewed["runId"] == record["runId"]
    assert viewed["isMocked"] is True and viewed["didExecuteRealProvider"] is False
    assert viewed["liveAcceptance"]["claimed"] is False
    resumed = run_plan(
        FIXTURE.parent,
        work_root=tmp_path,
        resume_run_dir=tmp_path / record["runId"],
        mock_provider="reference",
    )
    before = _mtimes(tmp_path)
    viewed = snapshot(runs_root=tmp_path)["runs"][0]
    assert _mtimes(tmp_path) == before
    assert viewed["status"] == resumed["status"]
    assert viewed["planRevision"] == resumed["planRevision"]
