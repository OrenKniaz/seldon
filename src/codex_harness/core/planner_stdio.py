import argparse
import json
import os
import queue
import sys
import threading
import time


def isolated(action, *args):
    try:
        return action(*args)
    except (Exception, SystemExit) as error:
        print(f"planner request failed: {type(error).__name__}", file=sys.stderr)


def frame(response):
    return json.dumps(response, allow_nan=False) if response is not None else ""


def reply(server, line):
    encoded = isolated(lambda: frame(server.handle_line(line)))
    if encoded is not None:
        return encoded
    request = json.loads(line)
    if "id" not in request:
        return ""
    response = {"jsonrpc": "2.0", "id": request["id"]}
    message = "Tool failed; inspect saved state before retrying (completion unconfirmed)."
    if request.get("method") == "tools/call":
        response["result"] = {"isError": True, "content": [{"type": "text", "text": message}]}
    else:
        response["error"] = {"code": -32603, "message": message}
    return frame(response)


def serve(server_type) -> None:
    parser = argparse.ArgumentParser(description="Seldon stdio MCP planner")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    with os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8") as protocol:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())  # Also redirects child output.
        server = server_type(args.workspace)
        try:
            incoming = queue.Queue()
            completed = object()
            server.audits.wake = server.acceptance.wake = lambda: incoming.put(completed)

            def read_lines():
                try:
                    for line in sys.stdin:
                        incoming.put(line)
                finally:
                    incoming.put(None)

            threading.Thread(target=read_lines, daemon=True).start()
            while True:
                if server.store is not None:
                    isolated(server.audits.drain, server.store)
                    isolated(server.acceptance.drain, server.store)
                expired = isolated(lambda: list(map(frame, server.expire_confirmation()))) or []
                for encoded in expired:
                    print(encoded, file=protocol, flush=True)
                pending = server._pending_confirmation
                wait = server.acceptance.wait_timeout()
                if pending:
                    remaining = max(0.01, pending["deadline"] - time.monotonic())
                    wait = min(wait, remaining) if wait is not None else remaining
                try:
                    line = incoming.get(timeout=wait)
                except queue.Empty:
                    continue
                if line is None:
                    break
                if line is completed:
                    continue
                encoded = reply(server, line)
                if encoded:
                    print(encoded, file=protocol, flush=True)
                    if server.store is not None:
                        for owner in (server.audits, server.acceptance, server.reviews):
                            isolated(owner.dispatch_after_flush, server.store)
        finally:
            isolated(server.close)
