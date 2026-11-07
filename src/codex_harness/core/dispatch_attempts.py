"""Bounded worker attempts and observed provider provenance."""

from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from tempfile import TemporaryFile
from codex_harness.core import attempt_control, attempt_provenance, efficiency, graph
from codex_harness.core.audit_process import run_audit_process
from codex_harness.core.package_provenance import adapter_provenance
from codex_harness.core.provider_launch import get_adapter
from codex_harness.core.record_io import read_record_json
from codex_harness.core.spine import (
    requirement_file_sources,
)
from codex_harness.core.dispatch_shared import (
    STATUS_PERMISSION_HELD,
    STATUS_AUDIT_HELD,
    STATUS_PROVENANCE_HELD,
    DENIAL_HELD_STATUSES,
    DENIAL_CAPABILITIES,
)
from codex_harness.core.dispatch_repository import (
    _changed_claim_inputs,
    _claim_inputs,
    _did_execute_real_provider,
    _file_sha256,
    _git,
    _git_run,
    _now,
    _requirement_validation,
    _spec_binding,
    _validation_inputs,
    _validation_tool_rules,
    _write_record,
)


def _source_path(path: str) -> bool:
    return "__pycache__" not in path and not path.startswith(".harness/")


def _worker_report(attempt_dir: Path) -> str:
    report = ""
    for event in efficiency.read_events(attempt_dir / "events.jsonl"):
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            report = item.get("text", "")
        elif event.get("type") == "assistant":
            text = "\n".join(
                block["text"]
                for block in event.get("message", {}).get("content", [])
                if isinstance(block, dict) and block.get("type") == "text"
            )
            if text:
                report = text
        elif event.get("type") == "result" and isinstance(event.get("result"), str):
            report = event["result"]
    return report


def _observe_attempt(record: dict, worktree: Path, attempt_dir: Path) -> None:
    # Observation must never fail the bead: record parse problems instead.
    try:
        report = _worker_report(attempt_dir)
    except Exception as error:  # noqa: BLE001
        report = ""
        record["observationError"] = f"worker report: {type(error).__name__}: {error}"
    record["workerReport"] = report[:4000]
    try:
        record["attempt"]["efficiency"] = efficiency.summarize_events(attempt_dir / "events.jsonl")
    except Exception as error:  # noqa: BLE001
        record["attempt"]["efficiency"] = None
        record["observationError"] = f"efficiency: {type(error).__name__}: {error}"
    blocker_path = worktree / ".harness" / "blocker.json"
    if blocker_path.exists() or blocker_path.is_symlink():
        if blocker_path.is_symlink() or not blocker_path.resolve().is_relative_to(
            worktree.resolve()
        ):
            blocker = {"reason": "Unsafe blocker file path; content not read."}
        else:
            text = blocker_path.read_text(encoding="utf-8")
            try:
                blocker = json.loads(text)
            except json.JSONDecodeError:
                blocker = {"reason": "Worker wrote invalid blocker JSON.", "details": text}
        record["blocker"] = blocker
    elif re.search(r"HARNESS-RESULT:\s*blocked\b", report):
        record["blocker"] = {"reason": record["workerReport"]}
    if "blocker" in record:
        record["status"] = "blocked: worker reported"
    _hold_permission_guard(record, attempt_dir)


