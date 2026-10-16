"""Disposable canonical stores and explicitly synthetic run/intervention records."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from codex_harness.core.plan_store import PlanStore

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"


def files(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def invoke(workspace, run, *options):
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "codex_harness.core.plan_cli",
            "--workspace",
            str(workspace),
            "supervision-report",
            "--run",
            str(run),
            "--machine-json",
            *map(str, options),
        ],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def test_report_without_forecast_or_intervention_log_is_unknown_and_read_only(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = PlanStore(workspace)
    store.import_spine(FIXTURE)
    spine_revision = store.spine().plan_revision()
    store.close()
    run = workspace / "run"
    (run / "evidence").mkdir(parents=True)
    (run / "evidence/run.json").write_text(
        json.dumps(
            {
                "runId": "source-fixture",
                "planRevision": spine_revision,
                "startedAt": "2026-10-16T01:00:00Z",
                "finishedAt": "2026-10-16T01:02:00Z",
                "beads": {},
                "claims": [],
                "isMocked": True,
            }
        ),
        encoding="utf-8",
    )
    before = files(workspace)
    result = invoke(workspace, run)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)["data"]
    assert report["readOnly"] is True
    assert report["forecast"]["state"] == "unavailable"
    assert report["reconciliation"] is None
    assert report["interventions"]["total"] is None
    assert report["interventions"]["knownEvents"] is None
    assert report["interventions"]["knownByOrigin"]["human"] is None
    assert report["interventions"]["activeMinutes"] is None
    assert report["supervisionSavings"] is None
    assert report["source"]["planRevision"] == spine_revision
    assert files(workspace) == before


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(repo):
    git(repo, "add", "-A")
    git(
        repo,
        "-c",
        "user.name=Source fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "source fixture",
    )
    return git(repo, "rev-parse", "HEAD")


def write_run(directory, value):
    (directory / "evidence").mkdir(parents=True)
    (directory / "evidence/run.json").write_text(json.dumps(value), encoding="utf-8")


def scenario(tmp_path, *, forecast_kind="good", protected=False, semantic=False):
    """Real canonical store/compiler and Git; all run/forecast outcomes are source fixtures."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    shutil.copytree(FIXTURE.parent / "template", workspace / "template")
    store = PlanStore(workspace)
    store.import_spine(FIXTURE)
    vision = store.spine().node("V1")
    vision["fields"]["requirementFiles"] = ["tests"]
    store.upsert_node(vision)
    independent = store.spine().node("A")
    independent.update(id="C", title="Independent source fixture", parents=["R2"])
    independent["fields"].update(
        ownedFiles=["independent.py"], proofModule="test_independent.py", dependsOn=[]
    )
    store.upsert_node(independent)
    (workspace / "template/independent.py").write_text("VALUE=1\n")
    (workspace / "template/tests/test_independent.py").write_text(
        "# independent source fixture proof\n"
    )
    baseline = store.compile_template(workspace / "template")
    baseline_spine_revision = store.spine().plan_revision()
    if protected:
        proof = workspace / "template/tests/test_money.py"
        proof.write_text(proof.read_text() + "\n# fixture proof revision\n", encoding="utf-8")
    elif semantic:
        node = store.spine().node("R1")
        node["text"] += "\nA source fixture requirement edit."
        store.upsert_node(node)
    target = store.compile_template(workspace / "template")
    spine_revision = store.spine().plan_revision()
    expected_impact = store.impact_since(baseline["planRevision"])
    store.close()
    repo = workspace / "product"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "tipsplit").mkdir()
    (repo / "tipsplit/money.py").write_text("VALUE=0\n")
    (repo / "tipsplit/cli.py").write_text("VALUE=0\n")
    (repo / "independent.py").write_text("VALUE=0\n")
    (repo / "README.md").write_text("source fixture\n")
    old_commit = commit(repo)
    old_tree = git(repo, "rev-parse", "HEAD^{tree}")
    old = {
        "runId": "before-fixture",
        "planRevision": baseline["planRevision"],
        "integrationValidation": {"integrationCommit": old_commit, "candidateTree": old_tree},
        "isMocked": True,
    }
    forecast = {
        "createdAt": "2026-10-16T00:00:00Z",
        "baseline": {
            "planRevision": baseline_spine_revision,
            "runId": old["runId"],
            "publicationRevision": old["planRevision"],
            "candidateCommit": old_commit,
            "candidateTree": old_tree,
        },
        "target": {"planRevision": spine_revision, "publicationRevision": target["planRevision"]},
        "predicted": {
            "redispatchedBeads": ["EXTRA"],
            "revalidatedBeads": ["B"],
            "reusedBeads": ["B"],
            "retainedClaimIds": ["C-original"],
        },
        "evidenceKind": "synthetic-source-fixture",
    }
    if forecast_kind == "late":
        forecast["createdAt"] = "2026-10-16T03:00:00Z"
    if forecast_kind == "revision":
        forecast["target"]["planRevision"] = "0" * 64
    if forecast_kind == "candidate":
        forecast["baseline"]["candidateTree"] = "0" * 40
    forecast_path = repo / "impact-forecast.json"
    forecast_path.write_text(json.dumps(forecast), encoding="utf-8")
    frozen = commit(repo)
    (repo / "tipsplit/money.py").write_text("VALUE=1\n")
    (repo / "README.md").write_text("changed source fixture\n")
    outcome = commit(repo)
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    if forecast_kind == "unbound":
        forecast["predicted"]["redispatchedBeads"] = ["A"]
        forecast_path.write_text(json.dumps(forecast), encoding="utf-8")
    original_claim = {
        "claimId": "B-original",
        "bead": "B",
        "candidateCommit": old_commit,
        "candidateTree": old_tree,
        "passed": True,
    }
    independent_claim = {**original_claim, "claimId": "C-original", "bead": "C"}
    current_claim = {
        "claimId": "B-revalidation",
        "bead": "B",
        "candidateCommit": outcome,
        "candidateTree": tree,
        "passed": True,
    }
    run = {
        "runId": "after-fixture",
        "planRevision": target["planRevision"],
        "repo": str(repo),
        "baseline": {"commit": frozen},
        "startedAt": "2026-10-16T01:00:00Z",
        "finishedAt": "2026-10-16T01:02:00Z",
        "integrationValidation": {"integrationCommit": outcome, "candidateTree": tree},
        "resumeSummary": {
            "redispatched": ["A"],
            "reused": ["B"],
            "invalidated": [],
            "retained": [],
        },
        "claimSummary": {
            "impacted": [
                {"claimId": "A-old", "changedInputs": {"sourceIdentity": "changed"}},
                {"claimId": "B-original", "changedInputs": {"dependencyIdentities": "changed"}},
            ],
            "retained": [{"claimId": "C-original"}],
            "fresh": [current_claim],
        },
        "claims": [original_claim, independent_claim, current_claim],
        "beads": {"A": {}, "B": {"revalidation": {"claim": current_claim}}, "C": {}},
        "isMocked": True,
        "didExecuteRealProvider": False,
    }
    if expected_impact.get("protectedFilesChanged"):
        run["claimSummary"]["impacted"].append(
            {"claimId": "C-original", "changedInputs": {"verificationIdentity": "changed"}}
        )
        run["claimSummary"]["retained"] = []
    before_dir, run_dir = workspace / "before", workspace / "after"
    write_run(before_dir, old)
    write_run(run_dir, run)
    return workspace, before_dir, run_dir, forecast_path, run, expected_impact


