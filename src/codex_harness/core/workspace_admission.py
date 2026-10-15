from __future__ import annotations

import os
import stat
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any


class WorkspaceAdmissionError(ValueError):
    pass


def _is_reparse_point(path: Path) -> bool:
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise WorkspaceAdmissionError(f"workspace-path-unreadable: {path}") from exc
    return stat.S_ISLNK(path_stat.st_mode) or bool(
        int(getattr(path_stat, "st_file_attributes", 0)) & 0x400
    )


def _identity(path: Path) -> tuple[int, int]:
    path_stat = path.lstat()
    return (int(path_stat.st_dev), int(path_stat.st_ino))


class WorkspaceAdmissionLease(AbstractContextManager[Any]):
    """Pin a lexical workspace root for one complete prepare transaction."""

    def __init__(self, lexical_path: Path) -> None:
        self.lexical_path = lexical_path
        self._identity = _identity(lexical_path)
        self._handle: Any | None = None
        self._handle_identity: tuple[int, int] | None = None

    def __enter__(self) -> WorkspaceAdmissionLease:
        if os.name != "nt":  # pragma: no cover - fail closed outside supported host
            raise WorkspaceAdmissionError("workspace-lease-unavailable")
        import ctypes
        from ctypes import wintypes

        create_file = ctypes.windll.kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(self.lexical_path),
            0x80000000,
            0x00000001 | 0x00000002,
            None,
            3,
            0x02000000,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise WorkspaceAdmissionError("workspace-lease-unavailable")
        self._handle = handle
        try:
            self._handle_identity = self._read_handle_identity()
            self.verify()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if self._handle is not None:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None

    def verify(self) -> None:
        if self._handle is None:
            raise WorkspaceAdmissionError("workspace-lease-unavailable")
        if self._read_handle_identity() != self._handle_identity:
            raise WorkspaceAdmissionError("workspace-path-changed-before-write")
        if self._normal_path(self._read_handle_final_path()) != self._normal_path(
            self.lexical_path
        ):
            raise WorkspaceAdmissionError("workspace-path-changed-before-write")
        admitted = admit_workspace_root(str(self.lexical_path))
        if admitted.lexical_path != self.lexical_path or admitted._identity != self._identity:
            raise WorkspaceAdmissionError("workspace-path-changed-before-write")

    def _read_handle_identity(self) -> tuple[int, int]:
        import ctypes
        from ctypes import wintypes

        class _FileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        information = _FileInformation()
        if not ctypes.windll.kernel32.GetFileInformationByHandle(
            self._handle, ctypes.byref(information)
        ):
            raise WorkspaceAdmissionError("workspace-lease-unavailable")
        return (
            int(information.dwVolumeSerialNumber),
            (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow),
        )

    def _read_handle_final_path(self) -> Path:
        import ctypes

        size = ctypes.windll.kernel32.GetFinalPathNameByHandleW(self._handle, None, 0, 0)
        if not size:
            raise WorkspaceAdmissionError("workspace-lease-unavailable")
        buffer = ctypes.create_unicode_buffer(size + 1)
        result = ctypes.windll.kernel32.GetFinalPathNameByHandleW(
            self._handle, buffer, len(buffer), 0
        )
        if not result or result >= len(buffer):
            raise WorkspaceAdmissionError("workspace-lease-unavailable")
        value = buffer.value
        return Path(value[4:] if value.startswith("\\\\?\\") else value)

    @staticmethod
    def _normal_path(path: Path) -> str:
        return os.path.normcase(os.path.normpath(str(path)))


def admit_workspace_root(value: object) -> WorkspaceAdmissionLease:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceAdmissionError("workspace-path-invalid")
    lexical_path = Path(value)
    if not lexical_path.is_absolute() or ".." in lexical_path.parts:
        raise WorkspaceAdmissionError("workspace-path-escape")
    lexical_path = Path(os.path.abspath(lexical_path))
    current = Path(lexical_path.anchor)
    for part in lexical_path.parts[1:]:
        current = current / part
        if current.exists() or current.is_symlink():
            if _is_reparse_point(current):
                raise WorkspaceAdmissionError("workspace-path-reparse")
    if not lexical_path.is_dir():
        raise WorkspaceAdmissionError("workspace-path-not-directory")
    try:
        resolved = lexical_path.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceAdmissionError("workspace-path-unreadable") from exc
    if os.path.normcase(os.path.normpath(str(resolved))) != os.path.normcase(
        os.path.normpath(str(lexical_path))
    ):
        raise WorkspaceAdmissionError("workspace-path-ambiguous")
    return WorkspaceAdmissionLease(lexical_path)


def acquire_workspace_lease(value: object) -> WorkspaceAdmissionLease:
    """Validate and pin a workspace before constructing any writer."""

    return admit_workspace_root(value)
