"""Public review start -> real canonical writer -> receipt reader, source-only."""

import json

import pytest
from tests.python.core import test_roundtable
from tests.python.core.test_planner_refusals import canonical_rows, refusal_total

from codex_harness.core import roundtable
from codex_harness.core.mcp_planner import PlannerServer

store = test_roundtable.store


@pytest.mark.parametrize("timeout_literal", ["-1", "0", "1e999"])
def test_public_review_timeout_refuses_before_canonical_reservation(
    store, monkeypatch, timeout_literal
):
    """Catch invalid timeouts leaving an admitted attempt without a result receipt.

    Existing launch-failure tests admit a valid operation first; they miss numeric
    overflow at public JSON decoding and refusal before canonical reservations.
    """
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("admission-or-process")
        raise AssertionError("Invalid timeout must refuse before admission or a process")

    monkeypatch.setattr(roundtable, "_run", forbidden)
    monkeypatch.setattr(roundtable.review_budget, "claim_planning_review_start", forbidden)
    server = PlannerServer(store.workspace)
    before, refusals = canonical_rows(store), refusal_total(store)
    request = dict(
        jsonrpc="2.0",
        id=1,
        method="tools/call",
        params=dict(
            name="harness_roundtable",
            arguments=dict(
                expected_workspace=str(store.workspace),
                reviewers=[
                    dict(name="test", provider="codex", model="gpt-6.1-sol", effort="medium")
                ],
                timeout="TIMEOUT_LITERAL",
            ),
        ),
    )
    try:
        response = server.handle_line(
            json.dumps(request).replace('"TIMEOUT_LITERAL"', timeout_literal)
        )["result"]
        assert response["isError"]
        assert "timeout must be a finite positive number" in response["content"][0]["text"]
        assert calls == []
        assert canonical_rows(store) == before
        assert refusal_total(store) == refusals + 1
    finally:
        server.close()


def invoke(server, tool_name, **arguments):
    return server.handle_line(
        json.dumps(
            dict(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params=dict(
                    name=tool_name,
                    arguments={"expected_workspace": str(server.workspace), **arguments},
                ),
            )
        )
    )["result"]
