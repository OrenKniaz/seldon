"""Actual persisted planner transports; no provider or human-approval proof."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.core import test_planner_resume

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


@pytest.mark.parametrize("transport", ["cli"])
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
                        "statement": "Approve",
                        "conditions": conditions,
                    },
                },
            }
        )
    )


@pytest.mark.parametrize("tool", ["harness_plan_resume"])
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


@pytest.mark.parametrize("case", ["decline", "stale"])
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


def test_native_resume_card_failure_retains_admitted_job_and_query_route(project, monkeypatch):
    """Rendering cannot erase an admitted job or its read-only query route.

    Catch generic uncertain-action advice hiding an already persisted original;
    ordinary successful-resume tests miss the post-admission renderer failure.
    """
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
    assert reply["result"]["_meta"]["executionConfirmation"]["human_identity_verified"] is False
    original_dir = Path(
        planner_jobs.worker_job_status(project.workspace, data["job"]["jobId"])["evidenceDir"]
    )
    originals = {
        p.relative_to(original_dir): p.read_bytes() for p in original_dir.rglob("*") if p.is_file()
    }
    request = json.loads((original_dir / "request.json").read_text(encoding="utf-8"))
    assert request["executionRequestId"]
    assert request["options"]["workers"] == options["workers"]
    # Reopen through the public route named by the failed response, not a copied result.
    reopened = PlannerServer(project.workspace)
    try:
        queried = rpc(
            reopened,
            "harness_worker_status",
            {"job_id": data["job"]["jobId"], "wait_seconds": 0},
            expected_workspace=project.workspace,
        )
        assert not queried["isError"], queried
        observed = queried["structuredContent"]
        assert observed["jobId"] == options["expected_revision"]
        assert observed["publicationRevision"] == options["expected_revision"]
        assert observed["options"] == request["options"]
        assert observed["nativeApproval"] == "recorded"
        assert observed["status"] == observed["liveness"] == "unknown"
        assert observed["harnessDeliveryProven"] is False
        assert approve_execution_wire(reopened, form) is None
        assert len(launches) == 1
        assert {
            p.relative_to(original_dir): p.read_bytes()
            for p in original_dir.rglob("*")
            if p.is_file()
        } == originals
    finally:
        reopened.close()
