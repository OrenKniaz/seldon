"""Public review start -> real canonical writer -> receipt reader, source-only."""

import json

from tests.python.core import test_roundtable

from codex_harness.core import roundtable
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PlanStore

store = test_roundtable.store


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


def test_public_review_start_preserves_explicit_tuple_and_mocked_receipt(store, monkeypatch):
    """Catch a missing public route, defaulted model, or fake access after adapter launch.

    The existing CLI/lower writer tests miss wire discovery, guard, input schema,
    canonical production and readback through the public recorded-review tool.
    """
    canonical = roundtable.run_roundtable
    selections = []

    def source_only_provider(store, **arguments):
        selections.append(arguments["reviewers"])
        return canonical(
            store,
            **arguments,
            runner=lambda *_: (0, "[]"),
            executable_for=lambda provider: provider,
        )

    monkeypatch.setattr(roundtable, "run_roundtable", source_only_provider)
    reviewer = dict(name="source-only", provider="codex", model="gpt-6.1-sol", effort="medium")
    server = PlannerServer(store.workspace)
    try:
        guide = invoke(server, "harness_schema", tool="harness_roundtable")
        schema = guide["structuredContent"]["inputSchema"]
        assert schema["properties"]["reviewers"]["items"]["required"] == [
            "name",
            "provider",
            "model",
            "effort",
        ]
        before = list(store.connection.iterdump())
        wrong = invoke(
            server,
            "harness_roundtable",
            expected_workspace=str(store.workspace / "wrong-project"),
            reviewers=[reviewer],
        )
        assert wrong["isError"] and wrong["workspaceBinding"]["match"] == "mismatch"
        malformed = invoke(
            server, "harness_roundtable", reviewers=[dict(name="x", provider="codex")]
        )
        assert malformed["isError"] and selections == []
        assert list(store.connection.iterdump()) == before
        started = invoke(server, "harness_roundtable", reviewers=[reviewer], max_rounds=1)
        assert not started["isError"], started
        result = started["structuredContent"]
        assert selections == [[reviewer]]
        assert result["isMocked"] is True and result["didRunProvider"] is False
        recorded = invoke(server, "harness_review", id=result["roundId"])
        assert not recorded["isError"]
        receipt = recorded["structuredContent"]["reviewers"][0]
        shard = receipt["shardDetails"][0]
        assert {key: shard["launchSelection"][key] for key in ("provider", "model", "effort")} == {
            key: reviewer[key] for key in ("provider", "model", "effort")
        }
        assert shard["accessObservation"]["outcome"] == "unknown"
        assert shard["accessObservation"]["isMocked"] is True
        assert not store.successful_reviews(store.manifest_revision())
    finally:
        server.close()
    with PlanStore(store.workspace) as reopened:
        saved = reopened.review(result["roundId"])["reviewers"][0]
        assert saved["shardDetails"] == receipt["shardDetails"]
