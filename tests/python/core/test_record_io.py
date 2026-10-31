"""Real concurrent atomic replacement; no provider or security-setting changes."""

import ctypes
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from ctypes import wintypes

import pytest

from codex_harness.core import dispatch, record_io
from codex_harness.core.record_io import open_record_read, read_record_json


@pytest.mark.parametrize("relative", ["request.json", "evidence/run.json"])
def test_supported_reader_serializes_cross_process_publication(tmp_path, relative):
    """Catch torn cross-process replacement at root/nested paths; backoff tests lack readers."""
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


@contextmanager
def native_nonparticipating_reader(path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    held = True

    def release():
        nonlocal held
        if held:
            if not close(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            held = False

    try:
        yield release
    finally:
        release()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows handle sharing")
def test_native_nonparticipating_reader_released_during_backoff_allows_publication(
    tmp_path, monkeypatch
):
    target = tmp_path / "run.json"
    record_io.write_record_json(target, {"revision": 1})
    backoffs = []
    with native_nonparticipating_reader(target) as release:

        def release_at_backoff(seconds):
            backoffs.append(seconds)
            assert json.loads(target.read_bytes()) == {"revision": 1}
            release()

        monkeypatch.setattr(record_io, "sleep", release_at_backoff, raising=False)
        record_io.write_record_json(target, {"revision": 2})
    assert backoffs == [0.01]
    assert json.loads(target.read_bytes()) == {"revision": 2}
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows handle sharing")
def test_native_persistent_reader_refuses_and_preserves_old_record_and_new_temp(
    tmp_path, monkeypatch
):
    target = tmp_path / "run.json"
    record_io.write_record_json(target, {"revision": 1})
    old_bytes = target.read_bytes()
    backoffs = []
    monkeypatch.setattr(record_io, "sleep", backoffs.append, raising=False)
    with native_nonparticipating_reader(target):
        with pytest.raises(PermissionError) as caught:
            record_io.write_record_json(target, {"revision": 2})
        assert caught.value.winerror in (5, 32)
        assert target.read_bytes() == old_bytes
        temps = [p for p in tmp_path.iterdir() if p != target]
        assert len(temps) == 1
        assert json.loads(temps[0].read_bytes()) == {"revision": 2}
    assert backoffs == [0.01, 0.02, 0.04]
