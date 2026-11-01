"""Native and chat adapters for the same persisted A/B subjects."""

import hashlib
from uuid import uuid4

from codex_harness.core import approvals
from codex_harness.core.approvals import records
from codex_harness.core.plan_store import PHASES, PlanStoreError
from codex_harness.core.spine import Spine

REQUEST = "confirmation-requested"
RESPONSE = "confirmation-responded"
CHAT = "chat-confirmation"
NATIVE = "mcp-client-elicitation"
CHAT_REVIEW_SCOPE = {
    "reviewer_node_id": {"type": "string", "minLength": 1},
}
CHAT_SCOPE = ("execution", *CHAT_REVIEW_SCOPE)


def _chat_evidence(request_id, expected_revision, quoted_message, quote_sha256, actor_label):
    if (
        not isinstance(quoted_message, str)
        or not isinstance(actor_label, str)
        or not actor_label.strip()
    ):
        raise ValueError("chat-confirmation-quote-and-actor-required")
    expected = hashlib.sha256(quoted_message.encode("utf-8")).hexdigest()
    if not isinstance(quote_sha256, str) or quote_sha256.strip().lower() != expected:
        raise ValueError(
            "chat-confirmation-quote-hash-mismatch: quote_sha256 must be the hex SHA-256 of "
            "quoted_message's exact UTF-8 bytes (no added newline)"
        )
    choice = quoted_message.strip()
    conditions = ""
    if not choice:
        raise ValueError("chat-confirmation-choice-required")
    if choice.casefold().startswith("changes:"):
        conditions = choice[8:].strip() or choice
    action = "cancel" if choice.casefold() == "cancel" else "accept"
    content = dict(
        approve=choice.casefold() not in {"no", "cancel"},
        statement=quoted_message,
        conditions=conditions,
    )
    evidence = dict(
        request_id=request_id,
        revision=expected_revision,
        quoted_message=quoted_message,
        quote_sha256=expected,
        actor_label=actor_label,
        actor_identity_verified=False,
        source_claim="latest-external-chat-message",
        source_verified=False,
    )
    return action, content, evidence


def planning_prerequisites(
    store, execution=None, *, reviewer_node_id=None, catalog=None, reviewer_node=None
):
    """One read-only B preflight; collect independent failures before any paid start."""
    from codex_harness.core.intake import validate_selection
    from codex_harness.core.planner_execution import execution_scope
    from codex_harness.core.planner_review import delivery_review_selection, selection_inputs

    action, missing = {}, []
    try:
        approvals.require_approval(store, "A")
    except PlanStoreError as error:
        missing.append(error.code)
    selected, node, catalog = selection_inputs(store, catalog=catalog, node=reviewer_node)
    if reviewer_node_id is not None and reviewer_node_id != (selected or {}).get("nodeId"):
        missing.append("reviewer-selection-current-node-required")
    if execution is None:
        action = (approvals.latest_request(store, "B") or {}).get("proposedAction", {})
    if execution is not None or "execution" not in action:
        required = {"expected_revision", "workers", "call_cap"} - (execution or {}).keys()
        missing.extend("execution." + key for key in sorted(required))
        if not required:
            try:
                action["execution"] = execution_scope(store, **execution, _candidate=True)
            except (ValueError, TypeError) as error:
                missing.append(str(error))
    if node is None:
        missing.append("reviewer-selection-required-before-B: propose exact delivery reviewers")
    else:
        sessions = selected.get(
            "authorSessions", {"implementationSessionIds": [], "reviewerSessionIds": {}}
        )
        try:
            validate_selection(catalog, node["fields"], sessions)
        except (ValueError, KeyError, TypeError) as error:
            missing.append(str(error))
        try:
            delivery_review_selection(node["fields"])
        except (ValueError, KeyError, TypeError) as error:
            missing.append(str(error))
    return action, list(dict.fromkeys(missing))


