"""Track G and linkshelf planner regressions; providers are never launched."""

import json
from pathlib import Path

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.planning_support import source_only_b, source_only_vision_lock
from tests.python.core.test_plan_cli import cli
from tests.python.core.test_planner_replay_gaps import review

from codex_harness.core import plan_store
from codex_harness.core.acceptance import acceptance_review, derive_scenarios, run_acceptance
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_quality import quality_report, render_quality_card
from codex_harness.core.plan_store import PlanStore, PlanStoreError

project = test_planner_replay_gaps.project


def check(store, name):
    return next(item for item in quality_report(store)["checks"] if item["id"] == name)


def lock(store):
    source_only_vision_lock(store, statement="Approve")


def scenarios(store):
    lock(store)
    result = derive_scenarios(store.locked_acceptance_spine())
    for item in result:
        item.update(steps=['"{python}" -c "print(42)"'], expect={"stdout": "42"})
    store.save_acceptance_scenarios(result)


def test_quality_planned_then_current_execution_then_stale(project, tmp_path):
    scenarios(project)
    project.connection.execute("UPDATE meta SET phase='requirements'")
    planned = check(project, "first-moment-proof")
    assert planned["status"] == "pass"
    assert planned["evidenceStatus"] == "planned" and planned["claim"] == "planned"
    assert "evidence: planned" in render_quality_card(quality_report(project))
    repo = tmp_path / "product"
    repo.mkdir()
    assert run_acceptance(project, repo, subject="product")["passed"]
    proved = check(project, "first-moment-proof")
    assert proved["evidenceStatus"] == "executed" and proved["claim"] == "proved"
    (repo / "changed.py").write_text("# product changed\n")
    assert check(project, "first-moment-proof")["claim"] == "planned"


@pytest.mark.parametrize("machine", [False, True])
def test_quality_failure_exit(project, machine):
    project.connection.execute("UPDATE meta SET phase='roundtable'")
    result = cli(project.workspace, "quality", code=2, machine=machine)
    assert "Planner quality:" in (result["text"] if machine else result)


@pytest.mark.parametrize("machine", [False, True])
def test_audit_failure_exit(project, machine):
    (project.workspace / "oracle/answer.py").write_text("def answer(): return 0\n")
    result = cli(project.workspace, "audit", "--oracle", "oracle", code=2, machine=machine)
    assert "Test audit: FAIL" in (result["text"] if machine else result)


def test_publish_entry_uses_publication_gate(project):
    test_planner_replay_gaps.confirmed_vision(project)
    source_only_b(project, reviewers=test_planner_replay_gaps.REVIEWERS)
    project.connection.execute("UPDATE meta SET phase='roundtable'")
    result = cli(project.workspace, "advance", "--to", "publish", code=2, machine=True)
    assert "plan-publication-evidence-required" in result["text"]
    assert "audit --oracle" in result["next"]
    cli(project.workspace, "audit", "--oracle", "oracle")
    result = cli(project.workspace, "advance", "--to", "publish", code=2, machine=True)
    assert "roundtable" in result["next"]
    review(project)
    cli(project.workspace, "advance", "--to", "publish")
    cli(project.workspace, "publish")
    assert "unrecognized arguments: --force" in cli(
        project.workspace, "advance", "--to", "execute", "--force", code=2
    )


def test_scenarios_survive_metadata_relock_and_reference_edit(project):
    scenarios(project)
    before = project.acceptance_scenarios()
    vision = project.spine().node("V1")
    vision["fields"]["validation"] += ["-v"]
    project.upsert_node(vision)
    lock(project)
    assert {s["id"] for s in project.acceptance_scenarios()} == {s["id"] for s in before}
    assert all(s["status"] == "stale" for s in project.acceptance_scenarios())
    with PlanStore(project.workspace) as reopened:
        assert reopened.acceptance_scenarios() == project.acceptance_scenarios()
    scenarios(project)
    items = project.acceptance_scenarios()
    items[0]["nodeIds"] = ["R1"]
    project.save_acceptance_scenarios(items)
    requirement = project.spine().node("R1")
    requirement["text"] += " clarified interface"
    project.upsert_node(requirement)
    assert project.acceptance_scenarios()[0]["status"] == "stale"
    assert project.acceptance_scenarios()[1]["status"] == "planned"


def test_derived_prose_is_draft_and_not_planned(project):
    lock(project)
    test = project.spine().node("TE")
    test["fields"].update(firstMoment=True, cleanState=True)
    project.upsert_node(test)
    project.connection.execute("UPDATE meta SET phase='requirements'")
    result = cli(project.workspace, "accept-scenarios", "--derive", machine=True)
    assert all(s["status"] == "draft" and not s["executable"] for s in result["data"]["scenarios"])
    assert "supply steps" in result["text"].lower()
    assert check(project, "first-moment-proof")["status"] == "fail"


