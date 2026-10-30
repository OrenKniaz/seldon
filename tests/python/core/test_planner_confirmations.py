"""Real store persistence for the leaf; native transport/human interaction is not tested."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from tests.python.core.planning_support import source_only_confirmation, source_only_expansion

from codex_harness.core.plan_store import PlanStoreError, open_plan_store
from codex_harness.core.planner_confirmations import complete_confirmation, prepare_confirmation

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"


def test_public_vision_lock_question_handles_accepted_cli_without_operation(tmp_path):
    """Protect draft-to-card handoff; full-operation card fixtures miss run13b's KeyError."""
    from tests.python.core.test_mcp_planner import workspace_wire

    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.semantic_schema import validate

    server = PlannerServer(tmp_path)
    started = workspace_wire(
        server,
        "harness_plan_start",
        idea="Count stdin words",
        name="TextMeter",
        plan_mode="semantic-v1",
        expected_workspace=str(tmp_path),
    )
    assert not started["isError"], started
    store = server.store
    source_only_expansion(store)
    source_only_confirmation(store, "align")
    store.advance("expand", by="source-test")
    node = store.spine().node("V1")
    node["fields"].update(
        planMode="semantic-v1",
        targets={"textmeter": {"module": "textmeter", "argv": []}},
        scenarios={
            "schema": "seldon/scenarios-v1",
            "scenarios": [
                {
                    "id": "S1",
                    "requirementRefs": ["R1"],
                    "contractRefs": ["D1"],
                    "setup": {"dirs": ["data/caller"]},
                    "actions": [
                        {
                            "id": "words",
                            "cwd": "data/caller",
                            "cli": {
                                "target": "textmeter",
                                "argv": [],
                                "stdin": {"utf8": "one\té\nthree  "},
                            },
                        }
                    ],
                    "observe": [
                        {
                            "action": "words",
                            "exitEquals": 0,
                            "stdoutJsonEquals": {"count": 3, "unit": "words"},
                            "stderrEquals": "",
                        }
                    ],
                    "verdict": "all",
                }
            ],
        },
    )
    try:
        for tool, args in [
            ("harness_upsert_node", {"node": node}),
            ("harness_advance", {"to_phase": "vision-lock"}),
        ]:
            result = workspace_wire(server, tool, expected_workspace=str(store.workspace), **args)
            assert not result["isError"], result["content"][0]["text"]
        revision, before = store.spine().plan_revision(), approvals(store)
        result = workspace_wire(
            server,
            "harness_prepare_chat_confirmation",
            gate="vision-lock",
            expected_workspace=str(store.workspace),
        )
        assert not result["isError"], result
        request = result["structuredContent"]
        assert "S1: words (Operation not recorded)" in request["card"]
        assert request["vision"] == node and request["plan_revision"] == revision
        saved = store._latest_draft_event("confirmation-requested")
        assert saved["vision"] == node and saved["plan_revision"] == revision
        assert saved["card"] == request["card"] and saved["request_id"] == request["request_id"]
        assert request["human_identity_verified"] is False and request["source_verified"] is False
        assert request["request_id"] in result["content"][0]["text"]
        assert store.spine().node("V1") == node and approvals(store) == before
        assert not store.status()["approval"]["granted"]
        # Displaying incomplete intent must not certify it for execution.
        with pytest.raises(ValueError, match=r"missing \['operation'\]"):
            validate(
                node["fields"]["scenarios"],
                tmp_path,
                node["fields"]["targets"],
                require_entries=False,
            )
    finally:
        server.close()


@pytest.fixture
def store(tmp_path):
    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        source_only_expansion(store)
        yield store


def answer(**updates):
    return {
        "approve": True,
        "statement": "Approve exactly this revision.\n",
        "conditions": "",
        **updates,
    }


def approvals(store):
    return [dict(row) for row in store.connection.execute("SELECT * FROM approvals")]


