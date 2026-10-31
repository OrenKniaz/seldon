"""Production admission regressions; controller subprocesses are source-test stubs."""

import json

import pytest
from tests.python.core.planning_support import (
    source_controller_roundtable,
    source_only_b,
    source_only_confirmation,
)
from tests.python.core.test_planner_replay_gaps import REVIEWERS, audit, confirmed_vision
from tests.python.core.test_planner_replay_gaps import project as project_fixture

from codex_harness.core import roundtable
from codex_harness.core.plan_store import PHASES, PlanStore, PlanStoreError


@pytest.fixture(name="project")
def admission_project(tmp_path, monkeypatch):
    for store in project_fixture.__wrapped__(tmp_path, monkeypatch):
        confirmed_vision(store)
        source_only_b(store, reviewers=REVIEWERS)
        yield store


def controller_review(store, monkeypatch, output="[]"):
    # Exercise the controller-owned launch path; this is not actual-provider proof.
    return source_controller_roundtable(
        store,
        reviewers=REVIEWERS,
        process_stub=lambda *args: (0, output, ""),
        executable_for=lambda provider: provider,
    )


def enter_publish(store):
    while store.phase() != "publish":
        current = store.phase()
        if current in ("align", "vision-lock"):
            source_only_confirmation(store, current, statement="Approve")
        store.advance(PHASES[PHASES.index(current) + 1], by="source-test-planner")


@pytest.mark.parametrize("falsy", [False, True])
def test_injected_pair_never_earns_real_review_or_publication_credit(project, falsy):
    class FalsyReview:
        def __bool__(self):
            return False

        def __call__(self, *args):
            return 0, "[]"

    injected = FalsyReview() if falsy else lambda *args: (0, "[]")
    audit(project)
    result = roundtable.run_roundtable(
        project,
        reviewers=REVIEWERS,
        runner=injected,
        executable_for=lambda provider: provider,
    )
    assert not project.successful_reviews(project.manifest_revision())
    assert result["crossSubscription"] is False
    assert result["isMocked"] is True and result["didRunProvider"] is False
    assert all(r["isMocked"] and not r["didRunProvider"] for r in result["reviewers"])
    with PlanStore(project.workspace) as reopened:
        rows = reopened.review(result["roundId"])["reviewers"]
        assert all(r["isMocked"] and not r["didRunProvider"] for r in rows)
        with pytest.raises(PlanStoreError, match="successful review"):
            enter_publish(reopened)


def test_controller_review_positive_and_unknown_history_excluded(project, monkeypatch):
    audit(project)
    result = controller_review(project, monkeypatch)
    assert result["crossSubscription"] is True
    assert result["isMocked"] is False and result["didRunProvider"] is True
    assert len(project.successful_reviews(project.manifest_revision())) == 2
    enter_publish(project)
    project.publish(by="source-test", export_dir=project.workspace / "plan")
    # Migration must never infer execution from an exit-zero historical row.
    project.connection.execute("UPDATE review_runs SET isMocked=NULL, didRunProvider=NULL")
    assert not project.successful_reviews(project.manifest_revision())


@pytest.mark.parametrize("surface", ["store", "cli", "mcp"])
def test_already_publish_stale_vision_refused_then_renewed(project, monkeypatch, surface):
    audit(project)
    controller_review(project, monkeypatch)
    enter_publish(project)
    vision = project.spine().node("V1")
    vision["text"] += " clarified scope"
    project.upsert_node(vision)
    audit(project)
    if surface == "store":
        with pytest.raises(PlanStoreError, match="plan-a-consent-required"):
            project.publish(by="source-test", export_dir=project.workspace / "plan")
    elif surface == "cli":
        from tests.python.core.test_plan_cli import cli

        assert "plan-a-consent-required" in cli(project.workspace, "publish", code=2)
    else:
        from codex_harness.core.mcp_planner import PlannerServer

        server = PlannerServer(project.workspace)
        try:
            reply = server.call_tool("harness_publish", {})
            assert reply["isError"] and "plan-a-consent-required" in str(reply)
        finally:
            server.close()
    source_only_confirmation(project, "vision-lock", statement="Approve")
    source_only_b(project, reviewers=REVIEWERS)
    audit(project)
    controller_review(project, monkeypatch)
    project.publish(by="source-test", export_dir=project.workspace / "plan")


