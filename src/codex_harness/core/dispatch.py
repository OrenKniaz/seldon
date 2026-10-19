"""Thin-core plan dispatch with parallel attempts and serial integrated proof."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from codex_harness.core import attempt_control, attempt_provenance, efficiency, graph, worker_packet
from codex_harness.core.mail import Mailbox
from codex_harness.core.memory_promotion import (
    PromotionEvidence,
    confirm_lesson,
    memory_prompt_for,
    promote_lesson,
)
from codex_harness.core.package_provenance import adapter_provenance
from codex_harness.core.provider_launch import get_adapter
from codex_harness.core.record_io import read_record_json, write_record_json
from codex_harness.core.runners import resolve_runner, validate_proof
from codex_harness.core.spine import (
    compile_manifest,
    load_spine,
    requirement_file_sources,
    requirement_files_revision,
)

STATUS_VALIDATED = "plan-validated"
STATUS_VALIDATED_NO_INTEGRATION = "plan-validated (no integration proof declared)"
STATUS_MOCK_ONLY = "source-test-only (mocked provider)"
STATUS_PERMISSION_HELD = "held: permission guard"
STATUS_AUDIT_HELD = "held: denial audit inconclusive"
STATUS_PROVENANCE_HELD = "held: local process provenance"
DENIAL_HELD_STATUSES = {STATUS_PERMISSION_HELD, STATUS_AUDIT_HELD}
DENIAL_CAPABILITIES = {
    "port": "provider-normal-headless-cli",
    "supportsSynchronousDenialInterception": False,
    "supportsPostHocDenialHold": True,
    "canPreventProviderInternalRetry": False,
    "structuredDenialFormats": [
        "claude: system.permission_denied, tool_result_meta.user-rejected, "
        "result.permission_denials",
        "codex: denied command_execution/file_change/mcp_tool_call item status",
    ],
    "otherTelemetry": "unknown; absence of observed denial is not authorization",
}
HARNESS_ROOT = Path(__file__).resolve().parents[3]


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
            *DENIAL_HELD_STATUSES,
        } and not reason.startswith(("rejected: ", "blocked: ")):
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


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(cwd: Path, message: str) -> str:
    identity = ["-c", "user.name=Harness Sample", "-c", "user.email=sample@harness.invalid"]
    _git(cwd, *identity, "commit", "-q", "--allow-empty", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


def _requirement_ids(repo: Path, names: list[str]) -> dict[str, str]:
    """Git object ids of the requirement files at HEAD (line-ending neutral)."""

    return {name: _git(repo, "rev-parse", f"HEAD:{name}") for name in names}


def _verify_requirement_files(
    plan: graph.Plan, manifest: dict, root: Path, *, source_fixture: bool = False
) -> None:
    expected = plan.requirement_files_revision
    if expected is None:
        if source_fixture:
            if "planRevision" not in manifest:
                return  # Legacy static sample inputs, explicitly executed as mocks.
            source = plan.root / "spine.json"
            if (
                source.is_file()
                and compile_manifest(load_spine(source))["planRevision"] == plan.plan_revision
            ):
                return  # Exact source-only compiler identity, never a composite publication.
        raise ValueError("published requirement files revision is missing")
    if requirement_files_revision(root, list(plan.requirement_files)) != expected:
        raise ValueError("protected requirement files revision mismatch")


def _validate(cwd: Path, manifest: dict, module: str) -> dict[str, object]:
    return validate_proof(cwd, manifest, module)


def _content_identity(repo: Path, commit: str, names) -> str:
    """SHA256 of sorted Git paths and LF-normalized blobs, never working files.

    Each path is UTF-8 followed by NUL, then an unsigned 8-byte big-endian
    length and the normalized blob. Missing selections use the length 2**64-1.
    Directories expand to tracked files; missing paths remain explicit inputs.
    """
    names = {str(PurePosixPath(name.replace("\\", "/"))) for name in names}
    if not names:
        return hashlib.sha256().hexdigest()
    listing = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-rz", "--name-only", commit, "--", *sorted(names)],
        capture_output=True,
        check=True,
    )
    tracked = {path.decode("utf-8") for path in listing.stdout.split(b"\0") if path}
    paths = tracked | {
        name
        for name in names
        if not any(path == name or path.startswith(name + "/") for path in tracked)
    }
    # Read all blobs in one subprocess; Git's batch protocol preserves exact
    # bytes (including trailing newlines), unlike the text-oriented _git helper.
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch", "-z"],
        input="".join(f"{commit}:{path}\0" for path in sorted(tracked)).encode("utf-8"),
        capture_output=True,
        check=True,
    )
    blobs = {}
    cursor = 0
    for path in sorted(tracked):
        end = result.stdout.index(b"\n", cursor)
        _, kind, size = result.stdout[cursor:end].split()
        if kind != b"blob":
            raise ValueError(f"claim input is not a blob: {commit}:{path}")
        cursor = end + 1
        blobs[path] = result.stdout[cursor : cursor + int(size)].replace(b"\r\n", b"\n")
        cursor += int(size) + 1
    digest = hashlib.sha256()
    for path in sorted(paths):
        content = blobs.get(path, b"")
        size = len(content) if path in blobs else 2**64 - 1
        digest.update(path.encode("utf-8") + b"\0" + size.to_bytes(8, "big") + content)
    return digest.hexdigest()


def _claim_inputs(repo: Path, plan: graph.Plan, bead_id: str, commit: str) -> dict:
    """Bind the whole manifest test directory, VISION/PLAN and requirementFiles.

    Bead proofs bind transitive prerequisites by default. proofDependsOn can
    explicitly select exercised prerequisites (and their ancestors), or [] for
    fully stubbed unit proofs. Integration always binds every bead.
    """

    def ancestors(ids):
        result = set(ids)
        for dependency in ids:
            result.update(ancestors(plan.beads[dependency].depends_on))
        return result

    if bead_id == "integration":
        dependencies = set(plan.beads)
        owned = [name for bead in plan.beads.values() for name in bead.owned_files]
        module = plan.integration_proof_module
        requirements = plan.integration_requirements
    else:
        bead = plan.beads[bead_id]
        dependencies = ancestors(
            bead.depends_on if bead.proof_depends_on is None else bead.proof_depends_on
        )
        owned, module, requirements = bead.owned_files, bead.proof_module, bead.requirements
    runner = resolve_runner(plan)
    verification_identity = _content_identity(
        repo, commit, runner.protected_paths(plan.requirement_files)
    )
    if plan.verification is not None:
        verification_identity = hashlib.sha256(
            (verification_identity + json.dumps(dict(plan.verification), sort_keys=True)).encode()
        ).hexdigest()
    return {
        "bead": bead_id,
        "requirementIds": list(requirements),
        "proofModule": module,
        "verificationIdentity": verification_identity,
        "sourceIdentity": _content_identity(repo, commit, owned),
        "dependencyIdentities": {
            dependency: _content_identity(repo, commit, plan.beads[dependency].owned_files)
            for dependency in sorted(dependencies)
        },
    }


def _claim(repo: Path, plan: graph.Plan, bead_id: str, commit: str, passed: bool) -> dict:
    claim = {
        **_claim_inputs(repo, plan, bead_id, commit),
        "candidateCommit": commit,
        "candidateTree": _git(repo, "rev-parse", f"{commit}^{{tree}}"),
        "passed": passed,
        "at": _now(),
    }
    claim["claimId"] = (
        f"{bead_id}:"
        + hashlib.sha256(
            json.dumps(claim, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    return claim


def _changed_claim_inputs(old: dict | None, inputs: dict) -> dict:
    # Module/requirement selection is part of the assertion even when all
    # verification files are unchanged. Failed or absent evidence is never reused.
    changed = {
        key: {"previous": old.get(key) if old else None, "current": inputs[key]}
        for key in (
            "verificationIdentity",
            "sourceIdentity",
            "dependencyIdentities",
            "proofModule",
            "requirementIds",
        )
        if old is None or old.get(key) != inputs[key]
    }
    if old is not None and not old.get("passed"):
        changed["passed"] = {"previous": old.get("passed"), "current": "pass required"}
    return changed


def _refs_payload(refs) -> list[dict[str, str]]:
    return [{"id": requirement, "revision": revision} for requirement, revision in refs]


def _bead_refs(bead: graph.Bead) -> dict:
    refs = {"requirementRefs": _refs_payload(bead.requirement_refs)}
    if bead.spec_refs is not None:
        refs["specRefs"] = [
            {"id": node_id, "kind": kind, "revision": revision}
            for node_id, kind, revision in bead.spec_refs
        ]
    return refs


def _identity_refs(record: dict) -> list[dict]:
    return record["specRefs"] if "specRefs" in record else record.get("requirementRefs", [])


def _spec_binding(record: dict) -> dict:
    return {"specRefs": record["specRefs"]} if "specRefs" in record else {}


def _update_requirement_files(repo: Path, plan: graph.Plan, from_revision: str) -> dict:
    """Compare template requirement blobs with HEAD, then commit only their changes."""
    template = plan.root / "template"
    names = [str(PurePosixPath(name.replace("\\", "/"))) for name in plan.requirement_files]
    sources = requirement_file_sources(template, names)
    tracked = (
        set(_git(repo, "ls-tree", "-rz", "--name-only", "HEAD", "--", *names).split("\0")) - {""}
        if names
        else set()
    )
    paths = []
    for name in sorted(tracked | sources.keys()):
        destination = repo / name
        if destination.is_symlink() or not destination.resolve().is_relative_to(repo.resolve()):
            raise ValueError(f"unsafe requirement destination: {name}")
        source = sources.get(name)
        if (
            source is not None
            and name in tracked
            and (
                _git(repo, "hash-object", f"--path={name}", str(source))
                == _git(repo, "rev-parse", f"HEAD:{name}")
            )
        ):
            continue
        paths.append(name)
    for name in paths:
        destination = repo / name
        if name in sources:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sources[name], destination)
        else:
            destination.unlink()
    commit = None
    if paths:
        _git(repo, "add", "-A", "--", *paths)
        commit = _commit(
            repo,
            f"Plan revision {plan.plan_revision[:12]}: update requirement files\n\n"
            f"Harness-Plan-Revision: {plan.plan_revision}\nHarness-Plan-Update: true",
        )
    return {
        "fromRevision": from_revision,
        "toRevision": plan.plan_revision,
        "commit": commit,
        "paths": paths,
    }


def _refs_text(refs: list[dict]) -> str:
    return ", ".join(f"{ref['id']}@{ref['revision'][:8]}" for ref in refs) or "none"


def _bead_prompt(
    bead: dict, plan_revision: str | None = None, *, plan: graph.Plan | None = None
) -> str:
    directory = graph.test_dir(plan if plan is not None else {"validation": []})
    command = f"python -B -m unittest discover -s {shlex.quote(directory)} -p {bead['proofModule']}"
    if plan is not None and plan.verification is not None:
        command = json.dumps(resolve_runner(plan).argv_for(bead["proofModule"]))
    prompt = (
        f"Implement {bead['title']} as documented in PLAN.md and VISION.md in this "
        f"worktree. Change ONLY these files: {', '.join(bead['ownedFiles'])}. "
        f"Do not change VISION.md, PLAN.md or anything under {directory}/. Do not install "
        "anything, change settings, commit or push. Check your work with: "
        f"{command}. "
        "Report what you changed and the checks you actually ran."
    )
    if plan_revision is not None:
        prompt += (
            f"\nPlan revision: {plan_revision[:12]}. "
            f"Requirements: {_refs_text(bead.get('requirementRefs', []))}."
        )
    if "specRefs" in bead:
        prompt += f"\nSpec refs: {json.dumps(bead['specRefs'], sort_keys=True)}."
    return prompt


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
    raw = subprocess.run(
        ["git", "-C", str(cwd), "status", "--porcelain", "-z", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
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
    effective_environment = {**os.environ, **launch.environment}
    request["environmentIndicators"] = {
        name: effective_environment.get(name)
        for name in ("CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_CI")
    }
    for requirement, planned in requirement_requests.items():
        (attempt_dir / requirement).mkdir(exist_ok=True)
        planned["environmentIndicators"] = request["environmentIndicators"]
        planned["denialCapabilities"] = dict(DENIAL_CAPABILITIES)
    _write_record(attempt_dir / "request.json", request)
    from codex_harness.core.provider_call_budget import claim_call

    claim_call(execution_job or request.get("executionJob"), request, attempt_dir, timeout=timeout)
    try:
        with events_path.open("wb") as events_file, stderr_path.open("wb") as stderr_file:
            process = subprocess.Popen(
                launch.command,
                cwd=cwd,
                env=effective_environment,
                stdin=subprocess.DEVNULL,
                stdout=events_file,
                stderr=stderr_file,
            )
    except OSError as error:
        result = {
            **request,
            "processId": None,
            "status": "launch-failed",
            "exitCode": None,
            "finishedAt": _now(),
            "eventsSha256": None,
            "changedPaths": None,
            "failure": {"type": type(error).__name__, "message": str(error)},
            "cleanup": {"status": "NOT_STARTED", "errors": []},
            "outputCollectionError": None,
            "changeObservationError": None,
            "outputComplete": False,
            "didRunProvider": False,
            "isMocked": False,
            "didRunIntegratedValidation": False,
            "isLiveProductProof": False,
        }
        return _save_attempt_result(attempt_dir, request, requirement_requests, result, None, None)
    timed_out = False
    cancellation = None
    failure = None
    cleanup = {"status": "NOT_REQUESTED", "errors": []}
    publication_path = attempt_dir / "request.json"
    failed_request_publication = False
    request["processId"] = process.pid
    request["processObservedAt"] = _now()
    try:
        # Every post-spawn operation remains inside ownership of the live child.
        publication_path = attempt_dir / attempt_provenance.FILENAME
        if request["command"] != list(launch.command) or request["launch"] != launch.to_payload():
            raise ValueError("actual Popen command/launch differs from request")
        original = attempt_provenance.publish(
            attempt_dir,
            request,
            process_id=process.pid,
            process_observed_at=request["processObservedAt"],
        )
        request["processProvenanceDigest"] = original["digest"]
        publication_path = attempt_dir / "request.json"
        _write_record(publication_path, request)
        for requirement, planned in requirement_requests.items():
            directory = attempt_dir / requirement
            planned["processId"] = process.pid
            planned["processObservedAt"] = request["processObservedAt"]
            planned["processProvenanceDigest"] = original["digest"]
            publication_path = directory / "request.json"
            _write_record(publication_path, planned)
        publication_path = None
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            cancellation = attempt_control.consume_cancel(
                process, attempt_dir, request, cleanup=cleanup
            )
            if cancellation and "cleanup" in cancellation:
                cleanup = cancellation["cleanup"]
            if cancellation and cancellation["status"] == "cancelled":
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(launch.command, timeout)
            try:
                process.wait(timeout=min(0.2, remaining))
            except subprocess.TimeoutExpired:
                pass
        if cancellation is None:
            cancellation = attempt_control.consume_cancel(
                process, attempt_dir, request, cleanup=cleanup
            )
    except subprocess.TimeoutExpired as error:
        timed_out = True
        failure = {"type": type(error).__name__, "timeoutSeconds": error.timeout}
        cleanup["status"] = "UNVERIFIED"
        try:
            if sys.platform == "win32":
                killed = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    timeout=15,
                )
                cleanup["commandExitCode"] = killed.returncode
                if killed.returncode != 0:
                    cleanup["errors"].append(
                        {
                            "type": "CleanupCommandFailed",
                            "exitCode": killed.returncode,
                            "message": killed.stderr.decode(errors="replace"),
                        }
                    )
            else:
                process.kill()
            process.wait(timeout=15)
        except (OSError, subprocess.SubprocessError) as cleanup_error:
            cleanup["errors"].append(
                {"type": type(cleanup_error).__name__, "message": str(cleanup_error)}
            )
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        failure = {"type": type(error).__name__, "message": str(error)}
        if publication_path is not None:
            failure.update(stage="post-spawn-publication", path=str(publication_path))
            failed_request_publication = True
        else:
            failure["stage"] = "attempt-control"
        attempt_control.stop_owned(process, cleanup)
    cleanup["controllerProcessExited"] = process.poll() is not None
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
        "exitCode": process.returncode,
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
        "didRunProvider": True,
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


def _file_sha256(executable: str | None) -> str | None:
    if not executable:
        return None
    path = Path(shutil.which(executable) or executable)
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _validation_tool_rules(
    provider: str, bead: dict, plan: graph.Plan | None = None
) -> tuple[str, ...]:
    if plan is not None and plan.verification is not None:
        runner = resolve_runner(plan)
        command = (
            runner.worker_command(bead["proofModule"])
            if runner.name == "command"
            else subprocess.list2cmdline(runner.argv_for(bead["proofModule"]))
        )
        if provider == "claude":
            return (f"Bash({command})", f"PowerShell({command})")
        if provider == "copilot":
            return (f"shell({command})",)
        return ()
    if provider == "claude":
        return ("Bash(python -B -m unittest:*)", "PowerShell(python -B -m unittest:*)")
    if provider == "copilot":
        return ("shell(python -B -m unittest)",)
    return ()


def _write_record(path: Path, record: dict) -> None:
    write_record_json(path, record)


def _did_execute_real_provider(record: dict) -> bool:
    attempt = record.get("attempt") or {}
    return bool(
        record["didRunProvider"]
        and not record["isMocked"]
        and attempt.get("launch", {}).get("cli") == record["provider"]
        and record["provider"] in {"codex", "claude", "copilot"}
    )


def _requirement_validation(
    bead: str,
    requirements,
    module: str | None,
    integrated: dict | None,
    plan_revision: str | None = None,
    requirement_refs: list[dict] | None = None,
    spec_refs: list[dict] | None = None,
) -> dict:
    validation = integrated["validation"] if integrated else {}
    entry = {
        "bead": bead,
        "requirements": list(requirements),
        "proofModule": module,
        "candidateCommit": integrated["integrationCommit"] if integrated else None,
        "command": validation.get("command"),
        "passed": validation.get("passed", False),
    }
    if plan_revision is not None:
        entry.update(planRevision=plan_revision, requirementRefs=requirement_refs or [])
    if spec_refs is not None:
        entry["specRefs"] = spec_refs
    return entry


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
    record["attempt"].pop("recoverResult", None)
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
    if attempt.get("recoverResult"):
        request = read_record_json(attempt_dir / "request.json")
        result = read_record_json(attempt_dir / "result.json")
        mismatch = _completed_attempt_identity_error(record, request, result)
        if mismatch:
            record["status"] = f"rejected: attempt identity mismatch ({mismatch})"
            attempt.pop("recoverResult", None)
            return record
        if not _check_attempt_provenance(record, request, result, bead["ownedFiles"]):
            return record
        attempt.update(result)
        record["didRunProvider"] = result["didRunProvider"]
    elif fake_worker is not None:
        attempt["mode"] = "mock:test-worker"
    elif mock_provider == "reference":
        attempt["mode"] = "mock:reference"
    elif mock_provider == "noop":
        attempt["mode"] = "mock:noop"
    else:
        launch = get_adapter(record["provider"]).provider_normal_command(
            prompt=record["prompt"],
            model=record["model"],
            reasoning_effort=record["effort"],
            executable=record["executable"],
            allowed_tools=_validation_tool_rules(record["provider"], bead, plan),
        )
        attempt.update(mode="provider-normal", launch=launch.to_payload(), dir=str(attempt_dir))
    if not attempt.get("recoverResult"):
        attempt_dir.mkdir()
        request = {
            "runId": record["runId"],
            "bead": record["bead"],
            "requirementId": f"{plan.name}-{record['bead']}:{'-'.join(bead['requirements'])}",
            "baseCommit": attempt["baseCommit"],
            **adapter_provenance(),
            "executableSha256": _file_sha256(record["executable"] or launch.command[0])
            if launch
            else None,
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
    protected = [
        str(PurePosixPath(name.replace("\\", "/")))
        for name in (*plan.requirement_files, resolve_runner(plan).test_dir)
    ]
    requirements_intact = not any(
        path == name or path.startswith(f"{name}/") for path in changed for name in protected
    )
    outside = [path for path in changed if path not in owned]
    attempt.update(changedPaths=changed, finishedAt=_now())
    record["guard"] = {
        "changesOutsideOwnedFiles": outside,
        "requirementFilesIntact": requirements_intact,
        "passed": not outside and requirements_intact and bool(changed),
    }
    if record["status"] == "blocked: worker reported":
        return record
    # Empty attempts still need proof on the current integration HEAD. They
    # never pass the change guard or create a worker/merge commit.
    if changed and not record["guard"]["passed"]:
        reason = (
            "requirement files changed"
            if not requirements_intact
            else "changes outside owned files"
        )
        record["status"] = f"rejected: {reason}"
    return record


def _integration_candidate(record: dict, bead: dict, repo: Path) -> tuple[str, Path, dict]:
    """Resume only the candidate staged for this exact completed attempt."""
    worker = Path(record["attempt"]["cwd"]).resolve()
    root = repo.parent / f"candidate-{worker.name}"
    ref = f"candidate/{record['bead']}/{worker.name}"
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
        _git(worker, "add", "--", *bead["ownedFiles"])
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
        _git(worktree, "add", "--", *bead["ownedFiles"])
        changed_paths = _git(
            worktree, "diff", "--cached", "--name-only", record["attempt"]["baseCommit"]
        ).splitlines()
        record["attempt"]["changedPaths"] = changed_paths
        changed = bool(changed_paths)
        record["guard"]["passed"] = changed
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
    if receipt.exists() and binding is not None:
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
        validation = _validate(validation_root, manifest, bead["proofModule"])
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
    record["candidate"]["validated"] = validation["passed"]
    if not validation["passed"]:
        record["status"] = (
            "integrated validation failed" if changed else "rejected: no source change"
        )
        return
    if not changed:
        record["status"] = "already-satisfied"
        return
    if _git(repo, "rev-parse", "HEAD") == binding["baseIntegration"]:
        _git(repo, "merge", "-q", "--ff-only", integration_commit)
    record["lessonsPromoted"] = []
    for lesson in bead.get("lessons", []):
        entry_id = promote_lesson(
            memory,
            text=lesson["text"],
            evidence=PromotionEvidence(integration_commit, str(receipt), lesson["decisionId"]),
            author=f"worker:{record['bead']}:{record['provider']}",
            provider=record["provider"],
            model=record["model"] or "unknown",
            recovery_identity=json.dumps(binding, sort_keys=True),
        )
        promoted = {**lesson, "entryId": entry_id, "confirmed": False}
        record["lessonsPromoted"].append(promoted)
        confirm_lesson(memory, entry_id, by=confirmer, recover=True)
        promoted["confirmed"] = True
    record["status"] = STATUS_MOCK_ONLY if record["isMocked"] else "requirement-validated"


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
    max_provider_calls: int | None = None,
    execution_job: str | Path | None = None,
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
    source_fixture = mock_provider is not None or set(plan.beads).issubset(fake_workers or {})
    _verify_requirement_files(plan, manifest, plan.root / "template", source_fixture=source_fixture)
    order = graph.topological_order(plan)
    retry_ids = {item.strip() for item in retry_blocked.split(",")} if retry_blocked else set()
    if retry_ids and not resume_run_dir:
        raise ValueError("retry_blocked requires resume_run_dir")
    if retry_ids == {"all"}:
        retry_ids = set(plan.beads)
    elif retry_ids - set(plan.beads):
        raise ValueError("retry_blocked contains unknown bead ids (or mixed 'all')")
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
    if real_attempts and (
        execution_job is None or type(max_provider_calls) is not int or max_provider_calls < 1
    ):
        raise ValueError("provider-call-owned-native-job-required")
    for mapping in (provider_for, model_for, effort_for, executable_for, fake_workers):
        if set(mapping) - set(plan.beads):
            raise ValueError("per-bead mappings contain unknown bead ids")

    run_id = f"{re.sub(r'[^A-Za-z0-9_-]', '-', plan.name)}-{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    run_dir = (
        Path(resume_run_dir).resolve() if resume_run_dir else Path(work_root).resolve() / run_id
    )
    repo, evidence, memory = run_dir / "repo", run_dir / "evidence", run_dir / "memory-workspace"
    if real_attempts:
        from codex_harness.core.provider_call_budget import bind_run, verify_job

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
        # terminal-preimage recovery of its original run, without resetting cap.
        bind_run(
            execution_job,
            plan.root,
            run_dir,
            max_provider_calls=max_provider_calls,
            resume=resume_run_dir is not None,
            retry_options={"retryBlocked": retry_blocked, "relaunchAmbiguous": relaunch_ambiguous},
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
            "verification": f"whole {graph.test_dir(plan)}/ + VISION.md + PLAN.md + "
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
            "redispatched": [],
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
        _git(repo, "init", "-q", "-b", "integration")
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
            },
        }
    _verify_requirement_files(plan, manifest, repo, source_fixture=source_fixture)
    done: set[str] = set()
    terminal: set[str] = set()
    running: dict[str, Future] = {}

    def fail(bead_id: str) -> None:
        terminal.add(bead_id)
        blocked = {bead_id}
        for dependent in order:
            if dependent not in terminal and blocked.intersection(plan.beads[dependent].depends_on):
                records[dependent]["status"] = f"blocked: dependency {bead_id} failed"
                terminal.add(dependent)
                blocked.add(dependent)

    if previous:
        retained_failures = []
        for bead_id in order:
            old = previous["beads"].get(bead_id)
            if old is None:
                continue
            old_attempt = old.get("attempt") or {}
            old_evidence = Path(old.get("evidenceDir", evidence / bead_id))
            request_path, result_path = (
                old_evidence / "attempt" / "request.json",
                old_evidence / "attempt" / "result.json",
            )
            try:
                request = read_record_json(request_path) if request_path.exists() else {}
            except (OSError, ValueError):
                request = {}
            # run.json may still be the pre-launch checkpoint after process death.
            # Project completed fields only after exact request/result and selected
            # worktree/base/packet checks; real process originals are still checked below.
            minimal_fields = {
                "cwd",
                "baseCommit",
                "startedAt",
                "planRevision",
                "requirementRefs",
                "specRefs",
            }
            if result_path.exists() and old_attempt and set(old_attempt).issubset(minimal_fields):
                result = read_record_json(result_path)
                projection = {
                    **old,
                    "attempt": {
                        key: value for key, value in old_attempt.items() if key != "startedAt"
                    },
                }
                if _completed_attempt_identity_error(projection, request, result) is None:
                    old["attempt"] = old_attempt = {**old_attempt, **result}
                    old["didRunProvider"] = result.get("didRunProvider", False)
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
            if _hold_permission_guard(old, old_evidence / "attempt"):
                records[bead_id] = old
                retained_failures.append(bead_id)
                record["resumeSummary"]["retained"].append(
                    {
                        "bead": bead_id,
                        "reason": "permission guard requires scoped user disposition"
                        if old["status"] == STATUS_PERMISSION_HELD
                        else "denial audit inconclusive: "
                        + str(old["permissionGuard"]["auditReason"]),
                    }
                )
            elif (
                old_attempt.get("status") == "timed-out"
                and old_attempt.get("cleanup", {}).get("controllerProcessExited") is not True
            ):
                # A terminal timeout does not establish that its owned child
                # stopped. Retry flags cannot replace the saved exit observation.
                records[bead_id] = old
                retained_failures.append(bead_id)
                record["resumeSummary"]["retained"].append(
                    {
                        "bead": bead_id,
                        "reason": "timed-out attempt requires observed controller exit",
                    }
                )
            elif old_attempt.get("status") == "cancelled" or (
                old_attempt.get("failure")
                and old_attempt.get("cleanup", {}).get("stopMethod") == "owned-Popen.kill"
            ):
                cleanup = old_attempt.get("cleanup", {})
                if bead_id not in retry_ids or cleanup.get("controllerProcessExited") is not True:
                    records[bead_id] = old
                    retained_failures.append(bead_id)
                    record["resumeSummary"]["retained"].append(
                        {
                            "bead": bead_id,
                            "reason": (
                                "cancelled"
                                if old_attempt.get("status") == "cancelled"
                                else "interrupted"
                            )
                            + " attempt requires targeted retry and observed controller exit",
                        }
                    )
            elif uncertain and not relaunch_ambiguous:
                records[bead_id] = old
                old["status"] = "ambiguous launch"
                retained_failures.append(bead_id)
                record["resumeSummary"]["retained"].append(
                    {"bead": bead_id, "reason": "ambiguous launch requires explicit relaunch"}
                )
            elif (validated or result_path.exists()) and not _check_attempt_provenance(
                old, owned_files=plan.beads[bead_id].owned_files
            ):
                # Targeted retry creates a new attempt namespace, never repairs
                # an old original. Cancellation/denial/ambiguous holds above win.
                if bead_id not in retry_ids:
                    records[bead_id] = old
                    retained_failures.append(bead_id)
                    record["resumeSummary"]["retained"].append(
                        {"bead": bead_id, "reason": old["localProcessProvenance"]["code"]}
                    )
            elif old.get("status") == "blocked: worker reported" or "blocker" in old:
                fresh_prompt = worker_packet.build_packet(
                    plan,
                    {**manifest["beads"][bead_id], "id": bead_id},
                    repo,
                    memory_prompt_for(memory),
                    provider=records[bead_id]["provider"],
                    proof_level=manifest.get("proofLevel"),
                )
                old_prompt = request.get("prompt") or old.get("prompt")
                if bead_id not in retry_ids and (old_prompt is None or fresh_prompt == old_prompt):
                    records[bead_id] = old
                    retained_failures.append(bead_id)
                    record["resumeSummary"]["retained"].append(
                        {
                            "bead": bead_id,
                            "reason": "worker-reported blocker retained: "
                            + (
                                "packet unchanged"
                                if old_prompt is not None
                                else "prior packet unavailable"
                            ),
                        }
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
            elif (
                not uncertain
                and result_path.exists()
                and (
                    not integrated
                    or (
                        (old_evidence / "integration-state.json").exists()
                        and old.get("status", "").startswith("bead failed:")
                    )
                )
                and old.get("planRevision") == plan.plan_revision
                and refs_match
            ):
                result = read_record_json(result_path)
                mismatch = _completed_attempt_identity_error(old, request, result)
                if mismatch:
                    records[bead_id] = old
                    old["status"] = f"rejected: attempt identity mismatch ({mismatch})"
                    old_attempt.pop("recoverResult", None)
                    old["candidate"]["validated"] = False
                    retained_failures.append(bead_id)
                    record["resumeSummary"]["retained"].append(
                        {"bead": bead_id, "reason": old["status"]}
                    )
                    continue
                if result["status"] == "exited" and result["exitCode"] == 0:
                    records[bead_id] = old
                    old.update(status="pending", evidenceDir=str(old_evidence))
                    old["attempt"] = {**(old.get("attempt") or {}), **result, "recoverResult": True}
                    record["resumeSummary"]["retained"].append(
                        {"bead": bead_id, "reason": "completed attempt recovered without relaunch"}
                    )
                    continue
            if records[bead_id] is not old or (uncertain and relaunch_ambiguous):
                history = copy.deepcopy(old)
                history.pop("superseded", None)
                history["status"] = "superseded"
                history["evidenceDir"] = str(old_evidence)
                if "blocker" in old and old.get("attempt"):
                    blocker_path = Path(old["attempt"]["cwd"]) / ".harness" / "blocker.json"
                    if blocker_path.exists() or blocker_path.is_symlink():
                        archive = old_evidence / "superseded-blocker.json"
                        blocker_path.rename(archive)
                        history["blockerPath"] = str(archive)
                records[bead_id]["superseded"] = [*old.get("superseded", []), history]
                for artifact in (request_path, result_path, old_evidence / "integrated.json"):
                    if artifact.exists():
                        payload = read_record_json(artifact)
                        payload["disposition"] = "superseded"
                        _write_record(artifact, payload)
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
                    if (current["attempt"] or {}).get("recoverResult"):
                        bead_evidence = Path(current["evidenceDir"])
                    else:
                        bead_evidence = evidence / bead_id
                        suffix = ""
                        if bead_evidence.exists():
                            number = 1
                            while (bead_evidence / f"retry-{number}").exists():
                                number += 1
                            bead_evidence /= f"retry-{number}"
                            suffix = f"-retry-{number}"
                        bead_evidence.mkdir()
                        base_commit = _git(repo, "rev-parse", "HEAD")
                        worktree = run_dir / f"bead-{bead_id}{suffix}"
                        _git(
                            repo,
                            "worktree",
                            "add",
                            "-q",
                            "-b",
                            f"bead-{bead_id}{suffix}",
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
                        )
                        current["attempt"] = {
                            "cwd": str(worktree),
                            "baseCommit": base_commit,
                            "startedAt": _now(),
                            "planRevision": current["planRevision"],
                            "requirementRefs": current["requirementRefs"],
                            **_spec_binding(current),
                        }
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
                recovering_completed_attempt = bool((current["attempt"] or {}).get("recoverResult"))
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
                        )
                except Exception as error:
                    # A worker exception fails its bead, preserving independent
                    # work and the failure in the same run's evidence.
                    current["status"] = f"bead failed: {type(error).__name__}: {error}"
                if previous and not recovering_completed_attempt:
                    request_path = Path(current["evidenceDir"]) / "attempt" / "request.json"
                    if request_path.exists():
                        request = read_record_json(request_path)
                        if request.get("processId") or request.get("didLaunchMockAttempt"):
                            record["resumeSummary"]["redispatched"].append(bead_id)
                current["finishedAt"] = _now()
                if current["integrated"] and current["integrated"].get("claim"):
                    add_claim(current["integrated"]["claim"])
                if current["status"] in {
                    "requirement-validated",
                    STATUS_MOCK_ONLY,
                    "already-satisfied",
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
    if previous:
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
        status = f"plan-failed: {failure} {records[failure]['status']}"
    elif (
        record["integrationValidation"] is not None
        and not record["integrationValidation"]["passed"]
    ):
        status = "plan-failed: integration proof failed"
    else:
        status = (
            STATUS_MOCK_ONLY
            if record["isMocked"]
            else STATUS_VALIDATED
            if plan.integration_proof_module is not None
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
