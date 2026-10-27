"""Compact display and durable resume for native planning confirmations.

Transport loss is not a user response. This module never grants approval or
authenticates chat quotes; the existing correlated bridge remains the writer.
"""

from __future__ import annotations

from codex_harness.core.planner_vision import vision_projection


def _excerpt(value):
    text = " ".join(str(value).split())
    return text if len(text) <= 150 else text[:147] + "..."


def compact_card(request):
    """Keep controls reachable; full scope stays in the stored card and node view."""
    if request["gate"] == "vision-lock":
        return f"Revision: {request['plan_revision']}\n{request['card']}"
    vision = request["vision"]
    sections = {item["label"]: item["text"] for item in vision_projection(vision)["sections"]}
    return (
        f"Confirm {request['gate']} · revision {request['plan_revision']}\n"
        f"Vision: {_excerpt(sections['Vision'])}\n"
        f"Users: {_excerpt(sections['Users'])}\n"
        f"Scope in: {_excerpt(sections['Scope in'])}\n"
        f"Done: {_excerpt(sections['Done'])}\n"
        f"Summary only. Review full details: harness_show_node id={vision['id']}. "
        "Approve the full displayed revision, or request changes in Conditions. "
        "Leave Conditions empty only for unconditional approval."
    )


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
