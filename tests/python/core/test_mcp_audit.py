"""Public planning audit through the real engine and persisted gate consumer.

Only disposable source-test stubs run. Native client responses are synthetic,
human identity stays unverified, and no provider or P1 product is launched.
"""

import io
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from tests.python.core import test_test_audit
from tests.python.core.planning_support import source_only_expansion
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows held-Job containment only")
def test_public_audit_persists_observed_job_quiescence(prospective):
    """Catch omitted collection/proof containment; helper-only tests miss the durable consumer."""
    root = prospective["template"]
    pulse = root.parent / "contained-pulse"
    child = (
        "import sys,time;from pathlib import Path;p=Path(sys.argv[1]);end=time.monotonic()+2\n"
        "while time.monotonic()<end:\n"
        " with p.open('ab') as f:f.write(b'x')\n"
        " time.sleep(.03)\n"
    )
    (root / "tests/test_app.py").write_text(
        "import subprocess,sys,time,unittest\nfrom pathlib import Path\nfrom app import value\n"
        f"PULSE=Path({str(pulse)!r})\nbefore=PULSE.stat().st_size if PULSE.exists() else 0\n"
        f"subprocess.Popen([sys.executable,'-I','-S','-c',{child!r},{str(pulse)!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "end=time.monotonic()+3\n"
        "while (not PULSE.exists() or PULSE.stat().st_size<=before) and time.monotonic()<end:"
        "time.sleep(.01)\n"
        "class Proof(unittest.TestCase):\n def test_value(self):\n"
        "  self.assertEqual(value(),42)\n"
    )
    server, revision = started(prospective)
    workspace = server.workspace
    try:
        result = finished_audit(server, mode="prospective")["structuredContent"]
        assert result["ok"] and result["manifestRevision"] == revision
        controls = [item["processControl"] for item in result["collection"]]
        controls.append(result["redBaseline"][0]["processControl"])
        assert all(item["membershipVerifiedBeforePayload"] for item in controls)
        assert all(
            item["quiescenceVerified"] and item["activeProcessesAfterTeardown"] == 0
            for item in controls
        )
        count = pulse.stat().st_size
        time.sleep(0.15)
        assert pulse.stat().st_size == count
    finally:
        server.close()
    with open_plan_store_readonly(workspace) as saved:
        assert saved.passing_audit(revision)["collection"] == result["collection"]


