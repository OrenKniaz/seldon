"""Actual planner consumers over durable source-only review/evidence fixtures.

Protected behavior: host repair guidance names all publication-blocking clusters.
Credible regression: generic phase prose hides stale/minor open work or sends a
prospective plan to an invented oracle directory. Existing review ingestion and
gate tests do not prove the MCP/CLI next-action contract. The primary boundary
is real store -> MCP wire serialization and CLI JSON, not a copied helper answer.
No model/provider, audit subprocess or external controller loop is exercised.
"""

import json
import subprocess

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.planning_support import source_controller_roundtable
from tests.python.core.test_mcp_planner import rpc

from codex_harness.core import plan_cli
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PlanStoreError
from codex_harness.core.roundtable import findings, run_roundtable

project = test_planner_replay_gaps.project


def _at_roundtable(store):
    test_planner_replay_gaps.confirmed_vision(store)
    for phase in ("requirements", "tech-stack", "plan", "roundtable"):
        store.advance(phase, by="source-test")
    store.compile_template(store.workspace / "template")


def _status_pair(store, capsys):
    server = PlannerServer(store.workspace)
    try:
        wire = rpc(server, "tools/call", {"name": "harness_plan_status", "arguments": {}})
        assert not wire["result"]["isError"]
        mcp = wire["result"]
    finally:
        server.close()
    assert plan_cli.main(["--workspace", str(store.workspace), "--json", "status"]) == 0
    cli = json.loads(capsys.readouterr().out)
    assert not cli["isError"]
    return mcp, cli


def test_actual_mcp_and_cli_name_all_open_publication_findings(project, monkeypatch, capsys):
    _at_roundtable(project)
    reviewers = [dict(name="Alpha", provider="codex"), dict(name="Beta", provider="claude")]
    old = run_roundtable(
        project,
        reviewers=reviewers,
        executable_for=lambda provider: provider,
        runner=lambda *_: (
            0,
            json.dumps(
                [
                    dict(
                        node_id="R1",
                        severity="minor",
                        finding="Stale minor specification gap",
                        suggestion="Record the exact boundary",
                    ),
                    dict(
                        node_id="B1",
                        severity="note",
                        finding="Packet readiness observation",
                        suggestion="Read the packet",
                    ),
                ]
            ),
        ),
    )
    # Explicit stage-provenance fixture: readiness is advisory, unlike the
    # canonical roundtable clusters created through the real ingestion above.
    project.connection.execute("UPDATE review_findings SET stage='readiness' WHERE node_id='B1'")
    requirement = project.spine().node("R1")
    requirement["text"] += " exact current requirement"
    project.upsert_node(requirement)
    project.compile_template(project.workspace / "template")
    new = run_roundtable(
        project,
        reviewers=reviewers[:1],
        executable_for=lambda provider: provider,
        runner=lambda *_: (
            0,
            json.dumps(
                [
                    dict(
                        node_id="R1",
                        severity="major",
                        finding="Current acceptance edge missing",
                        suggestion="Specify the observed error",
                    ),
                ]
            ),
        ),
    )
    rows = findings(project, "open")
    expected = {row["id"] for row in rows if row["node_id"] == "R1"}
    readiness = {row["id"] for row in rows if row["node_id"] == "B1"}
    assert len(expected) == 2 and len(readiness) == 1
    before = list(project.connection.iterdump())
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("Status guidance must not launch a process")
        ),
    )
    mcp, cli = _status_pair(project, capsys)
    for status in (mcp["structuredContent"], cli["status"]):
        state = status["reviewResolution"]
        assert state["nextAction"]["kind"] == "reconcile_findings"
        assert set(state["nextAction"]["findingIds"]) == expected
        assert {row["id"] for row in state["blockingOpenFindings"]} == expected
        assert not readiness & set(state["nextAction"]["findingIds"])
        assert {row["revisionRelation"] for row in state["blockingOpenFindings"]} == {
            "stale",
            "current",
        }
        assert {row["severity"] for row in state["blockingOpenFindings"]} == {"minor", "major"}
        assert {row["roundId"] for row in state["blockingOpenFindings"]} == {
            old["roundId"],
            new["roundId"],
        }
        assert any(len(row["memberIds"]) == 2 for row in state["blockingOpenFindings"])
        assert state["budget"]["reservation"] == "unknown_to_product"
    assert "harness_resolve_findings" in mcp["content"][0]["text"].split("Next:")[-1]
    assert "resolve" in cli["next"] and "oracle oracle" not in cli["next"]
    assert list(project.connection.iterdump()) == before
    question = project.ask("Which edge is intended?", ["R1"])
    mcp, cli = _status_pair(project, capsys)
    assert (
        mcp["structuredContent"]["reviewResolution"]["nextAction"]["kind"]
        == "existing_canonical_blocker"
    )
    assert "harness_answer" in mcp["content"][0]["text"].split("Next:")[-1]
    assert "answer --id " + question in cli["next"]


