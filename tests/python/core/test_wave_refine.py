"""Source-only wave mechanism witnesses; no provider or product-completion credit."""

import contextlib
import io
import json
import subprocess
import sys
from copy import deepcopy

import pytest
from tests.python.core.test_plan_store import draft_inputs, draft_node

from codex_harness.core.plan_cli import main
from codex_harness.core.plan_store import open_plan_store
from codex_harness.core.spine import Spine, compile_manifest
from codex_harness.core.wave_refine import refinement_impact


def cli(workspace, *args, code=0):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = main(["--workspace", str(workspace), "--json", *args])
    body = output.getvalue()
    assert result == code, body
    return json.loads(body)


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def setup_wave(workspace, *, locked=False, two_sketches=False):
    workspace.mkdir(exist_ok=True)
    cli(workspace, "start", "--idea", "Retain two outcomes", "--name", "links")
    with open_plan_store(workspace) as store:
        inputs = draft_inputs(store)
        vision = store.spine().node("V1")
        vision["fields"].update(
            validation=["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
            requirementFiles=[],
        )
        store.upsert_node(vision)
        x1 = store.spine().node("X1")
        x1["fields"].update(
            waveBoundaryIntegrationTestId="T1", waveBoundaryIntegrationModule="test_first.py"
        )
        store.upsert_node(x1)
        x2 = store.spine().node("X2")
        x2["fields"].update(
            waveBoundaryIntegrationTestId="TG", waveBoundaryIntegrationModule="test_global.py"
        )
        store.upsert_node(x2)
        b1 = store.spine().node("B1")
        b1["fields"].update(ownedFiles=["links.py"], proofModule="test_first.py")
        store.upsert_node(b1)
        store.upsert_node(
            draft_node("T1", "test", dict(level="unit", module="test_first.py"), ["B1"])
        )
        store.upsert_node(
            draft_node("TG", "test", dict(level="e2e", module="test_global.py"), ["R1", "R2"])
        )
        store.upsert_node(draft_node("CONTRACT", "design", {}, ["R1"]))
        spine = store.spine()
        plan = inputs["map_node"]["fields"]
        for obligation in plan["obligations"]:
            for ref in obligation["sourceRefs"]:
                ref["revision"] = spine.revision(ref["id"])
        plan["obligations"].append(
            dict(
                id="O-H",
                kind="handoff",
                sourceRefs=[dict(id="CONTRACT", revision=spine.revision("CONTRACT"))],
                waveIds=["W1", "W2"],
                criterion="Retain interface",
                requiredEvidence=["fixture-contract"],
                requiredForFullVision=True,
            )
        )
        plan["contracts"] = [
            dict(
                id="C",
                producerWaveId="W1",
                consumerWaveIds=["W2"],
                specRef=dict(id="CONTRACT", revision=spine.revision("CONTRACT")),
                interface=dict(
                    symbol="links", inputShape={}, outputShape={}, errors=[], invariants=[]
                ),
                verificationObligationIds=["O-H"],
            )
        ]
        plan["waves"][0]["outgoingContractIds"] = ["C"]
        plan["waves"][1]["incomingContractIds"] = ["C"]
        inputs["detail_nodes"][1]["fields"]["sketches"][0]["expectedContractIds"] = ["C"]
        if locked:
            plan["contracts"][0]["interface"]["outputShape"] = {"type": "dict"}
        if two_sketches:
            second = deepcopy(inputs["detail_nodes"][1]["fields"]["sketches"][0])
            second["id"] = "SK2B"
            inputs["detail_nodes"][1]["fields"]["sketches"].append(second)
        inputs["expected_spine_revision"] = spine.plan_revision()
    cli(workspace, "wave-map", "--json", json.dumps(inputs))
    template = workspace / "template"
    oracle = workspace / "reference"
    for root in (template, oracle):
        (root / "tests").mkdir(parents=True)
        (root / "tests/__init__.py").write_text("")
        (root / "tests/test_first.py").write_text(
            "import unittest\nfrom links import links\nclass TestLinks(unittest.TestCase):\n"
            " def test_links(self):\n  self.assertEqual(links(), [('a', 'b')])\n"
        )
        (root / "tests/test_global.py").write_text(
            "import unittest\nclass TestGlobal(unittest.TestCase):\n def test_global(self):\n"
            "  from consumer import render\n  self.assertEqual(render(), 'a->b')\n"
        )
    (template / "links.py").write_text("def links() -> list[tuple[str, str]]:\n return []\n")
    (oracle / "links.py").write_text(
        "def links() -> list[tuple[str, str]]:\n return [('a', 'b')]\n"
    )
    git(workspace, "init")
    git(workspace, "add", "template", "reference")
    git(
        workspace,
        "-c",
        "user.name=Source fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "source fixture",
    )
    return workspace


def plan_confirmation(workspace, by="user", statement="Synthetic displayed two-wave plan approval"):
    cli(workspace, "compile", "--template", "template", "--out", "manifest.json")
    cli(
        workspace, "confirm", "--gate", "align", "--statement", "Synthetic source fixture alignment"
    )
    cli(workspace, "advance", "--to", "expand")
    cli(workspace, "advance", "--to", "vision-lock")
    cli(
        workspace,
        "confirm",
        "--gate",
        "vision-lock",
        "--statement",
        "Synthetic source fixture vision approval",
    )
    for phase in ("features", "requirements", "tech-stack", "plan"):
        cli(workspace, "advance", "--to", phase)
    return cli(
        workspace,
        "advance",
        "--to",
        "roundtable",
        "--by",
        by,
        "--statement",
        statement,
    )["data"]


def publish(workspace):
    """Exercise source-only export mechanics; synthetic reviews earn no real credit."""
    from tests.python.core.planning_support import source_only_publication

    with source_only_publication():
        return _publish_source_only(workspace)


def _publish_source_only(workspace):
    manifest = cli(workspace, "compile", "--template", "template", "--out", "manifest.json")["data"]
    cli(workspace, "audit", "--oracle", "reference", "--baseline", "template")
    response = workspace / "source-only-review.json"
    response.write_text("[]")
    cli(
        workspace,
        "roundtable",
        "--reviewer",
        '{"name":"source-codex","provider":"codex"}',
        "--reviewer",
        '{"name":"source-claude","provider":"claude"}',
        "--test-only-review-output",
        str(response),
    )
    with open_plan_store(workspace) as store:
        phase = store.phase()
    if phase != "publish":
        cli(workspace, "advance", "--to", "publish")
    published = cli(workspace, "publish")["data"]["id"]
    assert published == manifest["planRevision"]
    return published


def dispatch(workspace, work_root, resume=None):
    from codex_harness.core.dispatch_cli import main as dispatch_main

    args = [
        "--plan",
        str(workspace / "manifest.json"),
        "--work-root",
        str(work_root),
        "--mock-provider",
        "reference",
    ]
    if resume:
        args.extend(["--resume", str(resume)])
    with contextlib.redirect_stdout(io.StringIO()):
        assert dispatch_main(args) == 0
    if resume:
        return resume
    return next(work_root.glob("*/evidence/run.json")).parent.parent


def save_scenarios(workspace):
    scenarios = [dict(id="first-moment", title="First moment", given="Clean", steps=[], expect={})]
    for index in (1, 2):
        expression = (
            "from links import links; assert links() == [('a', 'b')]"
            if index == 1
            else "from consumer import render; assert render() == 'a->b'"
        )
        scenarios.append(
            dict(
                id=f"X{index}",
                title=f"Exit {index}",
                given="Clean",
                steps=[f'"{{python}}" -B -c "{expression}"'],
                expect={"exit": 0},
                nodeIds=[f"X{index}"],
            )
        )
    cli(workspace, "accept-scenarios", "--input", json.dumps(scenarios))


def response_for(context):
    interface = context["interfaces"][0]
    return dict(
        schema="dot-wave-refinement-response-v1",
        contextDigest=context["contextDigest"],
        refinements=[
            dict(
                sketchId="SK2",
                nodes=[
                    draft_node(
                        "B2",
                        "bead",
                        dict(
                            ownedFiles=["consumer.py"],
                            dependsOn=["B1"],
                            lessons=[],
                            proofModule="test_global.py",
                        ),
                        ["R2"],
                    ),
                    draft_node("T2", "test", dict(level="unit", module="test_global.py"), ["B2"]),
                ],
                interfaceRefs=[f"{interface['blob']}:links"],
                reason="Candidate links() returns a list; iterate pairs",
                replacedAssumptions=["links().items() dictionary assumption"],
            )
        ],
        contradictions=[],
        questions=[],
    )


def test_two_wave_cli_git_sqlite_source_flow(tmp_path):
    workspace = setup_wave(tmp_path / "toy")
    report = cli(workspace, "compile", "--template", "template", "--out", "manifest.json")
    manifest = report["data"]
    # Baseline semantic red: it selects the future global proof and has no scope.
    assert manifest["integration"]["proofModule"] == "test_first.py"
    assert manifest["waveScope"]["waveIds"] == ["W1"]
    assert manifest["waveScope"]["laterSketchIds"] == ["SK2"]
    assert len(manifest["waveScope"]["requiredObligationIds"]) == 6
    with open_plan_store(workspace) as store:
        assert store.spine().ids("bead") == ["B1"]
        store._check_coverage(store.spine(), "bead")
    lock = plan_confirmation(workspace)
    assert (
        lock["confirmedBy"] == "user"
        and lock["confirmationOrigin"] == "user"
        and lock["isProxy"] is False
    )
    p1 = publish(workspace)
    save_scenarios(workspace)
    run = dispatch(workspace, tmp_path / "runs")
    before_run = json.loads((run / "evidence/run.json").read_text())
    assert before_run["integrationValidation"]["passed"] is True and before_run["isMocked"] is True
    assert (
        subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "tests.test_global"],
            cwd=run / "repo",
            capture_output=True,
        ).returncode
        != 0
    )
    exits = cli(workspace, "wave-exit", "--run", str(run), "--through", "W1")["data"]
    assert exits["scenarioIds"] == ["X1"] and exits["passed"] is True
    assert exits["evidenceClass"] == "source-test-only"
    run_path = run / "evidence/run.json"
    original_run = run_path.read_bytes()
    for key in ("failed", "foreign", "publication", "module", "claim"):
        changed = json.loads(original_run)
        if key == "failed":
            changed["integrationValidation"]["passed"] = False
        elif key == "foreign":
            changed["repo"] = str(tmp_path)
        elif key == "publication":
            changed["planRevision"] = "0" * 64
        elif key == "module":
            changed["integrationValidation"]["claim"]["proofModule"] = "test_global.py"
        else:
            changed["claims"] = []
        run_path.write_text(json.dumps(changed))
        cli(workspace, "wave-exit", "--run", str(run), "--through", "W1", code=2)
    run_path.write_bytes(original_run)
    dirty = run / "repo/untracked.txt"
    dirty.write_text("dirty candidate must refuse")
    cli(workspace, "wave-exit", "--run", str(run), "--through", "W1", code=2)
    dirty.unlink()
    context = cli(workspace, "refine-wave", "W2", "--run", str(run), "--prepare")["data"]
    assert context["interfaces"][0]["symbols"][0]["returns"] == "list[tuple[str, str]]"
    response = workspace / "response.json"
    response.write_bytes(json.dumps(response_for(context)).encode())
    proposal = cli(
        workspace,
        "refine-wave",
        "W2",
        "--run",
        str(run),
        "--response",
        "@response.json",
        "--expected-spine",
        context["spineRevision"],
    )["data"]
    assert proposal["state"] == "complete" and proposal["completedRealDelta"] == 0
    with open_plan_store(workspace) as store:
        before = store.spine().to_dict()
        event_count = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        with pytest.raises(ValueError, match="Duplicate JSON key"):
            store.record_wave_refinement(
                context["spineRevision"], context["contextDigest"], b'{"schema":1,"schema":2}'
            )
        assert store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == event_count
        run_path.write_bytes(original_run + b"\n")
        with pytest.raises(ValueError, match="Fresh passing cumulative exits"):
            store.apply_wave_refinement(proposal["proposalId"], context["spineRevision"])
        assert store.spine().to_dict() == before
        run_path.write_bytes(original_run)
        # A required unanswered question makes the entire otherwise-valid proposal unapplied.
        unresolved = response_for(context)
        unresolved["questions"] = ["Confirm semantics not established by static syntax"]
        saved = store.record_wave_refinement(
            context["spineRevision"], context["contextDigest"], json.dumps(unresolved).encode()
        )
        with pytest.raises(ValueError, match="Whole target"):
            store.apply_wave_refinement(saved["proposalId"], context["spineRevision"])
        assert store.spine().to_dict() == before
        original_write = store._write_node
        calls = []

        def fail_second(spine, key):
            calls.append(key)
            if len(calls) == 2:
                raise RuntimeError("synthetic atomic rollback witness")
            original_write(spine, key)

        store._write_node = fail_second
        with pytest.raises(RuntimeError, match="rollback witness"):
            store.apply_wave_refinement(proposal["proposalId"], context["spineRevision"])
        assert store.spine().to_dict() == before
        assert store._latest_draft_event("wave-refinement-applied") is None
    from codex_harness.core.wave_refine import proposal as project_proposal

    with open_plan_store(workspace) as store:
        before_locked = store.spine().to_dict()
    locked_data = deepcopy(before_locked)
    next(n for n in locked_data["nodes"] if n["id"] == "MAP")["fields"]["contracts"][0][
        "interface"
    ]["outputShape"] = {"type": "dict"}
    locked = Spine(locked_data)
    locked_before = locked.to_dict()
    card = project_proposal(locked, context, response.read_bytes())
    assert card["state"] == "unapplied" and card["proposedDetail"] is None
    assert card["cards"][0]["classification"] == "locked"
    assert locked.to_dict() == locked_before
    assert (
        project_proposal(Spine(before_locked), context, response.read_bytes())["proposalId"]
        == proposal["proposalId"]
    )
    assert (
        project_proposal(Spine(before_locked), context, response.read_bytes() + b"\n")["proposalId"]
        != proposal["proposalId"]
    )
    applied = cli(
        workspace,
        "refine-wave",
        "W2",
        "--apply",
        proposal["proposalId"],
        "--expected-spine",
        context["spineRevision"],
    )["data"]
    assert applied["editedNodeIds"] == ["B2", "DETAIL2", "T2"]
    assert (
        applied["readiness"] == "not-run"
        and applied["review"] == applied["publication"] == "required"
    )
    assert (
        cli(
            workspace,
            "refine-wave",
            "W2",
            "--apply",
            proposal["proposalId"],
            "--expected-spine",
            context["spineRevision"],
        )["data"]
        == applied
    )
    (workspace / "template/consumer.py").write_text("def render():\n raise NotImplementedError\n")
    (workspace / "reference/consumer.py").write_text(
        "from links import links\ndef render():\n"
        " return ','.join(a + '->' + b for a, b in links())\n"
    )
    p2 = publish(workspace)
    assert p2 != p1
    with open_plan_store(workspace) as store:
        final = store.published_manifest(p2)
        assert store.wave_product_lock() == lock
        assert final["beads"]["B2"]["dependsOn"] == ["B1"]
        assert final["waveScope"]["waveIds"] == ["W1", "W2"]
        assert len(final["waveScope"]["requiredObligationIds"]) == 6
        assert final["waveScope"]["globalIntegration"]["state"] == "outstanding"
        assert store.connection.execute("SELECT count(*) FROM acceptance_runs").fetchone()[0] == 0
    cli(workspace, "wave-exit", "--run", str(run), "--through", "W2", code=2)
    run = dispatch(workspace, tmp_path / "runs", resume=run)
    fresh = cli(workspace, "wave-exit", "--run", str(run), "--through", "W2")["data"]
    assert fresh["scenarioIds"] == ["X1", "X2"] and fresh["passed"] is True
    assert fresh["publicationRevision"] == p2 and exits["publicationRevision"] == p1
    assert fresh["evidenceClass"] == "source-test-only"


