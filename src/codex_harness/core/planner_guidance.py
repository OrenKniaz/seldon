"""Small public CLI/MCP targets and incremental planning guidance; no state writes."""

# These are operations named by this helper's Next guidance, not a parallel router.
NEXT_TARGETS = {
    "schema": ("schema", "harness_schema"),
    "node": ("node", "harness_upsert_node"),
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
    gaps = {item["gap"] for item in spine.coverage_gaps()}
    if not features:
        stage, kind = "features", "feature"
    elif not requirements:
        stage, kind = "requirements", "requirement"
    elif "requirement-without-test" in gaps:
        stage, kind = "tests", "test"
    elif "requirement-without-bead" in gaps:
        stage, kind = "bead", "bead"
    else:
        stage, kind = "ready", None
    return {
        "stage": stage,
        "kind": kind,
        "status": (
            f"Plan turn: {stage} · {len(features)} features · {len(requirements)} requirements · "
            f"{len(tests)} tests · {len(beads)} beads"
        ),
        "schemaTarget": None
        if kind is None
        else {"cli": f"schema --kind {kind}", "mcp": f"harness_schema kind={kind}"},
        "writeTarget": None
        if kind is None
        else {
            "cli": "node --json @feature-nodes.jsonl",
            "mcp": "harness_upsert_node node=<one schema-valid node>",
        },
        "guidance": (
            "Work one feature at a time: features → requirements → tests → bead. "
            "Inspect the schema at first use; finish each turn with this one-line status. "
            "CLI node accepts a requirements+tests JSONL batch in one atomic write; "
            "MCP currently accepts one node per call. These are planning records, "
            "not proof that the product works."
        ),
    }