def _record_source_evidence(store):
    # Bookkeeping envelope through the actual writer, not an executed audit.
    # Controller-path source doubles below are not real provider/model witnesses.
    store.record_audit(
        store.manifest_revision(),
        {
            "ok": True,
            "proofLevel": "prospective",
            "isMocked": True,
            "sourceOnly": "Resolution bookkeeping fixture, not product audit proof",
        },
    )
    source_controller_roundtable(
        store,
        process_stub=lambda *_: (0, "[]"),
        reviewers=[dict(name="Alpha", provider="codex"), dict(name="Beta", provider="claude")],
        executable_for=lambda provider: provider,
        max_rounds=4,
    )


def test_current_evidence_sequence_never_invents_an_oracle(project, capsys):
    _at_roundtable(project)
    _record_source_evidence(project)
    old_revision = project.manifest_revision()
    requirement = project.spine().node("R1")
    requirement["text"] += " changed exact requirement"
    project.upsert_node(requirement)
    project.compile_template(project.workspace / "template")
    assert project.manifest_revision() != old_revision
    assert not (project.workspace / "reference").exists()
    # This fixture's oracle is irrelevant: the reader may not guess its path or
    # reuse the old revision's prospective mode as current evidence.
    mcp, cli = _status_pair(project, capsys)
    for status in (mcp["structuredContent"], cli["status"]):
        state = status["reviewResolution"]
        assert state["audit"]["current"] is False and state["review"]["current"] is False
        assert state["nextAction"]["kind"] == "audit_required"
        assert state["nextAction"]["auditMode"] == "unknown"
    assert "audit --oracle oracle" not in cli["next"]
    project.record_audit(
        project.manifest_revision(), {"ok": True, "proofLevel": "prospective", "isMocked": True}
    )
    run_roundtable(
        project,
        reviewers=[dict(name="Mock", provider="codex")],
        runner=lambda *_: (0, "[]"),
        executable_for=lambda provider: provider,
    )
    mcp, cli = _status_pair(project, capsys)
    assert cli["status"]["reviewResolution"]["nextAction"]["kind"] == "review_required"
    assert cli["status"]["reviewResolution"]["review"]["current"] is False
    assert cli["status"]["reviewResolution"]["audit"]["proofLevel"] == "prospective"
    _record_source_evidence(project)
    mcp, cli = _status_pair(project, capsys)
    for status in (mcp["structuredContent"], cli["status"]):
        assert status["reviewResolution"]["nextAction"]["kind"] == "attempt_phase_advance"
        assert len(status["reviewResolution"]["review"]["successfulReviewIds"]) == 2
    assert "advance --to publish" in cli["next"]


