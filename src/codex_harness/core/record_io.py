"""UTF-8 process JSON and atomic records, with bounded Windows sharing."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep
from typing import BinaryIO


def write_stdout_json(value, *, sort_keys=False) -> None:
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False, sort_keys=sort_keys) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


@contextmanager
def _record_guard(path: str | os.PathLike[str]) -> Iterator[None]:
    if os.name != "nt":
        yield
        return

    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    wait = kernel.WaitForSingleObject
    wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait.restype = wintypes.DWORD
    release = kernel.ReleaseMutex
    release.argtypes = (wintypes.HANDLE,)
    release.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    canonical = os.path.normcase(os.path.realpath(path))
    name = "Local\\SeldonRecord-" + hashlib.sha256(os.fsencode(canonical)).hexdigest()
    # Default object security, no persistent file/store, no changed file rights.
    handle = create(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        outcome = wait(handle, 5000)
        if outcome == 0x102:
            raise TimeoutError(f"Record sharing wait exceeded 5 seconds: {path}")
        if outcome not in (0, 0x80):  # Acquired, or acquired after an abandoned owner.
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        try:
            if acquired and not release(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            close(handle)


@contextmanager
def open_record_read(path: str | os.PathLike[str]) -> Iterator[BinaryIO]:
    """Binary read context; close promptly so participating writers may publish.

    Windows readers and writers serialize by canonical path before opening the
    file, across processes in the same Windows session. The maximum wait is five
    seconds. Access failures and timeouts propagate without I/O retries. POSIX
    readers retain ordinary atomic-replacement semantics. This is no authority
    or security boundary; nonparticipating readers can still cause a refusal.
    """
    with _record_guard(path), open(path, "rb") as stream:
        yield stream


def read_record_json(path: str | os.PathLike[str]):
    """Read one UTF-8 JSON record under the participating-reader contract."""
    with open_record_read(path) as stream:
        return json.loads(stream.read().decode("utf-8"))


def _replace_record(temporary: Path, destination: Path) -> None:
    """Retry only bounded Windows replacement refusals under the caller's guard."""
    delays = (0.01, 0.02, 0.04)
    for attempt in range(len(delays) + 1):
        try:
            temporary.replace(destination)
            return
        except PermissionError as error:
            if (
                os.name != "nt"
                or getattr(error, "winerror", None) not in (5, 32)
                or attempt == len(delays)
            ):
                raise
            sleep(delays[attempt])


def write_record_json(path: str | os.PathLike[str], record: dict) -> None:
    """Publish a complete record; retain the old record and new temp on refusal.

    Under the existing guard, retry replacement of the same closed, fsynced temp
    only for Windows PermissionError codes 5 and 32, with at most four attempts.
    Backoff requests total at most 70 ms; this is not a wall-time guarantee.
    Other I/O is not retried. Persistent refusal propagates with the temp retained.
    This bounds a known sharing failure class, not an established historical cause.
    """
    path = Path(path)
    with _record_guard(path):
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            json.dump(record, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        _replace_record(temporary, path)


def write_record_json_once(path: str | os.PathLike[str], record: dict) -> None:
    """Create complete JSON once; refuse existing destinations without retries.

    Hard-link publication requires filesystem support. On refusal the closed,
    fsynced temporary and any existing original are retained. This participating
    writer protocol is not OS immutability or same-user authentication.
    """
    path = Path(path)
    with _record_guard(path):
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            json.dump(record, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.link(temporary, path)
        try:
            temporary.unlink()
        except OSError:
            # The original was published. An extra temporary link is harmless;
            # never report failed creation and stop a child after success.
            pass
