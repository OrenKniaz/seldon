"""Local process/record fixtures only; no actual provider or recovery proof."""

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest

from codex_harness.core.attempt_activity import observe_attempt_activity


def request(attempt):
    attempt.mkdir(parents=True, exist_ok=True)
    value = dict(
        status="launching",
        startedAt="2026-01-01T00:00:00+00:00",
        requirementId="local-fixture",
        planRevision="fixture-revision",
        prompt="NEVER_EMIT_PROMPT",
    )
    (attempt / "request.json").write_text(json.dumps(value), encoding="utf-8")
    for name in ("events.jsonl", "stderr.txt"):
        (attempt / name).write_bytes(b"")
    return value


def test_real_self_terminating_process_activity_quiet_threshold_and_crashed_controller(tmp_path):
    attempt = tmp_path / "attempt"
    request(attempt)
    with (
        (attempt / "events.jsonl").open("wb") as events,
        (attempt / "stderr.txt").open("wb") as errors,
    ):
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                'import time; print(\'{"type":"local-progress"}\', flush=True); time.sleep(0.7)',
            ],
            stdout=events,
            stderr=errors,
            stdin=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 5
            while not (attempt / "events.jsonl").stat().st_size:
                assert time.monotonic() < deadline, "Local process produced no output"
                time.sleep(0.01)
            anchor = max(path.stat().st_mtime for path in attempt.iterdir())
            observed = datetime.fromtimestamp(anchor, UTC)
            before = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in attempt.iterdir()
            }
            recent = observe_attempt_activity(attempt, now=observed + timedelta(seconds=1))
            assert recent["stuck"] is False and recent["secondsSinceActivity"] == 1
            quiet = observe_attempt_activity(attempt, now=observed + timedelta(seconds=300))
            assert quiet["stuck"] is True and quiet["secondsSinceActivity"] == 300
            assert quiet["state"] == "unknown" and quiet["isTerminal"] is None
            assert quiet["processLiveness"] == "unknown"
            assert "NEVER_EMIT_PROMPT" not in json.dumps(quiet)
            assert {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in attempt.iterdir()
            } == before
        finally:
            assert process.wait(timeout=5) == 0
    # Actual child exit with no controller result still cannot be inferred by this observer.
    crashed = observe_attempt_activity(attempt, now=observed + timedelta(seconds=301))
    assert crashed["isTerminal"] is None and crashed["processLiveness"] == "unknown"


@pytest.mark.parametrize("field", ["runId", "bead", "cwd", "baseCommit"])
def test_swapped_canonical_identity_cannot_make_attempt_terminal(tmp_path, field):
    attempt = tmp_path / "attempt"
    value = request(attempt)
    value.update(runId="run-one", bead="A", cwd=str(tmp_path / "worktree"), baseCommit="a" * 40)
    (attempt / "request.json").write_text(json.dumps(value), encoding="utf-8")
    result = {**value, "status": "cancelled", "finishedAt": "2026-01-01T00:00:01+00:00"}
    result[field] = "different-canonical-identity"
    (attempt / "result.json").write_text(json.dumps(result), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in attempt.iterdir()}
    observation = observe_attempt_activity(attempt)
    assert observation["state"] == "unknown"
    assert observation["isTerminal"] is observation["stuck"] is None
    assert observation["recordedStatus"] == "launching"
    assert {path.name: path.read_bytes() for path in attempt.iterdir()} == before
