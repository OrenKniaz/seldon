"""Steering against real committed integration ranges, never the working tree."""

import subprocess
from pathlib import Path

import pytest

from codex_harness.core.spine import Spine, compile_manifest, load_spine
from codex_harness.core.spine_render import render_markdown
from codex_harness.core.steering import has_blockers, render_steering_card, steering_report

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


def test_clean_change_uses_committed_content_and_is_read_only(repository):
    repo, base = repository
    head = commit(repo, {"product.py": "def run():\n    return 2\n"})
    (repo / "product.py").write_text("def run():\n    pass\n", encoding="utf-8")
    before = git(repo, "status", "--porcelain")
    result = steering_report(repo=repo, base_commit=base, head_commit=head, manifest=manifest())
    assert result == {"findings": [], "summary": {"blocker": 0, "major": 0, "minor": 0, "total": 0}}
    assert not has_blockers(result)
    assert render_steering_card(result) == "clean"
    assert git(repo, "status", "--porcelain") == before


@pytest.mark.parametrize(
    "body", ["pass", "...", "raise NotImplementedError", "raise NotImplementedError('later')"]
)
def test_public_stub_blocks(repository, body):
    result = report(repository, {"product.py": f'def run():\n    """Public API."""\n    {body}\n'})
    assert result["findings"][0] == {
        "code": "stub-left",
        "severity": "blocker",
        "bead": "A",
        "path": "product.py",
        "line": 1,
        "detail": "Function run contains a placeholder implementation",
        "requirementIds": ["R1"],
    }
    assert has_blockers(result)


def test_private_stub_major_and_methods_block(repository):
    result = report(
        repository,
        {
            "product.py": (
                "def _helper():\n    pass\nclass Service:\n    async def run(self):\n        ...\n"
            )
        },
    )
    assert [(item["line"], item["severity"]) for item in result["findings"]] == [
        (4, "blocker"),
        (1, "major"),
    ]


def test_private_requirement_named_stub_blocks(repository):
    spine = load_spine(FIXTURE)
    data = spine.to_dict()
    next(node for node in data["nodes"] if node["id"] == "R1")["text"] = "_helper returns cents"
    from codex_harness.core.spine import Spine

    spine = Spine(data)
    result = report(repository, {"product.py": "def _helper():\n    pass\n"}, spine=spine)
    stub = next(item for item in result["findings"] if item["code"] == "stub-left")
    assert stub["severity"] == "blocker"


@pytest.mark.parametrize("marker", ["TODO", "FIXME", "XXX"])
def test_added_comments_major(repository, marker):
    result = report(
        repository, {"product.py": f"# {marker}: finish validation\ndef run():\n    return 2\n"}
    )
    assert [item["severity"] for item in result["findings"]] == ["major"]
    assert result["findings"][0]["code"] == "stub-left"


def test_placeholder_return_with_comment_blocks(repository):
    result = report(repository, {"product.py": "def run():\n    return None  # TODO: implement\n"})
    assert result["summary"] == {"blocker": 1, "major": 1, "minor": 0, "total": 2}


def test_existing_comment_and_markers_in_strings_not_reported(repository):
    repo, _ = repository
    base = commit(repo, {"product.py": '# TODO: historic\nmessage = "FIXME XXX"\nvalue = 1\n'})
    result = report(
        (repo, base), {"product.py": '# TODO: historic\nmessage = "FIXME XXX"\nvalue = 2\n'}
    )
    assert result["findings"] == []


@pytest.mark.parametrize(
    "source",
    [
        "import unittest.mock\n",
        "from unittest import mock\n",
        "import mock\n",
        "from unittest.mock import patch\n",
        "fake_data = 4\n",
        "def dummy_result():\n    return 4\n",
        "class Service:\n    def run(self):\n        self.stub_result = 4\n",
    ],
)
def test_mock_in_product(repository, source):
    result = report(repository, {"product.py": source})
    assert [(item["code"], item["severity"]) for item in result["findings"]] == [
        ("mock-in-product", "major")
    ]


def test_test_code_may_import_mocks(repository):
    owned = manifest()
    owned["beads"]["A"]["ownedFiles"] = ["test_helpers.py"]
    result = report(
        repository, {"test_helpers.py": "from unittest.mock import patch\n"}, manifest=owned
    )
    assert result["findings"] == []


@pytest.mark.parametrize("path", ["tests/test_product.py", "REQUIREMENTS.md"])
@pytest.mark.parametrize("content", ["changed\n", None])
def test_requirement_edits_and_deletions_block(repository, path, content):
    result = report(repository, {path: content})
    assert result["findings"][0]["code"] == "test-weakened"
    assert result["findings"][0]["severity"] == "blocker"
    assert result["summary"]["total"] == 1


