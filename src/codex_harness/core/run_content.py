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
            k: {"type": "object"} for k in ("intake", "catalog", "reviewers", "waveMap", "research")
        },
        **{
            k: {"type": "array", "items": {"type": "object"}}
            for k in (
                "reviewPolicy",
                "scenarios",
                "findings",
                "questions",
                "answers",
                "assumptions",
                "mail",
                "workers",
                "roleTuples",
            )
        },
        "nodes": {"type": "array", "items": NODE_SCHEMA},
        "remove": {"type": "array", "items": TEXT},
        "max_parallel": {"type": "integer"},
        "timeout": {"type": "number"},
        "question": TEXT,
    },
    required=[],
    additionalProperties=False,
)


def apply_content(store, content):
    from codex_harness.core.planner_schema import validate_input
    from codex_harness.core.planner_inspection import validate_setup

    validate_input(content, CONTENT_SCHEMA, "content")
    if content.get("nodes"):
        for node in content["nodes"]:
            validate_setup(node["fields"].get("setup", {}), store.workspace)
        batch_nodes(store, content["nodes"], lambda n: validate_input(n, NODE_SCHEMA, "node"))
    for identifier in content.get("remove", []):
        store.remove_node(identifier)
    if "intake" in content:
        store.record_intake(content["intake"], store._revision())
    if "catalog" in content:
        store.record_reviewer_catalog(content["catalog"], store._revision())
    if "reviewers" in content:
        store.record_reviewer_selection(content["reviewers"], store._revision())
    if "reviewPolicy" in content:
        store.record_plan_review_policy(content["reviewPolicy"], store.manifest_revision())
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


def apply_options(run, content):
    for key in ("workers", "max_parallel", "timeout", "roleTuples"):
        if key in content:
            run[key] = content[key]
