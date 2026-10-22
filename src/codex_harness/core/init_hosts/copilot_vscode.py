"""VS Code workspace MCP configuration (experimental host journey)."""

from .json_config import merge

PATH = ".vscode/mcp.json"
GUIDANCE = "Open this folder in VS Code; review/approve seldon in MCP configuration."


def render(text, entry, *, remove=False, yes=False):
    return merge(text, "servers", {"type": "stdio", **entry}, remove=remove, yes=yes)
