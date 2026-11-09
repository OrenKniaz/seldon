"""C4/C23/I6: immutable canonical records and an atomic run cursor in SQLite."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, project):
        self.root = project / ".seldon"
        self.root.mkdir()
        (self.root / "artifacts").mkdir()
        self.db = sqlite3.connect(self.root / "state.sqlite3")
        self.db.executescript("""
            CREATE TABLE run (id INTEGER PRIMARY KEY CHECK(id=1), cursor TEXT NOT NULL);
            CREATE TABLE plan_revision (id INTEGER PRIMARY KEY, parent INTEGER,
                                        digest TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE record (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE artifact (digest TEXT PRIMARY KEY, location TEXT NOT NULL,
                                   producer TEXT NOT NULL, media_type TEXT NOT NULL);
        """)
        self.db.commit()

    @contextmanager
    def transaction(self):
        with self.db:
            yield

    def record(self, kind, body):
        row = self.db.execute("INSERT INTO record(kind,body) VALUES (?,?)", (kind, encode(body)))
        return f"r{row.lastrowid}"

    def read(self, ref):
        row = self.db.execute("SELECT body FROM record WHERE id=?", (int(ref[1:]),)).fetchone()
        if row is None:
            raise ValueError(f"Unknown record: {ref}")
        return json.loads(row[0])

    def records(self, kind=None):
        rows = self.db.execute("SELECT id,kind,body FROM record ORDER BY id").fetchall()
        return [(f"r{i}", k, json.loads(body)) for i, k, body in rows if kind is None or k == kind]

    def cursor(self, value=None):
        if value is not None:
            self.db.execute("INSERT INTO run VALUES(1,?) ON CONFLICT(id) DO UPDATE SET cursor=?",
                            (encode(value), encode(value)))
        row = self.db.execute("SELECT cursor FROM run WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def revision(self, document, parent=None):
        row = self.db.execute("INSERT INTO plan_revision(parent,digest,document) VALUES (?,?,?)",
                              (parent, digest(document), encode(document)))
        return row.lastrowid

    def plan(self, revision):
        row = self.db.execute(
            "SELECT document FROM plan_revision WHERE id=?", (revision,)).fetchone()
        return json.loads(row[0])

    def artifact(self, content, producer, media_type="text/plain"):
        key = hashlib.sha256(content).hexdigest()
        location = f"artifacts/{key}"
        path = self.root / location
        if not path.exists():
            path.write_bytes(content)
        self.db.execute("INSERT OR IGNORE INTO artifact VALUES (?,?,?,?)",
                        (key, location, producer, media_type))
        return key

    # C19 extension: sourced lessons and structured mail are additional record kinds.
