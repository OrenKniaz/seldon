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
    """Protect semantic packet/gates; catch default bulk echoes missed by direct-builder tests."""
    from tests.python.core.test_mcp_planner import rpc, workspace_wire

    from codex_harness.core import plan_cli

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
    canonical = packet_view(store, "B")
    packet = canonical["packet"]
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
        }
        assert packet not in summary["content"][0]["text"]
        assert "Summary only; include_packet=true" in summary["content"][0]["text"]
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
        assert plan_cli.main(["--workspace", str(store.workspace), "--json", "packet", "B"]) == 0
        assert json.loads(capsys.readouterr().out)["data"] == canonical
        sizes = {
            key: len(json.dumps(reply, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            for key, reply in (("default", summary), ("full", full))
        }
        assert sizes["default"] < sizes["full"], sizes
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


def test_canonical_plan_contracts_red_build_validation_pass(semantic_plan):
    """Protect producer→dispatch→real CLI success; catches RED-as-proof and final rerun of stubs."""
    store, manifest = semantic_plan
    packet = worker_packet.build_packet(
        load_plan(store.workspace),
        {**manifest["beads"]["C"], "id": "C"},
        store.workspace / "template",
        None,
    )
    assert MARKER_FIRST in packet
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
