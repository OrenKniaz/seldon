"""Source wiring evidence: real CLI products and controller, simulated workers only."""

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest
from tests.python.core.planning_support import (
    source_only_confirmation,
    source_only_expansion,
)
from tests.python.core.test_semantic_runner import WEB_PAGE, web_document

from codex_harness.core import dispatch, roundtable, worker_packet
from codex_harness.core.graph import load_plan
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PHASES, open_plan_store
from codex_harness.core.spine import Spine, compile_manifest


@pytest.fixture
def web_plan(semantic_plan):
    store, _ = semantic_plan
    reference = store.workspace / "design" / "direction.png"
    reference.parent.mkdir()
    reference.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8A"
            "AwMCAO+aX1sAAAAASUVORK5CYII="
        )
    )
    vision = store.spine().node("V1")
    document = web_document()
    document["scenarios"][0].update(requirementRefs=["R"], contractRefs=["CLI"])
    vision["fields"].update(
        productSurface="web",
        targets={"page": {"kind": "web", "entry": "index.html"}},
        scenarios=document,
        taste=dict(
            audience="Visitors booking a visit",
            tone="Calm",
            references=["Quiet spaces"],
            brand="Ink on white",
            requiredSections=["Welcome", "Details"],
            sourceRef="user:42",
        ),
        designRefs=[
            dict(
                path="design/direction.png",
                type="image",
                provenance="user:42",
                use="Colour and spacing",
                sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            )
        ],
    )
    store.upsert_node(vision)
    for key in ("C", "B"):
        bead = store.spine().node(key)
        bead["fields"]["ownedFiles"] = ["index.html"]
        store.upsert_node(bead)
    manifest = store.compile_template(store.workspace / "template")
    (store.workspace / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")
    return store, manifest


def test_captured_writes_are_atomic_and_reopen_on_every_read_surface(semantic_plan):
    """Protect saved-draft readability; late MCP validation missed UX5 value and F3 labels.

    Replay actual writes at the store owner; prove rejected bytes never reach CLI/MCP/HTTP.
    Missing requirement/design references stay readable draft gaps rather than write errors.
    """
    import io
    from contextlib import redirect_stdout
    from copy import deepcopy

    from tests.python.core.test_cockpit_server import get, running

    from codex_harness.core.plan_cli import main

    store, _ = semantic_plan
    fixtures = Path(__file__).resolve().parents[2] / "fixtures"
    captured = json.loads((fixtures / "f3-turn11-vision.json").read_text("utf-8"))
    store.upsert_node(captured)
    with open_plan_store(store.workspace) as reopened:
        assert reopened.spine().node("V1") == captured
        expected = reopened.status()
    malformed = json.loads((fixtures / "ux5-turn7-upsert.json").read_text("utf-8"))["node"]
    variants = [malformed, deepcopy(captured)]
    del variants[1]["fields"]["scenarios"]["scenarios"][0]["actions"][0]["cli"]["operation"]
    for candidate, field in zip(variants, ["value", "operation"], strict=True):
        before = store.path.read_bytes()
        with pytest.raises(ValueError, match=field):
            store.upsert_node(candidate)
        assert store.path.read_bytes() == before
        with open_plan_store(store.workspace) as reopened:
            assert reopened.spine().node("V1") == captured
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["--workspace", str(store.workspace), "--json", "status"]) == 0
    assert expected["plan_revision"] in output.getvalue()
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool("harness_plan_status", {})
        assert (
            not reply["isError"]
            and reply["structuredContent"]["plan_revision"] == expected["plan_revision"]
        )
    finally:
        server.close()
    with running(store.workspace, workspace=store.workspace) as connection:
        code, _, body = get(connection, "/api/status")
        assert (
            code == 200 and json.loads(body)["plan"]["plan_revision"] == expected["plan_revision"]
        )


