"""C4/C23/I6: immutable canonical records and an atomic run cursor in SQLite."""

import hashlib
import json
import os
import uuid
import sqlite3
import threading
from contextlib import contextmanager


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, project):
        self.root = project / ".seldon"
        self.root.mkdir(exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        self._local = threading.local()
        self._lock = threading.RLock()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS run (id INTEGER PRIMARY KEY CHECK(id=1), cursor TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plan_revision (id INTEGER PRIMARY KEY, parent INTEGER,
                                        digest TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS record (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifact (digest TEXT PRIMARY KEY, location TEXT NOT NULL,
                                   producer TEXT NOT NULL, media_type TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS unit (id TEXT PRIMARY KEY, body TEXT NOT NULL);
        """)
        self.db.commit()

    @contextmanager
    def transaction(self):
        with self._lock:
            nested = self.db.in_transaction
            if not nested:
                self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                if not nested:
                    self.db.commit()
            except BaseException:
                if not nested:
                    self.db.rollback()
                raise

    @property
    def db(self):
        if not hasattr(self._local, "db"):
            self._local.db = sqlite3.connect(self.root / "state.sqlite3", isolation_level=None, timeout=30)
        return self._local.db

    def units(self):
        return {ref: json.loads(body) for ref, body in self.db.execute("SELECT id,body FROM unit ORDER BY rowid")}

    def unit(self, ref, body):
        self.db.execute("INSERT INTO unit VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                        (ref, encode(body)))

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
        with self._lock:
            if not path.exists():
                temporary = path.with_name(key + "-" + uuid.uuid4().hex)
                temporary.write_bytes(content)
                os.replace(temporary, path)
            self.db.execute("INSERT OR IGNORE INTO artifact VALUES (?,?,?,?)",
                            (key, location, producer, media_type))
        return key

    def claim_controller(self):
        """C23/I6: reopening cannot race a surviving controller."""
        lease = (self.root / "controller.lock").open("a+b")
        if lease.tell() == 0:
            lease.write(b"0")
            lease.flush()
        lease.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lease.close()
            raise ValueError("Another controller owns this run; wait for it to exit") from None
        return lease
