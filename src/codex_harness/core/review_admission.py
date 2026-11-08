"""Plan review schema/admission; finite ownership belongs to the run controller."""

from __future__ import annotations
from codex_harness.core.plan_store import PlanStore, PlanStoreError
from codex_harness.core.run_setup import require_invocation


def _table(store: PlanStore) -> None:
    store.connection.execute(
        "CREATE TABLE IF NOT EXISTS review_runs ("
        "id TEXT PRIMARY KEY, round_id TEXT NOT NULL, plan_revision TEXT NOT NULL, "
        "reviewer TEXT NOT NULL, provider TEXT NOT NULL, model TEXT, exit_code INTEGER, "
        "findings INTEGER NOT NULL, invalid INTEGER NOT NULL, "
        "started_at TEXT NOT NULL, finished_at TEXT NOT NULL, "
        "packetBound INTEGER, outOfPacketTools TEXT, isMocked INTEGER, "
        "didRunProvider INTEGER, shards INTEGER, shardDetails TEXT, truncatedNodes TEXT, "
        "reshardedFrom TEXT, nodesDigest TEXT)"
    )


def _admit_review(store, reviewer, run_id, revision, *, mocked=False):
    with store._transaction():
        from codex_harness.core.run_ownership import ensure_effect

        ensure_effect(store)
        if store.manifest_revision() != revision:
            raise PlanStoreError("roundtable-plan-changed", "Review the new plan revision")
        if not mocked:
            require_invocation(store, "reviewer", reviewer)
        _table(store)
        store.connection.execute(
            "CREATE TABLE IF NOT EXISTS review_attempts (id TEXT PRIMARY KEY, "
            "plan_revision TEXT, reviewer TEXT, provider TEXT, "
            "state TEXT NOT NULL DEFAULT 'running', error TEXT)"
        )
        store.connection.execute(
            "INSERT OR IGNORE INTO review_attempts (id,plan_revision,reviewer,provider) VALUES (?, ?, ?, ?)",
            (run_id, revision, reviewer["name"], reviewer["provider"]),
        )
        from codex_harness.core.run_ownership import attach_attempt

        attach_attempt(store, "plan-review:" + run_id)
