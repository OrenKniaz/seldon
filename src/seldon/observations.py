"""C14/C26/I9, R9: measured facts from check execution and delivered source."""

import ast
import hashlib
import json
import os
import re
from collections import Counter
from heapq import nlargest
from pathlib import Path

from . import gitwork
from .store import encode


_JS_NON_CODE = re.compile(
    r"""//.*|/\*.*?\*/|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`""")
_JS_MORE_CODE = re.compile(r";\s*[\w$({\[]|}\s*(?!(?:else|catch|finally)\b)[\w$]")


def _statement_lines(path, text, lines):
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return None
        counts = Counter(node.lineno for node in ast.walk(tree) if isinstance(node, ast.stmt))
        return sum(count > 1 for count in counts.values())
    if suffix in {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts"}:
        return sum(bool(_JS_MORE_CODE.search(_JS_NON_CODE.sub(" ", line))) for line in lines)
    return None


def code_shape(product_files):
    """Describe the same delivered text the judge sees. Statement counts for JS are estimates."""
    facts = {}
    for path, text in product_files.items():
        lines = text.splitlines()
        longest = nlargest(3, enumerate(lines, 1), key=lambda row: len(row[1]))
        facts[path] = {
            "lineCount": len(lines),
            "longestLineChars": len(longest[0][1]) if longest else 0,
            "linesOver160Chars": sum(len(line) > 160 for line in lines),
            "linesWithMultipleStatements": _statement_lines(path, text, lines),
            "longestLines": [{"line": n, "chars": len(line), "utf8Bytes": len(line.encode("utf-8")),
                              "prefix": line[:120]} for n, line in longest],
        }
    return facts


def runtime_file(name):
    return name.endswith(".json") and (name == "operation-lifecycle.json"
                                       or name.startswith(("browser-visits-", "server-cleanup-")))


def _read(path):
    if not path.is_file() or path.is_symlink():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [{"observationError": f"Could not read {path.name}: {error}"}]
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return [{"observationError": f"{path.name} must contain a JSON array of observation objects"}]
    return rows


def runtime(result, checkout, temp, evidence, *, walkthrough, commit):
    facts = {}
    if visits := [visit for path in sorted(evidence.glob("browser-visits-*.json")) for visit in _read(path)]:
        facts["browserVisits"] = visits
    if walkthrough:
        facts["operationLifecycle"] = _read(evidence / "operation-lifecycle.json") or [
            {"observed": False, "reason": "The walkthrough recorded no documented operation lifecycle."}]
    if result["executionStarted"] and result["cleanupVerified"]:
        cleanup = _file_cleanup(checkout, temp, commit)
        if servers := [row for path in sorted(evidence.glob("server-cleanup-*.json")) for row in _read(path)]:
            cleanup["serverData"] = servers
        facts["fileCleanup"] = cleanup
    return facts


def _file_cleanup(checkout, temp, commit):
    # Include ignored data and modified tracked files. A failed rename isn't needed to
    # query Windows delete sharing, and an open POSIX handle does not prevent unlink.
    if os.name != "nt":
        return {"probed": False, "reason": "Windows delete-sharing probe is unavailable on this platform."}
    changed = gitwork.git(checkout, "diff", "--name-only", "-z", commit, binary=True)
    new = gitwork.git(checkout, "ls-files", "--others", "-z", binary=True)
    files = set(os.fsdecode(name) for name in (changed + new).split(b"\0") if name)
    paths = [("checkout", checkout, checkout / name) for name in sorted(files)]
    for folder, dirs, names in os.walk(temp):
        parent = Path(folder)
        dirs[:] = [name for name in dirs if not (parent / name).is_junction()]
        paths.extend(("temp", temp, parent / name) for name in sorted(names))
    outcomes = []
    checked = 0
    for area, root, path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            continue
        checked += 1
        if code := _delete_access(path):
            outcomes.append({"root": area, "path": path.relative_to(root).as_posix(),
                             "windowsError": code, "locked": code in (32, 33)})
    return {"probed": bool(checked), "filesChecked": checked, "blockedFiles": outcomes}


def _delete_access(path):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                          wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    open_file.restype = wintypes.HANDLE
    close_handle = kernel.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    # DELETE access, sharing read/write/delete, existing file only.
    handle = open_file(str(path), 0x10000, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        return ctypes.get_last_error()
    close_handle(handle)
    return 0


def _lifecycle_facts(value):
    if isinstance(value, dict):
        return {key: _lifecycle_facts(item) for key, item in value.items()}
    if isinstance(value, list) and value:
        if all(type(item) in (int, float) for item in value):
            # Lifecycle judgment needs the shape and bounds of a series. The observation
            # record retains the complete values for checks that need individual samples.
            return {"count": len(value), "min": min(value), "max": max(value),
                    "first": value[0], "last": value[-1],
                    "sha256": hashlib.sha256(encode(value).encode()).hexdigest()}
        return [_lifecycle_facts(item) for item in value]
    return value


def judge_fields(body):
    facts = {key: body[key] for key in ("browserVisits", "fileCleanup") if key in body}
    if "operationLifecycle" in body:
        facts["operationLifecycle"] = _lifecycle_facts(body["operationLifecycle"])
    return facts


def final_guide_checks(store):
    from .plan import execution, guide_checks
    return guide_checks(execution(store))
