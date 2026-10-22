"""Public planning audit through the real engine and persisted gate consumer.

Only disposable source-test stubs run. Native client responses are synthetic,
human identity stays unverified, and no provider or P1 product is launched.
"""

import json

import pytest
from tests.python.core import test_test_audit
from tests.python.core.test_mcp_planner import native_confirmation_result

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PHASES, open_plan_store_readonly

prospective = test_test_audit.prospective


def invoke(server, tool_name, **arguments):
    return server.handle_line(
        json.dumps(
            dict(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params=dict(
                    name=tool_name,
                    arguments={"expected_workspace": str(server.workspace), **arguments},
                ),
            )
        )
    )["result"]


def started(prospective):
    server = PlannerServer(prospective["template"].parent)
    assert not invoke(server, "harness_plan_start", idea="Source-only red stub", name="Audit")[
        "isError"
    ]
    nodes = []
    for original in prospective["spine"].to_dict()["nodes"]:
        node = {**original, "id": "V1" if original["id"] == "V" else original["id"]}
        node["parents"] = ["V1" if key == "V" else key for key in original["parents"]]
        nodes.append(node)
    saved = invoke(server, "harness_upsert_nodes", nodes=nodes)
    assert not saved["isError"], saved
    compiled = invoke(server, "harness_compile")
    assert not compiled["isError"], compiled
    while server.store.phase() != "roundtable":
        phase = server.store.phase()
        if phase in ("align", "vision-lock"):
            confirmed = native_confirmation_result(
                server, phase, "SOURCE-ONLY synthetic client response, not human acceptance"
            )
            assert not confirmed["isError"]
            assert confirmed["structuredContent"]["human_identity_verified"] is False
        assert not invoke(server, "harness_advance", to_phase=PHASES[PHASES.index(phase) + 1])[
            "isError"
        ]
    return server, compiled["structuredContent"]["planRevision"]


def test_public_prospective_audit_clears_only_matching_audit_gate(prospective):
    """Catch the missing native route and false or lost prospective audit credit.

    CLI and engine tests miss public discovery/help, workspace dispatch and the
    actual stored audit's consumption by public advance after reopening.
    """
    assert "harness_audit" in {tool["name"] for tool in PlannerServer(".")._wire_tools()}
    server, revision = started(prospective)
    root = server.workspace
    try:
        blocked = invoke(server, "harness_advance", to_phase="publish")
        assert blocked["isError"] and "passing audit" in blocked["content"][0]["text"]
        guide = invoke(server, "harness_schema", tool="harness_audit")
        assert not guide["isError"]
        assert guide["structuredContent"]["inputSchema"]["properties"]["mode"]["enum"] == [
            "prospective"
        ]
        approvals = server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0]
        audited = invoke(server, "harness_audit", mode="prospective")
        assert not audited["isError"], audited
        result = audited["structuredContent"]
        assert result["ok"] is True and result["proofLevel"] == "prospective"
        assert result["manifestRevision"] == revision and result["warning"]
        assert result["integrationChecked"] is False
        assert result["redBaseline"][0]["passed"] is False
        assert all(item["passed"] for item in result["collection"])
        assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == (
            approvals
        )
    finally:
        server.close()
    with open_plan_store_readonly(root) as saved:
        assert saved.passing_audit(revision)["id"] == result["id"]
        assert saved.proof_level(revision) == "prospective"
        assert not saved.successful_reviews(revision)
    resumed = PlannerServer(root)
    try:
        refused = invoke(resumed, "harness_advance", to_phase="publish")
        assert refused["isError"] and "successful review" in refused["content"][0]["text"]
        assert "Missing passing audit" not in refused["content"][0]["text"]
        assert resumed.store.phase() == "roundtable"
        proof = prospective["template"] / "tests/test_app.py"
        proof.write_text(proof.read_text() + "\n# A changed protected proof revision.\n")
        recompiled = invoke(resumed, "harness_compile")
        changed = recompiled["structuredContent"]["planRevision"]
        assert not recompiled["isError"] and changed != revision
        stale = invoke(resumed, "harness_advance", to_phase="publish")
        assert stale["isError"] and "passing audit" in stale["content"][0]["text"]
        assert not resumed.store.passing_audit(changed)
        assert resumed.store.passing_audit(revision)["id"] == result["id"]
    finally:
        resumed.close()


