"""Read-only review-policy and finding projections; the baton owns continuation."""

POLICY_ADOPTION_WARNING = (
    "No supported verified finding-closure producer is available. Adopting this optional "
    "fail-closed policy can leave quality and publication held when findings arise, even after "
    "author repair, dispositions or a clean rereview. Ordinary policy-free work is unchanged. "
    "Inspect the policy contract and its limits before explicit adoption."
)


def plan_review_policy_slots_schema() -> dict:
    """The same explicit role input for CLI policy and native adoption."""
    return {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "provider", "required"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "required": {"type": "boolean"},
                "provider": {"type": "string", "enum": ["codex", "claude", "copilot"]},
            },
        },
    }


def plan_review_policy_contract(store) -> dict:
    """Shared contract for explicit optional prospective policy declaration."""
    string = {"type": "string", "minLength": 1}
    revision = store.manifest_revision()
    example = dict(
        schema="seldon/plan-review-policy-v1",
        stage="plan",
        expectedManifestRevision=revision,
        slots=[dict(name="reviewer", provider="codex", required=True)],
    )
    existing = [
        row["payload"]
        for row in store._plan_review_policies()
        if row["payload"]["manifestRevision"] == revision
    ]
    if existing:
        example["slots"] = existing[-1]["slots"]
    return {
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["schema", "stage", "expectedManifestRevision", "slots"],
            "properties": {
                "schema": {"type": "string", "enum": ["seldon/plan-review-policy-v1"]},
                "stage": {"type": "string", "enum": ["plan"]},
                "expectedManifestRevision": string,
                "slots": plan_review_policy_slots_schema(),
            },
        },
        "example": example,
        "exampleFiles": {
            "policy.json": example,
            "reviewer.json": {
                "name": example["slots"][0]["name"],
                "provider": example["slots"][0]["provider"],
            },
        },
        "exampleContent": {"reviewPolicy": example["slots"]},
        "adoptionWarning": POLICY_ADOPTION_WARNING,
        "adoption": "Explicit content.reviewPolicy in a typed answer or steering amendment only, "
        "before attempts on this revision. "
        "Ordinary policy-free roundtables retain legacy behavior, including latest-success "
        "selection; automatic adoption is not implemented.",
        "exampleUse": "Supply content.reviewPolicy with your reviewer names/providers through "
        "harness_loop_answer or harness_steer. "
        "At least one required slot; distinct names; roles fixed before attempts. "
        "This illustrative role choice grants no authorization or model selection.",
        "limits": "Plan stage only. Existing counts, caps, native approval and final acceptance "
        "unchanged. Optional metadata never waives existing quality FAIL thresholds. "
        "Model/effort and independence are not certified by reviewer/provider roles.",
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
