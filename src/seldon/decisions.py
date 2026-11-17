"""C8/C16/C20; R17: planner first, concrete choices, and only dependent work waits."""

from dataclasses import replace
from pathlib import Path
import traceback

from . import inference, plan, questions, recovery
from .answers import ConsumptionFailure
from .providers import ProviderFailure
from .states import advance


class Waiting(Exception):
    """The next words belong to a future chat turn."""


def holds(store):
    cursor = store.cursor()
    if not cursor.decision:
        return False
    item = store.read(cursor.decision)
    if item["gate"] == "UNIT":
        return False
    if item["gate"] == "CHECK-IN":
        return cursor.state in ("EXECUTE", "C")
    return cursor.state == item["subject"].get("step", item["gate"])


def affected(store, ref):
    contracts = plan.select(plan.execution(store), "unit")
    refs = {ref}
    while added := {r for r, u in contracts.items() if r not in refs
                    and refs.intersection(u["dependencies"])}:
        refs.update(added)
    return refs


def again(store, subject, source, direction):
    ref = subject["unit"]
    for owner, unit in store.units().items():
        if unit.state == "ASKED" and recovery.lineage(store, owner) == recovery.lineage(store, ref):
            advance(store, "again", source, unit=owner, thread=None,
                    base=unit.base if unit.worktree else store.cursor().candidate,
                    callFolder=None, callAttempt=None,
                    failure=unit.failure + "\nDirection: " + direction)


def coach(store, provider, subject):
    """Runs outside integration and database locks, including when submitted by the scheduler."""
    ref = subject.get("unit")
    document = plan.execution(store) if store.cursor().revision else {}
    facts = {"Unresolved work": subject, "Earlier user answers": questions.user_words(store),
             "Vision": store.read(store.cursor().vision) if store.cursor().vision else None,
             "Decisions": document.get("architecture", {}).get("decisions", []),
             "User answers": [b for _, _, b in store.records("user_words")]}
    if ref:
        facts.update({"Execution facts": questions.execution_facts(store, ref),
            "Requirements": questions.requirement_facts(document,
                plan.select(document, "unit")[ref]["requirements"]),
            **questions.agreement_facts(document, ref)})
    else:
        facts["Requirements and agreements"] = document
    response = None
    def consume(source, answer):
        nonlocal subject, response
        key, subject = recovery.subject(store, "UNIT" if ref else subject["step"], subject)
        history = store.recovery(key)
        if "guidance" in answer and not history["coached"]:
            history.update(coached=True, waiting=False)
            store.recovery(key, history)
            direction = store.record("planner_answer", {"subject": subject, "source": source,
                "guidance": answer["guidance"], "words": answer["guidance"], "origin": "planner"})
            if ref:
                again(store, subject, source, answer["guidance"])
            else:
                store.record("approval", {"request": store.cursor().decision, "subject": subject,
                    "words": direction, "choice": "revise", "origin": "planner"})
                store.cursor(replace(store.cursor(), decision=None, requestedChange=direction, failure=""))
            return None
        ask = answer.get("ask") or {"question": "How should we continue?", "options": [
            {"label": "Try the planner's new approach", "means": answer["guidance"]}]}
        ask = {"question": ask["question"], "options": [
            {**o, "effect": "direct"} for o in ask["options"]]}
        if ref:
            ask["options"].append(option("Leave this out", "Leave out this unit and its dependents", "drop"))
        elif "guidance" in answer:
            ask["options"].append(option("Stop", "Stop the dream", "stop"))
        response = {**subject, "ask": ask, "planner": source}
    try:
        inference.call(store, provider, questions.build("COACH", facts), consume=consume, field="guidance/ask")
    except ConsumptionFailure as error:
        cause, shape = record_fault(store, error)
        recovery.failed(store, cause, shape=shape)
        response = {**subject, "failure": shape["failure"], "ask": pause_ask(str(error))}
    return response


def ask_unit(store, provider, ref):
    try:
        row = store.unit(ref)
        tried = [b["failure"] for _, _, b in store.records("unit_failure") if b["unit"] == ref]
        subject = {"unit": ref, "tried": tried, "failure": row.failure,
                   "affects": sorted(affected(store, ref) - {ref}), "recommendation": row.failure}
        if checks := recovery.repeated(store, ref):
            # Like a repeated step fault: the same approach again would only loop (R15, R17).
            subject["ask"] = {"question": f"This is Seldon's fault: {ref} failed the same checks again "
                f"({', '.join(checks)}) after the approach you chose. Expected them to pass; got the "
                "failure shown. We recommend Pause until Seldon is fixed.",
                "options": [option("Pause", "Pause here; your work is saved and can resume", "pause"),
                            option("Try again", "Retry this unit if its cause has been fixed", "direct"),
                            option("Leave this out", "Leave out this unit and its dependents", "drop")]}
            request(store, "UNIT", subject)
        elif subject := coach(store, provider, subject):
            request(store, "UNIT", subject)
    finally:
        store.close()


