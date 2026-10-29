"""Connection projections consume real planner output, without host-access claims."""

import json

from tests.python.core.planning_support import source_only_confirmation

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import open_plan_store_readonly
from codex_harness.core.planner_connection import connection_projection, connection_reply


def wire(server, tool, **arguments):
    return server.handle_line(
        json.dumps(
            dict(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params=dict(name=tool, arguments=arguments),
            )
        )
    )


def test_connection_identity_survives_new_server_on_same_durable_plan(tmp_path):
    """Protect saved-phase resume against reconnect showing a new/other project.

    Existing routing tests prove the target guard, but not connection projection
    of an advanced phase, a distinct plan name, or its durable revision.
    """
    project = tmp_path / "tally"
    project.mkdir()
    server = PlannerServer(project)
    try:
        started = wire(
            server,
            "harness_plan_start",
            expected_workspace=str(project),
            plan_mode="legacy",
            idea="Count local events",
            name="A different plan title",
        )
        assert not started["result"]["isError"]
        # Actual canonical producer; changing phase requires an explicit test proxy.
        source_only_confirmation(server.store, "align")
        server.store.advance("expand", by="source-test")
    finally:
        server.close()
    with open_plan_store_readonly(project) as saved:
        expected = saved.status()
        revision = saved.spine().plan_revision()
    reconnect = PlannerServer(project)
    try:
        observed = wire(reconnect, "harness_plan_status", expected_workspace=str(project))
        reply = observed
        identity = reply["result"]["plannerConnection"]
        assert identity["projectName"] == "tally"
        assert identity["planName"] == expected["name"] == "A different plan title"
        assert identity["phase"] == "expand" and identity["phaseNumber"] == 2
        assert identity["planRevision"] == expected["plan_revision"] == revision
        assert reply["result"]["content"][0]["text"].startswith(
            "Seldon · project: tally · phase 2/11: expand\n"
        )
        assert "Open a new chat in the same project" in identity["recovery"]
        assert "harness_plan_status" in identity["recovery"]
        assert str(project) in identity["recovery"]
        assert identity["clientWorkspaceVerified"] is False
        assert reconnect.store is None  # Discovery remains read-only and closes its store.
    finally:
        reconnect.close()


def test_wrong_project_reply_keeps_refusal_before_store_access(tmp_path, monkeypatch):
    """Protect fail-closed routing when adding a friendly header to refusal.

    A formatter that opens the store would violate the existing guard; existing
    guard tests do not invoke this new projection consumer.
    """
    from codex_harness.core import mcp_planner

    def forbidden(*args, **kwargs):
        raise AssertionError("No store access or dispatch before routing refusal")

    monkeypatch.setattr(mcp_planner, "open_plan_store", forbidden)
    monkeypatch.setattr(mcp_planner, "open_plan_store_readonly", forbidden)
    monkeypatch.setattr(PlannerServer, "_dispatch", forbidden)
    bound, intended = tmp_path / "habits", tmp_path / "tally"
    server = PlannerServer(bound)
    try:
        refused = wire(
            server,
            "harness_plan_start",
            expected_workspace=str(intended),
            plan_mode="legacy",
            idea="x",
            name="x",
        )
        payload = refused["result"]
        assert payload["isError"]
        assert payload["workspaceBinding"] == refused["result"]["workspaceBinding"]
        assert payload["workspaceBinding"]["match"] == "mismatch"
        assert payload["structuredContent"] == refused["result"]["structuredContent"]
        assert payload["plannerConnection"]["phase"] == "unknown"
        assert payload["content"][0]["text"].startswith(
            "Seldon · project: habits · phase ?/11: unknown\nworkspace-mismatch"
        )
        assert not bound.exists() and not intended.exists()
    finally:
        server.close()


def test_native_card_projection_preserves_requested_schema_and_protocol_errors(tmp_path):
    """Protect native approval payloads from display-only connection changes.

    Native confirmation tests cover approval binding, but not the new decorator
    accidentally rewriting schemas or protocol control messages.
    """
    request = dict(
        jsonrpc="2.0",
        id="native-card",
        method="elicitation/create",
        params=dict(message="Approve current understanding", requestedSchema={"type": "object"}),
    )
    decorated = connection_reply(request, tmp_path, {"phase": "align"})
    assert decorated["params"]["message"].startswith("Seldon · project:")
    assert decorated["params"]["requestedSchema"] == request["params"]["requestedSchema"]
    assert decorated["id"] == request["id"] and decorated["method"] == request["method"]
    assert request["params"]["message"] == "Approve current understanding"
    error = dict(jsonrpc="2.0", id=None, error=dict(code=-32700, message="Parse error"))
    assert connection_reply(error, tmp_path) is error
    assert connection_projection(tmp_path)["phaseNumber"] is None
