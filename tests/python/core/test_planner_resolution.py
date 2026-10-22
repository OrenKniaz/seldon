"""Actual planner consumers over durable source-only review/evidence fixtures.

Protected behavior: host repair guidance names all publication-blocking clusters.
Credible regression: generic phase prose hides stale/minor open work or sends a
prospective plan to an invented oracle directory. Existing review ingestion and
gate tests do not prove the MCP/CLI next-action contract. The primary boundary
is real store -> MCP wire serialization and CLI JSON, not a copied helper answer.
Provider launches and initial native responses are source-only doubles. The
bounded public CLI sequence also runs a real oracle audit and its subprocesses.
No live model, external controller or worker delivery is exercised.
"""

import json
import shlex
import subprocess
import sys

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


@pytest.mark.parametrize("failed_provider", [False, True], ids=["clean", "quality-failure"])
def test_publication_continuation_uses_configured_gate_not_worker_quality(
    project, capsys, failed_provider
):
    """The real phase mutation is the oracle; review returns are SOURCE-ONLY doubles."""
    from codex_harness.core.planner_resume import resume_plan

    _at_roundtable(project)
    project.record_audit(
        project.manifest_revision(), {"ok": True, "proofLevel": "prospective", "isMocked": True}
    )
    for name, provider, code in [
        ("Alpha", "codex", 0),
        ("Beta", "claude", 1 if failed_provider else 0),
    ]:
        source_controller_roundtable(
            project,
            process_stub=lambda *_, code=code: (
                code,
                "SOURCE-ONLY failed review" if code else "[]",
            ),
            reviewers=[dict(name=name, provider=provider)],
            executable_for=lambda provider: provider,
            max_rounds=4,
        )
    before = list(project.connection.iterdump())
    mcp, cli = _status_pair(project, capsys)
    resumed = resume_plan(project.workspace)
    assert list(project.connection.iterdump()) == before
    assert "advance --to publish" in cli["next"]
    assert "harness_advance to_phase=publish" in mcp["content"][0]["text"].split("Next:")[-1]
    # Execute the actual shared public action, not a copied expected command.
    prefix = ["--workspace", str(project.workspace), "--json"]
    action = cli["status"]["continuation"]["planning"]
    assert plan_cli.main([*prefix, *action["cliArguments"]]) == 0
    actual = json.loads(capsys.readouterr().out)
    assert not actual["isError"] and project.phase() == "publish"
    print(
        json.dumps({"beforeCLI": cli, "beforeMCP": mcp, "resume": resumed, "actualAdvance": actual})
    )
    for status in (mcp["structuredContent"], cli["status"], resumed["canonicalStatus"]):
        from codex_harness.core.planner_views import continuation_projection

        projection = status["continuation"]
        assert continuation_projection(status, status["reviewResolution"]) == projection
        assert projection["planning"]["kind"] == "attempt_phase_advance"
        assert projection["planning"]["targetPhase"] == "publish"
        assert projection["workerQuality"]["blocked"] is failed_provider
        assert projection["productAcceptance"]["accepted"] is False
    assert resumed["nextAction"]["kind"] == "attempt_phase_advance"
    # Local SOURCE-ONLY plan export, not Git/private publication or worker proof.
    server = PlannerServer(project.workspace)
    try:
        publication = rpc(
            server,
            "tools/call",
            {
                "name": "harness_publish",
                "arguments": {
                    "proof_level": "prospective",
                    "reason": "SOURCE-ONLY bookkeeping audit; no executed oracle proof",
                },
            },
        )["result"]
        assert not publication["isError"], publication
        current = server._status()
        assert current["publication"]["up_to_date"] is True
        assert current["pending_gate"] is None  # Actual configured publication prerequisites met.
        assert current["continuation"]["workerQuality"]["blocked"] is failed_provider
        if failed_provider:
            assert current["continuation"]["recommendationScope"] == "worker-admission"
            assert current["continuation"]["planning"]["kind"] == "not_applicable"
            assert "harness_review" in publication["content"][0]["text"].split("Next:")[-1]
            held = resume_plan(project.workspace)
            assert held["nextAction"]["kind"] == "review_required"
        print(
            json.dumps(
                {"sourceOnlyPublication": publication, "postPublication": current["continuation"]}
            )
        )
    finally:
        server.close()


