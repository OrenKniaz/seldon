"""Schema recovery at the real MCP/store boundary; no provider execution."""

import json

import pytest

from codex_harness.core.mcp_planner import PlannerServer


def invoke(server, tool_name, **arguments):
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        )
    )["result"]


def test_schema_discovery_before_start_has_usable_examples_without_creating_plan(tmp_path):
    # A cold planner cannot recover by reading source. tools/list only describes
    # fields as an opaque object, so it misses the bead/test repair contract.
    server = PlannerServer(tmp_path)
    try:
        guide = invoke(server, "harness_schema", kind="bead")
        assert not guide["isError"], guide
        data = guide["structuredContent"]
        assert not (tmp_path / ".codex-harness").exists()
        assert data["inputSchema"]["additionalProperties"] is False
        assert "ownedFiles" in data["fieldRules"]
        assert data["allowedParentKinds"] == ["decision", "design", "requirement"]
        assert not invoke(server, "harness_plan_start", idea="Recover invalid plans", name="Demo")[
            "isError"
        ]
        for node in data["prerequisiteNodes"]:
            assert not invoke(server, "harness_upsert_node", node=node)["isError"]
        assert not invoke(server, "harness_upsert_node", **data["example"])["isError"]
        assert server.store.spine().node(data["example"]["node"]["id"])["kind"] == "bead"
        assert server.store.phase() == "align"
        assert invoke(server, "harness_advance", to_phase="expand")["isError"]
    finally:
        server.close()


@pytest.mark.parametrize(
    "kind,fields,code,field",
    [
        ("bead", {"ownedFiles": "src/parser.py"}, "spine-bead-fields-invalid", "proofModule"),
        (
            "test",
            {"module": "tests/test_parser.py", "level": "integration"},
            "spine-test-fields-invalid",
            "unit",
        ),
    ],
)
def test_invalid_fields_explain_rule_and_repair_without_mutating_store(
    tmp_path, kind, fields, code, field
):
    # A plausible field error formerly returned only "Invalid ... fields".
    # Generic argument-type tests never reach the canonical kind validator.
    server = PlannerServer(tmp_path)
    try:
        invoke(server, "harness_plan_start", idea="Parse input", name="Parser")
        vision = {
            "id": "V1",
            "kind": "vision",
            "title": "Parse input",
            "text": "",
            "parents": [],
            "fields": {},
        }
        assert not invoke(server, "harness_upsert_node", node=vision)["isError"]
        revision = server.store.spine().plan_revision()
        result = invoke(
            server,
            "harness_upsert_node",
            node={
                "id": "invalid",
                "kind": kind,
                "title": "Reject malformed input",
                "text": "Parser boundary must reject malformed input",
                "parents": [],
                "fields": fields,
            },
        )
        text = result["content"][0]["text"]
        assert result["isError"]
        assert code in text
        assert "Rule:" in text and field in text and "Example:" in text
        assert "harness_schema" in text
        assert server.store.spine().plan_revision() == revision
    finally:
        server.close()


@pytest.mark.parametrize(
    "kind",
    [
        "vision",
        "feature",
        "story",
        "requirement",
        "acceptance",
        "decision",
        "design",
        "bead",
        "test",
    ],
)
def test_each_discovered_example_passes_real_store_and_preserves_open_fields(tmp_path, kind):
    # Documentation can drift while validator-only tests stay green. Exercise
    # the returned payload itself, including the intentionally open fields object.
    server = PlannerServer(tmp_path)
    try:
        invoke(server, "harness_plan_start", idea="Parser", name="Parser")
        guide = invoke(server, "harness_schema", kind=kind)["structuredContent"]
        for node in guide["prerequisiteNodes"]:
            assert not invoke(server, "harness_upsert_node", node=node)["isError"]
        guide["example"]["node"]["fields"]["projectNote"] = {"planned": True}
        result = invoke(server, "harness_upsert_node", **guide["example"])
        assert not result["isError"], result
        before = server.store.spine().plan_revision()
        events = server.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        contract = invoke(server, "harness_schema", tool="harness_record_confirmation")
        assert not contract["isError"]
        assert contract["structuredContent"]["inputSchema"]["required"] == ["gate"]
        assert "user_statement" not in contract["structuredContent"]["inputSchema"]["properties"]
        assert server.store.spine().plan_revision() == before
        assert (
            server.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events
        )
    finally:
        server.close()


def test_schema_rejects_unknown_tool_and_irrelevant_kind_without_creating_plan(tmp_path):
    server = PlannerServer(tmp_path)
    try:
        for arguments in ({"tool": "not_a_tool"}, {"tool": "harness_advance", "kind": "bead"}):
            result = invoke(server, "harness_schema", **arguments)
            assert result["isError"]
            assert "tool-input-or-operation-error" in result["content"][0]["text"]
        assert not (tmp_path / ".codex-harness").exists()
    finally:
        server.close()