def read_report(fixture, *args):
    workspace, baseline, run, forecast, _, _ = fixture
    result = invoke(workspace, run, "--baseline-run", baseline, "--forecast", forecast, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)["data"]


def test_frozen_forecast_misses_extras_reuse_and_proof_revalidation(tmp_path):
    fixture = scenario(tmp_path)
    before = files(fixture[0])
    report = read_report(fixture)
    assert report["forecast"]["state"] == "bound-records"
    assert report["reconciliation"]["redispatchedBeads"] == {
        "misses": ["A"],
        "extras": ["EXTRA"],
        "consistent": [],
    }
    assert report["actual"]["reusedBeads"] == ["B"]
    assert report["actual"]["revalidatedBeads"] == ["B"]
    assert report["actual"]["actualWorkerRedispatchedBeads"] is None
    assert report["actual"]["invalidatedClaimIds"] == ["A-old", "B-original"]
    assert report["actual"]["retainedClaimIds"] == ["C-original"]
    assert (
        report["actual"]["sourceCandidates"][0]["candidateCommit"]
        != report["source"]["candidateCommit"]
    )
    assert "C" not in report["actual"]["revalidatedBeads"]
    assert report["impact"]["affectedBeads"] == fixture[-1]["affectedBeads"]
    assert report["source"]["planRevision"] != report["source"]["publicationRevision"]
    assert report["impact"]["reportingBreakdown"]["semanticTraceBeads"] == []
    assert report["impact"]["reportingBreakdown"]["dependencyConsumers"] == []
    assert report["impact"]["reportingBreakdown"]["protectedProofBeads"] == []
    assert report["actual"]["changedSourceOwners"] == ["A"]
    assert report["actual"]["dependentProofConsumers"] == ["B"]
    assert report["actual"]["unaffectedSourceOwners"] == ["C"]
    assert report["supervisionSavings"] is None
    assert files(fixture[0]) == before