@pytest.mark.parametrize("case", ["required-fail", "required-clean", "finding"])
def test_continuation_policy_and_finding_actions_keep_reciprocal_success(project, capsys, case):
    from codex_harness.core.planner_resume import resume_plan

    _at_roundtable(project)
    project.record_audit(
        project.manifest_revision(), {"ok": True, "proofLevel": "prospective", "isMocked": True}
    )
    if case.startswith("required"):
        project.record_plan_review_policy(
            [
                dict(name=name, provider=provider, required=True)
                for name, provider in [("Alpha", "codex"), ("Beta", "claude")]
            ],
            project.manifest_revision(),
        )
    for name, provider in [("Alpha", "codex"), ("Beta", "claude")]:
        code = 1 if case == "required-fail" and name == "Beta" else 0
        output = (
            json.dumps(
                [
                    dict(
                        node_id="R1",
                        severity="major",
                        finding="SOURCE-ONLY unresolved clause",
                        suggestion="Inspect and repair accepted requirement",
                    )
                ]
            )
            if case == "finding"
            else "[]"
        )
        source_controller_roundtable(
            project,
            process_stub=lambda *_, code=code, output=output: (code, output),
            reviewers=[dict(name=name, provider=provider)],
            executable_for=lambda provider: provider,
        )
    mcp, cli = _status_pair(project, capsys)
    resumed = resume_plan(project.workspace)
    projections = [
        mcp["structuredContent"]["continuation"],
        cli["status"]["continuation"],
        resumed["canonicalStatus"]["continuation"],
    ]
    assert projections[0] == projections[1] == projections[2]
    action = projections[0]["planning"]
    server = PlannerServer(project.workspace)
    try:
        if case == "required-clean":
            assert action["kind"] == "attempt_phase_advance"
            result = rpc(
                server, "tools/call", {"name": action["mcpTool"], "arguments": action["arguments"]}
            )["result"]
            assert not result["isError"] and project.phase() == "publish"
        else:
            assert action["kind"] == (
                "review_policy_hold" if case == "required-fail" else "reconcile_findings"
            )
            assert cli["status"]["pending_gate"] == (
                "plan-review-policy-unmet" if case == "required-fail" else "plan-findings-open"
            )
            assert cli["status"]["phase_gate"] == "zero-open-clarifications"
            before = list(project.connection.iterdump())
            inspection = action["inspection"]
            result = rpc(
                server,
                "tools/call",
                {"name": inspection["mcpTool"], "arguments": inspection["arguments"]},
            )["result"]
            assert not result["isError"] and list(project.connection.iterdump()) == before
            refused = rpc(
                server,
                "tools/call",
                {"name": "harness_advance", "arguments": {"to_phase": "publish"}},
            )["result"]
            assert refused["isError"] and project.phase() == "roundtable"
            assert (
                "plan-review-policy-unmet" if case == "required-fail" else "plan-findings-open"
            ) in refused["content"][0]["text"]
        print(
            json.dumps(
                {
                    "case": case,
                    "projection": projections[0],
                    "executedActionResult": result,
                    "postPhase": project.phase(),
                }
            )
        )
    finally:
        server.close()


def test_continuation_stale_action_and_concurrent_observation_refresh(project, capsys):
    from codex_harness.core.planner_views import review_resolution_state

    _at_roundtable(project)
    _record_source_evidence(project)
    _, cli = _status_pair(project, capsys)
    old_status = cli["status"]
    action = old_status["continuation"]["planning"]
    assert action["kind"] == "attempt_phase_advance"
    changed = project.spine().node("R1")
    changed["text"] += " SOURCE-ONLY concurrent changed requirement"
    project.upsert_node(changed)
    observed = review_resolution_state(project, old_status)
    assert observed["nextAction"]["kind"] == "unavailable_refresh"
    assert observed["continuation"]["classification"] == "unavailable"
    assert (
        plan_cli.main(["--workspace", str(project.workspace), "--json", *action["cliArguments"]])
        == 2
    )
    refused = json.loads(capsys.readouterr().out)
    assert "plan-publication-evidence-required" in refused["text"]
    assert project.phase() == "roundtable"
    assert refused["status"]["continuation"]["planning"]["kind"] == "audit_required"
    print(
        json.dumps(
            {
                "oldAction": action,
                "concurrentProjection": observed["continuation"],
                "actualRefusal": refused,
            }
        )
    )


