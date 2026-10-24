"""Canonical compile/edit/reload/public impact; no provider or product execution."""

import json
import shutil
from copy import deepcopy
from pathlib import Path

from tests.python.core.test_mcp_audit import invoke

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
from codex_harness.core.spine import compile_manifest

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"


def public(workspace, tool, **arguments):
    server = PlannerServer(workspace)
    try:
        result = invoke(server, tool, **arguments)
        assert not result["isError"], result
        return result["structuredContent"]
    finally:
        server.close()


def test_public_dependency_impact_agrees_with_imported_baseline_before_and_after_compile(tmp_path):
    """Catch omitted direct/transitive consumers; earlier parity missed compile/reload checkpoints.

    The after-compile checkpoint records inherited protected-doc widening separately;
    it cannot justify omitting a consumer before compilation.
    """
    producer, foreign = tmp_path / "producer", tmp_path / "foreign"
    shutil.copytree(FIXTURE / "template", producer / "template")
    with open_plan_store(producer) as store:
        store.import_spine(FIXTURE / "spine.json")
        independent = deepcopy(store.spine().node("A"))
        independent.update(id="C", title="Independent", parents=["R6"])
        independent["fields"].update(
            ownedFiles=["tipsplit/independent.py"],
            proofModule="test_cli.py",
            dependsOn=[],
            lessons=[],
        )
        store.upsert_node(independent)
        transitive = deepcopy(store.spine().node("B"))
        transitive.update(id="D", title="Transitive consumer")
        transitive["fields"].update(ownedFiles=["tipsplit/follow_on.py"], dependsOn=["B"])
        store.upsert_node(transitive)
        snapshot = tmp_path / "baseline-spine.json"
        store.export_spine(snapshot)
    baseline = public(producer, "harness_compile")
    saved = tmp_path / "baseline-manifest.json"
    saved.write_text(json.dumps(baseline), encoding="utf-8")
    original_baseline = saved.read_bytes()
    shutil.copytree(producer / "template", foreign / "template")
    with open_plan_store(foreign) as store:
        store.import_spine(snapshot)
        assert not store.connection.execute(
            "SELECT 1 FROM events WHERE kind='manifest-compiled'"
        ).fetchall()
    traces = {}
    for origin, workspace in (("local", producer), ("imported", foreign)):
        with open_plan_store_readonly(workspace) as store:
            edited = deepcopy(store.spine().node("D3"))
        edited["text"] += "\nProducer A's money rounding contract changed."
        public(workspace, "harness_upsert_node", node=edited)
        with open_plan_store_readonly(workspace) as store:
            current = compile_manifest(store.spine(), workspace / "template")
            assert {
                key for key in baseline["beads"] if baseline["beads"][key] != current["beads"][key]
            } == {"A"}
        traces[origin] = {}
        for checkpoint in ("before-recompile", "after-recompile"):
            if checkpoint == "after-recompile":
                public(workspace, "harness_compile")
            with open_plan_store_readonly(workspace) as store:
                before = tuple(store.connection.iterdump())
            traces[origin][checkpoint] = public(workspace, "harness_impact", since=str(saved))
            with open_plan_store_readonly(workspace) as store:
                assert tuple(store.connection.iterdump()) == before
            assert saved.read_bytes() == original_baseline
    (tmp_path / "public-impact-traces.json").write_text(json.dumps(traces, indent=2))
    assert traces["local"]["before-recompile"]["affectedBeads"] == ["A", "B", "D"], traces
    assert traces["imported"]["before-recompile"]["affectedBeads"] == ["A", "B", "D"]
    assert all(rows["before-recompile"]["untouchedBeads"] == ["C"] for rows in traces.values())
    assert (
        traces["local"]["after-recompile"]["affectedBeads"]
        == traces["imported"]["after-recompile"]["affectedBeads"]
    )
    assert all("B" in rows["after-recompile"]["affectedBeads"] for rows in traces.values())
