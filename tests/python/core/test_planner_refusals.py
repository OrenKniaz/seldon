"""Refusal recovery: old tests check gates, not a complete bounded explanation."""

from tests.python.core.test_semantic_wiring import semantic_plan as _semantic_plan

semantic_plan = _semantic_plan


def canonical_rows(store):
    """Keep all planning rows and prior events; refusal telemetry has its own assertions."""
    return {
        name: list(
            store.connection.execute(
                f'SELECT * FROM "{name}"'
                + (" WHERE kind != 'planner-refusal'" if name == "events" else "")
            )
        )
        for (name,) in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'"
        )
    }


def refusal_total(store):
    return store.connection.execute(
        "SELECT count(*) FROM events WHERE kind='planner-refusal'"
    ).fetchone()[0]
