"""Actual bounded local children and explicitly synthetic publication/stop errors."""

import json
import subprocess

import pytest
from tests.python.core.test_dispatch_cancel import (
    local_attempt,
)

from codex_harness.core import dispatch
from codex_harness.core.record_io import read_record_json


@pytest.mark.parametrize("raw", [b"{"], ids=["truncated"])
def test_malformed_cached_observation_contains_child_and_preserves_bytes(
    tmp_path, monkeypatch, raw
):
    _, cwd, attempt, request, launch = local_attempt(tmp_path, seconds=1, timeout=5)
    observation = attempt / "cancel-observation.json"
    observation.write_bytes(raw)
    children, stops = [], []
    popen, kill = subprocess.Popen, subprocess.Popen.kill

    def spawn(command, *args, **kwargs):
        child = popen(command, *args, **kwargs)
        if tuple(command) == launch.command:
            children.append(child)
        return child

    def observe_stop(child):
        stops.append(child)
        return kill(child)

    monkeypatch.setattr(subprocess.Popen, "kill", observe_stop)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    try:
        result = dispatch._run_provider(launch, cwd, attempt, request, 5, {})
        assert result["status"] in {"observation-failed", dispatch.STATUS_AUDIT_HELD}
        assert result["failure"]["stage"] == "attempt-control"
        assert result["failure"]["type"] in {"ValueError", "JSONDecodeError"}
        assert result["cancellation"] is None  # Corrupt evidence does not establish cancellation.
        assert result["outputComplete"] is False
        assert result["cleanup"]["controllerProcessExited"] is True
        assert result["cleanup"]["status"] == "UNVERIFIED"
        assert len(stops) == 1 and stops[0] is children[0]
        assert observation.read_bytes() == raw
        assert read_record_json(attempt / "result.json") == result
        assert not (attempt / "cancel-request.json").exists()
    finally:
        for child in children:
            child.wait(timeout=5)  # Bounded baseline orphan observation only; no fallback stop.


@pytest.mark.parametrize("failed_alias", [True])
@pytest.mark.parametrize("stop_error", [PermissionError])
def test_post_spawn_publication_failure_contains_owned_child_once(
    tmp_path, monkeypatch, failed_alias, stop_error
):
    _, cwd, attempt, request, launch = local_attempt(tmp_path, seconds=1, timeout=5)
    planned = {"RA": {**request, "requirementId": "RA"}}
    failed_path = attempt / "RA/request.json" if failed_alias else attempt / "request.json"
    writes, stops, children = [], [], []
    real_write, real_popen, real_kill = (
        dispatch._write_record,
        subprocess.Popen,
        subprocess.Popen.kill,
    )

    def publish(path, value):
        if path == failed_path and value.get("processId"):
            writes.append(path)
            raise PermissionError("synthetic post-Popen publication refusal")
        return real_write(path, value)

    def spawn(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    def stop(child):
        stops.append(child)
        if stop_error:
            raise stop_error("synthetic stop refusal/unsupported operation")
        return real_kill(child)

    monkeypatch.setattr(dispatch, "_write_record", publish)
    monkeypatch.setattr(subprocess.Popen, "kill", stop)
    monkeypatch.setattr(dispatch.subprocess, "Popen", spawn)
    try:
        result = dispatch._run_provider(launch, cwd, attempt, request, 5, planned)
        assert result["status"] in {"observation-failed", dispatch.STATUS_AUDIT_HELD}
        assert result["failure"]["stage"] == "post-spawn-publication"
        assert result["failure"]["type"] == "PermissionError"
        assert result["failure"]["path"] == str(failed_path)
        assert result["outputComplete"] is False
        assert result["isLiveProductProof"] is False
        assert result["cleanup"]["status"] == "UNVERIFIED"
        assert result["cleanup"]["descendantsStopped"] is None
        assert result["cleanup"]["controllerProcessExited"] is (stop_error is None)
        assert result["exitCode"] != 0
        assert len(stops) == 1 and stops[0] is children[0]
        assert writes == [failed_path]  # Never retry the refused publication at finalization.
        if stop_error:
            assert result["cleanup"]["errors"][0]["type"] == stop_error.__name__
            assert result["exitCode"] is None
        saved = json.loads((attempt / "result.json").read_text(encoding="utf-8"))
        alias = json.loads((attempt / "RA/result.json").read_text(encoding="utf-8"))
        assert saved == result
        assert alias["status"] == result["status"] and alias["processId"] == children[0].pid
    finally:
        # Observation only. Even the red baseline's orphan is bounded to one second.
        for child in children:
            child.wait(timeout=5)
