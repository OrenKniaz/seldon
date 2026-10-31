"""Compact display and durable resume for native planning confirmations.

Transport loss is not a user response. This module never grants approval or
authenticates chat quotes; the existing correlated bridge remains the writer.
"""

from __future__ import annotations


def compact_card(request):
    """Native and chat display the same complete persisted approval subject."""
    return request["card"]


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
