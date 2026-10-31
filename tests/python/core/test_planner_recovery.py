"""Recovery through real local processes, canonical stores and public consent.

The dispatch test payload launches only Python locally, never a coding provider.
Publication uses the retained source fixture's real audit and synthetic reviews.
"""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.core.execution_support import publish_source_store, source_grant

from codex_harness.core import audit_process
from codex_harness.core import planner_jobs as jobs
from codex_harness.core import planner_recovery as recovery
from codex_harness.core.managed_paths import short
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_cli import main as cli

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"
LOCAL_PAYLOAD = """
import subprocess, sys
from pathlib import Path
from codex_harness.core import planner_jobs as jobs, provider_call_budget as budget
from codex_harness.core.record_io import write_record_json_once
workspace, job_id = Path(sys.argv[1]), sys.argv[2]
directory = jobs._directory(workspace, job_id)
def local_dispatch(plan, **options):
    request = jobs._request(directory)
    run = directory / 'runs' / 'local-child'
    (run / 'evidence').mkdir(parents=True)
    budget.bind_run(directory, plan, run, max_provider_calls=options['max_provider_calls'])
    worker = request['options']['workers'][0]
    bead = jobs.read_record_json(plan / 'sample.json')['beads'][worker['bead']]
    record = dict(worker, runId=run.name, planRevision=request['publicationRevision'],
                  ownedFiles=bead['ownedFiles'], requirementRefs=bead.get('requirementRefs'),
                  specRefs=bead.get('specRefs'))
    budget.claim_call(directory, record, run / 'evidence' / 'attempt', timeout=options['timeout'])
    subprocess.run([sys.executable, '-B', '-c',
                    "from pathlib import Path; Path('child-exited').write_text('done')"],
                   cwd=workspace, check=True)
    return dict(runId=run.name, status='plan-failed: finite local child scenario')
jobs.dispatch.run_plan = local_dispatch
terminal = jobs.execute_worker_job(workspace, job_id)
assert terminal['status'] == 'completed', terminal
"""


@pytest.fixture
def admitted(tmp_path, monkeypatch):
    store, revision = publish_source_store(tmp_path, spine_file=FIXTURE)
    workers = [
        dict(bead=bead, provider="codex", model="gpt-6.1-sol", effort="medium")
        for bead in store.published_manifest(revision)["beads"]
    ]
    source_grant(store, revision, workers)
    launches = []

    def launch(command, **kwargs):
        launches.append(command)
        return SimpleNamespace(pid=12345)

    with monkeypatch.context() as patch:
        patch.setattr(jobs.subprocess, "Popen", launch)
        job = jobs.start_worker_job(store, revision, workers, call_cap=16)
    yield store, revision, workers, job, launches, launch
    store.close()


def tool(server, name, **arguments):
    return server.handle_line(
        json.dumps(
            dict(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params=dict(
                    name=name, arguments=dict(expected_workspace=str(server.workspace), **arguments)
                ),
            )
        )
    )["result"]


def answer(server, request, quote="Approve"):
    return tool(
        server,
        "harness_record_chat_confirmation",
        gate="recovery",
        request_id=request["request_id"],
        expected_revision=request["proof"]["publicationRevision"],
        quoted_message=quote,
        quote_sha256=hashlib.sha256(quote.encode()).hexdigest(),
        actor_label="source-test-user",
    )


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_local_child_exit_readmit_once_with_cumulative_v2_usage(admitted, monkeypatch):
    """Catch unsafe/duplicate successor launch and budget reset at the owning boundary.

    Old job tests fake Popen/dispatch and cannot prove descendant teardown or a
    successor's real canonical provider-claim consumer. No cleanup field is mocked.
    """
    store, revision, workers, job, launches, launch = admitted
    assert Path(job["evidenceDir"]).name == short(revision)
    actual_run = audit_process.run_audit_process

    def local_payload(command, **options):
        return actual_run(
            [sys.executable, "-P", "-B", "-c", LOCAL_PAYLOAD, str(store.workspace), revision],
            **options,
        )

    monkeypatch.setattr(audit_process, "run_audit_process", local_payload)
    assert recovery.inspect(store.workspace, revision)["verdict"] == "UNKNOWN"
    recovery.monitor(store.workspace, revision)
    proof = recovery.inspect(store.workspace, revision)
    assert proof["verdict"] == "VERIFIED-QUIESCENT", proof
    assert (store.workspace / "child-exited").read_text() == "done"
    assert proof["spentCalls"] == 1 and proof["remainingCalls"] == 15
    old = snapshot(Path(job["evidenceDir"]))
    server = PlannerServer(store.workspace)
    try:
        offered = tool(
            server,
            "harness_recovery",
            mode="resolve",
            job_id=revision,
            choice="readmit",
            reason="Finite local child exited",
        )
        assert not offered["isError"], offered
        request = offered["structuredContent"]
        completed = answer(server, request)
        assert not completed["isError"], completed
        successor = completed["structuredContent"]["disposition"]["successor"]
        with pytest.raises(ValueError, match="plan-b-consent-required"):
            jobs.start_worker_job(store, revision, workers, call_cap=16)
        assert not jobs._directory(store.workspace, successor).exists()
        options = dict(expected_revision=revision, workers=workers, call_cap=16)
        execution = tool(
            server, "harness_prepare_chat_confirmation", gate="execution", execution=options
        )
        assert not execution["isError"], execution
        execution_id = execution["structuredContent"]["request_id"]
        consent = tool(
            server,
            "harness_record_chat_confirmation",
            gate="execution",
            request_id=execution_id,
            expected_revision=store._revision(),
            quoted_message="Approve",
            quote_sha256=hashlib.sha256(b"Approve").hexdigest(),
            actor_label="source-test-user",
        )
        assert not consent["isError"], consent
        with monkeypatch.context() as patch:
            patch.setattr(jobs.subprocess, "Popen", launch)
            started = tool(server, "harness_worker_start", **options)
            assert not started["isError"], started
            new_job = started["structuredContent"]
            repeated = tool(server, "harness_worker_start", **options)
            assert not repeated["isError"], repeated
            assert repeated["structuredContent"] == new_job
        assert new_job["jobId"] == successor != revision
        assert Path(new_job["evidenceDir"]).name == short(successor)
        assert new_job["publicationRevision"] == revision
        assert len(launches) == 2
        assert recovery.inspect(store.workspace, successor)["spentCalls"] == 1
        assert snapshot(Path(job["evidenceDir"])) == old
        assert answer(server, request)["isError"]

        # Run the same finite payload in the successor through the real claim consumer.
        def successor_payload(command, **options):
            return actual_run(
                [sys.executable, "-P", "-B", "-c", LOCAL_PAYLOAD, str(store.workspace), successor],
                **options,
            )

        monkeypatch.setattr(audit_process, "run_audit_process", successor_payload)
        recovery.monitor(store.workspace, successor)
        assert recovery.inspect(store.workspace, successor)["spentCalls"] == 2
        assert (
            jobs._request(jobs._directory(store.workspace, successor))["publicationRevision"]
            == revision
        )
    finally:
        server.close()