def test_resolution_reads_preserve_state_and_canonical_refusal(project, monkeypatch, capsys):
    _at_roundtable(project)
    _record_source_evidence(project)
    before = list(project.connection.iterdump())
    files = {
        str(path): path.read_bytes()
        for path in (project.workspace / "template").rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("Resolution reads must not launch a process")
        ),
    )
    mcp, cli = _status_pair(project, capsys)
    assert cli["status"]["reviewResolution"]["nextAction"]["kind"] == "attempt_phase_advance"
    assert list(project.connection.iterdump()) == before
    assert {
        str(path): path.read_bytes()
        for path in (project.workspace / "template").rglob("*")
        if path.is_file()
    } == files
    requirement = project.spine().node("R1")
    requirement["text"] += " invalidates displayed evidence"
    project.upsert_node(requirement)
    project.compile_template(project.workspace / "template")
    with pytest.raises(PlanStoreError) as error:
        project.advance("publish", by="source-test")
    assert error.value.code == "plan-publication-evidence-required"
    vision = project.spine().node("V1")
    vision["fields"]["users"] = "Changed intended users"
    vision["fields"].pop("validation")
    project.upsert_node(vision)
    mcp, cli = _status_pair(project, capsys)
    assert cli["status"]["reviewResolution"]["manifestRevision"] is None
    assert cli["status"]["reviewResolution"]["nextAction"]["kind"] == "existing_canonical_blocker"
    assert "harness_record_confirmation" in mcp["content"][0]["text"].split("Next:")[-1]
    missing = project.workspace / "never-created"
    server = PlannerServer(missing)
    try:
        response = rpc(server, "tools/call", {"name": "harness_plan_status", "arguments": {}})
        assert response["result"]["isError"]
        assert not missing.exists()
    finally:
        server.close()


def test_partial_review_failure_keeps_supported_retry_guidance(project, capsys):
    """A failed attempted provider must not disappear behind another success.

    Existing quality tests cover the fail/warn distinction, not MCP/CLI advice.
    Real persistence and consumers own this boundary; only launch returns are
    source-controller doubles, never evidence of actual provider execution.
    """
    _at_roundtable(project)
    project.record_audit(
        project.manifest_revision(), {"ok": True, "proofLevel": "prospective", "isMocked": True}
    )
    source_controller_roundtable(
        project,
        process_stub=lambda *_: (0, "[]"),
        reviewers=[dict(name="Alpha", provider="codex")],
        executable_for=lambda provider: provider,
        max_rounds=4,
    )
    single_mcp, single_cli = _status_pair(project, capsys)
    failed = source_controller_roundtable(
        project,
        process_stub=lambda *_: (1, "SOURCE-ONLY failed reviewer; no provider launched"),
        reviewers=[dict(name="Beta", provider="claude")],
        executable_for=lambda provider: provider,
        max_rounds=4,
    )
    assert len(project.successful_reviews(project.manifest_revision())) == 1
    before = list(project.connection.iterdump())
    mcp, cli = _status_pair(project, capsys)
    for status in (mcp["structuredContent"], cli["status"]):
        state = status["reviewResolution"]
        assert state["nextAction"]["kind"] == "review_required"
        assert state["review"]["current"] is True  # Existing admission fact is unchanged.
        assert state["review"]["qualityStatus"] == "fail"
        assert "claude" in state["review"]["advice"]
        assert "retry" in state["review"]["advice"]
        assert state["review"]["detailRoute"]["roundId"] == failed["roundId"]
        assert len(state["review"]["advice"]) <= 512
        assert "SOURCE-ONLY failed reviewer" not in json.dumps(state)
    assert "harness_review" in mcp["content"][0]["text"].split("Next:")[-1]
    assert "review" in cli["next"] and "roundtable --reviewer" in cli["next"]
    assert list(project.connection.iterdump()) == before
    for status in (single_mcp["structuredContent"], single_cli["status"]):
        state = status["reviewResolution"]
        assert state["nextAction"]["kind"] == "attempt_phase_advance"
        assert state["review"]["qualityStatus"] == "warn"
        assert "cross-subscription" in state["review"]["advice"]
