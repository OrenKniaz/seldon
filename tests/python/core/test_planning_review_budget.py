"""Cumulative transactional usage replaces immutable registration/file originals."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.python.core import test_semantic_wiring
from tests.python.core.test_planner_chat_confirmations import (
    approve_b,
    prepare_combined_b,
    review,
)

from codex_harness.core import approvals, roundtable
from codex_harness.core import planner_confirmations as confirmation
from codex_harness.core import provider_call_budget as budget
from codex_harness.core.plan_store import open_plan_store

semantic_plan = test_semantic_wiring.semantic_plan


def test_refused_start_leaves_no_reservation_and_b_renewal_recovers_after_restart(
    semantic_plan, monkeypatch
):
    """
    Run23 regression: refusal stranded a round reservation and blocked amendment; V1 tested
    only registered caps.
    """
    store, _ = semantic_plan
    request, options = prepare_combined_b(store, cap=1)
    reviewer = request["subject"]["reviewers"][0]
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: (0, "[]"))
    before = list(store.connection.iterdump())
    with pytest.raises(ValueError, match="plan-b-consent-required"):
        review(store, reviewer)
    assert list(store.connection.iterdump()) == before
    approve_b(store, request)
    review(store, reviewer)
    spent = budget.planning_review_budget(store)
    assert spent == dict(maxStarts=1, spentAdmissions=1)
    before = list(store.connection.iterdump())
    with pytest.raises(ValueError, match="cap-exhausted"):
        review(store, reviewer)
    assert list(store.connection.iterdump()) == before
    fresh = confirmation.prepare_chat_confirmation(
        store, "plan-execution", execution=options, review_max_starts=2
    )
    assert any(
        d["field"] == "reviewAllowance" and d["before"] == 1 and d["after"] == 2
        for d in fresh["diff"]
    )
    approve_b(store, fresh)
    with open_plan_store(store.workspace) as reopened:
        assert budget.planning_review_budget(reopened) == dict(maxStarts=2, spentAdmissions=1)
        review(reopened, reviewer)
        assert budget.usage(reopened, "review") == 2
        with pytest.raises(ValueError, match="raise the round limit"):
            review(reopened, reviewer)
        assert budget.usage(reopened, "review") == 2


def test_competing_real_review_consumers_get_one_final_slot(semantic_plan, monkeypatch):
    """
    Catch count/insert races through two real connections; serial renewal misses the final-
    slot interleaving.
    """
    store, _ = semantic_plan
    request, _ = prepare_combined_b(store, cap=1)
    approve_b(store, request)
    reviewer = request["subject"]["reviewers"][0]
    launches = []
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: launches.append(1) or (0, "[]"))

    def attempt(_):
        with open_plan_store(store.workspace) as reopened:
            try:
                review(reopened, reviewer)
                return True
            except ValueError as error:
                assert "cap-exhausted" in str(error)
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, range(2))) == 1
    assert launches == [1] and budget.usage(store, "review") == 1
    assert store.connection.execute("SELECT count(*) FROM review_attempts").fetchone()[0] == 1


def test_launch_admission_rollback_preserves_the_final_slot(semantic_plan, monkeypatch):
    """
    Catch usage committed before a failed attempt insert; concurrency tests never inject
    SQLite write failure.
    """
    store, _ = semantic_plan
    request, _ = prepare_combined_b(store, cap=1)
    approve_b(store, request)
    reviewer = request["subject"]["reviewers"][0]
    # A real SQLite failure after usage insertion, before an actual launch.
    roundtable._table(store)
    store.connection.execute(
        "CREATE TABLE review_attempts (id TEXT PRIMARY KEY, plan_revision TEXT, "
        "reviewer TEXT, provider TEXT)"
    )
    store.connection.execute(
        "CREATE TRIGGER reject_test_attempt BEFORE INSERT ON review_attempts "
        "BEGIN SELECT RAISE(ABORT, 'source-only write failure'); END"
    )
    monkeypatch.setattr(
        roundtable, "_run", lambda *a, **k: pytest.fail("Rollback must precede launch")
    )
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        review(store, reviewer)
    assert budget.usage(store, "review") == 0
    assert store.connection.execute("SELECT count(*) FROM review_attempts").fetchone()[0] == 0
    assert approvals.require_approval(store, "B")


def test_auto_allowance_is_part_of_b_and_large_total_requires_explanation(semantic_plan):
    """
    Catch separate catalog/budget prompts and silent large ceilings; renewal tests supply
    explicit small totals.
    """
    store, _ = semantic_plan
    _, options = prepare_combined_b(store)
    request = confirmation.prepare_chat_confirmation(store, "plan-execution", execution=options)
    assert request["subject"]["reviewAllowance"] == 5
    assert str(5) in request["card"]
    with pytest.raises(ValueError, match="explanation"):
        confirmation.prepare_chat_confirmation(
            store, "plan-execution", execution=options, review_max_starts=11
        )
    request = confirmation.prepare_chat_confirmation(
        store,
        "plan-execution",
        execution=options,
        review_max_starts=11,
        allowance_reason="Three required review stages and repair reserve",
    )
    approve_b(store, request)
    assert budget.planning_review_budget(store)["maxStarts"] == 11


def legacy_delivery(tmp_path, *, complete_card=True, damaged=False, chat_fault=None):
    """Retained V1 record fixture, never a production manual repair or launch.

    Copy the unchanged V1 table shapes from a disposable seed. The final fixture
    has no V2 marker/ledger, so opening it exercises actual automatic conversion.
    """
    import hashlib
    import json
    import sqlite3

    from tests.python.core.test_planner_confirmations import store as fixture_store

    seed_root = tmp_path / "seed"
    seed_root.mkdir()
    fixture = fixture_store.__wrapped__(seed_root)
    seed = next(fixture)
    data = seed._data()
    rows = {
        table: [tuple(r) for r in seed.connection.execute(f"SELECT * FROM {table}")]
        for table in ("nodes", "meta")
    }
    schemas = [
        seed.connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[
            0
        ]
        for table in ("nodes", "meta", "events", "approvals")
    ]
    revision = seed._revision()
    fixture.close()
    root = tmp_path / "legacy"
    internal = root / ".codex-harness"
    internal.mkdir(parents=True)
    connection = sqlite3.connect(internal / "plan.sqlite3")
    for sql in schemas:
        connection.execute(sql)
    for table, values in rows.items():
        connection.executemany(
            f"INSERT INTO {table} VALUES ({','.join('?' for _ in values[0])})", values
        )
    card = (
        approvals.EFFECTS["A"]
        + "\nControls: retained V1 preimage\n"
        + json.dumps(data, ensure_ascii=False)
    )
    if not complete_card:
        card = "An excerpt without the complete scope and lock effect"
    digest = hashlib.sha256(card.encode()).hexdigest()
    request = dict(
        request_id="retained-a",
        gate="align+vision-lock",
        phase="align",
        plan_revision=revision,
        workspace=str(root),
        card=card,
        card_digest=digest,
        channel=confirmation.CHAT,
    )
    response = dict(
        request_id="retained-a",
        state="approved",
        action="accept",
        card_digest=digest,
        provenance=confirmation.CHAT,
        content=dict(approve=True, statement="Approve", conditions=""),
        approval_ids={"align": "old-align", "vision-lock": "old-lock"},
    )
    _, _, response["chat"] = confirmation._chat_evidence(
        request["request_id"],
        revision,
        "Approve",
        hashlib.sha256(b"Approve").hexdigest(),
        "source-only retained actor",
    )
    if chat_fault == "actor":
        response["chat"].pop("actor_label")
    elif chat_fault == "quote":
        response["chat"]["quoted_message"] = "No"
    for gate, identity in response["approval_ids"].items():
        connection.execute(
            "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?)",
            (identity, gate, revision, confirmation.CHAT, "Approve", "retained"),
        )
    original = dict(
        schema="seldon/planning-review-budget-v1",
        workspace=str(root),
        campaign="retained",
        maxStarts=2,
    )
    claim = dict(
        registrationDigest=hashlib.sha256(
            json.dumps(original, sort_keys=True).encode()
        ).hexdigest(),
        ordinal=1,
        reviewer=dict(name="old", provider="codex"),
        pid=123,
    )
    for event, payload in [
        (confirmation.REQUEST, request),
        (confirmation.RESPONSE, response),
        ("planning-review-budget-registered", original),
        ("planning-review-start-claimed", claim),
    ]:
        connection.execute(
            "INSERT INTO events (kind, at, payload_json) VALUES (?, 'retained', ?)",
            (event, json.dumps(payload)),
        )
    connection.commit()
    connection.close()
    directory = internal / "planning-review-budget"
    (directory / "claims").mkdir(parents=True)
    (directory / "registration.json").write_text(json.dumps(original), encoding="utf-8")
    (directory / "claims/00000001.json").write_text(
        json.dumps({} if damaged else claim), encoding="utf-8"
    )
    return root


@pytest.mark.parametrize(
    "complete,chat_fault,converted",
    [(False, None, False), (True, None, True), (True, "actor", False), (True, "quote", False)],
)
def test_conversion_twice_preserves_spend_and_only_exact_displayed_consent(
    tmp_path, complete, chat_fault, converted
):
    """
    Catch duplicate import or excerpt promotion on restart; renewal begins with an
    already-V2 store.
    """
    root = legacy_delivery(tmp_path, complete_card=complete, chat_fault=chat_fault)
    for _ in range(2):
        with open_plan_store(root) as store:
            assert budget.usage(store, "review") == 1
            assert len(approvals.records(store, "approval-v2-migrated")) == 1
            assert len(approvals.records(store, approvals.EVENT)) == int(converted)
            if converted:
                saved = approvals.require_approval(store, "A")
                assert (
                    saved["words"] == "Approve" and saved["actor"] == "source-only retained actor"
                )
            else:
                with pytest.raises(ValueError):
                    approvals.require_approval(store, "A")
            with pytest.raises(ValueError):
                approvals.require_approval(store, "B")
            assert store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 2


def test_conversion_missing_or_inconsistent_usage_stops_without_import(tmp_path):
    """
    Catch malformed historical claims silently resetting spend; exact-match conversion uses
    intact originals.
    """
    import sqlite3

    root = legacy_delivery(tmp_path, damaged=True)
    with pytest.raises(ValueError, match="invalid-original"):
        open_plan_store(root)
    with sqlite3.connect(root / ".codex-harness/plan.sqlite3") as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM events WHERE kind='approval-v2-migrated'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT 1 FROM sqlite_master WHERE name='usage_ledger'").fetchone()
            is None
        )


def test_auto_sizing_explains_scheduled_total_above_ten_without_granting_it(semantic_plan):
    """Catch silent clamping; explicit-total tests never exercise scheduled sizing."""
    store, _ = semantic_plan
    _, options = prepare_combined_b(store)
    node = store.spine().node("CHOICE")
    original = node["fields"]["reviewers"][0]
    node["fields"]["reviewers"] = [
        {**original, "name": name} for name in ("first", "second", "third")
    ]
    store.record_reviewer_selection(node, store._revision())
    options["expected_revision"] = store.compile_template(store.workspace / "template")[
        "planRevision"
    ]
    question = confirmation.prepare_chat_confirmation(store, "plan-execution", execution=options)
    assert question["subject"]["reviewAllowance"] == 15
    assert "scheduled" in question["subject"]["allowanceReason"]
    assert budget.usage(store, "review") == 0 and not approvals.current(store, "B")
    with pytest.raises(ValueError):
        store.advance("roundtable", by="source-test")
    approve_b(store, question)
    assert approvals.require_approval(store, "B")["subject"]["reviewAllowance"] == 15


def test_missing_converted_ledger_never_restarts_at_zero(semantic_plan, monkeypatch):
    """Catch recreated lost usage; damaged V1 files have no V2 conversion marker."""
    store, _ = semantic_plan
    request, _ = prepare_combined_b(store)
    approve_b(store, request)
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: (0, "[]"))
    review(store, request["subject"]["reviewers"][0])
    assert budget.usage(store, "review") == 1
    # Unsupported corruption counterfactual in this temporary test database.
    store.connection.execute("DROP TABLE usage_ledger")
    with pytest.raises(ValueError, match="usage-ledger-missing-after-conversion"):
        open_plan_store(store.workspace)
    assert (
        store.connection.execute("SELECT 1 FROM sqlite_master WHERE name='usage_ledger'").fetchone()
        is None
    )
