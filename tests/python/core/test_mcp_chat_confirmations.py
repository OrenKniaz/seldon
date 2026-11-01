"""Public caller-mediated decisions; synthetic external replies never prove human identity."""

import hashlib

import pytest
from tests.python.core import test_planner_replay_gaps, test_semantic_wiring
from tests.python.core.test_mcp_planner import (
    answer_native,
    begin_native,
    start,
    workspace_wire,
)

from codex_harness.core.mcp_planner import PlannerServer

project = test_planner_replay_gaps.project
semantic_plan = test_semantic_wiring.semantic_plan


def tool(server, name, **arguments):
    return workspace_wire(server, name, expected_workspace=str(server.workspace), **arguments)


def record(server, question, quote="Approve", *, apply_budget=True):
    """A separate typed source-only external-message fixture, not an observed human reply."""
    result = tool(
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
    return result


def test_run22_vision_edit_after_a_requires_fresh_vision_reply(semantic_plan):
    """Protect displayed intent: catch an early anchor crediting changed vision wording.

    Prior stale-reply tests edit before answering; run 22 needs content invalidation after A
    but before leaving Vision Lock, followed by supported fresh approval recovery.
    """
    from tests.python.core.planning_support import source_only_expansion

    store, _ = semantic_plan
    source_only_expansion(store)
    server = PlannerServer(store.workspace)
    try:
        a = tool(server, "harness_prepare_chat_confirmation", gate="align+vision-lock")[
            "structuredContent"
        ]
        assert record(server, a)["structuredContent"]["state"] == "approved"
        for phase in ("expand", "vision-lock"):
            assert not tool(server, "harness_advance", to_phase=phase)["isError"]
        vision = store.spine().node("V1")
        vision["text"] += " Changed user-visible promise after A."
        refused = tool(server, "harness_upsert_node", node=vision)
        assert refused["isError"] and "vision-locked-wording" in str(refused)
        assert not tool(server, "harness_upsert_node", node=vision, reopen_vision_lock=True)[
            "isError"
        ]
        refused = tool(server, "harness_advance", to_phase="features")
        assert refused["isError"] and refused["refusalCode"] == "plan-a-consent-required"
        assert store.phase() == "vision-lock" and not store.status()["approval"]["granted"]
        fresh = tool(
            server, "harness_prepare_chat_confirmation", gate="vision-lock", include_detail=True
        )["structuredContent"]
        assert fresh["subject"] != a["subject"]
        assert record(server, fresh)["structuredContent"]["state"] == "approved"
        assert not tool(server, "harness_advance", to_phase="features")["isError"]
    finally:
        server.close()


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
        assert question["choices"] == ["Yes", "Approve", "No", "Cancel", "Changes: <text>"]
        assert not server.store.status()["approval"]["granted"]
        completed = record(server, question, "Approve\n")
        assert not completed["isError"], completed
        outcome = completed["structuredContent"]
        assert outcome["provenance"] == "chat-confirmation"
        assert not outcome["chat"]["source_verified"] and not outcome["human_identity_verified"]
        status = tool(server, "harness_plan_status")["structuredContent"]
        assert status["approval"]["origin"] == "chat-confirmation"
        assert "caller-mediated" in tool(server, "harness_plan_status")["content"][0]["text"]
        advanced = tool(server, "harness_advance", to_phase="expand")
        assert not advanced["isError"], advanced
        server.close()
        server = PlannerServer(tmp_path)
        assert tool(server, "harness_plan_status")["structuredContent"]["phase"] == "expand"
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
                statement="Approve",
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
                statement="Approve",
                conditions="",
            )
            assert result["result"]["structuredContent"]["state"] == "approved"
        assert not tool(current, "harness_advance", to_phase="expand")["isError"]
    finally:
        old.close()
        current.close()
