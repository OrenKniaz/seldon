import hashlib

from tests.python.core.mcp_planner_support import (
    workspace_wire,
)


def tool(server, name, **arguments):
    return workspace_wire(server, name, expected_workspace=str(server.workspace), **arguments)


def record(server, question, quote="Approve", *, apply_budget=True):
    """A separate typed source-only external-message fixture, not an observed human reply."""
    result = tool(
        server,
        "harness_record_chat_confirmation",
        gate=question.get("gate", "execution"),
        request_id=question["request_id"],
        expected_revision=question.get(
            "plan_revision", question.get("scope", {}).get("publicationRevision")
        ),
        quoted_message=quote,
        quote_sha256=hashlib.sha256(quote.encode()).hexdigest(),
        actor_label="source-only external tester; identity and source unverified",
    )
    return result