def test_stdio_audit_returns_durable_attempt_before_real_engine_finishes(prospective):
    """Protect prompt admission when a real protected-module import stalls.

    Existing in-process audit tests wait for completion, so they miss a lost
    tools/call reply before any durable identity reaches the caller.
    """
    workspace = prospective["template"].parent
    entered, release = workspace / "audit-entered", workspace / "audit-release"
    proof = prospective["template"] / "tests/test_app.py"
    original = proof.read_text(encoding="utf-8")
    proof.write_text(
        f"open({str(entered)!r}, 'w').close()\n"
        "from pathlib import Path\nimport time\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(0.01)\n" + original,
        encoding="utf-8",
    )
    setup, _ = started(prospective)
    setup.close()
    replies = queue.Queue()
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "codex_harness.core.mcp_planner",
            "--workspace",
            str(workspace),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    def read_replies():
        for line in process.stdout:
            replies.put(json.loads(line))

    reader = threading.Thread(target=read_replies, daemon=True)
    reader.start()
    try:
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 39,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                }
            )
            + "\n"
        )
        process.stdin.flush()
        assert replies.get(timeout=5)["id"] == 39
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                }
            )
            + "\n"
        )
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 40, "method": "ping"}) + "\n")
        process.stdin.flush()
        assert replies.get(timeout=5)["id"] == 40, "Real stdio handshake failed before audit"
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 41,
                    "method": "tools/call",
                    "params": {
                        "name": "harness_audit",
                        "arguments": {
                            "mode": "prospective",
                            "expected_workspace": str(workspace),
                        },
                    },
                }
            )
            + "\n"
        )
        process.stdin.flush()
        try:
            admission = replies.get(timeout=2)
        except queue.Empty:
            pytest.fail("Public audit blocked behind the real engine without an attempt ID")
        assert not release.exists(), "Fixture released before admission was observed"
        data = admission["result"]["structuredContent"]
        assert data["attemptId"] and data["state"] in {"queued", "running"}
        assert data["proofCreditGranted"] is False

        def tool(name, **arguments):
            process.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 42,
                        "method": "tools/call",
                        "params": {
                            "name": name,
                            "arguments": {
                                "expected_workspace": str(workspace),
                                **arguments,
                            },
                        },
                    }
                )
                + "\n"
            )
            process.stdin.flush()
            return replies.get(timeout=5)["result"]

        pending = tool("harness_audit_status", id=data["attemptId"])["structuredContent"]
        assert pending["state"] in {"queued", "running"} and not pending["proofCreditGranted"]
        duplicate = tool("harness_audit", mode="prospective")["structuredContent"]
        assert duplicate["attemptId"] == data["attemptId"]
        app = prospective["template"] / "app.py"
        app_bytes = app.read_bytes()
        app.write_bytes(app_bytes + b"\n# Different captured source key\n")
        busy = tool("harness_audit", mode="prospective")["structuredContent"]
        assert busy["attemptId"] == data["attemptId"] and "busy" in busy["reason"]
        app.write_bytes(app_bytes)
        release.write_text("release", encoding="utf-8")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            completed = tool("harness_audit_status", id=data["attemptId"])["structuredContent"]
            if completed["state"] == "completed":
                break
            time.sleep(0.02)
        assert completed["state"] == "completed", completed
        assert entered.exists() and completed["result"]["ok"]
        assert completed["proofCreditGranted"] and completed["auditId"]
    finally:
        release.write_text("release", encoding="utf-8")
        process.stdin.close()
        process.wait(timeout=30)
        reader.join(timeout=2)
        errors = process.stderr.read()
        process.stdout.close()
        process.stderr.close()
        assert process.returncode == 0, errors
    with open_plan_store_readonly(workspace) as saved:
        reopened = saved.audit_attempt(data["attemptId"])
        assert reopened["state"] == "completed" and reopened["proofCreditGranted"]


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
    assert not invoke(server, "harness_plan_start", idea="Source-only red stub", name="Audit")[
        "isError"
    ]
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
            confirmed = native_confirmation_result(
                server, phase, "SOURCE-ONLY synthetic client response, not human acceptance"
            )
            assert not confirmed["isError"]
            assert confirmed["structuredContent"]["human_identity_verified"] is False
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


def test_stale_completion_and_late_unbound_writer_cannot_grant_credit(prospective):
    """Protect captured freshness and provenance; normal success misses late old writers."""
    server, _ = started(prospective)
    try:
        admission = invoke(server, "harness_audit", mode="prospective")["structuredContent"]
        app = prospective["template"] / "app.py"
        app.write_text(app.read_text() + "\n# Changed after capture\n")
        server.audits.dispatch_after_flush(server.store)
        server.audits.thread.join(timeout=30)
        assert not server.audits.thread.is_alive()
        server.audits.drain(server.store)
        response = invoke(server, "harness_audit_status", id=admission["attemptId"])
        stale = response["structuredContent"]
        assert stale["state"] == "stale" and stale["result"]["ok"]
        assert stale["auditId"] is None and not stale["proofCreditGranted"]
        legacy_id = server.store.record_audit(
            server.store.manifest_revision(), {"ok": True, "proofLevel": "prospective"}
        )
        assert legacy_id and server.store.passing_audit(server.store.manifest_revision()) is None
        public = invoke(server, "harness_plan_status")["structuredContent"]
        assert public["reviewResolution"]["audit"]["current"] is False
        refused = invoke(server, "harness_advance", to_phase="publish")
        assert refused["isError"] and "audit-binding-required" in refused["content"][0]["text"]
    finally:
        server.close()


