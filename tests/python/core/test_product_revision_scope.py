"""Real Git fixtures for the declared acceptance product's revision scope."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from codex_harness.core.acceptance import _integrated_revision_v1, integrated_revision


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def git_parent(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    git(parent, "init", "-q")
    (parent / ".gitignore").write_text("/work/\nignored/\n", encoding="utf-8")
    git(parent, "add", ".gitignore")
    git(
        parent,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "disposable scope fixture",
    )
    return parent


@pytest.mark.parametrize("revision", [integrated_revision, _integrated_revision_v1])
def test_ignored_ancestor_plain_product_binds_actual_bytes(tmp_path, revision):
    parent = git_parent(tmp_path)
    product = parent / "work" / "product"
    product.mkdir(parents=True)
    source = product / "tiny.py"
    source.write_text("print(1)\n", encoding="utf-8")
    assert not (product / ".git").exists()
    assert Path(git(product, "rev-parse", "--show-toplevel")) == parent
    assert git(product, "ls-files", "--cached", "--others", "--exclude-standard") == ""
    before = revision(product)

    source.write_text("raise SystemExit(2)\n", encoding="utf-8")

    assert revision(product) != before

    before = revision(product)
    (product / "new-code.py").write_text("print('new')\n", encoding="utf-8")
    assert revision(product) != before
    before = revision(product)
    (product / "__pycache__").mkdir()
    (product / "__pycache__" / "cache.pyc").write_bytes(b"cache")
    (parent / "unrelated.py").write_text("outside the product\n", encoding="utf-8")
    assert revision(product) == before


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("revision", [integrated_revision, _integrated_revision_v1])
@pytest.mark.parametrize("worktree", [False, True])
def test_product_git_root_and_gitfile_keep_digest_and_ignore_contract(tmp_path, revision, worktree):
    parent = git_parent(tmp_path)
    (parent / "app.py").write_text("print('tracked')\n", encoding="utf-8")
    git(parent, "add", "app.py")
    git(
        parent,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "tracked product",
    )
    product = tmp_path / "linked" if worktree else parent
    if worktree:
        git(parent, "worktree", "add", "--detach", str(product), "HEAD")
        assert (product / ".git").is_file()
    else:
        assert (product / ".git").is_dir()
    head = git(product, "rev-parse", "HEAD")
    files = [
        (name, hashlib.sha256((product / name).read_bytes()).hexdigest())
        for name in (".gitignore", "app.py")
    ]
    expected = (
        {"algorithm": "tree-bytes-v2", "head": head, "files": files}
        if revision is integrated_revision
        else {"head": head, "diff": hashlib.sha256(b"").hexdigest(), "files": []}
    )
    assert revision(product) == _digest(expected)
    before = revision(product)
    (product / "ignored").mkdir()
    (product / "ignored/cache.py").write_text("ignored\n", encoding="utf-8")
    assert revision(product) == before
    (product / "app.py").write_text("print('changed')\n", encoding="utf-8")
    assert revision(product) != before
    before = revision(product)
    (product / "new-code.py").write_text("print('untracked')\n", encoding="utf-8")
    assert revision(product) != before


@pytest.mark.parametrize("revision", [integrated_revision, _integrated_revision_v1])
def test_broken_product_git_marker_refuses_instead_of_falling_back(tmp_path, revision):
    product = tmp_path / "product"
    product.mkdir()
    (product / ".git").write_text("gitdir: missing-directory\n", encoding="utf-8")
    (product / "app.py").write_text("print(1)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot determine product Git scope"):
        revision(product)
