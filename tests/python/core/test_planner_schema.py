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


@pytest.mark.parametrize(
    "tool,required",
    [
        (
            "harness_intake",
            {
                "fields.required": {
                    "requestKind",
                    "sizeEstimate",
                    "delta",
                    "ambiguity",
                    "consequence",
                    "dependencyBreadth",
                    "constraints",
                    "rationale",
                    "assumptions",
                    "expectedSupervision",
                    "nextGate",
                    "reviewerDecisionId",
                    "confirmation",
                    "supersedes",
                    "adaptation",
                },
                "fields.optional": {
                    "schema",
                    "dot-intake-v1",
                    "recipe",
                    "baseline-amendment",
                    "coherent-plan",
                    "outcome-waves",
                },
                "requestKind": {"new", "feature", "fix", "refactor", "continue"},
                "sizeEstimate": {"S", "M", "L"},
                "delta": {
                    "summary",
                    "outcome",
                    "nodeIds",
                    "successCriteria",
                    "ownershipAreas",
                    "interfaceIds",
                },
                "ambiguity": {"level", "reasons", "low", "material", "high"},
                "consequence": {
                    "level",
                    "reversibility",
                    "risks",
                    "ordinary",
                    "hard-invariant",
                    "public-boundary",
                },
                "dependencyBreadth": {"producerIds", "crossWaveContracts"},
                "constraints": {"runtime", "security", "spend", "frictionBudget"},
                "adaptation": {
                    "previousRecipe",
                    "reason",
                    "affectedObligationIds",
                    "evidenceRefs",
                    "authority",
                },
                "authorityShape": {
                    "approvalId",
                    "eventId",
                    "origin",
                    "statement",
                    "sourceRef",
                    "authorizedScopeDigest",
                    "user",
                    "standing-delegation",
                },
                "remainingFields": {
                    "rationale",
                    "expectedSupervision",
                    "nextGate",
                    "reviewerDecisionId",
                    "assumptions",
                    "confirmation",
                    "supersedes",
                    "adaptation",
                    "id",
                    "revision",
                },
            },
        ),
        (
            "harness_reviewer_catalog",
            {
                "catalog": {
                    "schema",
                    "dot-reviewer-catalog-v1",
                    "id",
                    "RC-",
                    "observedAt",
                    "providers",
                    "digest",
                },
                "provider": {
                    "provider",
                    "executable",
                    "executableDigest",
                    "version",
                    "installed",
                    "authentication",
                    "discovery",
                    "source",
                    "choices",
                    "reason",
                },
                "provider.identity": {
                    "provider",
                    "executable",
                    "executableDigest",
                    "version",
                    "installed",
                    "reason",
                },
                "provider.observations": {
                    "authentication",
                    "available",
                    "unavailable",
                    "unknown",
                    "discovery",
                    "observed",
                    "unsupported",
                    "blocked",
                    "failed",
                    "source",
                    "kind",
                    "supported-list",
                    "provider-local-metadata",
                    "reference",
                    "digest",
                    "choices",
                },
                "choice": {
                    "model",
                    "effort",
                    "availability",
                    "reviewPathSupported",
                    "reason",
                    "choiceId",
                    "kind",
                    "named",
                    "value",
                    "unsupported",
                    "available",
                    "unavailable",
                    "unknown",
                },
                "derivedHashes": {
                    "choiceId",
                    "provider",
                    "executable",
                    "executableDigest",
                    "version",
                    "model",
                    "effort",
                    "digest",
                },
                "availableRule": {
                    "installed",
                    "executable",
                    "version",
                    "authentication",
                    "discovery",
                    "source",
                    "unknown",
                },
            },
        ),
        (
            "harness_reviewer_choice",
            {
                "fields.required": {
                    "schema",
                    "catalogId",
                    "catalogDigest",
                    "reviewers",
                    "stages",
                    "assurance",
                    "confirmation",
                    "recommendation",
                },
                "binding": {"dot-reviewer-selection-v1", "catalogId", "catalogDigest", "choiceId"},
                "reviewers": {"name", "provider", "choiceId", "model", "effort"},
                "stages": {"plan", "readiness", "wave-acceptance", "full-vision"},
                "assurance": {
                    "independentRequired",
                    "minIndependentReviewers",
                    "crossProviderRequired",
                    "basisRefs",
                },
                "recommendation": {"choiceIds", "reason", "catchRateEvidenceRefs"},
                "author_sessions": {"implementationSessionIds", "reviewerSessionIds"},
                "authorityShape": {
                    "approvalId",
                    "eventId",
                    "origin",
                    "statement",
                    "sourceRef",
                    "authorizedScopeDigest",
                    "user",
                    "standing-delegation",
                },
                "draftRule": {
                    "confirmation",
                    "null",
                    "reviewer-confirmation-required",
                    "independence",
                },
            },
        ),
    ],
)
def test_first_typed_refusal_teaches_owner_contract_then_supported_diagnostic(
    tmp_path, tool, required
):
    import re

    server = PlannerServer(tmp_path)
    try:
        invoke(server, "harness_plan_start", idea="Preserve accepted task", name="Draft")
        revision = server.store.spine().plan_revision()
        events = server.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        refusal = invoke(server, tool)
        text = refusal["content"][0]["text"]
        assert refusal["isError"] and "Owning typed contract:" in text
        assert "harness_schema tool=" + tool in text
        first, _ = json.JSONDecoder().raw_decode(text.split("Owning typed contract: ", 1)[1])
        for field, terms in required.items():
            assert field in first, (tool, field)
            assert terms <= set(re.findall(r"[A-Za-z][A-Za-z0-9_-]*", first[field])), (tool, field)
        guide = invoke(server, "harness_schema", tool=tool)
        assert not guide["isError"]
        contract = guide["structuredContent"]["owningContract"]
        assert contract == first
        if tool == "harness_reviewer_catalog":
            assert "confirmation" not in contract and "confirmationRoute" not in contract
            assert "supplied observations" in contract["discoveryRoute"]
        else:
            assert "null" in contract["confirmation"]
            assert "typed-confirmation-producer-unavailable" in contract["confirmationRoute"]
        assert "reviewer-catalog-producer-unavailable" in contract["discoveryRoute"]
        next_tool = contract["nextTool"]
        assert next_tool == {"name": "harness_preflight", "arguments": {}}
        diagnostic = invoke(server, next_tool["name"], **next_tool["arguments"])
        assert not diagnostic["isError"]
        report = diagnostic["structuredContent"]
        assert report["executionReadiness"] == "not-proven" and report["didRunProvider"] is False
        assert all(p["authentication"] == "unknown" for p in report["providers"])
        assert server.store.spine().plan_revision() == revision
        assert (
            server.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events
        )
    finally:
        server.close()


def test_cli_missing_intake_waves_next_reaches_complete_schema_then_preflight(tmp_path, capsys):
    from codex_harness.core.plan_cli import main

    def call(*args):
        result = main(["--workspace", str(tmp_path), "--json", *args])
        return result, json.loads(capsys.readouterr().out)

    assert call("start", "--idea", "A bounded task", "--name", "Draft")[0] == 0
    code, missing = call("waves")
    assert code == 0 and missing["data"]["intake"] is None
    assert missing["next"].endswith("schema --tool harness_intake")
    code, guide = call("schema", "--tool", "harness_intake")
    assert code == 0
    assert "constraints" in guide["data"]["owningContract"]
    assert (
        "typed-confirmation-producer-unavailable"
        in guide["data"]["owningContract"]["confirmationRoute"]
    )
    assert guide["next"].endswith("preflight")
    code, diagnostic = call("preflight")
    assert code == 0 and diagnostic["data"]["executionReadiness"] == "not-proven"
    assert diagnostic["data"]["didRunProvider"] is False
