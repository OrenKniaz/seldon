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

import pytest
from tests.python.core import test_test_audit
from tests.python.core.test_mcp_planner import native_confirmation_result

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PHASES, open_plan_store_readonly

prospective = test_test_audit.prospective


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


def test_pre_result_engine_exception_records_dispatch_without_proof(prospective):
    """Protect dispatch truth when the real engine raises before returning its envelope."""
    server, _ = started(prospective)
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

    monkeypatch.setattr(threading.Thread, "start", refused_start)
    exit_code = plan_cli.main(["--workspace", str(root), "--json", "audit", "--prospective"])
    response = json.loads(capsys.readouterr().out)
    assert exit_code == 2 and response["isError"]
    failed = response["data"]
    assert failed["attemptId"] and failed["state"] == "failed"
    assert failed["didRunValidation"] is False and failed["proofCreditGranted"] is False
    assert "Controlled thread creation refusal" in failed["error"]
    with open_plan_store_readonly(root) as reopened:
        assert reopened.audit_attempt(failed["attemptId"])["error"] == failed["error"]


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
