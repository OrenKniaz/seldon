"""Installed project activation and canonical MCP entry points."""

import argparse
import sys
from importlib.metadata import version


def mcp_main():
    if sys.argv[1:] == ["--version"]:
        print(f"seldon-mcp {version('seldon')}")
        return
    from codex_harness.core.mcp_planner import main as serve

    serve()


def main(argv=None):
    from codex_harness.core.project_init import run_init

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=version("seldon"))
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Register Seldon in this project only")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--host", choices=["codex", "claude", "copilot-vscode", "all"])
    init.add_argument("--name")
    init.add_argument("--yes", action="store_true")
    init.add_argument(
        "--approval-mode",
        choices=["approve", "prompt"],
        help="Explicit project permission choice for all current/future Seldon tools; "
        "approve skips tool prompts, prompt requests them (default: approve with consent)",
    )
    init.add_argument("--dry-run", action="store_true")
    modes = init.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--remove", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run_init(args)
    except (ValueError, OSError) as error:
        print(f"Seldon init: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
