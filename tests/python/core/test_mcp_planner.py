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
    assert "ONLY with the user's own words after they confirmed" in confirmation["description"]
    assert server.handle_line('{"jsonrpc":"2.0","method":"notifications/initialized"}') is None
    assert rpc(server, "ping")["result"] == {}
    assert rpc(server, "unknown")["error"]["code"] == -32601


def test_each_tool_and_all_eleven_phases_to_publication(server, tmp_path):
    seen = set()

    def run(tool_name, **args):
        seen.add(tool_name)
        return call(server, tool_name, **args)

    assert "Phase 1/11: Align" in run("harness_plan_start", idea="Split fairly", name="tip-split")
    assert "approval:align" in run("harness_plan_status")
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
    call(server, "harness_record_confirmation", gate="align", user_statement="Yes, that is right")
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
    call(server, "harness_record_confirmation", gate="align", user_statement="Yes")
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
