"""Actual public Next consumers over existing source-only planning fixtures.

Review returns/bookkeeping audits and native clients are labelled test inputs,
never live provider/user evidence. No workers, model calls or P1 stores are used.
"""

import json

from tests.python.core import test_planner_replay_gaps

project = test_planner_replay_gaps.project


def wire(server, tool_name, **arguments):
    return server.handle_line(
        json.dumps(
            dict(
                jsonrpc="2.0",
                id=7,
                method="tools/call",
                params=dict(
                    name=tool_name,
                    arguments={"expected_workspace": str(server.workspace), **arguments},
                ),
            )
        )
    )


def result(server, tool_name, **arguments):
    return wire(server, tool_name, **arguments)["result"]
