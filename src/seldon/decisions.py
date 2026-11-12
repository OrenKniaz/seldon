"""C8/C11/C21/I6; R11/R12: subject-bound approvals, plan repair and reviewed criterion levels."""

from copy import deepcopy
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
            event = "approve-final" if cursor.state == "B" and cursor.finalCommit else "approve"
            advance(store, event, approval, decision=None, **{fields[cursor.state]: approval},
                    **({"pendingJudge": None} if event == "approve-final" else {}))
        elif cursor.state == "C":
            from .run import eligible
            eligible(store)
            outcome = store.record("outcome", {"accepted": not scripted, "scriptedUser": scripted,
                "verification_complete": True, "candidate": cursor.candidate, "approval": approval,
                "reason": "Scripted C, not owner acceptance" if scripted else "User accepted the demo commit",
                "nextAction": None, "failed": [], "blocked": []})
            advance(store, "scripted" if scripted else "approve", outcome, decision=None)
        else:
            raise ValueError("No approval gate in " + cursor.state)


def repair(store, provider):
    from . import plan
    cursor = store.cursor()
    marking = store.read(cursor.pendingJudge) if cursor.pendingJudge else None
    if cursor.B and not marking:
        raise ValueError("Post-B plan steering is deferred to M3")
    if cursor.repairs >= 3:
        raise ValueError("Three pre-B plan repairs exhausted: " + cursor.failure)
    vision = store.read(cursor.vision)["answer"]

    def check(answer):
        document = plan.create(vision, answer)
        if marking:
            expected = deepcopy(store.plan(cursor.revision))
            expected["nodes"][marking["subject"]]["body"]["level"] = "unit"
            if plan.add_guide(document) != expected:
                raise ValueError("Only the named criterion's level may change to unit; "
                                 "preserve the approved plan")
        if plan.add_guide(document) == store.plan(cursor.revision):
            raise ValueError("Repair changes nothing; resolve the named review gap")

    facts = {
        "Original request": store.read(cursor.kickoff)["words"], "Approved vision": vision,
        "Plan": store.plan(cursor.revision), "Review gap": cursor.failure}
    if cursor.requestedChange:
        facts["Requested change"] = store.read(cursor.requestedChange)["words"]
    result, answer = inference.call(store, provider, questions.build("REPAIR", facts), check=check)
    document = plan.add_guide(plan.create(vision, answer))
    with store.transaction():
        revision = store.revision(document, cursor.revision)
        advance(store, "repaired", result, revision=revision, repairs=cursor.repairs + 1,
                review=None, decision=None, requestedChange=None,
                **({"B": None} if marking else {}))
