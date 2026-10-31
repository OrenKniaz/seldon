"""Native publication-job originals and durable finite claim boundaries, no providers."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.test_planner_execution import grant_execution
from tests.python.core.test_planner_resume import _published

from codex_harness.core import dispatch, planner_jobs
from codex_harness.core.provider_call_budget import bind_run, claim_call, verify_job

project = test_planner_replay_gaps.project


def _controller(store, monkeypatch, callback, *, cap=2):
    options = _published(store)
    grant_execution(store, options, call_cap=cap)
    monkeypatch.setattr(
        planner_jobs.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=12345)
    )
    original = planner_jobs.start_worker_job(
        store, options["expected_revision"], options["workers"], timeout=5, call_cap=cap
    )
    directory = Path(original["evidenceDir"])

    def source_dispatch(plan, **kwargs):
        run = directory / "runs/source-only-run"
        bind_run(directory, plan, run, max_provider_calls=cap)
        record = dict(
            runId=run.name,
            bead="B1",
            provider="codex",
            model="gpt-6.1-sol",
            effort="medium",
            planRevision=planner_jobs._request(directory)["jobId"],
            **{
                key: json.loads((Path(plan) / "sample.json").read_text())["beads"]["B1"][key]
                for key in ("ownedFiles", "requirementRefs", "specRefs")
            },
        )
        callback(directory, run, record)
        return dict(runId=run.name, status=dispatch.STATUS_MOCK_ONLY)

    monkeypatch.setattr(planner_jobs.dispatch, "run_plan", source_dispatch)
    terminal = planner_jobs.execute_worker_job(
        store.workspace, planner_jobs._request(directory)["jobId"]
    )
    assert terminal["status"] == "completed", terminal
    return directory


def test_parallel_claims_are_finite_and_persist_without_refunds(project, monkeypatch):
    def inspect(directory, run, record):
        def claim(index):
            try:
                return claim_call(directory, record, run / f"attempt-{index}")
            except ValueError as error:
                assert "worker-call-cap-exhausted" in str(error)
                return None

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(claim, range(8)))
        assert sorted(item for item in results if item is not None) == [1, 2]
        before = list(project.connection.execute("SELECT * FROM usage_ledger WHERE kind='worker'"))
        with pytest.raises(ValueError, match="cap-exhausted"):
            claim_call(directory, record, run / "recovered-uncertain-attempt")
        assert (
            list(project.connection.execute("SELECT * FROM usage_ledger WHERE kind='worker'"))
            == before
        )

    _controller(project, monkeypatch, inspect)


def test_scope_pid_and_second_run_cannot_borrow_or_reset_budget(project, monkeypatch):
    def inspect(directory, run, record):
        with pytest.raises(ValueError, match="scope-mismatch"):
            claim_call(directory, {**record, "model": "unapproved-model"}, run / "bad")
        with pytest.raises(ValueError, match="fixed-job-run"):
            bind_run(
                directory,
                directory / "plan",
                project.workspace / "another-root/run",
                max_provider_calls=2,
            )
        with pytest.raises(FileExistsError):
            bind_run(directory, directory / "plan", directory / "runs/second", max_provider_calls=2)
        import os

        monkeypatch.setattr(
            "codex_harness.core.provider_call_budget.os.getpid", lambda: os.getppid()
        )
        with pytest.raises(ValueError, match="owned-controller"):
            verify_job(directory)

    _controller(project, monkeypatch, inspect)


def test_actual_popen_failure_still_spends_call_and_exhaustion_prevents_next(project, monkeypatch):
    from codex_harness.core.provider_launch import ProviderNormalLaunch

    def inspect(directory, run, record):
        calls = []
        with pytest.raises(ValueError, match="timeout-scope-mismatch"):
            claim_call(directory, record, run / "unapproved-narrower-timeout", timeout=0.5)
        assert not (directory / "provider-call-budget").exists()

        def refuse(*args, **kwargs):
            calls.append(1)
            raise OSError("SOURCE-ONLY launch refusal")

        monkeypatch.setattr(dispatch.subprocess, "Popen", refuse)
        launch = ProviderNormalLaunch(
            cli="codex", command=("source-only-never-executed",), protections=("SOURCE-ONLY",)
        )
        for index in range(2):
            attempt = run / f"attempt-{index}"
            attempt.mkdir(parents=True)
            request = {
                **record,
                "command": list(launch.command),
                "launch": launch.to_payload(),
                "executionJob": str(directory),
            }
            if index == 0:
                result = dispatch._run_provider(launch, project.workspace, attempt, request, 5, {})
                assert result["status"] == "launch-failed"
            else:
                with pytest.raises(ValueError, match="cap-exhausted"):
                    dispatch._run_provider(launch, project.workspace, attempt, request, 5, {})
        assert calls == [1]

    _controller(project, monkeypatch, inspect, cap=1)


def test_same_original_terminal_preimage_allows_one_recovery_without_refill(project, monkeypatch):
    from codex_harness.core.record_io import write_record_json, write_record_json_once

    def inspect(directory, run, record):
        evidence = run / "evidence"
        evidence.mkdir(parents=True)
        path = evidence / "run.json"
        finished = datetime.now(UTC)
        write_record_json_once(
            path, {"status": "plan-failed: source-only", "finishedAt": finished.isoformat()}
        )
        assert claim_call(directory, record, run / "first-attempt") == 1

        def recover(_):
            try:
                bind_run(
                    directory,
                    directory / "plan",
                    run,
                    max_provider_calls=2,
                    resume=True,
                    retry_options={"retryBlocked": "B1"},
                )
                return True
            except FileExistsError:
                return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(recover, range(4))) == 1
        assert claim_call(directory, record, run / "explicit-retry") == 2
        write_record_json(
            path,
            {
                "status": "plan-failed: source-only",
                "finishedAt": (finished + timedelta(seconds=1)).isoformat(),
            },
        )
        assert recover(0)
        with pytest.raises(ValueError, match="cap-exhausted"):
            claim_call(directory, record, run / "no-refill-after-second-terminal")

    _controller(project, monkeypatch, inspect)


@pytest.mark.parametrize(
    "case", ["unknown", "paused", "empty-failure", "missing-time", "naive-time", "malformed-time"]
)
def test_unknown_or_malformed_run_cannot_claim_recovery(project, monkeypatch, case):
    """Actual original-run state must be terminal before a recovery claim exists."""
    from codex_harness.core.record_io import write_record_json_once

    def inspect(directory, run, record):
        finished = datetime.now(UTC).isoformat()
        status = "plan-failed: SOURCE-ONLY terminal fixture"
        if case in {"unknown", "paused"}:
            status = case
        elif case == "empty-failure":
            status = "plan-failed: "
        elif case == "missing-time":
            finished = None
        elif case == "naive-time":
            finished = datetime.now(UTC).replace(tzinfo=None).isoformat()
        else:
            finished = "not-a-timestamp"
        (run / "evidence").mkdir(parents=True)
        path = run / "evidence/run.json"
        write_record_json_once(path, {"status": status, "finishedAt": finished})
        assert claim_call(directory, record, run / "original-attempt") == 1
        before = list(project.connection.execute("SELECT * FROM usage_ledger WHERE kind='worker'"))
        raw = path.read_bytes()
        with pytest.raises(ValueError, match="terminal-original-required"):
            bind_run(directory, directory / "plan", run, max_provider_calls=2, resume=True)
        assert not (directory / "dispatch-resume").exists()
        assert path.read_bytes() == raw
        assert (
            list(project.connection.execute("SELECT * FROM usage_ledger WHERE kind='worker'"))
            == before
        )

    _controller(project, monkeypatch, inspect)


def test_raised_worker_total_uses_current_b_without_resetting_active_job_spend(
    project, monkeypatch
):
    """Catch old job caps freezing renewed B; review renewal misses worker ownership checks."""
    from codex_harness.core import approvals
    from codex_harness.core import planner_confirmations as confirmation
    from codex_harness.core.plan_store import open_plan_store
    from codex_harness.core.provider_call_budget import usage

    def inspect(directory, run, record):
        assert claim_call(directory, record, run / "first") == 1
        with pytest.raises(ValueError, match="cap-exhausted"):
            claim_call(directory, record, run / "refused")
        current = approvals.current(project, "B")["subject"]["execution"]
        request = confirmation.prepare_confirmation(
            project,
            "plan-execution",
            execution=dict(
                expected_revision=record["planRevision"],
                workers=current["workers"],
                max_parallel=current["maxParallel"],
                timeout=current["timeout"],
                call_cap=2,
            ),
        )
        assert any(d["field"] == "execution.callCap" for d in request["diff"])
        confirmation.complete_confirmation(
            project,
            request["request_id"],
            "accept",
            dict(approve=True, statement="Approve", conditions=""),
        )
        assert claim_call(directory, record, run / "renewed") == 2
        with open_plan_store(project.workspace) as reopened:
            assert usage(reopened, "worker") == 2
        with pytest.raises(ValueError, match="cap-exhausted"):
            claim_call(directory, record, run / "still-exhausted")

    _controller(project, monkeypatch, inspect, cap=1)


def test_new_b_candidate_cannot_admit_an_old_job_snapshot(project, monkeypatch):
    """Catch today's template authorizing an old job; limit renewal keeps identical inputs."""
    from codex_harness.core import planner_confirmations as confirmation
    from codex_harness.core.provider_call_budget import usage

    def inspect(directory, run, record):
        source = project.workspace / "template/answer.py"
        source.write_text(source.read_text() + "\n# regenerated candidate input\n")
        manifest = project.compile_template(project.workspace / "template")
        original = json.loads((directory / "request.json").read_text())
        scope = original["executionScope"]
        request = confirmation.prepare_confirmation(
            project,
            "plan-execution",
            execution=dict(
                expected_revision=manifest["planRevision"],
                workers=scope["workers"],
                max_parallel=scope["maxParallel"],
                timeout=scope["timeout"],
                call_cap=scope["callCap"],
            ),
        )
        assert any("candidate.templateFiles" in d["field"] for d in request["diff"])
        confirmation.complete_confirmation(
            project,
            request["request_id"],
            "accept",
            dict(approve=True, statement="Approve", conditions=""),
        )
        with pytest.raises(ValueError) as refusal:
            claim_call(directory, record, run / "old-candidate")
        assert refusal.value.code == "plan-b-consent-required"
        assert any("candidate.templateFiles" in d["field"] for d in refusal.value.approval["diff"])
        assert usage(project, "worker") == 0

    _controller(project, monkeypatch, inspect)
