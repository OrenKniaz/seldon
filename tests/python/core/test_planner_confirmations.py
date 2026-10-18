"""Real store persistence for the leaf; native transport/human interaction is not tested."""

import json
from pathlib import Path

import pytest

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
    node["fields"]["scope_in"] = "Complete long scope " * 90
    store.upsert_node(node)
    request = prepare_confirmation(store, "align")
    assert json.loads(request["card"].split("\n\n", 1)[1]) == node
    assert request["plan_revision"] == store.spine().plan_revision()
    with open_plan_store(store.workspace) as reopened:
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
        # Historical supplied approval is setup, not native-user evidence.
        store.advance("expand", by="operator", statement="Legacy setup")
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
