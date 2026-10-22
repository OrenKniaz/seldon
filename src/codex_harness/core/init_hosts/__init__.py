"""Small host registry; writers produce text without filesystem side effects."""

from . import claude, codex, copilot_vscode

HOSTS = {"codex": codex, "claude": claude, "copilot-vscode": copilot_vscode}
MARKER = "seldon-init v1"


def server_entry(command, root):
    return {
        "command": command,
        "args": ["-I", "-m", "codex_harness.core.seldon_mcp", "--workspace", str(root)],
        "env": {"SELDON_INIT": MARKER},
    }
