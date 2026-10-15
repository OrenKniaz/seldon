"""Append-only per-project shared memory store backed by SQLite FTS5.

Any agent (any provider) can add, search, confirm and render project facts.
Rows are never edited or deleted; confirmation and correction append a new
entry that supersedes the old one. Credential-looking text is refused.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import astuple, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from codex_harness.core.workspace_admission import (
    WorkspaceAdmissionError,
    acquire_workspace_lease,
)

SCHEMA = "codex-harness/project-memory-v1"
MAX_TEXT_CHARS = 2000
MAX_TAG_CHARS = 64
MAX_TAGS = 16
SCHEMA_VERSION = 1
STATUSES = ("unverified", "confirmed")
DB_RELATIVE_PATH = Path(".codex-harness") / "project-memory.sqlite3"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
LOCK_FILE_NAME = "project-memory.lock"
OPERATION_LOCK_TIMEOUT_SECONDS = 30.0
PROJECT_MEMORY_PROMPT_LIMIT = 10
PROJECT_MEMORY_PROMPT_MAX_CHARS = 1500

_SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("sk-api-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("github-fine-grained-token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("aws-access-key-id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack-token", re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    (
        "jwt",
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
    (
        "credential-assignment",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token"
            r"|auth[_-]?token|token|client[_-]?secret)\s*[:=]\s*\S+"
        ),
    ),
)

_ENTRY_COLUMNS = (
    "e.id, e.text, e.tags_json, e.status, e.author, e.provider, e.model, e.run_id, "
    "e.bead_id, e.turn_id, e.commit_sha, e.created_at, e.supersedes, "
    "EXISTS(SELECT 1 FROM entries s WHERE s.supersedes = e.id)"
)


class ProjectMemoryError(ValueError):
    """Typed project-memory failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProjectMemorySecretError(ProjectMemoryError):
    """Raised when text looks like a credential and is refused."""


def project_memory_prompt_part(workspace_root: str | Path) -> str | None:
    """Read bounded confirmed facts; absent storage is optional, failures propagate."""
    store = ProjectMemoryStore(workspace_root)
    if not Path(workspace_root).is_dir():
        raise ProjectMemoryError(
            "project-memory-workspace-missing", "project memory workspace is missing"
        )
    try:
        store.path.lstat()
    except FileNotFoundError:
        return None
    entries = store.confirmed_slice(
        limit=PROJECT_MEMORY_PROMPT_LIMIT, max_chars=PROJECT_MEMORY_PROMPT_MAX_CHARS
    )
    facts = [" ".join(entry.text.split()) for entry in entries]
    facts = [fact for fact in facts if fact]
    if not facts:
        return None
    return "projectMemory=confirmed_constraints_follow_them:" + " | ".join(facts)


@dataclass(frozen=True)
class Provenance:
    author: str | None = None
    provider: str | None = None
    model: str | None = None
    run_id: str | None = None
    bead_id: str | None = None
    turn_id: str | None = None
    commit: str | None = None


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    text: str
    tags: tuple[str, ...]
    status: str
    provenance: Provenance = field(default_factory=Provenance)
    created_at: str = ""
    supersedes: str | None = None
    superseded: bool = False
    score: float | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "text": self.text,
            "tags": list(self.tags),
            "status": self.status,
            "provenance": {
                "author": self.provenance.author,
                "provider": self.provenance.provider,
                "model": self.provenance.model,
                "runId": self.provenance.run_id,
                "beadId": self.provenance.bead_id,
                "turnId": self.provenance.turn_id,
                "commit": self.provenance.commit,
            },
            "createdAt": self.created_at,
            "supersedes": self.supersedes,
            "superseded": self.superseded,
            "score": self.score,
        }


_NO_PROVENANCE = Provenance()


def find_secret(text: str) -> str | None:
    """Return the NAME of the first credential rule matching text, never the secret."""
    for name, pattern in _SECRET_RULES:
        if pattern.search(text):
            return name
    return None


def _is_linked(path: Path) -> bool:
    """True for a symlink or any Windows reparse point (junctions included)."""
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(path_stat.st_mode) or bool(
        int(getattr(path_stat, "st_file_attributes", 0)) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _lock_byte(fd: int) -> None:
    if sys.platform != "win32":
        raise OSError("project memory operation lock requires Windows")
    import msvcrt

    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)


