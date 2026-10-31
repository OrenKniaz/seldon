"""Public executable admission; native responses are synthetic, never human proof."""

from tests.python.core.planning_support import (
    source_only_b,
    source_only_expansion,
    source_only_publication,
)
from tests.python.core.test_mcp_audit import invoke
from tests.python.core.test_mcp_planner import native_confirmation_result
from tests.python.core.test_planner_refusals import canonical_rows

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PHASES, open_plan_store_readonly
from codex_harness.core.roundtable import run_roundtable


def node(identity, kind, parents, fields=None):
    return dict(
        id=identity, kind=kind, title=identity, text=identity, parents=parents, fields=fields or {}
    )


def planned(workspace, *, disconnected):
    """Real public authoring/compile, synthetic native responses, no provider execution."""
    tmp_path = workspace
    template = tmp_path / "template"
    (template / "tests").mkdir(parents=True)
    (template / "app.py").write_text("def value(): raise NotImplementedError\n")
    (template / "tests/test_app.py").write_text(
        "import unittest\nfrom app import value\nclass Proof(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(value(), 42)\n"
    )
    server = PlannerServer(tmp_path)
    try:
        assert not invoke(
            server, "harness_plan_start", plan_mode="legacy", idea="Linked work", name="Lineage"
        )["isError"]
        vision = invoke(server, "harness_show_node", id="V1")["structuredContent"]
        vision = {k: vision[k] for k in ("id", "kind", "title", "text", "parents", "fields")}
        vision["fields"].update(
            validation=["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
            requirementFiles=[],
        )
        nodes = [
            vision,
            node("F", "feature", [] if disconnected else ["V1"]),
            node("DRAFT", "feature", []),  # Unrelated orphan drafts stay editable/admissible.
            node("R", "requirement", ["F"]),
            node("T", "test", ["R"], dict(module="test_app.py", level="unit")),
            node(
                "B",
                "bead",
                ["R"],
                dict(ownedFiles=["app.py"], dependsOn=[], proofModule="test_app.py", lessons=[]),
            ),
        ]
        assert not invoke(server, "harness_upsert_nodes", nodes=nodes)["isError"]
        source_only_expansion(server.store)
        while server.store.phase() != "plan":
            phase = server.store.phase()
            if phase in ("align", "vision-lock"):
                response = native_confirmation_result(server, phase)
                assert not response["isError"]
                assert response["structuredContent"]["human_identity_verified"] is False
            assert not invoke(server, "harness_advance", to_phase=PHASES[PHASES.index(phase) + 1])[
                "isError"
            ]
        assert not invoke(server, "harness_compile")["isError"]
    except BaseException:
        server.close()
        raise
    if not disconnected:
        source_only_b(server.store)
    return server


def test_disconnected_executable_bead_refuses_plan_admission_after_reopen(tmp_path):
    """Catch visionless execution; existing coverage tests only remove tests or beads."""
    planned(tmp_path, disconnected=True).close()
    server = PlannerServer(tmp_path)
    try:
        with open_plan_store_readonly(tmp_path) as saved:
            assert saved.spine().coverage_gaps() == [{"id": "B", "gap": "bead-without-vision"}]
            before = canonical_rows(saved)
        refused = invoke(server, "harness_advance", to_phase="roundtable")
        assert refused["isError"], refused
        assert "plan-bead-without-vision" in refused["content"][0]["text"]
        assert "B" in refused["content"][0]["text"]
        assert canonical_rows(server.store) == before
        assert server.store.phase() == "plan"
        assert server.store.status()["published_revision"] is None
        assert not invoke(server, "harness_upsert_node", node=node("F", "feature", ["V1"]))[
            "isError"
        ]
        assert not invoke(server, "harness_compile")["isError"]
        source_only_b(server.store)
        assert not invoke(server, "harness_advance", to_phase="roundtable")["isError"]
        held = invoke(server, "harness_advance", to_phase="publish")
        assert held["isError"]
        assert server.store.status()["published_revision"] is None
        assert server.store.phase() == "roundtable"
    finally:
        server.close()


def source_evidence(server):
    """Actual public prospective audit plus explicitly simulated review, never live credit."""
    source_only_b(
        server.store,
        reviewers=[
            dict(name="fixture-codex", provider="codex"),
            dict(name="fixture-claude", provider="claude"),
        ],
    )
    result = invoke(server, "harness_audit", mode="prospective")
    assert not result["isError"], result["content"][0]["text"]
    admission = result["structuredContent"]
    if admission["state"] in {"queued", "running"}:
        assert not admission["proofCreditGranted"]
        assert server.audits.prepared is not None  # Only our fresh admission may dispatch.
        server.audits.dispatch_after_flush(server.store)
        server.audits.thread.join(timeout=30)
        assert not server.audits.thread.is_alive()
        server.audits.drain(server.store)
    else:
        assert admission["state"] == "completed", admission
    result = invoke(server, "harness_audit_status", id=admission["attemptId"])
    assert not result["isError"], result
    receipt = result["structuredContent"]
    assert receipt["state"] == "completed" and receipt["result"]["ok"]
    assert receipt["proofCreditGranted"] and receipt["completedAllRequiredSteps"]
    assert receipt["auditId"] == receipt["result"]["id"]
    run_roundtable(
        server.store,
        reviewers=[
            dict(name="fixture-codex", provider="codex"),
            dict(name="fixture-claude", provider="claude"),
        ],
        runner=lambda *args: (0, "[]"),
        executable_for=lambda provider: provider,
    )
    assert not server.store.successful_reviews(server.store.manifest_revision())
    return receipt


def test_publication_rechecks_lineage_after_post_admission_edit(tmp_path, monkeypatch):
    """Catch a post-admission disconnect; Plan-only guards miss later canonical edits."""
    from codex_harness.core import test_audit

    engine, executions = test_audit.audit_prospective_tests, []

    def execute(**arguments):
        executions.append(1)
        return engine(**arguments)

    monkeypatch.setattr(test_audit, "audit_prospective_tests", execute)
    server = planned(tmp_path, disconnected=False)
    try:
        source_only_b(server.store)
        assert not invoke(server, "harness_advance", to_phase="roundtable")["isError"]
        original = source_evidence(server)
        with source_only_publication():
            assert not invoke(server, "harness_advance", to_phase="publish")["isError"]
        assert not invoke(server, "harness_upsert_node", node=node("F", "feature", []))["isError"]
        assert not invoke(server, "harness_compile")["isError"]
        source_evidence(server)
    finally:
        server.close()
    server = PlannerServer(tmp_path)
    try:
        with open_plan_store_readonly(tmp_path) as saved:
            before = canonical_rows(saved)
        with source_only_publication():
            refused = invoke(
                server,
                "harness_publish",
                proof_level="prospective",
                reason="SOURCE-ONLY mechanics; no live reviewer/human acceptance",
            )
        assert refused["isError"], refused
        assert "plan-bead-without-vision" in refused["content"][0]["text"]
        assert canonical_rows(server.store) == before
        assert server.store.phase() == "publish"
        assert server.store.status()["published_revision"] is None
        assert not invoke(server, "harness_upsert_node", node=node("F", "feature", ["V1"]))[
            "isError"
        ]
        assert not invoke(server, "harness_compile")["isError"]
        held = invoke(server, "harness_publish", proof_level="prospective", reason="No oracle")
        assert held["isError"]
        assert server.store.status()["published_revision"] is None
        before_audits = server.store.connection.execute(
            "SELECT count(*) FROM audit_runs"
        ).fetchone()[0]
        before_executions = list(executions)
        restored = source_evidence(server)
        assert restored["attemptId"] == original["attemptId"]
        assert restored["auditId"] == original["auditId"]
        assert executions == before_executions  # Revisited terminal candidate never replays engine.
        assert server.store.connection.execute("SELECT count(*) FROM audit_runs").fetchone()[0] == (
            before_audits
        )
        with source_only_publication():
            published = invoke(
                server,
                "harness_publish",
                proof_level="prospective",
                reason="SOURCE-ONLY mechanics; no live reviewer/human acceptance",
            )
        assert not published["isError"], published
    finally:
        server.close()
    with open_plan_store_readonly(tmp_path) as saved:
        assert saved.publication_current(saved.manifest_revision())
