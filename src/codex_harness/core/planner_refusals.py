"""One renderer for structured producer refusals; no prose-derived authority."""

TEACHING = (
    "Relay actual user words through harness_chat(text, reply_to?). Display its replies and questions. "
    "Seldon calls its configured models and owns setup, IDs, gates, scheduling and recovery. "
    "Use read-only inspection for status. Chat supplies no planner JSON or transition calls."
)


def render(refusal):
    return f"{refusal['code']}: {refusal['reason']}\nCurrent: {refusal['current']}\nNext: {refusal['nextAction']}"
