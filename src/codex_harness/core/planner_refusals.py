"""One renderer for structured producer refusals; no prose-derived authority."""

TEACHING = (
    "Relay actual user words through harness_chat(text, reply_to?). Display its replies and questions. "
    "Seldon calls its configured models and owns setup, IDs, gates, scheduling and recovery. "
    "Use read-only inspection for status. Chat supplies no planner JSON or transition calls."
)


def render(refusal):
    return f"{refusal['code']}: {refusal['reason']}\nCurrent: {refusal['current']}\nNext: {refusal['nextAction']}"


def reply(error, workspace):
    refusal = dict(code=getattr(error, "code", "operation-error"), reason=str(error),
                   current=dict(workspace=str(workspace)),
                   nextAction=getattr(error, "nextAction", "Read harness_plan_status and supply the named correction through harness_chat"))
    return dict(isError=True, refusalCode=refusal["code"], structuredContent=dict(refusal=refusal),
                content=[dict(type="text", text=render(refusal))])
