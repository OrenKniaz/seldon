"""Actual persisted planner transports; no provider or human-approval proof."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.core import test_planner_replay_gaps, test_planner_resume

from codex_harness.core import planner_jobs
from codex_harness.core.mcp_planner import PlannerServer

project = test_planner_resume.project


def rpc(server, name, arguments=None, *, expected_workspace=None):
    """Use the independently known fixture target for positive same-project calls."""
    arguments = dict(arguments or {})
    if expected_workspace is not None:
        arguments = {"expected_workspace": str(expected_workspace), **arguments}
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
    )["result"]


def cli(workspace, *arguments):
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "codex_harness.core.plan_cli",
            "--workspace",
            str(workspace),
            "--json",
            "resume",
            *arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


@pytest.mark.parametrize("transport", ["mcp", "cli"])
def test_existing_chat_resume_returns_persisted_hold_without_starting_workers(tmp_path, transport):
    """A reconnect exposes the real saved gate instead of requiring chat reconstruction."""
    server = PlannerServer(tmp_path)
    assert not rpc(
        server,
        "harness_plan_start",
        {"idea": "Small saved idea", "name": "Saved"},
        expected_workspace=tmp_path,
    )["isError"]
    server.close()
    database = tmp_path / ".codex-harness/plan.sqlite3"
    original = database.read_bytes()
    if transport == "mcp":
        server = PlannerServer(tmp_path)
        try:
            reply = rpc(server, "harness_plan_resume", expected_workspace=tmp_path)
        finally:
            server.close()
        assert not reply["isError"], reply
        data = reply["structuredContent"]
    else:
        process = cli(tmp_path)
        assert process.returncode == 0, process.stdout + process.stderr
        reply = json.loads(process.stdout)
        assert not reply["isError"], reply
        data = reply["data"]
    assert data["schema"] == "seldon/planner-resume-v1"
    assert data["state"] == "held"
    assert data["job"] is None
    assert data["execution"]["capacityVerified"] is False
    assert data["execution"]["authorizationReported"] is False
    assert data["liveness"] == "unknown"
    assert data["harnessDeliveryProven"] is False
    assert data["observationPersisted"] is True
    assert Path(data["observationPath"]).is_file()
    assert data["canonicalStatus"]["name"] == "Saved"
    assert data["canonicalStatus"]["pending_gate"] == "approval:align"
    assert not data["canonicalStatus"]["approval"]["granted"]
    assert database.read_bytes() == original
    assert not (tmp_path / ".codex-harness/worker-jobs").exists()


@pytest.mark.parametrize("transport", ["mcp", "cli"])
def test_missing_plan_resume_never_creates_workspace(tmp_path, transport):
    """Inspection cannot turn a missing saved conversation into a new plan."""
    workspace = tmp_path / "absent" / "project"
    if transport == "mcp":
        server = PlannerServer(workspace)
        try:
            reply = rpc(server, "harness_plan_resume", expected_workspace=workspace)
        finally:
            server.close()
        assert not reply["isError"], reply
        data = reply["structuredContent"]
    else:
        process = cli(workspace)
        assert process.returncode == 0, process.stdout + process.stderr
        data = json.loads(process.stdout)["data"]
    assert data["state"] == "held" and data["reasonCode"] == "plan-missing"
    assert data["canonicalStatus"] is None
    assert data["observationPersisted"] is False
    assert not workspace.parent.exists()


@pytest.mark.parametrize("transport", ["mcp", "cli"])
def test_malformed_resume_cannot_reopen_writable_store_or_approve(tmp_path, transport, monkeypatch):
    """Transport input errors must not fall through normal writable status rendering."""
    server = PlannerServer(tmp_path)
    assert not rpc(
        server,
        "harness_plan_start",
        {"idea": "Saved", "name": "Saved"},
        expected_workspace=tmp_path,
    )["isError"]
    server.close()
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    if transport == "mcp":
        server = PlannerServer(tmp_path)

        def forbidden(*args, **kwargs):
            pytest.fail("Resume error must not open writable status/store")

        monkeypatch.setattr(server, "_store", forbidden)
        monkeypatch.setattr(server, "_status", forbidden)
        try:
            reply = rpc(
                server,
                "harness_plan_resume",
                {"by": "user", "approve": True},
                expected_workspace=tmp_path,
            )
        finally:
            server.close()
        assert reply["isError"]
        assert "unexpected" in reply["content"][0]["text"]
    else:
        process = cli(tmp_path, "--workers", "not JSON")
        assert process.returncode == 2, process.stdout + process.stderr
        assert json.loads(process.stdout)["isError"]
    assert {
        p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before


def test_chat_hold_then_separate_worker_start_and_cli_reconnect_preserves_original(
    project, monkeypatch
):
    """Resume only holds/reattaches; separate existing start uses a source-only Popen spy."""
    server = PlannerServer(project.workspace)
    held = rpc(server, "harness_plan_resume", expected_workspace=project.workspace)
    assert not held["isError"] and held["structuredContent"]["state"] == "held"
    options = test_planner_resume._published(project)
    launches = []
    with monkeypatch.context() as patch:
        patch.setattr(
            planner_jobs.subprocess,
            "Popen",
            lambda *a, **k: launches.append((a, k)) or SimpleNamespace(pid=54321),
        )
        started = rpc(server, "harness_plan_resume", options, expected_workspace=project.workspace)
        assert not launches
        assert (
            started["structuredContent"]["reasonCode"] == "native-execution-confirmation-required"
        )
        native_execution_client(server)
        form = execution_wire(
            server,
            "harness_worker_start",
            {key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")},
            expected_workspace=project.workspace,
        )
        separate = approve_execution_wire(server, form)["result"]
        assert not separate["isError"], separate
    server.close()
    assert not started["isError"], started
    data = started["structuredContent"]
    assert data["state"] == "held" and len(launches) == 1
    assert data["execution"]["authorizationReported"] is True
    assert data["execution"]["capacityVerified"] is False
    command, _ = launches[0]
    assert command[0][command[0].index("-m") + 1] == "codex_harness.core.planner_jobs"
    directory = project.workspace / ".codex-harness/worker-jobs" / options["expected_revision"]
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    assert request["options"] == {
        "workers": options["workers"],
        "maxParallel": 2,
        "timeout": options["timeout"],
        "callCap": options["call_cap"],
    }
    originals = {
        p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }
    resumed = cli(project.workspace, "--expected-revision", options["expected_revision"])
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    reply = json.loads(resumed.stdout)
    assert not reply["isError"] and reply["data"]["state"] == "reattached"
    assert reply["data"]["job"]["jobId"] == options["expected_revision"]
    assert reply["data"]["job"]["status"] == "unknown"
    assert reply["data"]["execution"]["authorizationReported"] is False
    assert reply["data"]["liveness"] == "unknown"
    assert reply["data"]["harnessDeliveryProven"] is False
    assert {
        p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    } == originals


@pytest.mark.parametrize("transport", ["mcp", "cli"])
def test_ready_resume_hold_reports_missing_host_admission(project, transport):
    """A ready plan cannot turn caller declarations into worker admission authority."""
    options = test_planner_resume._published(project)
    if transport == "mcp":
        server = PlannerServer(project.workspace)
        try:
            reply = rpc(server, "harness_plan_resume", expected_workspace=project.workspace)
        finally:
            server.close()
        assert not reply["isError"], reply
        data = reply["structuredContent"]
        text, next_step = reply["content"][0]["text"].rsplit("\nNext: ", 1)
        assert "request_execution=true" in next_step
    else:
        process = cli(project.workspace)
        assert process.returncode == 0, process.stdout + process.stderr
        reply = json.loads(process.stdout)
        data, text, next_step = reply["data"], reply["text"], reply["next"]
        assert " status " in next_step
    assert data["state"] == "held" and data["job"] is None
    assert "held" in text.lower()
    assert '"canonicalStatus":' not in text
    assert data["reasonCode"] == "native-execution-confirmation-required"
    assert options["expected_revision"] == data["publishedRevision"]
    assert "native execution decision" in text.lower()
    assert "worker-start" not in next_step and "execution_authorized" not in next_step
    assert not (project.workspace / ".codex-harness/worker-jobs").exists()


@pytest.mark.parametrize("transport", ["mcp", "cli"])
def test_missing_resume_hold_offers_supported_start_without_creating(tmp_path, transport):
    """A missing saved plan needs a usable entry action, not opaque refresh JSON."""
    workspace = tmp_path / "missing"
    if transport == "mcp":
        server = PlannerServer(workspace)
        try:
            reply = rpc(server, "harness_plan_resume", expected_workspace=workspace)
        finally:
            server.close()
        assert not reply["isError"], reply
        data = reply["structuredContent"]
        text, next_step = reply["content"][0]["text"].rsplit("\nNext: ", 1)
        assert "harness_plan_start" in next_step and "idea=" in next_step and "name=" in next_step
    else:
        process = cli(workspace)
        assert process.returncode == 0, process.stdout + process.stderr
        reply = json.loads(process.stdout)
        data, text, next_step = reply["data"], reply["text"], reply["next"]
        assert " start " in next_step and "--idea" in next_step and "--name" in next_step
    assert data["state"] == "held" and data["reasonCode"] == "plan-missing"
    assert "no saved plan" in text.lower()
    assert '"canonicalStatus":' not in text
    assert not workspace.exists()


def test_reattached_resume_preserves_original_when_observation_cannot_be_saved(
    project, monkeypatch
):
    """Reporting failure cannot erase a separately admitted original or cause a new launch."""
    options = test_planner_resume._published(project)
    (project.workspace / ".codex-harness/planner-resume").write_text("occupied", encoding="utf-8")
    launches = []
    with monkeypatch.context() as patch:
        patch.setattr(
            planner_jobs.subprocess,
            "Popen",
            lambda *a, **k: launches.append((a, k)) or SimpleNamespace(pid=54321),
        )
        server = PlannerServer(project.workspace)
        try:
            native_execution_client(server)
            form = execution_wire(
                server,
                "harness_worker_start",
                {
                    key: options[key]
                    for key in ("expected_revision", "workers", "timeout", "call_cap")
                },
                expected_workspace=project.workspace,
            )
            assert not approve_execution_wire(server, form)["result"]["isError"]
            reply = rpc(
                server, "harness_plan_resume", options, expected_workspace=project.workspace
            )
        finally:
            server.close()
    assert not reply["isError"], reply
    assert len(launches) == 1
    data = reply["structuredContent"]
    assert data["state"] == "reattached"
    assert data["job"]["jobId"] == options["expected_revision"]
    assert data["observationPersisted"] is False
    text = reply["content"][0]["text"]
    assert "reattached" in text.lower()
    assert "observation" in text.lower()
    assert "resume held" not in text.lower()
    assert "harness_worker_status job_id=" + options["expected_revision"] in text


@pytest.mark.parametrize("transport", ["mcp", "cli"])
def test_early_planning_resume_offers_status_before_audit_or_review(project, transport):
    """Approved early planning must continue its actual phase before publication proof."""
    test_planner_replay_gaps.confirmed_vision(project)
    assert project.phase() == "features"
    assert project.status()["open_clarifications"] == 0
    if transport == "mcp":
        server = PlannerServer(project.workspace)
        try:
            reply = rpc(server, "harness_plan_resume", expected_workspace=project.workspace)
        finally:
            server.close()
        assert not reply["isError"], reply
        data = reply["structuredContent"]
        text, next_step = reply["content"][0]["text"].rsplit("\nNext: ", 1)
        assert "harness_plan_status" in next_step
    else:
        process = cli(project.workspace)
        assert process.returncode == 0, process.stdout + process.stderr
        reply = json.loads(process.stdout)
        data, text, next_step = reply["data"], reply["text"], reply["next"]
        assert next_step.rstrip().endswith(" status")
    assert data["state"] == "held" and data["reasonCode"] == "planning-phase-required"
    assert data["nextAction"]["kind"] == "inspect_plan_phase"
    assert data["nextAction"]["phase"] == "features"
    assert "features" in text.lower()
    assert "audit" not in next_step and "review" not in next_step
    assert not (project.workspace / ".codex-harness/worker-jobs").exists()


@pytest.mark.parametrize("transport", ["mcp", "cli"])
def test_complete_resume_claims_cannot_authorize_worker_launch(project, transport, monkeypatch):
    """Caller declarations must not substitute for independent host admission authority."""
    options = test_planner_resume._published(project)
    if transport == "mcp":
        launches = []
        with monkeypatch.context() as patch:
            patch.setattr(
                planner_jobs.subprocess,
                "Popen",
                lambda *a, **k: launches.append((a, k)) or SimpleNamespace(pid=54321),
            )
            server = PlannerServer(project.workspace)
            try:
                reply = rpc(
                    server, "harness_plan_resume", options, expected_workspace=project.workspace
                )
            finally:
                server.close()
        assert not reply["isError"], reply
        assert not launches, "Caller-only resume declarations reached actual job Popen"
        data = reply["structuredContent"]
    else:
        marker = project.workspace / "unexpected-resume-launch.txt"
        arguments = [
            "--workspace",
            str(project.workspace),
            "--json",
            "resume",
            "--expected-revision",
            options["expected_revision"],
            "--workers",
            json.dumps(options["workers"]),
            "--execution-authorized",
            "--reserved-worker-calls",
            "1",
            "--deadline",
            options["deadline"],
            "--timeout",
            "5",
        ]
        # Actual separate CLI process/main, with only its dangerous child return doubled.
        script = (
            "import json,sys\nfrom pathlib import Path\nfrom types import SimpleNamespace\n"
            "from unittest.mock import patch\n"
            "from codex_harness.core.plan_cli import main\n"
            "def observed(*a, **k):\n"
            "    Path(sys.argv[2]).write_text('unexpected launch', encoding='utf-8')\n"
            "    return SimpleNamespace(pid=54321)\n"
            "with patch('codex_harness.core.planner_jobs.subprocess.Popen', observed):\n"
            "    result = main(json.loads(sys.argv[1]))\n"
            "raise SystemExit(result)\n"
        )
        process = subprocess.run(
            [sys.executable, "-B", "-c", script, json.dumps(arguments), str(marker)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        assert not marker.exists(), "Caller-only CLI declarations reached actual job Popen"
        data = json.loads(process.stdout)["data"]
    assert (
        data["state"] == "held" and data["reasonCode"] == "native-execution-confirmation-required"
    )
    assert data["execution"]["authorizationReported"] is True
    assert data["execution"]["authorizationVerified"] is False
    assert data["execution"]["capacityVerified"] is False
    assert data["job"] is None
    assert not (project.workspace / ".codex-harness/worker-jobs").exists()


def execution_wire(server, name, arguments, request_id=81, *, expected_workspace=None):
    """Bind this synthetic client to its known test project, not inferred host identity."""
    if expected_workspace is not None:
        arguments = {"expected_workspace": str(expected_workspace), **arguments}
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
    )


def native_execution_client(server):
    server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 80,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}},
            }
        )
    )
    server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))


def approve_execution_wire(server, form, *, action="accept", conditions=""):
    assert form.get("method") == "elicitation/create", form
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": form["id"],
                "result": {
                    "action": action,
                    "content": {
                        "approve": True,
                        "statement": "SOURCE-ONLY synthetic native execution decision",
                        "conditions": conditions,
                    },
                },
            }
        )
    )


@pytest.mark.parametrize("tool", ["harness_worker_start", "harness_plan_resume"])
def test_explicit_execution_request_requires_actual_native_wire_before_launch(
    project, tool, monkeypatch
):
    """A supported explicit execution request displays its own native decision before work."""
    options = test_planner_resume._published(project)
    arguments = {key: options[key] for key in ("expected_revision", "workers", "timeout")}
    arguments["call_cap"] = 1
    if tool == "harness_plan_resume":
        arguments["request_execution"] = True
    launches = []
    with monkeypatch.context() as patch:
        patch.setattr(
            planner_jobs.subprocess,
            "Popen",
            lambda *a, **k: launches.append(1) or SimpleNamespace(pid=54321),
        )
        server = PlannerServer(project.workspace)
        native_execution_client(server)
        try:
            response = execution_wire(server, tool, arguments, expected_workspace=project.workspace)
            assert response.get("method") == "elicitation/create", response
            assert "worker" in response["params"]["message"].lower()
            assert options["expected_revision"] in response["params"]["message"]
            assert not launches
        finally:
            server.close()


def test_direct_worker_start_without_native_original_refuses_before_process(project, monkeypatch):
    """Bypassing handle_line cannot turn caller arguments into native execution approval."""
    options = test_planner_resume._published(project)
    launches = []
    with monkeypatch.context() as patch:
        patch.setattr(
            planner_jobs.subprocess,
            "Popen",
            lambda *a, **k: launches.append(1) or SimpleNamespace(pid=54321),
        )
        server = PlannerServer(project.workspace)
        try:
            reply = server.call_tool(
                "harness_worker_start",
                {
                    key: options[key]
                    for key in ("expected_revision", "workers", "timeout", "call_cap")
                },
            )
        finally:
            server.close()
    assert not launches, "Direct worker-start reached Popen without a native execution original"
    assert reply["isError"], reply
    assert "worker-execution-grant-required" in reply["content"][0]["text"]


@pytest.mark.parametrize("tool", ["harness_worker_start", "harness_plan_resume"])
def test_native_execution_wire_approves_exact_proposal_once_then_reattaches(
    project, tool, monkeypatch
):
    """Actual correlated native response admits once; this synthetic client is not a human."""
    options = test_planner_resume._published(project)
    arguments = {
        key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")
    }
    arguments["max_parallel"] = 1
    if tool == "harness_plan_resume":
        arguments["request_execution"] = True
    launches = []
    with monkeypatch.context() as patch:
        patch.setattr(
            planner_jobs.subprocess,
            "Popen",
            lambda *a, **k: launches.append((a, k)) or SimpleNamespace(pid=54321),
        )
        server = PlannerServer(project.workspace)
        native_execution_client(server)
        form = execution_wire(server, tool, arguments, expected_workspace=project.workspace)
        assert form.get("method") == "elicitation/create", form
        assert not launches and not (project.workspace / ".codex-harness/worker-jobs").exists()
        forged = {
            "jsonrpc": "2.0",
            "id": "unknown-native-wire",
            "result": {
                "action": "accept",
                "content": {"approve": True, "statement": "quoted", "conditions": ""},
            },
        }
        assert server.handle_line(json.dumps(forged)) is None
        assert not launches
        reply = approve_execution_wire(server, form)
        assert reply["id"] == 81 and not reply["result"]["isError"], reply
        approval = reply["result"]["_meta"]["executionConfirmation"]
        assert approval["state"] == "approved" and approval["human_identity_verified"] is False
        assert approval["scope"]["workers"] == options["workers"]
        assert approval["scope"]["publicationRevision"] == options["expected_revision"]
        assert approval["scope"]["callCap"] == 1 and approval["scope"]["maxParallel"] == 1
        assert len(launches) == 1
        assert approve_execution_wire(server, form) is None  # Replay is not a new tool invocation.
        assert len(launches) == 1
        changed = {**arguments, "timeout": arguments["timeout"] + 1}
        refused = execution_wire(server, tool, changed, 82, expected_workspace=project.workspace)
        assert (
            refused["result"]["isError"]
            or refused["result"]["structuredContent"]["state"] == "held"
        )
        assert len(launches) == 1
        server.close()
    resumed = cli(project.workspace)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    data = json.loads(resumed.stdout)["data"]
    assert data["state"] == "reattached" and data["job"]["jobId"] == options["expected_revision"]
    assert data["execution"]["capacityVerified"] is False
    assert data["liveness"] == "unknown" and data["harnessDeliveryProven"] is False


@pytest.mark.parametrize(
    "case", ["cancel", "decline", "conditions", "stale", "timeout", "disconnect", "foreign-content"]
)
def test_native_execution_nonapproval_never_admits_or_replays(project, monkeypatch, case):
    """Only an unconditional current correlated decision can become a new job."""
    options = test_planner_resume._published(project)
    arguments = {
        key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")
    }
    arguments["request_execution"] = True
    launches = []
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *a, **k: launches.append(1) or SimpleNamespace(pid=54321),
    )
    server = PlannerServer(project.workspace, confirmation_timeout=0 if case == "timeout" else 120)
    native_execution_client(server)
    form = execution_wire(
        server, "harness_plan_resume", arguments, expected_workspace=project.workspace
    )
    assert form.get("method") == "elicitation/create", form
    if case == "stale":
        node = project.spine().node("R1")
        node["text"] += " changed after native execution proposal"
        project.upsert_node(node)
    if case == "disconnect":
        server.close()
        server = PlannerServer(project.workspace)
        assert approve_execution_wire(server, form) is None
    elif case == "foreign-content":
        reply = server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": form["id"],
                    "result": {
                        "action": "accept",
                        "content": {
                            "approve": True,
                            "statement": "quoted",
                            "conditions": "",
                            "scope": {"approved": True},
                        },
                    },
                }
            )
        )
        assert reply["result"]["isError"]
    else:
        reply = approve_execution_wire(
            server,
            form,
            action=case if case in {"cancel", "decline"} else "accept",
            conditions="Only if something changes" if case == "conditions" else "",
        )
        assert reply["result"]["isError"], reply
        assert approve_execution_wire(server, form) is None
    server.close()
    assert not launches and not (project.workspace / ".codex-harness/worker-jobs").exists()


def test_native_resume_preserves_actual_job_when_optional_observation_is_blocked(
    project, monkeypatch
):
    """A post-admission metadata failure cannot erase the admitted job or invite retry."""
    options = test_planner_resume._published(project)
    arguments = {
        key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")
    }
    arguments["request_execution"] = True
    launches = []
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *a, **k: launches.append(1) or SimpleNamespace(pid=54321),
    )
    server = PlannerServer(project.workspace)
    native_execution_client(server)
    form = execution_wire(
        server, "harness_plan_resume", arguments, expected_workspace=project.workspace
    )
    observation_dir = project.workspace / ".codex-harness/planner-resume"
    # The initial inspection may have observations: block the exact next UUID
    # writer by replacing only the emptyable observation directory with a file.
    for path in observation_dir.iterdir():
        path.unlink()
    observation_dir.rmdir()
    observation_dir.write_text("SOURCE-ONLY optional reporting obstruction", encoding="utf-8")
    reply = approve_execution_wire(server, form)
    server.close()
    assert not reply["result"]["isError"], reply
    data = reply["result"]["structuredContent"]
    assert (
        data["state"] == "handoff_requested"
        and data["observationError"] == "observation-write-failed"
    )
    assert data["job"]["jobId"] == options["expected_revision"] and len(launches) == 1
    assert "harness_worker_status job_id=" in reply["result"]["content"][0]["text"]


def test_native_resume_card_failure_retains_admitted_job_and_query_route(project, monkeypatch):
    """Rendering cannot erase an admitted job or grant a retry."""
    from codex_harness.core import mcp_planner

    options = test_planner_resume._published(project)
    arguments = {
        key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")
    }
    arguments["request_execution"] = True
    launches = []
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *a, **k: launches.append(1) or SimpleNamespace(pid=54321),
    )
    server = PlannerServer(project.workspace)
    native_execution_client(server)
    form = execution_wire(
        server, "harness_plan_resume", arguments, expected_workspace=project.workspace
    )
    with monkeypatch.context() as patch:

        def broken_card(data):
            raise ValueError("SOURCE-ONLY renderer failure")

        patch.setattr(mcp_planner, "resume_card", broken_card)
        reply = approve_execution_wire(server, form)
    server.close()
    assert reply["result"]["isError"]
    data = reply["result"]["structuredContent"]
    assert (
        data["state"] == "handoff_requested"
        and data["job"]["jobId"] == options["expected_revision"]
    )
    assert len(launches) == 1
    assert "harness_worker_status job_id=" in reply["result"]["content"][0]["text"]
