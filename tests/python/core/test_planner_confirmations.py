"""V2 replaces V1 revision/gate credits and selection-specific consent tests.

Behavior is tested through canonical producers and consumers. Forms and declared
actors are source fixtures; no human authentication or provider execution proof.
"""

from pathlib import Path

import pytest
from tests.python.core.planning_support import source_only_expansion

from codex_harness.core import approvals
from codex_harness.core import planner_confirmations as confirmation
from codex_harness.core.plan_store import open_plan_store

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"


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


@pytest.fixture
def store(tmp_path):
    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        source_only_expansion(store)
        yield store


def answer(**updates):
    return dict(approve=True, statement="Approve", conditions="", **updates)


def decide(store, gate="align+vision-lock", *, action="accept", content=None, **scope):
    request = confirmation.prepare_confirmation(store, gate, **scope)
    result = confirmation.complete_confirmation(
        store, request["request_id"], action, content or answer()
    )
    return request, result


@pytest.mark.parametrize("field", ["scope_in", "quality_bar", "done_feels_like", "expansion"])
def test_changed_scope_requires_a_and_displays_field_diff(store, field):
    """
    Catch a stale semantic lock reused after intent edits; detail-only tests miss scope
    changes.
    """
    decide(store)
    original = store.spine().node("V1")
    vision = store.spine().node("V1")
    if field == "expansion":
        vision["fields"][field]["upgrades"][0]["disposition"] = "reject"
    else:
        vision["fields"][field] = "Changed accepted intent"
    store.upsert_node(vision, reopen_vision_lock=True)
    with pytest.raises(ValueError) as refusal:
        store.advance("expand", by="source-test")
    assert field in str(refusal.value)
    request = confirmation.prepare_confirmation(store, "align+vision-lock")
    assert any(field in d["field"] for d in request["diff"])
    assert request["subject"]["effect"] in request["card"]
    store.upsert_node(original, reopen_vision_lock=True)
    assert not store.status()["vision_lock_stale"]
    assert approvals.require_approval(store, "A")
    store.advance("expand", by="source-test")


@pytest.mark.parametrize(
    "action,content,state",
    [
        ("decline", None, "decline"),
        ("cancel", None, "cancel"),
        ("accept", {"approve": False, "statement": "No", "conditions": ""}, "declined"),
        ("accept", {"approve": True, "statement": "No", "conditions": ""}, "declined"),
        (
            "accept",
            {"approve": True, "statement": "Approve if changed", "conditions": ""},
            "changes-requested",
        ),
        (
            "accept",
            {"approve": True, "statement": "Approve", "conditions": " "},
            "changes-requested",
        ),
    ],
)
def test_explicit_nonapproval_supersedes_prior_consent(store, action, content, state):
    """Catch historical fallback after refusal; offers alone grant no authority in V2."""
    decide(store)
    offered = confirmation.prepare_confirmation(store, "align+vision-lock")
    assert approvals.require_approval(store, "A")
    result = confirmation.complete_confirmation(store, offered["request_id"], action, content)
    assert result["state"] == state and result["approval_id"] is None
    with pytest.raises(ValueError):
        store.advance("expand", by="source-test")
    with pytest.raises(ValueError):
        store.locked_acceptance_spine()


@pytest.mark.parametrize("fault", ["unknown", "channel", "shape", "superseded", "scope"])
def test_response_binding_rechecks_subject_and_never_mints_stale_consent(store, fault):
    """Catch wrong/stale responses at the real writer; a launch spy cannot protect recording."""
    request = confirmation.prepare_confirmation(store, "align+vision-lock")
    if fault == "superseded":
        vision = store.spine().node("V1")
        vision["text"] += " revised before replacement"
        store.upsert_node(vision)
        confirmation.prepare_confirmation(store, "align+vision-lock")
    if fault == "scope":
        vision = store.spine().node("V1")
        vision["text"] += " changed"
        store.upsert_node(vision)
    if fault in {"unknown", "shape", "channel"}:
        with pytest.raises(ValueError):
            if fault == "channel":
                confirmation.complete_chat_confirmation(
                    store,
                    request["request_id"],
                    gate=request["gate"],
                    expected_revision=request["plan_revision"],
                    quoted_message="Approve",
                    quote_sha256=approvals.fingerprint("wrong"),
                    actor_label="source tester",
                )
            else:
                confirmation.complete_confirmation(
                    store,
                    "unknown" if fault == "unknown" else request["request_id"],
                    "accept",
                    {} if fault == "shape" else answer(),
                )
    else:
        result = confirmation.complete_confirmation(
            store, request["request_id"], "accept", answer()
        )
        assert result["state"] == "stale"
    with pytest.raises(ValueError):
        approvals.require_approval(store, "A")


def test_approval_and_lock_snapshot_rollback_together(store, monkeypatch):
    """Catch partial lock writes after a failed response commit; replay tests miss write failure."""
    request = confirmation.prepare_confirmation(store, "align+vision-lock")
    original = store._event

    def fail(kind, payload):
        original(kind, payload)
        if kind == confirmation.RESPONSE:
            raise RuntimeError("source-only interrupted response")

    before = list(store.connection.iterdump())
    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError):
        confirmation.complete_confirmation(store, request["request_id"], "accept", answer())
    assert list(store.connection.iterdump()) == before


@pytest.mark.parametrize("chat", [False, True])
@pytest.mark.parametrize("words", ["Approve except execution", "I am undecided"])
def test_qualified_or_unclear_words_request_changes_at_shared_writer(store, chat, words):
    """Catch non-prefix qualifications/uncertainty; AV2-1 covers the full native/job path."""
    import hashlib

    request = confirmation.prepare_confirmation(store, "align+vision-lock", _chat=chat)
    if chat:
        result = confirmation.complete_chat_confirmation(
            store,
            request["request_id"],
            gate=request["gate"],
            expected_revision=request["plan_revision"],
            quoted_message=words,
            quote_sha256=hashlib.sha256(words.encode()).hexdigest(),
            actor_label="source tester",
        )
    else:
        result = confirmation.complete_confirmation(
            store,
            request["request_id"],
            "accept",
            dict(approve=True, statement=words, conditions=""),
        )
    assert result["state"] == "changes-requested" and result["approval_id"] is None
    assert approvals.current(store, "A")["words"] == words
    with pytest.raises(ValueError, match="plan-a-consent-required"):
        store.advance("expand", by="source tester")
