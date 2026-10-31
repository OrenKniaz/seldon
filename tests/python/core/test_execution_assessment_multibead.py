"""Real canonical two-bead source children reach the public targeted assessment."""

import hashlib
import json
from pathlib import Path

import pytest
from tests.python.core.test_acceptance import public_assessment_repair_fixture

from codex_harness.core.dispatch import (
    _claim_inputs,
    _commit,
    _git,
    read_completed_execution_evidence,
)
from codex_harness.core.graph import load_plan
from codex_harness.core.mcp_planner import PlannerServer


def test_earlier_unchanged_bead_can_prepare_assessment_after_later_integration(
    tmp_path, monkeypatch
):
    # Existing one-bead assessment fixtures delete B, so they cannot expose the
    # reader incorrectly requiring A's original integration to remain final HEAD.
    store, server, tool, calls, finding, current, repo, reviewers, _evidence = (
        public_assessment_repair_fixture(tmp_path, monkeypatch, second_bead=True)
    )
    try:
        job_id = store._meta()["published_revision"]
        job = tool("harness_worker_status", {"job_id": job_id, "include_detail": True})
        run_path = Path(job["run"]["recordPath"])
        run = json.loads(run_path.read_text(encoding="utf-8"))
        original_a = run["beads"]["A"]["integrated"]["integrationCommit"]
        final_b = run["beads"]["B"]["integrated"]["integrationCommit"]
        assert job["status"] == "completed"
        assert original_a != final_b == _git(repo, "rev-parse", "HEAD")
        assert _git(repo, "merge-base", "--is-ancestor", original_a, final_b) == ""
        assert _git(repo, "rev-parse", f"{original_a}:A.py") == _git(repo, "rev-parse", "HEAD:A.py")
        plan = load_plan(run_path.parents[3] / "plan")
        assert _claim_inputs(repo, plan, "A", original_a) == _claim_inputs(repo, plan, "A", final_b)
        originals = Path(run["beads"]["A"]["evidenceDir"])
        before = {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in originals.rglob("*")
            if path.is_file()
        }
        result = server.call_tool(
            "harness_accept_assess", {"prepare": {"finding_ids": [finding["id"]], "bead_id": "A"}}
        )
        (tmp_path / "w5-assessment-evidence.json").write_text(
            json.dumps(
                dict(
                    sourceOnly=True,
                    nativeProviderCalls=0,
                    workerJobId=job_id,
                    runRecord=str(run_path),
                    originalA=original_a,
                    finalB=final_b,
                    publicResult=result,
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
        assert not result["isError"], result["structuredContent"]
        prepared = result["structuredContent"]
        assert prepared["requestTemplate"]["expected_product_run_id"] == current["id"]
        assert prepared["artifacts"] and prepared["witnesses"]
        server.store.close()
        reopened = PlannerServer(store.workspace)
        after_reopen = reopened.call_tool(
            "harness_accept_assess", {"prepare": {"finding_ids": [finding["id"]], "bead_id": "A"}}
        )
        assert not after_reopen["isError"], after_reopen
        assert after_reopen["structuredContent"]["witnesses"] == prepared["witnesses"]
        evidence = read_completed_execution_evidence(
            store.workspace, job_id, "A", expected_repo=repo, expected_publication_revision=job_id
        )
        assert evidence["candidateCommit"] == final_b
        assert evidence["candidateTree"] == _git(repo, "rev-parse", "HEAD^{tree}")
        assert evidence["integrated"]["integrationCommit"] == original_a
        request = prepared["requestTemplate"]
        request.update(reviewer=reviewers[0], max_rounds=6, max_total=6, timeout=15)
        request["members"][0].update(
            artifactPaths=[prepared["artifacts"][0]["path"]],
            witnessIds=[prepared["witnesses"][0]["id"]],
        )
        assessed = reopened.call_tool("harness_accept_assess", {"request": request})
        (tmp_path / "w5-assessment-consumption.json").write_text(
            json.dumps(assessed, indent=2), encoding="utf-8"
        )
        assert not assessed["isError"], assessed
        assert assessed["structuredContent"]["members"][0]["support"] == "assessed-current"
        assert len(calls) == 3  # Two original reviews, one SOURCE-ONLY assessment child.
        reopened.store.close()
        assert {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in originals.rglob("*")
            if path.is_file()
        } == before
    finally:
        store.close()


def test_current_inputs_lineage_and_receipts_must_remain_proven(tmp_path, monkeypatch):
    # This actual D->A->B job supplies a real exercised prerequisite. A historical
    # passing claim cannot cover changed source/proofs/dependencies, an unproven
    # descendant, an unrelated tree, or corrupted original integration receipts.
    store, _server, tool, _calls, _finding, _current, repo, _reviewers, _evidence = (
        public_assessment_repair_fixture(tmp_path, monkeypatch, second_bead=True, prerequisite=True)
    )
    try:
        job_id = store._meta()["published_revision"]
        job = tool("harness_worker_status", {"job_id": job_id, "include_detail": True})
        run_path = Path(job["run"]["recordPath"])
        run = json.loads(run_path.read_text(encoding="utf-8"))
        canonical_head = _git(repo, "rev-parse", "HEAD")
        canonical_tree = _git(repo, "rev-parse", "HEAD^{tree}")
        plan = load_plan(run_path.parents[3] / "plan")
        baseline = _claim_inputs(repo, plan, "A", canonical_head)
        assert baseline["dependencyIdentities"].keys() == {"D"}

        def read():
            return read_completed_execution_evidence(
                store.workspace,
                job_id,
                "A",
                expected_repo=repo,
                expected_publication_revision=job_id,
            )

        assert read()["candidateCommit"] == canonical_head
        owned_path = repo / "A.py"
        owned_bytes = owned_path.read_bytes()
        _git(repo, "update-index", "--assume-unchanged", "A.py")
        owned_path.write_bytes(owned_bytes + b"\n# Hidden dirty input in disposable fixture\n")
        assert _git(repo, "status", "--porcelain") == ""
        with pytest.raises(ValueError, match="execution-current-integrated-witness-required"):
            read()
        owned_path.write_bytes(owned_bytes)
        _git(repo, "update-index", "--no-assume-unchanged", "A.py")
        run_bytes = run_path.read_bytes()
        for stale_surface in ("claims", "requirementValidation"):
            changed_run = json.loads(run_bytes)
            selected_rows = [item for item in changed_run[stale_surface] if item["bead"] == "A"]
            selected_rows[-1]["passed"] = False
            run_path.write_text(json.dumps(changed_run), encoding="utf-8")
            with pytest.raises(ValueError, match="execution-current-selected-proof-required"):
                read()
            run_path.write_bytes(run_bytes)
        for name, field in (
            ("A.py", "sourceIdentity"),
            ("tests/test_A.py", "verificationIdentity"),
            ("D.py", "dependencyIdentities"),
        ):
            path = repo / name
            path.write_bytes(path.read_bytes() + b"\n# SOURCE-ONLY changed input\n")
            _git(repo, "add", "--", name)
            _commit(repo, "Source-only stale-input counterfactual")
            assert _claim_inputs(repo, plan, "A", "HEAD")[field] != baseline[field]
            with pytest.raises(ValueError, match="execution-selected-proof-inputs-changed"):
                read()
            _git(repo, "restore", f"--source={canonical_head}", "--worktree", "--", name)
            _git(repo, "add", "--", name)
            _commit(repo, "Restore isolated fixture input")
        assert _claim_inputs(repo, plan, "A", "HEAD") == baseline
        assert _git(repo, "rev-parse", "HEAD^{tree}") == canonical_tree
        with pytest.raises(ValueError, match="execution-current-head-proof-required"):
            read()  # Same checked inputs, but this manual descendant has no job proof.
        unrelated = _git(
            repo,
            "-c",
            "user.name=Source Test",
            "-c",
            "user.email=source@example.invalid",
            "commit-tree",
            canonical_tree,
            "-m",
            "Unrelated identical source-only tree",
        )
        _git(repo, "switch", "--detach", unrelated)
        with pytest.raises(ValueError, match="execution-original-candidate-not-ancestor"):
            read()
        _git(repo, "switch", "--detach", canonical_head)
        assert read()["candidateCommit"] == canonical_head
        for identity, reason in (
            ("B", "execution-current-head-proof-required"),
            ("A", "execution-current-integrated-witness-required"),
        ):
            receipt = Path(run["beads"][identity]["evidenceDir"]) / "integrated.json"
            raw = receipt.read_bytes()
            changed = json.loads(raw)
            changed["validation"]["passed"] = False
            receipt.write_text(json.dumps(changed), encoding="utf-8")
            with pytest.raises(ValueError, match=reason):
                read()
            receipt.write_bytes(raw)  # Independent counterfactuals in this disposable fixture.
    finally:
        store.close()
