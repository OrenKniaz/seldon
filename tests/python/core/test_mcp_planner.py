"""Real MCP -> PlanStore -> spine -> export integration, without a provider."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.python.core.planning_support import publication_evidence
from tests.python.core.test_plan_store import draft_inputs, draft_intake, mock_catalog, mock_choice
from tests.python.core.test_preflight import inject_mock_discovery

from codex_harness.core.mcp_planner import PHASE_DIAGRAMS, TOOLS, PlannerServer
from codex_harness.core.plan_store import PHASES, open_plan_store
from codex_harness.core.planner_views import banner, chat_diagram
from codex_harness.core.spine import MARKDOWN_FILES, load_spine

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit" / "spine.json"
EXPECTED_TOOLS = {
    "harness_schema",
    "harness_preflight",
    "harness_intake",
    "harness_reviewer_catalog",
    "harness_reviewer_choice",
    "harness_wave_map",
    "harness_waves",
    "harness_review",
    "harness_readiness",
    "harness_packet_view",
    "harness_show_node",
    "harness_nodes",
    "harness_resolve_finding",
    "harness_resolve_findings",
    "harness_accept_scenarios",
    "harness_accept_run",
    "harness_accept_rebind",
    "harness_accept_review",
    "harness_accept",
    "harness_mail_send",
    "harness_mail_inbox",
    "harness_mail_ack",
    "harness_mail_reply",
    "harness_plan_start",
    "harness_plan_status",
    "harness_understanding_card",
    "harness_record_confirmation",
    "harness_advance",
    "harness_upsert_node",
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
}


def rpc(server, method, params=None, request_id=1):
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


def native_confirmation_result(
    server, gate, statement="Synthetic native client response", **content
):
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


def native_confirm(server, gate, statement="Synthetic native client response"):
    result = native_confirmation_result(server, gate, statement)
    assert not result["isError"], result
    return result["content"][0]["text"]


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


@pytest.mark.parametrize("case", ["no-capability", "old-version", "not-initialized", "direct-call"])
def test_native_confirmation_requires_negotiated_ready_client(server, case):
    start(server)
    if case == "direct-call":
        result = server.call_tool("harness_record_confirmation", {"gate": "align"})
    else:
        result = begin_native(
            server,
            initialized=case != "not-initialized",
            version="2025-03-26" if case == "old-version" else "2025-06-18",
            capabilities={} if case == "no-capability" else None,
        )["result"]
    assert result["isError"]
    assert "plan-confirmation-origin-unavailable" in result["content"][0]["text"]
    assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    assert server._pending_confirmation is None


@pytest.mark.parametrize("version", ["2025-06-18", "future"])
def test_native_response_correlation_replay_and_full_words(server, version):
    start(server)
    outbound = begin_native(server, version=version)
    assert outbound["method"] == "elicitation/create" and outbound["id"] != 94
    assert server.store.status()["plan_revision"] in outbound["params"]["message"]
    assert "Scope in:" in outbound["params"]["message"]
    assert "default" not in outbound["params"]["requestedSchema"]["properties"]["approve"]
    assert (
        answer_native(server, "invented-id", approve=True, statement="Forged", conditions="")
        is None
    )
    assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    words = "Exact complete response. " + "Long native response. " * 100
    result = answer_native(server, outbound["id"], approve=True, statement=words, conditions="")
    assert result["id"] == 94 and not result["result"]["isError"]
    data = result["result"]["structuredContent"]
    assert data["state"] == "approved" and data["content"]["statement"] == words
    assert data["provenance"] == "mcp-client-elicitation" and not data["human_identity_verified"]
    assert (
        answer_native(server, outbound["id"], approve=True, statement=words, conditions="") is None
    )
    assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 1
    call(server, "harness_advance", to_phase="expand")


@pytest.mark.parametrize("change", ["metadata", "changed-vision", "pending", "conditional"])
def test_native_status_uses_current_credit_and_distinguishes_history(server, change):
    """Protocol -> real store -> chat status; no providers or human identity proof."""
    start(server)
    native_confirm(server, "align")
    call(server, "harness_advance", to_phase="expand")
    call(server, "harness_advance", to_phase="vision-lock")
    native_confirm(server, "vision-lock", "Synthetic native vision confirmation")
    call(server, "harness_advance", to_phase="features")
    original = rpc(server, "tools/call", {"name": "harness_plan_status"})["result"]
    original_text = original["content"][0]["text"]
    assert original["structuredContent"]["approval"]["granted"]
    assert "Current vision-lock approval: granted" in original_text
    assert "native MCP client-reported response" in original_text
    assert "natural-person identity is not authenticated" in original_text
    assert "approved by proxy (mcp-client-elicitation)" not in original_text
    assert "Vision lock: locked vision" in original_text

    if change in {"metadata", "changed-vision"}:
        node = server.store.spine().node("V1")
        if change == "metadata":
            node["fields"]["validation"] = ["-B", "-m", "unittest", "discover"]
        else:
            node["text"] += " A new material user outcome."
        server.store.upsert_node(node)
    else:
        outbound = begin_native(server, gate="vision-lock")
        assert outbound["method"] == "elicitation/create"
        if change == "conditional":
            answer_native(
                server,
                outbound["id"],
                approve=True,
                statement="Before approval, review",
                conditions="First show the findings digest.",
            )
    current = rpc(server, "tools/call", {"name": "harness_plan_status"})["result"]
    status, text = current["structuredContent"], current["content"][0]["text"]
    if change == "metadata":
        assert status["approval"]["granted"] and not status["vision_lock_stale"]
        assert "Vision lock: locked vision" in text
    else:
        assert not status["approval"]["granted"]
        assert status["pending_gate"] == "approval:vision-lock"
        assert "Current vision-lock approval: not granted" in text
        assert "Vision lock: not currently granted" in text
        assert "Vision lock: locked vision" not in text
        assert "harness_record_confirmation gate=vision-lock" in text
        if change != "changed-vision":
            # Request revocation is distinct from a material content edit.
            assert not status["vision_lock_stale"]
    assert "Historical vision-lock record (not current approval credit)" in text
    assert "Historical statement: Synthetic native vision confirmation" in text


def test_native_status_keeps_legacy_statement_without_approval_credit(server):
    start(server)
    native_confirm(server, "align")
    call(server, "harness_advance", to_phase="expand")
    call(server, "harness_advance", to_phase="vision-lock")
    # Explicit legacy fixture: a historical row is not a native client response.
    with server.store._transaction():
        server.store._approve("vision-lock", "proxy:historical", "Historical words remain intact")
    result = rpc(server, "tools/call", {"name": "harness_plan_status"})["result"]
    status, text = result["structuredContent"], result["content"][0]["text"]
    assert not status["approval"]["granted"]
    assert "Current vision-lock approval: not granted" in text
    assert "Vision lock: locked vision" not in text
    assert "recorded by proxy:historical" in text
    assert "Historical statement: Historical words remain intact" in text
    assert "approved by you" not in text and "approved by proxy" not in text


@pytest.mark.parametrize(
    "case", ["decline", "cancel", "conditional", "negative", "stale", "invalid"]
)
def test_native_nonapproval_response_never_grants_gate(server, case):
    start(server)
    outbound = begin_native(server)
    content = dict(
        approve=case != "negative", statement="Yes, but first review my conditions", conditions=""
    )
    if case == "conditional":
        content["conditions"] = "First give me the reviewer digest."
    if case == "stale":
        node = server.store.spine().node("V1")
        node["text"] += " New material scope."
        server.store.upsert_node(node)
    if case == "invalid":
        content["verified"] = True
    result = answer_native(
        server,
        outbound["id"],
        action=case if case in {"decline", "cancel"} else "accept",
        **({} if case in {"decline", "cancel"} else content),
    )["result"]
    assert result["isError"]
    assert result["structuredContent"]["state"] != "approved"
    if case == "conditional":
        assert result["structuredContent"]["content"] == content
    assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    assert server._pending_confirmation is None
    call(server, "harness_advance", to_phase="expand", error=True)


@pytest.mark.parametrize("case", ["timeout", "disconnect", "client-cancel"])
def test_native_pending_lifetime_never_promotes_missing_response(server, case):
    start(server)
    outbound = begin_native(server)
    workspace = server.workspace
    if case == "timeout":
        server._pending_confirmation["deadline"] = -1
        responses = server.expire_confirmation()
        assert responses[0]["method"] == "notifications/cancelled"
        assert responses[1]["id"] == 94 and responses[1]["result"]["isError"]
    elif case == "disconnect":
        server.close()
    else:
        assert (
            server.handle_line(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/cancelled",
                        "params": {"requestId": 94},
                    }
                )
            )
            is None
        )
    assert server._pending_confirmation is None
    assert (
        answer_native(server, outbound["id"], approve=True, statement="Late", conditions="") is None
    )
    with open_plan_store(workspace) as store:
        assert store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
        event = store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='confirmation-responded' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert json.loads(event[0])["action"] == "cancel"


def test_native_quoted_or_forged_tool_arguments_cannot_complete_response(server):
    start(server)
    for arguments in (
        {"gate": "align", "user_statement": "Earlier words"},
        {"gate": "align", "verified": True},
        {"gate": "align", "response_id": "invented", "origin": "human"},
    ):
        result = rpc(
            server,
            "tools/call",
            {
                "name": "harness_record_confirmation",
                "arguments": arguments,
            },
        )["result"]
        assert result["isError"]
    assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0


def test_worker_mcp_start_status_reconnect_preserve_one_admission(tmp_path, monkeypatch):
    """Real job persistence with explicit process/store doubles; no workers launched."""
    from types import SimpleNamespace

    from tests.python.core.test_planner_jobs import SourceOnlyStore

    from codex_harness.core import planner_jobs

    store, launches = SourceOnlyStore(tmp_path), []
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *a, **k: (launches.append((a, k)) or SimpleNamespace(pid=321)),
    )
    workers = [
        dict(bead=key, provider="codex", model="gpt-6.1-sol", effort="medium")
        for key in store.manifest["beads"]
    ]
    for _ in range(2):
        client = PlannerServer(tmp_path)
        monkeypatch.setattr(client, "_store", lambda: store)
        monkeypatch.setattr(
            client,
            "_status",
            lambda: dict(
                store.status(),
                name="Source-only",
                plan_revision=store.revision,
                open_clarifications=0,
                open_assumptions=0,
            ),
        )
        started = rpc(
            client,
            "tools/call",
            {
                "name": "harness_worker_start",
                "arguments": {
                    "expected_revision": store.revision,
                    "workers": workers,
                    "timeout": 61,
                    "max_parallel": 1,
                },
            },
        )["result"]
        assert not started["isError"], started
        data = started["structuredContent"]
        assert data["status"] == "unknown" and not data["harnessDeliveryProven"]
        status = rpc(
            client,
            "tools/call",
            {
                "name": "harness_worker_status",
                "arguments": {
                    "job_id": data["jobId"],
                },
            },
        )["result"]
        assert status["structuredContent"] == data
        assert "harness_worker_status job_id=" in status["content"][0]["text"]
        client.close()
    assert len(launches) == 1


def test_native_stdio_request_response_keeps_original_tool_id(tmp_path):
    """Actual stdio loop, deterministic wire-ID fixture, no providers or human proof."""
    messages = [
        dict(
            jsonrpc="2.0",
            id=1,
            method="initialize",
            params={
                "protocolVersion": "2025-06-18",
                "capabilities": {"elicitation": {}},
            },
        ),
        dict(jsonrpc="2.0", method="notifications/initialized"),
        dict(
            jsonrpc="2.0",
            id=2,
            method="tools/call",
            params={
                "name": "harness_plan_start",
                "arguments": {"idea": "Native demo", "name": "Native demo"},
            },
        ),
        dict(
            jsonrpc="2.0",
            id=3,
            method="tools/call",
            params={
                "name": "harness_record_confirmation",
                "arguments": {"gate": "align"},
            },
        ),
        dict(
            jsonrpc="2.0",
            id="seldon-confirmation-fixture",
            result={
                "action": "accept",
                "content": {
                    "approve": True,
                    "statement": "Synthetic stdio client confirms",
                    "conditions": "",
                },
            },
        ),
        dict(
            jsonrpc="2.0",
            id=4,
            method="tools/call",
            params={
                "name": "harness_advance",
                "arguments": {"to_phase": "expand"},
            },
        ),
    ]
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "from types import SimpleNamespace; "
                "from codex_harness.core import mcp_planner; "
                "mcp_planner.uuid4=lambda: SimpleNamespace(hex='fixture'); mcp_planner.main()"
            ),
            "--workspace",
            str(tmp_path),
        ],
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2, "seldon-confirmation-fixture", 3, 4]
    assert replies[2]["method"] == "elicitation/create"
    assert replies[3]["result"]["structuredContent"]["state"] == "approved"
    assert not replies[4]["result"]["isError"]


def test_mail_tools_through_line_handler_without_starting_plan(server):
    def mail(name, **args):
        result = server.call_tool(name, args)
        assert not result["isError"], result
        if name == "harness_mail_inbox":
            return result["structuredContent"]["messages"]
        return result["structuredContent"]

    message_id = mail(
        "harness_mail_send",
        from_agent="worker:A:codex",
        to_agent="planner",
        subject="Blocked",
        body="Need the R1 decision",
        refs=["A", "R1"],
        kind="blocker",
    )["id"]
    assert not (server.workspace / ".codex-harness" / "plan.sqlite3").exists()
    assert mail("harness_mail_inbox")[0]["id"] == message_id
    refused = call(server, "harness_mail_ack", id=message_id, by="worker:B:claude", error=True)
    assert "mail-ack-wrong-agent" in refused
    reply_id = mail("harness_mail_reply", id=message_id, from_agent="planner", body="Use R1")["id"]
    reply = mail("harness_mail_inbox", agent="worker:A:codex")[0]
    assert reply["id"] == reply_id and reply["thread_id"] == message_id
    mail("harness_mail_ack", id=message_id, by="planner")
    assert mail("harness_mail_inbox") == []
    assert mail("harness_mail_inbox", unacked_only=False)[0]["acked_by"] == "planner"
    assert "mail-secret-refused" in call(
        server,
        "harness_mail_send",
        error=True,
        from_agent="user",
        to_agent="planner",
        subject="Note",
        body="password=refused",
    )
    assert mail("harness_mail_inbox") == []


def call(server, tool_name, *, error=False, **args):
    response = rpc(server, "tools/call", {"name": tool_name, "arguments": args})
    result = response["result"]
    assert result["isError"] is error, result
    text = result["content"][0]["text"]
    assert text.splitlines()[0] == banner(server._status())
    assert text.splitlines()[-1].startswith("Next:")
    assert sum(line.startswith("Next:") for line in text.splitlines()) == 1
    return text


@pytest.fixture
def server(tmp_path):
    instance = PlannerServer(tmp_path)
    yield instance
    instance.close()


def start(server):
    return call(server, "harness_plan_start", idea="Split the bill fairly", name="tip-split")


@pytest.mark.parametrize("existing_plan", [False, True])
def test_preflight_protocol_never_opens_or_creates_plan(tmp_path, monkeypatch, existing_plan):
    inject_mock_discovery(monkeypatch)
    workspace = tmp_path if existing_plan else tmp_path / "absent" / "project"
    if existing_plan:
        with open_plan_store(workspace) as store:
            store.upsert_node(
                dict(
                    id="V1",
                    kind="vision",
                    title="Existing plan",
                    text="Intent",
                    parents=[],
                    fields={},
                )
            )
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    def forbidden(*args, **kwargs):
        pytest.fail("Preflight must not invoke the normal status/store seam")

    monkeypatch.setattr(PlannerServer, "_status", forbidden)
    instance = PlannerServer(workspace)
    try:
        result = rpc(
            instance,
            "tools/call",
            dict(name="harness_preflight", arguments={"providers": ["codex", "claude"]}),
        )["result"]
        assert not result["isError"], result
        report = result["structuredContent"]
        assert [row["executable"]["status"] for row in report["providers"]] == [
            "available",
            "missing",
        ]
        assert report["isMocked"] is True and report["didRunProvider"] is False
        assert report["planDatabase"]["status"] == ("available" if existing_plan else "missing")
        assert "authentication UNKNOWN" in result["content"][0]["text"]
        assert "execution readiness NOT PROVEN" in result["content"][0]["text"]
        for arguments in ({"providers": ["unsupported"]}, {"providers": []}, {"unknown": True}):
            refused = rpc(
                instance, "tools/call", dict(name="harness_preflight", arguments=arguments)
            )["result"]
            assert refused["isError"] and "Next:" in refused["content"][0]["text"]
        assert instance.store is None
    finally:
        instance.close()
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_draft_tools_same_store_reopen_and_unmet_chat(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
    server = PlannerServer(tmp_path)
    try:

        def invoke(name, **arguments):
            result = rpc(server, "tools/call", dict(name=name, arguments=arguments))["result"]
            assert result["isError"] is False, result
            return result

        intake = invoke(
            "harness_intake",
            node=draft_intake(),
            expected_spine_revision=inputs["expected_spine_revision"],
        )
        catalog = invoke(
            "harness_reviewer_catalog",
            catalog=mock_catalog(),
            expected_spine_revision=intake["structuredContent"]["spineRevision"],
        )
        choice = invoke(
            "harness_reviewer_choice",
            node=mock_choice(catalog["structuredContent"]["catalog"], "standing-delegation"),
            expected_spine_revision=catalog["structuredContent"]["spineRevision"],
        )
        assert choice["structuredContent"]["independence"]["qualifyingReviewerNames"] == []
        inputs["expected_spine_revision"] = choice["structuredContent"]["spineRevision"]
        mapped = invoke("harness_wave_map", **inputs)
        assert set(mapped["structuredContent"]["nodeRevisions"]) == {"MAP", "DETAIL1", "DETAIL2"}
        projected = invoke("harness_waves")
        text = projected["content"][0]["text"]
        assert "Later meaningful refinement" in text and "Later owner" in text
        assert "pending producers: B1" in text and "Current owner" in text
        assert "Current exit facts" in text and "Later exit facts" in text
        assert "completed-real 0" in text and "outstanding 5" in text
        assert "standing-delegation" in text and "independence-unknown" in text
        assert "gpt-6.1-sol" in text and "high" in text
        assert "eligibility" in text.lower() and "not measured" in text
        before = server.store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        invoke("harness_waves")
        assert (
            server.store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == before
        )
        server.close()
        reopened = invoke("harness_waves")
        assert reopened == projected
        assert "Outcome 2" in invoke("harness_plan_status")["content"][0]["text"]
        stale = rpc(server, "tools/call", dict(name="harness_wave_map", arguments=inputs))["result"]
        assert stale["isError"] and "plan-spine-stale" in stale["content"][0]["text"]
    finally:
        server.close()


@pytest.mark.parametrize("target", ["intake", "choice", "map", "detail"])
def test_review_mcp_malformed_fields_refuses_atomically(tmp_path, target):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
        revision = store.spine().plan_revision()
        events = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
    if target in ("intake", "choice"):
        node = draft_intake()
        node["fields"] = []
        name = "harness_intake" if target == "intake" else "harness_reviewer_choice"
        arguments = dict(node=node, expected_spine_revision=revision)
    else:
        if target == "map":
            inputs["map_node"]["fields"] = []
        else:
            inputs["detail_nodes"][0]["fields"] = []
        name, arguments = "harness_wave_map", inputs
    server = PlannerServer(tmp_path)
    try:
        result = rpc(server, "tools/call", dict(name=name, arguments=arguments))["result"]
        assert result["isError"]
        assert "plan-draft-fields-invalid" in result["content"][0]["text"]
        assert server.store.spine().plan_revision() == revision
        assert (
            server.store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == events
        )
    finally:
        server.close()


def test_draft_observation_replacement_stales_choice_and_missing_preimage_refuses(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
    server = PlannerServer(tmp_path)
    try:
        missing = rpc(
            server, "tools/call", dict(name="harness_intake", arguments=dict(node=draft_intake()))
        )["result"]
        assert missing["isError"] and "expected_spine_revision" in missing["content"][0]["text"]
        catalog = server.store.record_reviewer_catalog(
            mock_catalog(), inputs["expected_spine_revision"]
        )["catalog"]
        selected = server.store.record_reviewer_selection(
            mock_choice(catalog, "user"),
            inputs["expected_spine_revision"],
            author_sessions={
                "implementationSessionIds": ["mock:author"],
                "reviewerSessionIds": {"primary": "mock:reviewer"},
            },
        )
        assert selected["independence"]["qualifyingReviewerNames"] == ["primary"]
        observation = mock_catalog()
        observation["id"] = "RC-00000000-0000-0000-0000-000000000002"
        server.store.record_reviewer_catalog(observation, selected["spineRevision"])
        result = server.call_tool("harness_waves", {})
        assert not result["isError"]
        assert result["structuredContent"]["reviewerSelection"]["state"] == "stale"
        assert "reviewer-catalog-stale" in result["content"][0]["text"]
    finally:
        server.close()


def add_fixture(server):
    spine = load_spine(FIXTURE)
    pending = {key: spine.node(key) for key in spine.ids()}
    pending["A"]["parents"].append("R6")  # Close the fixture's documented coverage gap.
    written = set()
    while pending:
        ready = [
            key
            for key, node in pending.items()
            if set(
                node["parents"]
                + node["fields"].get("dependsOn", [])
                + node["fields"].get("lessons", [])
            )
            <= written
        ]
        assert ready
        for key in ready:
            node = pending.pop(key)
            call(server, "harness_upsert_node", node=node)
            written.add(key)


@pytest.mark.parametrize("version", ["2024-11-05", "2025-03-26", "2025-06-18", "future", None])
def test_initialize_negotiation(server, version):
    response = rpc(server, "initialize", {"protocolVersion": version}, 17)
    assert response["id"] == 17
    assert response["result"] == {
        "protocolVersion": version if version in ("2024-11-05", "2025-03-26") else "2025-06-18",
        "serverInfo": {"name": "harness-planner", "version": "1.0.0"},
        "capabilities": {"tools": {}},
    }


def test_list_tools_and_notifications(server):
    tools = rpc(server, "tools/list")["result"]["tools"]
    assert {tool["name"] for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert "properties" in tool["inputSchema"]
        assert "required" in tool["inputSchema"]
    confirmation = next(tool for tool in tools if tool["name"] == "harness_record_confirmation")
    assert "Requires negotiated elicitation" in confirmation["description"]
    assert set(confirmation["inputSchema"]["properties"]) == {"gate"}
    assert server.handle_line('{"jsonrpc":"2.0","method":"notifications/initialized"}') is None
    assert rpc(server, "ping")["result"] == {}
    assert rpc(server, "unknown")["error"]["code"] == -32601


def test_each_tool_and_all_eleven_phases_to_publication(server, tmp_path):
    seen = set()

    def run(tool_name, **args):
        seen.add(tool_name)
        if tool_name == "harness_record_confirmation":
            return native_confirm(server, args["gate"], args["user_statement"])
        return call(server, tool_name, **args)

    assert "Phase 1/11: Align" in run("harness_plan_start", idea="Split fairly", name="tip-split")
    assert "approval:align" in run("harness_plan_status")
    run("harness_schema", kind="requirement")
    card = run(
        "harness_understanding_card",
        vision="Split fairly",
        users="Diners",
        first_user_moment="Shares total exactly",
        scope_in="Dollars",
        scope_out="History",
        assumptions=["Users have a CLI"],
    )
    assert "Your call: confirm, or tell me what's wrong." in card
    assert card.splitlines()[-1].startswith("Next:")
    assert server.store.spine().node("V1")["fields"]["users"] == "Diners"
    assert server.store.status()["open_assumptions"] == 1
    question = run("harness_ask", question="How many diners?", node_ids=["V1"])
    question_id = question.split("Question id: ")[1].splitlines()[0]
    run("harness_answer", id=question_id, answer="At least one")
    assumption = run("harness_add_assumption", text="English output", node_ids=["V1"])
    assumption_id = assumption.split("Assumption id: ")[1].splitlines()[0]
    run(
        "harness_resolve_assumption",
        id=assumption_id,
        confirmed=True,
        rationale="Confirmed scope",
        by="planner",
    )
    run(
        "harness_decision_card",
        id="temporary",
        question="Language?",
        options=[{"name": "Python", "pros": "Simple", "cons": "Runtime needed"}],
        recommendation="Python",
        parents=["V1"],
    )
    run("harness_remove_node", id="temporary")
    add_fixture(server)
    seen.add("harness_upsert_node")
    run("harness_show_node", id="V1")
    run("harness_nodes", kind="requirement")
    run("harness_diagram", kind="feature-map")
    run("harness_render")
    assert {path.name for path in (tmp_path / "plan" / "docs").iterdir()} == set(MARKDOWN_FILES)
    for phase in PHASES[1:]:
        current = server.store.phase()
        if current in ("align", "vision-lock"):
            run("harness_record_confirmation", gate=current, user_statement="I confirm this vision")
        if phase == "publish":
            publication_evidence(server.store)
        text = run("harness_advance", to_phase=phase)
        assert server.store.phase() == phase
        if phase in PHASE_DIAGRAMS:
            kind = PHASE_DIAGRAMS[phase]
            assert chat_diagram(server.store.spine(), kind) in text
        if phase == "publish":
            publication_evidence(server.store)
            published = run("harness_publish")
            review_id = server.store.connection.execute(
                "SELECT round_id FROM review_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0]
            run("harness_review", id=review_id)
            revision = server.store.status()["published_revision"]
            assert revision in published
            assert "Wave 1 (single bead): A" in published
            assert "Wave 2 (single bead): B" in published
            assert "Open assumptions: 1" in published
            assert (tmp_path / "plan" / "revisions" / f"{revision}.json").is_file()
            assert (tmp_path / "plan" / revision / "manifest.json").is_file()
            assert all((tmp_path / "plan" / revision / name).is_file() for name in MARKDOWN_FILES)
            run("harness_impact")
    run("harness_mail_send", from_agent="user", to_agent="planner", subject="Note", body="Ready")
    mail_id = server.result_data["id"]
    run("harness_mail_inbox")
    run("harness_mail_reply", id=mail_id, from_agent="planner", body="Understood")
    run("harness_mail_ack", id=mail_id, by="planner")
    # Acceptance and readiness have dedicated integration journeys.
    assert seen == EXPECTED_TOOLS - {
        name
        for name in EXPECTED_TOOLS
        if name.startswith("harness_accept")
        or name
        in {
            "harness_resolve_finding",
            "harness_resolve_findings",
            "harness_packet_view",
            "harness_readiness",
            "harness_preflight",
            "harness_intake",
            "harness_reviewer_catalog",
            "harness_reviewer_choice",
            "harness_wave_map",
            "harness_waves",
            "harness_worker_start",
            "harness_worker_status",
        }
    }
    decision = server.store.spine().node("D3")
    decision["text"] += " Clarified rounding."
    run("harness_upsert_node", node=decision)
    digest = run("harness_impact")
    assert "Affected beads: A" in digest
    assert "Untouched beads: B" in digest
    assert "Affected tests: test_money" in digest
    assert "Stale docs: PLAN.md, VISION.md" in digest
    server.close()
    with open_plan_store(tmp_path) as reopened:
        assert reopened.phase() == "accept"
        assert reopened.spine().node("D3")["text"].endswith("Clarified rounding.")


def test_confirmation_missing_empty_and_stale(server):
    start(server)
    text = call(server, "harness_advance", error=True, to_phase="expand")
    assert "plan-approval-required" in text and "align needs approval" in text
    assert "pending_gate: approval:align" in text
    call(server, "harness_record_confirmation", error=True, gate="align", user_statement="  ")
    native_confirm(server, "align", "Yes, that is right")
    node = server.store.spine().node("V1")
    node["text"] += " Changed scope."
    call(server, "harness_upsert_node", node=node)
    assert "plan-approval-stale" in call(server, "harness_advance", error=True, to_phase="expand")
    assert server.store.phase() == "align"


@pytest.mark.parametrize(
    "name,args",
    [
        ("harness_resolve_assumption", {"id": "missing", "confirmed": "false"}),
        ("harness_record_confirmation", {"gate": "align", "user_statement": "", "by": "user"}),
        ("harness_understanding_card", {"vision": "only one field"}),
        ("harness_upsert_node", {"node": {"id": "bad"}}),
        ("harness_diagram", {"kind": "invented"}),
        ("harness_ask", {"question": "why?", "node_ids": ["missing"]}),
        ("harness_publish", {}),
        ("harness_impact", {}),
        ("missing-tool", {}),
    ],
)
def test_invalid_tool_calls_do_not_crash_or_mutate(server, name, args):
    start(server)
    before = server.store.status()
    call(server, name, error=True, **args)
    assert server.store.status() == before
    call(server, "harness_plan_status")


@pytest.mark.parametrize(
    "line,code",
    [
        ("{broken", -32700),
        ("[]", -32600),
        ("null", -32600),
        ('{"jsonrpc":"1.0","method":"ping","id":1}', -32600),
        ('{"jsonrpc":"2.0","method":"ping","id":[]}', -32600),
        ('{"jsonrpc":"2.0","method":"ping","id":1,"params":[]}', -32602),
        ('{"jsonrpc":"2.0","method":"ping","id":NaN}', -32700),
    ],
)
def test_malformed_lines_recover(server, line, code):
    assert server.handle_line(line)["error"]["code"] == code
    assert rpc(server, "ping")["result"] == {}


def test_restart_resumes_without_overwriting_vision(server, tmp_path):
    start(server)
    native_confirm(server, "align", "Yes")
    call(server, "harness_advance", to_phase="expand")
    before = server.store.status()
    server.close()
    # Even an invalid first call after restart must report the durable phase/revision.
    invalid = call(
        server, "harness_resolve_assumption", error=True, id="missing", confirmed="false"
    )
    assert invalid.splitlines()[0] == banner(before)
    assert "Phase 2/11: Expand" in call(
        server, "harness_plan_start", idea="Other idea", name="Other name"
    )
    assert server.store.status() == before
    assert server.store.spine().node("V1")["text"] == "Split the bill fairly"
    assert not (tmp_path / "plan").exists()


def test_unavailable_status_is_error_and_server_recovers(server, monkeypatch):
    start(server)
    original = server.store.status

    def unavailable():
        raise OSError("Store unavailable")

    monkeypatch.setattr(server.store, "status", unavailable)
    response = rpc(server, "tools/call", {"name": "harness_plan_status"})["result"]
    assert response["isError"] is True
    assert response["content"][0]["text"].startswith("Harness Plan Mode: ")
    monkeypatch.setattr(server.store, "status", original)
    call(server, "harness_plan_status")


def test_render_containment_and_custom_directory(server, tmp_path):
    start(server)
    outside = tmp_path.parent / "outside-views"
    call(server, "harness_render", error=True, out_dir=str(outside))
    assert not outside.exists()
    paths = call(server, "harness_render", out_dir="custom/docs")
    assert str(tmp_path / "custom" / "docs" / "VISION.md") in paths
    call(server, "harness_render", out_dir=None)


def test_unstarted_errors_and_mutating_notifications(server, tmp_path):
    call(server, "harness_advance", error=True, to_phase="expand")
    assert (
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "harness_plan_start",
                        "arguments": {"idea": "bad", "name": "bad"},
                    },
                }
            )
        )
        is None
    )
    assert not (tmp_path / ".codex-harness").exists()


def test_stdio_subprocess_protocol_and_recovery(tmp_path):
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "harness_plan_start",
                "arguments": {"idea": "Fair shares", "name": "Diners 🧭"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "harness_advance", "arguments": {"to_phase": "expand"}},
        },
        {"jsonrpc": "2.0", "id": 5, "method": "ping"},
    ]
    wire = "{malformed\n" + "\n".join(json.dumps(item) for item in requests) + "\n"
    result = subprocess.run(
        [sys.executable, "-m", "codex_harness.core.mcp_planner", "--workspace", str(tmp_path)],
        input=wire,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(replies) == 6
    assert replies[0]["error"]["code"] == -32700
    assert {tool["name"] for tool in replies[2]["result"]["tools"]} == EXPECTED_TOOLS
    assert len(TOOLS) == len(EXPECTED_TOOLS)
    assert "Diners 🧭" in replies[3]["result"]["content"][0]["text"]
    assert replies[4]["result"]["isError"] is True
    assert replies[5]["result"] == {}
    for reply in replies[3:5]:
        assert reply["result"]["content"][0]["text"].startswith("Harness Plan Mode: ")
    assert "planner tool failed:" in result.stderr


def confirmation_rpc(server, **arguments):
    """Retain the actual synthetic wire exchange in the private scenario fixture."""
    request = dict(
        jsonrpc="2.0",
        id=41,
        method="tools/call",
        params=dict(name="harness_advance", arguments=arguments),
    )
    response = server.handle_line(json.dumps(request, ensure_ascii=False))
    with (server.workspace / "confirmation-wire.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(dict(request=request, response=response), ensure_ascii=False) + "\n"
        )
    return response["result"]


def mcp_wave_plan(workspace):
    from tests.python.core.test_wave_refine import cli, setup_wave

    setup_wave(workspace)
    cli(workspace, "compile", "--template", "template", "--out", "manifest.json")
    server = PlannerServer(workspace)
    try:
        for target in PHASES[1 : PHASES.index("plan") + 1]:
            current = server._store().phase()
            if current in ("align", "vision-lock"):
                native_confirm(server, current, "Synthetic earlier vision confirmation")
            call(server, "harness_advance", to_phase=target)
        return server
    except BaseException:
        server.close()
        raise


def test_advance_advertises_optional_confirmation_transport(server):
    tools = rpc(server, "tools/list")["result"]["tools"]
    advance = next(tool for tool in tools if tool["name"] == "harness_advance")
    schema = advance["inputSchema"]
    assert set(schema["properties"]) == {"to_phase", "user_statement", "by"}
    assert schema["required"] == ["to_phase"]
    assert schema["properties"]["user_statement"]["type"] == "string"
    assert "plan" in advance["description"] and "proxy:" in advance["description"]


@pytest.mark.parametrize("actor", [None, "user", "proxy:synthetic-Δ"])
def test_mcp_wave_confirmation_exact_event_lock_and_projection(tmp_path, actor):
    from codex_harness.core.waves import product_digest

    server = mcp_wave_plan(tmp_path / "wave")
    workspace = server.workspace
    statement = "  Synthetic approval: café Δ\r\nW1 now; W2 retained.  "
    try:
        before = server.store.spine().plan_revision()
        vision_lock = server.store._meta()["locked_vision_json"]
        args = dict(to_phase="roundtable", user_statement=statement)
        if actor is not None:
            args["by"] = actor
        result = confirmation_rpc(server, **args)
        assert not result["isError"], result
        assert server.store.phase() == "roundtable"
        row = server.store.connection.execute(
            "SELECT id, payload_json FROM events WHERE kind='phase-advanced' ORDER BY id DESC"
        ).fetchone()
        event = json.loads(row["payload_json"])
        lock = server.store.wave_product_lock()
        assert event["from"] == "plan" and event["to"] == "roundtable"
        assert event["by"] == lock["confirmedBy"] == (actor or "user")
        assert event["statement"] == lock["statement"] == statement
        assert lock["sourceRef"] == f"plan-event:{row['id']}"
        assert lock["spineRevision"] == event["plan_revision"] == before
        assert lock["spine"] == server.store.spine().to_dict()
        assert lock["mapNodeId"] == "MAP"
        assert lock["waveMapRevision"] == server.store.spine().revision("MAP")
        proxy = actor is not None and actor.startswith("proxy:")
        assert lock["isProxy"] is proxy
        assert lock["confirmationOrigin"] == ("proxy" if proxy else "user")
        projected = rpc(server, "tools/call", dict(name="harness_waves"))["result"]
        data = projected["structuredContent"]
        assert not projected["isError"] and data["waveProductLock"] == lock
        assert lock["digest"] == product_digest(
            server.store.spine(),
            data["mapNode"]["fields"],
            [node["fields"] for node in data["detailNodes"]],
        )
        assert [node["fields"]["detail"] for node in data["detailNodes"]] == ["detailed", "sketch"]
        assert server.store._meta()["locked_vision_json"] == vision_lock
    finally:
        server.close()
    with open_plan_store(workspace) as reopened:
        assert reopened.wave_product_lock() == lock


def test_mcp_bare_wave_advance_keeps_planner_and_no_historical_lock(tmp_path):
    server = mcp_wave_plan(tmp_path / "bare")
    try:
        assert server.store.outcome_waves()["waveProductLock"] is None
        result = confirmation_rpc(server, to_phase="roundtable")
        assert not result["isError"], result
        event = server.store._latest_draft_event("phase-advanced")
        assert event["by"] == "planner" and event["statement"] is None
        assert "waveProductLock" not in event
        assert server.store.outcome_waves()["waveProductLock"] is None
        server.close()
        projected = rpc(server, "tools/call", dict(name="harness_waves"))["result"]
        assert projected["structuredContent"]["waveProductLock"] is None
    finally:
        server.close()


@pytest.mark.parametrize(
    "arguments",
    [
        dict(by="user"),
        dict(by="proxy:synthetic"),
        dict(user_statement=""),
        dict(user_statement=" \r\n\t ", by="user"),
        dict(user_statement="synthetic", by="planner"),
        dict(user_statement="synthetic", by="proxy:"),
        dict(user_statement="synthetic", by="proxy:  "),
        dict(user_statement="synthetic", by="opus-user-proxy"),
        dict(user_statement="synthetic", by="worker:synthetic"),
        dict(user_statement=None),
        dict(user_statement=17),
        dict(user_statement="synthetic", by=None),
        dict(to_phase="tech-stack", user_statement="synthetic"),
        dict(to_phase="plan", user_statement="synthetic"),
    ],
)
def test_mcp_wave_confirmation_refuses_without_mutation(tmp_path, arguments):
    server = mcp_wave_plan(tmp_path / "refused")
    try:
        before = server.store.status()
        events = list(server.store.connection.execute("SELECT * FROM events"))
        args = dict(to_phase="roundtable") | arguments
        result = confirmation_rpc(server, **args)
        assert result["isError"], result
        assert server.store.status() == before
        assert list(server.store.connection.execute("SELECT * FROM events")) == events
        assert server.store.outcome_waves()["waveProductLock"] is None
    finally:
        server.close()


@pytest.mark.parametrize("gate,target", [("align", "expand"), ("vision-lock", "features")])
def test_mcp_advance_confirmation_cannot_substitute_vision_gate(server, gate, target):
    start(server)
    if gate == "vision-lock":
        native_confirm(server, "align", "Synthetic align")
        call(server, "harness_advance", to_phase="expand")
        call(server, "harness_advance", to_phase="vision-lock")
    before = server.store.status()
    events = list(server.store.connection.execute("SELECT * FROM events"))
    result = confirmation_rpc(server, to_phase=target, user_statement="Synthetic misplaced words")
    assert result["isError"], result
    assert server.store.status() == before
    assert list(server.store.connection.execute("SELECT * FROM events")) == events
    assert not server.store.connection.execute(
        "SELECT * FROM approvals WHERE gate=?", (gate,)
    ).fetchall()
