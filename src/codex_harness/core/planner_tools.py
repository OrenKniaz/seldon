"""One chat mutation and canonical read-only inspection."""

from codex_harness.core.spine import ALLOWED_PARENTS

PHASE_DIAGRAMS = {
    "vision-lock": "user-journey",
    "features": "feature-map",
    "tech-stack": "decision-map",
    "plan": "bead-dag",
    "publish": "trace",
}
STRING = {"type": "string", "minLength": 1}


def _object(properties: dict, required=None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def _tool(name: str, description: str, properties: dict, required=None) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": _object(properties, required),
    }


TOOLS = [
    _tool(
        "harness_chat",
        "Relay the user's actual words and display Seldon's replies. The server owns all decisions, IDs, setup and transitions.",
        {"text": STRING, "reply_to": STRING}, ["text"],
    ),
    _tool(
        "harness_judgment",
        "Read current bound semantic credit; candidate judgments require current candidate.",
        {"judgment_id": STRING, "candidate": STRING},
        ["judgment_id"],
    ),
    _tool(
        "harness_schema",
        "Read existing tool input contracts and actionable canonical node examples; "
        "does not create a plan, approve gates or establish proof.",
        {
            "tool": STRING,
            "kind": {"type": "string", "enum": list(ALLOWED_PARENTS)},
            "include_teaching": {"type": "boolean"},
        },
        [],
    ),
    _tool(
        "harness_preflight",
        "Read-only prerequisites; never launches providers or claims execution readiness.",
        {
        },
        [],
    ),
    _tool(
        "harness_waves",
        "Read full draft outcomes and outstanding obligations; no execution evidence is bound.",
        {},
    ),
    _tool("harness_review", "Read a recorded review and its shard evidence.", {"id": STRING}),
    _tool(
        "harness_review_policy",
        "Inspect the existing bound plan-review policy contract read-only. "
        "Does not adopt/refresh policy, start reviews or establish verified closure.",
        {},
    ),
    _tool(
        "harness_packet_view",
        "Read worker packet size and semantic suite IDs/target bindings from the current "
        "compiled or published plan. "
        "Use the default summary + semanticSuite first; request include_packet=true only when "
        "those cannot answer a concrete review/debug question about exact packet text. "
        "Reviewers receive their canonical packets independently.",
        {
            "id": STRING,
            "provider": {"type": "string", "enum": ["codex", "claude", "copilot"]},
            "include_packet": {"type": "boolean", "default": False},
        },
        ["id"],
    ),
    _tool(
        "harness_show_node",
        "Read a node and its linked protected test functions or named body.",
        {
            "id": STRING,
            "body": {"type": "boolean"},
            "test": STRING,
            "include_source": {"type": "boolean"},
        },
        ["id"],
    ),
    _tool(
        "harness_nodes",
        "Read a one-line listing of current nodes, optionally by kind.",
        {"kind": {"type": "string", "enum": list(ALLOWED_PARENTS)}},
        [],
    ),
    _tool(
        "harness_mail_inbox",
        "Read mail (defaults to planner's unacknowledged inbox).",
        {"agent": STRING, "unacked_only": {"type": "boolean"}},
        [],
    ),
    _tool(
        "harness_plan_status",
        "Show canonical gates and a labelled vision summary; full=true returns complete status.",
        {"full": {"type": "boolean"}, "include_detail": {"type": "boolean"}},
        [],
    ),
    _tool(
        "harness_diagram",
        "Generate Mermaid and a text fallback from the store.",
        {"kind": {"type": "string", "enum": list(PHASE_DIAGRAMS.values())}},
    ),
    _tool(
        "harness_impact",
        "Show impact since a manifest revision or file; default: publication or last compilation.",
        {"since": STRING},
        [],
    ),
]
