"""C8/C21/I6; R11/R12: subject-bound approvals."""

from dataclasses import replace

from . import inference, questions
from .states import advance


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
        if answer["choice"] != "approve":
            if answer["choice"] == "revise" and cursor.state in ("A", "B"):
                advance(store, "revise", raw, decision=None, failure=str(answer), requestedChange=raw)
                return
            raise ValueError("User did not approve " + item["gate"] + ": " + str(answer))
        scripted = store.read(cursor.kickoff)["setup"].get("scriptedUser", False)
        approval = store.record("approval", {"request": ref, "subject": item["subject"],
            "words": raw, "interpretation": interpretation, "scripted": scripted})
        fields = {"A": "A", "B": "B", "CHECK-IN": "checkin"}
        if cursor.state in fields:
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
        else:
            raise ValueError("No approval gate in " + cursor.state)
