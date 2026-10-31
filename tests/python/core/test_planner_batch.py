"""Public mixed-node MCP batching through the shared canonical writer; no providers."""

import json
from copy import deepcopy

from tests.python.core.test_planner_refusals import canonical_rows

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.planner_guidance import planning_turn
from codex_harness.core.status_api import snapshot


def invoke(server, tool_name, **arguments):
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {"expected_workspace": str(server.workspace), **arguments},
                },
            }
        )
    )["result"]


def start(server):
    assert not invoke(
        server, "harness_plan_start", plan_mode="legacy", idea="Parser", name="Parser"
    )["isError"]
    guide = invoke(server, "harness_schema", tool="harness_upsert_nodes")
    assert not guide["isError"]
    guide = guide["structuredContent"]
    for node in guide["prerequisiteNodes"]:
        assert not invoke(server, "harness_upsert_node", node=node)["isError"]
    return guide["example"]["nodes"]


def test_public_feature_batch_saves_requirement_and_test_then_guides_next_turn(tmp_path):
    """Catch missing MCP batch access or divergence from canonical persisted coverage.

    Existing CLI batch proof cannot exercise MCP discovery/schema/dispatch or readback.
    """
    server = PlannerServer(tmp_path)
    try:
        nodes = start(server)
        # Forward references are valid within the same canonical batch.
        result = invoke(server, "harness_upsert_nodes", nodes=list(reversed(nodes)))
        assert not result["isError"], result
        for node in nodes:
            actual = invoke(server, "harness_show_node", id=node["id"])["structuredContent"]
            assert actual["fields"] == node["fields"]
            assert actual["parents"] == node["parents"]
        assert planning_turn(server._store().spine())["stage"] == "bead"
    finally:
        server.close()
    persisted = snapshot(workspace=tmp_path)["plan"]["spine"]
    assert {node["id"] for node in nodes} <= {node["id"] for node in persisted}


def test_public_invalid_mixed_batch_leaves_nodes_and_events_unchanged(tmp_path):
    """Catch partial saves when a later node fails or repeated IDs would overwrite.

    Existing CLI refusal tests miss the new MCP argument/dispatch adapter boundary.
    """
    server = PlannerServer(tmp_path)
    try:
        nodes = start(server)
        store = server._store()
        before = canonical_rows(store)
        invalid = deepcopy(nodes)
        invalid[-1]["parents"] = ["missing-requirement"]
        refused = invoke(server, "harness_upsert_nodes", nodes=invalid)
        assert refused["isError"]
        assert canonical_rows(store) == before
        duplicate = invoke(server, "harness_upsert_nodes", nodes=[nodes[0], nodes[0]])
        assert duplicate["isError"]
        assert canonical_rows(store) == before
        malformed = invoke(server, "harness_upsert_nodes", nodes=[nodes[0], {"id": "T-broken"}])
        assert malformed["isError"]
        assert canonical_rows(store) == before
    finally:
        server.close()
