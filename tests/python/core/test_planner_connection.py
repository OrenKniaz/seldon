"""Connection projections consume real planner output, without host-access claims."""

import json


def wire(server, tool, **arguments):
    return server.handle_line(
        json.dumps(
            dict(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params=dict(name=tool, arguments=arguments),
            )
        )
    )
