"""Source-only wave mechanism witnesses; no provider or product-completion credit."""

import contextlib
import io
import json
import subprocess
import sys
from copy import deepcopy

import pytest
from tests.python.core.planning_support import source_only_expansion
from tests.python.core.test_plan_store import draft_inputs, draft_node

from codex_harness.core.plan_cli import main
from codex_harness.core.plan_store import open_plan_store
from codex_harness.core.spine import Spine


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
    cli(
        workspace,
        "start",
        "--plan-mode",
        "legacy",
        "--idea",
        "Retain two outcomes",
        "--name",
        "links",
    )
    with open_plan_store(workspace) as store:
        inputs = draft_inputs(store)
        vision = store.spine().node("V1")
        vision["fields"].update(
            validation=["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
            requirementFiles=[],
        )
        store.upsert_node(vision)
        source_only_expansion(store)
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
    from tests.python.core.planning_support import source_only_b, source_only_confirmation

    cli(workspace, "compile", "--template", "template", "--out", "manifest.json")
    with open_plan_store(workspace) as store:
        source_only_confirmation(store, "align")
    cli(workspace, "advance", "--to", "expand")
    cli(workspace, "advance", "--to", "vision-lock")
    with open_plan_store(workspace) as store:
        source_only_confirmation(store, "vision-lock")
    for phase in ("features", "requirements", "tech-stack", "plan"):
        cli(workspace, "advance", "--to", phase)
    with open_plan_store(workspace) as store:
        source_only_b(
            store,
            reviewers=[
                dict(name="source-codex", provider="codex"),
                dict(name="source-claude", provider="claude"),
            ],
        )
    cli(workspace, "advance", "--to", "roundtable", "--by", by, "--statement", statement)
    with open_plan_store(workspace) as store:
        return store.wave_product_lock()


def publish(workspace):
    """Exercise source-only export mechanics; synthetic reviews earn no real credit."""
    from tests.python.core.planning_support import source_only_publication

    with source_only_publication():
        return _publish_source_only(workspace)


def _publish_source_only(workspace):
    from tests.python.core.planning_support import source_only_b

    with open_plan_store(workspace) as store:
        source_only_b(
            store,
            reviewers=[
                dict(name="source-codex", provider="codex"),
                dict(name="source-claude", provider="claude"),
            ],
        )
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
    """Catch lost frozen scope through refine/review/publish; A/B/C journeys have one wave."""
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