@pytest.mark.parametrize("gate", ["align", "vision-lock"])
def test_actual_run3_vision_is_excerpted_without_changing_saved_authority(tmp_path, gate):
    """Catch the real 7k character field size failure missed by the small scenario fixture."""
    from tests.python.core.test_mcp_planner import workspace_wire

    from codex_harness.core.mcp_planner import PlannerServer

    node = json.loads(
        (Path(__file__).parents[2] / "fixtures/tally-run3-vision.json").read_text(encoding="utf-8")
    )
    server = PlannerServer(tmp_path)
    try:
        for tool, arguments in [
            ("harness_plan_start", dict(idea=node["text"], name="Tally", plan_mode="semantic-v1")),
            ("harness_upsert_node", dict(node=node)),
        ]:
            result = workspace_wire(server, tool, expected_workspace=str(tmp_path), **arguments)
            assert not result["isError"], result
        store = server.store
        if gate == "vision-lock":
            source_only_confirmation(store, "align")
            store.advance("expand", by="source-test")
            store.advance("vision-lock", by="source-test")
        revision, before = store.spine().plan_revision(), approvals(store)
        result = workspace_wire(
            server, "harness_prepare_chat_confirmation", gate=gate, expected_workspace=str(tmp_path)
        )
        assert not result["isError"], result
        request = result["structuredContent"]
        prefix, body = result["content"][0]["text"].split("<<<SHOW-VERBATIM\n", 1)
        card, footer = body.split("\nSHOW-VERBATIM>>>", 1)
        (tmp_path / f"actual-{gate}-question.txt").write_text(request["card"], encoding="utf-8")
        assert len(card) <= 2500, (len(card), card)
        assert "do not summarise, shorten or add to it" in prefix
        assert "Full vision: template/VISION.md" in card
        assert card.endswith("Full scenarios: template/VISION.md and template/SCENARIOS.json")
        assert node["text"].replace("\r\n", "\n").split("\n\n", 1)[0] in card and "…" in card
        for key in ("scope_in", "scope_out", "quality_bar", "done_feels_like"):
            assert node["fields"][key][:100] in card
        for scenario in node["fields"]["scenarios"]["scenarios"]:
            assert any(line.startswith(scenario["id"] + ":") for line in card.splitlines())
        for upgrade in node["fields"]["expansion"]["upgrades"]:
            assert any(
                upgrade["id"] in line and upgrade["disposition"] in line and upgrade["text"] in line
                for line in card.splitlines()
            )
        assert "exitEquals" not in card and "fileJsonEquals" not in card
        assert request["request_id"] in footer and revision in footer
        assert "quote their latest actual chat message verbatim" in footer
        saved = store._latest_draft_event("confirmation-requested")
        assert saved["vision"] == store.spine().node(node["id"]) == node
        assert saved["card"] == request["card"] and saved["request_id"] == request["request_id"]
        assert saved["plan_revision"] == revision == store.spine().plan_revision()
        assert approvals(store) == before and not store.status()["approval"]["granted"]
    finally:
        server.close()


@pytest.mark.parametrize("prefix", ["Complete sentence. ", ""])
def test_excerpt_cuts_at_sentence_or_word_boundary_and_keeps_full_words(store, prefix):
    """Catch partial-word cuts and lost scope; retained run3 has a short first paragraph."""
    node = store.spine().node("V1")
    text = prefix + "wholeword " * 100
    node["text"] = text + "\n\nSecond paragraph must remain stored."
    node["fields"]["scope_in"] = text
    store.upsert_node(node)
    request = prepare_confirmation(store, "align")
    card = request["card"]
    vision = card.split("Vision:\n", 1)[1].split("\nFull vision:", 1)[0]
    scope = card.split("Scope in:\n", 1)[1].split("\nFull vision:", 1)[0]
    for actual, limit in ((vision, 600), (scope, 300)):
        assert len(actual) <= limit and actual.endswith("…")
        assert actual[:-1] == (prefix.strip() if prefix else " ".join(actual[:-1].split()))
        assert text.startswith(actual[:-1])
        if not prefix:
            assert actual.endswith("wholeword…")
    assert request["vision"] == store.spine().node("V1") == node
    assert approvals(store) == []


