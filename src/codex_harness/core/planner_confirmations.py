"""Native/chat gate records and caller-declared reviewer-choice scope bindings.

Only the native bridge completes native requests after correlating a response to its
outbound request. This leaf neither authenticates a human nor turns caller-supplied
words into native responses. Explicit bound chat decisions have separately labelled,
lower-assurance authority. Legacy quote entry points stay refused. Neither channel
authenticates a human; reviewer-choice records carry no native gate credit.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from uuid import uuid4

from codex_harness.core.plan_store import PHASES, PlanStore, PlanStoreError
from codex_harness.core.spine import Spine

REQUEST = "confirmation-requested"
RESPONSE = "confirmation-responded"
REVIEWER_CONFIRMATION = "reviewer-selection-confirmed"
CHAT = "chat-confirmation"
NATIVE = "mcp-client-elicitation"


def _chat_question(request, revision):
    request.update(channel=CHAT, choices=["Approve", "No", "Cancel", "Changes: <text>"])
    request["card"] += (
        f"\nChat confirmation {request['request_id']} for revision {revision}.\n"
        "Reply exactly Approve, No, Cancel, or Changes: <text>. No response grants nothing. "
        "The caller must quote your latest actual external chat message verbatim, with its "
        "SHA-256 and an actor label. Actor identity and message source are unverified; "
        "the hash binds the supplied quote and does not authenticate its author."
    )
    return request


def _chat_evidence(request_id, expected_revision, quoted_message, quote_sha256, actor_label):
    if (
        not isinstance(quoted_message, str)
        or not isinstance(actor_label, str)
        or not actor_label.strip()
    ):
        raise ValueError("chat-confirmation-quote-and-actor-required")
    if hashlib.sha256(quoted_message.encode("utf-8")).hexdigest() != quote_sha256:
        raise ValueError("chat-confirmation-quote-hash-mismatch")
    choice = quoted_message.strip()
    conditions = ""
    if choice.casefold() not in {"approve", "no", "cancel"}:
        if not choice.casefold().startswith("changes:") or not choice[8:].strip():
            raise ValueError("chat-confirmation-choice-unclear: use a displayed choice")
        conditions = choice[8:].strip()
    action = "cancel" if choice.casefold() == "cancel" else "accept"
    content = dict(
        approve=choice.casefold() == "approve", statement=quoted_message, conditions=conditions
    )
    evidence = dict(
        request_id=request_id,
        revision=expected_revision,
        quoted_message=quoted_message,
        quote_sha256=quote_sha256,
        actor_label=actor_label,
        actor_identity_verified=False,
        source_claim="latest-external-chat-message",
        source_verified=False,
    )
    return action, content, evidence


def _response_channel(request, response, revision):
    channel = request.get("channel", NATIVE)
    if channel not in {NATIVE, CHAT} or response.get("provenance") != channel:
        return None
    if channel == CHAT:
        try:
            saved = response["chat"]
            action, content, evidence = _chat_evidence(
                request["request_id"],
                revision,
                saved["quoted_message"],
                saved["quote_sha256"],
                saved["actor_label"],
            )
        except (KeyError, TypeError, ValueError):
            return None
        if (
            saved != evidence
            or response.get("action") != action
            or response.get("content") != content
        ):
            return None
    return channel


def reviewer_selection_scope(node: dict, catalog: dict, author_sessions: dict) -> str:
    """Bind the complete normalized proposal, not caller-provided authority labels."""
    from codex_harness.core import intake

    proposal = deepcopy(node)
    proposal["fields"]["confirmation"] = None
    proposal["fields"] = intake.validate_selection(catalog, proposal["fields"], author_sessions)[
        "selection"
    ]
    return intake.canonical_digest(
        {
            "node": proposal,
            "authorSessions": author_sessions,
            "catalog": {"id": catalog["id"], "digest": catalog["digest"]},
        }
    )


def reviewer_confirmation_target(store: PlanStore, before: Spine, node_id: str) -> tuple:
    """Resolve only the current recorded choice and current catalog in the transaction."""
    if not isinstance(node_id, str) or node_id not in before.ids("decision"):
        raise PlanStoreError("plan-reviewer-confirmation-target-invalid", str(node_id))
    node = before.node(node_id)
    event = store._latest_draft_event("reviewer-selection-recorded")
    if (
        node["fields"].get("schema") != "dot-reviewer-selection-v1"
        or event is None
        or event["nodeId"] != node_id
        or event["nodeRevisions"].get(node_id) != before.revision(node_id)
    ):
        raise PlanStoreError("plan-reviewer-confirmation-target-stale", node_id)
    catalog = store._latest_draft_event("reviewer-catalog-recorded")
    if catalog is None:
        raise PlanStoreError("plan-catalog-unavailable", "Record an explicit observation first")
    # Includes exact catalog ID/digest and all existing compatibility validation.
    scope = reviewer_selection_scope(node, catalog["catalog"], event["authorSessions"])
    if node["fields"]["confirmation"] is not None:
        raise PlanStoreError("plan-reviewer-confirmation-already-recorded", node_id)
    if any(
        record["nodeId"] == node_id and record["scopeDigest"] == scope
        for _, record in _records(store, REVIEWER_CONFIRMATION)
    ):
        raise PlanStoreError("plan-reviewer-confirmation-already-recorded", node_id)
    return node, catalog["catalog"], event["authorSessions"], scope


def reviewer_record_authority(event_id: int, record: dict) -> dict:
    return {
        "approvalId": record["recordId"],
        "eventId": event_id,
        "origin": "user",
        "statement": record["statement"],
        "sourceRef": f"reviewer-confirmation:event:{event_id}",
        "authorizedScopeDigest": record["scopeDigest"],
    }


def validate_reviewer_record_scope(
    store: PlanStore, node: dict, catalog: dict, author_sessions: dict
) -> None:
    """Validate this producer's records; legacy supplied provenance stays unverified."""
    from codex_harness.core import intake

    intake.validate_selection(catalog, node["fields"], author_sessions)
    authority = node["fields"].get("confirmation")
    if authority is None:
        return
    records = list(_records(store, REVIEWER_CONFIRMATION))
    known = next(
        (
            (event_id, record)
            for event_id, record in records
            if authority["approvalId"] == record["recordId"] or authority["eventId"] == event_id
        ),
        None,
    )
    if known is None and not authority["sourceRef"].startswith("reviewer-confirmation:event:"):
        return  # Existing caller-supplied authority is not promoted to generated evidence.
    if known is None:
        raise PlanStoreError("plan-reviewer-confirmation-record-invalid", node["id"])
    event_id, record = known
    if (
        authority != reviewer_record_authority(event_id, record)
        or record["nodeId"] != node["id"]
        or record["scopeDigest"] != reviewer_selection_scope(node, catalog, author_sessions)
        or record["provenance"] != "caller-declared-user-action"
        or record["human_identity_verified"] is not False
    ):
        raise PlanStoreError("plan-reviewer-confirmation-scope-stale", node["id"])


