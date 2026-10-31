"""Real MCP -> PlanStore -> spine -> export integration, without a provider."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from tests.python.core import test_test_audit

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.planner_views import banner

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit" / "spine.json"
revision_proof = test_test_audit.prospective


def workspace_wire(server, tool_name, **arguments):
    """Actual unscoped wire; never fill missing caller intent from the server."""
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 901,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        )
    )["result"]


def workspace_files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("harness_plan_start", {"idea": "Tally", "name": "Tally"}),
        ("harness_worker_start", {}),
        ("harness_advance", {"to_phase": "tech-stack"}),
        ("harness_plan_resume", {"request_execution": True}),
        ("harness_record_confirmation", {"gate": "align"}),
        (
            "harness_mail_send",
            {"from_agent": "user", "to_agent": "planner", "subject": "x", "body": "x"},
        ),
    ],
)
def test_wire_workspace_refuses_before_store_or_native_interception(
    tmp_path, monkeypatch, tool, arguments
):
    """Protect pre-binding isolation and truthful phase unknowns; normal refusals read status."""
    from codex_harness.core import mcp_planner

    bound, expected = tmp_path / "bound", tmp_path / "expected"
    server = PlannerServer(bound)
    opened, providers = [], []

    def forbidden_open(*args, **kwargs):
        opened.append(args)
        raise AssertionError("Workspace refusal must precede store access")

    def forbidden_provider(*args, **kwargs):
        providers.append(args)
        raise AssertionError("Workspace refusal must precede provider admission")

    monkeypatch.setattr(mcp_planner, "open_plan_store", forbidden_open)
    monkeypatch.setattr(subprocess, "Popen", forbidden_provider)
    try:
        for supplied, code in (
            (None, "workspace-assertion-required"),
            (str(expected), "workspace-mismatch"),
            ("relative", "workspace-assertion-invalid"),
        ):
            context = {} if supplied is None else {"expected_workspace": supplied}
            reply = workspace_wire(server, tool, **arguments, **context)
            assert reply["isError"] and code in reply["content"][0]["text"], reply
            assert reply["workspaceBinding"]["clientWorkspaceVerified"] is False
            if tool == "harness_advance":
                assert reply["structuredContent"]["currentPhase"] == "unknown"
                assert reply["structuredContent"]["nextPhase"] == "unknown"
                assert "next legal phase: unknown" in reply["content"][0]["text"]
            assert (
                str(bound) in reply["content"][0]["text"]
                and str(supplied) in reply["content"][0]["text"]
            )
            assert server.store is None and server._pending_confirmation is None
            assert not bound.exists() and not expected.exists() and not opened and not providers
    finally:
        server.close()


def test_wire_workspace_correct_projects_and_stale_connection(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    servers = [PlannerServer(first), PlannerServer(second)]
    try:
        for project, server in zip((first, second), servers, strict=True):
            project.mkdir()
            result = workspace_wire(
                server,
                "harness_plan_start",
                plan_mode="legacy",
                idea=project.name,
                name=project.name,
                expected_workspace=str(project / "unused" / ".."),
            )
            assert not result["isError"], result
            assert result["workspaceBinding"]["match"] == "matched"
            assert result["workspaceBinding"]["clientWorkspaceVerified"] is False
        before = {str(p): workspace_files(p) for p in (first, second)}
        # Editing/reloading an external config cannot retarget either existing fixed server.
        config = tmp_path / "connection-config.json"
        config.write_text(json.dumps({"workspace": str(second)}), encoding="utf-8")
        for cached in (servers[0], PlannerServer(first)):
            try:
                refused = workspace_wire(
                    cached,
                    "harness_plan_start",
                    plan_mode="legacy",
                    idea="Second",
                    name="Second",
                    expected_workspace=json.loads(config.read_text())["workspace"],
                )
                assert refused["isError"] and "workspace-mismatch" in refused["content"][0]["text"]
                assert cached.workspace == first
            finally:
                if cached is not servers[0]:
                    cached.close()
        assert {str(p): workspace_files(p) for p in (first, second)} == before
        for project, server in zip((first, second), servers, strict=True):
            status = workspace_wire(server, "harness_plan_status", expected_workspace=str(project))
            assert not status["isError"] and status["structuredContent"]["name"] == project.name
            resumed = workspace_wire(
                server,
                "harness_plan_start",
                plan_mode="legacy",
                idea="Existing intent",
                name="Resume",
                expected_workspace=str(project),
            )
            assert not resumed["isError"]
            assert server._status()["name"] == project.name
    finally:
        for server in servers:
            server.close()


EXPECTED_TOOLS = {
    "harness_recovery",
    "harness_schema",
    "harness_preflight",
    "harness_intake",
    "harness_reviewer_catalog",
    "harness_reviewer_choice",
    "harness_wave_map",
    "harness_waves",
    "harness_review",
    "harness_review_policy",
    "harness_adopt_review_policy",
    "harness_readiness",
    "harness_compile",
    "harness_packet_view",
    "harness_show_node",
    "harness_revise_test",
    "harness_nodes",
    "harness_resolve_finding",
    "harness_resolve_findings",
    "harness_accept_scenarios",
    "harness_accept_run",
    "harness_accept_status",
    "harness_accept_rebind",
    "harness_accept_review",
    "harness_accept_assess",
    "harness_accept",
    "harness_mail_send",
    "harness_mail_inbox",
    "harness_mail_ack",
    "harness_mail_reply",
    "harness_plan_start",
    "harness_plan_status",
    "harness_understanding_card",
    "harness_record_confirmation",
    "harness_prepare_chat_confirmation",
    "harness_record_chat_confirmation",
    "harness_advance",
    "harness_upsert_node",
    "harness_upsert_nodes",
    "harness_roundtable",
    "harness_audit",
    "harness_audit_status",
    "harness_remove_node",
    "harness_ask",
    "harness_answer",
    "harness_add_assumption",
    "harness_resolve_assumption",
    "harness_decision_card",
    "harness_diagram",
    "harness_impact",
    "harness_render",
    "harness_publish",
    "harness_worker_start",
    "harness_worker_status",
    "harness_plan_resume",
}


def rpc(server, method, params=None, request_id=1):
    # Existing same-project fixtures explicitly target their constructed project.
    # Routing counterexamples use workspace_wire instead, with no implicit intent.
    if (
        method == "tools/call"
        and isinstance(params, dict)
        and isinstance(params.get("arguments", {}), dict)
    ):
        params = {
            **params,
            "arguments": {
                "expected_workspace": str(server.workspace),
                **params.get("arguments", {}),
            },
        }
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
    )


def native_confirmation_result(server, gate, statement="Approve", **content):
    """Exercise the actual correlated wire bridge; this synthetic client is not a human."""
    rpc(
        server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}}
    )
    server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    outbound = rpc(
        server,
        "tools/call",
        {"name": "harness_record_confirmation", "arguments": {"gate": gate}},
        71,
    )
    assert outbound["method"] == "elicitation/create", outbound
    response = server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": outbound["id"],
                "result": {
                    "action": "accept",
                    "content": {
                        "approve": True,
                        "statement": statement,
                        "conditions": "",
                        **content,
                    },
                },
            }
        )
    )
    assert response["id"] == 71
    return response["result"]


def begin_native(
    server, *, gate="align", initialized=True, version="2025-06-18", capabilities=None
):
    rpc(
        server,
        "initialize",
        {
            "protocolVersion": version,
            "capabilities": {"elicitation": {}} if capabilities is None else capabilities,
        },
    )
    if initialized:
        server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    return rpc(
        server,
        "tools/call",
        {
            "name": "harness_record_confirmation",
            "arguments": {"gate": gate},
        },
        94,
    )


def answer_native(server, wire_id, *, action="accept", **content):
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": wire_id,
                "result": {"action": action, **({"content": content} if content else {})},
            }
        )
    )


def call(server, tool_name, *, error=False, **args):
    response = rpc(server, "tools/call", {"name": tool_name, "arguments": args})
    result = response["result"]
    assert result["isError"] is error, result
    text = result["content"][0]["text"]
    assert text.splitlines()[0].startswith("Seldon · project: ")
    assert banner(server._status()) in text.splitlines()
    assert text.splitlines()[-1].startswith("Next:")
    assert sum(line.startswith("Next:") for line in text.splitlines()) == 1
    return text


@pytest.fixture
def server(tmp_path):
    instance = PlannerServer(tmp_path)
    yield instance
    instance.close()


def start(server):
    from tests.python.core.planning_support import source_only_expansion

    result = call(
        server,
        "harness_plan_start",
        plan_mode="legacy",
        idea="Split the bill fairly",
        name="tip-split",
    )
    source_only_expansion(server.store)
    return result


@pytest.fixture
def revision_server(revision_proof):
    from tests.python.core.test_mcp_audit import started

    (revision_proof["template"] / "app.py").write_text(
        "class StoreError(ValueError): pass\n"
        "class Store:\n"
        "    def __init__(self, path): self.path = path\n"
        "    def load(self): raise NotImplementedError('planned storage')\n"
    )
    (revision_proof["template"] / "tests/test_app.py").write_text(
        "import tempfile, unittest\nfrom pathlib import Path\nfrom app import Store, StoreError\n"
        "class StorageProof(unittest.TestCase):\n"
        "    def setUp(self):\n"
        "        self.tmp = tempfile.TemporaryDirectory()\n"
        "        self.addCleanup(self.tmp.cleanup)\n"
        "        self.path = Path(self.tmp.name) / 'cards.json'\n"
        "    def test_malformed_json(self):\n"
        "        self.path.write_bytes(b'{')\n"
        "        store = Store(self.path)\n"
        "        with self.assertRaises(StoreError):\n"
        "            store.load()\n"
        "        self.assertEqual(self.path.read_bytes(), b'{')\n"
    )
    instance, _ = started(revision_proof)
    yield instance
    instance.close()


def test_public_protected_test_revision_writes_linked_source(revision_server):
    """Catch node-only review repairs; prior compile tests manually author changed files.

    The real writer adds Pocketboard's three omitted loaded-schema cases, and the
    real audit/packet/publication consumers must reject reuse of the previous proof.
    No provider result or successful review credit is fabricated.
    """
    from tests.python.core.test_mcp_audit import finished_audit

    server = revision_server
    path = server.workspace / "template/tests/test_app.py"
    before = path.read_bytes()
    revision = server.store.manifest_revision()
    previous = finished_audit(server, mode="prospective")["structuredContent"]
    assert previous["ok"], previous
    read = workspace_wire(
        server,
        "harness_show_node",
        expected_workspace=str(server.workspace),
        id="T",
        include_source=True,
    )["structuredContent"]["testFiles"][0]
    assert read["source"].encode() == before
    source = read["source"]
    for name, payload in (
        ("unsupported_version", b'{"version":99,"cards":[]}'),
        ("missing_cards", b'{"version":1}'),
        ("nonlist_cards", b'{"version":1,"cards":{}}'),
    ):
        source += (
            f"    def test_{name}(self):\n"
            '        """Reject invalid loaded schema; malformed JSON misses these envelopes."""\n'
            f"        self.path.write_bytes({payload!r})\n"
            "        store = Store(self.path)\n"
            "        with self.assertRaises(StoreError):\n"
            "            store.load()\n"
            f"        self.assertEqual(self.path.read_bytes(), {payload!r})\n"
        )
    reply = workspace_wire(
        server,
        "harness_revise_test",
        expected_workspace=str(server.workspace),
        node_id="T",
        path="tests/test_app.py",
        expected_manifest_revision=revision,
        expected_file_sha256=read["sha256"],
        source=source,
        reason="Address the review finding in the linked proof.",
    )
    assert not reply["isError"], reply
    result = reply["structuredContent"]
    assert path.read_bytes() == source.encode()
    assert result["manifestRevision"] != revision
    assert result["manifestRevision"] == server.store.manifest_revision()
    assert result["beforeSha256"] == hashlib.sha256(before).hexdigest()
    assert result["afterSha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert server.store.passing_audit(revision) is None
    assert server.store.audit_history(revision)["audits"][0]["id"] == previous["id"]
    assert "harness_compile" in call(server, "harness_packet_view", error=True, id="B")
    call(server, "harness_compile")
    packet = workspace_wire(
        server,
        "harness_packet_view",
        expected_workspace=str(server.workspace),
        id="B",
    )
    assert not packet["isError"], packet
    current = finished_audit(server, mode="prospective")["structuredContent"]
    assert current["ok"], current
    assert current["redBaseline"][0]["testsRun"] == 4
    assert current["manifestRevision"] == result["manifestRevision"]
    assert current["id"] != previous["id"]
    assert server.store.passing_audit(result["manifestRevision"])["id"] == current["id"]
    assert not server.store.successful_reviews(result["manifestRevision"])
    call(server, "harness_advance", error=True, to_phase="publish")
    assert server.store.status()["published_revision"] is None
    event = server.store.connection.execute(
        "SELECT payload_json FROM events WHERE kind='protected-test-revised'"
    ).fetchone()
    assert json.loads(event[0]) == result


@pytest.mark.parametrize("change", ["manifest", "raw-bytes"])
def test_public_test_revision_refuses_stale_preimage(revision_server, change):
    """Catch lost author updates, including normalized-newline hash collisions missed by compile."""
    server = revision_server
    proof = server.workspace / "template/tests/test_app.py"
    before = proof.read_bytes()
    revision = server.store.manifest_revision()
    if change == "manifest":
        node = server.store.spine().node("R")
        node["text"] += " Current acceptance wording."
        call(server, "harness_upsert_node", node=node)
    else:
        changed = before.replace(b"\r\n", b"\n")
        proof.write_bytes(changed if changed != before else before.replace(b"\n", b"\r\n"))
        assert server.store.manifest_revision() == revision
    retained = proof.read_bytes()
    reply = call(
        server,
        "harness_revise_test",
        error=True,
        node_id="T",
        path="tests/test_app.py",
        expected_manifest_revision=revision,
        expected_file_sha256=hashlib.sha256(before).hexdigest(),
        source=before.decode() + "\n# stale author replacement\n",
        reason="Review repair",
    )
    assert "plan-test-preimage-stale" in reply
    assert proof.read_bytes() == retained
    assert not server.store.connection.execute(
        "SELECT 1 FROM events WHERE kind='protected-test-revised'"
    ).fetchone()


@pytest.mark.parametrize("path", ["../outside.py", "app.py", "tests/unlinked.py"])
def test_public_test_revision_refuses_escape_and_unowned_files(revision_server, path):
    """Catch arbitrary-file writes; linked success and stale checks do not enforce ownership."""
    server = revision_server
    target = server.workspace / "template" / path
    if path != "app.py":
        target.write_text("# must survive\n")
    before = target.read_bytes()
    reply = call(
        server,
        "harness_revise_test",
        error=True,
        node_id="T",
        path=path,
        expected_manifest_revision=server.store.manifest_revision(),
        expected_file_sha256=hashlib.sha256(before).hexdigest(),
        source="# must not replace\n",
        reason="Review repair",
    )
    assert "plan-test-path-invalid" in reply
    assert target.read_bytes() == before


def test_public_test_revision_replace_failure_keeps_proof(revision_server, monkeypatch):
    """Catch truncate-first repairs; successful replacement tests cannot expose write failures."""
    from codex_harness.core import plan_store

    server = revision_server
    proof = server.workspace / "template/tests/test_app.py"
    before = proof.read_bytes()
    revision = server.store.manifest_revision()

    def denied_replace(*args):
        raise PermissionError("controlled failed replacement")

    monkeypatch.setattr(plan_store.os, "replace", denied_replace)
    reply = call(
        server,
        "harness_revise_test",
        error=True,
        node_id="T",
        path="tests/test_app.py",
        expected_manifest_revision=revision,
        expected_file_sha256=hashlib.sha256(before).hexdigest(),
        source=before.decode() + "\n# revised\n",
        reason="Review repair",
    )
    assert "controlled failed replacement" in reply
    assert proof.read_bytes() == before and server.store.manifest_revision() == revision
    assert not list(proof.parent.glob(".proof-*"))
    assert not server.store.connection.execute(
        "SELECT 1 FROM events WHERE kind='protected-test-revised'"
    ).fetchone()


def test_public_test_revision_serializes_competing_authors(revision_server, monkeypatch):
    """Catch two accepted writes from one preimage; sequential stale checks miss lost updates."""
    import threading

    from codex_harness.core import plan_store

    server = revision_server
    proof = server.workspace / "template/tests/test_app.py"
    before = proof.read_bytes()
    revision = server.store.manifest_revision()
    at_replace, release, second_started, second_done = (threading.Event() for _ in range(4))
    replace = plan_store.os.replace
    outcomes = {}

    def held_replace(staged, target):
        at_replace.set()
        assert release.wait(10), "Fixture never released the first replacement"
        return replace(staged, target)

    monkeypatch.setattr(plan_store.os, "replace", held_replace)

    def author(name):
        other = PlannerServer(server.workspace)
        try:
            if name == "second":
                second_started.set()
            outcomes[name] = workspace_wire(
                other,
                "harness_revise_test",
                expected_workspace=str(server.workspace),
                node_id="T",
                path="tests/test_app.py",
                expected_manifest_revision=revision,
                expected_file_sha256=hashlib.sha256(before).hexdigest(),
                source=before.decode() + f"\n# {name} author\n",
                reason="Review repair",
            )
        finally:
            other.close()
            if name == "second":
                second_done.set()

    first = threading.Thread(target=author, args=("first",))
    second = threading.Thread(target=author, args=("second",))
    first.start()
    try:
        assert at_replace.wait(10)
        second.start()
        assert second_started.wait(10)
        assert not second_done.wait(0.1), "Second writer bypassed the reservation"
    finally:
        release.set()
        first.join(10)
        if second.ident is not None:
            second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert not outcomes["first"]["isError"], outcomes
    assert outcomes["second"]["isError"], outcomes
    assert "plan-test-preimage-stale" in outcomes["second"]["content"][0]["text"]
    assert proof.read_bytes() == before + b"\n# first author\n"


@pytest.mark.parametrize(
    "setup",
    [
        {"projectFolder": ".."},
        {"repoUrl": "https://user:secret123@github.com/a/b"},
        {"repoUrl": "https://github.com/a/b?token=secret123"},
    ],
)
def test_setup_refused_before_store_or_echo(server, tmp_path, setup):
    """Protect confinement/secrecy before writes; previous starts had no setup boundary."""
    reply = call(server, "harness_plan_start", error=True, idea="Tiny", name="Tiny", setup=setup)
    assert "secret123" not in reply
    assert not (tmp_path / ".codex-harness/plan.sqlite3").exists()


def test_setup_start_resume_and_node_edit(server, tmp_path):
    """Protect durable optional choices; old mode-resume tests miss identity loss/overwrite."""
    setup = dict(
        projectFolder=str(tmp_path),
        gitName="Sample User",
        gitEmail="sample@example.test",
        githubLogin="sample-user",
        repoUrl="https://github.com/sample-user/demo",
    )
    call(server, "harness_plan_start", idea="Tiny", name="Tiny", setup=setup)
    assert server.store.spine().node("V1")["fields"]["setup"] == setup
    server.close()
    server = PlannerServer(tmp_path)
    try:
        resumed = call(server, "harness_plan_start", idea="Tiny", name="Tiny", setup={})
        assert "GitHub login: sample-user" in resumed and "Git name: Sample User" in resumed
        assert "sample-user" in call(server, "harness_plan_status")
        node = server.store.spine().node("V1")
        node["fields"]["setup"]["githubLogin"] = "edited-user"
        call(server, "harness_upsert_node", node=node)
        assert "edited-user" in call(server, "harness_plan_start", idea="Tiny", name="Tiny")
        revision = server.store.spine().plan_revision()
        node["fields"]["setup"]["repoUrl"] = "https://a:secret123@github.com/a/b"
        refused = call(server, "harness_upsert_nodes", error=True, nodes=[node])
        assert "secret123" not in refused
        assert server.store.spine().plan_revision() == revision
    finally:
        server.close()