@pytest.mark.parametrize("kind", ["late", "unbound", "revision", "candidate"])
def test_invalid_forecast_cannot_score_perfect_prediction(tmp_path, kind):
    report = read_report(scenario(tmp_path, forecast_kind=kind))
    assert report["forecast"]["state"] == "unavailable"
    assert report["reconciliation"] is None
    assert any(row["code"] == "frozen-forecast-binding" for row in report["missingDependencies"])


def test_protected_test_revision_preserves_broad_canonical_proof_impact(tmp_path):
    report = read_report(scenario(tmp_path, protected=True))
    breakdown = report["impact"]["reportingBreakdown"]
    assert breakdown["directChangedNodes"] == []
    assert breakdown["semanticTraceBeads"] == []
    assert breakdown["dependencyConsumers"] == []
    assert breakdown["protectedProofBeads"] == ["A", "B", "C"]
    assert report["impact"]["protectedFilesChanged"] is True


def test_semantic_trace_dependency_and_protected_effects_are_separate(tmp_path):
    report = read_report(scenario(tmp_path, semantic=True))
    effects = report["impact"]["reportingBreakdown"]
    assert "R1" in effects["directChangedNodes"]
    assert effects["semanticTraceBeads"] == ["A"]
    assert effects["dependencyConsumers"] == ["B"]
    assert effects["protectedProofBeads"] == ["A", "B", "C"]
    assert report["actual"]["retainedClaimIds"] == []


def test_unrelated_later_commit_does_not_rewrite_original_claim_or_actual_window(tmp_path):
    fixture = scenario(tmp_path)
    original = deepcopy(fixture[4]["claims"])
    repo = Path(fixture[4]["repo"])
    (repo / "unrelated.txt").write_text("later source fixture\n")
    later = commit(repo)
    report = read_report(fixture)
    assert report["source"]["candidateCommit"] == later
    assert report["actual"]["outcomeCandidate"]["candidateCommit"] != later
    assert report["actual"]["claims"] == original
    assert "unrelated.txt" not in report["actual"]["changedPaths"]
    assert report["forecast"]["state"] == "bound-records"