def _understanding(vision: dict) -> str:
    from codex_harness.core.planner_vision import render_vision, vision_projection

    return render_vision(vision_projection(vision))


def _records(store: PlanStore, kind: str):
    for row in store.connection.execute(
        "SELECT id, payload_json FROM events WHERE kind=? ORDER BY id DESC", (kind,)
    ):
        yield row[0], json.loads(row[1])


def approval_credit(store: PlanStore, gate: str, revision: str) -> dict | None:
    """Read original correlated records; supplied words/by fields alone earn no credit."""
    requests = {row["request_id"]: row for _, row in _records(store, REQUEST)}
    latest = next((row for _, row in _records(store, REQUEST) if row["gate"] == gate), None)
    for _, response in _records(store, RESPONSE):
        if response.get("state") != "approved" or response.get("gate") != gate:
            continue
        request = requests.get(response.get("request_id"))
        content = response.get("content")
        if (
            not request
            or request != latest
            or request.get("gate") != gate
            or response.get("phase") != request.get("phase")
            or request.get("plan_revision") != revision
            or response.get("plan_revision") != revision
            or response.get("action") != "accept"
            or not _response_channel(request, response, revision)
            or not isinstance(content, dict)
            or content.get("approve") is not True
            or content.get("conditions") != ""
        ):
            continue
        approval = store.connection.execute(
            "SELECT * FROM approvals WHERE id=?", (response.get("approval_id"),)
        ).fetchone()
        if (
            approval
            and approval["gate"] == gate
            and approval["plan_revision"] == revision
            and approval["by"] == _response_channel(request, response, revision)
            and approval["statement"] == content.get("statement")
        ):
            return dict(approval)
    return None


