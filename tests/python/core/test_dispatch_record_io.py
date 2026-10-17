"""Actual bounded local children and explicitly synthetic publication/stop errors."""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from tests.python.core.test_dispatch_cancel import (
    local_attempt,
    source_only_packet_renderer,
    wait_for_request,
)

from codex_harness.core import attempt_control, dispatch
from codex_harness.core.record_io import open_record_read, read_record_json


@pytest.mark.parametrize(
    "raw",
    [b"{", b"[]", b'{"status":"cancelled","request":null}'],
    ids=["truncated", "list", "request-null"],
)
def test_malformed_cached_observation_contains_child_and_preserves_bytes(
    tmp_path, monkeypatch, raw
):
    _, cwd, attempt, request, launch = local_attempt(tmp_path, seconds=1)
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


def test_valid_cached_observation_from_actual_producer_never_stops_twice(tmp_path, monkeypatch):
    run, cwd, attempt, request, launch = local_attempt(tmp_path)
    stops, kill = [], subprocess.Popen.kill

    def observe_stop(child):
        stops.append(child)
        return kill(child)

    monkeypatch.setattr(subprocess.Popen, "kill", observe_stop)
    with subprocess.Popen(launch.command, cwd=cwd, stdout=subprocess.DEVNULL) as child:
        request["processId"] = child.pid
        dispatch._write_record(attempt / "request.json", request)
        attempt_control.request_cancel(run, "A")
        first = attempt_control.consume_cancel(child, attempt, request)
        second = attempt_control.consume_cancel(child, attempt, request)
        assert first["status"] == "cancelled" and first == second
        assert first["cleanup"]["controllerProcessExited"] is True
        assert len(stops) == 1 and stops[0] is child


@pytest.mark.parametrize("failed_alias", [False, True])
@pytest.mark.parametrize("stop_error", [None, PermissionError, NotImplementedError])
def test_post_spawn_publication_failure_contains_owned_child_once(
    tmp_path, monkeypatch, failed_alias, stop_error
):
    _, cwd, attempt, request, launch = local_attempt(tmp_path, seconds=1)
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


def test_cancel_stops_owned_child_while_supported_request_reader_is_held(tmp_path, monkeypatch):
    run, cwd, attempt, request, launch = local_attempt(tmp_path)
    stopped, kill = Event(), subprocess.Popen.kill

    def observe_stop(child):
        kill(child)
        stopped.set()

    monkeypatch.setattr(subprocess.Popen, "kill", observe_stop)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch._run_provider, launch, cwd, attempt, request, 10, {})
        published = wait_for_request(attempt / "request.json", future)
        with open_record_read(attempt / "request.json") as reader:
            attempt_control.request_cancel(run, "A")
            assert stopped.wait(timeout=5)
            assert json.loads(reader.read()) == published
        result = future.result(timeout=10)
    assert result["status"] == "cancelled"
    assert result["cleanup"]["controllerProcessExited"] is True
    assert read_record_json(attempt / "request.json")["status"] == "cancelled"


def test_synthetic_observation_refusal_after_stop_cannot_trigger_second_stop(tmp_path, monkeypatch):
    run, cwd, attempt, request, launch = local_attempt(tmp_path, seconds=1)
    stops, writes = [], []
    write = attempt_control._write

    def refused_stop(child):
        stops.append(child)
        raise PermissionError("synthetic stop refusal")

    def refused_observation(path, value):
        if path.name == "cancel-observation.json":
            writes.append(path)
            raise PermissionError("synthetic observation publication refusal")
        return write(path, value)

    monkeypatch.setattr(subprocess.Popen, "kill", refused_stop)
    monkeypatch.setattr(attempt_control, "_write", refused_observation)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch._run_provider, launch, cwd, attempt, request, 5, {})
        wait_for_request(attempt / "request.json", future)
        attempt_control.request_cancel(run, "A")
        result = future.result(timeout=5)
    assert len(stops) == len(writes) == 1
    assert result["status"] != "exited"
    assert result["outputComplete"] is False
    assert result["exitCode"] is None
    assert result["cleanup"]["controllerProcessExited"] is False
    assert result["cleanup"]["errors"][0]["type"] == "PermissionError"