def test_web_toolchain_drift_blocks_build_before_browser(web_plan, monkeypatch, tmp_path):
    """Catch stale RED across real tool-byte drift; existing drift checks omit Node."""
    store, _ = web_plan
    plan = load_plan(store.workspace)
    node_file = tmp_path / "node.exe"
    node_file.write_bytes(b"identity one; never launched")
    monkeypatch.setenv("SELDON_WEB_NODE", str(node_file))
    identity = dispatch._semantic_identity(plan)
    assert set(identity["webToolchain"]) == {"node", "playwright", "chromium", "adapter"}
    node_file.write_bytes(b"identity two; never launched")
    assert dispatch._semantic_identity(plan)["webToolchain"] != identity["webToolchain"]
    with pytest.raises(ValueError, match="current Contracts RED"):
        dispatch._semantic_validation(
            tmp_path, plan, {"contractsRed": dict(identity, state="RED")}, "build"
        )
    from codex_harness.core.acceptance import (
        acceptance_inputs,
        capture_acceptance,
        record_acceptance,
    )

    lock_vision(store)
    product = store.workspace / "identity-product"
    product.mkdir()
    (product / "index.html").write_text(WEB_PAGE, encoding="utf-8")
    captured = capture_acceptance(acceptance_inputs(store, product, subject="product"))
    assert captured["webToolchain"] == dispatch._semantic_identity(plan)["webToolchain"]
    node_file.write_bytes(b"identity three; never launched")
    with pytest.raises(ValueError, match="changed during acceptance"):
        record_acceptance(store, captured, [])
    assert store.connection.execute("SELECT count(*) FROM acceptance_runs").fetchone()[0] == 0


STUB = "import sys\nprint('SELDON-STUB hello', file=sys.stderr)\nsys.exit(3)\n"
PRODUCT = "print('Hello', end='')\n"
MARKER_FIRST = (
    "Write SELDON-STUB <operation> for the declared operation to stderr and exit 3 before "
    "argument parsing, importing product modules, or product I/O."
)


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
def semantic_plan(tmp_path_factory, monkeypatch):
    # This source fixture supplies only Codex; host installations are not its providers.
    monkeypatch.setattr(
        roundtable,
        "_executable",
        lambda provider, _: sys.executable if provider == "codex" else None,
    )
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


def lock_vision(store):
    source_only_expansion(store)
    for phase in PHASES[1 : PHASES.index("vision-lock") + 2]:
        if store.phase() in {"align", "vision-lock"}:
            source_only_confirmation(store, store.phase())
        store.advance(phase, by="source-test")


def test_semantic_advance_chain_retains_steps_before_first_refusal(semantic_plan):
    """Catch whole-chain rollback; the requirements-start test has no earlier successful step."""
    store, _ = semantic_plan
    lock_vision(store)
    vision = store.spine().node("V1")
    vision["fields"]["scenarios"]["scenarios"][0]["contractRefs"] = ["missing-contract"]
    store.upsert_node(vision)
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool("harness_advance", {"to_phase": "plan"})
        assert reply["isError"] and "plan-semantic-scenarios-invalid" in str(reply)
        assert reply["structuredContent"]["currentPhase"] == "requirements"
        assert reply["structuredContent"]["nextPhase"] == "tech-stack"
        assert store.phase() == "requirements"
        events = store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='phase-advanced' ORDER BY id"
        ).fetchall()
        assert [(json.loads(row[0])["from"], json.loads(row[0])["to"]) for row in events][-1] == (
            "features",
            "requirements",
        )
        server.close()
        with open_plan_store(store.workspace) as reopened:
            assert reopened.phase() == "requirements"
    finally:
        server.close()


@pytest.fixture
def semantic_acceptance_product(semantic_plan):
    store, _ = semantic_plan
    lock_vision(store)
    # Canonical spec work after lock must supersede the snapshot's old scenario ID.
    vision = store.spine().node("V1")
    vision["fields"]["scenarios"]["scenarios"][0]["id"] = "authored-greeting"
    store.upsert_node(vision)
    product = store.workspace / "product"
    product.mkdir()
    (product / "hello.py").write_text(PRODUCT, encoding="utf-8")
    return store, product


def execute(store, *, work_root=None, **kwargs):
    return dispatch.run_plan(
        store.workspace,
        work_root=work_root or store.workspace / "runs",
        max_parallel=1,
        fake_workers=workers(**kwargs),
    )


