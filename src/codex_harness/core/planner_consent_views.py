"""Conversation guidance for the canonical A/B subjects and delivered C."""


def consent_recovery(code):
    gate = "vision-lock" if code == "plan-a-consent-required" else "plan-execution"
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
        return "Stop; clarify changes, then show a fresh A/B question and wait for Approve."
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
            else "harness_prepare_chat_confirmation gate=vision-lock; obtain fresh scope consent."
        )
    if phase == "expand":
        return (
            "harness_advance to_phase=vision-lock; the current A subject is checked before locking."
        )
    if phase == "plan":
        return (
            "harness_advance to_phase=roundtable; use B within its recorded limits."
            if plan_authorized
            else "harness_compile; harness_prepare_chat_confirmation gate=plan-execution. "
            "Show the complete B subject and field diff; wait for the actual reply."
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