@pytest.mark.parametrize("mode", ["locked", "incomplete"])
def test_store_keeps_locked_or_incomplete_target_wholly_unapplied(tmp_path, mode):
    workspace = setup_wave(
        tmp_path / mode, locked=mode == "locked", two_sketches=mode == "incomplete"
    )
    lock = plan_confirmation(workspace)
    publication = publish(workspace)
    save_scenarios(workspace)
    run = dispatch(workspace, tmp_path / "runs")
    cli(workspace, "wave-exit", "--run", str(run), "--through", "W1")
    context = cli(workspace, "refine-wave", "W2", "--run", str(run), "--prepare")["data"]
    with open_plan_store(workspace) as store:
        before = store.spine().to_dict()
        result = store.record_wave_refinement(
            context["spineRevision"],
            context["contextDigest"],
            json.dumps(response_for(context)).encode(),
        )
        assert result["proposedDetail"] is None and result["state"] == "unapplied"
        if mode == "incomplete":
            assert result["unrefinedSketchIds"] == ["SK2B"]
        else:
            assert result["cards"][0]["classification"] == "locked"
        with pytest.raises(ValueError, match="Whole target"):
            store.apply_wave_refinement(result["proposalId"], context["spineRevision"])
        assert store.spine().to_dict() == before
        assert store.wave_product_lock() == lock
        assert store._meta()["published_revision"] == publication
        assert len(store.published_manifest(publication)["waveScope"]["requiredObligationIds"]) == 6