def _hold_permission_guard(record: dict, attempt_dir: Path) -> bool:
    """Sticky evidence hold, including pre-guard receipts and superseded attempts.

    Neither retry_blocked, a new packet, a later pass nor an exact-command tool
    allowance is a user disposition. This port has no scoped clearance API.
    """
    attempt = record.get("attempt") or {}
    guards = [record.get("permissionGuard"), attempt.get("permissionGuard")]
    result_path = attempt_dir / "result.json"
    saved = {}
    if result_path.is_file():
        saved = read_record_json(result_path)
        guards.append(saved.get("permissionGuard"))
    observations = (record, attempt, saved)
    did_run_provider = any(item.get("didRunProvider") is True for item in observations)
    mocked = not did_run_provider and any(
        item.get("isMocked") is True or str(item.get("mode", "")).startswith("mock:")
        for item in observations
    )
    completed = (
        saved.get("status", attempt.get("status"))
        in {
            "exited",
            "timed-out",
            "observation-failed",
            *DENIAL_HELD_STATUSES,
        }
        or record.get("didRunIntegratedValidation") is True
    )
    audit_required = not mocked and (did_run_provider or completed)
    events_path = attempt_dir / "events.jsonl"
    if events_path.is_file() or audit_required:
        audit = efficiency.guard_disposition(events_path)
        guards.append(audit)
        record["permissionGuard"] = audit
        if record.get("attempt") is not None:
            record["attempt"]["permissionGuard"] = audit
    for previous in record.get("superseded", []):
        previous_dir = Path(previous["evidenceDir"]) / "attempt"
        if _hold_permission_guard(previous, previous_dir):
            guards.append(previous["permissionGuard"])
    held = [
        guard
        for guard in guards
        if guard
        and (
            guard.get("requiresUserDisposition")
            or (audit_required and guard.get("requiresAuditHold"))
        )
    ]
    if not held:
        return False
    guard = next(
        (g for g in held if g.get("disposition") == "guard-breach"),
        next((g for g in held if g.get("requiresUserDisposition")), held[0]),
    )
    status = STATUS_PERMISSION_HELD if guard.get("requiresUserDisposition") else STATUS_AUDIT_HELD
    record.update(status=status, permissionGuard=guard, reused=False)
    if record.get("attempt") is not None:
        record["attempt"]["permissionGuard"] = guard
    if record.get("candidate"):
        record["candidate"]["validated"] = False
    return True


def _update_efficiency(record: dict) -> None:
    attempts = []
    for bead in record["beads"].values():
        for candidate in [*bead.get("superseded", []), bead]:
            attempt = candidate.get("attempt") or {}
            if attempt.get("efficiency"):
                attempts.append(attempt["efficiency"])
            elif attempt.get("mode"):
                attempts.append(dict.fromkeys(efficiency.FIELDS))
    record["efficiency"] = efficiency.aggregate(attempts)


def _changed_paths(cwd: Path) -> list[str]:
    raw = _git_run(cwd, "status", "--porcelain", "-z", "--untracked-files=all", text=True).stdout
    paths = [entry[3:] for entry in raw.split("\0") if len(entry) > 3]
    return sorted(path for path in paths if _source_path(path))


