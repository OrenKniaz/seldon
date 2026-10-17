"""Canonical tip-split ancestry through rendered plans and the real graph consumer."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
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
    diff,
    impact,
    load_spine,
    save_spine,
)
from codex_harness.core.spine_render import render_markdown

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit"


def _without_refs(beads):
    """Compare beads ignoring requirement refs, which only v2 manifests carry."""

    return {key: replace(bead, requirement_refs=(), spec_refs=None) for key, bead in beads.items()}


def _node(node_id: str, kind: str, parents: list[str] | None = None, **fields) -> dict:
    return {
        "id": node_id,
        "kind": kind,
        "title": node_id,
        "text": f"Text for {node_id}",
        "parents": parents or [],
        "fields": fields,
    }


def _data(*nodes: dict) -> dict:
    return {"schema": SCHEMA, "name": "test", "nodes": list(nodes)}


def _fixture_data() -> dict:
    return json.loads((FIXTURE / "spine.json").read_text(encoding="utf-8"))


def _edit(data: dict, node_id: str) -> dict:
    return next(node for node in data["nodes"] if node["id"] == node_id)


@pytest.mark.parametrize("parent", ["V1", "F1"])
def test_acceptance_load_revision_and_terminal_impact(parent, tmp_path):
    data = _data(
        _node("V1", "vision"),
        _node("F1", "feature", ["V1"]),
        _node("AC1", "acceptance", [parent], steps=["python --version"]),
    )
    old = Spine(data)
    path = tmp_path / "spine.json"
    save_spine(old, path)
    assert load_spine(path).to_dict() == old.to_dict()
    data["nodes"].reverse()
    assert Spine(data).plan_revision() == old.plan_revision()
    _edit(data, parent)["text"] += " changed"
    assert "AC1" in impact(old, Spine(data))["affected"]
    data = old.to_dict()
    _edit(data, "AC1")["fields"]["steps"].append("python -V")
    new = Spine(data)
    assert old.revision("AC1") != new.revision("AC1")
    assert impact(old, new)["affected"] == ["AC1"]
    assert impact(old, new)["staleDocs"] == ["REQUIREMENTS.md"]
    assert old.children("AC1") == []


@pytest.mark.parametrize("fields", [{}, {"steps": []}])
def test_acceptance_without_steps_is_gap(fields):
    old = Spine(_data(_node("V1", "vision"), _node("AC1", "acceptance", ["V1"], **fields)))
    assert old.coverage_gaps() == [
        {"id": "AC1", "gap": "acceptance scenario has no executable steps"}
    ]
    data = old.to_dict()
    _edit(data, "AC1")["fields"]["steps"] = ["python -V"]
    new = Spine(data)
    assert new.coverage_gaps() == []
    assert impact(old, new)["staleDocs"] == ["REQUIREMENTS.md", "TRACE.md"]


def test_acceptance_rejects_other_parent_kinds_and_downstream_nodes():
    data = _data(
        _node("V1", "vision"),
        _node("F1", "feature", ["V1"]),
        _node("S1", "story", ["F1"]),
        _node("AC1", "acceptance", ["S1"], steps=["python -V"]),
    )
    with pytest.raises(SpineError, match="Invalid parent kind"):
        Spine(data)
    _edit(data, "AC1")["parents"] = ["V1"]
    _edit(data, "S1")["parents"] = ["AC1"]
    with pytest.raises(SpineError, match="Invalid parent kind"):
        Spine(data)


@pytest.mark.parametrize(
    ("nodes", "code"),
    [
        ([_node("V1", "vision"), _node("V1", "vision")], "spine-duplicate-id"),
        ([_node("V1", "vision"), _node("F1", "feature", ["missing"])], "spine-unknown-parent"),
        (
            [_node("V1", "vision"), _node("R1", "requirement", ["V1"])],
            "spine-parent-kind-not-allowed",
        ),
        (
            [_node("V1", "vision"), _node("F1", "feature", ["F2"]), _node("F2", "feature", ["F1"])],
            "spine-cycle",
        ),
        ([], "spine-vision-count"),
        ([_node("V1", "vision"), _node("V2", "vision")], "spine-vision-count"),
        ([_node("V1", "vision"), _node("A", "bead")], "spine-bead-fields-invalid"),
        (
            [_node("V1", "vision"), _node("test", "test", module="test.py", level="other")],
            "spine-test-fields-invalid",
        ),
    ],
)
def test_validation_codes(nodes: list[dict], code: str, tmp_path: Path) -> None:
    path = tmp_path / "spine.json"
    path.write_text(json.dumps(_data(*nodes)), encoding="utf-8")
    with pytest.raises(SpineError) as caught:
        load_spine(path)
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ownedFiles", "money.py"),
        ("ownedFiles", []),
        ("proofModule", ""),
        ("dependsOn", ["missing"]),
        ("dependsOn", ["D1"]),
        ("dependsOn", ["A"]),
        ("lessons", ["missing"]),
        ("lessons", ["R1"]),
        ("provider", None),
    ],
)
def test_bead_field_boundaries(field: str, value: object) -> None:
    data = _fixture_data()
    _edit(data, "A")["fields"][field] = value
    with pytest.raises(SpineError) as caught:
        Spine(data)
    assert caught.value.code == "spine-bead-fields-invalid"


def test_bead_dependency_cycle() -> None:
    data = _fixture_data()
    _edit(data, "A")["fields"]["dependsOn"] = ["B"]
    with pytest.raises(SpineError) as caught:
        Spine(data)
    assert caught.value.code == "spine-bead-fields-invalid"


@pytest.mark.parametrize("content", ["{", "[]", '{"schema": "old", "nodes": []}'])
def test_schema_errors(content: str, tmp_path: Path) -> None:
    path = tmp_path / "spine.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SpineError) as caught:
        load_spine(path)
    assert caught.value.code == "spine-schema-invalid"


def test_navigation_all_paths_and_bidirectional_edges() -> None:
    spine = load_spine(FIXTURE / "spine.json")
    assert spine.node("A")["title"] == "Bead A: money core"
    for node_id in spine.ids():
        for parent in spine.parents(node_id):
            assert node_id in spine.children(parent)
        for child in spine.children(node_id):
            assert node_id in spine.parents(child)
    paths = spine.trace_up("A")
    assert ["A", "R1", "S1", "F1", "V1"] in paths
    assert ["A", "D3", "V1"] in paths
    assert len(paths) == 7
    assert all(path[0] == "A" and path[-1] == "V1" for path in paths)
    assert spine.trace_up("R6") == [["R6", "F1", "V1"], ["R6", "F2", "V1"]]
    assert spine.trace_up("V1") == [["V1"]]
    assert spine.descendants("D3") == ["A", "test_money"]
    assert spine.children("test_money") == []


def test_revision_stability_and_canonical_hash() -> None:
    data = _fixture_data()
    old = Spine(data)
    reordered = deepcopy(data)
    reordered["nodes"].reverse()
    for node in reordered["nodes"]:
        node["parents"].reverse()
    same = Spine(reordered)
    assert old.plan_revision() == same.plan_revision()
    assert diff(old, same) == {"added": [], "removed": [], "changed": []}
    node = old.node("A")
    canonical = json.dumps(node, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert old.revision("A") == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    lines = "\n".join(f"{key}:{old.revision(key)}" for key in old.ids())
    assert old.plan_revision() == hashlib.sha256(lines.encode("utf-8")).hexdigest()
    _edit(data, "D3")["text"] += " New rounding policy."
    new = Spine(data)
    assert old.revision("D3") != new.revision("D3")
    assert old.revision("A") == new.revision("A")  # Local content, not ancestor content.
    assert old.plan_revision() != new.plan_revision()
    node["text"] = "Attempted mutation"
    assert old.node("A")["text"] != node["text"]


def test_save_load_is_deterministic(tmp_path: Path) -> None:
    spine = load_spine(FIXTURE / "spine.json")
    path = tmp_path / "spine.json"
    save_spine(spine, path)
    first = path.read_bytes()
    save_spine(load_spine(path), path)
    assert path.read_bytes() == first
    assert b"\r\n" not in first
    assert load_spine(path).plan_revision() == spine.plan_revision()


def test_coverage_gaps_include_uncovered_requirement_and_orphans() -> None:
    spine = Spine(
        _data(
            _node("V1", "vision"),
            _node("F1", "feature", ["V1"]),
            _node("R1", "requirement", ["F1"]),
            _node(
                "A", "bead", ownedFiles=["a.py"], proofModule="test.py", dependsOn=[], lessons=[]
            ),
            _node("test", "test", module="test.py", level="unit"),
        )
    )
    assert spine.coverage_gaps() == [
        {"id": "A", "gap": "bead-without-vision"},
        {"id": "R1", "gap": "requirement-without-bead"},
        {"id": "R1", "gap": "requirement-without-test"},
        {"id": "test", "gap": "test-covering-nothing"},
    ]
    assert spine.trace_up("A") == []


def test_coverage_through_design_and_bead() -> None:
    spine = Spine(
        _data(
            _node("V1", "vision"),
            _node("F1", "feature", ["V1"]),
            _node("R1", "requirement", ["F1"]),
            _node("design", "design", ["R1"]),
            _node(
                "A",
                "bead",
                ["design"],
                ownedFiles=["a.py"],
                proofModule="test.py",
                dependsOn=[],
                lessons=[],
            ),
            _node("test", "test", ["A"], module="test.py", level="unit"),
        )
    )
    assert spine.coverage_gaps() == []
    assert compile_manifest_with_root(spine)["beads"]["A"]["requirements"] == ["R1"]


def compile_manifest_with_root(spine: Spine) -> dict:
    data = spine.to_dict()
    _edit(data, "V1")["fields"] = {"validation": [], "requirementFiles": []}
    return compile_manifest(Spine(data))


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


def test_compile_rejects_ambiguous_integration_and_missing_root_fields() -> None:
    data = _fixture_data()
    data["nodes"].append(_node("another_e2e", "test", ["R6"], module="other.py", level="e2e"))
    with pytest.raises(SpineError) as caught:
        compile_manifest(Spine(data))
    assert caught.value.code == "spine-integration-invalid"
    with pytest.raises(SpineError) as caught:
        compile_manifest(Spine(_data(_node("V1", "vision"))))
    assert caught.value.code == "spine-schema-invalid"


def test_tip_rounding_impact_and_exact_stale_documents(monkeypatch) -> None:
    data = _fixture_data()
    old = Spine(data)
    _edit(data, "D3")["text"] += " Changed rounding."
    new = Spine(data)
    old_docs, new_docs = render_markdown(old), render_markdown(new)
    # Impact must not render to discover document changes.
    monkeypatch.setattr(
        "codex_harness.core.spine_render.render_markdown",
        lambda _: pytest.fail("impact re-rendered the spine"),
    )
    result = impact(old, new)
    assert result["changed"] == ["D3"]
    assert result["affected"] == ["A", "D3", "test_money"]
    assert result["affectedBeads"] == ["A"]
    assert result["untouchedBeads"] == ["B"]
    assert result["affectedTests"] == ["test_money"]
    assert result["staleDocs"] == sorted(key for key in old_docs if old_docs[key] != new_docs[key])
    assert result["staleDocs"] == ["PLAN.md", "VISION.md"]


def test_added_removed_and_changed_impact_uses_both_graphs() -> None:
    data = _fixture_data()
    old = Spine(data)
    data["nodes"] = [node for node in data["nodes"] if node["id"] != "D3"]
    _edit(data, "A")["parents"].remove("D3")
    data["nodes"].append(_node("D6", "decision", ["V1"]))
    new = Spine(data)
    assert diff(old, new) == {"added": ["D6"], "removed": ["D3"], "changed": ["A"]}
    result = impact(old, new)
    assert result["affected"] == ["A", "D3", "D6", "test_money"]
    assert result["affectedBeads"] == ["A"]
    assert result["untouchedBeads"] == ["B"]


def test_retired_beads_and_tests_are_affected() -> None:
    data = _fixture_data()
    old = Spine(data)
    data["nodes"] = [node for node in data["nodes"] if node["id"] not in ("B", "test_cli")]
    result = impact(old, Spine(data))
    assert result["affectedBeads"] == ["B"]
    assert result["affectedTests"] == ["test_cli"]
    assert result["untouchedBeads"] == ["A"]


def test_unchanged_and_name_only_impact() -> None:
    data = _fixture_data()
    old = Spine(data)
    data["name"] = "renamed"
    new = Spine(data)
    assert impact(old, new) == {
        "changed": [],
        "affected": [],
        "affectedBeads": [],
        "affectedTests": [],
        "untouchedBeads": ["A", "B"],
        "staleDocs": [],
    }
    assert render_markdown(old) == render_markdown(new)
    assert compile_manifest(new)["sample"] == "renamed"


def test_rendering_is_deterministic_and_complete() -> None:
    data = _fixture_data()
    spine = Spine(data)
    documents = render_markdown(spine)
    assert documents == render_markdown(spine)
    data["nodes"].reverse()
    for node in data["nodes"]:
        node["parents"].reverse()
    assert documents == render_markdown(Spine(data))
    assert sorted(documents) == list(MARKDOWN_FILES)
    assert all(
        content.startswith("<!-- Generated from spine.json; document revision ")
        for content in documents.values()
    )
    assert "D3: Tip rounding" in documents["VISION.md"]
    assert documents["REQUIREMENTS.md"].count("R6: Integrated restaurant bill") == 2
    assert "R1@" + spine.revision("R1")[:12] in documents["PLAN.md"]
    assert "tipsplit/money.py" in documents["PLAN.md"]
    for bead_id in spine.ids("bead"):
        section = documents["PLAN.md"].split(f"## {bead_id}:", 1)[1].split("\n## ", 1)[0]
        for ancestor in spine.ancestors(bead_id):
            if spine.node(ancestor)["kind"] in {"requirement", "decision"}:
                assert spine.node(ancestor)["text"] in section
    assert "test_end_to_end.py" in documents["PLAN.md"]
    assert "test_end_to_end (e2e; test_end_to_end.py)" in documents["TRACE.md"]
    assert "## Gaps" in documents["TRACE.md"]
    assert "R6: requirement-without-bead" in documents["TRACE.md"]


def test_cli_render_compile_and_impact(tmp_path: Path) -> None:
    prefix = [sys.executable, "-B", "-m", "codex_harness.core.spine"]

    def snapshot(root: Path) -> dict[str, bytes | None]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
            for path in sorted(root.rglob("*"))
        }

    repository_before = snapshot(FIXTURE)
    reference = tmp_path / "reference"
    shutil.copytree(FIXTURE, reference)
    for name in MARKDOWN_FILES:
        path = reference / "template" / name
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    reference_before = snapshot(reference)
    workspace = tmp_path / "workspace"
    shutil.copytree(reference, workspace)
    source = workspace / "spine.json"
    outdir, manifest = tmp_path / "docs", tmp_path / "sample.json"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([*prefix, "render", str(source), str(outdir)], check=True, env=env)
    assert {path.name: path.read_text(encoding="utf-8") for path in outdir.iterdir()} == (
        render_markdown(load_spine(source))
    )
    subprocess.run([*prefix, "compile", str(source), str(manifest)], check=True, env=env)
    assert {name: (workspace / "template" / name).read_bytes() for name in MARKDOWN_FILES} == {
        name: content.encode("utf-8")
        for name, content in render_markdown(load_spine(source)).items()
    }
    assert _without_refs(load_plan(manifest).beads) == _without_refs(load_plan(FIXTURE).beads)
    result = subprocess.run(
        [*prefix, "impact", str(source), str(source)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert json.loads(result.stdout)["changed"] == []
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    result = subprocess.run(
        [*prefix, "compile", str(bad), str(manifest)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2
    assert "spine-schema-invalid" in result.stderr
    assert snapshot(FIXTURE) == repository_before
    assert snapshot(reference) == reference_before


def test_compile_requirement_content_revision_is_line_ending_neutral(tmp_path: Path) -> None:
    template = tmp_path / "template"
    shutil.copytree(FIXTURE / "template", template)
    spine = load_spine(FIXTURE / "spine.json")
    legacy = compile_manifest(spine)
    before = compile_manifest(spine, template)
    assert legacy["planRevision"] == spine.plan_revision()
    assert "requirementFilesRevision" not in legacy
    expected = hashlib.sha256(
        f"{spine.plan_revision()}:{before['requirementFilesRevision']}".encode()
    ).hexdigest()
    assert before["planRevision"] == expected
    proof = template / "tests/test_money.py"
    revised = proof.read_bytes().replace(b"\r\n", b"\n") + b"\n# revised requirement\n"
    proof.write_bytes(revised)
    after = compile_manifest(spine, template)
    assert after["planRevision"] != before["planRevision"]
    assert after["beads"] == before["beads"]
    proof.write_bytes(revised.replace(b"\n", b"\r\n"))
    assert compile_manifest(spine, template) == after
    (template / "tipsplit/money.py").write_text("unprotected change\n")
    assert compile_manifest(spine, template) == after
    proof.unlink()
    assert compile_manifest(spine, template)["planRevision"] != after["planRevision"]


def test_spine_compile_cli_template_default_and_override(tmp_path: Path) -> None:
    shutil.copytree(FIXTURE / "template", tmp_path / "template")
    spine = load_spine(FIXTURE / "spine.json")
    source, out = tmp_path / "spine.json", tmp_path / "sample.json"
    save_spine(spine, source)
    command = [
        sys.executable,
        "-B",
        "-m",
        "codex_harness.core.spine",
        "compile",
        str(source),
        str(out),
    ]
    subprocess.run(command, check=True)
    assert json.loads(out.read_text()) == compile_manifest(spine, tmp_path / "template")
    assert {
        name: (tmp_path / "template" / name).read_text(encoding="utf-8") for name in MARKDOWN_FILES
    } == render_markdown(spine)
    alternative = tmp_path / "alternative"
    shutil.copytree(tmp_path / "template", alternative)
    (alternative / "VISION.md").write_text("Alternative vision\n")
    subprocess.run([*command, "--template", str(alternative), "--no-render"], check=True)
    assert (alternative / "VISION.md").read_text() == "Alternative vision\n"
    subprocess.run([*command, "--template", str(alternative)], check=True)
    assert (alternative / "VISION.md").read_text(encoding="utf-8") == render_markdown(spine)[
        "VISION.md"
    ]
    assert json.loads(out.read_text()) == compile_manifest(spine, alternative)
    source.rename(tmp_path / "isolated.json")
    shutil.rmtree(tmp_path / "template")
    command[-2] = str(tmp_path / "isolated.json")
    subprocess.run(command, check=True)
    assert json.loads(out.read_text()) == compile_manifest(spine)


def test_recompile_updates_rounding_docs_and_requirement_revision(tmp_path: Path) -> None:
    template = tmp_path / "template"
    shutil.copytree(FIXTURE / "template", template)
    data = _fixture_data()
    source, out = tmp_path / "spine.json", tmp_path / "sample.json"
    command = [
        sys.executable,
        "-B",
        "-m",
        "codex_harness.core.spine",
        "compile",
        str(source),
        str(out),
    ]
    save_spine(Spine(data), source)
    subprocess.run(command, check=True)
    before = json.loads(out.read_text())
    # The requirement repeats the decision; update both canonical texts rather
    # than concealing a contradiction in the authoritative spine.
    for node_id in ("D3", "R2"):
        node = _edit(data, node_id)
        node["text"] = node["text"].replace("rounded half up", "always rounds up")
    save_spine(Spine(data), source)
    subprocess.run(command, check=True)
    after = json.loads(out.read_text())
    for name in ("PLAN.md", "VISION.md"):
        text = (template / name).read_text(encoding="utf-8")
        assert "always rounds up" in text
        assert "half up" not in text
    assert after["requirementFilesRevision"] != before["requirementFilesRevision"]
    assert set(MARKDOWN_FILES) <= set(after["requirementFiles"])


@pytest.mark.parametrize(
    "name", ["../outside", "C:/outside", "/outside", ".git/config", "reference/source.py"]
)
def test_compile_requirement_revision_rejects_unsafe_paths(tmp_path: Path, name: str) -> None:
    data = _fixture_data()
    _edit(data, "V1")["fields"]["requirementFiles"] = [name]
    with pytest.raises(SpineError) as caught:
        compile_manifest(Spine(data), tmp_path)
    assert caught.value.code == "spine-requirement-path-invalid"
