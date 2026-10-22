"""Claude Code project MCP configuration."""

from .json_config import merge

PATH = ".mcp.json"
GUIDANCE = "Open a Claude Code chat here and approve seldon when prompted."


def render(text, entry, *, remove=False, yes=False):
    return merge(text, "mcpServers", entry, remove=remove, yes=yes)