def test_audit_status_missing_store_is_readonly(tmp_path):
    """Protect inspection confinement; valid workspace tests miss accidental initialization."""
    server = PlannerServer(tmp_path)
    try:
        assert invoke(server, "harness_audit_status", id="unknown")["isError"]
        assert invoke(server, "harness_audit_status", manifest_revision="unknown")["isError"]
        assert not (tmp_path / ".codex-harness").exists()
    finally:
        server.close()


def test_public_legacy_history_by_revision_preserves_rows_without_correlation(prospective):
    """Protect recovery of old failed results; attempt-ID reads cannot recover ID-less calls."""
    server, revision = started(prospective)
    root = server.workspace
    identifiers = [
        server.store.record_audit(
            revision,
            {
                "ok": passed,
                "proofLevel": "prospective",
                "findings": [] if passed else [{"code": "retained-legacy-failure"}],
            },
        )
        for passed in (False, True)
    ]
    before = list(server.store.connection.iterdump())
    server.close()
    reader = PlannerServer(root)
    try:
        schema = invoke(reader, "harness_schema", tool="harness_audit_status")["structuredContent"]
        assert len(schema["inputSchema"]["oneOf"]) == 2
        assert invoke(reader, "harness_audit_status")["isError"]
        assert invoke(reader, "harness_audit_status", id="bad", manifest_revision=revision)[
            "isError"
        ]
        response = invoke(reader, "harness_audit_status", manifest_revision=revision)
        assert not response["isError"]
        history = response["structuredContent"]
        assert [row["id"] for row in history["audits"]] == identifiers
        assert [row["result"]["ok"] for row in history["audits"]] == [False, True]
        assert all(row["bindingProvenance"] == "unbound-history" for row in history["audits"])
        assert all(row["at"] for row in history["audits"])
        assert not history["requestCorrelationKnown"] and not history["serverQuiescenceKnown"]
        assert not history["proofCreditGranted"] and "attemptId" not in history
    finally:
        reader.close()
    with open_plan_store_readonly(root) as saved:
        assert list(saved.connection.iterdump()) == before
        assert saved.passing_audit(revision) is None


def test_failed_admission_flush_never_dispatches_real_engine(prospective, monkeypatch):
    """Protect reply-before-engine ordering; normal pipes never fail their flush."""
    from codex_harness.core import mcp_planner, test_audit

    server, _ = started(prospective)
    root = server.workspace
    server.close()
    calls = []
    engine = test_audit.audit_prospective_tests

    def observed(**arguments):
        calls.append(1)
        return engine(**arguments)

    class Stream(io.StringIO):
        def reconfigure(self, **kwargs):
            pass

    class BrokenReply(Stream):
        def write(self, text):
            raise BrokenPipeError("Controlled client reply loss")

    request = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 41,
                "method": "tools/call",
                "params": {
                    "name": "harness_audit",
                    "arguments": {
                        "mode": "prospective",
                        "expected_workspace": str(root),
                    },
                },
            }
        )
        + "\n"
    )
    with monkeypatch.context() as patch:
        patch.setattr(test_audit, "audit_prospective_tests", observed)
        patch.setattr(sys, "argv", ["planner", "--workspace", str(root)])
        patch.setattr(sys, "stdin", Stream(request))
        patch.setattr(sys, "stdout", BrokenReply())
        with pytest.raises(BrokenPipeError, match="Controlled"):
            mcp_planner.main()
    assert calls == []
    with open_plan_store_readonly(root) as saved:
        rows = saved.connection.execute("SELECT id FROM audit_attempts").fetchall()
        assert len(rows) == 1
        held = saved.audit_attempt(rows[0][0])
        assert held["state"] == "unknown-interrupted" and not held["didRunValidation"]
        assert not held["proofCreditGranted"]


def test_real_child_launch_failure_persists_failed_verdict_without_credit(prospective, monkeypatch):
    """Protect terminal refusal for launch failures; ordinary red proofs do launch children."""
    server, _ = started(prospective)
    try:
        admission = invoke(server, "harness_audit", mode="prospective")["structuredContent"]
        with monkeypatch.context() as patch:
            patch.setattr(sys, "executable", str(server.workspace / "missing-python.exe"))
            server.audits.dispatch_after_flush(server.store)
            server.audits.thread.join(timeout=10)
            assert not server.audits.thread.is_alive()
        server.audits.drain(server.store)
        result = invoke(server, "harness_audit_status", id=admission["attemptId"])
        failed = result["structuredContent"]
        assert result["isError"] and failed["state"] == "completed"
        assert failed["didRunValidation"] and failed["result"]["ok"] is False
        assert not failed["completedAllRequiredSteps"]
        assert failed["auditId"] and not failed["proofCreditGranted"]
    finally:
        server.close()


