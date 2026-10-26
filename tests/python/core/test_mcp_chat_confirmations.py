"""Public caller-mediated decisions; synthetic external replies never prove human identity."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.test_mcp_planner import (
    answer_native,
    begin_native,
    rpc,
    start,
    workspace_wire,
)
from tests.python.core.test_planner_resume import _published

from codex_harness.core import planner_jobs
from codex_harness.core.mcp_planner import PlannerServer

project = test_planner_replay_gaps.project


def tool(server, name, **arguments):
    return workspace_wire(server, name, expected_workspace=str(server.workspace), **arguments)


def record(server, question, quote="Approve"):
    """A separate typed source-only external-message fixture, not an observed human reply."""
    return tool(
        server,
        "harness_record_chat_confirmation",
        gate=question.get("gate", "execution"),
        request_id=question["request_id"],
        expected_revision=question.get(
            "plan_revision", question.get("scope", {}).get("publicationRevision")
        ),
        quoted_message=quote,
        quote_sha256=hashlib.sha256(quote.encode()).hexdigest(),
        actor_label="source-only external tester; identity and source unverified",
    )


@pytest.mark.parametrize("native", ["decline", "unavailable"])
def test_public_native_failure_offers_fresh_chat_then_advances(tmp_path, native):
    """Catch missing public fallback/status lies; leaf-only tests bypass the wire and view."""
    server = PlannerServer(tmp_path)
    try:
        start(server)
        if native == "decline":
            prepared = begin_native(server, gate="align")
            original = server._pending_confirmation["request"]["request_id"]
            response = answer_native(server, prepared["id"], action="decline")["result"]
            assert response["structuredContent"]["state"] == "decline"
        else:
            original = None
            response = tool(server, "harness_record_confirmation", gate="align")
        assert response["isError"]
        question = response["structuredContent"]["chatConfirmation"]
        assert question["channel"] == "chat-confirmation" and question["request_id"] != original
        assert question["choices"] == ["Approve", "No", "Cancel", "Changes: <text>"]
        assert not server.store.status()["approval"]["granted"]
        assert "unverified" in question["card"]
        completed = record(server, question, "Approve\n")
        assert not completed["isError"], completed
        outcome = completed["structuredContent"]
        assert outcome["provenance"] == "chat-confirmation"
        assert not outcome["chat"]["source_verified"] and not outcome["human_identity_verified"]
        status = tool(server, "harness_plan_status")["structuredContent"]
        assert status["approval"]["origin"] == "chat-confirmation"
        assert "caller-mediated" in tool(server, "harness_plan_status")["content"][0]["text"]
        # Native retry must not silently supersede an already-valid chat decision.
        reused = tool(server, "harness_record_confirmation", gate="align")
        assert (
            not reused["isError"]
            and reused["structuredContent"]["provenance"] == "chat-confirmation"
        )
        advanced = tool(server, "harness_advance", to_phase="expand")
        assert not advanced["isError"], advanced
        server.close()
        server = PlannerServer(tmp_path)
        assert tool(server, "harness_plan_status")["structuredContent"]["phase"] == "expand"
    finally:
        server.close()


@pytest.mark.parametrize("native", ["decline", "unavailable"])
def test_public_chat_execution_records_then_separate_start_consumes_exact_grant(
    project, monkeypatch, native
):
    """Catch re-prompt superseding chat credit; leaf job tests bypass the native MCP interceptor."""
    options = _published(project)
    proposal = {
        key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")
    }
    server = PlannerServer(project.workspace)
    launches = []
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *args, **kwargs: launches.append((args, kwargs)) or SimpleNamespace(pid=23456),
    )
    try:
        if native == "decline":
            rpc(
                server,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"elicitation": {}},
                },
            )
            server.handle_line(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            )
            pending = rpc(
                server, "tools/call", {"name": "harness_worker_start", "arguments": proposal}
            )
            assert pending["method"] == "elicitation/create"
            response = answer_native(server, pending["id"], action="decline")["result"]
            assert response["structuredContent"]["executionConfirmation"]["state"] == "decline"
        else:
            response = tool(server, "harness_worker_start", **proposal)
        assert response["isError"] and launches == []
        question = response["structuredContent"]["chatConfirmation"]
        assert question["scope"]["publicationRevision"] == proposal["expected_revision"]
        assert question["scope"]["callCap"] == 1
        completed = record(server, question)
        assert not completed["isError"], completed
        assert completed["structuredContent"]["state"] == "approved" and launches == []
        result = tool(server, "harness_worker_start", **proposal)
        assert not result["isError"], result
        job = result["structuredContent"]
        assert len(launches) == 1
        assert job["nativeApproval"] == "unproven"
        assert job["approvalProvenance"] == "chat-confirmation"
        assert job["approvalAssurance"] == "caller-mediated" and not job["actorIdentityVerified"]
        reused = tool(server, "harness_worker_start", **proposal)
        assert not reused["isError"] and len(launches) == 1
        assert reused["structuredContent"]["jobId"] == job["jobId"]
        server.close()
        server = PlannerServer(project.workspace)
        reopened = tool(server, "harness_worker_status", job_id=job["jobId"])
        assert not reopened["isError"], reopened
        assert reopened["structuredContent"]["approvalProvenance"] == "chat-confirmation"
    finally:
        server.close()


@pytest.mark.parametrize(
    "quote,state", [("No", "declined"), ("Cancel", "cancel"), ("Changes: clarify", "conditional")]
)
def test_public_chat_negative_reply_gives_no_credit_and_new_question(tmp_path, quote, state):
    """Catch public No-to-yes or stale-question reuse; native negatives lack the typed chat seam."""
    server = PlannerServer(tmp_path)
    try:
        start(server)
        offered = tool(server, "harness_prepare_chat_confirmation", gate="align")
        assert not offered["isError"], offered
        question = offered["structuredContent"]
        response = record(server, question, quote)
        assert not response["isError"], response
        outcome = response["structuredContent"]
        assert outcome["state"] == state and outcome["approval_id"] is None
        assert outcome["chatConfirmation"]["request_id"] != question["request_id"]
        assert not server.store.status()["approval"]["granted"]
        assert tool(server, "harness_advance", to_phase="expand")["isError"]
        assert record(server, question)["isError"]  # Answer replay never grants approval.
    finally:
        server.close()


@pytest.mark.parametrize("extra", ["approve", "origin", "execution"])
def test_public_chat_record_refuses_conflicting_or_foreign_fields(tmp_path, extra):
    """Catch a widened wire adapter; leaf rejection alone cannot enforce its shape."""
    server = PlannerServer(tmp_path)
    try:
        start(server)
        question = tool(server, "harness_prepare_chat_confirmation", gate="align")[
            "structuredContent"
        ]
        arguments = dict(
            gate="align",
            request_id=question["request_id"],
            expected_revision=question["plan_revision"],
            quoted_message="No",
            quote_sha256=hashlib.sha256(b"No").hexdigest(),
            actor_label="source-only",
        )
        arguments[extra] = True if extra == "approve" else "mcp-client-elicitation"
        refused = tool(server, "harness_record_chat_confirmation", **arguments)
        assert refused["isError"]
        assert not server.store.status()["approval"]["granted"]
    finally:
        server.close()


def test_public_chat_reply_to_changed_plan_remains_stale(tmp_path):
    """Catch a public adapter dropping revision binding; happy replies never change the plan."""
    server = PlannerServer(tmp_path)
    try:
        start(server)
        question = tool(server, "harness_prepare_chat_confirmation", gate="align")[
            "structuredContent"
        ]
        vision = server.store.spine().node("V1")
        vision["text"] += " Changed scope after the displayed question."
        assert not tool(server, "harness_upsert_node", node=vision)["isError"]
        result = record(server, question)
        assert not result["isError"], result  # It records the stale answer without granting.
        assert result["structuredContent"]["state"] == "stale"
        assert not tool(server, "harness_plan_status")["structuredContent"]["approval"]["granted"]
        assert tool(server, "harness_advance", to_phase="expand")["isError"]
    finally:
        server.close()


@pytest.mark.parametrize("native", ["cancel", "timeout"])
def test_public_native_cancel_or_missing_reply_offers_zero_credit_question(tmp_path, native):
    """Catch timeout/cancel granting approval or losing fallback; decline uses another exit."""
    server = PlannerServer(tmp_path)
    try:
        start(server)
        pending = begin_native(server, gate="align")
        original = server._pending_confirmation["request"]["request_id"]
        if native == "timeout":
            server._pending_confirmation["deadline"] = 0
            response = server.expire_confirmation()[-1]["result"]
            assert response["structuredContent"]["state"] == "pending"
        else:
            response = answer_native(server, pending["id"], action="cancel")["result"]
            assert response["structuredContent"]["state"] == "cancel"
        question = response["structuredContent"]["chatConfirmation"]
        assert question["request_id"] != original and question["channel"] == "chat-confirmation"
        assert not server.store.status()["approval"]["granted"]
        assert (
            answer_native(server, pending["id"], approve=True, statement="late", conditions="")
            is None
        )
        assert not server.store.status()["approval"]["granted"]
    finally:
        server.close()


@pytest.mark.parametrize("current_approved", [False, True])
def test_superseded_native_timeout_cannot_replace_current_question_or_credit(
    tmp_path, current_approved
):
    """Catch fallback minted by an old timeout; response-only overlap tests lack missing replies."""
    old, current = PlannerServer(tmp_path), PlannerServer(tmp_path)
    try:
        start(old)
        begin_native(old, gate="align")
        displayed = begin_native(current, gate="align")
        current_id = current._pending_confirmation["request"]["request_id"]
        if current_approved:
            result = answer_native(
                current,
                displayed["id"],
                approve=True,
                statement="SOURCE-ONLY current native client",
                conditions="",
            )
            assert result["result"]["structuredContent"]["state"] == "approved"
        old._pending_confirmation["deadline"] = 0
        timed_out = old.expire_confirmation()[-1]["result"]
        assert timed_out["structuredContent"]["state"] == "pending"
        assert "chatConfirmation" not in timed_out["structuredContent"]
        from codex_harness.core.planner_confirmations import REQUEST, _records

        assert next(row for _, row in _records(old.store, REQUEST))["request_id"] == current_id
        if not current_approved:
            result = answer_native(
                current,
                displayed["id"],
                approve=True,
                statement="SOURCE-ONLY current native client",
                conditions="",
            )
            assert result["result"]["structuredContent"]["state"] == "approved"
        assert not tool(current, "harness_advance", to_phase="expand")["isError"]
    finally:
        old.close()
        current.close()


def test_fallback_creation_rechecks_original_under_the_canonical_writer(tmp_path, monkeypatch):
    """Catch a newer native request between read and fallback write; serial cases miss it."""
    from codex_harness.core import planner_confirmations

    old, current = PlannerServer(tmp_path), PlannerServer(tmp_path)
    prepare = planner_confirmations.prepare_chat_confirmation
    newer = []

    def interleave(store, gate, **kwargs):
        # Schedule another real public producer; the hook supplies no approval or result.
        newer.append(begin_native(current, gate=gate))
        return prepare(store, gate, **kwargs)

    try:
        start(old)
        displayed = begin_native(old, gate="align")
        monkeypatch.setattr(planner_confirmations, "prepare_chat_confirmation", interleave)
        old_reply = answer_native(old, displayed["id"], action="decline")["result"]
        assert old_reply["structuredContent"]["state"] == "decline"
        current_reply = answer_native(
            current,
            newer[0]["id"],
            approve=True,
            statement="SOURCE-ONLY newer native client",
            conditions="",
        )["result"]
        assert current_reply["structuredContent"]["state"] == "approved"
        assert not tool(current, "harness_advance", to_phase="expand")["isError"]
    finally:
        old.close()
        current.close()


def test_execution_fallback_cannot_supersede_interleaved_native_request(project, monkeypatch):
    """Catch execution's writer missing the planning CAS; leaf success lacks an interleave."""
    from codex_harness.core import planner_execution

    options = _published(project)
    proposal = {
        key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")
    }
    old, current = PlannerServer(project.workspace), PlannerServer(project.workspace)
    prepare = planner_execution.prepare_chat_execution
    newer, launches = [], []

    def interleave(store, **kwargs):
        newer.append(
            rpc(current, "tools/call", {"name": "harness_worker_start", "arguments": proposal})
        )
        return prepare(store, **kwargs)

    try:
        for server in (old, current):
            rpc(
                server,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"elicitation": {}},
                },
            )
            server.handle_line(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            )
        displayed = rpc(old, "tools/call", {"name": "harness_worker_start", "arguments": proposal})
        assert displayed["method"] == "elicitation/create"
        monkeypatch.setattr(planner_execution, "prepare_chat_execution", interleave)
        monkeypatch.setattr(
            planner_jobs.subprocess,
            "Popen",
            lambda *args, **kwargs: launches.append(1) or SimpleNamespace(pid=23456),
        )
        response = answer_native(old, displayed["id"], action="decline")["result"]
        assert response["structuredContent"]["executionConfirmation"]["state"] == "decline"
        assert "chatConfirmation" not in response["structuredContent"] and launches == []
        current_reply = answer_native(
            current,
            newer[0]["id"],
            approve=True,
            statement="SOURCE-ONLY current execution decision",
            conditions="",
        )["result"]
        assert not current_reply["isError"], current_reply
        assert len(launches) == 1
        assert current_reply["structuredContent"]["approvalProvenance"] == "mcp-client-elicitation"
    finally:
        old.close()
        current.close()