def test_scope_refuses_unassigned_real_bead_and_requires_boundary_trace(tmp_path):
    workspace = setup_wave(tmp_path / "scope")
    with open_plan_store(workspace) as store:
        original = store.spine().to_dict()
    bad = deepcopy(original)
    bad["nodes"].append(
        draft_node(
            "OUT",
            "bead",
            dict(ownedFiles=["out.py"], dependsOn=[], lessons=[], proofModule="test_global.py"),
            ["R2"],
        )
    )
    with pytest.raises(ValueError, match="Unassigned"):
        compile_manifest(Spine(bad))
    bad = deepcopy(original)
    next(n for n in bad["nodes"] if n["id"] == "T1")["parents"] = ["R2"]
    with pytest.raises(ValueError, match="cumulative trace"):
        compile_manifest(Spine(bad))


def test_nonwave_compiler_keeps_exact_legacy_identity():
    from pathlib import Path

    from codex_harness.core.spine import _canonical, _hash, load_spine

    fixture = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"
    spine = load_spine(fixture)
    manifest = compile_manifest(spine)
    assert "waveScope" not in manifest and manifest["planRevision"] == spine.plan_revision()
    # Captured from the preserved be82 compiler, not generated by the new compiler.
    assert (
        _hash(_canonical(manifest))
        == "348a7518777a5cf8c8514330b84f4903fa07d599ae5f22e9f273304062230999"
    )


