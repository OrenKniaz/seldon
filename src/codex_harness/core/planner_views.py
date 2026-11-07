"""Read-only review-policy and finding projections; the baton owns continuation."""


def plan_review_policy_contract(store) -> dict:
    """Observe retained policy; structural choices are never planner answers."""
    return {
        "owner": "code",
        "policy": store.plan_review_policy_state(store.manifest_revision()),
        "selection": "Run reviewers come from frozen kickoff preferences or deterministic defaults.",
        "limits": "Retained policy and native closure requirements remain binding. "
        "Preferences grant neither permission nor observed independence.",
        "unavailableProducers": [
            "observed author/reviewer separation",
            "targeted per-member semantic assessment",
            "general accepted-clause witness",
        ],
        "closureContract": "content.findings uses the existing canonical finding writer; "
        "declarations alone never verify repairs.",
    }


def review_finding_members(store, review: dict) -> list[dict]:
    """Exact recorded member details for the existing review detail endpoint."""
    from codex_harness.core.roundtable import findings

    rounds = {row["round_id"] for row in review.get("reviewers", []) if row.get("round_id")}
    return [
        {
            key: row.get(key)
            for key in (
                "id",
                "cluster_id",
                "memberIds",
                "memberCount",
                "node_id",
                "round_id",
                "plan_revision",
                "reviewer",
                "provider",
                "severity",
                "status",
                "state",
                "judgmentIds",
                "finding",
                "suggestion",
                "resolution",
            )
        }
        for row in findings(store, expand=True)
        if row.get("round_id") in rounds
    ]
