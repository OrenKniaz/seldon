"""Durable MCP review admission; provider work owns a separate SQLite connection."""

import json
import sqlite3
import threading
from pathlib import Path

from codex_harness.core import acceptance_recovery as recovery
from codex_harness.core import approvals, roundtable
from codex_harness.core.plan_store import PlanStoreError, open_plan_store

ADMITTED = "acceptance-review-admitted"
UPDATED = "acceptance-review-state"
# A live worker survives a client/server object reconnect within this process.
# A new process cannot attest the former owner: UNKNOWN, never automatic replay.
_owners = {}


def acceptance_reviewers(selected):
    """Two distinct reviewers; cross-subscription is preferred, never required."""
    if len(selected) < 2 or len({r["name"] for r in selected}) != len(selected):
        raise ValueError("Two distinct reviewers required")
    return dict(independentRequired=True, minIndependentReviewers=2, crossProviderRequired=False)


def selection_inputs(store, *, catalog=None, node=None):
    """Read exact proposed tuples and optional catalog observations for every consumer."""
    selected = store._latest_draft_event("reviewer-selection-recorded") or {}
    node = node or (store.spine().node(selected["nodeId"]) if selected else None)
    if catalog is None:
        for record in approvals.records(store, "reviewer-catalog-recorded"):
            observed = record["catalog"]
            if not node or all(
                observed[k] == node["fields"]["catalog" + k.title()] for k in ("id", "digest")
            ):
                catalog = observed
                break
    return selected, node, catalog


def delivery_review_selection(fields):
    """Honor the selected providers; installed offerings never broaden user authorization."""
    selected = [approvals.reviewer_tuple(r) for r in fields["reviewers"]]
    if len(selected) == 1:
        selected.append({**selected[0], "name": selected[0]["name"] + "-2"})
    assurance = acceptance_reviewers(selected)
    return selected, dict(
        stages=list(dict.fromkeys([*fields["stages"], "plan", "full-vision"])), assurance=assurance
    )


def authorized_acceptance_reviewers(store, reviewers):
    approved = approvals.require_approval(store, "B")
    selected = approved["subject"]["reviewers"] if reviewers is None else list(reviewers)
    acceptance_reviewers(selected)
    return selected


