"""C12/C13/C19: isolated owned Git worktrees and byte-verified integration."""

import hashlib
import os
import uuid
from pathlib import Path

from .evidence import execute
from .plan import safe_path
from .store import digest


def git(project, *args):
    result = execute(["git", *args], project)
    if result["exitCode"] != 0 or not result["cleanupVerified"]:
        raise RuntimeError(result["stderr"].decode("utf-8", errors="replace"))


def files(root):
    result = {}
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = [name for name in subdirs if name not in (".git", ".seldon")]
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == ".git":
                continue
            safe_path(relative)
            if path.is_symlink():
                raise ValueError(f"Product files must be ordinary files: {relative}")
            result[relative] = path.read_bytes()
    return result


def candidate(content):
    manifest = {path: hashlib.sha256(data).hexdigest() for path, data in sorted(content.items())}
    return {"digest": digest(manifest), "files": manifest}


def admit(project, scratch, owned_paths):
    baseline = files(project)
    key = "worker-" + uuid.uuid4().hex
    root = scratch / key
    git(project, "worktree", "add", "--orphan", "-b", key, str(root))
    for name, data in baseline.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {"root": root, "baseline": baseline, "ownedPaths": owned_paths}


def changes(assignment, reported_paths):
    after = files(assignment["root"])
    before = assignment["baseline"]
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    if not changed <= set(assignment["ownedPaths"]):
        raise ValueError(f"Worker changed unowned files: {changed - set(assignment['ownedPaths'])}")
    if set(reported_paths) != changed:
        raise ValueError("Worker artifact paths must match the observed changed files")
    if not changed <= after.keys():
        raise ValueError("Day-one workers deliver files; deletion is not implemented")
    return {name: after[name] for name in changed}


def integrate(project, baseline, changed):
    current = files(project)
    conflicts = [name for name in changed if current.get(name) != baseline.get(name)]
    if conflicts:
        raise ValueError(f"Integration conflicts: {conflicts}")
    for name, data in changed.items():
        target = project / safe_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    delivered = files(project)
    if any(delivered[name] != data for name, data in changed.items()):
        raise ValueError("Integrated files differ from worker output")
    return {"candidateRef": candidate(delivered),
            "changedPaths": sorted(changed), "conflictPaths": []}


# C12 extension: ready-work selection admits nonconflicting tasks within observed availability.
