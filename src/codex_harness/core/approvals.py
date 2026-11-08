"""Canonical A/B subjects and actual consent words; provenance never authenticates a human."""

import hashlib
import json
from contextlib import nullcontext
from copy import deepcopy
from uuid import uuid4

from codex_harness.core import planner_jobs
from codex_harness.core.plan_store import PlanStoreError
from codex_harness.core.planner_vision import SECTIONS
from codex_harness.core.reviewer_evidence import review_tier

EFFECTS = {
    "A": "Your decision confirms understanding and locks this scope and these expansion decisions.",
    "B": "Your decision authorizes scoped work, workers, reviews, validation and quiescent same-work "
    "recovery and same-scope bead repartitioning inside this worker envelope. User provider/model restrictions apply to reviewers. "
    "Cross-subscription is preferred, never required; two distinct same-provider reviewers "
    "are accepted. No separate exception decision is needed.",
}
EVENT = "approval-v2-recorded"
CHOICES = ["Reply in your own words; approve, decline, or describe changes"]

UNDERSTANDING_FIELDS = tuple(aliases[0] for _, aliases in SECTIONS if aliases[0] != "assumptions")


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def records(store, event):
    return [
        json.loads(row[0])
        for row in store.connection.execute(
            "SELECT payload_json FROM events WHERE kind=? ORDER BY id DESC", (event,)
        )
    ]


def current(store, kind):
    """Only the latest decision counts; a refusal supersedes earlier consent."""
    saved = next((r for r in records(store, EVENT) if r["subject"]["kind"] == kind), None)
    request = latest_request(store, kind)
    if saved and request and request["request_id"] != saved["request_id"]:
        responded = {r["request_id"] for r in records(store, "confirmation-responded")}
        if request["request_id"] not in responded and any(
            r["request_id"] == request["request_id"]
            for r in records(store, "confirmation-pending-judgment")
        ):
            return dict(saved, state="pending-judgment")
    if saved and saved["state"] == "approved" and not saved.get("judgment"):
        return dict(saved, state="pending-judgment")
    return saved


def latest_request(store, kind):
    return next(
        (
            r
            for r in records(store, "confirmation-requested")
            if r.get("subject", {}).get("kind") == kind
        ),
        None,
    )


def intent(vision):
    return {
        "title": vision["title"],
        "understanding": vision["text"],
        "fields": deepcopy(vision["fields"]),
    }


def assess(store, purpose, facts, *, subject_ids=None, prepare=True):
    """Bind gathered facts to the single 1j judgment; never infer a verdict."""
    identity = fingerprint(dict(purpose=purpose, facts=facts))
    with nullcontext() if not prepare or store.connection.in_transaction else store._transaction():
        for saved in records(store, "semantic-assessment-requested"):
            if saved["factsDigest"] == identity:
                result = store.judgment(saved["judgmentId"])
                if result["current"]:
                    return dict(result, purpose=purpose)
        if not prepare:
            return dict(
                status="pending-judgment",
                positiveCredit=False,
                concreteGap=False,
                meaning={},
                purpose=purpose,
            )
        store._event("semantic-facts", dict(purpose=purpose, facts=facts))
        event_id = store.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        judgment = store.prepare_judgment(
            question=purpose,
            subject_ids=subject_ids or store.spine().ids("vision"),
            source_event_ids=[event_id],
        )
        store._event(
            "semantic-assessment-requested", dict(factsDigest=identity, judgmentId=judgment["id"])
        )
        return dict(store.judgment(judgment["id"]), purpose=purpose)


def expansion_assessment(store, *, prepare=True):
    vision = store.spine().node(store.spine().ids("vision")[0])
    return assess(
        store,
        "vision-expansion",
        dict(
            vision=vision,
            question="Are the macro/micro improvements and applicable quality criteria adequate? "
            "Consider UX/visual, performance, maintainability, architecture, observability and failure "
            "behavior; research reasons/choices, open-loop metric/provenance/stops and brownfield "
            "existing behavior/evidence where applicable. Counts never establish adequacy.",
        ),
        prepare=prepare,
    )


def intent_assessment(store, vision, *, prepare=True):
    locked = json.loads(store._meta()["locked_vision_json"])
    return assess(
        store,
        "vision-intent",
        dict(
            before=locked,
            after=vision,
            diff=field_diff(locked, vision),
            question="Judge complete before/after intent and impact. "
            "meaning.impact is preserved, changed or narrowed; explain affected trace items. "
            "Narrowing must become a visible user-approved deviation.",
        ),
        prepare=prepare,
    )


