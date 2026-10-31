"""Retained semantic candidates use the ordinary integration and delivery owners."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from codex_harness.core import planner_jobs as jobs
from codex_harness.core.record_io import read_record_json


def _semantic_validation(root, plan, record, phase):
    from codex_harness.core import semantic_runner
    from codex_harness.core.dispatch import _git, _semantic_identity

    identity = _semantic_identity(plan)
    red = record.get("contractsRed")
    if phase == "build" and (
        not red or red.get("state") != "RED" or any(red.get(k) != v for k, v in identity.items())
    ):
        raise ValueError(
            "semantic-v1 requires current Contracts RED; scenarios/contracts/runner changed"
        )
    results = semantic_runner.run(
        plan.semantic["scenarios"],
        root,
        plan.semantic["targets"],
        "contracts" if phase == "contracts" else "validation",
    )
    expected = plan.semantic["scenarios"]["scenarios"]
    complete = len(results) == len(expected) and all(
        r["id"] == s["id"] for r, s in zip(results, expected, strict=True)
    )
    red_ok = complete and all(
        r["state"] == "FAIL" and r.get("stubActions") == [a["id"] for a in s["actions"]]
        for r, s in zip(results, expected, strict=True)
    )
    passed = (
        phase == "build"
        and complete
        and all(r["state"] == "PASS" and not r.get("stubActions") for r in results)
    )
    state = (
        "RED"
        if phase == "contracts" and red_ok
        else "PASS"
        if passed
        else "ERROR"
        if phase == "contracts" or any(r["state"] == "ERROR" for r in results)
        else "FAIL"
    )
    return {
        **identity,
        "phase": "contracts" if phase == "contracts" else "validation",
        "state": state,
        "passed": passed,
        "admitted": red_ok if phase == "contracts" else passed,
        "candidateCommit": _git(root, "rev-parse", "HEAD"),
        "candidateTree": _git(root, "rev-parse", "HEAD^{tree}"),
        "linkedRed": hashlib.sha256(json.dumps(red, sort_keys=True).encode()).hexdigest()
        if red
        else None,
        "results": results,
    }


def _read_git(root, *arguments):
    # Inspection must never refresh the index or append dispatcher failure logs.
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            f"safe.directory={root.as_posix()}",
            "-C",
            str(root),
            *arguments,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ValueError(
            "revalidate: cannot read retained Git evidence; inspect run: " + result.stderr[:512]
        )
    return result.stdout.strip()


def retained(workspace, job_id):
    """Read-only eligibility and candidate binding; no worker or verifier starts."""
    from codex_harness.core import dispatch as d

    directory = jobs._directory(workspace, job_id)
    request = jobs._request(directory)
    terminal = read_record_json(jobs._inside(directory, directory / "terminal.json"))
    if terminal.get("requestDigest") != request["requestDigest"]:
        raise ValueError("revalidate: terminal identity mismatch; inspect original job")
    paths = list(directory.glob("runs/*/evidence/run.json"))
    if len(paths) != 1:
        raise ValueError("revalidate: one retained run required; inspect job evidence")
    path = jobs._inside(directory, paths[0])
    run = read_record_json(path)
    plan = d.graph.load_plan(directory / "plan")
    if run["planRevision"] != request["publicationRevision"] or plan.semantic is None:
        raise ValueError("revalidate: retained semantic publication required; inspect job")
    manifest = read_record_json(directory / "plan/sample.json")
    builds = [
        plan.beads[key] for key, b in manifest["beads"].items() if b["semanticPhase"] == "build"
    ]
    if len(builds) != 1:
        raise ValueError("revalidate: one completed Build required; inspect run")
    bead = run["beads"][builds[0].id]
    state = read_record_json(
        jobs._inside(directory, Path(bead["evidenceDir"]) / "integration-state.json")
    )
    semantic = (bead.get("integrated") or {}).get("semantic", {})
    verifier_error = (
        not semantic
        and bead["status"].startswith("bead failed:")
        and bead.get("candidate", {}).get("commit") == state.get("candidateCommit")
        and bead.get("guard", {}).get("passed") is True
    )
    if not verifier_error and (
        semantic.get("phase") != "validation" or semantic.get("state") not in {"FAIL", "ERROR"}
    ):
        raise ValueError(
            "revalidate: no failed Validation candidate; inspect or finish Build first"
        )
    root = jobs._inside(directory, Path(state["candidateWorktree"]))
    semantic = semantic or state
    repo = jobs._inside(directory, Path(run["repo"]))
    if _read_git(root, "status", "--porcelain") or (
        _read_git(root, "rev-parse", "HEAD") != semantic["candidateCommit"]
        or _read_git(root, "rev-parse", "HEAD^{tree}") != semantic["candidateTree"]
    ):
        raise ValueError(
            "revalidate: retained candidate changed; inspect evidence before rebuilding"
        )
    if _read_git(repo, "rev-parse", "HEAD") != state["baseIntegration"]:
        raise ValueError("revalidate: integration base changed; inspect original run")
    return dict(
        runPath=str(path),
        runDigest=jobs._digest(run),
        bead=builds[0].id,
        candidateCommit=semantic["candidateCommit"],
        candidateTree=semantic["candidateTree"],
        verifier=d._semantic_identity(plan),
    )


def deliver(workspace, request, run):
    """Shared worker completion: expose only a validated integration's owned files."""
    from codex_harness.core import dispatch as d

    if run.get("semanticValidation", {}).get("state") != "PASS" or run["status"].startswith(
        "plan-failed"
    ):
        return
    directory = jobs._directory(workspace, request["jobId"])
    manifest = read_record_json(directory / "plan/sample.json")
    repo = jobs._inside(directory, Path(run["repo"]))
    if d._git(repo, "rev-parse", "HEAD^{tree}") != run["semanticValidation"]["candidateTree"]:
        raise ValueError("delivery: integration tree differs from Validation PASS")
    names = sorted({n for b in manifest["beads"].values() for n in b["ownedFiles"]})
    # Resolve every path before writing any product file, preserving planner metadata.
    files = []
    for name in names:
        if Path(name).parts[0].startswith("."):
            raise ValueError("delivery: metadata is not a product file")
        source = jobs._inside(repo, repo / name)
        target = jobs._inside(workspace, Path(workspace) / name)
        if source.is_file():
            if target.exists() and target.read_bytes() != source.read_bytes():
                baseline = jobs._inside(directory, directory / "plan/template" / name)
                if not baseline.is_file() or target.read_bytes() != baseline.read_bytes():
                    raise ValueError(
                        f"delivery: existing {name} changed; preserve it and reconcile first"
                    )
            files.append((source, target))
        elif target.exists():
            raise ValueError(
                f"delivery: retained candidate omits existing {name}; reconcile it first"
            )
    for source, target in files:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    run["delivery"] = dict(
        root=str(Path(workspace).resolve()),
        files=[str(t) for _, t in files],
        candidateTree=run["semanticValidation"]["candidateTree"],
    )
    d._write_record(repo.parent / "evidence/run.json", run)
    return run["delivery"]


