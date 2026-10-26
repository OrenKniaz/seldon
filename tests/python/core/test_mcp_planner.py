"""Real MCP -> PlanStore -> spine -> export integration, without a provider."""

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from tests.python.core import test_test_audit
from tests.python.core.planning_support import publication_evidence
from tests.python.core.test_plan_store import draft_inputs, draft_intake, mock_catalog, mock_choice
from tests.python.core.test_preflight import inject_mock_discovery

from codex_harness.core.mcp_planner import PHASE_DIAGRAMS, TOOLS, PlannerServer
from codex_harness.core.plan_store import PHASES, open_plan_store
from codex_harness.core.planner_views import banner, chat_diagram
from codex_harness.core.spine import MARKDOWN_FILES, load_spine

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit" / "spine.json"
revision_proof = test_test_audit.prospective


def workspace_wire(server, tool_name, **arguments):
    """Actual unscoped wire; never fill missing caller intent from the server."""
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 901,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        )
    )["result"]


def workspace_files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_public_ordinary_reviewer_choice_confirmation_is_durable_and_scoped(tmp_path, monkeypatch):
    """Simulated catalog/tester action; real public wire/store, no identity/provider proof."""
    from codex_harness.core.intake import canonical_digest
    from codex_harness.core.plan_store import open_plan_store_readonly

    def no_launch(*args, **kwargs):
        raise AssertionError("Ordinary confirmation must not start a provider or process")

    monkeypatch.setattr(subprocess, "Popen", no_launch)
    server = PlannerServer(tmp_path)
    trace = []

    def invoke(tool_name, **arguments):
        reply = workspace_wire(server, tool_name, expected_workspace=str(tmp_path), **arguments)
        trace.append({"tool": tool_name, "arguments": arguments, "response": reply})
        assert not reply["isError"], reply
        return reply["structuredContent"]

    try:
        invoke("harness_plan_start", idea="Explicit reviewer choice", name="Tester action")
        catalog = invoke(
            "harness_reviewer_catalog",
            catalog=mock_catalog(),
            expected_spine_revision=server.store.spine().plan_revision(),
        )["catalog"]
        sessions = {"implementationSessionIds": ["simulated-author"], "reviewerSessionIds": {}}
        node = mock_choice(catalog)
        selected = invoke(
            "harness_reviewer_choice",
            node=node,
            author_sessions=sessions,
            expected_spine_revision=server.store.spine().plan_revision(),
        )
        stored_before = server.store.spine().node(node["id"])
        assert stored_before["fields"]["confirmation"] is None
        assert callable(server.store.confirm_reviewer_selection)  # Frozen leaf already exists.
        guide = invoke("harness_schema", tool="harness_confirm_reviewer_choice")
        schema = guide["inputSchema"]
        assert set(schema["required"]) == {
            "node_id",
            "expected_spine_revision",
            "approve",
            "statement",
            "conditions",
            "expected_workspace",
        }
        assert set(schema["properties"]) == set(schema["required"])
        assert schema["additionalProperties"] is False
        statement = "Tester chooses exactly this recorded proposal.\nRetain all words and spaces.  "
        result = invoke(
            "harness_confirm_reviewer_choice",
            node_id=node["id"],
            expected_spine_revision=selected["spineRevision"],
            approve=True,
            statement=statement,
            conditions="",
        )
        record, authority = result["confirmationRecord"], result["selection"]["confirmation"]
        expected_scope = canonical_digest(
            {
                "node": stored_before,
                "authorSessions": sessions,
                "catalog": {"id": catalog["id"], "digest": catalog["digest"]},
            }
        )
        assert record["scopeDigest"] == authority["authorizedScopeDigest"] == expected_scope
        assert record["preimageRevision"] == selected["spineRevision"]
        assert result["spineRevision"] != selected["spineRevision"]
        assert record["statement"] == authority["statement"] == statement
        assert record["human_identity_verified"] is False
        assert record["provenance"] == "caller-declared-user-action"
        assert result["approvalGranted"] is False and result["lockState"] == "unconfirmed"
        assert result["unmetRequirements"] == [
            hold
            for hold in selected["unmetRequirements"]
            if not hold.startswith("reviewer-confirmation-required:")
        ]
        assert any("independence-unknown" in hold for hold in result["unmetRequirements"])
        assert any("cross-provider" in hold for hold in result["unmetRequirements"])
        projected = invoke("harness_waves")["reviewerSelection"]
        assert projected["selection"] == result["selection"]
        assert projected["unmetRequirements"] == result["unmetRequirements"]
        server.close()
        with open_plan_store_readonly(tmp_path) as persisted:
            rows = persisted.connection.execute(
                "SELECT id,payload_json FROM events WHERE kind='reviewer-selection-confirmed'"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0][0] == authority["eventId"] == record["eventId"]
            payload = json.loads(rows[0][1])
            assert payload == {key: value for key, value in record.items() if key != "eventId"}
            assert authority["sourceRef"] == f"reviewer-confirmation:event:{rows[0][0]}"
            assert authority["approvalId"] == payload["recordId"]
            assert persisted.spine().node(node["id"])["fields"]["confirmation"] == authority
            assert persisted.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
        server = PlannerServer(tmp_path)
        assert invoke("harness_waves")["reviewerSelection"] == projected
        print(json.dumps({"sourceOnly": True, "simulatedCatalog": catalog, "wire": trace}))
    finally:
        server.close()


def test_wire_workspace_missing_intent_cannot_resume_other_project(tmp_path, monkeypatch):
    from codex_harness.core import mcp_planner

    habits, tally = tmp_path / "habits", tmp_path / "tally"
    for project in (habits, tally):
        project.mkdir()
        seed = PlannerServer(project)
        assert not seed.call_tool(
            "harness_plan_start", {"idea": project.name, "name": project.name}
        )["isError"]
        seed.close()
    before = {str(p): workspace_files(p) for p in (habits, tally)}
    opens, providers = [], []
    original = mcp_planner.open_plan_store

    def observed_open(*args, **kwargs):
        opens.append(args)
        return original(*args, **kwargs)

    def no_provider(*args, **kwargs):
        providers.append(args)
        raise AssertionError("No provider may launch for a missing workspace assertion")

    monkeypatch.setattr(mcp_planner, "open_plan_store", observed_open)
    monkeypatch.setattr(subprocess, "Popen", no_provider)
    cached, other = PlannerServer(habits), PlannerServer(tally)
    try:
        reply = workspace_wire(cached, "harness_plan_start", idea="Tally", name="Tally")
        print(json.dumps({"missingIntentResponse": reply, "openedStores": len(opens)}))
        assert reply["isError"], reply  # Baseline silently resumes the bound Habits plan.
        assert "workspace-assertion-required" in reply["content"][0]["text"]
        assert reply["workspaceBinding"]["serverWorkspace"] == str(habits.resolve())
        assert reply["workspaceBinding"]["expectedWorkspace"] is None
        assert not opens and not providers and cached.store is other.store is None
        assert {str(p): workspace_files(p) for p in (habits, tally)} == before
    finally:
        cached.close()
        other.close()


def public_test_reviewer_choice(server):
    """Source-only supplied catalog facts and sessions; no actual discovery."""

    def invoke(tool_name, **arguments):
        reply = workspace_wire(
            server, tool_name, expected_workspace=str(server.workspace), **arguments
        )
        assert not reply["isError"], reply
        return reply["structuredContent"]

    invoke("harness_plan_start", idea="Public action controls", name="Test choice")
    catalog = invoke(
        "harness_reviewer_catalog",
        catalog=mock_catalog(),
        expected_spine_revision=server.store.spine().plan_revision(),
    )["catalog"]
    node = mock_choice(catalog)
    selected = invoke(
        "harness_reviewer_choice",
        node=node,
        expected_spine_revision=server.store.spine().plan_revision(),
    )
    return node, selected, catalog


@pytest.mark.parametrize(
    "case",
    ["negative", "conditional", "malformed", "stale", "catalog", "replay", "proposal", "sessions"],
)
def test_public_ordinary_choice_refuses_invalid_or_replayed_action_without_credit(tmp_path, case):
    server = PlannerServer(tmp_path)
    try:
        node, selected, _ = public_test_reviewer_choice(server)
        arguments = dict(
            node_id=node["id"],
            expected_spine_revision=selected["spineRevision"],
            approve=True,
            statement="Explicit simulated tester action",
            conditions="",
        )
        tool_name = "harness_confirm_reviewer_choice"
        if case in ("replay", "proposal", "sessions"):
            confirmed = workspace_wire(
                server, tool_name, expected_workspace=str(tmp_path), **arguments
            )
            assert not confirmed["isError"], confirmed
            arguments["expected_spine_revision"] = confirmed["structuredContent"]["spineRevision"]
            if case == "proposal":
                proposal = server.store.spine().node(node["id"])
                proposal["fields"]["recommendation"]["reason"] += " changed"
                changed = workspace_wire(
                    server, "harness_upsert_node", expected_workspace=str(tmp_path), node=proposal
                )
                assert not changed["isError"]
                arguments["expected_spine_revision"] = server.store.spine().plan_revision()
                consumer = workspace_wire(server, "harness_waves", expected_workspace=str(tmp_path))
                assert consumer["structuredContent"]["reviewerSelection"]["state"] == "stale"
                assert "selection" not in consumer["structuredContent"]["reviewerSelection"]
            elif case == "sessions":
                tool_name = "harness_reviewer_choice"
                arguments = dict(
                    node=server.store.spine().node(node["id"]),
                    expected_spine_revision=arguments["expected_spine_revision"],
                    author_sessions={
                        "implementationSessionIds": [],
                        "reviewerSessionIds": {"primary": "new-simulated-session"},
                    },
                )
        elif case == "negative":
            arguments["approve"] = False
        elif case == "conditional":
            arguments["conditions"] = "Only if unknown availability becomes verified"
        elif case == "malformed":
            arguments["approve"] = "true"
        elif case == "stale":
            arguments["expected_spine_revision"] = "0" * 64
        else:
            replacement = mock_catalog()
            replacement["id"] = "RC-00000000-0000-0000-0000-000000000002"
            changed = workspace_wire(
                server,
                "harness_reviewer_catalog",
                expected_workspace=str(tmp_path),
                catalog=replacement,
                expected_spine_revision=selected["spineRevision"],
            )
            assert not changed["isError"]
            assert server.store.spine().plan_revision() == selected["spineRevision"]
        before = list(server.store.connection.iterdump())
        refused = workspace_wire(server, tool_name, expected_workspace=str(tmp_path), **arguments)
        assert refused["isError"], refused
        assert list(server.store.connection.iterdump()) == before
        assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
        records = server.store.connection.execute(
            "SELECT count(*) FROM events WHERE kind='reviewer-selection-confirmed'"
        ).fetchone()[0]
        assert records == (1 if case in ("replay", "proposal", "sessions") else 0)
    finally:
        server.close()


@pytest.mark.parametrize(
    "field",
    [
        "by",
        "origin",
        "approvalId",
        "eventId",
        "sourceRef",
        "authorizedScopeDigest",
        "human_identity_verified",
    ],
)
def test_public_ordinary_choice_rejects_authority_injection(tmp_path, field):
    server = PlannerServer(tmp_path)
    try:
        node, selected, _ = public_test_reviewer_choice(server)
        before = list(server.store.connection.iterdump())
        result = workspace_wire(
            server,
            "harness_confirm_reviewer_choice",
            expected_workspace=str(tmp_path),
            node_id=node["id"],
            expected_spine_revision=selected["spineRevision"],
            approve=True,
            statement="Simulated action",
            conditions="",
            **{field: "injected"},
        )
        assert result["isError"], result
        assert list(server.store.connection.iterdump()) == before
        assert server.store.spine().node(node["id"])["fields"]["confirmation"] is None
    finally:
        server.close()


