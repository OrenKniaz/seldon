"""Explicit public policy declaration through the real writer; no provider or approval."""

import json
import subprocess

import pytest
from tests.python.core.test_planner_refusals import canonical_rows, refusal_total

from codex_harness.core import mcp_planner, roundtable
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
from codex_harness.core.spine import SCHEMA, Spine, save_spine

TOOL = "harness_adopt_review_policy"
SLOTS = [
    dict(name="Required", provider="codex", required=True),
    dict(name="Optional", provider="claude", required=False),
]


def wire(server, tool_name, **arguments):
    return server.handle_line(
        json.dumps(
            dict(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params=dict(name=tool_name, arguments=arguments),
            )
        )
    )["result"]


@pytest.fixture
def project(tmp_path, monkeypatch):
    template = tmp_path / "template"
    (template / "tests").mkdir(parents=True)
    (template / "tests/test_source.py").write_text(
        "# Protected policy-fixture input; never executed.\n"
    )
    nodes = [
        dict(id=key, kind=kind, title=key, text=key, parents=parents, fields=fields)
        for key, kind, parents, fields in [
            (
                "V1",
                "vision",
                [],
                {
                    "validation": ["-m", "unittest", "discover", "-s", "tests", "-p"],
                    "requirementFiles": ["tests"],
                },
            ),
            ("F", "feature", ["V1"], {}),
            ("R", "requirement", ["F"], {}),
            (
                "B",
                "bead",
                ["R"],
                {
                    "ownedFiles": ["source.py"],
                    "dependsOn": [],
                    "lessons": [],
                    "proofModule": "test_source.py",
                },
            ),
            ("T", "test", ["R"], {"module": "test_source.py", "level": "unit"}),
        ]
    ]
    source = tmp_path / "spine.json"
    save_spine(Spine(dict(schema=SCHEMA, name="Policy source fixture", nodes=nodes)), source)
    with open_plan_store(tmp_path) as store:
        store.import_spine(source)
        revision = store.compile_template(template)["planRevision"]

    def no_provider(*args, **kwargs):
        raise AssertionError("Policy adoption must never launch a process")

    monkeypatch.setattr(subprocess, "Popen", no_provider)
    return tmp_path, revision


def test_explicit_adoption_reaches_canonical_writer_and_reopened_native_consumers(project):
    """Catch missing native producer/lost declaration; direct writer tests miss wire and reopen."""
    workspace, revision = project
    server = PlannerServer(workspace)
    try:
        result = wire(
            server,
            TOOL,
            expected_workspace=str(workspace),
            expectedManifestRevision=revision,
            slots=SLOTS,
        )
        assert not result["isError"], result
        declaration = result["structuredContent"]["declaration"]
        assert declaration == dict(
            schema="seldon/plan-review-policy-v1",
            stage="plan",
            manifestRevision=revision,
            slots=sorted(SLOTS, key=lambda s: s["name"]),
        )
        assert "no review" in result["content"][0]["text"].lower()
        schema = wire(server, "harness_schema", tool=TOOL)["structuredContent"]["inputSchema"]
        assert set(schema["required"]) == {
            "slots",
            "expectedManifestRevision",
            "expected_workspace",
        }
        assert set(schema["properties"]) == set(schema["required"])
        assert set(schema["properties"]["slots"]["items"]["required"]) == {
            "name",
            "provider",
            "required",
        }
    finally:
        server.close()
    with open_plan_store_readonly(workspace) as saved:
        assert saved._plan_review_policies()[-1]["payload"] == declaration
        before = list(saved.connection.iterdump())
        assert saved.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
        assert saved.connection.execute("SELECT count(*) FROM review_runs").fetchone()[0] == 0
    resumed = PlannerServer(workspace)
    try:
        inspection = wire(resumed, "harness_review_policy", expected_workspace=str(workspace))
        assert not inspection["isError"]
        contract = inspection["structuredContent"]
        assert contract["example"]["slots"] == declaration["slots"]
        assert TOOL in contract["adoption"] and TOOL in inspection["content"][0]["text"]
        status = wire(resumed, "harness_plan_status", expected_workspace=str(workspace))
        policy = status["structuredContent"]["reviewPolicy"]
        assert policy == result["structuredContent"]["reviewPolicy"]
        assert policy["declaredRevision"] == revision and policy["policyEventId"]
        assert policy["active"] and not policy["eligible"]
        assert policy["requiredFailures"] == ["Required"]
        assert policy["successfulReviewIds"] == []
        assert status["structuredContent"]["phase"] == "align"
        assert not status["structuredContent"]["approval"]["granted"]
        assert resumed.store is None  # Existing inspection/status readers remain read-only.
    finally:
        resumed.close()
    with open_plan_store_readonly(workspace) as saved:
        assert list(saved.connection.iterdump()) == before


