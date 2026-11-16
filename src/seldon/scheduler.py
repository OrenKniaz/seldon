"""C12/C18/I5/I6; R6/R7/R11: dependency, scope and capacity waits are explicit and local."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace

from . import decisions, plan, units
from .states import advance


def execute(store, provider, capacity, user):
    doc = store.plan(store.cursor().revision)
    contracts = plan.select(doc, "unit")
    running = {}
    with ThreadPoolExecutor(max_workers=capacity) as pool:
        while True:
            decisions.ask_unit(store)
            if user and store.cursor().decision:
                try:
                    with store.integration_lock:
                        decisions.decide(store, provider, user)
                except decisions.Waiting:
                    user = None
            rows = store.units()
            for ref, row in rows.items():
                if ref in running or row.state in ("DONE", "ASKED", "DROPPED"):
                    continue
                unmet = [d for d in contracts[ref]["dependencies"] if rows[d].state != "DONE"]
                conflict = [r for r in running
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
                            waiting="")
                running[ref] = pool.submit(units.run_unit, store, provider, ref)
            if not running:
                if store.cursor().decision:
                    raise decisions.Waiting()
                if pending := [r for r, u in rows.items() if u.state not in ("DONE", "DROPPED")]:
                    raise ValueError("No unit can progress: " + ", ".join(pending))
                return
            wait(running.values(), return_when=FIRST_COMPLETED)
            for ref in [r for r, future in running.items() if future.done()]:
                running.pop(ref).result()