def scope_assessment(store, *, prepare=False, before=None):
    saved = next((r for r in records(store, EVENT) if r["subject"]["kind"] == "B"), {})
    before = (
        before
        if before is not None
        else saved.get("subject", {}).get("scope", {})
        if saved.get("state") == "approved"
        else {}
    )
    if not before:
        return {}
    after = planner_jobs.execution_inputs(store)
    if before.get("digest") == after["digest"]:
        return {}
    old, new = before.get("nodes", {}), after["nodes"]
    if any(
        (old.get(k) or new[k])["kind"] in {"feature", "story", "requirement", "acceptance"}
        for k in old.keys() ^ new.keys()
    ):
        return dict(status="changed", meaning=dict(impact="WHAT"))
    return assess(
        store,
        "scope-intent",
        dict(
            before=before,
            after=after,
            diff=field_diff(before, after),
            question="Does this change alter WHAT the user approved — features, user-visible behavior, data flows, "
            "external services/third parties, privacy/security posture, acceptance — or only HOW it is built? "
            "meaning.impact is WHAT or HOW. Unsure stays inconclusive.",
        ),
        subject_ids=list(after["nodes"]),
        prepare=prepare,
    )


def reviewer_tuple(reviewer):
    effort = reviewer.get("effort")
    return {
        **{k: reviewer.get(k) for k in ("name", "provider", "model")},
        "effort": effort.get("value") if isinstance(effort, dict) else effort,
    }


def catalog_status(store):
    from codex_harness.core.planner_review import selection_inputs

    return "unverified" if selection_inputs(store)[2] is None else "recorded"


def subject(store, kind, proposed_action=None):
    """Build from canonical content plus actual action arguments, never gate history."""
    if kind not in EFFECTS:
        raise ValueError("approval-kind-invalid")
    result = dict(version=2, workspace=str(store.workspace), kind=kind, effect=EFFECTS[kind])
    if kind == "A":
        spine = store.spine()
        return {**result, "scope": intent(spine.node(spine.ids("vision")[0]))}
    action = proposed_action or {}
    approved = next((r for r in records(store, EVENT) if r["subject"]["kind"] == "B"), {})
    previous = (approved or latest_request(store, "B") or {}).get("subject", {})
    execution = action.get("execution", previous.get("execution"))
    if execution is None:
        raise PlanStoreError("plan-b-consent-required", "Prepare B with exact workers and limits")
    limits = previous.get("execution", {}) if approved.get("state") == "approved" else {}
    workers = {
        canonical({k: w[k] for k in ("provider", "model", "effort")}) for w in execution["workers"]
    }
    workers.update(
        canonical({k: w[k] for k in ("provider", "model", "effort")})
        for w in limits.get("workers", [])
    )
    # execution_scope owns tuple/limit validation; this internal subject binds its result.
    from codex_harness.core.planner_review import delivery_review_selection, selection_inputs

    _, node, _ = selection_inputs(store)
    if node is None:
        raise ValueError("reviewer-selection-required-before-B: propose exact delivery reviewers")
    reviewers, selection = delivery_review_selection(node["fields"])
    actual = action.get("reviewer")
    if actual is not None and reviewer_tuple(actual) not in reviewers:
        reviewers.append(reviewer_tuple(actual))
    selection["stages"].sort()
    return {
        **result,
        "scope": planner_jobs.execution_inputs(store),
        "execution": dict(
            workers=[json.loads(w) for w in sorted(workers)],
            maxParallel=max(execution["maxParallel"], limits.get("maxParallel", 0)),
            timeout=max(execution["timeout"], limits.get("timeout", 0)),
        ),
        "reviewers": reviewers,
        "reviewTier": review_tier(reviewers),
        "reviewSelection": selection,
    }


def field_diff(before, after, path=""):
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            field = f"{path}.{key}".lstrip(".")
            if key in before and key in after:
                changes.extend(field_diff(before[key], after[key], field))
            else:
                changes.append(
                    dict(
                        field=field,
                        before=before.get(key),
                        after=after.get(key),
                        beforePresent=key in before,
                        afterPresent=key in after,
                    )
                )
        return changes
    return (
        []
        if canonical(before) == canonical(after)
        else [dict(field=path, before=before, after=after)]
    )


def render(payload, changes, catalog):
    lines = [
        f"Your decision: {payload['kind']}. {CHOICES[0]}.",
        payload["effect"],
    ]
    if payload["kind"] == "B":
        lines.append("Review tier: " + payload["reviewTier"])
        lines.append("catalog: " + catalog)
    lines.extend(
        f"{title}:\n{json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)}"
        for title, value in (
            ("Complete approval subject", payload),
            ("Changes from your previous decision", changes),
        )
    )
    return "\n".join(lines)


def proposal(store, kind, proposed_action=None):
    payload = subject(store, kind, proposed_action)
    previous = current(store, kind)
    changes = field_diff(previous["subject"] if previous else {}, payload)
    catalog = catalog_status(store) if kind == "B" else None
    result = dict(
        subject=payload,
        fingerprint=fingerprint(payload),
        diff=changes,
        catalog=catalog,
        card=render(payload, changes, catalog),
    )
    if kind == "B":
        result["scopeJudgment"] = scope_assessment(store)
    if kind == "A" and store._meta()["locked_vision_json"] and changes:
        impact = intent_assessment(
            store, store.spine().node(store.spine().ids("vision")[0]), prepare=False
        )
        result["intentJudgment"] = impact
        result["deviation"] = dict(
            judgmentId=impact.get("id"),
            diff=changes,
            impact=impact["meaning"].get("impact", "pending-judgment"),
        )
        result["card"] += "\nProposed charter deviation (requires your approval):\n" + canonical(
            result["deviation"]
        )
    return result