@pytest.mark.parametrize("gate", ["align", "vision-lock"])
def test_six_scenario_question_is_short_complete_and_revision_bound(store, gate):
    """Catch full scenario data overwhelming chat; existing card fixtures have no scenarios."""
    from tests.python.core.test_mcp_planner import workspace_wire

    from codex_harness.core.mcp_planner import PlannerServer

    node = store.spine().node("V1")
    node.update(title="Tally", text="Track local expenses exactly through a small offline CLI.")
    intents = [
        "read_empty_ledger",
        "add_and_list_records",
        "total_exact_decimals",
        "filter_by_category",
        "reject_invalid_amounts",
        "preserve_corrupt_data",
    ]
    node["fields"].update(
        users="One local user",
        first_user_moment="Add lunch and read the total",
        scope_in="Add, list, total and category filters with local JSON storage",
        scope_out="No networking, GUI or currency conversion",
        scenarios={
            "schema": "seldon/scenarios-v1",
            "scenarios": [
                dict(
                    id=f"S{i}",
                    requirementRefs=["R1"],
                    contractRefs=["CLI"],
                    setup={"files": [{"path": "data/ledger.json", "utf8": "[]"}]},
                    actions=[
                        dict(
                            id=intent,
                            cli=dict(
                                target="tally",
                                operation="list",
                                argv=["--db", {"path": "data/ledger.json"}, "list"],
                            ),
                        )
                    ],
                    observe=[
                        dict(action=intent, exitEquals=0, stdoutEquals="[]\n"),
                        {"path": "data/ledger.json", "fileJsonEquals": []},
                    ],
                    verdict="all",
                )
                for i, intent in enumerate(intents, 1)
            ],
        },
    )
    server = PlannerServer(store.workspace)
    try:
        result = workspace_wire(
            server, "harness_upsert_node", node=node, expected_workspace=str(store.workspace)
        )
        assert not result["isError"], result
        if gate == "vision-lock":
            # Source-only prerequisite response; the tested question remains unanswered.
            source_only_confirmation(store, "align")
            store.advance("expand", by="source-test")
            store.advance("vision-lock", by="source-test")
        revision = store.spine().plan_revision()
        before = approvals(store)
        result = workspace_wire(
            server,
            "harness_prepare_chat_confirmation",
            gate=gate,
            expected_workspace=str(store.workspace),
        )
        assert not result["isError"], result
        request = result["structuredContent"]
        text = result["content"][0]["text"]
        prefix, body = text.split("<<<SHOW-VERBATIM\n", 1)
        card, footer = body.split("\nSHOW-VERBATIM>>>", 1)
        (store.workspace / f"{gate}-question.txt").write_text(text, encoding="utf-8")
        assert len(card) <= 2500, (len(card), card)
        assert "do not summarise, shorten or add to it" in prefix
        assert card.endswith("Full scenarios: template/VISION.md and template/SCENARIOS.json")
        for key in (
            "scope_in",
            "scope_out",
            "quality_bar",
            "done_feels_like",
        ):
            assert node["fields"][key] in card
        assert node["text"] in card
        for i, intent in enumerate(intents, 1):
            assert any(
                f"S{i}" in line and intent.replace("_", " ") in line for line in card.splitlines()
            )
        for upgrade in node["fields"]["expansion"]["upgrades"]:
            assert any(
                all(upgrade[key] in line for key in ("id", "text", "disposition"))
                for line in card.splitlines()
            )
        assert "exitEquals" not in card and "fileJsonEquals" not in card
        assert request["request_id"] in footer and revision in footer
        assert "quote their latest actual chat message verbatim" in footer
        saved = store._latest_draft_event("confirmation-requested")
        assert {**saved, "source_verified": False, "human_identity_verified": False} == request
        assert saved["vision"] == node
        assert request["plan_revision"] == store.spine().plan_revision() == revision
        assert approvals(store) == before and not store.status()["approval"]["granted"]
    finally:
        server.close()


def test_incomplete_expansion_stays_readable_without_consent(store):
    """Catch an Align crash on missing upgrade words; the existing gap test removes expansion."""
    node = store.spine().node("V1")
    del node["fields"]["expansion"]["upgrades"][0]["impact"]
    store.upsert_node(node)
    request = prepare_confirmation(store, "align")
    assert "Not recorded" in request["card"]
    assert "Upgrade 1" in request["card"]
    assert request["vision"] == node and approvals(store) == []


def test_missing_expansion_refuses_entry_without_changing_phase_or_credit(store):
    """Catch the unguarded Expand exit; existing tests only guard approval gates."""
    node = store.spine().node("V1")
    del node["fields"]["expansion"]
    store.upsert_node(node)
    source_only_confirmation(store, "align")
    store.advance("expand", by="planner")
    before = database_records(store)
    with pytest.raises(PlanStoreError, match="Expansion required before Vision Lock"):
        store.advance("vision-lock", by="planner")
    assert store.phase() == "expand" and database_records(store) == before


