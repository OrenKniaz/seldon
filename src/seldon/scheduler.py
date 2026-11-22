"""C12/C18/I5/I6; R6/R7/R11: dependency, scope and capacity waits are explicit and local."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace

from . import architecture, decisions, detail, fixups, flow_review, gitwork, inference, judgment, plan
from . import plan_review, proof, questions, unit_questions, units, wave_verify
from .answers import recorder
from .states import TERMINAL, advance
from .store import UnitRow


def execute(store, provider, capacity, user):
    doc = store.plan(store.cursor().revision)
    contracts = plan.select(doc, "unit")
    running = {}
    with ThreadPoolExecutor(max_workers=capacity) as pool:
        while True:
            if user and store.cursor().decision:
                try:
                    decisions.decide(store, provider, user)
                except decisions.Waiting:
                    pass
            if store.cursor().state == "STOPPED":
                return
            rows = store.units()
            coaching = any(rows[r].state == "ASKED" for r in running)
            for ref, row in rows.items():
                if (row.state == "ASKED" and ref not in running and not coaching
                        and not units.contract_gap(store, ref)
                        and not store.cursor().decision and len(running) < capacity):
                    running[ref] = pool.submit(units.ask_unit, store, provider, ref)
                    coaching = True
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
                pending = [r for r, u in rows.items() if u.state not in ("DONE", "DROPPED", "ASKED")]
                if pending and not any(u.state == "ASKED" and units.contract_gap(store, r) for r, u in rows.items()):
                    raise ValueError("No unit can progress: " + ", ".join(pending))
                return
            wait(running.values(), timeout=2, return_when=FIRST_COMPLETED)
            for ref in [r for r, future in running.items() if future.done()]:
                running.pop(ref).result()


def eligible(store):
    cursor = store.cursor()
    document = plan.execution(store)
    checkin = store.read(cursor.checkin)["subject"] if cursor.checkin else {}
    if (not cursor.B or checkin.get("commit") != cursor.candidate
            or checkin.get("wave") != cursor.wave or cursor.finalCommit != cursor.candidate
            or any(u.state not in ("DONE", "DROPPED") for u in store.units().values())):
        raise ValueError("C requires every unit DONE or DROPPED and final proof after check-in")
    declarations = {r: (c["command"], c["requirements"]) for r, c in plan.select(document, "check").items()}
    observed = {o["check"]: (o["command"], o["requirements"]) for o in map(store.read, cursor.finalObservations)
                if o["commit"] == cursor.candidate}
    if declarations != observed or proof.failures(store, cursor.finalObservations):
        raise ValueError("Every declared check needs passing execution on the final commit")
    if any(not row.judgment for row in store.units().values() if row.state != "DROPPED"):
        raise ValueError("C requires current unit judgments after contract changes")
    verdicts = [store.read(r) for r in cursor.demoVerdicts]
    trees = judgment.scope_trees(store, plan.select(document, "unit"))
    for kind, refs in (("FLOW", cursor.requirementVerdicts), ("PRODUCT", cursor.qualityVerdicts)):
        rows = [store.read(r) for r in refs]
        subjects = judgment.subjects(document, kind)
        if (len(rows) != len(subjects) or {r["subject"] for r in rows} != subjects.keys()
                or not all(judgment.current(r, trees) and judgment.ready(r["answer"]) for r in rows)):
            raise ValueError("C requires each final subject satisfied or explicitly not judged")
    if ({v["feature"] for v in verdicts} != set(plan.select(document, "feature"))
            or len(verdicts) != len(plan.select(document, "feature"))
            or not all(v["commit"] == cursor.candidate and judgment.current(v, trees)
                       and judgment.ready(v["answer"])
                       for v in verdicts)):
        raise ValueError("C requires a satisfied final verdict for every feature")
    if not (product := cursor.productVerdict and store.read(cursor.productVerdict)) or (
            product["commit"] != cursor.candidate or not judgment.ready(product["answer"])):
        raise ValueError("C requires satisfied product quality and try-it guide on the final commit")
    gitwork.unchanged(store.root.parent, cursor.candidate)


def step(store, provider, user, capacity, cursor):
    state = cursor.state
    if state == "CHECK-IN":
        next_wave = plan.execution(store)["architecture"]["waves"][cursor.wave:cursor.wave + 1]
        with store.transaction():
            decisions.request(store, state, {"commit": cursor.candidate, "wave": cursor.wave,
                "built": plan.built(store)["Finished wave"], "next wave goal": next_wave[0]["goal"] if next_wave else None})
            if next_wave:
                advance(store, "next", cursor.candidate, wave=cursor.wave + 1, waveStart=cursor.candidate, repairs=0)
            else:
                advance(store, "last", cursor.candidate)
    elif state == "ARCHITECT":
        finished = max((u.wave for u in store.units().values()), default=0)
        if cursor.wave == finished and cursor.finalCommit == cursor.candidate:
            cursor = advance(store, "steer", cursor.requestedChange or cursor.candidate,
                             wave=finished + 1, waveStart=cursor.candidate, repairs=0)
        replan = cursor.wave == finished  # a plan gap re-plans the unverified wave in place
        vision = questions.vision_body(store.read(cursor.vision)["answer"])
        previous = store.plan(cursor.revision) if cursor.revision else None
        built = plan.built(store) if cursor.wave > 1 and not replan else None
        outline = previous["architecture"] if previous else None
        gaps = [b for _, _, b in store.records("architecture_gap") if b["vision"] == cursor.vision]
        def consume(result, answer):
            if "scope" in answer:
                advance(store, "scope", result, requestedChange=result, A=None, B=None)
                return
            document = dict(previous) if built or replan else {
                "nodes": {"vision": {"kind": "vision", "body": vision}}}
            document["architecture"] = {k: v for k, v in answer.items() if k != "why"}
            revision = store.revision(document, cursor.revision)
            advance(store, "outlined", result, revision=revision)

        brief = unit_questions.architect_brief(
            vision, questions.user_words(store), gaps[-1]["gap"] if gaps else None,
            outline if gaps or built else None, built=built)
        setup = store.read(cursor.kickoff)["setup"]
        brief["facts"].update(architecture.planning_approach(
            setup, vision, continuing=bool(gaps or built)))
        inference.call(store, provider, brief,
            check=lambda a: architecture.outline(a, vision, record=recorder(store)),
            consume=consume, field="areas/contracts/decisions/waves")
    elif state == "DETAIL":
        detail.step(store, provider, capacity)
    elif state == "REVIEW":
        plan_review.step(store, provider, capacity)
    elif state == "REPAIR":
        plan_review.repair(store)
    elif state == "EXECUTE":
        if decisions.holds(store):
            raise decisions.Waiting()
        if not plan.approved(store):
            raise ValueError("Execution requires B for the accepted vision")
        with store.transaction():
            for ref, body in plan.select(plan.execution(store), "unit").items():
                row = store.unit(ref)
                if row is None:
                    store.unit(ref, UnitRow(wave=cursor.wave))
                    advance(store, "create", cursor.B, unit=ref)
                elif row.state == "DONE" and row.wave < cursor.wave and (
                        ref == "guide" or any(r.startswith(f"{cursor.wave}.")
                                              for r in body["requirements"])):
                    advance(store, "extend", cursor.revision, unit=ref,
                            **{k: v for k, v in vars(UnitRow(wave=cursor.wave,
                                base=cursor.candidate)).items() if k != "state"})
        execute(store, provider, capacity, user)
        if store.cursor().state not in TERMINAL:
            advance(store, "finished", "No more units can run in this pass")
    elif state == "WAVE-VERIFY":
        if fixups.replan_worker_agreements(store):
            return True
        observations = wave_verify.verify(store, cursor.candidate)
        gaps = proof.gaps(store, cursor.candidate, observations)
        if not gaps:
            gaps = units.judge_wave(store, provider, observations)
        if planned := [g for g in gaps if g["kind"] in ("plan", "contract")]:
            fixups.replan(store, planned)
        elif gaps:
            fixups.apply(store, gaps)
        else:
            advance(store, "passed", observations, finalCommit=cursor.candidate,
                    finalObservations=observations)
    elif state == "FLOW-JUDGE":
        flow_review.step(store, provider)
    elif state == "PRODUCT-JUDGE":
        judgment.step(store, provider)
