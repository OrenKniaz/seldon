"""Schema recovery at the real MCP/store boundary; no provider execution."""

import json


def invoke(server, tool_name, **arguments):
    # Same-project fixtures explicitly name their intended workspace on the wire.
    arguments = {"expected_workspace": str(server.workspace), **arguments}
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        )
    )["result"]
