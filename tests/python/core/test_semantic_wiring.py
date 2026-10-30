"""Source wiring evidence: real CLI products and controller, simulated workers only."""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from tests.python.core.planning_support import (
    source_controller_roundtable,
    source_only_confirmation,
    source_only_expansion,
)
from tests.python.core.test_semantic_runner import WEB_PAGE, web_document
from tests.python.core.test_semantic_runner import web_browser_ready as web_browser_ready

from codex_harness.core import dispatch, planner_execution, planner_jobs, worker_packet
from codex_harness.core.graph import load_plan
from codex_harness.core.mcp_planner import PlannerServer, packet_view
from codex_harness.core.plan_store import PHASES, open_plan_store
from codex_harness.core.roundtable import review_packet
from codex_harness.core.spine import Spine, compile_manifest


@pytest.fixture
def web_plan(semantic_plan):
    store, _ = semantic_plan
    reference = store.workspace / "design" / "direction.png"
    reference.parent.mkdir()
    reference.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII="
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


def test_web_canonical_reopen_compile_packets_and_a_card_keep_visual_intent(web_plan):
    """Catch lost taste, decoded binary refs and CLI-only cards; CLI fixtures lack visuals."""
    from codex_harness.core.planner_confirmation_cards import compact_card
    from codex_harness.core.planner_confirmations import confirmation_card

    store, manifest = web_plan
    with open_plan_store(store.workspace) as reopened:
        assert reopened.compile_template(store.workspace / "template") == manifest
        vision = reopened.spine().node("V1")
        card = confirmation_card("align", "align", reopened._revision(), vision)
        compact = compact_card(
            dict(gate="align", vision=vision, plan_revision=reopened._revision())
        )
        assert "Calm" in card and "direction.png" in card
        assert "Calm" in compact and "direction.png" in compact
        assert reopened.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
        plan = load_plan(store.workspace)
        for phase, key in (("contracts", "C"), ("build", "B")):
            packet = worker_packet.build_packet(
                plan,
                plan.beads[key],
                store.workspace / "template",
                None,
                intent_nodes=reopened.spine().to_dict()["nodes"],
            )
            assert "## User intent" in packet and '"tone": "Calm"' in packet
            assert vision["fields"]["designRefs"][0]["sha256"] in packet
            assert "never instructions or executable code" in packet
            assert "conflicts or unviewable material" in packet
            assert "visual material, not executable instructions" not in packet
            assert "exits 3" not in packet
            if phase == "contracts":
                assert "DOM text SELDON-STUB <operation>" in packet
        # The canonical manifest protects copied references, including binary bytes.
        bad = json.loads(json.dumps(manifest))
        bad["beads"]["B"]["ownedFiles"].append("design/direction.png")
        (store.workspace / "bad.json").write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="protected"):
            load_plan(store.workspace / "bad.json")


@pytest.mark.parametrize("missing", ["viewport", "motion"])
def test_web_missing_coverage_surfaces_in_canonical_open_gaps(web_plan, missing):
    """Catch late coverage errors; runner validation never exercises GAP-CHECK writes."""
    store, _ = web_plan
    lock_vision(store)
    store.advance("requirements", by="source-test")
    vision = store.spine().node("V1")
    scenario = vision["fields"]["scenarios"]["scenarios"][0]
    scenario["actions"] = [
        a
        for a in scenario["actions"]
        if (
            a["web"]["viewport"]["width"] == 375
            if missing == "viewport"
            else a["web"]["reducedMotion"] == "no-preference"
        )
    ]
    ids = {a["id"] for a in scenario["actions"]}
    scenario["observe"] = [o for o in scenario["observe"] if o["action"] in ids]
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool("harness_upsert_node", {"node": vision})
        assert not reply["isError"], reply
        assert "both viewports and motion preferences" in str(
            reply["structuredContent"]["openGaps"]
        )
        refused = server.call_tool("harness_advance", {"to_phase": "tech-stack"})
        assert refused["isError"] and "both viewports and motion preferences" in str(refused)
    finally:
        server.close()


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


