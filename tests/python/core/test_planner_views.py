"""Deterministic chat cards and status, with no providers or filesystem."""


def status(phase="align"):
    return {
        "name": "tip-split",
        "plan_revision": "0123456789abcdef",
        "phase": phase,
        "open_clarifications": 2,
        "pending_gate": "approval:align" if phase == "align" else None,
    }
