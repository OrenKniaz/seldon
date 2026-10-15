"""Atomic JSON publication and participating readers, with bounded Windows sharing."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO


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


def write_record_json(path: str | os.PathLike[str], record: dict) -> None:
    """Publish one complete JSON record; preserve refusal and its temporary file."""
    path = Path(path)
    with _record_guard(path):
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            json.dump(record, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        temporary.replace(path)