def test_complete_onewave_compiler_requires_no_new_declaration(tmp_path):
    workspace = setup_wave(tmp_path / "single")
    with open_plan_store(workspace) as store:
        data = store.spine().to_dict()
    data["nodes"] = [n for n in data["nodes"] if n["id"] != "DETAIL2"]
    nodes = {n["id"]: n for n in data["nodes"]}
    plan = nodes["MAP"]["fields"]
    plan["order"] = ["W1"]
    plan["waves"] = [plan["waves"][0]]
    plan["waves"][0].update(
        requirementIds=["R1", "R2"], exitScenarioIds=["X1", "X2"], outgoingContractIds=[]
    )
    plan["contracts"] = []
    for node in (nodes["X1"], nodes["X2"]):
        node["fields"].pop("waveBoundaryIntegrationTestId")
        node["fields"].pop("waveBoundaryIntegrationModule")
    data["nodes"].append(
        draft_node(
            "B2",
            "bead",
            dict(
                ownedFiles=["consumer.py"],
                dependsOn=["B1"],
                lessons=[],
                proofModule="test_global.py",
            ),
            ["R2"],
        )
    )
    nodes["DETAIL1"]["fields"]["beadIds"].append("B2")
    current = Spine(data)
    for obligation in plan["obligations"]:
        obligation["waveIds"] = ["W1"]
        for ref in obligation["sourceRefs"]:
            ref["revision"] = current.revision(ref["id"])
    current = Spine(data)
    manifest = compile_manifest(current)
    assert "waveScope" not in manifest
    assert manifest["planRevision"] == current.plan_revision()
    assert manifest["integration"]["proofModule"] == "test_global.py"


