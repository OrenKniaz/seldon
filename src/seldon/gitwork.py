"""C12/C19/C23/I6; R6/R9/R11: Git snapshots, isolated worktrees and replayable publication."""

import json
import os
import subprocess
import threading
import uuid
from dataclasses import replace
from functools import lru_cache
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


def contains(project, commit, base):
    return subprocess.run(["git", "merge-base", "--is-ancestor", commit, base], cwd=project,
                          capture_output=True, timeout=120).returncode == 0


def init(root):
    git(root, "init", "--quiet")
    empty = git(root, "mktree", data=b"")
    revision = commit(root, empty)
    git(root, "update-ref", "HEAD", revision)
    return revision


# Git does not make concurrent `worktree add` on one repository safe on Windows: parallel wave
# observations raced on .git/worktrees/<name>/commondir (gate 11-13). One process, one adder.
_WORKTREE_ADD = threading.Lock()


def admit(project, scratch, base):
    root = Path(scratch) / ("worker-" + uuid.uuid4().hex)
    with _WORKTREE_ADD:
        git(project, "worktree", "add", "--quiet", "--detach", str(root), base)
    return str(root)


def checkout(root, revision):
    git(root, "reset", "--hard", "--quiet", revision)


def clean(root, revision):
    """Restore an observation checkout, including ignored outputs and nested fixture repos."""
    checkout(root, revision)
    git(root, "clean", "-ffdx", "--quiet")


def paths(root, revision):
    return [e.split(b"\t", 1)[1].decode("utf-8") for e in tree_entries(root, revision)]


def repository(root):
    directory = Path(root) / ".git"
    if directory.is_file():
        directory = Path(root) / directory.read_text("utf-8").strip().removeprefix("gitdir: ")
    if (directory / "commondir").is_file():
        directory = directory / (directory / "commondir").read_text("utf-8").strip()
    return str(directory.resolve())


@lru_cache(maxsize=256)
def _tree_entries(repo, revision):
    # Callers supply immutable commit ids, so worktree resets cannot stale these objects.
    return tuple(e for e in git(repo, "ls-tree", "-rzt", revision, binary=True).split(b"\0") if e)


def tree_entries(root, revision):
    return tuple(e for e in _tree_entries(repository(root), revision)
                 if e.split(b"\t", 1)[0].split()[1] != b"tree")


def blobs(root, revision, patterns, named=None):
    blobs = {}
    for entry in tree_entries(root, revision):
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
        result[path] = content
        batch.read(1)  # Git separates size-delimited blobs with a newline.
    return result


def scope_tree(root, revision, patterns, scratch):
    """Git's tree identity for the owned paths, preserving modes, deletions and names."""
    repo = repository(root)
    if len(patterns) == 1 and patterns[0].endswith("/**"):
        directory = patterns[0][:-3]
        if directory and "/" not in directory and not any(c in directory for c in "*?["):
            for entry in _tree_entries(repo, revision):
                header, path = entry.split(b"\t", 1)
                if header.split()[1] == b"tree" and path.decode("utf-8") == directory:
                    return _directory_scope_tree(repo, entry)
    entries = tuple(e for e in tree_entries(root, revision)
                    if matches(e.split(b"\t", 1)[1].decode("utf-8"), patterns))
    return _scope_tree(repo, entries, scratch)


@lru_cache(maxsize=1024)
def _directory_scope_tree(repo, entry):
    # Reuse the existing subtree, wrapping it in its original root path: the same full scope tree.
    return git(repo, "mktree", "-z", data=entry + b"\0")


@lru_cache(maxsize=1024)
def _scope_tree(root, entries, scratch):
    # Identical Git entries reuse the same Git-generated tree across worker and final commits.
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
    fields = git(root, "diff", "--cached", "--no-renames", "--raw", "--no-abbrev", "-z",
                 base).split("\0")
    changes = [(path, header.split()[1], header.split()[3])
               for header, path in zip(fields[0:-1:2], fields[1::2])]
    changed = [p for p, _, _ in changes]
    bad = [p for p in changed if not matches(safe_path(p), scope)]
    if bad:
        raise ValueError("Out-of-scope writes: " + ", ".join(bad))
    product, tests = import_commits(root, base, [changes, [c for c in changes if matches(c[0], check_paths)]])
    return tests, product, changed


def import_commits(root, parent, patches):
    """Git builds snapshots in one batch; the worker index and published refs are untouched."""
    stream, refs = [], []
    for number, changes in enumerate(patches, 1):
        ref = "refs/seldon/snapshot-" + uuid.uuid4().hex
        refs.append(ref)
        stream.extend([f"commit {ref}", f"mark :{number}",
                       "committer Seldon <seldon@local> now", "data 16", "Seldon candidate",
                       f"from {parent}"])
        for path, mode, oid in changes:
            name = json.dumps(path, ensure_ascii=False)
            stream.append(f"D {name}" if mode == "000000" else f"M {mode} {oid} {name}")
        stream.extend(["", f"get-mark :{number}"])
    for ref in refs:
        stream.extend([f"reset {ref}", ""])
    stream.append("done\n")
    return git(root, "fast-import", "--quiet", "--date-format=now", "--done",
               data="\n".join(stream).encode("utf-8")).splitlines()


def integrated(project, scratch, base, product, current):
    if base == current:
        return product
    # Git writes the merge tree directly; an unresolved path cannot become a candidate tree.
    try:
        tree = git(project, "-c", "merge.renames=false", "merge-tree", "--write-tree",
                   "--merge-base", base, current, product).splitlines()[0]
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
                 "--ignored=matching", env={"GIT_OPTIONAL_LOCKS": "0"}).splitlines()
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
