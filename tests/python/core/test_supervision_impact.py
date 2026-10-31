"""Disposable canonical stores and explicitly synthetic run/intervention records."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

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
