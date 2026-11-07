"""Execute the eligible graph; run_loop alone chooses continuation."""

from __future__ import annotations
import copy
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from codex_harness.core import attempt_provenance, graph, worker_packet
from codex_harness.core.mail import Mailbox
from codex_harness.core.managed_paths import directory, run_directory, short
from codex_harness.core.memory_promotion import (
    memory_prompt_for,
)
from codex_harness.core.candidate_observations import _execution_identity
from codex_harness.core.record_io import read_record_json
from codex_harness.core.dispatch_shared import (
    STATUS_VALIDATED,
    STATUS_VALIDATED_NO_INTEGRATION,
    STATUS_MOCK_ONLY,
    STATUS_PROVENANCE_HELD,
    DENIAL_HELD_STATUSES,
    DENIAL_CAPABILITIES,
)
from codex_harness.core.dispatch_repository import (
    _bead_refs,
    _changed_claim_inputs,
    _claim,
    _claim_inputs,
    _commit,
    _did_execute_real_provider,
    _git,
    _identity_refs,
    _now,
    _requirement_ids,
    _requirement_validation,
    _spec_binding,
    _update_requirement_files,
    _validate,
    _verify_requirement_files,
    _write_record,
)
from codex_harness.core.dispatch_attempts import (
    _attempt_bead,
    _check_attempt_provenance,
    _hold_permission_guard,
    _update_efficiency,
)
from codex_harness.core.dispatch_integration import _integrate_bead


def _notify_failures(record: dict, evidence: Path) -> None:
    """Mail goes to the run memory workspace, alongside cross-provider project memory.

    SQLite event ids make delivery idempotent even if a crash precedes run.json.
    A different attempt evidence directory denotes a new event, not a resume duplicate.
    Bodies contain reasons and evidence paths, never raw provider/test output.
    """
    events = []
    for bead_id, bead in record["beads"].items():
        reason = bead["status"]
        if reason not in {
            "integrated validation failed",
            "ambiguous launch",
            "provider launch-failed",
            "provider timed-out",
            "provider observation-failed",
            "provider evidence-finalization-failed",
            *DENIAL_HELD_STATUSES,
        } and not reason.startswith(
            (
                "rejected: ",
                "blocked: ",
                "provider exited nonzero (",
                "prelaunch failed: ",
                "bead failed: ",
            )
        ):
            continue
        dependency = reason.removeprefix("blocked: dependency ").removesuffix(" failed")
        source = record["beads"].get(dependency, {}) if reason.startswith("blocked:") else {}
        event = {
            "runId": record["runId"],
            "planRevision": bead["planRevision"],
            "bead": bead_id,
            "reason": reason,
            "specRefs": _identity_refs(bead),
            "evidencePaths": [str(evidence / "run.json")],
        }
        if reason == "blocked: worker reported":
            event.update(blocker=bead["blocker"], workerReport=bead["workerReport"])
        for path in (
            bead.get("evidenceDir"),
            source.get("evidenceDir"),
            (bead.get("integrated") or {}).get("receiptPath"),
        ):
            if path:
                event["evidencePaths"].append(str(path))
        events.append(event)
    if record["status"].startswith("plan-failed:"):
        events.append(
            {
                "runId": record["runId"],
                "planRevision": record["planRevision"],
                "bead": "plan",
                "reason": record["status"],
                "specRefs": [
                    ref for bead in record["beads"].values() for ref in _identity_refs(bead)
                ],
                "evidencePaths": [str(evidence / "run.json")],
                "failedEvents": events.copy(),
                "integrationCommit": (record.get("integrationValidation") or {}).get(
                    "integrationCommit"
                ),
            }
        )
    if not events:
        return
    mailbox = Mailbox(record["memoryWorkspace"])
    for event in events:
        body = json.dumps(event, sort_keys=True)
        bead_refs = list(record["beads"]) if event["bead"] == "plan" else [event["bead"]]
        mailbox.send_event(
            body,
            from_agent="harness-dispatcher",
            to_agent="planner",
            subject=f"Replan: {event['bead']}",
            body=body,
            refs=list(dict.fromkeys([*bead_refs, *[ref["id"] for ref in event["specRefs"]]])),
            kind="blocker" if event["reason"] == "blocked: worker reported" else "replan-trigger",
        )


