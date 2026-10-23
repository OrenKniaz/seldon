"""Canonical imported planning state through the public card writer; no human response."""

import json

import pytest
from tests.python.core.test_mcp_planner import workspace_wire

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
from codex_harness.core.spine import SCHEMA, Spine, save_spine


@pytest.mark.parametrize("vision_id", ["V", "V1"], ids=["imported-id", "conventional-id"])
def test_understanding_card_edits_imported_identity_and_binds_current_confirmation(
    tmp_path, vision_id
):
    """Catch wrong node/assumption/result IDs; start-based card tests only exercise V1."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    vision = dict(
        id=vision_id,
        kind="vision",
        title="Imported intent",
        text="Keep imported text",
        parents=[],
        fields={
            "quality": "Retain this bar",
            "custom": {"retained": True},
            "assumptions": ["Already recorded"],
        },
    )
    feature = dict(
        id="F",
        kind="feature",
        title="Existing feature",
        text="Keep this feature",
        parents=[vision_id],
        fields={},
    )
    source = tmp_path / "import.json"
    save_spine(Spine(dict(schema=SCHEMA, name="Imported card", nodes=[vision, feature])), source)
    with open_plan_store(workspace) as store:
        store.import_spine(source)
        existing_assumption = store.add_assumption("Already recorded", [vision_id])
        before_revision = store.spine().plan_revision()
        before = list(store.connection.iterdump())

    arguments = dict(
        vision="Completed understanding",
        users="Local user",
        first_user_moment="Inspect the plan",
        scope_in="Planning",
        scope_out="Workers",
        assumptions=["Already recorded", "New assumption"],
    )
    server = PlannerServer(workspace)
    try:
        foreign = tmp_path / "foreign"
        refused = workspace_wire(
            server, "harness_understanding_card", expected_workspace=str(foreign), **arguments
        )
        assert refused["isError"] and "workspace-mismatch" in refused["content"][0]["text"]
        assert server.store is None and not foreign.exists()
        with open_plan_store_readonly(workspace) as saved:
            assert list(saved.connection.iterdump()) == before

        result = workspace_wire(
            server, "harness_understanding_card", expected_workspace=str(workspace), **arguments
        )
        assert not result["isError"], result
        assert result["structuredContent"]["id"] == vision_id
        status = workspace_wire(server, "harness_plan_status", expected_workspace=str(workspace))
        assert not status["isError"]
        current = status["structuredContent"]
        after_revision = current["spine_revision"]
        assert after_revision != before_revision
        assert current["phase"] == "align" and current["pending_gate"] == "approval:align"
        assert not current["approval"]["granted"]

        # Negotiate only a display request. No acceptance or human response is supplied.
        server.handle_line(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=2,
                    method="initialize",
                    params=dict(protocolVersion="2025-06-18", capabilities={"elicitation": {}}),
                )
            )
        )
        server.handle_line(json.dumps(dict(jsonrpc="2.0", method="notifications/initialized")))
        outbound = server.handle_line(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=3,
                    method="tools/call",
                    params=dict(
                        name="harness_record_confirmation",
                        arguments=dict(gate="align", expected_workspace=str(workspace)),
                    ),
                )
            )
        )
        assert outbound["method"] == "elicitation/create", outbound
    finally:
        server.close()

    with open_plan_store_readonly(workspace) as saved:
        spine = saved.spine()
        expected = {**vision, "fields": {**vision["fields"], **arguments}}
        assert spine.ids("vision") == [vision_id]
        assert spine.node(vision_id) == expected
        assert spine.node("F") == feature
        assert spine.plan_revision() == after_revision
        assumptions = [dict(row) for row in saved.connection.execute("SELECT * FROM assumptions")]
        assert len(assumptions) == 2
        assert {row["text"] for row in assumptions} == {"Already recorded", "New assumption"}
        assert all(json.loads(row["node_ids_json"]) == [vision_id] for row in assumptions)
        assert (
            next(row for row in assumptions if row["text"] == "Already recorded")["id"]
            == existing_assumption
        )
        requests = saved.connection.execute(
            "SELECT payload_json FROM events WHERE kind='confirmation-requested'"
        ).fetchall()
        assert len(requests) == 1
        request = json.loads(requests[0][0])
        assert request["vision"] == expected and request["plan_revision"] == after_revision
        assert request["gate"] == request["phase"] == saved.phase() == "align"
        assert saved.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
