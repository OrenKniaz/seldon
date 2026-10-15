from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from codex_harness.core import project_memory
from codex_harness.core.project_memory import (
    MAX_TEXT_CHARS,
    MemoryEntry,
    ProjectMemoryError,
    ProjectMemorySecretError,
    ProjectMemoryStore,
    Provenance,
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


def test_bm25_ranking(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    once = store.add("the cache layer sometimes returns stale values for users")
    thrice = store.add("cache cache cache")
    hits = store.search("cache")
    assert [hit.id for hit in hits] == [thrice.id, once.id]
    first, second = hits[0].score, hits[1].score
    assert first is not None and second is not None
    assert first <= second


def test_status_and_tag_filters(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    store.add("queue worker restarts on deploy", tags=["ops", "queue"])
    confirmed = store.add("queue worker uses sqlite", status="confirmed", tags=["queue"])
    assert [hit.id for hit in store.search("queue", status="confirmed")] == [confirmed.id]
    assert len(store.search("queue", status="unverified")) == 1
    assert len(store.search("queue", tags=["queue"])) == 2
    assert len(store.search("queue", tags=["queue", "OPS"])) == 1
    assert store.search("queue", tags=["missing"]) == []
    with pytest.raises(ProjectMemoryError) as excinfo:
        store.search("queue", status="bogus")
    assert excinfo.value.code == "project-memory-status-invalid"


def test_supersession(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    a = store.add("port is 7000")
    b = store.add("port is 7373", supersedes=a.id)
    assert b.supersedes == a.id
    assert [hit.id for hit in store.search("port")] == [b.id]
    shown = store.search("port", include_superseded=True)
    assert {hit.id for hit in shown} == {a.id, b.id}
    assert next(hit for hit in shown if hit.id == a.id).superseded is True
    with pytest.raises(ProjectMemoryError) as excinfo:
        store.add("port is 9000", supersedes=a.id)
    assert excinfo.value.code == "project-memory-already-superseded"
    with pytest.raises(ProjectMemoryError) as missing:
        store.add("port is 1", supersedes="deadbeef")
    assert missing.value.code == "project-memory-supersedes-missing"
    assert len(store.search("port", include_superseded=True)) == 2


def test_confirm_flow_and_rendering(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    original = store.add(
        "Releases are cut from main",
        tags=["release"],
        provenance=Provenance(author="a", provider="codex", model="gpt-x", run_id="run-1"),
    )
    confirmed = store.confirm(original.id, by="reviewer", provider="claude", model="m1")
    assert confirmed.status == "confirmed"
    assert confirmed.supersedes == original.id
    assert confirmed.text == original.text
    assert confirmed.tags == original.tags
    assert confirmed.provenance.author == "reviewer"
    assert confirmed.provenance.provider == "claude"
    assert confirmed.provenance.run_id == "run-1"
    assert [hit.id for hit in store.search("releases")] == [confirmed.id]
    assert [entry.id for entry in store.confirmed_slice()] == [confirmed.id]
    rendered = store.render_markdown()
    assert rendered.startswith("# Project memory (confirmed facts)")
    assert "Releases are cut from main" in rendered
    assert "claude/m1 reviewer" in rendered
    assert f"id {confirmed.id[:8]}" in rendered
    with pytest.raises(ProjectMemoryError) as again:
        store.confirm(original.id, by="reviewer")
    assert again.value.code == "project-memory-already-superseded"
    with pytest.raises(ProjectMemoryError) as already:
        store.confirm(confirmed.id, by="reviewer")
    assert already.value.code == "project-memory-already-confirmed"
    with pytest.raises(ProjectMemoryError) as missing:
        store.confirm("nope", by="reviewer")
    assert missing.value.code == "project-memory-entry-missing"
    other = store.add("another fact")
    with pytest.raises(ProjectMemoryError) as nobody:
        store.confirm(other.id, by="  ")
    assert nobody.value.code == "project-memory-confirmer-missing"


def test_render_is_bounded_and_empty_message(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    assert "_No confirmed project facts yet._" in store.render_markdown()
    for index in range(40):
        store.add(f"confirmed fact number {index}\nwith a newline", status="confirmed")
    for max_chars in (80, 300, 1000, 4000):
        rendered = store.render_markdown(limit=100, max_chars=max_chars)
        assert len(rendered) <= max_chars
    rendered = store.render_markdown(limit=100, max_chars=1000)
    assert "\nwith" not in rendered
    assert rendered.count("\n- ") >= 3
    sliced = store.confirmed_slice(limit=100, max_chars=100)
    assert sum(len(entry.text) for entry in sliced) <= 100
    assert all(entry.text.startswith("confirmed fact") for entry in sliced)
    assert sliced[0].text.startswith("confirmed fact number 39")


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


def test_linked_database_file_is_refused(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    (tmp_path / "b").mkdir()
    store_b = ProjectMemoryStore(tmp_path / "b")
    store_b.add("project b database entry")
    storage_a = root_a / ".codex-harness"
    storage_a.mkdir(parents=True)
    try:
        os.symlink(store_b.path, storage_a / "project-memory.sqlite3")
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable here: {exc}")

    with pytest.raises(ProjectMemoryError) as excinfo:
        ProjectMemoryStore(root_a).add("must not write through a linked file")
    assert excinfo.value.code == "project-memory-storage-unsafe"
    assert [entry.text for entry in store_b.search("database")] == ["project b database entry"]


def _two_projects(tmp_path: Path) -> tuple[Path, ProjectMemoryStore, bytes]:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    store_b = ProjectMemoryStore(root_b)
    store_b.add("only project b knows this")
    return root_a, store_b, store_b.path.read_bytes()


def test_missing_project_root_is_refused_and_never_created(tmp_path: Path) -> None:
    root = tmp_path / "never-admitted" / "project"
    with pytest.raises(ProjectMemoryError) as excinfo:
        ProjectMemoryStore(root).add("no project yet")
    assert excinfo.value.code == "project-memory-workspace-missing"
    assert not (tmp_path / "never-admitted").exists()


def test_linked_ancestor_route_refuses_before_any_outside_creation(tmp_path: Path) -> None:
    outside = tmp_path / "outside-parent"
    outside.mkdir()
    linked_parent = tmp_path / "linked-parent"
    _link_directory(linked_parent, outside)

    with pytest.raises(ProjectMemoryError) as missing:
        ProjectMemoryStore(linked_parent / "new-root").add("must not create outside")
    assert missing.value.code == "project-memory-workspace-missing"
    assert not (outside / "new-root").exists()

    (outside / "existing-root").mkdir()
    with pytest.raises(ProjectMemoryError) as linked:
        ProjectMemoryStore(linked_parent / "existing-root").add("must not write outside")
    assert linked.value.code == "project-memory-storage-unsafe"
    assert list((outside / "existing-root").iterdir()) == []


def test_storage_swap_at_sqlite_open_is_prevented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_a, store_b, b_before = _two_projects(tmp_path)
    store_a = ProjectMemoryStore(root_a)
    store_a.add("seed project a storage")
    storage_a = root_a / ".codex-harness"
    original_connect = project_memory.sqlite3.connect
    attempts: list[str] = []

    def swap_then_connect(*args: object, **kwargs: object) -> object:
        try:
            os.rename(storage_a, root_a / "admitted-storage-before-swap")
        except OSError as exc:
            attempts.append(type(exc).__name__)
        else:
            attempts.append("renamed")
            _link_directory(storage_a, store_b.path.parent)
        return original_connect(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(project_memory.sqlite3, "connect", swap_then_connect)
    store_a.add("written while the storage is held")
    monkeypatch.setattr(project_memory.sqlite3, "connect", original_connect)

    assert attempts == ["PermissionError"]
    assert store_b.path.read_bytes() == b_before
    assert [entry.text for entry in store_b.search("project")] == ["only project b knows this"]
    assert "written while the storage is held" in [e.text for e in store_a.search("held")]


def test_database_replacement_at_sqlite_open_is_prevented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_a, store_b, b_before = _two_projects(tmp_path)
    store_a = ProjectMemoryStore(root_a)
    store_a.add("seed project a database")
    decoy = tmp_path / "decoy.sqlite3"
    decoy.write_bytes(b_before)
    original_connect = project_memory.sqlite3.connect
    attempts: list[str] = []

    def replace_then_connect(*args: object, **kwargs: object) -> object:
        try:
            os.replace(decoy, store_a.path)
        except OSError as exc:
            attempts.append(type(exc).__name__)
        else:
            attempts.append("replaced")
        return original_connect(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(project_memory.sqlite3, "connect", replace_then_connect)
    store_a.add("written while the database is pinned")
    monkeypatch.setattr(project_memory.sqlite3, "connect", original_connect)

    assert attempts == ["PermissionError"]
    assert store_b.path.read_bytes() == b_before
    assert "written while the database is pinned" in [e.text for e in store_a.search("pinned")]


def test_hard_linked_database_or_sidecar_is_refused(tmp_path: Path) -> None:
    root_a, store_b, b_before = _two_projects(tmp_path)
    storage_a = root_a / ".codex-harness"
    storage_a.mkdir()
    os.link(store_b.path, storage_a / "project-memory.sqlite3")
    with pytest.raises(ProjectMemoryError) as aliased:
        ProjectMemoryStore(root_a).add("must not write through a hard link")
    assert aliased.value.code == "project-memory-storage-unsafe"
    (storage_a / "project-memory.sqlite3").unlink()

    os.link(store_b.path, storage_a / "project-memory.sqlite3-journal")
    with pytest.raises(ProjectMemoryError) as sidecar:
        ProjectMemoryStore(root_a).add("must not journal into another project")
    assert sidecar.value.code == "project-memory-storage-unsafe"
    assert store_b.path.read_bytes() == b_before


def test_only_the_pinned_database_and_durable_journal_exist(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    entry = store.add("journal stays durable and pinned")
    store.confirm(entry.id, by="tester")
    assert sorted(path.name for path in store.path.parent.iterdir()) == [
        "project-memory.lock",
        "project-memory.sqlite3",
        "project-memory.sqlite3-journal",
    ]


def test_wal_files_are_refused_before_any_write(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    store.add("existing fact")
    before = store.path.read_bytes()
    (store.path.parent / "project-memory.sqlite3-wal").write_bytes(b"")
    with pytest.raises(ProjectMemoryError) as excinfo:
        store.add("must not write beside a WAL file")
    assert excinfo.value.code == "project-memory-storage-unsafe"
    assert store.path.read_bytes() == before


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


def test_case_sensitive_secret_in_tag_is_refused_before_lowercasing(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    aws_shaped = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
    with pytest.raises(ProjectMemorySecretError) as excinfo:
        store.add("harmless note", tags=[aws_shaped])
    assert aws_shaped not in str(excinfo.value)
    assert aws_shaped.lower() not in str(excinfo.value)
    assert store.search("harmless") == []
    normal = store.add("tag normalization still works", tags=["  PitFall "])
    assert normal.tags == ("pitfall",)


@pytest.mark.parametrize(("name", "sample"), _secret_samples())
def test_secrets_are_refused_and_not_stored(tmp_path: Path, name: str, sample: str) -> None:
    store = ProjectMemoryStore(tmp_path)
    assert find_secret(sample) is not None, name
    with pytest.raises(ProjectMemorySecretError) as excinfo:
        store.add(f"deploy notes {sample} end")
    assert excinfo.value.code == "project-memory-secret-refused"
    assert sample not in str(excinfo.value)
    assert store.search("deploy notes end") == []


def test_secret_in_tag_is_refused(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    sample = "gh" + "p_" + "C" * 24
    with pytest.raises(ProjectMemorySecretError):
        store.add("harmless text", tags=[sample])
    assert store.search("harmless") == []


def test_secret_in_provenance_or_confirmer_is_refused(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    sample = "gh" + "p_" + "D" * 24
    with pytest.raises(ProjectMemorySecretError):
        store.add("harmless text", provenance=Provenance(commit=sample))
    entry = store.add("another harmless note")
    with pytest.raises(ProjectMemorySecretError):
        store.confirm(entry.id, by=sample)
    assert [hit.id for hit in store.search("harmless note")] == [entry.id]
    assert store.get(entry.id).status == "unverified"


@pytest.mark.parametrize(
    "text",
    ["rotate the token weekly", "password reset flow is broken"],
)
def test_benign_prose_is_accepted(tmp_path: Path, text: str) -> None:
    store = ProjectMemoryStore(tmp_path)
    assert store.add(text).text == text
    assert find_secret(text) is None


def test_text_length_limits(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    assert len(store.add("x" * MAX_TEXT_CHARS).text) == MAX_TEXT_CHARS
    with pytest.raises(ProjectMemoryError) as too_long:
        store.add("x" * (MAX_TEXT_CHARS + 1))
    assert too_long.value.code == "project-memory-text-too-long"
    with pytest.raises(ProjectMemoryError) as empty:
        store.add("   ")
    assert empty.value.code == "project-memory-text-empty"


def test_tag_validation(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    with pytest.raises(ProjectMemoryError) as long_tag:
        store.add("fact", tags=["t" * 65])
    assert long_tag.value.code == "project-memory-tags-invalid"
    with pytest.raises(ProjectMemoryError) as many:
        store.add("fact", tags=[f"t{i}" for i in range(17)])
    assert many.value.code == "project-memory-tags-invalid"
    with pytest.raises(ProjectMemoryError) as bad_status:
        store.add("fact", status="maybe")
    assert bad_status.value.code == "project-memory-status-invalid"
    assert store.add("fact", tags=["", "  ", "a", "A"]).tags == ("a",)


def test_unicode_round_trip(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    text = "הזיכרון המשותף עובד 日本語のメモ 🚀"
    added = store.add(text)
    assert store.get(added.id).text == text
    hits = store.search("הזיכרון")
    assert [hit.id for hit in hits] == [added.id]
    assert hits[0].text == text


def test_two_agents_share_one_store(tmp_path: Path) -> None:
    agent_a = ProjectMemoryStore(tmp_path)
    agent_b = ProjectMemoryStore(tmp_path)
    pitfall = "Windows path separators break the junction check; use pathlib"
    agent_a.add(
        pitfall,
        tags=["pitfall"],
        provenance=Provenance(author="agent-a", provider="codex", model="gpt-x"),
    )
    hits: list[MemoryEntry] = agent_b.search("junction windows")
    assert len(hits) == 1
    assert hits[0].text == pitfall
    assert hits[0].provenance.author == "agent-a"
    assert hits[0].provenance.provider == "codex"
    assert hits[0].provenance.model == "gpt-x"


def _run_cli(tmp_path: Path, *cli_args: str) -> tuple[int, dict[str, object]]:
    src_dir = Path(project_memory.__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-m", "codex_harness", "memory", "project", *cli_args],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(src_dir)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    parsed: object = json.loads(completed.stdout)
    assert isinstance(parsed, dict)
    return completed.returncode, {str(key): value for key, value in parsed.items()}


def test_hot_journal_swap_during_recovery_is_prevented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    store = ProjectMemoryStore(tmp_path)
    kept = store.add("committed fact survives a swap attempt", tags=["durability"])
    confirmed = store.confirm(kept.id, by="tester")
    child = subprocess.run(
        [sys.executable, "-c", _CRASH_CHILD, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(Path(project_memory.__file__).parents[2])},
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 9, child.stderr
    journal = store.path.parent / "project-memory.sqlite3-journal"
    hot_before = journal.read_bytes()
    assert hot_before[:8] != bytes(8)
    original_connect = project_memory.sqlite3.connect
    attempts: list[str] = []

    def swap_journal_then_connect(*args: object, **kwargs: object) -> object:
        try:
            os.rename(journal, journal.with_name("preserved-hot-journal"))
        except OSError as exc:
            attempts.append(type(exc).__name__)
        else:
            attempts.append("renamed")
            journal.write_bytes(b"")
        return original_connect(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(project_memory.sqlite3, "connect", swap_journal_then_connect)
    current = store.search("committed fact survives")
    monkeypatch.setattr(project_memory.sqlite3, "connect", original_connect)

    assert attempts == ["PermissionError"]
    assert [entry.id for entry in current] == [confirmed.id]
    assert store.search("zebraquartz") == []
    assert journal.read_bytes()[:8] == bytes(8)  # rollback zeroed, never deleted
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)


_CONFIRM_CHILD = """
import sys
from codex_harness.core.project_memory import ProjectMemoryStore
try:
    ProjectMemoryStore(sys.argv[1]).confirm(sys.argv[2], by=sys.argv[3])
    print("ok")
except Exception as exc:
    print("refused", getattr(exc, "code", type(exc).__name__))
"""


def test_concurrent_confirmations_serialize_without_deadlock(tmp_path: Path) -> None:
    store = ProjectMemoryStore(tmp_path)
    entry = store.add("concurrent confirm target")
    script = _CONFIRM_CHILD
    env = {**os.environ, "PYTHONPATH": str(Path(project_memory.__file__).parents[2])}
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), entry.id, f"agent-{index}"],
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        for index in range(4)
    ]
    outcomes = sorted(process.communicate(timeout=120)[0].strip() for process in processes)
    assert outcomes.count("ok") == 1
    assert all(line.startswith("refused project-memory-") for line in outcomes if line != "ok")
    current = store.search("concurrent confirm target")
    assert [item.status for item in current] == ["confirmed"]
    assert len(store.search("concurrent confirm target", include_superseded=True)) == 2
