"""The single continuation policy, persisted in PlanStore's transaction/event stream."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import SubprocessError
from uuid import uuid4

from codex_harness.core import approvals, planner_confirmations as confirmations
from codex_harness.core.mail import Mailbox
from codex_harness.core.plan_types import PlanStoreError, _now
from codex_harness.core.run_ownership import ensure_effect
from codex_harness.core.status_api import run_baton
from codex_harness.core.run_content import apply_answer
from codex_harness.core.run_setup import initialize
from codex_harness.core.run_questions import ask_question, response as question_response

EVENT = "run-loop-cursor"
TERMINAL = {"STOPPED", "DONE"}
CONTROL_STATES = {"pause": "PAUSED", "stop": "STOPPED", "repair": "PLAN", "replan": "PLAN"}


def cursor(store):
    row = store.connection.execute(
        "SELECT payload_json FROM events WHERE kind=? ORDER BY id DESC LIMIT 1", (EVENT,)
    ).fetchone()
    run = json.loads(row[0]) if row else None
    if run:
        from codex_harness.core.plan_types import refuse_old_format
        from codex_harness.core.run_schemas import strict_schema
        request = run.get("userRequest") or {}
        if request.get("gate") == "invocations" or request.get("subject", {}).get("kind") == "P":
            refuse_old_format("Saved invocation permission")
        if pending := run.get("question"):
            try:
                strict_schema(pending["answerSchema"])
            except ValueError:
                refuse_old_format("Saved question")
            if pending["purpose"] == "trace" and pending["answerSchema"]["properties"].get("edits", {}).get("type") != "object":
                refuse_old_format("Saved trace question")
    return run


def save(store, run):
    if run.get("question"):
        run["question"].pop("judgment", None)  # Display-only enrichment, including retained cursors.
    run.update(revision=store._revision(), phase=store.phase(), observedAt=_now())
    store._event(EVENT, run)


def _steering(store, run, reason, *, failed=None):
    from codex_harness.core.run_context import recovery_context
    context = recovery_context(store, run)
    family = "direction"
    if run["step"] == "plan-review" and not any(r["subject"]["kind"] == "B" and r["state"] == "approved" for r in approvals.records(store, approvals.EVENT)):
        members = store.finding_state()["dispositionPrerequisite"]["members"]
        if members and all(m["classification"] == "defect-in-plan" for m in members):
            family, reason = "repair", "trace"
            context = dict(reviewFindings=members, obligations=[m["finding"] for m in members],
                nodes=[n for n in store.list_nodes() if n["kind"] not in {"vision","decision","result","amendment"}], targets=[], scenarios=[],
                instructions="Repair these findings within the accepted vision using typed edits; CODE binds their identities and revision. Workers write code.")
    if failed:
        diagnostics = failed.get("diagnostics", {})
        context = dict(failedRequestId=failed["id"], rejectedResponseEventId=failed.get("rejectedResponseEventId"),
                       **diagnostics,
                       rejectedError=failed["error"], internalFailure=True)
        if diagnostics.get("kind") == "graph" and diagnostics.get("editableFields") and not diagnostics.get("obligations"):
            original = next(s["payload"]["context"] for s in failed["sourceRefs"] if s["kind"] == "loop-question")
            context = {**original, **context}
            family = "repair"
            reason = "trace"
    run.update(state="STEER", question=None, effect=None)
    ask_question(store, run, family, reason, context=context)


def _user(store, run, gate, **scope):
    if gate == "plan-execution" and run.get("pendingConsent") and not run.get("freshConsent"):
        resumed = confirmations.reattach_pending_words(store, run["pendingConsent"])
        if resumed:
            request, outcome = resumed
            run.update(state="WAIT_USER", userRequest=request, question=None, effect=None)
            if outcome["state"] == "pending-judgment":
                ask_question(
                    store,
                    run,
                    "user-interpretation",
                    outcome["judgment"]["purpose"],
                    judgment=outcome["judgment"],
                )
            else:
                _decision(store, run, outcome)
            return
    request = confirmations.prepare_chat_confirmation(store, gate, **scope)
    run.update(
        state="WAIT_USER",
        userRequest=request,
        question=None,
        effect=None,
        nextAction="Retain the user's actual words for this exact displayed subject",
        reason=request["card"],
    )


def _pending(store, run):
    """Consume only the latest canonical judgment records, not view recommendations."""
    seen = set()
    for record in approvals.records(store, "judgment-recorded"):
        if record["id"] in seen:
            continue
        seen.add(record["id"])
        if record.get("supersededBy") or record["disposition"] in {"steer", "complete"}:
            continue
        if run.get("freshConsent") and record["question"] == "scope-intent":
            continue
        if any(ref["kind"] == "loop-question" for ref in record["sourceRefs"]):
            continue
        current = store.judgment(record["id"], candidate=record["candidate"])
        if not current["current"] or current["positiveCredit"] or current["concreteGap"]:
            continue
        purpose = next((ref["payload"]["check"] for ref in record["sourceRefs"] if ref["kind"] == "candidate-observation"), record["question"])
        family = "user-interpretation" if purpose == "user-decision" else "assess"
        ask_question(store, run, family, purpose, judgment=current)
        return True
    needed = store.finding_state()["dispositionPrerequisite"]
    if not needed["complete"] and needed["nextAction"]["kind"] == "reconcile_findings":
        if all(m["status"] == "open" and m["resolution"] for m in needed["members"]) and run["step"] == "plan-review":
            _steering(store, run, "Repair the retained open plan findings")
        else:
            ask_question(store, run, "author", "finding-dispositions", context=needed)
        return True
    return False


def start(store, *, setup=None):
    if cursor(store) is None and setup is None:
        from codex_harness.core import run_setup
        setup = run_setup.kickoff(store.workspace)
        run_setup.validate(store, setup)
    initialize(store, setup=setup)
    return run_loop(store)


def _record_answer(store, *, request_id, answer, source, provider=None, facts=None):
    """Persist the answer, its canonical writes and the cursor before acknowledging."""
    with store._transaction():
        run = cursor(store)
        pending = (run or {}).get("question")
        if (
            not pending
            or pending["requestId"] != request_id
            or run["state"] in TERMINAL | {"PAUSED"}
        ):
            return dict(state="superseded", requestId=request_id,
                        message="Request superseded or consumed; re-ask using the current baton's outstanding requestId")
        retained = store._judgment_record(pending["requestId"])
        if pending["revision"] != store._revision() or retained["disposition"] in {"steer", "complete"}:
            _steering(store, run, "Retained request superseded or consumed; re-ask against current evidence")
            save(store, run)
            return dict(state="superseded", requestId=request_id, message=run["reason"])
        if run.get("semanticHeld") and pending["family"] != "direction":
            raise PlanStoreError(
                "semantic-new-evidence-required",
                "Supply new evidence or direction through harness_chat before another semantic attempt",
            )
        planner_answer = pending["family"] in {"author", "direction", "repair"}
        pending = dict(pending, context={**pending["context"], **retained.get("diagnostics",{})})
        result = store.submit_judgment(
            judgment_id=pending["requestId"],
            response=answer,
            source=source,
            decoder=lambda raw, record: question_response(pending, raw, record, provider, facts=facts),
            apply_meaning=(lambda meaning: apply_answer(store, pending, meaning)) if planner_answer else None,
        )
        result = store.judgment(result["id"], candidate=pending["candidate"])
        if result["disposition"] in {"exact-fix", "steer"}:
            if result["disposition"] == "exact-fix" and pending["family"] in {"author", "repair"} and (result.get("diagnostics",{}).get("editableFields") or result.get("diagnostics",{}).get("obligations")):
                ask_question(store,run,pending["family"],"trace",judgment=result,context={**pending["context"],**result["diagnostics"]})
            if result["disposition"] == "steer":
                if pending["family"] in {"direction", "repair"}:
                    run.update(state="PAUSED", question=None, semanticHeld=True,
                               reason=result["error"], nextAction="Supply new direction through harness_chat; steering exhausted")
                else:
                    _steering(store, run, result["error"], failed=result)
            save(store, run)
            return
        meaning = result["meaning"]
        if pending["purpose"] == "planning-size":
            vision = store.spine().node(store.spine().ids("vision")[0])
            override = vision["fields"].get("sizeOverride")
            vision["fields"]["planSize"] = override["size"] if override and meaning["overrideAccepted"] else meaning["size"]
            store.upsert_node(vision)
            run["reply"] = f"Size: {vision['fields']['planSize']}. {meaning['process']}. You may override the size in your own words."
            store._event("chat-message", dict(role="seldon", text=run["reply"], sourceRequest=pending["requestId"]))
        if meaning.get("reply"):
            run["reply"] = meaning["reply"]
            store._event("chat-message", dict(role="seldon", text=meaning["reply"], sourceRequest=pending["requestId"]))
        if result["verdict"] in {"inconclusive", "unsupported"}:
            run.update(
                semanticHeld=True,
                nextAction="Supply new evidence or recorded direction through harness_chat",
                reason="Meaning remains pending; no invocation or reservation is held",
            )
            save(store, run)
            return
        run.update(question=None, semanticHeld=False)
        if not planner_answer:
            outcome = confirmations.apply_judgment(store, result)
            if outcome:
                if outcome.get("id") and not outcome.get("state"):
                    run.update(
                        state="DONE",
                        userRequest=None,
                        reason="Bound C accepted",
                        nextAction="Complete",
                    )
                else:
                    _decision(store, run, outcome)
            elif pending["purpose"] == "execution" and run["state"] == "JUDGE":
                run["state"] = "NEXT"
            elif result["concreteGap"]:
                run.update(observation=result, repairRequired=bool(run.get("jobId")))
                _steering(store, run, "; ".join(result["gaps"]))
            elif pending["family"] == "user-interpretation" and run.get("userRequest"):
                words = next(iter(approvals.records(store, "run-user-words")), {})
                choice = result["meaning"].get("decision")
                if choice == "approve":
                    request = run["userRequest"]
                    if request.get("clarificationId"):
                        store.answer(request["clarificationId"], words["words"]["quoted_message"])
                    run.update(state="PLAN", userRequest=None)
                elif choice in {"decline", "cancel"}:
                    run.update(state="PAUSED", reason="User declined the pending action")
                else:
                    run.update(state="PLAN", userRequest=None)
            elif run["state"] == "JUDGE":
                run["state"] = "NEXT"
        else:
            store._event("run-semantic-answer", dict(requestId=request_id, family=pending["family"],
                         answer=meaning, source=source, sourceVerified=False))
            option = meaning["option"] if pending["family"] == "direction" else meaning.get("readiness", "ready")
            if pending["family"] == "direction":
                _direction(
                    store,
                    run,
                    option,
                    ({"question": meaning["question"]} if meaning.get("question") else {}),
                    meaning["rationale"],
                    direction_judgment_id=result["id"],
                )
            elif option == "draft":
                ask_question(store, run, "author", pending["purpose"], context=pending["context"])
            elif option in {"research", "ask_user"}:
                _direction(store, run, option, ({"question": meaning["question"]} if meaning.get("question") else {}), meaning["rationale"])
            elif pending["purpose"] == "plan-outline" and "targets" in pending["answerSchema"]["properties"]:
                outline = next(r for r in approvals.records(store,"trace-outline") if r["requestId"] == pending["requestId"])
                context = {**pending["context"], **{k:v for k,v in outline.items() if k != "requestId"}}
                if any(context[k] for k in ("nodes","targets","scenarios")):
                    ask_question(store,run,"author","trace",context=context)
                else:
                    run.update(state="PLAN",step="prepare")
            elif pending["purpose"] == "finding-dispositions":
                pass
            elif run["step"] == "understanding":
                run.update(state="PLAN", step="trace")
            else:
                run.update(state="PLAN", step="prepare")
        save(store, run)


def _decision(store, run, outcome):
    if outcome["state"] == "pending-judgment":
        return
    run["question"] = None
    kind = "acceptance-consent-pending" if outcome.get("runId") else confirmations.RESPONSE
    row = store.connection.execute(
        "SELECT id FROM events WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)
    ).fetchone()
    if row:
        run["userDecisionEventId"] = row["id"]
    if outcome["state"] != "approved":
        run.update(
            state="PAUSED" if outcome["state"] in {"declined", "decline", "cancel"} else "PLAN",
            step=("understanding" if outcome.get("gate") == "align+vision-lock" else "trace")
            if outcome["state"] in {"changes-requested", "changes_requested"}
            else run["step"],
            reason="User decision: " + outcome["state"],
            userRequest=None,
        )
        return
    gate = outcome["gate"]
    run["userRequest"] = None
    if gate == "plan-execution":
        run.update(state="BUILD", step="execution", pendingConsent=None, freshConsent=False)
    else:
        run.update(state="PLAN", step="trace")


def _interrupt(store, run, *, advance_generation=True):
    old_effect = run.get("effect")
    if advance_generation:
        run["generation"] += 1
    if (pending := run.get("question")) and not (
        run.get("freshConsent") and pending["purpose"] == "scope-intent"
    ):
        record = store._judgment_record(pending["requestId"])
        record.update(
            supersededBy="control:" + str(run["generation"])
            if advance_generation
            else "words:" + uuid4().hex,
            disposition="steer",
            error="Superseded by recorded direction or new actual user words",
            owner=None,
        )
        store._event("judgment-recorded", record)
    run.update(question=None, effect=None, semanticHeld=False, settling=old_effect)


def user_reply(store, user_words):
    """Retain words for the current displayed subject; no caller chooses transitions."""
    initialize(store)
    with store._transaction():
        run = cursor(store)
        if run is None or run["state"] in TERMINAL:
            raise PlanStoreError("run-terminal", "This run is terminal")
        request = run.get("userRequest")
        if not request or request.get("request_id") != user_words.get("request_id"):
            raise PlanStoreError(
                "user-subject-stale",
                "Retain actual words for the current userRequest.request_id",
            )
        gate = request["gate"]
        if user_words.get("expected_revision") != request["plan_revision"]:
            raise PlanStoreError(
                "user-subject-stale", "Retain words against the current displayed revision"
            )
        if run.get("effect") or run.get("semanticHeld"):
            _interrupt(store, run, advance_generation=False)
        if gate == "C":
            outcome = store.accept(
                by=user_words["actor_label"], statement=user_words["quoted_message"]
            )
            if outcome.get("judgment"):
                ask_question(
                    store,
                    run,
                    "user-interpretation",
                    "user-decision",
                    judgment=outcome["judgment"],
                )
            elif outcome.get("id"):
                run.update(
                    state="DONE",
                    userRequest=None,
                    reason="Bound C accepted",
                    nextAction="Complete",
                )
        elif gate in {"research", "ask_user", "wave-check-in"}:
            assessment = approvals.assess(
                store,
                "user-decision",
                dict(
                    subject=request,
                    words=user_words["quoted_message"],
                    source=user_words,
                    question="Interpret these retained actual user words for the displayed question; never infer human identity",
                ),
            )
            store._event(
                "run-user-words",
                dict(request=request, words=user_words, judgmentId=assessment["id"]),
            )
            ask_question(
                store, run, "user-interpretation", "user-decision", judgment=assessment
            )
        else:
            outcome = confirmations.complete_chat_confirmation(store, gate=gate, **user_words)
            if outcome["state"] == "pending-judgment":
                ask_question(
                    store,
                    run,
                    "user-interpretation"
                    if outcome["judgment"]["purpose"] == "user-decision"
                    else "assess",
                    outcome["judgment"]["purpose"],
                    judgment=outcome["judgment"],
                )
            else:
                _decision(store, run, outcome)
        save(store, run)
    return run_loop(store)


def control(store,text,message_id):
    """Literal controls have one loop owner."""
    with store._transaction():
        run = cursor(store)
        if run["state"] not in TERMINAL:
            retained = run.get("userRequest")
            _interrupt(store,run)
            if text == "/resume" and not retained:
                # Resume after a released attempt needs bound meaning; the
                # recovery owner decides whether that direction permits work.
                run["semanticHeld"] = False
                _steering(store,run,"user-direction")
            elif text == "/resume" and retained:
                if retained["gate"] == "C":
                    _offer_c(store,run)
                elif retained["gate"] in {"align+vision-lock","plan-execution"}:
                    scope = retained.get("proposedAction",{})
                    save(store,run)  # Bind the refreshed decision to this control generation.
                    _user(store,run,retained["gate"], **{k:v for k,v in dict(execution=scope.get("execution")).items() if v is not None})
                else:
                    run.update(state="WAIT_USER",userRequest=retained)
            else:
                _direction(store,run,{"/pause":"pause","/stop":"stop","/resume":"continue"}[text],{},"Explicit user run control")
                if text == "/pause":
                    run["userRequest"] = retained
            store._event("run-control",dict(direction=text[1:],messageId=message_id,generation=run["generation"]))
            save(store,run)
    return run_loop(store)


def _direction(store, run, direction, content, rationale, *, direction_judgment_id=None):
    if direction not in {"continue", "repair", "replan", "research", "ask_user", "pause", "stop"}:
        raise ValueError("Choose continue, repair, replan, research, ask_user, pause or stop")
    if direction_judgment_id and run.get("jobId") and direction in {"continue", "repair", "replan"}:
        from codex_harness.core import planner_recovery as recovery

        if (recovery.disposition(store, run["jobId"]) or {}).get("choice") == "steer":
            run["recoveryDirection"] = direction_judgment_id
            if direction == "continue" and not content:
                resolved = recovery.resolve(
                    store,
                    run["jobId"],
                    "readmit",
                    rationale,
                    direction_judgment_id=direction_judgment_id,
                )
                run.update(resumeRun=resolved.get("resumeRun"), state="BUILD")
                return
    run.update(reason=rationale, userRequest=None)
    if direction == "continue" and run.get("repairRequired") and not content.keys() - {"mail"}:
        _steering(store, run, "Observed proof requires worker repair or new evidence")
        run["semanticHeld"] = True
    elif direction in {"pause", "stop"}:
        run.update(
            state=CONTROL_STATES[direction],
            nextAction="Explicit user resume" if direction == "pause" else "Terminal",
        )
    elif direction in {"repair", "replan"}:
        run.update(
            state="PLAN",
            step=run.pop("returnAuthorStep", "trace"),
            repairDirection=direction_judgment_id if run.get("jobId") else None,
        )
    elif direction == "continue" and run.get("freshConsent"):
        run.update(state="PLAN", step="prepare")
    elif direction in {"research", "ask_user"}:
        if not isinstance(content.get("question"), str) or not content["question"].strip():
            raise ValueError("A user/research choice needs the actual question in content.question")
        clarification = store.ask(content["question"], store.spine().ids("vision"))
        run.update(
            state="WAIT_USER",
            userRequest=dict(
                gate=direction,
                request_id=uuid4().hex,
                plan_revision=store._revision(),
                question=content.get("question"),
                research=content.get("research"),
                clarificationId=clarification,
                returnStep=run["step"],
            ),
        )
    else:
        if content.keys() - {"mail"}:
            run.update(state="PLAN", step="trace")
        else:
            run["state"] = "NEXT" if run.get("jobId") else "REVIEW" if run["step"] == "plan-review" else "PLAN"


def _release(store, run, effect, reason):
    from codex_harness.core import planner_jobs as jobs, planner_recovery as recovery

    if any(
        r["effect"]["id"] == effect["id"] for r in approvals.records(store, "run-owner-released")
    ):
        return
    (store.path.parent / ("effect-stop-" + effect["id"])).touch()
    if effect.get("jobId"):
        job_id = effect["jobId"]
        directory = jobs._directory(store.workspace, job_id)
        if (directory / "request.json").exists() and not recovery.disposition(store, job_id):
            recovery.resolve(
                store, job_id, "abandon" if run["state"] == "STOPPED" else "steer", reason
            )
    for identifier in effect.get("attemptIds", []):
        if not recovery.disposition(store, identifier):
            proof = recovery.attempt_proof(store, identifier)
            if proof["attempt"]["state"] not in {"completed", "stale", "stopped", "superseded"}:
                recovery.release_attempt(store, identifier, reason)
    store._event(
        "run-owner-released",
        dict(
            effect=effect,
            reason=reason,
            ownershipReleased=True,
            quiescenceVerified=False,
            quarantine=True,
            nextAction="Inspect retained cleanup evidence; recover only from a verified source",
        ),
    )


def _reconcile(store, run):
    """The first awake turn settles ownership before any possible dispatch."""
    from codex_harness.core.planner_recovery import release_attempt

    bound = {
        identifier
        for effect in (run.get("effect"), run.get("settling"))
        if effect
        for identifier in effect.get("attemptIds", [])
    }
    for kind, table in (
        ("acceptance", "acceptance_attempts"),
        ("plan-review", "review_attempts"),
        ("audit", "audit_attempts"),
    ):
        if "state" not in {
            row["name"] for row in store.connection.execute(f"PRAGMA table_info({table})")
        }:
            continue
        for row in store.connection.execute(
            f"SELECT id FROM {table} WHERE state IN ('queued','running','unknown-interrupted')"
        ).fetchall():
            identifier = kind + ":" + row["id"]
            if identifier not in bound:
                release_attempt(
                    store,
                    identifier,
                    "Activation released an unbound historical admission; process quiescence UNKNOWN",
                )
    if effect := run.get("settling"):
        _release(store, run, effect, "User control revoked the previous owner")
        run["settling"] = None
    effect = run.get("effect")
    if not effect:
        return False
    expired = datetime.now(UTC) >= datetime.fromisoformat(effect["deadlineAt"])
    gap = (datetime.now(UTC) - datetime.fromisoformat(run["observedAt"])).total_seconds()
    if gap > 2:
        store._event(
            "run-wake-gap",
            dict(elapsedSeconds=gap, effectId=effect["id"], deadlineAt=effect["deadlineAt"]),
        )
    if effect["kind"] == "build":
        from codex_harness.core import planner_jobs as jobs

        observed = jobs.segment_status(store, effect)
        if observed.get("terminal"):
            if not observed["verifiedExit"]:
                _quarantine(store, run, effect, "Bounded segment cleanup is UNVERIFIED")
                return False
            if observed["terminal"]["status"] == "failed":
                _recover(store, run, effect, "Bounded worker segment failed", observation=observed)
                return False
            run.update(effect=None, state="OBSERVE", observation=observed)
            return False
        if (expired or observed.get("deadOwner")) and (jobs.segment_directory(store.workspace, effect["jobId"], effect.get("segmentId") or effect["id"]) / "request.json").is_file():
            _recover(store, run, effect, "Dead or expired controller on activation", observation=observed)
            return False
        # Only a receipt from the existing owner can attest liveness. Unknown is
        # quarantined; a PID or a missing terminal record never counts as live.
        if observed.get("launched"):
            return True
        from codex_harness.core.audit_process import process_observation

        owner = process_observation(effect["ownerPid"])
        if (
            "launch" not in observed
            and not expired
            and effect.get("ownerIdentity")
            and owner.get("running")
            and owner.get("identity") == effect["ownerIdentity"]
        ):
            return True
        if (jobs.segment_directory(store.workspace, effect["jobId"], effect.get("segmentId") or effect["id"]) / "request.json").is_file():
            _recover(store, run, effect, "Interrupted segment admission", observation=observed)
        else:
            run.update(generation=run["generation"] + 1, effect=None, state="BUILD")
        return False
    else:
        from codex_harness.core.audit_process import process_observation

        observed = process_observation(effect.get("ownerPid"))
        if (
            not expired
            and effect.get("ownerIdentity")
            and observed.get("identity") == effect["ownerIdentity"]
            and observed.get("running")
        ):
            return True
    if _consume_completed_effect(store, run, effect):
        return False
    if effect["kind"] == "judgment-continuation":
        from codex_harness.core.planner_recovery import inspect

        if inspect(store.workspace, run["jobId"])["revalidation"]["available"]:
            run.update(generation=run["generation"] + 1, effect=None, state="NEXT")
            return False  # Reobserve retained bytes under current B; never stop the exited worker.
    _quarantine(store, run, effect, "Interrupted effect has no verified live owner")
    return False


def _reviewed(store, run, reviewed):
    run.update(effect=None, observation=dict(review=reviewed, findings=store.finding_state()))
    prerequisite = store.review_prerequisite(store.manifest_revision())
    ready = prerequisite["complete"] or prerequisite["unavailable"]
    if not ready and not _pending(store, run):
        _steering(store, run, "Review did not establish current semantic readiness")


def _continued(store, run, result):
    from codex_harness.core import planner_candidate as candidate

    run["effect"] = None
    if result["state"] == "pending-build":
        selected = candidate.retained(store.workspace, run["jobId"])
        run.update(
            state="BUILD", resumeRun=str(Path(selected["runPath"]).parent.parent)
        )
    elif result["state"] == "PASS":
        run["step"] = "validation-complete"
        _steering(
            store, run, "Integrated GREEN: resync with the vision before acceptance"
        )
    elif result.get("proof") and not result["proof"].get("judgmentId"):
        run.update(state="OBSERVE", repairRequired=False)
    elif result["state"] == "ERROR":
        _steering(store, run, "Retained observation refused: " + str(result["results"]))
    elif result["state"] == "pending-judgment":
        run.update(observation=result, repairRequired=True)
        _steering(
            store,
            run,
            "Execution meaning did not admit the observed proof; supply a repair or new evidence",
        )
    else:
        _recover(
            store,
            run,
            dict(jobId=run["jobId"]),
            "Observed execution gap: " + json.dumps(result),
            observation=result,
        )


def _consume_completed_effect(store, run, effect):
    """Consume exact completed originals before releasing an interrupted owner."""
    try:
        if effect["kind"] == "review" and (reviewed := store.completed_review(effect)):
            run.update(generation=run["generation"] + 1, state="REVIEW")
            _reviewed(store, run, reviewed)
            return True
        if effect["kind"] == "semantic":
            from codex_harness.core.run_invocations import completed_response

            pending = run.get("question")
            completed = completed_response(store, effect, pending, run["setup"]["roleTuples"])
            if completed is None:
                return False
            selected, value, facts = completed
            run.update(generation=run["generation"] + 1, effect=None)
            save(store, run)
            store._event(
                "run-invocation-completed",
                dict(
                    effectId=effect["id"],
                    requestId=pending["requestId"],
                    role=pending["role"],
                    selected=selected,
                    didRunProvider=True,
                    sourceVerified=False,
                ),
            )
            _record_answer(
                store,
                request_id=pending["requestId"],
                answer=value,
                source="provider-normal:" + selected["provider"] + ":" + selected["model"],
                provider=selected["provider"],
                facts=facts,
            )
            run.clear()
            run.update(cursor(store))
            return True
        if effect["kind"] == "judgment-continuation":
            from codex_harness.core import planner_jobs as jobs, planner_candidate as candidate

            segment = jobs.latest_segment(store, run["jobId"])
            observed = jobs.segment_status(store, dict(jobId=run["jobId"], id=segment.name))
            completed = observed.get("terminal", {}).get("continuation", {})
            if completed.get("effectId") != effect["id"]:
                result = candidate.completed_revalidation(store, run["jobId"], effect)
                if not observed["verifiedExit"] or result is None:
                    return False
                jobs.complete_continuation(store, result)
                completed = dict(result=result)
            run["generation"] += 1
            _continued(store, run, completed["result"])
            return True
        if effect["kind"] != "acceptance":
            return False
        from codex_harness.core.planner_acceptance import completed_observation

        completed = completed_observation(store, effect)
        if completed is None:
            return False
        observed, review = completed
        run.update(
            generation=run["generation"] + 1,
            effect=None,
            state="NEXT",
            step="acceptance-captured",
            capturedAcceptance=observed,
            resumeReview=review,
        )
        return True
    except (ValueError, OSError, KeyError, StopIteration) as error:
        run["completionError"] = str(error)
        return False


def _quarantine(store, run, effect, reason):
    run["generation"] += 1
    run["effect"] = None
    save(store, run)
    _release(store, run, effect, reason)
    _steering(store, run, reason + "; retained namespace quarantined, no blind retry")
    run["semanticHeld"] = True


def _recover(store, run, effect, reason, *, observation=None):
    from codex_harness.core import planner_recovery as recovery
    run["generation"] += 1
    run["effect"] = None
    save(store, run)
    resolved = recovery.resolve(store, effect["jobId"], "readmit", reason,
                                controller_observation=(observation or {}).get("processObservation"))
    run.update(effect=None, resumeRun=resolved.get("resumeRun"), observation=dict(recovery=resolved, execution=observation))
    if resolved["choice"] == "abandon":
        run["semanticHeld"] = True
    if resolved["choice"] == "readmit":
        run.update(state="BUILD", reason=reason + "; automatic recovery within existing B")
    else:
        _steering(
            store,
            run,
            "Permission/uncertain launch held"
            if resolved["choice"] == "abandon"
            else "Two automatic recoveries exhausted; planner direction required",
        )


def run_loop(store):
    run = cursor(store)
    if run and run["state"] not in TERMINAL | {"PAUSED"}:
        initialize(store)
    while True:
        _step(store)
        if not _automatic_question(store):
            return run_baton(store)


def _step(store):
    """Reconcile then step until meaning, a real user, bounded work or terminal state."""
    with store._transaction():
        run = cursor(store)
        if run is None:
            raise PlanStoreError("run-not-started", "Call harness_chat")
        for error in Mailbox.deliver_run_requests(store, run["runId"]):
            if run["state"] not in TERMINAL | {"PAUSED"}:
                _steering(store, run, "Structured mail was refused by its owner: " + error)
        if _reconcile(store, run):
            return run_baton(store)
        previous = approvals.current(store, "B") or approvals.latest_request(store, "B")
        if previous and not run.get("freshConsent") and run["state"] not in TERMINAL | {"PAUSED"}:
            try:
                store.compile_template(store.workspace / "template")
            except PlanStoreError:
                pass  # Author incomplete structural data before preparing its scope comparison.
            else:
                approvals.scope_assessment(store, before=previous["subject"]["scope"], prepare=True)
        save(store, run)
    while run["state"] not in TERMINAL | {"PAUSED"} and not run.get("question"):
        effect_kind = None
        with store._transaction():
            run = cursor(store)
            if run.get("effect") or run.get("question") or run["state"] in TERMINAL | {"PAUSED"}:
                break
            if _pending(store, run):
                save(store, run)
                break
            state = run["state"]
            if state == "WAIT_USER":
                save(store, run)
                break
            if state == "PLAN":
                if run["step"] == "prepare":
                    try:
                        approvals.require_approval(store, "A")
                    except PlanStoreError:
                        _user(store, run, "align+vision-lock")
                    else:
                        try:
                            if store.spine().semantic: (store.workspace / "template").mkdir(exist_ok=True)
                            store.compile_template(store.workspace / "template")
                            store.advance_to("roundtable", by="run-loop")
                        except ValueError as error:
                            ask_question(store,run,"author","trace",context={**getattr(error,"diagnostics",{}),"obligations":[str(error)],
                                "instructions":"Complete the compiler obligations inside the accepted scope; workers write code."})
                        else: run.update(state="REVIEW", step="plan-review")
                elif run["step"] == "trace":
                    try:
                        approvals.require_approval(store, "A")
                    except PlanStoreError:
                        from codex_harness.core.planner_vision import expansion_status

                        if expansion_status(store.spine().node(store.spine().ids("vision")[0]))[
                            "gaps"
                        ]:
                            run["step"] = "understanding"
                        else:
                            _user(store, run, "align+vision-lock")
                    else:
                        store.advance_to("plan", by="run-loop")
                if run["state"] == "PLAN" and not run.get("userRequest") and not run.get("question"):
                    ask_question(store,run,"author","plan-outline" if run["step"] == "trace" else run["step"],
                                 context=dict(instructions="Author semantics only. Workers write source and tests."))
            elif state == "REVIEW":
                from codex_harness.core.run_setup import select_reviewers, workers
                select_reviewers(store, run)
                run["workers"] = workers(store, run)
                if _pending(store, run):
                    save(store, run)
                    break
                prerequisite = store.review_prerequisite(store.manifest_revision())
                if prerequisite["complete"] or prerequisite["unavailable"]:
                    execution = dict(
                        expected_revision=store.manifest_revision(),
                        workers=run["workers"],
                        max_parallel=run["max_parallel"],
                        timeout=run["timeout"],
                    )
                    from codex_harness.core.planner_execution import execution_scope

                    scope = execution_scope(store, **execution, _candidate=True)
                    try:
                        approvals.require_approval(store, "B", dict(execution=scope))
                    except PlanStoreError:
                        _user(store, run, "plan-execution", execution=execution)
                    else:
                        run.update(state="BUILD", step="execution", resumeRun=None)
                else:
                    effect_kind = "review"
            elif state == "BUILD":
                if not run.get("resumeRun"):
                    store.advance_to("publish", by="run-loop")
                    level = store.proof_level(store.manifest_revision()) or "oracle-verified"
                    revision = store.publish(
                        by="run-loop",
                        export_dir=store.workspace / "plan",
                        proof_level=level,
                        reason="Retained prospective audit; no oracle execution is claimed"
                        if level == "prospective"
                        else None,
                    )
                    run["publicationRevision"] = revision
                    store.advance_to("execute", by="run-loop")
                    if run.get("recoveryDirection") and run.get("jobId"):
                        from codex_harness.core import (
                            planner_recovery as recovery,
                            planner_jobs as jobs,
                        )

                        prior = recovery.disposition(store, run["jobId"]) or {}
                        if (
                            prior.get("choice") == "steer"
                            and jobs._request(jobs._directory(store.workspace, run["jobId"]))[
                                "publicationRevision"
                            ]
                            == revision
                        ):
                            recovery.resolve(
                                store,
                                run["jobId"],
                                "readmit",
                                run["reason"],
                                direction_judgment_id=run["recoveryDirection"],
                            )
                if run.get("repairDirection") and run.get("jobId"):
                    from codex_harness.core import (
                        planner_jobs as jobs,
                        planner_recovery as recovery,
                    )

                    previous = jobs._request(jobs._directory(store.workspace, run["jobId"]))
                    if previous["publicationRevision"] == run[
                        "publicationRevision"
                    ] and not recovery.disposition(store, run["jobId"]):
                        store._event(
                            "run-repair-required",
                            dict(
                                jobId=run["jobId"],
                                observation=run.get("observation"),
                                judgmentId=run["repairDirection"],
                            ),
                        )
                        resolved = recovery.resolve(store, run["jobId"], "readmit", run["reason"])
                        if resolved["choice"] == "steer":
                            _steering(
                                store, run, "Two repairs exhausted; choose a recorded direction"
                            )
                    run["repairDirection"] = None
                if not run.get("question"):
                    effect_kind = "build"
            elif state == "OBSERVE":
                from codex_harness.core.planner_candidate import current_job_run, record_judgment

                candidate, record = current_job_run(store, required=True)
                proof = record.get("semanticValidation") or record.get("semanticContracts")
                if proof and not proof.get("judgmentId"):
                    prepared = record_judgment(
                        store, repo=candidate["candidateRoot"], check="execution"
                    )
                    run["state"] = "JUDGE"
                    ask_question(store, run, "assess", "execution", judgment=prepared)
                elif record["status"].startswith("plan-failed"):
                    _steering(store, run, record["status"])
                else:
                    run["state"] = "NEXT"
            elif state == "NEXT":
                from codex_harness.core import planner_candidate as candidate
                from codex_harness.core import planner_recovery as recovery

                selected, record = candidate.current_job_run(store)
                proof = (record or {}).get("semanticValidation") or (record or {}).get("semanticContracts")
                prior = recovery.disposition(store, run["jobId"]) or {}
                if not selected["complete"] or prior.get("validation", {}).get("state") in {"ERROR", "FAIL", "pending-judgment"}:
                    available = recovery.inspect(store.workspace, run["jobId"])
                    if "revalidate" in available["options"]:
                        effect_kind = "judgment-continuation"
                    else:
                        _steering(store, run, selected["reason"])
                elif run.get("step") == "acceptance-reviewed":
                    _offer_c(store, run)
                elif proof and proof.get("judgmentStatus") == "pending-judgment":
                    effect_kind = "judgment-continuation"
                elif (
                    not store.spine().semantic
                    or run.get("step") == "validation-complete"
                    or proof
                    and proof["passed"]
                ):
                    quality = (
                        candidate.judgments_for(store, selected["candidateRoot"]).get(
                            "visualQuality", {}
                        )
                        if store.spine().semantic
                        else {}
                    )
                    if store.spine().semantic and not quality.get("passed"):
                        prepared = candidate.record_judgment(
                            store, repo=selected["candidateRoot"], check="visualQuality"
                        )
                        ask_question(store, run, "assess", "visualQuality", judgment=prepared)
                    else:
                        store.advance_to("accept", by="run-loop")
                        effect_kind = "acceptance"
                else:
                    _steering(store, run, "Choose a safe direction from retained observations")
            elif state == "STEER":
                _steering(store, run, run["reason"])
            if effect_kind:
                from codex_harness.core.audit_process import process_observation

                effect = dict(
                    id=uuid4().hex,
                    kind=effect_kind,
                    generation=run["generation"],
                    ownerPid=os.getpid(),
                    deadlineAt=(
                        datetime.now(UTC)
                        + timedelta(
                            seconds=run["timeout"]
                            * (len(run["workers"]) + 1 if effect_kind == "build" else 1)
                        )
                    ).isoformat(),
                )
                if effect_kind == "acceptance" and (captured := run.get("capturedAcceptance")):
                    effect["attemptIds"] = ["acceptance:" + captured["attemptId"]]
                effect["ownerIdentity"] = process_observation(os.getpid()).get("identity")
                if effect_kind == "build":
                    from codex_harness.core.planner_recovery import admission_id

                    effect["jobId"] = (
                        run["jobId"]
                        if run.get("resumeRun")
                        else admission_id(store, run["publicationRevision"])
                    )
                    run["repairRequired"] = False
                run.update(
                    effect=effect,
                    nextAction="Await bounded " + effect_kind,
                    reason="Effect identity committed before launch",
                )
            save(store, run)
        if effect_kind:
            _execute_effect(store, run, effect_kind)
            run = cursor(store)
            if run.get("effect") or run.get("userRequest"):
                break
        run = cursor(store)


def review_response(store, launch, invoke, consume, retry, capture=None):
    """One retry policy; the existing judgment owns its exact-fix allowance."""
    while True:
        ensure_effect(store)
        if capture is not None:
            capture(launch, None)
        response = invoke(launch)
        if capture is not None:
            capture(launch, response)
        record, items = consume(response)
        if record.get("disposition") != "exact-fix":
            return launch, response, record, items
        judgment = store.judgment(record["judgmentId"])
        launch = retry(judgment)


def review_dossier(store, result, reviewer, record, *, executable, runner, timeout,
                   token_budget=None, shards=None, stop_on_invalid=False, capture=None,
                   abandon=None, before_launch=None, **packet_options):
    """Replace overflowed dossiers once; the request owner retains obligations."""
    from codex_harness.core import review_packets
    from codex_harness.core.review_rounds import _review_shard
    from codex_harness.core.worker_packet_budget import PROVIDER_TOKEN_BUDGETS

    provider = reviewer["provider"]
    budget = PROVIDER_TOKEN_BUDGETS[provider] if token_budget is None else token_budget
    if shards is None:
        shards = review_packets.packet_shards(store, provider, token_budget=budget, **packet_options)
    for attempt in range(2):
        completed = []
        record.update(shards=len(shards), shardDetails=[],
                      truncatedNodes=sorted({key for shard in shards for key in shard["truncatedNodes"]}))
        for shard in shards:
            shard_record, items = _review_shard(
                store, result, reviewer, shard, executable=executable,
                runner=runner, timeout=timeout, capture=capture,
                **({"before_launch": before_launch} if before_launch is not None else {}),
            )
            record["shardDetails"].append(shard_record)
            completed.append((shard_record, items))
            if shard_record.get("primaryError", {}).get("promptTooLong"):
                break
            if shard_record.get("primaryError", {}).get("authenticationFailed") or stop_on_invalid and shard_record["invalid"]:
                yield from completed
                return
        else:
            yield from completed
            return
        if attempt == 1:
            yield from completed
            return
        record["reshardedFrom"] = dict(budget=budget, shards=len(shards),
            shardDetails=record["shardDetails"], reason=shard_record["primaryError"]["reason"])
        if abandon is not None:
            abandon()
            yield from completed
            return
        budget = max(1, budget // 2)
        record["reshardedFrom"]["newBudget"] = budget
        try:
            shards = review_packets.packet_shards(store, provider, token_budget=budget, **packet_options)
        except ValueError as error:
            # A header that cannot fit must not hide the actual provider failure.
            record["reshardedFrom"]["reshardError"] = str(error)
            yield from completed
            return
        prior = record["reshardedFrom"]["shardDetails"][0]
        for shard in shards:
            shard["reviewRoundId"] = prior["reviewRoundId"]
        review_packets.bind_review_dossiers(store, {provider: shards}, [reviewer],
            candidate_run={"id": prior["acceptanceRunId"]} if prior["acceptanceRunId"] else None)
        record["reshardedFrom"]["newShardCount"] = len(shards)


def _review_acceptance(store, repo):
    from codex_harness.core.planner_review import review

    budgets = cursor(store)["effect"].get("reviewTokenBudgets", {})
    while True:
        previous = next(iter(approvals.records(store, "acceptance-review-failed")), {})
        try:
            return review(store, repo, token_budgets=budgets or None)
        except ValueError:
            failed = next(iter(approvals.records(store, "acceptance-review-failed")), {})
            overflow = next(
                (
                    row
                    for row in failed.get("reviewers", [])
                    if row.get("reshardedFrom") and row["provider"] not in budgets
                ),
                None,
            )
            if failed.get("id") == previous.get("id") or overflow is None:
                raise
            budgets[overflow["provider"]] = max(1, overflow["reshardedFrom"]["budget"] // 2)
            with store._transaction():
                ensure_effect(store)
                run = cursor(store)
                run["effect"]["reviewTokenBudgets"] = budgets
                save(store, run)


def _offer_c(store, run):
    from codex_harness.core import acceptance
    from codex_harness.core.planner_candidate import current_job_run

    _, product = current_job_run(store, required=True)
    observed = acceptance._latest_run(store)
    run.update(effect=None, step="acceptance-reviewed", state="NEXT")
    try:
        if reason := acceptance.run_invalid_reason(store, observed):
            acceptance._refuse(reason)
        acceptance.require_c_evidence(store, observed)
    except PlanStoreError as error:
        if not _pending(store, run):
            run.update(observation=dict(run=observed, reason=str(error)), repairRequired=True)
            _steering(
                store, run, "Acceptance evidence requires a recorded direction: " + str(error)
            )
        return
    vision = store.spine().node(store.spine().ids("vision")[0])
    run.update(
        state="WAIT_USER",
        userRequest=dict(
            gate="C",
            request_id="C:" + observed["id"],
            plan_revision=store._revision(),
            run=observed,
            delivery=product.get("delivery", dict(root=product["repo"])),
            guide=vision["fields"].get(
                "first_user_moment", vision["fields"].get("firstUserMoment", vision["text"])
            ),
        ),
        nextAction="Live walkthrough and try-it guide, then actual user C",
        reason="Acceptance evidence retained; C is a user decision",
    )


def _execute_effect(store, run, kind):
    generation, identifier = run["generation"], run["effect"]["id"]
    store._run_generation = generation
    store._run_effect_id = identifier
    try:
        if kind == "review":
            from codex_harness.core import roundtable
            from codex_harness.core.planner_review import (
                delivery_review_selection,
                selection_inputs,
            )

            reviewers = delivery_review_selection(selection_inputs(store)[1]["fields"])[0]
            reviewed = roundtable.run_roundtable(store, reviewers=reviewers, timeout=run["timeout"])
            with store._transaction():
                ensure_effect(store, generation)
                run = cursor(store)
                _reviewed(store, run, reviewed)
                save(store, run)
        elif kind == "build":
            from codex_harness.core import planner_jobs as jobs

            if run.get("resumeRun"):
                observed = jobs.start_segment(store, run["jobId"], identifier, run["resumeRun"])
            else:
                observed = jobs.start_worker_job(
                    store,
                    run["publicationRevision"],
                    run["workers"],
                    max_parallel=run["max_parallel"],
                    timeout=run["timeout"],
                    segment_id=identifier,
                )
            with store._transaction():
                ensure_effect(store, generation)
                run = cursor(store)
                run["jobId"] = observed["jobId"]
                run["effect"]["jobId"] = observed["jobId"]
                if observed.get("terminal"):
                    run["effect"]["segmentId"] = observed["terminal"].get("segmentId")
                save(store, run)
        elif kind == "judgment-continuation":
            from codex_harness.core import planner_candidate as candidate, planner_recovery as recovery

            prior = recovery.disposition(store, run["jobId"]) or {}
            refresh = not candidate.current_job_run(store)[0]["complete"] or prior.get("choice") == "revalidate"
            if refresh:
                recovery.resolve(store, run["jobId"], "revalidate", run["reason"])
            try:
                result = candidate.revalidate(store, run["jobId"], identifier)
            except (ValueError, OSError, SubprocessError) as error:
                result = dict(state="ERROR", results=[dict(reason=str(error))], delivery=None)
            from codex_harness.core.planner_jobs import complete_continuation

            complete_continuation(store, result)
            with store._transaction():
                ensure_effect(store, generation)
                run = cursor(store)
                _continued(store, run, result)
                save(store, run)
        else:
            from codex_harness.core.planner_acceptance import run_capture
            from codex_harness.core.planner_candidate import current_job_run as candidate_product

            selected, product = candidate_product(store, required=True)
            repo = str(store.workspace) if store.spine().semantic else product["repo"]
            observed = run.get("capturedAcceptance") or run_capture(store, repo)
            if observed["state"] == "completed" and not observed["result"]["passed"]:
                with store._transaction():
                    ensure_effect(store, generation)
                    current = cursor(store)
                    current.update(effect=None, observation=observed, repairRequired=True)
                    _steering(
                        store,
                        current,
                        "Acceptance observed a product gap; author worker-owned repair",
                    )
                    save(store, current)
                return
            if observed["state"] != "completed":
                raise ValueError(
                    "Acceptance did not establish integrated GREEN: " + str(observed.get("error"))
                )
            if run.get("resumeReview"):
                from codex_harness.core.planner_review import review
                from codex_harness.core.run_ownership import attach_attempt

                attach_attempt(store, "review:" + run["resumeReview"])
                review(store, repo, resume=run["resumeReview"])
            else:
                _review_acceptance(store, repo)
            with store._transaction():
                ensure_effect(store, generation)
                run = cursor(store)
                run.update(capturedAcceptance=None, resumeReview=None)
                _offer_c(store, run)
                save(store, run)
    except (ValueError, OSError) as error:
        with store._transaction():
            current = cursor(store)
            if (
                current["generation"] == generation
                and (current.get("effect") or {}).get("id") == identifier
            ):
                _release(store, current, current["effect"], f"Effect failed: {error}")
                _steering(store, current, f"{type(error).__name__}: {error}")
                admission = store.connection.execute(
                    "SELECT min(id) FROM events WHERE kind=? AND json_extract(payload_json,'$.effect.id')=?",
                    (EVENT, identifier),
                ).fetchone()[0]
                exhausted = any(
                    json.loads(row[0]).get("fixesIssued") == 2
                    and json.loads(row[0]).get("disposition") == "steer"
                    for row in store.connection.execute(
                        "SELECT payload_json FROM events WHERE kind='judgment-recorded' AND id>?",
                        (admission,),
                    )
                )
                current.update(
                    semanticHeld=not exhausted,
                    nextAction="Answer the bound planner direction"
                    if exhausted
                    else "Supply recorded planner direction or new evidence; no automatic retry",
                )
                save(store, current)
    finally:
        store._run_generation = None
        store._run_effect_id = None


def _automatic_question(store):
    """Invoke authorized semantic roles; MCP itself never initiates host completions."""
    from codex_harness.core.run_invocations import invoke, completed_response
    from codex_harness.core.audit_process import process_observation

    with store._transaction():
        run = cursor(store)
        pending = run.get("question")
        if (not pending or run.get("effect") or run.get("semanticHeld")
                or run["state"] in TERMINAL | {"PAUSED"}):
            return False
        if (pending["revision"] != store._revision()
                or store._judgment_record(pending["requestId"])["disposition"] in {"steer", "complete"}):
            _record_answer(store, request_id=pending["requestId"], answer=None, source="run-loop:retained")
            return True
        selected = next((r for r in run["setup"]["roleTuples"] if r["role"] == pending["role"]), None)
        if selected is None:
            raise ValueError(f"setup.roleTuples: missing required {pending['role']} role; resolve kickoff before continuing")
        pending = dict(pending, judgment=store.judgment(pending["requestId"], candidate=pending["candidate"]))
        identifier = uuid4().hex
        run["effect"] = dict(
            id=identifier,
            kind="semantic",
            generation=run["generation"],
            ownerPid=os.getpid(),
            deadlineAt=(datetime.now(UTC) + timedelta(seconds=run["timeout"])).isoformat(),
            requestId=pending["requestId"],
            ownerIdentity=process_observation(os.getpid()).get("identity"),
        )
        save(store, run)
    try:
        invoke(
            store,
            selected,
            pending,
            run["timeout"],
            dict(workspace=str(store.workspace), generation=run["generation"], effectId=identifier),
        )
        with store._transaction():
            ensure_effect(store, run["generation"], identifier)
            current = cursor(store)
            selected, value, facts = completed_response(store, current["effect"], pending, run["setup"]["roleTuples"])
            current["effect"] = None
            store._event(
                "run-invocation-completed",
                dict(
                    effectId=identifier,
                    requestId=pending["requestId"],
                    role=pending["role"],
                    selected=selected,
                    didRunProvider=True,
                    sourceVerified=False,
                ),
            )
            save(store, current)
            _record_answer(
                store,
                request_id=pending["requestId"],
                answer=value,
                source=f"provider-normal:{selected['provider']}:{selected['model']}",
                provider=selected["provider"],
                facts=facts,
            )
        return True
    except (ValueError, OSError) as error:
        with store._transaction():
            current = cursor(store)
            if (current.get("effect") or {}).get("id") == identifier:
                current["effect"] = None
                _steering(store, current, f"Semantic invocation pending: {error}")
                current.update(
                    semanticHeld=True,
                    nextAction="Supply recorded planner direction or new evidence; no automatic retry",
                )
                save(store, current)
        return False


def wait_for_question(store):
    """A retained bounded segment may finish after its initiating MCP request is lost."""
    while True:
        run = cursor(store)
        effect = run.get("effect")
        if not effect:
            if run["state"] in TERMINAL | {"PAUSED"} or run.get("semanticHeld") or run.get("userRequest"):
                return run_baton(store)
            run_loop(store)
            continue
        expired = datetime.now(UTC) >= datetime.fromisoformat(effect["deadlineAt"])
        if effect["kind"] == "build":
            from codex_harness.core.planner_jobs import segment_status

            observed = segment_status(store, effect)
            if (
                expired
                or observed.get("deadOwner")
                or observed.get("terminal")
            ):
                run_loop(store)
        else:
            from codex_harness.core.audit_process import process_observation

            owner = process_observation(effect["ownerPid"])
            if (
                expired
                or not owner.get("running")
                or owner.get("identity") != effect.get("ownerIdentity")
            ):
                run_loop(store)
        time.sleep(0.05)
    return run_baton(store)
