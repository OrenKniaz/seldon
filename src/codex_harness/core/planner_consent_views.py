"""Conversation guidance for the canonical A/B subjects and delivered C."""


def consent_recovery(code):
    gate = "align+vision-lock" if code == "plan-a-consent-required" else "plan-execution"
    return (
        f"harness_prepare_chat_confirmation gate={gate}; show the complete subject and field "
        "diff, wait for the actual reply, then harness_record_chat_confirmation. "
        "For first B supply execution={expected_revision,workers,call_cap,max_parallel,timeout}."
    )


def semantic_consent_next(status, result, *, plan_authorized=False):
    """Three decisions; missing information and changed scope still require a reply."""
    if result.get("chatConfirmation"):
        return (
            "harness_record_chat_confirmation after showing the fresh card/diff "
            "and waiting for the reply."
        )
    if result.get("channel") == "chat-confirmation":
        return "Show request; wait for the actual reply, then harness_record_chat_confirmation."
    if (
        result.get("gate") in {"align+vision-lock", "plan-execution"}
        and result.get("state") != "approved"
    ):
        return "Clarify changes, show fresh A/B, then wait for Yes or another listed reply."
    if status.get("open_clarifications"):
        return "Ask the genuine missing-information question; record it with harness_answer."
    phase = status["phase"]
    if phase in {"align", "vision-lock"}:
        if status.get("approval", {}).get("granted"):
            return "harness_advance to_phase=" + ("expand" if phase == "align" else "features")
        return (
            "Prepare understanding, proposed expansion dispositions, quality and Done; "
            "harness_understanding_card creates durable A."
            if phase == "align"
            else "harness_prepare_chat_confirmation gate=align+vision-lock; ask for scope consent."
        )
    if phase == "expand":
        return "harness_advance to_phase=vision-lock"
    if phase == "plan":
        return (
            "harness_advance to_phase=roundtable; use B within its recorded limits."
            if plan_authorized
            else "harness_prepare_chat_confirmation gate=plan-execution"
            if result.get("planRevision")
            else "harness_compile"
        )
    if phase == "accept":
        acceptance = status.get("acceptance", {})
        if acceptance.get("invalidReason") == "acceptance-approval-required":
            run = acceptance["reviews"][-1]["runId"]
            return (
                "C: Does this delivery meet the locked vision? "
                f"Product run {run}, delivery {acceptance['repo']}. "
                "Wait for owner words, then harness_accept."
            )
        return acceptance.get("next", "harness_plan_status")
    return None


def status_next(status, *, store):
    """One status next step for chat and cockpit; no approval or launch authority."""
    from codex_harness.core import planner_views as views
    from codex_harness.core.planner_guidance import BATCH_GUIDANCE

    phase = status["phase"]
    acceptance = status.get("acceptance", {})
    if (
        status.get("planMode") == "semantic-v1"
        and phase in {"publish", "execute"}
        and (revision := status.get("published_revision"))
    ):
        from codex_harness.core.planner_jobs import _directory, worker_job_status
        from codex_harness.core.planner_recovery import admission_id

        revision = admission_id(store, revision)

        if _directory(store.workspace, revision).is_dir():
            job = worker_job_status(store.workspace, revision)
            if job.get("status") == "failed":
                return f"harness_recovery mode=inspect job_id={revision}; supported repair within B"
            if job.get("run", {}).get("delivery"):
                return "harness_advance to_phase=" + ("execute" if phase == "publish" else "accept")
            return f"harness_worker_status job_id={revision} wait_seconds=120"
        if phase == "execute":
            return "harness_worker_start with the exact workers and limits approved in B"
    if (
        phase == "publish"
        and status.get("publication", {}).get("up_to_date")
        and not acceptance.get("accepted")
    ):
        if status.get("continuation", {}).get("recommendation", {}).get("kind") != "not_applicable":
            return views.review_resolution_next(status["reviewResolution"])
        return "harness_advance to_phase=execute"
    if phase == "accept":
        if acceptance.get("invalidReason") == "acceptance-findings-open":
            return (
                "harness_accept_assess schema=true; inspect actual acquisition choices. "
                "Preserve AF originals; record any intended declaration "
                "before fresh assessment. "
                "A later disposition mutation stales current support; support never clears "
                "findings or native final acceptance."
            )
        return (
            semantic_consent_next(status, {})
            if status.get("planMode") == "semantic-v1"
            else acceptance.get("next", "harness_plan_status")
        )
    if status.get("planMode") == "semantic-v1":
        hint = semantic_consent_next(status, {}, plan_authorized=bool(store._current_approval("B")))
        if hint:
            return hint
    gate = status.get("pending_gate")
    if gate == "harness_plan_start":
        return "harness_plan_start idea=<your idea> name=<plan name>"
    if gate and gate.startswith("approval:"):
        return (
            "harness_record_confirmation gate="
            + gate.split(":", 1)[1]
            + "; review the displayed understanding and answer the native client form."
        )
    if status.get("open_clarifications"):
        return "Answer the open questions with harness_answer before advancing."
    if status.get("planMode") == "semantic-v1" and phase in {
        "features",
        "requirements",
        "tech-stack",
    }:
        return BATCH_GUIDANCE
    if phase in {"roundtable", "publish"}:
        resolution = status.get("reviewResolution")
        if resolution and resolution["nextAction"]["kind"] != "not_applicable":
            return views.review_resolution_next(resolution)
    if acceptance.get("accepted"):
        return "Review the recorded acceptance above; harness_plan_status shows current evidence."
    return views.NEXT.get(phase, "Retry harness_plan_status.")