def run_plan(
    plan_dir: str | Path,
    *,
    work_root: str | Path,
    provider_for: Mapping[str, str] | None = None,
    model_for: Mapping[str, str] | None = None,
    effort_for: Mapping[str, str] | None = None,
    executable_for: Mapping[str, str] | None = None,
    mock_provider: str | None = None,
    fake_workers: Mapping[str, Callable[[Path], None]] | None = None,
    max_parallel: int = 2,
    timeout: int = 900,
    confirmer: str = "harness-integrator",
    resume_run_dir: str | Path | None = None,
    relaunch_ambiguous: bool = False,
    retry_blocked: str | None = None,
    require_denial_interception: bool = False,
    execution_job: str | Path | None = None,
    setup: dict | None = None,
    intent_nodes: list[dict] | None = None,
    worker_outcomes: dict | None = None,
) -> dict:
    """Run a validated work graph; only integrated passes release dependents.

    Attempts own separate worktrees. This calling thread alone creates them,
    merges finished changes, writes receipts and confirms project lessons.
    """
    if require_denial_interception:
        raise ValueError(
            "denial-interception-unsupported: provider-normal headless CLI has no synchronous "
            "tool callback; only post-hoc completion/integration/resume holds are supported"
        )
    if max_parallel < 1 or timeout <= 0:
        raise ValueError("max_parallel and timeout must be positive")
    if mock_provider not in {None, "reference", "noop"}:
        raise ValueError(f"unknown mock provider {mock_provider!r}")
    plan = graph.load_plan(Path(plan_dir))
    manifest_path = Path(plan_dir)
    if manifest_path.is_dir():
        manifest_path /= "sample.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if plan.semantic is not None and (os.name != "nt" or max_parallel != 1):
        raise ValueError(
            "Non-Windows/parallel Build unsupported in semantic-v1; use Windows and max_parallel=1"
        )
    source_fixture = mock_provider is not None or set(plan.beads).issubset(fake_workers or {})
    _verify_requirement_files(plan, manifest, plan.root / "template", source_fixture=source_fixture)
    order = graph.topological_order(plan)
    if retry_blocked or relaunch_ambiguous:
        raise ValueError("retry belongs to the run loop; dispatch never readmits an attempt")
    for bead_id in order:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", bead_id):
            raise ValueError(f"unsafe bead id {bead_id!r}")
        lessons = manifest["beads"][bead_id].get("lessons", [])
        if not isinstance(lessons, list) or any(
            not isinstance(lesson, dict)
            or any(
                not isinstance(lesson.get(key), str) or not lesson[key].strip()
                for key in ("decisionId", "text")
            )
            for lesson in lessons
        ):
            raise ValueError(f"invalid lessons for bead {bead_id}")
    provider_for, model_for = provider_for or {}, model_for or {}
    effort_for, executable_for = effort_for or {}, executable_for or {}
    fake_workers = fake_workers or {}
    real_attempts = mock_provider is None and not set(plan.beads).issubset(fake_workers)
    if real_attempts and execution_job is None:
        raise ValueError("provider-call-owned-native-job-required")
    for mapping in (provider_for, model_for, effort_for, executable_for, fake_workers):
        if set(mapping) - set(plan.beads):
            raise ValueError("per-bead mappings contain unknown bead ids")
    run_id = short(datetime.now(UTC).isoformat())
    run_dir = run_directory(work_root, run_id, manifest["beads"], resume_run_dir)
    repo, evidence, memory = run_dir / "repo", run_dir / "evidence", run_dir / "memory-workspace"
    if real_attempts:
        from codex_harness.core.planner_jobs import verify_job

        approved = verify_job(execution_job)["options"]
        if (
            approved["maxParallel"] != max_parallel
            or approved["timeout"] != timeout
            or executable_for
        ):
            raise ValueError("provider-call-launch-options-mismatch")
        for worker in approved["workers"]:
            bead_id = worker["bead"]
            if (
                provider_for.get(bead_id, "codex") != worker["provider"]
                or model_for.get(bead_id) != worker["model"]
                or effort_for.get(bead_id) != worker["effort"]
            ):
                raise ValueError("provider-call-worker-scope-mismatch")

        # Every real run is produced once by the original owned job controller.
        # CLI real resume is held. Only this controller may claim sequential
        # terminal-preimage recovery of its original run.
        attempt_provenance.bind_run(
            execution_job,
            plan.root,
            run_dir,
            resume=resume_run_dir is not None,
            retry_options={"retryBlocked": retry_blocked, "relaunchAmbiguous": relaunch_ambiguous},
        )
    control = None
    if execution_job is not None:
        from codex_harness.core.planner_jobs import owned_segment

        admitted = read_record_json(owned_segment(execution_job) / "request.json")
        control = dict(
            workspace=str(Path(execution_job).resolve().parent.parent.parent),
            generation=admitted["generation"],
            effectId=admitted["segmentId"],
        )
    previous = None
    if resume_run_dir:
        previous = read_record_json(evidence / "run.json")
        if (
            previous["plan"] != plan.name
            or Path(previous["repo"]).resolve() != repo
            or _git(repo, "branch", "--show-current") != "integration"
            or _git(repo, "status", "--porcelain")
        ):
            raise ValueError("resume requires the existing clean integration repo for this plan")
        run_id = previous["runId"]
        _notify_failures(previous, evidence)
    else:
        evidence.mkdir(parents=True)
        memory.mkdir()
    records = {
        bead_id: {
            "runId": run_id,
            "control": control,
            "bead": bead_id,
            "planRevision": plan.plan_revision,
            **_bead_refs(plan.beads[bead_id]),
            "provider": provider_for.get(bead_id) or plan.beads[bead_id].provider or "codex",
            "model": model_for.get(bead_id),
            "effort": effort_for.get(bead_id),
            "executable": executable_for.get(bead_id),
            "prompt": None,
            "workerReport": "",
            "attempt": None,
            "guard": None,
            "integrated": None,
            "candidate": {"ref": None, "commit": None, "tree": None, "validated": False},
            "lessonsPromoted": [],
            "status": "pending",
            "isMocked": mock_provider is not None or bead_id in fake_workers,
            "didRunProvider": False,
            "didRunIntegratedValidation": False,
        }
        for bead_id in order
    }
    record = {
        "schema": "codex-harness/plan-run-v1",
        "control": control,
        "runId": run_id,
        "plan": plan.name,
        "planRevision": plan.plan_revision,
        "startedAt": _now(),
        "repo": str(repo),
        "memoryWorkspace": str(memory),
        "beads": records,
        "isMocked": mock_provider is not None or bool(fake_workers),
        "integrationValidation": None,
        "status": "running",
        "isLiveProductProof": False,
        "didExecuteRealProvider": False,
        "denialCapabilities": dict(DENIAL_CAPABILITIES),
        "claims": copy.deepcopy(previous.get("claims", [])) if previous else [],
        "claimSummary": {"retained": [], "impacted": [], "fresh": []},
        "claimIdentityPolicy": {
            "verification": "scenarios/contracts/binding + runner digest; controller receipts"
            if plan.semantic is not None
            else f"whole {graph.test_dir(plan)}/ + VISION.md + PLAN.md + "
            "requirementFiles; Git blobs, LF",
            "source": "ownedFiles at candidate commit; Git blobs, LF",
            "dependencies": "transitive prerequisites; explicit proofDependsOn for stubbed proofs; "
            "integration binds every bead",
        },
        "requirementValidation": [
            _requirement_validation(
                bead_id,
                plan.beads[bead_id].requirements,
                plan.beads[bead_id].proof_module,
                None,
                plan.plan_revision,
                records[bead_id]["requirementRefs"],
                records[bead_id].get("specRefs"),
            )
            for bead_id in order
        ],
        "liveAcceptance": {
            "claimed": False,
            "reason": "local dispatcher run; no served or live acceptance step",
        },
    }
    record["requirementValidation"].append(
        _requirement_validation(
            "integration",
            plan.integration_requirements,
            plan.integration_proof_module,
            None,
            plan.plan_revision,
            manifest.get("integration", {}).get("requirementRefs", []),
        )
        if plan.integration_proof_module is not None
        else {
            "bead": "integration",
            "declared": False,
            "planRevision": plan.plan_revision,
            "requirementRefs": [],
        }
    )
    if previous:
        record["baseline"] = previous["baseline"]
        record["startedAt"] = previous["startedAt"]
        record["resumedAt"] = _now()
        record["integrationValidationHistory"] = previous.get("integrationValidationHistory", [])
        if previous.get("integrationValidation"):
            record["integrationValidationHistory"].append(previous["integrationValidation"])
        record["resumeSummary"] = {
            "reused": [],
            "invalidated": [],
            "retained": [],
        }
        plan_changed = previous.get("planRevision") != plan.plan_revision
        if plan_changed:
            record["planUpdate"] = _update_requirement_files(
                repo, plan, previous.get("planRevision")
            )
        elif "planUpdate" in previous:
            record["planUpdate"] = previous["planUpdate"]
    else:
        shutil.copytree(plan.root / "template", repo)
        _verify_requirement_files(plan, manifest, repo, source_fixture=source_fixture)
        metadata = repo / ".git"
        if metadata.exists() or metadata.is_symlink():
            raise ValueError("dispatch repository metadata must be newly owned")
        _git(repo, "init", "-q", "-b", "integration")
        exclude = metadata / "info/exclude"
        for path in (metadata, exclude.parent, exclude):
            if path.is_symlink() or (
                path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400
            ):
                raise ValueError("dispatch repository metadata redirected")
        if (
            any(
                Path(_git(repo, "rev-parse", "--path-format=absolute", option)) != metadata
                for option in ("--git-dir", "--git-common-dir")
            )
            or Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"))
            != exclude
        ):
            raise ValueError("dispatch repository metadata outside owned repository")
        exclude.parent.mkdir(exist_ok=True)
        with exclude.open("ab") as stream:
            stream.write(b"\n__pycache__/\n*.pyc\n")
        _git(repo, "add", "-A")
        baseline = _commit(repo, "Sample baseline: documented requirement and fixed validation")
        baseline_root = run_dir / "baseline"
        _git(repo, "worktree", "add", "-q", "--detach", str(baseline_root), baseline)
        record["baseline"] = {
            "commit": baseline,
            "root": str(baseline_root),
            "requirementFiles": _requirement_ids(repo, list(plan.requirement_files)),
            "validation": {
                bead_id: _validate(baseline_root, manifest, plan.beads[bead_id].proof_module)
                for bead_id in order
                if plan.semantic is None
            },
        }
    _verify_requirement_files(plan, manifest, repo, source_fixture=source_fixture)
    done: set[str] = set()
    terminal: set[str] = set()
    running: dict[str, Future] = {}

    def fail(bead_id: str) -> None:
        terminal.add(bead_id)
        blocked = {bead_id}
        waiting = records[bead_id]["status"] == "awaiting judgment"
        if waiting:
            record["status"] = "pending-judgment"
        for dependent in order:
            if dependent not in terminal and blocked.intersection(plan.beads[dependent].depends_on):
                records[dependent]["status"] = (
                    f"awaiting judgment: dependency {bead_id}"
                    if waiting
                    else f"blocked: dependency {bead_id} failed"
                )
                terminal.add(dependent)
                blocked.add(dependent)

    if previous:
        retained_failures = []
        for bead_id in order:
            old = previous["beads"].get(bead_id)
            if old is None:
                continue
            if old.get("attempt") is None:
                continue  # An unstarted dependent may continue; it is not a retry.
            old_evidence = Path(old.get("evidenceDir", evidence / bead_id))
            request_path, result_path = (
                old_evidence / "attempt" / "request.json",
                old_evidence / "attempt" / "result.json",
            )
            try:
                request = read_record_json(request_path) if request_path.exists() else {}
            except (OSError, ValueError):
                request = {}
            uncertain = request.get("status") == "launching" and not result_path.exists()
            refs_match = _identity_refs(old) == _identity_refs(records[bead_id])
            # Explicit refs allow unaffected v2 beads to retain their original
            # proof binding across a global revision change; v1 needs exact identity.
            identity_matches = refs_match and (
                old.get("planRevision") == plan.plan_revision
                or "specRefs" in old
                or bool(old.get("requirementRefs"))
            )
            integrated = old.get("integrated")
            if "candidate" not in old:
                old["candidate"] = {
                    "ref": None,
                    "commit": integrated["integrationCommit"] if integrated else None,
                    "tree": _git(repo, "rev-parse", f"{integrated['integrationCommit']}^{{tree}}")
                    if integrated
                    else None,
                    "validated": bool(integrated and integrated["validation"]["passed"]),
                }
            validated = bool(
                integrated
                and integrated["validation"]["passed"]
                and old["didRunIntegratedValidation"]
                and old["candidate"]["validated"]
            )
            if plan.semantic is not None:
                validated = bool(
                    integrated
                    and integrated.get("semantic", {}).get("admitted")
                    and old.get("planRevision") == plan.plan_revision
                )
            # One recovery owner: resume reuses integrated proven work only.
            # Every other outcome stays terminal in its old namespace;
            # only the run loop may request a fresh job within current B.
            if (
                _hold_permission_guard(old, old_evidence / "attempt")
                or uncertain
                or not _check_attempt_provenance(old, owned_files=plan.beads[bead_id].owned_files)
            ):
                records[bead_id] = old
                retained_failures.append(bead_id)
                record["resumeSummary"]["retained"].append(
                    dict(bead=bead_id, reason="Original attempt requires canonical recovery")
                )
            elif (
                validated
                and identity_matches
                and not old.get("status", "").startswith("bead failed:")
            ):
                _git(repo, "merge-base", "--is-ancestor", integrated["integrationCommit"], "HEAD")
                # This reuses implementation only. Claims are checked after the
                # dependencies have reached their current integration candidate.
                records[bead_id] = old
                old.update(status="reused", reused=True, acceptedPlanRevision=plan.plan_revision)
            if records[bead_id] is not old:
                records[bead_id] = old
                retained_failures.append(bead_id)
                record["resumeSummary"]["retained"].append(
                    dict(bead=bead_id, reason="Fresh job required; original evidence retained")
                )
        for bead_id in retained_failures:
            fail(bead_id)
        for bead_id in order:
            current = records[bead_id]
            record["requirementValidation"][order.index(bead_id)] = _requirement_validation(
                bead_id,
                plan.beads[bead_id].requirements,
                plan.beads[bead_id].proof_module,
                current.get("revalidation") or current["integrated"],
                plan.plan_revision if current.get("revalidation") else current["planRevision"],
                current["requirementRefs"],
                current.get("specRefs"),
            )
            if current["status"] in DENIAL_HELD_STATUSES | {STATUS_PROVENANCE_HELD}:
                record["requirementValidation"][order.index(bead_id)]["passed"] = False
            if current.get("reused"):
                record["requirementValidation"][order.index(bead_id)]["reused"] = True
        record["isMocked"] = record["isMocked"] or any(
            bead["isMocked"] for bead in records.values()
        )
    _write_record(evidence / "run.json", record)
    _notify_failures(record, evidence)

    def latest_claim(bead_id):
        return next(
            (claim for claim in reversed(record["claims"]) if claim["bead"] == bead_id), None
        )

    def add_claim(claim):
        record["claims"].append(claim)
        record["claimSummary"]["fresh"].append(claim)

    def prove_reused_bead(bead_id):
        current = records[bead_id]
        if _hold_permission_guard(current, Path(current["evidenceDir"]) / "attempt"):
            record["requirementValidation"][order.index(bead_id)]["passed"] = False
            return False
        if not _check_attempt_provenance(current, owned_files=plan.beads[bead_id].owned_files):
            record["requirementValidation"][order.index(bead_id)]["passed"] = False
            return False
        if plan.semantic is not None:
            proof = current["integrated"]["semantic"]
            original = read_record_json(Path(current["integrated"]["receiptPath"]))
            if original != current["integrated"] or any(
                proof.get(k) != v for k, v in _execution_identity(plan).items()
            ):
                current["status"] = "rejected: stale semantic receipt"
                return False
            _git(repo, "merge-base", "--is-ancestor", proof["candidateCommit"], "HEAD")
            record[
                "semanticValidation"
                if manifest["beads"][bead_id]["semanticPhase"] == "build"
                else "semanticContracts"
            ] = proof
            if manifest["beads"][bead_id]["semanticPhase"] == "build":
                return proof["passed"] and proof["candidateTree"] == _git(
                    repo, "rev-parse", "HEAD^{tree}"
                )
            return proof["state"] == "RED"
        commit = _git(repo, "rev-parse", "HEAD")
        old = latest_claim(bead_id)
        changed = _changed_claim_inputs(old, _claim_inputs(repo, plan, bead_id, commit))
        if changed:
            revalidation = {
                "integrationCommit": commit,
                "candidateTree": _git(repo, "rev-parse", "HEAD^{tree}"),
                "planRevision": plan.plan_revision,
                **_bead_refs(plan.beads[bead_id]),
                "validation": _validate(repo, manifest, plan.beads[bead_id].proof_module),
            }
            claim = _claim(repo, plan, bead_id, commit, revalidation["validation"]["passed"])
            revalidation["claim"] = claim
            current["revalidation"] = revalidation
            current["didRunIntegratedValidation"] = True
            add_claim(claim)
            _write_record(Path(current["evidenceDir"]) / "revalidation.json", revalidation)
            if not claim["passed"]:
                current["status"] = "integrated validation failed"
                if previous:
                    record["resumeSummary"]["invalidated"].append(bead_id)
        proof = current.get("revalidation") or current["integrated"]
        record["requirementValidation"][order.index(bead_id)] = _requirement_validation(
            bead_id,
            plan.beads[bead_id].requirements,
            plan.beads[bead_id].proof_module,
            proof,
            plan.plan_revision,
            _bead_refs(plan.beads[bead_id])["requirementRefs"],
            _bead_refs(plan.beads[bead_id]).get("specRefs"),
        )
        record["requirementValidation"][order.index(bead_id)]["reused"] = bool(
            not changed and previous and old in previous.get("claims", [])
        )
        return (latest_claim(bead_id) or {}).get("passed", False)

    def persist_observation() -> Path:
        path = evidence / "run.json"
        _write_record(path, record)
        return path

    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        while len(terminal) < len(order):
            for bead_id in graph.ready_beads(plan, done, set(running) | terminal):
                current = records[bead_id]
                if current.get("reused"):
                    if previous:
                        record["resumeSummary"]["reused"].append(bead_id)
                    if prove_reused_bead(bead_id):
                        done.add(bead_id)
                        terminal.add(bead_id)
                    else:
                        fail(bead_id)
                    continue
                if len(running) >= max_parallel:
                    break
                try:
                    bead_evidence = directory(evidence, bead_id)
                    bead_evidence.mkdir()
                    base_commit = _git(repo, "rev-parse", "HEAD")
                    worktree = directory(run_dir, f"bead-{bead_id}")
                    _git(
                        repo,
                        "worktree",
                        "add",
                        "-q",
                        "-b",
                        worktree.name,
                        str(worktree),
                        base_commit,
                    )
                    current["prompt"] = worker_packet.build_packet(
                        plan,
                        {**manifest["beads"][bead_id], "id": bead_id},
                        worktree,
                        memory_prompt_for(memory),
                        provider=current["provider"],
                        proof_level=manifest.get("proofLevel"),
                        setup=setup,
                        intent_nodes=intent_nodes,
                        previous_outcome=(worker_outcomes or {}).get(bead_id),
                    )
                    current["attempt"] = {
                        "cwd": str(worktree),
                        "baseCommit": base_commit,
                        "startedAt": _now(),
                        "planRevision": current["planRevision"],
                        "requirementRefs": current["requirementRefs"],
                        **_spec_binding(current),
                    }
                    current["redObservations"] = [
                        p if p["phase"] == "contracts" else p["red"]
                        for b in records.values()
                        if (p := (b.get("integrated") or {}).get("semantic")) and p["admitted"]
                    ]
                    current["evidenceDir"] = str(bead_evidence)
                    current["status"] = "running"
                    _write_record(evidence / "run.json", record)
                    running[bead_id] = pool.submit(
                        _attempt_bead,
                        copy.deepcopy(current),
                        manifest["beads"][bead_id],
                        plan=plan,
                        evidence=bead_evidence,
                        fake_worker=fake_workers.get(bead_id),
                        mock_provider=mock_provider,
                        timeout=timeout,
                        execution_job=execution_job,
                    )
                except (OSError, subprocess.SubprocessError, ValueError) as error:
                    current["status"] = f"prelaunch failed: {type(error).__name__}: {error}"
                    fail(bead_id)
                    _write_record(evidence / "run.json", record)
                    _notify_failures(record, evidence)
            if not running:
                continue
            wait(running.values(), return_when=FIRST_COMPLETED)
            # Recheck after each merge: another earlier bead can finish while
            # integrated validation runs. Always choose graph order among the
            # attempts finished now; lesson writes stay on this thread.
            while any(future.done() for future in running.values()):
                bead_id = next(
                    bead_id for bead_id in order if bead_id in running and running[bead_id].done()
                )
                current = records[bead_id]
                try:
                    current.update(running.pop(bead_id).result())
                    if current["status"] == "running":
                        _integrate_bead(
                            current,
                            manifest["beads"][bead_id],
                            repo=repo,
                            manifest=manifest,
                            memory=memory,
                            receipt=Path(current["evidenceDir"]) / "integrated.json",
                            confirmer=confirmer,
                            manifest_path=manifest_path,
                            persist_observation=persist_observation,
                        )
                except Exception as error:
                    # A worker exception fails its bead, preserving independent
                    # work and the failure in the same run's evidence.
                    current["status"] = f"bead failed: {type(error).__name__}: {error}"
                current["finishedAt"] = _now()
                if plan.semantic is not None and current["integrated"]:
                    key = (
                        "semanticContracts"
                        if manifest["beads"][bead_id]["semanticPhase"] == "contracts"
                        else "semanticValidation"
                    )
                    record[key] = current["integrated"]["semantic"]
                if current["integrated"] and current["integrated"].get("claim"):
                    add_claim(current["integrated"]["claim"])
                if current["status"] in {
                    "requirement-validated",
                    STATUS_MOCK_ONLY,
                    "already-satisfied",
                    "contracts-red",
                }:
                    done.add(bead_id)
                    terminal.add(bead_id)
                else:
                    fail(bead_id)
                record["requirementValidation"][order.index(bead_id)] = _requirement_validation(
                    bead_id,
                    plan.beads[bead_id].requirements,
                    plan.beads[bead_id].proof_module,
                    current["integrated"],
                    current["planRevision"],
                    current["requirementRefs"],
                    current.get("specRefs"),
                )
                attempted = [
                    bead for bead in records.values() if (bead["attempt"] or {}).get("mode")
                ]
                record["didExecuteRealProvider"] = bool(attempted) and all(
                    _did_execute_real_provider(bead) for bead in attempted
                )
                _update_efficiency(record)
                _write_record(evidence / "run.json", record)
                _notify_failures(record, evidence)
    # Later beads may own a predecessor's files. Recheck accepted assertions at
    # the final head as well, so their final disposition cannot be stale.
    for bead_id in order:
        if bead_id in done and not prove_reused_bead(bead_id):
            done.remove(bead_id)
    if plan.integration_proof_module is not None:
        commit = _git(repo, "rev-parse", "HEAD")
        old = latest_claim("integration")
        changed = _changed_claim_inputs(old, _claim_inputs(repo, plan, "integration", commit))
        if changed:
            record["integrationValidation"] = {
                **_validate(repo, manifest, plan.integration_proof_module),
                "integrationCommit": commit,
                "candidateTree": _git(repo, "rev-parse", "HEAD^{tree}"),
            }
            claim = _claim(
                repo, plan, "integration", commit, record["integrationValidation"]["passed"]
            )
            record["integrationValidation"]["claim"] = claim
            add_claim(claim)
        else:
            record["integrationValidation"] = previous["integrationValidation"]
        record["requirementValidation"][-1] = _requirement_validation(
            "integration",
            plan.integration_requirements,
            plan.integration_proof_module,
            {
                "integrationCommit": record["integrationValidation"]["integrationCommit"],
                "validation": record["integrationValidation"],
            },
            plan.plan_revision,
            manifest.get("integration", {}).get("requirementRefs", []),
        )
    if previous and plan.semantic is None:
        # Dispositions describe the final head, including assertions made before
        # a later bead changed overlapping ownership. Never leave an early RETAINED.
        seen = set()
        commit = _git(repo, "rev-parse", "HEAD")
        for old in reversed(previous.get("claims", [])):
            bead_id = old["bead"]
            if bead_id in seen or bead_id not in {*plan.beads, "integration"}:
                continue
            if bead_id == "integration" and plan.integration_proof_module is None:
                continue
            seen.add(bead_id)
            changed = _changed_claim_inputs(old, _claim_inputs(repo, plan, bead_id, commit))
            if changed:
                record["claimSummary"]["impacted"].append(
                    {
                        "claimId": old["claimId"],
                        "changedInputs": changed,
                    }
                )
            else:
                record["claimSummary"]["retained"].append(
                    {
                        "claimId": old["claimId"],
                        "why": "verificationIdentity, sourceIdentity and dependencyIdentities "
                        "matched; proofModule and requirementIds matched; previous proof passed",
                    }
                )
    failure = next((bead_id for bead_id in order if bead_id not in done), None)
    if failure is not None:
        status = (
            "pending-judgment"
            if records[failure]["status"] == "awaiting judgment"
            else f"plan-failed: {failure} {records[failure]['status']}"
        )
    elif record["integrationValidation"] and not record["integrationValidation"]["passed"]:
        status = "plan-failed: integration proof failed"
    else:
        status = (
            STATUS_MOCK_ONLY
            if record["isMocked"]
            else STATUS_VALIDATED
            if plan.integration_proof_module is not None or plan.semantic is not None
            else STATUS_VALIDATED_NO_INTEGRATION
        )
    record.update(status=status, finishedAt=_now(), isLiveProductProof=False)
    attempted = [bead for bead in records.values() if (bead["attempt"] or {}).get("mode")]
    record["didExecuteRealProvider"] = bool(attempted) and all(
        _did_execute_real_provider(bead) for bead in attempted
    )
    _update_efficiency(record)
    _write_record(evidence / "run.json", record)
    _notify_failures(record, evidence)
    return record