def test_post_spawn_refusal_with_unobserved_exit_cannot_relaunch_on_resume(tmp_path, monkeypatch):
    from tests.python.core.test_dispatch import _bind_protected, _tiny_plan

    from codex_harness.core.provider_launch import ProviderNormalLaunch

    plan = _tiny_plan(tmp_path, dependent=True)
    _bind_protected(plan)
    stops, failed = [], []
    write = dispatch._write_record

    class LocalAdapter:
        def provider_normal_command(self, **kwargs):
            return ProviderNormalLaunch(
                cli="local-process-test",
                command=(
                    sys.executable,
                    "-B",
                    "-c",
                    'import time; print(\'{"type":"turn.completed","isMocked":true}\', '
                    "flush=True); time.sleep(3)",
                ),
                protections=("Bounded local child with mocked terminal telemetry; no provider.",),
            )

    def refuse_publication(path, value):
        if path.name == "request.json" and value.get("processId") and not failed:
            # Make the audit conclusive, so it cannot conceal the cleanup hold defect.
            events = path.parent / "events.jsonl"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if b"turn.completed" in events.read_bytes():
                    break
                time.sleep(0.01)
            assert b"turn.completed" in events.read_bytes()
            failed.append(path)
            raise PermissionError("synthetic post-spawn publication refusal")
        return write(path, value)

    def refuse_stop(child):
        stops.append(child)
        raise PermissionError("synthetic owned-stop refusal")

    monkeypatch.setattr(dispatch, "get_adapter", lambda name: LocalAdapter())
    source_only_packet_renderer(monkeypatch)
    monkeypatch.setattr(dispatch, "_write_record", refuse_publication)
    monkeypatch.setattr(subprocess.Popen, "kill", refuse_stop)
    try:
        first = dispatch.run_plan(
            plan,
            work_root=tmp_path / "runs",
            provider_for={"A": "local-process-test", "B": "local-process-test"},
        )
        a = first["beads"]["A"]
        assert a["attempt"]["failure"]["stage"] == "post-spawn-publication"
        assert a["attempt"]["cleanup"]["controllerProcessExited"] is False
        assert a["permissionGuard"]["requiresAuditHold"] is False
        assert first["didExecuteRealProvider"] is False
        run = Path(a["evidenceDir"]).parents[1]
        resumed = dispatch.run_plan(
            plan,
            work_root=tmp_path / "unused",
            resume_run_dir=run,
            retry_blocked="A",
            relaunch_ambiguous=True,
            fake_workers={"A": lambda cwd: pytest.fail("Unobserved child exit allowed relaunch")},
        )
        assert resumed["beads"]["A"]["attempt"] == a["attempt"]
        assert not resumed["beads"]["A"]["candidate"]["validated"]
        assert not resumed["beads"]["B"]["didRunProvider"]
        assert resumed["resumeSummary"]["redispatched"] == []
        assert "observed controller exit" in resumed["resumeSummary"]["retained"][0]["reason"]
        assert len(stops) == 1
    finally:
        for child in stops:
            child.wait(timeout=5)
    stops[0].wait(timeout=5)  # Observe the bounded fixture; never stop twice.


def test_synthetic_stop_refusal_with_natural_exit_zero_stays_cancelled(tmp_path, monkeypatch):
    run, cwd, attempt, request, launch = local_attempt(tmp_path, seconds=0.5)
    stops = []

    def refused_after_exit(child):
        stops.append(child)
        child.wait(timeout=5)
        raise PermissionError("synthetic refusal overlapping natural exit zero")

    monkeypatch.setattr(subprocess.Popen, "kill", refused_after_exit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch._run_provider, launch, cwd, attempt, request, 5, {})
        wait_for_request(attempt / "request.json", future)
        attempt_control.request_cancel(run, "A")
        result = future.result(timeout=5)
    assert len(stops) == 1
    assert result["exitCode"] == 0 and result["status"] == "cancelled"
    assert result["outputComplete"] is False
    assert result["cleanup"]["controllerProcessExited"] is True
    assert result["cleanup"]["status"] == "UNVERIFIED"
    assert result["cleanup"]["errors"][0]["type"] == "PermissionError"
