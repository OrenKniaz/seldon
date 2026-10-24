"""Primary result versus follow-up status, through real public wire and persistence.

Protect a completed write from being labelled failed solely by status rendering.
Existing batch tests cover valid compilation states; status-failure tests exercise
status as the primary operation, not a committed write followed by observation.
No live providers or product workers are used; the audit fixture runs local proof.
"""

import json
from copy import deepcopy

import pytest
from tests.python.core import test_mcp_audit, test_planner_replay_gaps

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PlanStoreError, open_plan_store_readonly

project = test_planner_replay_gaps.project
prospective = test_mcp_audit.prospective


def wire(server, trace, name, **arguments):
    request = {
        "jsonrpc": "2.0",
        "id": len(trace) + 1,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": {"expected_workspace": str(server.workspace), **arguments},
        },
    }
    response = server.handle_line(json.dumps(request))
    trace.append({"request": request, "response": response})
    (server.workspace / "response-trace.json").write_text(json.dumps(trace, indent=2))
    assert response["id"] == request["id"]
    return response["result"]


def count_node_events(store, node_id):
    return store.connection.execute(
        "SELECT count(*) FROM events WHERE kind='node-upserted' "
        "AND json_extract(payload_json, '$.id')=?",
        (node_id,),
    ).fetchone()[0]


def test_completed_public_batch_survives_status_failure_and_recovers_without_replay(project):
    """Catch lost IDs/false failure after commit; prove reopen, refusal and repair once."""
    server, trace = PlannerServer(project.workspace), []
    node = deepcopy(project.spine().node("TE"))
    node["id"] = "TE2"
    before_events = project.connection.execute("SELECT count(*) FROM events").fetchone()[0]
    try:
        saved = wire(server, trace, "harness_upsert_nodes", nodes=[node])
        assert project.spine().node("TE2") == node
        assert count_node_events(project, "TE2") == 1
        assert (
            project.connection.execute("SELECT count(*) FROM events").fetchone()[0]
            == before_events + 1
        )
        assert saved["isError"] is False, saved
        assert saved["structuredContent"] == {"ids": ["TE2"]}
        assert saved["statusAvailable"] is False
        assert saved["workspaceBinding"]["match"] == "matched"
        assert saved["plannerConnection"]["projectRoot"] == str(project.workspace)
        assert saved["plannerConnection"]["phase"] == "unknown"
        assert saved["plannerConnection"]["planRevision"] == "unknown"
        text = saved["content"][0]["text"]
        assert "Changed node: TE2" in text and "Canonical status unavailable" in text
        assert "completed" in text and "read-only" in text.split("Next:")[-1]
        assert "Do not repeat" in text.split("Next:")[-1]
    finally:
        server.close()
    with open_plan_store_readonly(project.workspace) as reopened:
        assert reopened.spine().node("TE2") == node
        assert count_node_events(reopened, "TE2") == 1
    server = PlannerServer(project.workspace)
    try:
        before = list(project.connection.iterdump())
        status = wire(server, trace, "harness_plan_status")
        assert status["isError"] is True and status["statusAvailable"] is False
        assert list(project.connection.iterdump()) == before
        invalid = deepcopy(node)
        invalid.update(id="not-saved", parents=["missing-parent"])
        rejected = wire(server, trace, "harness_upsert_nodes", nodes=[invalid])
        assert rejected["isError"] is True
        assert "spine-unknown-parent" in rejected["content"][0]["text"]
        assert "structuredContent" not in rejected
        assert list(project.connection.iterdump()) == before
        # A distinct explicit repair changes the saved node, never repeats the original batch.
        node["fields"]["level"] = "unit"
        repaired = wire(server, trace, "harness_upsert_node", node=node)
        assert repaired["isError"] is False and repaired["statusAvailable"] is True
        assert count_node_events(project, "TE2") == 2
        before = list(project.connection.iterdump())
        status = wire(server, trace, "harness_plan_status")
        assert status["isError"] is False and status["statusAvailable"] is True
        assert status["structuredContent"]["phase"] == project.phase()
        assert list(project.connection.iterdump()) == before
        assert (
            sum(item["request"]["params"]["name"] == "harness_upsert_nodes" for item in trace) == 2
        )
    finally:
        server.close()


def test_primary_exception_after_real_write_never_becomes_success(project, monkeypatch):
    """Catch treating result_data/side effects as completion when dispatch raised.

    A real batch writes once before an injected completion failure; ordinary
    rejected-input tests cannot expose this uncertain outcome with retained IDs.
    """
    original = PlannerServer._dispatch

    def completion_lost(self, name, args, **kwargs):
        body = original(self, name, args, **kwargs)
        if name == "harness_upsert_nodes":
            raise OSError("Primary completion unavailable")
        return body

    monkeypatch.setattr(PlannerServer, "_dispatch", completion_lost)
    node = deepcopy(project.spine().node("R1"))
    node["id"] = "R2"
    server = PlannerServer(project.workspace)
    try:
        response = wire(server, [], "harness_upsert_nodes", nodes=[node])
        assert response["isError"] is True and response["statusAvailable"] is True
        assert "Primary completion unavailable" in response["content"][0]["text"]
        assert "read-only" in response["content"][0]["text"].split("Next:")[-1]
        assert "uncertain mutation" in response["content"][0]["text"].split("Next:")[-1]
        assert project.spine().node("R2") == node
        assert count_node_events(project, "R2") == 1
    finally:
        server.close()


