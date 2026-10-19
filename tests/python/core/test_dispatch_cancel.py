"""Local process fixtures and injected provider builders; no model/provider calls."""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
    from tests.python.core.test_dispatch import _tiny_plan

    from codex_harness.core.provider_call_budget import bind_run

    source = _tiny_plan(tmp_path / "local-source")
    manifest = json.loads((source / "sample.json").read_text())
    manifest["beads"].pop("B")
    manifest["beads"]["A"]["ownedFiles"] = ["local.py"]
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
        ownedFiles=["local.py"],
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


def test_cli_request_producer_stops_owned_child_and_preserves_uncertainty(tmp_path, capsys):
    from codex_harness.core.dispatch_cli import main

    run, cwd, attempt, request, launch = local_attempt(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch._run_provider, launch, cwd, attempt, request, 10, {})
        published = wait_for_request(attempt / "request.json", future)
        assert (
            main(
                ["cancel", "--run", str(run), "--bead", "A", "--started-at", published["startedAt"]]
            )
            == 0
        )
        submitted = json.loads(capsys.readouterr().out)
        assert submitted["status"] == "cancellation-requested"
        assert submitted["target"]["processId"] == published["processId"]
        result = future.result(timeout=15)
    assert result["status"] == "cancelled"
    assert result["cleanup"]["status"] == "UNVERIFIED"
    assert result["cleanup"]["controllerProcessExited"] is True
    assert result["cleanup"]["descendantsStopped"] is None
    assert result["cancellation"]["status"] == "cancelled"
    assert result["outputComplete"] is False


@pytest.mark.parametrize("bad", ["stale", "malformed"])
def test_invalid_request_does_not_stop_or_relabel_normal_completion(tmp_path, bad):
    run, cwd, attempt, request, launch = local_attempt(tmp_path, seconds=0.5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch._run_provider, launch, cwd, attempt, request, 10, {})
        published = wait_for_request(attempt / "request.json", future)
        binding = {
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
        }
        value = {"target": {**binding, "startedAt": "other-attempt"}}
        (attempt / "cancel-request.json").write_text(
            "{" if bad == "malformed" else json.dumps(value), encoding="utf-8"
        )
        result = future.result(timeout=15)
    assert result["status"] == "exited" and result["exitCode"] == 0
    assert result["cancellation"]["status"].startswith("rejected-")
    assert result["cleanup"]["status"] == "NOT_REQUESTED"


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


def test_completed_result_wins_late_request_without_mutation(tmp_path):
    from codex_harness.core.attempt_control import request_cancel

    run, cwd, attempt, request, launch = local_attempt(tmp_path, seconds=0)
    result = dispatch._run_provider(launch, cwd, attempt, request, 10, {})
    before = (attempt / "result.json").read_bytes()
    with pytest.raises(ValueError, match="already-terminal"):
        request_cancel(run, "A")
    assert result["status"] == "exited" and result["exitCode"] == 0
    assert (attempt / "result.json").read_bytes() == before


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


@pytest.mark.parametrize("error", [PermissionError, NotImplementedError])
def test_stop_failure_is_synthetic_single_method_no_retry(tmp_path, monkeypatch, error):
    from codex_harness.core.attempt_control import request_cancel

    run, cwd, attempt, request, launch = local_attempt(tmp_path, seconds=1)
    calls = []

    def refused(process):
        calls.append(process)
        raise error("synthetic denied/unsupported stop")

    monkeypatch.setattr(subprocess.Popen, "kill", refused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch._run_provider, launch, cwd, attempt, request, 10, {})
        wait_for_request(attempt / "request.json", future)
        request_cancel(run, "A")
        result = future.result(timeout=5)
    assert len(calls) == 1
    assert result["status"] == "cancelled"
    assert result["cleanup"]["controllerProcessExited"] is False
    assert result["cleanup"]["status"] == "UNVERIFIED"
    assert result["exitCode"] is None
    assert result["cleanup"]["errors"][0]["type"] == error.__name__
    # Observation only: the bounded fixture exits naturally, with no second stop.
    calls[0].wait(timeout=5)


def test_cancelled_incomplete_telemetry_retains_audit_hold(tmp_path):
    from codex_harness.core.attempt_control import request_cancel

    run, cwd, attempt, request, launch = local_attempt(tmp_path, terminal=False)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatch._run_provider, launch, cwd, attempt, request, 10, {})
        wait_for_request(attempt / "request.json", future)
        request_cancel(run, "A")
        result = future.result(timeout=15)
    assert result["status"] == "cancelled"
    assert result["permissionGuard"]["didObservePermissionDenial"] is None
    assert result["permissionGuard"]["requiresAuditHold"] is True
    bead = {"attempt": result, "candidate": {"validated": False}}
    assert dispatch._hold_permission_guard(bead, attempt) is True
    assert bead["status"] == dispatch.STATUS_AUDIT_HELD