def test_public_ordinary_choice_workspace_refusal_precedes_store_and_provider(
    tmp_path, monkeypatch
):
    from codex_harness.core import mcp_planner

    bound, intended = tmp_path / "bound", tmp_path / "intended"
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Wrong/missing intent must refuse before store or provider")

    monkeypatch.setattr(mcp_planner, "open_plan_store", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    for workspace, supplied in ((bound, None), (bound, str(intended)), (intended, str(bound))):
        server = PlannerServer(workspace)
        try:
            context = {} if supplied is None else {"expected_workspace": supplied}
            reply = workspace_wire(server, "harness_confirm_reviewer_choice", **context)
            assert reply["isError"]
            expected = "workspace-assertion-required" if supplied is None else "workspace-mismatch"
            assert expected in reply["content"][0]["text"]
            assert server.store is None and server._pending_confirmation is None
            assert not calls and not bound.exists() and not intended.exists()
        finally:
            server.close()


def test_public_ordinary_choice_two_bound_projects_keep_distinct_records(tmp_path):
    servers = [PlannerServer(tmp_path / name) for name in ("first", "second")]
    records = []
    try:
        for server in servers:
            server.workspace.mkdir()
            node, selected, _ = public_test_reviewer_choice(server)
            result = workspace_wire(
                server,
                "harness_confirm_reviewer_choice",
                expected_workspace=str(server.workspace),
                node_id=node["id"],
                expected_spine_revision=selected["spineRevision"],
                approve=True,
                statement=f"Simulated tester selects {server.workspace.name} proposal",
                conditions="",
            )
            assert not result["isError"], result
            assert result["workspaceBinding"]["serverWorkspace"] == str(server.workspace)
            assert result["workspaceBinding"]["match"] == "matched"
            record = result["structuredContent"]["confirmationRecord"]
            records.append(record["recordId"])
            assert (
                server.store.spine().node(node["id"])["fields"]["confirmation"]["approvalId"]
                == record["recordId"]
            )
            assert result["structuredContent"]["approvalGranted"] is False
        assert len(set(records)) == 2
    finally:
        for server in servers:
            server.close()


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("harness_plan_start", {"idea": "Tally", "name": "Tally"}),
        ("harness_worker_start", {}),
        ("harness_plan_resume", {"request_execution": True}),
        ("harness_record_confirmation", {"gate": "align"}),
        (
            "harness_mail_send",
            {"from_agent": "user", "to_agent": "planner", "subject": "x", "body": "x"},
        ),
    ],
)
def test_wire_workspace_refuses_before_store_or_native_interception(
    tmp_path, monkeypatch, tool, arguments
):
    from codex_harness.core import mcp_planner

    bound, expected = tmp_path / "bound", tmp_path / "expected"
    server = PlannerServer(bound)
    opened, providers = [], []

    def forbidden_open(*args, **kwargs):
        opened.append(args)
        raise AssertionError("Workspace refusal must precede store access")

    def forbidden_provider(*args, **kwargs):
        providers.append(args)
        raise AssertionError("Workspace refusal must precede provider admission")

    monkeypatch.setattr(mcp_planner, "open_plan_store", forbidden_open)
    monkeypatch.setattr(subprocess, "Popen", forbidden_provider)
    try:
        for supplied, code in (
            (None, "workspace-assertion-required"),
            (str(expected), "workspace-mismatch"),
            ("relative", "workspace-assertion-invalid"),
        ):
            context = {} if supplied is None else {"expected_workspace": supplied}
            reply = workspace_wire(server, tool, **arguments, **context)
            assert reply["isError"] and code in reply["content"][0]["text"], reply
            assert reply["workspaceBinding"]["clientWorkspaceVerified"] is False
            assert (
                str(bound) in reply["content"][0]["text"]
                and str(supplied) in reply["content"][0]["text"]
            )
            assert server.store is None and server._pending_confirmation is None
            assert not bound.exists() and not expected.exists() and not opened and not providers
    finally:
        server.close()


def test_wire_workspace_correct_projects_and_stale_connection(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    servers = [PlannerServer(first), PlannerServer(second)]
    try:
        for project, server in zip((first, second), servers, strict=True):
            project.mkdir()
            result = workspace_wire(
                server,
                "harness_plan_start",
                idea=project.name,
                name=project.name,
                expected_workspace=str(project / "unused" / ".."),
            )
            assert not result["isError"], result
            assert result["workspaceBinding"]["match"] == "matched"
            assert result["workspaceBinding"]["clientWorkspaceVerified"] is False
        before = {str(p): workspace_files(p) for p in (first, second)}
        # Editing/reloading an external config cannot retarget either existing fixed server.
        config = tmp_path / "connection-config.json"
        config.write_text(json.dumps({"workspace": str(second)}), encoding="utf-8")
        for cached in (servers[0], PlannerServer(first)):
            try:
                refused = workspace_wire(
                    cached,
                    "harness_plan_start",
                    idea="Second",
                    name="Second",
                    expected_workspace=json.loads(config.read_text())["workspace"],
                )
                assert refused["isError"] and "workspace-mismatch" in refused["content"][0]["text"]
                assert cached.workspace == first
            finally:
                if cached is not servers[0]:
                    cached.close()
        assert {str(p): workspace_files(p) for p in (first, second)} == before
        for project, server in zip((first, second), servers, strict=True):
            status = workspace_wire(server, "harness_plan_status", expected_workspace=str(project))
            assert not status["isError"] and status["structuredContent"]["name"] == project.name
            resumed = workspace_wire(
                server,
                "harness_plan_start",
                idea="Existing intent",
                name="Resume",
                expected_workspace=str(project),
            )
            assert not resumed["isError"]
            assert server._status()["name"] == project.name
    finally:
        for server in servers:
            server.close()


def test_wire_workspace_cold_discovery_and_schema_without_store(tmp_path):
    workspace = tmp_path / "missing-plan"
    server = PlannerServer(workspace)
    try:
        status = workspace_wire(server, "harness_plan_status")
        assert not status["isError"], status
        assert status["workspaceBinding"]["serverWorkspace"] == str(workspace)
        assert status["workspaceBinding"]["match"] == "not-supplied"
        assert status["workspaceBinding"]["clientWorkspaceVerified"] is False
        listed = rpc(server, "tools/list")["result"]["tools"]
        for tool in listed:
            required = tool["inputSchema"]["required"]
            assert ("expected_workspace" in required) is (
                tool["name"] not in {"harness_schema", "harness_preflight", "harness_plan_status"}
            )
        schema = workspace_wire(server, "harness_schema", tool="harness_plan_start")
        assert "expected_workspace" in schema["structuredContent"]["inputSchema"]["required"]
        assert "unauthenticated" in schema["content"][0]["text"]
        assert server.store is None and not workspace.exists()
    finally:
        server.close()


def test_wire_workspace_preserves_explicit_cli_producer(tmp_path, capsys):
    from codex_harness.core.plan_cli import main

    project = tmp_path / "cli-project"
    project.mkdir()
    assert (
        main(
            [
                "--workspace",
                str(project),
                "--json",
                "start",
                "--idea",
                "Explicit CLI",
                "--name",
                "CLI",
            ]
        )
        == 0
    )
    assert not json.loads(capsys.readouterr().out)["isError"]
    assert main(["--workspace", str(project), "--json", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["name"] == "CLI"


def test_wire_workspace_existing_discovery_never_opens_writable_store(tmp_path, monkeypatch):
    from codex_harness.core import mcp_planner

    seed = PlannerServer(tmp_path)
    assert not seed.call_tool("harness_plan_start", {"idea": "Actual plan", "name": "Existing"})[
        "isError"
    ]
    expected = seed._status()
    seed.close()
    before = workspace_files(tmp_path)
    opened = []
    original = mcp_planner.open_plan_store

    def observed_writable(*args, **kwargs):
        opened.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(mcp_planner, "open_plan_store", observed_writable)
    server = PlannerServer(tmp_path)
    try:
        status = workspace_wire(server, "harness_plan_status")
        assert not status["isError"], status
        for field in ("name", "phase", "plan_revision", "pending_gate"):
            assert status["structuredContent"][field] == expected[field]
        schema = workspace_wire(server, "harness_schema", tool="harness_resolve_findings")
        assert not schema["isError"] and "closureExample" in schema["structuredContent"]
        assert not opened, "Unscoped discovery must not initialize or migrate the bound store"
        assert server.store is None and workspace_files(tmp_path) == before
    finally:
        server.close()


def test_public_execution_assessment_schema_and_choices_do_not_create_plan(tmp_path):
    workspace = tmp_path / "not-started"
    server = PlannerServer(workspace)
    reply = server.call_tool("harness_accept_assess", {"schema": True})
    assert not reply.get("isError"), reply
    contract = reply["structuredContent"]
    tool = next(item for item in TOOLS if item["name"] == "harness_accept_assess")
    assert contract["inputSchema"] == tool["inputSchema"]
    assert len(contract["inputSchema"]["oneOf"]) == 3
    held = server.call_tool("harness_accept_assess", {"prepare": {}})
    assert held["isError"]
    assert "harness_plan_start" in held["content"][0]["text"]
    assert not workspace.exists()


EXPECTED_TOOLS = {
    "harness_schema",
    "harness_preflight",
    "harness_intake",
    "harness_reviewer_catalog",
    "harness_reviewer_choice",
    "harness_confirm_reviewer_choice",
    "harness_wave_map",
    "harness_waves",
    "harness_review",
    "harness_review_policy",
    "harness_adopt_review_policy",
    "harness_readiness",
    "harness_compile",
    "harness_packet_view",
    "harness_show_node",
    "harness_revise_test",
    "harness_nodes",
    "harness_resolve_finding",
    "harness_resolve_findings",
    "harness_accept_scenarios",
    "harness_accept_run",
    "harness_accept_rebind",
    "harness_accept_review",
    "harness_accept_assess",
    "harness_accept",
    "harness_mail_send",
    "harness_mail_inbox",
    "harness_mail_ack",
    "harness_mail_reply",
    "harness_plan_start",
    "harness_plan_status",
    "harness_understanding_card",
    "harness_record_confirmation",
    "harness_advance",
    "harness_upsert_node",
    "harness_upsert_nodes",
    "harness_roundtable",
    "harness_audit",
    "harness_audit_status",
    "harness_remove_node",
    "harness_ask",
    "harness_answer",
    "harness_add_assumption",
    "harness_resolve_assumption",
    "harness_decision_card",
    "harness_diagram",
    "harness_impact",
    "harness_render",
    "harness_publish",
    "harness_worker_start",
    "harness_worker_status",
    "harness_plan_resume",
}


@pytest.mark.parametrize(
    "arguments",
    [
        {"schema": True, "prepare": {}},
        {"schema": False},
        {"prepare": {"extra": True}},
        {"prepare": {"finding_ids": []}},
        {"request": {"members": []}},
        {"extra": True},
    ],
)
def test_public_assessment_invalid_modes_teach_contract_without_writes(tmp_path, arguments):
    workspace = tmp_path / "absent"
    reply = PlannerServer(workspace).call_tool("harness_accept_assess", arguments)
    assert reply["isError"]
    assert "Owning execution assessment contract" in reply["content"][0]["text"]
    assert reply["structuredContent"]["contract"]["inputSchema"] == next(
        row["inputSchema"] for row in TOOLS if row["name"] == "harness_accept_assess"
    )
    assert not workspace.exists()


def test_accept_phase_status_teaches_current_assessment_without_mutation(tmp_path, monkeypatch):
    import hashlib

    from tests.python.core.test_acceptance import (
        public_assessment_repair_fixture,
        public_current_support,
    )

    fixture = public_assessment_repair_fixture(tmp_path, monkeypatch)
    store, server, tool, calls, af, current, repo, reviewers, evidence = fixture
    try:
        tool("harness_advance", {"to_phase": "execute"})
        tool("harness_advance", {"to_phase": "accept"})
        _, selected = public_current_support(fixture)

        def artifacts():
            return {
                str(path.relative_to(store.workspace)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in store.workspace.rglob("*")
                if path.is_file()
                and ".git" not in path.parts
                and not path.name.startswith("plan.sqlite3")
            }

        before_db = list(store.connection.iterdump())
        before_files = artifacts()
        before_support = store.execution_assessment_state()
        reply = server.call_tool("harness_plan_status", {})
        assert not reply["isError"]
        acceptance = reply["structuredContent"]["acceptance"]
        assert reply["structuredContent"]["phase"] == "accept"
        assert acceptance["accepted"] is False
        assert acceptance["invalidReason"] == "acceptance-findings-open"
        assert acceptance["executionAssessments"]["members"][0]["registrationId"] == selected
        next_action = reply["content"][0]["text"].rsplit("\nNext: ", 1)[1]
        assert next_action.startswith("harness_accept_assess schema=true;"), next_action
        assert "before fresh assessment" in next_action
        assert "stales" in next_action
        name, field = next_action.split(";", 1)[0].split()
        key, value = field.split("=", 1)
        schema = tool(name, {key: json.loads(value)})
        assert schema["closureEligible"] is False
        name, field = schema["acquisition"]["choices"].split(" / ", 1)[0].split()
        key, value = field.split("=", 1)
        prepared = tool(name, {key: json.loads(value)})
        assert prepared["state"] == "selection_required"
        assert af["id"] in {row["id"] for row in prepared["choices"]["findings"]}
        assert list(store.connection.iterdump()) == before_db
        assert artifacts() == before_files
        assert store.execution_assessment_state() == before_support
        assert len(calls) == 3
    finally:
        store.close()


@pytest.mark.parametrize("case", ["closed", "open", "one-provider-error"])
def test_review_continuation_uses_current_gate_with_retained_afs(tmp_path, monkeypatch, case):
    import hashlib

    from tests.python.core.test_acceptance import (
        public_assessment_repair_fixture,
        public_current_support,
    )

    fixture = public_assessment_repair_fixture(tmp_path, monkeypatch)
    store, server, tool, calls, af, current, repo, reviewers, evidence = fixture
    try:
        tool("harness_advance", {"to_phase": "execute"})
        tool("harness_advance", {"to_phase": "accept"})
        originals = store.acceptance_findings()
        if case != "open":
            for finding in originals:
                tool(
                    "harness_resolve_finding",
                    dict(
                        id=finding["id"],
                        status="resolved",
                        resolution=(
                            "SOURCE ONLY declaration before fresh assessment; "
                            "actual worker repaired bool IDs"
                        ),
                    ),
                )
        _, selected = public_current_support(fixture)
        declared = store.acceptance_findings()
        reply = server.call_tool(
            "harness_accept_review",
            dict(
                repo=str(repo),
                execution_assessment_ids=[selected],
                reviewers=reviewers[:1] if case == "one-provider-error" else reviewers,
            ),
        )
        status = store.status()
        acceptance = status["acceptance"]
        assert acceptance["accepted"] is False
        assert store.acceptance_findings() == declared
        assert [
            {k: v for k, v in row.items() if k not in {"status", "resolution"}} for row in declared
        ] == [
            {k: v for k, v in row.items() if k not in {"status", "resolution"}} for row in originals
        ]
        next_step = reply["content"][0]["text"].rsplit("\nNext: ", 1)[1]
        (evidence / "continuation-before-action.json").write_text(
            json.dumps(
                dict(
                    sourceOnly=True,
                    case=case,
                    reply=reply,
                    acceptance=acceptance,
                    originalFindings=originals,
                    declaredFindings=declared,
                    callCount=len(calls),
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
        if case == "closed":
            assert not reply["isError"], reply
            assert acceptance["invalidReason"] == "acceptance-approval-required", acceptance
            assert acceptance["next"].startswith("harness_accept statement=")
            assert next_step == acceptance["next"], next_step
            name, *fields = shlex.split(next_step)
            arguments = {field.split("=", 1)[0]: field.split("=", 1)[1] for field in fields}
            assert set(arguments) == {"statement", "by"}
            arguments.update(
                statement="SOURCE ONLY explicit proxy acceptance of this repaired fixture",
                by="proxy:source-test",
            )
            recorded = tool(name, arguments)
            assert recorded["isProxy"] is True
            after = store.status()["acceptance"]
            assert after["accepted"] is True and after["humanOriginVerified"] is False
            assert after["isProxy"] is True and after["approvalOrigin"] == "recorded-unverified"
            assert (
                store.connection.execute("SELECT count(*) FROM acceptance_approvals").fetchone()[0]
                == 1
            )
            assert len(calls) == 5
        elif case == "open":
            assert (
                not reply["isError"] and acceptance["invalidReason"] == "acceptance-findings-open"
            )
            assert next_step.startswith("harness_accept_assess schema=true;")
            before_db = list(store.connection.iterdump())
            before_support = store.execution_assessment_state()
            before_a = hashlib.sha256((repo / "A.py").read_bytes()).hexdigest()
            name, field = next_step.split(";", 1)[0].split()
            key, value = field.split("=", 1)
            schema = tool(name, {key: json.loads(value)})
            name, field = schema["acquisition"]["choices"].split(" / ", 1)[0].split()
            key, value = field.split("=", 1)
            choices = tool(name, {key: json.loads(value)})
            assert af["id"] in {row["id"] for row in choices["choices"]["findings"]}
            assert list(store.connection.iterdump()) == before_db
            assert store.execution_assessment_state() == before_support
            assert hashlib.sha256((repo / "A.py").read_bytes()).hexdigest() == before_a
            assert len(calls) == 5
        else:
            assert reply["isError"] and "cross-subscription" in reply["content"][0]["text"]
            assert acceptance["invalidReason"] == "acceptance-review-required"
            assert not next_step.startswith(
                ("harness_accept_assess", "harness_accept statement=")
            ), next_step
            assert "reported blocker" in next_step
            assert len(calls) == 3
            refused = server.call_tool(
                "harness_accept",
                dict(
                    statement="SOURCE ONLY cannot replace missing ordinary reviews",
                    by="proxy:source-test",
                ),
            )
            assert (
                refused["isError"]
                and "native cross-subscription evidence" in refused["content"][0]["text"]
            )
            assert store.status()["acceptance"]["accepted"] is False
            assert (
                store.connection.execute("SELECT count(*) FROM acceptance_approvals").fetchone()[0]
                == 0
            )
            assert len(calls) == 3
        assert store.acceptance_findings() == declared
        assert store.execution_assessment_state()["closureEligible"] is False
        (evidence / "continuation-final.json").write_text(
            json.dumps(
                dict(sourceOnly=True, case=case, calls=calls, status=store.status()), indent=2
            ),
            encoding="utf-8",
        )
    finally:
        store.close()


def rpc(server, method, params=None, request_id=1):
    # Existing same-project fixtures explicitly target their constructed project.
    # Routing counterexamples use workspace_wire instead, with no implicit intent.
    if (
        method == "tools/call"
        and isinstance(params, dict)
        and isinstance(params.get("arguments", {}), dict)
    ):
        params = {
            **params,
            "arguments": {
                "expected_workspace": str(server.workspace),
                **params.get("arguments", {}),
            },
        }
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
    )


def native_confirmation_result(
    server, gate, statement="Synthetic native client response", **content
):
    """Exercise the actual correlated wire bridge; this synthetic client is not a human."""
    rpc(
        server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}}
    )
    server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    outbound = rpc(
        server,
        "tools/call",
        {"name": "harness_record_confirmation", "arguments": {"gate": gate}},
        71,
    )
    assert outbound["method"] == "elicitation/create", outbound
    response = server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": outbound["id"],
                "result": {
                    "action": "accept",
                    "content": {
                        "approve": True,
                        "statement": statement,
                        "conditions": "",
                        **content,
                    },
                },
            }
        )
    )
    assert response["id"] == 71
    return response["result"]


def native_confirm(server, gate, statement="Synthetic native client response"):
    result = native_confirmation_result(server, gate, statement)
    assert not result["isError"], result
    return result["content"][0]["text"]


def begin_native(
    server, *, gate="align", initialized=True, version="2025-06-18", capabilities=None
):
    rpc(
        server,
        "initialize",
        {
            "protocolVersion": version,
            "capabilities": {"elicitation": {}} if capabilities is None else capabilities,
        },
    )
    if initialized:
        server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    return rpc(
        server,
        "tools/call",
        {
            "name": "harness_record_confirmation",
            "arguments": {"gate": gate},
        },
        94,
    )


def answer_native(server, wire_id, *, action="accept", **content):
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": wire_id,
                "result": {"action": action, **({"content": content} if content else {})},
            }
        )
    )