def _run_provider(
    launch,
    cwd: Path,
    attempt_dir: Path,
    request: dict,
    timeout: int,
    requirement_requests: dict[str, dict],
    *,
    execution_job=None,
) -> dict:
    # Stream output durably instead of waiting for pipe EOF, which descendants
    # can keep open after the controller's process has exited.
    events_path, stderr_path = attempt_dir / "events.jsonl", attempt_dir / "stderr.txt"
    request["denialCapabilities"] = dict(DENIAL_CAPABILITIES)
    tool_dir = attempt_dir.resolve() / "tools"
    tool_dir.mkdir(exist_ok=True)
    effective_environment = {
        **os.environ,
        **launch.environment,
        "PYTHONDONTWRITEBYTECODE": "1",
        **dict.fromkeys(
            (
                "TEMP",
                "TMP",
                "TMPDIR",
                "XDG_CACHE_HOME",
                "npm_config_cache",
                "PLAYWRIGHT_MCP_OUTPUT_DIR",
                "PLAYWRIGHT_MCP_USER_DATA_DIR",
            ),
            str(tool_dir),
        ),
    }
    request["environmentIndicators"] = {
        name: effective_environment.get(name)
        for name in ("CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_CI")
    }
    for requirement, planned in requirement_requests.items():
        (attempt_dir / requirement).mkdir(exist_ok=True)
        planned["environmentIndicators"] = request["environmentIndicators"]
        planned["denialCapabilities"] = dict(DENIAL_CAPABILITIES)
    _write_record(attempt_dir / "request.json", request)

    attempt_provenance.verify_attempt(
        execution_job or request.get("executionJob"), request, attempt_dir, timeout=timeout
    )
    cancellation = failure = None
    cleanup = {}
    code = None
    publication_path = attempt_dir / attempt_provenance.FILENAME
    failed_request_publication = timed_out = False

    def publish(pid):
        nonlocal publication_path
        request.update(processId=pid, processObservedAt=_now())
        if request["command"] != list(launch.command) or request["launch"] != launch.to_payload():
            raise ValueError("actual owned command/launch differs from request")
        original = attempt_provenance.publish(
            attempt_dir, request, process_id=pid, process_observed_at=request["processObservedAt"]
        )
        request["processProvenanceDigest"] = original["digest"]
        publication_path = attempt_dir / "request.json"
        _write_record(publication_path, request)
        for requirement, planned in requirement_requests.items():
            planned.update(
                processId=pid,
                processObservedAt=request["processObservedAt"],
                processProvenanceDigest=original["digest"],
            )
            publication_path = attempt_dir / requirement / "request.json"
            _write_record(publication_path, planned)
        publication_path = None

    def stop_when():
        nonlocal cancellation
        if request.get("processId"):
            cancellation = attempt_control.consume_cancel(attempt_dir, request)
        return "cancelled" if cancellation and cancellation["status"] == "requested" else None

    try:
        with (
            events_path.open("wb") as output,
            stderr_path.open("wb") as errors,
            TemporaryFile() as prompt,
        ):
            if launch.stdin_text is not None:
                prompt.write(launch.stdin_text.encode("utf-8"))
                prompt.seek(0)
            completed = run_audit_process(
                launch.command,
                cwd=cwd,
                env=effective_environment,
                stdin=prompt if launch.stdin_text is not None else subprocess.DEVNULL,
                stdout=output,
                stderr=errors,
                capture_output=False,
                timeout=timeout,
                on_launch=publish,
                stop_when=stop_when,
            )
            cleanup = completed.audit_control
            code = completed.returncode
    except subprocess.TimeoutExpired as error:
        timed_out = True
        failure = dict(type=type(error).__name__, timeoutSeconds=error.timeout)
        cleanup = getattr(error, "audit_control", {})
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        cleanup = getattr(error, "audit_control", {})
        failure = dict(type=type(error).__name__, message=str(error))
        failed_request_publication = (
            publication_path is not None and request.get("processId") is not None
        )
        if failed_request_publication:
            failure.update(stage="post-spawn-publication", path=str(publication_path))
    verified = cleanup.get("quiescenceVerified") is True
    cleanup.update(
        status="VERIFIED" if verified else "UNVERIFIED",
        errors=[] if verified else ([failure] if failure else []),
        controllerProcessExited=verified,
        descendantsStopped=verified,
    )
    if request.get("processId"):
        cancellation = attempt_control.consume_cancel(
            attempt_dir, request, control=cleanup, exited=True
        )
    streams = {}
    output_errors = []
    for name, path in (("stdout", events_path), ("stderr", stderr_path)):
        try:
            streams[name] = path.read_bytes()
        except OSError as error:
            output_errors.append(
                {"stream": name, "type": type(error).__name__, "message": str(error)}
            )
    stdout, stderr = streams.get("stdout"), streams.get("stderr")
    output_error = {"errors": output_errors} if output_errors else None
    change_error = None
    try:
        changed = _changed_paths(cwd)
    except (OSError, subprocess.SubprocessError) as error:
        changed = None
        change_error = {"type": type(error).__name__, "message": str(error)}
    result = {
        **request,
        "status": "cancelled"
        if cancellation and cancellation["status"] == "cancelled"
        else "timed-out"
        if timed_out
        else "observation-failed"
        if failure or output_error or change_error
        else "exited",
        "exitCode": code,
        "finishedAt": _now(),
        "eventsSha256": hashlib.sha256(stdout).hexdigest() if stdout is not None else None,
        "changedPaths": changed,
        "failure": failure,
        "cleanup": cleanup,
        "cancellation": cancellation,
        "outputCollectionError": output_error,
        "changeObservationError": change_error,
        "outputComplete": failure is None
        and output_error is None
        and not (cancellation and cancellation["status"] == "cancelled"),
        "didRunProvider": True
        if request.get("processId")
        else False
        if cleanup.get("payloadReleased") is False
        else None,
        "isMocked": False,
        "didRunIntegratedValidation": False,
        "isLiveProductProof": False,
    }
    return _save_attempt_result(
        attempt_dir,
        request,
        requirement_requests,
        result,
        stdout,
        stderr,
        update_requests=not failed_request_publication,
    )


