"""C2/C18/C23/I1/I6; R7/R11: every lifecycle move is declared and recorded here."""

from dataclasses import replace


RUN = {
    "NEW": {"start": "ALIGN"},
    "ALIGN": {"aligned": "A"},
    "A": {"approve": "ARCHITECT", "revise": "ALIGN"},
    "ARCHITECT": {"outlined": "ARCH-REVIEW"},
    "ARCH-REVIEW": {"reviewed": "DETAIL", "reviewed-details": "REVIEW",
                    "contract-gap": "ARCHITECT"},
    "DETAIL": {"detailed": "REVIEW", "outline-changed": "ARCH-REVIEW",
               "contract-gap": "ARCHITECT"},
    "REVIEW": {"reviewed": "B", "gap": "REPAIR"},
    "REPAIR": {"area-gap": "DETAIL"},
    "B": {"approve": "EXECUTE", "revise": "REPAIR"},
    "EXECUTE": {"finished": "WAVE-VERIFY"},
    "WAVE-VERIFY": {"passed": "FLOW-JUDGE", "rework": "EXECUTE"},
    "CHECK-IN": {"approve": "PRODUCT-JUDGE"},
    "FLOW-JUDGE": {"satisfied": "CHECK-IN", "uncertain": "FLOW-FACTS",
                   "gap": "EXECUTE"},
    "FLOW-FACTS": {"resolved": "FLOW-JUDGE", "gap": "EXECUTE"},
    "PRODUCT-JUDGE": {"satisfied": "C", "uncertain": "PRODUCT-FACTS",
                      "gap": "EXECUTE"},
    "PRODUCT-FACTS": {"resolved": "PRODUCT-JUDGE", "gap": "EXECUTE"},
    "C": {"approve": "ACCEPTED", "scripted": "SCRIPTED_C"},
    "ACCEPTED": {}, "SCRIPTED_C": {}, "STOPPED": {},
}
UNIT = {
    "NEW": {"create": "WAITING"},
    "WAITING": {"ready": "READY", "recover": "WAITING", "asked": "ASKED"},
    "READY": {"start": "WORKING", "recover": "READY", "asked": "ASKED"},
    "WORKING": {"returned": "PROVING", "recover": "REWORK", "asked": "ASKED",
                "reopen": "WORKING", "wait-owner": "WAITING"},
    "PROVING": {"proven": "JUDGING", "recover": "REWORK", "asked": "ASKED"},
    "JUDGING": {"approved": "INTEGRATING", "recover": "REWORK", "asked": "ASKED"},
    "INTEGRATING": {"integrated": "DONE", "recover": "REWORK", "asked": "ASKED"},
    "DONE": {"recover": "REWORK", "asked": "ASKED"},
    "REWORK": {"retry": "WORKING"},
    "ASKED": {"again": "REWORK", "drop": "DROPPED"}, "DROPPED": {},
}


def advance(store, event, cause, unit=None, **updates):
    with store.transaction():
        row = store.unit(unit) if unit else store.cursor()
        table = UNIT if unit else RUN
        target = ("STOPPED" if not unit and event == "stop" and row.state not in
                  ("ACCEPTED", "SCRIPTED_C", "STOPPED") else table[row.state].get(event))
        if target is None:
            raise ValueError(f"Illegal {'unit' if unit else 'run'} move: {row.state} + {event}")
        if "state" in updates:
            raise ValueError("State is owned by the transition table")
        value = replace(row, state=target, **updates)
        if unit:
            store.unit(unit, value)
        else:
            store.cursor(value)
        store.record("transition", {"unit": unit, "input": row.state, "event": event,
                                    "next": target, "cause": cause})
        return value