def test_pre_result_engine_exception_records_dispatch_without_proof(prospective, capsys):
    """Catch concurrent retry after engine failure; unstarted failure misses dispatch races."""
    from codex_harness.core import plan_cli

    server, revision = started(prospective)
    workspace = server.workspace
    try:
        admission = invoke(server, "harness_audit", mode="prospective")["structuredContent"]
        captured = server.audits.prepared[1]
        captured.template.rename(captured.template.with_name("removed-inputs"))
        server.audits.dispatch_after_flush(server.store)
        server.audits.thread.join(timeout=10)
        assert not server.audits.thread.is_alive()
        server.audits.drain(server.store)
        result = invoke(server, "harness_audit_status", id=admission["attemptId"])
        failed = result["structuredContent"]
        assert result["isError"] and failed["state"] == "failed"
        assert failed["didRunValidation"] and failed["result"] is None
        assert failed["error"] and not failed["completedAllRequiredSteps"]
        assert not failed["proofCreditGranted"]
    finally:
        server.close()
    barrier = threading.Barrier(2)
    admitted = threading.Barrier(2)
    outcomes, failures = [], []

    def retry():
        contender = PlannerServer(workspace)
        try:
            barrier.wait(timeout=10)
            receipt = invoke(contender, "harness_audit", mode="prospective")["structuredContent"]
            prepared = contender.audits.prepared is not None
            admitted.wait(timeout=10)  # Both requests race before either dispatches.
            contender.audits.dispatch_after_flush(contender.store)
            if contender.audits.thread is not None:
                contender.audits.thread.join(timeout=30)
                assert not contender.audits.thread.is_alive()
                contender.audits.drain(contender.store)
            outcomes.append((receipt, prepared))
        except BaseException as error:
            failures.append(error)
        finally:
            contender.close()

    contenders = [threading.Thread(target=retry) for _ in range(2)]
    for contender in contenders:
        contender.start()
    for contender in contenders:
        contender.join(timeout=45)
    assert not failures, failures
    assert all(not contender.is_alive() for contender in contenders)
    assert len(outcomes) == 2
    retry_ids = {receipt["attemptId"] for receipt, _ in outcomes}
    assert len(retry_ids) == 1 and failed["attemptId"] not in retry_ids
    assert sum(prepared for _, prepared in outcomes) == 1
    with open_plan_store_readonly(workspace) as reopened:
        assert reopened.audit_attempt(failed["attemptId"]) == failed
        completed = reopened.audit_attempt(retry_ids.pop())
        assert completed["result"]["ok"] and completed["proofCreditGranted"]
        assert reopened.passing_audit(revision)["id"] == completed["auditId"]
        assert len(reopened.audit_history(revision)["audits"]) == 1
        assert reopened.connection.execute("SELECT count(*) FROM audit_attempts").fetchone()[0] == 2
    assert plan_cli.main(["--workspace", str(workspace), "--json", "audit", "--prospective"]) == 0
    cached = json.loads(capsys.readouterr().out)["data"]
    assert cached["attemptId"] == completed["attemptId"]