def revalidate(store, job_id, request_id):
    """Rerun verifiers, then let the existing integrator accept or reject the candidate."""
    from codex_harness.core import dispatch as d

    selected = retained(store.workspace, job_id)
    directory = jobs._directory(store.workspace, job_id)
    request = jobs._request(directory)
    manifest, _ = jobs._publication(store, request["publicationRevision"])
    if jobs._digest(manifest) != request["manifestDigest"]:
        raise ValueError("revalidate: publication changed; inspect current plan")
    path = Path(selected["runPath"])
    run = read_record_json(path)
    plan = d.graph.load_plan(directory / "plan")
    bead = run["beads"][selected["bead"]]
    # Reuse valid RED; only a changed verifier/scenario identity needs fresh proof.
    red = bead["contractsRed"]
    if any(red.get(k) != v for k, v in d._semantic_identity(plan).items()):
        contracts = run["beads"][plan.beads[selected["bead"]].depends_on[0]]
        red_root = jobs._inside(directory, Path(contracts["integrated"]["root"]))
        red = d._semantic_validation(red_root, plan, contracts, "contracts")
        d._write_record(Path(bead["evidenceDir"]) / f"contracts-{request_id}.json", red)
    if red["state"] != "RED":
        raise ValueError(
            "revalidate: retained Contracts no longer RED; inspect verifier results: "
            + json.dumps(red["results"])
        )
    bead["contractsRed"] = red
    receipt = jobs._inside(directory, Path(bead["evidenceDir"]) / "integrated.json")
    if receipt.exists():
        receipt.rename(receipt.with_name(f"validation-{request_id}.json"))
    bead["status"] = "running"
    bead["integrated"] = None
    d._integrate_bead(
        bead,
        manifest["beads"][selected["bead"]],
        repo=Path(run["repo"]),
        manifest=manifest,
        memory=path.parent.parent / "memory-workspace",
        receipt=receipt,
        confirmer="harness-integrator",
        manifest_path=directory / "plan/sample.json",
    )
    if bead["integrated"] is None:
        raise ValueError("revalidate refused: " + bead["status"] + "; inspect attempt provenance")
    proof = bead["integrated"]["semantic"]
    run["semanticValidation"] = proof
    run["status"] = (
        (d.STATUS_MOCK_ONLY if run["isMocked"] else d.STATUS_VALIDATED)
        if proof["passed"]
        else f"plan-failed: {selected['bead']} {bead['status']}"
    )
    run["finishedAt"] = d._now()
    index = list(d.graph.topological_order(plan)).index(selected["bead"])
    b = plan.beads[selected["bead"]]
    run["requirementValidation"][index] = d._requirement_validation(
        b.id,
        b.requirements,
        b.proof_module,
        bead["integrated"],
        plan.plan_revision,
        bead["requirementRefs"],
        bead.get("specRefs"),
    )
    d._write_record(path, run)
    delivery = deliver(store.workspace, request, run)
    return dict(
        state=proof["state"], results=proof["results"], delivery=delivery, runId=run["runId"]
    )