def test_public_failed_audit_records_findings_without_granting_credit(prospective):
    """Catch an adapter treating engine failure as success or dropping its receipt.

    Existing engine refusals do not prove the public result and persisted failure
    keep the actual advance consumer held without a fabricated passing audit.
    """
    server, _ = started(prospective)
    try:
        proof = prospective["template"] / "tests/test_app.py"
        proof.write_text("invalid Python syntax here!\n")
        compiled = invoke(server, "harness_compile")
        assert not compiled["isError"]
        revision = compiled["structuredContent"]["planRevision"]
        audited = invoke(server, "harness_audit", mode="prospective")
        assert audited["isError"]
        result = audited["structuredContent"]
        assert result["ok"] is False and result["proofLevel"] == "prospective"
        assert any(item["code"] == "prospective-syntax-error" for item in result["findings"])
        row = server.store.connection.execute(
            "SELECT manifest_revision, passed, result_json FROM audit_runs WHERE id=?",
            (result["id"],),
        ).fetchone()
        assert row[0] == revision and row[1] == 0
        assert json.loads(row[2])["findings"] == result["findings"]
        assert not server.store.passing_audit(revision)
        blocked = invoke(server, "harness_advance", to_phase="publish")
        assert blocked["isError"] and server.store.phase() == "roundtable"
    finally:
        server.close()


def test_public_audit_binding_and_input_refuse_before_effects(tmp_path, monkeypatch):
    """Catch audit bypassing the workspace guard or accepting outside/oracle paths.

    Engine tests lack transport assertions; older route tests cannot establish
    that this newly registered mutator receives the same early refusal boundary.
    """
    from codex_harness.core import mcp_planner

    calls = []

    def forbidden(*args, **kwargs):
        calls.append("store-or-dispatch")
        raise AssertionError("Refuse before store or audit subprocess effects")

    monkeypatch.setattr(mcp_planner, "open_plan_store", forbidden)
    monkeypatch.setattr(mcp_planner, "open_plan_store_readonly", forbidden)
    monkeypatch.setattr(PlannerServer, "_dispatch", forbidden)
    root = tmp_path / "absent-project"
    server = PlannerServer(root)
    try:
        for arguments, code in (
            ({"mode": "prospective"}, "workspace-assertion-required"),
            (
                {"mode": "prospective", "expected_workspace": str(tmp_path / "other")},
                "workspace-mismatch",
            ),
        ):
            response = server.handle_line(
                json.dumps(
                    dict(
                        jsonrpc="2.0",
                        id=1,
                        method="tools/call",
                        params=dict(name="harness_audit", arguments=arguments),
                    )
                )
            )["result"]
            assert response["isError"] and code in response["content"][0]["text"]
        for arguments in (
            {"mode": "oracle-verified"},
            {"mode": "prospective", "template": str(tmp_path / "outside")},
        ):
            assert invoke(server, "harness_audit", **arguments)["isError"]
        assert calls == [] and not root.exists()
    finally:
        server.close()


def test_public_audit_refuses_template_link_outside_workspace(prospective, tmp_path, monkeypatch):
    """Catch a fixed-looking template path resolving outside the project.

    Argument confinement alone does not catch a project-local link; the existing
    compile-route check is now required before the new audit engine is called.
    """
    from codex_harness.core import test_audit

    server, _ = started(prospective)
    external = tmp_path.parent / (tmp_path.name + "-external-template")
    prospective["template"].rename(external)
    link = prospective["template"]
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError as error:
        server.close()
        pytest.skip(f"Directory symlink privilege unavailable: {error}")
    before = list(server.store.connection.iterdump())

    def forbidden(*args, **kwargs):
        pytest.fail("A template link must refuse before the audit engine")

    monkeypatch.setattr(test_audit, "audit_prospective_tests", forbidden)
    try:
        refused = invoke(server, "harness_audit", mode="prospective")
        assert refused["isError"]
        assert "Audit template must stay in the fixed workspace" in refused["content"][0]["text"]
        assert list(server.store.connection.iterdump()) == before
    finally:
        server.close()