def test_stored_module_binding_dispatches_relative_argv_from_caller_cwd(semantic_plan):
    """Catch cwd/argv drift through the canonical producer; other fixtures use absolute args."""
    store, _ = semantic_plan
    vision = store.spine().node("V1")
    vision["fields"]["targets"] = {
        "greet": {"module": "probe", "argv": [{"path": "payload.bin", "relative": True}]}
    }
    s = vision["fields"]["scenarios"]["scenarios"][0]
    s["setup"] = {
        "dirs": ["data/caller"],
        "files": [{"path": "data/note.txt", "utf8": "word"}],
    }
    s["actions"][0].update(cwd="data/caller")
    captured = json.loads(
        (Path(__file__).resolve().parents[2] / "fixtures/f3-turn11-vision.json").read_text("utf-8")
    )
    label = next(
        arg
        for scenario in captured["fields"]["scenarios"]["scenarios"]
        for action in scenario["actions"]
        for arg in action["cli"]["argv"]
        if isinstance(arg, str) and "snowman" in arg
    )
    literals = [label, "https://example.test/a:b/../c"]
    s["actions"][0]["cli"]["argv"] = [{"path": "data/note.txt", "relative": True}, *literals]
    expected = dict(
        launch=["-B", "-m", "probe", "payload.bin", "../note.txt", *literals],
        cwd="caller",
        body="word",
        package="probe",
    )
    s["observe"] = [
        {"action": "hello", "exitEquals": 0, "stdoutJsonEquals": expected, "stderrEquals": ""}
    ]
    store.upsert_node(vision)
    with pytest.raises(ValueError, match="Both workers must own"):
        store.compile_template(store.workspace / "template")
    for key in ("C", "B"):
        bead = store.spine().node(key)
        bead["fields"]["ownedFiles"] = ["probe/__main__.py", "probe/__init__.py"]
        store.upsert_node(bead)
    manifest = store.compile_template(store.workspace / "template")
    (store.workspace / "sample.json").write_text(json.dumps(manifest))
    packet = worker_packet.build_packet(
        load_plan(store.workspace),
        {**manifest["beads"]["C"], "id": "C"},
        store.workspace / "template",
        None,
    )
    assert '"module": "probe"' in packet and "probe/__main__.py" in packet

    def write(root, source):
        (root / "probe").mkdir(exist_ok=True)
        (root / "probe/__init__.py").touch()
        (root / "probe/__main__.py").write_text(source, encoding="utf-8")

    record = dispatch.run_plan(
        store.workspace,
        work_root=store.workspace / "runs",
        max_parallel=1,
        fake_workers={
            "C": lambda root: write(root, STUB),
            "B": lambda root: write(
                root,
                "import json,sys\nfrom pathlib import Path\n"
                "print(json.dumps(dict(launch=sys.orig_argv[1:],cwd=Path.cwd().name,"
                "body=Path(sys.argv[2]).read_text(),package=__package__)))\n",
            ),
        },
    )
    assert record["status"] == dispatch.STATUS_MOCK_ONLY, record
    red = record["beads"]["C"]["integrated"]["semantic"]
    green = record["semanticValidation"]
    assert red["state"] == "RED" and not red["passed"]
    assert green["state"] == "PASS" and green["passed"] and green["linkedRed"]
    assert red["scenarioBinding"] == green["scenarioBinding"]
    actual = green["results"][0]["actions"]["hello"]
    assert json.loads(actual["stdout"]) == expected and actual["stderr"] == ""
    assert all(
        r["cleanup"]["quiescenceVerified"] and r["cleanup"]["activeProcessesAfterTeardown"] == 0
        for r in red["results"] + green["results"]
    )
    saved = json.loads((Path(record["repo"]).parent / "evidence/run.json").read_text())
    assert saved["semanticValidation"] == green
    assert not record["isLiveProductProof"]


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
    reason = record["beads"]["C"]["integrated"]["semantic"]["results"][0]["reason"]
    assert MARKER_FIRST in reason


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


def test_public_dirs_example_compiles_to_real_runner_with_deliberate_negative(semantic_plan):
    """Catch teaching/compile/runner drift; standalone setup tests bypass the typed producer."""
    from codex_harness.core.semantic_runner import run

    store, _ = semantic_plan
    server = PlannerServer(store.workspace)
    try:
        result = server.call_tool("harness_schema", {"kind": "vision"})
        assert not result["isError"], result
        example = result["structuredContent"]["semanticScenarios"]["fieldExample"]
        document = example["scenarios"]
        positive = document["scenarios"][0]
        assert positive["setup"]["dirs"] == ["data"]
        positive["setup"] = {"dirs": positive["setup"]["dirs"]}
        positive.update(requirementRefs=["R"], contractRefs=["CLI"])
        positive["observe"] = [
            {"action": "a1", "exitEquals": 0, "stdoutJsonEquals": []},
            {"path": "data/db.json", "fileJsonEquals": []},
        ]
        document["scenarios"].append(
            dict(
                positive,
                id="missing-parent-negative",
                setup={},
                actions=[{k: v for k, v in positive["actions"][0].items() if k != "cwd"}],
                observe=[
                    {"action": "a1", "exitEquals": 2, "stderrNonempty": True},
                    {"path": "data/db.json", "fileAbsent": True},
                ],
            )
        )
        example["targets"]["app"]["entry"] = "hello.py"  # Existing fixture-owned entry.
        vision = store.spine().node("V1")
        vision["fields"].update(scenarios=document, targets=example["targets"])
        result = server.call_tool("harness_upsert_node", {"node": vision})
        assert not result["isError"], result
        compiled = store.compile_template(store.workspace / "template")["semantic"]
        product = store.workspace / "template"
        (product / "hello.py").write_text(
            "import argparse,sys\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser();p.add_argument('--db');p.add_argument('operation')\n"
            "db=Path(p.parse_args().db)\n"
            "if not db.parent.is_dir():\n print('missing parent',file=sys.stderr);sys.exit(2)\n"
            "assert not any(db.parent.iterdir())\n"
            "db.write_text('[]',encoding='utf-8');print('[]')\n",
            encoding="utf-8",
        )
        results = run(compiled["scenarios"], product, compiled["targets"], "validation")
        assert [r["state"] for r in results] == ["PASS", "PASS"], results
        assert all(r["cleanup"]["quiescenceVerified"] for r in results)
    finally:
        server.close()


