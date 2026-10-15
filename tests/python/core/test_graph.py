"""Manifest loading and dispatch readiness for the work graph."""

import hashlib
import json
from pathlib import Path

import pytest

from codex_harness.core.graph import (
    PlanError,
    load_plan,
    ready_beads,
    topological_order,
    waves,
)
from codex_harness.core.graph import (
    test_dir as manifest_test_dir,
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


def test_real_fixture() -> None:
    plan = load_plan(FIXTURE)
    assert plan == load_plan(FIXTURE / "sample.json")
    assert plan.name == "dogfood-tipsplit"
    assert plan.root == FIXTURE
    assert plan.validation == ("-B", "-m", "unittest", "discover", "-s", "tests", "-p")
    assert plan.requirement_files == ("VISION.md", "PLAN.md", "tests")
    assert list(plan.beads) == ["A", "B"]
    assert plan.beads["A"].provider == "codex"
    assert plan.beads["A"].owned_files == ("tipsplit/money.py",)
    assert plan.beads["A"].requirements == ("R1", "R2", "R3")
    assert plan.beads["B"].depends_on == ("A",)
    assert plan.integration_requirements == ("R6",)
    assert plan.integration_proof_module == "test_end_to_end.py"
    assert topological_order(plan) == ["A", "B"]
    assert ready_beads(plan, []) == ["A"]
    assert ready_beads(plan, ["A"]) == ["B"]
    assert ready_beads(plan, [], ["A"]) == []
    assert ready_beads(plan, ["A"], ["B"]) == []
    assert ready_beads(plan, ["A", "B"]) == []
    assert len(plan.plan_revision) == 64
    assert plan.beads["A"].requirement_refs == ()
    assert plan.beads["A"].spec_refs is None


def test_waves_diamond(tmp_path):
    manifest = _manifest("D", "C", "A", "B")
    for key, deps in {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]}.items():
        manifest["beads"][key]["dependsOn"] = deps
    assert waves(load_plan(_write(tmp_path, manifest))) == [["A"], ["C", "B"], ["D"]]


@pytest.mark.parametrize(
    "value",
    [
        "..",
        "../tests",
        "tests/../other",
        "/tests",
        "C:\\tests",
        "\\\\server\\tests",
        "",
        " ",
        ".",
        "-p",
        None,
    ],
)
def test_invalid_validation_test_directory(tmp_path, value):
    manifest = _manifest("A")
    manifest["validation"] = ["-m", "unittest", "discover", "-s"]
    if value is not None:
        manifest["validation"].append(value)
    with pytest.raises(PlanError, match="test directory"):
        load_plan(_write(tmp_path, manifest))


def test_manifest_test_directory_default_and_plan(tmp_path):
    manifest = _manifest("A")
    assert manifest_test_dir(manifest) == "tests"
    manifest["validation"] += ["discover", "-s", "tests_cli", "-p"]
    assert manifest_test_dir(manifest) == "tests_cli"
    assert manifest_test_dir(load_plan(_write(tmp_path, manifest))) == "tests_cli"


@pytest.mark.parametrize(
    "arguments",
    [
        ["-s", "safe", "-s", "other"],
        ["-s", "safe", "--start-directory", "other"],
        ["--start-directory=safe", "-sother"],
        ["-s", "safe", "-t", "."],
        ["-s", "safe", "--top-level-directory=other"],
        ["-s", "safe", "-tother"],
        ["-s", "safe", "-vsother"],
        ["-s", "safe", "--start-dir", "other"],
        ["-s", "safe", "-vtother"],
        ["other", "-s", "safe"],
    ],
)
def test_ambiguous_discovery_rejected_at_compile(tmp_path, arguments):
    manifest = _manifest("A")
    manifest["validation"] = ["-m", "unittest", "discover", *arguments]
    with pytest.raises(PlanError, match="discovery"):
        load_plan(_write(tmp_path, manifest))


@pytest.mark.parametrize(
    "arguments",
    [
        ["--start-directory", "checks"],
        ["--start-directory=checks"],
        ["-schecks"],
    ],
)
def test_discovery_start_directory_forms_share_protection(tmp_path, arguments):
    manifest = _manifest("A")
    manifest["validation"] = ["-m", "unittest", "discover", *arguments]
    manifest["requirementFiles"] = ["VISION.md"]
    assert manifest_test_dir(manifest) == "checks"
    manifest["beads"]["A"]["ownedFiles"] = ["checks/test_A.py"]
    with pytest.raises(PlanError) as error:
        load_plan(_write(tmp_path, manifest))
    assert error.value.code == "plan-owned-file-protected"


@pytest.mark.parametrize("owned", ["tests_cli", "tests_cli/test_A.py", "tests_cli\\test_A.py"])
def test_manifest_tests_protected_without_requirement_entry(tmp_path, owned):
    manifest = _manifest("A")
    manifest["validation"] += ["discover", "-s", "tests_cli", "-p"]
    manifest["requirementFiles"] = ["VISION.md"]
    manifest["beads"]["A"]["ownedFiles"] = [owned]
    with pytest.raises(PlanError) as caught:
        load_plan(_write(tmp_path, manifest))
    assert caught.value.code == "plan-owned-file-protected"


@pytest.mark.parametrize("schema", ["v1", "v2"])
def test_optional_spec_refs_load_for_both_versions(tmp_path: Path, schema: str) -> None:
    manifest = _manifest("A")
    manifest["schema"] = f"codex-harness/provider-sample-{schema}"
    manifest["beads"]["A"]["specRefs"] = [
        {"id": "D3", "kind": "decision", "revision": "c" * 64},
        {"id": "design", "kind": "design", "revision": "d" * 64},
        {"id": "R1", "kind": "requirement", "revision": "b" * 64},
    ]
    assert load_plan(_write(tmp_path, manifest)).beads["A"].spec_refs == (
        ("D3", "decision", "c" * 64),
        ("design", "design", "d" * 64),
        ("R1", "requirement", "b" * 64),
    )


