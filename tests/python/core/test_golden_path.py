"""Scripted clients and providers; real stdio MCP, processes, verifier and delivery."""

import hashlib
import json
import os
import queue
import runpy
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


ROOT = Path(__file__).resolve().parents[3]



def trace_contract_golden(workspace):
    """Protect public full/collapsed brownfield traces and exact amendment freshness.

    Catch dropped tests/baselines during compilation, broad evidence invalidation and
    destructive feature replacement, metadata edits laundering retained observations and
    stale revision references retaining current evidence credit; scenario-only delivery
    journeys miss typed trace persistence, revision binding and independent branches. Observations here are retained fixture evidence,
    never live-provider acceptance. No workers or providers execute in this journey.
    """
    from copy import deepcopy

    from tests.python.core.mcp_planner_support import workspace_wire
    from tests.python.core.semantic_wiring_support import node

    from codex_harness.core.graph import load_plan
    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.spine import Spine, SpineError

    for mode, full in (("semantic-v1", True), ("legacy", False)):
        root = workspace / mode
        root.mkdir(parents=True)
        (root / "template").mkdir()
        server = PlannerServer(root)

        def call(tool_name, *, error=False, **args):
            reply = workspace_wire(server, tool_name, expected_workspace=str(root), **args)
            assert reply["isError"] is error, (tool_name, reply)
            return reply.get("structuredContent", {})

        def ref(key):
            return {"id": key, "revision": server.store.spine().revision(key)}

        try:
            call("harness_plan_start", idea="Tune a sensor while retaining readback", name="sensor", plan_mode=mode)
            vision = server.store.spine().node("V1")
            if mode == "legacy":
                vision["fields"].update(validation=["opaque-simulator"], requirementFiles=[])
                (root / "template" / "tests").mkdir()
            nodes = [vision]
            for suffix in ("1", "2"):
                f, r, t, b, c = (prefix + suffix for prefix in ("F", "R", "T", "B", "C"))
                nodes.append(node(f, "feature", ["V1"]))
                parent = f
                if full:
                    story = "S" + suffix
                    nodes.append(node(story, "story", [f]))
                    parent = story
                nodes.append(node(r, "requirement", [parent]))
                parent = r
                if full:
                    architecture, design, contract = "A" + suffix, "D" + suffix, "K" + suffix
                    nodes.extend([node(architecture, "architecture", [r]), node(design, "design", [architecture]), node(contract, "contract", [design])])
                    parent = contract
                nodes.extend([
                    node(t, "test", [parent], module="sensor/readback", level="semantic", execution={"argv": ["opaque-simulator", "--measure"], "rationale": "Measure retained sensor behavior"}),
                    node(b, "bead", [t], ownedFiles=["sensor" + suffix + ".bin"], dependsOn=[], lessons=[], **({"semanticPhase": "build"} if mode == "semantic-v1" else {"proofModule": "sensor/readback"})),
                    node(c, "code", [b], path="sensor" + suffix + ".bin"),
                ])
            call("harness_upsert_nodes", nodes=nodes)
            additions = []
            for suffix in ("1", "2"):
                result = "E" + suffix
                evidence = {"itemRef": ref("C" + suffix), "candidate": "baseline-candidate", "source": {"reference": "baseline:measurement:" + suffix, "realProvider": False, "historical": True}, "observation": {"reference": "measurement:" + suffix, "outcome": "passed", "rms": 0.2}, "state": "integrated"}
                additions.append(node(result, "result", ["C" + suffix], evidence=evidence))
            projected = Spine({**server.store.spine().to_dict(), "nodes": server.store.spine().to_dict()["nodes"] + additions})
            features = []
            for suffix in ("1", "2"):
                feature = server.store.spine().node("F" + suffix)
                feature["fields"]["baseline"] = {"source": "existing-repository", "behavior": "Read existing sensor channel " + suffix, "rationale": "Preserve verified channel readback", "codeRefs": [ref("C" + suffix)], "testRefs": [ref("T" + suffix)], "resultRefs": [{"id": "E" + suffix, "revision": projected.revision("E" + suffix)}]}
                feature["fields"]["evidenceRefs"] = [{"id": "E" + suffix, "revision": projected.revision("E" + suffix)}]
                features.append(feature)
            # Baseline annotation changes the upstream feature, so supply the retained
            # observations in the same canonical transaction rather than refreshing later.
            call("harness_upsert_nodes", nodes=features + additions)
            if full:
                call("harness_upsert_nodes", nodes=[node("RES", "research", ["V1"], rationale="Investigate sensor calibration"), node("MET", "metric", ["V1"], definition="Measured residual noise", unit="dB")])
                call("harness_upsert_nodes", nodes=[node("STOP", "stop-condition", ["MET"], metricRefs=[ref("MET")], condition="Residual noise reaches agreed threshold"), node("PENDING", "result", ["R1"], metricRefs=[ref("MET")], evidence={"itemRef": ref("R1"), "candidate": "next-measurement", "source": {"reference": "planned:measurement", "realProvider": False, "historical": False}, "observation": None, "state": "awaiting"})])
                call("harness_upsert_node", node=node("ITER", "iteration", ["MET"], metricRefs=[ref("MET")], resultRefs=[ref("E1")], stopConditionRefs=[ref("STOP")], researchRefs=[ref("RES")]))
                call("harness_upsert_node", node=node("BAT", "baton", ["ITER"], decisionRefs=[], assumptions=["Baseline sensor remains available"], openQuestions=["Which calibration meets the agreed metric?"], currentState="Baseline mapped; measurement awaits execution", nextSafeAction="Review the calibration proposal", rationale="Preserve current behavior while planning", iterationRefs=[ref("ITER")]))
            forged = deepcopy(additions[0])
            forged["id"] = "FORGED_LIVE"
            forged["fields"]["evidence"]["state"] = "validated live"
            call("harness_upsert_node", node=forged, error=True)
            extra_result = deepcopy(additions[0])
            extra_result["id"] = "E3"
            extra_result["fields"]["evidence"]["source"]["historical"] = False
            extra_result["fields"]["evidence"]["observation"]["reference"] = "fixture:fresh-channel-measurement"
            call("harness_upsert_node", node=extra_result)
            if full:
                call("harness_upsert_node", node=node("POINTER", "decision", ["V1"], batonRefs=[ref("BAT")], rationale="Resume from the canonical sensor baton"))
            initial = call("harness_compile", include_detail=True)
            sample = root / "sample.json"
            sample.write_text(json.dumps(initial), encoding="utf-8")
            consumed = load_plan(sample)
            assert consumed.trace == initial["trace"]
            assert initial["evidenceStates"]["C1"]["state"] == "integrated"
            assert initial["evidenceStates"]["C2"]["state"] == "integrated"
            if full:
                assert initial["evidenceStates"]["MET"]["state"] == "awaiting"
                assert initial["evidenceStates"]["PENDING"]["state"] == "awaiting"
            retained = server.store.spine().to_dict()
            server.close()
            server = PlannerServer(root)
            reloaded = call("harness_compile", include_detail=True)
            assert reloaded == initial
            assert reloaded["trace"] == retained
            assert set(reloaded["traceRevisions"]) == {n["id"] for n in retained["nodes"]}
            assert len(reloaded["beads"]) == 2
            assert all(n["fields"]["level"] == "semantic" for n in retained["nodes"] if n["kind"] == "test")
            # Record an upward discovery without granting consent or silently editing R1.
            old_requirement = ref("R1")
            discovery = node("DISC", "discovery", ["C1"], source="baseline:measurement:1")
            decision = node("DEC", "decision", ["DISC"], rationale="Propose improved calibration")
            call("harness_upsert_nodes", nodes=[discovery, decision])
            amendment = node("AM", "amendment", ["DISC"], sourceRef=ref("DISC"), targetRefs=[old_requirement], proposedChange="Calibrate the first sensor channel", rationale="Observed drift in first channel", decisionRefs=[ref("DEC")])
            call("harness_upsert_node", node=amendment)
            assert ref("R1") == old_requirement
            before = server.store.spine()
            requirement = before.node("R1")
            requirement["text"] = "Calibrate first-channel readback while preserving the second channel"
            metadata_edit = before.node("E3")
            metadata_edit["fields"]["evidence"]["note"] = "Retained observation, extra annotation only"
            call("harness_upsert_nodes", nodes=[requirement, metadata_edit])
            assert server.store.spine().node("E3")["fields"]["evidence"]["state"] == "stale"
            mismatched = node("OLD_ITEM", "result", ["R1"], evidence={"itemRef": old_requirement, "candidate": "fixture-current-candidate", "source": {"reference": "fixture:stale-input", "realProvider": False, "historical": False}, "observation": {"reference": "fixture:old-requirement-observation", "outcome": "passed"}, "state": "integrated"})
            call("harness_upsert_node", node=mismatched)
            assert server.store.spine().node("OLD_ITEM")["fields"]["evidence"]["state"] == "stale"
            changed = call("harness_compile", include_detail=True)
            assert changed["evidenceStates"]["OLD_ITEM"]["state"] == "stale"
            assert next(n for n in changed["trace"]["nodes"] if n["id"] == "OLD_ITEM")["fields"]["evidence"]["state"] == "stale"
            spine = server.store.spine()
            assert spine.node("E1")["fields"]["evidence"]["state"] == "stale"
            assert spine.node("E2") == before.node("E2")
            assert spine.node("F2") == before.node("F2")
            assert spine.node("AM") == before.node("AM")
            for suffix in ("1", "2"):
                expected = deepcopy(before.node("E" + suffix)["fields"]["evidence"])
                actual = spine.node("E" + suffix)["fields"]["evidence"]
                assert {k: v for k, v in actual.items() if k != "state"} == {k: v for k, v in expected.items() if k != "state"}
            assert changed["evidenceStates"]["C1"]["state"] == "stale"
            assert changed["evidenceStates"]["C2"]["state"] == "integrated"
            assert spine.trace_up("E1")[0][-1] == "V1"
            feature = spine.node("F1")
            feature["fields"].pop("baseline")
            call("harness_upsert_node", node=feature, error=True)
            assert server.store.spine().to_dict() == spine.to_dict()
            imported = spine.to_dict()
            imported["nodes"] = [n for n in imported["nodes"] if n["id"] == "V1" or n["id"].endswith("2")]
            path = root / "narrowed.json"
            path.write_text(json.dumps(imported), encoding="utf-8")
            with pytest.raises(SpineError) as refusal:
                server.store.import_spine(path)
            assert refusal.value.code == "spine-evidence-preservation-required"
            assert server.store.spine().to_dict() == spine.to_dict()
        finally:
            server.close()


