"""Replay-3 CLI regressions, using real stores, unittest proofs and subprocesses."""

import json
from pathlib import Path

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.test_plan_cli import cli

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_cli import parser
from codex_harness.core.plan_store import PlanStore, PlanStoreError

project = test_planner_replay_gaps.project


def fail_oracle(project):
    (project.workspace / "oracle/answer.py").write_text("def answer(): return 99\n")


@pytest.mark.parametrize("machine", [False, True])
def test_item1_audit_fail_exits_two(project, machine):
    fail_oracle(project)
    result = cli(
        project.workspace,
        "audit",
        "--baseline",
        "template",
        "--oracle",
        "oracle",
        code=2,
        machine=machine,
    )
    assert "Test audit: FAIL" in (result["text"] if machine else result)


def test_item2_baseline_options_are_explicit(project):
    error = cli(
        project.workspace,
        "audit",
        "--baseline",
        str(project.workspace / "template"),
        "--oracle",
        "oracle",
        code=2,
        machine=True,
    )
    assert "template" in error["text"] and "invalid choice" in error["text"]
    assert "--baseline template" in error["next"]
    args = parser().parse_args(
        [
            "--workspace",
            str(project.workspace),
            "audit",
            "--oracle",
            "oracle",
            "--baseline-ref",
            "refs/heads/main",
        ]
    )
    assert args.baseline_ref == "refs/heads/main" and args.baseline is None
    with pytest.raises(ValueError):
        parser().parse_args(
            [
                "--workspace",
                str(project.workspace),
                "audit",
                "--oracle",
                "oracle",
                "--baseline",
                "template",
                "--baseline-ref",
                "HEAD",
            ]
        )


def test_item3_audit_has_untruncated_failure_artifacts(project):
    # Emit enough output to detect the old runner's tail-only truncation.
    proof = project.workspace / "template/tests/test_answer.py"
    proof.write_text(
        proof.read_text()
        + "\nprint('BEGIN PROOF')\n"
        + "print('long diagnostic line ' * 100)\n"
        + "print('END PROOF')\n"
    )
    fail_oracle(project)
    result = cli(project.workspace, "audit", "--oracle", "oracle", code=2, machine=True)
    modules = set()
    for failure in result["data"]["findings"]:
        modules.add(failure["module"])
        full = Path(failure["artifactPath"]).read_text(encoding="utf-8")
        assert full == failure["output"]
        assert failure["outputPreview"] == "\n".join(full.splitlines()[:40])
        assert failure["outputPreview"] in result["text"]
        assert failure["artifactPath"] in result["text"]
        assert "AssertionError" in full
        if failure["module"] == "test_answer.py":
            assert len(full) > 2000 and "BEGIN PROOF" in full and "END PROOF" in full
    assert modules == {"test_answer.py", "test_journey.py"}


def test_item4_created_entities_return_ids(project):
    w = project.workspace
    assert (
        cli(w, "start", "--idea", "Answer", "--name", "Answer", machine=True)["data"]["id"] == "V1"
    )
    question = cli(w, "ask", "--question", "Who?", machine=True)["data"]["id"]
    cli(w, "answer", "--id", question, "--text", "Operator")
    assumption = cli(w, "assume", "--text", "Offline", machine=True)["data"]["id"]
    assert project.connection.execute(
        "SELECT id FROM assumptions WHERE id=?", (assumption,)
    ).fetchone()
    confirmation = cli(w, "confirm", "--gate", "align", "--statement", "Accepted", machine=True)[
        "data"
    ]["id"]
    assert project.connection.execute(
        "SELECT id FROM approvals WHERE id=?", (confirmation,)
    ).fetchone()
    node = project.spine().node("F1")
    assert cli(w, "node", "--json", json.dumps(node), machine=True)["data"]["id"] == "F1"
    assert cli(w, "node", "--json", json.dumps([node]), machine=True)["data"]["id"] == ["F1"]
    audit = cli(w, "audit", "--oracle", "oracle", machine=True)["data"]
    assert project.connection.execute(
        "SELECT id FROM audit_runs WHERE id=?", (audit["id"],)
    ).fetchone()


def test_item5_resolution_requires_rationale_and_persists_attribution(project):
    w = project.workspace
    item_id = cli(w, "assume", "--text", "Offline", machine=True)["data"]["id"]
    error = cli(w, "resolve-assumption", "--id", item_id, "--confirmed", code=2, machine=True)
    assert "--rationale" in error["text"]
    result = cli(
        w,
        "resolve-assumption",
        "--id",
        item_id,
        "--confirmed",
        "--rationale",
        "User chose offline operation",
        "--by",
        "user",
        machine=True,
    )
    assert "user" in result["text"] and "User chose offline operation" in result["text"]
    with PlanStore(w) as reopened:
        row = reopened.connection.execute(
            "SELECT * FROM assumptions WHERE id=?", (item_id,)
        ).fetchone()
        assert row["status"] == "confirmed"
        assert row["rationale"] == "User chose offline operation" and row["resolved_by"] == "user"


@pytest.mark.parametrize("batch", [False, True])
def test_item6_metadata_keeps_lock_vision_edit_requires_reapproval(project, batch):
    w = project.workspace
    for gate, phase in (("align", "expand"), ("vision-lock", "features")):
        if gate == "vision-lock":
            cli(w, "advance", "--to", "vision-lock")
        cli(w, "confirm", "--gate", gate, "--statement", "User approved")
        cli(w, "advance", "--to", phase)
    locked = project.status()["vision_locked_revision"]
    vision = project.spine().node("V1")
    vision["fields"]["validation"] += ["-v"]
    vision["fields"]["requirementFiles"] = ["tests/test_answer.py", "tests/test_journey.py"]

    def edit():
        return cli(w, "node", "--json", json.dumps([vision] if batch else vision), machine=True)

    assert not edit()["status"]["vision_lock_stale"]
    assert project.status()["vision_locked_revision"] == locked
    # Returning to the lock checkpoint must retain metadata-only approval too.
    resumed = cli(w, "advance", "--to", "vision-lock", machine=True)
    assert "advance --to features" in resumed["next"]
    cli(w, "advance", "--to", "features")
    cli(w, "advance", "--to", "requirements")
    vision["text"] += " Changed vision."
    changed = edit()
    assert changed["status"]["vision_lock_stale"]
    assert "confirm --gate vision-lock" in changed["next"] and "renewed approval" in changed["next"]
    refused = cli(w, "advance", "--to", "tech-stack", code=2, machine=True)
    assert "plan-approval-stale" in refused["text"]
    cli(w, "confirm", "--gate", "vision-lock", "--statement", "User accepts revised vision")
    cli(w, "advance", "--to", "tech-stack")
    vision["fields"]["scope_in"] = "More scope"
    edit()
    with pytest.raises(PlanStoreError, match="renewed approval"):
        project.advance("plan", by="planner")


def test_item7_cli_wording_uses_commands_mcp_keeps_tools(tmp_path):
    output = cli(tmp_path, "start", "--idea", "Tiny", "--name", "Tiny", machine=True)
    assert "card --vision" in output["next"] and "harness_" not in json.dumps(output)
    with_server = PlannerServer(tmp_path)
    try:
        reply = with_server.call_tool("harness_plan_start", {"idea": "Tiny", "name": "Tiny"})
        assert "harness_understanding_card" in reply["content"][0]["text"]
    finally:
        with_server.close()
    error = cli(tmp_path, "impact", code=2, machine=True)
    assert "compile" in error["next"] and "audit" in error["next"]
    assert "harness_" not in json.dumps(error)