def _release_operation_lock(fd: int) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def _link_count(path: Path) -> int:
    try:
        return int(path.lstat().st_nlink)
    except FileNotFoundError:
        return 0


def _refuse_secrets(values: Sequence[str]) -> None:
    for value in values:
        rule = find_secret(value)
        if rule is not None:
            raise ProjectMemorySecretError(
                "project-memory-secret-refused",
                f"refused: text looks like it contains a credential ({rule}); "
                "never store secrets in project memory",
            )


def _normalize_tags(tags: Sequence[str] | None) -> tuple[str, ...]:
    raw_tags = [raw for raw in tags or ()]
    # Check the caller's exact values: lowercasing can hide case-sensitive key shapes.
    _refuse_secrets(raw_tags)
    normalized: list[str] = []
    for raw in raw_tags:
        tag = raw.strip().lower()
        if tag and tag not in normalized:
            normalized.append(tag)
    if len(normalized) > MAX_TAGS:
        raise ProjectMemoryError(
            "project-memory-tags-invalid", f"at most {MAX_TAGS} tags are allowed"
        )
    for tag in normalized:
        if len(tag) > MAX_TAG_CHARS:
            raise ProjectMemoryError(
                "project-memory-tags-invalid", f"tags are limited to {MAX_TAG_CHARS} characters"
            )
    return tuple(normalized)


def _validate_status(status: str) -> str:
    if status not in STATUSES:
        raise ProjectMemoryError(
            "project-memory-status-invalid", f"status must be one of: {', '.join(STATUSES)}"
        )
    return status


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _req_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _row_to_entry(row: Sequence[object], score: float | None = None) -> MemoryEntry:
    loaded: object = json.loads(_req_str(row[2]) or "[]")
    tags = tuple(str(tag) for tag in loaded) if isinstance(loaded, list) else ()
    return MemoryEntry(
        id=_req_str(row[0]),
        text=_req_str(row[1]),
        tags=tags,
        status=_req_str(row[3]),
        provenance=Provenance(
            author=_opt_str(row[4]),
            provider=_opt_str(row[5]),
            model=_opt_str(row[6]),
            run_id=_opt_str(row[7]),
            bead_id=_opt_str(row[8]),
            turn_id=_opt_str(row[9]),
            commit=_opt_str(row[10]),
        ),
        created_at=_req_str(row[11]),
        supersedes=_opt_str(row[12]),
        superseded=bool(row[13]),
        score=score,
    )


