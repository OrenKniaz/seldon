"""Steering against real committed integration ranges, never the working tree."""

import subprocess
from pathlib import Path

import pytest

from codex_harness.core.steering import steering_report

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(repo, files):
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    git(repo, "add", "--all")
    git(
        repo,
        "-c",
        "user.name=Steering Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-qm",
        "snapshot",
    )
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    base = commit(
        tmp_path,
        {
            "product.py": "def run():\n    return 1\n",
            "tests/test_product.py": "def test_run():\n    assert True\n",
            "REQUIREMENTS.md": "run must work\n",
        },
    )
    return tmp_path, base


def manifest():
    return {
        "requirementFiles": ["tests/", "REQUIREMENTS.md"],
        "beads": {"A": {"ownedFiles": ["product.py"], "requirements": ["R1"]}},
    }


def report(repository, files, **kwargs):
    repo, base = repository
    head = commit(repo, files)
    return steering_report(
        repo=repo,
        base_commit=base,
        head_commit=head,
        manifest=kwargs.pop("manifest", manifest()),
        **kwargs,
    )
