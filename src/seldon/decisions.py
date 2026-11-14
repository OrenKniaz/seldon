"""C8/C21/I6; R11/R12: subject-bound approvals."""

from dataclasses import replace

from . import inference, plan, questions
from .states import advance


class Waiting(Exception):
    """The next words belong to a future chat turn."""


def ask_unit(store):
    if store.cursor().decision:
        return
    document = store.plan(store.cursor().revision)
    for ref, row in store.units().items():
        if row.state == "ASKED":
            affected = {ref}
            while added := {r for r, u in store.units().items() if r not in affected
                    and affected.intersection(plan.prerequisites(document, r) + u.waitingFor)}:
                affected.update(added)
            request(store, "UNIT", {"unit": ref,
                "tried": [b["failure"] for _, _, b in store.records("unit_failure") if b["unit"] == ref],
                "affects": sorted(affected - {ref}), "recommendation": "",
                "drop": "Dropping is not available yet; try again, give direction, or use stop",
                "options": ["Try again", "Give direction", "Stop the dream"]})
            return


def stop(store, words):
    with store.transaction():
        outcome = store.record("outcome", {"accepted": False, "verification_complete": False,
                                           "reason": words})
        advance(store, "stop", outcome, decision=None, failure="")


def request(store, gate, subject):
    with store.transaction():
        cursor = store.cursor()
        ref = store.record("decision_request", {"gate": gate, "subject": subject,
            "origin": store.read(cursor.kickoff)["setup"].get("userOrigin", "user")})
        store.cursor(replace(cursor, decision=ref))


def decide(store, provider, user):
    cursor = store.cursor()
    ref = cursor.decision
    item = store.read(ref)
    words = user({"request": ref, **item})
    scripted = store.read(cursor.kickoff)["setup"].get("scriptedUser", False)
    if scripted and item["gate"] not in ("A", "B", "CHECK-IN", "C") and isinstance(words, dict):
        raise Waiting()
    raw = store.record("user_words", {"request": ref, "words": words, "origin": item["origin"]})
    if isinstance(words, dict):
        if (set(words) != {"request", "subject", "choice"} or words["request"] != ref
                or words["subject"] != item["subject"]
                or words["choice"] not in ("approve", "revise", "decline")):
            raise ValueError("Decision response must bind to this displayed request and subject")
        answer, interpretation = words, None
    else:
        interpretation, answer = inference.call(store, provider, questions.build("DECIDE", {
            "Decision": item["gate"], "Displayed subject": item["subject"], "User's words": words}))
    with store.transaction():
        if store.cursor().decision != ref:
            raise ValueError("Decision changed; obtain a fresh response")
        choice = answer["choice"]
        approval = store.record("approval", {"request": ref, "subject": item["subject"],
            "words": raw, "interpretation": interpretation, "scripted": scripted, "choice": choice})
        fields = {"A": "A", "B": "B", "CHECK-IN": "checkin"}
        hold = (item["gate"] == "UNIT" and choice == "decline" or choice == "revise"
                and item["gate"] in ("CHECK-IN", "C"))
        if hold:
            store.cursor(replace(cursor, failure=(item["subject"]["drop"] if item["gate"] == "UNIT" else
                "Changes after B arrive with steering (M3); approve or stop")))
        elif item["gate"] == "UNIT":
            ref = item["subject"]["unit"]
            unit = store.unit(ref)
            advance(store, "again", raw, unit=ref, recoveries=0, thread=None,
                    base=unit.base if unit.worktree else cursor.candidate, callFolder=None, callAttempt=None,
                    failure=unit.failure + "\nUser direction: " + str(words) if choice == "revise" else unit.failure)
            store.cursor(replace(cursor, decision=None, failure=""))
        elif choice == "decline":
            stop(store, words)
        elif "step" in item["subject"]:
            store.cursor(replace(cursor, decision=None, failure=""))
        elif choice == "revise":
            advance(store, "revise", raw, decision=None, failure="", requestedChange=raw)
        elif cursor.state in fields:
            advance(store, "approve", approval, decision=None, **{fields[cursor.state]: approval})
        elif cursor.state == "C":
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

    if hold:
        raise Waiting()
