"""Native publication-job originals and durable finite claim boundaries, no providers."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.test_planner_execution import grant_execution
from tests.python.core.test_planner_resume import _published

from codex_harness.core import dispatch, planner_jobs
from codex_harness.core.provider_call_budget import bind_run, claim_call

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