@pytest.mark.parametrize("case", ["no-capability", "old-version", "not-initialized", "direct-call"])
def test_native_confirmation_requires_negotiated_ready_client(server, case):
    start(server)
    if case == "direct-call":
        result = server.call_tool("harness_record_confirmation", {"gate": "align"})
    else:
        result = begin_native(
            server,
            initialized=case != "not-initialized",
            version="2025-03-26" if case == "old-version" else "2025-06-18",
            capabilities={} if case == "no-capability" else None,
        )["result"]
    assert result["isError"]
    assert "plan-confirmation-origin-unavailable" in result["content"][0]["text"]
    assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    assert server._pending_confirmation is None


@pytest.mark.parametrize("version", ["2025-06-18", "future"])
def test_native_response_correlation_replay_and_full_words(server, version):
    start(server)
    outbound = begin_native(server, version=version)
    assert outbound["method"] == "elicitation/create" and outbound["id"] != 94
    assert server.store.status()["plan_revision"] in outbound["params"]["message"]
    assert "Scope in:" in outbound["params"]["message"]
    assert "default" not in outbound["params"]["requestedSchema"]["properties"]["approve"]
    assert (
        answer_native(server, "invented-id", approve=True, statement="Forged", conditions="")
        is None
    )
    assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    words = "Exact complete response. " + "Long native response. " * 100
    result = answer_native(server, outbound["id"], approve=True, statement=words, conditions="")
    assert result["id"] == 94 and not result["result"]["isError"]
    data = result["result"]["structuredContent"]
    assert data["state"] == "approved" and data["content"]["statement"] == words
    assert data["provenance"] == "mcp-client-elicitation" and not data["human_identity_verified"]
    assert (
        answer_native(server, outbound["id"], approve=True, statement=words, conditions="") is None
    )
    assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 1
    call(server, "harness_advance", to_phase="expand")


@pytest.mark.parametrize("change", ["metadata", "changed-vision", "pending", "conditional"])
def test_native_status_uses_current_credit_and_distinguishes_history(server, change):
    """Protocol -> real store -> chat status; no providers or human identity proof."""
    start(server)
    native_confirm(server, "align")
    call(server, "harness_advance", to_phase="expand")
    call(server, "harness_advance", to_phase="vision-lock")
    native_confirm(server, "vision-lock", "Synthetic native vision confirmation")
    call(server, "harness_advance", to_phase="features")
    original = rpc(server, "tools/call", {"name": "harness_plan_status"})["result"]
    original_text = original["content"][0]["text"]
    assert original["structuredContent"]["approval"]["granted"]
    assert "Current vision-lock approval: granted" in original_text
    assert "native MCP client-reported response" in original_text
    assert "natural-person identity is not authenticated" in original_text
    assert "approved by proxy (mcp-client-elicitation)" not in original_text
    assert "Vision lock: locked vision" in original_text

    if change in {"metadata", "changed-vision"}:
        node = server.store.spine().node("V1")
        if change == "metadata":
            node["fields"]["validation"] = ["-B", "-m", "unittest", "discover"]
        else:
            node["text"] += " A new material user outcome."
        server.store.upsert_node(node)
    else:
        outbound = begin_native(server, gate="vision-lock")
        assert outbound["method"] == "elicitation/create"
        if change == "conditional":
            answer_native(
                server,
                outbound["id"],
                approve=True,
                statement="Before approval, review",
                conditions="First show the findings digest.",
            )
    current = rpc(server, "tools/call", {"name": "harness_plan_status"})["result"]
    status, text = current["structuredContent"], current["content"][0]["text"]
    if change == "metadata":
        assert status["approval"]["granted"] and not status["vision_lock_stale"]
        assert "Vision lock: locked vision" in text
    else:
        assert not status["approval"]["granted"]
        assert status["pending_gate"] == "approval:vision-lock"
        assert "Current vision-lock approval: not granted" in text
        assert "Vision lock: not currently granted" in text
        assert "Vision lock: locked vision" not in text
        assert "harness_record_confirmation gate=vision-lock" in text
        if change != "changed-vision":
            # Request revocation is distinct from a material content edit.
            assert not status["vision_lock_stale"]
    assert "Historical vision-lock record (not current approval credit)" in text
    assert "Historical statement: Synthetic native vision confirmation" in text