def _save_attempt_result(
    attempt_dir: Path,
    request: dict,
    requirement_requests: dict[str, dict],
    result: dict,
    stdout: bytes | None,
    stderr: bytes | None,
    *,
    update_requests: bool = True,
) -> dict:
    # CLI stdout is buffered telemetry, not an interceptable tool callback. Hold
    # at the first boundary we own; retain the exit code and raw events/results.
    holder = {"attempt": result}
    if _hold_permission_guard(holder, attempt_dir):
        result["statusBeforePermissionHold"] = result["status"]
        result["permissionGuard"] = holder["permissionGuard"]
        # Cancellation is terminal control evidence, never clean completion.
        # The bead/resume consumer still enforces the separate audit hold.
        if result["status"] != "cancelled":
            result["status"] = holder["status"]
    # Publish the result first: a crash must leave either a recoverable result
    # or a launching request, never a terminal request without its observation.
    _write_record(attempt_dir / "result.json", result)
    if update_requests:
        request.update(status=result["status"], finishedAt=result["finishedAt"])
        _write_record(attempt_dir / "request.json", request)
    # One process addressed all planned requirements. Bind each observation to
    # its pre-launch request, preserving the shared process and raw output.
    for requirement, planned in requirement_requests.items():
        directory = attempt_dir / requirement
        directory.mkdir(exist_ok=True)
        if update_requests:
            planned.update(status=result["status"], finishedAt=result["finishedAt"])
            _write_record(directory / "request.json", planned)
        bound_result = {**result, "requirementId": planned["requirementId"]}
        _write_record(directory / "result.json", bound_result)
        if stdout is not None:
            (directory / "events.jsonl").write_bytes(stdout)
        if stderr is not None:
            (directory / "stderr.txt").write_bytes(stderr)
    return result


def _completed_attempt_identity_error(record: dict, request: dict, result: dict) -> str | None:
    """Check original local attribution, not genuine live runtime admission."""
    fields = (
        "runId",
        "bead",
        "requirementId",
        "baseCommit",
        "adapterCommit",
        "executableSha256",
        "cwd",
        "launch",
        "command",
        "mode",
        "prompt",
        "planRevision",
        "requirementRefs",
        "startedAt",
    )
    fields += tuple(
        key
        for key in ("provider", "model", "effort", "ownedFiles")
        if request.get("mode") == "provider-normal" or key in request or key in result
    )
    if "adapterProvenance" in request or "adapterProvenance" in result:
        fields += ("adapterProvenance",)
    for field in fields:
        if field not in request or field not in result or request[field] != result[field]:
            return f"request/result {field}"
    if request.get("specRefs") != result.get("specRefs"):
        return "request/result specRefs"
    for field in ("runId", "bead", "prompt", "planRevision", "requirementRefs", "specRefs"):
        if request.get(field) != record.get(field):
            return f"request/run {field}"
    attempt = record.get("attempt") or {}
    for field in fields:
        if field in attempt and request[field] != attempt[field]:
            return f"request/attempt {field}"
    for field in ("cwd", "baseCommit"):
        if field not in attempt:
            return f"attempt missing {field}"
    return None