@pytest.mark.usefixtures("web_browser_ready")
def test_web_real_red_validation_and_product_acceptance(web_plan, monkeypatch):
    """Catch web dispatch/acceptance mismatch with real Chromium; packet checks cannot render."""
    from codex_harness.core.acceptance import current_run, run_acceptance, run_invalid_reason
    from codex_harness.core.semantic_runner import run

    store, _ = web_plan
    lock_vision(store)
    manifest = store.compile_template(store.workspace / "template")
    (store.workspace / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")

    # Workers are fixtures; RED, Validation and acceptance observations use the real browser.
    def contracts(root):
        (root / "index.html").write_text("<!doctype html><h1>SELDON-STUB visit</h1>")

    def build(root):
        (root / "index.html").write_text(WEB_PAGE, encoding="utf-8")

    record = dispatch.run_plan(
        store.workspace,
        work_root=store.workspace / "runs",
        max_parallel=1,
        fake_workers={"C": contracts, "B": build},
        intent_nodes=store.locked_acceptance_spine().to_dict()["nodes"],
    )
    assert record["semanticContracts"]["state"] == "RED", record
    green = record["semanticValidation"]
    assert (
        green["state"] == "PASS"
        and green["webToolchain"] == record["semanticContracts"]["webToolchain"]
    )
    receipt = run_acceptance(store, Path(record["repo"]), subject="product")
    assert receipt["passed"] and receipt["webToolchain"] == green["webToolchain"]
    assert current_run(store) == receipt
    # Successful scenario evidence still is not a user's final acceptance grant.
    assert store.connection.execute("SELECT count(*) FROM acceptance_approvals").fetchone()[0] == 0
    monkeypatch.setenv("SELDON_WEB_NODE", str(store.workspace / "absent-node.exe"))
    assert run_invalid_reason(store, receipt) == "web-toolchain-changed"
    monkeypatch.delenv("SELDON_WEB_NODE")
    (Path(record["repo"]) / "index.html").write_text("<!doctype html><h1>SELDON-STUB visit</h1>")
    bad = run(
        load_plan(store.workspace).semantic["scenarios"],
        Path(record["repo"]),
        load_plan(store.workspace).semantic["targets"],
        "validation",
    )
    assert bad[0]["state"] == "ERROR", bad


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


def lock_vision(store):
    source_only_expansion(store)
    for phase in PHASES[1 : PHASES.index("vision-lock") + 2]:
        if store.phase() in {"align", "vision-lock"}:
            source_only_confirmation(store, store.phase())
        store.advance(phase, by="source-test")


@pytest.mark.parametrize(
    "path",
    [
        ("scenarios",),
        ("targets",),
        ("scenarios", "schema"),
        ("scenarios", "scenarios"),
        ("scenarios", "scenarios", 0, "requirementRefs"),
        ("scenarios", "scenarios", 0, "contractRefs"),
    ],
)
@pytest.mark.parametrize("malformed", [False, True], ids=["missing", "null"])
def test_semantic_advance_teaches_incomplete_required_fields(semantic_plan, path, malformed):
    """Catch raw key/type errors at public advance; runner tests bypass partial authoring."""
    store, _ = semantic_plan
    lock_vision(store)
    store.advance("requirements", by="source-test")
    vision = store.spine().node("V1")
    parent = vision["fields"]
    for key in path[:-1]:
        parent = parent[key]
    if malformed:
        parent[path[-1]] = None
    else:
        del parent[path[-1]]
    store.upsert_node(vision)
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool("harness_advance", {"to_phase": "tech-stack"})
        assert reply["isError"], reply
        message = str(reply)
        assert "plan-semantic-scenarios-invalid" in message
        assert str(path[-1]) in message
        assert "harness_schema kind=vision semanticScenarios" in message
        assert "unexpected tool-input-or-operation-error" not in message
        assert store.phase() == "requirements"
    finally:
        server.close()


@pytest.mark.parametrize("tool", ["harness_upsert_node", "harness_upsert_nodes"])
@pytest.mark.parametrize(
    "problem", ["contract", "requirement", "both", "operation", "ready", "bead"]
)
def test_semantic_writes_report_canonical_gaps_without_refusal(semantic_plan, tool, problem):
    """Catch late/duplicated checks or refused writes; advance tests never inspect write replies."""
    store, _ = semantic_plan
    lock_vision(store)
    store.advance("requirements", by="source-test")
    if problem == "bead":
        store.advance("tech-stack", by="source-test")
        store.advance("plan", by="source-test")
    vision = store.spine().node("V1")
    scenario = vision["fields"]["scenarios"]["scenarios"][0]
    batch = []
    if problem in {"contract", "both"}:
        scenario["contractRefs"] = ["D1"]
    if problem in {"requirement", "both"}:
        # A real canonical producer adds the requirement before the consumer checks coverage.
        requirement = node("R6", "requirement", ["F"])
        if tool == "harness_upsert_nodes":
            batch.append(requirement)
        else:
            store.upsert_node(requirement)
    if problem == "operation":
        del scenario["actions"][0]["cli"]["operation"]
    item = vision
    if problem == "bead":
        item = store.spine().node("B")
        item["fields"]["dependsOn"] = []
    server = PlannerServer(store.workspace)
    try:
        events = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        batch.append(item)
        args = {"node": item} if tool == "harness_upsert_node" else {"nodes": batch}
        reply = server.call_tool(tool, args)
        assert not reply["isError"], reply
        assert store.spine().node(item["id"]) == item
        assert store.connection.execute("SELECT count(*) FROM events").fetchone()[
            0
        ] == events + len(batch)
        assert store.phase() == ("plan" if problem == "bead" else "requirements")
        gaps = reply["structuredContent"]["openGaps"]
        text = reply["content"][0]["text"]
        assert text.splitlines()[-1].startswith("Next: ")
        assert sum(line.startswith("Next: ") for line in text.splitlines()) == 1
        if tool == "harness_upsert_nodes":
            assert reply["structuredContent"]["ids"] == [n["id"] for n in batch]
            assert reply["structuredContent"]["revisions"] == {
                n["id"]: store.spine().revision(n["id"]) for n in batch
            }
            assert reply["structuredContent"]["plan_revision"] == store.spine().plan_revision()
            assert "no harness_show_node/harness_plan_status read-back" in text
        if problem == "ready":
            assert gaps == [] and "Open before" not in text
            assert text.splitlines()[-1] == (
                "Next: harness_advance to_phase=plan; every intermediate gate still runs."
            )
            if tool == "harness_upsert_nodes":
                # The normal status-card consumer must still render the semantic turn;
                # write-only tests miss missing display fields in the guidance contract.
                status = server.call_tool("harness_plan_status", {})
                assert not status["isError"], status
                turn = status["structuredContent"]["planningTurn"]
                assert turn["schemaTarget"] is None
                assert "harness_upsert_nodes" in turn["writeTarget"]["mcp"]
        else:
            assert (
                gaps
                and "Open before " + ("roundtable" if problem == "bead" else "tech-stack") in text
            )
            if problem in {"contract", "both"}:
                assert "D1 missing/stale (contractRefs in first-moment)" in gaps
            if problem in {"requirement", "both"}:
                assert "R6 uncovered by any scenario" in gaps
            if problem == "operation":
                assert "operation" in str(gaps)
            if problem == "bead":
                assert "Contracts -> Build" in str(gaps)
            assert text.splitlines()[-2].startswith("Open before ")
        # The authoritative gate must still reject exactly the same persisted bad data.
        next_phase = "roundtable" if problem == "bead" else "tech-stack"
        advance = server.call_tool("harness_advance", {"to_phase": next_phase})
        assert advance["isError"] == (problem != "ready"), advance
        if gaps:
            assert all(gap in str(advance) for gap in gaps)
            assert store.phase() == ("plan" if problem == "bead" else "requirements")
    finally:
        server.close()


def test_semantic_advance_admits_complete_canonical_data_unchanged(semantic_plan):
    """Catch chained gate bypass/event loss; single-step tests miss repair -> plan journeys."""
    store, manifest = semantic_plan
    lock_vision(store)
    store.advance("requirements", by="source-test")
    before = store.spine().to_dict()
    server = PlannerServer(store.workspace)
    try:
        vision = store.spine().node("V1")
        original = vision["fields"]["scenarios"]["scenarios"][0]["contractRefs"]
        vision["fields"]["scenarios"]["scenarios"][0]["contractRefs"] = ["D1"]
        store.upsert_node(vision)
        refused = server.call_tool("harness_advance", {"to_phase": "plan"})
        assert refused["isError"], refused
        assert "author design node D1 (contractRefs)" in str(refused)
        assert refused["structuredContent"]["currentPhase"] == "requirements"
        assert refused["structuredContent"]["nextPhase"] == "tech-stack"
        assert "Still in requirements; next legal phase: tech-stack." in str(refused)
        assert store.phase() == "requirements"
        vision["fields"]["scenarios"]["scenarios"][0]["contractRefs"] = original
        store.upsert_node(vision)

        def records():
            return [
                (row[0], json.loads(row[1]))
                for row in store.connection.execute(
                    "SELECT kind, payload_json FROM events ORDER BY id"
                )
            ]

        start = len(records())
        reply = server.call_tool("harness_advance", {"to_phase": "plan"})
        assert not reply["isError"], reply
        assert "Entered plan planning phase." in str(reply)
        assert "currentPhase" not in reply.get("structuredContent", {})
        assert "nextPhase" not in reply.get("structuredContent", {})
        assert store.phase() == "plan"
        chained = records()[start:]
        assert [(kind, event["from"], event["to"]) for kind, event in chained] == [
            ("phase-advanced", "requirements", "tech-stack"),
            ("phase-advanced", "tech-stack", "plan"),
        ]
        same = server.call_tool("harness_advance", {"to_phase": "plan"})
        assert same["isError"] and "plan-phase-order" in str(same)
        assert not server.call_tool("harness_advance", {"to_phase": "requirements"})["isError"]
        start = len(records())
        for phase in ("tech-stack", "plan"):
            assert not server.call_tool("harness_advance", {"to_phase": phase})["isError"]
        assert records()[start:] == chained
        assert store.spine().to_dict() == before
        assert compile_manifest(store.spine())["semantic"] == manifest["semantic"]
    finally:
        server.close()


def test_semantic_advance_chain_retains_steps_before_first_refusal(semantic_plan):
    """Catch whole-chain rollback; the requirements-start test has no earlier successful step."""
    store, _ = semantic_plan
    lock_vision(store)
    vision = store.spine().node("V1")
    del vision["fields"]["scenarios"]["scenarios"][0]["actions"][0]["cli"]["operation"]
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


@pytest.mark.parametrize(
    "current,target,consent",
    [
        ("align", "requirements", {}),
        ("expand", "features", {}),
        ("vision-lock", "requirements", {}),
        ("requirements", "roundtable", {}),
        ("requirements", "publish", {}),
        ("tech-stack", "roundtable", {}),
        ("plan", "publish", {}),
        ("requirements", "plan", {"user_statement": "Approve"}),
        ("requirements", "plan", {"by": "user"}),
    ],
)
def test_semantic_advance_chain_keeps_strict_boundaries(semantic_plan, current, target, consent):
    """Catch consent/review crossing; single-step gate tests cannot detect eager chain writes."""
    store, _ = semantic_plan
    source_only_expansion(store)
    while PHASES.index(store.phase()) < PHASES.index(current):
        if store.phase() in {"align", "vision-lock"}:
            source_only_confirmation(store, store.phase())
        store.advance(PHASES[PHASES.index(store.phase()) + 1], by="source-test")
    before = store.connection.execute("SELECT * FROM events ORDER BY id").fetchall()
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool("harness_advance", {"to_phase": target, **consent})
        assert reply["isError"], reply
        expected = "Confirmation fields require" if consent else "plan-phase-order"
        assert expected in str(reply)
        assert reply["structuredContent"]["currentPhase"] == current
        assert store.phase() == current
        assert store.connection.execute("SELECT * FROM events ORDER BY id").fetchall() == before
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


@pytest.mark.parametrize("output,passed", [(PRODUCT, True), ("print('Wrong')\n", False)])
def test_semantic_accept_cli_runs_canonical_product(semantic_acceptance_product, output, passed):
    """Catch legacy prose execution/false PASS; prior CLI tests author legacy commands only."""
    import subprocess
    import sys

    from codex_harness.core.acceptance import current_run

    store, product = semantic_acceptance_product
    (product / "hello.py").write_text(output, encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "codex_harness.core.plan_cli",
            "--workspace",
            str(store.workspace),
            "--json",
            "accept-run",
            "--repo",
            str(product),
            "--subject",
            "product",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == (0 if passed else 2), completed.stdout + completed.stderr
    reply = json.loads(completed.stdout)["data"]
    assert reply["passed"] is passed, reply
    assert [r["id"] for r in reply["results"]] == ["authored-greeting"], reply
    observation = reply["results"][0]["semantic"]
    assert observation["state"] == ("PASS" if passed else "FAIL"), observation
    assert observation["actions"]["hello"]["stdout"].replace("\r\n", "\n") == (
        "Hello" if passed else "Wrong\n"
    )
    assert observation["cleanup"]["quiescenceVerified"]
    assert current_run(store)["id"] == reply["id"]


@pytest.mark.parametrize("key", ["text", "title", "quality_bar"])
def test_locked_vision_refuses_incidental_wording_without_mutation(semantic_plan, key):
    """Protect locked wording; catches silent rewrites that scenario-runner tests never upsert."""
    store, _ = semantic_plan
    lock_vision(store)
    original = store.spine().node("V1")
    changed = store.spine().node("V1")
    (changed if key in {"text", "title"} else changed["fields"])[key] += " revised"
    revision = store._revision()
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool("harness_upsert_node", {"node": changed})
        assert reply["isError"] and "vision-locked-wording" in str(reply)
        assert key in str(reply) and "reopen_vision_lock=true" in str(reply)
    finally:
        server.close()
    assert store.spine().node("V1") == original and store._revision() == revision
    assert not store._meta()["vision_lock_stale"]


@pytest.mark.parametrize(
    "mutation",
    ["", "float", "stderr", "error", "create", "mkdir", "rewrite", "remove"],
)
def test_stored_byte_cwd_scenarios_detect_observable_contract_violations(semantic_plan, mutation):
    """Catch byte/cwd/type/text/tree false passes; existing compiled fixtures use root/text only."""
    from codex_harness.core.semantic_runner import run

    store, _ = semantic_plan
    vision = store.spine().node("V1")
    scenarios = []
    for content, exit_code in [({"utf8": "x"}, 0), ({"base64": "/w=="}, 2)]:
        scenarios.append(
            dict(
                id=f"bytes-{exit_code}",
                requirementRefs=["R"],
                contractRefs=["CLI"],
                setup=dict(
                    files=[
                        dict(path="data/caller/payload.bin", **content),
                        dict(path="data/caller/sibling", utf8="keep"),
                    ],
                    dirs=["data/caller/empty"],
                    snapshots=[dict(id="tree", path="data/caller")],
                ),
                actions=[
                    dict(
                        id="a",
                        cwd="data/caller",
                        cli=dict(target="greet", operation="hello", argv=["payload.bin"]),
                    )
                ],
                observe=[
                    dict(
                        action="a",
                        exitEquals=exit_code,
                        **(
                            dict(stdoutJsonEquals={"bytes": 1}, stderrEquals="")
                            if exit_code == 0
                            else dict(stdoutEquals="", stderrContains="payload.bin")
                        ),
                    ),
                    dict(path="data/caller", bytesUnchangedFrom="tree"),
                ],
                verdict="all",
            )
        )
    vision["fields"]["scenarios"]["scenarios"] = scenarios
    store.upsert_node(vision)
    compiled = store.compile_template(store.workspace / "template")["semantic"]
    product = store.workspace / "template"
    (product / "hello.py").write_text(
        "import runpy\nrunpy.run_module('probe', run_name='__main__')\n", encoding="utf-8"
    )
    changes = {
        "": "",
        "float": "size = float(size)",
        "stderr": "print(' ', file=sys.stderr)",
        "error": "label = 'unrelated'",
        "create": "Path('extra').write_bytes(b'new')",
        "mkdir": "Path('new-empty').mkdir()",
        "rewrite": "Path('sibling').write_bytes(b'changed')",
        "remove": "Path('empty').rmdir()",
    }
    if not mutation:
        (product / "probe.py").write_text(STUB, encoding="utf-8")
        red = run(compiled["scenarios"], product, compiled["targets"], "contracts")
        assert [r["state"] for r in red] == ["FAIL", "FAIL"], red
    (product / "probe.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "assert Path.cwd().name == 'caller'\n"
        "raw = Path(sys.argv[1]).read_bytes()\nsize = len(raw)\nlabel = sys.argv[1]\n"
        + changes[mutation]
        + "\n"
        "if raw == b'\\xff':\n print('invalid '+label,file=sys.stderr);sys.exit(2)\n"
        "print(json.dumps({'bytes':size}))\n",
        encoding="utf-8",
    )
    results = run(compiled["scenarios"], product, compiled["targets"], "validation")
    assert [r["state"] for r in results] == (
        ["PASS", "PASS"]
        if not mutation
        else ["PASS", "FAIL"]
        if mutation == "error"
        else ["FAIL", "PASS"]
        if mutation in ("float", "stderr")
        else ["FAIL", "FAIL"]
    ), results
    assert all(r["cleanup"]["quiescenceVerified"] for r in results)
    assert all(r["cleanup"]["activeProcessesAfterTeardown"] == 0 for r in results)


def test_locked_vision_explicit_reopen_marks_lock_stale(semantic_plan):
    """Protect deliberate edits; catches a dropped MCP reopen flag absent in store-only tests."""
    store, _ = semantic_plan
    lock_vision(store)
    changed = store.spine().node("V1")
    changed["text"] += " revised"
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool(
            "harness_upsert_node", {"node": changed, "reopen_vision_lock": True}
        )
        assert not reply["isError"], reply
    finally:
        server.close()
    assert store.spine().node("V1") == changed and store._meta()["vision_lock_stale"]


def test_locked_vision_allows_scenario_only_edit(semantic_plan):
    """Protect post-lock spec work; catches overbroad locking absent in pre-lock compile tests."""
    store, _ = semantic_plan
    lock_vision(store)
    changed = store.spine().node("V1")
    changed["fields"]["scenarios"]["scenarios"][0]["id"] = "revised-first-moment"
    store.upsert_node(changed)
    assert store.spine().node("V1") == changed and not store._meta()["vision_lock_stale"]


def test_vision_lock_chat_result_leads_with_verbatim_instruction(semantic_plan):
    """Protect full card display; catches MCP prefix/summary cues missed by native-card tests."""
    store, _ = semantic_plan
    source_only_expansion(store)
    source_only_confirmation(store, "align")
    store.advance("expand", by="source-test")
    store.advance("vision-lock", by="source-test")
    server = PlannerServer(store.workspace)
    try:
        reply = server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "harness_prepare_chat_confirmation",
                        "arguments": {
                            "gate": "vision-lock",
                            "expected_workspace": str(store.workspace),
                        },
                    },
                }
            )
        )["result"]
        assert not reply["isError"], reply
        text = reply["content"][0]["text"]
        instruction = (
            "Your reply must be exactly the text between the markers, copied in full; "
            "do not summarise, shorten or add to it."
        )
        assert text.startswith(instruction)
        before, card = text.split("<<<SHOW-VERBATIM\n", 1)
        card, footer = card.split("\nSHOW-VERBATIM>>>", 1)
        assert instruction in before and "For the agent:" in footer
        for words in (
            "Offline greeting CLI",
            "first-moment",
            "Retain every cent",
            "Shares add up",
            "accept",
            "reject",
            "defer",
        ):
            assert words in card
        assert "For the agent:" not in card
    finally:
        server.close()


