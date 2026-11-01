"""Canonical A/B subjects and actual consent words; provenance never authenticates a human."""

import hashlib
import json
from copy import deepcopy
from uuid import uuid4

from codex_harness.core.plan_store import PlanStoreError
from codex_harness.core.planner_vision import SECTIONS
from codex_harness.core.reviewer_evidence import review_tier
from codex_harness.core.spine import Spine

EFFECTS = {
    "A": "Approve confirms understanding and locks this scope and these expansion decisions.",
    "B": "Yes authorizes scoped work, workers, all reviews, validation and quiescent same-work "
    "recovery within B's limits. User provider/model restrictions apply to reviewers. "
    "Cross-subscription is preferred, never required; two distinct same-provider reviewers "
    "are accepted. No separate exception decision is needed.",
}
EVENT = "approval-v2-recorded"
AFFIRMATIVE = {
    "approve",
    "approved",
    "yes",
    "accept",
    "accepted",
    "i approve",
    "i accept",
    "confirmed",
}
CHOICES = ["Yes", "Approve", "No", "Cancel", "Changes: <text>"]

UNDERSTANDING_FIELDS = tuple(aliases[0] for _, aliases in SECTIONS if aliases[0] != "assumptions")
INTENT_FIELDS = {key for _, aliases in SECTIONS for key in aliases} | {
    "expansion",
    "journey",
    "taste",
    "designRefs",
}


def decision(words):
    return " ".join(words.casefold().split()).rstrip(".!")


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
    return next((r for r in records(store, EVENT) if r["subject"]["kind"] == kind), None)


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
        "fields": {k: v for k, v in vision["fields"].items() if k in INTENT_FIELDS},
    }


def reviewer_tuple(reviewer):
    effort = reviewer.get("effort")
    return {
        **{k: reviewer.get(k) for k in ("name", "provider", "model")},
        "effort": effort.get("value") if isinstance(effort, dict) else effort,
    }


def executable_candidate(manifest, template_files):
    """The executable preimage, excluding publication-only proof bookkeeping."""
    executable = deepcopy(manifest)
    if executable.get("proofLevel") == "prospective":
        executable["planRevision"] = executable.pop("sourceManifestRevision")
        executable.pop("proofLevel")
        executable.pop("proofLevelReason")
    return dict(manifestDigest=fingerprint(executable), templateFiles=template_files)


def subject(store, kind, proposed_action=None):
    """Build from canonical content plus actual action arguments, never gate history."""
    if kind not in EFFECTS:
        raise ValueError("approval-kind-invalid")
    spine = Spine(store._data())
    visions = spine.ids("vision")
    if len(visions) != 1:
        raise PlanStoreError("plan-confirmation-card-unavailable", "One vision is required")
    result = dict(version=2, workspace=str(store.workspace), kind=kind, effect=EFFECTS[kind])
    if kind == "A":
        return {**result, "scope": intent(spine.node(visions[0]))}
    action = proposed_action or {}
    previous = current(store, "B") or latest_request(store, "B") or {}
    previous = previous.get("subject", {})
    execution = action.get("execution", previous.get("execution"))
    if execution is None:
        raise PlanStoreError("plan-b-consent-required", "Prepare B with exact workers and limits")
    # execution_scope owns tuple/limit validation; this internal subject binds its result.
    limits = {k: execution[k] for k in ("callCap", "maxParallel", "timeout")}
    from codex_harness.core.planner_review import delivery_review_selection, selection_inputs

    _, node, catalog = selection_inputs(store)
    reviewers, selection = [], None
    if node:
        if catalog is None:
            raise ValueError("reviewer-catalog-original-missing")
        reviewers, selection = delivery_review_selection(node["fields"])
    if not reviewers:
        raise ValueError("reviewer-selection-required-before-B: propose exact delivery reviewers")
    from codex_harness.core.provider_call_budget import DEFAULT_REVIEW_ROUNDS

    policy = deepcopy(previous.get("reviewPolicy")) or dict(
        maxRounds=DEFAULT_REVIEW_ROUNDS, maxTotal=None, timeout=900
    )
    policy["timeout"] = max(900, policy["timeout"])  # Formal acceptance's actual launch timeout.
    for key, value in action.get("reviewLimits", {}).items():
        if key == "maxTotal" and value is None and policy[key] is not None:
            policy[key] = None
        elif value is not None and policy[key] is not None and value > policy[key]:
            policy[key] = value
    actual = action.get("reviewer")
    if actual is not None and reviewer_tuple(actual) not in reviewers:
        reviewers.append(reviewer_tuple(actual))
    scheduled = {stage: len(reviewers) * policy["maxRounds"] for stage in ("plan", "full-vision")}
    allowance = max(sum(scheduled.values()) + len(reviewers), previous.get("reviewAllowance", 0))
    explanation = previous.get(
        "allowanceReason", "Plan and formal acceptance rounds plus one repair round in reserve"
    )
    if action.get("renewReviews"):
        from codex_harness.core.provider_call_budget import usage

        allowance = max(allowance, usage(store, "review") + len(reviewers))
        explanation = "Additional review round after the cumulative allowance was spent"
    return {
        **result,
        "aFingerprint": fingerprint(subject(store, "A")),
        "execution": dict(workers=execution["workers"], **limits),
        "reviewers": reviewers,
        "reviewTier": review_tier(reviewers),
        "reviewSelection": selection,
        "reviewPolicy": policy,
        "scheduledReviewStarts": scheduled,
        "repairReserve": allowance - sum(scheduled.values()),
        "reviewAllowance": allowance,
        "allowanceReason": explanation,
    }


