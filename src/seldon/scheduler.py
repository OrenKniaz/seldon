"""C12/C18/I5/I6; R6/R7/R11: dependency, scope and capacity waits are explicit and local."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace

from . import plan, units
from .states import advance


def execute(store, provider, capacity):
    document = store.plan(store.cursor().revision)
    contracts = plan.select(document, "unit")
    running = {}
    with ThreadPoolExecutor(max_workers=capacity) as pool:
        while True:
            rows = store.units()
            for ref, row in rows.items():
                if ref in running.values() or row.state in ("DONE", "ASKED", "DROPPED"):
                    continue
                deps = plan.prerequisites(document, ref) + row.waitingFor
                unmet = [d for d in deps if rows[d].state != "DONE"]
                conflict = [r for r in running.values()
                            if plan.overlaps(contracts[ref]["scope"], contracts[r]["scope"])]
                reason = ("Dependencies not DONE: " + ", ".join(unmet) if unmet else
                          "Scope overlaps running units: " + ", ".join(conflict) if conflict else
                          f"Capacity occupied: {capacity}" if len(running) >= capacity else "")
                if reason:
                    if reason != row.waiting:
                        with store.transaction():
                            store.record("unit_wait", {"unit": ref, "reason": reason})
                            store.unit(ref, replace(store.unit(ref), waiting=reason))
                    continue
                if row.state == "WAITING":
                    advance(store, "ready", "Dependencies DONE and scope available", unit=ref,
                            waiting="", waitingFor=[])
                running[pool.submit(units.run_unit, store, provider, ref)] = ref
            if not running:
                pending = [r for r, u in store.units().items()
                           if u.state not in ("DONE", "ASKED", "DROPPED")]
                if pending and not any(u.state == "ASKED" for u in store.units().values()):
                    raise ValueError("No unit can progress: " + ", ".join(pending))
                return
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                running.pop(future)
                future.result()