@pytest.mark.parametrize("status", ["resolved", "rejected", "deferred"])
@pytest.mark.parametrize("boundary", ["enter", "final"])
def test_open_blocker_refused_at_enter_and_final_publish_then_disposition(
    project, monkeypatch, status, boundary
):
    audit(project)
    output = json.dumps(
        [
            dict(
                node_id="R1",
                severity="blocker",
                finding="Unproven behavior",
                suggestion="Prove or explicitly disposition",
            )
        ]
    )
    controller_review(project, monkeypatch)
    if boundary == "final":
        enter_publish(project)
    controller_review(project, monkeypatch, output)
    if boundary == "enter":
        while project.phase() != "roundtable":
            current = project.phase()
            if current in ("align", "vision-lock"):
                source_only_confirmation(project, current, statement="Approve")
            project.advance(PHASES[PHASES.index(current) + 1], by="source-test")
    with pytest.raises(PlanStoreError, match="open.*finding|finding.*open"):
        if boundary == "enter":
            project.advance("publish", by="source-test")
        else:
            project.publish(by="source-test", export_dir=project.workspace / "plan")
    for finding in roundtable.findings(project, "open"):
        roundtable.resolve_finding(
            project, finding["id"], status=status, resolution="Explicit source-test disposition"
        )
    if boundary == "enter":
        project.advance("publish", by="source-test")
    project.publish(by="source-test", export_dir=project.workspace / "plan")


def test_controller_launch_publication_positive(project, monkeypatch):
    audit(project)
    controller_review(project, monkeypatch)
    enter_publish(project)
    revision = project.publish(by="source-test", export_dir=project.workspace / "plan")
    assert project.publication_current(revision)


@pytest.mark.parametrize("flags", [(None, None), (0, 0), (1, 1)])
def test_only_explicit_real_execution_flags_earn_credit(project, monkeypatch, flags):
    controller_review(project, monkeypatch)
    project.connection.execute("UPDATE review_runs SET isMocked=?, didRunProvider=?", flags)
    assert not project.successful_reviews(project.manifest_revision())


def test_legacy_schema_unknown_does_not_earn_credit_after_migration(project):
    project.connection.execute(
        "CREATE TABLE review_runs (id TEXT PRIMARY KEY, round_id TEXT NOT NULL, "
        "plan_revision TEXT NOT NULL, reviewer TEXT NOT NULL, provider TEXT NOT NULL, "
        "model TEXT, exit_code INTEGER, findings INTEGER NOT NULL, invalid INTEGER NOT NULL, "
        "started_at TEXT NOT NULL, finished_at TEXT NOT NULL)"
    )
    project.connection.execute(
        "INSERT INTO review_runs VALUES ('old', 'old-round', ?, 'old-reviewer', 'codex', "
        "NULL, 0, 0, 0, 'old', 'old')",
        (project.manifest_revision(),),
    )
    assert not project.successful_reviews(project.manifest_revision())
    roundtable.run_roundtable(
        project, reviewers=[], runner=lambda *args: pytest.fail("No reviewer selected")
    )
    old = project.review("old")["reviewers"][0]
    assert old["isMocked"] is None and old["didRunProvider"] is None
    assert not project.successful_reviews(project.manifest_revision())


def test_new_open_finding_prevents_advance_from_published_revision(project, monkeypatch):
    audit(project)
    controller_review(project, monkeypatch)
    enter_publish(project)
    project.publish(by="source-test", export_dir=project.workspace / "plan")
    output = json.dumps(
        [
            dict(
                node_id="R1",
                severity="blocker",
                finding="New review blocker",
                suggestion="Resolve before execution",
            )
        ]
    )
    controller_review(project, monkeypatch, output)
    with pytest.raises(PlanStoreError, match="open.*finding|finding.*open"):
        project.advance("execute", by="source-test")
    for item in roundtable.findings(project, "open"):
        roundtable.resolve_finding(project, item["id"], resolution="Verified fix")
    project.advance("execute", by="source-test")
