"""Captured acceptance work; only the MCP owner's serial thread writes PlanStore."""

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


class AcceptanceOwner:
    def __init__(self, wake=None):
        self.instance = uuid4().hex
        self.wake = wake
        self.prepared = None
        self.thread = None
        self.messages = queue.Queue()
        self.stopped = threading.Event()
        self.deadline = None
        self.active = None

    def status(self, store, attempt_id):
        exists = store.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='acceptance_attempts'"
        ).fetchone()
        row = (
            store.connection.execute(
                "SELECT * FROM acceptance_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if exists
            else None
        )
        if row is None:
            raise PlanStoreError("acceptance-attempt-unknown", attempt_id)
        state = row["state"]
        if state in {"queued", "running"} and row["owner"] != self.instance:
            state = "unknown-interrupted"
        inputs = json.loads(row["inputs_json"])
        progress = json.loads(row["progress_json"])
        return dict(
            attemptId=attempt_id,
            state=state,
            ownerInstance=row["owner"],
            **{
                key: inputs.get(key)
                for key in (
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
                item["id"]
                for item in inputs["scenarios"]
                if item["id"] not in {done["id"] for done in progress}
            ],
            scenarioCount=len(inputs["scenarios"]),
            startedAt=row["started_at"],
            deadlineAt=row["deadline_at"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            resultScope=(
                "Captured product snapshot; current validity requires canonical freshness checks"
            ),
            error=row["error"],
            reason=row["error"] or "Owner not observed; child liveness UNKNOWN; no replay"
            if state == "unknown-interrupted"
            else None,
            quiescence="UNVERIFIED",
        )

    def start(self, store, repo, *, subject):
        if self.thread is not None and self.thread.is_alive():
            return self.status(store, self.active)
        captured = acceptance_inputs(store, repo, subject=subject)
        with store._transaction():
            if acceptance_inputs(store, repo, subject=subject) != captured:
                raise PlanStoreError(
                    "acceptance-revision-changed", "Inputs changed during admission"
                )
            key = _hash(captured)
            row = store.connection.execute(
                "SELECT id,attempt_key FROM acceptance_attempts "
                "WHERE state IN ('queued','running','unknown-interrupted')"
            ).fetchone()
            if row:
                result = self.status(store, row["id"])
                result["reason"] = (
                    "Existing acceptance owns this workspace; inspect status, never replay"
                )
                return result
            while row := store.connection.execute(
                "SELECT id FROM acceptance_attempts WHERE attempt_key=?", (key,)
            ).fetchone():
                # A separate explicit run after known termination is fresh execution.
                # Polling uses status; uncertain/in-flight owners were refused above.
                key = _hash([key, row["id"]])
            attempt_id = uuid4().hex
            previous = store.connection.execute(
                "SELECT id FROM acceptance_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            store.connection.execute(
                "INSERT INTO acceptance_attempts "
                "(id,attempt_key,owner,state,inputs_json,progress_json,superseded_run_id) "
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
        self.prepared = (attempt_id, captured)
        self.stopped.clear()
        return self.status(store, attempt_id)

    def dispatch_after_flush(self, store):
        if self.prepared is None:
            return
        attempt_id, captured = self.prepared
        self.prepared = None
        with store._transaction():
            try:
                fresh = (
                    acceptance_inputs(store, captured["repo"], subject=captured["subject"])
                    == captured
                )
            except (PlanStoreError, OSError, ValueError):
                fresh = False
            if not fresh:
                store.connection.execute(
                    "UPDATE acceptance_attempts SET state='stale',error=? "
                    "WHERE id=? AND owner=? AND state='queued'",
                    (
                        "Captured inputs changed before dispatch; no child launched",
                        attempt_id,
                        self.instance,
                    ),
                )
                return
            started = datetime.now(UTC)
            deadline = time.monotonic() + captured["maxExecutionSeconds"]
            self.deadline, self.active = deadline, attempt_id
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
                raise PlanStoreError(
                    "acceptance-owner-mismatch", "Only the admitted owner may dispatch"
                )

        def emit(kind, value):
            self.messages.put((attempt_id, captured, kind, value))
            if self.wake:
                self.wake()

        def execute():
            try:
                actual = capture_acceptance(
                    captured, deadline=deadline, should_stop=self.stopped.is_set
                )
                acknowledged = threading.Event()
                emit("captured", (actual, acknowledged))
                while not acknowledged.wait(0.02):
                    if self.stopped.is_set():
                        return
                if self.stopped.is_set():
                    return
                if (
                    capture_acceptance(captured, deadline=deadline, should_stop=self.stopped.is_set)
                    != actual
                ):
                    raise PlanStoreError(
                        "acceptance-revision-changed", "Product changed before launch"
                    )
                results = run_scenarios(
                    captured["repo"],
                    captured["scenarios"],
                    on_result=lambda result: emit("progress", result),
                    should_stop=self.stopped.is_set,
                    deadline=deadline,
                    semantic=captured.get("semantic"),
                )
                if any(
                    step.get("cleanupUncertainty")
                    for item in results
                    for step in [*item["steps"], item.get("semantic", {})]
                ):
                    emit(
                        "unknown",
                        "Child cleanup uncertain; no further metadata or scenario children",
                    )
                elif not self.stopped.is_set():
                    terminal = capture_acceptance(
                        captured, deadline=deadline, should_stop=self.stopped.is_set
                    )
                    emit("complete", (actual, terminal, results))
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
            self.stopped.set()
            with store._transaction():
                store.connection.execute(
                    "UPDATE acceptance_attempts SET state='unknown-interrupted',error=? "
                    "WHERE id=? AND owner=? AND state='running'",
                    (
                        "Whole-run deadline exhausted; metadata/child liveness UNKNOWN; no replay",
                        self.active,
                        self.instance,
                    ),
                )
            self.deadline = None
        while not self.messages.empty():
            attempt_id, captured, kind, value = self.messages.get_nowait()
            if kind == "captured":
                actual, acknowledged = value
                with store._transaction():
                    row = store.connection.execute(
                        "SELECT state FROM acceptance_attempts WHERE id=? AND owner=?",
                        (attempt_id, self.instance),
                    ).fetchone()
                    try:
                        fresh = (
                            acceptance_inputs(store, captured["repo"], subject=captured["subject"])
                            == captured
                        )
                    except (PlanStoreError, OSError, ValueError):
                        fresh = False
                    if (
                        row is None
                        or row["state"] != "running"
                        or not fresh
                        or self.stopped.is_set()
                    ):
                        self.stopped.set()
                        if row and row["state"] == "running":
                            store.connection.execute(
                                "UPDATE acceptance_attempts SET state='stale',error=? WHERE id=?",
                                (
                                    "Canonical inputs changed before metadata acknowledgement",
                                    attempt_id,
                                ),
                            )
                    else:
                        store.connection.execute(
                            "UPDATE acceptance_attempts SET inputs_json=? WHERE id=?",
                            (json.dumps(actual), attempt_id),
                        )
                acknowledged.set()  # Only after the canonical capture transaction committed.
                continue
            with store._transaction():
                row = store.connection.execute(
                    "SELECT * FROM acceptance_attempts WHERE id=? AND owner=? AND state='running'",
                    (attempt_id, self.instance),
                ).fetchone()
                if row is None:
                    continue  # An interrupted owner can never publish late credit.
                if kind == "progress":
                    progress = json.loads(row["progress_json"])
                    progress.append(value)
                    store.connection.execute(
                        "UPDATE acceptance_attempts SET progress_json=? WHERE id=?",
                        (json.dumps(progress), attempt_id),
                    )
                    continue
                result, error, state = None, None, "failed"
                if kind == "complete":
                    actual, terminal, results = value
                    try:
                        if actual != terminal:
                            raise PlanStoreError(
                                "acceptance-revision-changed",
                                "Product metadata changed during execution",
                            )
                        result = record_acceptance(store, actual, results, deadline=self.deadline)
                        state = "completed"
                    except (PlanStoreError, OSError, ValueError) as failure:
                        state, error = "stale", str(failure)
                        if getattr(failure, "code", None) in {
                            "acceptance-deadline-exhausted",
                            "acceptance-owner-stopped",
                        }:
                            state = "unknown-interrupted"
                            self.stopped.set()
                else:
                    error = value
                    if kind == "unknown":
                        state = "unknown-interrupted"
                        self.stopped.set()
                store.connection.execute(
                    "UPDATE acceptance_attempts SET state=?,result_json=?,error=? WHERE id=?",
                    (state, json.dumps(result) if result else None, error, attempt_id),
                )
                store._event(
                    "acceptance-attempt-completed", dict(attemptId=attempt_id, state=state)
                )
                self.deadline = None

    def wait_timeout(self):
        return max(0.01, self.deadline - time.monotonic()) if self.deadline is not None else None

    def close(self, store):
        self.stopped.set()
        self.prepared = None
        if store is not None:
            self.drain(store)
            with store._transaction():
                store.connection.execute(
                    "UPDATE acceptance_attempts SET state='unknown-interrupted',error=? "
                    "WHERE owner=? AND state IN ('queued','running')",
                    ("Owner closed; child liveness UNKNOWN; no replay", self.instance),
                )
