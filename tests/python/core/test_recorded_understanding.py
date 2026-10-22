"""The recorded excerpt is public intent, not arbitrary store metadata or approval."""

import json

import pytest
from tests.python.core.test_cockpit_server import _adapter
from tests.python.core.test_mcp_planner import answer_native, begin_native, call

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.status_api import snapshot


@pytest.mark.parametrize("done_key", ["done_feels_like", "doneFeelsLike", "done", "both"])
def test_public_understanding_aliases_reach_shipped_consumer_without_metadata(tmp_path, done_key):
    server = PlannerServer(tmp_path)
    words = "Complete accepted Done words, including every specified edge case. " * 4
    vision = dict(
        id="V1",
        kind="vision",
        title="Recorded public intent",
        text="Track habits locally",
        parents=[],
        fields={
            "done_feels_like" if done_key == "both" else done_key: words,
            "done_means": "Additional accepted detail.",
            "users": False,
            "internalMetadata": {"private": "never-export-this"},
        },
    )
    if done_key == "both":
        vision["fields"]["doneFeelsLike"] = "Retained secondary alias words."
    try:
        call(server, "harness_plan_start", idea="Track habits locally", name="Fixture")
        call(server, "harness_upsert_node", node=vision)
        request = begin_native(server)
        report = snapshot(workspace=tmp_path)
        excerpt = _adapter(report)["needs"][0]["recordedUnderstanding"]
        assert excerpt is not None
        sections = {item["label"]: item["text"] for item in excerpt["sections"]}
        expected = {
            "Vision": vision["text"],
            "Users": "No",
            "Done": words,
            "Done means": "Additional accepted detail.",
        }
        if done_key == "both":
            expected["Done feels like"] = "Retained secondary alias words."
        assert sections == expected
        assert "never-export-this" not in json.dumps(excerpt)
        # The full canonical node retains every original word/field.
        assert server.store.spine().node("V1") == vision
        assert report["plan"]["approval"]["granted"] is False
        answer_native(server, request["id"], action="cancel")
        assert not _adapter(snapshot(workspace=tmp_path))["needs"][0].get("recordedUnderstanding")
    finally:
        server.close()
