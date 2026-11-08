"""Bound review verdict ingestion and canonical planning finding views."""
from __future__ import annotations

from codex_harness.core.plan_store import PlanStore
from codex_harness.core.run_questions import review_decoder
from codex_harness.core.review_provider import _binding_fields, packet_binding

def review_members(store, judgment, shard):
    from codex_harness.core.spine import scenario_nodes
    known = set(store.spine().ids()) | {"plan"}
    mapping = {key: value for key, value in scenario_nodes(store.spine()).items() if key not in known}
    known |= mapping.keys()
    valid = []
    for item in judgment["meaning"].get("findings", []):
        reported = item["node_id"]
        for owner in mapping.get(reported, [reported if reported in known else "plan"]):
            finding = dict(item, judgmentId=judgment["id"], node_id=owner, outOfShard=owner not in {*shard["nodeIds"], "plan"})
            if reported not in known:
                finding["finding"] = f"[{reported}] {item['finding']}"
            if reported in mapping:
                finding["scenarioId"] = reported
            valid.append(finding)
    if judgment["verdict"] == "negative":
        valid.extend(dict(node_id="plan", severity="major", finding=gap,
            suggestion=judgment["rationale"], classification="defect-in-plan", scope_reason=judgment["rationale"],
            judgmentId=judgment["id"], judgmentGap=True, outOfShard=False) for gap in judgment["gaps"])
    return valid

def _parse_review_return(store, reviewer, shard, response, *, mocked):
    """Consume one bound 1j verdict; output structure alone grants no review credit."""
    record = {key: value for key, value in shard.items() if key != "packet"}
    record.update(exitCode=response[0], rejected=[], invalid=0,
                  isMocked=mocked, didRunProvider=not mocked)
    record.update(packet_binding(response[1], reviewer["provider"]))
    if record["exitCode"] != 0:
        raise ValueError(f"Reviewer exited with code {record['exitCode']}")
    judgment = store.review_judgment(reviewer, shard, prepare=False)
    if not judgment.get("id"):
        raise ValueError("Prepare the bound 1j review before returning a verdict")
    if judgment["disposition"] == "complete":
        from codex_harness.core.planner_schema import judgment_response
        parsed = judgment_response(review_decoder(reviewer["provider"], shard)(response[1], judgment), judgment)
        if any(parsed[key] != judgment[key] for key in parsed):
            raise ValueError("Original review reply differs from its retained judgment")
    else:
        judgment = store.submit_judgment(judgment_id=judgment["id"], response=response[1],
                                         source="review:" + reviewer["name"], review_context=_binding_fields(record),
                                         decoder=review_decoder(reviewer["provider"], shard))
    from codex_harness.core.acceptance_recovery import runtime_identity
    try:
        runtime = runtime_identity(reviewer["provider"], reviewer, response[1], response[2] if len(response) > 2 else "")
    except (ValueError, TypeError, KeyError):
        runtime = None
    record.update(requestedProvider=reviewer["provider"], requestedModel=reviewer.get("model"),
                  observedRuntime=runtime, runtimeVerified=runtime is not None and not mocked)
    record.update(judgmentId=judgment["id"], verdict=judgment["verdict"],
                  rationale=judgment["rationale"], disposition=judgment["disposition"],
                  positiveCredit=judgment["positiveCredit"], owner=None)
    if judgment["disposition"] in {"exact-fix", "steer"}:
        record["rejected"].append(dict(status="pending-judgment", reason=judgment["error"]))
        record["invalid"] = 1
        return record, []
    return record, review_members(store, judgment, shard)
def findings(store: PlanStore, status=None, *, expand=False) -> list[dict]:
    if status is not None and status not in ("open", "pending", "closed", "resolved", "rejected", "deferred"):
        raise ValueError("Unknown finding status")
    rows = [{**row, **_binding_fields(row), "outOfShard": bool(row.get("outOfShard", False))}
            for row in store.finding_state()["members"] if row.get("stage") != "acceptance"]
    groups = {}
    for row in rows:
        groups.setdefault(row["cluster_id"] or row["id"], []).append(row)
    result = []
    for canonical_id, members in groups.items():
        canonical = next(row for row in members if row["id"] == canonical_id)
        representative = next((member for state in ("open", "pending") for member in members
                               if member["state"] == state), canonical)
        for row in members if expand else [representative]:
            if status is not None and row["state" if status in {"open", "pending", "closed"} else "status"] != status:
                continue
            item = {
                **row,
                "memberCount": len(members),
                "memberIds": [member["id"] for member in members],
                "clusteredWith": canonical_id if row["id"] != canonical_id else None,
            }
            if not expand:
                item["outOfPacketTools"] = list(
                    dict.fromkeys(tool for member in members for tool in member["outOfPacketTools"])
                )
                item["packetBound"] = (
                    False
                    if any(member["packetBound"] is False for member in members)
                    else True
                    if all(member["packetBound"] is True for member in members)
                    else None
                )
                item["outOfShard"] = any(member["outOfShard"] for member in members)
            result.append(item)
    return result