@pytest.mark.parametrize("completion_error", ["os", "store"])
def test_single_node_commit_before_result_requires_inspection_but_input_refusal_keeps_help(
    project, monkeypatch, completion_error
):
    """Catch retry advice when canonical commit raises before result_data is set.

    The existing uncertain-batch test sets result_data before its injected error;
    this real single-node producer exercises the missing completion boundary.
    A typed store error must not be assumed to prove a refusal before effects.
    """
    store_type, calls = type(project), []
    original = store_type.upsert_node

    def commit_then_raise(self, node):
        calls.append(node["id"])
        original(self, node)
        if completion_error == "store":
            raise PlanStoreError("plan-export-conflict", "Completion lost before dispatch result")
        raise OSError("Completion lost before dispatch result")

    monkeypatch.setattr(store_type, "upsert_node", commit_then_raise)
    server, trace = PlannerServer(project.workspace), []
    node = deepcopy(project.spine().node("R1"))
    node["id"] = "R2"
    before = list(project.connection.iterdump())
    try:
        refused = wire(server, trace, "harness_upsert_node")  # Missing required input.
        assert refused["isError"] is True and refused["statusAvailable"] is True
        assert "harness_schema" in refused["content"][0]["text"]
        assert "retry this tool" in refused["content"][0]["text"].split("Next:")[-1]
        assert calls == [] and list(project.connection.iterdump()) == before
        response = wire(server, trace, "harness_upsert_node", node=node)
        assert response["isError"] is True and response["statusAvailable"] is True
        assert server.result_data is None and "structuredContent" not in response
        assert calls == ["R2"] and project.spine().node("R2") == node
        assert count_node_events(project, "R2") == 1
        next_step = response["content"][0]["text"].split("Next:")[-1]
        assert "read-only" in next_step and "uncertain mutation" in next_step
        assert "retry this tool" not in next_step
    finally:
        server.close()
    with open_plan_store_readonly(project.workspace) as reopened:
        assert reopened.spine().node("R2") == node
        assert count_node_events(reopened, "R2") == 1
    assert calls == ["R2"]  # Observation and reopen never replay the mutation.


def test_failed_audit_receipt_stays_failed_when_followup_status_is_unavailable(
    prospective, monkeypatch
):
    """Catch promoting a returned audit failure to success after observation fails.

    Existing failed-audit tests have healthy status; use the real syntax audit and
    persisted failure receipt rather than a mocked successful dispatch response.
    """
    server, _ = test_mcp_audit.started(prospective)
    trace = []
    try:
        proof = prospective["template"] / "tests/test_app.py"
        proof.write_text("invalid Python syntax here!\n")
        assert not wire(server, trace, "harness_compile")["isError"]
        from codex_harness.core import test_audit

        engine, executions = test_audit.audit_prospective_tests, []

        def execute(**arguments):
            executions.append(1)
            return engine(**arguments)

        monkeypatch.setattr(test_audit, "audit_prospective_tests", execute)
        admission = wire(server, trace, "harness_audit", mode="prospective")
        assert not admission["isError"]
        attempt = admission["structuredContent"]
        assert not attempt["proofCreditGranted"]
        server.audits.dispatch_after_flush(server.store)
        server.audits.thread.join(timeout=30)
        assert not server.audits.thread.is_alive()
        server.audits.drain(server.store)
        before = tuple(server.store.connection.iterdump())

        def unavailable(self):
            raise OSError("Follow-up status unavailable")

        monkeypatch.setattr(PlannerServer, "_status", unavailable)
        result = wire(server, trace, "harness_audit_status", id=attempt["attemptId"])
        assert result["isError"] is True and result["statusAvailable"] is False
        state = result["structuredContent"]
        assert state["state"] == "completed" and not state["proofCreditGranted"]
        receipt = state["result"]
        assert receipt["ok"] is False
        assert any(item["code"] == "prospective-syntax-error" for item in receipt["findings"])
        row = server.store.connection.execute(
            "SELECT passed FROM audit_runs WHERE id=?", (receipt["id"],)
        ).fetchone()
        assert row[0] == 0
        assert tuple(server.store.connection.iterdump()) == before
        assert executions == [1]  # Follow-up observation failure never reruns validation.
    finally:
        server.close()
