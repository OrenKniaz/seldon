"""Display-only recovery for planner refusals; never reads state or grants authority."""

import json
import re
import sqlite3

TEACHING = (
    "Never read, search or execute Seldon's implementation or runtime files. "
    "If stuck, call harness_plan_status or ask the user."
)

# Codes observed in runs 15–23 and UX journeys, grouped by their owning rule.
RULES = [
    (
        (
            "plan-b-consent-required combined-consent-required "
            "combined-consent-requires-exact-chat-response "
            "combined-execution-and-review-budget-required "
            "planning-review-budget-consent-required planning-reviewer-consent-required "
            "reviewer-confirmation-required native-execution-confirmation-required "
            "worker-execution-native-origin-required"
        ),
        "The requested work lacks current consent.",
        "Consent must cover the exact candidate, reviewers and limits.",
        (
            "ask the user to answer Seldon's current consent question exactly as shown "
            "(Needs your go-ahead (B) when applicable)."
        ),
    ),
    (
        "planning-review-budget-immutable-scope",
        "The registered review budget cannot be edited by this request.",
        "Only a fresh, bound B approval can raise the campaign cap; spent starts remain counted.",
        "ask the user to decide on a higher campaign total through Seldon's fresh B question.",
    ),
    (
        (
            "planning-review-start-cap-exhausted provider-call-cap-exhausted "
            "roundtable-total-budget-exhausted execution-shared-review-budget-exhausted"
        ),
        "The provider start allowance is exhausted.",
        "Spent admissions count against the original ceiling, including failed starts.",
        "ask the user to stop this campaign because its allowance is exhausted.",
    ),
    (
        (
            "planning-review-budget-invalid-original "
            "provider-call-claim-original-invalid planning-review-budget-invalid-scope "
            "worker-execution-limits-invalid worker-job-call-cap-required"
        ),
        "The requested limits or original budget records are invalid.",
        "Execution requires valid limits and intact original admission records.",
        None,
    ),
    (
        (
            "plan-approval-required plan-approval-stale plan-confirmation-gate-mismatch "
            "plan-confirmation-response-invalid plan-gate-invalid "
            "plan-confirmation-origin-unavailable confirmation-request-superseded"
        ),
        "The approval is missing or does not match the current gate.",
        "Only a valid response for the current gate and revision grants approval.",
        None,
    ),
    (
        (
            "combined-inputs-stale combined-reviewer-scope-stale "
            "plan-reviewer-confirmation-scope-stale "
            "plan-reviewer-confirmation-target-stale worker-execution-candidate-stale"
        ),
        "The approved inputs have changed.",
        "Consent and evidence bind to exact inputs; earlier approval cannot cover edits.",
        None,
    ),
    (
        (
            "plan-catalog-unavailable reviewer-catalog-stale "
            "reviewer-catalog-producer-unavailable reviewer-catalog-refresh-required "
            "reviewer-catalog-hash-mismatch reviewer-effort-invalid "
            "plan-reviewer-confirmation-record-invalid "
            "plan-reviewer-confirmation-target-invalid "
            "plan-reviewer-confirmation-already-recorded"
        ),
        "The reviewer choice cannot be used as submitted.",
        "Reviewer choices must match the offered catalog and recorded confirmation.",
        None,
    ),
    (
        (
            "plan-semantic-scenarios-invalid acceptance-scenario-invalid "
            "acceptance-scenarios-required spine-schema-invalid "
            "spine-parent-kind-not-allowed"
        ),
        "The submitted planning data is incomplete or invalid.",
        "Planning records must satisfy the supported typed schema and traceability rules.",
        None,
    ),
    (
        "plan-expansion-required plan-phase-invalid plan-phase-order planning-phase-required",
        "The requested phase transition is not available.",
        "Required planning steps and current phase prerequisites must be satisfied.",
        None,
    ),
    (
        (
            "canonical-prerequisite plan-findings-open "
            "plan-publication-evidence-required plan-review-policy-unmet "
            "acceptance-run-required acceptance-review-required "
            "acceptance-attempt-unknown acceptance-review-interrupted "
            "worker-execution-canonical-prerequisite "
            "worker-execution-frozen-semantic-candidate-required"
        ),
        "Required planning, review or execution evidence is missing or held.",
        "Only current candidate-bound evidence satisfies the owning gate.",
        None,
    ),
    (
        (
            "workspace-mismatch workspace-assertion-required "
            "workspace-discovery-unavailable plan-observation-conflict"
        ),
        "Seldon could not safely use the requested workspace or observe its state.",
        (
            "The fixed connection and canonical workspace must agree; unknown state "
            "grants no authority."
        ),
        "ask the user to verify the intended project and its Seldon connection before continuing.",
    ),
    (
        "tool-input-or-operation-error",
        "The tool could not complete the request.",
        "Only supported inputs and operations are accepted; an error grants no retry authority.",
        None,
    ),
]
MESSAGES = {
    code: (what, why, action) for codes, what, why, action in RULES for code in codes.split()
}


