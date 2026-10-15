"""Real concurrent atomic replacement; no provider or security-setting changes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from codex_harness.core import dispatch
from codex_harness.core.record_io import open_record_read, read_record_json


def test_atomic_publication_without_reader(tmp_path):
    path = tmp_path / "run.json"
    dispatch._write_record(path, {"revision": 1})
    dispatch._write_record(path, {"revision": 2})
    assert read_record_json(path) == {"revision": 2}


@pytest.mark.parametrize("relative", ["request.json", "evidence/run.json", "run.json"])
def test_supported_reader_serializes_cross_process_publication(tmp_path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    dispatch._write_record(path, {"revision": 1})
    script = (
        "import sys; from codex_harness.core.record_io import write_record_json; "
        "print('ready',flush=True); write_record_json(sys.argv[1],{'revision':2}); "
        "print('published',flush=True)"
    )
    child = None
    try:
        with open_record_read(path) as reader:
            child = subprocess.Popen(
                [sys.executable, "-B", "-c", script, str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert child.stdout.readline().strip() == "ready"
            if os.name == "nt":
                with pytest.raises(subprocess.TimeoutExpired):
                    child.wait(timeout=0.2)
            assert json.loads(reader.read()) == {"revision": 1}
        stdout, stderr = child.communicate(timeout=7)
        assert child.returncode == 0, stderr
        assert stdout.strip() == "published"
        assert read_record_json(path) == {"revision": 2}
    finally:
        if child:
            child.wait(timeout=7)


def test_missing_record_open_propagates_without_creating_file(tmp_path):
    path = tmp_path / "absent.json"
    with pytest.raises(FileNotFoundError):
        with open_record_read(path):
            pytest.fail("Missing record must not open")
    assert not path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows bounded sharing wait")
def test_sharing_timeout_precedes_io_and_does_not_mutate_record(tmp_path):
    path = tmp_path / "run.json"
    dispatch._write_record(path, {"revision": 1})
    script = (
        "import sys; from codex_harness.core.record_io import write_record_json\n"
        "print('ready',flush=True)\n"
        "try: write_record_json(sys.argv[1],{'revision':2})\n"
        "except TimeoutError: print('bounded-timeout',flush=True)\n"
    )
    with open_record_read(path) as reader:
        with subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as child:
            stdout, stderr = child.communicate(timeout=7)
            assert child.returncode == 0, stderr
            assert stdout.splitlines() == ["ready", "bounded-timeout"]
        assert json.loads(reader.read()) == {"revision": 1}
        assert list(tmp_path.iterdir()) == [path]


def test_synthetic_read_refusal_propagates_once(tmp_path, monkeypatch):
    from codex_harness.core import record_io

    calls = []

    def refused(path, mode):
        calls.append((path, mode))
        raise PermissionError("synthetic read refusal")

    monkeypatch.setattr(record_io, "open", refused, raising=False)
    with pytest.raises(PermissionError, match="synthetic read refusal"):
        read_record_json(tmp_path / "record.json")
    assert calls == [(tmp_path / "record.json", "rb")]


def test_synthetic_publication_refusal_propagates_once(tmp_path, monkeypatch):
    path = tmp_path / "run.json"
    dispatch._write_record(path, {"revision": 1})
    calls = []

    def refused(temporary, destination):
        calls.append(destination)
        raise PermissionError("synthetic atomic publication refusal")

    monkeypatch.setattr(Path, "replace", refused)
    with pytest.raises(PermissionError, match="synthetic atomic publication refusal"):
        dispatch._write_record(path, {"revision": 2})
    assert calls == [path]
    assert read_record_json(path) == {"revision": 1}