def option(label, means, effect):
    return {"label": label, "means": means, "effect": effect}


def record_fault(store, error):
    step = store.cursor().state
    cause = error.__cause__ if isinstance(error.__cause__, ProviderFailure) else error
    failure = f"{type(cause).__name__}: {cause}"
    shape = {"step": step, "failure": failure}
    frames = [f for f in traceback.extract_tb(cause.__traceback__)
              if Path(__file__).parent == Path(f.filename).parent
              and Path(f.filename).name != "processes.py"
              and not (Path(f.filename).name == "gitwork.py" and f.name == "git")]
    location = (f"src/seldon/{Path(frames[-1].filename).name}:{frames[-1].lineno} {frames[-1].name}"
                if frames else None)
    cause = store.record("step_failure", {**shape, "exceptionType": type(cause).__name__,
        "message": str(cause), "location": location})
    return cause, shape


def pause_ask(reason):
    return {"question": "This is Seldon's fault: recovery did not resolve this step. "
        f"We recommend Stop to pause here until Seldon is fixed.\n{reason}",
        "options": [option("Stop", "Pause here; your work is saved and can resume", "pause"),
                    option("Try again", "Retry this step if its cause has been fixed", "direct")]}


def step_fault(store, provider, error):
    cause, shape = record_fault(store, error)
    step, failure = shape["step"], shape["failure"]
    key = recovery.lineage(store)
    repeated = shape in store.recovery(key)["seen"]
    recovery.failed(store, cause, shape=shape)
    subject = {"step": step, "tried": [failure], "failure": failure}
    if repeated:
        subject["ask"] = pause_ask(str(error))
    else:
        subject = coach(store, provider, subject)
    if subject:
        request(store, step, subject)


def gate_ask(store, gate, subject):
    if "ask" in subject:
        return subject["ask"]
    if gate == "A":
        opts = [option("Build this", "Build the proposed vision", "approve")]
        opts += [{**o, "effect": "direct"} for o in subject["vision"]["alternatives"]]
    elif gate == "B":
        opts = [option("Build this plan", "Approve the plan", "approve"),
                option("Build it and do not stop at check-ins", "Approve and waive later check-ins", "waive")]
    elif gate == "CHECK-IN":
        opts = [option("approve", "Continue with the proposed next work", "approve"),
                option("ask for changes", "Describe the changes you want", "direct"),
                option("stop", "End the dream here", "stop")]
    elif gate == "C":
        opts = [option("accept", "Accept the demonstrated product", "approve"),
                option("ask for changes", "Describe the changes you want", "direct"),
                option("stop", "End without accepting the product", "stop")]
    else:
        return None  # A planning question is coached by decide before it reaches the user.
    return {"question": f"How should we continue at {gate}?", "options": opts}


def stop(store, words):
    with store.transaction():
        outcome = store.record("outcome", {"accepted": False, "verification_complete": False,
                                           "reason": words})
        advance(store, "stop", outcome, decision=None, failure="")


def request(store, gate, subject):
    with store.transaction():
        cursor = store.cursor()
        waived = gate == "CHECK-IN" and cursor.waived
        if cursor.decision and not waived:
            return
        if gate == "CHECK-IN":
            subject = {**subject, "open gaps": [b for _, _, b in store.records("open_gap")]}
        key = None
        if gate == "UNIT" or "step" in subject:
            key, subject = recovery.subject(store, gate, subject)
        ask = gate_ask(store, gate, subject)
        ref = store.record("decision_request", {"gate": gate, "subject": subject,
            **({"ask": ask} if ask else {}),
            "origin": store.read(cursor.kickoff)["setup"].get("userOrigin", "user")})
        if waived:
            approval = store.record("approval", {"request": ref, "subject": subject,
                "words": store.record("user_words", {"request": ref, "words": "waived by the user",
                    "origin": "user waiver"}), "choice": "approve"})
            store.cursor(replace(cursor, checkin=approval))
            return
        store.cursor(replace(cursor, decision=ref))
        if key:
            state = store.recovery(key)
            state["question"] = ref
            store.recovery(key, state)