def prepare_confirmation(store: PlanStore, gate: str, *, _chat=False) -> dict:
    """Persist exactly what the native client will display for the current gate."""
    with store._transaction():
        if gate not in ("align", "vision-lock"):
            raise PlanStoreError("plan-gate-invalid", str(gate))
        phase = store.phase()
        if phase != gate and not (
            gate == "vision-lock" and PHASES.index(phase) > PHASES.index("vision-lock")
        ):
            raise PlanStoreError("plan-confirmation-gate-mismatch", f"Current phase is {phase}")
        spine = Spine(store._data())
        visions = spine.ids("vision")
        if len(visions) != 1:
            raise PlanStoreError("plan-confirmation-card-unavailable", "One vision is required")
        vision = spine.node(visions[0])
        revision = spine.plan_revision()
        heading = f"Confirm {gate}" if phase == gate else f"Renew vision lock during {phase}"
        card = (
            f"{heading} for revision {revision}.\n"
            "Review the complete recorded understanding below. Approve only this gate and "
            "revision. Retain your complete words; put any conditions or requested changes "
            "in conditions. A conditional response does not approve this gate.\n\n"
            + _understanding(vision)
        )
        request = dict(
            request_id=uuid4().hex,
            gate=gate,
            phase=phase,
            plan_revision=revision,
            card=card,
            vision=vision,
        )
        if _chat:
            _chat_question(request, revision)
        store._event(REQUEST, request)
        return request


def prepare_chat_confirmation(store: PlanStore, gate: str) -> dict:
    """Offer a fresh question without interpreting a previous native decline."""
    return prepare_confirmation(store, gate, _chat=True)


def complete_confirmation(
    store: PlanStore, request_id: str, action: str, content: dict | None = None
) -> dict:
    return _complete_confirmation(store, request_id, action, content)


def complete_chat_confirmation(
    store, request_id, *, gate, expected_revision, quoted_message, quote_sha256, actor_label
):
    """Caller-mediated authority; neither latest-message origin nor actor is authenticated."""
    action, content, evidence = _chat_evidence(
        request_id, expected_revision, quoted_message, quote_sha256, actor_label
    )
    return _complete_confirmation(
        store, request_id, action, content, chat=evidence, expected_gate=gate
    )


def _complete_confirmation(
    store, request_id, action, content=None, *, chat=None, expected_gate=None
):
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
        channel = CHAT if chat is not None else NATIVE
        if request.get("channel", NATIVE) != channel:
            raise PlanStoreError("plan-confirmation-channel-mismatch", request_id)
        if chat is not None and chat["revision"] != request["plan_revision"]:
            raise PlanStoreError("plan-confirmation-revision-mismatch", request_id)
        if chat is not None and expected_gate != request["gate"]:
            raise PlanStoreError("plan-confirmation-gate-mismatch", request_id)
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
                approval_id = store._approve(request["gate"], channel, content["statement"])
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
            provenance=channel,
            human_identity_verified=False,
        )
        if chat is not None:
            outcome["chat"] = chat
        store._event(RESPONSE, outcome)
        return outcome
