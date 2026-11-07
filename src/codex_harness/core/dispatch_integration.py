"""Serial candidate integration and proof consumption."""

from __future__ import annotations
import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from codex_harness.core import graph
from codex_harness.core.managed_paths import directory
from codex_harness.core.memory_promotion import (
    MemoryPromotionUnavailable,
    PromotionEvidence,
    confirm_lesson,
    promote_lesson,
)
from codex_harness.core.candidate_observations import _execution_identity, _semantic_validation
from codex_harness.core.record_io import read_record_json
from codex_harness.core.dispatch_shared import STATUS_MOCK_ONLY
from codex_harness.core.dispatch_repository import (
    _bead_refs,
    _claim,
    _claim_inputs,
    _commit,
    _git,
    _identity_refs,
    _refs_text,
    _spec_binding,
    _validate,
    _verify_requirement_files,
    _write_record,
)
from codex_harness.core.dispatch_attempts import (
    _changed_paths,
    _check_attempt_provenance,
    _hold_permission_guard,
)


def _integration_candidate(record: dict, bead: dict, repo: Path) -> tuple[str, Path, dict]:
    """Resume only the candidate staged for this exact completed attempt."""
    worker = Path(record["attempt"]["cwd"]).resolve()
    root = directory(repo.parent, f"candidate-{worker.name}")
    ref = f"candidate/{record['bead']}/{worker.name}"
    if not root.name.startswith("candidate-"):
        ref = f"candidate/{root.name}"
    state_path = Path(record["evidenceDir"]) / "integration-state.json"
    identity = {
        "runId": record["runId"],
        "bead": record["bead"],
        "planRevision": record["planRevision"],
        "requirementRefs": record["requirementRefs"],
        "specRefs": record.get("specRefs"),
        "attemptDirectory": str((state_path.parent / "attempt").resolve()),
        "workerWorktree": str(worker),
        "attemptBase": record["attempt"]["baseCommit"],
        "packetSha256": hashlib.sha256(record["prompt"].encode()).hexdigest(),
        "ownedFiles": bead["ownedFiles"],
        "candidateRef": ref,
        "candidateWorktree": str(root.resolve()),
        "receiptPath": str((state_path.parent / "integrated.json").resolve()),
    }
    branches = _git(repo, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{ref}")
    if state_path.exists():
        state = read_record_json(state_path)
        if any(state.get(key) != value for key, value in identity.items()):
            raise ValueError("integration recovery identity mismatch")
    else:
        if branches or root.exists():
            raise ValueError("integration recovery candidate has no bound state")
        state = {
            **identity,
            "baseIntegration": _git(repo, "rev-parse", "HEAD"),
            "workerTree": _git(worker, "write-tree"),
        }
        _write_record(state_path, state)
    worker_head = _git(worker, "rev-parse", "HEAD")
    if "workerCommit" not in state:
        if worker_head == state["attemptBase"]:
            if _git(worker, "write-tree") != state["workerTree"]:
                raise ValueError("integration recovery worker tree mismatch")
            worker_head = _commit(
                worker,
                f"{bead['title']} (worker change, committed by Harness)\n\n"
                f"Harness-Plan-Revision: {record['planRevision']}\n"
                f"Harness-Requirements: {_refs_text(record['requirementRefs'])}"
                + (
                    "\nHarness-Spec-Revision: "
                    + hashlib.sha256(
                        json.dumps(
                            record["specRefs"], sort_keys=True, separators=(",", ":")
                        ).encode("utf-8")
                    ).hexdigest()
                    if "specRefs" in record
                    else ""
                ),
            )
        if _git(worker, "rev-parse", "HEAD^{tree}") != state["workerTree"] or _git(
            worker, "rev-list", "--parents", "-n", "1", worker_head
        ).split()[1:] != [state["attemptBase"]]:
            raise ValueError("integration recovery worker commit mismatch")
        state["workerCommit"] = worker_head
        state["candidateTree"] = _git(
            repo, "merge-tree", "--write-tree", state["baseIntegration"], worker_head
        ).splitlines()[0]
        _write_record(state_path, state)
    if (
        worker_head != state["workerCommit"]
        or _git(worker, "rev-parse", "HEAD^{tree}") != state["workerTree"]
        or _git(worker, "status", "--porcelain")
    ):
        raise ValueError("integration recovery worker changed")
    if not branches:
        if root.exists() or _git(repo, "rev-parse", "HEAD") != state["baseIntegration"]:
            raise ValueError("integration recovery base mismatch")
        _git(repo, "worktree", "add", "-q", "-b", ref, str(root), state["baseIntegration"])
    registrations = _git(repo, "worktree", "list", "--porcelain").split("\n\n")
    if not any(
        f"worktree {root.resolve().as_posix()}" in block.splitlines()
        and f"branch refs/heads/{ref}" in block.splitlines()
        for block in registrations
    ):
        raise ValueError("integration recovery worktree mismatch")
    if _git(root, "status", "--porcelain"):
        raise ValueError("integration recovery candidate is dirty")
    candidate = _git(root, "rev-parse", "HEAD")
    if candidate == state["baseIntegration"] and "candidateCommit" not in state:
        _git(
            root,
            "-c",
            "user.name=Harness Sample",
            "-c",
            "user.email=sample@harness.invalid",
            "merge",
            "-q",
            "--no-ff",
            "-m",
            f"Integrate {bead['title']}",
            state["workerCommit"],
        )
        candidate = _git(root, "rev-parse", "HEAD")
    if (
        _git(root, "rev-list", "--parents", "-n", "1", candidate).split()[1:]
        != [state["baseIntegration"], state["workerCommit"]]
        or _git(root, "rev-parse", "HEAD^{tree}") != state["candidateTree"]
        or state.get("candidateCommit", candidate) != candidate
        or _git(repo, "rev-parse", ref) != candidate
    ):
        raise ValueError("integration recovery candidate mismatch")
    head = _git(repo, "rev-parse", "HEAD")
    if head != state["baseIntegration"]:
        _git(repo, "merge-base", "--is-ancestor", candidate, "HEAD")
    state["candidateCommit"] = candidate
    _write_record(state_path, state)
    return state["workerCommit"], root, state


def _integrate_bead(
    record: dict,
    bead: dict,
    *,
    repo: Path,
    manifest: dict,
    memory: Path,
    receipt: Path,
    confirmer: str,
    manifest_path: Path,
    persist_observation: Callable[[], Path] | None = None,
) -> None:
    if _hold_permission_guard(record, Path(record["evidenceDir"]) / "attempt"):
        return
    if not _check_attempt_provenance(record, owned_files=bead["ownedFiles"]):
        return
    worktree = Path(record["attempt"]["cwd"])
    current_plan = graph.load_plan(manifest_path)
    _verify_requirement_files(
        current_plan, manifest, current_plan.root / "template", source_fixture=record["isMocked"]
    )
    _verify_requirement_files(current_plan, manifest, repo, source_fixture=record["isMocked"])
    current_bead = current_plan.beads.get(record["bead"])
    if (
        current_plan.plan_revision != record["planRevision"]
        or current_bead is None
        or _identity_refs(_bead_refs(current_bead)) != _identity_refs(record)
    ):
        record["status"] = "rejected: stale plan revision"
        return
    changed = bool(record["attempt"]["changedPaths"])
    bead_commit = None
    validation_root = repo
    candidate_ref = None
    if changed:
        staged = sorted(set(record["attempt"]["changedPaths"]) & set(_changed_paths(worktree)))
        if staged:
            _git(worktree, "add", "--", *staged)
        changed_paths = _git(
            worktree, "diff", "--cached", "--name-only", record["attempt"]["baseCommit"]
        ).splitlines()
        record["attempt"]["changedPaths"] = changed_paths
        changed = bool(changed_paths)
    binding = None
    if changed:
        bead_commit, validation_root, binding = _integration_candidate(record, bead, repo)
        candidate_ref = binding["candidateRef"]
    integration_commit = _git(validation_root, "rev-parse", "HEAD")
    record["candidate"] = {
        "ref": candidate_ref,
        "commit": integration_commit,
        "tree": _git(validation_root, "rev-parse", "HEAD^{tree}"),
        "validated": False,
    }
    if current_plan.semantic is not None:
        if receipt.exists():
            prior = read_record_json(receipt)
            semantic = prior["semantic"]
            expected = {
                **_execution_identity(current_plan),
                "candidateCommit": integration_commit,
                "candidateTree": record["candidate"]["tree"],
            }
            if (
                any(semantic.get(k) != v for k, v in expected.items())
                or (
                    bead["semanticPhase"] == "build"
                    and semantic["red"] not in record.get("redObservations", [])
                    and semantic["red"]["candidateCommit"] != record["attempt"]["baseCommit"]
                )
                or prior.get("recoveryBinding") != binding
            ):
                raise ValueError("integration recovery semantic receipt mismatch")
        else:
            semantic = _semantic_validation(
                validation_root, current_plan, record, bead["semanticPhase"]
            )
        if semantic["state"] == "incomplete":
            record["status"] = semantic["reason"]
            return
        validation = {"passed": semantic["passed"]}
        record["integrated"] = {
            "beadCommit": bead_commit,
            "integrationCommit": integration_commit,
            "root": str(validation_root),
            "receiptPath": str(receipt),
            "planRevision": record["planRevision"],
            "requirementRefs": record["requirementRefs"],
            **_spec_binding(record),
            "validation": validation,
            "semantic": semantic,
            **({"recoveryBinding": binding} if binding is not None else {}),
        }
        _write_record(receipt, record["integrated"])
    elif receipt.exists() and binding is not None:
        integrated = read_record_json(receipt)
        claim = integrated.get("claim", {})
        inputs = _claim_inputs(validation_root, current_plan, record["bead"], integration_commit)
        if (
            integrated.get("recoveryBinding") != binding
            or integrated.get("beadCommit") != bead_commit
            or integrated.get("integrationCommit") != integration_commit
            or integrated.get("root") != str(validation_root)
            or integrated.get("receiptPath") != str(receipt)
            or integrated.get("planRevision") != record["planRevision"]
            or integrated.get("requirementRefs") != record["requirementRefs"]
            or integrated.get("specRefs") != record.get("specRefs")
            or any(claim.get(key) != value for key, value in inputs.items())
            or claim.get("candidateCommit") != integration_commit
            or claim.get("candidateTree") != record["candidate"]["tree"]
            or claim.get("passed") != integrated.get("validation", {}).get("passed")
        ):
            raise ValueError("integration recovery receipt mismatch")
        record["integrated"] = integrated
        validation = integrated["validation"]
    else:
        validation = _validate(
            validation_root, manifest, bead["proofModule"], owned_files=tuple(bead["ownedFiles"])
        )
        record["integrated"] = {
            "beadCommit": bead_commit,
            "integrationCommit": integration_commit,
            "root": str(validation_root),
            "candidate": {**record["candidate"], "validated": validation["passed"]},
            "validation": validation,
            "receiptPath": str(receipt),
            "planRevision": record["planRevision"],
            "requirementRefs": record["requirementRefs"],
            **_spec_binding(record),
            "claim": _claim(
                validation_root,
                current_plan,
                record["bead"],
                integration_commit,
                validation["passed"],
            ),
            **({"recoveryBinding": binding} if binding is not None else {}),
        }
        _write_record(receipt, record["integrated"])
    record["didRunIntegratedValidation"] = True
    _complete_integration(
        record,
        bead,
        repo,
        manifest,
        memory,
        receipt,
        confirmer,
        manifest_path,
        current_plan,
        changed,
        binding,
        persist_observation,
    )


def _complete_integration(
    record,
    bead,
    repo,
    manifest,
    memory,
    receipt,
    confirmer,
    manifest_path,
    current_plan,
    changed,
    binding,
    persist_observation=None,
):
    from codex_harness.core.run_ownership import effect_guard

    with effect_guard(record.get("control")):
        validation = record["integrated"]["validation"]
        validation_root = Path(record["integrated"]["root"])
        integration_commit = record["integrated"]["integrationCommit"]
        record["candidate"]["validated"] = validation["passed"]
        semantic = record["integrated"].get("semantic")
        if not (semantic["admitted"] if semantic else validation["passed"]):
            record["status"] = (
                "awaiting judgment"
                if semantic and semantic["state"] == "pending-judgment"
                else "integrated validation failed"
            )
            return
        if not changed:
            record["status"] = "already-satisfied"
            return
        _git(repo, "merge", "-q", "--ff-only", integration_commit)
        record["lessonsPromoted"] = []
        for index, lesson in enumerate(bead.get("lessons", []) if validation["passed"] else []):
            promoted = {**lesson, "entryId": None, "confirmed": False}
            record["lessonsPromoted"].append(promoted)
            try:
                entry_id = promote_lesson(
                    memory,
                    text=lesson["text"],
                    evidence=PromotionEvidence(
                        integration_commit,
                        str(receipt),
                        lesson["decisionId"],
                        f"{manifest_path.resolve()}#/beads/{record['bead']}/lessons/{index}/text",
                    ),
                    author=f"worker:{record['bead']}:{record['provider']}",
                    provider=record["provider"],
                    model=record["model"] or "unknown",
                    recovery_identity=json.dumps(binding, sort_keys=True),
                )
                promoted["entryId"] = entry_id
                confirm_lesson(memory, entry_id, by=confirmer, recover=True)
                promoted["confirmed"] = True
            except (MemoryPromotionUnavailable, OSError, sqlite3.Error) as error:
                # Only advisory memory operations are in this catch. Proof/receipt writes
                # and typed ownership, provenance and confirmation refusals still fail closed.
                promoted["error"] = {
                    "code": getattr(error, "code", "advisory-memory-unavailable"),
                    "message": f"{type(error).__name__}: {error}",
                }
        record["status"] = STATUS_MOCK_ONLY if record["isMocked"] else "requirement-validated"
        if current_plan.semantic is not None and bead["semanticPhase"] == "contracts":
            record["status"] = "contracts-red"
