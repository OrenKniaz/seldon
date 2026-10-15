"""Planner read-only inspection and visible finding dispositions, without providers."""

import json

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.test_plan_cli import cli
from tests.python.core.test_plan_quality import FINDING
from tests.python.core.test_planner_replay_gaps import review

from codex_harness.core import roundtable
from codex_harness.core.mcp_planner import PlannerServer

project = test_planner_replay_gaps.project


@pytest.mark.parametrize("start_option", ["-s", "--start-directory"])
def test_show_node_includes_derived_protected_test_dir(project, start_option):
    vision = project.spine().node("V1")
    vision["fields"]["requirementFiles"] = ["VISION.md"]
    vision["fields"]["validation"] = [
        start_option if argument == "-s" else argument
        for argument in vision["fields"]["validation"]
    ]
    project.upsert_node(vision)
    result = project.show_node("R1")
    assert {file["path"] for file in result["testFiles"]} == {
        "tests/test_answer.py",
        "tests/test_journey.py",
    }
    before = project.manifest_revision()
    path = project.workspace / "template/tests/test_answer.py"
    path.write_text(path.read_text() + "\n# changed protected test\n")
    assert project.manifest_revision() != before


def test_show_requirement_and_named_body_are_read_only(project):
    before = list(project.connection.iterdump())
    path = project.workspace / "template/tests/test_answer.py"
    source = path.read_text() + "\nraise RuntimeError('must never execute')\n"
    path.write_text(source)
    result = cli(project.workspace, "show", "R1", machine=True)
    assert result["data"]["kind"] == "requirement"
    assert result["data"]["parents"] == ["F1"]
    assert result["data"]["children"] == ["B1", "T1", "TE"]
    files = result["data"]["testFiles"]
    assert {file["path"] for file in files} == {"tests/test_answer.py", "tests/test_journey.py"}
    assert all(file["functions"] == ["Proof.test_result"] for file in files)
    result = cli(project.workspace, "show", "T1", "--body", "--test", "test_result", machine=True)
    assert "self.assertEqual(answer(), 42)" in result["text"]
    assert "must never execute" not in result["text"]
    assert not result["data"]["testFiles"][0]["truncated"]
    assert list(project.connection.iterdump()) == before
    assert path.read_text() == source


def test_show_body_budget_and_mcp_read_only(project):
    path = project.workspace / "template/tests/test_answer.py"
    path.write_text("def test_large():\n    assert '" + "é" * 9000 + "'\n", encoding="utf-8")
    before = list(project.connection.iterdump())
    server = PlannerServer(project.workspace)
    try:
        result = server.call_tool(
            "harness_show_node", {"id": "R1", "body": True, "test": "test_large"}
        )
        assert not result["isError"], result
        assert result["content"][0]["text"].endswith(
            "Next: Your call: review the understanding, then give your actual confirmation words "
            "for align."
        )
        files = result["structuredContent"]["testFiles"]
        assert sum(len(file.get("body", "").encode("utf-8")) for file in files) <= 8192
        assert files[0]["truncated"]
        assert "[truncated: 8 KB test body budget]" in result["content"][0]["text"]
        assert list(project.connection.iterdump()) == before
    finally:
        server.close()
    result = cli(project.workspace, "show", "T1", "--body", "--test", "test_large", machine=True)
    assert len(result["data"]["testFiles"][0]["body"].encode("utf-8")) <= 8192


@pytest.mark.parametrize("flags", [("--body",), ("--body", "--test", "missing")])
def test_show_invalid_body_request(project, flags):
    result = cli(project.workspace, "show", "T1", *flags, machine=True, code=2)
    assert "requires --test" in result["text"] or "Test not found" in result["text"]


def test_nodes_filter_and_mcp_parity(project):
    before = list(project.connection.iterdump())
    result = cli(project.workspace, "nodes", "--kind", "requirement", machine=True)
    assert "R1 · requirement · R1" in result["text"]
    assert [node["id"] for node in result["data"]["nodes"]] == ["R1"]
    assert len(cli(project.workspace, "nodes", machine=True)["data"]["nodes"]) == 6
    server = PlannerServer(project.workspace)
    try:
        reply = server.call_tool("harness_nodes", {"kind": "requirement"})
        assert reply["structuredContent"] == result["data"]
        assert reply["content"][0]["text"].endswith(
            "Next: Your call: review the understanding, then give your actual confirmation words "
            "for align."
        )
    finally:
        server.close()
    assert list(project.connection.iterdump()) == before


def test_resolved_flag_is_exclusive(project):
    result = cli(
        project.workspace,
        "resolve",
        "--id",
        "absent",
        "--resolution",
        "fixed",
        "--resolved",
        "--deferred",
        machine=True,
        code=2,
    )
    assert "not allowed with argument" in result["text"]


def test_redisposition_and_quality_full_ids(project):
    review(project, runner=lambda *args: (0, json.dumps([FINDING])))
    finding_ids = [item["id"] for item in roundtable.findings(project)]
    for finding_id in finding_ids:
        result = cli(
            project.workspace,
            "resolve",
            "--id",
            finding_id,
            "--resolution",
            "Evidence disproves the finding",
            "--rejected",
        )
        assert f"{finding_id}: open → rejected" in result
        result = cli(
            project.workspace,
            "resolve",
            "--id",
            finding_id,
            "--resolution",
            "Real gap retained for follow-up",
            "--deferred",
        )
        assert f"{finding_id}: rejected → deferred" in result
    result = cli(project.workspace, "quality")
    assert "Deferred findings:" in result
    assert all(finding_id in result for finding_id in finding_ids)
    result = cli(
        project.workspace,
        "resolve",
        "--id",
        finding_ids[0],
        "--resolution",
        "Now fixed and verified",
        "--resolved",
    )
    assert f"{finding_ids[0]}: deferred → resolved" in result