def test_required_cli_checks_and_captured_s8_finding_reach_review_packet(semantic_plan):
    """Protect required evidence and finding identity at packet/parser owner boundaries.

    Run25 HC promises had no producer; parsed-JSON tests missed framing, open stdin and help.
    Replay the actual S8 finding without a paid review and retain real process observations.
    """
    from tests.python.core.test_acceptance_recovery import protocol

    from codex_harness.core import acceptance

    store, _ = semantic_plan
    vision = store.spine().node("V1")
    suite = vision["fields"]["scenarios"]["scenarios"]
    base = suite[0]
    base.update(id="HC1-HC3", setup={"dirs": ["data/caller"]})
    base["actions"][0].update(cwd="data/caller")
    base["actions"][0]["cli"].update(stdin={"base64": "aGVsbG8="}, argv=["--label", "archive copy"])
    base["observe"] = [
        dict(action="hello", exitEquals=0, stdoutEndsWith="\n", stdoutContains='"bytes":5')
    ]
    for sid, args, code in [
        ("S8", ["--help"], 0),
        ("HC2", ["--label"], 2),
        ("HC2-unknown", ["--unknown"], 2),
        ("HC2-positional", ["unexpected"], 2),
        ("HC2-abbreviated", ["--lab", "x"], 2),
    ]:
        suite.append(
            dict(
                base,
                id=sid,
                actions=[
                    dict(
                        id="probe",
                        cli=dict(
                            target="greet",
                            operation="hello",
                            argv=args,
                            stdin=dict(holdOpen=True, exitWithin=5),
                        ),
                    )
                ],
                observe=[
                    dict(action="probe", exitEquals=code, stdinOpenAtExit=True, elapsedAtMost=5),
                    *(
                        [
                            dict(action="probe", stdoutContains=word)
                            for word in ("stdin", "bytes", "sha256", "label", "--label")
                        ]
                        if code == 0
                        else []
                    ),
                ],
            )
        )
    store.upsert_node(vision)
    compiled = store.compile_template(store.workspace / "template")["semantic"]
    assert compiled["scenarios"] == vision["fields"]["scenarios"]
    lock_vision(store)
    from tests.python.core.planning_support import source_only_b

    reviewers = source_only_b(
        store, reviewers=[dict(name=name, provider="codex") for name in ("left", "right")]
    )
    before = json.dumps(store.spine().node("V1"))
    product = store.workspace / "candidate"
    product.mkdir()
    (product / "hello.py").write_text(
        "import argparse,hashlib,json,sys\np=argparse.ArgumentParser(descr"
        "iption='stdin bytes sha256 label JSON',allow_abbrev=False);p.add"
        "_argument('--label');a=p.parse_args()\n"
        "raw=sys.stdin.buffer.read();sys.stdout.buffer.write((json.dumps("
        "dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),label"
        "=a.label),separators=(',',':'))+'\\n').encode())\n",
        encoding="utf-8",
    )
    result = acceptance.run_acceptance(store, product, subject="product")
    assert result["passed"], result
    evidence = acceptance._candidate_evidence(store, product, result)
    required = {r["scenarioId"]: r for r in evidence["requiredEvidence"]}
    assert set(required) == {
        "HC1-HC3",
        "S8",
        "HC2",
        "HC2-unknown",
        "HC2-positional",
        "HC2-abbreviated",
    }
    receipts = {row["id"]: row for row in result["results"]}
    hc1 = receipts["HC1-HC3"]["semantic"]["actions"]["hello"]
    assert Path(hc1["command"][2]).is_absolute() and Path(hc1["cwd"]).parts[-2:] == (
        "data",
        "caller",
    )
    assert hc1["stdout"].endswith("\n") and hc1["stdout"].count("\n") == 1
    held = receipts["S8"]["semantic"]["actions"]["probe"]
    assert held["stdinOpenAtExit"] and held["elapsedSeconds"] <= 5
    assert all(word in held["stdout"] for word in ("stdin", "bytes", "sha256", "label", "--label"))
    assert all(r["candidateRevision"] == result["revision"] for r in required.values())
    assert json.dumps(store.spine().node("V1")) == before
    prepared = acceptance._prepare_acceptance_review(store, product, reviewers, None)
    packet = prepared[4]
    assert '"requiredEvidence"' in packet and '"stdinOpenAtExit": true' in packet
    packet += roundtable.review_packet(store)
    assert '"S8": ["R"]' in packet
    finding = json.loads(
        (Path(__file__).resolve().parents[2] / "fixtures/run25-s8-finding.json").read_text("utf-8")
    )
    record, parsed = roundtable._parse_review_return(
        store,
        dict(provider="claude"),
        dict(nodeIds=["R"]),
        protocol("claude", text=json.dumps([finding])),
        mocked=False,
    )
    assert (
        not record["rejected"] and parsed[0]["node_id"] == "R" and parsed[0]["scenarioId"] == "S8"
    )
    assert parsed[0]["finding"] == finding["finding"] and not parsed[0]["outOfShard"]
    # A real story ID keeps its identity even if an authored scenario has the same ID.
    store.upsert_node(node("S8", "story", ["F"]))
    _, parsed = roundtable._parse_review_return(
        store,
        dict(provider="claude"),
        dict(nodeIds=["S8"]),
        protocol("claude", text=json.dumps([finding])),
        mocked=False,
    )
    assert parsed[0]["node_id"] == "S8" and "scenarioId" not in parsed[0]