def test_wrong_original_candidate_and_stale_publication_stay_unknown(tmp_path):
    fixture = scenario(tmp_path)
    record = fixture[4]
    record["integrationValidation"]["candidateTree"] = "0" * 40
    (fixture[2] / "evidence/run.json").write_text(json.dumps(record))
    report = read_report(fixture)
    assert report["reconciliation"] is None
    assert report["actual"]["changedPaths"] is None
    assert any(row["code"] == "original-product-candidate" for row in report["missingDependencies"])


@pytest.mark.parametrize(
    "kind", ["wrong-tree", "latest-failure", "conflicting-alias", "missing-original"]
)
def test_retained_observation_never_resurrects_contradictory_or_missing_claim(tmp_path, kind):
    fixture = scenario(tmp_path)
    record = fixture[4]
    claim = next(item for item in record["claims"] if item["bead"] == "C")
    if kind == "wrong-tree":
        claim["candidateTree"] = "0" * 40
    elif kind == "latest-failure":
        record["claims"].append({**claim, "claimId": "C-current-failed", "passed": False})
    elif kind == "conflicting-alias":
        record["claims"].append({**claim, "passed": False})
    else:
        record["claims"].remove(claim)
    (fixture[2] / "evidence/run.json").write_text(json.dumps(record))
    report = read_report(fixture)
    assert report["actual"]["retainedClaimIds"] is None
    assert report["reconciliation"]["retainedClaimIds"] is None
    assert any(row["code"] == "original-retained-claim" for row in report["missingDependencies"])


def test_original_duplicate_rows_do_not_inflate_claim_or_reuse_counts(tmp_path):
    fixture = scenario(tmp_path)
    record = fixture[4]
    record["claims"].append(deepcopy(record["claims"][1]))
    record["claimSummary"]["retained"].append({"claimId": "C-original"})
    record["resumeSummary"]["reused"].append("B")
    (fixture[2] / "evidence/run.json").write_text(json.dumps(record))
    report = read_report(fixture)
    assert report["actual"]["retainedClaimIds"] == ["C-original"]
    assert report["actual"]["reusedBeads"] == ["B"]


def test_latest_impacted_claim_cannot_resurrect_older_retained_identity(tmp_path):
    fixture = scenario(tmp_path)
    record = fixture[4]
    old = next(claim for claim in record["claims"] if claim["bead"] == "C")
    latest = {**old, "claimId": "C-current-invalidated", **record["integrationValidation"]}
    latest["candidateCommit"] = record["integrationValidation"]["integrationCommit"]
    record["claims"].append(latest)
    record["claimSummary"]["impacted"].append(
        {
            "claimId": latest["claimId"],
            "changedInputs": {"verificationIdentity": "authoritative-current-invalidation"},
        }
    )
    (fixture[2] / "evidence/run.json").write_text(json.dumps(record))
    report = read_report(fixture)
    assert report["actual"]["retainedClaimIds"] is None
    assert report["reconciliation"]["retainedClaimIds"] is None
    assert any(
        "current-failure-or-invalidation" in row["detail"] for row in report["missingDependencies"]
    )
    assert report["actual"]["actualWorkerRedispatchedBeads"] is None
    assert report["supervisionSavings"] is None


def test_fresh_distinct_retained_claim_does_not_inherit_older_invalidation(tmp_path):
    fixture = scenario(tmp_path)
    record = fixture[4]
    old = next(claim for claim in record["claims"] if claim["bead"] == "C")
    fresh = {
        **old,
        "claimId": "C-fresh",
        "candidateCommit": record["integrationValidation"]["integrationCommit"],
        "candidateTree": record["integrationValidation"]["candidateTree"],
    }
    record["claims"].append(fresh)
    record["claimSummary"]["impacted"].append(
        {
            "claimId": old["claimId"],
            "changedInputs": {"verificationIdentity": "historical-invalidation"},
        }
    )
    record["claimSummary"]["retained"] = [{"claimId": fresh["claimId"]}]
    (fixture[2] / "evidence/run.json").write_text(json.dumps(record))
    report = read_report(fixture)
    assert report["actual"]["retainedClaimIds"] == ["C-fresh"]
    assert not any(
        row["code"] == "original-retained-claim" for row in report["missingDependencies"]
    )


