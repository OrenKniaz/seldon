"""Durable recovery proof; synthetic client responses are not human acceptance."""

from pathlib import Path

import pytest

from codex_harness.core.plan_store import PlanStoreError, open_plan_store
from codex_harness.core.planner_confirmation_cards import (
    compact_card,
    pending_confirmation,
    pending_outcome,
    resume_or_prepare_confirmation,
)
from codex_harness.core.planner_confirmations import complete_confirmation

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"
ANSWER = {"approve": True, "statement": "Synthetic client response.\n", "conditions": ""}


def test_pending_display_survives_reopen_and_approves_once(tmp_path):
    """Protect resume: old tests consumed every timeout, never resumed unanswered state."""
    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        request = resume_or_prepare_confirmation(store, "align")
        assert pending_outcome(request)["state"] == "pending"
        assert not store.status()["approval"]["granted"]
    with open_plan_store(tmp_path) as store:
        assert resume_or_prepare_confirmation(store, "align") == request
        assert (
            store.connection.execute(
                "SELECT count(*) FROM events WHERE kind='confirmation-requested'"
            ).fetchone()[0]
            == 1
        )
        result = complete_confirmation(store, request["request_id"], "accept", ANSWER)
        assert result["state"] == "approved"
        assert result["content"] == ANSWER
        assert result["human_identity_verified"] is False
        store.advance("expand", by="planner")
        with pytest.raises(PlanStoreError) as caught:
            complete_confirmation(store, request["request_id"], "accept", ANSWER)
        assert caught.value.code == "plan-confirmation-already-consumed"


def test_resume_replaces_changed_scope_and_rejects_old_response(tmp_path):
    """Protect scope binding: prior stale tests did not cover the resume selector."""
    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        old = resume_or_prepare_confirmation(store, "align")
        node = store.spine().node("V1")
        node["text"] += " Material new scope."
        store.upsert_node(node)
        assert pending_confirmation(store, "align") is None
        current = resume_or_prepare_confirmation(store, "align")
        assert current["request_id"] != old["request_id"]
        assert complete_confirmation(store, old["request_id"], "accept", ANSWER)["state"] == "stale"
        assert not store.status()["approval"]["granted"]
        assert pending_confirmation(store, "align") == current


def test_explicit_cancel_is_final_and_failed_prepare_rolls_back(tmp_path, monkeypatch):
    """Protect user cancel/atomicity: resume must not revive consumed or partial cards."""
    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        old = resume_or_prepare_confirmation(store, "align")
        complete_confirmation(store, old["request_id"], "cancel")
        assert pending_confirmation(store, "align") is None
        original = store._event

        def fail(kind, payload):
            original(kind, payload)
            raise RuntimeError("injected persistence failure")

        monkeypatch.setattr(store, "_event", fail)
        with pytest.raises(RuntimeError, match="persistence failure"):
            resume_or_prepare_confirmation(store, "align")
        assert pending_confirmation(store, "align") is None
        assert (
            store.connection.execute(
                "SELECT count(*) FROM events WHERE kind='confirmation-requested'"
            ).fetchone()[0]
            == 1
        )


def test_compact_card_bounds_long_content_without_discarding_full_scope(tmp_path):
    """Protect reachable controls: existing card tests require full unbounded detail."""
    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        node = store.spine().node("V1")
        node["fields"].update(
            vision="Long vision\n" * 1000,
            users="Long users\n" * 1000,
            scope_in="Full scope\n" * 1000,
            done_feels_like="Done\n" * 1000,
        )
        store.upsert_node(node)
        request = resume_or_prepare_confirmation(store, "align")
        message = compact_card(request)
        assert len(message) < 1100 and len(message.splitlines()) == 6
        assert request["plan_revision"] in message
        assert "harness_show_node id=V1" in message
        assert "Summary only" in message and "Conditions" in message
        assert node["fields"]["scope_in"] in request["card"]
        assert request["vision"] == node
