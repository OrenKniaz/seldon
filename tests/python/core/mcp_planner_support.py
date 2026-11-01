import json


def workspace_wire(server, tool_name, **arguments):
    """Actual unscoped wire; never fill missing caller intent from the server."""
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 901,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        )
    )["result"]


def rpc(server, method, params=None, request_id=1):
    # Existing same-project fixtures explicitly target their constructed project.
    # Routing counterexamples use workspace_wire instead, with no implicit intent.
    if (
        method == "tools/call"
        and isinstance(params, dict)
        and isinstance(params.get("arguments", {}), dict)
    ):
        params = {
            **params,
            "arguments": {
                "expected_workspace": str(server.workspace),
                **params.get("arguments", {}),
            },
        }
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
    )
