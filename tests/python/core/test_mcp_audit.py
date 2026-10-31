"""Public planning audit through the real engine and persisted gate consumer.

Only disposable source-test stubs run. Native client responses are synthetic,
human identity stays unverified, and no provider or P1 product is launched.
"""

import json
import queue
import subprocess
import sys
import threading
import time

from tests.python.core import test_test_audit
from tests.python.core.planning_support import source_only_b, source_only_expansion
from tests.python.core.test_mcp_planner import native_confirmation_result

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PHASES, open_plan_store_readonly

prospective = test_test_audit.prospective


def test_public_stdio_audit_gives_proof_eof_without_closing_control_input(prospective):
    """Catch proof reading the MCP control pipe; admission tests never read proof stdin."""
    workspace = prospective["template"].parent
    done = workspace / "real-engine-returned"
    (prospective["template"] / "tests/test_app.py").write_text(
        "import sys, unittest\nfrom app import value\nclass Proof(unittest.TestCase):\n"
        "    def setUp(self):\n        self.assertEqual(sys.stdin.read(), '')\n"
        "    def test_value(self):\n        self.assertEqual(value(), 42)\n"
    )
    setup, revision = started(prospective)
    setup.close()
    bootstrap = (
        "import runpy,sys\nfrom pathlib import Path\n"
        "from codex_harness.core import test_audit,runners\n"
        "real_run=test_audit.run_audit_process\n"
        "def bounded(*args,**kwargs):\n"
        " kwargs['timeout']=min(kwargs.get('timeout',120),1)\n"
        " return real_run(*args,**kwargs)\n"
        "test_audit.run_audit_process=runners.run_audit_process=bounded\n"
        "real_engine=test_audit.audit_prospective_tests\n"
        "def observed(**kwargs):\n"
        " try:return real_engine(**kwargs)\n"
        f" finally:Path({str(done)!r}).write_text('returned')\n"
        "test_audit.audit_prospective_tests=observed\n"
        f"sys.argv=['planner','--workspace',{str(workspace)!r}]\n"
        "runpy.run_module('codex_harness.core.mcp_planner',run_name='__main__')\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", bootstrap],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    replies, errors = queue.Queue(), []
    reader = threading.Thread(
        target=lambda: [replies.put(json.loads(line)) for line in process.stdout], daemon=True
    )
    error_reader = threading.Thread(target=lambda: errors.extend(process.stderr), daemon=True)
    reader.start()
    error_reader.start()
    request_id = 0

    def request(method, params=None):
        nonlocal request_id
        request_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
        response = replies.get(timeout=10)
        assert response["id"] == request_id and "error" not in response, response
        return response["result"]

    def tool(name, **arguments):
        return request(
            "tools/call",
            {"name": name, "arguments": {"expected_workspace": str(workspace), **arguments}},
        )["structuredContent"]

    try:
        request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        admitted = tool("harness_audit", mode="prospective")
        attempt_id = admitted["attemptId"]  # Only actual structured identity, never regex text.
        assert not admitted["proofCreditGranted"]
        # This marker records real engine return, not its verdict. Avoid sending
        # more control bytes while the deliberately broken proof may read them.
        deadline = time.monotonic() + 30
        while not done.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert done.exists(), "Real engine did not finish within fixture bounds"
        deadline = time.monotonic() + 10
        while True:
            completed = tool("harness_audit_status", id=attempt_id)
            if completed["state"] not in {"queued", "running"}:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert completed["state"] == "completed", completed
        (workspace / "public-observed.json").write_text(json.dumps(completed), encoding="utf-8")
        assert completed["result"]["ok"], completed["result"]["redBaseline"]
        assert completed["manifestRevision"] == revision and completed["proofCreditGranted"]
        if sys.platform == "win32":
            controls = [item["processControl"] for item in completed["result"]["collection"]]
            controls.extend(item["processControl"] for item in completed["result"]["redBaseline"])
            assert all(
                c["quiescenceVerified"] and c["activeProcessesAfterTeardown"] == 0 for c in controls
            )
        assert not process.stdin.closed and request("ping") == {}
    finally:
        process.stdin.close()
        process.wait(timeout=30)
        reader.join(timeout=2)
        error_reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
        assert process.returncode == 0, "".join(errors)
    with open_plan_store_readonly(workspace) as saved:
        retained = saved.audit_attempt(attempt_id)
        assert retained["result"] == completed["result"]
        assert retained["engineIdentity"] == completed["engineIdentity"]
        assert retained["auditId"] == completed["auditId"]


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