def read_completed_execution_evidence(
    workspace, worker_job_id, bead_id, *, expected_repo, expected_publication_revision
):
    """Read selected terminal originals; never integrate, retry or bless a receipt path."""
    from codex_harness.core import planner_jobs

    status = planner_jobs.worker_job_status(workspace, worker_job_id)
    if (
        status["publicationRevision"] != expected_publication_revision
        or status["status"] != "completed"
        or "run" not in status
        or status["terminal"].get("runId") != status["run"]["runId"]
        or Path(status["run"]["repo"]).resolve() != Path(expected_repo).resolve()
    ):
        raise ValueError("execution-current-terminal-job-required")
    directory = planner_jobs._directory(workspace, worker_job_id)
    files = {}

    def original(path, root):
        path, root = Path(path), Path(root).resolve()
        if not path.resolve().is_relative_to(root):
            raise ValueError("execution-original-outside-selected-root")
        for part in (path, *path.parents):
            if part.is_symlink() or getattr(part.stat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("execution-original-redirected")
            if part == root:
                break
        raw = path.read_bytes()
        files[str(path.resolve())] = hashlib.sha256(raw).hexdigest()
        return raw.decode("utf-8")

    def record(path, root):
        return json.loads(original(path, root))

    request_job = record(directory / "request.json", directory)
    terminal = record(directory / "terminal.json", directory)
    started = record(directory / "started.json", directory)
    if any(
        item.get("requestDigest") != request_job["requestDigest"] for item in (terminal, started)
    ):
        raise ValueError("execution-job-originals-changed")
    manifest = record(directory / "plan/sample.json", directory)
    if planner_jobs._digest(manifest) != request_job["manifestDigest"]:
        raise ValueError("execution-job-manifest-changed")
    plan = graph.load_plan(directory / "plan")
    run_path = Path(status["run"]["recordPath"])
    run = record(run_path, directory)
    if bead_id not in manifest["beads"] or bead_id not in run["beads"]:
        raise ValueError("execution-bead-not-in-canonical-job")
    bead, selected = manifest["beads"][bead_id], run["beads"][bead_id]
    owned = bead.get("ownedFiles")
    if (
        not isinstance(owned, list)
        or not owned
        or len(set(owned)) != len(owned)
        or any(
            not isinstance(name, str)
            or not name
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            for name in owned
        )
        or selected.get("bead") != bead_id
        or selected.get("runId") != run["runId"]
        or selected.get("planRevision") != expected_publication_revision
        or selected.get("isMocked") is not False
        or not _did_execute_real_provider(selected)
        or selected.get("didRunProvider") is not True
        or selected.get("didRunIntegratedValidation") is not True
        or selected.get("status") != "requirement-validated"
        or selected.get("provider") != "codex"
        or selected.get("observationError")
    ):
        raise ValueError("execution-selected-author-or-ownership-unsupported")
    evidence = Path(selected["evidenceDir"])
    attempt = evidence / "attempt"
    request = record(attempt / "request.json", run_path.parent)
    result = record(attempt / "result.json", run_path.parent)
    original(attempt / attempt_provenance.FILENAME, run_path.parent)
    if (
        _completed_attempt_identity_error(selected, request, result)
        or request.get("mode") != "provider-normal"
        or result.get("status") != "exited"
        or type(result.get("exitCode")) is not int
        or result.get("exitCode") != 0
        or result.get("isMocked") is not False
        or result.get("didRunProvider") is not True
        or result.get("outputComplete") is not True
        or result.get("outputCollectionError")
        or result.get("changeObservationError")
        or result.get("failure")
        or result.get("cancellation")
        or attempt_provenance.read_and_compare(
            attempt, request, result, record=selected, expected_owned_files=owned
        )["state"]
        != "consistent"
    ):
        raise ValueError("execution-author-originals-unverified")
    executable = Path(request["command"][0])
    if (
        not executable.is_file()
        or hashlib.sha256(executable.read_bytes()).hexdigest().upper()
        != request["executableSha256"]
    ):
        raise ValueError("execution-author-executable-unverified")
    stdout = original(attempt / "events.jsonl", run_path.parent)
    stderr = original(attempt / "stderr.txt", run_path.parent)
    if hashlib.sha256(stdout.encode("utf-8")).hexdigest() != result.get("eventsSha256"):
        raise ValueError("execution-author-stdout-original-changed")
    audit = efficiency.guard_disposition(attempt / "events.jsonl")
    if audit.get("requiresUserDisposition") or audit.get("requiresAuditHold"):
        raise ValueError("execution-author-existing-permission-or-audit-hold")
    integrated = record(evidence / "integrated.json", run_path.parent)
    binding = record(evidence / "integration-state.json", run_path.parent)
    repo = Path(expected_repo).resolve()
    commit = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    original_commit = integrated.get("integrationCommit")
    if not isinstance(original_commit, str) or not original_commit:
        raise ValueError("execution-current-integrated-witness-required")
    original_tree = _git(repo, "rev-parse", f"{original_commit}^{{tree}}")
    current_inputs = _claim_inputs(repo, plan, bead_id, commit)
    if _changed_claim_inputs(integrated.get("claim"), current_inputs):
        # This slice retains the original observed witness only when its full
        # assertion still applies. Revalidation is not a substitute author.
        raise ValueError("execution-selected-proof-inputs-changed")
    candidate = selected.get("candidate") or {}
    worker = Path(request["cwd"]).resolve()
    if (
        _git(repo, "status", "--porcelain")
        or integrated != selected["integrated"]
        or integrated.get("recoveryBinding") != binding
        or binding.get("runId") != run["runId"]
        or binding.get("bead") != bead_id
        or binding.get("attemptDirectory") != str(attempt.resolve())
        or binding.get("ownedFiles") != owned
        or binding.get("planRevision") != expected_publication_revision
        or binding.get("attemptBase") != request["baseCommit"]
        or binding.get("workerWorktree") != str(Path(request["cwd"]).resolve())
        or binding.get("packetSha256") != hashlib.sha256(request["prompt"].encode()).hexdigest()
        or binding.get("receiptPath") != str((evidence / "integrated.json").resolve())
        or binding.get("candidateCommit") != original_commit
        or candidate.get("commit") != original_commit
        or _git(worker, "rev-parse", "HEAD") != binding.get("workerCommit")
        or _git(worker, "rev-parse", "HEAD^{tree}") != binding.get("workerTree")
        or _git(worker, "status", "--porcelain")
        or _git(repo, "rev-parse", binding["candidateRef"]) != original_commit
        or _git(repo, "rev-list", "--parents", "-n", "1", original_commit).split()[1:]
        != [binding.get("baseIntegration"), binding.get("workerCommit")]
        or binding.get("candidateTree") != original_tree
        or candidate.get("tree") != original_tree
        or candidate.get("validated") is not True
        or integrated.get("requirementRefs") != selected["requirementRefs"]
        or integrated.get("specRefs") != selected.get("specRefs")
        or any(
            integrated.get("claim", {}).get(key) != value
            for key, value in _claim_inputs(repo, plan, bead_id, original_commit).items()
        )
        or integrated.get("claim", {}).get("candidateCommit") != original_commit
        or integrated.get("claim", {}).get("candidateTree") != candidate["tree"]
        or integrated.get("claim", {}).get("passed") is not True
        or integrated.get("validation", {}).get("passed") is not True
        or integrated.get("validation", {}).get("inputIntegrity", {}).get("passed") is not True
        or not _validation_inputs(repo)[1]
    ):
        raise ValueError("execution-current-integrated-witness-required")
    claims = run.get("claims", [])
    latest = next((item for item in reversed(claims) if item.get("bead") == bead_id), None)
    disposition = next(
        (item for item in run.get("requirementValidation", []) if item.get("bead") == bead_id), {}
    )
    expected = _requirement_validation(
        bead_id,
        plan.beads[bead_id].requirements,
        plan.beads[bead_id].proof_module,
        integrated,
        expected_publication_revision,
        selected["requirementRefs"],
        selected.get("specRefs"),
    )
    if latest != integrated["claim"] or any(
        disposition.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("execution-current-selected-proof-required")
    ancestry = _git_run(repo, "merge-base", "--is-ancestor", original_commit, commit, ok=(0, 1))
    if ancestry.returncode:
        raise ValueError("execution-original-candidate-not-ancestor")
    if commit != original_commit:
        # Bind the later HEAD to an actual terminal-job integration, not any
        # manual descendant whose selected-bead files happen to be unchanged.
        head_id, head = next(
            (
                (key, value)
                for key, value in run["beads"].items()
                if value.get("candidate", {}).get("commit") == commit
            ),
            (None, None),
        )
        if head_id not in plan.beads or head.get("status") != "requirement-validated":
            raise ValueError("execution-current-head-proof-required")
        head_directory = Path(head["evidenceDir"])
        head_integrated = record(head_directory / "integrated.json", run_path.parent)
        head_binding = record(head_directory / "integration-state.json", run_path.parent)
        head_claim = next((item for item in reversed(claims) if item.get("bead") == head_id), None)
        head_disposition = next(
            (item for item in run["requirementValidation"] if item.get("bead") == head_id), {}
        )
        head_expected = _requirement_validation(
            head_id,
            plan.beads[head_id].requirements,
            plan.beads[head_id].proof_module,
            head_integrated,
            expected_publication_revision,
            head["requirementRefs"],
            head.get("specRefs"),
        )
        if (
            head_integrated != head["integrated"]
            or head_integrated.get("recoveryBinding") != head_binding
            or head_integrated.get("integrationCommit") != commit
            or head_integrated.get("candidate") != head.get("candidate")
            or head.get("candidate", {}).get("validated") is not True
            or head.get("candidate", {}).get("tree") != tree
            or head_binding.get("candidateCommit") != commit
            or head_binding.get("candidateTree") != tree
            or head_binding.get("runId") != run["runId"]
            or head_binding.get("bead") != head_id
            or head_binding.get("planRevision") != expected_publication_revision
            or _git(repo, "rev-parse", head_binding["candidateRef"]) != commit
            or _git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
            != [head_binding.get("baseIntegration"), head_binding.get("workerCommit")]
            or head_integrated.get("claim") != head_claim
            or _changed_claim_inputs(head_claim, _claim_inputs(repo, plan, head_id, commit))
            or head_claim.get("candidateCommit") != commit
            or head_claim.get("candidateTree") != tree
            or head_claim.get("passed") is not True
            or head_integrated.get("validation", {}).get("passed") is not True
            or head_integrated.get("validation", {}).get("inputIntegrity", {}).get("passed")
            is not True
            or any(head_disposition.get(key) != value for key, value in head_expected.items())
        ):
            raise ValueError("execution-current-head-proof-required")
    for name in owned:
        original(repo / name, repo)
    for name, path in requirement_file_sources(repo, manifest["requirementFiles"]).items():
        original(path, repo)
        if path.read_bytes() != (directory / "plan/template" / name).read_bytes():
            raise ValueError("execution-protected-source-changed")
        original(directory / "plan/template" / name, directory)
    return dict(
        jobId=worker_job_id,
        beadId=bead_id,
        runId=run["runId"],
        repo=str(repo),
        publicationRevision=expected_publication_revision,
        candidateCommit=commit,
        candidateTree=tree,
        ownedFiles=owned,
        request=request,
        result=result,
        stdout=stdout,
        stderr=stderr,
        integrated=integrated,
        files=files,
    )


def _check_attempt_provenance(
    record: dict, request: dict | None = None, result: dict | None = None, owned_files=None
) -> bool:
    directory = Path(record["evidenceDir"]) / "attempt"
    try:
        request = read_record_json(directory / "request.json") if request is None else request
        result = read_record_json(directory / "result.json") if result is None else result
    except (OSError, ValueError) as error:
        observation = {
            "state": "unavailable",
            "code": "attempt-provenance-unavailable",
            "path": str(directory / attempt_provenance.FILENAME),
            "detail": f"{type(error).__name__}: {error}",
        }
    else:
        observation = attempt_provenance.read_and_compare(
            directory,
            request,
            result,
            record=record,
            expected_owned_files=owned_files,
        )
    record["localProcessProvenance"] = observation
    if observation["state"] in {"consistent", "source-only"}:
        return True
    record.update(status=STATUS_PROVENANCE_HELD, reused=False)
    record["candidate"]["validated"] = False
    return False


def _attempt_bead(
    record: dict,
    bead: dict,
    *,
    plan: graph.Plan,
    evidence: Path,
    fake_worker: Callable[[Path], None] | None,
    mock_provider: str | None,
    timeout: int,
    execution_job=None,
) -> dict:
    worktree = Path(record["attempt"]["cwd"])
    attempt = record["attempt"]
    attempt_dir = evidence / "attempt"
    if _hold_permission_guard(record, attempt_dir):
        return record
    launch = None
    if fake_worker is not None:
        attempt["mode"] = "mock:test-worker"
    elif mock_provider == "reference":
        attempt["mode"] = "mock:reference"
    elif mock_provider == "noop":
        attempt["mode"] = "mock:noop"
    else:
        from codex_harness.core.roundtable import _executable

        executable = record["executable"] or _executable(record["provider"], None)
        if executable:
            executable = str(Path(executable).resolve())
        launch = get_adapter(record["provider"]).provider_normal_command(
            prompt=record["prompt"],
            prompt_via_stdin=True,
            model=record["model"],
            reasoning_effort=record["effort"],
            executable=executable,
            allowed_tools=_validation_tool_rules(record["provider"], bead, plan),
        )
        attempt.update(mode="provider-normal", launch=launch.to_payload(), dir=str(attempt_dir))
    attempt_dir.mkdir()
    request = {
        "runId": record["runId"],
        "bead": record["bead"],
        "requirementId": f"{plan.name}-{record['bead']}:{'-'.join(bead['requirements'])}",
        "baseCommit": attempt["baseCommit"],
        **adapter_provenance(),
        "executableSha256": _file_sha256(launch.command[0]) if launch else None,
        "cwd": str(worktree),
        "launch": launch.to_payload() if launch else {"command": [attempt["mode"]]},
        "command": list(launch.command) if launch else [attempt["mode"]],
        "mode": attempt["mode"],
        "provider": record["provider"],
        "model": record["model"],
        "effort": record["effort"],
        "ownedFiles": attempt_provenance.normalized_owned(bead["ownedFiles"]),
        "prompt": record["prompt"],
        "planRevision": record["planRevision"],
        "requirementRefs": record["requirementRefs"],
        "specRefs": record.get("specRefs"),
        **_spec_binding(record),
        "startedAt": _now(),
        "status": "launching",
    }
    _write_record(attempt_dir / "request.json", request)
    requirement_requests = {
        requirement: {
            **request,
            "requirementId": requirement,
            "attemptGroup": request["requirementId"],
        }
        for requirement in bead["requirements"]
    }
    for requirement, planned in requirement_requests.items():
        directory = attempt_dir / requirement
        directory.mkdir()
        _write_record(directory / "request.json", planned)
    try:
        if launch is not None:
            result = _run_provider(
                launch,
                worktree,
                attempt_dir,
                request,
                timeout,
                requirement_requests,
                execution_job=execution_job,
            )
        else:
            request["didLaunchMockAttempt"] = True
            _write_record(attempt_dir / "request.json", request)
            if fake_worker is not None:
                fake_worker(worktree)
            elif mock_provider == "reference":
                for owned in bead["ownedFiles"]:
                    destination = worktree / owned
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(plan.root / "reference" / owned, destination)
            result = _save_attempt_result(
                attempt_dir,
                request,
                requirement_requests,
                {
                    **request,
                    "status": "exited",
                    "exitCode": 0,
                    "finishedAt": _now(),
                    "didRunProvider": False,
                    "isMocked": True,
                },
                None,
                None,
            )
    except (OSError, subprocess.SubprocessError) as error:
        attempt.update(
            status="evidence-finalization-failed",
            failure={"type": type(error).__name__, "message": str(error)},
        )
        record["didRunProvider"] = bool(request.get("processId"))
        record["status"] = "provider evidence-finalization-failed"
        _observe_attempt(record, worktree, attempt_dir)
        return record
    attempt.update(result)
    record["didRunProvider"] = result["didRunProvider"]
    # Check the original again after execution; mutable result labels alone
    # cannot restore a missing or changed controller observation.
    if result.get("status") == "exited" and not _check_attempt_provenance(
        record, request, result, bead["ownedFiles"]
    ):
        return record
    _observe_attempt(record, worktree, attempt_dir)
    if record["status"] in DENIAL_HELD_STATUSES:
        return record
    if record["status"] != "blocked: worker reported" and (
        attempt["status"] != "exited" or attempt["exitCode"] != 0
    ):
        record["status"] = (
            f"provider exited nonzero ({attempt['exitCode']})"
            if attempt["status"] == "exited"
            else f"provider {attempt['status']}"
        )
        return record

    # Compare against the launch commit as well as the index: worker commits
    # must not hide changes from the ownership/requirement guard.
    changed = sorted(
        path
        for path in (
            set(_changed_paths(worktree))
            | set(_git(worktree, "diff", "--name-only", attempt["baseCommit"]).splitlines())
        )
        if _source_path(path)
    )
    owned = {str(PurePosixPath(name.replace("\\", "/"))) for name in bead["ownedFiles"]}
    # The canonical graph rejects ownership of protected inputs; one guard covers
    # both protected writes and any other file outside the bead's ownership.
    outside = [path for path in changed if path not in owned]
    attempt.update(changedPaths=changed, finishedAt=_now())
    record["guard"] = {
        "changesOutsideOwnedFiles": outside,
        "passed": not outside,
    }
    if record["status"] == "blocked: worker reported":
        return record
    # Empty attempts still need proof on integration HEAD; they create no commit.
    if changed and not record["guard"]["passed"]:
        record["status"] = "rejected: changes outside owned files"
    return record
