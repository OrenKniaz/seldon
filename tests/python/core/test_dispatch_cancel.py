"""Local process fixtures and injected provider builders; no model/provider calls."""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.python.core.execution_support import (
    source_execution_context,
    source_process_observations,
)

from codex_harness.core import dispatch
from codex_harness.core.provider_launch import ProviderNormalLaunch
from codex_harness.core.record_io import read_record_json


def source_only_packet_renderer(monkeypatch):
    """Reuse supported packet prose while retaining an explicit local CLI identity."""
    source_process_observations(monkeypatch)
    build = dispatch.worker_packet.build_packet

    def local_packet(*args, provider, **kwargs):
        return build(*args, provider="codex", **kwargs) + (
            "\nSOURCE-ONLY local Python process fixture; no supported provider executes.\n"
        )

    monkeypatch.setattr(dispatch.worker_packet, "build_packet", local_packet)


def wait_for_request(path, future=None):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if future is not None and future.done():
            future.result()
        if path.exists():
            request = read_record_json(path)
            if request.get("processId"):
                return request
        time.sleep(0.02)
    raise AssertionError("Local child request was not published")


def local_attempt(tmp_path, *, seconds=3, terminal=True, timeout=10):
    from tests.python.core.test_dispatch import _tiny_audited_plan

    from codex_harness.core.provider_call_budget import bind_run

    source = _tiny_audited_plan(tmp_path / "local-source")
    manifest = json.loads((source / "sample.json").read_text())
    manifest["beads"].pop("B")
    manifest["beads"]["A"]["ownedFiles"].append("local.py")
    (source / "template/local.py").write_text("VALUE = 0\n")
    (source / "sample.json").write_text(json.dumps(manifest))
    context = source_execution_context(tmp_path / "canonical", source, timeout=timeout)
    scoped_bead = json.loads((context["plan"] / "sample.json").read_text())["beads"]["A"]
    run = context["runs"] / "local-run"
    bind_run(context["directory"], context["plan"], run, max_provider_calls=16)
    cwd = run / "repo"
    cwd.mkdir(parents=True)
    subprocess.run(["git", "-C", str(cwd), "init", "-q"], check=True, capture_output=True)
    evidence = run / "evidence/A"
    attempt = evidence / "attempt"
    attempt.mkdir(parents=True)
    request = {
        "runId": "local-run",
        "bead": "A",
        "startedAt": "local-start",
        "cwd": str(cwd),
        "baseCommit": "local-fixture",
        "requirementId": "RA",
        "status": "launching",
        "isMocked": True,
    }
    dispatch._write_record(
        run / "evidence/run.json",
        {
            "runId": "local-run",
            "beads": {"A": {"status": "running", "evidenceDir": str(evidence)}},
        },
    )
    event = json.dumps({"type": "turn.completed" if terminal else "turn.started", "isMocked": True})
    launch = ProviderNormalLaunch(
        cli="codex",
        command=(
            sys.executable,
            "-B",
            "-c",
            "# SOURCE-ONLY local process fixture\n"
            f"import time; print({event!r}, flush=True); time.sleep({seconds})",
        ),
        protections=("Explicit local process fixture; no provider runs.",),
    )
    request.update(
        provider="codex",
        model="gpt-6.1-sol",
        effort="medium",
        ownedFiles=scoped_bead["ownedFiles"],
        planRevision=context["revision"],
        executionJob=str(context["directory"]),
        requirementRefs=scoped_bead["requirementRefs"],
        specRefs=scoped_bead["specRefs"],
        adapterCommit="b" * 40,
        executableSha256=dispatch._file_sha256(sys.executable),
        prompt="Source-only local child fixture λ\r\n",
        mode="provider-normal",
        command=list(launch.command),
        launch=launch.to_payload(),
    )
    return run, cwd, attempt, request, launch


def test_buffered_controller_consumes_cancel_and_stops_actual_owned_child(tmp_path):
    run, cwd, attempt, request, launch = local_attempt(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch._run_provider, launch, cwd, attempt, request, 10, {})
        published = wait_for_request(attempt / "request.json", future)
        # Baseline witness: the existing controller ignores a matching durable
        # request. The supported producer/CLI is covered separately after repair.
        dispatch._write_record(
            attempt / "cancel-request.json",
            {
                "target": {
                    key: published[key]
                    for key in (
                        "runId",
                        "bead",
                        "startedAt",
                        "cwd",
                        "baseCommit",
                        "requirementId",
                        "processId",
                    )
                },
                "requestedAt": "local-cancel",
                "reason": "source regression",
            },
        )
        result = future.result(timeout=15)
    assert result["status"] == "cancelled"
    assert result["cleanup"]["controllerProcessExited"] is True
    assert result["exitCode"] != 0
    assert result["isLiveProductProof"] is False
    assert read_record_json(attempt / "result.json")["status"] == "cancelled"


def test_producer_refuses_stale_target_and_foreign_evidence_without_writes(tmp_path):
    from codex_harness.core.attempt_control import request_cancel

    run, cwd, attempt, request, launch = local_attempt(tmp_path, seconds=0.5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch._run_provider, launch, cwd, attempt, request, 10, {})
        wait_for_request(attempt / "request.json", future)
        with pytest.raises(ValueError, match="cancel-stale-attempt"):
            request_cancel(run, "A", started_at="old-attempt")
        assert not (attempt / "cancel-request.json").exists()
        path = run / "evidence/run.json"
        record = read_record_json(path)
        record["beads"]["A"]["evidenceDir"] = str(tmp_path / "foreign")
        dispatch._write_record(path, record)
        with pytest.raises(ValueError, match="cancel-path-outside-evidence"):
            request_cancel(run, "A")
        assert not (tmp_path / "foreign").exists()
        assert future.result(timeout=15)["status"] == "exited"


def test_natural_exit_before_consumption_wins_real_process_race(tmp_path):
    from codex_harness.core import attempt_control

    run, cwd, attempt, request, launch = local_attempt(tmp_path, seconds=0.2)
    with subprocess.Popen(launch.command, cwd=cwd, stdout=subprocess.DEVNULL) as process:
        request["processId"] = process.pid
        dispatch._write_record(attempt / "request.json", request)
        attempt_control.request_cancel(run, "A")
        process.wait(timeout=5)
        observation = attempt_control.consume_cancel(process, attempt, request)
    assert observation["status"] == "not-applied"
    assert observation["reason"] == "completed-before-cancel"
    assert process.returncode == 0