def test_whole_lock_displays_expansion_and_confirms_one_revision(store):
    """Catch undisplayed expansion choices; Align card tests miss whole-lock scope."""
    node = store.spine().node("V1")
    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.planner_vision import expansion_status

    server = PlannerServer(store.workspace)
    try:
        assert not server.call_tool("harness_upsert_node", {"node": node})["isError"]
    finally:
        server.close()
    source_only_confirmation(store, "align")
    store.advance("expand", by="planner")
    store.advance("vision-lock", by="planner")
    request = prepare_confirmation(store, "vision-lock")
    from codex_harness.core.planner_confirmation_cards import compact_card

    assert request["card"] in compact_card(request)
    assert "proposed dispositions" in request["card"]
    for words in ("quality", "Retain every cent", "Shares add up"):
        assert words in request["card"]
    for upgrade in node["fields"]["expansion"]["upgrades"]:
        assert all(upgrade[key] in request["card"] for key in ("id", "text", "disposition"))
    assert not store.status()["approval"]["granted"]
    with open_plan_store(store.workspace) as reopened:
        result = complete_confirmation(reopened, request["request_id"], "accept", answer())
        assert result["state"] == "approved"
        reopened.advance("features", by="planner")
        assert reopened.phase() == "features"
        assert expansion_status(reopened.spine().node("V1"), downstream_nodes=[])[
            "untracedAcceptedUpgradeRefs"
        ] == ["1"]
        decision = reopened.spine().node("D1")
        decision["fields"]["expansionRefs"] = ["1"]
        reopened.upsert_node(decision)
        assert (
            expansion_status(reopened.spine().node("V1"), downstream_nodes=[decision])[
                "untracedAcceptedUpgradeRefs"
            ]
            == []
        )
        assert reopened.spine().node("V1") == node


def recorded_reviewer(store, *, catalog_update=None):
    """Supplied source-only observation; never actual account discovery/entitlement."""
    from tests.python.core.test_plan_store import mock_catalog, mock_choice

    catalog = mock_catalog()
    if catalog_update:
        catalog_update(catalog)
    current = store.record_reviewer_catalog(catalog, store.spine().plan_revision())["catalog"]
    node = mock_choice(current)
    result = store.record_reviewer_selection(node, store.spine().plan_revision())
    return current, node, result


def database_records(store):
    return list(store.connection.iterdump())


@pytest.mark.parametrize("elapsed", [599, 601])
def test_native_align_reading_window_accepts_late_response_but_not_expired(
    tmp_path, monkeypatch, elapsed
):
    """Protect reading time through actual protocol and durable gate completion.

    Existing lifetime tests force an expired internal deadline; they miss the
    default cancelling a correlated answer after two minutes of reading.
    This virtual client does not prove the desktop host waits ten minutes.
    """
    from tests.python.core.test_mcp_planner import answer_native, begin_native, start

    from codex_harness.core import mcp_planner

    clock = [1000.0]
    monkeypatch.setattr(mcp_planner.time, "monotonic", lambda: clock[0])
    server = mcp_planner.PlannerServer(tmp_path)
    try:
        start(server)
        revision = server.store.spine().plan_revision()
        outbound = begin_native(server)
        request_id = server._pending_confirmation["request"]["request_id"]
        clock[0] += elapsed
        result = answer_native(server, outbound["id"], action="accept", **answer())["result"]
        assert server.store.spine().plan_revision() == revision
        assert result["isError"] is (elapsed > 600)
        assert result["structuredContent"]["state"] == ("pending" if elapsed > 600 else "approved")
        assert result["structuredContent"]["request_id"] == request_id
    finally:
        server.close()
    with open_plan_store(tmp_path) as reopened:
        rows = approvals(reopened)
        if elapsed > 600:
            assert rows == []
            with pytest.raises(PlanStoreError):
                reopened.advance("expand", by="planner")
        else:
            assert len(rows) == 1 and rows[0]["statement"] == answer()["statement"]
            assert rows[0]["by"] == "mcp-client-elicitation"
            reopened.advance("expand", by="planner")