def test_native_status_keeps_legacy_statement_without_approval_credit(server):
    start(server)
    native_confirm(server, "align")
    call(server, "harness_advance", to_phase="expand")
    call(server, "harness_advance", to_phase="vision-lock")
    # Explicit legacy fixture: a historical row is not a native client response.
    with server.store._transaction():
        server.store._approve("vision-lock", "proxy:historical", "Historical words remain intact")
    result = rpc(server, "tools/call", {"name": "harness_plan_status"})["result"]
    status, text = result["structuredContent"], result["content"][0]["text"]
    assert not status["approval"]["granted"]
    assert "Current vision-lock approval: not granted" in text
    assert "Vision lock: locked vision" not in text
    assert "recorded by proxy:historical" in text
    assert "Historical statement: Historical words remain intact" in text
    assert "approved by you" not in text and "approved by proxy" not in text


@pytest.mark.parametrize(
    "case", ["decline", "cancel", "conditional", "negative", "stale", "invalid"]
)
def test_native_nonapproval_response_never_grants_gate(server, case):
    start(server)
    outbound = begin_native(server)
    content = dict(
        approve=case != "negative", statement="Yes, but first review my conditions", conditions=""
    )
    if case == "conditional":
        content["conditions"] = "First give me the reviewer digest."
    if case == "stale":
        node = server.store.spine().node("V1")
        node["text"] += " New material scope."
        server.store.upsert_node(node)
    if case == "invalid":
        content["verified"] = True
    result = answer_native(
        server,
        outbound["id"],
        action=case if case in {"decline", "cancel"} else "accept",
        **({} if case in {"decline", "cancel"} else content),
    )["result"]
    assert result["isError"]
    assert result["structuredContent"]["state"] != "approved"
    if case == "conditional":
        assert result["structuredContent"]["content"] == content
    assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    assert server._pending_confirmation is None
    call(server, "harness_advance", to_phase="expand", error=True)


@pytest.mark.parametrize("case", ["timeout", "disconnect", "rpc-error", "client-cancel"])
def test_native_pending_lifetime_never_promotes_missing_response(server, case):
    """Protect no-response recovery without granting or losing a native approval.

    Catch timeout/disconnect being stored as user cancellation; the leaf tests
    cannot prove correlation of a fresh wire ID to the same durable request.
    """
    start(server)
    outbound = begin_native(server)
    durable_id = server._pending_confirmation["request"]["request_id"]
    original_request = server._pending_confirmation["request"]
    workspace = server.workspace
    if case == "timeout":
        server._pending_confirmation["deadline"] = -1
        responses = server.expire_confirmation()
        assert responses[0]["method"] == "notifications/cancelled"
        assert responses[1]["id"] == 94 and responses[1]["result"]["isError"]
        assert responses[1]["result"]["workspaceBinding"]["serverWorkspace"] == str(workspace)
    elif case == "disconnect":
        server.close()
    elif case == "rpc-error":
        failed = server.handle_line(
            json.dumps(
                dict(jsonrpc="2.0", id=outbound["id"], error=dict(code=-32603, message="Closed"))
            )
        )
        assert failed["result"]["structuredContent"]["state"] == "pending"
    else:
        assert (
            server.handle_line(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/cancelled",
                        "params": {"requestId": 94},
                    }
                )
            )
            is None
        )
    assert server._pending_confirmation is None
    assert (
        answer_native(server, outbound["id"], approve=True, statement="Late", conditions="") is None
    )
    with open_plan_store(workspace) as store:
        assert store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
        event = store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='confirmation-responded' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if case == "client-cancel":
            assert json.loads(event[0])["action"] == "cancel"
        else:
            assert event is None
    if case != "client-cancel":
        reconnect = PlannerServer(workspace)
        try:
            reoffered = begin_native(reconnect)
            assert reoffered["id"] != outbound["id"]
            fresh = reconnect._pending_confirmation["request"]
            assert fresh["request_id"] != durable_id
            assert fresh["plan_revision"] == original_request["plan_revision"]
            assert fresh["gate"] == original_request["gate"]
            assert fresh["card"] == original_request["card"]
            assert (
                answer_native(
                    reconnect, outbound["id"], approve=True, statement="Late", conditions=""
                )
                is None
            )
            approved = answer_native(
                reconnect,
                reoffered["id"],
                approve=True,
                statement="SOURCE-ONLY synthetic current response",
                conditions="",
            )
            assert not approved["result"]["isError"]
            call(reconnect, "harness_advance", to_phase="expand")
            assert reconnect._status()["phase"] == "expand"
        finally:
            reconnect.close()


@pytest.mark.parametrize("older_first", [True, False])
@pytest.mark.parametrize("current_action", ["accept", "cancel"])
def test_overlapping_native_displays_only_current_request_can_approve(
    server, older_first, current_action
):
    """Catch the reproduced two-client replay and consumed-request crash.

    Single-client timeout/reconnect tests cannot establish ordering between two
    simultaneously live native clients displaying the same saved revision.
    """
    start(server)
    old = begin_native(server)
    old_request = server._pending_confirmation["request"]
    current = PlannerServer(server.workspace)
    try:
        new = begin_native(current)
        new_request = current._pending_confirmation["request"]
        assert new_request["request_id"] != old_request["request_id"]
        assert new_request["card"] == old_request["card"]

        def answer_old():
            refused = answer_native(
                server,
                old["id"],
                approve=True,
                statement="SOURCE-ONLY stale display",
                conditions="",
            )
            assert refused["result"]["isError"]
            assert refused["result"]["structuredContent"]["state"] == "stale"

        if older_first:
            answer_old()
        result = answer_native(
            current,
            new["id"],
            action=current_action,
            approve=True,
            statement="SOURCE-ONLY current display",
            conditions="",
        )
        assert result["result"]["structuredContent"]["state"] == (
            "approved" if current_action == "accept" else "cancel"
        )
        if not older_first:
            answer_old()
        assert current.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == (
            1 if current_action == "accept" else 0
        )
        call(current, "harness_advance", to_phase="expand", error=current_action == "cancel")
    finally:
        current.close()


def test_native_quoted_or_forged_tool_arguments_cannot_complete_response(server):
    start(server)
    for arguments in (
        {"gate": "align", "user_statement": "Earlier words"},
        {"gate": "align", "verified": True},
        {"gate": "align", "response_id": "invented", "origin": "human"},
    ):
        result = rpc(
            server,
            "tools/call",
            {
                "name": "harness_record_confirmation",
                "arguments": arguments,
            },
        )["result"]
        assert result["isError"]
    assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0


@pytest.fixture
def native_worker_project(tmp_path, monkeypatch):
    # Real canonical store replaces the old SourceOnlyStore double because an
    # execution grant must be written/consumed on the actual database.
    from tests.python.core import test_planner_replay_gaps

    yield from test_planner_replay_gaps.project.__wrapped__(tmp_path, monkeypatch)


def test_worker_mcp_start_status_reconnect_preserve_one_admission(
    native_worker_project, monkeypatch
):
    """Real canonical grant/job persistence; synthetic native client and process return."""
    from types import SimpleNamespace

    from tests.python.core import test_planner_resume
    from tests.python.core.test_plan_resume_transport import (
        approve_execution_wire,
        execution_wire,
        native_execution_client,
    )

    from codex_harness.core import planner_jobs

    store, launches = native_worker_project, []
    options = test_planner_resume._published(store)
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *a, **k: launches.append((a, k)) or SimpleNamespace(pid=321),
    )
    for index in range(2):
        client = PlannerServer(store.workspace)
        native_execution_client(client)
        response = execution_wire(
            client,
            "harness_worker_start",
            {
                "expected_workspace": str(store.workspace),
                "expected_revision": options["expected_revision"],
                "workers": options["workers"],
                "timeout": 61,
                "max_parallel": 1,
                "call_cap": 1,
            },
        )
        started = (approve_execution_wire(client, response) if index == 0 else response)["result"]
        assert not started["isError"], started
        data = started["structuredContent"]
        assert data["status"] == "unknown" and not data["harnessDeliveryProven"]
        status = rpc(
            client,
            "tools/call",
            {"name": "harness_worker_status", "arguments": {"job_id": data["jobId"]}},
        )["result"]
        assert status["structuredContent"] == data
        assert "harness_worker_status job_id=" in status["content"][0]["text"]
        client.close()
    assert len(launches) == 1


def test_native_stdio_request_response_keeps_original_tool_id(tmp_path):
    """Actual stdio loop, deterministic wire-ID fixture, no providers or human proof."""
    messages = [
        dict(
            jsonrpc="2.0",
            id=1,
            method="initialize",
            params={
                "protocolVersion": "2025-06-18",
                "capabilities": {"elicitation": {}},
            },
        ),
        dict(jsonrpc="2.0", method="notifications/initialized"),
        dict(
            jsonrpc="2.0",
            id=2,
            method="tools/call",
            params={
                "name": "harness_plan_start",
                "arguments": {"idea": "Native demo", "name": "Native demo"},
            },
        ),
        dict(
            jsonrpc="2.0",
            id=3,
            method="tools/call",
            params={
                "name": "harness_record_confirmation",
                "arguments": {"gate": "align"},
            },
        ),
        dict(
            jsonrpc="2.0",
            id="seldon-confirmation-fixture",
            result={
                "action": "accept",
                "content": {
                    "approve": True,
                    "statement": "Synthetic stdio client confirms",
                    "conditions": "",
                },
            },
        ),
        dict(
            jsonrpc="2.0",
            id=4,
            method="tools/call",
            params={
                "name": "harness_advance",
                "arguments": {"to_phase": "expand"},
            },
        ),
    ]
    for message in messages:
        if message.get("method") == "tools/call":
            message["params"]["arguments"]["expected_workspace"] = str(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "from types import SimpleNamespace; "
                "from codex_harness.core import mcp_planner; "
                "mcp_planner.uuid4=lambda: SimpleNamespace(hex='fixture'); mcp_planner.main()"
            ),
            "--workspace",
            str(tmp_path),
        ],
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2, "seldon-confirmation-fixture", 3, 4]
    assert replies[2]["method"] == "elicitation/create"
    assert replies[3]["result"]["structuredContent"]["state"] == "approved"
    assert not replies[4]["result"]["isError"]


def test_mail_tools_through_line_handler_without_starting_plan(server):
    def mail(name, **args):
        result = server.call_tool(name, args)
        assert not result["isError"], result
        if name == "harness_mail_inbox":
            return result["structuredContent"]["messages"]
        return result["structuredContent"]

    message_id = mail(
        "harness_mail_send",
        from_agent="worker:A:codex",
        to_agent="planner",
        subject="Blocked",
        body="Need the R1 decision",
        refs=["A", "R1"],
        kind="blocker",
    )["id"]
    assert not (server.workspace / ".codex-harness" / "plan.sqlite3").exists()
    assert mail("harness_mail_inbox")[0]["id"] == message_id
    refused = call(server, "harness_mail_ack", id=message_id, by="worker:B:claude", error=True)
    assert "mail-ack-wrong-agent" in refused
    reply_id = mail("harness_mail_reply", id=message_id, from_agent="planner", body="Use R1")["id"]
    reply = mail("harness_mail_inbox", agent="worker:A:codex")[0]
    assert reply["id"] == reply_id and reply["thread_id"] == message_id
    mail("harness_mail_ack", id=message_id, by="planner")
    assert mail("harness_mail_inbox") == []
    assert mail("harness_mail_inbox", unacked_only=False)[0]["acked_by"] == "planner"
    assert "mail-secret-refused" in call(
        server,
        "harness_mail_send",
        error=True,
        from_agent="user",
        to_agent="planner",
        subject="Note",
        body="password=refused",
    )
    assert mail("harness_mail_inbox") == []


def call(server, tool_name, *, error=False, **args):
    response = rpc(server, "tools/call", {"name": tool_name, "arguments": args})
    result = response["result"]
    assert result["isError"] is error, result
    text = result["content"][0]["text"]
    assert text.splitlines()[0].startswith("Seldon · project: ")
    assert banner(server._status()) in text.splitlines()
    assert text.splitlines()[-1].startswith("Next:")
    assert sum(line.startswith("Next:") for line in text.splitlines()) == 1
    return text


@pytest.fixture
def server(tmp_path):
    instance = PlannerServer(tmp_path)
    yield instance
    instance.close()


def start(server):
    return call(server, "harness_plan_start", idea="Split the bill fairly", name="tip-split")