@pytest.mark.parametrize("phase", ["roundtable", "publish"])
def test_metadata_only_compile_prerequisite_has_precise_public_gate(project, capsys, phase):
    """Metadata removal preserves approval, but cannot claim publication readiness."""
    from codex_harness.core.planner_resume import resume_plan

    _at_roundtable(project)
    _record_source_evidence(project)  # SOURCE-ONLY recorded audit/review envelopes.
    if phase == "publish":
        project.advance("publish", by="source-test")
    original_revision = project.manifest_revision()
    original_approval = project.status()["approval"]
    original_vision = project.spine().node("V1")
    edited_vision = json.loads(json.dumps(original_vision))
    edited_vision["fields"].pop("validation")
    server = PlannerServer(project.workspace)
    try:
        changed = rpc(
            server,
            "tools/call",
            {"name": "harness_upsert_node", "arguments": {"node": edited_vision}},
        )["result"]
        assert not changed["isError"], changed
        before = list(project.connection.iterdump())
        mcp, cli = _status_pair(project, capsys)
        resumed = resume_plan(project.workspace)
        assert list(project.connection.iterdump()) == before
        statuses = [mcp["structuredContent"], cli["status"], resumed["canonicalStatus"]]
        for status in statuses:
            assert status["approval"] == original_approval
            assert status["approval"]["granted"] is True
            assert status["reviewResolution"]["manifestRevision"] is None
            assert status["continuation"]["planning"]["kind"] == "compile_required"
        assert (
            statuses[0]["continuation"]
            == statuses[1]["continuation"]
            == statuses[2]["continuation"]
        )
        assert "compile --template" in cli["next"]
        assert "harness_compile" in mcp["content"][0]["text"].split("Next:")[-1]
        mutation = (
            {"name": "harness_advance", "arguments": {"to_phase": "publish"}}
            if phase == "roundtable"
            else {
                "name": "harness_publish",
                "arguments": {"proof_level": "prospective", "reason": "SOURCE-ONLY fixture"},
            }
        )
        refused = rpc(server, "tools/call", mutation)["result"]
        assert refused["isError"] and project.phase() == phase
        assert list(project.connection.iterdump()) == before
        print(
            json.dumps(
                {
                    "phase": phase,
                    "missingMetadataCLI": cli,
                    "MCP": mcp,
                    "resume": resumed,
                    "actualRefusal": refused,
                }
            ),
            file=sys.stderr,
        )
        for status in statuses:
            assert status["pending_gate"] == "compile-required"
            assert status["phase_gate"] == (
                "zero-open-clarifications"
                if phase == "roundtable"
                else "current-revision-published"
            )
        repaired = rpc(
            server,
            "tools/call",
            {"name": "harness_upsert_node", "arguments": {"node": original_vision}},
        )["result"]
        assert not repaired["isError"], repaired
        assert (
            plan_cli.main(
                [
                    "--workspace",
                    str(project.workspace),
                    "--json",
                    "compile",
                    "--template",
                    str(project.workspace / "template"),
                    "--out",
                    str(project.workspace / "manifest.json"),
                ]
            )
            == 0
        )
        compiled = json.loads(capsys.readouterr().out)
        assert not compiled["isError"] and project.manifest_revision() == original_revision
        _, refreshed = _status_pair(project, capsys)
        action = refreshed["status"]["continuation"]["planning"]
        assert action["kind"] == (
            "attempt_phase_advance" if phase == "roundtable" else "attempt_publication"
        )
        if phase == "publish":
            command = shlex.split(refreshed["next"].split(" (attempt only;")[0])
            assert command[:3] == ["python", "-m", "codex_harness.core.plan_cli"]
            command = [
                "SOURCE-ONLY bookkeeping audit" if token == "<actual no-oracle reason>" else token
                for token in command[3:]
            ]
            assert plan_cli.main(["--json", *command]) == 0
            actual = json.loads(capsys.readouterr().out)
        else:
            actual = rpc(
                server,
                "tools/call",
                {"name": action["mcpTool"], "arguments": action["arguments"]},
            )["result"]
        assert not actual["isError"], actual
        assert project.phase() == "publish"
        if phase == "publish":
            assert project.status()["publication"]["up_to_date"] is True
        print(
            json.dumps(
                {
                    "repairedCLI": refreshed,
                    "compiled": compiled,
                    "executedAction": actual,
                    "postStatus": project.status(),
                }
            )
        )
    finally:
        server.close()