@pytest.mark.parametrize(
    "updates",
    [
        {"approve": False},
        {"approve": "true"},
        {"approve": 1},
        {"statement": " \n\t"},
        {"statement": None},
        {"conditions": "Only if availability later becomes known"},
        {"conditions": None},
    ],
)
def test_ordinary_reviewer_action_refuses_without_writes_or_native_credit(store, updates):
    _, node, selected = recorded_reviewer(store)
    before = database_records(store)
    with pytest.raises(PlanStoreError) as error:
        store.confirm_reviewer_selection(node["id"], selected["spineRevision"], **answer(**updates))
    assert error.value.code == "plan-reviewer-confirmation-action-invalid"
    assert database_records(store) == before
    assert store.spine().node(node["id"])["fields"]["confirmation"] is None
    assert approvals(store) == []


@pytest.mark.parametrize(
    "field",
    [
        "approvalId",
        "eventId",
        "sourceRef",
        "authorizedScopeDigest",
        "origin",
        "by",
        "human_identity_verified",
    ],
)
def test_ordinary_reviewer_action_rejects_caller_authority_injection(store, field):
    _, node, selected = recorded_reviewer(store)
    before = database_records(store)
    with pytest.raises(TypeError, match=field):
        store.confirm_reviewer_selection(
            node["id"], selected["spineRevision"], **answer(), **{field: "caller-injected"}
        )
    assert database_records(store) == before
    assert approvals(store) == []


@pytest.mark.parametrize(
    "case,code",
    [
        ("stale-revision", "plan-spine-stale"),
        ("edited-proposal", "plan-reviewer-confirmation-target-stale"),
        ("wrong-schema", "plan-reviewer-confirmation-target-stale"),
        ("wrong-target", "plan-reviewer-confirmation-target-invalid"),
        ("replaced-catalog", "reviewer-catalog-stale"),
    ],
)
def test_ordinary_reviewer_action_rechecks_recorded_preimage_and_current_catalog(store, case, code):
    from tests.python.core.test_plan_store import mock_catalog

    _, node, selected = recorded_reviewer(store)
    revision, target = selected["spineRevision"], node["id"]
    if case == "stale-revision":
        vision = store.spine().node("V1")
        vision["title"] += " changed"
        store.upsert_node(vision)
    elif case in ("edited-proposal", "wrong-schema"):
        proposal = store.spine().node(target)
        if case == "edited-proposal":
            proposal["fields"]["recommendation"]["reason"] += " changed"
        else:
            proposal["fields"]["schema"] = "dot-intake-v1"
        store.upsert_node(proposal)
        revision = store.spine().plan_revision()
    elif case == "wrong-target":
        target = "V1"
    else:
        catalog = mock_catalog()
        catalog["id"] = "RC-00000000-0000-0000-0000-000000000002"
        store.record_reviewer_catalog(catalog, revision)
        assert store.spine().plan_revision() == revision
    before = database_records(store)
    with pytest.raises(ValueError) as error:
        store.confirm_reviewer_selection(target, revision, **answer())
    assert getattr(error.value, "code", str(error.value).split(":")[0]) == code
    assert database_records(store) == before
    assert approvals(store) == []


def test_ordinary_reviewer_confirmation_replay_and_event_node_failure_are_atomic(
    store, monkeypatch
):
    _, node, selected = recorded_reviewer(store)
    before = database_records(store)
    save = store._save_draft_nodes

    def fail_after_node(*args, **kwargs):
        save(*args, **kwargs)
        raise PlanStoreError("injected-node-failure", "Rollback event and node together")

    monkeypatch.setattr(store, "_save_draft_nodes", fail_after_node)
    with pytest.raises(PlanStoreError) as error:
        store.confirm_reviewer_selection(node["id"], selected["spineRevision"], **answer())
    assert error.value.code == "injected-node-failure"
    assert database_records(store) == before
    monkeypatch.setattr(store, "_save_draft_nodes", save)
    confirmed = store.confirm_reviewer_selection(node["id"], selected["spineRevision"], **answer())
    before = database_records(store)
    for revision, code in (
        (selected["spineRevision"], "plan-spine-stale"),
        (confirmed["spineRevision"], "plan-reviewer-confirmation-already-recorded"),
    ):
        with pytest.raises(PlanStoreError) as error:
            store.confirm_reviewer_selection(node["id"], revision, **answer())
        assert error.value.code == code
        assert database_records(store) == before
    assert (
        len(
            list(
                store.connection.execute(
                    "SELECT id FROM events WHERE kind='reviewer-selection-confirmed'"
                )
            )
        )
        == 1
    )
    assert approvals(store) == []
    # Removing the derived attachment cannot manufacture another record for the
    # same canonical proposal; a genuinely edited proposal needs its own scope.
    reset = store.spine().node(node["id"])
    reset["fields"]["confirmation"] = None
    selected_again = store.record_reviewer_selection(reset, store.spine().plan_revision())
    before = database_records(store)
    with pytest.raises(PlanStoreError) as error:
        store.confirm_reviewer_selection(node["id"], selected_again["spineRevision"], **answer())
    assert error.value.code == "plan-reviewer-confirmation-already-recorded"
    assert database_records(store) == before


