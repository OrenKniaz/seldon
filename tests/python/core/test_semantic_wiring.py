"""Source wiring evidence: real CLI products and controller, simulated workers only."""

import json
from pathlib import Path

import pytest
from tests.python.core.planning_support import (
    source_controller_roundtable,
    source_only_confirmation,
    source_only_expansion,
)

from codex_harness.core import dispatch, planner_execution, planner_jobs, worker_packet
from codex_harness.core.graph import load_plan
from codex_harness.core.mcp_planner import PlannerServer, packet_view
from codex_harness.core.plan_store import PHASES, open_plan_store
from codex_harness.core.roundtable import review_packet
from codex_harness.core.spine import Spine, compile_manifest

STUB = "import sys\nprint('SELDON-STUB hello', file=sys.stderr)\nsys.exit(3)\n"
PRODUCT = "print('Hello', end='')\n"


def node(key, kind, parents, **fields):
    return dict(
        id=key,
        kind=kind,
        title=key,
        text=f"{key}: offline greeting CLI",
        parents=parents,
        fields=fields,
    )


@pytest.fixture
def semantic_plan(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("sv")
    server = PlannerServer(tmp_path)
    reply = server.call_tool(
        "harness_plan_start",
        dict(idea="Offline greeting CLI", name="greeting", plan_mode="semantic-v1"),
    )
    assert not reply["isError"], reply
    store = server.store
    for item in [
        node("F", "feature", ["V1"]),
        node("R", "requirement", ["F"]),
        node("D", "decision", ["V1"]),
        node("CLI", "design", ["R"], grammar="hello prints Hello and exits 0"),
        node(
            "C",
            "bead",
            ["CLI"],
            ownedFiles=["hello.py"],
            dependsOn=[],
            lessons=["D"],
            semanticPhase="contracts",
        ),
        node(
            "B",
            "bead",
            ["CLI"],
            ownedFiles=["hello.py", "test_hello.py"],
            dependsOn=["C"],
            lessons=[],
            semanticPhase="build",
        ),
    ]:
        store.upsert_node(item)
    spine = store.spine()
    vision = spine.node("V1")
    vision["fields"].update(
        targets={"greet": {"entry": "hello.py", "argv": []}},
        scenarios={
            "schema": "seldon/scenarios-v1",
            "scenarios": [
                {
                    "id": "first-moment",
                    "requirementRefs": [f"R@{spine.revision('R')}"],
                    "contractRefs": [f"CLI@{spine.revision('CLI')}"],
                    "setup": {"files": []},
                    "actions": [
                        {
                            "id": "hello",
                            "cli": {"target": "greet", "operation": "hello", "argv": []},
                        }
                    ],
                    "observe": [{"action": "hello", "exitEquals": 0, "stdoutEquals": "Hello"}],
                    "verdict": "all",
                }
            ],
        },
    )
    store.upsert_node(vision)
    template = tmp_path / "template"
    template.mkdir()
    manifest = store.compile_template(template)
    (tmp_path / "sample.json").write_text(json.dumps(manifest))
    yield store, manifest
    server.close()


def workers(*, stub=STUB, product=PRODUCT, tamper=False):
    def contracts(root):
        (root / "hello.py").write_text(stub)

    def build(root):
        (root / "hello.py").write_text(product)
        (root / "test_hello.py").write_text(
            "# Behavior: greeting; catches wrong text; scenarios omit function-level checks.\n"
            "import unittest, subprocess, sys\n"
            "class Greeting(unittest.TestCase):\n"
            " def test_greeting(self):\n"
            "  self.assertEqual(subprocess.check_output([sys.executable, 'hello.py']), b'Hello')\n"
        )
        if tamper:
            (root / "SCENARIOS.json").write_text("{}")

    return {"C": contracts, "B": build}


def execute(store, *, work_root=None, **kwargs):
    return dispatch.run_plan(
        store.workspace,
        work_root=work_root or store.workspace / "runs",
        max_parallel=1,
        fake_workers=workers(**kwargs),
    )


def test_mode_is_opt_in_and_creation_only(tmp_path):
    """Protect legacy default and durable mode; catches resume conversion missed by runner tests."""
    server = PlannerServer(tmp_path)
    assert not server.call_tool("harness_plan_start", dict(idea="CLI", name="legacy"))["isError"]
    assert not server.store.spine().semantic
    refused = server.call_tool(
        "harness_plan_start", dict(idea="CLI", name="legacy", plan_mode="semantic-v1")
    )
    assert refused["isError"] and "immutable" in str(refused)
    vision = server.store.spine().node("V1")
    vision["fields"]["planMode"] = "semantic-v1"
    with pytest.raises(ValueError, match="immutable"):
        server.store.upsert_node(vision)
    server.close()
    with open_plan_store(tmp_path) as reopened:
        assert not reopened.spine().semantic


def test_scenario_data_compiles_and_keeps_review_publish_and_packet_gates(
    semantic_plan, monkeypatch
):
    """Protect data-only planning; catches hidden legacy gates that standalone runner tests miss."""
    store, manifest = semantic_plan
    assert "proofModule" not in manifest["beads"]["B"]
    assert not store.spine().ids("test") and not store.spine().coverage_gaps()
    assert not (store.workspace / "template/tests").exists()
    source_only_expansion(store)
    while store.phase() != "roundtable":
        if store.phase() in {"align", "vision-lock"}:
            source_only_confirmation(store, store.phase())
        store.advance(PHASES[PHASES.index(store.phase()) + 1], by="source-test")
    manifest = store.compile_template(store.workspace / "template")
    with pytest.raises(ValueError, match="successful review") as error:
        store.advance("publish", by="source-test")
    assert "passing audit" not in str(error.value)
    with pytest.raises(ValueError, match="Enter publish"):
        store.publish(by="source-test", export_dir=store.workspace / "published")
    packet = packet_view(store, "B")["packet"]
    assert "SCENARIOS.json" in packet and "Hello" in packet and "Implement the product" in packet
    assert "vision expansion" in review_packet(store)
    assert store.passing_audit(manifest["planRevision"]) is None
    state = store.status()
    assert state["reviewResolution"]["audit"] == {
        "current": False,
        "proofLevel": None,
        "applicable": False,
    }
    assert state["pending_gate"] == "successful-review-required"
    # Synthetic reviews exercise publication/admission; they establish no provider proof.
    source_controller_roundtable(
        store,
        process_stub=lambda *_: (0, "[]"),
        reviewers=[
            {"name": "source-codex", "provider": "codex"},
            {"name": "source-claude", "provider": "claude"},
        ],
        executable_for=lambda p: p,
    )
    store.advance("publish", by="source-test")
    revision = store.publish(by="source-test", export_dir=store.workspace / "published")
    assert store.status()["publishedProofLevel"] is None
    selection = [
        dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium") for b in ("C", "B")
    ]
    assert (
        planner_execution.execution_scope(store, revision, selection, max_parallel=1, call_cap=2)[
            "publicationRevision"
        ]
        == revision
    )
    monkeypatch.setattr(worker_packet, "MAX_CHARACTERS", 10)
    with pytest.raises(ValueError, match="exceed"):
        packet_view(store, "B")


def test_canonical_plan_contracts_red_build_validation_pass(semantic_plan):
    """Protect producer→dispatch→real CLI success; catches RED-as-proof and final rerun of stubs."""
    store, manifest = semantic_plan
    job = planner_jobs._directory(store.workspace, manifest["planRevision"])
    job.mkdir(parents=True)
    request = dict(
        schema=planner_jobs.SCHEMA, jobId=job.name, publicationRevision=job.name, options={}
    )
    request["requestDigest"] = planner_jobs._digest(request)
    (job / "request.json").write_text(json.dumps(request))
    record = execute(store, work_root=job / "runs")
    assert record["status"] == dispatch.STATUS_MOCK_ONLY, record
    red = record["beads"]["C"]["integrated"]["semantic"]
    green = record["semanticValidation"]
    assert red["state"] == "RED" and not red["passed"]
    assert not record["beads"]["C"]["lessonsPromoted"]
    assert green["state"] == "PASS" and green["passed"] and green["linkedRed"]
    assert red["scenarioBinding"] == green["scenarioBinding"]
    assert all(r["cleanup"]["quiescenceVerified"] for r in red["results"] + green["results"])
    assert green["candidateTree"] != red["candidateTree"]
    assert record["requirementValidation"][0]["passed"] is False
    assert record["requirementValidation"][1]["passed"] is True
    assert not record["claims"] and not record["isLiveProductProof"]
    saved = json.loads((Path(record["repo"]).parent / "evidence/run.json").read_text())
    assert saved["semanticValidation"] == green
    public = planner_jobs.worker_job_status(store.workspace, job.name)
    assert public["run"]["semanticValidation"] == green and public["status"] == "unknown"
    resumed = dispatch.run_plan(
        store.workspace,
        work_root=store.workspace / "runs",
        max_parallel=1,
        fake_workers=workers(),
        resume_run_dir=Path(record["repo"]).parent,
    )
    assert resumed["status"] == dispatch.STATUS_MOCK_ONLY, resumed
    assert resumed["semanticValidation"] == green


@pytest.mark.parametrize(
    "source", ["raise ImportError('missing dependency')\n", "print('wrong stub')\n"]
)
def test_contracts_infrastructure_is_error_and_blocks_build(semantic_plan, source):
    """Protect RED; catches import/marker errors admitted as RED, unlike exit-only checks."""
    store, _ = semantic_plan
    record = execute(store, stub=source)
    assert record["beads"]["C"]["integrated"]["semantic"]["state"] == "ERROR", record
    assert record["beads"]["B"]["attempt"] is None
    assert "semanticValidation" not in record


def test_contracts_requires_every_scenario_to_fail(semantic_plan):
    """Protect all-scenario RED; catches satisfied stubs, which marker-only checks miss."""
    store, _ = semantic_plan
    vision = store.spine().node("V1")
    second = json.loads(json.dumps(vision["fields"]["scenarios"]["scenarios"][0]))
    second.update(id="stub-already-satisfies", observe=[{"action": "hello", "exitEquals": 3}])
    vision["fields"]["scenarios"]["scenarios"].append(second)
    store.upsert_node(vision)
    manifest = store.compile_template(store.workspace / "template")
    (store.workspace / "sample.json").write_text(json.dumps(manifest))
    record = execute(store)
    result = record["beads"]["C"]["integrated"]["semantic"]
    assert [r["state"] for r in result["results"]] == ["FAIL", "PASS"]
    assert result["state"] == "ERROR" and not result["admitted"]
    assert record["beads"]["B"]["attempt"] is None


@pytest.mark.parametrize("protected", ["SCENARIOS.json", "PLAN.md"])
def test_build_cannot_change_scenarios(semantic_plan, protected):
    """Protect frozen expectations; catches ownership escape absent from standalone runner tests."""
    store, _ = semantic_plan
    selected = workers()
    build = selected["B"]

    def tamper(root):
        build(root)
        (root / protected).write_text("{}")

    selected["B"] = tamper
    record = dispatch.run_plan(
        store.workspace, work_root=store.workspace / "runs", max_parallel=1, fake_workers=selected
    )
    assert record["beads"]["B"]["status"] == "rejected: requirement files changed", record
    assert record["beads"]["B"]["integrated"] is None
    assert "semanticValidation" not in record


@pytest.mark.parametrize("product,state", [("print('Wrong')\n", "FAIL"), (STUB, "ERROR")])
def test_validation_requires_all_pass_and_no_markers(semantic_plan, product, state):
    """Protect fulfillment; catches wrong products/remaining stubs missed by RED admission."""
    store, _ = semantic_plan
    record = execute(store, product=product)
    result = record["beads"]["B"]["integrated"]["semantic"]
    assert result["state"] == state and not result["passed"], record
    assert record["status"].startswith("plan-failed:")


@pytest.mark.parametrize(
    "change,error",
    [
        ("reference", "Stale/unknown"),
        ("gui", "unsupported in semantic-v1"),
        ("http", "unsupported in semantic-v1"),
        ("parallel", "unsupported in semantic-v1"),
    ],
)
def test_semantic_admission_rejects_stale_or_deferred_scope(semantic_plan, change, error):
    """Protect scope/binding; catches stale refs and expansion missed by runner-only tests."""
    store, _ = semantic_plan
    data = store.spine().to_dict()
    root = next(n["fields"] for n in data["nodes"] if n["kind"] == "vision")
    if change == "reference":
        root["scenarios"]["scenarios"][0]["contractRefs"] = ["CLI@old"]
    elif change == "gui":
        root["productSurface"] = "gui"
    elif change == "http":
        root["scenarios"]["scenarios"][0]["actions"][0] = {"id": "hello", "http": {}}
    else:
        data["nodes"].append(
            node(
                "B2",
                "bead",
                ["CLI"],
                ownedFiles=["other.py"],
                dependsOn=["C"],
                lessons=[],
                semanticPhase="build",
            )
        )
    with pytest.raises(ValueError, match=error):
        compile_manifest(Spine(data))


def test_legacy_still_requires_protected_tests_and_proof_modules(semantic_plan):
    """Protect legacy admission; catches global relaxation missed by semantic success tests."""
    store, _ = semantic_plan
    data = store.spine().to_dict()
    next(n for n in data["nodes"] if n["kind"] == "vision")["fields"].pop("planMode")
    with pytest.raises(ValueError, match="Invalid bead fields"):
        Spine(data)
    plan = load_plan(store.workspace)
    assert plan.semantic and plan.beads["B"].proof_module is None


def test_schema_help_scenario_example_validates_and_errors_name_keys(tmp_path):
    """The taught example must pass the real validator; refusals must name the bad keys."""
    from codex_harness.core.planner_schema import schema_help
    from codex_harness.core.semantic_schema import validate

    tools = [{"name": "harness_upsert_node", "inputSchema": {}}]
    example = schema_help(tools, kind="vision")["semanticScenarios"]["fieldExample"]
    assert validate(example["scenarios"], tmp_path, example["targets"], require_entries=False)
    bad = json.loads(json.dumps(example["scenarios"]))
    bad["scenarios"][0]["steps"] = []
    with pytest.raises(ValueError, match=r"unknown \['steps'\]"):
        validate(bad, tmp_path, example["targets"], require_entries=False)


def test_mixed_action_path_observation_teaches_split(tmp_path):
    """Planners tried per-action file checks; the refusal must say how to express them."""
    from codex_harness.core.planner_schema import schema_help
    from codex_harness.core.semantic_schema import validate

    tools = [{"name": "harness_upsert_node", "inputSchema": {}}]
    example = schema_help(tools, kind="vision")["semanticScenarios"]["fieldExample"]
    bad = json.loads(json.dumps(example["scenarios"]))
    bad["scenarios"][0]["observe"][1]["action"] = "a1"
    with pytest.raises(ValueError, match="split into separate scenarios"):
        validate(bad, tmp_path, example["targets"], require_entries=False)


def test_status_reports_invalid_semantic_scenarios_as_a_gap(semantic_plan):
    """Invalid scenario data must stay a readable planning gap, not unavailable status."""
    store, _ = semantic_plan
    original = store.spine().node("V1")
    vision = json.loads(json.dumps(original))
    vision["fields"]["scenarios"]["scenarios"][0]["observe"] = [{"steps": []}]
    store.upsert_node(vision)
    try:
        gaps = [g for g in store.status()["coverage_gaps"] if g["gap"].startswith("semantic")]
        assert gaps and "unknown ['steps']" in gaps[0]["message"]
    finally:
        store.upsert_node(original)
    assert not [g for g in store.status()["coverage_gaps"] if g["gap"].startswith("semantic")]


def test_bare_scenario_refs_compile_and_stale_pins_still_fail(semantic_plan):
    """Planners cannot see node revisions; bare IDs must work, explicit pins stay checked."""
    store, _ = semantic_plan
    data = store.spine().to_dict()
    scenario = next(n for n in data["nodes"] if n["kind"] == "vision")["fields"]["scenarios"]
    scenario["scenarios"][0].update(requirementRefs=["R"], contractRefs=["CLI"])
    assert compile_manifest(Spine(data))["planRevision"]
    scenario["scenarios"][0]["requirementRefs"] = ["R@old"]
    with pytest.raises(ValueError, match="Stale/unknown semantic reference"):
        compile_manifest(Spine(data))


def test_semantic_review_packet_scopes_reviewers_to_three_jobs(semantic_plan):
    """A real run looped on edge-case findings; semantic reviewers must not review test depth."""
    store, _ = semantic_plan
    packet = review_packet(store)
    assert "Review only three things" in packet
    assert "Look for missing features or edge cases" not in packet