def test_contained_repository_cannot_redirect_git_metadata_outside_canonical_roots(
    tmp_path, monkeypatch
):
    from codex_harness.core import supervision

    fixture = scenario(tmp_path)
    external = tmp_path / "disposable-external-repo"
    external.mkdir()
    git(external, "init", "-q")
    (external / "value.txt").write_text("owned local source fixture\n")
    foreign_commit = commit(external)
    linked = fixture[0] / "linked-product"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {external / '.git'}\n")
    fixture[4]["repo"] = str(linked)
    (fixture[2] / "evidence/run.json").write_text(json.dumps(fixture[4]))
    calls, real_git = [], supervision._git

    def observe_git(*args):
        calls.append(args)
        return real_git(*args)

    monkeypatch.setattr(supervision, "_git", observe_git)
    report = supervision.report(
        fixture[0], fixture[2], baseline_run=fixture[1], forecast=fixture[3]
    )
    assert report["source"]["candidateCommit"] is None, (
        f"Foreign fixture commit was observed: {foreign_commit}"
    )
    assert report["source"]["candidateTree"] is None
    assert report["actual"]["retainedClaimIds"] is None
    assert calls == []  # Refuse before any inferred Git read, including retained-claim reads.
    assert any("git-directory-outside" in row["detail"] for row in report["missingDependencies"])
    assert report["reconciliation"] is None


def test_commondir_cannot_escape_product_metadata_before_git_reads(tmp_path, monkeypatch):
    from codex_harness.core import supervision

    fixture = scenario(tmp_path)
    external = tmp_path / "disposable-common-git"
    external.mkdir()
    git(external, "init", "-q")
    (external / "foreign.txt").write_text("new owned common-directory fixture\n")
    foreign_commit = commit(external)
    repo = Path(fixture[4]["repo"])
    (repo / ".git/commondir").write_text(str(external / ".git") + "\n")
    (repo / ".git/HEAD").write_bytes((external / ".git/HEAD").read_bytes())
    calls, real_git = [], supervision._git

    def observe_git(*args):
        calls.append(args)
        return real_git(*args)

    monkeypatch.setattr(supervision, "_git", observe_git)
    result = supervision.report(
        fixture[0], fixture[2], baseline_run=fixture[1], forecast=fixture[3]
    )
    assert result["source"]["candidateCommit"] is None, (
        f"Foreign common commit was read: {foreign_commit}"
    )
    assert result["source"]["candidateTree"] is None
    assert calls == []
    assert result["actual"]["retainedClaimIds"] is None
    assert result["reconciliation"] is None
    assert any("commondir" in row["detail"] for row in result["missingDependencies"])


def test_stale_run_publication_cannot_bind_to_current_editable_plan(tmp_path):
    fixture = scenario(tmp_path)
    store = PlanStore(fixture[0])
    node = store.spine().node("R1")
    node["text"] += "\nLater revision not in the original run."
    store.upsert_node(node)
    store.compile_template(fixture[0] / "template")
    store.close()
    report = read_report(fixture)
    assert report["reconciliation"] is None
    assert report["source"]["runPublicationRevision"] != report["source"]["publicationRevision"]
    assert any(
        row["code"] == "publication-revision-mismatch" for row in report["missingDependencies"]
    )


def test_report_never_creates_absent_workspace_or_store(tmp_path):
    workspace = tmp_path / "absent"
    result = invoke(workspace, tmp_path / "absent-run")
    assert result.returncode == 0
    assert json.loads(result.stdout)["data"]["source"]["planRevision"] is None
    assert not workspace.exists()


def test_malformed_report_command_does_not_initialize_workspace(tmp_path):
    workspace = tmp_path / "absent"
    malformed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "codex_harness.core.plan_cli",
            "--workspace",
            str(workspace),
            "supervision-report",
        ],
        capture_output=True,
        text=True,
    )
    assert malformed.returncode == 2
    assert not workspace.exists()


