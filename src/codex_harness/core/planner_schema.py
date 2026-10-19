"""Read-only help for existing planner contracts; validation stays in MCP/Spine."""

from copy import deepcopy

from codex_harness.core.spine import ALLOWED_PARENTS

FIELD_RULES = {
    "bead": (
        "fields.ownedFiles: nonempty array of nonblank strings; "
        "fields.proofModule: nonblank string; fields.dependsOn and fields.lessons: "
        "arrays of unique existing bead/decision IDs respectively (empty allowed). "
        "Optional fields.provider: nonblank string. Dependencies must be acyclic."
    ),
    "test": "fields.module: nonblank string; fields.level: unit or e2e.",
}


def _node(kind, node_id, parents, fields=None, text=None):
    return {
        "id": node_id,
        "kind": kind,
        "title": "Reject malformed parser input",
        "text": text or "Parser boundary: reject malformed input before saving any data.",
        "parents": parents,
        "fields": fields or {},
    }


def node_example(kind):
    """Small planning examples, never observations or evidence of acceptance."""
    vision = _node("vision", "V1", [])
    feature = _node("feature", "F1", ["V1"])
    requirement = _node(
        "requirement",
        "R1",
        ["F1"],
        text="Parser boundary: malformed input must be rejected without saving data. "
        "Planned unit verification: submit truncated JSON and assert no record is saved. "
        "Credible bug: parser saves partial data before raising. Existing happy-path "
        "proof misses this rejection and rollback behavior; add the negative case.",
    )
    examples = {
        "vision": ([], vision),
        "feature": ([vision], feature),
        "story": ([vision, feature], _node("story", "S1", ["F1"])),
        "requirement": ([vision, feature], requirement),
        "acceptance": ([vision], _node("acceptance", "A1", ["V1"])),
        "decision": ([vision], _node("decision", "D1", ["V1"])),
        "design": ([vision, feature, requirement], _node("design", "G1", ["R1"])),
        "bead": (
            [vision, feature, requirement],
            _node(
                "bead",
                "B1",
                ["R1"],
                {
                    "ownedFiles": ["src/parser.py"],
                    "proofModule": "test_parser",
                    "dependsOn": [],
                    "lessons": [],
                },
            ),
        ),
        "test": (
            [vision, feature, requirement],
            _node(
                "test",
                "T1",
                ["R1"],
                {
                    "module": "test_parser",
                    "level": "unit",
                },
            ),
        ),
    }
    return examples[kind]


def schema_help(tools, *, tool="harness_upsert_node", kind=None):
    """Return actual transport schema plus advisory help for canonical node fields."""
    contract = next((item for item in tools if item["name"] == tool), None)
    if contract is None:
        raise ValueError(f"Unknown tool: {tool}; choose one of {sorted(t['name'] for t in tools)}")
    if kind is not None and tool != "harness_upsert_node":
        raise ValueError("kind applies only to harness_upsert_node")
    result = {
        "tool": tool,
        "inputSchema": deepcopy(contract["inputSchema"]),
        "tools": [item["name"] for item in tools],
        "nodeKinds": list(ALLOWED_PARENTS),
        "authority": "MCP input validation and PlanStore/Spine remain authoritative; "
        "examples are planned content, not approval, proof or phase completion.",
    }
    if tool == "harness_upsert_node":
        kind = kind or "requirement"
        if kind not in ALLOWED_PARENTS:
            raise ValueError(f"Unknown node kind: {kind}; choose one of {list(ALLOWED_PARENTS)}")
        prerequisites, example = node_example(kind)
        result.update(
            kind=kind,
            allowedParentKinds=sorted(ALLOWED_PARENTS[kind]),
            fieldRules=FIELD_RULES.get(
                kind, "No additional kind-specific fields required by Spine."
            ),
            fieldsPolicy="fields is an open object of finite JSON values; additional keys allowed. "
            "Later quality, compilation and phase gates still apply.",
            parentRule="Parent IDs must exist, be unique and acyclic, with allowed kinds.",
            prerequisiteNodes=prerequisites,
            example={"node": example},
            exampleUse="In a new started plan, upsert prerequisiteNodes in order, then example. "
            "For an existing plan, use its actual IDs and preserve accepted intent.",
        )
    return result


def node_error_help(arguments):
    """Explain rejected input without replacing or relaxing the owning validator."""
    import json

    node = arguments.get("node") if isinstance(arguments, dict) else None
    kind = node.get("kind") if isinstance(node, dict) else None
    if not isinstance(kind, str) or kind not in ALLOWED_PARENTS:
        return "Inspect harness_schema for required node keys, types and valid kinds."
    _, example = node_example(kind)
    return (
        "Rule: "
        + FIELD_RULES.get(kind, "Use the required node keys and types in harness_schema.")
        + " Parent IDs must already exist with allowed kinds; fields remains open JSON."
        + "\nExample: "
        + json.dumps({"node": example}, ensure_ascii=False)
        + f"\nUse harness_schema kind={kind} for prerequisites and the full input contract; "
        "adapt IDs to the current plan and retain accepted intent."
    )
