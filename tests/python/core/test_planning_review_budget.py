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
    request, options = prepare_combined_b(store)
    reviewer = request["subject"]["reviewers"][0]
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: (0, "[]"))
    before = list(store.connection.iterdump())
    with pytest.raises(ValueError, match="plan-b-consent-required"):
        review(store, reviewer)
    assert list(store.connection.iterdump()) == before
    approve_b(store, request)
    # Attempted/uncertain starts consume the same real cumulative consumer ledger.
    for _ in range(9):
        budget.claim_planning_review_start(store, reviewer)
    review(store, reviewer)
    spent = budget.planning_review_budget(store)
    assert spent == dict(maxStarts=10, spentAdmissions=10)
    before = list(store.connection.iterdump())
    with pytest.raises(ValueError, match="cap-exhausted"):
        review(store, reviewer)
    assert list(store.connection.iterdump()) == before
    fresh = confirmation.prepare_chat_confirmation(store, "plan-execution", execution=options)
    assert any(
        d["field"] == "reviewAllowance" and d["before"] == 10 and d["after"] == 12
        for d in fresh["diff"]
    )
    approve_b(store, fresh)
    with open_plan_store(store.workspace) as reopened:
        assert budget.planning_review_budget(reopened) == dict(maxStarts=12, spentAdmissions=10)
        review(reopened, reviewer)
        assert budget.usage(reopened, "review") == 11
        with pytest.raises(ValueError, match="raise the round limit"):
            review(reopened, reviewer)
        assert budget.usage(reopened, "review") == 11


def test_competing_real_review_consumers_get_one_final_slot(semantic_plan, monkeypatch):
    """
    Catch count/insert races through two real connections; serial renewal misses the final-
    slot interleaving.
    """
    store, _ = semantic_plan
    request, _ = prepare_combined_b(store)
    approve_b(store, request)
    reviewer = request["subject"]["reviewers"][0]
    for _ in range(9):
        budget.claim_planning_review_start(store, reviewer)
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
    assert launches == [1] and budget.usage(store, "review") == 10
    assert store.connection.execute("SELECT count(*) FROM review_attempts").fetchone()[0] == 1


def test_launch_admission_rollback_preserves_the_final_slot(semantic_plan, monkeypatch):
    """
    Catch usage committed before a failed attempt insert; concurrency tests never inject
    SQLite write failure.
    """
    store, _ = semantic_plan
    request, _ = prepare_combined_b(store)
    approve_b(store, request)
    reviewer = request["subject"]["reviewers"][0]
    for _ in range(9):
        budget.claim_planning_review_start(store, reviewer)
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
    assert budget.usage(store, "review") == 9
    assert store.connection.execute("SELECT count(*) FROM review_attempts").fetchone()[0] == 0
    assert approvals.require_approval(store, "B")


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