def decide(store, provider, user):
    cursor = store.cursor()
    ref = cursor.decision
    item = store.read(ref)
    if "ask" not in item:
        subject = coach(store, provider, item["subject"])
        if subject is None:
            return
        with store.transaction():
            store.cursor(replace(store.cursor(), decision=None))
            request(store, item["gate"], subject)
        return decide(store, provider, user)
    words = user({"request": ref, **item})
    scripted = store.read(cursor.kickoff)["setup"].get("scriptedUser", False)
    if scripted and item["gate"] not in ("A", "B", "CHECK-IN", "C") and isinstance(words, dict):
        raise Waiting()
    raw = store.record("user_words", {"request": ref, "words": words, "origin": item["origin"]})
    direction, interpretation = str(words), None
    if isinstance(words, dict):
        if (set(words) != {"request", "subject", "choice"} or words["request"] != ref
                or words["subject"] != item["subject"]
                or words["choice"] not in ("approve", "revise", "decline")):
            raise ValueError("Decision response must bind to this displayed request and subject")
        choice = words["choice"]
        effect = {"approve": "approve", "revise": "direct", "decline": "stop"}[choice]
    else:
        options = item["ask"]["options"]
        normal = lambda text: "".join(text.casefold().split())
        picked = next((o for o in options if normal(o["label"]) == normal(words)), None)
        if picked is None:
            selection = {}
            def consume(source, answer):
                choice = answer["choice"]
                picked = next((o for o in options if o["label"] == choice), None)
                effect = picked["effect"] if picked else {
                    "Other": "direct", "Stop": "stop", "Waive": "waive", "Unclear": None}[choice]
                selection.update(picked=picked, choice=choice, effect=effect, why=answer["why"])
            interpretation, _ = inference.call(store, provider, questions.build("DECIDE", {
                "Decision": item["gate"], "Displayed subject": item["subject"],
                "Displayed options": item["ask"], "User's words": words},
                targets=[o["label"] for o in options] + (["Waive"] if item["gate"] == "CHECK-IN" else [])),
                consume=consume, field="choice")
            picked, choice, effect = (selection[k] for k in ("picked", "choice", "effect"))
            if effect is None:
                with store.transaction():
                    store.cursor(replace(store.cursor(), failure=selection["why"]))
                raise Waiting()
        else:
            choice, effect = picked["label"], picked["effect"]
        if picked:
            direction = picked["label"] + ": " + picked["means"]
    if effect == "pause":
        raise Waiting()  # Keep the displayed request and step intact for a later answer.
    with store.transaction():
        cursor = store.cursor()
        if cursor.decision != ref:
            raise ValueError("Decision changed; obtain a fresh response")
        directed = (store.record("decision_direction", {"words": direction, "verbatim": raw})
                    if effect == "direct" and direction != str(words) else raw)
        approval = store.record("approval", {"request": ref, "subject": item["subject"],
            "words": directed, "userWords": raw, "interpretation": interpretation, "scripted": scripted,
            "choice": "revise" if effect == "direct" else choice, "effect": effect})
        recovery.answered(store, item["subject"], raw)
        if effect == "stop":
            stop(store, words)
            return
        updates = {"decision": None, "failure": ""}
        if effect == "waive":
            updates["waived"] = True
        if item["gate"] == "UNIT":
            if effect == "drop":
                for owner in sorted(affected(store, item["subject"]["unit"])):
                    row = store.unit(owner)
                    if row.state == "WAITING":
                        advance(store, "asked", approval, unit=owner)
                    if store.unit(owner).state == "ASKED":
                        advance(store, "drop", approval, unit=owner, waiting="")
            else:
                again(store, item["subject"], raw, direction)
            store.cursor(replace(store.cursor(), **updates))
        elif "step" in item["subject"]:
            store.cursor(replace(cursor, **updates, requestedChange=directed))
        elif effect == "direct":
            advance(store, "revise" if item["gate"] in ("A", "B") else "steer", raw,
                    **updates, requestedChange=directed)
        elif item["gate"] in ("A", "B", "CHECK-IN"):
            field = {"A": "A", "B": "B", "CHECK-IN": "checkin"}[item["gate"]]
            if cursor.state == item["gate"]:
                advance(store, "approve", approval, **updates, **{field: approval})
            else:
                store.cursor(replace(cursor, **updates, **{field: approval}))
        elif item["gate"] == "C":
            from .run import eligible
            from .judgment import not_judged
            eligible(store)
            deferred = not_judged(store)
            outcome = store.record("outcome", {"accepted": not scripted, "scriptedUser": scripted,
                "verification_complete": not bool(deferred), "notJudged": deferred,
                "candidate": cursor.candidate, "approval": approval,
                "reason": "Scripted C, not owner acceptance" if scripted else "User accepted the demo commit",
                "nextAction": None, "failed": [], "blocked": []})
            advance(store, "scripted" if scripted else "approve", outcome, decision=None)