@pytest.mark.parametrize(
    "change", ["node-identity", "proposal", "sessions", "authority", "catalog"]
)
def test_generated_reviewer_credit_cannot_follow_changed_scope(store, change):
    from tests.python.core.test_plan_store import mock_catalog

    catalog, node, selected = recorded_reviewer(store)
    store.confirm_reviewer_selection(node["id"], selected["spineRevision"], **answer())
    proposal = deepcopy(store.spine().node(node["id"]))
    sessions = {"implementationSessionIds": [], "reviewerSessionIds": {}}
    if change == "node-identity":
        proposal["id"] = "OTHER"
    elif change == "proposal":
        proposal["fields"]["recommendation"]["reason"] += " changed"
    elif change == "sessions":
        sessions["reviewerSessionIds"]["primary"] = "new-simulated-session"
    elif change == "authority":
        proposal["fields"]["confirmation"]["statement"] = "Injected replacement"
    else:
        replacement = mock_catalog()
        replacement["id"] = "RC-00000000-0000-0000-0000-000000000002"
        store.record_reviewer_catalog(replacement, store.spine().plan_revision())
    before = database_records(store)
    with pytest.raises(ValueError) as error:
        store.record_reviewer_selection(
            proposal, store.spine().plan_revision(), author_sessions=sessions
        )
    assert getattr(error.value, "code", str(error.value).split(":")[0]) == (
        "reviewer-catalog-stale"
        if change == "catalog"
        else "plan-reviewer-confirmation-scope-stale"
    )
    assert database_records(store) == before
    assert approvals(store) == []
    if change == "catalog":
        projected = store.outcome_waves()["reviewerSelection"]
        assert projected["state"] == "stale"
        assert "selection" not in projected
    assert catalog["digest"] == node["fields"]["catalogDigest"]


@pytest.mark.parametrize("unknown", ["availability", "authentication", "review-path"])
def test_ordinary_reviewer_confirmation_clears_only_its_own_hold(store, unknown):
    def update(catalog):
        provider = catalog["providers"][0]
        if unknown == "availability":
            provider["choices"][0]["availability"] = "unknown"
        elif unknown == "authentication":
            provider["authentication"] = "unknown"
            provider["choices"][0]["availability"] = "unknown"
        else:
            provider["choices"][0]["reviewPathSupported"] = False

    _, node, selected = recorded_reviewer(store, catalog_update=update)
    result = store.confirm_reviewer_selection(node["id"], selected["spineRevision"], **answer())
    assert result["unmetRequirements"] == [
        hold
        for hold in selected["unmetRequirements"]
        if "reviewer-confirmation-required" not in hold
    ]
    assert len(result["unmetRequirements"]) >= 3
    assert approvals(store) == []
    assert result["approvalGranted"] is False
    assert result["confirmationRecord"]["human_identity_verified"] is False