def test_scenario_refresh_uses_current_metadata_revision(project):
    scenarios(project)
    vision = project.spine().node("V1")
    vision["fields"]["validation"] += ["-v"]
    project.upsert_node(vision)
    assert not project.status()["vision_lock_stale"]
    project.save_acceptance_scenarios(project.acceptance_scenarios())
    assert all(item["status"] == "planned" for item in project.acceptance_scenarios())


def test_inline_step_expectations_are_executable(project, tmp_path):
    scenarios(project)
    items = project.acceptance_scenarios()
    for item in items:
        item["steps"] = [{"command": item["steps"][0], "expect": item["expect"]}]
        item["expect"] = {}
    project.save_acceptance_scenarios(items)
    assert all(item["executable"] for item in project.acceptance_scenarios())
    repo = tmp_path / "product"
    repo.mkdir()
    assert run_acceptance(project, repo, subject="product")["passed"]


def test_stale_scenarios_cannot_be_accepted(project, tmp_path):
    scenarios(project)
    vision = project.spine().node("V1")
    vision["text"] += " revised promise"
    project.upsert_node(vision)
    lock(project)
    repo = tmp_path / "product"
    repo.mkdir()
    assert run_acceptance(project, repo, subject="product")["passed"]
    acceptance_review(
        project,
        repo,
        reviewers=[{"name": "a", "provider": "codex"}, {"name": "b", "provider": "claude"}],
        runner=lambda *args: (0, "[]"),
    )
    project.connection.execute("UPDATE meta SET phase='accept'")
    with pytest.raises(PlanStoreError) as caught:
        project.accept(by="user", statement="Accept")
    assert caught.value.code == "acceptance-scenarios-stale"


def test_assumption_actor_is_required_and_validated(project):
    item = project.add_assumption("Use the stable interface")
    args = ("resolve-assumption", "--id", item, "--confirmed", "--rationale", "Reviewed")
    result = cli(project.workspace, *args, code=2, machine=True)
    assert "--by" in result["text"]
    cli(project.workspace, *args, "--by", "someone", code=2)
    cli(project.workspace, *args, "--by", "proxy:reviewer")
    row = project.connection.execute(
        "SELECT resolved_by FROM assumptions WHERE id=?", (item,)
    ).fetchone()
    assert row[0] == "proxy:reviewer"
    server = PlannerServer(project.workspace)
    try:
        result = server.call_tool(
            "harness_resolve_assumption", dict(id=item, confirmed=True, rationale="Reviewed")
        )
        assert result["isError"] and "by" in result["content"][0]["text"]
    finally:
        server.close()


def test_impact_before_publish_revision_file_default_and_mcp(project):
    result = cli(
        project.workspace, "compile", "--template", "template", "--out", "saved.json", machine=True
    )
    revision = result["data"]["planRevision"]
    requirement = project.spine().node("R1")
    requirement["text"] += " clarified interface"
    project.upsert_node(requirement)
    for flags in ((), ("--since", revision), ("--since", "saved.json")):
        result = cli(project.workspace, "impact", *flags, machine=True)
        assert result["data"]["affectedBeads"] == ["B1"]
        assert "R1" in json.dumps(result["data"])
    server = PlannerServer(project.workspace)
    try:
        result = server.call_tool("harness_impact", {"since": revision})
        assert not result["isError"], result
        assert result["structuredContent"]["affectedBeads"] == ["B1"]
    finally:
        server.close()


def test_compile_snapshot_matches_compiled_spine_during_edit(project, monkeypatch):
    original = plan_store.compile_manifest

    def compile_and_edit(*args, **kwargs):
        manifest = original(*args, **kwargs)
        with PlanStore(project.workspace) as writer:
            node = writer.spine().node("R1")
            node["text"] += " concurrent edit"
            writer.upsert_node(node)
        return manifest

    monkeypatch.setattr(plan_store, "compile_manifest", compile_and_edit)
    manifest = project.compile_template(project.workspace / "template")
    assert project.impact_since(manifest["planRevision"])["changed"] == ["R1"]


def test_impact_includes_changed_protected_proofs(project):
    manifest = project.compile_template(project.workspace / "template")
    path = project.workspace / "template/tests/test_answer.py"
    path.write_text(path.read_text() + "\n# revised proof\n")
    assert project.impact_since(manifest["planRevision"])["affectedBeads"] == ["B1"]