@pytest.fixture
def revision_server(revision_proof):
    from tests.python.core.test_mcp_audit import started

    (revision_proof["template"] / "app.py").write_text(
        "class StoreError(ValueError): pass\n"
        "class Store:\n"
        "    def __init__(self, path): self.path = path\n"
        "    def load(self): raise NotImplementedError('planned storage')\n"
    )
    (revision_proof["template"] / "tests/test_app.py").write_text(
        "import tempfile, unittest\nfrom pathlib import Path\nfrom app import Store, StoreError\n"
        "class StorageProof(unittest.TestCase):\n"
        "    def setUp(self):\n"
        "        self.tmp = tempfile.TemporaryDirectory()\n"
        "        self.addCleanup(self.tmp.cleanup)\n"
        "        self.path = Path(self.tmp.name) / 'cards.json'\n"
        "    def test_malformed_json(self):\n"
        "        self.path.write_bytes(b'{')\n"
        "        store = Store(self.path)\n"
        "        with self.assertRaises(StoreError):\n"
        "            store.load()\n"
        "        self.assertEqual(self.path.read_bytes(), b'{')\n"
    )
    instance, _ = started(revision_proof)
    yield instance
    instance.close()


def test_public_protected_test_revision_writes_linked_source(revision_server):
    """Catch node-only review repairs; prior compile tests manually author changed files.

    The real writer adds Pocketboard's three omitted loaded-schema cases, and the
    real audit/packet/publication consumers must reject reuse of the previous proof.
    No provider result or successful review credit is fabricated.
    """
    from tests.python.core.test_mcp_audit import finished_audit

    server = revision_server
    path = server.workspace / "template/tests/test_app.py"
    before = path.read_bytes()
    revision = server.store.manifest_revision()
    previous = finished_audit(server, mode="prospective")["structuredContent"]
    assert previous["ok"], previous
    read = workspace_wire(
        server,
        "harness_show_node",
        expected_workspace=str(server.workspace),
        id="T",
        include_source=True,
    )["structuredContent"]["testFiles"][0]
    assert read["source"].encode() == before
    source = read["source"]
    for name, payload in (
        ("unsupported_version", b'{"version":99,"cards":[]}'),
        ("missing_cards", b'{"version":1}'),
        ("nonlist_cards", b'{"version":1,"cards":{}}'),
    ):
        source += (
            f"    def test_{name}(self):\n"
            '        """Reject invalid loaded schema; malformed JSON misses these envelopes."""\n'
            f"        self.path.write_bytes({payload!r})\n"
            "        store = Store(self.path)\n"
            "        with self.assertRaises(StoreError):\n"
            "            store.load()\n"
            f"        self.assertEqual(self.path.read_bytes(), {payload!r})\n"
        )
    reply = workspace_wire(
        server,
        "harness_revise_test",
        expected_workspace=str(server.workspace),
        node_id="T",
        path="tests/test_app.py",
        expected_manifest_revision=revision,
        expected_file_sha256=read["sha256"],
        source=source,
        reason="Address the review finding in the linked proof.",
    )
    assert not reply["isError"], reply
    result = reply["structuredContent"]
    assert path.read_bytes() == source.encode()
    assert result["manifestRevision"] != revision
    assert result["manifestRevision"] == server.store.manifest_revision()
    assert result["beforeSha256"] == hashlib.sha256(before).hexdigest()
    assert result["afterSha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert server.store.passing_audit(revision) is None
    assert server.store.audit_history(revision)["audits"][0]["id"] == previous["id"]
    assert "harness_compile" in call(server, "harness_packet_view", error=True, id="B")
    call(server, "harness_compile")
    packet = workspace_wire(
        server,
        "harness_packet_view",
        expected_workspace=str(server.workspace),
        id="B",
    )
    assert not packet["isError"], packet
    current = finished_audit(server, mode="prospective")["structuredContent"]
    assert current["ok"], current
    assert current["redBaseline"][0]["testsRun"] == 4
    assert current["manifestRevision"] == result["manifestRevision"]
    assert current["id"] != previous["id"]
    assert server.store.passing_audit(result["manifestRevision"])["id"] == current["id"]
    assert not server.store.successful_reviews(result["manifestRevision"])
    refused = call(server, "harness_advance", error=True, to_phase="publish")
    assert "successful review" in refused and "passing audit" not in refused
    assert server.store.status()["published_revision"] is None
    event = server.store.connection.execute(
        "SELECT payload_json FROM events WHERE kind='protected-test-revised'"
    ).fetchone()
    assert json.loads(event[0]) == result


@pytest.mark.parametrize("change", ["manifest", "raw-bytes"])
def test_public_test_revision_refuses_stale_preimage(revision_server, change):
    """Catch lost author updates, including normalized-newline hash collisions missed by compile."""
    server = revision_server
    proof = server.workspace / "template/tests/test_app.py"
    before = proof.read_bytes()
    revision = server.store.manifest_revision()
    if change == "manifest":
        node = server.store.spine().node("R")
        node["text"] += " Current acceptance wording."
        call(server, "harness_upsert_node", node=node)
    else:
        changed = before.replace(b"\r\n", b"\n")
        proof.write_bytes(changed if changed != before else before.replace(b"\n", b"\r\n"))
        assert server.store.manifest_revision() == revision
    retained = proof.read_bytes()
    reply = call(
        server,
        "harness_revise_test",
        error=True,
        node_id="T",
        path="tests/test_app.py",
        expected_manifest_revision=revision,
        expected_file_sha256=hashlib.sha256(before).hexdigest(),
        source=before.decode() + "\n# stale author replacement\n",
        reason="Review repair",
    )
    assert "plan-test-preimage-stale" in reply
    assert proof.read_bytes() == retained
    assert not server.store.connection.execute(
        "SELECT 1 FROM events WHERE kind='protected-test-revised'"
    ).fetchone()


@pytest.mark.parametrize("path", ["../outside.py", "app.py", "tests/unlinked.py"])
def test_public_test_revision_refuses_escape_and_unowned_files(revision_server, path):
    """Catch arbitrary-file writes; linked success and stale checks do not enforce ownership."""
    server = revision_server
    target = server.workspace / "template" / path
    if path != "app.py":
        target.write_text("# must survive\n")
    before = target.read_bytes()
    reply = call(
        server,
        "harness_revise_test",
        error=True,
        node_id="T",
        path=path,
        expected_manifest_revision=server.store.manifest_revision(),
        expected_file_sha256=hashlib.sha256(before).hexdigest(),
        source="# must not replace\n",
        reason="Review repair",
    )
    assert "plan-test-path-invalid" in reply
    assert target.read_bytes() == before


def test_public_test_revision_replace_failure_keeps_proof(revision_server, monkeypatch):
    """Catch truncate-first repairs; successful replacement tests cannot expose write failures."""
    from codex_harness.core import plan_store

    server = revision_server
    proof = server.workspace / "template/tests/test_app.py"
    before = proof.read_bytes()
    revision = server.store.manifest_revision()

    def denied_replace(*args):
        raise PermissionError("controlled failed replacement")

    monkeypatch.setattr(plan_store.os, "replace", denied_replace)
    reply = call(
        server,
        "harness_revise_test",
        error=True,
        node_id="T",
        path="tests/test_app.py",
        expected_manifest_revision=revision,
        expected_file_sha256=hashlib.sha256(before).hexdigest(),
        source=before.decode() + "\n# revised\n",
        reason="Review repair",
    )
    assert "controlled failed replacement" in reply
    assert proof.read_bytes() == before and server.store.manifest_revision() == revision
    assert not list(proof.parent.glob(".proof-*"))
    assert not server.store.connection.execute(
        "SELECT 1 FROM events WHERE kind='protected-test-revised'"
    ).fetchone()


def test_public_test_revision_refuses_reparse_directory(revision_server):
    """Catch a tests-directory junction escaping the workspace; lexical path tests miss it."""
    server = revision_server
    tests = server.workspace / "template/tests"
    proof = tests / "test_app.py"
    before = proof.read_bytes()
    revision = server.store.manifest_revision()
    outside = server.workspace / "unowned-tests"
    tests.rename(outside)
    if sys.platform == "win32":
        created = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(tests), str(outside)],
            capture_output=True,
            text=True,
        )
        assert created.returncode == 0, created.stdout + created.stderr
    else:
        tests.symlink_to(outside, target_is_directory=True)
    try:
        reply = workspace_wire(
            server,
            "harness_revise_test",
            expected_workspace=str(server.workspace),
            node_id="T",
            path="tests/test_app.py",
            expected_manifest_revision=revision,
            expected_file_sha256=hashlib.sha256(before).hexdigest(),
            source=before.decode() + "\n# must not follow junction\n",
            reason="Review repair",
        )
        assert reply["isError"] and "plan-test-path-invalid" in reply["content"][0]["text"]
        assert (outside / "test_app.py").read_bytes() == before
    finally:
        if sys.platform == "win32":
            tests.rmdir()
        else:
            tests.unlink()
        outside.rename(tests)


def test_public_test_revision_serializes_competing_authors(revision_server, monkeypatch):
    """Catch two accepted writes from one preimage; sequential stale checks miss lost updates."""
    import threading

    from codex_harness.core import plan_store

    server = revision_server
    proof = server.workspace / "template/tests/test_app.py"
    before = proof.read_bytes()
    revision = server.store.manifest_revision()
    at_replace, release, second_started, second_done = (threading.Event() for _ in range(4))
    replace = plan_store.os.replace
    outcomes = {}

    def held_replace(staged, target):
        at_replace.set()
        assert release.wait(10), "Fixture never released the first replacement"
        return replace(staged, target)

    monkeypatch.setattr(plan_store.os, "replace", held_replace)

    def author(name):
        other = PlannerServer(server.workspace)
        try:
            if name == "second":
                second_started.set()
            outcomes[name] = workspace_wire(
                other,
                "harness_revise_test",
                expected_workspace=str(server.workspace),
                node_id="T",
                path="tests/test_app.py",
                expected_manifest_revision=revision,
                expected_file_sha256=hashlib.sha256(before).hexdigest(),
                source=before.decode() + f"\n# {name} author\n",
                reason="Review repair",
            )
        finally:
            other.close()
            if name == "second":
                second_done.set()

    first = threading.Thread(target=author, args=("first",))
    second = threading.Thread(target=author, args=("second",))
    first.start()
    try:
        assert at_replace.wait(10)
        second.start()
        assert second_started.wait(10)
        assert not second_done.wait(0.1), "Second writer bypassed the reservation"
    finally:
        release.set()
        first.join(10)
        if second.ident is not None:
            second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert not outcomes["first"]["isError"], outcomes
    assert outcomes["second"]["isError"], outcomes
    assert "plan-test-preimage-stale" in outcomes["second"]["content"][0]["text"]
    assert proof.read_bytes() == before + b"\n# first author\n"


@pytest.mark.parametrize("directory,allowed", [("checks", True), ("tests", False)])
def test_public_test_revision_uses_active_runner_directory(revision_server, directory, allowed):
    """Catch accepting legacy tests while refusing active checks; default-only tests miss drift."""
    server = revision_server
    template = server.workspace / "template"
    before = (template / "tests/test_app.py").read_bytes()
    (template / "checks").mkdir()
    (template / "checks/test_app.py").write_bytes(before)
    vision = server.store.spine().node("V1")
    vision["fields"]["requirementFiles"] = ["tests", "checks"]
    vision["fields"]["verification"] = {
        "runner": "unittest",
        "argv": ["-B", "-m", "unittest", "discover", "-s", "checks", "-p"],
    }
    call(server, "harness_upsert_node", node=vision)
    readback = workspace_wire(
        server,
        "harness_show_node",
        expected_workspace=str(server.workspace),
        id="T",
        include_source=True,
    )["structuredContent"]["testFiles"]
    assert {item["path"] for item in readback} == {"tests/test_app.py", "checks/test_app.py"}
    revision = server.store.manifest_revision()
    source = before.decode() + "\n# active runner proof revision\n"
    response = workspace_wire(
        server,
        "harness_revise_test",
        expected_workspace=str(server.workspace),
        node_id="T",
        path=f"{directory}/test_app.py",
        expected_manifest_revision=revision,
        expected_file_sha256=hashlib.sha256(before).hexdigest(),
        source=source,
        reason="Review repair must stay inside the active runner directory",
    )
    if allowed:
        assert not response["isError"], response
        assert (template / "checks/test_app.py").read_bytes() == source.encode()
        assert (template / "tests/test_app.py").read_bytes() == before
    else:
        assert response["isError"], response
        assert "plan-test-path-invalid" in response["content"][0]["text"]
        assert (template / "tests/test_app.py").read_bytes() == before
        assert not server.store.connection.execute(
            "SELECT 1 FROM events WHERE kind='protected-test-revised'"
        ).fetchone()


