"""Captured acceptance work; only the serial owner admits current results."""

import json
import queue
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from codex_harness.core.acceptance import (
    _hash,
    acceptance_inputs,
    capture_acceptance,
    record_acceptance,
    run_scenarios,
)
from codex_harness.core.plan_store import PlanStoreError
from codex_harness.core.planner_recovery import release_attempt


class AcceptanceOwner:
    def __init__(self, wake=None):
        self.instance, self.wake = uuid4().hex, wake
        self.prepared = self.thread = self.deadline = self.active = self.stop_file = None
        self.messages, self.changed, self.stopped = (
            queue.Queue(),
            threading.Event(),
            threading.Event(),
        )

    def _stop(self):
        self.stopped.set()
        if self.stop_file:
            self.stop_file.touch()

    def status(self, store, attempt_id):
        row = store.connection.execute(
            "SELECT * FROM acceptance_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise PlanStoreError("acceptance-attempt-unknown", attempt_id)
        state = row["state"]
        if state in {"queued", "running"} and row["owner"] != self.instance:
            state = "unknown-interrupted"
        inputs, progress = json.loads(row["inputs_json"]), json.loads(row["progress_json"])
        return dict(
            attemptId=attempt_id,
            state=state,
            ownerInstance=row["owner"],
            **{
                k: inputs.get(k)
                for k in (
                    "repo",
                    "subject",
                    "revision",
                    "planRevision",
                    "scenarioRevision",
                    "stepTimeoutSeconds",
                    "maxExecutionSeconds",
                )
            },
            progress=progress,
            productCaptured=inputs.get("revision") is not None,
            unfinishedScenarioIds=[
                s["id"] for s in inputs["scenarios"] if s["id"] not in {r["id"] for r in progress}
            ],
            scenarioCount=len(inputs["scenarios"]),
            startedAt=row["started_at"],
            deadlineAt=row["deadline_at"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
            reason=row["error"],
            quiescence="UNVERIFIED",
            ownershipReleased=state not in {"queued", "running", "unknown-interrupted"},
            resultScope="Captured snapshot; canonical freshness checks own current validity",
            next="harness_recovery mode=resolve job_id=acceptance:"
            + attempt_id
            + " choice=abandon reason=<release uncertain owner>"
            if state == "unknown-interrupted"
            else None,
        )

    def start(self, store, repo, *, subject):
        if self.thread is not None and self.thread.is_alive():
            return self.status(store, self.active)
        with store._transaction():
            row = store.connection.execute(
                "SELECT id FROM acceptance_attempts WHERE state IN ('queued','running','unknown-interrupted')"
            ).fetchone()
            if row:
                return self.status(store, row["id"])
            captured = acceptance_inputs(store, repo, subject=subject)
            attempt_id = uuid4().hex
            key = _hash([captured, attempt_id])
            previous = store.connection.execute(
                "SELECT id FROM acceptance_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            store.connection.execute(
                "INSERT INTO acceptance_attempts (id,attempt_key,owner,state,inputs_json,progress_json,superseded_run_id) "
                "VALUES (?,?,?,'queued',?,'[]',?)",
                (
                    attempt_id,
                    key,
                    self.instance,
                    json.dumps(captured),
                    previous["id"] if previous else None,
                ),
            )
            store._event("acceptance-admitted", dict(attemptId=attempt_id, inputDigest=key))
        self.stop_file = store.workspace / ".codex-harness" / f"acceptance-stop-{attempt_id}"
        self.prepared = (attempt_id, captured)
        self.stopped.clear()
        return self.status(store, attempt_id)

    def dispatch_after_flush(self, store):
        if self.prepared is None:
            return
        attempt_id, captured = self.prepared
        self.prepared = None
        started = datetime.now(UTC)
        deadline = time.monotonic() + captured["maxExecutionSeconds"]
        self.deadline, self.active = deadline, attempt_id
        with store._transaction():
            changed = store.connection.execute(
                "UPDATE acceptance_attempts SET state='running',started_at=?,deadline_at=? "
                "WHERE id=? AND owner=? AND state='queued'",
                (
                    started.isoformat(),
                    (started + timedelta(seconds=captured["maxExecutionSeconds"])).isoformat(),
                    attempt_id,
                    self.instance,
                ),
            ).rowcount
            if changed != 1:
                return  # A released admission cannot dispatch late.

        def emit(kind, value):
            self.messages.put((attempt_id, captured, kind, value))
            self.changed.set()
            if self.wake:
                self.wake()

        def execute():
            def stopping():
                return self.stopped.is_set() or self.stop_file.exists()

            try:
                actual = capture_acceptance(captured, deadline=deadline, should_stop=stopping)
                acknowledged = threading.Event()
                emit("captured", (actual, acknowledged))
                while not acknowledged.wait(0.02):
                    if stopping() or time.monotonic() >= deadline:
                        return
                if stopping():
                    return
                if capture_acceptance(captured, deadline=deadline, should_stop=stopping) != actual:
                    raise PlanStoreError(
                        "acceptance-revision-changed",
                        "Product changed before launch. Rerun acceptance on the current integrated revision.",
                    )
                results = run_scenarios(
                    captured["repo"],
                    captured["scenarios"],
                    on_result=lambda result: emit("progress", result),
                    should_stop=stopping,
                    deadline=deadline,
                    semantic=captured.get("semantic"),
                    judgments=captured.get("judgments"),
                    stop_file=self.stop_file,
                )
                if any(
                    step.get("cleanupUncertainty")
                    for item in results
                    for step in [*item["steps"], item.get("semantic", {})]
                ):
                    emit("unknown", "Child cleanup uncertain; evidence quarantined")
                elif not stopping():
                    terminal = capture_acceptance(captured, deadline=deadline, should_stop=stopping)
                    if actual != terminal:
                        raise PlanStoreError(
                            "acceptance-revision-changed", "Product changed during execution"
                        )
                    emit("complete", (actual, results))
            except Exception as error:
                unknown = isinstance(error, subprocess.TimeoutExpired) or getattr(
                    error, "code", None
                ) in {"acceptance-deadline-exhausted", "acceptance-owner-stopped"}
                emit(
                    "unknown" if unknown else "error",
                    f"{getattr(error, 'code', type(error).__name__)}: {error}",
                )

        self.thread = threading.Thread(target=execute, name="planner-acceptance", daemon=True)
        try:
            self.thread.start()
        except RuntimeError as error:
            emit("error", str(error))

    def drain(self, store):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self._stop()
            with store._transaction():
                release_attempt(
                    store,
                    "acceptance:" + self.active,
                    "Whole-run deadline exhausted; child liveness UNKNOWN",
                )
            self.deadline = None
        while not self.messages.empty():
            attempt_id, captured, kind, value = self.messages.get_nowait()
            acknowledged = None
            with store._transaction():
                row = store.connection.execute(
                    "SELECT * FROM acceptance_attempts WHERE id=? AND owner=? AND state='running'",
                    (attempt_id, self.instance),
                ).fetchone()
                if row is None:
                    if kind == "captured":
                        value[1].set()
                    continue
                if kind == "captured":
                    actual, acknowledged = value
                    try:
                        if (
                            acceptance_inputs(store, captured["repo"], subject=captured["subject"])
                            != captured
                        ):
                            raise PlanStoreError(
                                "acceptance-revision-changed",
                                "Canonical inputs changed before launch",
                            )
                        store.connection.execute(
                            "UPDATE acceptance_attempts SET inputs_json=? WHERE id=?",
                            (json.dumps(actual), attempt_id),
                        )
                    except (PlanStoreError, OSError, ValueError) as error:
                        self._stop()
                        release_attempt(store, "acceptance:" + attempt_id, str(error))
                elif kind == "progress":
                    store.connection.execute(
                        "UPDATE acceptance_attempts SET progress_json=? WHERE id=?",
                        (json.dumps([*json.loads(row["progress_json"]), value]), attempt_id),
                    )
                    continue
                elif kind == "unknown":
                    self._stop()
                    release_attempt(store, "acceptance:" + attempt_id, value)
                else:
                    result, error, state = None, None, "failed"
                    try:
                        if kind == "complete":
                            actual, results = value
                            result = record_acceptance(
                                store, actual, results, deadline=self.deadline
                            )
                            state = "completed"
                        else:
                            error = value
                    except (PlanStoreError, OSError, ValueError) as failure:
                        state, error = "stale", str(failure)
                    store.connection.execute(
                        "UPDATE acceptance_attempts SET state=?,result_json=?,error=? WHERE id=?",
                        (state, json.dumps(result) if result else None, error, attempt_id),
                    )
                    store._event(
                        "acceptance-attempt-completed", dict(attemptId=attempt_id, state=state)
                    )
                if kind != "captured":
                    self.deadline = None
            if acknowledged:
                acknowledged.set()

    def wait_timeout(self):
        return max(0.01, self.deadline - time.monotonic()) if self.deadline is not None else None

    def close(self, store):
        self._stop()
        self.prepared = None
        if store is not None:
            with store._transaction():
                for row in store.connection.execute(
                    "SELECT id FROM acceptance_attempts WHERE owner=? AND state IN ('queued','running')",
                    (self.instance,),
                ).fetchall():
                    release_attempt(
                        store, "acceptance:" + row["id"], "Owner closed; child liveness UNKNOWN"
                    )


def job_status(self, arguments):
    """Read captured job history without running whole-plan freshness/projection."""
    try:
        from codex_harness.core import planner_views as views
        from codex_harness.core.mcp_planner import TOOLS as self_tools
        from codex_harness.core.mcp_planner import _validate

        tool = next(t for t in self_tools if t["name"] == "harness_accept_status")
        _validate(arguments, tool["inputSchema"])
        from codex_harness.core.planner_review import _owners, status_reply

        wait = arguments.get("wait_seconds", 0)
        if type(wait) not in (int, float) or not 0 <= wait <= 60:
            raise ValueError("wait_seconds must be between 0 and 60")
        end = time.monotonic() + wait
        while wait:
            self.acceptance.changed.clear()
            self.acceptance.drain(self._store())
            owner = _owners.get(arguments["id"])
            if isinstance(owner, threading.Thread):
                owner.join(max(0, end - time.monotonic()))
                break
            from codex_harness.core.planner_review import status as review_status

            observed = review_status(self._store(), arguments["id"]) or self.acceptance.status(
                self._store(), arguments["id"]
            )
            if observed["state"] not in {"queued", "running"} or time.monotonic() >= end:
                break
            self.acceptance.changed.wait(max(0, end - time.monotonic()))
        if reply := status_reply(self, arguments["id"]):
            return reply
        data = self.acceptance.status(self._store(), arguments["id"])
        self.result_data = data
        result = data["result"]
        failed = data["state"] in {"failed", "stale", "unknown-interrupted", "abandoned"} or bool(
            result and not result["passed"]
        )
        body = (
            views.acceptance_run_card(result) if result else views.data_card("Acceptance job", data)
        )
        next_step = data.get("next") or "harness_accept_status id=" + data["attemptId"]
        if data["state"] == "abandoned":
            next_step = (
                "Inspect quarantined evidence; a new explicit harness_accept_run is required"
            )
        if result and not failed:
            next_step = (
                "harness_accept_run repo=<integrated-repo> subject=product"
                if data["subject"] == "oracle"
                else "harness_accept_review repo=<integrated-repo>"
            )
        body += "\nCaptured snapshot only; current proof/review/approval checks actual freshness."
    except Exception as error:
        data, failed = None, True
        body = f"{getattr(error, 'code', type(error).__name__)}: {error}"
        next_step = "Inspect the admitted attempt ID and workspace; do not resubmit work."
    return {
        "isError": failed,
        "jobOnly": True,
        "planStatusIncluded": False,
        **({"structuredContent": data} if data is not None else {}),
        "content": [{"type": "text", "text": body + "\nNext: " + next_step}],
    }
