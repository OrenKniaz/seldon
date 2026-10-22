"""Compact card proof; synthetic source state is not human acceptance."""

from pathlib import Path

from codex_harness.core.plan_store import open_plan_store
from codex_harness.core.planner_confirmation_cards import compact_card
from codex_harness.core.planner_confirmations import prepare_confirmation

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"


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
        request = prepare_confirmation(store, "align")
        message = compact_card(request)
        assert len(message) < 1100 and len(message.splitlines()) == 6
        assert request["plan_revision"] in message
        assert "harness_show_node id=V1" in message
        assert "Summary only" in message and "Conditions" in message
        assert node["fields"]["scope_in"] in request["card"]
        assert request["vision"] == node
