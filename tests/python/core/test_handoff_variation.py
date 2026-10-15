"""An unusual canonical plan through compile, audit and mock dispatch."""

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from codex_harness.core import dispatch, plan_cli
from codex_harness.core.dispatch import STATUS_MOCK_ONLY, _claim_inputs, _commit, _git, run_plan
from codex_harness.core.graph import load_plan
from codex_harness.core.plan_store import PlanStore
from codex_harness.core.spine import MARKDOWN_FILES, compile_manifest, load_spine
from codex_harness.core.test_audit import audit_revised_tests

FIXTURE = Path(__file__).parent / "fixtures/handoff_variation"


@pytest.fixture
def variation(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "get_adapter", lambda *args: pytest.fail("real provider launch"))
    workspace = tmp_path / "plan"
    shutil.copytree(FIXTURE, workspace, ignore=shutil.ignore_patterns("__pycache__"))
    with PlanStore(workspace) as store:
        store.import_spine(workspace / "spine.json")
    assert (
        plan_cli.main(
            [
                "--workspace",
                str(workspace),
                "compile",
                "--template",
                "template",
                "--out",
                "manifest.json",
            ]
        )
        == 0
    )
    return workspace


def test_compile_binds_implicit_test_directory(variation):
    spine = load_spine(variation / "spine.json")
    assert spine.node("V1")["fields"]["requirementFiles"] == []
    before = compile_manifest(spine, variation / "template")
    assert "checks" in before["requirementFiles"]
    proof = variation / "template/checks/proof_core.py"
    proof.write_text(proof.read_text() + "\n# changed protected proof\n", encoding="utf-8")
    after = compile_manifest(spine, variation / "template")
    assert before["planRevision"] != after["planRevision"]
    with PlanStore(variation) as store:
        assert store.manifest_revision() == after["planRevision"]


def test_variation_audit_dispatch_packets_and_candidate_binding(variation, tmp_path):
    manifest_path = variation / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert set(MARKDOWN_FILES) <= set(manifest["requirementFiles"])
    assert all((variation / "template" / name).is_file() for name in MARKDOWN_FILES)
    plan = load_plan(manifest_path)
    assert not plan.beads["A"].depends_on and not plan.beads["B"].depends_on
    assert plan.beads["C"].depends_on == ("A", "B")
    audit = audit_revised_tests(
        repo=variation,
        revised_template=variation / "template",
        manifest=manifest,
        impact={"affectedBeads": list(plan.beads)},
        oracle_dir=variation / "reference",
    )
    assert audit["baseline"] == "template"
    assert audit["ok"] and audit["integrationChecked"] and audit["integrationProof"]["passed"]
    record = run_plan(manifest_path, work_root=tmp_path / "runs", mock_provider="reference")
    assert record["status"] == STATUS_MOCK_ONLY
    assert record["isMocked"] and not record["didExecuteRealProvider"]
    assert not record["isLiveProductProof"]
    assert record["integrationValidation"]["passed"]
    repo = Path(record["repo"])
    for bead_id, bead in record["beads"].items():
        assert bead["integrated"]["validation"]["passed"]
        packet = bead["prompt"]
        assert (
            f"python -B -m unittest discover -s checks -p {plan.beads[bead_id].proof_module}"
            in packet
        )
        commands = [
            line for line in packet.splitlines() if line.startswith("python -B -m unittest")
        ]
        assert len(commands) >= 2
        for command in commands:
            result = subprocess.run(
                [sys.executable, *shlex.split(command)[1:]],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert "Ran 1 test" in result.stderr
    claim = record["integrationValidation"]["claim"]
    assert claim["candidateCommit"] == _git(repo, "rev-parse", "HEAD")
    assert claim["candidateTree"] == _git(repo, "rev-parse", "HEAD^{tree}")
    before = _claim_inputs(repo, plan, "A", "HEAD")["verificationIdentity"]
    proof = repo / "checks/proof_core.py"
    proof.write_text(proof.read_text() + "\n# changed verification\n", encoding="utf-8")
    _git(repo, "add", "checks/proof_core.py")
    _commit(repo, "change proof identity in temporary fixture")
    assert before != _claim_inputs(repo, plan, "A", "HEAD")["verificationIdentity"]
    # Existing-data audit must use committed code, never a dirty workspace body.
    (repo / "app/core.py").write_text("raise RuntimeError('dirty code')\n", encoding="utf-8")
    audit = audit_revised_tests(
        repo=repo,
        revised_template=variation / "template",
        manifest=manifest,
        impact={"affectedBeads": []},
        oracle_dir=variation / "reference",
    )
    assert audit["baseline"] == "HEAD" and audit["ok"]
    assert (repo / "app/core.py").read_text() == "raise RuntimeError('dirty code')\n"


def test_variation_rejects_worker_edit_under_checks(variation, tmp_path):
    def worker(worktree):
        shutil.copy2(variation / "reference/app/core.py", worktree / "app/core.py")
        proof = worktree / "checks/proof_core.py"
        proof.write_text(proof.read_text() + "\n# worker changed proof\n", encoding="utf-8")

    record = run_plan(
        variation / "manifest.json",
        work_root=tmp_path / "runs",
        mock_provider="reference",
        fake_workers={"A": worker},
    )
    bead = record["beads"]["A"]
    assert bead["status"] == "rejected: requirement files changed"
    assert not bead["guard"]["requirementFilesIntact"]
    assert bead["integrated"] is None
    assert record["beads"]["C"]["status"] == "blocked: dependency A failed"
