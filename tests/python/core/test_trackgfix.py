"""Editor revision 2 regressions, without launching providers."""

import json
import shutil
from pathlib import Path

import pytest
from tests.python.core.test_plan_cli import cli
from tests.python.core.test_roundtable import finding, large_spine

from codex_harness.core import roundtable
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_quality import _cross_bead_contract
from codex_harness.core.plan_store import PlanStore, PlanStoreError
from codex_harness.core.spine import Spine
from codex_harness.core.spine_render import render_markdown

FIXTURE = Path(__file__).parent / "fixtures/ts_runner"


@pytest.fixture
def editor(tmp_path):
    shutil.copytree(FIXTURE / "template", tmp_path / "template")
    with PlanStore(tmp_path) as store:
        store.import_spine(FIXTURE / "spine.json")
        consumer = store.spine().node("FORMAT")
        consumer["parents"].append("R_SUM")
        consumer["fields"]["dependsOn"] = ["SUM"]
        store.upsert_node(consumer)
        store.connection.execute("UPDATE meta SET phase='plan'")
        yield store


@pytest.mark.parametrize(
    "specifier,owned",
    [
        ("../src/sum.ts", "src/sum.ts"),
        ("../src/sum", "src/sum.ts"),
        ("../src/sum.js", "src/sum.ts"),
        ("../src/sum", "src/sum/index.ts"),
        ("../src/sum", "src/sum.js"),
    ],
)
def test_typescript_consumer_import(editor, specifier, owned):
    node = editor.spine().node("SUM")
    node["fields"]["ownedFiles"] = [owned]
    editor.upsert_node(node)
    proof = editor.workspace / "template/tests/format.test.ts"
    proof.write_text(
        f"import {{ sum }} from '{specifier}';\ntest('sum', () => {{ sum(2, 3); }});\n"
    )
    status, evidence, _ = _cross_bead_contract(editor, editor.spine())
    assert status == "pass", evidence
    assert evidence["checked"] == [{"contract": "R_SUM", "producer": "SUM", "consumer": "FORMAT"}]


def test_command_missing_import_and_explicit_contract(editor):
    assert _cross_bead_contract(editor, editor.spine())[0] == "warn"
    node = editor.spine().node("R_SUM")
    node["fields"]["contractTest"] = "tests/format.test.ts"
    editor.upsert_node(node)
    assert _cross_bead_contract(editor, editor.spine())[0] == "pass"


def test_command_runner_render(editor):
    docs = render_markdown(editor.spine())
    for name in ("PLAN.md", "REQUIREMENTS.md"):
        assert "node" in docs[name] and "{proof}" in docs[name]
        assert "unittest" not in docs[name]
        assert "Append the bead" not in docs[name]


def test_show_node_test_names_and_block(editor):
    proof = editor.workspace / "template/tests/format.test.ts"
    proof.write_text(
        "import test from 'node:test';\n"
        "test('outer', () => { const value = { nested: '}' };\n"
        '  it("inner", () => { assert.equal(1, 1); });\n});\n'
        "test('huge', () => {\n//" + "é" * 9000 + "\n});\n",
        encoding="utf-8",
    )
    result = cli(editor.workspace, "show", "T_FORMAT", machine=True)
    assert result["data"]["testFiles"][0]["functions"] == ["outer", "inner", "huge"]
    assert "best effort" in result["text"].lower()
    result = cli(editor.workspace, "show", "R_FORMAT", "--body", "--test", "outer", machine=True)
    body = result["data"]["testFiles"][0]["body"]
    assert body.endswith("});") and "it(" in body and "huge" not in body
    result = cli(editor.workspace, "show", "T_FORMAT", "--body", "--test", "huge", machine=True)
    entry = result["data"]["testFiles"][0]
    assert entry["truncated"] and len(entry["body"].encode()) <= 8192
    cli(editor.workspace, "show", "T_FORMAT", "--body", "--test", "unknown", code=2)


@pytest.mark.parametrize("provider", ["codex", "claude", "copilot"])
def test_packet_exact_compiled_readonly(editor, provider):
    from codex_harness.core.graph import load_plan
    from codex_harness.core.worker_packet import build_packet

    manifest = editor.compile_template(editor.workspace / "template")
    manifest["proofLevel"] = "prospective"
    # A recorded compilation is the packet source, including its proof choice.
    editor._event("manifest-compiled", {"manifest": manifest, "spine": editor.spine().to_dict()})
    path = editor.workspace / "saved.json"
    path.write_text(json.dumps(manifest))
    expected = build_packet(
        load_plan(path),
        {"id": "FORMAT", **manifest["beads"]["FORMAT"]},
        editor.workspace / "template",
        None,
        provider=provider,
        proof_level="prospective",
    )
    before = list(editor.connection.iterdump())
    result = cli(editor.workspace, "packet", "FORMAT", "--provider", provider, machine=True)
    data = result["data"]
    assert data["packet"] == expected
    assert data["characters"] == len(expected)
    assert data["bytes"] == len(expected.encode())
    assert "Packet size:" in result["text"]
    assert list(editor.connection.iterdump()) == before
    server = PlannerServer(editor.workspace)
    try:
        result = server.call_tool("harness_packet_view", {"id": "FORMAT", "provider": provider})
        assert not result["isError"], result
        assert result["structuredContent"] == data
    finally:
        server.close()