def test_unknown_refuses_readmit_abandon_terminal_and_inspect_no_writes(admitted, capsys):
    """Catch read-only error-path writes and bypassing UNKNOWN or terminal quarantine.

    Existing status/consent tests neither exercise recovery nor compare whole
    workspace bytes across MCP and CLI inspection, including invalid requests.
    """
    store, revision, workers, job, launches, _ = admitted
    before = snapshot(store.workspace)
    server = PlannerServer(store.workspace)
    try:
        for job_id in (revision, "invalid", "0" * 64):
            tool(server, "harness_recovery", mode="inspect", job_id=job_id)
            cli(["--workspace", str(store.workspace), "recovery", "inspect", "--job-id", job_id])
        malformed = server.handle_line(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=2,
                    method="tools/call",
                    params=dict(name="harness_recovery", arguments="inspect"),
                )
            )
        )
        assert malformed["result"]["isError"]
        server.close()
        assert snapshot(store.workspace) == before
        refused = tool(
            server,
            "harness_recovery",
            mode="resolve",
            job_id=revision,
            choice="readmit",
            reason="Unknown cannot release",
        )
        assert refused["isError"] and "VERIFIED-QUIESCENT" in str(refused)
        offered = tool(
            server,
            "harness_recovery",
            mode="resolve",
            job_id=revision,
            choice="abandon",
            reason="Preserve unknown execution in quarantine",
        )
        assert not offered["isError"], offered
        request = offered["structuredContent"]
        completed = answer(server, request)
        assert not completed["isError"], completed
        assert completed["structuredContent"]["disposition"]["quarantine"] is True
        assert recovery.inspect(store.workspace, revision)["disposition"]["choice"] == "abandon"
        assert jobs.worker_job_status(store.workspace, revision)["status"] == "abandoned"
        with pytest.raises(ValueError, match="abandoned-terminal"):
            jobs.start_worker_job(store, revision, workers, call_cap=16)
        assert len(launches) == 1
        assert snapshot(Path(job["evidenceDir"])) == {
            str(Path(path).relative_to(Path(job["evidenceDir"]).relative_to(store.workspace))): data
            for path, data in before.items()
            if Path(path).is_relative_to(Path(job["evidenceDir"]).relative_to(store.workspace))
        }
    finally:
        server.close()
    capsys.readouterr()


def test_decline_and_stale_proof_grant_no_resolution(admitted):
    """Catch reused/changed targets and the old recovery adapter accepting any words.

    A/B gate tests do not protect the recovery adapter's route through V2 consent.
    """
    store, revision, _, _, _, _ = admitted
    server = PlannerServer(store.workspace)
    try:
        first = recovery.prepare(store, revision, "abandon", "Stopped testing")
        declined = answer(server, first, "No")
        assert not declined["isError"], declined
        assert recovery.disposition(store, revision) is None
        assert answer(server, first)["isError"]
        undecided = recovery.prepare(store, revision, "abandon", "Stopped testing")
        reply = answer(server, undecided, "Please inspect first")
        assert not reply["isError"], reply
        assert reply["structuredContent"]["state"] == "changes-requested"
        assert recovery.disposition(store, revision) is None
        second = recovery.prepare(store, revision, "abandon", "Stopped testing")
        (jobs._directory(store.workspace, revision) / "temp").mkdir()
        assert answer(server, second)["isError"]
        assert recovery.disposition(store, revision) is None
        assert recovery.approvals.records(store, recovery.RESOLVED) == []
    finally:
        server.close()