def latest_run_id(store):
    row = store.connection.execute(
        "SELECT id FROM acceptance_runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def entries(store):
    return approvals.records(store, ADMITTED)


def status(store, attempt_id):
    entry = next((r for r in entries(store) if r["attemptId"] == attempt_id), None)
    if entry is None:
        return None
    update = next(
        (r for r in approvals.records(store, UPDATED) if r["attemptId"] == attempt_id),
        dict(state="queued", result=None, error=None),
    )
    saved = store.connection.execute(
        "SELECT result_json FROM acceptance_reviews WHERE id=?", (attempt_id,)
    ).fetchone()
    if saved:
        result = json.loads(saved[0])
        result["findings"] = [
            r for r in store.acceptance_findings() if r["id"] in result["findingIds"]
        ]
        update = dict(state="completed", result=result, error=None)
    state = update["state"]
    if state in {"queued", "running"} and attempt_id not in _owners:
        state = "unknown-interrupted"
    result = {**entry, **update, "state": state, "kind": "review"}
    from codex_harness.core.planner_recovery import disposition

    if resolved := disposition(store, "review:" + attempt_id):
        result.update(
            state="abandoned",
            result=None,
            ownershipReleased=True,
            quiescenceVerified=False,
            recovery=resolved,
            next="Review quarantined; inspect retained evidence before a new explicit admission",
        )
        return result
    if state == "unknown-interrupted":
        result.update(
            outcome="UNKNOWN",
            reason="Owner not observed; paid starts/liveness UNKNOWN. No automatic replay.",
            next="harness_chat: provide direction after inspecting retained review evidence",
        )
    return result


def ensure_active(store, attempt_id):
    from codex_harness.core.planner_recovery import disposition
    from codex_harness.core.run_ownership import ensure_effect

    ensure_effect(store)

    if disposition(store, "review:" + attempt_id):
        raise PlanStoreError("acceptance-review-disposed", "Late owner cannot launch or publish")


class ReviewOwner:
    def __init__(self):
        self.prepared = None

    def dispatch_after_flush(self, store):
        if self.prepared is None:
            return
        workspace, entry = self.prepared
        self.prepared = None
        attempt_id = entry["attemptId"]

        def execute():
            try:
                with open_plan_store(workspace) as worker:
                    with worker._transaction():
                        ensure_active(worker, attempt_id)
                        worker._event(
                            UPDATED,
                            dict(attemptId=attempt_id, state="running", result=None, error=None),
                        )
                    prior_failure = worker.latest_review_failure()
                    try:
                        if (
                            worker._revision() != entry["planRevision"]
                            or latest_run_id(worker) != entry["runId"]
                        ):
                            raise ValueError("acceptance-revision-changed before dispatch")
                        result = review(worker, **entry["arguments"], attempt_id=attempt_id)
                        update = dict(state="completed", result=result, error=None)
                    except Exception as error:
                        headers = recovery._events(worker, recovery.STARTED, attempt_id)
                        returned = recovery._events(worker, recovery.COMPLETED, attempt_id)
                        registered = recovery._events(worker, recovery.REGISTERED, attempt_id)
                        known = not headers or (
                            {r["slot"] for _, r in returned}
                            == {r["id"] for r in headers[0][1]["slots"]}
                            and (
                                not registered
                                and all(r.get("schema") == 1 for _, r in returned)
                                or all(r.get("schema") == 2 for _, r in returned)
                                and {r["id"] for _, r in registered}
                                == {r.get("launchId") for _, r in returned}
                            )
                        )
                        update = dict(
                            state="failed" if known else "unknown-interrupted",
                            result=None,
                            error=f"{getattr(error, 'code', type(error).__name__)}: {error}",
                            approval=getattr(error, "approval", None),
                            failure=worker.latest_review_failure()
                            if worker.latest_review_failure() != prior_failure
                            else None,
                        )
                    with worker._transaction():
                        worker._event(UPDATED, dict(attemptId=attempt_id, **update))
            finally:
                _owners.pop(attempt_id, None)

        thread = threading.Thread(target=execute, name="planner-review", daemon=True)
        _owners[attempt_id] = thread
        try:
            thread.start()
        except RuntimeError:
            _owners.pop(attempt_id, None)
            raise

        # A flushed worker uses its own connection and completes independently.


def review(
    store,
    repo,
    *,
    reviewers=None,
    runner=None,
    token_budgets=None,
    resume=None,
    execution_assessment_ids=None,
    attempt_id=None,
) -> dict:
    from codex_harness.core.acceptance import (
        _execute_acceptance_review,
        _prepare_acceptance_review,
        _resume_acceptance_review,
    )

    if resume is not None:
        if any(
            value is not None
            for value in (reviewers, runner, token_budgets, execution_assessment_ids)
        ):
            raise ValueError("Resume uses only the original registered review settings")
        return _resume_acceptance_review(store, repo, resume)
    if execution_assessment_ids is not None and reviewers is None:
        raise ValueError(
            "Execution support requires explicitly authorized ordinary "
            "reviewers; supply reviewers before discovery or launch"
        )
    run, selected, dossiers, evidence, packet, included_header, context, original_identity = (
        _prepare_acceptance_review(
            store,
            repo,
            reviewers,
            token_budgets,
            **(
                {"execution_assessment_ids": execution_assessment_ids}
                if execution_assessment_ids is not None
                else {}
            ),
        )
    )
    executables = {
        item["provider"]: item["provider"]
        if runner is not None
        else roundtable._executable(item["provider"], None)
        for item in selected
    }
    for provider, executable in executables.items():
        if not executable:
            raise ValueError(f"Reviewer executable unavailable: {provider}")
    roundtable.bind_review_dossiers(store, dossiers, selected, candidate_run=run)
    attempt = recovery.ReviewAttempt.start(
        store,
        repo,
        run,
        selected,
        dossiers,
        executables,
        evidence,
        mocked=runner is not None,
        expected_identity=original_identity,
        attempt_id=attempt_id,
    )
    try:
        return _execute_acceptance_review(
            store,
            repo,
            run,
            selected,
            dossiers,
            packet,
            included_header,
            context,
            executables,
            attempt,
            runner,
            token_budgets,
        )
    except recovery.CaptureFailure as error:
        raise recovery.refusal(error) from error
    except sqlite3.Error as error:
        raise PlanStoreError(
            "acceptance-review-interrupted",
            f"Attempt {attempt.id}: {error}. Preserve original receipts; harness_chat supplies recorded "
            "direction to the run loop, which owns recovery and further admission.",
        ) from error