def test_selected_cancel_preserves_completed_work_and_requires_targeted_resume(
    tmp_path, monkeypatch
):
    from tests.python.core.test_dispatch import _bind_protected, _tiny_plan

    from codex_harness.core.attempt_control import request_cancel

    plan = _tiny_plan(tmp_path, dependent=True)
    manifest_path = plan / "sample.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["beads"]["C"] = {
        **manifest["beads"]["A"],
        "title": "Bead C",
        "ownedFiles": ["C.py"],
        "requirements": ["RC"],
        "proofModule": "test_C.py",
    }
    (plan / "template/C.py").write_text("VALUE = 0\n")
    (plan / "template/tests/test_C.py").write_text(
        (plan / "template/tests/test_A.py").read_text().replace("from A import", "from C import")
    )
    manifest_path.write_text(json.dumps(manifest))
    _bind_protected(plan)
    launched = []

    class LocalAdapter:
        def provider_normal_command(self, *, prompt, **kwargs):
            bead = "A" if "Bead A" in prompt else "C"
            launched.append(bead)
            # Source writes and the explicitly mocked terminal observation finish
            # before an idle local-process tail. No actual provider turn is claimed.
            script = (
                "# SOURCE-ONLY local process fixture\n"
                f"from pathlib import Path; Path('{bead}.py').write_text('VALUE = 1\\n'); "
                'import time; print(\'{"type":"turn.completed","isMocked":true}\', flush=True); '
                + ("time.sleep(15)" if bead == "A" else "")
            )
            return ProviderNormalLaunch(
                cli="codex",
                command=(sys.executable, "-B", "-c", script),
                protections=("local fixture",),
            )

    monkeypatch.setattr(dispatch, "get_adapter", lambda name: LocalAdapter())
    source_only_packet_renderer(monkeypatch)
    context = source_execution_context(tmp_path / "canonical", plan, max_parallel=2, timeout=25)
    plan, runs = context["plan"], context["runs"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            dispatch.run_plan,
            plan,
            work_root=runs,
            **context["kwargs"],
        )
        deadline = time.monotonic() + 25
        run = None
        while time.monotonic() < deadline:
            if future.done():
                pytest.fail(f"Local run ended before cancellation: {future.result()['status']}")
            candidates = list(runs.glob("*/evidence/run.json")) if runs.exists() else []
            if candidates:
                snapshot = read_record_json(candidates[0])
                if snapshot["beads"]["C"]["candidate"]["validated"]:
                    run = candidates[0].parents[1]
                    break
            time.sleep(0.05)
        assert run is not None, "Independent local work did not finish"
        request_cancel(run, "A")
        first = future.result(timeout=25)
    a, b, c = (first["beads"][key] for key in ("A", "B", "C"))
    assert a["attempt"]["status"] == "cancelled" and a["integrated"] is None
    assert not a["candidate"]["validated"]
    assert b["status"] == "blocked: dependency A failed" and not b["didRunProvider"]
    assert c["candidate"]["validated"] is True
    assert first["didExecuteRealProvider"] is False
    persisted = read_record_json(Path(a["evidenceDir"]) / "attempt/RA/result.json")
    assert persisted["status"] == "cancelled"

    retained = dispatch.run_plan(
        plan,
        work_root=runs,
        resume_run_dir=run,
        **context["kwargs"],
    )
    assert retained["beads"]["A"]["attempt"]["status"] == "cancelled"
    assert sorted(launched) == ["A", "C"]
    resumed = dispatch.run_plan(
        plan,
        work_root=runs,
        resume_run_dir=run,
        retry_blocked="A",
        mock_provider="noop",
        fake_workers={
            key: lambda cwd, key=key: (cwd / f"{key}.py").write_text("VALUE = 1\n")
            for key in ("A", "B")
        },
    )
    assert resumed["status"] == dispatch.STATUS_MOCK_ONLY
    assert resumed["resumeSummary"]["redispatched"] == ["A", "B"]
    assert resumed["beads"]["C"]["attempt"] == c["attempt"]
    assert resumed["beads"]["A"]["superseded"][0]["attempt"]["status"] == "cancelled"