@pytest.mark.parametrize("source", ["compiled", "published", "audit"])
def test_impact_hash_sources_and_prefix(editor, source):
    manifest = editor.compile_template(editor.workspace / "template")
    revision = manifest["planRevision"]
    workspace = editor.workspace / source
    workspace.mkdir()
    shutil.copytree(editor.workspace / "template", workspace / "template")
    snapshot = {**editor.spine().to_dict(), "manifest": manifest}
    path = workspace / "spine.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with PlanStore(workspace) as isolated:
        isolated.import_spine(path)
        if source == "compiled":
            isolated.compile_template(workspace / "template")
        elif source == "published":
            isolated.connection.execute(
                "INSERT INTO revisions VALUES (?, 'now', ?)", (revision, json.dumps(snapshot))
            )
        else:
            isolated.record_audit(revision, {"ok": True, "manifest": manifest})
        node = isolated.spine().node("R_SUM")
        node["text"] += " clarified"
        isolated.upsert_node(node)
        assert isolated.impact_since(revision[:12]) == isolated.impact_since(revision)
        assert "SUM" in isolated.impact_since(revision[:12])["affectedBeads"]


def test_packet_prospective_publication_and_stale_refusal(editor):
    from codex_harness.core.graph import load_plan
    from codex_harness.core.mcp_planner import packet_view
    from codex_harness.core.test_audit import PROSPECTIVE_WARNING
    from codex_harness.core.worker_packet import build_packet

    manifest = editor.compile_template(editor.workspace / "template")
    manifest.update(
        sourceManifestRevision=manifest["planRevision"],
        planRevision="a" * 64,
        proofLevel="prospective",
    )
    snapshot = {**editor.spine().to_dict(), "manifest": manifest}
    editor.connection.execute(
        "INSERT INTO revisions VALUES (?, 'now', ?)",
        (manifest["planRevision"], json.dumps(snapshot)),
    )
    editor.connection.execute("UPDATE meta SET published_revision=?", (manifest["planRevision"],))
    path = editor.workspace / "published.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before = list(editor.connection.iterdump())
    result = packet_view(editor, "FORMAT")
    expected = build_packet(
        load_plan(path),
        {"id": "FORMAT", **manifest["beads"]["FORMAT"]},
        editor.workspace / "template",
        None,
        proof_level="prospective",
    )
    assert result["packet"] == expected and PROSPECTIVE_WARNING in result["packet"]
    assert list(editor.connection.iterdump()) == before
    node = editor.spine().node("R_SUM")
    node["text"] += " changed"
    editor.upsert_node(node)
    with pytest.raises(ValueError, match="No current compiled or published manifest"):
        packet_view(editor, "FORMAT")


def test_unknown_and_ambiguous_hash(editor):
    for revision in ("a" * 64, "abcdef12" + "0" * 56, "abcdef12" + "1" * 56):
        editor.connection.execute(
            "INSERT INTO audit_runs VALUES (?, ?, 1, '{}', 'now')", (revision, revision)
        )
    for revision, reason in (("abcdef12", "ambiguous"), ("b" * 12, "unknown")):
        with pytest.raises(PlanStoreError, match=f"(?i){reason}.*Next:"):
            editor.impact_since(revision)


def test_shard_upstream_context_and_budget(editor, monkeypatch):
    for key in ("F1", "F2"):
        editor.upsert_node(
            dict(id=key, kind="feature", title=key, text="", parents=["V1"], fields={})
        )
    data = large_spine(editor).to_dict()
    nodes = {n["id"]: n for n in data["nodes"]}
    # A cross-feature dependency whose requirement is not a descendant of this feature.
    nodes["FORMAT"]["fields"]["dependsOn"] = ["SUM"]
    nodes["SUM"]["parents"] = ["large-124"]
    nodes["FORMAT"]["parents"] = ["large-000"]
    spine = Spine(data)
    monkeypatch.setattr(editor, "spine", lambda: spine)
    report = {"manifestRevision": spine.plan_revision(), "checks": [], "score": 0}
    shards = roundtable.packet_shards(editor, "claude", report=report, token_budget=15000)
    selected = next(s for s in shards if "large-000" in s["nodeIds"])
    assert "large-124" in selected["contextNodeIds"]
    assert "context only" in selected["packet"].lower()
    assert "Requirement 124" in selected["packet"]
    assert all(
        s["estimatedTokens"] == roundtable.estimated_tokens(s["packet"]) <= 15000 for s in shards
    )


def test_out_of_shard_finding_persisted(editor, monkeypatch):
    original = roundtable.packet_shards

    def shards(*args, **kwargs):
        packet = original(*args, **kwargs)[0]
        packet["nodeIds"] = ["V1"]
        return [packet]

    monkeypatch.setattr(roundtable, "packet_shards", shards)
    result = roundtable.run_roundtable(
        editor,
        reviewers=[{"name": "test", "provider": "claude"}],
        executable_for=lambda _: "claude",
        runner=lambda *args: (0, json.dumps([finding("R_SUM"), finding("nonexistent")])),
    )
    assert result["invalid"] == 1
    assert result["findings"] == 1
    saved = roundtable.findings(editor)[0]
    assert saved["node_id"] == "R_SUM" and saved["outOfShard"] is True
