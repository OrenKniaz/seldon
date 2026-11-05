import time


def finish_review(server, reply):
    data = reply.get("structuredContent", {})
    if reply["isError"] or data.get("kind") != "review":
        return reply
    server.reviews.dispatch_after_flush(server.store)
    deadline = time.time() + 30
    while time.time() < deadline:
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
        time.sleep(0.25)
    raise AssertionError("Public review exceeded 30s wall-clock deadline")
