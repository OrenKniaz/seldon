"""Read-only file activity observations; no process inspection or recovery authority."""

from __future__ import annotations

import json
import math
import stat
from datetime import UTC, datetime
from pathlib import Path

TERMINAL_STATUSES = {"cancelled", "exited", "timed-out", "launch-failed", "observation-failed"}


def _invalid_constant(value):
    raise ValueError("Nonfinite JSON value")


def _time(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Timestamp lacks timezone")
    return parsed.astimezone(UTC)


def _file(root, name):
    path = root / name
    if not path.resolve().is_relative_to(root):
        raise ValueError("Attempt evidence escapes its directory")
    return path


def _record(root, name):
    path = _file(root, name)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Attempt record is not a regular file")
    with path.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    after = path.stat()
    if len(data) > 1024 * 1024 or (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("Record oversized or changed during observation")
    record = json.loads(data, parse_constant=_invalid_constant)
    if not isinstance(record, dict):
        raise ValueError("Attempt record is not an object")
    return record, after.st_mtime


def observe_attempt_activity(attempt_dir, *, now=None, no_progress_seconds=300):
    """Observe quiet evidence, never assert that a child is alive or safely stopped.

    Missing result with a valid launching/running request can have a measurable
    quiet interval, but its lifecycle remains unknown. Stream contents and PIDs
    are never inspected; metadata activity is not semantic product progress.
    """
    if (
        isinstance(no_progress_seconds, bool)
        or not isinstance(no_progress_seconds, int | float)
        or not (math.isfinite(no_progress_seconds) and no_progress_seconds > 0)
    ):
        raise ValueError("no_progress_seconds must be positive and finite")
    now = datetime.now(UTC) if now is None else now
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    observation = {
        "state": "unavailable",
        "recordedStatus": None,
        "isTerminal": None,
        "processLiveness": "unknown",
        "lastObservedActivityAt": None,
        "secondsSinceActivity": None,
        "noProgressThresholdSeconds": no_progress_seconds,
        "stuck": None,
        "reason": "Canonical attempt evidence unavailable",
    }
    if attempt_dir is None:
        return observation
    try:
        root = Path(attempt_dir).resolve()
        request, modified = _record(root, "request.json")
        started = _time(request["startedAt"])
        status = request["status"]
        if not isinstance(status, str) or started > now:
            raise ValueError("Invalid request status/time")
        observation.update(state="unknown", recordedStatus=status)
        try:
            result, _ = _record(root, "result.json")
        except FileNotFoundError:
            result = None
        if result is not None:
            if any(
                (key in request) != (key in result) or result.get(key) != request.get(key)
                for key in (
                    "runId",
                    "bead",
                    "cwd",
                    "baseCommit",
                    "requirementId",
                    "planRevision",
                    "startedAt",
                )
            ):
                raise ValueError("Result does not match request")
            status = result["status"]
            observation["recordedStatus"] = status
            if status not in TERMINAL_STATUSES:
                raise ValueError("Result is held or has no recognized terminal status")
            finished = _time(result["finishedAt"])
            if not started <= finished <= now:
                raise ValueError("Invalid terminal time")
            return {
                **observation,
                "state": "terminal",
                "isTerminal": True,
                "stuck": False,
                "lastObservedActivityAt": finished.isoformat(),
                "secondsSinceActivity": (now - finished).total_seconds(),
                "reason": "Terminal receipt recorded; process/descendant liveness is unverified",
            }
        if status not in {"launching", "running"}:
            raise ValueError("Missing result; held or partial lifecycle evidence")
        timestamps = [modified, started.timestamp()]
        for name in ("events.jsonl", "stderr.txt"):
            info = _file(root, name).stat()
            if not _file(root, name).is_file():
                raise ValueError("Output evidence is not a regular file")
            timestamps.append(info.st_mtime)
        last = datetime.fromtimestamp(max(timestamps), UTC)
        if last > now:
            raise ValueError("Activity timestamp is in the future")
        seconds = (now - last).total_seconds()
        return {
            **observation,
            "lastObservedActivityAt": last.isoformat(),
            "secondsSinceActivity": seconds,
            "stuck": seconds >= no_progress_seconds,
            "reason": "File activity only; missing result leaves lifecycle and safe stop unknown",
        }
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        return {**observation, "reason": f"Evidence unavailable/ambiguous: {type(error).__name__}"}
