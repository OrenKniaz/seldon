"""C4/C23/I6; R11/R14: typed durable cursors and immutable canonical records."""

import hashlib
import json
import os
import sqlite3
import threading
import types
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from functools import lru_cache
from typing import get_args, get_origin, get_type_hints


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class RunRow:
    lineage: str = ""
    state: str = "NEW"
    kickoff: str = ""
    scratch: str = ""
    candidate: str = ""
    waveStart: str = ""
    revision: int | None = None
    vision: str | None = None
    review: str | None = None
    decision: str | None = None
    requestedChange: str | None = None
    A: str | None = None
    B: str | None = None
    checkin: str | None = None
    repairs: int = 0
    failure: str = ""
    publication: str | None = None
    finalCommit: str | None = None
    finalObservations: list[str] = field(default_factory=list)
    demoVerdicts: list[str] = field(default_factory=list)
    productVerdict: str | None = None
    requirementVerdicts: list[str] = field(default_factory=list)
    qualityVerdicts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UnitRow:
    lineage: str = ""
    state: str = "NEW"
    base: str | None = None
    worktree: str | None = None
    T: str | None = None
    P: str | None = None
    thread: str | None = None
    callFolder: str | None = None
    callAttempt: str | None = None
    report: str | None = None
    recoveries: int = 0
    failure: str = ""
    observations: list[str] = field(default_factory=list)
    judgment: str | None = None
    integrated: str | None = None
    waiting: str = ""


@lru_cache(maxsize=None)
def row_hints(model):
    return get_type_hints(model)


def checked(value, model):
    if type(value) is not model:
        raise ValueError(f"Expected {model.__name__}")

    def matches(item, hint):
        origin = get_origin(hint)
        if origin is types.UnionType:
            return any(matches(item, branch) for branch in get_args(hint))
        if origin is list:
            return type(item) is list and all(matches(child, get_args(hint)[0]) for child in item)
        return type(item) is hint

    for name, hint in row_hints(model).items():
        if not matches(getattr(value, name), hint):
            raise ValueError(f"{model.__name__}.{name}: expected {hint}")
    from .states import RUN, UNIT
    if value.state not in (RUN if model is RunRow else UNIT):
        raise ValueError(f"Unknown {model.__name__} state: {value.state}")
    return value


def decoded(body, model):
    value = json.loads(body)
    value.setdefault("lineage", "")
    if set(value) != {f.name for f in fields(model)}:
        raise ValueError(f"Stored {model.__name__} has unknown or missing fields")
    result = model(**value)
    checked(result, model)
    return result


class Store:
    def __init__(self, project):
        self.root = project / ".seldon"
        self.root.mkdir(exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        self._local = threading.local()
        self._lock = threading.RLock()
        self.integration_lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS run (id INTEGER PRIMARY KEY CHECK(id=1), cursor TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plan_revision (id INTEGER PRIMARY KEY, parent INTEGER,
                                                      document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS record
                (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS unit (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recovery (id TEXT PRIMARY KEY, body TEXT NOT NULL);
        """)

    @property
    def db(self):
        if not hasattr(self._local, "db"):
            self._local.db = sqlite3.connect(self.root / "state.sqlite3", isolation_level=None, timeout=30)
            self._local.db.execute("PRAGMA synchronous=FULL")
        return self._local.db

    def close(self):
        if hasattr(self._local, "db"):
            self._local.db.close()
            del self._local.db

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

    def units(self):
        return {ref: decoded(body, UnitRow) for ref, body in
                self.db.execute("SELECT id,body FROM unit ORDER BY rowid")}

    def recovery(self, ref, value=None):
        if value is not None:
            self.db.execute("INSERT INTO recovery VALUES (?,?) "
                            "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                            (ref, encode(value)))
        row = self.db.execute("SELECT body FROM recovery WHERE id=?", (ref,)).fetchone()
        state = json.loads(row[0]) if row else {}
        return state if state and not state.get("resolved") else dict(
            rounds=0, stage=0, question=None, answer=None, attempts=[], seen=[])

    def unit(self, ref, value=None):
        if value is not None:
            self.db.execute("INSERT INTO unit VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                            (ref, encode(asdict(checked(value, UnitRow)))))
        row = self.db.execute("SELECT body FROM unit WHERE id=?", (ref,)).fetchone()
        return decoded(row[0], UnitRow) if row else None

    def cursor(self, value=None):
        if value is not None:
            self.db.execute("INSERT INTO run VALUES(1,?) "
                            "ON CONFLICT(id) DO UPDATE SET cursor=excluded.cursor",
                            (encode(asdict(checked(value, RunRow))),))
        row = self.db.execute("SELECT cursor FROM run WHERE id=1").fetchone()
        return decoded(row[0], RunRow) if row else None

    def record(self, kind, body):
        with self._lock:
            row = self.db.execute("INSERT INTO record(kind,body) VALUES (?,?)", (kind, encode(body)))
            return f"r{row.lastrowid}"

    def read(self, ref):
        row = self.db.execute("SELECT body FROM record WHERE id=?", (int(ref[1:]),)).fetchone()
        if row is None:
            raise ValueError(f"Unknown record: {ref}")
        return json.loads(row[0])

    def records(self, kind=None):
        query = "SELECT id,kind,body FROM record"
        args = ()
        if kind is not None:
            query += " WHERE kind=?"
            args = (kind,)
        return [(f"r{i}", k, json.loads(b)) for i, k, b in self.db.execute(query + " ORDER BY id", args)]

    def revision(self, document, parent=None):
        from . import architecture, judgment
        with self.transaction():
            row = self.db.execute("INSERT INTO plan_revision(parent,document) VALUES (?,?)",
                                  (parent, encode(document)))
            if parent is not None:
                affected = architecture.stale_consumers(self.plan(parent), document) & self.units().keys()
                if affected:
                    cursor = self.cursor()
                    self.record("contract_change", {"revision": row.lastrowid, "parent": parent,
                                                    "consumers": sorted(affected)})
                    for ref in affected - {"guide"}:  # guide_written is independent of contract text.
                        self.unit(ref, replace(self.unit(ref), judgment=None))
                    fields = {}
                    for field in ("requirementVerdicts", "qualityVerdicts"):
                        fields[field] = [r for r in getattr(cursor, field) if not affected.intersection(
                            judgment.owners(document, self.read(r)["kind"], self.read(r)["subject"]))]
                    fields["demoVerdicts"] = [r for r in cursor.demoVerdicts if not affected.intersection(
                        self.read(r)["scopeTrees"])]
                    self.cursor(replace(cursor, **fields, productVerdict=None))
            return row.lastrowid

    def plan(self, revision):
        row = self.db.execute("SELECT document FROM plan_revision WHERE id=?", (revision,)).fetchone()
        return json.loads(row[0])

    def artifact(self, content, producer):
        key = hashlib.sha256(content).hexdigest()
        path = self.root / "artifacts" / key
        with self._lock:
            if not path.exists():
                temporary = path.with_name(key + "-" + uuid.uuid4().hex)
                temporary.write_bytes(content)
                os.replace(temporary, path)
        return str(path)

    def claim_controller(self):
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
