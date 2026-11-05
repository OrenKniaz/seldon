import time


def drain_acceptance(owner, store, timeout=20):
    deadline = time.time() + timeout
    while owner.thread and owner.thread.is_alive() and time.time() < deadline:
        owner.thread.join(timeout=0.01)
        owner.drain(store)
    assert not owner.thread or not owner.thread.is_alive(), f"Acceptance exceeded {timeout}s wall-clock deadline"
    owner.drain(store)


def finish_public_acceptance(server, admitted):
    """Drive the same serial flush/completion hooks as stdio for direct-call tests."""
    assert not admitted["isError"], admitted
    server.acceptance.dispatch_after_flush(server.store)
    drain_acceptance(server.acceptance, server.store)
    return server.call_tool(
        "harness_accept_status", {"id": admitted["structuredContent"]["attemptId"]}
    )