def execute(store, *, work_root=None, **kwargs):
    return dispatch.run_plan(
        store.workspace,
        work_root=work_root or store.workspace / "runs",
        max_parallel=1,
        fake_workers=workers(**kwargs),
    )


@pytest.mark.parametrize("mode", [None, "legacy", "semantic-v1"])
def test_mode_defaults_only_at_creation_and_survives_resume(tmp_path, mode):
    """Catch default/resume conversion and explicit conflicts; runner tests bypass plan start."""
    server = PlannerServer(tmp_path)
    args = dict(idea="CLI", name="saved")
    if mode is not None:
        args["plan_mode"] = mode
    assert not server.call_tool("harness_plan_start", args)["isError"]
    semantic = mode != "legacy"
    assert server.store.spine().semantic == semantic
    if semantic:
        assert server.store.spine().node("V1")["fields"]["planMode"] == "semantic-v1"
    revision = server.store.spine().plan_revision()
    server.close()
    server = PlannerServer(tmp_path)
    assert not server.call_tool("harness_plan_start", dict(idea="Ignored", name="saved"))["isError"]
    assert server.store.spine().semantic == semantic
    assert server.store.spine().plan_revision() == revision
    conflicting = "legacy" if semantic else "semantic-v1"
    refused = server.call_tool(
        "harness_plan_start", dict(idea="CLI", name="saved", plan_mode=conflicting)
    )
    assert refused["isError"] and "immutable" in str(refused)
    vision = server.store.spine().node("V1")
    vision["fields"]["planMode"] = conflicting
    with pytest.raises(ValueError, match="immutable"):
        server.store.upsert_node(vision)
    server.close()
    with open_plan_store(tmp_path) as reopened:
        assert reopened.spine().semantic == semantic
        assert reopened.spine().plan_revision() == revision


