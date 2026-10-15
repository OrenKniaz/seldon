"""Loopback-only, GET-only cockpit server."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from codex_harness.core.status_api import snapshot


def create_server(*, workspace=None, runs_root=None, port=7480):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/status":
                body = json.dumps(snapshot(workspace=workspace, runs_root=runs_root)).encode()
                self._reply(200, body, "application/json; charset=utf-8")
            elif self.path in ("/", "/board"):
                self._reply(
                    200,
                    Path(__file__)
                    .with_name("workspace.html" if self.path == "/" else "board.html")
                    .read_bytes(),
                    "text/html; charset=utf-8",
                )
            else:
                self._reply(404, b"Not found", "text/plain")

        def _reject(self):
            self._reply(405, b"Method not allowed", "text/plain")

        def __getattr__(self, name):
            if name.startswith("do_"):
                return self._reject
            raise AttributeError(name)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--runs", required=True)
    parser.add_argument("--port", type=int, default=7480)
    args = parser.parse_args()
    with create_server(workspace=args.workspace, runs_root=args.runs, port=args.port) as server:
        print(f"Read-only board: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
