"""Real concurrent atomic replacement; no provider or security-setting changes."""

import ctypes
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_harness.core import dispatch, record_io
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


def synthetic_error(code):
    error = PermissionError("explicitly synthetic replacement refusal")
    if code is not None:
        error.winerror = code
    return error


@pytest.fixture
def synthetic_policy_controls(monkeypatch):
    state = {"guardHeld": False, "guardEntries": 0, "delays": [], "streams": [], "fsyncs": []}
    real_temporary_file = record_io.NamedTemporaryFile
    real_fsync = os.fsync

    def temporary_file(**kwargs):
        stream = real_temporary_file(**kwargs)
        state["streams"].append(stream)
        return stream

    def fsync(descriptor):
        real_fsync(descriptor)
        state["fsyncs"].append(descriptor)

    @contextmanager
    def guard(path):
        assert not state["guardHeld"]
        state["guardHeld"] = True
        state["guardEntries"] += 1
        try:
            yield
        finally:
            state["guardHeld"] = False

    def sleep(seconds):
        assert state["guardHeld"], "Retry must stay inside the existing mutex"
        state["delays"].append(seconds)

    monkeypatch.setattr(record_io, "_record_guard", guard)
    monkeypatch.setattr(record_io, "NamedTemporaryFile", temporary_file)
    monkeypatch.setattr(record_io.os, "fsync", fsync)
    monkeypatch.setattr(record_io, "sleep", sleep, raising=False)
    return state


@pytest.mark.skipif(os.name != "nt", reason="Windows retry policy")
@pytest.mark.parametrize("code", [5, 32])
def test_synthetic_transient_replace_reuses_closed_complete_temp_inside_guard(
    tmp_path, monkeypatch, synthetic_policy_controls, code
):
    target = tmp_path / "run.json"
    target.write_text('{"revision": 1}', encoding="utf-8")
    old_bytes = target.read_bytes()
    calls = []
    real_replace = Path.replace
    state = synthetic_policy_controls

    def replace(temporary, destination):
        assert state["guardHeld"]
        assert state["guardEntries"] == 1
        assert len(state["streams"]) == len(state["fsyncs"]) == 1
        assert state["streams"][0].closed
        assert destination == target
        assert json.loads(temporary.read_bytes()) == {"revision": 2}
        calls.append(temporary)
        if len(calls) <= 2:
            assert target.read_bytes() == old_bytes
            raise synthetic_error(code)
        return real_replace(temporary, destination)

    monkeypatch.setattr(Path, "replace", replace)
    record_io.write_record_json(target, {"revision": 2})
    assert len(calls) == 3 and len(set(calls)) == 1
    assert state["delays"] == [0.01, 0.02]
    assert not state["guardHeld"]
    assert json.loads(target.read_bytes()) == {"revision": 2}
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(os.name != "nt", reason="Windows retry policy")
@pytest.mark.parametrize("code", [5, 32])
def test_synthetic_retry_exhaustion_reraises_last_error_and_retains_both_files(
    tmp_path, monkeypatch, synthetic_policy_controls, code
):
    target = tmp_path / "run.json"
    target.write_text('{"revision": 1}', encoding="utf-8")
    old_bytes = target.read_bytes()
    calls, errors = [], []
    state = synthetic_policy_controls

    def replace(temporary, destination):
        assert state["guardHeld"]
        assert state["guardEntries"] == 1
        assert len(state["streams"]) == len(state["fsyncs"]) == 1
        assert state["streams"][0].closed
        assert destination == target
        assert target.read_bytes() == old_bytes
        assert json.loads(temporary.read_bytes()) == {"revision": 2}
        calls.append(temporary)
        errors.append(synthetic_error(code))
        raise errors[-1]

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(PermissionError) as caught:
        record_io.write_record_json(target, {"revision": 2})
    assert len(calls) == 4 and len(set(calls)) == 1
    assert caught.value is errors[-1]
    assert state["delays"] == [0.01, 0.02, 0.04]
    assert not state["guardHeld"]
    assert target.read_bytes() == old_bytes
    assert json.loads(calls[0].read_bytes()) == {"revision": 2}
    assert set(tmp_path.iterdir()) == {target, calls[0]}


