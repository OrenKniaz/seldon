"""Publication-bound background worker dispatch; records are observations, not liveness.

One publication admits one original job. Reconnecting never retries an ambiguous
launch. Existing dispatcher attempt/recovery records remain the worker authority.
This local protocol is not authentication or confinement of the host planner.
"""

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
import time
from datetime import UTC, datetime
from pathlib import Path

from codex_harness.core import dispatch
from codex_harness.core.graph import load_plan
from codex_harness.core.intake import _model
from codex_harness.core.managed_paths import directory, short
from codex_harness.core.plan_store import open_plan_store
from codex_harness.core.provider_launch import get_adapter
from codex_harness.core.record_io import read_record_json, write_record_json_once
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
        raise ValueError("worker-job-path-outside-workspace")
    return path.resolve()


def _directory(workspace, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{64}", job_id):
        raise ValueError("worker-job-id-invalid")
    path = _inside(workspace, directory(Path(workspace) / ".codex-harness" / "worker-jobs", job_id))
    if (path / "request.json").is_file() and _request(path)["jobId"] != job_id:
        raise ValueError("worker-job-request-identity-mismatch")
    return path


def _files(template):
    if template.is_symlink() or not template.is_dir():
        raise ValueError("worker-job-template-required")
    result = {}
    for path in sorted(template.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(template.resolve()):
            raise ValueError("worker-job-template-path-invalid")
        if ".git" in path.relative_to(template).parts:
            raise ValueError("worker-job-template-git-metadata-refused")
        if path.is_file():
            result[path.relative_to(template).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        elif not path.is_dir():
            raise ValueError("worker-job-template-file-invalid")
    return result


def _publication(store, expected_revision):
    status = store.status()
    if status["published_revision"] != expected_revision or not expected_revision:
        raise ValueError("worker-job-current-publication-required")
    if (
        status["phase"] not in {"publish", "execute"}
        or status["pending_gate"] is not None
        or not store.publication_current(store.manifest_revision())
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


def start_worker_job(
    store, expected_revision, workers, *, max_parallel=2, timeout=900, call_cap=None
):
    """Admit once and return promptly; no mock, executable override or retry surface.

    New admissions check the current B subject against the actual execution scope.
    A local call ceiling is not verified subscription quota or human identity.
    """
    if type(max_parallel) is not int or max_parallel < 1:
        raise ValueError("worker-job-max-parallel-must-be-positive")
    if not _valid_timeout(timeout):
        raise ValueError("worker-job-timeout-must-be-positive")
    from codex_harness.core.planner_recovery import admission_id

    job_id = admission_id(store, expected_revision)
    directory = _directory(store.workspace, job_id)

    def reattach():
        if (directory / "request.json").is_file():
            previous = _request(directory)
            manifest = read_record_json(_inside(directory, directory / "plan/sample.json"))
            selection = _workers(manifest, workers)
            options = dict(
                workers=selection,
                maxParallel=max_parallel,
                timeout=timeout,
            )
            if "callCap" in previous["options"]:
                options["callCap"] = (
                    previous["options"]["callCap"] if call_cap is None else call_cap
                )
            elif call_cap is not None:
                raise ValueError("worker-job-publication-already-admitted-with-other-options")
            if previous["options"] != options:
                raise ValueError("worker-job-publication-already-admitted-with-other-options")
        return worker_job_status(store.workspace, job_id)

    # An unlaunched job may be re-admitted within the current B limits;
    # retain the old namespace as evidence.
    stranded = directory.is_dir() and _ended_before_launch(directory)
    if directory.is_dir() and not stranded:
        return reattach()
    from codex_harness.core.planner_execution import (
        admit_execution,
        admitted_execution_provenance,
        execution_scope,
        require_execution,
    )

    manifest, template = _publication(store, expected_revision)
    selection = _workers(manifest, workers)
    scope = execution_scope(
        store,
        expected_revision,
        selection,
        max_parallel=max_parallel,
        timeout=timeout,
        call_cap=call_cap,
    )
    require_execution(store, scope)
    options = {
        "workers": selection,
        "maxParallel": max_parallel,
        "timeout": timeout,
        "callCap": call_cap,
    }
    directory.parent.mkdir(parents=True, exist_ok=True)
    if stranded:
        index = 1
        while directory.with_name(f"{directory.name}.prelaunch-failed-{index}").exists():
            index += 1
        directory.rename(directory.with_name(f"{directory.name}.prelaunch-failed-{index}"))
    try:
        directory.mkdir()
    except FileExistsError:
        # A concurrent writer may not have published the original yet. Never
        # wait for it or seize its directory and launch another child.
        return reattach()
    try:
        execution_id = admit_execution(store, scope, job_id)
        execution_provenance = admitted_execution_provenance(store, execution_id, scope, job_id)
        before = _files(template)
        if before != scope["templateFiles"]:
            raise ValueError("worker-job-template-changed-before-snapshot")
        plan = directory / "plan"
        plan.mkdir()
        shutil.copytree(template, plan / "template")
        if before != _files(template) or before != _files(plan / "template"):
            raise ValueError("worker-job-template-changed-during-snapshot")
        write_record_json_once(plan / "sample.json", manifest)
        load_plan(plan)
        # Recheck after copying, before publishing an executable admission.
        if _publication(store, expected_revision)[0] != manifest:
            raise ValueError("worker-job-publication-changed-during-snapshot")
        request = {
            "schema": SCHEMA,
            "jobId": job_id,
            "containment": "windows-job-monitor-v1",
            "publicationRevision": expected_revision,
            "manifestDigest": _digest(manifest),
            "templateFiles": before,
            "options": options,
            "admittedAt": _now(),
            "executionRequestId": execution_id,
            "executionScope": scope,
            "executionProvenance": execution_provenance,
        }
        request["requestDigest"] = _digest(request)
        write_record_json_once(directory / "request.json", request)
        command = [
            sys.executable,
            "-P",
            "-B",
            "-m",
            "codex_harness.core.planner_jobs",
            "--workspace",
            str(store.workspace.resolve()),
            "--job-id",
            job_id,
        ]
        flags = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        with (directory / "stdout.txt").open("xb") as stdout:
            with (directory / "stderr.txt").open("xb") as stderr:
                child = subprocess.Popen(
                    command,
                    cwd=store.workspace,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    # A product package must not replace this controller before
                    # its publication guards run. -P removes implicit cwd; pin
                    # only this child's import path, keeping all other provider
                    # account/configuration environment settings unchanged.
                    env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
                    **flags,
                )
        write_record_json_once(
            directory / "launch.json",
            {"requestDigest": request["requestDigest"], "pid": child.pid, "at": _now()},
        )
    except Exception as error:
        # If Popen succeeded but writing launch failed, a child may be running.
        # Record an admission error, never a false terminal outcome or relaunch.
        write_record_json_once(
            directory / "admission-error.json",
            {"type": type(error).__name__, "message": str(error), "at": _now()},
        )
        raise
    return worker_job_status(store.workspace, job_id)


def worker_job_status(workspace, job_id, *, wait_seconds=0):
    """Read durable observations only, including after a new MCP connection.

    No terminal original means UNKNOWN, even if launch/started records exist.
    A PID and an old running snapshot are never evidence of current liveness.
    """
    if type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= 600:
        raise ValueError("wait_seconds must be between 0 and 600")
    if wait_seconds:
        deadline = time.monotonic() + wait_seconds
        current = worker_job_status(workspace, job_id)
        while "terminal" not in current:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1, remaining))
            current = worker_job_status(workspace, job_id)
        return current
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
    error_path = _inside(directory, directory / "admission-error.json")
    if error_path.is_file():
        result["admissionError"] = read_record_json(error_path)
    if not (directory / "request.json").is_file():
        return {**result, "reason": "Admission incomplete; do not relaunch"}
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
    result["providerCallCap"] = request["options"].get("callCap")
    result["capacityVerified"] = False
    for name in ("started", "terminal"):
        path = directory / f"{name}.json"
        if not path.is_file():
            continue
        record = read_record_json(_inside(directory, path))
        if record.get("requestDigest") != request["requestDigest"]:
            raise ValueError("worker-job-outcome-identity-mismatch")
        result[name] = record
        if name == "terminal":
            result["status"] = record["status"]
    # Dispatcher records can advance while the long job is running. They remain
    # observed snapshots, and cannot substitute for a completed job original.
    runs = directory / "runs"
    paths = list(runs.glob("*/evidence/run.json")) if runs.is_dir() else []
    if len(paths) == 1:
        path = _inside(directory, paths[0])
        run = read_record_json(path)
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
                key: {field: bead.get(field) for field in ("status", "provider", "model", "effort")}
                for key, bead in run["beads"].items()
            },
        }
        result["didRunProvider"] = run["didExecuteRealProvider"]
    elif paths:
        raise ValueError("worker-job-dispatch-record-ambiguous")
    if "terminal" not in result:
        result["reason"] = "No terminal original; read dispatcher evidence; do not relaunch"
    elif result["terminal"]["status"] == "failed" or (
        result["terminal"]["status"] == "completed"
        and result["terminal"].get("dispatchStatus", "").startswith("plan-failed: ")
    ):
        result["reason"] = (
            "Reconnect reattaches to inspect original evidence. "
            "Replacement-controller continuation is not supported. "
            "A terminal record does not establish controller exit or current child liveness; "
            "do not relaunch ambiguous work."
        )
        if _ended_before_launch(_directory(workspace, job_id)):
            result["reason"] = (
                "Ended before any provider launched. Retry within the current B limits; "
                "harness_worker_start re-admits this publication."
            )
    from codex_harness.core.planner_recovery import worker_status

    return worker_status(workspace, result)


