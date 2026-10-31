"""Managed path regressions through real dispatcher records; no provider launches."""

import json
from pathlib import Path

import pytest
from tests.python.core.test_dispatch import _tiny_plan

from codex_harness.core import dispatch, planner_jobs


def test_deep_job_paths_and_long_titles_keep_receipt_identities(tmp_path_factory):
    """Catch title/job path growth through Git and receipts, absent in small fixtures."""
    workspace = tmp_path_factory.mktemp("p") / "nested"
    workspace.mkdir()
    plan = _tiny_plan(workspace)
    job_id = "a" * 64
    job = planner_jobs._directory(workspace, job_id)
    lengths = []
    for title in ("Small", "A very long product title " * 30):
        manifest_path = plan / "sample.json"
        manifest = json.loads(manifest_path.read_text())
        if "A" in manifest["beads"]:
            manifest["beads"]["long-bead-identity-that-must-survive"] = manifest["beads"].pop("A")
        manifest["sample"] = title
        manifest_path.write_text(json.dumps(manifest))

        def worker(owned):
            return lambda worktree: (worktree / owned).write_text("VALUE = 1\n")

        result = dispatch.run_plan(
            plan,
            work_root=job / "runs",
            fake_workers={
                key: worker(bead["ownedFiles"][0]) for key, bead in manifest["beads"].items()
            },
        )
        assert result["status"] == dispatch.STATUS_MOCK_ONLY
        root = Path(result["repo"]).parent
        lengths.append(len(root.name))
        assert result["plan"] == title
        for bead_id, bead in result["beads"].items():
            request = json.loads((Path(bead["evidenceDir"]) / "attempt/request.json").read_text())
            assert request["bead"] == bead_id
            assert request["runId"] == result["runId"]
            assert request["planRevision"] == result["planRevision"]
        assert max(len(str(p)) for p in root.rglob("*")) <= 200
    assert lengths == [12, 12]


def test_overlong_run_refuses_before_creating_namespace(tmp_path):
    """Catch late Git failures before mutation; successful short roots cannot prove refusal."""
    work_root = tmp_path / ("x" * 100)
    with pytest.raises(ValueError, match="safe Windows limit.*shorten workspace"):
        dispatch.run_plan(_tiny_plan(tmp_path), work_root=work_root, mock_provider="reference")
    assert not work_root.exists()
