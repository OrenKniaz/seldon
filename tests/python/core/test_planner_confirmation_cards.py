"""The complete visible subject is authority, in either channel."""

from tests.python.core import test_planner_confirmations

from codex_harness.core.planner_confirmation_cards import compact_card
from codex_harness.core.planner_confirmations import prepare_confirmation

store = test_planner_confirmations.store


def test_native_display_is_the_complete_persisted_subject(store):
    """Catch excerpt-only authority; V1 card tests intentionally required truncated display."""
    vision = store.spine().node("V1")
    vision["fields"]["scope_in"] = ["Complete scope words " * 200]
    store.upsert_node(vision)
    request = prepare_confirmation(store, "align+vision-lock")
    assert compact_card(request) == request["card"]
    assert vision["fields"]["scope_in"][0] in request["card"]
    assert request["subject"]["effect"] in request["card"]