def prepare_confirmation(
    store,
    gate,
    *,
    _chat=False,
    _expected_request_id=None,
    execution=None,
    reviewer_node_id=None,
):
    if gate not in {"align", "vision-lock", "align+vision-lock", "plan-execution"}:
        raise PlanStoreError("plan-gate-invalid", str(gate))
    semantic = Spine(store._data()).semantic
    if semantic and gate != "plan-execution":
        gate = "align+vision-lock"
    with store._transaction():
        if _expected_request_id is not None:
            latest = next((r for r in records(store, REQUEST) if r["gate"] == gate), None)
            if latest is None or latest["request_id"] != _expected_request_id:
                raise ValueError("confirmation-request-superseded")
        phase = store.phase()
        if (
            not semantic
            and gate != "plan-execution"
            and phase != {"align+vision-lock": "align"}.get(gate, gate)
        ):
            if gate not in {"vision-lock", "align+vision-lock"} or PHASES.index(
                phase
            ) < PHASES.index("vision-lock"):
                raise PlanStoreError("plan-confirmation-gate-mismatch", f"Current phase is {phase}")
        kind = "B" if gate == "plan-execution" else "A"
        action = {}
        if kind == "B":
            action, missing = planning_prerequisites(
                store, execution, reviewer_node_id=reviewer_node_id
            )
            if missing:
                raise PlanStoreError(
                    "plan-b-prerequisites-missing", "Missing B prerequisites: " + "; ".join(missing)
                )
            from codex_harness.core.provider_call_budget import usage

            previous = approvals.current(store, "B")
            if previous and usage(store, "review") >= previous["subject"]["reviewAllowance"]:
                action["renewReviews"] = True
        else:
            from codex_harness.core.planner_vision import expansion_status

            vision = Spine(store._data()).node(Spine(store._data()).ids("vision")[0])
            if expansion_status(vision)["gaps"]:
                raise PlanStoreError("plan-expansion-required", "Prepare expansion before consent")
        request = dict(
            **approvals.proposal(store, kind, action),
            request_id=uuid4().hex,
            gate=gate,
            phase=phase,
            plan_revision=store._revision(),
            proposedAction=action,
            vision=Spine(store._data()).node(Spine(store._data()).ids("vision")[0]),
            channel=CHAT if _chat else NATIVE,
            choices=approvals.CHOICES,
        )
        consumed = {r["request_id"] for r in records(store, RESPONSE)}
        latest = approvals.latest_request(store, kind)
        if (
            (_chat or kind == "B")
            and latest
            and latest["request_id"] not in consumed
            and all(latest.get(k) == request[k] for k in ("fingerprint", "gate", "channel"))
        ):
            return latest
        store._event(REQUEST, request)
        return request


def prepare_chat_confirmation(store, gate, **scope):
    return prepare_confirmation(store, gate, _chat=True, **scope)


def complete_confirmation(store, request_id, action, content=None):
    return _complete_confirmation(store, request_id, action, content)


def complete_chat_confirmation(
    store, request_id, *, gate, expected_revision, quoted_message, quote_sha256, actor_label
):
    action, content, evidence = _chat_evidence(
        request_id, expected_revision, quoted_message, quote_sha256, actor_label
    )
    return _complete_confirmation(
        store, request_id, action, content, chat=evidence, expected_gate=gate
    )


def _complete_confirmation(
    store, request_id, action, content=None, *, chat=None, expected_gate=None
):
    with store._transaction():
        request = next((r for r in records(store, REQUEST) if r["request_id"] == request_id), None)
        if request is None:
            raise PlanStoreError("plan-confirmation-request-unknown", str(request_id))
        channel = CHAT if chat is not None else NATIVE
        if request.get("channel", NATIVE) != channel:
            raise PlanStoreError("plan-confirmation-channel-mismatch", request_id)
        if chat is not None and chat["revision"] != request["plan_revision"]:
            raise PlanStoreError("plan-confirmation-revision-mismatch", request_id)
        if chat is not None and expected_gate != request["gate"]:
            raise PlanStoreError("plan-confirmation-gate-mismatch", request_id)
        if any(r["request_id"] == request_id for r in records(store, RESPONSE)):
            raise PlanStoreError("plan-confirmation-already-consumed", request_id)
        if action not in {"accept", "decline", "cancel"} or (
            action == "accept"
            and (
                not isinstance(content, dict)
                or set(content) != {"approve", "statement", "conditions"}
                or type(content["approve"]) is not bool
                or not isinstance(content["statement"], str)
                or not content["statement"].strip()
                or not isinstance(content["conditions"], str)
            )
        ):
            raise PlanStoreError("plan-confirmation-response-invalid", "Expected complete response")
        state = action
        affirmative = False
        if action == "accept":
            state = (
                "conditional"
                if content["conditions"]
                else "accept"
                if content["approve"]
                else "declined"
            )
            affirmative = state == "accept"
        latest = approvals.latest_request(store, request.get("subject", {}).get("kind"))
        if "subject" not in request or latest != request and affirmative:
            state = "stale"
        record = None
        if "subject" in request:
            record = approvals.record_approval(
                store,
                request,
                words=(content or {}).get("statement", action),
                actor=chat["actor_label"] if chat else "native-client-declared-user",
                channel=channel,
                affirmative=affirmative,
                state=state,
                conditions=(content or {}).get("conditions", ""),
            )
            state = record["state"]
        outcome = dict(
            request_id=request_id,
            gate=request["gate"],
            phase=request["phase"],
            plan_revision=request["plan_revision"],
            action=action,
            content=content,
            state=state,
            approval_id=record["id"] if state == "approved" else None,
            provenance=channel,
            human_identity_verified=False,
        )
        if state == "changes-requested":
            outcome["reasonCode"] = "approval-changes-requested"
        if request.get("proposedAction", {}).get("execution"):
            outcome["scope"] = request["proposedAction"]["execution"]
        if chat is not None:
            outcome["chat"] = chat
        store._event(RESPONSE, outcome)
        return outcome