def _three_wave_dependency_impact_witness(tmp_path):
    workspace = setup_wave(tmp_path / "three")
    with open_plan_store(workspace) as store:
        before = store.spine().to_dict()
    nodes = before["nodes"]
    nodes.extend(
        [
            draft_node(
                "B2",
                "bead",
                dict(
                    ownedFiles=["second.py"],
                    dependsOn=["B1"],
                    lessons=[],
                    proofModule="tests.test_second",
                ),
                ["R2"],
            ),
            draft_node("R3", "requirement", {}, ["F1"]),
            draft_node("X3", "acceptance", dict(steps=["Third outcome"]), ["V1"]),
            draft_node(
                "B3",
                "bead",
                dict(
                    ownedFiles=["third.py"],
                    dependsOn=["B2"],
                    lessons=[],
                    proofModule="tests.test_third",
                ),
                ["R3"],
            ),
            draft_node(
                "U",
                "bead",
                dict(ownedFiles=["u.py"], dependsOn=[], lessons=[], proofModule="tests.test_u"),
                ["R1"],
            ),
            draft_node("T2", "test", dict(level="unit", module="tests.test_second"), ["B2"]),
            draft_node("T3", "test", dict(level="unit", module="tests.test_third"), ["B3"]),
        ]
    )
    current = Spine(before)
    by_id = {n["id"]: n for n in nodes}
    plan = by_id["MAP"]["fields"]
    plan["order"].append("W3")
    w3 = deepcopy(plan["waves"][1])
    w3.update(
        id="W3",
        requirementIds=["R3"],
        exitScenarioIds=["X3"],
        dependsOnWaves=["W2"],
        incomingContractIds=[],
        outgoingContractIds=[],
    )
    plan["waves"].append(w3)
    for oid, kind, source in (("O-R3", "requirement", "R3"), ("O-X3", "scenario", "X3")):
        plan["obligations"].append(
            dict(
                id=oid,
                kind=kind,
                sourceRefs=[dict(id=source, revision=current.revision(source))],
                waveIds=["W3"],
                criterion="Third outcome",
                requiredEvidence=["product-integration"],
                requiredForFullVision=True,
            )
        )
    next(o for o in plan["obligations"] if o["id"] == "O-GLOBAL")["waveIds"].append("W3")
    by_id["DETAIL1"]["fields"]["beadIds"].append("U")
    by_id["DETAIL2"]["fields"].update(detail="detailed", beadIds=["B2"], sketches=[])
    d3 = deepcopy(by_id["DETAIL2"])
    d3.update(id="DETAIL3", title="Third detail", text="Retain third content")
    d3["fields"].update(waveId="W3", beadIds=["B3"], verificationObligationIds=["O-R3", "O-X3"])
    nodes.append(d3)
    old = Spine(before)
    after = deepcopy(before)
    next(n for n in after["nodes"] if n["id"] == "B2")["text"] = (
        "Iterate observed list instead of dict.items"
    )
    new = Spine(after)
    observed = refinement_impact(old, new)
    assert observed["affectedBeads"] == ["B2", "B3"]
    assert observed["affectedTests"] == ["T2", "T3"]
    assert observed["impactedRequirementIds"] == ["R2", "R3"]
    assert observed["affectedWaves"] == ["W2", "W3"]
    assert observed["untouchedBeads"] == ["B1", "U"]
    assert old.node("B3") == new.node("B3")
    assert old.node("DETAIL3") == new.node("DETAIL3")
    retired = deepcopy(after)
    retired["nodes"] = [n for n in retired["nodes"] if n["id"] not in ("B2", "T2")]
    next(n for n in retired["nodes"] if n["id"] == "B3")["fields"]["dependsOn"] = []
    next(n for n in retired["nodes"] if n["id"] == "DETAIL2")["fields"].update(
        detail="sketch",
        beadIds=[],
        sketches=[
            dict(
                id="SK2",
                title="Retired detail",
                ownershipArea="Later owner",
                requirementIds=["R2"],
                expectedContractIds=["C"],
                risks=[],
                pendingProducerIds=["B1"],
                detailAfterWaveId="W1",
            )
        ],
    )
    removal = refinement_impact(old, Spine(retired))
    assert removal["affectedBeads"] == ["B2", "B3"] and removal["retiredBeads"] == ["B2"]
    broad = refinement_impact(old, new, protected_files_changed=True)
    assert broad["affectedBeads"] == ["B1", "B2", "B3", "U"]
    assert broad["proofInvalidationReasons"] == ["requirementFilesRevision changed"]
    return old, new


def test_three_wave_dependency_impact_before_any_body_write(tmp_path):
    _three_wave_dependency_impact_witness(tmp_path)


@pytest.mark.parametrize("edge", ["detail-after", "contract"])
def test_downstream_sketch_context_changes_without_body_writes(tmp_path, edge):
    old, new = _three_wave_dependency_impact_witness(tmp_path)

    def sketch(spine):
        data = spine.to_dict()
        data["nodes"] = [n for n in data["nodes"] if n["id"] not in {"B3", "T3"}]
        nodes = {n["id"]: n for n in data["nodes"]}
        nodes["DETAIL3"]["fields"].update(
            detail="sketch",
            beadIds=[],
            sketches=[
                dict(
                    id="SK3",
                    title="Third sketch",
                    ownershipArea="Third owner",
                    requirementIds=["R3"],
                    expectedContractIds=["C23"] if edge == "contract" else [],
                    risks=[],
                    pendingProducerIds=[],
                    detailAfterWaveId="W2" if edge == "detail-after" else "W1",
                )
            ],
        )
        if edge == "contract":
            plan = nodes["MAP"]["fields"]
            plan["contracts"].append(
                dict(
                    id="C23",
                    producerWaveId="W2",
                    consumerWaveIds=["W3"],
                    specRef=dict(id="CONTRACT", revision=spine.revision("CONTRACT")),
                    interface=dict(
                        symbol="next_links", inputShape={}, outputShape={}, errors=[], invariants=[]
                    ),
                    verificationObligationIds=["O23"],
                )
            )
            plan["waves"][1]["outgoingContractIds"].append("C23")
            plan["waves"][2]["incomingContractIds"].append("C23")
            plan["obligations"].append(
                dict(
                    id="O23",
                    kind="handoff",
                    sourceRefs=[dict(id="CONTRACT", revision=spine.revision("CONTRACT"))],
                    waveIds=["W2", "W3"],
                    criterion="Third interface",
                    requiredEvidence=["fixture-contract"],
                    requiredForFullVision=True,
                )
            )
        return Spine(data)

    before, after = sketch(old), sketch(new)
    result = refinement_impact(before, after)
    assert result["affectedWaves"] == ["W2", "W3"]
    assert result["impactedRequirementIds"] == ["R2", "R3"]
    assert before.node("DETAIL3") == after.node("DETAIL3")


