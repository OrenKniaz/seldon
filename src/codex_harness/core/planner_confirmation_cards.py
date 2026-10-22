"""Compact display and durable resume for native planning confirmations.

Transport loss is not a user response. This module never grants approval or
authenticates chat quotes; the existing correlated bridge remains the writer.
"""

from __future__ import annotations

from codex_harness.core.planner_confirmations import (
    REQUEST,
    RESPONSE,
    _records,
    prepare_confirmation,
)
from codex_harness.core.planner_vision import vision_projection
from codex_harness.core.spine import Spine


def pending_confirmation(store, gate):
    """Return only the latest unanswered display, with its original exact scope."""
    latest = next(_records(store, REQUEST), None)
    if latest is None:
        return None
    event_id, request = latest
    if (
        request["gate"] != gate
        or request["phase"] != store.phase()
        or request["plan_revision"] != Spine(store._data()).plan_revision()
        or any(row["request_id"] == request["request_id"] for _, row in _records(store, RESPONSE))
        or store.connection.execute(
            "SELECT 1 FROM events WHERE kind='phase-advanced' AND id>? LIMIT 1", (event_id,)
        ).fetchone()
        is not None
    ):
        return None
    return request


def resume_or_prepare_confirmation(store, gate):
    """Reuse a pending display atomically; callers allocate a fresh transport ID."""
    return prepare_confirmation(store, gate, resume_pending=True)


def _excerpt(value):
    text = " ".join(str(value).split())
    return text if len(text) <= 150 else text[:147] + "..."


def compact_card(request):
    """Keep controls reachable; full scope stays in the stored card and node view."""
    vision = request["vision"]
    sections = {item["label"]: item["text"] for item in vision_projection(vision)["sections"]}
    return (
        f"Confirm {request['gate']} · revision {request['plan_revision']}\n"
        f"Vision: {_excerpt(sections['Vision'])}\n"
        f"Users: {_excerpt(sections['Users'])}\n"
        f"Scope: {_excerpt(sections['Scope in'])}\n"
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