def test_scenario_data_compiles_and_keeps_review_publish_and_packet_gates(
    semantic_plan, monkeypatch, capsys
):
    """Protect suite inspection and summary-first teaching after run14's unnecessary full fetch.

    Size-only packet checks miss absent guidance about when exact packet text is needed.
    """
    from tests.python.core.test_mcp_planner import rpc, workspace_wire

    from codex_harness.core import plan_cli

    store, manifest = semantic_plan
    vision = store.spine().node("V1")
    vision["fields"]["targets"] = {"greet": {"module": "greeting", "argv": []}}
    second = json.loads(json.dumps(vision["fields"]["scenarios"]["scenarios"][0]))
    second["id"] = "repeat-greeting"
    vision["fields"]["scenarios"]["scenarios"].append(second)
    store.upsert_node(vision)
    for key in ("C", "B"):
        bead = store.spine().node(key)
        bead["fields"]["ownedFiles"] = ["greeting/__init__.py", "greeting/__main__.py"]
        store.upsert_node(bead)
    assert "proofModule" not in manifest["beads"]["B"]
    assert not store.spine().ids("test") and not store.spine().coverage_gaps()
    assert not (store.workspace / "template/tests").exists()
    source_only_expansion(store)
    while store.phase() != "roundtable":
        if store.phase() in {"align", "vision-lock"}:
            source_only_confirmation(store, store.phase())
        store.advance(PHASES[PHASES.index(store.phase()) + 1], by="source-test")
    manifest = store.compile_template(store.workspace / "template")
    (store.workspace / "sample.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="successful review") as error:
        store.advance("publish", by="source-test")
    assert "passing audit" not in str(error.value)
    with pytest.raises(ValueError, match="Enter publish"):
        store.publish(by="source-test", export_dir=store.workspace / "published")
    canonical = packet_view(store, "B")
    packet = canonical["packet"]
    assert packet.encode("utf-8") == worker_packet.build_packet(
        load_plan(store.workspace),
        {"id": "B", **manifest["beads"]["B"]},
        store.workspace / "template",
        None,
        provider="codex",
        proof_level=manifest.get("proofLevel"),
        intent_nodes=store.locked_acceptance_spine().to_dict()["nodes"],
    ).encode("utf-8")
    # The real confirmed store -> packet consumer must retain intent; builder-only
    # coverage cannot catch a caller dropping it (the run19 failure).
    assert "## User intent\n" in packet
    assert "Offline greeting CLI" in packet and "R: R: offline greeting CLI" in packet
    locked = store.locked_acceptance_spine().node("V1")
    assert locked["fields"]["quality_bar"] in packet
    assert locked["fields"]["done_feels_like"] in packet
    assert "stop and report the conflict instead of choosing." in packet
    assert len(packet) <= worker_packet.MAX_CHARACTERS
    assert "SCENARIOS.json" in packet and "Hello" in packet and "Implement the product" in packet
    # The scenario spec is large; it must appear once, not inline and again as a file.
    assert packet.count('"first-moment"') == 1
    before = list(store.connection.iterdump())
    server = PlannerServer(store.workspace)
    try:

        def read(tool_name="harness_packet_view", **arguments):
            reply = workspace_wire(
                server, tool_name, expected_workspace=str(store.workspace), **arguments
            )
            assert not reply["isError"], reply
            return reply

        summary = read(id="B")
        assert summary["structuredContent"] == {
            "characters": len(packet),
            "bytes": len(packet.encode("utf-8")),
            "semanticSuite": canonical["semanticSuite"],
        }
        suite = summary["structuredContent"]["semanticSuite"]
        assert suite["mode"] == "semantic-v1"
        assert suite["scenarioIds"] == ["first-moment", "repeat-greeting"]
        assert suite["targets"] == {"greet": {"module": "greeting", "argv": []}}
        assert "semantic.scenarios and semantic.targets" in suite["teaching"]
        assert "legacy Current acceptance scenarios section may be empty" in suite["teaching"]
        assert "not execution proof" in suite["teaching"]
        assert packet not in summary["content"][0]["text"]
        assert "summary + semanticSuite first" in summary["content"][0]["text"]
        assert (
            "include_packet=true only when those cannot answer a concrete review/debug question "
            "about exact packet text" in summary["content"][0]["text"]
        )
        assert read(id="B", include_packet=False) == summary
        full = read(id="B", include_packet=True)
        assert full["structuredContent"] == canonical
        assert packet in full["content"][0]["text"]
        schema = read("harness_schema", tool="harness_packet_view")["structuredContent"]
        assert schema["inputSchema"]["properties"]["include_packet"] == {
            "type": "boolean",
            "default": False,
        }
        tools = rpc(server, "tools/list")["result"]["tools"]
        teaching = next(tool for tool in tools if tool["name"] == "harness_packet_view")
        assert "Reviewers receive their canonical packets independently" in teaching["description"]
        assert "default summary + semanticSuite first" in teaching["description"]
        assert (
            "only when those cannot answer a concrete review/debug question"
            in teaching["description"]
        )
        assert plan_cli.main(["--workspace", str(store.workspace), "--json", "packet", "B"]) == 0
        assert json.loads(capsys.readouterr().out)["data"] == canonical
        sizes = {
            key: len(json.dumps(reply, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            for key, reply in (("default", summary), ("full", full))
        }
        assert sizes["default"] < 2500 < sizes["full"], sizes
        (store.workspace / "packet-bytes.json").write_text(json.dumps(sizes), encoding="utf-8")
    finally:
        server.close()
    assert list(store.connection.iterdump()) == before
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
    # Worker start pre-gates on resume; semantic plans have no audit to hold on.
    from codex_harness.core.planner_resume import resume_plan

    held = resume_plan(store.workspace, expected_revision=revision)
    assert held["reasonCode"] == "native-execution-confirmation-required", held
    monkeypatch.setattr(worker_packet, "MAX_CHARACTERS", 10)
    with pytest.raises(ValueError, match="exceed"):
        packet_view(store, "B")


def review_with_findings(store, *, severity="major"):
    source_only_expansion(store)
    while store.phase() != "roundtable":
        if store.phase() in {"align", "vision-lock"}:
            source_only_confirmation(store, store.phase())
        store.advance(PHASES[PHASES.index(store.phase()) + 1], by="source-test")
    revision = store.compile_template(store.workspace / "template")["planRevision"]
    result = source_controller_roundtable(
        store,
        process_stub=lambda *_: (
            0,
            json.dumps(
                [
                    dict(node_id=key, severity=severity, finding=message, suggestion="Repair it")
                    for key, message in (
                        ("F", "Clarify greeting scenario"),
                        ("D", "Document choice"),
                    )
                ]
            ),
        ),
        reviewers=[
            {"name": "source-codex", "provider": "codex"},
            {"name": "source-claude", "provider": "claude"},
        ],
        executable_for=lambda p: p,
    )
    assert result["invalid"] == 0 and result["findings"] == 4, result
    return revision


def repair_review_scenarios(store, *, leave_open=False):
    from codex_harness.core.roundtable import findings

    vision = store.spine().node("V1")
    vision["fields"]["scenarios"]["scenarios"][0]["setup"]["files"] = [
        {"path": "data/input.txt", "utf8": "hello"}
    ]
    store.upsert_node(vision)
    store.compile_template(store.workspace / "template")
    store.resolve_findings(
        [
            dict(
                id=row["id"],
                status="resolved" if row["node_id"] == "F" else "deferred",
                resolution="Scenario repaired; choice deferred with rationale",
            )
            for row in findings(store)
            if not leave_open or row["node_id"] != "D"
        ]
    )


def test_review_carries_scenario_and_named_repairs_through_publication(semantic_plan):
    """Protect repair→publish; catch redundant reviews missed by clean-review tests."""
    from codex_harness.core.planner_views import status_card

    store, _ = semantic_plan
    original = review_with_findings(store)
    repair_review_scenarios(store)
    for repair in ("First clarification", "Second clarification"):
        feature = store.spine().node("F")
        feature["text"] = repair
        store.upsert_node(feature)
        current = store.compile_template(store.workspace / "template")["planRevision"]
        assert current != original
        reviews = store.successful_reviews(current)
        assert len(reviews) == 2
        assert all(r["carriedForward"] and r["carriedFromRevision"] == original for r in reviews)
    state = store.status()
    assert state["reviewResolution"]["review"]["current"] is True
    assert state["reviewResolution"]["review"]["carriedForward"] is True
    assert state["reviewResolution"]["nextAction"]["kind"] == "attempt_phase_advance"
    assert "carried forward" in state["reviewResolution"]["nextAction"]["message"]
    assert "review current (carried forward after repairs)" in status_card(state, [])
    store.advance("publish", by="source-test")
    published = store.publish(by="source-test", export_dir=store.workspace / "published")
    assert published == current
    assert store.status()["pending_gate"] is None
    selection = [
        dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium") for b in ("C", "B")
    ]
    scope = planner_execution.execution_scope(
        store, published, selection, max_parallel=1, call_cap=2
    )
    assert scope["publicationRevision"] == published
    assert store.connection.execute("SELECT count(*) FROM review_runs").fetchone()[0] == 2


def test_review_repair_carries_canonical_derived_bytes_before_and_after_compile(semantic_plan):
    """Catch stale generated bytes blocking repairs; existing carry tests compile first."""
    from codex_harness.core.roundtable import findings

    store, _ = semantic_plan
    review_with_findings(store)
    vision = store.spine().node("V1")
    vision["fields"]["scenarios"]["scenarios"][0]["setup"]["files"] = [
        {"path": "data/input.txt", "utf8": "hello"}
    ]
    store.upsert_node(vision)
    store.resolve_findings(
        [
            dict(id=row["id"], status="resolved", resolution="Repaired scenario setup")
            for row in findings(store)
        ]
    )
    assert len(store.successful_reviews(store.manifest_revision())) == 2
    store.compile_template(store.workspace / "template")
    assert len(store.successful_reviews(store.manifest_revision())) == 2
    (store.workspace / "template/VISION.md").write_text("tampered derived filename")
    assert not store.successful_reviews(store.manifest_revision())


@pytest.mark.parametrize(
    "change", ["vision-text", "open", "blocker", "unrelated", "template", "old-row", "policy"]
)
def test_review_carry_refuses_unreviewed_scope_or_ineligible_findings(semantic_plan, change):
    """Protect review boundaries; catch stale credit missed by exact-revision tests."""
    store, _ = semantic_plan
    review_with_findings(store, severity="blocker" if change == "blocker" else "major")
    repair_review_scenarios(store, leave_open=change == "open")
    if change in {"vision-text", "unrelated"}:
        item = store.spine().node("V1" if change == "vision-text" else "B")
        item["text"] += " New scope"
        # A deliberate vision change reopens the lock; carry must still refuse it.
        store.upsert_node(item, reopen_vision_lock=change == "vision-text")
        store.compile_template(store.workspace / "template")
    elif change == "template":
        (store.workspace / "template/extra.txt").write_text("unreviewed template change")
    elif change == "old-row":
        store.connection.execute("UPDATE review_runs SET nodesDigest=NULL")
    elif change == "policy":
        store.record_plan_review_policy(
            [dict(name="source-codex", provider="codex", required=True)], store.manifest_revision()
        )
    current = store.manifest_revision()
    assert not store.successful_reviews(current)
    assert store.status()["reviewResolution"]["review"]["current"] is False
    with pytest.raises(
        ValueError,
        match="successful review|review policy|renewed approval|open roundtable findings",
    ):
        store.advance("publish", by="source-test")


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
    s["actions"][0]["cli"]["argv"] = [{"path": "data/note.txt", "relative": True}]
    expected = dict(
        launch=["-B", "-m", "probe", "payload.bin", "../note.txt"],
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


@pytest.mark.parametrize("clarification,run22", [(False, False), (True, False), (False, True)])
def test_canonical_plan_contracts_red_build_validation_pass(
    semantic_plan, monkeypatch, clarification, run22
):
    """Protect public A/B/C→dispatch→CLI→acceptance, with one genuine clarification when needed.

    Catches duplicate stops, native elicitation and premature acceptance; separate-gate tests
    never consume the combined public requests through review, publish and final acceptance.
    Run 22 authors subordinate scope after A at Vision Lock and advances to Execute before
    starting workers; the original journey authored everything before A and started at Publish.
    """
    import sys
    from copy import deepcopy
    from types import SimpleNamespace

    from tests.python.core.test_acceptance import finish_public_acceptance
    from tests.python.core.test_acceptance_recovery import protocol
    from tests.python.core.test_mcp_chat_confirmations import record as external_reply
    from tests.python.core.test_mcp_planner import rpc, workspace_wire
    from tests.python.core.test_plan_store import mock_catalog, mock_choice

    from codex_harness.core import plan_cli, roundtable

    store, manifest = semantic_plan
    server = PlannerServer(store.workspace)
    confirmations = []

    def call(name, **args):
        reply = workspace_wire(server, name, expected_workspace=str(store.workspace), **args)
        assert server._pending_confirmation is None, "Default semantic journey must never elicit"
        assert not reply["isError"], reply
        if name not in {"harness_accept_review", "harness_accept"}:
            assert "C: Does this delivery" not in str(reply)
        return reply

    def approve(request):
        confirmations.append(request["gate"])
        result = external_reply(server, request)
        assert not result["isError"] and result["structuredContent"]["state"] == "approved", result

    rpc(
        server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}}
    )
    server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    if run22:
        authored = store.spine().to_dict()["nodes"]
        vision = store.spine().node("V1")
        targets = vision["fields"].pop("targets")
        scenarios = vision["fields"].pop("scenarios")
        call("harness_upsert_node", node=vision)
        for key in ("B", "C", "CLI", "D", "R", "F"):
            call("harness_remove_node", id=key)
    source_only_expansion(store)  # Proposed dispositions only; no confirmation credit.
    if clarification:
        question = call(
            "harness_ask", question="Must the greeting end with a newline?", node_ids=["V1"]
        )
        confirmations.append("clarification")
        call("harness_answer", id=question["structuredContent"]["id"], answer="No; exactly Hello.")
    card = call(
        "harness_understanding_card",
        vision="Offline greeting CLI",
        users="CLI user",
        first_user_moment="Read Hello",
        scope_in="Offline greeting",
        scope_out="GUI",
        assumptions=[],
    )
    a = card["structuredContent"]
    # CT-2: the mandatory user excerpt, not a wrapper, must explain approval's effect.
    excerpt = a["card"].split("<<<SHOW-VERBATIM\n", 1)[1].split("\nSHOW-VERBATIM>>>", 1)[0]
    assert (
        "Approve confirms understanding and locks this scope and these expansion decisions."
        in excerpt
    )
    persisted = json.loads(
        store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='confirmation-requested' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    )
    assert persisted["card"] == a["card"] and a["card"] in card["content"][0]["text"]
    assert set(a["scopes"]) == {"align", "vision-lock"}
    approve(a)
    if run22:
        call("harness_advance", to_phase="expand")
        call("harness_advance", to_phase="vision-lock")
        vision = store.spine().node("V1")
        vision["fields"].update(targets=targets, scenarios=scenarios)
        call("harness_upsert_nodes", nodes=[n for n in authored if n["id"] != "V1"] + [vision])
        assert store._revision() != a["plan_revision"]
        assert store.status()["approval"]["granted"]
    for phase in PHASES[PHASES.index(store.phase()) + 1 : PHASES.index("roundtable") + 1]:
        if phase == "roundtable":
            break
        call("harness_advance", to_phase=phase)
    catalog = mock_catalog()
    other = deepcopy(catalog["providers"][0])
    other.update(provider="claude", executable="C:/mock/claude.exe")
    other["choices"][0]["model"] = "claude-sonnet-5-5"
    catalog["providers"].append(other)
    catalog = call(
        "harness_reviewer_catalog", catalog=catalog, expected_spine_revision=store._revision()
    )["structuredContent"]["catalog"]
    selection = mock_choice(catalog)
    choice = catalog["providers"][1]["choices"][0]
    selection["fields"]["reviewers"].append(
        dict(
            name="secondary",
            provider="claude",
            **{k: choice[k] for k in ("choiceId", "model", "effort")},
        )
    )
    call("harness_reviewer_choice", node=selection, expected_spine_revision=store._revision())
    call("harness_compile", include_detail=True)
    manifest = store._manifest(store.spine())
    assert (
        plan_cli.main(
            [
                "--workspace",
                str(store.workspace),
                "review-budget",
                "--campaign",
                "three-replies",
                "--max-starts",
                "4",
            ]
        )
        == 0
    )
    options = dict(
        expected_revision=manifest["planRevision"],
        max_parallel=1,
        timeout=5,
        call_cap=2,
        workers=[
            dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium") for b in ("C", "B")
        ],
    )
    b = call(
        "harness_prepare_chat_confirmation",
        gate="plan-execution",
        execution=options,
        reviewer_node_id="CHOICE",
    )["structuredContent"]
    approve(b)
    call("harness_advance", to_phase="roundtable")
    (store.workspace / "sample.json").write_text(json.dumps(manifest))
    reviewers = b["scopes"]["review-budget"]["reviewers"]
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)

    # Synthetic external-process telemetry, never actual provider/product qualification.
    def review_process(launch, *_):
        provider = launch.cli
        result = protocol(provider, text="[]")
        model = next(r["model"] for r in reviewers if r["provider"] == provider)
        return tuple(v.replace("source-only", model) if isinstance(v, str) else v for v in result)

    monkeypatch.setattr(roundtable, "_run", review_process)
    call("harness_roundtable", reviewers=reviewers, max_rounds=1, max_total=2, timeout=5)
    call("harness_advance", to_phase="publish")
    call("harness_publish")
    if run22:
        call("harness_advance", to_phase="execute")
    packet = worker_packet.build_packet(
        load_plan(store.workspace),
        {**manifest["beads"]["C"], "id": "C"},
        store.workspace / "template",
        None,
    )
    assert MARKER_FIRST in packet
    job = planner_jobs._directory(store.workspace, manifest["planRevision"])
    with monkeypatch.context() as launch_spy:
        launch_spy.setattr(
            planner_jobs.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=23456)
        )
        call("harness_worker_start", **options)
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

    try:
        for phase in ("accept",) if run22 else ("execute", "accept"):
            advanced = call("harness_advance", to_phase=phase)
            hint = advanced["content"][0]["text"].split("Next: ", 1)[1]
            assert "harness_accept_run" in hint and "If they agree" not in hint
            for tool, arguments in (
                ("harness_plan_status", {}),
                ("harness_worker_status", {"job_id": job.name, "wait_seconds": 0}),
            ):
                reply = server.call_tool(tool, arguments)
                assert not reply["isError"], reply
                assert reply["content"][0]["text"].split("Next: ", 1)[1] == hint
        refused = workspace_wire(
            server,
            "harness_accept",
            expected_workspace=str(store.workspace),
            by="user",
            statement="Premature",
        )
        assert refused["isError"]
        result = finish_public_acceptance(
            server, call("harness_accept_run", repo=record["repo"], subject="product")
        )
        assert result["structuredContent"]["result"]["passed"], result
        reviewed = call("harness_accept_review", repo=record["repo"], reviewers=reviewers)
        assert "C: Does this delivery meet the locked vision?" in str(reviewed)
        confirmations.append("accept")
        call(
            "harness_accept",
            by="user",
            statement="SOURCE-ONLY external owner: Yes, meets the locked vision.",
        )
        assert confirmations == (["clarification"] if clarification else []) + [
            "align+vision-lock",
            "plan-execution",
            "accept",
        ]
        requests = [
            json.loads(r[0])
            for r in store.connection.execute(
                "SELECT payload_json FROM events WHERE kind='confirmation-requested' ORDER BY id"
            )
        ]
        assert [r["gate"] for r in requests] == ["align+vision-lock", "plan-execution"]
        assert all(r["channel"] == "chat-confirmation" for r in requests)
        assert (
            store.connection.execute("SELECT count(*) FROM acceptance_approvals").fetchone()[0] == 1
        )
    finally:
        server.close()
    # A fresh connection must read the terminal run, rather than offer it again.
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool("harness_plan_status", {})
        assert not reply["isError"], reply
        assert "Validation is not final acceptance" not in reply["content"][0]["text"]
    finally:
        server.close()


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
    """Catch late scenario authoring or invalid help; runner tests bypass schema teaching."""
    from codex_harness.core.planner_schema import schema_help
    from codex_harness.core.semantic_schema import validate

    tools = [{"name": "harness_upsert_node", "inputSchema": {}}]
    server = PlannerServer(tmp_path)
    try:
        started = server.call_tool("harness_plan_start", dict(idea="Parser", name="Parser"))
        assert not started["isError"], started
        digest = started["structuredContent"]["schemaDigest"]
        teaching = digest["semanticScenarios"]
        assert teaching == schema_help(tools, kind="vision")["semanticScenarios"]
        assert "scope_in" in digest["visionFields"]
        assert digest["visionFields"]["planMode"] == "semantic-v1"
        assert "expansion" in digest["visionRules"]
        guidance = started["structuredContent"]["authoringGuidance"]
        assert "all features+requirements+design nodes" in guidance
        assert "ONE harness_upsert_nodes" in guidance
        assert "Product files are owned by workers" in guidance
        assert "no per-phase harness_schema calls needed" in started["content"][0]["text"]
    finally:
        server.close()
    flow = teaching["phaseFlow"]
    assert "During Vision, author vision.fields.scenarios + vision.fields.targets" in flow
    assert "one forward harness_advance to_phase=plan" in flow
    assert "every intermediate gate still runs" in flow
    example = teaching["fieldExample"]
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