@pytest.mark.parametrize("change", ["proposal", "sessions", "catalog"])
def test_current_reviewer_assurance_read_rechecks_generated_scope(store, change):
    """Persist a changed current draft through the existing internal writer; read must hold.

    Deliberately exercise the consumer beyond public recorder's earlier refusal.
    These supplied observations and edits are source fixtures, not actual discovery.
    """
    from tests.python.core.test_plan_store import mock_catalog

    _, node, selected = recorded_reviewer(store)
    store.confirm_reviewer_selection(node["id"], selected["spineRevision"], **answer())
    original = store.spine().node(node["id"])
    if change == "catalog":
        replacement = mock_catalog()
        replacement["id"] = "RC-00000000-0000-0000-0000-000000000002"
        revision = store.spine().plan_revision()
        store.record_reviewer_catalog(replacement, revision)
        assert store.spine().plan_revision() == revision
    else:
        proposal = deepcopy(original)
        sessions = {"implementationSessionIds": [], "reviewerSessionIds": {}}
        if change == "proposal":
            proposal["fields"]["recommendation"]["reason"] += " changed after confirmation"
        else:
            sessions["reviewerSessionIds"]["primary"] = "new-supplied-session"
        current_revision = store.spine().plan_revision()
        with store._transaction():
            before = store._draft_preimage(current_revision)
            store._save_draft_nodes(
                before,
                [proposal],
                "reviewer-selection-recorded",
                {"nodeId": node["id"], "authorSessions": sessions},
            )
        current = store.spine()
        event = store._latest_draft_event("reviewer-selection-recorded")
        assert event["nodeRevisions"][node["id"]] == current.revision(node["id"])
    assert (
        store.spine().node(node["id"])["fields"]["confirmation"]
        == original["fields"]["confirmation"]
    )
    before_read = database_records(store)
    projected = store.outcome_waves()["reviewerSelection"]
    assert projected["state"] == "stale"
    assert "selection" not in projected and "independence" not in projected
    assert projected["unmetRequirements"]
    assert database_records(store) == before_read
    assert approvals(store) == []


def test_request_card_and_response_survive_reopen_exactly(store):
    node = store.spine().node("V1")
    node["fields"].update(
        vision="A readable bill splitter",
        users="People sharing dinner",
        first_user_moment="See the exact shares",
        scope_in="Complete long scope " * 90,
        scope_out="No foreign currencies",
        quality_bar="Every cent is retained",
        done_feels_like="Everyone knows what to pay",
        assumptions=["Only dollars", "Tip is a whole percent"],
        customMaterialDetail={"boundaryCondition": "Preserve this full custom detail"},
    )
    store.upsert_node(node)
    request = prepare_confirmation(store, "align")
    assert request["vision"] == node
    for label in ("Vision", "Scope in", "Scope out", "Quality", "Done"):
        assert f"{label}:\n" in request["card"]
    for key in ("scope_out", "quality_bar", "done_feels_like"):
        assert node["fields"][key] in request["card"]
    assert node["fields"]["scope_in"] not in request["card"]
    assert "Full vision: template/VISION.md" in request["card"]
    # Complete words/aliases/custom details stay in the saved authority, not the excerpt.
    assert request["vision"] == node
    assert '"fields"' not in request["card"]
    assert '"kind"' not in request["card"]
    assert "requirementFiles" not in request["card"]
    assert request["plan_revision"] == store.spine().plan_revision()
    with open_plan_store(store.workspace) as reopened:
        saved = json.loads(
            reopened.connection.execute(
                "SELECT payload_json FROM events WHERE kind='confirmation-requested'"
            ).fetchone()[0]
        )
        assert saved == request
        result = complete_confirmation(reopened, request["request_id"], "accept", answer())
    assert result["state"] == "approved"
    assert result["human_identity_verified"] is False
    assert approvals(store)[0]["statement"] == answer()["statement"]
    assert approvals(store)[0]["by"] == "mcp-client-elicitation"
    store.advance("expand", by="planner")
    with pytest.raises(PlanStoreError) as caught:
        complete_confirmation(store, request["request_id"], "accept", answer())
    assert caught.value.code == "plan-confirmation-already-consumed"


@pytest.mark.parametrize("change", ["revision", "gate", "phase-roundtrip", "new-display"])
def test_stale_card_cannot_grant_gate_credit(store, change):
    request = prepare_confirmation(store, "align")
    if change == "revision":
        node = store.spine().node("V1")
        node["text"] += " Changed scope."
        store.upsert_node(node)
    elif change in ("gate", "phase-roundtrip"):
        source_only_confirmation(store, "align")
        store.advance("expand", by="planner")
        if change == "gate":
            store.advance("vision-lock", by="planner")
        else:
            store.advance("align", by="planner")
    else:
        prepare_confirmation(store, "align")
    before = approvals(store)
    result = complete_confirmation(store, request["request_id"], "accept", answer())
    assert result["state"] == "stale"
    assert result["approval_id"] is None
    assert approvals(store) == before
    with pytest.raises(PlanStoreError) as caught:
        complete_confirmation(store, request["request_id"], "accept", answer())
    assert caught.value.code == "plan-confirmation-already-consumed"


