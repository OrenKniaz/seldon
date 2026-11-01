"""Durable MCP review admission; provider work owns a separate SQLite connection."""

import json
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

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
    selected = (
        approvals.require_approval(store, "B")["subject"]["reviewers"]
        if reviewers is None
        else list(reviewers)
    )
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
            state="stopped" if resolved["choice"] == "abandon" else "superseded",
            recovery=resolved,
            next="Review stopped; spent claim retained"
            if resolved["choice"] == "abandon"
            else "harness_accept_review with the original arguments to admit the one retry",
        )
        return result
    if state == "unknown-interrupted":
        result.update(
            outcome="UNKNOWN",
            reason="Owner not observed; paid starts/liveness UNKNOWN. No automatic replay.",
            next=(
                "Inspect the original owner and outcome; UNKNOWN stays held. "
                f"harness_recovery mode=resolve job_id=review:{attempt_id} "
                "choice=abandon reason=<stop with spent claim retained> stops without retry."
            ),
        )
    return result


def ensure_active(store, attempt_id):
    from codex_harness.core.planner_recovery import disposition

    if disposition(store, "review:" + attempt_id):
        raise PlanStoreError("acceptance-review-disposed", "Late owner cannot launch or publish")


def recovery_proof(workspace, job_id):
    from codex_harness.core.plan_store import open_plan_store_readonly
    from codex_harness.core.planner_recovery import disposition
    from codex_harness.core.provider_call_budget import usage

    with open_plan_store_readonly(workspace) as store:
        observed = status(store, job_id.removeprefix("review:"))
        if observed is None:
            raise ValueError("review-attempt-unknown")
        proof = dict(
            jobId=job_id,
            verdict="UNKNOWN" if observed["state"] == "unknown-interrupted" else observed["state"],
            attempt=observed,
            spentCalls=usage(store, "review"),
        )
        proof["proofDigest"] = approvals.fingerprint(proof)
        proof["disposition"] = disposition(store, job_id)
        return proof


def status_reply(server, attempt_id):
    store = server._store()
    data = status(store, attempt_id)
    if data is None:
        return None
    from codex_harness.core import planner_views as views

    body = views.data_card("Review attempt", data)
    if data.get("approval"):
        body += "\n" + data["approval"]["card"]
    if data.get("failure"):
        body += "\n" + views.review_card(data["failure"])
    if data.get("result"):
        body += "\n" + views.review_card(data["result"])
    failed = data["state"] in {"failed", "unknown-interrupted"}
    next_step = data.get("next", "harness_accept_status id=" + attempt_id)
    if data["state"] in {"completed", "failed"}:
        server.result_data = data.get("result") or data.get("approval") or data.get("failure")
        next_step = server._next(
            "harness_accept_review", data["arguments"], server._status(), failed
        )
    return dict(
        isError=failed,
        jobOnly=True,
        planStatusIncluded=False,
        structuredContent=data,
        content=[
            dict(
                type="text",
                text=body + "\nNext: " + next_step,
            )
        ],
    )


def finish_cli(server, reply):
    """Keep the existing blocking CLI contract while MCP admission stays prompt."""
    data = reply.get("structuredContent", {})
    if reply["isError"] or data.get("kind") != "review":
        return reply
    server.reviews.dispatch_after_flush(server.store)
    while data["state"] in {"queued", "running"}:
        time.sleep(0.05)
        reply = server.call_tool("harness_accept_status", {"id": data["attemptId"]})
        data = reply["structuredContent"]
    if data.get("result") is not None:
        reply["structuredContent"] = data["result"]
    return reply


class ReviewOwner:
    def __init__(self):
        self.prepared = None

    def start(self, store, **arguments):
        from codex_harness.core.planner_recovery import disposition

        if arguments.get("resume"):
            attempt_id = arguments["resume"]
            if observed := status(store, attempt_id):
                return observed  # Never resume an async owner through the synchronous importer.
            return review(store, **arguments)  # Historical completed-receipt import; no launch.
        support = arguments.get("execution_assessment_ids")
        if support is not None:
            if arguments.get("reviewers") is None:
                raise ValueError(
                    "Execution support requires explicitly authorized ordinary reviewers"
                )
            from codex_harness.core.acceptance import selected_execution_support

            selected_execution_support(store, support, repo=arguments["repo"])
        with store._transaction():
            previous = next(iter(entries(store)), None)
            successor = None
            if previous:
                observed = status(store, previous["attemptId"])
                resolved = disposition(store, "review:" + previous["attemptId"])
                if resolved:
                    if resolved["choice"] == "abandon":
                        raise ValueError("acceptance-review-stopped: recovery chose stop")
                    if arguments != previous["arguments"]:
                        raise ValueError("review-retry-requires-original-arguments")
                    successor = resolved["successor"]
                elif observed["state"] in {"queued", "running", "unknown-interrupted"}:
                    return observed
            attempt_id = successor or uuid4().hex
            entry = dict(
                attemptId=attempt_id,
                repo=str(Path(arguments["repo"]).resolve()),
                arguments=arguments,
                planRevision=store._revision(),
                runId=latest_run_id(store),
                result=None,
                error=None,
            )
            store._event(ADMITTED, entry)
            _owners[attempt_id] = self
            self.prepared = (store.workspace, entry)
        return status(store, attempt_id)

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
                        known = not headers or len(returned) == len(headers[0][1]["slots"])
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

    def close(self):
        if self.prepared:
            _owners.pop(self.prepared[1]["attemptId"], None)
            self.prepared = None
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
            f"Attempt {attempt.id}: {error}. Preserve originals; use accept-review --resume "
            f"{attempt.id} --repo {Path(repo).resolve()} to recover only completed evidence.",
        ) from error
