"""Small public CLI/MCP targets and incremental planning guidance; no state writes."""

# These are operations named by this helper's Next guidance, not a parallel router.
NEXT_TARGETS = {
    "schema": ("schema", "harness_schema"),
    "node": ("node", "harness_upsert_node"),
    "batch": ("node", "harness_upsert_nodes"),
    "status": ("status", "harness_plan_status"),
    "advance": ("advance", "harness_advance"),
    "compile": ("compile", "harness_compile"),
    "scenarios": ("accept-scenarios", "harness_accept_scenarios"),
}


def planning_turn(spine) -> dict:
    """Choose the next small authoring turn from stored traceability, not call caps."""
    features = spine.ids("feature")
    requirements = spine.ids("requirement")
    tests = spine.ids("test")
    beads = spine.ids("bead")
    gaps = spine.coverage_gaps()
    stage, kind = ("ready", None) if features else ("features", "feature")
    feature_id, requirement_ids = None, []
    for feature in features:
        local = [key for key in requirements if feature in spine.ancestors(key)]
        local_gaps = {item["gap"] for item in gaps if item["id"] in local}
        if not local:
            stage, kind = "requirements", "requirement"
        elif "requirement-without-test" in local_gaps:
            stage, kind = "tests", "test"
        elif "requirement-without-bead" in local_gaps:
            stage, kind = "bead", "bead"
        else:
            continue
        feature_id, requirement_ids = feature, local
        break
    focus = (
        f"Focus {feature_id}; existing requirement IDs: {', '.join(requirement_ids) or 'none'}. "
        if feature_id
        else ""
    )
    return {
        "stage": stage,
        "kind": kind,
        "featureId": feature_id,
        "requirementIds": requirement_ids,
        "status": (
            f"Plan turn: {stage} · {len(features)} features · {len(requirements)} requirements · "
            f"{len(tests)} tests · {len(beads)} beads"
            + (f" · focus {feature_id}" if feature_id else "")
        ),
        "schemaTarget": None
        if kind is None
        else {"cli": f"schema --kind {kind}", "mcp": f"harness_schema kind={kind}"},
        "writeTarget": None
        if kind is None
        else {
            "cli": "node --json @feature-nodes.jsonl",
            "mcp": "harness_upsert_nodes nodes=<one feature's requirements and tests>",
        },
        "guidance": (
            focus + "Work one feature at a time: features → requirements → tests → bead. "
            "This focus is advisory, not a phase gate or delivery proof. "
            "At first use inspect harness_schema tool=harness_upsert_nodes "
            "(CLI schema --tool harness_upsert_nodes) and each node kind's schema. "
            "CLI node accepts a requirements+tests JSONL batch in one atomic write; "
            "MCP harness_upsert_nodes uses the same writer. These are planning records, "
            "not proof that the product works."
        ),
    }