def require_approval(store, kind, proposed_action=None):
    """The single consent check. Missing/different returns a complete actionable card."""
    if kind == "B":
        require_approval(store, "A")
    proposed = proposal(store, kind, proposed_action)
    saved = current(store, kind)
    impact = proposed.get("scopeJudgment" if kind == "B" else "intentJudgment", {})
    candidate = proposed["subject"]
    if (
        saved
        and impact.get("positiveCredit")
        and impact["meaning"].get("impact") == ("HOW" if kind == "B" else "preserved")
    ):
        candidate = dict(candidate, scope=saved["subject"]["scope"])
    if saved and saved["state"] == "approved" and saved["fingerprint"] == fingerprint(candidate):
        return saved
    pending = (
        kind == "B"
        and impact
        and impact["status"] != "changed"
        and not (impact.get("positiveCredit") or impact.get("concreteGap"))
    )
    code = "pending-judgment" if pending else f"plan-{kind.lower()}-consent-required"
    error = PlanStoreError(code, code + ": " + proposed["card"])
    error.approval = proposed
    raise error


def record_approval(
    store, request, *, words, actor, channel, affirmative, state, conditions="", source=None
):
    """Caller owns the SQLite transaction encompassing response and scope recheck."""
    if not isinstance(words, str) or not isinstance(actor, str) or not actor.strip():
        raise ValueError("approval-words-and-actor-required")
    payload = request["subject"]
    judgment = (
        (scope_assessment(store, before=payload.get("scope", {}), prepare=True) or None)
        if payload["kind"] == "B" and state not in {"stale", "decline", "declined", "cancel"}
        else None
    )
    candidate = subject(store, payload["kind"], request.get("proposedAction"))
    if judgment and judgment.get("positiveCredit") and judgment["meaning"].get("impact") == "HOW":
        candidate = dict(candidate, scope=payload["scope"])
    if fingerprint(payload) != fingerprint(candidate):
        state = (
            "pending-judgment"
            if judgment
            and judgment["status"] != "changed"
            and not (judgment.get("positiveCredit") or judgment.get("concreteGap"))
            else "stale"
        )
    if state not in {"stale", "decline", "declined", "cancel", "pending-judgment"}:
        if payload["kind"] == "A":
            judgment = expansion_assessment(store)
            if judgment["positiveCredit"] and request.get("deviation"):
                judgment = intent_assessment(
                    store, store.spine().node(store.spine().ids("vision")[0])
                )
        if judgment is None or judgment["positiveCredit"]:
            judgment = assess(
                store,
                "user-decision",
                dict(
                    request_id=request["request_id"],
                    subject=payload,
                    revision=request["plan_revision"],
                    words=words,
                    actor=actor,
                    channel=channel,
                    conditions=conditions,
                    source=source,
                    question="Interpret these exact user words "
                    "against this displayed subject. meaning.decision is approve, changes_requested, decline or "
                    "unclear. Ambiguity stays inconclusive. Do not invent user authority.",
                ),
            )
            choice = judgment["meaning"].get("decision") if judgment["positiveCredit"] else None
            state = {
                "approve": "accept",
                "decline": "declined",
                "cancel": "cancel",
                "changes-requested": "changes-requested",
                "changes_requested": "changes-requested",
            }.get(choice, "pending-judgment")
        else:
            state = "pending-judgment"
        if judgment["concreteGap"]:
            state = "changes-requested"
    record = dict(
        id=uuid4().hex,
        subject=payload,
        card=request["card"],
        fingerprint=fingerprint(payload),
        words=words,
        actor=actor,
        channel=channel,
        state="approved" if affirmative and state == "accept" else state,
        request_id=request["request_id"],
        human_identity_verified=False,
        judgment=judgment,
        deviation=request.get("deviation"),
    )
    if state not in {"stale", "pending-judgment"}:
        store._event(EVENT, record)
    if record["state"] == "approved" and payload["kind"] == "B":
        for pending in store.finding_state()["pendingJudgments"]:
            if pending["question"] == "scope-intent":
                prior = store._judgment_record(pending["id"])
                prior.update(
                    disposition="steer",
                    owner=None,
                    supersededBy=record["id"],
                    error="Scope explicitly approved by a newer B decision",
                )
                store._event("judgment-recorded", prior)
    if record["state"] == "approved" and payload["kind"] == "A":
        spine = store.spine()
        store.connection.execute(
            "UPDATE meta SET locked_vision_json=?, vision_lock_stale=0, vision_locked_revision=?",
            (json.dumps(spine.node(spine.ids("vision")[0])), spine.plan_revision()),
        )
    return record