def test_public_acceptance_contract_derives_complete_executable_coverage(server, monkeypatch):
    def no_process(*args, **kwargs):
        raise AssertionError("Scenario authoring must not execute a command or provider")

    monkeypatch.setattr(subprocess, "Popen", no_process)
    start(server)
    vision = server.store.spine().node("V1")
    vision["fields"] = {
        "first_user_moment": "Split a bill fairly",
        "scope_in": ["Round cents", "Show each diner's share"],
    }
    call(server, "harness_upsert_node", node=vision)
    native_confirm(server, "align")
    call(server, "harness_advance", to_phase="expand")
    call(server, "harness_advance", to_phase="vision-lock")
    native_confirm(server, "vision-lock")
    call(server, "harness_schema", tool="harness_accept_scenarios")
    contract = server.result_data
    properties = contract["inputSchema"]["properties"]
    assert properties["derive"]["type"] == "boolean"
    call(server, "harness_accept_scenarios", derive=True)
    derived = server.result_data["scenarios"]
    assert {item["id"] for item in derived} == {"first-moment", "scope-1", "scope-2"}
    # Populate returned canonical IDs using only the advertised planned example.
    examples = properties["scenarios"].get("examples", [])
    example = examples[0][0] if examples else {}
    planned = [
        {
            **item,
            "steps": example.get("steps", item["steps"]),
            "expect": example.get("expect", item["expect"]),
        }
        for item in derived
    ]
    call(server, "harness_accept_scenarios", scenarios=planned)
    saved = server.result_data["scenarios"]
    rejected = call(server, "harness_accept_scenarios", error=True, scenarios=planned[:-1])
    assert "acceptance-coverage-required" in rejected
    call(server, "harness_accept_scenarios")
    assert server.result_data["scenarios"] == saved
    print(json.dumps({"publicContract": contract, "saved": saved, "missingCoverage": rejected}))
    assert all(item["executable"] and item["status"] == "planned" for item in saved), saved
    assert "harness_accept_scenarios derive=true" in rejected
    assert "given" in properties["scenarios"]["description"]
    assert "expect" in properties["scenarios"]["description"]
    assert all(item["nodeRevisions"]["V1"] for item in saved)
    assert (
        server.store.connection.execute("SELECT count(*) FROM acceptance_runs").fetchone()[0] == 0
    )


def test_public_compile_then_packet_current_manifest_without_execution(
    server, tmp_path, monkeypatch
):
    def no_process(*args, **kwargs):
        raise AssertionError(
            "Compilation and packet inspection must not launch a worker or provider"
        )

    monkeypatch.setattr(subprocess, "Popen", no_process)
    start(server)
    add_fixture(server)
    template = tmp_path / "template"
    # Authored static test inputs are prerequisites, never generated execution proof.
    for source in (FIXTURE.parent / "template" / "tests").rglob("*"):
        if source.is_file():
            target = template / "tests" / source.relative_to(FIXTURE.parent / "template" / "tests")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    before_phase = server.store.phase()
    before_packet = call(server, "harness_packet_view", error=True, id="A", provider="codex")
    reply = rpc(server, "tools/call", {"name": "harness_compile", "arguments": {}})["result"]
    print(json.dumps({"packetBeforeCompile": before_packet, "publicCompile": reply}))
    assert not reply["isError"], reply
    manifest = reply["structuredContent"]
    assert "harness_compile" in before_packet
    assert manifest["planRevision"] == server.store.manifest_revision()
    assert manifest["requirementFilesRevision"]
    recorded = json.loads(
        server.store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='manifest-compiled' ORDER BY id DESC"
        ).fetchone()[0]
    )
    assert recorded["manifest"] == manifest
    assert recorded["spine"] == server.store.spine().to_dict()
    assert server.store.phase() == before_phase
    assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    event_count = server.store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
    call(server, "harness_packet_view", id="A", provider="codex")
    packet = server.result_data
    assert packet["bytes"] == len(packet["packet"].encode("utf-8")) > 0
    assert packet["characters"] == len(packet["packet"])
    assert (
        server.store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == event_count
    )
    for context, code in (
        ({}, "workspace-assertion-required"),
        ({"expected_workspace": str(tmp_path / "other")}, "workspace-mismatch"),
    ):
        refused = workspace_wire(server, "harness_compile", **context)
        assert refused["isError"] and code in refused["content"][0]["text"], refused
        assert (
            server.store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
            == event_count
        )
    (template / "tests").rename(template / "authored-tests-held")
    try:
        missing = call(server, "harness_compile", error=True)
        assert "plan-requirement-file-missing" in missing
        assert (
            server.store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
            == event_count
        )
    finally:
        (template / "authored-tests-held").rename(template / "tests")
    server.close()
    with open_plan_store(tmp_path) as reopened:
        assert reopened.manifest_revision() == manifest["planRevision"]
    call(server, "harness_packet_view", id="A", provider="codex")
    assert server.result_data == packet
    protected = next((template / "tests").rglob("test_*.py"))
    original = protected.read_bytes()
    protected.write_bytes(original + b"\n# Changed protected test input\n")
    stale = call(server, "harness_packet_view", error=True, id="A", provider="codex")
    assert "harness_compile" in stale
    protected.write_bytes(original)
    bead = server.store.spine().node("A")
    bead["text"] += " Current revised plan."
    call(server, "harness_upsert_node", node=bead)
    assert "harness_compile" in call(server, "harness_packet_view", error=True, id="A")
    call(server, "harness_compile")
    assert server.result_data["planRevision"] != manifest["planRevision"]
    call(server, "harness_packet_view", id="A")
    bead["fields"]["ownedFiles"] = ["../escape.py"]
    call(server, "harness_upsert_node", node=bead)
    compiled_count = server.store.connection.execute(
        "SELECT count(*) FROM events WHERE kind='manifest-compiled'"
    ).fetchone()[0]
    invalid = call(server, "harness_compile", error=True)
    assert "plan-schema-invalid" in invalid
    assert (
        server.store.connection.execute(
            "SELECT count(*) FROM events WHERE kind='manifest-compiled'"
        ).fetchone()[0]
        == compiled_count
    )
    call(server, "harness_packet_view", error=True, id="A")
    assert not (tmp_path / "escape.py").exists()
    assert server.store.phase() == before_phase
    assert (
        server.store.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_runs'"
        ).fetchone()
        is None
    )
    assert not (tmp_path / "plan" / "revisions").exists()


