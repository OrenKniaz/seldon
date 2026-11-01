import json
from uuid import uuid4


def protocol(provider, *, text=None):
    """Explicit synthetic main-session telemetry; no executable is launched."""
    session = str(uuid4())
    text = text or json.dumps(
        [{"node_id": "R1", "severity": "note", "finding": "SOURCE ONLY gap", "suggestion": ""}]
    )
    if provider == "codex":
        events = [
            {"type": "thread.started", "thread_id": session},
            {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
            {"type": "turn.completed"},
        ]
        stderr = (
            "Codex initialized with event: SessionConfiguredEvent { "
            f"session_id: SessionId {{ uuid: {session} }}, "
            f"thread_id: ThreadId {{ uuid: {session} }}, "
            'forked_from_id: None, parent_thread_id: None, model: "source-only", '
            "reasoning_effort: Some(High) }"
        )
    else:
        events = [
            {"type": "system", "subtype": "init", "session_id": session, "model": "source-only"},
            {
                "type": "assistant",
                "session_id": session,
                "message": {"model": "source-only", "content": [{"type": "text", "text": text}]},
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": session,
                "permission_denials": [],
                "result": text,
            },
        ]
        stderr = ""
    return 0, "\n".join(json.dumps(event) for event in events), stderr
