"""Read-only format admission before any current-run writer opens."""

import sqlite3


def admit_store(store):
    if store.path.is_file():
        store.connection = sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True)
        store.connection.row_factory = sqlite3.Row
        try:
            if require_current_store(store.connection):
                from codex_harness.core.run_loop import cursor
                from codex_harness.core.acceptance_runs import _latest_run
                cursor(store)
                _latest_run(store)
        finally:
            store.connection.close()

def require_current_store(connection):
    # Read before initialization: unsupported stores must never be upgraded in place.
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
        return False
    try:
        connection.execute("SELECT vision_lock_stale, locked_vision_json FROM meta LIMIT 0")
        connection.execute("SELECT rationale, resolved_by FROM assumptions LIMIT 0")
        connection.execute("SELECT engine_identity FROM audit_attempts LIMIT 0")
        for table, columns in (("review_runs", "packetBound,outOfPacketTools,isMocked,didRunProvider,shards,shardDetails,truncatedNodes,reshardedFrom,nodesDigest"),
                               ("review_attempts", "state,error")):
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                connection.execute(f"SELECT {columns} FROM {table} LIMIT 0")
    except sqlite3.OperationalError:
        from codex_harness.core.plan_types import refuse_old_format
        refuse_old_format("Plan store")
    return True

