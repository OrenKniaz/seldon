"""One current subject and one consent rule for the local A/B journey.

Words, actor and channel are retained provenance, never human authentication.
Machine prerequisites and candidate-bound product acceptance remain separate.
"""

import hashlib
import json
from copy import deepcopy
from uuid import uuid4

from codex_harness.core.plan_store import PlanStoreError
from codex_harness.core.spine import Spine

EFFECTS = {
    "A": "Approve confirms understanding and locks this scope and these expansion decisions.",
    "B": "Approve authorizes the approved work, all reviews, execution, validation and "
    "quiescent same-work recovery within these cumulative limits and exact choices.",
}
EVENT = "approval-v2-recorded"


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


def intent(vision):
    return {
        "title": vision["title"],
        "understanding": vision["text"],
        "fields": {
            k: v
            for k, v in vision["fields"].items()
            if k
            in {
                "vision",
                "users",
                "first_user_moment",
                "firstUserMoment",
                "scope_in",
                "scopeIn",
                "scope_out",
                "scopeOut",
                "assumptions",
                "quality_bar",
                "qualityBar",
                "quality",
                "done_feels_like",
                "doneFeelsLike",
                "done",
                "expansion",
                "journey",
                "taste",
                "designRefs",
            }
        },
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
    from codex_harness.core import planner_jobs as jobs

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
    previous = current(store, "B")
    previous = (
        previous["subject"]
        if previous
        else next(
            (
                r["subject"]
                for r in records(store, "confirmation-requested")
                if r.get("subject", {}).get("kind") == "B"
            ),
            {},
        )
    )
    execution = action.get("execution", previous.get("execution"))
    if execution is None:
        raise PlanStoreError("plan-b-consent-required", "Prepare B with exact workers and limits")
    manifest = store._manifest(spine)
    workers = jobs._workers(manifest, execution["workers"])
    limits = {k: execution[k] for k in ("callCap", "maxParallel", "timeout")}
    if (
        type(limits["callCap"]) is not int
        or limits["callCap"] < len(workers)
        or type(limits["maxParallel"]) is not int
        or limits["maxParallel"] < 1
        or not jobs._valid_timeout(limits["timeout"])
        or spine.semantic
        and limits["maxParallel"] != 1
    ):
        raise ValueError("worker-execution-limits-invalid")
    selected = store._latest_draft_event("reviewer-selection-recorded")
    reviewers, selection = [], None
    if selected:
        node = spine.node(selected["nodeId"])
        catalog = next(
            (
                r
                for r in records(store, "reviewer-catalog-recorded")
                if r["catalog"]["id"] == node["fields"]["catalogId"]
                and r["catalog"]["digest"] == node["fields"]["catalogDigest"]
            ),
            None,
        )
        if catalog is None:
            raise ValueError("reviewer-catalog-original-missing")
        from codex_harness.core.intake import validate_selection

        validate_selection(catalog["catalog"], node["fields"], selected["authorSessions"])
        reviewers = [reviewer_tuple(r) for r in node["fields"]["reviewers"]]
        selection = {k: node["fields"][k] for k in ("stages", "assurance")}
    policy = deepcopy(action.get("reviewPolicy", previous.get("reviewPolicy")))
    if policy is None:
        policy = dict(maxRounds=2, maxTotal=None, timeout=900)
    if (
        not isinstance(policy, dict)
        or set(policy) != {"maxRounds", "maxTotal", "timeout"}
        or type(policy["maxRounds"]) is not int
        or policy["maxRounds"] < 1
        or policy["maxTotal"] is not None
        and (type(policy["maxTotal"]) is not int or policy["maxTotal"] < 1)
        or not jobs._valid_timeout(policy["timeout"])
    ):
        raise ValueError("planning-review-policy-invalid")
    for key, value in action.get("reviewLimits", {}).items():
        if key == "maxTotal" and value is None and policy[key] is not None:
            policy[key] = None
        elif value is not None and policy[key] is not None and value > policy[key]:
            policy[key] = value
    actual = action.get("reviewer")
    if actual is not None and reviewer_tuple(actual) not in reviewers:
        reviewers.append(reviewer_tuple(actual))
    allowance = action.get("reviewAllowance", previous.get("reviewAllowance"))
    explanation = action.get("allowanceReason", previous.get("allowanceReason", ""))
    if allowance is None:
        # Planning + per-bead + final review, with one repair round in reserve.
        if current(store, "B"):
            allowance = previous["reviewAllowance"]
        else:
            from codex_harness.core.provider_call_budget import usage

            spent = usage(store, "review")
            scheduled = max(1, len(reviewers) * (len(workers) + 3))
            allowance = spent + scheduled
            if allowance > 10:
                explanation = (
                    f"{spent} spent starts plus {scheduled} scheduled reviews and repair reserve"
                )
    if type(allowance) is not int or allowance < 1:
        raise ValueError("planning-review-budget-invalid-scope")
    if allowance > 10 and (not isinstance(explanation, str) or not explanation.strip()):
        raise ValueError("planning-review-allowance-above-10-needs-explanation")
    return {
        **result,
        "aFingerprint": fingerprint(subject(store, "A")),
        "execution": dict(workers=workers, **limits),
        "reviewers": reviewers,
        "reviewSelection": selection,
        "reviewPolicy": policy,
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
    return (
        f"Your decision: approve {payload['kind']}? Reply Approve, No, Cancel, "
        "or Changes: <text>.\n"
        + payload["effect"]
        + (
            f"\nB includes up to {payload['reviewAllowance']} reviews in total."
            if payload["kind"] == "B"
            else ""
        )
        + "\nComplete approval subject:\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        + "\nChanges from your previous decision:\n"
        + json.dumps(changes, ensure_ascii=False, sort_keys=True, indent=2)
    )


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
    decision = " ".join(words.casefold().split()).rstrip(".!")
    if state not in {"stale", "decline", "cancel"}:
        if conditions:
            state = "changes-requested"
        elif decision in {"no", "cancel"}:
            state = "declined" if decision == "no" else "cancel"
        elif (
            not affirmative
            or state != "accept"
            or decision
            not in {
                "approve",
                "approved",
                "yes",
                "accept",
                "accepted",
                "i approve",
                "i accept",
                "confirmed",
            }
        ):
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
