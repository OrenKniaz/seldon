"""Canonical policy declaration -> guarded public read-only contract and Next.

Existing CLI policy tests cannot detect a native inspection gap or a native
handler opening a writable store. Source-only confirmation prepares the fixture;
no providers, human approval or policy adoption through inspection is claimed.
"""

import json
import subprocess
from contextlib import contextmanager

import pytest
from tests.python.core.test_plan_store import FIXTURE, _link_r6, _to

from codex_harness.core import mcp_planner, plan_cli, planner_views, roundtable
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PlanStore, open_plan_store


@pytest.fixture
def project(tmp_path):
    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        _link_r6(store)
        _to(store, "roundtable")
        yield store


def wire(server, tool, arguments):
    reply = server.handle_line(
        json.dumps(
            dict(
                jsonrpc="2.0",
                id=7,
                method="tools/call",
                params=dict(name=tool, arguments=arguments),
            )
        )
    )
    assert reply["id"] == 7
    return reply["result"]


def test_current_and_stale_policy_contract_reaches_native_reader_and_preserves_cli(
    project, monkeypatch, capsys
):
    """Catch CLI-only stale Next, missing contract detail, hidden adoption or write access.

    Real policy writer and CLI producer bind the expected payload; all reads must
    preserve declarations/events/holds, including optional roles and unknown proof.
    """
    revision = project.manifest_revision()
    declaration = project.record_plan_review_policy(
        [
            dict(name="Required", provider="codex", required=True),
            dict(name="Optional", provider="claude", required=False),
        ],
        revision,
    )
    original_reader = mcp_planner.open_plan_store_readonly
    reader_calls, forbidden_calls = [], []

    def forbid(*args, **kwargs):
        forbidden_calls.append(args)
        raise AssertionError("Inspection must not open writable state, adopt or launch")

    @contextmanager
    def reader(workspace):
        with original_reader(workspace) as store:
            assert store.connection.execute("PRAGMA query_only").fetchone()[0] == 1
            reader_calls.append(str(workspace))
            yield store

    for stale in (False, True):
        if stale:
            node = project.spine().node("R1")
            node["text"] += " Source-only changed requirement"
            project.upsert_node(node)
        before = list(project.connection.iterdump())
        contract = planner_views.plan_review_policy_contract(project)
        assert (
            plan_cli.main(
                ["--workspace", str(project.workspace), "--json", "roundtable", "--policy-schema"]
            )
            == 0
        )
        cli = json.loads(capsys.readouterr().out)
        assert cli["data"] == contract
        assert contract["example"]["expectedManifestRevision"] == project.manifest_revision()
        if not stale:
            assert contract["example"]["slots"] == declaration["slots"]
        with monkeypatch.context() as patch:
            patch.setattr(mcp_planner, "open_plan_store", forbid)
            patch.setattr(mcp_planner, "open_plan_store_readonly", reader)
            patch.setattr(PlanStore, "record_plan_review_policy", forbid)
            patch.setattr(roundtable, "run_roundtable", forbid)
            patch.setattr(subprocess, "Popen", forbid)
            server = PlannerServer(project.workspace)
            try:
                arguments = {"expected_workspace": str(project.workspace)}
                result = wire(server, "harness_review_policy", arguments)
                assert not result["isError"], result
                assert result["structuredContent"] == contract
                assert result["workspaceBinding"]["match"] == "matched"
                text = result["content"][0]["text"]
                assert contract["adoptionWarning"] in text and contract["limits"] in text
                assert all(value in text for value in contract["unavailableProducers"])
                own_next = text.rsplit("\nNext: ", 1)[1]
                assert "harness_review_policy" not in own_next
                assert "MCP policy adoption/refresh is unavailable" not in own_next
                assert ("harness_compile" if stale else "harness_adopt_review_policy") in own_next
                status = wire(server, "harness_plan_status", arguments)
                policy = status["structuredContent"]["reviewPolicy"]
                assert policy["active"] and not policy["eligible"]
                assert policy["declaredRevision"] == revision
                if stale:
                    assert "review-policy-revision-stale" in policy["reasons"]
                    assert (
                        status["content"][0]["text"].rsplit("\nNext: ", 1)[1]
                        == "harness_review_policy"
                    )
                assert server.store is None
            finally:
                server.close()
        assert plan_cli.main(["--workspace", str(project.workspace), "--json", "status"]) == 0
        cli_status = json.loads(capsys.readouterr().out)
        if stale:
            assert "roundtable --policy-schema" in cli_status["next"]
        assert project._plan_review_policies()[-1]["payload"] == declaration
        assert list(project.connection.iterdump()) == before
    assert len(reader_calls) == 4 and not forbidden_calls


@pytest.mark.parametrize("binding", ["absent", "foreign", "relative"])
def test_inspection_workspace_refusal_precedes_any_store_or_provider(
    tmp_path, monkeypatch, binding
):
    """Catch a new read tool bypassing mandatory caller intent; no stored fixture can help."""
    workspace = tmp_path / "missing"
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Binding must refuse before store or provider access")

    monkeypatch.setattr(mcp_planner, "open_plan_store", forbidden)
    monkeypatch.setattr(mcp_planner, "open_plan_store_readonly", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    args = (
        {}
        if binding == "absent"
        else {
            "expected_workspace": str(tmp_path / "foreign") if binding == "foreign" else "relative"
        }
    )
    server = PlannerServer(workspace)
    try:
        result = wire(server, "harness_review_policy", args)
        assert result["isError"] and result["workspaceBinding"]["match"] != "matched"
        assert "workspace-" in result["content"][0]["text"]
        assert not calls and server.store is None and not workspace.exists()
    finally:
        server.close()


def test_bound_inspection_without_plan_refuses_without_creating_workspace(tmp_path, monkeypatch):
    """Catch missing-plan inspection initializing a writable store; discovery stays cold."""
    workspace = tmp_path / "missing"

    def forbidden(*args, **kwargs):
        raise AssertionError("No writable fallback on missing plan")

    monkeypatch.setattr(mcp_planner, "open_plan_store", forbidden)
    server = PlannerServer(workspace)
    try:
        schema = wire(server, "harness_schema", {"tool": "harness_review_policy"})
        assert not schema["isError"], schema
        contract = schema["structuredContent"]["inputSchema"]
        assert contract["required"] == ["expected_workspace"]
        assert set(contract["properties"]) == {"expected_workspace"}
        result = wire(server, "harness_review_policy", {"expected_workspace": str(workspace)})
        assert result["isError"] and "Call harness_plan_start first" in result["content"][0]["text"]
        assert server.store is None and not workspace.exists()
    finally:
        server.close()


def test_inspection_rejects_mutation_arguments_without_adoption_or_launch(project, monkeypatch):
    """Catch accidentally treating inspection payload as optional policy declaration."""
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Inspection has no policy/admission writer")

    monkeypatch.setattr(PlanStore, "record_plan_review_policy", forbidden)
    monkeypatch.setattr(roundtable, "run_roundtable", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    before = list(project.connection.iterdump())
    server = PlannerServer(project.workspace)
    try:
        result = wire(
            server,
            "harness_review_policy",
            {
                "expected_workspace": str(project.workspace),
                "policy": planner_views.plan_review_policy_contract(project)["example"],
                "refresh": True,
            },
        )
        assert result["isError"]
        assert not calls and list(project.connection.iterdump()) == before
    finally:
        server.close()