def test_continuation_missing_plan_surfaces_do_not_create_state(tmp_path, capsys):
    from codex_harness.core.planner_resume import resume_plan
    from codex_harness.core.planner_views import review_resolution_state

    missing = tmp_path / "missing"
    server = PlannerServer(missing)
    try:
        status = server._status()
        view = review_resolution_state(None, status)
        mcp = rpc(server, "tools/call", {"name": "harness_plan_status", "arguments": {}})["result"]
        assert not mcp["isError"] and view["nextAction"]["kind"] == "unavailable_refresh"
        assert mcp["content"][0]["text"].split("Next:")[-1].startswith(" harness_plan_start")
        assert plan_cli.main(["--workspace", str(missing), "--json", "status"]) == 2
        cli = json.loads(capsys.readouterr().out)
        resumed = resume_plan(missing)
        assert cli["isError"] and resumed["reasonCode"] == "plan-missing"
        assert resumed["nextAction"]["kind"] == "start_plan"
        assert not missing.exists()
        print(json.dumps({"missingMCP": mcp, "missingCLI": cli, "missingResume": resumed}))
    finally:
        server.close()


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
        assert not response["result"]["isError"]
        assert "harness_plan_start" in response["result"]["content"][0]["text"]
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
        assert state["nextAction"]["kind"] == "attempt_phase_advance"
        assert state["review"]["current"] is True  # Existing admission fact is unchanged.
        assert status["continuation"]["workerQuality"]["blocked"] is True
        assert state["review"]["qualityStatus"] == "fail"
        assert "claude" in state["review"]["advice"]
        assert "retry" in state["review"]["advice"]
        assert state["review"]["detailRoute"]["roundId"] == failed["roundId"]
        assert len(state["review"]["advice"]) <= 512
        assert "SOURCE-ONLY failed reviewer" not in json.dumps(state)
    assert "harness_advance to_phase=publish" in mcp["content"][0]["text"].split("Next:")[-1]
    assert "advance --to publish" in cli["next"] and "review quality warning" in cli["next"]
    assert list(project.connection.iterdump()) == before
    for status in (single_mcp["structuredContent"], single_cli["status"]):
        state = status["reviewResolution"]
        assert state["nextAction"]["kind"] == "attempt_phase_advance"
        assert state["review"]["qualityStatus"] == "warn"
        assert "cross-subscription" in state["review"]["advice"]


@pytest.mark.parametrize("disposition", ["resolved", "rejected", "deferred"])
def test_blocked_public_cli_advice_uses_executable_disposition_flags(project, capsys, disposition):
    """The host must be able to follow the refusal without reading source.

    Reviewer process returns are explicit source-only doubles; canonical finding
    and publication guards, the CLI parser and persistence remain real.
    """
    _at_roundtable(project)
    source_controller_roundtable(
        project,
        process_stub=lambda *_: (
            0,
            json.dumps(
                [
                    dict(
                        node_id="R1",
                        severity="major",
                        finding="SOURCE-ONLY recorded contract question",
                        suggestion="Inspect the accepted answer contract before disposition",
                    )
                ]
            ),
        ),
        reviewers=[dict(name="Alpha", provider="codex")],
        executable_for=lambda provider: provider,
    )
    prefix = ["--workspace", str(project.workspace), "--json"]
    assert plan_cli.main([*prefix, "advance", "--to", "publish"]) == 2
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["isError"] and project.phase() == "roundtable"
    advice = refusal["next"]
    for supported in ("--resolved", "--rejected", "--deferred"):
        assert supported in advice
    assert "--status" not in advice
    assert plan_cli.main([*prefix, "findings"]) == 0
    reported = json.loads(capsys.readouterr().out)
    rows = reported["data"]
    if isinstance(rows, dict):
        rows = rows["findings"]
    recorded = next(row for row in rows if row["status"] == "open")
    assert recorded["id"] in advice
    assert recorded["node_id"] == "R1" and recorded["finding"] and recorded["suggestion"]
    assert (
        plan_cli.main(
            [
                *prefix,
                "resolve",
                "--id",
                recorded["id"],
                "--" + disposition,
                "--resolution",
                "SOURCE-ONLY explicit host disposition; not product acceptance",
            ]
        )
        == 0
    )
    response = json.loads(capsys.readouterr().out)
    assert not response["isError"]
    stored = next(row for row in findings(project) if row["id"] == recorded["id"])
    assert stored["status"] == disposition
    assert "SOURCE-ONLY explicit host disposition" in stored["resolution"]
    assert project.phase() == "roundtable"
    assert not project.status()["publication"]["up_to_date"]