@pytest.mark.parametrize("existing_plan", [False, True])
def test_preflight_protocol_never_opens_or_creates_plan(tmp_path, monkeypatch, existing_plan):
    inject_mock_discovery(monkeypatch)
    workspace = tmp_path if existing_plan else tmp_path / "absent" / "project"
    if existing_plan:
        with open_plan_store(workspace) as store:
            store.upsert_node(
                dict(
                    id="V1",
                    kind="vision",
                    title="Existing plan",
                    text="Intent",
                    parents=[],
                    fields={},
                )
            )
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    def forbidden(*args, **kwargs):
        pytest.fail("Preflight must not invoke the normal status/store seam")

    monkeypatch.setattr(PlannerServer, "_status", forbidden)
    instance = PlannerServer(workspace)
    try:
        result = rpc(
            instance,
            "tools/call",
            dict(name="harness_preflight", arguments={"providers": ["codex", "claude"]}),
        )["result"]
        assert not result["isError"], result
        report = result["structuredContent"]
        assert [row["executable"]["status"] for row in report["providers"]] == [
            "available",
            "missing",
        ]
        assert report["isMocked"] is True and report["didRunProvider"] is False
        assert report["planDatabase"]["status"] == ("available" if existing_plan else "missing")
        assert "authentication UNKNOWN" in result["content"][0]["text"]
        assert "execution readiness NOT PROVEN" in result["content"][0]["text"]
        for arguments in ({"providers": ["unsupported"]}, {"providers": []}, {"unknown": True}):
            refused = rpc(
                instance, "tools/call", dict(name="harness_preflight", arguments=arguments)
            )["result"]
            assert refused["isError"] and "Next:" in refused["content"][0]["text"]
        assert instance.store is None
    finally:
        instance.close()
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_draft_tools_same_store_reopen_and_unmet_chat(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
    server = PlannerServer(tmp_path)
    try:

        def invoke(name, **arguments):
            result = rpc(server, "tools/call", dict(name=name, arguments=arguments))["result"]
            assert result["isError"] is False, result
            return result

        intake = invoke(
            "harness_intake",
            node=draft_intake(),
            expected_spine_revision=inputs["expected_spine_revision"],
        )
        catalog = invoke(
            "harness_reviewer_catalog",
            catalog=mock_catalog(),
            expected_spine_revision=intake["structuredContent"]["spineRevision"],
        )
        choice = invoke(
            "harness_reviewer_choice",
            node=mock_choice(catalog["structuredContent"]["catalog"], "standing-delegation"),
            expected_spine_revision=catalog["structuredContent"]["spineRevision"],
        )
        assert choice["structuredContent"]["independence"]["qualifyingReviewerNames"] == []
        inputs["expected_spine_revision"] = choice["structuredContent"]["spineRevision"]
        mapped = invoke("harness_wave_map", **inputs)
        assert set(mapped["structuredContent"]["nodeRevisions"]) == {"MAP", "DETAIL1", "DETAIL2"}
        projected = invoke("harness_waves")
        text = projected["content"][0]["text"]
        assert "Later meaningful refinement" in text and "Later owner" in text
        assert "pending producers: B1" in text and "Current owner" in text
        assert "Current exit facts" in text and "Later exit facts" in text
        assert "completed-real 0" in text and "outstanding 5" in text
        assert "standing-delegation" in text and "independence-unknown" in text
        assert "gpt-6.1-sol" in text and "high" in text
        assert "eligibility" in text.lower() and "not measured" in text
        before = server.store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        invoke("harness_waves")
        assert (
            server.store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == before
        )
        server.close()
        reopened = invoke("harness_waves")
        assert reopened == projected
        assert "Outcome 2" in invoke("harness_plan_status")["content"][0]["text"]
        stale = rpc(server, "tools/call", dict(name="harness_wave_map", arguments=inputs))["result"]
        assert stale["isError"] and "plan-spine-stale" in stale["content"][0]["text"]
    finally:
        server.close()


@pytest.mark.parametrize("target", ["intake", "choice", "map", "detail"])
def test_review_mcp_malformed_fields_refuses_atomically(tmp_path, target):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
        revision = store.spine().plan_revision()
        events = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
    if target in ("intake", "choice"):
        node = draft_intake()
        node["fields"] = []
        name = "harness_intake" if target == "intake" else "harness_reviewer_choice"
        arguments = dict(node=node, expected_spine_revision=revision)
    else:
        if target == "map":
            inputs["map_node"]["fields"] = []
        else:
            inputs["detail_nodes"][0]["fields"] = []
        name, arguments = "harness_wave_map", inputs
    server = PlannerServer(tmp_path)
    try:
        result = rpc(server, "tools/call", dict(name=name, arguments=arguments))["result"]
        assert result["isError"]
        assert "plan-draft-fields-invalid" in result["content"][0]["text"]
        assert server.store.spine().plan_revision() == revision
        assert (
            server.store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == events
        )
    finally:
        server.close()


def test_draft_observation_replacement_stales_choice_and_missing_preimage_refuses(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
    server = PlannerServer(tmp_path)
    try:
        missing = rpc(
            server, "tools/call", dict(name="harness_intake", arguments=dict(node=draft_intake()))
        )["result"]
        assert missing["isError"] and "expected_spine_revision" in missing["content"][0]["text"]
        catalog = server.store.record_reviewer_catalog(
            mock_catalog(), inputs["expected_spine_revision"]
        )["catalog"]
        selected = server.store.record_reviewer_selection(
            mock_choice(catalog, "user"),
            inputs["expected_spine_revision"],
            author_sessions={
                "implementationSessionIds": ["mock:author"],
                "reviewerSessionIds": {"primary": "mock:reviewer"},
            },
        )
        assert selected["independence"]["qualifyingReviewerNames"] == ["primary"]
        observation = mock_catalog()
        observation["id"] = "RC-00000000-0000-0000-0000-000000000002"
        server.store.record_reviewer_catalog(observation, selected["spineRevision"])
        result = server.call_tool("harness_waves", {})
        assert not result["isError"]
        assert result["structuredContent"]["reviewerSelection"]["state"] == "stale"
        assert "reviewer-catalog-stale" in result["content"][0]["text"]
    finally:
        server.close()


def add_fixture(server):
    spine = load_spine(FIXTURE)
    pending = {key: spine.node(key) for key in spine.ids()}
    pending["A"]["parents"].append("R6")  # Close the fixture's documented coverage gap.
    written = set()
    while pending:
        ready = [
            key
            for key, node in pending.items()
            if set(
                node["parents"]
                + node["fields"].get("dependsOn", [])
                + node["fields"].get("lessons", [])
            )
            <= written
        ]
        assert ready
        for key in ready:
            node = pending.pop(key)
            call(server, "harness_upsert_node", node=node)
            written.add(key)


@pytest.mark.parametrize("version", ["2024-11-05", "2025-03-26", "2025-06-18", "future", None])
def test_initialize_negotiation(server, version):
    response = rpc(server, "initialize", {"protocolVersion": version}, 17)
    assert response["id"] == 17
    assert response["result"] == {
        "protocolVersion": version if version in ("2024-11-05", "2025-03-26") else "2025-06-18",
        "serverInfo": {"name": "harness-planner", "version": "1.0.0"},
        "capabilities": {"tools": {}},
    }


def test_list_tools_and_notifications(server):
    tools = rpc(server, "tools/list")["result"]["tools"]
    assert {tool["name"] for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert "properties" in tool["inputSchema"]
        assert "required" in tool["inputSchema"]
    confirmation = next(tool for tool in tools if tool["name"] == "harness_record_confirmation")
    assert "Requires negotiated elicitation" in confirmation["description"]
    assert set(confirmation["inputSchema"]["properties"]) == {"gate", "expected_workspace"}
    assert server.handle_line('{"jsonrpc":"2.0","method":"notifications/initialized"}') is None
    assert rpc(server, "ping")["result"] == {}
    assert rpc(server, "unknown")["error"]["code"] == -32601


def test_each_tool_and_all_eleven_phases_to_publication(server, tmp_path):
    seen = set()

    def run(tool_name, **args):
        seen.add(tool_name)
        if tool_name == "harness_record_confirmation":
            return native_confirm(server, args["gate"], args["user_statement"])
        return call(server, tool_name, **args)

    assert "Phase 1/11: Align" in run("harness_plan_start", idea="Split fairly", name="tip-split")
    assert "approval:align" in run("harness_plan_status")
    assert "Durable plan resume" in run("harness_plan_resume")
    run("harness_schema", kind="requirement")
    card = run(
        "harness_understanding_card",
        vision="Split fairly",
        users="Diners",
        first_user_moment="Shares total exactly",
        scope_in="Dollars",
        scope_out="History",
        assumptions=["Users have a CLI"],
    )
    assert "Your call: confirm, or tell me what's wrong." in card
    assert card.splitlines()[-1].startswith("Next:")
    assert server.store.spine().node("V1")["fields"]["users"] == "Diners"
    assert server.store.status()["open_assumptions"] == 1
    question = run("harness_ask", question="How many diners?", node_ids=["V1"])
    question_id = question.split("Question id: ")[1].splitlines()[0]
    run("harness_answer", id=question_id, answer="At least one")
    assumption = run("harness_add_assumption", text="English output", node_ids=["V1"])
    assumption_id = assumption.split("Assumption id: ")[1].splitlines()[0]
    run(
        "harness_resolve_assumption",
        id=assumption_id,
        confirmed=True,
        rationale="Confirmed scope",
        by="planner",
    )
    run(
        "harness_decision_card",
        id="temporary",
        question="Language?",
        options=[{"name": "Python", "pros": "Simple", "cons": "Runtime needed"}],
        recommendation="Python",
        parents=["V1"],
    )
    run("harness_remove_node", id="temporary")
    add_fixture(server)
    seen.add("harness_upsert_node")
    before_inspection = list(server.store.connection.iterdump())
    assert "Inspection only" in run("harness_review_policy")
    assert (
        server.result_data["example"]["expectedManifestRevision"]
        == server.store.manifest_revision()
    )
    assert list(server.store.connection.iterdump()) == before_inspection
    run("harness_show_node", id="V1")
    run("harness_nodes", kind="requirement")
    run("harness_diagram", kind="feature-map")
    run("harness_render")
    assert {path.name for path in (tmp_path / "plan" / "docs").iterdir()} == set(MARKDOWN_FILES)
    for phase in PHASES[1:]:
        current = server.store.phase()
        if current in ("align", "vision-lock"):
            run("harness_record_confirmation", gate=current, user_statement="I confirm this vision")
        if phase == "publish":
            publication_evidence(server.store)
        text = run("harness_advance", to_phase=phase)
        assert server.store.phase() == phase
        if phase in PHASE_DIAGRAMS:
            kind = PHASE_DIAGRAMS[phase]
            assert chat_diagram(server.store.spine(), kind) in text
        if phase == "publish":
            publication_evidence(server.store)
            published = run("harness_publish")
            review_id = server.store.connection.execute(
                "SELECT round_id FROM review_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0]
            run("harness_review", id=review_id)
            revision = server.store.status()["published_revision"]
            assert revision in published
            assert "Wave 1 (single bead): A" in published
            assert "Wave 2 (single bead): B" in published
            assert "Open assumptions: 1" in published
            assert (tmp_path / "plan" / "revisions" / f"{revision}.json").is_file()
            assert (tmp_path / "plan" / revision / "manifest.json").is_file()
            assert all((tmp_path / "plan" / revision / name).is_file() for name in MARKDOWN_FILES)
            run("harness_impact")
    run("harness_mail_send", from_agent="user", to_agent="planner", subject="Note", body="Ready")
    mail_id = server.result_data["id"]
    run("harness_mail_inbox")
    run("harness_mail_reply", id=mail_id, from_agent="planner", body="Understood")
    run("harness_mail_ack", id=mail_id, by="planner")
    # Acceptance and readiness have dedicated integration journeys.
    assert seen == EXPECTED_TOOLS - {
        name
        for name in EXPECTED_TOOLS
        if name.startswith("harness_accept")
        or name
        in {
            "harness_resolve_finding",
            "harness_resolve_findings",
            "harness_packet_view",
            "harness_compile",
            "harness_revise_test",  # Dedicated real writer/audit/invalidation journey.
            "harness_readiness",
            "harness_upsert_nodes",  # Dedicated real mixed-batch/atomic-refusal journey.
            "harness_roundtable",  # Dedicated public protected-producer/receipt journey.
            "harness_adopt_review_policy",  # test_mcp_review_policy_adoption: writer/reopen/guards.
            "harness_audit",  # Dedicated real audit/persisted-gate/refusal journey.
            "harness_audit_status",  # Dedicated committed-result/read-only history journey.
            "harness_preflight",
            "harness_intake",
            "harness_reviewer_catalog",
            "harness_reviewer_choice",
            "harness_confirm_reviewer_choice",
            "harness_wave_map",
            "harness_waves",
            "harness_worker_start",
            "harness_worker_status",
        }
    }
    decision = server.store.spine().node("D3")
    decision["text"] += " Clarified rounding."
    run("harness_upsert_node", node=decision)
    digest = run("harness_impact")
    assert "Affected beads: A, B" in digest
    assert "Untouched beads: none" in digest
    assert "Affected tests: test_cli, test_money" in digest
    assert "Stale docs: PLAN.md, VISION.md" in digest
    server.close()
    with open_plan_store(tmp_path) as reopened:
        assert reopened.phase() == "accept"
        assert reopened.spine().node("D3")["text"].endswith("Clarified rounding.")


def test_confirmation_missing_empty_and_stale(server):
    start(server)
    text = call(server, "harness_advance", error=True, to_phase="expand")
    assert "plan-approval-required" in text and "align needs approval" in text
    assert "pending_gate: approval:align" in text
    call(server, "harness_record_confirmation", error=True, gate="align", user_statement="  ")
    native_confirm(server, "align", "Yes, that is right")
    node = server.store.spine().node("V1")
    node["text"] += " Changed scope."
    call(server, "harness_upsert_node", node=node)
    assert "plan-approval-stale" in call(server, "harness_advance", error=True, to_phase="expand")
    assert server.store.phase() == "align"


@pytest.mark.parametrize(
    "name,args",
    [
        ("harness_resolve_assumption", {"id": "missing", "confirmed": "false"}),
        ("harness_record_confirmation", {"gate": "align", "user_statement": "", "by": "user"}),
        ("harness_understanding_card", {"vision": "only one field"}),
        ("harness_upsert_node", {"node": {"id": "bad"}}),
        ("harness_diagram", {"kind": "invented"}),
        ("harness_ask", {"question": "why?", "node_ids": ["missing"]}),
        ("harness_publish", {}),
        ("harness_impact", {}),
        ("missing-tool", {}),
    ],
)
def test_invalid_tool_calls_do_not_crash_or_mutate(server, name, args):
    start(server)
    before = server.store.status()
    call(server, name, error=True, **args)
    assert server.store.status() == before
    call(server, "harness_plan_status")


@pytest.mark.parametrize(
    "line,code",
    [
        ("{broken", -32700),
        ("[]", -32600),
        ("null", -32600),
        ('{"jsonrpc":"1.0","method":"ping","id":1}', -32600),
        ('{"jsonrpc":"2.0","method":"ping","id":[]}', -32600),
        ('{"jsonrpc":"2.0","method":"ping","id":1,"params":[]}', -32602),
        ('{"jsonrpc":"2.0","method":"ping","id":NaN}', -32700),
    ],
)
def test_malformed_lines_recover(server, line, code):
    assert server.handle_line(line)["error"]["code"] == code
    assert rpc(server, "ping")["result"] == {}


def test_restart_resumes_without_overwriting_vision(server, tmp_path):
    start(server)
    native_confirm(server, "align", "Yes")
    call(server, "harness_advance", to_phase="expand")
    before = server.store.status()
    server.close()
    # Even an invalid first call after restart must report the durable phase/revision.
    invalid = call(
        server, "harness_resolve_assumption", error=True, id="missing", confirmed="false"
    )
    assert invalid.splitlines()[0].startswith("Seldon · project: ")
    assert banner(before) in invalid.splitlines()
    assert "Phase 2/11: Expand" in call(
        server, "harness_plan_start", idea="Other idea", name="Other name"
    )
    assert server.store.status() == before
    assert server.store.spine().node("V1")["text"] == "Split the bill fairly"
    assert not (tmp_path / "plan").exists()


def test_unavailable_status_is_error_and_server_recovers(server, monkeypatch):
    start(server)
    store_type = type(server.store)
    original = store_type.status

    def unavailable(self):
        raise OSError("Store unavailable")

    monkeypatch.setattr(store_type, "status", unavailable)
    response = rpc(server, "tools/call", {"name": "harness_plan_status"})["result"]
    assert response["isError"] is True
    assert response["content"][0]["text"].startswith("Seldon · project: ")
    assert "\nHarness Plan Mode: " in response["content"][0]["text"]
    monkeypatch.setattr(store_type, "status", original)
    call(server, "harness_plan_status")


def test_render_containment_and_custom_directory(server, tmp_path):
    start(server)
    outside = tmp_path.parent / "outside-views"
    call(server, "harness_render", error=True, out_dir=str(outside))
    assert not outside.exists()
    paths = call(server, "harness_render", out_dir="custom/docs")
    assert str(tmp_path / "custom" / "docs" / "VISION.md") in paths
    call(server, "harness_render", out_dir=None)


def test_unstarted_errors_and_mutating_notifications(server, tmp_path):
    call(server, "harness_advance", error=True, to_phase="expand")
    assert (
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "harness_plan_start",
                        "arguments": {"idea": "bad", "name": "bad"},
                    },
                }
            )
        )
        is None
    )
    assert not (tmp_path / ".codex-harness").exists()