@pytest.mark.parametrize(
    "raw",
    [
        b"\xef\xbb\xbf{}",
        b'{"a":1,"a":2}',
        b'{"a":{"b":1,"b":2}}',
        b'{"n":NaN}',
        b'{"n":Infinity}',
        b'{"n":1e999}',
        b"{}{}",
        b"[]",
        b"\xff",
    ],
)
def test_strict_captured_bytes_reject_before_projection(raw):
    from codex_harness.core.wave_refine import parse_json

    with pytest.raises(ValueError):
        parse_json(raw)


def test_static_git_blob_extraction_never_imports(tmp_path):
    from codex_harness.core.wave_refine import extract_interfaces

    git(tmp_path, "init")
    (tmp_path / "danger.py").write_text(
        "from dataclasses import dataclass as dc\nfrom typing import TypedDict as TD\n"
        "raise RuntimeError('must never import')\n@dc\nclass Link:\n name: str\n"
        "class Row(TD):\n value: int\n"
        "async def links(x: str) -> list[tuple[str, str]]:\n return []\n"
    )
    git(tmp_path, "add", "danger.py")
    git(
        tmp_path,
        "-c",
        "user.name=Source fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Static blob",
    )
    records = extract_interfaces(tmp_path, "HEAD", ["danger.py"])
    assert records[0]["symbols"][0]["fields"] == {"name": "str"}
    assert records[0]["aliases"]["TD"] == "TypedDict"
    assert records[0]["symbols"][1]["bases"] == ["TypedDict"]
    assert records[0]["symbols"][2]["returns"] == "list[tuple[str, str]]"
    with pytest.raises(ValueError, match="Unsafe interface path"):
        extract_interfaces(tmp_path, "HEAD", ["../danger.py"])


@pytest.mark.parametrize("case", ["logical-contract", "unknown-required-interface"])
def test_protected_contract_and_unknown_interface_cannot_be_self_classified_safe(tmp_path, case):
    from codex_harness.core.wave_refine import proposal

    workspace = setup_wave(tmp_path / case)
    with open_plan_store(workspace) as store:
        spine = store.spine()
    context = dict(
        contextDigest="a" * 64,
        targetDetail=spine.node("DETAIL2"),
        interfaces=[
            dict(
                blob="b" * 40,
                path="links.py",
                symbols=[
                    dict(
                        symbol="links",
                        kind="function",
                        returns="list[tuple[str, str]]",
                        returnShape="array",
                    )
                ],
            )
        ],
    )
    response = response_for(context)
    if case == "logical-contract":
        response["contradictions"] = [
            dict(
                sourceId="C",
                interfaceRefs=["b" * 40 + ":links"],
                resolution="Planner claims this can be resolved in detail",
                classification="detail",
            )
        ]
    else:
        context["interfaces"][0]["symbols"][0].update(returns="unknown", returnShape="unknown")
    result = proposal(spine, context, json.dumps(response).encode())
    assert result["state"] == "unapplied" and result["proposedDetail"] is None
    if case == "logical-contract":
        assert result["cards"][0]["classification"] == "locked"
    else:
        assert result["questions"]


def test_original_sketch_producer_dependency_cannot_be_dropped(tmp_path):
    from codex_harness.core.wave_refine import proposal

    workspace = setup_wave(tmp_path / "producer")
    with open_plan_store(workspace) as store:
        spine = store.spine()
    context = dict(
        contextDigest="a" * 64,
        targetDetail=spine.node("DETAIL2"),
        interfaces=[
            dict(
                path="links.py",
                blob="b" * 40,
                symbols=[
                    dict(symbol="links", kind="function", returns="list", returnShape="array")
                ],
            )
        ],
    )
    response = response_for(context)
    response["refinements"][0]["nodes"][0]["fields"]["dependsOn"] = []
    with pytest.raises(ValueError, match="producer"):
        proposal(spine, context, json.dumps(response).encode())


def test_boundary_uses_existing_unittest_filename_selector(tmp_path):
    workspace = setup_wave(tmp_path / "filename")
    with open_plan_store(workspace) as store:
        data = store.spine().to_dict()
    nodes = {n["id"]: n for n in data["nodes"]}
    nodes["V1"]["fields"]["validation"] = ["-B", "-m", "unittest", "discover", "-s", "tests", "-p"]
    nodes["B1"]["fields"]["proofModule"] = "test_first.py"
    for test, scenario, module in (("T1", "X1", "test_first.py"), ("TG", "X2", "test_global.py")):
        nodes[test]["fields"]["module"] = module
        nodes[scenario]["fields"]["waveBoundaryIntegrationModule"] = module
    spine = Spine(data)
    for obligation in nodes["MAP"]["fields"]["obligations"]:
        for ref in obligation["sourceRefs"]:
            ref["revision"] = spine.revision(ref["id"])
    manifest = compile_manifest(Spine(data), workspace / "template")
    assert manifest["integration"]["proofModule"] == "test_first.py"


def test_whitespace_plan_statement_never_sources_wave_lock(tmp_path):
    workspace = setup_wave(tmp_path / "blank")
    assert plan_confirmation(workspace, statement="   ") is None
    with open_plan_store(workspace) as store:
        with pytest.raises(ValueError, match="actual displayed-plan confirmation"):
            store.wave_product_lock()