def test_public_repair_loop_refreshes_review_before_canonical_publication(
    project, monkeypatch, capsys
):
    """A bounded host sequence follows public replies, without bypassing gates.

    Initial native forms and reviewer processes are source-only fixture inputs.
    The author edit, parser, real oracle audit, fresh packet and publication
    guards run normally. This is neither a live controller nor product proof.
    """
    from codex_harness.core import roundtable

    _at_roundtable(project)
    packets = []

    def reviewer_return(launch, *_):
        packets.append(launch.stdin_text)
        return (
            0,
            json.dumps(
                [
                    dict(
                        node_id="R1",
                        severity="major",
                        finding="The accepted answer 42 is absent from the requirement text",
                        suggestion=(
                            "State the accepted answer in R1, retaining the protected proofs"
                        ),
                    )
                ]
                if len(packets) == 1
                else []
            ),
        )

    reviewer = dict(name="Alpha", provider="codex")
    source_controller_roundtable(
        project,
        process_stub=reviewer_return,
        reviewers=[reviewer],
        executable_for=lambda provider: provider,
    )
    monkeypatch.setattr(roundtable, "_run", reviewer_return)
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)
    prefix = ["--workspace", str(project.workspace), "--json"]

    def public(*arguments, expected=0):
        assert plan_cli.main([*prefix, *arguments]) == expected
        result = json.loads(capsys.readouterr().out)
        assert result["isError"] is (expected != 0)
        return result

    refusal = public("advance", "--to", "publish", expected=2)
    assert "resolve" in refusal["next"]
    assert refusal["data"]["authorHandoff"]["state"] == "author_action_required"
    assert refusal["text"].count("**Author repair:") == 1
    original_revision = refusal["status"]["manifest_revision"]
    open_findings = public("findings")["data"]["findings"]
    assert len(open_findings) == 1 and open_findings[0]["status"] == "open"
    finding_id = open_findings[0]["id"]
    assert finding_id in refusal["next"]
    shown = public("show", "R1")["data"]
    repaired = {key: shown[key] for key in ("id", "kind", "title", "text", "parents", "fields")}
    repaired["text"] = "Receive the accepted answer 42"
    public("node", "--json", json.dumps(repaired))
    public("compile", "--template", "template", "--out", "manifest.json")
    public(
        "resolve",
        "--id",
        finding_id,
        "--resolved",
        "--resolution",
        "R1 now states accepted answer 42; unchanged protected unit and journey proofs cover it",
    )
    stale = public("status")["status"]["reviewResolution"]
    assert stale["manifestRevision"] != original_revision
    assert stale["audit"]["current"] is False and stale["review"]["current"] is False
    assert stale["nextAction"]["kind"] == "audit_required"
    # The oracle route is explicitly supplied by this source fixture, not guessed.
    assert public("audit", "--oracle", "oracle")["data"]["ok"] is True
    pending = public("status")["status"]["reviewResolution"]
    assert pending["nextAction"]["kind"] == "review_required"
    assert len(packets) == 1
    public("advance", "--to", "publish", expected=2)
    reviewed = public("roundtable", "--reviewer", json.dumps(reviewer))
    assert not reviewed["data"]["invalid"] and len(packets) == 2
    assert packets[0] != packets[1] and "Receive the accepted answer 42" in packets[1]
    ready = public("status")["status"]["reviewResolution"]
    assert ready["nextAction"]["kind"] == "attempt_phase_advance"
    public("advance", "--to", "publish")
    public("publish")
    terminal = public("status")["status"]
    assert terminal["publication"]["up_to_date"] is True
    assert terminal["published_revision"] == ready["manifestRevision"]
    assert terminal["acceptance"]["accepted"] is False
    preserved = next(row for row in findings(project) if row["id"] == finding_id)
    assert preserved["status"] == "resolved" and preserved["resolution"]
    assert len(list(project.connection.execute("SELECT id FROM review_runs"))) == 2


