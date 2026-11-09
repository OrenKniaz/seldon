"""Publication-bound dispatch; recovery ends or quarantines attempts before readmission."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from codex_harness.core import approvals, dispatch
from codex_harness.core.graph import load_plan
from codex_harness.core.intake import _model
from codex_harness.core.managed_paths import directory, short
from codex_harness.core.plan_store import open_plan_store
from codex_harness.core.provider_launch import get_adapter
from codex_harness.core.record_io import read_record_json, write_record_json, write_record_json_once
from codex_harness.core.spine import compile_manifest

SCHEMA = "seldon/planner-worker-job-v1"


def _now():
    return datetime.now(UTC).isoformat()


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _inside(workspace, path):
    root, path = Path(workspace).resolve(), Path(path)
    if (
        path.is_symlink()
        or any(parent.is_symlink() for parent in path.parents if parent != root)
        or not path.resolve().is_relative_to(root)
    ):
        raise ValueError(f"worker-job-path-outside-workspace: planner_jobs._inside refused path={str(path)!r}; root={str(root)!r}")
    return path.resolve()


def _directory(workspace, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{64}", job_id):
        raise ValueError("worker-job-id-invalid")
    path = _inside(workspace, directory(Path(workspace) / ".codex-harness" / "worker-jobs", job_id))
    if (path / "request.json").is_file() and _request(path)["jobId"] != job_id:
        raise ValueError("worker-job-request-identity-mismatch")
    return path


def execution_inputs(store):
    spine = store.spine()
    payload = dict(
        nodes={n["id"]: n for n in spine.to_dict()["nodes"]
               if not spine.semantic or n["fields"].get("schema") != "dot-reviewer-selection-v1"},
        files={} if spine.semantic else file_inputs(store.workspace / "template"),
    )
    return dict(payload, digest=_digest(payload))


def file_inputs(template):
    if template.is_symlink() or not template.is_dir():
        raise ValueError("worker-job-template-required")
    result = {}
    for path in sorted(template.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(template.resolve()):
            raise ValueError("worker-job-template-path-invalid")
        if ".git" in path.relative_to(template).parts:
            raise ValueError("worker-job-template-git-metadata-refused")
        if path.is_file():
            raw = path.read_bytes()
            result[path.relative_to(template).as_posix()] = dict(
                sha256=hashlib.sha256(raw).hexdigest(),
                content=raw.decode("utf-8", errors="backslashreplace"),
            )
        elif not path.is_dir():
            raise ValueError("worker-job-template-file-invalid")
    return result


def _files(template):
    return {name: item["sha256"] for name, item in file_inputs(template).items()}


def snapshot_plan(template, path, manifest, files):
    path.mkdir(parents=True)
    shutil.copytree(template, path / "template")
    if files != _files(template) or files != _files(path / "template"):
        raise ValueError("worker-job-template-changed-during-snapshot")
    write_record_json_once(path / "sample.json", manifest)
    return load_plan(path)


def _publication(store, expected_revision):
    meta = store._meta()
    if meta["published_revision"] != expected_revision or not expected_revision:
        raise ValueError("worker-job-current-publication-required")
    if meta["phase"] not in {"publish", "execute"} or not store.publication_current(
        store.manifest_revision()
    ):
        raise ValueError("worker-job-publication-stale-or-gated")
    manifest = store.published_manifest(expected_revision)
    template = _inside(store.workspace, store.workspace / "template")
    current = compile_manifest(store.spine(), template)
    expected = dict(manifest)
    if expected.get("proofLevel") == "prospective":
        expected["planRevision"] = expected.pop("sourceManifestRevision", None)
        expected.pop("proofLevel")
        expected.pop("proofLevelReason", None)
    if current != expected or "requirementFilesRevision" not in manifest:
        raise ValueError("worker-job-published-manifest-template-mismatch")
    store.check_requirement_files(manifest, template)
    return manifest, template


def _workers(manifest, workers):
    if not isinstance(workers, list) or len(workers) != len(manifest["beads"]):
        raise ValueError("worker-job-explicit-worker-tuple-required-for-every-bead")
    result = {}
    for worker in workers:
        if not isinstance(worker, dict) or set(worker) != {"bead", "provider", "model", "effort"}:
            raise ValueError("worker-job-worker-requires-bead-provider-model-effort")
        if any(not isinstance(value, str) or not value.strip() for value in worker.values()):
            raise ValueError("worker-job-worker-values-must-be-nonempty")
        bead = worker["bead"]
        if bead not in manifest["beads"] or bead in result:
            raise ValueError("worker-job-worker-bead-invalid-or-duplicate")
        if worker["provider"] != worker["provider"].strip().lower():
            raise ValueError("worker-job-provider-must-use-canonical-name")
        get_adapter(worker["provider"])
        _model(worker["model"])
        result[bead] = dict(worker)
    return [result[bead] for bead in sorted(result)]


def _request(directory):
    request = read_record_json(_inside(directory, directory / "request.json"))
    original = {key: value for key, value in request.items() if key != "requestDigest"}
    if (
        request.get("schema") != SCHEMA
        or directory.name not in {request.get("jobId"), short(request.get("jobId", ""))}
        or not re.fullmatch(r"[0-9a-f]{64}", request.get("publicationRevision", ""))
        or request.get("requestDigest") != _digest(original)
    ):
        raise ValueError("worker-job-request-identity-mismatch")
    return request


def _valid_timeout(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def verify_job(directory, record=None):
    from codex_harness.core.plan_store import open_plan_store_readonly
    from codex_harness.core.planner_execution import admitted_execution

    if directory is None:
        raise ValueError("provider-call-owned-native-job-required")
    directory = Path(directory).resolve()
    workspace = directory.parent.parent.parent
    request = _request(directory)
    if _directory(workspace, request["jobId"]) != directory:
        raise ValueError("provider-call-job-path-invalid")
    scope = request.get("executionScope")
    with open_plan_store_readonly(workspace) as store:
        owned_segment(directory)
        if not admitted_execution(
            store, request.get("executionRequestId"), scope, request["jobId"]
        ):
            raise ValueError("provider-call-execution-binding-stale")
    snapshot = read_record_json(_inside(directory, directory / "plan/sample.json"))
    if (
        _digest(snapshot) != request["manifestDigest"]
        or _files(directory / "plan/template") != request["templateFiles"]
    ):
        raise ValueError("provider-call-protected-snapshot-changed")
    if record is not None:
        from codex_harness.core.attempt_provenance import normalized_owned

        selected = next((w for w in scope["workers"] if w["bead"] == record.get("bead")), None)
        bead = snapshot["beads"].get(record.get("bead"), {})
        if (
            selected is None
            or any(record.get(k) != selected[k] for k in ("provider", "model", "effort"))
            or record.get("planRevision") != request["publicationRevision"]
            or normalized_owned(record.get("ownedFiles", []))
            != normalized_owned(bead.get("ownedFiles", []))
            or record.get("requirementRefs") != bead.get("requirementRefs")
            or record.get("specRefs") != bead.get("specRefs")
        ):
            raise ValueError("provider-call-worker-scope-mismatch")
    return request


def start_worker_job(
    store, expected_revision, workers, *, max_parallel=2, timeout=900, segment_id=None
):
    """Publish root admission before admitting a separately committed segment."""
    from codex_harness.core.run_ownership import ensure_effect
    from codex_harness.core.run_loop import cursor, save
    from codex_harness.core.planner_recovery import admission_id
    from codex_harness.core.planner_execution import (
        admit_execution, admitted_execution, execution_scope, require_execution,
    )

    request = None
    with store._transaction():
        ensure_effect(store)
        if type(max_parallel) is not int or max_parallel < 1:
            raise ValueError("worker-job-max-parallel-must-be-positive")
        if not _valid_timeout(timeout):
            raise ValueError("worker-job-timeout-must-be-positive")
        job_id = admission_id(store, expected_revision)
        directory = _directory(store.workspace, job_id)
        if (directory / "request.json").is_file():
            previous = _request(directory)
            manifest = read_record_json(_inside(directory, directory / "plan/sample.json"))
            options = dict(workers=_workers(manifest, workers), maxParallel=max_parallel, timeout=timeout)
            if previous["options"] != options:
                raise ValueError("worker-job-publication-already-admitted-with-other-options")
            segment = latest_segment(store, job_id, admitted=True)
            if segment != directory:
                run = cursor(store)
                run["effect"]["segmentId"] = segment.name
                save(store, run)
                return worker_job_status(store.workspace, job_id)
        else:
            # No root request means no segment could have entered. Only this
            # transaction's admission owner may discard its confined partial copy.
            if directory.exists():
                shutil.rmtree(directory)
            manifest, template = _publication(store, expected_revision)
            selection = _workers(manifest, workers)
            scope = execution_scope(
                store, expected_revision, selection, max_parallel=max_parallel, timeout=timeout,
            )
            require_execution(store, scope)
            directory.mkdir(parents=True)
            execution_id = admit_execution(store, scope, job_id)
            execution_provenance = admitted_execution(store, execution_id, scope, job_id)["channel"]
            before = _files(template)
            if before != scope["templateFiles"]:
                raise ValueError("worker-job-template-changed-before-snapshot")
            snapshot_plan(template, directory / "plan", manifest, before)
            if _publication(store, expected_revision)[0] != manifest:
                raise ValueError("worker-job-publication-changed-during-snapshot")
            request = dict(
                schema=SCHEMA, jobId=job_id, containment="windows-job-monitor-v1",
                publicationRevision=expected_revision, manifestDigest=_digest(manifest),
                templateFiles=before,
                options=dict(workers=selection, maxParallel=max_parallel, timeout=timeout),
                admittedAt=_now(), executionRequestId=execution_id, executionScope=scope,
                executionProvenance=execution_provenance,
            )
            predecessor = next(
                (row for row in approvals.records(store, "worker-recovery-resolved")
                 if row.get("successor") == job_id), None,
            )
            if predecessor and predecessor["proof"].get("revalidation", {}).get("runPath"):
                prior = read_record_json(Path(predecessor["proof"]["revalidation"]["runPath"]))
                request["previousDelivery"] = prior.get("delivery", {})
            request["requestDigest"] = _digest(request)
    if request is not None:
        with store._transaction():
            ensure_effect(store)
            write_record_json_once(directory / "request.json", request)
    return start_segment(store, job_id, segment_id, None)


def worker_job_status(workspace, job_id):
    """Read the selected segment outcome; incomplete publication stays pending."""
    directory = _directory(workspace, job_id)
    if not directory.is_dir():
        raise ValueError("worker-job-not-found")
    result = {
        "jobId": job_id,
        "publicationRevision": job_id,
        "status": "unknown",
        "liveness": "unknown",
        "didRunProvider": None,
        "harnessDeliveryProven": False,
        "evidenceDir": str(directory),
    }
    if not (directory / "request.json").is_file():
        return {**result, "status": "pending", "reason": "Admission pending; the run loop re-admits unchanged work"}
    request = _request(directory)
    result["publicationRevision"] = request["publicationRevision"]
    result["options"] = request["options"]
    has_execution = bool(request.get("executionRequestId") and request.get("executionScope"))
    # Before this field existed, the execution-original contract was native-only.
    provenance = request.get("executionProvenance") or (
        "mcp-client-elicitation" if has_execution else "unverified-or-unavailable"
    )
    result["approvalProvenance"] = provenance
    result["nativeApproval"] = (
        "recorded" if has_execution and provenance == "mcp-client-elicitation" else "unproven"
    )
    result["approvalAssurance"] = (
        "caller-mediated"
        if provenance == "chat-confirmation"
        else "client-reported"
        if has_execution
        else "unproven"
    )
    result["actorIdentityVerified"] = False
    result["capacityVerified"] = False
    from codex_harness.core.plan_store import open_plan_store_readonly

    with open_plan_store_readonly(workspace) as store:
        segment = latest_segment(store, job_id)
        if segment == directory or not (segment / "request.json").is_file():
            return dict(result, status="pending", reason="Segment admission pending")
        observed = segment_status(store, dict(jobId=job_id, id=segment.name))
        from codex_harness.core.run_loop import cursor

        current = cursor(store)
        effect = current.get("effect") or {}
        pending = current.get("jobId") == job_id and effect.get("kind") == "judgment-continuation" and (
            observed.get("terminal", {}).get("continuation", {}).get("effectId") != effect["id"]
        )
    result.update(observed)
    result["status"] = "pending" if pending else observed.get("terminal", {}).get("status", "running" if observed.get("launch") else "pending")
    runs = directory / "runs"
    paths = list(runs.glob("*/evidence/run.json")) if runs.is_dir() else []
    if len(paths) == 1:
        path = _inside(directory, paths[0])
        run = read_record_json(path)
        if run.get("control", {}).get("effectId") != observed.get("terminal", {}).get("continuation", {}).get("effectId", segment.name):
            result["status"] = "pending"
        if (
            run.get("planRevision") != request["publicationRevision"]
            or Path(run.get("repo", "")).resolve() != (path.parent.parent / "repo").resolve()
        ):
            raise ValueError("worker-job-dispatch-identity-mismatch")
        result["run"] = {
            "runId": run["runId"],
            "status": run["status"],
            "recordPath": str(path),
            "repo": run["repo"],
            "isMocked": run["isMocked"],
            "didExecuteRealProvider": run["didExecuteRealProvider"],
            **{
                key: run[key]
                for key in ("semanticContracts", "semanticValidation", "delivery")
                if key in run
            },
            "beads": {
                key: {
                    **{
                        field: bead.get(field)
                        for field in ("status", "provider", "model", "effort", "guard")
                    },
                    "candidateRoot": (bead.get("integrated") or {}).get("root")
                    or (bead.get("attempt") or {}).get("cwd"),
                }
                for key, bead in run["beads"].items()
            },
        }
        result["didRunProvider"] = run["didExecuteRealProvider"]
    elif paths:
        raise ValueError("worker-job-dispatch-record-ambiguous")
    if result["status"] != "completed":
        result["reason"] = (
            "Awaiting judgment; inspect the bound candidate before revalidation."
            if result["status"] == "pending-judgment"
            else "Worker started; completion and current liveness remain unproven."
            if result["status"] == "running"
            else "The run loop reconciles failed or stalled work within current B."
        )
    from codex_harness.core.planner_recovery import worker_status

    return worker_status(workspace, result)


def admitted_work(store, directory, request):
    """Bind work to the admitted B before controller or provider launch."""
    manifest = read_record_json(directory / "plan/sample.json")
    authority = next(
        (
            r
            for r in approvals.records(store, approvals.EVENT)
            if r["id"] == request["executionRequestId"]
        ),
        None,
    )
    if authority is None:
        raise ValueError("Recovery requires original bound B; retain evidence and inspect admission")
    accepted = authority["subject"]["scope"]["nodes"]
    admitted = request["executionScope"]["inputs"]["nodes"]
    work = {}
    for key in manifest["beads"]:
        node = accepted[key] if key in accepted else admitted[key]
        todo, upstream = list(node["parents"]), {}
        while todo:
            identifier = todo.pop()
            if identifier in upstream:
                continue
            item = accepted[identifier] if identifier in accepted else admitted[identifier]
            if item["kind"] != "vision":
                upstream[identifier] = item
                todo.extend(item["parents"])
        work[key] = _digest(
            dict(
                bead=key,
                phase=manifest["beads"][key].get("semanticPhase"),
                ownedFiles=node["fields"]["ownedFiles"],
                obligations=upstream,
            )
        )
    return work


def segment_outcomes(directory, segment, *, effect_id=None):
    """Publish work and launch certainty even when dispatch has no attempt."""
    paths = list(directory.glob("runs/*/evidence/run.json"))
    run = read_record_json(_inside(directory, paths[0])) if len(paths) == 1 else {}
    if (run.get("control") or {}).get("effectId") != (effect_id or segment["segmentId"]):
        retained = segment.get("resumeRun")
        run = dict(beads={key: bead for key, bead in run.get("beads", {}).items()
            if retained and Path(retained).resolve() == paths[0].parent.parent.resolve()
            and (bead.get("integrated") or {}).get("semantic", {}).get("admitted")
        })
    entered = (directory / "segments" / segment["segmentId"] / "started.json").is_file()
    unobserved = "unknown launch" if entered else "not launched"
    outcomes = {}
    for key, work in segment["work"].items():
        bead = run.get("beads", {}).get(key, {})
        work_status = bead.get("status", unobserved)
        if work_status in {"contracts-red", "requirement-validated", dispatch.STATUS_MOCK_ONLY,
            "already-satisfied", "reused",
        }:
            outcomes[key] = dict(workId=work, status=work_status, workStatus=work_status,
                                 fault=False, permissionGuard={}, launchCertain=True)
            continue  # Replacement controllers account only for unfinished work.
        result_path = _inside(directory, Path(bead.get("evidenceDir", directory)) / "attempt/result.json")
        result = read_record_json(result_path) if result_path.is_file() else {}
        outcomes[key] = dict(
            workId=work, workStatus=work_status, permissionGuard=result.get("permissionGuard") or {},
            status=result.get("status", "not launched" if bead and not bead.get("attempt") else unobserved),
            fault=not bead or bool(
                bead.get("attempt") or work_status.startswith("prelaunch failed:")),
            launchCertain=not entered or bool(bead) and (
                not bead.get("attempt") or bead.get("isMocked") or type(result.get("didRunProvider")) is bool
            ),
        )
    return outcomes


def dispatch_job(store, directory, request, resume_run_dir=None):
    workers = _workers(
        read_record_json(directory / "plan/sample.json"), request["options"]["workers"]
    )
    spine = store.spine()
    from codex_harness.core.planner_recovery import RESOLVED

    run = dispatch.run_plan(
        directory / "plan",
        work_root=directory / "runs",
        resume_run_dir=resume_run_dir,
        provider_for={w["bead"]: w["provider"] for w in workers},
        model_for={w["bead"]: w["model"] for w in workers},
        effort_for={w["bead"]: w["effort"] for w in workers},
        max_parallel=request["options"]["maxParallel"],
        timeout=request["options"]["timeout"],
        execution_job=directory,
        setup=spine.node(spine.ids("vision")[0])["fields"].get("setup", {}),
        intent_nodes=store.locked_acceptance_spine().to_dict()["nodes"],
        worker_outcomes=next(
            (
                {
                    key: value
                    + "; "
                    + r["reason"]
                    + "; "
                    + json.dumps(r["proof"].get("repairObservation"))
                    for key, value in r["proof"].get("faultOutcomes", {}).items()
                }
                for r in approvals.records(store, RESOLVED)
                if r.get("successor") == request["jobId"]
            ),
            {},
        ),
    )
    from codex_harness.core.candidate_delivery import deliver

    deliver(store.workspace, request, run, request["publicationRevision"])
    return run


def segment_directory(workspace, job_id, segment_id):
    if not isinstance(segment_id, str) or not re.fullmatch(r"[0-9a-f]{32}", segment_id):
        raise ValueError("worker-segment-id-invalid")
    return _inside(workspace, _directory(workspace, job_id) / "segments" / segment_id)


def latest_segment(store, job_id, *, admitted=False):
    """The committed build identity owns even an interrupted admission."""
    from codex_harness.core.run_loop import EVENT

    row = store.connection.execute(
        "SELECT payload_json FROM events WHERE kind=? "
        "AND json_extract(payload_json,'$.effect.kind')='build' "
        "AND json_extract(payload_json,'$.effect.jobId')=? "
        "AND (?=0 OR json_extract(payload_json,'$.effect.segmentId') IS NOT NULL) "
        "ORDER BY id DESC LIMIT 1",
        (EVENT, job_id, int(admitted)),
    ).fetchone()
    if row:
        effect = json.loads(row[0])["effect"]
        return segment_directory(store.workspace, job_id, effect.get("segmentId") or effect["id"])
    return _directory(store.workspace, job_id)


def owned_segment(directory):
    """Resolve a fresh bounded owner without changing the original admission."""
    from codex_harness.core.plan_store import open_plan_store_readonly
    from codex_harness.core.run_loop import cursor
    from codex_harness.core.run_ownership import ensure_effect

    directory = Path(directory).resolve()
    workspace = directory.parent.parent.parent
    request = _request(directory)
    with open_plan_store_readonly(workspace) as store:
        run = cursor(store)
        ensure_effect(store, run["generation"])
        effect = run["effect"]
        path = segment_directory(workspace, request["jobId"], effect["id"])
        started, segment = (
            read_record_json(path / "started.json"),
            read_record_json(path / "request.json"),
        )
        if (
            effect.get("jobId") != request["jobId"]
            or segment["generation"] != run["generation"]
            or started.get("pid") != os.getpid()
            or started.get("requestDigest") != request["requestDigest"]
        ):
            raise ValueError("provider-call-owned-segment-required")
        return path


def start_segment(store, job_id, segment_id, resume_run_dir):
    """A new finite owner for retained work; admission identity precedes Popen."""
    from codex_harness.core.run_loop import cursor, save
    from codex_harness.core.run_ownership import ensure_effect
    from codex_harness.core.audit_process import process_observation

    run = cursor(store)
    ensure_effect(store, run["generation"])
    segment_id = segment_id or run["effect"]["id"]
    path = segment_directory(store.workspace, job_id, segment_id)
    path.mkdir(parents=True)
    request = dict(
        jobId=job_id,
        segmentId=segment_id,
        generation=run["generation"],
        deadlineAt=run["effect"]["deadlineAt"],
        resumeRun=resume_run_dir,
        work=admitted_work(store, path.parent.parent, _request(path.parent.parent)),
    )
    write_record_json_once(path / "request.json", request)
    with store._transaction():
        ensure_effect(store, run["generation"])
        run["jobId"] = job_id
        run["effect"].update(jobId=job_id, segmentId=segment_id)
        save(store, run)
    command = [
        sys.executable,
        "-P",
        "-B",
        "-m",
        "codex_harness.core.planner_jobs",
        "--workspace",
        str(store.workspace),
        "--job-id",
        job_id,
        "--segment",
        segment_id,
    ]
    flags = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    with store._transaction():
        ensure_effect(store, run["generation"], segment_id)
        with (path / "stdout.txt").open("xb") as stdout, (path / "stderr.txt").open("xb") as stderr:
            child = subprocess.Popen(
                command,
                cwd=store.workspace,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
                **flags,
            )
        write_record_json_once(
            path / "launch.json",
            dict(
                pid=child.pid,
                at=_now(),
                processIdentity=process_observation(child.pid).get("identity"),
            ),
        )
    return dict(jobId=job_id, segmentId=segment_id)


def verified_segment_exit(request, segment_id, receipt):
    if not receipt:
        return False
    if any(
        r.get("requestDigest") != request["requestDigest"] or r.get("segmentId") != segment_id
        for r in (receipt, receipt["terminal"])
    ):
        raise ValueError("worker-job-outcome-identity-mismatch")
    control = receipt.get("control", {})
    return (
        control.get("backend") == "windows-job"
        and control.get("membershipVerifiedBeforePayload") is True
        and control.get("quiescenceVerified") is True
        and control.get("activeProcessesAfterTeardown") == 0
    )


def finish_segment(directory, path, control, code=1):
    """The bounded owner publishes outcome and cleanup together, last."""
    request, segment = _request(directory), read_record_json(path / "request.json")
    terminal_path = path / "terminal.json"
    terminal = read_record_json(terminal_path) if terminal_path.is_file() else dict(
        status="failed", reason="Payload exited without a terminal observation",
        exitCode=code, requestDigest=request["requestDigest"], segmentId=path.name,
        at=_now(), outcomes=segment_outcomes(directory, segment),
    )
    outcome = dict(
        requestDigest=request["requestDigest"], segmentId=path.name,
        control=control, terminal=terminal,
    )
    if not verified_segment_exit(request, path.name, outcome):
        terminal.update(status="failed", reason="Segment cleanup remains unverified")
    try:
        write_record_json_once(path / "quiescence.json", outcome)
    except FileExistsError:
        pass  # The bounded monitor published this segment's outcome first.


def complete_continuation(store, result):
    from codex_harness.core.run_loop import cursor
    from codex_harness.core.run_ownership import ensure_effect

    with store._transaction():
        run = cursor(store)
        ensure_effect(store)
        path = latest_segment(store, run["jobId"]) / "quiescence.json"
        outcome = read_record_json(path)
        if result.get("proof"):
            outcome["terminal"]["outcomes"] = segment_outcomes(
                path.parent.parent.parent, read_record_json(path.parent / "request.json"),
                effect_id=run["effect"]["id"],
            )
        outcome["terminal"].update(
            status="completed" if result["state"] == "PASS" else
            result["state"] if result["state"] in {"pending-build", "pending-judgment"} else "failed",
            dispatchStatus=result["state"],
            continuation=dict(effectId=run["effect"]["id"], result=result),
        )
        write_record_json(path, outcome)


def segment_status(store, effect):
    from codex_harness.core.audit_process import process_observation

    workspace, job_id = store.workspace, effect["jobId"]
    segment_id = effect.get("segmentId", effect["id"])
    path = segment_directory(workspace, job_id, segment_id)
    result = {}
    for name in ("quiescence", "launch"):
        if (path / (name + ".json")).exists():
            result[name] = read_record_json(path / (name + ".json"))
    if outcome := result.get("quiescence"):
        result["terminal"] = outcome["terminal"]
    if launch := result.get("launch"):
        observed = process_observation(launch["pid"])
        result["processObservation"] = observed
        result["launched"] = bool(
            launch.get("processIdentity")
            and observed.get("identity") == launch["processIdentity"]
            and observed.get("running")
        )
        result["deadOwner"] = observed.get("running") is False
    result["verifiedExit"] = bool(outcome) and verified_segment_exit(
        _request(_directory(workspace, job_id)),
        segment_id,
        result.get("quiescence", {}),
    )
    return result


def execute_worker_job(workspace, job_id, segment_id):
    """One bounded dispatch segment ends before any semantic question is asked."""
    directory = _directory(workspace, job_id)
    path = segment_directory(workspace, job_id, segment_id)
    request, segment = _request(directory), read_record_json(path / "request.json")
    write_record_json_once(
        path / "started.json",
        dict(requestDigest=request["requestDigest"], at=_now(), pid=os.getpid()),
    )
    terminal = dict(requestDigest=request["requestDigest"], segmentId=segment_id, status="failed")
    try:
        with open_plan_store(workspace) as store:
            store._run_generation = segment["generation"]
            run = dispatch_job(store, directory, request, segment["resumeRun"])
        terminal.update(
            runId=run["runId"],
            dispatchStatus=run["status"],
            status="pending-judgment"
            if run["status"] == "pending-judgment"
            else "failed"
            if run["status"].startswith("plan-failed")
            else "completed",
        )
    except Exception as error:
        terminal["error"] = dict(
            type=type(error).__name__,
            message=str(error),
            traceback="".join(traceback.format_exception(error, limit=-6))[-4096:],
        )
    terminal["at"] = _now()
    terminal["outcomes"] = segment_outcomes(directory, segment)
    write_record_json_once(path / "terminal.json", terminal)
    return terminal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--segment", required=True)
    args = parser.parse_args(argv)
    from codex_harness.core.planner_recovery import monitor

    return monitor(args.workspace, args.job_id, args.segment)


if __name__ == "__main__":
    raise SystemExit(main())