def finished_audit(server, **arguments):
    admission = invoke(server, "harness_audit", **arguments)
    assert not admission["isError"], admission
    attempt = admission["structuredContent"]
    assert not attempt["proofCreditGranted"]
    server.audits.dispatch_after_flush(server.store)
    server.audits.thread.join(timeout=30)
    assert not server.audits.thread.is_alive()
    server.audits.drain(server.store)
    response = invoke(server, "harness_audit_status", id=attempt["attemptId"])
    state = response["structuredContent"]
    assert state["state"] == "completed", state
    return {**response, "structuredContent": state["result"]}


def started(prospective):
    server = PlannerServer(prospective["template"].parent)
    assert not invoke(
        server, "harness_plan_start", plan_mode="legacy", idea="Source-only red stub", name="Audit"
    )["isError"]
    nodes = []
    for original in prospective["spine"].to_dict()["nodes"]:
        node = {**original, "id": "V1" if original["id"] == "V" else original["id"]}
        node["parents"] = ["V1" if key == "V" else key for key in original["parents"]]
        nodes.append(node)
    saved = invoke(server, "harness_upsert_nodes", nodes=nodes)
    assert not saved["isError"], saved
    source_only_expansion(server.store)
    compiled = invoke(server, "harness_compile")
    assert not compiled["isError"], compiled
    while server.store.phase() != "roundtable":
        phase = server.store.phase()
        if phase in ("align", "vision-lock"):
            confirmed = native_confirmation_result(server, phase, "Approve")
            assert not confirmed["isError"]
            assert confirmed["structuredContent"]["human_identity_verified"] is False
        if phase == "plan":
            from tests.python.core.planning_support import source_only_b

            source_only_b(server.store)
            compiled = invoke(server, "harness_compile")
        assert not invoke(server, "harness_advance", to_phase=PHASES[PHASES.index(phase) + 1])[
            "isError"
        ]
    return server, compiled["structuredContent"]["planRevision"]


def test_public_prospective_audit_clears_only_matching_audit_gate(prospective):
    """Catch the missing native route and false or lost prospective audit credit.

    CLI and engine tests miss public discovery/help, workspace dispatch and the
    actual stored audit's consumption by public advance after reopening.
    """
    assert "harness_audit" in {tool["name"] for tool in PlannerServer(".")._wire_tools()}
    server, revision = started(prospective)
    root = server.workspace
    try:
        blocked = invoke(server, "harness_advance", to_phase="publish")
        assert blocked["isError"] and "passing audit" in blocked["content"][0]["text"]
        guide = invoke(server, "harness_schema", tool="harness_audit")
        assert not guide["isError"]
        assert guide["structuredContent"]["inputSchema"]["properties"]["mode"]["enum"] == [
            "prospective"
        ]
        approvals = server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0]
        audited = finished_audit(server, mode="prospective")
        assert not audited["isError"], audited
        result = audited["structuredContent"]
        assert result["ok"] is True and result["proofLevel"] == "prospective"
        assert result["manifestRevision"] == revision and result["warning"]
        assert result["integrationChecked"] is False
        assert result["redBaseline"][0]["passed"] is False
        assert all(item["passed"] for item in result["collection"])
        assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == (
            approvals
        )
    finally:
        server.close()
    with open_plan_store_readonly(root) as saved:
        assert saved.passing_audit(revision)["id"] == result["id"]
        assert saved.proof_level(revision) == "prospective"
        assert not saved.successful_reviews(revision)
    resumed = PlannerServer(root)
    try:
        refused = invoke(resumed, "harness_advance", to_phase="publish")
        assert refused["isError"] and "successful review" in refused["content"][0]["text"]
        assert "Missing passing audit" not in refused["content"][0]["text"]
        assert resumed.store.phase() == "roundtable"
        app = prospective["template"] / "app.py"
        original_app = app.read_bytes()
        app.write_bytes(original_app + b"\n# Raw source changes outside protected manifest\n")
        assert resumed.store.manifest_revision() == revision
        assert resumed.store.passing_audit(revision) is None
        app.write_bytes(original_app)
        assert resumed.store.passing_audit(revision)["id"] == result["id"]
        proof = prospective["template"] / "tests/test_app.py"
        proof.write_text(proof.read_text() + "\n# A changed protected proof revision.\n")
        recompiled = invoke(resumed, "harness_compile")
        changed = recompiled["structuredContent"]["planRevision"]
        assert not recompiled["isError"] and changed != revision
        stale = invoke(resumed, "harness_advance", to_phase="publish")
        assert stale["isError"] and "plan-b-consent-required" in stale["content"][0]["text"]
        source_only_b(resumed.store)
        stale = invoke(resumed, "harness_advance", to_phase="publish")
        assert stale["isError"] and "passing audit" in stale["content"][0]["text"]
        assert not resumed.store.passing_audit(changed)
        assert (
            resumed.store.passing_audit(revision) is None
        )  # History is retained, current inputs differ.
    finally:
        resumed.close()