def test_semantic_review_packet_keeps_three_jobs_and_adds_scenario_feasibility(semantic_plan):
    """A real run looped on edge-case findings; semantic reviewers must not review test depth."""
    store, _ = semantic_plan
    packet = review_packet(store)
    assert "Review only four things" in packet
    for job in (
        "Expansion within scope",
        "Vision integrity",
        "Spine trickle-down",
        "Scenario feasibility",
    ):
        assert job in packet
    assert "files, parent dirs, prior state" in packet
    assert "each negative scenario's missing precondition is deliberate" in packet
    assert "major only for a break in (1)-(4)" in packet
    assert "Look for missing features or edge cases" not in packet


def test_stored_stdin_compiles_to_real_runner_without_mutating_input(semantic_plan):
    """Catch compiler dropping stdin or IDs; direct runner probes bypass canonical storage."""
    from codex_harness.core.semantic_runner import run

    store, _ = semantic_plan
    vision = store.spine().node("V1")
    document = vision["fields"]["scenarios"]
    item = document["scenarios"][0]
    item["actions"][0]["cli"]["stdin"] = {"base64": "/wANCoA="}
    action_id = item["actions"][0]["id"]
    item["observe"] = [{"action": action_id, "exitEquals": 0, "stdoutJsonEquals": "ff000d0a80"}]
    store.upsert_node(vision)
    before = json.dumps(store.spine().node("V1"))
    compiled = store.compile_template(store.workspace / "template")["semantic"]
    assert compiled["scenarios"] == document
    product = store.workspace / "template"
    (product / "hello.py").write_text(
        "import json,sys\nprint(json.dumps(sys.stdin.buffer.read().hex()))\n"
    )
    compiled_before = json.dumps(compiled)
    results = run(compiled["scenarios"], product, compiled["targets"], "validation")
    assert [(r["id"], r["state"]) for r in results] == [(item["id"], "PASS")], results
    assert results[0]["actions"][action_id]["exit"] == 0
    assert results[0]["cleanup"]["quiescenceVerified"]
    assert results[0]["cleanup"]["activeProcessesAfterTeardown"] == 0
    assert json.dumps(compiled) == compiled_before
    assert json.dumps(store.spine().node("V1")) == before


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


