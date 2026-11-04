"""Compact display and durable resume for native planning confirmations.

Transport loss is not a user response. This module never grants approval or
authenticates chat quotes; the existing correlated bridge remains the writer.
"""


def pending_outcome(request):
    """Truthful timeout/disconnect result, without fabricating a user cancellation."""
    return {
        "request_id": request["request_id"],
        "gate": request["gate"],
        "phase": request["phase"],
        "plan_revision": request["plan_revision"],
        "state": "pending",
        "approval_id": None,
        "provenance": "no-client-response",
        "human_identity_verified": False,
    }


def pending_decisions(store):
    """Reload pending human replies without consuming them or reconstructing meaning."""
    from codex_harness.core.approvals import records

    consumed = {r["request_id"] for r in records(store, "confirmation-responded")}
    pending = {}
    for reply in records(store, "confirmation-pending-judgment"):
        key = reply["request_id"]
        if key not in consumed and key not in pending:
            pending[key] = dict(reply, judgment=store.judgment(reply["judgment"]["id"]))
    return list(pending.values())


def pending_judgments(store):
    """Expose the latest consumer question per purpose after a reload or batch edit."""
    from codex_harness.core.approvals import records

    result, seen = [], set()
    for request in records(store, "semantic-assessment-requested"):
        judgment = store.judgment(request["judgmentId"])
        purpose = judgment["question"]
        if purpose in seen:
            continue
        seen.add(purpose)
        if judgment["status"] == "pending-judgment":
            result.append({key: judgment[key] for key in ("id", "question", "status", "current", "disposition", "positiveCredit", "concreteGap")})
    return result