def refusal_text(text, workspace, values):
    if "\nWhat happened: " in text:
        return text  # Wire binding may project the same reply a second time.
    body, separator, previous = text.rpartition("Next: ")
    body = (body if separator else text).rstrip("; \n")
    if previous.startswith("Hold:"):
        body += "\n" + previous
    current = {}

    def collect(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, dict | list):
                    collect(item, path + key + ".")
                elif key in {
                    "phase",
                    "pending_gate",
                    "state",
                    "status",
                    "gate",
                    "reasonCode",
                    "maxStarts",
                    "spentAdmissions",
                    "callCap",
                    "providerCallCap",
                    "liveness",
                    "dispatchStatus",
                    "targetedAssessment",
                    "match",
                    "expectedWorkspace",
                    "serverWorkspace",
                }:
                    current[path + key] = item
        elif isinstance(value, list):
            for index, item in enumerate(value):
                collect(item, path + str(index) + ".")

    collect(values)
    code = refusal_code(body, values)
    what, why, action = MESSAGES.get(
        code,
        (
            "The request is held or failed.",
            "The reported error does not authorize bypassing the owning rule.",
            None,
        ),
    )
    if (
        action is None
        and code in MESSAGES
        and previous.startswith(("harness_", "Ask the user", "ask the user"))
    ):
        action = previous
    current.setdefault("unreported values", "unknown; no allowance or approval inferred")
    action = action or (
        f"harness_plan_status expected_workspace={json.dumps(str(workspace))}"
        "; read-only; inspect the reported blocker for the supported next step. "
        "Do not repeat an uncertain mutation; do not inspect Seldon's implementation."
    )
    return (
        f"{body}\nWhat happened: {what}\nWhy: {why}"
        f"\nCurrent values: {json.dumps(current, ensure_ascii=False)}\nNext: {action}"
    )


def refusal_hold(value):
    if not isinstance(value, dict):
        return False
    return (
        any(
            value.get(key)
            in (
                "held",
                "failed",
                "stale",
                "unknown-interrupted",
                "pending",
                "declined",
                "decline",
                "cancel",
                "conditional",
            )
            for key in ("state", "status")
        )
        or bool(
            value.get("holdReasons")
            or value.get("admissionError")
            or str(value.get("dispatchStatus", "")).startswith("plan-failed")
        )
        or any(
            refusal_hold(value.get(key))
            for key in ("data", "result", "terminal", "job", "executionConfirmation")
        )
    )


def refusal_code(text, values):
    coded = re.search(r"(?:^|\n)([a-z]+(?:-[a-z0-9]+)+)(?=:|$)", text)
    inner = re.search(r"tool-input-or-operation-error: ([a-z]+(?:-[a-z0-9]+)+)(?:[:;\s]|$)", text)
    explicit = values.get("code") or values.get("data", {}).get("reasonCode")
    if explicit:
        return explicit
    if inner and inner[1] in MESSAGES:
        return inner[1]
    return coded[1] if coded else "tool-input-or-operation-error"


def refusal_context(server, failed, message, code):
    from codex_harness.core.provider_call_budget import planning_review_budget

    values = {"result": server.result_data, "code": code}
    if failed and server.store and any(word in message for word in ("budget", "cap-exhausted")):
        try:
            values["budget"] = planning_review_budget(server.store)
        except (ValueError, OSError, sqlite3.Error):
            values["budget"] = {"state": "unknown"}
    return values


def compact_refusal(name, reply, workspace, status, data):
    if name == "harness_worker_status" and refusal_hold(data or {}):
        reply["content"][0]["text"] = refusal_text(
            reply["content"][0]["text"], workspace, {"status": status, "data": data}
        )
