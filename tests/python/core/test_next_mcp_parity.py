"""Actual public Next consumers over existing source-only planning fixtures.

Review returns/bookkeeping audits and native clients are labelled test inputs,
never live provider/user evidence. No workers, model calls or P1 stores are used.
"""

import json
import shlex

import pytest
from tests.python.core import test_planner_replay_gaps, test_planner_resolution
from tests.python.core.planning_support import source_controller_roundtable

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.planner_audits import audit_synchronously

project = test_planner_replay_gaps.project


def wire(server, tool_name, **arguments):
    return server.handle_line(
        json.dumps(
            dict(
                jsonrpc="2.0",
                id=7,
                method="tools/call",
                params=dict(
                    name=tool_name,
                    arguments={"expected_workspace": str(server.workspace), **arguments},
                ),
            )
        )
    )


def result(server, tool_name, **arguments):
    return wire(server, tool_name, **arguments)["result"]


def next_line(reply):
    return reply["content"][0]["text"].rsplit("\nNext: ", 1)[1]


def test_resume_compile_next_uses_existing_native_compiler(project):
    """Catch resume falling back to CLI even though native compilation exists.

    Existing metadata-removal tests cover status/CLI, not resume's emitted footer.
    """
    test_planner_resolution._at_roundtable(project)
    server = PlannerServer(project.workspace)
    try:
        vision = project.spine().node("V1")
        vision["fields"].pop("validation")
        assert not result(server, "harness_upsert_node", node=vision)["isError"]
        before = list(project.connection.iterdump())
        resumed = result(server, "harness_plan_resume")
        assert "harness_compile" in next_line(resumed)
        assert "existing CLI" not in next_line(resumed)
        assert list(project.connection.iterdump()) == before
    finally:
        server.close()


def test_failed_advance_and_resume_name_native_roundtable(project):
    """Catch error/resume advice bypassing the ordinary status native special case.

    Existing successful status tests miss these two public recovery consumers.
    """
    test_planner_resolution._at_roundtable(project)
    assert audit_synchronously(project)["proofCreditGranted"]
    server = PlannerServer(project.workspace)
    try:
        before = list(project.connection.iterdump())
        blocked = result(server, "harness_advance", to_phase="publish")
        assert blocked["isError"] and project.phase() == "roundtable"
        resumed = result(server, "harness_plan_resume")
        for reply in (blocked, resumed):
            assert "harness_roundtable" in next_line(reply)
            assert "name/provider/model/effort" in next_line(reply)
            assert "existing CLI" not in next_line(reply)
        assert list(project.connection.iterdump()) == before
    finally:
        server.close()


def test_unlocked_vision_next_opens_native_form_without_quote_argument(project):
    """Catch the owning acceptance instruction supplying a forbidden statement arg.

    Native-origin tests reject forged arguments but do not execute this generated
    instruction after a real canonical vision edit.
    """
    test_planner_replay_gaps.confirmed_vision(project)
    server = PlannerServer(project.workspace)
    try:
        vision = project.spine().node("V1")
        vision["text"] += " SOURCE-ONLY changed understanding."
        assert not result(server, "harness_upsert_node", node=vision)["isError"]
        status = result(server, "harness_plan_status")["structuredContent"]
        assert status["acceptance"]["invalidReason"] == "vision-unlocked"
        command = shlex.split(status["acceptance"]["next"])
        arguments = dict(token.split("=", 1) for token in command[1:])
        server.handle_line(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=1,
                    method="initialize",
                    params=dict(protocolVersion="2025-06-18", capabilities={"elicitation": {}}),
                )
            )
        )
        server.handle_line(json.dumps(dict(jsonrpc="2.0", method="notifications/initialized")))
        approvals = project.connection.execute("SELECT count(*) FROM approvals").fetchone()[0]
        offered = wire(server, command[0], **arguments)
        assert offered.get("method") == "elicitation/create", offered
        assert (
            project.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == approvals
        )
    finally:
        server.close()


@pytest.mark.parametrize("subject", ["draft", "oracle", "product"])
def test_acceptance_next_follows_actual_scenario_and_run_subject(project, subject):
    """Catch draft -> execution and oracle -> ordinary acceptance-review misdirection.

    Existing scenario/run tests cover validity and subject truth, not their Next
    operation; product remains the positive sequencing control. Reference code
    is an existing tiny fixture, not a delivered product or provider witness.
    """
    test_planner_replay_gaps.confirmed_vision(project)
    server = PlannerServer(project.workspace)
    try:
        derived = result(server, "harness_accept_scenarios", derive=True)
        assert not derived["isError"]
        if subject == "draft":
            assert "harness_accept_scenarios" in next_line(derived)
            assert "harness_accept_run" not in next_line(derived)
            return
        scenarios = derived["structuredContent"]["scenarios"]
        for scenario in scenarios:
            scenario.update(
                steps=[
                    '"{python}" -B -c "import sys; sys.path.insert(0, r\'{repo}\'); '
                    'from answer import answer; assert answer() == 42"'
                ],
                expect={"exit": 0},
            )
        saved = result(server, "harness_accept_scenarios", scenarios=scenarios)
        assert not saved["isError"] and "harness_accept_run" in next_line(saved)
        run = result(
            server, "harness_accept_run", repo=str(project.workspace / "oracle"), subject=subject
        )
        assert not run["isError"], run
        assert run["structuredContent"]["subject"] == subject
        assert "harness_accept_status" in next_line(run)
        server.acceptance.dispatch_after_flush(server.store)
        from tests.python.core.test_acceptance import drain_acceptance

        drain_acceptance(server.acceptance, server.store)
        run = result(server, "harness_accept_status", id=run["structuredContent"]["attemptId"])
        assert not run["isError"], run
        if subject == "oracle":
            assert "subject=product" in next_line(run)
            assert "harness_accept_review" not in next_line(run)
        else:
            assert next_line(run).startswith("harness_accept_review")
    finally:
        server.close()


@pytest.mark.parametrize("quality_failure", [False, True])
def test_current_publication_next_preserves_inspection_and_quality_scope(project, quality_failure):
    """Catch missing execution ceilings and discarded worker-quality recommendation.

    Existing publication tests protect authority, not the clean post-publication
    status handoff; source-controller returns are explicit nonlive test inputs.
    """
    test_planner_resolution._at_roundtable(project)
    assert audit_synchronously(project)["proofCreditGranted"]
    for name, provider, code in (
        ("Alpha", "codex", 0),
        ("Beta", "claude", 1 if quality_failure else 0),
    ):
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
    server = PlannerServer(project.workspace)
    try:
        assert not result(server, "harness_advance", to_phase="publish")["isError"]
        published = result(
            server, "harness_publish", proof_level="prospective", reason="SOURCE-ONLY bookkeeping"
        )
        assert not published["isError"], published
        before = list(project.connection.iterdump())
        status = result(server, "harness_plan_status")
        assert status["structuredContent"]["publication"]["up_to_date"] is True
        assert (
            status["structuredContent"]["continuation"]["workerQuality"]["blocked"]
            is quality_failure
        )
        for reply in (published, status):
            advice = next_line(reply)
            if quality_failure:
                assert "harness_review" in advice and "harness_roundtable" in advice
                assert "existing CLI" not in advice
            else:
                assert advice.startswith("harness_plan_resume")
                assert "call_cap" in advice and "native" in advice
                assert not advice.startswith("harness_worker_start")
        assert list(project.connection.iterdump()) == before
        assert not (project.workspace / ".codex-harness/worker-jobs").exists()
    finally:
        server.close()
