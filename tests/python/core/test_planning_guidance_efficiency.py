"""Public schema/batch -> persisted ancestry -> CLI/MCP advice, without providers.

Protect feature-local turns: a complete feature hid a second empty feature, and
global test gaps could preempt finishing the current feature's bead. Existing
single-feature coverage tests cannot expose either cross-feature error. Compiler
help is checked against real public compilation, not a copied fixture answer.
Native setup uses the existing labelled synthetic client; no human claim.
"""

import json
from copy import deepcopy

from tests.python.core import test_planner_replay_gaps
from tests.python.core.test_planner_batch import invoke
from tests.python.core.test_planner_replay_gaps import confirmed_vision

from codex_harness.core import plan_cli
from codex_harness.core.mcp_planner import PlannerServer

project = test_planner_replay_gaps.project


def schema(server, **args):
    result = invoke(server, "harness_schema", **args)
    assert not result["isError"], result
    return result["structuredContent"]


def save(server, nodes):
    result = invoke(server, "harness_upsert_nodes", nodes=nodes)
    assert not result["isError"], result


def turn(server, capsys, feature, stage):
    before = list(server._store().connection.iterdump())
    wire = invoke(server, "harness_plan_status")
    assert not wire["isError"], wire
    actual = wire["structuredContent"]["planningTurn"]
    assert actual["stage"] == stage, actual
    assert actual["featureId"] == feature
    assert plan_cli.main(["--workspace", str(server.workspace), "--json", "status"]) == 0
    cli = json.loads(capsys.readouterr().out)
    assert cli["status"]["planningTurn"] == actual
    if feature:
        assert feature in wire["content"][0]["text"]
        assert "harness_schema tool=harness_upsert_nodes" in actual["guidance"]
    assert list(server._store().connection.iterdump()) == before
    return actual


def test_empty_second_feature_continues_through_public_batch_and_reopened_guidance(project, capsys):
    """Catch false ready after F1 and ignored story ancestry; one-feature tests miss F2."""
    confirmed_vision(project)
    server = PlannerServer(project.workspace)
    try:
        guide = schema(server, tool="harness_upsert_nodes")
        feature = deepcopy(next(n for n in guide["prerequisiteNodes"] if n["kind"] == "feature"))
        feature["id"] = "F2"
        feature["fields"] = {"optional": False, "count": 0, "notes": "", "items": []}
        save(server, [feature])
        current = turn(server, capsys, "F2", "requirements")
        assert current["requirementIds"] == []
        assert "F2" in current["guidance"]
        story = deepcopy(schema(server, kind="story")["example"]["node"])
        story.update(id="S2", parents=["F2"])
        requirement, test = deepcopy(guide["example"]["nodes"])
        requirement.update(id="R2", parents=["S2"])
        test.update(id="T2", parents=["R2"])
        requirement["text"] = "Keep every accepted requirement word. " * 20
        save(server, [requirement, story])  # Forward references use the existing atomic writer.
        assert turn(server, capsys, "F2", "tests")["requirementIds"] == ["R2"]
        save(server, [test])
        assert turn(server, capsys, "F2", "bead")["requirementIds"] == ["R2"]
        bead = deepcopy(schema(server, kind="bead")["example"]["node"])
        bead.update(id="B2", parents=["R2"])
        save(server, [bead])
        turn(server, capsys, None, "ready")
    finally:
        server.close()
    reopened = PlannerServer(project.workspace)
    try:
        turn(reopened, capsys, None, "ready")
        spine = reopened._store().spine()
        assert spine.node("F2")["fields"] == feature["fields"]
        assert spine.node("R2")["text"] == requirement["text"]
        assert "F2" in spine.ancestors("R2")
    finally:
        reopened.close()


