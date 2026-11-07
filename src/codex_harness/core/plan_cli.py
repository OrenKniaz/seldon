"""CLI commands enter the same controller as MCP; status is a read-only baton."""

import json
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.planner_cli_input import parser, read_json


def main(argv=None):
    args = vars(parser().parse_args(argv))
    workspace, command = args.pop("workspace"), args.pop("command")
    args.pop("json")
    for key in ("answer", "content", "user_words", "role_tuples"):
        if args.get(key) is not None:
            args[key] = read_json(args[key])
    names = dict(
        start="plan_start",
        answer="loop_answer",
        steer="steer",
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
        print(json.dumps(reply.get("structuredContent", reply), ensure_ascii=False))
        return 2 if reply["isError"] else 0
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
