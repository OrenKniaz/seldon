"""Real store persistence for the leaf; native transport/human interaction is not tested."""

import json
from pathlib import Path

import pytest
from tests.python.core.planning_support import source_only_confirmation

from codex_harness.core.plan_store import PlanStoreError, open_plan_store
from codex_harness.core.planner_confirmations import complete_confirmation, prepare_confirmation

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"


@pytest.fixture
def store(tmp_path):
    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
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
    for label in (
        "Vision",
        "Users",
        "First moment",
        "Scope in",
        "Scope out",
        "Quality",
        "Done",
        "Assumptions",
    ):
        assert f"{label}:\n" in request["card"]
    for key in (
        "vision",
        "users",
        "first_user_moment",
        "scope_in",
        "scope_out",
        "quality_bar",
        "done_feels_like",
    ):
        assert node["fields"][key] in request["card"]
    assert node["text"] in request["card"]
    assert "- Only dollars\n- Tip is a whole percent" in request["card"]
    assert "Boundary condition:\nPreserve this full custom detail" in request["card"]
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
