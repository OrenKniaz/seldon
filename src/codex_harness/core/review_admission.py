"""Plan review schema/admission; finite ownership belongs to the run controller."""

from __future__ import annotations
from codex_harness.core.plan_store import PlanStore, PlanStoreError
from codex_harness.core.approvals import require_invocation


def _table(store: PlanStore) -> None:
    store.connection.execute(
        "CREATE TABLE IF NOT EXISTS review_runs ("
        "id TEXT PRIMARY KEY, round_id TEXT NOT NULL, plan_revision TEXT NOT NULL, "
        "reviewer TEXT NOT NULL, provider TEXT NOT NULL, model TEXT, exit_code INTEGER, "
        "findings INTEGER NOT NULL, invalid INTEGER NOT NULL, "
        "started_at TEXT NOT NULL, finished_at TEXT NOT NULL)"
    )
    for table in ("review_runs",):
        columns = {row[1] for row in store.connection.execute(f"PRAGMA table_info({table})")}
        for name, kind in (("packetBound", "INTEGER"), ("outOfPacketTools", "TEXT")):
            if name not in columns:
                store.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    columns = {row[1] for row in store.connection.execute("PRAGMA table_info(review_runs)")}
    for name, kind in (
        ("isMocked", "INTEGER"),
        ("didRunProvider", "INTEGER"),
        ("shards", "INTEGER"),
        ("shardDetails", "TEXT"),
        ("truncatedNodes", "TEXT"),
        ("reshardedFrom", "TEXT"),
        ("nodesDigest", "TEXT"),
    ):
        if name not in columns:
            store.connection.execute(f"ALTER TABLE review_runs ADD COLUMN {name} {kind}")


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
            "plan_revision TEXT, reviewer TEXT, provider TEXT)"
        )
        columns = {row[1] for row in store.connection.execute("PRAGMA table_info(review_attempts)")}
        for name, declaration in (("state", "TEXT NOT NULL DEFAULT 'running'"), ("error", "TEXT")):
            if name not in columns:
                store.connection.execute(
                    f"ALTER TABLE review_attempts ADD COLUMN {name} {declaration}"
                )
        store.connection.execute(
            "INSERT OR IGNORE INTO review_attempts (id,plan_revision,reviewer,provider) VALUES (?, ?, ?, ?)",
            (run_id, revision, reviewer["name"], reviewer["provider"]),
        )
        from codex_harness.core.run_ownership import attach_attempt

        attach_attempt(store, "plan-review:" + run_id)
