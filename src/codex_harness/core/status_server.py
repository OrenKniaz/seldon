"""Loopback-only, GET-only cockpit server."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from codex_harness.core.status_api import snapshot


def create_server(*, workspace=None, runs_root=None, port=7480):
    core = Path(__file__).parent
    fixtures = core.parents[2] / "tests" / "browser" / "cockpit" / "fixtures"
    assets = {
        "/": (core / "cockpit.html", "text/html; charset=utf-8"),
        "/cockpit.css": (core / "cockpit.css", "text/css; charset=utf-8"),
        "/cockpit.js": (core / "cockpit.js", "text/javascript; charset=utf-8"),
        "/legacy": (core / "workspace.html", "text/html; charset=utf-8"),
        "/board": (core / "board.html", "text/html; charset=utf-8"),
        "/fixtures/linkshelf.json": (fixtures / "public" / "linkshelf.json", "application/json"),
        "/fixtures/linkshelf-planning.json": (
            fixtures / "public" / "linkshelf-planning.json",
            "application/json",
        ),
        "/fixtures/csv2md.json": (fixtures / "public" / "csv2md.json", "application/json"),
        "/fixtures/synthetic.json": (fixtures / "synthetic" / "stress.json", "application/json"),
    }

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
            else:
                route = urlsplit(self.path).path
                asset = assets.get(route) if route != "/board" or self.path == "/board" else None
                if asset is not None and asset[0].is_file():
                    self._reply(200, asset[0].read_bytes(), asset[1])
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