@pytest.mark.parametrize(
    "refs",
    [
        None,
        ["D3"],
        [{"id": "D3", "revision": "a" * 64}],
        [{"id": "D3", "kind": "vision", "revision": "a" * 64}],
        [{"id": "D3", "kind": "decision", "revision": "wrong"}],
        [{"id": "D3", "kind": "decision", "revision": "a" * 64}] * 2,
    ],
)
def test_invalid_spec_refs(tmp_path: Path, refs) -> None:
    manifest = _manifest("A")
    manifest["beads"]["A"]["specRefs"] = refs
    with pytest.raises(PlanError) as caught:
        load_plan(_write(tmp_path, manifest))
    assert caught.value.code == "plan-schema-invalid"


def test_v2_revision_and_requirement_refs(tmp_path: Path) -> None:
    manifest = _manifest("A")
    manifest["planRevision"] = "a" * 64
    manifest["beads"]["A"]["requirementRefs"] = [{"id": "R1", "revision": "b" * 64}]
    plan = load_plan(_write(tmp_path, manifest))
    assert plan.plan_revision == manifest["planRevision"]
    assert plan.beads["A"].requirement_refs == (("R1", "b" * 64),)
    assert plan.beads["A"].requirements == ("R1",)


def test_v1_revision_is_canonical_and_content_sensitive(tmp_path: Path) -> None:
    manifest = _manifest("A")
    path = _write(tmp_path, manifest)
    revision = load_plan(path).plan_revision
    assert (
        revision
        == hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    )
    path.write_text(json.dumps(manifest, sort_keys=True, indent=4))
    assert load_plan(path).plan_revision == revision
    manifest["beads"]["A"]["title"] = "Revised bead A"
    assert load_plan(_write(tmp_path, manifest)).plan_revision != revision


@pytest.mark.parametrize(
    "refs",
    [
        None,
        ["R1"],
        [{"id": "R1"}],
        [{"id": "R1", "revision": "wrong"}],
        [{"id": "R2", "revision": "b" * 64}],
    ],
)
def test_invalid_requirement_refs(tmp_path: Path, refs) -> None:
    manifest = _manifest("A")
    manifest["beads"]["A"]["requirementRefs"] = refs
    with pytest.raises(PlanError) as caught:
        load_plan(_write(tmp_path, manifest))
    assert caught.value.code == "plan-schema-invalid"


@pytest.mark.parametrize("field", ["sample", "validation", "requirementFiles", "beads"])
def test_missing_plan_fields(tmp_path: Path, field: str) -> None:
    manifest = _manifest("A")
    del manifest[field]
    with pytest.raises(PlanError) as caught:
        load_plan(_write(tmp_path, manifest))
    assert caught.value.code == "plan-schema-invalid"


@pytest.mark.parametrize("field", ["title", "ownedFiles", "requirements", "proofModule"])
def test_missing_bead_fields(tmp_path: Path, field: str) -> None:
    manifest = _manifest("A")
    del manifest["beads"]["A"][field]
    with pytest.raises(PlanError) as caught:
        load_plan(_write(tmp_path, manifest))
    assert caught.value.code == "plan-schema-invalid"


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


@pytest.mark.parametrize("transitive", [False, True])
def test_ordered_ownership_overlap(tmp_path: Path, transitive: bool) -> None:
    manifest = _manifest("C", "B", "A")
    manifest["beads"]["C"]["ownedFiles"] = ["src/A.py"]
    manifest["beads"]["B"]["dependsOn"] = ["A"]
    manifest["beads"]["C"]["dependsOn"] = ["B" if transitive else "A"]
    expected = ["A", "B", "C"] if transitive else ["A", "C", "B"]
    assert topological_order(load_plan(_write(tmp_path, manifest))) == expected


@pytest.mark.parametrize("owned", ["tests/test.py", "tests\\test.py", "tests", "VISION.md"])
def test_protected_ownership(tmp_path: Path, owned: str) -> None:
    manifest = _manifest("A")
    manifest["beads"]["A"]["ownedFiles"] = [owned]
    with pytest.raises(PlanError) as caught:
        load_plan(_write(tmp_path, manifest))
    assert caught.value.code == "plan-owned-file-protected"


def test_manifest_order_breaks_independent_ties(tmp_path: Path) -> None:
    plan = load_plan(_write(tmp_path, _manifest("C", "A", "B")))
    assert topological_order(plan) == ["C", "A", "B"]
    assert ready_beads(plan, iter(()), iter(())) == ["C", "A", "B"]
    assert plan.beads["C"].depends_on == ()
    assert plan.beads["C"].provider is None
    assert plan.integration_requirements == ()
    assert plan.integration_proof_module is None


@pytest.mark.parametrize("content", ["{", "[]", '{"beads": []}'])
def test_invalid_json_or_shape(tmp_path: Path, content: str) -> None:
    path = tmp_path / "sample.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(PlanError) as caught:
        load_plan(path)
    assert caught.value.code == "plan-schema-invalid"


@pytest.mark.parametrize("owned", ["../tests/test.py", "/tests/test.py", "C:\\tests\\test.py"])
def test_paths_must_be_relative(tmp_path: Path, owned: str) -> None:
    manifest = _manifest("A")
    manifest["beads"]["A"]["ownedFiles"] = [owned]
    with pytest.raises(PlanError) as caught:
        load_plan(_write(tmp_path, manifest))
    assert caught.value.code == "plan-schema-invalid"