@pytest.mark.parametrize("binding", ["missing", "foreign"])
def test_adoption_workspace_refusal_precedes_store_or_process(tmp_path, monkeypatch, binding):
    """Catch guard omission on the new mutator; stored policy tests cannot prove cold refusal."""

    def forbidden(*args, **kwargs):
        raise AssertionError("No store/process access before independently supplied workspace")

    monkeypatch.setattr(mcp_planner, "open_plan_store", forbidden)
    monkeypatch.setattr(mcp_planner, "open_plan_store_readonly", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    workspace = tmp_path / "missing"
    arguments = dict(slots=SLOTS, expectedManifestRevision="not-a-current-revision")
    if binding == "foreign":
        arguments["expected_workspace"] = str(tmp_path / "other")
    server = PlannerServer(workspace)
    try:
        result = wire(server, TOOL, **arguments)
        assert result["isError"] and "workspace-" in result["content"][0]["text"]
        assert server.store is None and not workspace.exists()
    finally:
        server.close()


@pytest.mark.parametrize("case", ["stale", "empty", "all-optional", "role-edit", "attempt-exists"])
def test_adoption_preserves_canonical_refusals_and_policy_free_history(project, case):
    """Catch policy/history changes on refusal; its audit append is the sole allowed delta."""
    workspace, revision = project
    slots = [dict(slot) for slot in SLOTS]
    expected = {
        "stale": "review-policy-revision-stale",
        "empty": "at least 1",
        "all-optional": "review-policy-required-slot-missing",
        "role-edit": "review-policy-fixed",
        "attempt-exists": "review-policy-attempts-exist",
    }[case]
    with open_plan_store(workspace) as store:
        assert store.plan_review_policy_state(revision) == dict(
            active=False, mode="legacy", eligible=None
        )
        if case == "stale":
            node = store.spine().node("R")
            node["text"] += " changed canonical requirement"
            store.upsert_node(node)
            assert store.compile_template(workspace / "template")["planRevision"] != revision
        elif case == "empty":
            slots = []
        elif case == "all-optional":
            slots = [dict(slot, required=False) for slot in slots]
        elif case == "role-edit":
            store.record_plan_review_policy(slots, revision)
            slots = [dict(slot, required=not slot["required"]) for slot in slots]
        elif case == "attempt-exists":
            reviewed = roundtable.run_roundtable(
                store,
                reviewers=[
                    dict(name="Required", provider="codex", model="gpt-6.1-sol", effort="medium")
                ],
                executable_for=lambda provider: provider,
                runner=lambda *args: (7, "Source-only failed review; no provider executed"),
            )
            assert roundtable.review_totals(store, revision)[0]["total"] == 1
            assert store.plan_review_policy_state(revision) == dict(
                active=False, mode="legacy", eligible=None
            )
            assert not store._plan_review_policies()
            assert store.review(reviewed["roundId"])["reviewers"][0]["isMocked"]
        before, refusal_count = canonical_rows(store), refusal_total(store)
    server = PlannerServer(workspace)
    try:
        refused = wire(
            server,
            TOOL,
            expected_workspace=str(workspace),
            expectedManifestRevision=revision,
            slots=slots,
        )
        assert refused["isError"] and expected in refused["content"][0]["text"], refused
    finally:
        server.close()
    with open_plan_store_readonly(workspace) as saved:
        assert canonical_rows(saved) == before
        assert refusal_total(saved) == refusal_count + 1


@pytest.mark.parametrize("case", ["stale-policy", "missing-validation"])
def test_policy_inspection_keeps_compilation_before_explicit_adoption(project, case):
    """Catch stale guidance offering mutation before prerequisites; ordinary adoption is current."""
    workspace, revision = project
    with open_plan_store(workspace) as store:
        store.record_plan_review_policy(SLOTS, revision)
        node = store.spine().node("R" if case == "stale-policy" else "V1")
        if case == "stale-policy":
            node["text"] += " uncompiled change"
        else:
            del node["fields"]["validation"]
        store.upsert_node(node)
        before = list(store.connection.iterdump())
    server = PlannerServer(workspace)
    try:
        result = wire(server, "harness_review_policy", expected_workspace=str(workspace))
        assert not result["isError"]
        advice = result["content"][0]["text"].rsplit("\nNext: ", 1)[1]
        assert "harness_compile" in advice and TOOL not in advice
        assert server.store is None
    finally:
        server.close()
    with open_plan_store_readonly(workspace) as saved:
        assert list(saved.connection.iterdump()) == before
