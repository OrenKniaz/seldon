"""Mechanical generation/deadline guards; run_loop alone chooses continuation."""

from contextlib import contextmanager
from datetime import UTC, datetime
from codex_harness.core.plan_types import PlanStoreError


def ensure_effect(store, generation=None, effect_id=None):
    from codex_harness.core.run_loop import cursor, TERMINAL

    run = cursor(store)
    expected = generation if generation is not None else getattr(store, "_run_generation", None)
    if expected is None:
        return
    effect = (run or {}).get("effect")
    expected_id = effect_id or getattr(store, "_run_effect_id", None)
    if (
        not effect
        or run["generation"] != expected
        or effect["generation"] != expected
        or expected_id is not None
        and effect["id"] != expected_id
        or run["state"] in TERMINAL | {"PAUSED"}
        or datetime.now(UTC) >= datetime.fromisoformat(effect["deadlineAt"])
    ):
        raise PlanStoreError(
            "run-owner-revoked",
            "This segment expired or was interrupted; read the current baton and use its pending request",
        )


def effect_binding(store):
    from codex_harness.core.run_loop import cursor

    run = cursor(store)
    ensure_effect(store, run["generation"])
    return dict(
        workspace=str(store.workspace), generation=run["generation"], effectId=run["effect"]["id"]
    )


def attach_attempt(store, attempt_id):
    from codex_harness.core.run_loop import cursor, save

    """Existing attempt owners bind their finite admission to this controller segment."""
    with store._transaction():
        ensure_effect(store)
        run = cursor(store)
        if run and run.get("effect"):
            run["effect"]["attemptIds"] = list(
                dict.fromkeys([*run["effect"].get("attemptIds", []), attempt_id])
            )
            save(store, run)


@contextmanager
def effect_guard(binding):
    """The canonical transaction linearizes integration/delivery against control."""
    if binding is None:
        yield
        return
    from codex_harness.core.plan_store import open_plan_store

    with open_plan_store(binding["workspace"]) as store:
        with store._transaction():
            ensure_effect(store, binding["generation"], binding["effectId"])
            yield
            ensure_effect(store, binding["generation"], binding["effectId"])