@pytest.mark.parametrize(
    "action,content,state",
    [
        ("accept", answer(conditions="But first show the reviewer digest.\n"), "conditional"),
        ("accept", answer(conditions=" "), "conditional"),
        ("accept", answer(approve=False), "declined"),
        ("decline", None, "decline"),
        ("cancel", None, "cancel"),
    ],
)
def test_nonapproval_preserves_complete_response_without_credit(store, action, content, state):
    request = prepare_confirmation(store, "align")
    result = complete_confirmation(store, request["request_id"], action, content)
    assert result["state"] == state
    assert result["content"] == content
    assert approvals(store) == []
    saved = json.loads(
        store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='confirmation-responded'"
        ).fetchone()[0]
    )
    assert saved == result


@pytest.mark.parametrize(
    "content",
    [
        answer(approve="true"),
        answer(statement=" "),
        answer(conditions=None),
        {**answer(), "verified": True},
    ],
)
def test_malformed_or_asserted_origin_does_not_create_approval(store, content):
    request = prepare_confirmation(store, "align")
    with pytest.raises(PlanStoreError) as caught:
        complete_confirmation(store, request["request_id"], "accept", content)
    assert caught.value.code == "plan-confirmation-response-invalid"
    assert approvals(store) == []


def test_unknown_id_and_wrong_gate_refuse(store):
    with pytest.raises(PlanStoreError) as caught:
        complete_confirmation(store, "invented-user-message", "accept", answer())
    assert caught.value.code == "plan-confirmation-request-unknown"
    with pytest.raises(PlanStoreError) as caught:
        prepare_confirmation(store, "vision-lock")
    assert caught.value.code == "plan-confirmation-gate-mismatch"
    assert approvals(store) == []


def test_response_and_approval_commit_atomically(store, monkeypatch):
    request = prepare_confirmation(store, "align")
    original = store._event

    def fail_response(kind, payload):
        if kind == "confirmation-responded":
            raise RuntimeError("simulated persistence failure")
        original(kind, payload)

    monkeypatch.setattr(store, "_event", fail_response)
    with pytest.raises(RuntimeError, match="persistence failure"):
        complete_confirmation(store, request["request_id"], "accept", answer())
    assert approvals(store) == []
    assert (
        store.connection.execute("SELECT count(*) FROM events WHERE kind='approved'").fetchone()[0]
        == 0
    )


@pytest.mark.parametrize("action", ["pending", "conditional", "decline", "cancel"])
def test_new_display_cannot_fall_back_to_prior_native_credit(store, action):
    source_only_confirmation(store, "align")
    assert store.status()["approval"]["granted"]
    request = prepare_confirmation(store, "align")
    if action == "conditional":
        complete_confirmation(
            store,
            request["request_id"],
            "accept",
            answer(
                statement="Approve this, but first show the reviewer digest.",
                conditions="First show the reviewer digest.",
            ),
        )
    elif action != "pending":
        complete_confirmation(store, request["request_id"], action)
    assert not store.status()["approval"]["granted"]
    with pytest.raises(PlanStoreError):
        store.advance("expand", by="planner")
    assert len(approvals(store)) == 1


def test_later_phase_vision_renewal_preserves_scope_conditions_and_evidence(store):
    from tests.python.core.planning_support import source_only_vision_lock

    source_only_vision_lock(store)
    store.advance("features", by="planner")
    store.advance("requirements", by="planner")
    node = store.spine().node("V1")
    node["text"] += " Additional scope."
    store.upsert_node(node)
    request = prepare_confirmation(store, "vision-lock")
    assert request["phase"] == "requirements"
    assert "Renew vision lock during requirements" in request["card"]
    conditional = answer(
        statement="Yes. But first show the digest.\n", conditions="But first show the digest.\n"
    )
    result = complete_confirmation(store, request["request_id"], "accept", conditional)
    assert result["content"] == conditional and result["state"] == "conditional"
    assert not store.status()["approval"]["granted"]
    with pytest.raises(PlanStoreError):
        store.advance("tech-stack", by="planner")
    before = store.spine().to_dict()
    source_only_confirmation(
        store, "vision-lock", statement="I now approve the full displayed scope."
    )
    assert store.spine().to_dict() == before
    store.advance("tech-stack", by="planner")
    assert store.status()["approval"]["granted"]