@pytest.mark.parametrize("fault", ["body", "cleanup", "both"])
def test_engine_failure_diagnostics_survive_public_status_and_reopen(
    prospective, monkeypatch, fault
):
    """Protect genuine verdicts and internal-failure evidence across housekeeping faults.

    Controlled SOURCE-ONLY faults follow real engine collection and red proof execution.
    They test error retention, not the live Windows lock cause or process quiescence.
    """
    from codex_harness.core import test_audit

    real_directory = test_audit.TemporaryDirectory
    real_order = test_audit.topological_order
    owned_directories = []

    class CleanupFault(real_directory):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            owned_directories.append(self)

        def cleanup(self):
            if fault in {"cleanup", "both"}:
                raise PermissionError(32, "Controlled cleanup fault", self.name)
            super().cleanup()

    def fail_after_actual_proofs(plan):
        yield from real_order(plan)
        raise RuntimeError("Controlled body fault " + "bounded-output-" * 4096)

    server, revision = started(prospective)
    workspace = server.workspace
    try:
        admission = invoke(server, "harness_audit", mode="prospective")["structuredContent"]
        with monkeypatch.context() as patch:
            patch.setattr(test_audit, "TemporaryDirectory", CleanupFault)
            if fault in {"body", "both"}:
                patch.setattr(test_audit, "topological_order", fail_after_actual_proofs)
            server.audits.dispatch_after_flush(server.store)
            server.audits.thread.join(timeout=30)
            assert not server.audits.thread.is_alive()
        server.audits.drain(server.store)
        response = invoke(server, "harness_audit_status", id=admission["attemptId"])
        failed = response["structuredContent"]
        if fault == "cleanup":
            assert not response["isError"] and failed["state"] == "completed"
            assert failed["completedAllRequiredSteps"] and failed["proofCreditGranted"]
            assert failed["auditId"] == failed["result"]["id"]
            assert failed["result"]["ok"] and failed["result"]["cleanupWarning"]
            warning = failed["result"]["cleanupWarning"][0]
            assert warning["error"]["kind"] == "PermissionError"
            assert warning["quiescence"] == "UNVERIFIED"
            assert warning["leftoverPath"] == owned_directories[0].name
            assert Path(warning["leftoverPath"]).is_dir()
        else:
            assert_failed_diagnostics(response, failed, fault)
    finally:
        server.close()
        # Only new owned fixtures are removed after the retained-warning observation.
        for directory in owned_directories:
            real_directory.cleanup(directory)
    reopened = PlannerServer(workspace)
    try:
        retained = invoke(reopened, "harness_audit_status", id=admission["attemptId"])
        assert retained["isError"] is (fault != "cleanup")
        assert retained["structuredContent"] == failed
        with open_plan_store_readonly(workspace) as saved:
            assert (saved.passing_audit(revision) is not None) is (fault == "cleanup")
    finally:
        reopened.close()


def assert_failed_diagnostics(response, failed, fault):
    """Shared assertions for the body-only and combined controlled failures."""
    assert response["isError"] and failed["state"] == "failed"
    assert failed["didRunValidation"] and not failed["completedAllRequiredSteps"]
    assert failed["result"] is None and failed["auditId"] is None
    assert not failed["proofCreditGranted"]
    error = failed["error"]
    assert error.startswith("ProspectiveAuditFailure: ")
    assert len(error.encode("utf-8")) <= 16384
    detail = json.loads(error.split(": ", 1)[1])
    assert detail["schema"] == "seldon/prospective-failure-v1"
    assert detail["stage"] == ("proof" if fault in {"body", "both"} else "body-complete")
    assert detail["collection"]["count"] >= 1
    assert all(item["passed"] for item in detail["collection"]["items"])
    assert detail["proofs"]["count"] == 1
    assert detail["proofs"]["items"][0]["failureClassification"] == "meaningful-red"
    if fault in {"body", "both"}:
        assert detail["bodyError"]["messageTruncated"] is True
    for key, expected in (
        ("bodyError", "RuntimeError" if fault in {"body", "both"} else None),
        ("cleanupError", "PermissionError" if fault in {"cleanup", "both"} else None),
    ):
        if expected is None:
            assert detail[key] is None
        else:
            assert detail[key]["kind"] == expected
            assert (
                detail[key]["message"].startswith("Controlled")
                or "Controlled" in detail[key]["message"]
            )
            frames = detail[key]["frames"]
            assert 1 <= len(frames) <= 6
            assert all(set(frame) == {"file", "function", "line"} for frame in frames)
            assert all(
                frame["file"] in {"test_audit.py", "runners.py", "planner_audits.py"}
                for frame in frames
            )


