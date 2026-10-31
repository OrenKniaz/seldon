"""Canonical tip-split ancestry through rendered plans and the real graph consumer."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from codex_harness.core.graph import load_plan
from codex_harness.core.spine import (
    MARKDOWN_FILES,
    SCHEMA,
    Spine,
    SpineError,
    compile_manifest,
    load_spine,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit"


def _without_refs(beads):
    """Compare beads ignoring requirement refs, which only v2 manifests carry."""

    return {key: replace(bead, requirement_refs=(), spec_refs=None) for key, bead in beads.items()}


def _data(*nodes: dict) -> dict:
    return {"schema": SCHEMA, "name": "test", "nodes": list(nodes)}


def _fixture_data() -> dict:
    return json.loads((FIXTURE / "spine.json").read_text(encoding="utf-8"))


def _edit(data: dict, node_id: str) -> dict:
    return next(node for node in data["nodes"] if node["id"] == node_id)


def test_compile_fixture_through_real_graph_consumer(tmp_path: Path) -> None:
    spine = load_spine(FIXTURE / "spine.json")
    manifest = compile_manifest(spine)
    path = tmp_path / "sample.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    actual, expected = load_plan(path), load_plan(FIXTURE)
    assert actual.name == expected.name
    assert actual.validation == expected.validation
    assert set(actual.requirement_files) == set(expected.requirement_files) | set(MARKDOWN_FILES)
    assert _without_refs(actual.beads) == _without_refs(expected.beads)
    assert all(bead.requirement_refs for bead in actual.beads.values())
    assert all(bead.spec_refs for bead in actual.beads.values())
    assert actual.integration_proof_module == expected.integration_proof_module
    assert actual.integration_requirements == expected.integration_requirements
    sample = json.loads((FIXTURE / "sample.json").read_text(encoding="utf-8"))
    assert manifest["beads"]["A"]["lessons"] == sample["beads"]["A"]["lessons"]
    assert manifest["planRevision"] == spine.plan_revision()
    for node_id in spine.ids("bead"):
        assert manifest["beads"][node_id]["requirementRefs"] == [
            {"id": key, "revision": spine.revision(key)}
            for key in sample["beads"][node_id]["requirements"]
        ]
    assert spine.coverage_gaps() == [{"id": "R6", "gap": "requirement-without-bead"}]


def test_compile_spec_refs_cover_all_spec_ancestors_and_isolate_d3_change() -> None:
    data = _fixture_data()
    old = Spine(data)
    before = compile_manifest(old)
    for bead_id in old.ids("bead"):
        assert before["beads"][bead_id]["specRefs"] == [
            {"id": key, "kind": old.node(key)["kind"], "revision": old.revision(key)}
            for key in old.ancestors(bead_id)
            if old.node(key)["kind"] in {"decision", "design", "requirement"}
        ]
    _edit(data, "D3")["text"] += " Revised rounding rule."
    after = compile_manifest(Spine(data))
    assert after["beads"]["A"]["specRefs"] != before["beads"]["A"]["specRefs"]
    assert after["beads"]["B"]["specRefs"] == before["beads"]["B"]["specRefs"]
    assert after["beads"]["A"]["requirementRefs"] == before["beads"]["A"]["requirementRefs"]


@pytest.mark.parametrize(
    "name", ["../outside", "C:/outside", "/outside", ".git/config", "reference/source.py"]
)
def test_compile_requirement_revision_rejects_unsafe_paths(tmp_path: Path, name: str) -> None:
    data = _fixture_data()
    _edit(data, "V1")["fields"]["requirementFiles"] = [name]
    with pytest.raises(SpineError) as caught:
        compile_manifest(Spine(data), tmp_path)
    assert caught.value.code == "spine-requirement-path-invalid"
