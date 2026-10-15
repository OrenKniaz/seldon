"""Replay3 repairs through canonical planning producers and CLI consumers."""

import json

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.test_plan_cli import cli
from tests.python.core.test_planner_replay_gaps import REVIEWERS, review

from codex_harness.core import roundtable
from codex_harness.core.plan_store import PlanStoreError
from codex_harness.core.spine_render import render_markdown

project = test_planner_replay_gaps.project


def test_compile_materializes_and_binds_docs(project):
    result = cli(
        project.workspace,
        "compile",
        "--template",
        "template",
        "--out",
        "manifest.json",
        machine=True,
    )
    manifest = json.loads((project.workspace / "manifest.json").read_text())
    for name, text in render_markdown(project.spine()).items():
        assert (project.workspace / "template" / name).read_text(encoding="utf-8") == text
    assert manifest["requirementFilesRevision"]
    assert result["data"]["planRevision"] == project.manifest_revision()
    before = project.manifest_revision()
    (project.workspace / "template/PLAN.md").write_text("changed")
    assert project.manifest_revision() != before


def test_missing_requirement_compile_audit_and_publish_refuse(project):
    vision = project.spine().node("V1")
    vision["fields"]["requirementFiles"].append("absent.py")
    project.upsert_node(vision)
    result = cli(
        project.workspace,
        "compile",
        "--template",
        "template",
        "--out",
        "manifest.json",
        machine=True,
        code=2,
    )
    assert "plan-requirement-file-missing" in result["text"]
    assert "compile" in result["next"] and "audit" in result["next"]
    result = cli(project.workspace, "audit", "--oracle", "oracle", machine=True, code=2)
    assert "plan-requirement-file-missing" in result["text"]
    project.connection.execute("UPDATE meta SET phase='publish'")
    with pytest.raises(PlanStoreError) as error:
        project.publish(by="planner", export_dir=project.workspace / "plan")
    assert error.value.code == "plan-requirement-file-missing"


def test_publish_rechecks_deleted_doc(project):
    project.compile_template(project.workspace / "template")
    (project.workspace / "template/PLAN.md").unlink()
    project.connection.execute("UPDATE meta SET phase='publish'")
    with pytest.raises(PlanStoreError, match="PLAN.md"):
        project.publish(by="planner", export_dir=project.workspace / "plan")


def test_prepublication_impact_next(project):
    result = cli(project.workspace, "impact", machine=True)
    assert result["status"]["published_revision"] is None
    assert result["data"]["changed"] == result["data"]["affectedBeads"] == []


def test_totals_survive_revision_and_cli_cap(project):
    first = review(project, REVIEWERS[:1], max_total=2)
    node = project.spine().node("R1")
    node["text"] += " revised"
    project.upsert_node(node)
    second = review(project, REVIEWERS[:1], max_total=2)
    assert first["reviewers"][0]["total"] == 1
    assert second["reviewers"][0]["round"] == 1
    assert second["reviewers"][0]["total"] == 2
    assert "round 1/2 for this revision" in roundtable.render_roundtable_card(second, [])
    status = cli(project.workspace, "status", machine=True)
    assert status["status"]["roundtable"][0]["total"] == 2
    assert "2 total" in status["text"]
    result = cli(
        project.workspace,
        "roundtable",
        "--reviewer",
        json.dumps(REVIEWERS[0]),
        "--max-total",
        "2",
        machine=True,
        code=2,
    )
    assert "Waive" in result["next"] and "remaining findings" in result["next"]
    with pytest.raises(PlanStoreError) as error:
        review(project, REVIEWERS[:1], max_total=2)
    assert error.value.code == "roundtable-total-budget-exhausted"
    assert review(project, REVIEWERS[:1], max_rounds=3)["reviewers"][0]["total"] == 3


def test_dossier_complete_test_bodies_and_deterministic_shard_budget(project):
    packet = roundtable.review_packet(project)
    assert "self.assertEqual(answer(), 42)" in packet
    assert "Planned proof (oracle-verified), not a worker build" in packet
    path = project.workspace / "template/tests/test_answer.py"
    path.write_text("# " + "x" * 60_000)
    bodies = roundtable._test_bodies(project)
    assert path.read_text() in bodies
    assert "[truncated: protected test body budget]" not in bodies
    assert bodies == roundtable._test_bodies(project)
    shards = roundtable.packet_shards(project, "claude", token_budget=5000)
    assert all(shard["estimatedTokens"] <= 5000 for shard in shards)
    assert any("R1" in shard["truncatedNodes"] for shard in shards)
    assert shards == roundtable.packet_shards(project, "claude", token_budget=5000)


def test_rendered_docs_compile_audit_review_publish_path(project):
    for name in render_markdown(project.spine()):
        (project.workspace / "template" / name).unlink()
    cli(project.workspace, "compile", "--template", "template", "--out", "manifest.json")
    cli(project.workspace, "audit", "--oracle", "oracle")
    review(project)
    project.connection.execute("UPDATE meta SET phase='publish'")
    revision = project.publish(by="planner", export_dir=project.workspace / "plan")
    manifest = project.published_manifest(revision)
    assert revision == project.manifest_revision()
    for name in manifest["requirementFiles"]:
        assert (project.workspace / "template" / name).exists()
    for name, text in render_markdown(project.spine()).items():
        assert (project.workspace / "template" / name).read_text(encoding="utf-8") == text
        assert (project.workspace / "plan" / revision / name).read_text(encoding="utf-8") == text


def test_total_cap_reservation_prevents_concurrent_invocation(project):
    from codex_harness.core.plan_store import PlanStore

    def concurrent(*args):
        with PlanStore(project.workspace) as other:
            with pytest.raises(PlanStoreError) as error:
                review(other, REVIEWERS[:1], max_total=1)
            assert error.value.code == "roundtable-total-budget-exhausted"
        return 0, "[]"

    result = review(project, REVIEWERS[:1], runner=concurrent, max_total=1)
    assert result["reviewers"][0]["total"] == 1
