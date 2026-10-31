from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from codex_harness.core import project_memory
from codex_harness.core.project_memory import (
    ProjectMemoryError,
    ProjectMemorySecretError,
    ProjectMemoryStore,
    find_secret,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="project memory holds Windows workspace leases; other hosts fail closed",
)


def _secret_samples() -> list[tuple[str, str]]:
    """Fake credentials assembled at runtime so no secret-shaped literal sits in this file."""
    return [
        ("private-key", "-----" + "BEGIN RSA PRIV" + "ATE KEY" + "-----"),
        ("sk-key", "s" + "k-" + "a1" * 12),
        ("sk-ant-key", "s" + "k-ant-" + "b2" * 12),
        ("github-ghp", "gh" + "p_" + "A" * 24),
        ("github-pat", "github" + "_pat_" + "B" * 24),
        ("aws", "AK" + "IA" + "ABCDEFGHIJKLMNOP"),
        ("slack", "xo" + "xb-" + "1234567890-abcdef"),
        ("google", "AI" + "za" + "x" * 35),
        ("bearer", "Bear" + "er " + "q" * 24),
        ("jwt", "ey" + "J" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12),
        ("assign-password", "pass" + "word = " + "hunter2" * 2),
        ("assign-api-key", "api_" + "key: " + "abc123" * 3),
    ]


def test_add_search_round_trip(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    added = store.add("The build uses uv for dependency management", tags=["Build", " build "])
    assert added.tags == ("build",)
    assert added.status == "unverified"
    hits = store.search("dependency uv")
    assert [hit.id for hit in hits] == [added.id]
    assert hits[0].score is not None
    assert store.get(added.id).text == added.text
    payload = hits[0].to_payload()
    assert payload["tags"] == ["build"]
    assert payload["superseded"] is False


def test_workspace_isolation_and_db_location(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    store_a = ProjectMemoryStore(root_a)
    store_b = ProjectMemoryStore(root_b)
    store_a.add("alpha secretless fact")
    assert store_b.search("alpha") == []
    assert store_a.path == root_a / ".codex-harness" / "project-memory.sqlite3"
    assert store_a.path.is_file()
    assert len(store_a.search("alpha")) == 1


def _link_directory(link: Path, target: Path) -> None:
    """Create a directory junction on Windows, a symlink elsewhere."""
    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip(f"cannot create a junction here: {completed.stderr.strip()}")
    else:
        os.symlink(target, link, target_is_directory=True)


def test_linked_storage_directory_is_refused_before_any_write(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    store_b = ProjectMemoryStore(root_b)
    store_b.add("only project b knows this")
    b_db_before = store_b.path.read_bytes()
    _link_directory(root_a / ".codex-harness", root_b / ".codex-harness")

    store_a = ProjectMemoryStore(root_a)
    with pytest.raises(ProjectMemoryError) as excinfo:
        store_a.add("must never reach project b")
    assert excinfo.value.code == "project-memory-storage-unsafe"
    with pytest.raises(ProjectMemoryError):
        store_a.search("project")

    assert store_b.path.read_bytes() == b_db_before
    assert [entry.text for entry in store_b.search("project")] == ["only project b knows this"]


_CRASH_CHILD = """
import os, sys
from codex_harness.core.project_memory import ProjectMemoryStore
store = ProjectMemoryStore(sys.argv[1])
with store._connection() as conn:
    conn.execute("PRAGMA cache_size=1")
    conn.execute("BEGIN IMMEDIATE")
    for index in range(4000):
        entry_id = "crash-%d" % index
        conn.execute(
            "INSERT INTO entries (id, text, tags_json, status, created_at) "
            "VALUES (?, ?, '[]', 'unverified', '2026-10-11T00:00:00+00:00')",
            (entry_id, "zebraquartz uncommitted payload " + "x" * 200),
        )
        conn.execute(
            "INSERT INTO entries_fts (entry_id, text, tags) VALUES (?, ?, '')",
            (entry_id, "zebraquartz uncommitted payload"),
        )
    os._exit(9)
"""


def test_crash_mid_write_recovers_committed_facts_consistently(tmp_path: Path) -> None:
    import sqlite3

    store = ProjectMemoryStore(tmp_path)
    kept = store.add("committed fact survives a crash", tags=["durability"])
    confirmed = store.confirm(kept.id, by="tester")
    db_size_before = store.path.stat().st_size
    child = subprocess.run(
        [sys.executable, "-c", _CRASH_CHILD, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(Path(project_memory.__file__).parents[2])},
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 9, child.stderr
    assert store.path.stat().st_size > db_size_before  # pages reached the file
    journal = store.path.parent / "project-memory.sqlite3-journal"
    assert journal.read_bytes()[:8] != bytes(8)  # a hot journal was left behind

    current = store.search("committed fact survives")
    assert [entry.id for entry in current] == [confirmed.id]
    assert current[0].status == "confirmed"
    assert store.search("zebraquartz") == []
    history = store.search("committed fact survives", include_superseded=True)
    assert {entry.id for entry in history} == {kept.id, confirmed.id}
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        indexed = conn.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0]
    assert entries == indexed == 2


@pytest.mark.parametrize(("name", "sample"), _secret_samples())
def test_secrets_are_refused_and_not_stored(tmp_path: Path, name: str, sample: str) -> None:
    store = ProjectMemoryStore(tmp_path)
    assert find_secret(sample) is not None, name
    with pytest.raises(ProjectMemorySecretError) as excinfo:
        store.add(f"deploy notes {sample} end")
    assert excinfo.value.code == "project-memory-secret-refused"
    assert sample not in str(excinfo.value)
    assert store.search("deploy notes end") == []


_CONFIRM_CHILD = """
import sys
from codex_harness.core.project_memory import ProjectMemoryStore
try:
    ProjectMemoryStore(sys.argv[1]).confirm(sys.argv[2], by=sys.argv[3])
    print("ok")
except Exception as exc:
    print("refused", getattr(exc, "code", type(exc).__name__))
"""
