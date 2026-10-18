"""Single-use confirmation records for the planner's native MCP elicitation bridge.

Only the bridge may call completion after correlating a client response to its
outbound request. This leaf neither authenticates a human nor turns caller-supplied
words, identifiers or origin flags into trusted responses. Existing legacy approval
entry points are unchanged until the bridge/store integration is applied.
"""

from __future__ import annotations

import json
from uuid import uuid4

from codex_harness.core.plan_store import PlanStore, PlanStoreError
from codex_harness.core.spine import Spine

REQUEST = "confirmation-requested"
RESPONSE = "confirmation-responded"


def _records(store: PlanStore, kind: str):
    for row in store.connection.execute(
        "SELECT id, payload_json FROM events WHERE kind=? ORDER BY id DESC", (kind,)
    ):
        yield row[0], json.loads(row[1])


def prepare_confirmation(store: PlanStore, gate: str) -> dict:
    """Persist exactly what the native client will display for the current gate."""
    with store._transaction():
        if gate not in ("align", "vision-lock"):
            raise PlanStoreError("plan-gate-invalid", str(gate))
        phase = store.phase()
        if phase != gate:
            raise PlanStoreError("plan-confirmation-gate-mismatch", f"Current phase is {phase}")
        spine = Spine(store._data())
        visions = spine.ids("vision")
        if len(visions) != 1:
            raise PlanStoreError("plan-confirmation-card-unavailable", "One vision is required")
        vision = spine.node(visions[0])
        revision = spine.plan_revision()
        card = (
            f"Confirm {gate} for revision {revision}.\n"
            "Review the complete recorded understanding below. Approve only this gate and "
            "revision. Retain your complete words; put any conditions or requested changes "
            "in conditions. A conditional response does not approve this gate.\n\n"
            + json.dumps(vision, ensure_ascii=False, sort_keys=True, indent=2)
        )
        request = dict(
            request_id=uuid4().hex,
            gate=gate,
            phase=phase,
            plan_revision=revision,
            card=card,
        )
        store._event(REQUEST, request)
        return request


def complete_confirmation(
    store: PlanStore, request_id: str, action: str, content: dict | None = None
) -> dict:
    """Consume one bridge-correlated response, retaining its exact words/conditions.

    Provenance describes a client-reported user action, not authenticated natural
    person identity. Do not expose this method as a tool accepting arbitrary origin
    or response IDs. Declined, cancelled, conditional and stale requests grant no
    approval. This function does not infer intent from free-form prose.
    """
    with store._transaction():
        saved = next(
            (
                (event_id, row)
                for event_id, row in _records(store, REQUEST)
                if row["request_id"] == request_id
            ),
            None,
        )
        if saved is None:
            raise PlanStoreError("plan-confirmation-request-unknown", str(request_id))
        request_event, request = saved
        if any(row["request_id"] == request_id for _, row in _records(store, RESPONSE)):
            raise PlanStoreError("plan-confirmation-already-consumed", request_id)
        if action not in ("accept", "decline", "cancel"):
            raise PlanStoreError("plan-confirmation-response-invalid", "Unknown response action")
        if content is not None and not isinstance(content, dict):
            raise PlanStoreError("plan-confirmation-response-invalid", "Expected content object")
        if action == "accept" and (
            not isinstance(content, dict)
            or set(content) != {"approve", "statement", "conditions"}
            or type(content["approve"]) is not bool
            or not isinstance(content["statement"], str)
            or not content["statement"].strip()
            or not isinstance(content["conditions"], str)
        ):
            raise PlanStoreError(
                "plan-confirmation-response-invalid",
                "Expected approve boolean, complete statement and conditions strings",
            )
        stale = (
            store.phase() != request["phase"]
            or Spine(store._data()).plan_revision() != request["plan_revision"]
            or store.connection.execute(
                "SELECT 1 FROM events WHERE kind='phase-advanced' AND id>? LIMIT 1",
                (request_event,),
            ).fetchone()
            is not None
            or next(_records(store, REQUEST))[1]["request_id"] != request_id
        )
        state = "stale" if stale else action
        approval_id = None
        if not stale and action == "accept":
            if content["conditions"] != "":
                state = "conditional"
            elif not content["approve"]:
                state = "declined"
            else:
                approval_id = store._approve(
                    request["gate"], "mcp-client-elicitation", content["statement"]
                )
                state = "approved"
        outcome = dict(
            request_id=request_id,
            gate=request["gate"],
            phase=request["phase"],
            plan_revision=request["plan_revision"],
            action=action,
            content=content,
            state=state,
            approval_id=approval_id,
            provenance="mcp-client-elicitation",
            human_identity_verified=False,
        )
        store._event(RESPONSE, outcome)
        return outcome