def test_active_canonical_store_is_observed_without_creating_or_mutating_files(tmp_path):
    store = PlanStore(tmp_path)
    try:
        store.import_spine(FIXTURE)
        revision = store.spine().plan_revision()
        run = tmp_path / "run"
        write_run(run, {"runId": "source-fixture", "planRevision": revision, "beads": {}})
        before = files(tmp_path)
        result = invoke(tmp_path, run)
        assert result.returncode == 0
        assert json.loads(result.stdout)["data"]["source"]["planRevision"] == revision
        assert files(tmp_path) == before
    finally:
        store.close()


def test_existing_mock_dispatch_producer_records_reach_read_only_cli(tmp_path):
    from codex_harness.core.dispatch import run_plan

    workspace = tmp_path / "project"
    shutil.copytree(FIXTURE.parent, workspace)
    store = PlanStore(workspace)
    store.import_spine(FIXTURE)
    manifest = store.compile_template(workspace / "template")
    store.close()
    (workspace / "sample.json").write_text(json.dumps(manifest))
    original = run_plan(workspace, work_root=workspace / "runs", mock_provider="reference")
    run = workspace / "runs" / original["runId"]
    before = files(workspace)
    result = invoke(workspace, run)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)["data"]
    assert report["source"]["runId"] == original["runId"]
    assert report["source"]["publicationRevision"] == original["planRevision"]
    assert report["actual"]["claims"] == original["claims"]
    assert (
        report["actual"]["outcomeCandidate"]["candidateCommit"]
        == original["integrationValidation"]["integrationCommit"]
    )
    assert report["actual"]["actualWorkerRedispatchedBeads"] is None
    assert original["didExecuteRealProvider"] is False
    assert report["isLiveProductProof"] is False
    assert report["supervisionSavings"] is None
    assert files(workspace) == before


