"""Run-local cancellation requests; only a live controller stops its owned child."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codex_harness.core.record_io import read_record_json, write_record_json

TARGET_FIELDS = ("runId", "bead", "startedAt", "cwd", "baseCommit", "requirementId", "processId")


def _now():
    return datetime.now(UTC).isoformat()


def _inside(path: Path, root: Path) -> Path:
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("cancel-path-outside-evidence")
    return path.resolve()


def _read(path):
    return read_record_json(path)


def _write(path, value):
    write_record_json(path, value)


def target(request):
    value = {key: request.get(key) for key in TARGET_FIELDS}
    if any(value[key] is None or value[key] == "" for key in TARGET_FIELDS):
        raise ValueError("cancel-attempt-identity-missing")
    if not isinstance(value["processId"], int) or value["processId"] <= 0:
        raise ValueError("cancel-attempt-not-running")
    return value


def request_cancel(run_dir, bead, *, started_at=None, reason="operator cancellation") -> dict:
    """Select the current attempt from run evidence; never accept a PID argument."""
    run_dir = Path(run_dir).resolve()
    evidence = run_dir / "evidence"
    run = _read(_inside(evidence / "run.json", evidence))
    current = run["beads"][bead]
    directory = _inside(Path(current["evidenceDir"]) / "attempt", evidence)
    request = _read(_inside(directory / "request.json", directory))
    binding = target(request)
    if binding["runId"] != run["runId"] or binding["bead"] != bead:
        raise ValueError("cancel-attempt-identity-mismatch")
    if started_at is not None and binding["startedAt"] != started_at:
        raise ValueError("cancel-stale-attempt")
    result = _inside(directory / "result.json", directory)
    if result.exists() or request.get("status") != "launching":
        raise ValueError("cancel-attempt-already-terminal")
    path = _inside(directory / "cancel-request.json", directory)
    if path.exists():
        existing = _read(path)
        if existing.get("target") != binding:
            raise ValueError("cancel-stale-request")
        return existing
    cancellation = {"target": binding, "requestedAt": _now(), "reason": str(reason)}
    _write(path, cancellation)
    return cancellation


def consume_cancel(directory, request, *, control=None, exited=False) -> dict | None:
    """Validate a bound request; only the Job receipt can attest cancellation."""
    directory = Path(directory)
    observation = _inside(directory / "cancel-observation.json", directory)
    if observation.exists():
        saved = _read(observation)
        if not isinstance(saved, dict):
            raise ValueError("cancel-observation-invalid-object")
        status = saved.get("status")
        if not isinstance(status, str) or status not in {
            "cancelled",
            "not-applied",
            "rejected-invalid",
            "rejected-stale",
        }:
            raise ValueError("cancel-observation-unsupported-status")
        if status in {"cancelled", "not-applied"}:
            saved_request = saved.get("request")
            if not isinstance(saved_request, dict) or not isinstance(
                saved_request.get("target"), dict
            ):
                raise ValueError("cancel-observation-invalid-request")
            target(saved_request["target"])
        if status == "cancelled" and saved.get("request", {}).get("target") != target(request):
            return {"status": "rejected-stale-observation", "observedAt": _now()}
        return saved
    try:
        path = _inside(directory / "cancel-request.json", directory)
        if not path.exists():
            return None
        cancellation = _read(path)
        matches = isinstance(cancellation, dict) and cancellation.get("target") == target(request)
    except (ValueError, TypeError, KeyError) as error:
        result = {"status": "rejected-invalid", "observedAt": _now(), "reason": str(error)}
        _write(observation, result)
        return result
    result = {"request": cancellation, "observedAt": _now(), "status": "rejected-stale"}
    if not matches:
        _write(observation, result)
        return result
    if not exited:
        return {**result, "status": "requested"}
    verified = control and control.get("quiescenceVerified") is True
    result.update(status="cancelled" if verified and control.get("stopReason") == "cancelled" else "not-applied",
                  reason="Owned Job stopped" if verified else "Completion won or cleanup is unverified",
                  cleanup=control or {})
    _write(observation, result)
    return result
