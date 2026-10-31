"""Refusal recovery: old tests check gates, not a complete bounded explanation."""

import json

import pytest
from tests.python.core.test_semantic_wiring import semantic_plan as _semantic_plan

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.planner_connection import connection_reply
from codex_harness.core.planner_refusals import MESSAGES, TEACHING, refusal_text
from codex_harness.core.project_init import guide
from codex_harness.core.provider_call_budget import planning_review_budget

semantic_plan = _semantic_plan


def canonical_rows(store):
    """Keep all planning rows and prior events; refusal telemetry has its own assertions."""
    return {
        name: list(
            store.connection.execute(
                f'SELECT * FROM "{name}"'
                + (" WHERE kind != 'planner-refusal'" if name == "events" else "")
            )
        )
        for (name,) in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'"
        )
    }


def refusal_total(store):
    return store.connection.execute(
        "SELECT count(*) FROM events WHERE kind='planner-refusal'"
    ).fetchone()[0]


@pytest.mark.parametrize("code", MESSAGES)
def test_each_mapped_refusal_explains_rule_values_and_one_action(code, tmp_path):
    """Catch a code mapped to opaque prose or multiple actions; gate tests miss wording."""
    text = refusal_text(
        code + ": refused\nNext: obsolete recovery",
        tmp_path,
        {"maxStarts": 4, "spentAdmissions": 2, "phase": "plan"},
    )
    what, why, action = MESSAGES[code]
    assert "What happened: " + what in text
    assert "Why: " + why in text
    assert '"maxStarts": 4' in text and '"spentAdmissions": 2' in text
    assert text.count("\nNext: ") == 1
    assert text.splitlines()[-1].startswith("Next: " + (action or "harness_plan_status"))
    assert "obsolete recovery" not in text
    assert refusal_text(text, tmp_path, {}) == text


def test_unknown_error_has_supported_status_action(tmp_path):
    """Catch new codes inviting implementation discovery; known-code cases cannot."""
    text = refusal_text("future-error: unavailable\nNext: harness_compile", tmp_path, {})
    assert all(part in text for part in ("What happened:", "Why:", "Current values:"))
    assert "unknown" in text
    assert text.splitlines()[-1] == (
        "Next: harness_plan_status expected_workspace="
        + json.dumps(str(tmp_path))
        + (
            "; read-only; inspect the reported blocker for the supported next step. Do "
            "not repeat an uncertain mutation; do not inspect Seldon's implementation."
        )
    )


def test_real_store_refusal_and_start_and_init_teach_supported_recovery(tmp_path):
    """Catch missed reply boundaries; helper cases never traverse canonical PlanStore."""
    server = PlannerServer(tmp_path)
    try:

        def call(tool_name, **arguments):
            return server.handle_line(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": tool_name,
                            "arguments": {
                                "expected_workspace": str(tmp_path),
                                **arguments,
                            },
                        },
                    }
                )
            )["result"]

        started = call("harness_plan_start", idea="Count words", name="Counter")
        assert not started["isError"]
        assert TEACHING in started["content"][0]["text"]
        assert TEACHING in guide(tmp_path, "Counter")
        before = canonical_rows(server.store)
        refused = call("harness_advance", to_phase="expand")
        assert refused["isError"]
        text = refused["content"][0]["text"]
        assert all(part in text for part in ("What happened:", "Why:", "Current values:"))
        assert '"status.phase": "align"' in text
        assert text.splitlines()[-1].startswith("Next:")
        assert text.count("\nNext:") == 1
        assert canonical_rows(server.store) == before
    finally:
        server.close()


def test_refusal_values_come_from_cumulative_b_after_real_launch(semantic_plan, monkeypatch):
    """Catch invented/reset recovery values; static formatter cases never spend from B."""
    from tests.python.core.test_planner_chat_confirmations import (
        approve_b,
        prepare_combined_b,
        review,
    )

    from codex_harness.core import roundtable

    store, _ = semantic_plan
    request, _ = prepare_combined_b(store, cap=1)
    approve_b(store, request)
    reviewer = request["subject"]["reviewers"][0]
    monkeypatch.setattr(roundtable, "_run", lambda *_: (0, "[]"))
    monkeypatch.setattr(roundtable, "_executable", lambda *_: "source-only")
    review(store, reviewer)
    server = PlannerServer(store.workspace)
    try:
        result = server.call_tool("harness_roundtable", {"reviewers": [reviewer]})
        assert result["isError"] and result["refusalCode"] == "plan-b-consent-required"
        text = result["content"][0]["text"]
        assert '"budget.maxStarts": 1' in text and '"budget.spentAdmissions": 1' in text
        assert "field diff" in text.splitlines()[-1]
        assert planning_review_budget(store) == dict(maxStarts=1, spentAdmissions=1)
    finally:
        server.close()


def test_failed_dispatch_inside_completed_job_explains_hold_without_changing_status(tmp_path):
    """Catch UX-2's completed/Git128 blind spot; error-flag tests cannot see nested failure."""
    data = {
        "status": "completed",
        "liveness": "unknown",
        "providerCallCap": 4,
        "terminal": {"dispatchStatus": "plan-failed: git add returned non-zero exit status 128"},
    }
    reply = connection_reply(
        {
            "isError": False,
            "structuredContent": data,
            "content": [{"type": "text", "text": "Worker job completed\nNext: old poll"}],
        },
        tmp_path,
    )
    assert reply["isError"] is False and reply["structuredContent"] == data
    text = reply["content"][0]["text"]
    assert all(part in text for part in ("What happened:", "Why:", "Current values:"))
    assert "128" in text and '"data.liveness": "unknown"' in text
    assert text.count("\nNext:") == 1
    assert text.splitlines()[-1].startswith("Next: harness_plan_status expected_workspace=")