def http_golden(workspace, semantic=False):
    """Protect subprocess HTTP planning through publication, real RED/GREEN and C.

    Catch ShelfLife's import-only audit deadlock and unknown concurrency helpers;
    the semantic CLI/static-page journeys never consume a prospective audit.
    Only providers are doubled; HTTP, dispatch and acceptance execute normally.
    """
    from tests.python.core.acceptance_recovery_support import protocol
    from tests.python.core.acceptance_support import finish_public_acceptance
    from tests.python.core.mcp_chat_confirmations_support import record
    from tests.python.core.mcp_planner_support import rpc, workspace_wire
    from tests.python.core.planning_support import source_only_expansion
    from tests.python.core.review_support import finish_review
    from tests.python.core.semantic_wiring_support import node

    from codex_harness.core import dispatch, roundtable
    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.plan_store import PHASES

    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("Use gpt-6.1-sol at medium effort for reviews.\n")
    server = PlannerServer(workspace)
    decisions = []

    def call(tool_name, **args):
        reply = workspace_wire(server, tool_name, expected_workspace=str(workspace), **args)
        if tool_name == "harness_accept_review":
            reply = finish_review(server, reply)
        if reply["isError"]:
            print("\n".join(c.get("text", "") for c in reply.get("content", [])))
        assert not reply["isError"], (tool_name, reply)
        return reply.get("structuredContent", {})

    def yes(card):
        decisions.append(card["gate"])
        reply = record(server, card, "Yes")
        assert not reply["isError"], reply

    product = """import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class API(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'shelf')
    def log_message(self, *args):
        pass
server = ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), API)
print(server.server_port, flush=True)
server.serve_forever()
"""
    proof = """import subprocess, sys, unittest
from pathlib import Path
from urllib.request import urlopen
class HTTP(unittest.TestCase):
    def test_status_and_body(self):
        with subprocess.Popen([sys.executable, '-B', 'server.py', '0'],
                              cwd=Path(__file__).resolve().parents[1],
                              stdout=subprocess.PIPE, text=True) as server:
            try:
                port = int(server.stdout.readline())
                with urlopen(f'http://127.0.0.1:{port}/items', timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b'shelf')
            finally:
                server.terminate()
                server.wait(timeout=5)
"""
    helper = """import subprocess, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen
def simultaneous():
    with subprocess.Popen([sys.executable, '-B', 'server.py', '0'],
                          cwd=Path(__file__).resolve().parents[1],
                          stdout=subprocess.PIPE, text=True) as server:
        try:
            port = int(server.stdout.readline())
            def get(_):
                with urlopen(f'http://127.0.0.1:{port}/items', timeout=5) as response:
                    return response.status, response.read()
            with ThreadPoolExecutor(max_workers=4) as pool:
                return list(pool.map(get, range(4)))
        finally:
            server.terminate()
            server.wait(timeout=5)
"""
    opaque = """import unittest
from helpers import simultaneous
class ConcurrentHTTP(unittest.TestCase):
    def test_simultaneous_requests(self):
        self.assertEqual(simultaneous(), [(200, b'shelf')] * 4)
"""
    try:
        rpc(server, "initialize", dict(protocolVersion="2025-06-18", capabilities={}))
        call(
            "harness_plan_start",
            idea="Read shelf items over HTTP",
            name="http",
            plan_mode="semantic-v1" if semantic else "legacy",
        )
        store = server.store
        source_only_expansion(store)
        call(
            "harness_understanding_card",
            vision="Read shelf items over HTTP",
            users="Local operator",
            first_user_moment="GET items returns shelf",
            scope_in="Concurrent HTTP reads",
            scope_out="Remote hosting",
            assumptions=[],
        )
        # Legacy understanding saves a node; request the shared A subject explicitly.
        yes(call("harness_prepare_chat_confirmation", gate="align+vision-lock"))
        vision = store.spine().node("V1")
        if semantic:
            # Round 3 submitted only these importer scenarios; HTTP/UI were never
            # submitted. Replay their missing coverage plus a proposed HTTP shape.
            # Catch the Requirements deadlock at the public writer/compiler boundary;
            # legacy HTTP and CLI-only journeys never exercised that boundary.
            captured = {
                "schema": "seldon/scenarios-v1",
                "scenarios": [
                    {
                        "id": "S_IMPORT_MIXED",
                        "requirementRefs": ["R4"],
                        "contractRefs": ["G1"],
                        "setup": {
                            "dirs": ["data"],
                            "files": [
                                {
                                    "path": "data/mixed.csv",
                                    "utf8": (
                                        "name,quantity,unit,expires\n"
                                        "Rice,2,kg,2030-01-10\n"
                                        "Beans,0,,\n"
                                        ",1,,\n"
                                        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                                        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx,1,,\n"
                                        "Bad negative,-1,,\n"
                                        "Bad fraction,1.5,,\n"
                                        "Bad date,1,,2030-02-30\n"
                                    ),
                                }
                            ],
                        },
                        "actions": [
                            {
                                "id": "import",
                                "cli": {
                                    "target": "importer",
                                    "operation": "import-mixed",
                                    "argv": [
                                        {"path": "data/mixed.csv"},
                                        "--db",
                                        {"path": "data/pantry.sqlite3"},
                                    ],
                                },
                            }
                        ],
                        "observe": [
                            {
                                "action": "import",
                                "exitEquals": 0,
                                "stdoutEquals": "imported 2, skipped 5\n",
                                "stderrEquals": "",
                            }
                        ],
                        "verdict": "all",
                    },
                    {
                        "id": "S_IMPORT_UNREADABLE",
                        "requirementRefs": ["R4"],
                        "contractRefs": ["G1"],
                        "setup": {"dirs": ["data"]},
                        "actions": [
                            {
                                "id": "unreadable",
                                "cli": {
                                    "target": "importer",
                                    "operation": "import-unreadable",
                                    "argv": [
                                        {"path": "data/missing.csv"},
                                        "--db",
                                        {"path": "data/pantry.sqlite3"},
                                    ],
                                },
                            }
                        ],
                        "observe": [
                            {
                                "action": "unreadable",
                                "exitEquals": 2,
                                "stdoutEquals": "",
                                "stderrNonempty": True,
                            }
                        ],
                        "verdict": "all",
                    },
                ],
            }
            vision["fields"].update(
                targets={"importer": {"entry": "import_items.py", "argv": []}},
                scenarios=captured,
            )
            requirements = [node(f"R{i}", "requirement", ["F"]) for i in range(1, 6)]
            owned = ["server.py", "import_items.py", "verify_http.py"]
            nodes = [
                vision,
                node("F", "feature", ["V1"]),
                *requirements,
                node("G1", "design", [r["id"] for r in requirements]),
                node(
                    "T_HTTP",
                    "test",
                    ["G1"],
                    module="verify_http.py",
                    level="semantic",
                    execution={
                        "argv": [sys.executable, "-B", "verify_http.py", "data/pantry.sqlite3"],
                        "rationale": "Planned importer/SQLite/HTTP readback verifier supplied and run later in this journey",
                    },
                ),
                node(
                    "C",
                    "bead",
                    ["G1"],
                    ownedFiles=owned,
                    dependsOn=[],
                    lessons=[],
                    semanticPhase="contracts",
                ),
                node(
                    "B",
                    "bead",
                    ["G1"],
                    ownedFiles=owned,
                    dependsOn=["C"],
                    lessons=[],
                    semanticPhase="build",
                ),
            ]
            call("harness_upsert_nodes", nodes=nodes)
            call("harness_advance", to_phase="plan")
            call("harness_compile", include_detail=True)
            assert not store.status()["coverage_gaps"], store.status()
            planned_scenarios = store.acceptance_scenarios()
            assert {
                s["requirementRefs"][0]
                for s in planned_scenarios
                if s.get("evidenceTier") == "scenario: unvalidated"
            } == {"R1", "R2", "R3", "R5"}
            assert all(s["executable"] for s in planned_scenarios if s["id"].startswith("S_IMPORT"))
            # Accept common HTTP request/response intent without pretending to run it.
            vision["fields"]["scenarios"]["scenarios"].append(
                dict(
                    id="S_HTTP",
                    requirementRefs=["R1", "R2", "R3", "R5"],
                    contractRefs=["G1"],
                    setup={},
                    actions=[{"id": "get", "http": {"method": "GET", "path": "/items"}}],
                    observe=[{"action": "get", "statusEquals": 200, "bodyEquals": "shelf"}],
                    verdict="all",
                )
            )
            call("harness_upsert_node", node=vision)
            call("harness_compile", include_detail=True)
            held = next(s for s in store.acceptance_scenarios() if s["id"] == "S_HTTP")
            assert held["evidenceTier"] == "scenario: unvalidated" and not held["executable"]
            from codex_harness.core.semantic_runner import run as run_scenarios

            pending = store._manifest(store.spine())["semantic"]
            (workspace / "import_items.py").write_text("# not executed\n")
            blocked = run_scenarios(
                pending["scenarios"], workspace, pending["targets"], "validation"
            )
            assert blocked[-1]["state"] == "ERROR", blocked
            assert "scenario: unvalidated" in blocked[0]["reason"], blocked
            assert not blocked[0]["cleanup"]["childLaunched"], blocked
            (workspace / "import_items.py").unlink()
            # Workers can supply an ordinary CLI verifier for the HTTP lifecycle.
            # Resolve the unvalidated intent before B; it grants no RED/GREEN credit.
            mixed = vision["fields"]["scenarios"]["scenarios"][0]
            mixed["requirementRefs"] = [f"R{i}" for i in range(1, 6)]
            mixed["actions"].append(
                {
                    "id": "http",
                    "cli": {
                        "target": "http",
                        "operation": "read",
                        "argv": [{"path": "data/pantry.sqlite3"}],
                    },
                }
            )
            mixed["observe"].append(
                {"action": "http", "exitEquals": 0, "stdoutEquals": "Rice,Beans\n"}
            )
            vision["fields"]["targets"]["http"] = {"entry": "verify_http.py", "argv": []}
            vision["fields"]["scenarios"]["scenarios"].pop()
            call("harness_upsert_node", node=vision)
            template = workspace / "template"
            template.mkdir(exist_ok=True)
            product = product.replace(
                "self.wfile.write(b'shelf')",
                "import sqlite3\n        with sqlite3.connect(sys.argv[2]) as db:\n"
                "            rows = db.execute('select name from items')\n"
                "            self.wfile.write(','.join(r[0] for r in rows).encode())",
            )
            (template / "server.py").write_text(product, encoding="utf-8")
            importer = """import csv, sys
try:
    rows = list(csv.DictReader(open(sys.argv[1], encoding='utf-8')))
except OSError:
    print('unreadable', file=sys.stderr)
    sys.exit(2)
def valid(row):
    from datetime import date
    try:
        if row['expires']: date.fromisoformat(row['expires'])
        return 1 <= len(row['name']) <= 80 and int(row['quantity']) >= 0
    except ValueError: return False
import sqlite3
accepted = [row for row in rows if valid(row)]
with sqlite3.connect(sys.argv[3]) as db:
    db.execute('create table if not exists items (name text)')
    db.executemany('insert into items values (?)', [(r['name'],) for r in accepted])
n = len(accepted)
print(f'imported {n}, skipped {len(rows)-n}')
"""
            verifier = """import subprocess, sys
from pathlib import Path
from urllib.request import urlopen
entry = str(Path(__file__).with_name('server.py'))
with subprocess.Popen([sys.executable, '-B', entry, '0', sys.argv[1]],
                      stdout=subprocess.PIPE, text=True) as server:
    try:
        port = int(server.stdout.readline())
        with urlopen(f'http://127.0.0.1:{port}/items', timeout=5) as response:
            if response.status != 200: raise RuntimeError(response.status)
            print(response.read().decode())
    finally:
        server.terminate()
        server.wait(timeout=5)
"""
        else:
            vision["fields"].update(
                validation=["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
                requirementFiles=["tests"],
            )
            call(
                "harness_upsert_nodes",
                nodes=[
                    vision,
                    node("F", "feature", ["V1"]),
                    node("R", "requirement", ["F"]),
                    node(
                        "B",
                        "bead",
                        ["R"],
                        ownedFiles=["server.py"],
                        dependsOn=[],
                        lessons=[],
                        proofModule="test_http*.py",
                    ),
                    node("T", "test", ["R"], level="e2e", module="test_http*.py"),
                ],
            )
            template = workspace / "template"
            (template / "tests").mkdir(parents=True)
            for name, content in {
                "server.py": product.replace("b'shelf'", "b'stub'"),
                "tests/test_http.py": proof,
                "tests/helpers.py": helper,
                "tests/test_http_concurrent.py": opaque,
            }.items():
                (template / name).write_text(content, encoding="utf-8")
            for phase in PHASES[PHASES.index(store.phase()) + 1 : PHASES.index("roundtable")]:
                call("harness_advance", to_phase=phase)
        choice = json.loads(
            (ROOT / "tests/fixtures/efficiency/jsonkeys-reviewers.json").read_text("utf-8")
        )["choice"]
        choice["fields"].update(catalogId=None, catalogDigest=None)
        for reviewer in choice["fields"]["reviewers"]:
            reviewer["choiceId"] = None
        choice["fields"]["recommendation"]["choiceIds"] = []
        call("harness_reviewer_choice", node=choice, expected_spine_revision=store._revision())
        manifest = call("harness_compile", include_detail=True)
        options = dict(
            expected_revision=manifest["planRevision"],
            max_parallel=1,
            timeout=60 if semantic else 30,
            call_cap=2 if semantic else 1,
            workers=[
                dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                for b in (("C", "B") if semantic else ("B",))
            ],
        )
        with pytest.MonkeyPatch.context() as patch:
            # These helpers also run before the stdio journey installs its absolute
            # import path. Relative PYTHONPATH breaks runner children in temp cwd.
            patch.setenv("PYTHONPATH", os.pathsep.join([str(ROOT / "src"), str(ROOT)]))
            patch.setattr(roundtable, "_executable", lambda *_: sys.executable)
            patch.setattr(
                roundtable,
                "_run",
                lambda launch, *_: tuple(
                    v.replace("source-only", "gpt-6.1-sol").replace("Some(High)", "Some(Medium)")
                    if isinstance(v, str)
                    else v
                    for v in protocol(launch.cli, text="[]")
                ),
            )
            yes(
                call(
                    "harness_prepare_chat_confirmation",
                    gate="plan-execution",
                    execution=options,
                    reviewer_node_id=choice["id"],
                )
            )
            if not semantic:
                audit = call("harness_audit", mode="prospective")
                server.audits.dispatch_after_flush(store)
                server.audits.thread.join(timeout=10)
                server.audits.drain(store)
                audited = call("harness_audit_status", id=audit["attemptId"])
                result = audited["result"]
                assert result["ok"] and audited["proofCreditGranted"], audited
                assert result["evidenceTier"] == "audit: unrecognized interface", result
                assert all(f["severity"] == "info" for f in result["findings"])
                assert {f["module"] for f in result["findings"]} == {"test_http*.py"}
                assert not result["collection"] and not audited["didRunValidation"]
                # The direct HTTP proof is recognized; only the opaque helper lowers the tier.
                assert len(result["findings"]) == 1, result
            call("harness_advance", to_phase="roundtable")
            assert store.status()["pending_gate"] != "passing-audit-required"
            call("harness_roundtable", max_rounds=1, max_total=2, timeout=5)
            call("harness_advance", to_phase="publish")
            if semantic:
                call("harness_publish")
            else:
                call(
                    "harness_publish",
                    proof_level="prospective",
                    reason="No reference implementation",
                )
            call("harness_advance", to_phase="execute")
            published = store.published_manifest(
                store.status()["publication"]["published_revision"]
            )
            (workspace / "sample.json").write_text(json.dumps(published), encoding="utf-8")

            def build(root):
                (root / "server.py").write_text(product, encoding="utf-8")

            if semantic:
                # C must consume the admitted job's real root delivery. Standalone
                # dispatch proves scenarios, but never supplies that delivery.
                contract_sources = {}
                for filename, operation in (
                    ("import_items.py", "import"),
                    ("verify_http.py", "read"),
                ):
                    contract_sources[filename] = (
                        "import sys\nprint('SELDON-STUB ' + "
                        "('import-unreadable' if 'missing.csv' in ' '.join(sys.argv) "
                        "else 'import-mixed'), file=sys.stderr)\nsys.exit(3)\n"
                        if operation == "import"
                        else "import sys\nprint('SELDON-STUB read', file=sys.stderr)\nsys.exit(3)\n"
                    )
                (workspace / "scripted-provider.json").write_text(
                    json.dumps(
                        {
                            "contracts": contract_sources,
                            "build": {
                                "server.py": product,
                                "import_items.py": importer,
                                "verify_http.py": verifier,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                actual_popen = subprocess.Popen

                def launch(argv, **kwargs):
                    if "codex_harness.core.planner_jobs" in argv:
                        argv = [
                            sys.executable,
                            "-B",
                            str(Path(__file__).resolve()),
                            "controller",
                            str(workspace),
                            "shelflife",
                            argv[-1],
                        ]
                    return actual_popen(argv, **kwargs)

                with pytest.MonkeyPatch.context() as process_patch:
                    process_patch.setattr(subprocess, "Popen", launch)
                    job = call("harness_worker_start", **options)
                    deadline = time.monotonic() + 180
                    while (
                        job["status"] not in {"completed", "failed"} and time.monotonic() < deadline
                    ):
                        job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=10)
                assert job["status"] == "completed", job
                run = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
                assert run["delivery"]["root"] == str(workspace.resolve()), run
            else:
                run = dispatch.run_plan(
                    workspace,
                    work_root=workspace / "runs",
                    max_parallel=1,
                    fake_workers={"B": build},
                )
            assert run["status"] == dispatch.STATUS_MOCK_ONLY, run
            if not semantic:
                assert not run["baseline"]["validation"]["B"]["passed"], run
            if semantic:
                assert run["beads"]["C"]["status"] == "contracts-red", run
                assert next(r for r in run["requirementValidation"] if r["bead"] == "B")[
                    "passed"
                ], run
            else:
                assert all(r["passed"] for r in run["requirementValidation"]), run
            if not semantic:
                derived = call("harness_accept_scenarios", derive=True)
                scenarios = derived["scenarios"]
                for scenario in scenarios:
                    pattern = (
                        "test_http.py"
                        if scenario["id"] == "first-moment"
                        else "test_http_concurrent.py"
                    )
                    scenario.update(
                        steps=[
                            '"{python}" -B -m unittest discover -s "{repo}/tests" -p ' + pattern
                        ],
                        expect={"exit": 0, "stderr": "OK"},
                    )
                call("harness_accept_scenarios", scenarios=scenarios)
            call("harness_advance", to_phase="accept")
            accepted = finish_public_acceptance(
                server,
                workspace_wire(
                    server,
                    "harness_accept_run",
                    expected_workspace=str(workspace),
                    repo=run["repo"],
                    subject="product",
                ),
            )
            assert accepted["structuredContent"]["result"]["passed"], accepted
            call("harness_accept_review", repo=run["repo"])
            decisions.append("accept")
            call("harness_accept", by="user", statement="Yes")
            assert store.status()["acceptance"]["accepted"]
            assert decisions == ["align+vision-lock", "plan-execution", "accept"]
            print(
                "golden ShelfLife scenarios: decisions=3; admitted root delivery; C accepted"
                if semantic
                else "golden http: decisions=3; HTTP + concurrency RED->GREEN; C accepted"
            )
    finally:
        server.close()


def provider_host(mode, workspace, surface, job_id=None):
    """Install external-provider doubles in each ordinary process owner.

    Preserve the detached controller, Windows Job monitor and ordinary dispatch;
    only replace provider execution with deterministic file-writing workers/reviewers.
    """
    from tests.python.core.acceptance_recovery_support import protocol

    from codex_harness.core import (
        audit_process,
        dispatch,
        mcp_planner,
        planner_jobs,
        planner_recovery,
        roundtable,
    )

    workspace = Path(workspace)
    filename = "index.html" if surface == "web" else "hashslip.py"
    actual_popen = subprocess.Popen
    actual_audit = audit_process.run_audit_process
    actual_run = dispatch.run_plan

    def command(role, project, job=None):
        return [sys.executable, "-B", str(Path(__file__).resolve()), role, project, surface] + (
            [job] if job else []
        )

    def launch(argv, **options):
        if "codex_harness.core.planner_jobs" in argv:
            argv = command("controller", argv[argv.index("--workspace") + 1], argv[-1])
        return actual_popen(argv, **options)

    def controller_payload(argv, **options):
        return actual_audit(command("worker", str(workspace), job_id), **options)

    def write_shelflife(root, phase):
        payload = json.loads((workspace / "scripted-provider.json").read_text(encoding="utf-8"))
        for name, text in payload[phase].items():
            (root / name).write_text(text, encoding="utf-8")

    def contracts(root):
        if surface == "shelflife":
            return write_shelflife(root, "contracts")
        first = surface == "cli" and not (workspace / "contracts-attempted").exists()
        if first:
            (workspace / "contracts-attempted").write_text("attempted", encoding="utf-8")
        (root / filename).write_text(
            "<!doctype html>"
            + "".join(
                f"<p>SELDON-STUB {op}</p>"
                for op in ("content", "navigation", "faq-pointer", "faq-keyboard")
            )
            if surface == "web"
            else "raise RuntimeError('broken stub')"
            if first
            else "import sys\n"
            "operation = 'help' if sys.argv[1:] == ['--help'] else 'receipt' "
            "if not sys.argv[1:] or sys.argv[1:2] == ['--label'] and len(sys.argv) == 3 "
            "else 'usage'\nprint('SELDON-STUB ' + operation, file=sys.stderr)\nsys.exit(3)\n",
            encoding="utf-8",
        )

    def build(root):
        if surface == "shelflife":
            return write_shelflife(root, "build")
        if surface == "cli" and not (workspace / "stall-attempted").exists():
            # A real descendant hangs mid-Build with uncommitted poisoned writes.
            # The Job owner must reap it; an exited fake cannot prove recovery.
            (workspace / "stall-attempted").write_text(str(root), encoding="utf-8")
            child = actual_popen(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "from pathlib import Path; import time; "
                    "Path('hashslip.py').write_text('POISON'); "
                    "Path('abandoned.txt').write_text('never merge'); time.sleep(600)",
                ],
                cwd=root,
            )
            child.wait()
        (root / filename).write_bytes((ROOT / "tests/fixtures/efficiency" / filename).read_bytes())
        if surface == "web":
            with (root / filename).open("a", encoding="utf-8") as out:
                out.write("<style>h1{background:linear-gradient(white,#fff)}</style>")

    def workers(plan, **options):
        return actual_run(plan, **options, fake_workers={"C": contracts, "B": build})

    def review(launch, *_):
        if "## Read-only candidate evidence" in launch.stdin_text:
            tier = "same-provider" if surface == "cli" else "cross-provider"
            assert f"## Review tier: {tier}" in launch.stdin_text
            assert "catalog: unverified" in launch.stdin_text
        text = "[]"
        if (
            mode == "server"
            and surface == "cli"
            and not (workspace / "review-output-sent").exists()
        ):
            (workspace / "review-output-sent").write_text("sent")
            payload = json.loads((ROOT / "tests/fixtures/efficiency/run26.json").read_text("utf-8"))
            text = json.dumps([payload["headingFinding"]])
        return tuple(
            value.replace(
                "source-only", "gpt-6.1-sol" if launch.cli == "codex" else "claude-sonnet-5-5"
            ).replace("Some(High)", "Some(Medium)" if surface == "web" else "Some(Medium)")
            if isinstance(value, str)
            else value
            for value in protocol(launch.cli, text=text)
        )

    subprocess.Popen = launch
    roundtable._run = review
    dispatch.run_plan = workers
    if mode == "server":
        original_dispatch = mcp_planner.PlannerServer._dispatch

        def noisy_dispatch(self, name, arguments, **options):
            if name == "harness_plan_start":
                print("tool diagnostic: café 中文")
                os.write(1, "raw diagnostic: 中文\n".encode())
            return original_dispatch(self, name, arguments, **options)

        mcp_planner.PlannerServer._dispatch = noisy_dispatch
        sys.argv = [sys.argv[0], "--workspace", str(workspace)]
        runpy.run_module("codex_harness.core.seldon_mcp", run_name="__main__")
    elif mode == "controller":
        # Accelerate the production no-progress clock, preserving B's scope.
        if surface == "cli":
            planner_recovery.STALL_SECONDS = 15
        audit_process.run_audit_process = controller_payload
        raise SystemExit(planner_recovery.monitor(workspace, job_id))
    else:
        result = planner_jobs.execute_worker_job(workspace, job_id)
        raise SystemExit(0 if result["status"] == "completed" else 1)


@pytest.mark.parametrize("surface", ["cli", "web"])
def test_golden_path(tmp_path, request, surface):
    """Protect idea→root product→accepted with three plain user decisions.

    Catch late acceptance reviewer/budget questions, reapproval after repairs/recovery,
    and changed limits launching without a diff; plan-review-only tests miss delivery.
    Replay run26/UX6 plans, reviewer tuples, schema refusals and browser actions.
    Catch incomplete preflight, invented TRACE nodes, illegal phase guidance and a late
    visual judgment staling scenarios; earlier authored-perfect journeys missed these seams.
    Replay the JSONKeys farm B refusal with installed Codex+Claude but two authorized Sol
    reviewers; prove the same-provider tier survives B, acceptance packet and final C.
    No model catalog is supplied: discovery-unavailable must still reach C with three
    decisions and catalog: unverified in B and the real acceptance packet. Catalog-backed
    fixtures missed the farm deadlock before B; exact proposed tuples must reach launches.
    Stub results are suite evidence only, never actual-provider acceptance.
    Replay UX-5's captured Unicode upsert and survive an uncaught malformed tool
    request plus tool stdout noise; direct handle_line tests miss pipe death.
    Replay ShelfLife: lose an exited owner's receipt, then hang a real descendant
    mid-Build. Both attempts must be quarantined and readmitted under the same B;
    ordinary successful fixtures missed UNKNOWN holding delivery forever.
    """
    if surface == "cli":
        trace_contract_golden(tmp_path.parent / (tmp_path.name + "-trace-contract"))
        http_golden(tmp_path / "http-journey")
        http_golden(tmp_path / "shelflife-scenarios", semantic=True)
    if surface == "web":
        request.getfixturevalue("web_browser_ready")
    started = time.monotonic()
    (tmp_path / "AGENTS.md").write_text(
        "Use gpt-6.1-sol at medium effort for all helper assignments, including reviews.\n",
        encoding="utf-8",
    )
    replies = queue.Queue()
    calls = decisions = 0
    counts = dict(happy=0, probes=0, polls=0)
    probing = False
    revision = None
    tools = tmp_path / "tools"
    tools.mkdir()
    codex = tools / ("codex.exe" if os.name == "nt" else "codex")
    captured_run = json.loads(
        (
            ROOT / "tests/fixtures/efficiency" / ("ux6.json" if surface == "web" else "run26.json")
        ).read_text("utf-8")
    )
    shutil.copy2(sys.executable, codex)
    shutil.copy2(sys.executable, tools / ("claude.exe" if os.name == "nt" else "claude"))
    # Keep OS/git/node tools while excluding the host's provider installations/bundles.
    paths = [str(tools), str(Path(shutil.which("git")).parent)]
    if os.name == "nt":
        paths += [
            os.path.join(os.environ["SystemRoot"], "System32"),
            os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0"),
        ]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
        "PATH": os.pathsep.join(paths),
        "LOCALAPPDATA": str(tools),
        "PYTHONIOENCODING": "cp1252",
    }
    if surface == "web":
        from codex_harness.core.semantic_runner import web_toolchain

        env["SELDON_WEB_CHROMIUM"] = web_toolchain(None, False)["chromium"]["path"]
    with (tmp_path / "server.log").open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "server", str(tmp_path), surface],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            env=env,
        )

        def read():
            for line in process.stdout:
                replies.put(json.loads(line))
            replies.put(None)

        threading.Thread(target=read, daemon=True).start()

        def rpc(method, params):
            process.stdin.write(
                json.dumps(
                    dict(jsonrpc="2.0", id=calls, method=method, params=params), ensure_ascii=False
                )
                + "\n"
            )
            process.stdin.flush()
            while True:
                reply = replies.get(timeout=180)
                assert reply is not None, (tmp_path / "server.log").read_text()
                if "id" in reply:
                    assert "error" not in reply, reply
                    return reply["result"]

        def call(tool_name, *, allow_error=False, _probe=False, **arguments):
            nonlocal calls, revision
            calls += 1
            category = (
                "polls"
                if tool_name in {"harness_worker_status", "harness_accept_status"}
                else "probes"
                if probing or _probe or allow_error
                else "happy"
            )
            counts[category] += 1
            result = rpc(
                "tools/call",
                dict(name=tool_name, arguments=dict(expected_workspace=str(tmp_path), **arguments)),
            )
            if result.get("isError") and not allow_error:
                print(json.dumps(result))
            if allow_error:
                assert result.get("isError"), result
                return result
            assert not result.get("isError"), (tool_name, json.dumps(result, ensure_ascii=True))
            content = "\n".join(c.get("text", "") for c in result.get("content", []))
            data = result.get("structuredContent", {})
            if tool_name == "harness_publish":
                assert "Next: harness_advance to_phase=execute" in content, content
            if tool_name == "harness_worker_status" and data.get("status") == "failed":
                assert "Next: harness_recovery mode=inspect" in content, content
            if tool_name == "harness_worker_status" and data.get("run", {}).get("delivery"):
                assert "Next: harness_advance to_phase=accept" in content, content
            revision = result.get("plannerConnection", {}).get("planRevision", revision)
            return result.get("structuredContent", {})

        def yes(card):
            nonlocal decisions
            decisions += 1
            call(
                "harness_record_chat_confirmation",
                gate=card["gate"],
                request_id=card["request_id"],
                expected_revision=card["plan_revision"],
                quoted_message="Yes",
                quote_sha256=hashlib.sha256(b"Yes").hexdigest(),
                actor_label="scripted user",
            )

        try:
            rpc(
                "initialize",
                dict(
                    protocolVersion="2025-06-18",
                    capabilities={},
                    clientInfo=dict(name="golden", version="1"),
                ),
            )
            process.stdin.write(
                json.dumps(dict(jsonrpc="2.0", method="notifications/initialized")) + "\n"
            )
            process.stdin.flush()
            call("harness_plan_status")
            call(
                "harness_plan_start",
                idea=captured_run["nodes"][0]["text"],
                name=captured_run["nodes"][0]["title"],
                plan_mode="semantic-v1",
            )
            if surface == "cli":
                calls += 1
                counts["probes"] += 1
                failed = rpc(
                    "tools/call",
                    dict(
                        name=["harness_upsert_node"],
                        arguments=dict(expected_workspace=str(tmp_path)),
                    ),
                )
                assert failed["isError"] and "unconfirmed" in failed["content"][0]["text"]
                assert rpc("ping", {}) == {}
                probing = True
                captured = json.loads(
                    (ROOT / "tests/fixtures/ux5-turn7-upsert.json").read_text("utf-8")
                )
                before = call("harness_show_node", id="V1")
                call("harness_upsert_node", node=captured["node"])
                unvalidated = call("harness_plan_status")["acceptance"]["scenarios"]
                assert any(
                    s.get("evidenceTier") == "scenario: unvalidated"
                    and "value" in s["validationMessage"]
                    and not s["executable"]
                    for s in unvalidated
                ), unvalidated
                captured_f3 = json.loads(
                    (ROOT / "tests/fixtures/f3-turn11-vision.json").read_text("utf-8")
                )
                call("harness_upsert_node", node=captured_f3)
                assert call("harness_show_node", id="V1")["fields"] == captured_f3["fields"]
                call(
                    "harness_upsert_node",
                    node={
                        k: before[k] for k in ("id", "kind", "title", "text", "parents", "fields")
                    },
                )
                probing = False
            a = call("harness_understanding_card", **captured_run["understanding"])
            yes(a)
            filename = "index.html" if surface == "web" else "hashslip.py"
            nodes = captured_run["nodes"]
            scenarios = nodes[0]["fields"]["scenarios"]
            written = call("harness_upsert_nodes", nodes=nodes)
            call("harness_advance", to_phase="plan")
            preflight = call(
                "harness_preflight",
                providers=["codex", "claude"],
                execution={},
                reviewer_node=next(
                    r["arguments"]["node"]
                    for r in captured_run["refusals"]
                    if r["tool"] == "harness_reviewer_choice"
                ),
            )
            issues = "; ".join(preflight["planningPrerequisites"])
            assert all(
                part in issues
                for part in (
                    "execution.call_cap",
                    "execution.expected_revision",
                    "execution.workers",
                    "reviewer-effort-invalid",
                )
            ), issues
            assert "reviewer-catalog" not in issues, issues
            assert preflight["catalog"] == "unverified"
            detected = preflight["providers"]
            assert all(p["executable"]["status"] == "available" for p in detected)
            farm = json.loads(
                (ROOT / "tests/fixtures/efficiency/jsonkeys-reviewers.json").read_text("utf-8")
            )
            choice = farm["choice"] if surface == "cli" else captured_run["choice"]
            choice["fields"].update(catalogId=None, catalogDigest=None)
            for reviewer in choice["fields"]["reviewers"]:
                reviewer["choiceId"] = None
            choice["fields"]["recommendation"]["choiceIds"] = []
            call("harness_reviewer_choice", node=choice, expected_spine_revision=revision)
            compiled = call("harness_compile", include_detail=True)
            options = dict(
                expected_revision=compiled["planRevision"],
                max_parallel=1,
                timeout=300,
                call_cap=4,
                workers=[
                    dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                    for b in ("C", "B")
                ],
            )
            proposal = dict(gate="plan-execution", execution=options, reviewer_node_id=choice["id"])
            if surface == "cli":
                # Farm turns 2-6: installed Claude must not override this two-Sol selection.
                captured = next(t["refusals"][-1] for t in reversed(farm["turns"]) if t["refusals"])
                proposal = {
                    k: v for k, v in captured["arguments"].items() if k != "expected_workspace"
                }
                proposal.update(execution=options, reviewer_node_id=choice["id"])
            b = call("harness_prepare_chat_confirmation", **proposal)
            repeated = call(
                "harness_prepare_chat_confirmation",
                gate="plan-execution",
                execution=options,
                reviewer_node_id=choice["id"],
                _probe=True,
            )
            assert repeated["request_id"] == b["request_id"]
            reviewers = b["subject"]["reviewers"]
            assert reviewers == [
                {
                    k: r[k] if k != "effort" else r[k]["value"]
                    for k in ("name", "provider", "model", "effort")
                }
                for r in choice["fields"]["reviewers"]
            ]
            tier = "same-provider" if surface == "cli" else "cross-provider"
            assert b["subject"]["reviewTier"] == tier and f"Review tier: {tier}" in b["card"]
            assert "Cross-subscription is preferred, never required" in b["card"]
            assert b["subject"]["scheduledReviewStarts"] == {"plan": 4, "full-vision": 4}
            assert b["subject"]["repairReserve"] == 2
            assert all(r["name"] in b["card"] and r["model"] in b["card"] for r in reviewers)
            assert b["card"].count("up to 10 reviews") == 1
            assert b["subject"]["catalog"] == "unverified"
            assert "catalog: unverified" in b["card"]
            yes(b)
            call("harness_advance", to_phase="roundtable")
            plan_review = call(
                "harness_roundtable",
                max_rounds=1,
                max_total=2,
                timeout=5,
            )
            assert [
                {k: r[k] for k in ("name", "provider", "model", "effort")}
                for r in plan_review["reviewers"]
            ] == reviewers
            if surface == "cli":
                review = call("harness_review", id=plan_review["roundId"], _probe=True)
                row = review["findingMembers"][0]
                assert row["node_id"] == "plan" and row["finding"].startswith("[TRACE]")
                captured_closure = next(
                    r["arguments"]["findings"][0]
                    for r in captured_run["refusals"]
                    if r["tool"] == "harness_resolve_findings"
                )
                closure = captured_closure["closure"]
                closure.update(
                    expectedManifestRevision=options["expected_revision"],
                    memberIds=[row["id"]],
                    nodeRefs=[dict(nodeId="G1", revision=written["revisions"]["G1"])],
                )
                call(
                    "harness_resolve_findings",
                    findings=[
                        dict(
                            id=row["id"],
                            status="resolved",
                            resolution=captured_closure["resolution"],
                            closure=closure,
                        )
                    ],
                )
            call("harness_advance", to_phase="publish")
            call("harness_publish")
            call("harness_advance", to_phase="execute")
            if surface == "cli":
                changed = call(
                    "harness_worker_start", **{**options, "call_cap": 5}, allow_error=True
                )
                diff = changed["structuredContent"]["chatConfirmation"]["diff"]
                assert [(d["field"], d["before"], d["after"]) for d in diff] == [
                    ("execution.callCap", 4, 5)
                ]
                assert (
                    sum(c["text"].count("Your decision: approve B?") for c in changed["content"])
                    == 1
                )
            job = call("harness_worker_start", **options)
            deadline = time.monotonic() + 300
            while job["status"] not in ("completed", "failed") and time.monotonic() < deadline:
                job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=60)
            if surface == "web":
                saved = json.loads(Path(job["run"]["recordPath"]).read_text("utf-8"))
                assert saved["semanticValidation"]["state"] == "FAIL"
                roots = saved["beads"]["B"]["integrated"]["root"]
                assert any(
                    a["checks"]["textContrastAA"] is None
                    for r in saved["semanticValidation"]["results"]
                    for a in r["actions"].values()
                )
                call(
                    "harness_accept_assess",
                    judgment=dict(
                        repo=roots,
                        check="textContrastAA",
                        passed=True,
                        statement="Scripted human fixture observation: all gradient endpoints are"
                        " white and generated label is dark; review retained viewport/f"
                        "ull-page images in both motion preferences. This is not automa"
                        "ted contrast proof.",
                        by="scripted observer",
                    ),
                )
                repaired = call(
                    "harness_recovery",
                    mode="resolve",
                    job_id=job["jobId"],
                    choice="revalidate",
                    reason="Consume candidate-bound manual contrast observation within B",
                )
                assert repaired["validation"]["state"] == "PASS", repaired
                job = call("harness_worker_status", job_id=job["jobId"])
            if surface == "cli":
                failed = json.loads(Path(job["run"]["recordPath"]).read_text())
                assert failed["beads"]["C"]["integrated"]["semantic"]["state"] == "ERROR"
                probing = True
                original = job["jobId"]
                proof = call("harness_recovery", mode="inspect", job_id=original)
                while proof["verdict"] != "VERIFIED-QUIESCENT" and time.monotonic() < deadline:
                    time.sleep(0.1)
                    proof = call("harness_recovery", mode="inspect", job_id=original)
                assert proof["verdict"] == "VERIFIED-QUIESCENT", proof
                evidence = Path(job["evidenceDir"])
                (evidence / "quiescence.json").rename(evidence / "retained-quiescence.json")
                resolved = call(
                    "harness_recovery",
                    mode="resolve",
                    job_id=original,
                    choice="readmit",
                    reason="Repair the failed Contracts stub",
                )
                assert "card" not in resolved
                assert resolved["proof"]["verdict"] == "ABANDONED"
                assert resolved["quarantine"] is True
                job = call("harness_worker_start", **options)
                assert job["jobId"] == resolved["successor"] != original
                while job["status"] not in ("completed", "failed") and time.monotonic() < deadline:
                    job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=60)
                assert job["status"] == "failed", job
                stalled = job["jobId"]
                proof = call("harness_recovery", mode="inspect", job_id=stalled)
                assert proof["verdict"] == "VERIFIED-QUIESCENT", proof
                print("RECOVERY-1 inspect proof:", json.dumps(proof, sort_keys=True))
                print("RECOVERY-1 job record:", json.dumps(job, sort_keys=True))
                assert proof["controllerAndChildren"]["stopReason"] == "worker-stalled"
                assert proof["controllerAndChildren"]["activeProcessesAfterTeardown"] == 0
                poisoned = Path((tmp_path / "stall-attempted").read_text(encoding="utf-8"))
                assert (poisoned / "hashslip.py").read_text() == "POISON"
                assert not (tmp_path / "abandoned.txt").exists()
                resolved = call(
                    "harness_recovery",
                    mode="resolve",
                    job_id=stalled,
                    choice="readmit",
                    reason="End stalled Build and retry within the same B",
                )
                assert resolved["quarantine"] is True
                job = call("harness_worker_start", **options)
                assert job["jobId"] == resolved["successor"] != stalled
                while job["status"] not in ("completed", "failed") and time.monotonic() < deadline:
                    job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=60)
                assert not (tmp_path / "abandoned.txt").exists()
                probing = False
            assert job["status"] == "completed", job
            assert job["run"]["semanticContracts"]["state"] == "RED", job
            assert job["run"]["semanticValidation"]["state"] == "PASS", job
            if surface == "web":
                # Protect mode provenance and preview guidance through canonical dispatch;
                # CLI journeys never render, and packet fixtures do not consume dispatch.
                retained = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
                proof = retained["semanticValidation"]["results"]
                actions = [a for r in proof for a in r["actions"].values()]
                assert all(a["browserMode"] in {"normal", "single-process"} for a in actions)
                # Execute the packet's actual shell command: a quoted Windows executable
                # without PowerShell's & looks plausible but never opens the preview.
                from codex_harness.core.audit_process import run_audit_process

                prompt = retained["beads"]["B"]["prompt"]
                command = next(c for c in prompt.split("`") if "--preview ." in c)
                try:
                    preview = run_audit_process(
                        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                        cwd=tmp_path,
                        timeout=20,
                    )
                    output, cleanup = preview.stdout, preview.audit_control
                except subprocess.TimeoutExpired as error:
                    output, cleanup = error.output, error.audit_control
                observed = json.loads(output)
                assert observed.get("state") != "ERROR", observed
                assert observed["browserMode"] in {"normal", "single-process"}
                assert cleanup["quiescenceVerified"]
                assert cleanup["activeProcessesAfterTeardown"] == 0
                print(
                    f"golden web: scenarios={len(proof)}; journeys={len(actions)}; "
                    f"browserModes={sorted({a['browserMode'] for a in actions})}"
                )
            assert (tmp_path / filename).is_file()
            if surface == "cli":
                assert subprocess.check_output(
                    [sys.executable, "-B", str(tmp_path / filename), "--help"], cwd=tmp_path
                ).startswith(b"usage:")
            if surface == "web":
                call(
                    "harness_accept_assess",
                    judgment=dict(
                        repo=str(tmp_path),
                        check="visualQuality",
                        passed=True,
                        statement="Scripted observer: actual fixture page matches the locked read"
                        "able quality bar and Done; source-only journey",
                        by="scripted user",
                    ),
                )
            call("harness_advance", to_phase="accept")
            deadline = time.monotonic() + 300
            acceptance = call("harness_accept_run", repo=str(tmp_path), subject="product")
            if surface == "web":
                judgment = dict(captured_run["judgments"][-1], repo=str(tmp_path))
                call("harness_accept_assess", judgment=judgment, _probe=True)
            while acceptance.get("result") is None and time.monotonic() < deadline:
                acceptance = call(
                    "harness_accept_status", id=acceptance["attemptId"], wait_seconds=60
                )
            assert acceptance["result"]["passed"], acceptance
            review = call("harness_accept_review", repo=str(tmp_path))
            attempt_id = review["attemptId"]
            deadline = time.monotonic() + 300
            while review["state"] in {"queued", "running"} and time.monotonic() < deadline:
                review = call("harness_accept_status", id=attempt_id, wait_seconds=60)
                assert review["attemptId"] == attempt_id, review
            assert review["state"] == "completed", review
            assert review["result"]["id"] == attempt_id, review
            assert [
                {k: r[k] for k in ("name", "provider", "model", "effort")}
                for r in review["result"]["reviewers"]
            ] == reviewers
            decisions += 1
            call("harness_accept", by="user", statement="yes")
            final_status = call("harness_plan_status", _probe=True)
            assert final_status["acceptance"]["accepted"]
            assert {s["id"] for s in final_status["acceptance"]["scenarios"]} == {
                s["id"] for s in scenarios["scenarios"]
            }
            assert all(s["lastResult"] == "pass" for s in final_status["acceptance"]["scenarios"])
            # The real saved producer reaches HTTP and the shipped browser view.
            # Catch revision/blocker/next-step divergence and broken asset wiring;
            # planning-only tests and synthetic UI fixtures miss this integration.
            from tests.python.core.cockpit_server_support import get, running

            before = {
                p: p.read_bytes() for p in (tmp_path / ".codex-harness").rglob("*") if p.is_file()
            }
            run_path = Path(job["run"]["recordPath"])
            runs_root = (
                run_path.parents[2] if run_path.parent.name == "evidence" else run_path.parents[1]
            )
            with running(tmp_path, workspace=tmp_path, runs_root=runs_root) as connection:
                status_code, _, body = get(connection, "/api/status")
                assert status_code == 200
                observed = json.loads(body)["plan"]
                for key in ("plan_revision", "pending_gate", "open_findings", "next", "acceptance"):
                    assert observed[key] == final_status[key], key
                if surface == "web":
                    subprocess.run(
                        [
                            "node",
                            str(ROOT / "tests/browser/cockpit/checks.js"),
                            f"http://127.0.0.1:{connection.port}",
                            observed["plan_revision"],
                            observed["next"],
                        ],
                        check=True,
                        timeout=45,
                    )
            after = {
                p: p.read_bytes() for p in (tmp_path / ".codex-harness").rglob("*") if p.is_file()
            }
            assert before == after
            assert decisions == 3
            assert counts["happy"] <= 25, counts
            assert sum(counts.values()) == calls
            print(
                f"golden {surface}: decisions={decisions}; planner MCP calls={calls}; "
                f"happy={counts['happy']}; probes={counts['probes']}; polls={counts['polls']}; "
                f"seconds={time.monotonic() - started:.2f}"
            )
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            assert process.returncode == 0, (tmp_path / "server.log").read_text("utf-8")


if __name__ == "__main__":
    provider_host(*sys.argv[1:])