def test_stdio_subprocess_protocol_and_recovery(tmp_path):
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "harness_plan_start",
                "arguments": {"idea": "Fair shares", "name": "Diners 🧭"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "harness_advance", "arguments": {"to_phase": "expand"}},
        },
        {"jsonrpc": "2.0", "id": 5, "method": "ping"},
    ]
    for request in requests:
        if request.get("method") == "tools/call":
            request["params"]["arguments"]["expected_workspace"] = str(tmp_path)
    wire = "{malformed\n" + "\n".join(json.dumps(item) for item in requests) + "\n"
    result = subprocess.run(
        [sys.executable, "-m", "codex_harness.core.mcp_planner", "--workspace", str(tmp_path)],
        input=wire,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(replies) == 6
    assert replies[0]["error"]["code"] == -32700
    assert {tool["name"] for tool in replies[2]["result"]["tools"]} == EXPECTED_TOOLS
    assert len(TOOLS) == len(EXPECTED_TOOLS)
    assert "Diners 🧭" in replies[3]["result"]["content"][0]["text"]
    assert replies[4]["result"]["isError"] is True
    assert replies[5]["result"] == {}
    for reply in replies[3:5]:
        assert reply["result"]["content"][0]["text"].startswith("Seldon · project: ")
        assert "\nHarness Plan Mode: " in reply["result"]["content"][0]["text"]
    assert "planner tool failed:" in result.stderr


def confirmation_rpc(server, **arguments):
    """Retain the actual synthetic wire exchange in the private scenario fixture."""
    request = dict(
        jsonrpc="2.0",
        id=41,
        method="tools/call",
        params=dict(
            name="harness_advance",
            arguments={"expected_workspace": str(server.workspace), **arguments},
        ),
    )
    response = server.handle_line(json.dumps(request, ensure_ascii=False))
    with (server.workspace / "confirmation-wire.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(dict(request=request, response=response), ensure_ascii=False) + "\n"
        )
    return response["result"]


def mcp_wave_plan(workspace):
    from tests.python.core.test_wave_refine import cli, setup_wave

    setup_wave(workspace)
    cli(workspace, "compile", "--template", "template", "--out", "manifest.json")
    server = PlannerServer(workspace)
    try:
        for target in PHASES[1 : PHASES.index("plan") + 1]:
            current = server._store().phase()
            if current in ("align", "vision-lock"):
                native_confirm(server, current, "Synthetic earlier vision confirmation")
            call(server, "harness_advance", to_phase=target)
        return server
    except BaseException:
        server.close()
        raise


def test_advance_advertises_optional_confirmation_transport(server):
    tools = rpc(server, "tools/list")["result"]["tools"]
    advance = next(tool for tool in tools if tool["name"] == "harness_advance")
    schema = advance["inputSchema"]
    assert set(schema["properties"]) == {"to_phase", "user_statement", "by", "expected_workspace"}
    assert schema["required"] == ["to_phase", "expected_workspace"]
    assert schema["properties"]["expected_workspace"]["type"] == "string"
    assert schema["properties"]["user_statement"]["type"] == "string"
    assert "plan" in advance["description"] and "proxy:" in advance["description"]


@pytest.mark.parametrize("actor", [None, "user", "proxy:synthetic-Δ"])
def test_mcp_wave_confirmation_exact_event_lock_and_projection(tmp_path, actor):
    from codex_harness.core.waves import product_digest

    server = mcp_wave_plan(tmp_path / "wave")
    workspace = server.workspace
    statement = "  Synthetic approval: café Δ\r\nW1 now; W2 retained.  "
    try:
        before = server.store.spine().plan_revision()
        vision_lock = server.store._meta()["locked_vision_json"]
        args = dict(to_phase="roundtable", user_statement=statement)
        if actor is not None:
            args["by"] = actor
        result = confirmation_rpc(server, **args)
        assert not result["isError"], result
        assert server.store.phase() == "roundtable"
        row = server.store.connection.execute(
            "SELECT id, payload_json FROM events WHERE kind='phase-advanced' ORDER BY id DESC"
        ).fetchone()
        event = json.loads(row["payload_json"])
        lock = server.store.wave_product_lock()
        assert event["from"] == "plan" and event["to"] == "roundtable"
        assert event["by"] == lock["confirmedBy"] == (actor or "user")
        assert event["statement"] == lock["statement"] == statement
        assert lock["sourceRef"] == f"plan-event:{row['id']}"
        assert lock["spineRevision"] == event["plan_revision"] == before
        assert lock["spine"] == server.store.spine().to_dict()
        assert lock["mapNodeId"] == "MAP"
        assert lock["waveMapRevision"] == server.store.spine().revision("MAP")
        proxy = actor is not None and actor.startswith("proxy:")
        assert lock["isProxy"] is proxy
        assert lock["confirmationOrigin"] == ("proxy" if proxy else "user")
        projected = rpc(server, "tools/call", dict(name="harness_waves"))["result"]
        data = projected["structuredContent"]
        assert not projected["isError"] and data["waveProductLock"] == lock
        assert lock["digest"] == product_digest(
            server.store.spine(),
            data["mapNode"]["fields"],
            [node["fields"] for node in data["detailNodes"]],
        )
        assert [node["fields"]["detail"] for node in data["detailNodes"]] == ["detailed", "sketch"]
        assert server.store._meta()["locked_vision_json"] == vision_lock
    finally:
        server.close()
    with open_plan_store(workspace) as reopened:
        assert reopened.wave_product_lock() == lock


def test_mcp_bare_wave_advance_keeps_planner_and_no_historical_lock(tmp_path):
    server = mcp_wave_plan(tmp_path / "bare")
    try:
        assert server.store.outcome_waves()["waveProductLock"] is None
        result = confirmation_rpc(server, to_phase="roundtable")
        assert not result["isError"], result
        event = server.store._latest_draft_event("phase-advanced")
        assert event["by"] == "planner" and event["statement"] is None
        assert "waveProductLock" not in event
        assert server.store.outcome_waves()["waveProductLock"] is None
        server.close()
        projected = rpc(server, "tools/call", dict(name="harness_waves"))["result"]
        assert projected["structuredContent"]["waveProductLock"] is None
    finally:
        server.close()


@pytest.mark.parametrize(
    "arguments",
    [
        dict(by="user"),
        dict(by="proxy:synthetic"),
        dict(user_statement=""),
        dict(user_statement=" \r\n\t ", by="user"),
        dict(user_statement="synthetic", by="planner"),
        dict(user_statement="synthetic", by="proxy:"),
        dict(user_statement="synthetic", by="proxy:  "),
        dict(user_statement="synthetic", by="opus-user-proxy"),
        dict(user_statement="synthetic", by="worker:synthetic"),
        dict(user_statement=None),
        dict(user_statement=17),
        dict(user_statement="synthetic", by=None),
        dict(to_phase="tech-stack", user_statement="synthetic"),
        dict(to_phase="plan", user_statement="synthetic"),
    ],
)
def test_mcp_wave_confirmation_refuses_without_mutation(tmp_path, arguments):
    server = mcp_wave_plan(tmp_path / "refused")
    try:
        before = server.store.status()
        events = list(server.store.connection.execute("SELECT * FROM events"))
        args = dict(to_phase="roundtable") | arguments
        result = confirmation_rpc(server, **args)
        assert result["isError"], result
        assert server.store.status() == before
        assert list(server.store.connection.execute("SELECT * FROM events")) == events
        assert server.store.outcome_waves()["waveProductLock"] is None
    finally:
        server.close()


@pytest.mark.parametrize("gate,target", [("align", "expand"), ("vision-lock", "features")])
def test_mcp_advance_confirmation_cannot_substitute_vision_gate(server, gate, target):
    start(server)
    if gate == "vision-lock":
        native_confirm(server, "align", "Synthetic align")
        call(server, "harness_advance", to_phase="expand")
        call(server, "harness_advance", to_phase="vision-lock")
    before = server.store.status()
    events = list(server.store.connection.execute("SELECT * FROM events"))
    result = confirmation_rpc(server, to_phase=target, user_statement="Synthetic misplaced words")
    assert result["isError"], result
    assert server.store.status() == before
    assert list(server.store.connection.execute("SELECT * FROM events")) == events
    assert not server.store.connection.execute(
        "SELECT * FROM approvals WHERE gate=?", (gate,)
    ).fetchall()


def test_mcp_closure_schema_example_records_unverified_and_holds_publication(tmp_path, monkeypatch):
    from tests.python.core.test_plan_store import _closure_policy_fixture

    from codex_harness.core import roundtable

    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        revision, _, _, _, _ = _closure_policy_fixture(store)
        original = store.spine().to_dict()
        monkeypatch.setattr(roundtable, "_run", lambda *_: pytest.fail("No provider allowed"))
        instance = PlannerServer(tmp_path)
        try:
            schema = rpc(
                instance,
                "tools/call",
                {
                    "name": "harness_schema",
                    "arguments": {"tool": "harness_resolve_findings"},
                },
            )["result"]
            assert not schema["isError"]
            advertised = schema["structuredContent"]
            assert (
                "closure"
                in advertised["inputSchema"]["properties"]["findings"]["items"]["properties"]
            )
            example = advertised["closureExample"]["example"]
            invalid = json.loads(json.dumps(example))
            invalid["findings"][0]["closure"]["reviewIds"] = ["invented-review"]
            before = list(store.connection.iterdump())
            rejected = rpc(
                instance,
                "tools/call",
                {
                    "name": "harness_resolve_findings",
                    "arguments": invalid,
                },
            )["result"]
            assert rejected["isError"]
            assert "record-stale-or-absent" in rejected["content"][0]["text"]
            assert "harness_schema tool=harness_resolve_findings" in rejected["content"][0]["text"]
            assert list(store.connection.iterdump()) == before
            response = rpc(
                instance,
                "tools/call",
                {
                    "name": "harness_resolve_findings",
                    "arguments": example,
                },
            )["result"]
            assert not response["isError"]
            declared = response["structuredContent"]["closureDeclarations"][0]
            assert declared["closure"] == "unverified" and declared["retainedObligation"] is True
            assert store.spine().to_dict() == original and store.manifest_revision() == revision
            blocked = rpc(
                instance,
                "tools/call",
                {
                    "name": "harness_advance",
                    "arguments": {"to_phase": "publish"},
                },
            )["result"]
            assert blocked["isError"]
            policy = blocked["structuredContent"]["authorHandoff"]["reviewPolicy"]
            assert policy["closureFindings"][0]["retainedObligation"] is True
            assert policy["producerAvailability"]["targetedAssessment"] == "unavailable"
            assert "Hold: targeted" in blocked["content"][0]["text"]
            assert store.phase() == "roundtable" and not store.status()["publication"]["up_to_date"]
            requirement = store.spine().node("R1")
            requirement["text"] += " Clarify the implicated contract before fresh inspection."
            edited = rpc(
                instance,
                "tools/call",
                {
                    "name": "harness_upsert_node",
                    "arguments": {"node": requirement},
                },
            )["result"]
            assert not edited["isError"]
            refreshed = rpc(
                instance,
                "tools/call",
                {
                    "name": "harness_plan_status",
                    "arguments": {},
                },
            )["result"]
            emitted = refreshed["content"][0]["text"].rsplit("\nNext: ", 1)[1]
            arguments = shlex.split(emitted)
            assert arguments == ["harness_review_policy"]
            from codex_harness.core.planner_views import plan_review_policy_contract

            before_inspection = list(store.connection.iterdump())
            expected_contract = plan_review_policy_contract(store)
            held = store.plan_review_policy_state(store.manifest_revision())
            inspected = rpc(
                instance,
                "tools/call",
                {
                    "name": arguments[0],
                    "arguments": {"expected_workspace": str(tmp_path.resolve())},
                },
            )["result"]
            assert not inspected["isError"], inspected
            assert inspected["structuredContent"] == expected_contract
            assert (
                expected_contract["example"]["expectedManifestRevision"]
                == store.manifest_revision()
            )
            text = inspected["content"][0]["text"]
            assert (
                expected_contract["adoptionWarning"] in text and expected_contract["limits"] in text
            )
            assert all(value in text for value in expected_contract["unavailableProducers"])
            assert store.plan_review_policy_state(store.manifest_revision()) == held
            assert list(store.connection.iterdump()) == before_inspection
        finally:
            instance.close()
