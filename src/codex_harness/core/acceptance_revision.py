"""Product revision identity and bounded metadata capture."""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

from codex_harness.core.plan_store import PlanStoreError
from codex_harness.core.semantic_runner import SOURCE_IGNORED

def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

def _metadata_remaining(deadline, should_stop=None):
    if should_stop and should_stop():
        raise PlanStoreError(
            "acceptance-owner-stopped", "Owner stopped; no further metadata operation"
        )
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PlanStoreError(
            "acceptance-deadline-exhausted", "Metadata exceeded the captured run budget"
        )
    return remaining

def _product_git_head(repo: Path, deadline=None, should_stop=None) -> str | None:
    """Bind Git metadata only to this product root, including linked worktrees.

    Ancestor repositories cannot stand in for a plain product directory.
    """
    top = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
        stdin=subprocess.DEVNULL,
        timeout=_metadata_remaining(deadline, should_stop),
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    if top.returncode != 0:
        if (repo / ".git").exists() or (repo / ".git").is_symlink():
            raise ValueError(f"Cannot determine product Git scope: {repo}")
        return None
    if not top.stdout.strip() or Path(top.stdout.strip()).resolve() != repo:
        return None
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        stdin=subprocess.DEVNULL,
        timeout=_metadata_remaining(deadline, should_stop),
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    return head.stdout.strip() if head.returncode == 0 else None

def integrated_revision(repo, *, deadline=None, should_stop=None) -> str:
    """Bind evidence to HEAD and actual working files, including untracked code."""
    _metadata_remaining(deadline, should_stop)
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise ValueError(f"Repository directory does not exist: {repo}")
    head = _product_git_head(repo, deadline, should_stop)
    if head is not None:
        listing = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            stdin=subprocess.DEVNULL,
            timeout=_metadata_remaining(deadline, should_stop),
            capture_output=True,
            check=True,
        )
        paths = sorted(set(listing.stdout.decode("utf-8").split("\0")) - {""})
    else:
        paths = []
        for path in repo.rglob("*"):
            _metadata_remaining(deadline, should_stop)
            if path.is_file():
                paths.append(path.relative_to(repo).as_posix())
        paths.sort()
    files = []
    for name in paths:
        path = repo / name
        if not SOURCE_IGNORED.intersection(Path(name).parts):
            _metadata_remaining(deadline, should_stop)
            digest = None
            if path.is_file():
                hasher = hashlib.sha256()
                with path.open("rb") as source:
                    while True:
                        _metadata_remaining(deadline, should_stop)
                        chunk = source.read(64 * 1024)
                        if not chunk:
                            break
                        _metadata_remaining(deadline, should_stop)
                        hasher.update(chunk)
                digest = hasher.hexdigest()
            files.append((name, digest))
    _metadata_remaining(deadline, should_stop)
    return _hash(
        {
            "algorithm": "tree-bytes-v2",
            "head": head,
            "files": files,
        }
    )
