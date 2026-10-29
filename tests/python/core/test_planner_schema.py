"""Schema recovery at the real MCP/store boundary; no provider execution."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from tests.python.core.planning_support import source_only_expansion

from codex_harness.core.mcp_planner import PlannerServer


def invoke(server, tool_name, **arguments):
    # Same-project fixtures explicitly name their intended workspace on the wire.
    arguments = {"expected_workspace": str(server.workspace), **arguments}
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


@pytest.mark.parametrize("source", ["PRODUCT-BRIEF.md", "specs/accepted-spec.md"])
def test_public_short_vision_example_preserves_actual_brief_and_existing_long_vision(
    tmp_path, source
):
    """Catch brief copying or a fake path; existing schema examples have no source document."""
    brief = "Preserve exact parser requirements, edge cases and acceptance details.\n" * 120
    document = tmp_path / source
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_bytes(brief.encode("utf-8"))
    original_bytes = document.read_bytes()
    server = PlannerServer(tmp_path)
    try:
        assert not invoke(
            server, "harness_plan_start", plan_mode="legacy", idea=brief, name="Parser"
        )["isError"]
        original = deepcopy(server.store.spine().node("V1"))
        guide = invoke(server, "harness_schema", kind="vision")["structuredContent"]
        # Teaching/help does not migrate or truncate any existing user-authored vision.
        assert server.store.spine().node("V1") == original
        shown = invoke(server, "harness_show_node", id="V1")["structuredContent"]
        assert shown["text"] == original["text"] == brief
        status = invoke(server, "harness_plan_status", full=True)
        assert brief in status["content"][0]["text"]
        node = deepcopy(guide["example"]["node"])
        taught = node["text"]
        assert "<actual source path>" in taught, taught
        node["text"] = taught.replace("<actual source path>", source)
        node["fields"]["vision"] = node["text"]
        assert len(node["text"]) < 300 and source in node["text"]
        assert brief not in node["text"]
        result = invoke(server, "harness_upsert_node", node=node)
        assert not result["isError"], result
        saved = invoke(server, "harness_show_node", id="V1")["structuredContent"]
        assert saved["text"] == node["text"] and saved["fields"] == node["fields"]
        assert document.read_bytes() == original_bytes
        if source != "PRODUCT-BRIEF.md":
            assert not (tmp_path / "PRODUCT-BRIEF.md").exists()
            assert "PRODUCT-BRIEF.md" not in saved["text"]
        assert invoke(server, "harness_advance", to_phase="expand")["isError"]
        assert not server.store.status()["approval"]["granted"]
    finally:
        server.close()


def test_public_compile_help_populates_existing_vision_metadata_and_compiles(tmp_path):
    """Protect schema-first compile: omitted argv/path shapes caused a live refusal.

    Existing compile tests prepopulate fixture metadata and miss learning by error.
    """
    from shutil import copytree

    from tests.python.core.test_mcp_planner import FIXTURE, add_fixture, start

    server = PlannerServer(tmp_path)
    try:
        start(server)
        add_fixture(server)
        vision = invoke(server, "harness_show_node", id="V1")["structuredContent"]
        node = {key: vision[key] for key in ("id", "kind", "title", "text", "parents", "fields")}
        node["fields"].pop("validation")
        node["fields"].pop("requirementFiles")
        # Initial authoring still accepts a vision without compilation metadata.
        assert not invoke(server, "harness_upsert_node", node=node)["isError"]
        original = deepcopy(node)
        vision_help = invoke(server, "harness_schema", kind="vision")["structuredContent"]
        compile_help = invoke(server, "harness_schema", tool="harness_compile")["structuredContent"]
        contract = compile_help["compilationMetadata"]
        assert vision_help["compilationMetadata"] == contract
        node["fields"].update(contract["fieldExample"])
        assert not invoke(server, "harness_upsert_node", node=node)["isError"]
        copytree(FIXTURE.parent / "template" / "tests", tmp_path / "template" / "tests")
        compiled = invoke(server, "harness_compile")
        assert not compiled["isError"], compiled
        manifest = compiled["structuredContent"]
        assert manifest["validation"] == contract["fieldExample"]["validation"]
        assert manifest["planRevision"] == server.store.manifest_revision()
        stored = server.store.spine().node("V1")
        for key in ("validation", "requirementFiles"):
            stored["fields"].pop(key)
        assert stored == original
        assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    finally:
        server.close()


@pytest.mark.parametrize("done_key", ["done_feels_like", "doneFeelsLike", "done"])
def test_public_vision_schema_populates_native_done_summary_without_losing_detail(
    tmp_path, done_key
):
    """Protect Done aliases and full detail across the public writer/request/readback.

    Compact cards intentionally omit extra sections; card-only assertions miss loss
    from the canonical request or the public full-detail route that the card names.
    """
    from tests.python.core.test_mcp_planner import answer_native, begin_native

    server = PlannerServer(tmp_path)
    try:
        discovered = invoke(server, "harness_schema", kind="vision")
        assert not discovered["isError"]
        guide = discovered["structuredContent"]
        assert not (tmp_path / ".codex-harness").exists()
        example = deepcopy(guide["example"]["node"])
        done = example["fields"].pop("done_feels_like")
        example["fields"][done_key] = done
        detail = "Retained extra done_means words: accepted edge cases and complete intent."
        example["fields"]["done_means"] = detail
        assert not invoke(
            server,
            "harness_plan_start",
            plan_mode="legacy",
            idea="Source-only schema example",
            name="Schema demo",
        )["isError"]
        source_only_expansion(server._store())
        example["fields"]["expansion"] = server.store.spine().node("V1")["fields"]["expansion"]
        assert not invoke(server, "harness_upsert_node", node=example)["isError"]
        assert invoke(server, "harness_advance", to_phase="expand")["isError"]

        align = begin_native(server)
        assert align["method"] == "elicitation/create"
        aligned = answer_native(
            server,
            align["id"],
            approve=True,
            statement="Synthetic source-client setup to reach vision-lock; no human claim.",
            conditions="",
        )
        assert aligned["result"]["structuredContent"]["state"] == "approved"
        assert not invoke(server, "harness_advance", to_phase="expand")["isError"]
        assert not invoke(server, "harness_advance", to_phase="vision-lock")["isError"]
        requested = begin_native(server, gate="vision-lock")
        assert requested["method"] == "elicitation/create"
        card = requested["params"]["message"]
        assert f"Done:\n{done}" in card
        assert "proposed dispositions" in card
        for upgrade in example["fields"]["expansion"]["upgrades"]:
            assert all(value in card for value in upgrade.values())
        # The real bridge persisted the complete scope, not just the native summary.
        full_request = json.loads(
            server.store.connection.execute(
                "SELECT payload_json FROM events WHERE kind='confirmation-requested' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        )
        assert full_request["gate"] == "vision-lock"
        assert full_request["plan_revision"] == server.store.spine().plan_revision()
        assert full_request["plan_revision"] in card
        assert full_request["vision"] == example
        assert f"Done:\n{done}" in full_request["card"]
        assert f"Done means:\n{detail}" in full_request["card"]
        for key in ("done_feels_like", "doneFeelsLike", "done", "done_means"):
            assert key in guide["fieldRules"]
        stored = invoke(server, "harness_show_node", id=example["id"])["structuredContent"]
        assert stored["fields"] == example["fields"]
        cancelled = answer_native(server, requested["id"], action="cancel")
        assert cancelled["result"]["structuredContent"]["state"] == "cancel"
        assert invoke(server, "harness_advance", to_phase="features")["isError"]
        status = invoke(server, "harness_plan_status")["structuredContent"]
        assert status["phase"] == "vision-lock"
        assert status["approval"]["gate"] != "vision-lock" or not status["approval"]["granted"]
    finally:
        server.close()


def typed_template(server, tool):
    guide = invoke(server, "harness_schema", tool=tool)["structuredContent"]
    template = deepcopy(guide["draftTemplate"])
    assert guide["templateState"] == "unconfirmed-draft; bind prerequisites before submission"
    assert guide["revisionBinding"] == {
        "tool": "harness_plan_status",
        "resultKey": "spine_revision",
        "argument": "expected_spine_revision",
    }
    for node in guide["prerequisiteNodes"]:
        assert not invoke(server, "harness_upsert_node", node=node)["isError"]
    template["expected_spine_revision"] = invoke(server, "harness_plan_status")[
        "structuredContent"
    ]["spine_revision"]
    print(json.dumps({"tool": tool, "emittedGuide": guide, "boundDraft": template}))
    return guide, template


def test_ordinary_reviewer_action_schema_is_cold_unbound_and_distinct_from_intake(tmp_path):
    server = PlannerServer(tmp_path / "absent")
    try:
        guide = invoke(server, "harness_schema", tool="harness_confirm_reviewer_choice")
        assert not guide["isError"], guide
        data = guide["structuredContent"]
        expected = {
            "node_id",
            "expected_spine_revision",
            "approve",
            "statement",
            "conditions",
            "expected_workspace",
        }
        assert set(data["inputSchema"]["required"]) == expected
        assert set(data["inputSchema"]["properties"]) == expected
        assert data["inputSchema"]["additionalProperties"] is False
        assert data["inputSchema"]["properties"]["approve"]["type"] == "boolean"
        assert data["inputSchema"]["properties"]["conditions"]["type"] == "string"
        assert data["actionTemplate"] == {
            "node_id": None,
            "expected_spine_revision": None,
            "approve": None,
            "statement": None,
            "conditions": "",
        }
        contract = data["owningContract"]
        assert "human_identity_verified=false" in contract["remainingHolds"]
        assert "reviewer-confirmation-required" in contract["remainingHolds"]
        assert "reviewer-catalog-producer-unavailable" in data["producerLimits"]
        choice = invoke(server, "harness_schema", tool="harness_reviewer_choice")[
            "structuredContent"
        ]
        assert "harness_confirm_reviewer_choice" in choice["owningContract"]["confirmationRoute"]
        assert "typed-confirmation-producer-unavailable" not in choice["producerLimits"]
        intake = invoke(server, "harness_schema", tool="harness_intake")["structuredContent"]
        assert (
            "typed-confirmation-producer-unavailable"
            in intake["owningContract"]["confirmationRoute"]
        )
        assert (
            "harness_record_confirmation only serves align"
            in intake["owningContract"]["confirmationRoute"]
        )
        assert not server.workspace.exists() and server.store is None
        guide_text = (
            Path(__file__).resolve().parents[3] / "docs/operator/planner-guide.md"
        ).read_text(encoding="utf-8")
        for text in (
            "harness_confirm_reviewer_choice",
            "caller-declared tester-as-user",
            "human_identity_verified=false",
            "reviewer-catalog-producer-unavailable",
            "typed-confirmation-producer-unavailable",
        ):
            assert text in guide_text
    finally:
        server.close()


def test_emitted_intake_draft_roundtrips_public_parser_and_store(tmp_path):
    server = PlannerServer(tmp_path)
    try:
        invoke(
            server,
            "harness_plan_start",
            plan_mode="legacy",
            idea="Reject malformed parser input",
            name="Parser",
        )
        guide, payload = typed_template(server, "harness_intake")
        assert payload["node"]["fields"]["confirmation"] is None
        result = invoke(server, "harness_intake", **payload)
        assert not result["isError"], result
        recorded = result["structuredContent"]
        print(json.dumps({"tool": "harness_intake", "request": payload, "response": result}))
        assert recorded["state"] == "draft" and recorded["lockState"] == "unconfirmed"
        assert recorded["approvalGranted"] is False
        stored = server.store.spine().node(payload["node"]["id"])["fields"]
        assert stored["confirmation"] is None and stored["recipe"] == "coherent-plan"
        assert stored["delta"] == payload["node"]["fields"]["delta"]
        assert "typed-confirmation-producer-unavailable" in guide["producerLimits"]
        assert server.store.phase() == "align"
    finally:
        server.close()


def test_emitted_catalog_and_choice_drafts_require_actual_supplied_bindings(tmp_path):
    # These observations are source-only doubles, never actual discovery or entitlement.
    from tests.python.core.test_intake import observation, selection

    server = PlannerServer(tmp_path)
    try:
        invoke(
            server,
            "harness_plan_start",
            plan_mode="legacy",
            idea="Review parser behavior",
            name="Parser",
        )
        catalog_guide, payload = typed_template(server, "harness_reviewer_catalog")
        assert payload["catalog"]["id"] is None and payload["catalog"]["observedAt"] is None
        assert payload["catalog"]["providers"] == []
        assert set(catalog_guide["providerTemplate"]) == {
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
        }
        assert set(catalog_guide["catalogChoiceTemplate"]) == {
            "model",
            "effort",
            "availability",
            "reviewPathSupported",
            "reason",
        }
        assert catalog_guide["providerTemplate"]["authentication"] == "unknown"
        assert catalog_guide["catalogChoiceTemplate"]["availability"] == "unknown"
        assert catalog_guide["effortTemplates"] == [
            {"kind": "named", "value": None},
            {"kind": "unsupported"},
        ]
        assert catalog_guide["sourceTemplates"] == [
            {"kind": kind, "reference": None, "digest": None}
            for kind in ("supported-list", "provider-local-metadata")
        ]
        assert "derived digest/choiceId" in catalog_guide["observationBindings"]
        before = server.store.spine().plan_revision()
        events = server.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        unbound = invoke(server, "harness_reviewer_catalog", **payload)
        print(
            json.dumps(
                {"tool": "harness_reviewer_catalog", "unbound": payload, "response": unbound}
            )
        )
        assert unbound["isError"] and "value-text-invalid" in unbound["content"][0]["text"]
        assert server.store.spine().plan_revision() == before
        assert (
            server.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events
        )
        assert "reviewer-catalog-producer-unavailable" in catalog_guide["producerLimits"]
        payload["catalog"] = observation()
        result = invoke(server, "harness_reviewer_catalog", **payload)
        assert not result["isError"], result
        catalog = result["structuredContent"]["catalog"]
        print(json.dumps({"sourceOnly": True, "suppliedCatalog": payload, "response": result}))
        assert result["structuredContent"]["approvalGranted"] is False
        assert "digest" in catalog and "choiceId" in catalog["providers"][0]["choices"][0]
        guide, choice = typed_template(server, "harness_reviewer_choice")
        fields = choice["node"]["fields"]
        assert fields["catalogId"] is None and fields["catalogDigest"] is None
        assert fields["reviewers"] == [] and fields["confirmation"] is None
        assert guide["reviewerTemplate"]["choiceId"] is None
        assert guide["reviewerTemplate"]["model"] is None
        assert fields["assurance"] == {
            "independentRequired": None,
            "minIndependentReviewers": None,
            "crossProviderRequired": None,
            "basisRefs": [],
        }
        assert "do not reduce that policy" in guide["catalogBindings"]
        assert (
            "harness_record_confirmation supports only align/vision-lock" in guide["producerLimits"]
        )
        unbound_choice = invoke(server, "harness_reviewer_choice", **choice)
        print(json.dumps({"unboundChoice": choice, "response": unbound_choice}))
        assert (
            unbound_choice["isError"]
            and "value-text-invalid" in unbound_choice["content"][0]["text"]
        )
        # Bind only the exact normalized supplied catalog tuple and explicit test policy.
        fields.update(selection(catalog))
        fields["confirmation"] = None
        result = invoke(server, "harness_reviewer_choice", **choice)
        assert not result["isError"], result
        data = result["structuredContent"]
        print(json.dumps({"sourceOnly": True, "selection": choice, "response": result}))
        assert data["approvalGranted"] is False and data["state"] == "draft"
        assert any(
            item.startswith("reviewer-confirmation-required:") for item in data["unmetRequirements"]
        )
        assert server.store.spine().node(choice["node"]["id"])["fields"]["confirmation"] is None
        assert "harness_confirm_reviewer_choice" in guide["producerLimits"]
        assert "typed-confirmation-producer-unavailable" not in guide["producerLimits"]
    finally:
        server.close()


def test_cli_emitted_intake_template_roundtrips_from_another_cwd(tmp_path):
    workspace, elsewhere = tmp_path / "workspace", tmp_path / "elsewhere"
    workspace.mkdir()
    elsewhere.mkdir()
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[3] / "src"))

    def call(*args):
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "codex_harness.core.plan_cli",
                "--workspace",
                str(workspace),
                "--json",
                *args,
            ],
            cwd=elsewhere,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        print(
            json.dumps(
                {
                    "argv": process.args,
                    "cwd": str(elsewhere),
                    "exitCode": process.returncode,
                    "stdout": process.stdout,
                    "stderr": process.stderr,
                }
            )
        )
        assert process.returncode == 0, process.stderr or process.stdout
        return json.loads(process.stdout)

    call("start", "--plan-mode", "legacy", "--idea", "Reject malformed input", "--name", "Parser")
    guide = call("schema", "--tool", "harness_intake")["data"]
    for index, node in enumerate(guide["prerequisiteNodes"]):
        path = tmp_path / f"node-{index}.json"
        path.write_text(json.dumps(node), encoding="utf-8")
        call("node", "--json", "@" + str(path))
    payload = guide["draftTemplate"]
    payload["expected_spine_revision"] = call("status")["data"]["spine_revision"]
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = call("intake", "--json", "@" + str(path))["data"]
    assert result["state"] == "draft" and result["approvalGranted"] is False
    assert result["intake"]["confirmation"] is None
    assert call("show", payload["node"]["id"])["data"]["fields"]["confirmation"] is None


@pytest.mark.parametrize("fault", ["missing", "enum", "origin", "revision", "digest", "source"])
def test_typed_draft_invalid_bindings_refuse_atomically(tmp_path, fault):
    from tests.python.core.test_intake import authority, observation

    server = PlannerServer(tmp_path)
    try:
        invoke(
            server,
            "harness_plan_start",
            plan_mode="legacy",
            idea="Reject malformed input",
            name="Parser",
        )
        _, payload = typed_template(server, "harness_intake")
        repaired = deepcopy(payload)
        tool, expected = "harness_intake", "value-shape-invalid"
        fields = payload["node"]["fields"]
        if fault == "missing":
            del fields["delta"]["successCriteria"]
        elif fault == "enum":
            fields["requestKind"] = "repair"
            expected = "value-enum-invalid"
        elif fault == "origin":
            fields["confirmation"] = authority(origin="invented-authority")
            expected = "value-enum-invalid"
        elif fault == "revision":
            # Actual previously current revision, made stale by a public edit.
            vision = deepcopy(server.store.spine().node("V1"))
            vision["text"] += " Changed accepted draft context."
            assert not invoke(server, "harness_upsert_node", node=vision)["isError"]
            expected = "plan-spine-stale"
        else:
            tool = "harness_reviewer_catalog"
            _, payload = typed_template(server, tool)
            payload["catalog"] = observation()  # Explicit source-only double.
            repaired = deepcopy(payload)
            if fault == "digest":
                payload["catalog"]["digest"] = "0" * 64
                expected = "reviewer-catalog-hash-mismatch"
            else:
                payload["catalog"]["providers"][0]["source"]["kind"] = "invented-origin"
                expected = "value-enum-invalid"
        before = server.store.spine().plan_revision()
        events = server.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        result = invoke(server, tool, **payload)
        print(json.dumps({"fault": fault, "tool": tool, "request": payload, "response": result}))
        assert result["isError"] and expected in result["content"][0]["text"], result
        assert server.store.spine().plan_revision() == before
        assert (
            server.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events
        )
        repaired["expected_spine_revision"] = invoke(server, "harness_plan_status")[
            "structuredContent"
        ]["spine_revision"]
        retry = invoke(server, tool, **repaired)
        print(json.dumps({"fault": fault, "repairRequest": repaired, "retryResponse": retry}))
        assert not retry["isError"], retry
        assert retry["structuredContent"]["state"] == "draft"
        assert retry["structuredContent"]["approvalGranted"] is False
        if tool == "harness_intake":
            assert (
                server.store.spine().node(repaired["node"]["id"])["fields"]["confirmation"] is None
            )
    finally:
        server.close()


def test_first_proof_authoring_help_exposes_stub_and_audit_prerequisites_without_creating_plan(
    tmp_path, capsys
):
    """Catch discovering missing CLI stubs only after a zero-finding review.

    Audit-route tests ask for help at audit time; cold example tests only save nodes.
    Check the public first-use MCP and CLI help instead, without running a product.
    """
    from codex_harness.core.plan_cli import main

    server = PlannerServer(tmp_path)
    try:
        guides = [
            invoke(server, "harness_schema", kind=kind)["structuredContent"]
            for kind in ("test", "bead")
        ]
        guides.append(
            invoke(server, "harness_schema", tool="harness_upsert_nodes")["structuredContent"]
        )
        assert main(["--workspace", str(tmp_path), "--json", "schema", "--kind", "test"]) == 0
        guides.append(json.loads(capsys.readouterr().out)["data"])
        for guide in guides:
            help_text = guide["proofAuthoring"]
            assert "before compile/review" in help_text["prepare"]
            assert "does not create those files" in help_text["prepare"]
            assert "tally/__main__.py" in help_text["cli"]
            assert "NotImplementedError" in help_text["cli"]
            assert "arbitrary helper/control-flow" in help_text["cli"]
            assert "not product implementation" in help_text["scope"]
            assert "Zero review findings do not establish a passing audit" in help_text["evidence"]
            assert (
                "collection, attributable red-stub failures and interface" in help_text["evidence"]
            )
        assert not (tmp_path / ".codex-harness").exists()
    finally:
        server.close()


def test_cli_schema_keeps_full_human_contract_and_compact_machine_view(tmp_path, capsys):
    """Catch compact MCP text hiding CLI help; wire-consumption tests miss CLI rendering."""
    from codex_harness.core.plan_cli import main

    request = ["schema", "--kind", "bead"]
    assert main(["--workspace", str(tmp_path), *request]) == 0
    human = capsys.readouterr().out
    assert '"inputSchema"' in human and '"ownedFiles"' in human
    assert main(["--workspace", str(tmp_path), "--json", *request]) == 0
    machine = json.loads(capsys.readouterr().out)
    assert "inputSchema" in machine["data"] and "ownedFiles" in machine["data"]["fieldRules"]
    assert '"inputSchema"' not in machine["text"]


def test_schema_discovery_before_start_has_usable_examples_without_creating_plan(tmp_path):
    """Catch duplicate schema text; field-only checks miss a verbose copy beside structured data."""
    # A cold planner cannot recover by reading source. tools/list only describes
    # fields as an opaque object, so it misses the bead/test repair contract.
    server = PlannerServer(tmp_path)
    try:
        guide = invoke(server, "harness_schema", kind="bead")
        assert not guide["isError"], guide
        data = guide["structuredContent"]
        text = guide["content"][0]["text"]
        assert "structuredContent" in text and '"inputSchema"' not in text
        assert len(text.encode("utf-8")) < 2000
        assert not (tmp_path / ".codex-harness").exists()
        assert data["inputSchema"]["additionalProperties"] is False
        assert "ownedFiles" in data["fieldRules"]
        assert data["allowedParentKinds"] == ["decision", "design", "requirement"]
        assert not invoke(
            server,
            "harness_plan_start",
            plan_mode="legacy",
            idea="Recover invalid plans",
            name="Demo",
        )["isError"]
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
        invoke(server, "harness_plan_start", plan_mode="legacy", idea="Parse input", name="Parser")
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
        invoke(server, "harness_plan_start", plan_mode="legacy", idea="Parser", name="Parser")
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
        assert contract["structuredContent"]["inputSchema"]["required"] == [
            "gate",
            "expected_workspace",
        ]
        assert (
            contract["structuredContent"]["inputSchema"]["properties"]["expected_workspace"]["type"]
            == "string"
        )
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
        invoke(
            server,
            "harness_plan_start",
            plan_mode="legacy",
            idea="Preserve accepted task",
            name="Draft",
        )
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
        elif tool == "harness_intake":
            assert "null" in contract["confirmation"]
            assert "typed-confirmation-producer-unavailable" in contract["confirmationRoute"]
        else:
            assert "null" in contract["confirmation"]
            assert "harness_confirm_reviewer_choice" in contract["confirmationRoute"]
            assert "typed-confirmation-producer-unavailable" not in contract["confirmationRoute"]
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


def test_guide_acceptance_node_route_roundtrips_without_proof_credit(tmp_path, capsys):
    from codex_harness.core.plan_cli import main
    from codex_harness.core.plan_store import open_plan_store_readonly

    guide_path = Path(__file__).resolve().parents[3] / "docs/operator/planner-guide.md"
    guide_text = guide_path.read_text(encoding="utf-8")
    paragraph = next(
        line for line in guide_text.splitlines() if line.startswith("Explicit `acceptance`")
    )
    trace = []

    def call(*arguments):
        command = ["--workspace", str(tmp_path), "--json", *arguments]
        assert main(command) == 0
        result = json.loads(capsys.readouterr().out)
        assert not result["isError"], result
        trace.append(dict(command=command, response=result))
        return result

    # The currently supported parser is a positive baseline control; the guide is the defect.
    call(
        "start",
        "--plan-mode",
        "legacy",
        "--idea",
        "Describe planned acceptance",
        "--name",
        "Acceptance contract",
    )
    schema = call("schema", "--kind", "acceptance")["data"]
    assert schema["kind"] == "acceptance"
    assert schema["allowedParentKinds"] == ["feature", "vision"]
    for node in schema["prerequisiteNodes"]:
        call("node", "--json", json.dumps(node))
    payload = schema["example"]["node"]
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    call("node", "--json", "@" + str(path))
    server = PlannerServer(tmp_path)
    try:
        status = invoke(server, "harness_plan_status")["structuredContent"]
        with open_plan_store_readonly(tmp_path) as stored:
            assert stored.spine().node(payload["id"]) == payload
            for table in (
                "acceptance_scenarios",
                "acceptance_runs",
                "acceptance_reviews",
                "approvals",
            ):
                assert stored.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert payload["fields"] == {}  # The emitted example has no executable steps.
        assert status["phase"] == "align" and status["approval"]["granted"] is False
        assert status["acceptance"]["accepted"] is False
        assert {
            "id": payload["id"],
            "gap": "acceptance scenario has no executable steps",
        } in status["coverage_gaps"]
        wire_schema = invoke(server, "harness_schema", kind="acceptance")["structuredContent"]
        # The explicit --workspace CLI producer retains the constructor contract;
        # wire calls additionally require the caller's intended-workspace assertion.
        assert wire_schema["inputSchema"]["required"] == [
            *schema["inputSchema"]["required"],
            "expected_workspace",
        ]
        assert (
            wire_schema["inputSchema"]["properties"].pop("expected_workspace")["type"] == "string"
        )
        wire_schema["inputSchema"]["required"] = schema["inputSchema"]["required"]
        assert wire_schema == schema
        print(
            json.dumps(
                dict(
                    publicCLI=trace, persistedNode=payload, status=status, guideParagraph=paragraph
                )
            )
        )
        assert "p schema --kind acceptance" in paragraph
        assert "p node --json @acceptance.json" in paragraph
        assert "harness_schema" in paragraph and "harness_upsert_node" in paragraph
        assert "not automatic" in paragraph and "proof" in paragraph
        assert "acceptance vision/feature" in guide_text
        assert "acceptance` spine nodes need a schema change" not in guide_text
    finally:
        server.close()


def test_cli_missing_intake_waves_next_reaches_complete_schema_then_preflight(tmp_path, capsys):
    from codex_harness.core.plan_cli import main

    def call(*args):
        result = main(["--workspace", str(tmp_path), "--json", *args])
        return result, json.loads(capsys.readouterr().out)

    assert (
        call("start", "--plan-mode", "legacy", "--idea", "A bounded task", "--name", "Draft")[0]
        == 0
    )
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