def test_current_feature_bead_precedes_later_feature_test_gap(project, capsys):
    """Catch global test priority sending F2 before finishing F1; coverage tests miss order."""
    confirmed_vision(project)
    server = PlannerServer(project.workspace)
    try:
        guide = schema(server, tool="harness_upsert_nodes")
        feature = deepcopy(next(n for n in guide["prerequisiteNodes"] if n["kind"] == "feature"))
        feature["id"] = "F2"
        requirement = deepcopy(guide["example"]["nodes"][0])
        requirement.update(id="R2", parents=["F2"])
        decision = deepcopy(schema(server, kind="decision")["example"]["node"])
        bead = project.spine().node("B1")
        bead["parents"] = [decision["id"]]  # Valid existing graph; R1 now has no bead.
        save(server, [decision, bead, feature, requirement])
        current = turn(server, capsys, "F1", "bead")
        assert current["requirementIds"] == ["R1"]
    finally:
        server.close()


def test_unbound_requirement_gaps_remain_visible_after_connected_features_finish(project, capsys):
    """Catch false ready for a valid parentless draft; feature-only tests miss it.

    Real batch/status/CLI prove advice without introducing parent validation.
    """
    confirmed_vision(project)
    server = PlannerServer(project.workspace)
    try:
        requirement, test = deepcopy(
            schema(server, tool="harness_upsert_nodes")["example"]["nodes"]
        )
        requirement.update(id="R-unbound", parents=[])
        save(server, [requirement])
        assert project.spine().node(requirement["id"]) == requirement
        current = turn(server, capsys, None, "tests")
        assert current["requirementIds"] == ["R-unbound"]
        assert "Unbound" in current["guidance"] and "R-unbound" in current["guidance"]
        assert project.spine().coverage_gaps() == [
            {"id": "R-unbound", "gap": "requirement-without-bead"},
            {"id": "R-unbound", "gap": "requirement-without-test"},
        ]
        test.update(id="T-unbound", parents=["R-unbound"])
        save(server, [test])
        assert turn(server, capsys, None, "bead")["requirementIds"] == ["R-unbound"]
        assert project.spine().node("R-unbound")["parents"] == []
        requirement["parents"] = ["F1"]
        save(server, [requirement])
        assert turn(server, capsys, "F1", "bead")["requirementIds"] == ["R-unbound", "R1"]
    finally:
        server.close()


def test_first_use_batch_and_test_help_match_real_single_e2e_suite_compiler(project):
    """Catch late two-e2e refusal and undocumented metadata; fixtures already know both."""
    server = PlannerServer(project.workspace)
    try:
        approvals = project.connection.execute("SELECT count(*) FROM approvals").fetchone()[0]
        before = list(project.connection.iterdump())
        batch = schema(server, tool="harness_upsert_nodes")
        test_help = schema(server, kind="test")
        compile_help = schema(server, tool="harness_compile")
        limits = batch["compilationLimits"]
        assert limits["maxE2eNodes"] == 1
        assert "multiple cases" in limits["integration"]
        assert test_help["compilationLimits"] == compile_help["compilationLimits"] == limits
        assert "compilationMetadata" in batch
        assert batch["compilationMetadata"] == compile_help["compilationMetadata"]
        assert list(project.connection.iterdump()) == before
        suite = project.workspace / "template/tests/test_journey.py"
        suite.write_text(
            suite.read_text() + "    def test_another_case(self): self.assertTrue(True)\n"
        )
        compiled = invoke(server, "harness_compile")
        assert not compiled["isError"], compiled
        assert compiled["structuredContent"]["integration"]["proofModule"] == "test_journey.py"
        duplicate = deepcopy(test_help["example"]["node"])
        duplicate.update(id="TE2", parents=["R1"])
        duplicate["fields"].update(level="e2e", module="test_journey.py")
        # The batch completes even though the subsequent compiler-based status fails.
        duplicate_result = invoke(server, "harness_upsert_nodes", nodes=[duplicate])
        assert duplicate_result["isError"] is False
        assert duplicate_result["statusAvailable"] is False
        assert duplicate_result["structuredContent"] == {"ids": ["TE2"]}
        assert project.spine().node("TE2") == duplicate
        refused = invoke(server, "harness_compile")
        assert refused["isError"] and "spine-integration-invalid" in refused["content"][0]["text"]
        assert (
            project.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == approvals
        )
    finally:
        server.close()
