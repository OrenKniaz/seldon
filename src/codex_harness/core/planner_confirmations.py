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
    effect = {
        "align+vision-lock": (
            "Approve confirms understanding and locks this scope and these expansion decisions."
        ),
        "plan-execution": (
            "Approve authorizes this plan, these review budgets, publication after review passes, "
            "execution and validation within these limits."
        ),
    }.get(request.get("gate"), "")
    subject = {
        "align": "the Align understanding below",
        "vision-lock": "the Vision Lock below",
        "align+vision-lock": "understanding, Align and Vision Lock together",
        "plan-execution": "these plan, reviewer, budget, publish, execution and validation limits",
    }.get(request.get("gate"), "the worker execution below")
    # Bound the user-facing text; recording rules stay outside the markers.
    request["card"] = (
        "Your reply must be exactly the text between the markers, copied in full; "
        "do not summarise, shorten or add to it.\n\n<<<SHOW-VERBATIM\n"
        f"**Your decision: approve {subject}?**\n"
        "Reply with exactly one of: **Approve** · **No** · **Cancel** · "
        "**Changes: <what to change>**\n\n"
        + (effect + "\n\n" if effect else "")
        + request["card"].rstrip()
        + "\nSHOW-VERBATIM>>>\n\n"
        f"For the agent: chat confirmation {request['request_id']} for revision {revision}. "
        "Wait for their reply. "
        "Record it with harness_record_chat_confirmation: quote their latest actual chat "
        "message verbatim, with its SHA-256 and an actor label. Actor identity and message "
        "source are unverified; the hash binds the quote, not its author. "
        "No reply grants nothing."
    )
    return request


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
        quote_sha256=expected,
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
                request.get("response_revision", revision),
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


def reviewer_confirmation_target(store, before, node_id, *, allow_existing=False) -> tuple:
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
        if allow_existing:
            validate_reviewer_record_scope(store, node, catalog["catalog"], event["authorSessions"])
            return node, catalog["catalog"], event["authorSessions"], scope
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
        bundle = combined_credit(store, "reviewer-selection")
        if bundle and bundle["scopes"]["reviewer-selection"] == reviewer_selection_scope(
            node, catalog, author_sessions
        ):
            node["fields"]["confirmation"] = dict(
                approvalId=bundle["request_id"],
                eventId=bundle["responseEvent"],
                origin="user",
                statement=bundle["response"]["content"]["statement"],
                sourceRef="combined-confirmation",
                authorizedScopeDigest=bundle["scopes"]["reviewer-selection"],
            )
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
    if known is None and not authority["sourceRef"].startswith(
        ("reviewer-confirmation:event:", "combined-confirmation")
    ):
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


def confirmation_card(gate: str, phase: str, revision: str, vision: dict) -> str:
    """The exact stored text of a planning confirmation request (one shared writer)."""
    from codex_harness.core.planner_vision import readable, vision_projection

    def excerpt(text, limit):
        if len(text) <= limit:
            return text
        head = text[: limit - 1]
        return (head.rsplit(". ", 1)[0] + "." if ". " in head else head.rpartition(" ")[0]) + "…"

    projection = vision_projection(vision)
    projection["sections"][0]["text"] = vision["text"].replace("\r\n", "\n").split("\n\n", 1)[0]
    lines = [projection["title"]]
    for section in (projection["sections"][i] for i in (0, 3, 4, 5, 6)):
        label, text = section["label"], section["text"]
        short = excerpt(text, 599 if label == "Vision" else 300)
        if label == "Vision" and text != vision["text"] and not short.endswith("…"):
            short += "…"
        lines.append(f"{label}:\n{short}")
        if label == "Vision" or short != text:
            lines.append("Full vision: template/VISION.md")
    fields = vision["fields"]
    for key in ("taste", "designRefs"):
        if key in fields:
            lines.append(f"{key}:\n{readable(fields[key])}")
    expansion = fields.get("expansion", {})
    for upgrade in expansion.get("upgrades", []):
        value = " | ".join(
            str(upgrade.get(k, "Not recorded")) for k in ("id", "disposition", "text")
        )
        lines.append(" ".join(value.splitlines()))
    scenarios = fields.get("scenarios", [])
    if isinstance(scenarios, dict):
        scenarios = scenarios.get("scenarios", [])
    lines.append("Scenarios:" if scenarios else "Scenarios: Not recorded")
    for scenario in scenarios:
        if isinstance(scenario, dict):
            intent = "; ".join(
                f"{a['id'].replace('_', ' ').replace('-', ' ')} "
                f"({a.get('cli', a.get('web', {})).get('operation', 'Operation not recorded')})"
                for a in scenario.get("actions", [])
            )
            scenario = f"{scenario['id']}: {intent or 'Intent not recorded'}"
        lines.append(" ".join(readable(scenario).splitlines()))
    lines.append("Full scenarios: template/VISION.md and template/SCENARIOS.json")
    understanding = "\n".join(lines)
    heading = f"Confirm {gate}" if phase == gate else f"Renew vision lock during {phase}"
    if gate == "vision-lock":
        return (
            f"{heading} for revision {revision[:12]}.\n"
            "Expansion proposed dispositions, quality bar and done definition:\n\n" + understanding
        )
    return (
        f"{heading} for revision {revision[:12]}.\n"
        + "Read the whole understanding below and approve only if it is right. "
        "To ask for changes, say what to change; a conditional answer does not approve "
        "this step.\n\n" + understanding
    )


