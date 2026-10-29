"""Stored vision/read-view parity and public guidance; no providers or human approval."""

import argparse
from copy import deepcopy

import pytest
from tests.python.core.planning_support import source_only_confirmation, source_only_expansion

from codex_harness.core.mcp_planner import TOOLS, PlannerServer
from codex_harness.core.plan_cli import batch_nodes, parser
from codex_harness.core.plan_store import PlanStoreError, open_plan_store
from codex_harness.core.planner_confirmations import _understanding
from codex_harness.core.planner_guidance import NEXT_TARGETS, planning_turn
from codex_harness.core.planner_schema import node_example
from codex_harness.core.planner_views import status_card
from codex_harness.core.planner_vision import expansion_status, render_vision, vision_projection
from codex_harness.core.status_api import snapshot


@pytest.mark.parametrize(
    "change,path",
    [
        (lambda f: f["expansion"].update(mode="auto"), "expansion.mode"),
        (lambda f: f["expansion"].update(reason=" "), "expansion.reason"),
        (lambda f: f["expansion"].update(upgrades=[]), "expansion.upgrades"),
        (lambda f: f["expansion"]["upgrades"][1].update(id="1"), "expansion.upgrades[1].id"),
        (lambda f: f["expansion"]["upgrades"][0].update(impact=""), "expansion.upgrades[0].impact"),
        (
            lambda f: f["expansion"]["upgrades"][0].update(
                disposition=None, confirmed=True, by="user"
            ),
            "expansion.upgrades[0].disposition",
        ),
        (lambda f: f.update(quality_bar=False, quality="Don't fall back"), "quality_bar"),
        (lambda f: f.update(done_feels_like=[]), "done_feels_like"),
    ],
)
def test_incomplete_expansion_reports_semantic_gap_without_approval(change, path, tmp_path):
    """Catch populated-but-incomplete drafts; projection tests only cover rendering."""
    with open_plan_store(tmp_path) as store:
        _, vision = node_example("vision")
        store.upsert_node(vision)
        source_only_expansion(store)
        vision = store.spine().node("V1")
        complete = expansion_status(vision)
        assert complete["gaps"] == [] and complete["untracedAcceptedUpgradeRefs"] is None
        change(vision["fields"])
        assert path in {gap["path"] for gap in expansion_status(vision)["gaps"]}
        store.upsert_node(vision)
        source_only_confirmation(store, "align")
        store.advance("expand", by="planner")
        before = list(store.connection.iterdump())
        with pytest.raises(PlanStoreError, match="Expansion required before Vision Lock"):
            store.advance("vision-lock", by="planner")
        assert list(store.connection.iterdump()) == before


def test_stored_vision_reaches_summary_and_cockpit_without_alias_or_detail_loss(tmp_path):
    """Protect view parity: duplicate aliases dropped Done/extra details in the cockpit.

    Existing card-only tests cannot catch differences after persistence and readback.
    """
    server = PlannerServer(tmp_path)
    try:
        reply = server.call_tool(
            "harness_plan_start", {"plan_mode": "legacy", "idea": "Parser", "name": "Parser"}
        )
        assert not reply["isError"]
        _, vision = node_example("vision")
        vision["fields"].pop("done_feels_like")
        vision["fields"]["doneFeelsLike"] = "Every invalid input leaves the original intact."
        vision["fields"]["done_means"] = "Keep the complete additional acceptance detail."
        vision["fields"]["quality"] = "Retain a secondary alias without losing its words."
        reply = server.call_tool("harness_upsert_node", {"node": vision})
        assert not reply["isError"]
    finally:
        server.close()
    with open_plan_store(tmp_path) as store:
        stored = store.spine().node("V1")
        projection = vision_projection(stored)
        # Normal cards preserve complete established text and alias precedence.
        assert render_vision(projection) == _understanding(stored)
    result = snapshot(workspace=tmp_path)
    assert result["plan"]["visionProjection"] == projection
    assert result["cockpit"]["vision"]["sections"] == projection["sections"]
    summary = status_card(result["plan"], [])
    assert render_vision(projection) in summary
    sections = {row["label"]: row["text"] for row in projection["sections"]}
    assert sections["Done"] == vision["fields"]["doneFeelsLike"]
    assert sections["Done means"] == vision["fields"]["done_means"]
    quality = [row["text"] for row in projection["sections"] if row["label"] == "Quality"]
    assert quality == [vision["fields"]["quality_bar"], vision["fields"]["quality"]]
    # A second canonical save proves explicit falsey values do not fall back to detail.
    vision["fields"] = {"vision": "", "users": None, "scope_in": [], "scope_out": False}
    with open_plan_store(tmp_path) as store:
        store.upsert_node(vision)
    result = snapshot(workspace=tmp_path)
    assert result["cockpit"]["vision"]["promise"] == ""
    sections = {row["label"]: row["text"] for row in result["cockpit"]["vision"]["sections"]}
    assert sections["Users"] == "Not specified"
    assert sections["Scope in"] == "None recorded"
    assert sections["Scope out"] == "No"
    assert sections["Done"] == "Not recorded"


def test_guidance_targets_exist_in_both_public_interfaces():
    """Catch Next naming an absent tool/command, previously seen with compile.

    Existing command tests exercise individual routes, not the advertised target set.
    """
    commands = next(
        action.choices
        for action in parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    tools = {tool["name"] for tool in TOOLS}
    assert {"compile", "scenarios"} <= NEXT_TARGETS.keys()
    for cli, mcp in NEXT_TARGETS.values():
        assert cli in commands
        assert mcp in tools


def test_planning_turn_uses_real_coverage_after_atomic_requirements_and_tests_write(tmp_path):
    """Catch guidance stuck at tests despite indirect test ancestry or a saved batch.

    Existing CLI batch tests do not exercise the next-turn consumer of stored coverage.
    """
    server = PlannerServer(tmp_path)
    try:
        assert not server.call_tool(
            "harness_plan_start", {"plan_mode": "legacy", "idea": "Parser", "name": "Parser"}
        )["isError"]
        store = server._store()
        assert planning_turn(store.spine())["stage"] == "features"
        _, feature = node_example("feature")
        store.upsert_node(feature)
        assert planning_turn(store.spine())["stage"] == "requirements"
        prerequisites, test = node_example("test")
        requirement = next(node for node in prerequisites if node["kind"] == "requirement")
        store.upsert_node(requirement)
        assert planning_turn(store.spine())["stage"] == "tests"
        batch_nodes(server, [deepcopy(requirement), test])
        turn = planning_turn(store.spine())
        assert turn["stage"] == "bead"
        assert turn["schemaTarget"] == {
            "cli": "schema --kind bead",
            "mcp": "harness_schema kind=bead",
        }
        _, bead = node_example("bead")
        store.upsert_node(bead)
        # Tests can be attached through a bead, exactly as canonical coverage permits.
        test["parents"] = [bead["id"]]
        store.upsert_node(test)
        assert planning_turn(store.spine())["stage"] == "ready"
    finally:
        server.close()