def test_public_failed_audit_records_findings_without_granting_credit(prospective):
    """Catch an adapter treating engine failure as success or dropping its receipt.

    Existing engine refusals do not prove the public result and persisted failure
    keep the actual advance consumer held without a fabricated passing audit.
    """
    server, _ = started(prospective)
    try:
        proof = prospective["template"] / "tests/test_app.py"
        proof.write_text("invalid Python syntax here!\n")
        compiled = invoke(server, "harness_compile")
        assert not compiled["isError"]
        revision = compiled["structuredContent"]["planRevision"]
        audited = finished_audit(server, mode="prospective")
        assert audited["isError"]
        result = audited["structuredContent"]
        assert result["ok"] is False and result["proofLevel"] == "prospective"
        assert any(item["code"] == "prospective-syntax-error" for item in result["findings"])
        row = server.store.connection.execute(
            "SELECT manifest_revision, passed, result_json FROM audit_runs WHERE id=?",
            (result["id"],),
        ).fetchone()
        assert row[0] == revision and row[1] == 0
        assert json.loads(row[2])["findings"] == result["findings"]
        assert not server.store.passing_audit(revision)
        cached = invoke(server, "harness_audit", mode="prospective")["structuredContent"]
        assert cached["attemptId"] == result["attemptId"] and cached["result"] == result
        assert server.audits.prepared is None
        blocked = invoke(server, "harness_advance", to_phase="publish")
        assert blocked["isError"] and server.store.phase() == "roundtable"
    finally:
        server.close()


def test_unflushed_owner_disconnect_holds_attempt_without_replay(prospective):
    """Protect unknown-owner slot retention; completed-audit tests miss pre-dispatch loss."""
    server, _ = started(prospective)
    admission = invoke(server, "harness_audit", mode="prospective")["structuredContent"]
    assert server.audits.thread is None  # No host flush/dispatch occurred.
    server.close()
    reopened = PlannerServer(prospective["template"].parent)
    try:
        status = invoke(reopened, "harness_audit_status", id=admission["attemptId"])
        data = status["structuredContent"]
        assert data["state"] == "unknown-interrupted" and not data["didRunValidation"]
        assert not data["proofCreditGranted"]
        app = prospective["template"] / "app.py"
        app.write_text(app.read_text() + "\n# New key cannot evade unknown ownership\n")
        busy = invoke(reopened, "harness_audit", mode="prospective")["structuredContent"]
        assert busy["attemptId"] == data["attemptId"] and "busy" in busy["reason"]
        assert reopened.audits.thread is None and reopened.audits.prepared is None
    finally:
        reopened.close()


def test_public_audit_binding_and_input_refuse_before_effects(tmp_path, monkeypatch):
    """Catch audit bypassing the workspace guard or accepting outside/oracle paths.

    Engine tests lack transport assertions; older route tests cannot establish
    that this newly registered mutator receives the same early refusal boundary.
    """
    from codex_harness.core import mcp_planner

    calls = []

    def forbidden(*args, **kwargs):
        calls.append("store-or-dispatch")
        raise AssertionError("Refuse before store or audit subprocess effects")

    monkeypatch.setattr(mcp_planner, "open_plan_store", forbidden)
    monkeypatch.setattr(mcp_planner, "open_plan_store_readonly", forbidden)
    monkeypatch.setattr(PlannerServer, "_dispatch", forbidden)
    root = tmp_path / "absent-project"
    server = PlannerServer(root)
    try:
        for arguments, code in (
            ({"mode": "prospective"}, "workspace-assertion-required"),
            (
                {"mode": "prospective", "expected_workspace": str(tmp_path / "other")},
                "workspace-mismatch",
            ),
        ):
            response = server.handle_line(
                json.dumps(
                    dict(
                        jsonrpc="2.0",
                        id=1,
                        method="tools/call",
                        params=dict(name="harness_audit", arguments=arguments),
                    )
                )
            )["result"]
            assert response["isError"] and code in response["content"][0]["text"]
        for arguments in (
            {"mode": "oracle-verified"},
            {"mode": "prospective", "template": str(tmp_path / "outside")},
        ):
            assert invoke(server, "harness_audit", **arguments)["isError"]
        assert calls == [] and not root.exists()
    finally:
        server.close()