def test_ownership_drift_and_requirement_prefix_boundary(repository):
    result = report(repository, {"other.py": "value = 2\n", "tests_extra.py": "value = 2\n"})
    assert [item["code"] for item in result["findings"]] == ["ownership-drift"] * 2


def test_rename_out_of_ownership_is_detected(repository):
    result = report(repository, {"product.py": None, "other.py": "def run():\n    return 1\n"})
    assert [item["code"] for item in result["findings"]] == ["ownership-drift"]


def test_real_spine_missing_cli_proof_and_unlinked_test(repository):
    repo, _ = repository
    spine = load_spine(FIXTURE)
    base = commit(
        repo,
        {
            "tests/test_product.py": None,
            "tests/test_money.py": "def test_money():\n    assert True\n",
            "tests/test_end_to_end.py": "def test_e2e():\n    assert True\n",
            "tests/test_unmapped.py": "def test_extra():\n    assert True\n",
            **render_markdown(spine),
        },
    )
    result = steering_report(repo=repo, base_commit=base, head_commit=base, spine=spine)
    assert result["summary"] == {"blocker": 0, "major": 2, "minor": 1, "total": 3}
    missing = [item for item in result["findings"] if item["code"] == "requirement-without-proof"]
    assert [item["requirementIds"] for item in missing] == [["R4"], ["R5"]]
    assert all("test_cli.py" in item["detail"] for item in missing)
    assert result["findings"][-1]["path"] == "tests/test_unmapped.py"
    # Working-tree presence cannot supply committed proof.
    (repo / "tests/test_cli.py").write_text("pass\n", encoding="utf-8")
    assert steering_report(repo=repo, base_commit=base, head_commit=base, spine=spine) == result
    head = commit(repo, {"tests/test_cli.py": "def test_cli():\n    assert True\n"})
    complete = steering_report(
        repo=repo, base_commit=head, head_commit=head, manifest=compile_manifest(spine), spine=spine
    )
    assert [item["code"] for item in complete["findings"]] == ["unlinked-test"]


def test_card_is_deterministic_and_grouped(repository):
    result = report(
        repository,
        {
            "product.py": "def run():\n    raise NotImplementedError\n",
            "other.py": "value = 3\n",
        },
    )
    expected = (
        "steering needed: 1 blockers, 1 major\n\nblocker (1)\n"
        "- stub-left [product.py:1]: Function run contains a placeholder implementation\n\n"
        "major (1)\n- ownership-drift [other.py]: Changed file has no bead owner"
    )
    assert render_steering_card(result) == expected
    assert (
        render_steering_card({**result, "findings": list(reversed(result["findings"]))}) == expected
    )


def test_invalid_commit_does_not_report_clean(repository):
    repo, base = repository
    with pytest.raises(subprocess.CalledProcessError):
        steering_report(repo=repo, base_commit=base, head_commit="missing", manifest=manifest())


@pytest.mark.parametrize("path", ["PLAN.md", "VISION.md", "REQUIREMENTS.md", "TRACE.md"])
def test_doc_drift_with_current_revision_is_major_and_read_only(repository, path):
    repo, _ = repository
    spine = load_spine(FIXTURE)
    docs = render_markdown(spine)
    docs[path] += "Edited outside the spine.\n"
    head = commit(repo, docs)
    (repo / path).write_text(render_markdown(spine)[path], encoding="utf-8")
    result = steering_report(repo=repo, base_commit=head, head_commit=head, spine=spine)
    finding = next(item for item in result["findings"] if item["code"] == "doc-drift")
    assert finding["path"] == path
    assert finding["severity"] == "major"
    assert finding["line"] == len(render_markdown(spine)[path].splitlines()) + 1


def test_stale_plan_after_decision_change_blocks_until_regenerated(repository):
    repo, _ = repository
    spine = load_spine(FIXTURE)
    head = commit(repo, render_markdown(spine))
    data = spine.to_dict()
    next(node for node in data["nodes"] if node["id"] == "D3")["text"] = "Tip always rounds up."
    revised = Spine(data)
    result = steering_report(repo=repo, base_commit=head, head_commit=head, spine=revised)
    drift = [item for item in result["findings"] if item["code"] == "doc-drift"]
    assert [(item["path"], item["severity"], item["line"]) for item in drift] == [
        ("PLAN.md", "blocker", 1),
        ("VISION.md", "blocker", 1),
    ]
    regenerated = commit(repo, render_markdown(revised))
    result = steering_report(
        repo=repo, base_commit=regenerated, head_commit=regenerated, spine=revised
    )
    assert not any(item["code"] == "doc-drift" for item in result["findings"])
