"""Subprocess-only planning, including the guide's exact worked example."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.python.core.test_plan_store import draft_inputs, draft_intake, mock_catalog, mock_choice

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import open_plan_store

ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs/operator/planner-legacy-example.md"


def cli(workspace, *args, code=0, cwd=None, machine=False):
    command = [sys.executable, "-m", "codex_harness.core.plan_cli", "--workspace", str(workspace)]
    if machine:
        command.append("--json")
    result = subprocess.run(
        [*command, *args],
        cwd=cwd or workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == code, result.stdout + result.stderr
    if machine:
        output = json.loads(result.stdout)
        text = output["text"]
        assert output["isError"] == (code != 0)
        assert "\nNext: " not in text
        assert result.stdout.count('"next":') == 1
    else:
        output = result.stdout
        text = output
    assert text.startswith("Seldon · project: ")
    assert "\nPlan **" in text
    if not machine:
        assert text.rstrip().splitlines()[-1].startswith("Next: ")
        assert sum(line.startswith("Next:") for line in text.splitlines()) == 1
    return output


def node(id, kind="feature", parents=None, **fields):
    return dict(id=id, kind=kind, title=id, text=id, parents=parents or ["V1"], fields=fields)


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def started(tmp_path):
    cli(tmp_path, "start", "--plan-mode", "legacy", "--idea", "Tiny", "--name", "Tiny")
    return tmp_path


def test_cli_mcp_sqlite_reopen_and_chat_draft_roundtrip(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
    revision = inputs["expected_spine_revision"]

    def mutate(command, envelope):
        path = tmp_path / f"{command}.json"
        save(path, envelope)
        return cli(tmp_path, command, "--json", "@" + str(path), machine=True)["data"]

    intake = mutate("intake", dict(node=draft_intake(), expected_spine_revision=revision))
    assert intake["intake"]["recipe"] == "outcome-waves"
    # Actual subprocess CLI and protocol MCP alternate against the same canonical SQLite authority.
    server = PlannerServer(tmp_path)
    catalog = server.call_tool(
        "harness_reviewer_catalog",
        dict(catalog=mock_catalog(), expected_spine_revision=intake["spineRevision"]),
    )
    assert not catalog["isError"]
    server.close()
    choice = mutate(
        "reviewer-choice",
        dict(
            node=mock_choice(catalog["structuredContent"]["catalog"], "user"),
            expected_spine_revision=intake["spineRevision"],
        ),
    )
    inputs["expected_spine_revision"] = choice["spineRevision"]
    mapped = mutate("wave-map", inputs)
    output = cli(tmp_path, "waves", machine=True)
    assert output["data"]["accounting"]["fullVision"]["totalObligations"] == 5
    assert output["data"]["accounting"]["fullVision"]["completedRealObligations"] == 0
    assert (
        "Later meaningful refinement" in output["text"] and "Current exit facts" in output["text"]
    )
    assert "wave-map" not in output["next"] and "waves" in output["next"]
    # catalog CLI route uses exactly the same envelope as MCP, with no new observation authority.
    assert (
        mutate(
            "reviewer-catalog",
            dict(catalog=mock_catalog(), expected_spine_revision=mapped["spineRevision"]),
        )["catalog"]
        == catalog["structuredContent"]["catalog"]
    )
    server = PlannerServer(tmp_path)
    try:
        reopened = server.call_tool("harness_waves", {})
        assert reopened["structuredContent"] == output["data"]
        assert {
            key: reopened["structuredContent"]["nodeRevisions"][key]
            for key in mapped["nodeRevisions"]
        } == mapped["nodeRevisions"]
        assert "Later owner" in server.call_tool("harness_plan_status", {})["content"][0]["text"]
    finally:
        server.close()
    refused = cli(
        tmp_path, "wave-map", "--json", "@" + str(tmp_path / "wave-map.json"), machine=True, code=2
    )
    assert "plan-spine-stale" in refused["text"] and "status" in refused["next"]


def test_batch_is_atomic_and_order_independent(started):
    workspace = started
    batch = workspace / "nodes.jsonl"
    batch.write_text(
        "\n".join(json.dumps(item) for item in [node("R1", "requirement", ["F1"]), node("F1")])
    )
    cli(workspace, "node", "--json", "@" + str(batch), machine=True)
    before = cli(workspace, "status", machine=True)["status"]["plan_revision"]
    batch.write_text(
        "\n".join(
            json.dumps(item)
            for item in [node("F1", changed=True), node("BAD", parents=["missing"])]
        )
    )
    assert "spine-unknown-parent" in cli(workspace, "node", "--json", "@" + str(batch), code=2)
    assert cli(workspace, "status", machine=True)["status"]["plan_revision"] == before
    batch.write_text(json.dumps(node("F2")) + "\n{broken")
    cli(workspace, "node", "--json", "@" + str(batch), code=2)
    assert cli(workspace, "status", machine=True)["status"]["plan_revision"] == before
    duplicate = [node("F2"), node("F2")]
    cli(workspace, "node", "--json", json.dumps(duplicate), code=2)
    cli(workspace, "node", "--json", "[]", code=2)
    cli(workspace, "remove-node", "--id", "F1", code=2)
    cli(workspace, "remove-node", "--id", "R1")
    cli(workspace, "remove-node", "--id", "F1")


def test_cli_setup_survives_restart(tmp_path):
    """Protect argparse-to-store-to-card flow; MCP tests cannot detect dropped CLI setup."""
    setup = dict(githubLogin="cli-user", gitName="CLI User")
    cli(tmp_path, "start", "--idea", "Tiny", "--name", "Tiny", "--setup", json.dumps(setup))
    with open_plan_store(tmp_path) as store:
        assert store.spine().node("V1")["fields"]["setup"] == setup
    assert "GitHub login: cli-user" in cli(tmp_path, "start", "--idea", "Tiny", "--name", "Tiny")