def legacy_confirmation_card(gate: str, phase: str, revision: str, vision: dict) -> str:
    """Card text stored before the decision-first wording; still recognised on read."""
    heading = f"Confirm {gate}" if phase == gate else f"Renew vision lock during {phase}"
    return (
        f"{heading} for revision {revision}.\n"
        "Review the complete recorded understanding below. Approve only this gate and "
        "revision. Retain your complete words; put any conditions or requested changes "
        "in conditions. A conditional response does not approve this gate.\n\n"
        + _understanding(vision)
    )


def _records(store: PlanStore, kind: str):
    for row in store.connection.execute(
        "SELECT id, payload_json FROM events WHERE kind=? ORDER BY id DESC", (kind,)
    ):
        yield row[0], json.loads(row[1])


def approval_credit(store: PlanStore, gate: str, revision: str) -> dict | None:
    """Read original correlated records; supplied words/by fields alone earn no credit."""
    requests = {row["request_id"]: row for _, row in _records(store, REQUEST)}
    latest = next((r for r in requests.values() if gate in r.get("scopes", [r["gate"]])), None)
    for _, response in _records(store, RESPONSE):
        if response.get("state") != "approved" or gate not in response.get(
            "approval_ids", {response.get("gate"): None}
        ):
            continue
        request = requests.get(response.get("request_id"))
        content = response.get("content")
        if (
            not request
            or request != latest
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
            "SELECT * FROM approvals WHERE id=?",
            (response.get("approval_ids", {}).get(gate, response.get("approval_id")),),
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


def prepare_confirmation(
    store, gate, *, _chat=False, _expected_request_id=None, execution=None, reviewer_node_id=None
):
    """Persist exactly what the native client will display for the current gate."""
    with store._transaction():
        if gate not in ("align", "vision-lock", "align+vision-lock", "plan-execution"):
            raise PlanStoreError("plan-gate-invalid", str(gate))
        if gate in {"align+vision-lock", "plan-execution"} and not _chat:
            raise ValueError("combined-consent-requires-exact-chat-response")
        if _expected_request_id is not None:
            latest = next((row for _, row in _records(store, REQUEST) if row["gate"] == gate), None)
            if latest is None or latest["request_id"] != _expected_request_id:
                raise ValueError("confirmation-request-superseded")
        phase = store.phase()
        if phase != {"align+vision-lock": "align", "plan-execution": "plan"}.get(
            gate, gate
        ) and not (gate == "vision-lock" and PHASES.index(phase) > PHASES.index("vision-lock")):
            raise PlanStoreError("plan-confirmation-gate-mismatch", f"Current phase is {phase}")
        spine = Spine(store._data())
        visions = spine.ids("vision")
        if len(visions) != 1:
            raise PlanStoreError("plan-confirmation-card-unavailable", "One vision is required")
        vision = spine.node(visions[0])
        revision = spine.plan_revision()
        card = confirmation_card(gate, phase, revision, vision)
        request = dict(
            request_id=uuid4().hex,
            gate=gate,
            phase=phase,
            plan_revision=revision,
            card=card,
            vision=vision,
        )
        if gate == "align+vision-lock":
            from codex_harness.core.planner_vision import expansion_status

            if expansion_status(vision)["gaps"]:
                raise PlanStoreError("plan-expansion-required", "Prepare expansion before consent")
            request["scopes"] = {g: revision for g in ("align", "vision-lock")}
        if gate == "plan-execution":
            from codex_harness.core.planner_execution import _control_digest, execution_scope
            from codex_harness.core.provider_call_budget import planning_review_budget

            budget = planning_review_budget(store)
            if execution is None or budget is None:
                raise ValueError("combined-execution-and-review-budget-required")
            execution = execution_scope(store, **execution, _candidate=True)
            request["scopes"] = {
                "plan-lock": revision,
                "publish": execution["manifestDigest"],
                "review-budget": {k: v for k, v in budget.items() if k != "spentAdmissions"},
                "execution": execution,
                "validation": execution,
            }
            request["scopes"]["review-budget"]["reviewers"] = []
            selected = store._latest_draft_event("reviewer-selection-recorded")
            reviewer_node_id = reviewer_node_id or (selected or {}).get("nodeId")
            if reviewer_node_id:
                request["reviewer_node_id"] = reviewer_node_id
                node, _, _, scope = reviewer_confirmation_target(
                    store, spine, reviewer_node_id, allow_existing=True
                )
                request["reviewer"] = node
                request["scopes"]["reviewer-selection"] = scope
                request["scopes"]["review-budget"]["reviewers"] = [
                    {
                        **{k: r[k] for k in ("name", "provider", "model")},
                        "effort": r["effort"].get("value"),
                    }
                    for r in node["fields"]["reviewers"]
                ]
        if "scopes" in request:
            from codex_harness.core.planner_execution import _control_digest

            request.update(workspace=str(store.workspace), controlDigest=_control_digest(store, 0))
            request["card"] += "\nCovered gates and limits:\n" + json.dumps(request["scopes"])
            request["card"] += (
                f"\nWorkspace: {store.workspace}\nCandidate: {revision}\n"
                f"Controls: {request['controlDigest']}\n"
                + json.dumps(store._data(), ensure_ascii=False)
            )
        if _chat:
            _chat_question(request, revision)
        store._event(REQUEST, request)
        return request


def prepare_chat_confirmation(store, gate, *, _expected_request_id=None, **scope):
    """Offer a fresh question without interpreting a previous native decline."""
    return prepare_confirmation(
        store, gate, _chat=True, _expected_request_id=_expected_request_id, **scope
    )


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
        if "scopes" in request:
            from codex_harness.core.planner_execution import _control_digest

            stale = stale or _control_digest(store, request_event) != request["controlDigest"]
        state = "stale" if stale else action
        approval_id = None
        approval_ids = {}
        if not stale and action == "accept":
            if content["conditions"] != "":
                state = "conditional"
            elif not content["approve"]:
                state = "declined"
            else:
                for gate in request.get("scopes", {request["gate"]: None}):
                    approval_ids[gate] = store._approve(gate, channel, content["statement"])
                approval_id = approval_ids.get(request["gate"])
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
        if "scopes" in request:
            outcome["approval_ids"] = approval_ids
        store._event(RESPONSE, outcome)
        return outcome


def _b_required(reason):
    return PlanStoreError("plan-b-consent-required", "Needs your go-ahead (B): " + reason)


def combined_credit(store, gate):
    """Resolve one original response and its gate-specific scope, never supplied authority."""
    request = next((r for _, r in _records(store, REQUEST) if gate in r.get("scopes", {})), None)
    if not request:
        if (
            gate in {"plan-lock", "review-budget", "publish", "execution", "validation"}
            and any(
                r["gate"] == "align+vision-lock" and r["state"] == "approved"
                for _, r in _records(store, RESPONSE)
            )
            and Spine(store._data()).semantic
        ):
            raise _b_required("prepare and approve plan-execution for this candidate.")
        return None
    revision = store._revision()
    if request["workspace"] != str(store.workspace) or not approval_credit(store, gate, revision):
        raise _b_required("combined-consent-required")
    if "execution" in request["scopes"]:
        from codex_harness.core.planner_jobs import _files

        if _files(store.workspace / "template") != request["scopes"]["execution"]["templateFiles"]:
            raise _b_required("combined-inputs-stale")
    if request.get("reviewer_node_id"):
        current = reviewer_confirmation_target(
            store, Spine(store._data()), request["reviewer_node_id"], allow_existing=True
        )
        if current[3] != request["scopes"]["reviewer-selection"]:
            raise _b_required("combined-reviewer-scope-stale")
    event, response = next(
        (e, r) for e, r in _records(store, RESPONSE) if r["request_id"] == request["request_id"]
    )
    request.update(response=response, responseEvent=event)
    return request
