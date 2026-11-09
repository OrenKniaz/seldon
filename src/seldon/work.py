"""C12/C13/C19: real Git bases, owned patches and replayable candidate publication."""

import hashlib
import os
import uuid
from pathlib import Path

from .evidence import execute
from .plan import safe_path
from .store import digest


def git(project, *args):
    result = execute(["git", "-c", "core.autocrlf=false", *args], project)
    if result["exitCode"] != 0 or not result["cleanupVerified"]:
        raise RuntimeError(result["stderr"].decode("utf-8", errors="replace"))
    return result["stdout"].decode().strip()


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


def revision(root, parent=None):
    git(root, "add", "-A", "--", ".", ":!.seldon")
    tree = git(root, "write-tree")
    ref = git(root, "-c", "user.name=Seldon", "-c", "user.email=seldon@local",
              "commit-tree", tree, *(["-p", parent] if parent else []), "-m", "Scoped candidate")
    committed = {}
    for entry in git(root, "ls-tree", "-rz", "--full-tree", ref).split("\0"):
        if entry:
            metadata, name = entry.split("\t", 1)
            committed[name] = metadata.split()[2]
    algorithm = git(root, "rev-parse", "--show-object-format")
    expected = {name: hashlib.new(algorithm, b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                for name, data in files(root).items()}
    mismatches = sorted(p for p in committed.keys() | expected.keys() if committed.get(p) != expected.get(p))
    if mismatches:
        raise ValueError(f"Commit/manifest mismatch: {mismatches}; correct product Git ignore/filter rules before publishing")
    return ref


def admit(project, scratch, owned_paths):
    base = git(project, "rev-parse", "HEAD")
    root = scratch / ("worker-" + uuid.uuid4().hex)
    git(project, "worktree", "add", "--detach", str(root), base)
    return {"worktree": str(root), "baseRevision": base, "ownedPaths": owned_paths,
            "baseline": candidate(files(root))}


def changes(assignment, reported_paths):
    after = files(Path(assignment["worktree"]))
    before = assignment["baseline"]["files"]
    hashes = candidate(after)["files"]
    changed = {name for name in before.keys() | hashes.keys() if before.get(name) != hashes.get(name)}
    if not changed <= set(assignment["ownedPaths"]):
        raise ValueError(f"Worker changed unowned files: {changed - set(assignment['ownedPaths'])}")
    if set(reported_paths) != changed:
        raise ValueError("Worker artifact paths must match the observed changed files")
    if not changed <= after.keys():
        raise ValueError("Workers deliver files; deletion is not implemented")
    return {name: after[name] for name in changed}


def prepare(project, scratch, assignment, changed):
    current = files(project)
    hashes = candidate(current)["files"]
    conflicts = [name for name in changed if hashes.get(name) != assignment["baseline"]["files"].get(name)]
    if conflicts:
        raise ValueError(f"Integration conflicts: {conflicts}")
    staging = admit(project, scratch, sorted(changed))
    root = Path(staging["worktree"])
    for name, data in changed.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {"baseRevision": staging["baseRevision"], "revision": revision(root, staging["baseRevision"]),
            "candidateRef": candidate({**current, **changed}), "changedPaths": sorted(changed), "conflictPaths": []}


def publish(project, store, publication):
    """Replay an intent before exposing its candidate; old/new bytes are both safe."""
    for name, patch in publication["diff"].items():
        target = project / safe_path(name)
        current = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if current not in (patch["before"], patch["after"]):
            raise ValueError(f"Publication preimage changed: {name}")
        if current != patch["after"]:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = store.root / "artifacts" / ("publish-" + uuid.uuid4().hex)
            temporary.write_bytes((store.root / "artifacts" / patch["after"]).read_bytes())
            os.replace(temporary, target)
    if candidate(files(project)) != publication["candidateRef"]:
        raise ValueError("Publication does not match its prepared candidate")
    if git(project, "rev-parse", "HEAD") != publication["revision"]:
        git(project, "update-ref", "HEAD", publication["revision"], publication["baseRevision"])