def _ended_before_launch(directory):
    """Terminal job whose every bead stopped before a provider attempt existed."""
    try:
        terminal = read_record_json(_inside(directory, directory / "terminal.json"))
        runs = list((directory / "runs").glob("*/evidence/run.json"))
        records = [read_record_json(_inside(directory, path)) for path in runs]
    except (OSError, ValueError):
        return False
    return (
        terminal.get("status") == "completed"
        and terminal.get("dispatchStatus", "").startswith("plan-failed: ")
        and len(records) == 1
        and records[0].get("didExecuteRealProvider") is False
        and all(
            bead.get("attempt") is None and bead.get("didRunProvider") is False
            for bead in records[0].get("beads", {}).values()
        )
    )


def execute_worker_job(workspace, job_id):
    """Owned background entry point, at most once; use the ordinary dispatcher."""
    directory = _directory(workspace, job_id)
    request = _request(directory)
    # Exclusive marker precedes all dispatch work. A duplicate child must stop.
    write_record_json_once(
        directory / "started.json",
        {"requestDigest": request["requestDigest"], "at": _now(), "pid": os.getpid()},
    )
    terminal = {"requestDigest": request["requestDigest"], "status": "failed"}
    try:
        plan = directory / "plan"
        manifest = read_record_json(_inside(directory, plan / "sample.json"))
        if (
            _digest(manifest) != request["manifestDigest"]
            or _files(plan / "template") != request["templateFiles"]
        ):
            raise ValueError("worker-job-snapshot-identity-mismatch")
        database = _inside(workspace, Path(workspace) / ".codex-harness" / "plan.sqlite3")
        if not database.is_file():
            raise ValueError("worker-job-plan-store-missing")
        with open_plan_store(workspace) as store:
            if _publication(store, request["publicationRevision"])[0] != manifest:
                raise ValueError("worker-job-publication-changed-before-launch")
            spine = store.spine()
            setup = spine.node(spine.ids("vision")[0])["fields"].get("setup", {})
            intent_nodes = store.locked_acceptance_spine().to_dict()["nodes"]
        workers = _workers(manifest, request["options"]["workers"])
        run = dispatch.run_plan(
            plan,
            work_root=directory / "runs",
            provider_for={row["bead"]: row["provider"] for row in workers},
            model_for={row["bead"]: row["model"] for row in workers},
            effort_for={row["bead"]: row["effort"] for row in workers},
            max_parallel=request["options"]["maxParallel"],
            timeout=request["options"]["timeout"],
            max_provider_calls=request["options"]["callCap"],
            execution_job=directory,
            setup=setup,
            intent_nodes=intent_nodes,
        )
        from codex_harness.core.planner_candidate import deliver

        deliver(workspace, request, run)
        terminal.update(runId=run["runId"], dispatchStatus=run["status"])
        terminal["status"] = "failed" if run["status"].startswith("plan-failed") else "completed"
    except Exception as error:
        terminal["error"] = {"type": type(error).__name__, "message": str(error)}
    terminal["at"] = _now()
    write_record_json_once(directory / "terminal.json", terminal)
    return terminal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    from codex_harness.core.planner_recovery import monitor

    return monitor(args.workspace, args.job_id)


if __name__ == "__main__":
    raise SystemExit(main())
