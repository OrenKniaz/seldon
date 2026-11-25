"""C20: persist the run registry and read only the requested status pages."""

import hmac
import os
import sqlite3
import sys
from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import parse_qs, urlsplit

from ..store import Store, encode
from .status_home import Home, read_summary, status_template
from .status_read import status

OPEN_PLAN_CACHE = 20


@dataclass
class Projection:
    identity: tuple
    store: Store
    prefix: str
    revision: str = ""
    body: bytes = b""

    def read(self, full):
        with self.store.db:
            self.store.db.execute("BEGIN")
            counter, = self.store.db.execute("PRAGMA data_version").fetchone()
            revision = f"{self.prefix}-{counter}"
            if full and revision != self.revision:
                self.body = encode(status(self.store)).encode()
                self.revision = revision
        if full:
            return "application/json", self.body, revision
        return "text/plain", revision.encode(), revision


class RunPages:
    def __init__(self, db):
        self.db = db
        self.opened = OrderedDict()
        self.plan = status_template("status_page.html")
        self.home = Home(db)

    def register(self, project):
        folder = str(project)
        row = self.db.execute("SELECT id FROM run WHERE folder = ?", (folder,)).fetchone()
        if row:
            return row[0]
        plan = read_summary(project)
        with self.db:
            run = self.db.execute("INSERT INTO run(folder, name, owner) VALUES (?, ?, ?)",
                                  (folder, plan.name, plan.owner)).lastrowid
            self.home.save(run, plan)
        return run

    def plan_state(self, run, project, full):
        info = (project / ".seldon/state.sqlite3").stat()
        identity = (info.st_dev, info.st_ino, info.st_ctime_ns if os.name == "nt" else None)
        projection = self.opened.pop(run, None)
        if projection is None or identity != projection.identity:
            if projection:
                projection.store.close()
            projection = Projection(identity, Store(project, readonly=True), token_urlsafe(9))
        self.opened[run] = projection
        if len(self.opened) > OPEN_PLAN_CACHE:
            self.opened.popitem(last=False)[1].store.close()
        return projection.read(full)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.local_host():
            return self.send_error(404)
        url = urlsplit(self.path)
        if url.path == "/favicon.ico":
            self.send_response(204)
            return self.end_headers()
        key, _, route = url.path.lstrip("/").partition("/")
        if not self.authorized(key):
            return self.send_error(404)
        pages = self.server.pages
        if not route and url.path.endswith("/"):
            page = parse_qs(url.query).get("page", ["1"])[0]
            page = max(int(page), 1) if page.isascii() and page.isdecimal() and len(page) <= 10 else 1
            return self.send_body("text/html", pages.home.render(page))
        run, _, name = route.partition("/")
        if not run.isascii() or not run.isdecimal() or len(run) > 18 or "/" not in route:
            return self.send_error(404)
        row = pages.db.execute("SELECT folder FROM run WHERE id = ?", (run,)).fetchone()
        if row and not name:
            body = pages.plan.replace("<!-- recent plans -->", pages.home.sidebar(int(run)))
            return self.send_body("text/html", body.encode())
        if row and name in ("state.json", "revision"):
            try:
                return self.send_body(*pages.plan_state(int(run), Path(row[0]), full=name == "state.json"))
            except (OSError, sqlite3.Error):
                pass
        self.send_error(404)

    def do_POST(self):
        if self.path != "/register":
            return self.send_error(501)
        if not self.local_host() or not self.authorized(self.headers.get("X-Seldon-Key", "")):
            return self.send_error(404)
        try:
            size = int(self.headers["Content-Length"])
            if not 0 < size <= 131072:
                raise ValueError("Invalid local path length")
            project = Path(os.fsdecode(self.rfile.read(size))).resolve()
            run = self.server.pages.register(project)
        except (KeyError, TypeError, ValueError, OSError, sqlite3.Error):
            return self.send_error(400)
        self.send_body("text/plain", str(run).encode())

    def local_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def authorized(self, key):
        return hmac.compare_digest(key.encode(), self.server.key.encode())

    def send_body(self, kind, body, version=None):
        self.send_response(200)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        if version:
            self.send_header("X-Seldon-Revision", version)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class StatusServer(HTTPServer):
    allow_reuse_address = os.name != "nt"

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(1)
        return connection, address


def serve(registry):
    registry.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(registry)) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS server (port INTEGER NOT NULL, secret TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS run
                (id INTEGER PRIMARY KEY, folder TEXT UNIQUE NOT NULL,
                 name TEXT NOT NULL, owner TEXT NOT NULL);
        """)
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT port, secret FROM server").fetchone()
            try:
                server = StatusServer(("127.0.0.1", row[0] if row else 0), Handler)
            except OSError as error:
                if error.errno not in (48, 98, 10048):  # Concurrent starters contend for the saved port.
                    raise
                return
            if not row:
                row = server.server_port, token_urlsafe(24)
                db.execute("INSERT INTO server VALUES (?, ?)", row)
        with server:
            server.key = row[1]
            server.pages = RunPages(db)
            server.serve_forever()


if __name__ == "__main__":
    serve(Path(sys.argv[1]))
