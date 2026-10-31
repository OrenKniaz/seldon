"""Drive asynchronous public review completion in existing synchronous consumers."""

import time


def finish_review(server, reply):
    data = reply.get("structuredContent", {})
    if reply["isError"] or data.get("kind") != "review":
        return reply
    server.reviews.dispatch_after_flush(server.store)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        observed = server.call_tool("harness_accept_status", {"id": data["attemptId"]})
        terminal = observed["structuredContent"]
        if terminal["state"] not in {"queued", "running"}:
            if terminal.get("result"):
                result = terminal["result"]
                observed["structuredContent"] = result
            elif terminal.get("approval"):
                observed["structuredContent"] = terminal["approval"]
            elif terminal.get("failure"):
                observed["structuredContent"] = terminal["failure"]
            else:
                observed.pop("structuredContent", None)
            return observed
        time.sleep(0.01)
    raise AssertionError("Public review did not terminate")


def completed_call(server, name, arguments):
    reply = server.call_tool(name, arguments)
    return finish_review(server, reply) if name == "harness_accept_review" else reply