def _public_review_with_members(project, monkeypatch, capsys, node_id="R1"):
    """Actual CLI producer response; provider processes alone are source doubles."""
    from codex_harness.core import roundtable

    _at_roundtable(project)
    calls = []

    def reviewer(*_):
        calls.append(len(calls) + 1)
        return 0, json.dumps(
            [
                dict(
                    node_id=node_id,
                    severity="major",
                    finding="Accepted answer is unspecified",
                    suggestion=f"Member {calls[-1]}: preserve the answer contract " + "x" * 1300,
                )
            ]
        )

    monkeypatch.setattr(roundtable, "_run", reviewer)
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)
    reviewers = [dict(name="Alpha", provider="codex"), dict(name="Beta", provider="claude")]
    prefix = ["--workspace", str(project.workspace), "--json"]
    assert (
        plan_cli.main(
            [
                *prefix,
                "roundtable",
                "--reviewer",
                json.dumps(reviewers[0]),
                "--reviewer",
                json.dumps(reviewers[1]),
                "--max-total",
                "1",
            ]
        )
        == 0
    )
    return json.loads(capsys.readouterr().out), calls, reviewers


@pytest.mark.parametrize("node_id", ["R1", "plan"])
def test_roundtable_return_hands_member_repairs_to_invoking_planner(
    project, monkeypatch, capsys, node_id
):
    response, calls, _ = _public_review_with_members(project, monkeypatch, capsys, node_id)
    handoff = response["data"]["authorHandoff"]
    assert len(calls) == 2
    assert handoff["manifestRevision"] == response["status"]["manifest_revision"]
    assert handoff["spineRevision"] == response["status"]["spine_revision"]
    assert handoff["clusterCount"] == 1 and handoff["memberCount"] == 2
    assert {row["id"] for row in handoff["members"]} == {
        row["id"] for row in findings(project, "open", expand=True)
    }
    assert {row["suggestion"].split(":")[0] for row in handoff["members"]} == {
        "Member 1",
        "Member 2",
    }
    assert all(row["suggestionTruncated"] for row in handoff["members"])
    assert all(row["revisionRelation"] == "current" for row in handoff["members"])
    assert all(len(row["memberIds"]) == 2 for row in handoff["members"])
    assert "author repair" in response["text"].lower()
    assert "Worker-owned product behavior" in response["text"]
    assert "findings --expand" in handoff["details"]["cliCommand"]
    assert "resolve" in response["next"] and "--status" not in response["next"]

    # The real existing detail endpoint must actually return both full suggestions.
    server = PlannerServer(project.workspace)
    try:
        route = handoff["members"][0]["detailRoute"]
        detail = rpc(
            server,
            "tools/call",
            {
                "name": route["mcpTool"],
                "arguments": {"id": route["roundId"]},
            },
        )["result"]
        assert not detail["isError"]
        exact = detail["structuredContent"]["findingMembers"]
        assert {row["suggestion"] for row in exact} == {
            row["suggestion"] for row in findings(project, expand=True)
        }
        assert all(row["suggestion"] in detail["content"][0]["text"] for row in exact)
        node_route = handoff["members"][0]["nodeRoute"]
        before = list(project.connection.iterdump())
        cli_route = shlex.split(
            next(
                line.split("Inspect affected node: ", 1)[1]
                for line in response["text"].splitlines()
                if "Inspect affected node: " in line
            )
        )
        assert plan_cli.main(cli_route[cli_route.index("--workspace") :]) == 0
        cli_node = capsys.readouterr().out
        node = rpc(
            server,
            "tools/call",
            {
                "name": node_route["mcpTool"],
                "arguments": {"id": node_route["id"]} if "id" in node_route else {},
            },
        )["result"]
        assert not node["isError"]
        if node_id == "plan":
            assert node_route["mcpTool"] == "harness_nodes" and "id" not in node_route
            assert node["structuredContent"]["nodes"] == project.list_nodes()
            assert all(row["id"] in cli_node for row in project.list_nodes())
            assert all(row["nodeId"] == "plan" for row in handoff["members"])
        else:
            assert node["structuredContent"]["id"] == node_id
            assert node_id in cli_node
        assert list(project.connection.iterdump()) == before
        assert len(calls) == 2
    finally:
        server.close()