def test_same_content_reconfirmation_keeps_publication_original_source(tmp_path):
    workspace = setup_wave(tmp_path / "reconfirm")
    original = plan_confirmation(workspace)
    publication = publish(workspace)
    save_scenarios(workspace)
    run = dispatch(workspace, tmp_path / "runs")
    cli(workspace, "advance", "--to", "plan")
    cli(
        workspace,
        "advance",
        "--to",
        "roundtable",
        "--by",
        "proxy:second-fixture",
        "--statement",
        "Second synthetic confirmation retained as separate history",
    )
    result = cli(workspace, "wave-exit", "--run", str(run), "--through", "W1")["data"]
    assert result["publicationRevision"] == publication
    with open_plan_store(workspace) as store:
        assert store.wave_product_lock() == original
        assert store._snapshot(publication)["waveProductLock"] == original
        last = store._latest_draft_event("phase-advanced")["waveProductLock"]
        assert last["confirmedBy"] == "proxy:second-fixture" and last["isProxy"] is True


def test_json_schema_only_producer_has_blob_anchored_root_reference(tmp_path):
    from codex_harness.core.wave_refine import extract_interfaces, proposal

    workspace = setup_wave(tmp_path / "json")
    schema_path = "links.schema.json"
    (workspace / schema_path).write_text(
        json.dumps({"type": "array", "items": {"type": "array", "items": {"type": "string"}}})
    )
    git(workspace, "add", schema_path)
    git(
        workspace,
        "-c",
        "user.name=Source fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Schema fixture",
    )
    with open_plan_store(workspace) as store:
        data = store.spine().to_dict()
    next(n for n in data["nodes"] if n["id"] == "B1")["fields"]["ownedFiles"] = [schema_path]
    next(n for n in data["nodes"] if n["id"] == "MAP")["fields"]["contracts"][0]["interface"][
        "symbol"
    ] = "#"
    spine = Spine(data)
    records = extract_interfaces(workspace, "HEAD", [schema_path])
    context = dict(
        contextDigest="a" * 64,
        targetDetail=spine.node("DETAIL2"),
        interfaces=records,
        boundary=dict(
            repo=str(workspace),
            candidateCommit=git(workspace, "rev-parse", "HEAD"),
            candidateTree=git(workspace, "rev-parse", "HEAD^{tree}"),
            productBytesRevision="c" * 64,
        ),
    )
    response = response_for(context)
    response["refinements"][0]["interfaceRefs"] = [records[0]["blob"] + ":#"]
    result = proposal(spine, context, json.dumps(response).encode())
    assert result["state"] == "complete"
    assert result["proposedDetail"]["fields"]["detail"] == "detailed"


def test_static_class_metadata_retains_optionality_and_defaults(tmp_path):
    from codex_harness.core.wave_refine import extract_interfaces

    git(tmp_path, "init")
    (tmp_path / "types.py").write_text(
        "from typing import TypedDict, Required, NotRequired\n"
        "from dataclasses import dataclass, field\n@dataclass(frozen=True)\nclass Link:\n"
        " name: str = 'x'\n values: list[str] = field(default_factory=list)\n"
        "class Row(TypedDict, total=False):\n name: Required[str]\n age: NotRequired[int]\n"
    )
    git(tmp_path, "add", "types.py")
    git(
        tmp_path,
        "-c",
        "user.name=Source fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Static metadata",
    )
    link, row = extract_interfaces(tmp_path, "HEAD", ["types.py"])[0]["symbols"]
    assert link["decorators"] == ["dataclass(frozen=True)"]
    assert link["defaults"] == {"name": "'x'", "values": "field(default_factory=list)"}
    assert row["keywords"] == {"total": "False"}
    assert row["fields"] == {"name": "Required[str]", "age": "NotRequired[int]"}


def test_static_interface_selection_refuses_oversize_before_capture(tmp_path):
    from codex_harness.core.wave_refine import extract_interfaces

    git(tmp_path, "init")
    (tmp_path / "large.py").write_text(
        "def links() -> list[str]:\n return []\n#" + "x" * (1024 * 1024)
    )
    git(tmp_path, "add", "large.py")
    git(
        tmp_path,
        "-c",
        "user.name=Source fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Bounded blob fixture",
    )
    with pytest.raises(ValueError, match="size bound"):
        extract_interfaces(tmp_path, "HEAD", ["large.py"])


def test_cli_record_receipt_never_echoes_exact_captured_response(tmp_path):
    workspace = setup_wave(tmp_path / "capture")
    plan_confirmation(workspace)
    publish(workspace)
    save_scenarios(workspace)
    run = dispatch(workspace, tmp_path / "runs")
    cli(workspace, "wave-exit", "--run", str(run), "--through", "W1")
    context = cli(workspace, "refine-wave", "W2", "--run", str(run), "--prepare")["data"]
    response = response_for(context)
    marker = "captured response — café Ω"
    response["refinements"][0]["replacedAssumptions"].append(marker)
    raw = json.dumps(response, ensure_ascii=False, indent=2).replace("\n", "\r\n").encode("utf-8")
    (workspace / "capture.json").write_bytes(raw)
    result = cli(
        workspace,
        "refine-wave",
        "W2",
        "--run",
        str(run),
        "--response",
        "@capture.json",
        "--expected-spine",
        context["spineRevision"],
    )
    rendered = json.dumps(result, ensure_ascii=False)
    assert "responseUtf8" not in rendered and marker not in rendered
    import hashlib

    assert result["data"]["responseRawSha256"] == hashlib.sha256(raw).hexdigest()
    with open_plan_store(workspace) as store:
        recorded = store._latest_draft_event("wave-refinement-recorded")
        assert recorded["responseUtf8"].encode("utf-8") == raw
        assert recorded["proposalId"] == result["data"]["proposalId"]


