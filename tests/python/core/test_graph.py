"""Manifest loading and dispatch readiness for the work graph."""

import json
from pathlib import Path

import pytest

from codex_harness.core.graph import (
    PlanError,
    load_plan,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit"


def _manifest(*ids: str) -> dict:
    return {
        "sample": "test-plan",
        "validation": ["-m", "unittest"],
        "requirementFiles": ["VISION.md", "tests/"],
        "beads": {
            bead_id: {
                "title": f"Bead {bead_id}",
                "ownedFiles": [f"src/{bead_id}.py"],
                "requirements": ["R1"],
                "proofModule": "test_core.py",
            }
            for bead_id in ids
        },
    }


def _write(tmp_path: Path, manifest: dict) -> Path:
    path = tmp_path / "sample.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("dependencies", "code"),
    [
        ({"A": ["missing"]}, "plan-unknown-dependency"),
        ({"A": ["B"], "B": ["A"]}, "plan-dependency-cycle"),
        ({"A": ["A"]}, "plan-dependency-cycle"),
    ],
)
def test_invalid_dependencies(tmp_path: Path, dependencies: dict, code: str) -> None:
    manifest = _manifest("A", "B")
    for bead_id, values in dependencies.items():
        manifest["beads"][bead_id]["dependsOn"] = values
    with pytest.raises(PlanError) as caught:
        load_plan(_write(tmp_path, manifest))
    assert caught.value.code == code


def test_unordered_ownership_overlap(tmp_path: Path) -> None:
    manifest = _manifest("A", "B")
    manifest["beads"]["B"]["ownedFiles"] = ["src/./A.py"]
    with pytest.raises(PlanError) as caught:
        load_plan(_write(tmp_path, manifest))
    assert caught.value.code == "plan-ownership-overlap"


@pytest.mark.parametrize("owned", ["tests/test.py", "tests\\test.py", "tests", "VISION.md"])
def test_protected_ownership(tmp_path: Path, owned: str) -> None:
    manifest = _manifest("A")
    manifest["beads"]["A"]["ownedFiles"] = [owned]
    with pytest.raises(PlanError) as caught:
        load_plan(_write(tmp_path, manifest))
    assert caught.value.code == "plan-owned-file-protected"


@pytest.mark.parametrize("owned", ["../tests/test.py", "/tests/test.py", "C:\\tests\\test.py"])
def test_paths_must_be_relative(tmp_path: Path, owned: str) -> None:
    manifest = _manifest("A")
    manifest["beads"]["A"]["ownedFiles"] = [owned]
    with pytest.raises(PlanError) as caught:
        load_plan(_write(tmp_path, manifest))
    assert caught.value.code == "plan-schema-invalid"