@pytest.mark.parametrize("operation", ["harness_advance", "harness_publish"])
def test_blocked_mcp_reply_contains_bound_author_handoff(project, monkeypatch, capsys, operation):
    _public_review_with_members(project, monkeypatch, capsys)
    if operation == "harness_publish":
        # Reach publish normally before a later real review exposes new blocking work.
        for row in findings(project):
            project.resolve_findings(
                [
                    dict(
                        id=row["id"],
                        status="rejected",
                        resolution="Source fixture: inspect subsequent review",
                    )
                ]
            )
        _record_source_evidence(project)
        project.advance("publish", by="source-test")
        source_controller_roundtable(
            project,
            process_stub=lambda *_: (
                0,
                json.dumps(
                    [
                        dict(
                            node_id="R1",
                            severity="major",
                            finding="Publication contract gap",
                            suggestion="Repair the requirement before publication",
                        )
                    ]
                ),
            ),
            reviewers=[dict(name="Gamma", provider="codex")],
            executable_for=lambda *_: sys.executable,
        )
    requirement = project.spine().node("R1")
    requirement["text"] += " revised author context"
    project.upsert_node(requirement)
    project.compile_template(project.workspace / "template")
    before = list(project.connection.iterdump())
    server = PlannerServer(project.workspace)
    try:
        result = rpc(
            server,
            "tools/call",
            {
                "name": operation,
                "arguments": {"to_phase": "publish"} if operation == "harness_advance" else {},
            },
        )["result"]
        assert result["isError"]
        handoff = result["structuredContent"]["authorHandoff"]
        assert handoff["manifestRevision"] == project.manifest_revision()
        assert all(row["revisionRelation"] == "stale" for row in handoff["members"])
        assert "author repair" in result["content"][0]["text"].lower()
        assert "harness_resolve_findings" in result["content"][0]["text"]
    finally:
        server.close()
    assert list(project.connection.iterdump()) == before
    assert not project.status()["publication"]["up_to_date"]


def test_exhausted_cli_review_holds_cap_without_waiver(project, monkeypatch, capsys):
    _, calls, reviewers = _public_review_with_members(project, monkeypatch, capsys)
    assert (
        plan_cli.main(
            [
                "--workspace",
                str(project.workspace),
                "--json",
                "roundtable",
                "--reviewer",
                json.dumps(reviewers[0]),
                "--max-total",
                "1",
            ]
        )
        == 2
    )
    response = json.loads(capsys.readouterr().out)
    assert len(calls) == 2
    assert "1/1 total logical review attempts" in response["text"]
    assert "waive" not in json.dumps(response).lower()
    assert "raising or omitting" not in json.dumps(response)
    assert "held" in response["next"].lower()
    assert response["data"]["authorHandoff"]["memberCount"] == 2