@pytest.mark.parametrize(
    "boundary", ["captured", "captured-body-error", "observation", "observation-body-error"]
)
def test_other_cleanup_boundaries_preserve_public_audit_receipts(
    prospective, monkeypatch, boundary
):
    """Catch lost async queue/proof results; the prospective-directory test misses these owners.

    Real collection/proofs run; only cleanup of new owned fixtures is fault-injected.
    The nested case catches earlier proof warnings lost on a later engine failure.
    """
    from codex_harness.core import planner_audits, runners, test_audit

    module = planner_audits if boundary.startswith("captured") else runners
    base = module.TemporaryDirectory
    owned = []

    class CleanupFault(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            owned.append(self)

        def cleanup(self):
            raise PermissionError(32, "Controlled boundary cleanup fault", self.name)

    def fail_after_proofs(plan):
        yield from order(plan)
        raise RuntimeError("Controlled captured body fault")

    order = test_audit.topological_order
    server, revision = started(prospective)
    workspace = server.workspace
    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, "TemporaryDirectory", CleanupFault)
            if boundary.endswith("body-error"):
                patch.setattr(test_audit, "topological_order", fail_after_proofs)
            admission = invoke(server, "harness_audit", mode="prospective")["structuredContent"]
            server.audits.dispatch_after_flush(server.store)
            server.audits.thread.join(timeout=30)
            assert not server.audits.thread.is_alive()
        server.audits.drain(server.store)
        reply = invoke(server, "harness_audit_status", id=admission["attemptId"])
        receipt = reply["structuredContent"]
        if boundary.endswith("body-error"):
            assert receipt["state"] == "failed" and receipt["result"] is None
            assert receipt["auditId"] is None and not receipt["proofCreditGranted"]
            detail = json.loads(receipt["error"].split(": ", 1)[1])
            assert len(receipt["error"].encode("utf-8")) <= 16384
            assert detail["bodyError"]["kind"] == "RuntimeError"
            if boundary == "captured-body-error":
                assert detail["inputCleanupWarning"]["error"]["kind"] == "PermissionError"
            else:
                warnings = detail["cleanupWarnings"]
                assert warnings["count"] == 1 and not warnings["truncated"]
                warning = warnings["items"][0]
                assert warning["error"]["kind"] == "PermissionError"
                assert warning["leftoverPath"] == owned[0].name
                assert Path(warning["leftoverPath"]).is_dir()
        else:
            assert not reply["isError"] and receipt["state"] == "completed"
            assert receipt["result"]["ok"] and receipt["proofCreditGranted"]
            warning = receipt["result"]["cleanupWarning"][0]
            assert warning["error"]["kind"] == "PermissionError" and warning["attempts"] == 3
            assert warning["leftoverPath"] == owned[0].name
            assert Path(warning["leftoverPath"]).is_dir()
    finally:
        server.close()
        for directory in owned:
            base.cleanup(directory)
    with open_plan_store_readonly(workspace) as saved:
        assert saved.audit_attempt(admission["attemptId"])["result"] == receipt["result"]
        assert saved.audit_attempt(admission["attemptId"])["error"] == receipt["error"]
        assert (saved.passing_audit(revision) is not None) is (not boundary.endswith("body-error"))