def test_whole_suite_acceptance_stops_actions_and_holds_missing_cleanup_receipts(
    semantic_plan, monkeypatch
):
    """Protect owner stop and UNKNOWN cleanup after suite unification.

    Legacy command-owner tests miss multiple actions inside the semantic subprocess;
    native launch normally attaches a receipt, so explicitly inject its documented absence.
    """
    import time

    from codex_harness.core import acceptance, semantic_runner
    from codex_harness.core.planner_acceptance import AcceptanceOwner

    store, _ = semantic_plan
    marker, release = store.workspace / "started-action", store.workspace / "release-action"
    product = store.workspace / "product"
    product.mkdir()
    (product / "hello.py").write_text(
        "from pathlib import Path\nimport sys,time\n"
        f"Path({str(marker)!r}).write_text(sys.argv[1])\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(.01)\n"
        "print('done')\n"
    )
    vision = store.spine().node("V1")
    scenario = vision["fields"]["scenarios"]["scenarios"][0]
    scenario["actions"] = [
        dict(id=name, cli=dict(target="greet", operation="hello", argv=[name]))
        for name in ("one", "two")
    ]
    scenario["observe"] = [
        dict(action=name, exitEquals=0, stdoutEquals="done\n") for name in ("one", "two")
    ]
    store.upsert_node(vision)
    lock_vision(store)
    owner = AcceptanceOwner()
    attempt = owner.start(store, product, subject="product")
    owner.dispatch_after_flush(store)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            owner.drain(store)
            time.sleep(0.01)
        assert marker.read_text() == "one"
        owner.close(store)
    finally:
        release.touch()
        owner.thread.join(timeout=10)
    assert not owner.thread.is_alive() and marker.read_text() == "one"
    owner.drain(store)
    assert owner.status(store, attempt["attemptId"])["state"] == "unknown-interrupted"
    assert not store.connection.execute("SELECT 1 FROM acceptance_runs").fetchone()

    def lost_receipt(*args, **kwargs):
        raise OSError("launch outcome unavailable without cleanup receipt")

    monkeypatch.setattr(semantic_runner, "run_audit_process", lost_receipt)
    inputs = acceptance.acceptance_inputs(store, product, subject="product")
    result = acceptance.run_scenarios(product, inputs["scenarios"], semantic=inputs["semantic"])
    assert not result[0]["passed"] and result[0]["semantic"]["cleanup"] == {}
    assert "cleanup receipt" in result[0]["semantic"]["cleanupUncertainty"]