@pytest.mark.parametrize("code", [None, 1, 13, 33])
def test_synthetic_unlisted_permission_refusal_stays_one_shot(
    tmp_path, monkeypatch, synthetic_policy_controls, code
):
    target = tmp_path / "run.json"
    target.write_text('{"revision": 1}', encoding="utf-8")
    error = synthetic_error(code)
    calls = []

    def replace(temporary, destination):
        calls.append(temporary)
        raise error

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(PermissionError) as caught:
        record_io.write_record_json(target, {"revision": 2})
    assert caught.value is error
    assert len(calls) == 1
    assert synthetic_policy_controls["delays"] == []
    assert json.loads(target.read_bytes()) == {"revision": 1}
    assert json.loads(calls[0].read_bytes()) == {"revision": 2}


@pytest.mark.parametrize("code", [5, 32])
def test_synthetic_windows_code_on_non_windows_path_stays_one_shot(
    tmp_path, monkeypatch, synthetic_policy_controls, code
):
    # Synthetic platform branch only; native tests below establish actual Windows behavior.
    monkeypatch.setattr(record_io, "os", SimpleNamespace(name="posix", fsync=os.fsync))
    target = tmp_path / "run.json"
    target.write_text('{"revision": 1}', encoding="utf-8")
    calls = []
    error = synthetic_error(code)

    def replace(temporary, destination):
        calls.append(temporary)
        raise error

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(PermissionError) as caught:
        record_io.write_record_json(target, {"revision": 2})
    assert caught.value is error and len(calls) == 1
    assert synthetic_policy_controls["delays"] == []


def test_synthetic_other_exception_type_stays_one_shot(
    tmp_path, monkeypatch, synthetic_policy_controls
):
    target = tmp_path / "run.json"
    error = OSError("explicitly synthetic non-PermissionError")
    error.winerror = 5
    calls = []

    def replace(temporary, destination):
        calls.append(temporary)
        raise error

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(OSError) as caught:
        record_io.write_record_json(target, {"revision": 2})
    assert caught.value is error and len(calls) == 1
    assert synthetic_policy_controls["delays"] == []
    assert not target.exists()
    assert json.loads(calls[0].read_bytes()) == {"revision": 2}


@pytest.mark.parametrize("code", [5, 32])
def test_synthetic_read_refusal_with_windows_code_stays_one_shot(
    tmp_path, monkeypatch, synthetic_policy_controls, code
):
    target = tmp_path / "run.json"
    calls = []
    error = synthetic_error(code)

    def refused(path, mode):
        calls.append((path, mode))
        raise error

    monkeypatch.setattr(record_io, "open", refused, raising=False)
    with pytest.raises(PermissionError) as caught:
        record_io.read_record_json(target)
    assert caught.value is error and calls == [(target, "rb")]
    assert synthetic_policy_controls["delays"] == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("stage", ["create", "write", "flush", "fsync"])
@pytest.mark.parametrize("code", [5, 32])
def test_synthetic_prepublication_refusal_stays_one_shot(
    tmp_path, monkeypatch, synthetic_policy_controls, stage, code
):
    target = tmp_path / "run.json"
    target.write_text('{"revision": 1}', encoding="utf-8")
    old_bytes = target.read_bytes()
    error = synthetic_error(code)
    calls = []
    replacements = []

    def refused(*args, **kwargs):
        calls.append(stage)
        raise error

    if stage == "create":
        monkeypatch.setattr(record_io, "NamedTemporaryFile", refused)
    elif stage == "write":
        monkeypatch.setattr(record_io.json, "dump", refused)
    elif stage == "flush":
        real_temporary_file = record_io.NamedTemporaryFile

        @contextmanager
        def temporary_file(**kwargs):
            with real_temporary_file(**kwargs) as stream:
                monkeypatch.setattr(stream, "flush", refused)
                yield stream

        monkeypatch.setattr(record_io, "NamedTemporaryFile", temporary_file)
    else:
        monkeypatch.setattr(record_io.os, "fsync", refused)

    monkeypatch.setattr(Path, "replace", lambda *args: replacements.append(args))
    with pytest.raises(PermissionError) as caught:
        record_io.write_record_json(target, {"revision": 2})
    assert caught.value is error
    assert calls == [stage]
    assert replacements == []
    assert synthetic_policy_controls["delays"] == []
    assert synthetic_policy_controls["guardEntries"] == 1
    assert not synthetic_policy_controls["guardHeld"]
    assert target.read_bytes() == old_bytes


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