def test_compact_public_responses_keep_canonical_detail_and_next_actions(semantic_plan):
    """Protect MCP replay size and evidence reachability after real semantic compilation.

    Catches bulk scenario replay, lost failure reasons, stale teaching and changed Next
    actions; existing fixture tests inspect canonical producers rather than MCP projections.
    """
    from codex_harness.core.semantic_runner import run

    store, manifest = semantic_plan
    server = PlannerServer(store.workspace)

    def call(name, **args):
        reply = server.call_tool(name, args)
        assert not reply["isError"], reply
        return reply

    def size(reply):
        return len(json.dumps(reply, separators=(",", ":")).encode())

    def next_action(reply):
        return reply["content"][0]["text"].rsplit("Next: ", 1)[1]

    try:
        first = call("harness_schema", kind="vision")
        teaching = first["structuredContent"]["semanticScenarios"]
        again = call("harness_schema", kind="vision")
        assert "digest" in again["structuredContent"]["semanticScenarios"]
        assert (
            call("harness_schema", kind="vision", include_teaching=True)["structuredContent"][
                "semanticScenarios"
            ]
            == teaching
        )
        vision = store.spine().node("V1")
        vision["text"] += " A revised plan."
        store.upsert_node(vision)
        assert (
            call("harness_schema", kind="vision")["structuredContent"]["semanticScenarios"]
            == teaching
        )
        for kind in ("vision", "feature", "requirement", "decision", "design", "test", "bead"):
            reply = call("harness_schema", kind=kind)
            assert size(reply) < 15000
            if kind != "vision":
                assert "semanticScenarios" not in reply["structuredContent"]
                assert "compilationMetadata" not in reply["structuredContent"]
        for name in ("harness_plan_status", "harness_compile"):
            compact = call(name)
            full = call(name, include_detail=True)
            assert size(compact) < 6000
            assert next_action(compact) == next_action(full)
            assert compact["structuredContent"]["digest"]
            if name == "harness_compile":
                assert full["structuredContent"] == store.compile_template(
                    store.workspace / "template"
                )
                assert compact["structuredContent"]["beads"]["count"] == len(manifest["beads"])
            else:
                assert full["structuredContent"] == server._status()
                assert (
                    compact["structuredContent"]["approval"]
                    == full["structuredContent"]["approval"]
                )
        # Real semantic runner -> durable dispatcher readback -> MCP, with no provider launch.
        product = store.workspace / "product"
        product.mkdir()
        (product / "hello.py").write_text("print('Wrong', end='')\n")
        semantic = store.compile_template(store.workspace / "template")["semantic"]
        evidence = dict(
            results=run(semantic["scenarios"], product, semantic["targets"], "validation")
        )
        assert evidence["results"][0]["state"] == "FAIL"
        job_id = manifest["planRevision"]
        directory = planner_jobs._directory(store.workspace, job_id)
        directory.mkdir(parents=True)
        request = dict(
            schema=planner_jobs.SCHEMA, jobId=job_id, publicationRevision=job_id, options={}
        )
        request["requestDigest"] = planner_jobs._digest(request)
        (directory / "request.json").write_text(json.dumps(request))
        (directory / "terminal.json").write_text(
            json.dumps(dict(requestDigest=request["requestDigest"], status="completed"))
        )
        run_dir = directory / "runs" / "source-only"
        (run_dir / "evidence").mkdir(parents=True)
        record = dict(
            runId="source-only",
            planRevision=job_id,
            repo=str(run_dir / "repo"),
            isMocked=True,
            didExecuteRealProvider=False,
            status="failed",
            beads={},
            semanticValidation=evidence,
        )
        (run_dir / "evidence" / "run.json").write_text(json.dumps(record))
        compact = call("harness_worker_status", job_id=job_id)
        full = call("harness_worker_status", job_id=job_id, include_detail=True)
        assert full["structuredContent"] == planner_jobs.worker_job_status(store.workspace, job_id)
        assert full["structuredContent"]["run"]["semanticValidation"] == evidence
        assert size(compact) < 6000
        failures = compact["structuredContent"]["run"]["semanticValidation"]["failures"]
        assert any(row["id"] == "first-moment" for row in failures)
        assert any(row.get("reason") == "observable mismatch" for row in failures)
        repeated = call("harness_worker_status", job_id=job_id)
        assert len(repeated["content"][0]["text"].splitlines()) == 1
        assert repeated["content"][0]["text"].startswith("unchanged since ")
        assert next_action(repeated) == next_action(compact) == next_action(full)
        assert size(repeated) < 2000
        # A changed terminal snapshot must be returned, even on the same connection.
        record["status"] = "blocked"
        (run_dir / "evidence" / "run.json").write_text(json.dumps(record))
        changed = call("harness_worker_status", job_id=job_id)
        assert changed["structuredContent"]["run"]["status"] == "blocked"
        print(
            "COMPACT_BYTES",
            json.dumps(
                dict(
                    schema_first=size(first),
                    schema_repeat=size(again),
                    worker=size(compact),
                    worker_full=size(full),
                    worker_repeat=size(repeated),
                )
            ),
        )
    finally:
        server.close()
