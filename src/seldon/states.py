"""C2/C18/C23/I1/I6; R7/R11: every lifecycle move is declared and recorded here."""

from dataclasses import replace


RUN = {
    "NEW": {"start": "ALIGN"},
    "ALIGN": {"aligned": "A", "fail": "INCOMPLETE"},
    "A": {"approve": "PLAN", "revise": "ALIGN", "fail": "INCOMPLETE"},
    "PLAN": {"planned": "REVIEW", "fail": "INCOMPLETE"},
    "REVIEW": {"adequate": "B", "gap": "REPAIR", "fail": "INCOMPLETE"},
    "REPAIR": {"repaired": "REVIEW", "fail": "INCOMPLETE"},
    "B": {"approve": "EXECUTE", "approve-final": "PRODUCT-JUDGE",
          "revise": "REPAIR", "fail": "INCOMPLETE"},
    "EXECUTE": {"finished": "WAVE-VERIFY", "fail": "INCOMPLETE"},
    "WAVE-VERIFY": {"passed": "CHECK-IN", "rework": "EXECUTE", "fail": "INCOMPLETE"},
    "CHECK-IN": {"approve": "DEMO-JUDGE", "fail": "INCOMPLETE"},
    "DEMO-JUDGE": {"satisfied": "PRODUCT-JUDGE", "uncertain": "DEMO-FACTS",
                   "gap": "EXECUTE", "fail": "INCOMPLETE"},
    "DEMO-FACTS": {"resolved": "DEMO-JUDGE", "gap": "EXECUTE", "fail": "INCOMPLETE"},
    "PRODUCT-JUDGE": {"satisfied": "C", "uncertain": "PRODUCT-FACTS",
                      "gap": "EXECUTE", "fail": "INCOMPLETE"},
    "PRODUCT-FACTS": {"resolved": "PRODUCT-JUDGE", "gap": "EXECUTE",
                      "plan-gap": "REPAIR", "fail": "INCOMPLETE"},
    "C": {"approve": "ACCEPTED", "scripted": "SCRIPTED_C", "fail": "INCOMPLETE"},
    "ACCEPTED": {}, "SCRIPTED_C": {}, "INCOMPLETE": {},
}
UNIT = {
    "NEW": {"create": "WAITING"},
    "WAITING": {"ready": "READY", "blocked": "BLOCKED"},
    "READY": {"start": "WORKING", "recover": "READY", "exhausted": "FAILED"},
    "WORKING": {"returned": "PROVING", "recover": "REWORK", "exhausted": "FAILED",
                "reopen": "WORKING"},
    "PROVING": {"proven": "JUDGING", "recover": "REWORK", "exhausted": "FAILED"},
    "JUDGING": {"approved": "INTEGRATING", "recover": "REWORK", "exhausted": "FAILED"},
    "INTEGRATING": {"integrated": "DONE", "recover": "REWORK", "exhausted": "FAILED"},
    "DONE": {"recover": "REWORK", "exhausted": "FAILED"},
    "REWORK": {"retry": "WORKING", "blocked": "BLOCKED"},
    "FAILED": {}, "BLOCKED": {},
}


def advance(store, event, cause, unit=None, **updates):
    with store.transaction():
        row = store.unit(unit) if unit else store.cursor()
        table = UNIT if unit else RUN
        if event not in table[row.state]:
            raise ValueError(f"Illegal {'unit' if unit else 'run'} move: {row.state} + {event}")
        if "state" in updates:
            raise ValueError("State is owned by the transition table")
        target = table[row.state][event]
        value = replace(row, state=target, **updates)
        if unit:
            store.unit(unit, value)
        else:
            store.cursor(value)
        store.record("transition", {"unit": unit, "input": row.state, "event": event,
                                    "next": target, "cause": cause})
        return value
