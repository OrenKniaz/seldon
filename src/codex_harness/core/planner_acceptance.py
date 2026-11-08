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
            next="harness_chat: provide direction after inspecting retained acceptance evidence"
            if state == "unknown-interrupted"
            else None,
        )

    def start(self, store, repo, *, subject):
        self.control_generation = getattr(store, "_run_generation", None)
        self.control_effect_id = getattr(store, "_run_effect_id", None)
        if self.thread is not None and self.thread.is_alive():
            return self.status(store, self.active)
        with store._transaction():
            from codex_harness.core.run_loop import cursor
            from codex_harness.core.run_ownership import ensure_effect, attach_attempt

            ensure_effect(store, self.control_generation, self.control_effect_id)
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
            attach_attempt(store, "acceptance:" + attempt_id)
            control = cursor(store)
            deadline = (
                min(
                    datetime.now(UTC) + timedelta(seconds=captured["maxExecutionSeconds"]),
                    datetime.fromisoformat(control["effect"]["deadlineAt"]),
                )
                if control and control.get("effect")
                else datetime.now(UTC) + timedelta(seconds=captured["maxExecutionSeconds"])
            )
            store.connection.execute(
                "UPDATE acceptance_attempts SET deadline_at=? WHERE id=?",
                (deadline.isoformat(), attempt_id),
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
        admitted_deadline = store.connection.execute(
            "SELECT deadline_at FROM acceptance_attempts WHERE id=?", (attempt_id,)
        ).fetchone()[0]
        remaining = max(0, (datetime.fromisoformat(admitted_deadline) - started).total_seconds())
        deadline = time.monotonic() + remaining
        self.deadline, self.active = deadline, attempt_id
        with store._transaction():
            from codex_harness.core.run_ownership import ensure_effect

            ensure_effect(store, self.control_generation, self.control_effect_id)
            changed = store.connection.execute(
                "UPDATE acceptance_attempts SET state='running',started_at=?,deadline_at=? "
                "WHERE id=? AND owner=? AND state='queued'",
                (
                    started.isoformat(),
                    admitted_deadline,
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
                from codex_harness.core.run_ownership import ensure_effect

                ensure_effect(store, self.control_generation, self.control_effect_id)
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


def run_capture(store, repo):
    """Return one finite capture observation; run_loop decides its disposition."""
    from codex_harness.core.run_ownership import ensure_effect

    owner = AcceptanceOwner()
    admitted = owner.start(store, repo, subject="product")
    owner.dispatch_after_flush(store)
    try:
        observed = owner.status(store, admitted["attemptId"])
        while observed["state"] in {"queued", "running"}:
            ensure_effect(store)
            owner.drain(store)
            observed = owner.status(store, admitted["attemptId"])
            if observed["state"] in {"queued", "running"}:
                owner.changed.wait(0.05)
                owner.changed.clear()
        return observed
    finally:
        owner.close(store)


def completed_observation(store, effect):
    """Read exact completed capture/review originals; continuation belongs to run_loop."""
    from codex_harness.core import acceptance, acceptance_recovery, approvals

    approvals.require_approval(store, "B")
    captured = [
        identifier.split(":", 1)[1]
        for identifier in effect.get("attemptIds", [])
        if identifier.startswith("acceptance:")
    ]
    if len(captured) != 1:
        return None
    row = store.connection.execute(
        "SELECT state,result_json FROM acceptance_attempts WHERE id=?", captured
    ).fetchone()
    if row is None or row["state"] != "completed" or not row["result_json"]:
        return None
    result = json.loads(row["result_json"])
    if result != acceptance.current_run(store):
        return None
    all_reviews = [
        identifier.split(":", 1)[1]
        for identifier in effect.get("attemptIds", [])
        if identifier.startswith("review:")
    ]
    reviews = [
        identifier
        for identifier in all_reviews
        if not acceptance_recovery._events(store, acceptance_recovery.ABANDONED, identifier)
    ]
    if len(reviews) > 1 or all_reviews and not reviews:
        return None
    if reviews:
        attempt = acceptance_recovery.ReviewAttempt.load(store, reviews[0])
        admitted = store.connection.execute(
            "SELECT 1 FROM acceptance_reviews WHERE id=?", (attempt.id,)
        ).fetchone()
        attempt.validate(result["repo"], result, admitted=bool(admitted))
    return dict(state="completed", result=result, attemptId=captured[0]), reviews[
        0
    ] if reviews else None