def test_engine_upgrade_admits_new_attempt_after_terminal_but_preserves_busy_hold(
    prospective, monkeypatch
):
    """Protect engine-build deduplication; input-only retry tests miss upgraded code."""
    from codex_harness.core import test_audit

    server, _ = started(prospective)
    try:
        first = invoke(server, "harness_audit", mode="prospective")["structuredContent"]
        engine = test_audit.audit_prospective_tests

        def upgraded_engine(**arguments):
            return engine(**arguments)  # Execute real proofs under a distinct loaded build.

        monkeypatch.setattr(test_audit, "audit_prospective_tests", upgraded_engine)
        busy = invoke(server, "harness_audit", mode="prospective")["structuredContent"]
        assert busy["attemptId"] == first["attemptId"] and "busy" in busy["reason"]
        server.audits.dispatch_after_flush(server.store)
        server.audits.thread.join(timeout=30)
        assert not server.audits.thread.is_alive()
        server.audits.drain(server.store)
        second = invoke(server, "harness_audit", mode="prospective")["structuredContent"]
        assert second["attemptId"] != first["attemptId"]
        assert second["engineIdentity"] != first["engineIdentity"]
        assert not second["proofCreditGranted"]
        server.audits.dispatch_after_flush(server.store)
        server.audits.thread.join(timeout=30)
        assert not server.audits.thread.is_alive()
        server.audits.drain(server.store)
        final = invoke(server, "harness_audit_status", id=second["attemptId"])["structuredContent"]
        assert final["result"]["ok"] and final["proofCreditGranted"]
    finally:
        server.close()
    with open_plan_store_readonly(prospective["template"].parent) as reopened:
        assert (
            reopened.audit_attempt(first["attemptId"])["engineIdentity"] == first["engineIdentity"]
        )
        assert (
            reopened.audit_attempt(second["attemptId"])["engineIdentity"]
            == second["engineIdentity"]
        )


def test_cli_thread_start_refusal_returns_persisted_failure_json(prospective, monkeypatch, capsys):
    """Protect normal CLI failure receipts; engine failures occur after thread creation."""
    from codex_harness.core import plan_cli

    server, _ = started(prospective)
    root = server.workspace
    server.close()

    def refused_start(thread):
        raise RuntimeError("Controlled thread creation refusal")

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", refused_start)
        exit_code = plan_cli.main(["--workspace", str(root), "--json", "audit", "--prospective"])
    response = json.loads(capsys.readouterr().out)
    assert exit_code == 2 and response["isError"]
    failed = response["data"]
    assert failed["attemptId"] and failed["state"] == "failed"
    assert failed["didRunValidation"] is False and failed["proofCreditGranted"] is False
    assert "Controlled thread creation refusal" in failed["error"]
    with open_plan_store_readonly(root) as reopened:
        assert reopened.audit_attempt(failed["attemptId"])["error"] == failed["error"]
    retried = PlannerServer(root)
    try:
        admission = invoke(retried, "harness_audit", mode="prospective")["structuredContent"]
        assert admission["attemptId"] != failed["attemptId"]
        assert admission["state"] == "queued" and not admission["proofCreditGranted"]
        assert admission["manifestRevision"] == failed["manifestRevision"]
        assert admission["inputDigest"] == failed["inputDigest"]
        assert admission["engineIdentity"] == failed["engineIdentity"]
        retried.audits.dispatch_after_flush(retried.store)
        retried.audits.thread.join(timeout=30)
        assert not retried.audits.thread.is_alive()
        retried.audits.drain(retried.store)
        completed = invoke(retried, "harness_audit_status", id=admission["attemptId"])[
            "structuredContent"
        ]
        assert completed["result"]["ok"] and completed["proofCreditGranted"]
        assert retried.store.audit_attempt(failed["attemptId"]) == failed
    finally:
        retried.close()


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


def test_public_audit_refuses_template_link_outside_workspace(prospective, tmp_path, monkeypatch):
    """Catch a fixed-looking template path resolving outside the project.

    Argument confinement alone does not catch a project-local link; the existing
    compile-route check is now required before the new audit engine is called.
    """
    from codex_harness.core import test_audit

    server, _ = started(prospective)
    external = tmp_path.parent / (tmp_path.name + "-external-template")
    prospective["template"].rename(external)
    link = prospective["template"]
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError as error:
        server.close()
        pytest.skip(f"Directory symlink privilege unavailable: {error}")
    before = list(server.store.connection.iterdump())

    def forbidden(*args, **kwargs):
        pytest.fail("A template link must refuse before the audit engine")

    monkeypatch.setattr(test_audit, "audit_prospective_tests", forbidden)
    try:
        refused = invoke(server, "harness_audit", mode="prospective")
        assert refused["isError"]
        assert "Audit template must stay in the fixed workspace" in refused["content"][0]["text"]
        assert list(server.store.connection.iterdump()) == before
    finally:
        server.close()