def test_author_handoff_bounds_members_and_retains_exact_public_details(
    project, monkeypatch, capsys
):
    from codex_harness.core import planner_views

    _at_roundtable(project)
    raw = [
        dict(
            node_id="R1",
            severity="major",
            finding="Same accepted answer contract gap",
            suggestion=f"Member {index}: " + "z" * 1400,
        )
        for index in range(35)
    ]
    reviewed = source_controller_roundtable(
        project,
        process_stub=lambda *_: (0, json.dumps(raw)),
        reviewers=[dict(name="Alpha", provider="codex")],
        executable_for=lambda *_: sys.executable,
    )
    handoff = planner_views.review_author_handoff(project, project.status())
    assert handoff["clusterCount"] == 1 and handoff["memberCount"] == 35
    assert len(handoff["members"]) == 16 and handoff["omittedMemberCount"] == 19
    assert all(row["membershipTruncated"] for row in handoff["members"])
    assert all(len(row["memberIds"]) == 32 for row in handoff["members"])
    fallback = shlex.split(handoff["details"]["cliCommand"])
    assert fallback[0:3] == ["python", "-m", "codex_harness.core.plan_cli"]
    retrieved = subprocess.run(
        fallback,
        cwd=project.workspace.parent,
        capture_output=True,
        text=True,
    )
    assert retrieved.returncode == 0, retrieved.stdout + retrieved.stderr
    # Execute the emitted workspace/argument set, not a hardcoded known round.
    exact_fallback = json.loads(retrieved.stdout)["data"]["findings"]
    assert len(exact_fallback) == 35
    assert {row["suggestion"] for row in exact_fallback} == {row["suggestion"] for row in raw}
    assert (
        plan_cli.main(
            [
                "--workspace",
                str(project.workspace),
                "--json",
                "review",
                reviewed["roundId"],
            ]
        )
        == 0
    )
    detailed = json.loads(capsys.readouterr().out)["data"]["findingMembers"]
    assert len(detailed) == 35
    assert {row["suggestion"] for row in detailed} == {row["suggestion"] for row in raw}
    assert all(len(row["memberIds"]) == 35 for row in detailed)


def test_changed_author_handoff_read_returns_refresh_without_stale_repairs(
    project, monkeypatch, capsys
):
    from codex_harness.core import planner_views, roundtable

    _public_review_with_members(project, monkeypatch, capsys)
    status = project.status()
    actual = roundtable.findings
    reads = []

    def changed(store, *args, **kwargs):
        reads.append(True)
        rows = actual(store, *args, **kwargs)
        if len(reads) == 2:
            requirement = store.spine().node("R1")
            requirement["text"] += " concurrent author edit"
            store.upsert_node(requirement)
        return rows

    monkeypatch.setattr(roundtable, "findings", changed)
    handoff = planner_views.review_author_handoff(project, status)
    assert handoff["state"] == "refresh_required"
    assert handoff["members"] == [] and handoff["memberCount"] is None
    assert handoff["nextAction"]["kind"] == "unavailable_refresh"
    assert handoff["next"] == "harness_plan_status"


def test_disposition_during_handoff_read_invalidates_unchanged_revision(
    project, monkeypatch, capsys
):
    from codex_harness.core import planner_views, roundtable

    _public_review_with_members(project, monkeypatch, capsys)
    status = project.status()
    revision = project._revision(), project.manifest_revision(), project.phase()
    actual = roundtable.findings
    reads = []

    def dispositioned(store, *args, **kwargs):
        rows = actual(store, *args, **kwargs)
        reads.append(True)
        if len(reads) == 2:
            # The real writer changes the review projection, not plan identity.
            store.resolve_findings(
                [
                    dict(
                        id=rows[0]["id"],
                        status="resolved",
                        resolution="Concurrent author disposition with recorded evidence",
                    )
                ]
            )
        return rows

    monkeypatch.setattr(roundtable, "findings", dispositioned)
    handoff = planner_views.review_author_handoff(project, status)
    assert (project._revision(), project.manifest_revision(), project.phase()) == revision
    assert all(row["status"] == "resolved" for row in actual(project, expand=True))
    assert handoff["state"] == "refresh_required"
    assert handoff["members"] == [] and handoff["memberCount"] is None
    assert handoff["nextAction"]["kind"] == "unavailable_refresh"
    assert handoff["next"] == "harness_plan_status"


def test_roundtable_without_started_plan_preserves_public_error(tmp_path, monkeypatch, capsys):
    def no_provider(*args, **kwargs):
        pytest.fail("Missing-plan error must not launch a provider")

    monkeypatch.setattr(subprocess, "Popen", no_provider)
    assert plan_cli.main(["--workspace", str(tmp_path), "--json", "roundtable"]) == 2
    response = json.loads(capsys.readouterr().out)
    assert response["isError"]
    assert "Call" in response["text"] and "start first" in response["text"]
    assert "start --idea" in response["next"]
    assert response["data"] is None
    assert not (tmp_path / ".codex-harness").exists()
