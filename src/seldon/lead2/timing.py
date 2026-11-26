"""C20/P11: project recorded Lead phase time and native usage."""

PHASES = {
    "LEAD2-ALIGN": "planning_pre_spec", "LEAD2-STACK": "planning_pre_spec",
    "LEAD2-SPEC": "planning_spec", "REVIEW": "planning_review",
    "WORK": "build", "LEAD2-CHUNK": "build", "LEAD2-STEER": "build",
    "LEAD2-WALK": "walk", "LEAD2-WALK-STEER": "walk",
}
USAGE = ("fresh_input_tokens", "cached_input_tokens", "output_tokens")


def time_and_cost(store):
    phases = dict.fromkeys(PHASES.values())
    phases = {key: {"seconds": 0.0, "rounds": 0, "usageMissing": 0, **dict.fromkeys(USAGE, 0)}
              for key in phases}
    metrics = {row["attempt"]: row for _, _, row in store.records("lead2_call_metric")}
    active, started = None, None
    review_rounds = 0
    for ref, _, row in store.records("attempt"):
        phase = PHASES.get(row["operation"])
        if not phase:
            continue
        entry = phases[phase]
        metric = metrics.get(ref, {})
        if row["operation"] == "REVIEW" and not row.get("retryOf"):
            review_rounds += 1
        active = None if metric else phase
        started = row.get("startedAt") if active else None
        entry["rounds"] += 1
        entry["seconds"] += metric.get("elapsedSeconds") or 0
        if usage := metric.get("usage"):
            for key in USAGE:
                entry[key] += usage[key]
        elif metric:
            entry["usageMissing"] += 1
    offered = {ref: row["offeredAt"] for ref, _, row in store.records("decision_request") if "offeredAt" in row}
    waited = 0.0
    for _, _, row in store.records("user_words"):
        if "receivedAt" in row and row.get("request") in offered:
            waited += max(0.0, row["receivedAt"] - offered.pop(row["request"]))
    cursor = store.cursor()
    if cursor.failure or cursor.decision:
        active = None
    return {"phases": phases, "active": active, "activeStartedAt": started if active else None, "ownerWaitSeconds": waited, "reviewRounds": review_rounds}
