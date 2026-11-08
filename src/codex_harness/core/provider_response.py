"""Shared native final-response selection; earlier tool text never earns credit."""

import json

def _response(output: str, provider: str, *, structured=False) -> str:
    """Select final provider text; never mine earlier tool/event payloads."""
    final = None
    has_events = False
    completed = False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or "type" not in event:
            continue
        has_events = True
        if structured and provider == "codex":
            if event["type"] in {"turn.failed", "error"}:
                raise ValueError("Codex semantic turn failed")
            completed |= event["type"] == "turn.completed"
        if provider == "codex" and event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                final = item.get("text")
        elif provider == "claude" and event.get("type") == "result":
            if event.get("is_error"):
                raise ValueError(
                    "Claude returned an error result: " + json.dumps(event, ensure_ascii=False)
                )
            final = json.dumps(event["structured_output"], ensure_ascii=False) if structured and "structured_output" in event else event.get("result") if not structured else None
        elif provider == "copilot" and event.get("type") == "assistant.message":
            final = event.get("data", {}).get("content")
    if has_events:
        if structured and provider == "codex" and not completed:
            raise ValueError("Codex semantic turn has no terminal completion")
        if not isinstance(final, str):
            raise ValueError("Provider stream has no final review text")
        return final
    if structured:
        raise ValueError("Provider stream has no final structured answer")
    return output

