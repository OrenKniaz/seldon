"""Read snapshots compose with writes without owning the caller's transaction."""

import sqlite3

import pytest

from codex_harness.core.plan_store import PlanStore
from codex_harness.core.planner_schema import node_example


def test_spine_read_preserves_pending_write_and_callers_rollback(tmp_path):
    """Catch a reader committing/rolling back its caller; readiness covers commit only."""
    with PlanStore(tmp_path, name="Before") as store, PlanStore(tmp_path) as observer:
        _, vision = node_example("vision")
        store.upsert_node(vision)
        before = store.spine().to_dict()
        with pytest.raises(RuntimeError, match="abort caller"):
            with store._transaction():
                store.connection.execute("UPDATE nodes SET text='pending' WHERE id='V1'")
                assert store.spine().node("V1")["text"] == "pending"
                assert store.connection.in_transaction
                assert observer.spine().to_dict() == before
                raise RuntimeError("abort caller")
        assert not store.connection.in_transaction
        assert store.spine().to_dict() == observer.spine().to_dict() == before


def test_spine_reads_name_and_nodes_from_one_sqlite_snapshot(tmp_path):
    """Catch torn metadata/nodes on concurrent import; readiness never changes plan name."""
    with PlanStore(tmp_path, name="Before") as reader, PlanStore(tmp_path) as writer:
        assert reader.list_nodes() == []
        _, vision = node_example("vision")
        reader.upsert_node(vision)
        before = reader.spine().to_dict()
        changed = False

        def interleave(cursor, values):
            nonlocal changed
            row = sqlite3.Row(cursor, values)
            if not changed and "id" in row.keys():
                changed = True
                # Commit a real concurrent replacement after the first node is read.
                with writer._transaction():
                    writer.connection.execute("UPDATE meta SET name='After'")
                    writer.connection.execute("UPDATE nodes SET text='after' WHERE id='V1'")
            return row

        reader.connection.row_factory = interleave
        try:
            snapshot = reader.spine().to_dict()
        finally:
            reader.connection.row_factory = sqlite3.Row
        assert changed and snapshot == before
        assert not reader.connection.in_transaction
        current = reader.spine()
        assert current.name == "After" and current.node("V1")["text"] == "after"