@pytest.mark.parametrize(
    "case", ["future-same-symbol", "future-renamed", "current-conflict", "foreign-observation"]
)
def test_contract_shapes_follow_target_and_actual_producer_observations(tmp_path, case):
    from codex_harness.core.acceptance import integrated_revision
    from codex_harness.core.wave_refine import digest, extract_interfaces, proposal

    before, _ = _three_wave_dependency_impact_witness(tmp_path)
    data = before.to_dict()
    data["nodes"] = [n for n in data["nodes"] if n["id"] not in {"B2", "T2", "B3", "T3"}]
    nodes = {n["id"]: n for n in data["nodes"]}
    plan = nodes["MAP"]["fields"]
    plan["contracts"][0]["interface"]["outputShape"] = {
        "type": "object" if case == "current-conflict" else "array"
    }
    nodes["TG"]["parents"].append("R3")
    nodes["X3"]["fields"].update(
        waveBoundaryIntegrationTestId="TF3", waveBoundaryIntegrationModule="test_future.py"
    )
    plan["waves"][1]["outgoingContractIds"].append("C23")
    plan["waves"][2]["incomingContractIds"].append("C23")
    plan["contracts"].append(
        dict(
            id="C23",
            producerWaveId="W2",
            consumerWaveIds=["W3"],
            specRef=dict(id="CONTRACT", revision=before.revision("CONTRACT")),
            interface=dict(
                symbol="links" if case == "future-same-symbol" else "future_links",
                inputShape={},
                outputShape={"type": "object"},
                errors=[],
                invariants=[],
            ),
            verificationObligationIds=["O23"],
        )
    )
    plan["obligations"].append(
        dict(
            id="O23",
            kind="handoff",
            sourceRefs=[dict(id="CONTRACT", revision=before.revision("CONTRACT"))],
            waveIds=["W2", "W3"],
            criterion="Retained future contract",
            requiredEvidence=["fixture-contract"],
            requiredForFullVision=True,
        )
    )
    for index in (2, 3):
        nodes[f"DETAIL{index}"]["fields"].update(
            detail="sketch",
            beadIds=[],
            sketches=[
                dict(
                    id=f"SK{index}",
                    title=f"Sketch {index}",
                    ownershipArea="Later owner",
                    requirementIds=[f"R{index}"],
                    expectedContractIds=["C"] if index == 2 else ["C23"],
                    risks=[],
                    pendingProducerIds=["B1"] if index == 2 else [],
                    detailAfterWaveId=f"W{index - 1}",
                )
            ],
        )
    current = Spine(data)
    for obligation in plan["obligations"]:
        for ref in obligation["sourceRefs"]:
            ref["revision"] = current.revision(ref["id"])
    spine = Spine(data)
    manifest = compile_manifest(spine)
    required = manifest["waveScope"]["requiredObligationIds"]
    assert manifest["waveScope"]["waveIds"] == ["W1"] and len(required) == 9
    workspace = tmp_path / "three"
    (workspace / "links.py").write_bytes((workspace / "reference/links.py").read_bytes())
    (workspace / ".gitignore").write_text(".codex-harness/\n")
    git(workspace, "add", "links.py", ".gitignore")
    git(
        workspace,
        "-c",
        "user.name=Source fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Candidate interface fixture",
    )
    assert not git(workspace, "status", "--porcelain")
    context = dict(
        targetDetail=spine.node("DETAIL2"),
        interfaces=extract_interfaces(workspace, "HEAD", ["links.py"]),
        boundary=dict(
            repo=str(workspace),
            candidateCommit=git(workspace, "rev-parse", "HEAD"),
            candidateTree=git(workspace, "rev-parse", "HEAD^{tree}"),
            productBytesRevision=integrated_revision(workspace),
        ),
    )
    if case == "foreign-observation":
        foreign = deepcopy(context["interfaces"][0])
        foreign.update(path="not-owned.py", blob="d" * 40)
        foreign["symbols"][0]["returnShape"] = "object"
        context["interfaces"].append(foreign)
    context["contextDigest"] = digest(context)
    raw = json.dumps(response_for(context)).encode()
    result = proposal(spine, context, raw)
    for name, value in (("spine", data), ("context", context), ("result", result)):
        (tmp_path / f"{name}.json").write_text(json.dumps(value, indent=2))
    (tmp_path / "response.json").write_bytes(raw)
    assert spine.to_dict() == data
    assert all(o["requiredForFullVision"] for o in plan["obligations"])
    if case == "current-conflict":
        assert result["state"] == "unapplied" and result["proposedDetail"] is None
        assert [c["sourceId"] for c in result["cards"]] == ["C"]
    else:
        assert result["state"] == "complete" and not result["cards"] and not result["questions"]
        candidate = Spine(
            {
                **spine.to_dict(),
                "nodes": [spine.node(k) for k in spine.ids() if k != "DETAIL2"]
                + result["proposedNodes"]
                + [result["proposedDetail"]],
            }
        )
        scope = compile_manifest(candidate)["waveScope"]
        assert scope["waveIds"] == ["W1", "W2"] and scope["laterSketchIds"] == ["SK3"]
        assert scope["requiredObligationIds"] == required
        assert scope["productLockDigest"] == manifest["waveScope"]["productLockDigest"]
        assert candidate.node("DETAIL3") == spine.node("DETAIL3")
        assert result["completedRealDelta"] == 0
