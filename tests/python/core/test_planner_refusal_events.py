"""Public refusals become durable learning data, not repeated formatter side effects."""

import json
from datetime import datetime

import pytest
from tests.python.core.planning_support import source_only_expansion
from tests.python.core.test_planner_refusals import canonical_rows

from codex_harness.core.mcp_planner import PlannerServer


def wire(server, tool_name, **arguments):
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 17,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {
                        "expected_workspace": str(server.workspace),
                        **arguments,
                    },
                },
            }
        )
    )["result"]


def events(server):
    return [
        (at, json.loads(payload))
        for at, payload in server.store.connection.execute(
            "SELECT at,payload_json FROM events WHERE kind='planner-refusal' ORDER BY id"
        )
    ]


def test_public_refusal_occurrences_persist_once_and_status_counts_survive_reopen(tmp_path):
    """
    Catch duplicate wire formatting, revision bumps and non-durable counters; old tests
    cover prose only.
    """
    server = PlannerServer(tmp_path)
    try:
        assert not wire(server, "harness_plan_start", idea="Count words", name="Counter")["isError"]
        source_only_expansion(server.store)
        assert not wire(server, "harness_prepare_chat_confirmation", gate="align")["isError"]
        assert events(server) == []
        before, revision = canonical_rows(server.store), server.store._revision()
        for count in (1, 2):
            result = wire(server, "harness_advance", to_phase="expand")
            assert result["isError"] and result["structuredContent"]["refusalRecording"] == {
                "state": "recorded"
            }
            assert len(events(server)) == count
        assert canonical_rows(server.store) == before
        for at, payload in events(server):
            assert datetime.fromisoformat(at).utcoffset().total_seconds() == 0
            assert payload == {
                "code": "plan-a-consent-required",
                "phase": "align",
                "tool": "harness_advance",
                "plan_revision": revision,
            }
        server.close()
        server = PlannerServer(tmp_path)
        for _ in range(2):
            status = wire(server, "harness_plan_status")
            assert not status["isError"]
            assert status["structuredContent"]["refusals_by_code"] == {"plan-a-consent-required": 2}
        (tmp_path / "other").mkdir()
        other = PlannerServer(tmp_path / "other")
        try:
            wire(other, "harness_plan_start", idea="Other", name="Other")
            assert wire(other, "harness_plan_status")["structuredContent"]["refusals_by_code"] == {}
        finally:
            other.close()
    finally:
        server.close()


def test_invalid_tool_arguments_cannot_leak_into_refusal_events(tmp_path):
    """
    Catch logging raw errors/arguments or parsing a caller string as a code; prose tests
    miss persistence.
    """
    server = PlannerServer(tmp_path)
    try:
        wire(server, "harness_plan_start", idea="Count", name="Counter")
        secret = "PRIVATE planning-review-budget-immutable-scope"
        result = wire(server, secret, password=secret)
        assert result["isError"]
        payload = events(server)[0][1]
        assert set(payload) == {"code", "phase", "tool", "plan_revision"}
        assert (
            payload["code"] == "tool-input-or-operation-error" and payload["tool"] == "unknown-tool"
        )
        assert "PRIVATE" not in json.dumps(payload)
    finally:
        server.close()


def test_unbound_or_missing_store_refusals_report_unavailable_without_creating_a_store(tmp_path):
    """
    Catch learning writes bypassing pre-admission confinement; normal refusal tests
    already have a store.
    """
    project = tmp_path / "missing"
    server = PlannerServer(project)
    try:
        for arguments in ({}, {"expected_workspace": str(tmp_path / "other")}):
            result = wire(server, "harness_advance", to_phase="expand", **arguments)
            assert result["isError"]
            assert result["structuredContent"]["refusalRecording"] == {"state": "unavailable"}
            assert not project.exists()
    finally:
        server.close()


@pytest.mark.parametrize("action", ["decline", "timeout"])
def test_native_confirmation_refusal_and_expiry_each_record_one_event(
    tmp_path, monkeypatch, action
):
    """
    Catch bypassing call_tool or double-counting expiry notifications; ordinary tool
    errors miss both seams.
    """
    from codex_harness.core import mcp_planner

    server = PlannerServer(tmp_path)
    try:
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"capabilities": {"elicitation": {}}},
                }
            )
        )
        server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        wire(server, "harness_plan_start", idea="Count", name="Counter", plan_mode="legacy")
        wire(
            server,
            "harness_understanding_card",
            vision="Count",
            users="Reader",
            first_user_moment="See count",
            scope_in="Text",
            scope_out="Network",
            assumptions=[],
        )
        source_only_expansion(server.store)
        offered = server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "harness_record_confirmation",
                        "arguments": {
                            "expected_workspace": str(tmp_path),
                            "gate": "align",
                        },
                    },
                }
            )
        )
        assert offered["method"] == "elicitation/create" and events(server) == []
        if action == "decline":
            server.handle_line(
                json.dumps({"jsonrpc": "2.0", "id": offered["id"], "result": {"action": "decline"}})
            )
        else:
            deadline = server._pending_confirmation["deadline"]
            monkeypatch.setattr(mcp_planner.time, "monotonic", lambda: deadline + 1)
            assert len(server.expire_confirmation()) == 2
        assert len(events(server)) == 1
        assert events(server)[0][1]["tool"] == "harness_record_confirmation"
    finally:
        server.close()