@pytest.mark.parametrize(
    "proof", ["missing", "import", "from", "explicit", "explicit-decision", "comment"]
)
def test_cross_bead_contract_checks_consumer_source(project, proof):
    producer = project.spine().node("B1")
    consumer = {
        **producer,
        "id": "B2",
        "fields": {
            **producer["fields"],
            "ownedFiles": ["consumer.py"],
            "dependsOn": ["B1"],
            "proofModule": "test_consumer.py",
        },
    }
    project.upsert_node(consumer)
    test = project.spine().node("T1")
    test.update(id="T2", fields={"module": "test_consumer.py", "level": "unit"})
    project.upsert_node(test)
    path = project.workspace / "template/tests/test_consumer.py"
    path.write_text(
        {
            "import": "import answer\n",
            "from": "from answer import answer\n",
            "comment": "# import answer\n",
        }.get(proof, "import unittest\n")
    )
    if proof.startswith("explicit"):
        req = project.spine().node("R1")
        req["fields"]["contractTest"] = "test_journey.py"
        project.upsert_node(req)
    if proof == "explicit-decision":
        project.upsert_node(
            dict(
                id="D1",
                kind="decision",
                title="Interface",
                text="Shared interface",
                parents=["R1"],
                fields={},
            )
        )
        for key in ("B1", "B2"):
            bead = project.spine().node(key)
            bead["parents"].append("D1")
            project.upsert_node(bead)
    project.connection.execute("UPDATE meta SET phase='plan'")
    result = check(project, "cross-bead-contract")
    assert result["status"] == ("warn" if proof in ("missing", "comment") else "pass")
    if result["status"] == "warn":
        assert "producer→consumer" in result["advice"]
        assert result["evidence"]["gaps"][0]["consumer"] == "B2"


def test_status_acceptance_once_and_brief(project):
    """Catch duplicate brief summaries overflowing seven lines after identity wrapping.

    Ordinary status tests miss the final compact CLI text and exact workspace.
    """
    lock(project)
    cli(project.workspace, "accept-scenarios", "--derive")
    text = cli(project.workspace, "status")
    assert text.count("0/3 scenarios proved") == 1
    assert '"lastResult":' not in text
    machine = cli(project.workspace, "status", machine=True)
    assert len(machine["data"]["acceptance"]["scenarios"]) == 3
    assert all(s["lastResult"] == "not-run" for s in machine["data"]["acceptance"]["scenarios"])
    brief = cli(project.workspace, "status", "--brief")
    for token in ("phase:", "pending_gate:", "Next:", "Planner quality:", "Open:"):
        assert token in brief
    assert '"lastResult":' not in brief
    assert brief.startswith(f"Seldon · project: {project.workspace.name} · phase ")
    assert brief.count("Seldon · project:") == brief.count("Next:") == 1
    assert f"workspace: {project.workspace}" in brief
    assert f"Revision {machine['status']['plan_revision'][:12]}" in brief
    assert f"phase: {machine['status']['phase']}" in brief
    assert (
        f"Open: {machine['status']['open_clarifications']} questions, "
        f"{machine['status']['open_assumptions']} assumptions"
    ) in brief
    assert len(brief.splitlines()) <= 7
    brief_json = cli(project.workspace, "status", "--brief", machine=True)
    assert brief_json["text"] == brief.rsplit("\nNext: ", 1)[0]
    assert brief_json["status"]["plan_revision"] == machine["status"]["plan_revision"]
    assert brief_json["data"]["acceptance"] == machine["data"]["acceptance"]


def test_guide_v9_contract(project):
    """Keep compact route discovery tied to actual named proof and refusal consumers.

    The historical version title is not a product promise; example replay alone
    misses named test inspection and helpful native-only approval refusals.
    """
    guide = (Path(__file__).resolve().parents[3] / "docs/operator/planner-guide.md").read_text(
        encoding="utf-8"
    )
    assert guide.startswith("# Planner guide\n") and len(guide.splitlines()) <= 140
    for token in (
        "show ID --body --test NAME",
        "planned",
        "proved",
        "impact --since",
        "--by",
        "cross-bead",
        "harness_record_confirmation gate=align",
        "harness_record_confirmation gate=vision-lock",
        "client `elicitation` capability",
        "conditions block credit",
        "plan-confirmation-origin-unavailable",
        "Plan-to-roundtable confirmation remains unchanged",
    ):
        assert token in guide
    before = list(project.connection.iterdump())
    inspected = cli(
        project.workspace, "show", "T1", "--body", "--test", "test_result", machine=True
    )
    assert any(
        "self.assertEqual(answer(), 42)" in file.get("body", "")
        for file in inspected["data"]["testFiles"]
    )
    missing_name = cli(project.workspace, "show", "T1", "--body", code=2, machine=True)
    assert "--body requires --test <name>" in missing_name["text"]
    refused = cli(
        project.workspace,
        "confirm",
        "--gate",
        "align",
        "--statement",
        "Quoted words",
        code=2,
        machine=True,
    )
    assert "plan-confirmation-origin-unavailable" in refused["text"]
    assert list(project.connection.iterdump()) == before
