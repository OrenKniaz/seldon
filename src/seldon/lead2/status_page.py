"""C20: serve the Fresh Lead pane on loopback for the lifetime of the chat server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock, Thread

from ..store import encode
from .status_read import status


class StatusPages:
    def __init__(self):
        self.runs = {}
        self.server = None
        self.lock = Lock()

    def add(self, project):
        with self.lock:
            if self.server is None:
                self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
                Thread(target=self.server.serve_forever, daemon=True).start()
            key = token_urlsafe(18)
            self.runs[key] = project
            return f"http://127.0.0.1:{self.server.server_port}/{key}/"

    def handler(self):
        html = Path(__file__).with_name("status_page.html").read_bytes()
        runs = self.runs

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                key, _, name = self.path.lstrip("/").partition("/")
                project = runs.get(key)
                if project is None or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    self.send_error(404)
                elif not name and self.path.endswith("/"):
                    self.send_body("text/html", html)
                elif name == "state.json":
                    self.send_body("application/json", encode(status(project)).encode())
                elif name == "favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                else:
                    self.send_error(404)

            def send_body(self, kind, body):
                self.send_response(200)
                self.send_header("Content-Type", f"{kind}; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass  # The MCP transport owns the console.

        return Handler
