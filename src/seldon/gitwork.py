"""C12/C19/C23/I6; R6/R9/R11: Git snapshots, isolated worktrees and replayable publication."""

import os
import subprocess
import uuid
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from .plan import matches, safe_path
from .states import advance


def git(root, *args, data=None, env=None, binary=False):
    result = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=root, input=data,
                            capture_output=True, timeout=120, env={**os.environ, **(env or {})},
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout if binary else result.stdout.decode("utf-8").strip()


def commit(root, tree, parent=None):
    return git(root, "-c", "user.name=Seldon", "-c", "user.email=seldon@local", "commit-tree", tree,
               *(["-p", parent] if parent else []), "-m", "Seldon candidate")


def init(root):
    git(root, "init", "--quiet")
    empty = git(root, "mktree", data=b"")
    revision = commit(root, empty)
    git(root, "update-ref", "HEAD", revision)
    return revision


def admit(project, scratch, base):
    root = Path(scratch) / ("worker-" + uuid.uuid4().hex)
    git(project, "worktree", "add", "--quiet", "--detach", str(root), base)
    return str(root)


def checkout(root, revision):
    git(root, "reset", "--hard", revision)


def paths(root, revision):
    return [p for p in git(root, "ls-tree", "-rz", "--name-only", revision).split("\0") if p]


def sources(root, revision, patterns, named=None, *, details=False):
    entries = git(root, "ls-tree", "-rz", revision, binary=True)
    blobs = {}
    for entry in entries.split(b"\0"):
        if entry:
            header, name = entry.split(b"\t", 1)
            _, kind, oid = header.split()
            path = name.decode("utf-8")
            if kind == b"blob" and (named is None or path in named) and matches(path, patterns):
                blobs[path] = oid
    if not blobs:
        return {}
    batch = BytesIO(git(root, "cat-file", "--batch", data=b"\n".join(blobs.values()) + b"\n", binary=True))
    result = {}
    for path in blobs:
        _, _, size = batch.readline().split()
        content = batch.read(int(size))
        if details:
            result[path] = {"present": True, "bytes": len(content), "commit": revision}
            try:
                result[path]["text"] = content.decode("utf-8")
            except UnicodeDecodeError:
                pass
        else:
            result[path] = content.decode("utf-8", errors="replace")
        batch.read(1)  # Git separates size-delimited blobs with a newline.
    return result


def scope_tree(root, revision, patterns, scratch):
    """Git's tree identity for the owned paths, preserving modes, deletions and names."""
    entries = git(root, "ls-tree", "-rz", revision, binary=True).split(b"\0")
    entries = [e for e in entries if e and matches(e.split(b"\t", 1)[1].decode("utf-8"), patterns)]
    env = {"GIT_INDEX_FILE": str(Path(scratch) / ("index-" + uuid.uuid4().hex))}
    if entries:
        git(root, "update-index", "-z", "--index-info", data=b"\0".join(entries) + b"\0", env=env)
    else:
        git(root, "read-tree", "--empty", env=env)
    return git(root, "write-tree", env=env)


def snapshots(root, base, scope, check_paths, scratch):
    root = Path(root)
    # Include ignored new files too: the observed worktree, not the worker's declared file list.
    git(root, "add", "--all", "--force", "--", ".", ":!.seldon")
    changed = [p for p in git(root, "diff", "--cached", "--no-renames", "--name-only", "-z",
                              base).split("\0") if p]
    bad = [p for p in changed if not matches(safe_path(p), scope)]
    if bad:
        raise ValueError("Out-of-scope writes: " + ", ".join(bad))
    product = commit(root, git(root, "write-tree"), base)
    env = {"GIT_INDEX_FILE": str(Path(scratch) / ("index-" + uuid.uuid4().hex))}
    git(root, "read-tree", base, env=env)
    tests_changed = [p for p in changed if matches(p, check_paths)]
    if tests_changed:
        git(root, "--literal-pathspecs", "restore", "--source", product, "--staged", "--",
            *tests_changed, env=env)
    tests = commit(root, git(root, "write-tree", env=env), base)
    return tests, product, changed


def integrated(project, scratch, base, product, current):
    if base == current:
        return product
    env = {"GIT_INDEX_FILE": str(Path(scratch) / ("index-" + uuid.uuid4().hex))}
    # Merge whole entries in a private index. An unresolved path cannot become a candidate tree.
    try:
        git(project, "read-tree", "-i", "-m", "--aggressive", base, current, product, env=env)
        tree = git(project, "write-tree", env=env)
    except RuntimeError as error:
        raise RuntimeError(f"Seldon integration fault (disjoint scopes): {error}") from error
    return commit(project, tree, current)


def check_commit(root, wave_start, product, check_paths, scratch):
    env = {"GIT_INDEX_FILE": str(Path(scratch) / ("index-" + uuid.uuid4().hex))}
    git(root, "read-tree", wave_start, env=env)
    names = [p for p in set(paths(root, wave_start)) | set(paths(root, product)) if matches(p, check_paths)]
    if names:
        git(root, "--literal-pathspecs", "restore", "--source", product, "--staged", "--", *names, env=env)
    return commit(root, git(root, "write-tree", env=env), wave_start)


def unchanged(root, revision):
    status = git(root, "status", "--porcelain=v2", "--branch", "--untracked-files=normal",
                 "--ignored=matching").splitlines()
    changes = [line for line in status if not line.startswith("# ")
               and line not in ("? .seldon/", "! .seldon/")]
    if changes or f"# branch.oid {revision}" not in status:
        raise ValueError("Candidate changed outside integration: " + "; ".join(changes))


def publish(project, store):
    """Intent is durable before apply. Reopen repeats apply and atomically confirms ownership."""
    cursor = store.cursor()
    if not cursor.publication:
        return
    intent = store.read(cursor.publication)
    current = git(project, "rev-parse", "HEAD")
    if current not in (intent["before"], intent["commit"]):
        raise ValueError("Publication preimage changed outside integration")
    checkout(project, intent["commit"])
    with store.transaction():
        store.record("integration", {"publication": cursor.publication, **intent})
        store.cursor(replace(store.cursor(), candidate=intent["commit"], publication=None))
        advance(store, "integrated", cursor.publication, unit=intent["unit"],
                integrated=intent["commit"], failure="")