def field_diff(before, after, path=""):
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            field = f"{path}.{key}".lstrip(".")
            if key not in before or key not in after:
                changes.append(
                    dict(
                        field=field,
                        before=before.get(key),
                        after=after.get(key),
                        beforePresent=key in before,
                        afterPresent=key in after,
                    )
                )
            else:
                changes.extend(field_diff(before[key], after[key], field))
        return changes
    return (
        []
        if canonical(before) == canonical(after)
        else [dict(field=path, before=before, after=after)]
    )


def render(payload, changes):
    lines = [
        f"Your decision: approve {payload['kind']}? Reply {', '.join(CHOICES)}.",
        payload["effect"],
    ]
    if payload["kind"] == "B":
        lines.append(f"B includes up to {payload['reviewAllowance']} reviews in total.")
        lines.append("Review tier: " + payload["reviewTier"])
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
    result = dict(
        subject=payload,
        fingerprint=fingerprint(payload),
        diff=changes,
        card=render(payload, changes),
    )
    if kind == "B":
        from codex_harness.core.provider_call_budget import usage

        result["usage"] = {
            "reviewUsed": usage(store, "review"),
            "workerUsed": usage(store, "worker"),
        }
        result["card"] += (
            f"\nReview starts: {result['usage']['reviewUsed']} used; "
            f"{max(0, payload['reviewAllowance'] - result['usage']['reviewUsed'])} remaining."
            f"\nWorker starts: {result['usage']['workerUsed']} used; "
            f"{max(0, payload['execution']['callCap'] - result['usage']['workerUsed'])} remaining."
            " Usage is cumulative and does not verify provider quota."
        )
    return result


def require_approval(store, kind, proposed_action=None):
    """The single consent check. Missing/different returns a complete actionable card."""
    if kind == "B":
        require_approval(store, "A")
    proposed = proposal(store, kind, proposed_action)
    saved = current(store, kind)
    if saved and saved["state"] == "approved" and saved["fingerprint"] == proposed["fingerprint"]:
        return saved
    code = f"plan-{kind.lower()}-consent-required"
    error = PlanStoreError(code, code + ": " + proposed["card"])
    error.approval = proposed
    raise error


def record_approval(store, request, *, words, actor, channel, affirmative, state, conditions=""):
    """Caller owns the SQLite transaction encompassing response and scope recheck."""
    if not isinstance(words, str) or not isinstance(actor, str) or not actor.strip():
        raise ValueError("approval-words-and-actor-required")
    payload = request["subject"]
    answer = decision(words)
    if state not in {"stale", "decline", "cancel"}:
        if conditions:
            state = "changes-requested"
        elif answer in {"no", "cancel"}:
            state = "declined" if answer == "no" else "cancel"
        elif not affirmative or state != "accept" or answer not in AFFIRMATIVE:
            state = "changes-requested"
    if state == "accept" and fingerprint(payload) != fingerprint(
        subject(store, payload["kind"], request.get("proposedAction"))
    ):
        state = "stale"
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
    )
    if state != "stale":
        store._event(EVENT, record)
    if record["state"] == "approved" and payload["kind"] == "A":
        spine = Spine(store._data())
        store.connection.execute(
            "UPDATE meta SET locked_vision_json=?, vision_lock_stale=0, vision_locked_revision=?",
            (json.dumps(spine.node(spine.ids("vision")[0])), spine.plan_revision()),
        )
    return record