def test_missing_store_is_not_created_when_original_run_exists(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    run = workspace / "run"
    write_run(run, {"runId": "source-fixture", "planRevision": "0" * 64, "beads": {}})
    before = files(workspace)
    result = invoke(workspace, run)
    assert result.returncode == 0
    report = json.loads(result.stdout)["data"]
    assert any(row["code"] == "canonical-plan-impact" for row in report["missingDependencies"])
    assert not (workspace / ".codex-harness").exists()
    assert files(workspace) == before


def test_inferred_repository_outside_roots_is_not_read(tmp_path, monkeypatch):
    from codex_harness.core import supervision

    fixture = scenario(tmp_path)
    fixture[4]["repo"] = str(tmp_path / "foreign-repository")
    (fixture[2] / "evidence/run.json").write_text(json.dumps(fixture[4]))
    calls = []
    monkeypatch.setattr(supervision, "_git", lambda *args: calls.append(args))
    report = supervision.report(
        fixture[0], fixture[2], baseline_run=fixture[1], forecast=fixture[3]
    )
    assert calls == []
    assert report["reconciliation"] is None
    assert any(row["code"] == "original-product-candidate" for row in report["missingDependencies"])


def test_intervention_origins_partial_duration_and_no_verified_human_inference(tmp_path):
    from codex_harness.core.supervision import interventions

    run = {
        "runId": "fixture",
        "startedAt": "2026-10-16T01:00:00Z",
        "finishedAt": "2026-10-16T01:02:00Z",
    }
    events = [
        {"id": str(index), "origin": origin, "at": "2026-10-16T01:01:00Z", "activeMinutes": minutes}
        for index, (origin, minutes) in enumerate(
            [("human", 2), ("proxy", None), ("agent-driver", 3), ("unspecified", 4)]
        )
    ]
    path = tmp_path / "interventions.json"
    log = {
        "runId": "fixture",
        "coverage": "partial",
        "windowStart": run["startedAt"],
        "windowEnd": run["finishedAt"],
        "events": events,
        "humanOriginVerified": True,
    }
    path.write_text(json.dumps(log))
    report = interventions(path, run)
    assert report["knownByOrigin"] == {"human": 1, "proxy": 1, "agent-driver": 1, "unknown": 1}
    assert report["knownActiveMinutesByOrigin"] == {
        "human": 2,
        "proxy": None,
        "agent-driver": 3,
        "unknown": 4,
    }
    assert (
        report["total"]
        is report["activeMinutes"]
        is report["idleMinutes"]
        is report["wallMinutes"]
        is None
    )
    assert report["humanOriginVerified"] is False
    log["coverage"] = "complete"
    path.write_text(json.dumps(log))
    report = interventions(path, run)
    assert report["total"] == 4 and report["activeMinutes"] is None
    assert report["wallMinutes"] == 2
    log["events"] = [events[0], events[0]]
    path.write_text(json.dumps(log))
    report = interventions(path, run)
    assert report["knownEvents"] == report["total"] == 1
    assert report["knownActiveMinutesByOrigin"]["human"] == 2
    assert report["activeMinutes"] is None  # No compatible interval evidence.


@pytest.mark.parametrize(
    "kind", ["missing-window", "negative", "nonfinite", "overlap", "conflicting-alias", "wrong-run"]
)
def test_incomplete_or_incompatible_timing_never_creates_complete_minutes(tmp_path, kind):
    from codex_harness.core.supervision import report

    fixture = scenario(tmp_path)
    event = {
        "id": "recorded-human",
        "origin": "human",
        "at": "2026-10-16T01:00:00Z",
        "activeStart": "2026-10-16T01:00:00Z",
        "activeEnd": "2026-10-16T01:01:00Z",
        "activeMinutes": 1,
    }
    log = {
        "runId": "after-fixture",
        "coverage": "complete",
        "windowStart": "2026-10-16T01:00:00Z",
        "windowEnd": "2026-10-16T01:02:00Z",
        "events": [event],
    }
    if kind == "missing-window":
        del log["windowEnd"]
    elif kind == "negative":
        event["activeMinutes"] = -1
    elif kind == "nonfinite":
        event["activeMinutes"] = float("nan")
    elif kind == "overlap":
        log["events"].append({**event, "id": "overlapping-agent", "origin": "agent-driver"})
    elif kind == "conflicting-alias":
        log["events"].append({**event, "origin": "proxy"})
    else:
        log["runId"] = "foreign-run"
    path = fixture[0] / "interventions.json"
    path.write_text(json.dumps(log))
    result = report(fixture[0], fixture[2], intervention_log=path)
    assert result["interventions"]["activeMinutes"] is None
    assert result["interventions"]["idleMinutes"] is None
    assert result["supervisionSavings"] is None
    assert result["interventions"]["humanOriginVerified"] is False


def test_complete_compatible_recorded_timing_remains_unverified_origin(tmp_path):
    from codex_harness.core.supervision import interventions

    run = {
        "runId": "fixture",
        "startedAt": "2026-10-16T01:00:00Z",
        "finishedAt": "2026-10-16T01:02:00Z",
    }
    event = {
        "id": "recorded-human",
        "origin": "human",
        "at": run["startedAt"],
        "activeStart": run["startedAt"],
        "activeEnd": "2026-10-16T01:01:00Z",
        "activeMinutes": 1,
    }
    path = tmp_path / "interventions.json"
    path.write_text(
        json.dumps(
            {
                "runId": "fixture",
                "coverage": "complete",
                "windowStart": run["startedAt"],
                "windowEnd": run["finishedAt"],
                "events": [event],
            }
        )
    )
    result = interventions(path, run)
    assert result["total"] == 1 and result["activeMinutes"] == 1
    assert result["wallMinutes"] == 2 and result["idleMinutes"] is None
    assert result["humanOriginVerified"] is False
