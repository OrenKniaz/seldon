"""Synthetic provider-protocol regressions; no native provider execution credit."""

import json

import pytest
from tests.python.core.test_roundtable import store as roundtable_store

from codex_harness.core import roundtable

store = roundtable_store


@pytest.fixture(autouse=True)
def forbid_native_launch(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Protocol tests must never launch a native provider")

    monkeypatch.setattr(roundtable, "_run", forbidden)


@pytest.mark.parametrize(
    "prose",
    [
        "The review is complete; see the written report.",
        "The report discusses context window limits and prompt is too long errors.",
    ],
)
def test_success_telemetry_and_final_prose_are_invalid_without_capacity_retry(store, prose):
    # Minimal synthetic shape derived from successful Claude JSONL. The earlier
    # valid array must not be searched when the final result violates protocol.
    output = "\n".join(
        map(
            json.dumps,
            [
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "[]"}]}},
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": prose,
                    "modelUsage": {"synthetic-model": {"contextWindow": 1000000}},
                },
            ],
        )
    )
    calls = []

    def runner(launch, cwd, timeout):
        calls.append(launch.stdin_text)
        assert len(calls) == 1, "Invalid successful prose must not trigger a capacity retry"
        return 0, output, ""

    result = roundtable.run_roundtable(
        store,
        reviewers=[{"name": "synthetic", "provider": "claude"}],
        runner=runner,
        max_rounds=1,
        max_total=1,
    )
    record = result["reviewers"][0]
    assert len(calls) == 1
    assert result["invalid"] == 1 and result["findings"] == 0
    assert "reshardedFrom" not in record
    error = record["shardDetails"][0]["primaryError"]
    assert error["promptTooLong"] is False
    assert "No JSON finding array in final review text" in error["reason"]
    assert not store.successful_reviews(result["planRevision"])
    assert record["isMocked"] is True and record["didRunProvider"] is False
