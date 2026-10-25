"""VS Code workspace MCP configuration (experimental host journey)."""

from .json_config import merge

PATH = ".vscode/mcp.json"
GUIDANCE = (
    "Manual approval: run Chat: Manage Tool Approval; use the Seldon server-group "
    "checkbox to approve, or leave it unchecked for prompts. No scoped autoapproval "
    "setting written; effective permissions UNVERIFIED."
)


def render(text, entry, *, remove=False, yes=False):
    return merge(text, "servers", {"type": "stdio", **entry}, remove=remove, yes=yes)
