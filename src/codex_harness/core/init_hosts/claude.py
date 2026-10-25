"""Claude Code project MCP configuration."""

import json

from .json_config import _unique, merge

PATH = ".mcp.json"
GUIDANCE = (
    "Open a Claude Code chat here and trust the folder; saved permissions need host verification."
)
PERMISSIONS_PATH = ".claude/settings.local.json"
SERVER_RULE = "mcp__seldon__*"


def render_permissions(text, mode=None):
    """Merge one server policy; narrower ask/deny rules retain their precedence."""
    data = json.loads(text, object_pairs_hook=_unique) if text.strip() else {}
    if not isinstance(data, dict) or not isinstance(data.get("permissions", {}), dict):
        raise ValueError("Expected Claude permissions object")
    permissions = data.setdefault("permissions", {})
    for key in ("allow", "ask", "deny"):
        values = permissions.get(key, [])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError(f"Expected Claude permissions.{key} string array")
    enabled = data.get("enabledMcpjsonServers", [])
    if not isinstance(enabled, list) or any(not isinstance(v, str) for v in enabled):
        raise ValueError("Expected Claude enabledMcpjsonServers string array")
    mode = mode or ("prompt" if SERVER_RULE in permissions.get("ask", []) else "approve")
    selected, other = ("allow", "ask") if mode == "approve" else ("ask", "allow")
    if SERVER_RULE in permissions.get(other, []):
        permissions[other] = [v for v in permissions[other] if v != SERVER_RULE]
    if SERVER_RULE not in permissions.get(selected, []):
        permissions.setdefault(selected, []).append(SERVER_RULE)
    if mode == "approve" and "seldon" not in enabled:
        data["enabledMcpjsonServers"] = [*enabled, "seldon"]
    if text.strip() and data == json.loads(text):
        return text, mode, False
    granted = mode == "approve" and (
        SERVER_RULE
        not in (json.loads(text).get("permissions", {}).get("allow", []) if text.strip() else [])
        or "seldon" not in enabled
    )
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode, granted


def render(text, entry, *, remove=False, yes=False):
    return merge(text, "mcpServers", entry, remove=remove, yes=yes)