class ProjectMemoryStore:
    """Per-project append-only memory; one SQLite file under the workspace."""

    def __init__(self, workspace_root: str | os.PathLike[str]) -> None:
        self._root = Path(os.path.abspath(workspace_root))
        self._path = self._root / DB_RELATIVE_PATH

    @property
    def path(self) -> Path:
        return self._path

    def _unsafe(self, detail: str) -> ProjectMemoryError:
        return ProjectMemoryError(
            "project-memory-storage-unsafe",
            f"refused: project memory storage is linked, aliased or moved ({detail})",
        )

    def _pin_leaf(self, path: Path) -> int:
        """Open (creating exclusively if absent) and hold one SQLite file.

        The descriptor is opened without delete sharing (CRT default on Windows),
        so the file cannot be removed, renamed or replaced while held. The path
        must name exactly the held file, must not be a link, and must have a
        single hard link. Existing content (including a hot journal) is kept.
        """
        binary = getattr(os, "O_BINARY", 0)
        try:
            if path.exists() or path.is_symlink():
                if _is_linked(path):
                    raise self._unsafe(f"{path.name} is a link")
                fd = os.open(path, os.O_RDWR | binary)
            else:
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | binary, 0o600)
        except FileExistsError as exc:
            raise self._unsafe(f"{path.name} appeared during creation") from exc
        try:
            held = os.fstat(fd)
            named = os.lstat(path)
            if (
                _is_linked(path)
                or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino)
                or held.st_nlink != 1
            ):
                raise self._unsafe(f"{path.name} does not name one unlinked held file")
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _acquire_operation_lock(self) -> int:
        """Serialize operations on this project's store (one connection at a time).

        Exclusive SQLite locking keeps the journal pinned through crash
        recovery but lets two connections deadlock on retained shared locks, so
        each operation first takes a byte lock on a dedicated, pinned lock file
        that SQLite never opens.
        """
        fd = self._pin_leaf(self._path.parent / LOCK_FILE_NAME)
        deadline = time.monotonic() + OPERATION_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                _lock_byte(fd)
                return fd
            except OSError as exc:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise ProjectMemoryError(
                        "project-memory-busy",
                        "another project memory operation is still running",
                    ) from exc
                time.sleep(0.05)

    def _pin_database_files(self) -> tuple[int, int]:
        """Hold the database and its durable rollback journal; refuse WAL files."""
        db = self._path
        for sidecar in (f"{db.name}-wal", f"{db.name}-shm"):
            if (db.parent / sidecar).exists() or (db.parent / sidecar).is_symlink():
                raise self._unsafe(f"unexpected {sidecar}")
        db_fd = self._pin_leaf(db)
        try:
            os.lseek(db_fd, 18, os.SEEK_SET)
            header = os.read(db_fd, 2)
            if header == bytes((2, 2)):
                raise self._unsafe("database is in WAL mode")
            journal_fd = self._pin_leaf(db.parent / f"{db.name}-journal")
        except BaseException:
            os.close(db_fd)
            raise
        return db_fd, journal_fd

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """One operation under held project, storage and database authority.

        Windows only: the canonical workspace leases (non-delete-shared
        handles) pin the existing project root and the storage directory for
        the whole connection. The database file and its durable rollback
        journal (PERSIST mode, never deleted) are both held open, so SQLite can
        only read and write those two pinned files, including during crash
        recovery (exclusive locking zeroes, never deletes, a hot journal);
        WAL files are refused.
        Memory never creates project roots; non-Windows hosts fail closed.
        """
        if not self._root.is_dir():
            raise ProjectMemoryError(
                "project-memory-workspace-missing",
                f"project memory needs an existing project directory: {self._root}",
            )
        storage = self._path.parent
        try:
            with acquire_workspace_lease(str(self._root)) as root_lease:
                if not (storage.exists() or storage.is_symlink()):
                    root_lease.verify()
                    storage.mkdir()
                with acquire_workspace_lease(str(storage)) as storage_lease:
                    lock_fd = self._acquire_operation_lock()
                    try:
                        db_fd, journal_fd = self._pin_database_files()
                    except BaseException:
                        _release_operation_lock(lock_fd)
                        raise
                    try:
                        with closing(
                            sqlite3.connect(self._path, timeout=30, isolation_level=None)
                        ) as conn:
                            conn.execute("PRAGMA busy_timeout=30000")
                            # Exclusive locking is set before the first read: SQLite
                            # then finishes any hot-journal rollback by zeroing the
                            # journal header instead of deleting it, so the held
                            # journal stays pinned through crash recovery too.
                            conn.execute("PRAGMA locking_mode=EXCLUSIVE")
                            # Durable rollback journal kept in place (never deleted),
                            # so the held journal file is the one SQLite uses.
                            mode = conn.execute("PRAGMA journal_mode=PERSIST").fetchone()
                            if not mode or str(mode[0]).lower() != "persist":
                                raise self._unsafe("durable journal mode unavailable")
                            conn.execute("PRAGMA synchronous=FULL")
                            self._ensure_schema(conn)
                            yield conn
                        root_lease.verify()
                        storage_lease.verify()
                    finally:
                        os.close(journal_fd)
                        os.close(db_fd)
                        _release_operation_lock(lock_fd)
        except WorkspaceAdmissionError as exc:
            raise self._unsafe(str(exc)) from exc

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5("
                "entry_id UNINDEXED, text, tags, tokenize='unicode61')"
            )
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "fts5" in message or "no such module" in message:
                raise ProjectMemoryError(
                    "project-memory-fts5-unavailable",
                    "SQLite FTS5 is unavailable in this Python build; project memory needs it",
                ) from exc
            raise
        conn.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            "id TEXT PRIMARY KEY, text TEXT NOT NULL, tags_json TEXT NOT NULL, "
            "status TEXT NOT NULL, author TEXT, provider TEXT, model TEXT, run_id TEXT, "
            "bead_id TEXT, turn_id TEXT, commit_sha TEXT, created_at TEXT NOT NULL, "
            "supersedes TEXT UNIQUE)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS schema (version INTEGER NOT NULL)")
        conn.execute(
            "INSERT INTO schema (version) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM schema)",
            (SCHEMA_VERSION,),
        )

    @staticmethod
    def _fetch(conn: sqlite3.Connection, entry_id: str) -> MemoryEntry | None:
        rows: list[tuple[object, ...]] = conn.execute(
            f"SELECT {_ENTRY_COLUMNS} FROM entries e WHERE e.id = ?", (entry_id,)
        ).fetchall()
        return _row_to_entry(rows[0]) if rows else None

    def _append(
        self,
        text: str,
        *,
        tags: tuple[str, ...],
        status: str,
        provenance: Provenance,
        supersedes: str | None,
        require_unconfirmed_original: bool = False,
    ) -> MemoryEntry:
        # Every write path (add, confirm) passes here: refuse before any write.
        _refuse_secrets([text, *tags, *(value for value in astuple(provenance) if value)])
        entry_id = uuid.uuid4().hex
        created_at = datetime.now(UTC).isoformat(timespec="microseconds")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if supersedes is not None:
                    original = self._fetch(conn, supersedes)
                    if original is None:
                        raise ProjectMemoryError(
                            "project-memory-supersedes-missing"
                            if not require_unconfirmed_original
                            else "project-memory-entry-missing",
                            f"entry {supersedes} does not exist",
                        )
                    if original.superseded:
                        raise ProjectMemoryError(
                            "project-memory-already-superseded",
                            f"entry {supersedes} is already superseded",
                        )
                    if require_unconfirmed_original and original.status == "confirmed":
                        raise ProjectMemoryError(
                            "project-memory-already-confirmed",
                            f"entry {supersedes} is already confirmed",
                        )
                conn.execute(
                    "INSERT INTO entries (id, text, tags_json, status, author, provider, model, "
                    "run_id, bead_id, turn_id, commit_sha, created_at, supersedes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        entry_id,
                        text,
                        json.dumps(list(tags), ensure_ascii=False),
                        status,
                        provenance.author,
                        provenance.provider,
                        provenance.model,
                        provenance.run_id,
                        provenance.bead_id,
                        provenance.turn_id,
                        provenance.commit,
                        created_at,
                        supersedes,
                    ),
                )
                conn.execute(
                    "INSERT INTO entries_fts (entry_id, text, tags) VALUES (?, ?, ?)",
                    (entry_id, text, " ".join(tags)),
                )
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                if supersedes is not None:
                    raise ProjectMemoryError(
                        "project-memory-already-superseded",
                        f"entry {supersedes} is already superseded",
                    ) from exc
                raise
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            created = self._fetch(conn, entry_id)
        if created is None:  # pragma: no cover - insert just committed
            raise ProjectMemoryError("project-memory-entry-missing", f"entry {entry_id} vanished")
        return created

    def add(
        self,
        text: str,
        *,
        tags: Sequence[str] = (),
        status: str = "unverified",
        provenance: Provenance = _NO_PROVENANCE,
        supersedes: str | None = None,
    ) -> MemoryEntry:
        clean = text.strip()
        if not clean:
            raise ProjectMemoryError("project-memory-text-empty", "text must not be empty")
        if len(clean) > MAX_TEXT_CHARS:
            raise ProjectMemoryError(
                "project-memory-text-too-long", f"text is limited to {MAX_TEXT_CHARS} characters"
            )
        _validate_status(status)
        normalized = _normalize_tags(tags)
        return self._append(
            clean,
            tags=normalized,
            status=status,
            provenance=provenance,
            supersedes=supersedes,
        )

    def get(self, entry_id: str) -> MemoryEntry:
        with self._connection() as conn:
            entry = self._fetch(conn, entry_id)
        if entry is None:
            raise ProjectMemoryError(
                "project-memory-entry-missing", f"entry {entry_id} does not exist"
            )
        return entry

    def confirm(
        self,
        entry_id: str,
        *,
        by: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> MemoryEntry:
        confirmer = by.strip()
        if not confirmer:
            raise ProjectMemoryError(
                "project-memory-confirmer-missing", "confirm requires a non-empty confirmer name"
            )
        original = self.get(entry_id)
        if original.superseded:
            raise ProjectMemoryError(
                "project-memory-already-superseded", f"entry {entry_id} is already superseded"
            )
        if original.status == "confirmed":
            raise ProjectMemoryError(
                "project-memory-already-confirmed", f"entry {entry_id} is already confirmed"
            )
        base = original.provenance
        return self._append(
            original.text,
            tags=original.tags,
            status="confirmed",
            provenance=Provenance(
                author=confirmer,
                provider=provider,
                model=model,
                run_id=base.run_id,
                bead_id=base.bead_id,
                turn_id=base.turn_id,
                commit=base.commit,
            ),
            supersedes=entry_id,
            require_unconfirmed_original=True,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        status: str | None = None,
        tags: Sequence[str] | None = None,
        include_superseded: bool = False,
    ) -> list[MemoryEntry]:
        if status is not None:
            _validate_status(status)
        required_tags = _normalize_tags(tags)
        tokens = re.findall(r"\w+", query)
        if not tokens:
            return []
        match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        bounded = max(1, min(100, limit))
        sql = (
            f"SELECT {_ENTRY_COLUMNS}, bm25(entries_fts) AS score "
            "FROM entries_fts JOIN entries e ON e.id = entries_fts.entry_id "
            "WHERE entries_fts MATCH ?"
        )
        params: list[str | int] = [match]
        if status is not None:
            sql += " AND e.status = ?"
            params.append(status)
        if not include_superseded:
            sql += " AND NOT EXISTS (SELECT 1 FROM entries s WHERE s.supersedes = e.id)"
        sql += " ORDER BY score ASC, e.rowid DESC"
        if not required_tags:
            sql += " LIMIT ?"
            params.append(bounded)
        with self._connection() as conn:
            rows: list[tuple[object, ...]] = conn.execute(sql, params).fetchall()
        hits: list[MemoryEntry] = []
        for row in rows:
            raw_score = row[14]
            score = float(raw_score) if isinstance(raw_score, int | float) else None
            entry = _row_to_entry(row, score)
            if all(tag in entry.tags for tag in required_tags):
                hits.append(entry)
        return hits[:bounded]

    def confirmed_slice(self, *, limit: int = 20, max_chars: int = 4000) -> list[MemoryEntry]:
        bounded = max(1, min(100, limit))
        with self._connection() as conn:
            rows: list[tuple[object, ...]] = conn.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM entries e WHERE e.status = 'confirmed' "
                "AND NOT EXISTS (SELECT 1 FROM entries s WHERE s.supersedes = e.id) "
                "ORDER BY e.rowid DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        selected: list[MemoryEntry] = []
        used = 0
        for row in rows:
            entry = _row_to_entry(row)
            if used + len(entry.text) > max_chars:
                break
            selected.append(entry)
            used += len(entry.text)
        return selected

    def render_markdown(self, *, limit: int = 20, max_chars: int = 4000) -> str:
        header = "# Project memory (confirmed facts)"
        lines = [header, ""]
        length = len(header) + 1
        count = 0
        for entry in self.confirmed_slice(limit=limit, max_chars=max_chars):
            bullet = _render_bullet(entry)
            if length + 1 + len(bullet) > max_chars:
                break
            lines.append(bullet)
            length += 1 + len(bullet)
            count += 1
        if count == 0:
            lines.append("_No confirmed project facts yet._")
        return "\n".join(lines)[:max_chars]


def _render_bullet(entry: MemoryEntry) -> str:
    text = " ".join(entry.text.split())
    prov = entry.provenance
    model_ref = "/".join(part for part in (prov.provider, prov.model) if part)
    who = " ".join(part for part in (model_ref, prov.author) if part) or "unknown"
    tag_part = f"tags: {', '.join(entry.tags)}; " if entry.tags else ""
    meta = f"{tag_part}by {who}, {entry.created_at[:10]}; id {entry.id[:8]}"
    return f"- {text} _({meta})_"
