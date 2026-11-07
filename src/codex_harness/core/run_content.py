"""Semantic payloads use existing canonical writers; never source or transitions."""

from codex_harness.core.planner_nodes import batch_nodes

TEXT = {"type": "string", "minLength": 1}
NODE_SCHEMA = dict(
    type="object",
    properties=dict(
        id=TEXT,
        kind=TEXT,
        title=TEXT,
        text={"type": "string"},
        fields={"type": "object"},
        parents={"type": "array", "items": TEXT},
    ),
    required=["id", "kind", "title", "text", "fields", "parents"],
    additionalProperties=False,
)
CONTENT_SCHEMA = dict(
    type="object",
    properties={
        **{
            k: {"type": "object"} for k in ("intake", "waveMap", "research")
        },
        **{
            k: {"type": "array", "items": {"type": "object"}}
            for k in (
                "scenarios",
                "findings",
                "questions",
                "answers",
                "assumptions",
                "mail",
            )
        },
        "nodes": {"type": "array", "items": NODE_SCHEMA},
        "remove": {"type": "array", "items": TEXT},
        "question": TEXT,
    },
    required=[],
    additionalProperties=False,
)


def apply_content(store, content):
    from codex_harness.core.planner_schema import validate_input
    from codex_harness.core.run_loop import cursor

    validate_input(content, CONTENT_SCHEMA, "content")
    if content.get("nodes"):
        run = cursor(store) or {}
        nodes = [n for n in content["nodes"] if n["id"] != run.get("reviewerNodeId")]
        for node in nodes:
            if node["kind"] == "bead":
                for key in ("provider", "model", "effort"):
                    node["fields"].pop(key, None)
            if node["kind"] == "vision":
                existing = next((v for v in store.list_nodes("vision") if v["id"] == node["id"]), None)
                node["fields"].pop("setup", None)
                if existing and "setup" in existing["fields"]:
                    node["fields"]["setup"] = existing["fields"]["setup"]
        if nodes:
            batch_nodes(store, nodes, lambda n: validate_input(n, NODE_SCHEMA, "node"))
    for identifier in content.get("remove", []):
        if identifier != (cursor(store) or {}).get("reviewerNodeId"):
            store.remove_node(identifier)
    if "intake" in content:
        store.record_intake(content["intake"], store._revision())
    if "waveMap" in content:
        store.record_wave_map(**content["waveMap"], expected_spine_revision=store._revision())
    if "scenarios" in content:
        store.save_acceptance_scenarios(content["scenarios"])
    if "findings" in content:
        store.resolve_findings(content["findings"])
    for row in content.get("questions", []):
        store.ask(**row)
    for row in content.get("answers", []):
        store.answer(row["id"], row["answer"])
    for row in content.get("assumptions", []):
        if "id" in row:
            args = {k: v for k, v in row.items() if k not in {"id", "confirmed"}}
            (store.confirm_assumption if row["confirmed"] else store.reject_assumption)(
                row["id"], **args
            )
        else:
            store.add_assumption(**row)
    for row in content.get("mail", []):
        store._event("run-mail-requested", row)
    if "research" in content:
        store._event("research-proposed", content["research"])


def apply_answer(store, pending, meaning):
    """Meaning readiness uses the existing owner and names its missing fields."""
    apply_content(store, meaning["content"])
    if meaning["option"] == "ready" and pending["purpose"] == "understanding":
        from codex_harness.core.planner_vision import expansion_status
        gaps = expansion_status(store.spine().node(store.spine().ids("vision")[0]))["gaps"]
        if gaps:
            raise ValueError("vision.fields: " + "; ".join(g["path"] + ": " + g["message"] for g in gaps))
