"""CLI commands enter the same controller as MCP; status is a read-only baton."""

from codex_harness.core.record_io import write_stdout_json
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.planner_cli_input import parser


def main(argv=None):
    args = vars(parser().parse_args(argv))
    workspace, command = args.pop("workspace"), args.pop("command")
    args.pop("json")
    names = dict(
        chat="chat",
        status="plan_status",
        schema="schema",
        nodes="nodes",
        show="show_node",
        mail="mail_inbox",
    )
    server = PlannerServer(workspace)
    try:
        reply = server.call_tool(
            "harness_" + names[command], {k: v for k, v in args.items() if v is not None}
        )
        write_stdout_json(reply.get("structuredContent", reply))
        return 2 if reply["isError"] else 0
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
