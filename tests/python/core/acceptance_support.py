import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def drain_acceptance(owner, store, timeout=20):
    deadline = time.time() + timeout
    while owner.thread and owner.thread.is_alive() and time.time() < deadline:
        owner.thread.join(timeout=0.01)
        owner.drain(store)
    assert not owner.thread or not owner.thread.is_alive(), (
        f"Acceptance exceeded {timeout}s wall-clock deadline"
    )
    owner.drain(store)


HTTP_DEADLINE = """import os as _os, threading as _threading, time as _time
_http_end = _time.time() + 15
def _http_watchdog():
    while _time.time() < _http_end:
        _time.sleep(.25)
    _os.write(2, b'HTTP verifier exceeded 15s wall-clock deadline, '
        b'including startup/readback/cleanup\\n')
    _os._exit(124)
_threading.Thread(target=_http_watchdog, daemon=True).start()
"""


def http_golden(workspace, semantic=False):
    """Protect subprocess HTTP planning through publication, real RED/GREEN and C.

    Catch ShelfLife's import-only audit deadlock and unknown concurrency helpers;
    the semantic CLI/static-page journeys never consume a prospective audit.
    Preserve opposing/dossier-replacement obligations, stale closures and exact B
    scope under verifier/catalog/wave changes; a frozen happy plan misses those seams.
    Preserve real importer/SQLite/server-restart RED-to-GREEN, bounded descendant
    cleanup, independent bead recovery counts, retained-candidate revalidation,
    changed-dependency delivery and migration/fresh-C binding. Ordinary successful
    CLI/UI execution cannot catch those HTTP/persistence/recovery regressions.
    Only external provider replies are doubled; owners, HTTP and evidence are real.
    """
    from tests.python.core.acceptance_recovery_support import protocol
    from tests.python.core.planning_support import source_only_expansion
    from tests.python.core.semantic_wiring_support import node
    from tests.python.core.test_golden_path import (
        judge_candidate,
        judge_reply,
        scripted_judgment,
        scripted_review_reply,
        scripted_worker,
        settle_decision,
    )

    from codex_harness.core import approvals, review_packets, review_provider, roundtable
    from codex_harness.core import planner_confirmations as confirmations
    from codex_harness.core import planner_jobs as jobs
    from codex_harness.core import planner_recovery as recovery
    from codex_harness.core.dispatch_shared import STATUS_AUDIT_HELD, STATUS_VALIDATED
    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.plan_store import PHASES, PlanStore
    from codex_harness.core.planner_candidate import record_judgment
    from codex_harness.core.planner_nodes import batch_nodes
    from codex_harness.core.planner_review import review as review_acceptance
    from codex_harness.core.readiness import readiness_review
    from codex_harness.core.status_api import plan_status

    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("Use gpt-6.1-sol at medium effort for reviews.\n")
    server = PlannerServer(workspace)
    decisions = []
    expected_job_id = None

    store = server.store = PlanStore(workspace, "http")

    def observe(operation):
        """Observe real domain results/refusals without restoring a deleted MCP alias."""
        try:
            result = operation()
        except (ValueError, OSError, KeyError, TypeError) as error:
            detail = getattr(error, "approval", None) or {}
            return dict(
                isError=True,
                refusalCode=getattr(error, "code", "operation-error"),
                structuredContent=detail,
                content=[dict(text=str(error))],
            )
        return dict(
            isError=False,
            structuredContent=result,
            content=[dict(text=json.dumps(result, default=str))],
        )

    def open_dream(*, idea, name, plan_mode):
        store.upsert_node(node("V1", "vision", [], **({"planMode": plan_mode} if semantic else {})))
        vision = store.spine().node("V1")
        vision.update(title=name, text=idea)
        store.upsert_node(vision)

    def understanding(**fields):
        vision = store.spine().node("V1")
        vision["fields"].update(fields)
        store.upsert_node(vision)

    def put_node(*, node):
        store.upsert_node(node)
        preserve_intent()

    def put_nodes(*, nodes):
        batch_nodes(store, nodes, lambda _: None)
        preserve_intent()

    def preserve_intent():
        if store._meta()["locked_vision_json"]:
            request = approvals.intent_assessment(store, store.spine().node("V1"), prepare=False)
            if request.get("id") and not request["positiveCredit"]:
                scripted_judgment(store, request, dict(impact="preserved"))

    def remove_node(*, id):
        return store.remove_node(id)

    def compile_current(*, include_detail=False):
        # The compiler's graph consumer must reject invalid DV2/order plans before export.
        from tempfile import TemporaryDirectory

        from codex_harness.core.graph import load_plan
        from codex_harness.core.spine import compile_manifest

        with TemporaryDirectory(dir=workspace) as scratch:
            manifest = Path(scratch) / "manifest.json"
            manifest.write_text(json.dumps(compile_manifest(store.spine())), encoding="utf-8")
            load_plan(manifest)
        if semantic:
            (workspace / "template").mkdir(exist_ok=True)
        return store.compile_template(workspace / "template")

    def advance_phase(*, to_phase):
        start, end = PHASES.index(store.phase()), PHASES.index(to_phase)
        steps = PHASES[start + 1 : end + 1] if end >= start else [to_phase]
        for phase in steps:
            store.advance(phase, by="golden:canonical-domain-fixture")

    def read_status(*, full=False, include_detail=False):
        return plan_status(store)

    def read_judgment(*, judgment_id, candidate=None):
        return store.judgment(judgment_id, **({"candidate": candidate} if candidate else {}))

    def save_wave_map(**arguments):
        return store.record_wave_map(**arguments)

    def save_catalog(**arguments):
        return store.record_reviewer_catalog(**arguments)

    def save_reviewers(**arguments):
        return store.record_reviewer_selection(**arguments)

    def prepare_words(**arguments):
        return confirmations.prepare_chat_confirmation(store, **arguments)

    def resolve_findings(*, findings):
        return store.resolve_findings(findings)

    def adopt_policy(*, expectedManifestRevision, slots):
        return store.record_plan_review_policy(slots, expectedManifestRevision)

    def review_readiness(**arguments):
        return readiness_review(store, **arguments)

    def review_plan(**arguments):
        return roundtable.run_roundtable(store, **arguments)

    def review_detail(*, id):
        from codex_harness.core.planner_views import review_finding_members

        result = store.review(id)
        return dict(result, findingMembers=review_finding_members(store, result))

    def publish_plan(**arguments):
        return store.publish(
            by="golden:canonical-domain-fixture", export_dir=workspace / "plan", **arguments
        )

    def admit_audit(*, mode):
        assert mode == "prospective"
        return server.audits.start(store)

    def audit_status(*, id):
        server.audits.drain(store)
        return store.audit_attempt(id, server.audits.instance)

    def acceptance_scenarios(*, derive=False, scenarios=None):
        from codex_harness.core.acceptance import derive_scenarios

        if scenarios is not None:
            store.save_acceptance_scenarios(scenarios)
        elif derive:
            store.save_acceptance_scenarios(derive_scenarios(store.locked_acceptance_spine()))
        return dict(scenarios=store.acceptance_scenarios())

    def recover(*, mode, job_id, **arguments):
        nonlocal expected_job_id
        if mode == "inspect":
            return recovery.inspect(workspace, job_id)
        if arguments.get("choice") == "revalidate":
            from codex_harness.core import run_loop

            # Retained verification owns a fresh bounded continuation, not the
            # already exited worker's control. Keep the controller's actual
            # validation/refusal receipt, including changed-product failures.
            previous = len(approvals.records(store, recovery.RESOLVED))
            run = run_loop.steer(
                store, direction="continue", rationale=arguments["reason"], content={}
            )
            recorded = recovery.disposition(store, job_id)
            assert len(approvals.records(store, recovery.RESOLVED)) > previous, run
            assert recorded and recorded["choice"] == "revalidate", (recorded, run)
            return recorded
        if arguments.get("choice") == "readmit":
            # The real monitor may already have applied the controller's automatic
            # recovery. Consume that durable disposition rather than spend a retry
            # or race a second resolution writer.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                recorded = recovery.disposition(store, job_id)
                if recorded:
                    expected_job_id = recorded.get("successor")
                    return recorded
                time.sleep(0.02)
        return recovery.resolve(store, job_id, **arguments)

    def resume_observed_hold(job_id, reason):
        from codex_harness.core import run_loop

        deadline = time.monotonic() + 5
        while not recovery.disposition(store, job_id) and time.monotonic() < deadline:
            time.sleep(0.02)
        released = recovery.disposition(store, job_id)
        assert released["choice"] == "abandon" and released["ownershipReleased"], released
        proof = recovery.inspect(workspace, job_id)
        assert proof["controllerAndChildren"]["quiescenceVerified"], proof
        # Unknown permission telemetry is never retried automatically. The fixture
        # supplies explicit repair meaning, updates the worker obligation and lets
        # the controller publish a fresh isolated namespace under the same B.
        repair = store.spine().node("B")
        repair["text"] += " " + reason
        resumed = run_loop.steer(
            store, direction="repair", rationale=reason, content=dict(nodes=[repair])
        )
        assert not resumed.get("effect") and resumed.get("question"), resumed
        return released

    def assess_execution(*, judgment):
        return record_judgment(store, **judgment)

    def admit_acceptance(*, repo, subject):
        return server.acceptance.start(store, repo, subject=subject)

    def acceptance_status(*, id):
        server.acceptance.drain(store)
        return server.acceptance.status(store, id)

    def finish_domain_acceptance(server, admitted):
        assert not admitted["isError"], admitted
        server.acceptance.dispatch_after_flush(store)
        drain_acceptance(server.acceptance, store)
        return observe(lambda: acceptance_status(id=admitted["structuredContent"]["attemptId"]))

    def acceptance_review(**arguments):
        return review_acceptance(store, **arguments)

    def accept_product(**arguments):
        return store.accept(**arguments)

    def worker_verifier(*, source, path, expected_manifest_revision, expected_file_sha256, **_):
        # Workers own source; the canonical scope consumer must still notice protected-byte changes.
        assert store.manifest_revision() == expected_manifest_revision
        target = workspace / "template" / path
        assert hashlib.sha256(target.read_bytes()).hexdigest() == expected_file_sha256
        scripted_worker(run["beads"]["B"]["prompt"], workspace / "template", {path: source})

    def answer_semantics(run):
        from codex_harness.core import run_loop

        pending = run.get("question")
        assert pending, run
        pending = dict(pending, judgment=store.judgment(pending["requestId"], candidate=pending["candidate"]))
        if pending["family"] in {"author", "direction"}:
            content = {}
            if pending["purpose"] == "finding-dispositions":
                members = pending["context"]["members"]
                retained = {}
                for record in approvals.records(store, "judgment-recorded"):
                    if (
                        record["question"] != "finding-disposition"
                        or record["verdict"] != "positive"
                    ):
                        continue
                    for ref in record["sourceRefs"]:
                        for proposal in ref["payload"].get("facts", {}).get("proposals", []):
                            if (
                                record["meaning"]["memberDispositions"].get(proposal["id"])
                                == proposal["status"]
                            ):
                                retained.setdefault(proposal["id"], proposal)
                scope = approvals.scope_assessment(store, prepare=False)
                assert scope["positiveCredit"] and scope["meaning"]["impact"] == "HOW", scope
                assert members and all(
                    m["state"] == "pending" and m["id"] in retained for m in members
                ), members
                content["findings"] = [dict(retained[m["id"]]) for m in members]
            value = dict(
                option="ready" if pending["family"] == "author" else "continue",
                rationale=(
                    "Reassess retained repair declarations after the HOW-only telemetry amendment"
                )
                if content
                else "Retain the accepted HTTP behavior and canonical worker ownership",
                content=content,
            )
        else:
            value = judge_reply(pending)
        return run_loop.answer(
            store,
            request_id=pending["requestId"],
            answer=value,
            source="golden:scripted-semantic-provider",
        )

    def read_execution(job_id):
        observed = jobs.worker_job_status(workspace, job_id)
        # A segment's exit does not settle the controller's automatic next effect.
        return (
            dict(observed, status="pending-controller")
            if observed["status"] == "completed"
            else observed
        )

    def start_execution(**options):
        nonlocal expected_job_id
        from codex_harness.core import run_loop

        run = run_loop.cursor(store)
        if run is None:
            # FIX17: operator fixture configures structure before the controller starts.
            from codex_harness.core.planner_review import selection_inputs, delivery_review_selection
            selected = delivery_review_selection(selection_inputs(store)[1]["fields"])[0]
            worker = {k: options["workers"][0][k] for k in ("provider", "model", "effort")}
            (workspace / ".codex-harness/setup.json").write_text(json.dumps(dict(worker=worker,
                reviewers=selected, max_parallel=options["max_parallel"], timeout=options["timeout"])), encoding="utf-8")
            run = run_loop.start(store)
        for _ in range(30):
            if run.get("effect"):
                assert run["effect"]["kind"] == "build", run
                selected = expected_job_id or run["jobId"]
                expected_job_id = None
                return execution_status(job_id=selected, wait_seconds=0)
            if run.get("question"):
                run = answer_semantics(run)
            else:
                assert not run.get("userRequest"), run
                run = run_loop.run_loop(store)
        raise AssertionError("HTTP semantic fixture failed to reach a bounded worker segment")

    def execution_status(*, job_id, wait_seconds):
        from codex_harness.core import run_loop

        deadline = time.monotonic() + wait_seconds

        def await_observation():
            # Match the original bounded status reader's one-second cadence.
            # Completion still requires the actual owner/cleanup receipts.
            time.sleep(max(0, min(1, deadline - time.monotonic())))

        while True:
            observed = read_execution(job_id)
            run = run_loop.cursor(store)
            segment = jobs.latest_segment(store, job_id)
            terminal_path, cleanup_path = segment / "terminal.json", segment / "quiescence.json"
            if terminal_path.is_file() and cleanup_path.is_file():
                terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
                cleanup = json.loads(cleanup_path.read_text(encoding="utf-8"))
                assert jobs.verified_segment_exit(
                    jobs._request(jobs._directory(workspace, job_id)),
                    segment.name,
                    terminal,
                    cleanup,
                )
                observed["terminal"] = terminal
                if terminal["status"] == "failed":
                    return dict(observed, status="failed")
                record = json.loads(Path(observed["run"]["recordPath"]).read_text(encoding="utf-8"))
                if record["status"] == STATUS_VALIDATED:
                    settled = run_loop.run_loop(store)
                    # Completion can wake automatic acceptance. Its ordinary owner
                    # finishes before the separate stale-capture/domain replay.
                    while settled.get("effect") and time.monotonic() < deadline:
                        await_observation()
                        settled = run_loop.cursor(store)
                    if settled.get("effect"):
                        return dict(observed, status="pending-controller")
                    return dict(observed, status="completed")
                # The same run loop consumes the exited segment, prepares its exact
                # execution question and releases a fresh bounded Build segment.
                run = run_loop.run_loop(store)
                while run.get("question"):
                    if run["question"]["family"] == "direction":
                        assert "Integrated GREEN" in run["question"]["purpose"], run
                        current = read_execution(job_id)
                        completed = json.loads(
                            Path(current["run"]["recordPath"]).read_text(encoding="utf-8")
                        )
                        assert completed["status"] == STATUS_VALIDATED, completed
                        assert completed["semanticValidation"]["state"] == "PASS", completed
                        assert completed["delivery"]["root"] == str(workspace.resolve()), completed
                        return dict(current, status="completed", terminal=terminal)
                    run = answer_semantics(run)
                if run.get("effect") and run["effect"]["id"] != segment.name:
                    return read_execution(job_id)
            if time.monotonic() >= deadline:
                return observed
            await_observation()

    def scope_how(rationale):
        from codex_harness.core.approvals import scope_assessment

        compile_current(include_detail=True)
        judgment = scope_assessment(store, prepare=True)
        if judgment.get("id") and not judgment["positiveCredit"]:
            scripted_judgment(store, judgment, dict(impact="HOW"), rationale=rationale)

    def yes(card):
        if card["gate"] != "invocations":
            decisions.append(card["gate"])
        outcome = confirmations.complete_chat_confirmation(
            store,
            card["request_id"],
            gate=card["gate"],
            expected_revision=card["plan_revision"],
            quoted_message="Yes",
            quote_sha256=hashlib.sha256(b"Yes").hexdigest(),
            actor_label="golden fixture; source and identity unverified",
        )
        assert settle_decision(store, outcome)["state"] == "approved"

    product = """import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class API(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(response_body().encode())
    def log_message(self, *args):
        pass
def response_body() -> str:
    return b'shelf'.decode()
server = ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), API)
print(server.server_port, flush=True)
server.serve_forever()
"""
    proof = (
        HTTP_DEADLINE
        + """import queue, subprocess, sys, threading, unittest
from pathlib import Path
from urllib.request import urlopen
class HTTP(unittest.TestCase):
    def test_status_and_body(self):
        with subprocess.Popen([sys.executable, '-B', 'server.py', '0'],
                              cwd=Path(__file__).resolve().parents[1],
                              stdout=subprocess.PIPE, text=True) as server:
            try:
                ports = queue.Queue()
                threading.Thread(
                    target=lambda: ports.put(server.stdout.readline()), daemon=True
                ).start()
                try:
                    port = int(ports.get(timeout=5))
                except queue.Empty:
                    raise TimeoutError("HTTP server did not publish a port within 5s")
                with urlopen(f'http://127.0.0.1:{port}/items', timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b'shelf')
            finally:
                server.kill()
                server.wait(timeout=5)
"""
    )
    helper = (
        HTTP_DEADLINE
        + """import queue, subprocess, sys, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen
def simultaneous():
    with subprocess.Popen([sys.executable, '-B', 'server.py', '0'],
                          cwd=Path(__file__).resolve().parents[1],
                          stdout=subprocess.PIPE, text=True) as server:
        try:
            ports = queue.Queue()
            threading.Thread(
                target=lambda: ports.put(server.stdout.readline()), daemon=True
            ).start()
            try:
                port = int(ports.get(timeout=5))
            except queue.Empty:
                raise TimeoutError("HTTP server did not publish a port within 5s")
            def get(_):
                with urlopen(f'http://127.0.0.1:{port}/items', timeout=5) as response:
                    return response.status, response.read()
            with ThreadPoolExecutor(max_workers=4) as pool:
                return list(pool.map(get, range(4)))
        finally:
            server.kill()
            server.wait(timeout=5)
"""
    )
    opaque = """import unittest
from helpers import simultaneous
from test_status import HTTP as ReadinessHTTP
class ConcurrentHTTP(unittest.TestCase):
    def test_simultaneous_requests(self):
        self.assertEqual(simultaneous(), [(200, b'shelf')] * 4)
"""
    try:
        open_dream(
            idea="Read shelf items over HTTP",
            name="http",
            plan_mode="semantic-v1" if semantic else "legacy",
        )
        store = server.store
        source_only_expansion(store)
        understanding(
            vision="Read shelf items over HTTP",
            users="Local operator",
            first_user_moment="GET items returns shelf",
            scope_in="Concurrent HTTP reads",
            scope_out="Remote hosting",
            assumptions=[],
        )
        # Legacy understanding saves a node; request the shared A subject explicitly.
        yes(prepare_words(gate="align+vision-lock"))
        vision = store.spine().node("V1")
        if semantic:
            missing = read_status(full=True)["acceptance"]
            assert not missing["candidatePrerequisite"]["complete"] and not missing["accepted"]
            assert (
                "retained candidate unavailable" in missing["invalidMessage"]
                and missing["candidatePrerequisite"]["next"]
            )
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
                            "dirs": [],
                            "files": [
                                {
                                    "path": "{data}/mixed.csv",
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
                                        {"input": "{data}/mixed.csv"},
                                        "--db",
                                        {"input": "{data}/pantry.sqlite3"},
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
                        "setup": {"dirs": []},
                        "actions": [
                            {
                                "id": "unreadable",
                                "cli": {
                                    "target": "importer",
                                    "operation": "import-unreadable",
                                    "argv": [
                                        {"input": "{data}/missing.csv"},
                                        "--db",
                                        {"input": "{data}/pantry.sqlite3"},
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
            requirements = [node(f"R{i}", "requirement", ["EXPORT_STORY"]) for i in range(1, 6)]
            owned = ["server.py", "import_items.py", "verify_http.py"]
            nodes = [
                vision,
                node("F", "feature", ["V1"]),
                dict(node("EXPORT_STORY", "story", ["F"]), text="export locally"),
                *requirements,
                dict(node("G1", "design", [r["id"] for r in requirements]), text="export locally"),
                node(
                    "T_HTTP",
                    "test",
                    ["G1"],
                    module="verify_http.py",
                    level="semantic",
                    execution={
                        "entry": "verify_http.py",
                        "runtime": sys.executable,
                        "argv": ["data/pantry.sqlite3"],
                        "testRoots": ["verify_http.py"],
                        "rationale": (
                            "Planned importer/SQLite/HTTP readback verifier "
                            "supplied and run later in this journey"
                        ),
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
                    "I",
                    "bead",
                    ["T_HTTP"],
                    ownedFiles=owned,
                    dependsOn=["C"],
                    lessons=[],
                    semanticPhase="build",
                ),
                node(
                    "B",
                    "bead",
                    ["G1"],
                    ownedFiles=owned,
                    dependsOn=["I", "C"],
                    lessons=[],
                    semanticPhase="build",
                ),
            ]
            put_nodes(nodes=nodes)
            # DV2: refuse a multi-build plan before any worker can run, then
            # replan through the public writer; earlier fixtures claimed multi-build.
            refused = observe(lambda: compile_current())
            assert refused["isError"] and (
                "semantic-v1 runs one build bead until Stage 3a (DV2): merge build beads into one"
            ) in str(refused)
            bead = store.spine().node("B")
            bead["fields"]["dependsOn"] = ["C"]
            put_node(node=bead)
            remove_node(id="I")
            # A contracts-only plan needs a new build, not an impossible merge.
            remove_node(id="B")
            missing_build = observe(lambda: compile_current())
            assert missing_build[
                "isError"
            ] and "add one build bead that depends on the contracts bead" in str(missing_build)
            assert "merge build beads" not in str(missing_build)
            put_node(node=bead)
            # Counting builds alone missed contracts-after-build early completion.
            contracts = store.spine().node("C")
            bead["fields"]["dependsOn"], contracts["fields"]["dependsOn"] = [], ["B"]
            put_nodes(nodes=[bead, contracts])
            wrong_order = observe(lambda: compile_current())
            assert wrong_order["isError"] and "contracts must supply RED first" in str(wrong_order)
            bead["fields"]["dependsOn"], contracts["fields"]["dependsOn"] = ["C"], []
            put_nodes(nodes=[bead, contracts])
            assert not list((workspace / ".codex-harness" / "worker-jobs").glob("*/started.json"))
            advance_phase(to_phase="plan")
            compile_current(include_detail=True)
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
            put_node(node=vision)
            compile_current(include_detail=True)
            held = next(s for s in store.acceptance_scenarios() if s["id"] == "S_HTTP")
            assert held["evidenceTier"] == "scenario: unvalidated" and not held["executable"]
            from codex_harness.core.semantic_runner import run as run_scenarios

            pending = store._manifest(store.spine())["semantic"]
            (workspace / "import_items.py").write_text("# not executed\n")
            blocked = run_scenarios(
                pending["scenarios"], workspace, pending["targets"], "validation"
            )
            assert blocked[-1]["state"] == "pending-judgment" and blocked[-1]["unperformed"], (
                blocked
            )
            assert "observation adapter" in blocked[-1]["reason"], blocked
            assert not blocked[-1].get("actions"), (
                blocked
            )  # Unknown intent never invents an execution.
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
            put_node(node=vision)
            template = workspace / "template"
            template.mkdir(exist_ok=True)
            product = product.replace(
                "self.wfile.write(response_body().encode())",
                "import sqlite3\n        from contextlib import closing\n"
                "        with closing(sqlite3.connect(sys.argv[2])) as db:\n"
                "            rows = db.execute('select name from items')\n"
                "            self.wfile.write(','.join(r[0] for r in rows).encode())",
            )
            (template / "server.py").write_text(product, encoding="utf-8")
            # A real template dependency is outside bead ownership and verifier roots.
            (template / "table_name.txt").write_text("items", encoding="utf-8")
            # Protect C20 at completion-to-delivery: dependencies formerly included
            # provider configuration; the old blanket dot guard also lost product dot-files.
            (template / ".codex").mkdir()
            (template / ".codex/config.toml").write_text(
                "# Template config must never be installed\n", encoding="utf-8"
            )
            (template / ".gitignore").write_text("*.local-product\n", encoding="utf-8")
            product = product.replace(
                "db.execute('select name from items')",
                (
                    "db.execute('select name from ' + "
                    "__import__('pathlib').Path('table_name.txt').read_text())"
                ),
            )
            importer = """import csv, sys
from contextlib import closing
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
with closing(sqlite3.connect(sys.argv[3])) as db, db:
    db.execute('create table if not exists items (name text)')
    db.executemany('insert into items values (?)', [(r['name'],) for r in accepted])
n = len(accepted)
print(f'imported {n}, skipped {len(rows)-n}')
"""
            verifier = (
                HTTP_DEADLINE
                + """import queue, sqlite3, subprocess, sys, threading
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
root = Path(__file__).resolve().parent
# T_HTTP runs independently of scenario setup. Import real input into a private DB;
# catch empty HTTP success, SQLite startup errors and optional-field row loss.
with TemporaryDirectory(dir=root) as scratch:
    csv = Path(scratch) / 'items.csv'
    db = Path(scratch) / 'items.sqlite3'
    csv.write_text('name,quantity,unit,expires\\nRice,2,kg,2030-01-10\\nBeans,0,,\\n')
    imported = subprocess.run([sys.executable, '-B', str(root / 'import_items.py'),
        str(csv), '--db', str(db)], capture_output=True, text=True, timeout=5)
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout == 'imported 2, skipped 0\\n', imported.stdout
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute(
            'select name from items order by rowid'
        ).fetchall() == [('Rice',), ('Beans',)]
    server = subprocess.Popen([sys.executable, '-B', str(root / 'server.py'), '0', str(db)],
        stdout=subprocess.PIPE, text=True)
    try:
        ports = queue.Queue()
        threading.Thread(target=lambda: ports.put(server.stdout.readline()), daemon=True).start()
        try:
            port = int(ports.get(timeout=5))
        except queue.Empty:
            raise TimeoutError('HTTP startup deadline: 5s')
        with urlopen(f'http://127.0.0.1:{port}/items', timeout=5) as response:
            assert response.status == 200, response.status
            body = response.read().decode()
            assert body == 'Rice,Beans', f'Expected imported rows over HTTP, got {body!r}'
            print(body)
    finally:
        server.kill()
        server.wait(timeout=5)
"""
            )
        else:
            vision["fields"].update(
                validation=["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
                requirementFiles=["tests"],
            )
            put_nodes(
                nodes=[
                    vision,
                    node("F", "feature", ["V1"]),
                    node("R", "requirement", ["F"]),
                    node("R_STATUS", "requirement", ["F"], text="Read readiness over HTTP"),
                    node(
                        "A",
                        "bead",
                        ["R_STATUS"],
                        ownedFiles=["status.py"],
                        dependsOn=[],
                        lessons=[],
                        proofModule="test_status.py",
                    ),
                    node(
                        "T_STATUS", "test", ["R_STATUS"], level="semantic", module="test_status.py"
                    ),
                    node(
                        "B",
                        "bead",
                        ["R"],
                        ownedFiles=["server.py"],
                        dependsOn=[],
                        lessons=[],
                        proofModule="test_http.py",
                    ),
                    node(
                        "T",
                        "test",
                        ["R", "R_STATUS"],
                        level="e2e",
                        module="test_http_concurrent.py",
                    ),
                    node(
                        "first-moment",
                        "acceptance",
                        ["F"],
                        nodeIds=["first-moment"],
                        given="Both HTTP services start from clean data",
                        steps=[],
                        expect={},
                        waveBoundaryIntegrationTestId="T",
                        waveBoundaryIntegrationModule="test_http_concurrent.py",
                    ),
                ]
            )
            template = workspace / "template"
            (template / "tests").mkdir(parents=True)
            for name, content in {
                "server.py": product.replace("b'shelf'", "b'stub'"),
                "status.py": product.replace("b'shelf'", "b'stub'"),
                "tests/test_status.py": proof.replace("'server.py'", "'status.py'").replace(
                    "b'shelf'", "b'ready'"
                ),
                "tests/test_http.py": proof,
                "tests/helpers.py": helper,
                "tests/test_http_concurrent.py": opaque,
            }.items():
                (template / name).write_text(content, encoding="utf-8")
            for phase in PHASES[PHASES.index(store.phase()) + 1 : PHASES.index("roundtable")]:
                advance_phase(to_phase=phase)
        if not semantic:
            # Report an actual applied wave refinement, not a fabricated event.
            # Prior consent journeys only used upsert_nodes and missed this writer.
            def outcome(wid, dependencies):
                return dict(
                    id=wid,
                    outcome="Both accepted HTTP services remain available",
                    demo="Run the HTTP checks",
                    requirementIds=["R", "R_STATUS"],
                    featureIds=["F"],
                    storyIds=[],
                    designIds=[],
                    riskIds=[],
                    exitScenarioIds=["first-moment"],
                    dependsOnWaves=dependencies,
                    incomingContractIds=[],
                    outgoingContractIds=[],
                )

            obligations = [
                dict(
                    id="O_" + key,
                    kind=kind,
                    sourceRefs=[dict(id=key, revision=store.spine().revision(key))],
                    waveIds=["first", "later"],
                    criterion="Preserve the accepted HTTP behavior",
                    requiredEvidence=[evidence],
                    requiredForFullVision=True,
                )
                for key, kind, evidence in (
                    ("R", "requirement", "source-test"),
                    ("R_STATUS", "requirement", "source-test"),
                    ("first-moment", "scenario", "clean-state-product"),
                    ("V1", "review", "independent-review"),
                )
            ]
            map_node = node(
                "WAVES",
                "decision",
                ["V1"],
                schema="dot-wave-map-v1",
                order=["first", "later"],
                waves=[outcome("first", []), outcome("later", ["first"])],
                contracts=[],
                obligations=obligations,
                globalReviewObligationId="O_V1",
            )
            detail_nodes = [
                node(
                    "WD1",
                    "design",
                    ["WAVES"],
                    schema="dot-wave-detail-v1",
                    waveId="first",
                    detail="detailed",
                    beadIds=["A", "B"],
                    sketches=[],
                    observedInterfaces=[],
                    verificationObligationIds=[],
                ),
                node(
                    "WD2",
                    "design",
                    ["WAVES"],
                    schema="dot-wave-detail-v1",
                    waveId="later",
                    detail="sketch",
                    beadIds=[],
                    sketches=[
                        dict(
                            id="SK_HTTP",
                            title="Refine HTTP adapter ownership after observing the service",
                            ownershipArea="HTTP adapter",
                            requirementIds=["R"],
                            expectedContractIds=[],
                            risks=[],
                            pendingProducerIds=["B"],
                            detailAfterWaveId="first",
                        )
                    ],
                    observedInterfaces=[],
                    verificationObligationIds=[],
                ),
            ]
            save_wave_map(
                map_node=map_node,
                detail_nodes=detail_nodes,
                expected_spine_revision=store._revision(),
            )
        choice = json.loads(
            (ROOT / "tests/fixtures/efficiency/jsonkeys-reviewers.json").read_text("utf-8")
        )["choice"]
        if semantic:
            # R7 starts with one build; review will re-partition its tests into C.
            bead = store.spine().node("B")
            bead["fields"]["dependsOn"] = []
            put_node(node=bead)
            remove_node(id="C")
            # Existing incomplete implementation gives the later test worker a
            # real assertion RED, rather than a missing-program startup error.
            (template / "import_items.py").write_text(
                importer.replace(
                    "if valid(row)", "if valid(row) and row['unit'] and row['expires']"
                ),
                encoding="utf-8",
            )
        choice["fields"].update(catalogId=None, catalogDigest=None)
        for reviewer in choice["fields"]["reviewers"]:
            reviewer["choiceId"] = None
        choice["fields"]["recommendation"]["choiceIds"] = []
        save_reviewers(node=choice, expected_spine_revision=store._revision())
        manifest = compile_current(include_detail=True)
        options = dict(
            expected_revision=manifest["planRevision"],
            max_parallel=1 if semantic else 2,
            timeout=180 if semantic else 15,
            workers=[
                dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                for b in (("B",) if semantic else ("A", "B"))
            ],
        )
        with pytest.MonkeyPatch.context() as patch:
            # These helpers also run before the stdio journey installs its absolute
            # import path. Relative PYTHONPATH breaks runner children in temp cwd.
            patch.setenv("PYTHONPATH", os.pathsep.join([str(ROOT / "src"), str(ROOT)]))
            for owner in (roundtable, review_provider):
                patch.setattr(owner, "_executable", lambda *_: sys.executable)
            review_history_ids = set()
            provider_launches = []

            def positive_review(launch, *_, **__):
                provider_launches.append(launch)
                if "\n## Bound Judgment\n" not in launch.stdin_text:
                    return protocol(
                        launch.cli,
                        text=json.dumps(
                            dict(ready=True, canVerify=True, blockers=[], questions=[])
                        ),
                    )
                request = json.loads(
                    launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split(
                        "\nReturn ONLY JSON", 1
                    )[0]
                )
                history = request["sourceRefs"][0]["payload"]["facts"]["packet"]
                assert all(key in history for key in review_history_ids)
                reviewer = request["sourceRefs"][0]["payload"]["facts"]["reviewer"]
                return protocol(
                    launch.cli,
                    model=reviewer["model"],
                    effort=reviewer["effort"],
                    text=scripted_review_reply(
                        launch,
                        [],
                        verdict="positive",
                        rationale=(
                            "The planned HTTP import, persistence and readback "
                            "cover the accepted local dream"
                        ),
                    ),
                )

            for owner in (roundtable, review_provider):
                patch.setattr(owner, "_run", positive_review)
            # The former call ceiling hid a separate readiness launch owner with no B check.
            # Ordinary review tests never exercised this public worker-packet consumer.
            selected_reviewers = [
                {
                    k: r[k] if k != "effort" else r[k]["value"]
                    for k in ("name", "provider", "model", "effort")
                }
                for r in choice["fields"]["reviewers"]
            ]
            yes(
                prepare_words(
                    gate="invocations",
                    role_tuples=[dict(role="reviewer", **r) for r in selected_reviewers],
                )
            )
            refused = observe(lambda: review_readiness(reviewers=selected_reviewers, beads=["B"]))
            assert refused["isError"] and "plan-b-consent-required" in str(refused)
            yes(
                prepare_words(
                    gate="plan-execution", execution=options, reviewer_node_id=choice["id"]
                )
            )
            if semantic:
                # Recording observed catalog metadata for the same exact reviewers
                # must not broaden consent or introduce another B decision.
                from tests.python.core.plan_store_support import mock_catalog

                catalog = mock_catalog()
                catalog["providers"][0]["choices"][0]["effort"]["value"] = "medium"
                catalog = save_catalog(catalog=catalog, expected_spine_revision=store._revision())[
                    "catalog"
                ]
                choice["fields"].update(catalogId=catalog["id"], catalogDigest=catalog["digest"])
                for reviewer in choice["fields"]["reviewers"]:
                    reviewer["choiceId"] = catalog["providers"][0]["choices"][0]["choiceId"]
                save_reviewers(node=choice, expected_spine_revision=store._revision())
                scope_how(
                    "Catalog provenance now identifies the same reviewers; no "
                    "product obligation changes"
                )
                manifest = compile_current(include_detail=True)
                options["expected_revision"] = manifest["planRevision"]
            if not semantic:
                # Mixed consent must refuse the entire selection before spending a start.
                # Earlier refusal tests used all-unapproved rosters and missed orphaned
                # required-slot attempts after an approved first reviewer ran.
                adopt_policy(
                    expectedManifestRevision=manifest["planRevision"],
                    slots=[
                        dict(name=r["name"], provider=r["provider"], required=True)
                        for r in selected_reviewers
                    ],
                )
                mixed = [
                    selected_reviewers[0],
                    dict(selected_reviewers[1], name="unapproved-observer"),
                ]

                def refuse_mixed(operation, **arguments):
                    before = len(provider_launches)
                    reply = observe(lambda: operation(reviewers=mixed, **arguments))
                    assert (
                        reply["isError"] and reply["refusalCode"] == "invocation-tuple-not-granted"
                    ), reply
                    text = "\n".join(c.get("text", "") for c in reply["content"])
                    assert (
                        "exact role/provider/model/effort" in text
                        and "draft choice grants none" in text
                    )
                    return len(provider_launches) - before

                mixed_launches = {"readiness": refuse_mixed(review_readiness, beads=["B"])}
            readiness = review_readiness(reviewers=selected_reviewers, beads=["B"])
            assert readiness["invalid"] == 0
            if not semantic:
                # I2/I6: contradictory ready flags cannot erase a real pending finding.
                # The old happy packet had no blocker to carry through the finding owner.
                def pending_readiness(launch, *_, **__):
                    return protocol(launch.cli, text=json.dumps(dict(
                        ready=True, canVerify=True, questions=[], blockers=[dict(
                            kind="missing-context", detail="Readback ownership needs judgment", nodeIds=["B"],
                        )],
                    )))

                with pytest.MonkeyPatch.context() as pending:
                    pending.setattr(roundtable, "_run", pending_readiness)
                    held_readiness = review_readiness(reviewers=selected_reviewers, beads=["B"])
                assert held_readiness["status"] == "not-yet", held_readiness
                members = [key for row in held_readiness["reviews"] for key in row["findingIds"]]
                judged = resolve_findings(findings=[dict(
                    id=key, status="resolved", resolution="The current packet assigns readback to B",
                ) for key in members])
                scripted_judgment(store, judged["judgment"],
                                  dict(memberDispositions={key: "resolved" for key in members}))
                readiness = review_readiness(reviewers=selected_reviewers, beads=["B"])
                assert readiness["status"] == "pass", readiness
                interrupted = admit_audit(mode="prospective")
                server.audits.close(store)  # Supported owner cleanup before dispatch.
                released = audit_status(id=interrupted["attemptId"])
                assert released["state"] == "abandoned" and not released["proofCreditGranted"]
                audit = admit_audit(mode="prospective")
                assert audit["attemptId"] != interrupted["attemptId"]
                server.audits.dispatch_after_flush(store)
                server.audits.thread.join(timeout=10)
                server.audits.drain(store)
                audited = audit_status(id=audit["attemptId"])
                result = audited["result"]
                assert result["ok"] and audited["proofCreditGranted"], audited
                assert result["evidenceTier"] == "audit: pending judgment", result
                assert result["judgmentStatus"] == "pending-judgment"
                assert not result["collection"] and not audited["didRunValidation"]
                # Source patterns cannot decide meaningfulness or reject an opaque helper.
                assert not result["findings"], result
            advance_phase(to_phase="roundtable")
            assert store.status()["pending_gate"] != "passing-audit-required"
            if not semantic:
                mixed_launches["roundtable"] = refuse_mixed(review_plan, timeout=5)
                exists = store.connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='review_attempts'"
                ).fetchone()
                attempts = (
                    [dict(row) for row in store.connection.execute("SELECT * FROM review_attempts")]
                    if exists
                    else []
                )
                assert mixed_launches == {"readiness": 0, "roundtable": 0} and not attempts, (
                    mixed_launches,
                    attempts,
                )
            if semantic:
                # Protect complete source members and invocation/revision-bound authority.
                # Earlier coverage merged opposing findings and never retried an unchanged packet.
                exhausted_calls, original_packets = {}, {}

                def malformed_review(launch, *_, **__):
                    request = json.loads(
                        launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split(
                            "\nReturn ONLY JSON", 1
                        )[0]
                    )
                    key = request["id"]
                    facts = request["sourceRefs"][0]["payload"]["facts"]
                    original_packets[facts["reviewer"]["name"]] = facts["packetSha256"]
                    exhausted_calls[key] = exhausted_calls.get(key, 0) + 1
                    malformed = request["responseExample"]
                    malformed["meaning"]["depth"] = ""
                    return protocol(
                        launch.cli,
                        text=json.dumps(malformed),
                        model=facts["reviewer"]["model"],
                        effort=facts["reviewer"]["effort"],
                    )

                with pytest.MonkeyPatch.context() as bad:
                    for owner in (roundtable, review_provider):
                        bad.setattr(owner, "_run", malformed_review)
                    exhausted = review_plan(timeout=5)
                assert sorted(exhausted_calls.values()) == [3, 3]
                assert all(
                    "meaning.depth" in read_judgment(judgment_id=key)["error"]
                    for key in exhausted_calls
                )
                assert exhausted["findings"] == 0 and not exhausted["successfulReviews"]
                exhausted_ids = set(exhausted_calls)
                # Terminal exact-fix exhaustion must teach fresh invocation, not
                # advertise a submission the owner necessarily rejects. Older flows
                # inspected the error count but never consumed its next-step card.
                terminal = read_status(full=True)
                requests = {r["id"]: r for r in terminal["pendingSemanticJudgments"]}
                assert all(
                    not requests[key]["submitReady"]
                    and requests[key]["action"] == "fresh-invocation"
                    for key in exhausted_ids
                )
                conflict = dict(
                    node_id="R2",
                    severity="major",
                    finding="HTTP readback evidence needs judgment",
                    suggestion="Prove persistence of both imported rows",
                    classification="defect-in-plan",
                    scope_reason="Required readback could be missing",
                )
                opposing = dict(
                    conflict,
                    suggestion="Explain whether extra readback coverage exceeds the brief",
                    classification="beyond-brief",
                    scope_reason="Additional coverage might exceed the brief",
                )
                heading_note = json.loads(
                    (ROOT / "tests/fixtures/efficiency/run26.json").read_text("utf-8")
                )["headingFinding"]

                def conflicting_review(launch, *_, **__):
                    request = json.loads(
                        launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split(
                            "\nReturn ONLY JSON", 1
                        )[0]
                    )
                    name = request["sourceRefs"][0]["payload"]["facts"]["reviewer"]["name"]
                    assert request["id"] not in exhausted_ids
                    assert (
                        request["sourceRefs"][0]["payload"]["facts"]["packetSha256"]
                        == original_packets[name]
                    )
                    concrete = name == choice["fields"]["reviewers"][0]["name"]
                    findings = (
                        [conflict, opposing, heading_note]
                        if concrete
                        else [
                            dict(
                                conflict,
                                finding="Evidence access unavailable",
                                suggestion="Obtain readback observations",
                            )
                        ]
                    )
                    return protocol(
                        launch.cli,
                        model=request["sourceRefs"][0]["payload"]["facts"]["reviewer"]["model"],
                        effort=request["sourceRefs"][0]["payload"]["facts"]["reviewer"]["effort"],
                        text=scripted_review_reply(
                            launch,
                            findings,
                            verdict="negative" if concrete else "inconclusive",
                            rationale=(
                                "Readback is unproven until its real observations are assessed"
                            )
                            if concrete
                            else "Unavailable observations cannot establish a concrete gap",
                            gaps=["HTTP readback evidence needs judgment"] if concrete else [],
                        ),
                    )

                with pytest.MonkeyPatch.context() as bad:
                    for owner in (roundtable, review_provider):
                        bad.setattr(owner, "_run", conflicting_review)
                    rejected = review_plan(timeout=5)
                assert not rejected["successfulReviews"]
                # Only invocation metadata changes on the otherwise identical initial dossier.
                assert all(
                    r["shardDetails"][0]["reviewInvocationId"]
                    != exhausted["reviewers"][i]["shardDetails"][0]["reviewInvocationId"]
                    for i, r in enumerate(rejected["reviewers"])
                )
                held = observe(lambda: advance_phase(to_phase="publish"))
                assert held["isError"]
                equivalence = rejected["clusters"]["judgment"]
                members = equivalence["sourceRefs"][0]["payload"]["facts"]["members"]
                assert len([row for row in members if row.get("judgmentGap")]) == 1
                members = [
                    row
                    for row in members
                    if row["finding"] == conflict["finding"] and not row.get("judgmentGap")
                ]
                assert len(members) == 2 and {row["suggestion"] for row in members} == {
                    conflict["suggestion"],
                    opposing["suggestion"],
                }
                assert {row["scope_reason"] for row in members} == {
                    conflict["scope_reason"],
                    opposing["scope_reason"],
                }
                assert equivalence["status"] == "pending-judgment"
                scripted_judgment(
                    store,
                    equivalence,
                    dict(groups=[[row["id"] for row in members]]),
                    rationale=(
                        "The two independently retained members concern readback; "
                        "neither rationale is discarded"
                    ),
                )
                first = [
                    dict(
                        id=members[0]["id"],
                        status="resolved",
                        resolution="Readback verifier covers the first member",
                    )
                ]
                pending = resolve_findings(findings=first)
                first_closure_id = pending["judgment"]["id"]
                scripted_judgment(
                    store,
                    pending["judgment"],
                    dict(memberDispositions={members[0]["id"]: "resolved"}),
                )
                resolve_findings(findings=first)
                remainder = review_detail(id=rejected["roundId"])["findingMembers"]
                assert any(
                    row["finding"] == "[TRACE] " + heading_note["finding"]
                    and row["state"] == "open"
                    for row in remainder
                )
                assert {
                    row["id"]
                    for row in remainder
                    if row["id"] in {m["id"] for m in members} and row["status"] == "open"
                } == {members[1]["id"]}
                projected = roundtable.findings(store)
                assert any(
                    row["id"] == members[1]["id"] and row["status"] == "open" for row in projected
                )
                # Every concrete negative gap survives independently of presentation;
                # an inconclusive major item remains pending, never a false blocker.
                gap_rows = [
                    r for r in roundtable.findings(store, expand=True) if r.get("judgmentGap")
                ]
                assert len(gap_rows) == 1 and all(r["state"] == "open" for r in gap_rows)
                review_history_ids.update(
                    row["id"] for row in roundtable.findings(store, expand=True)
                )
                assert all(r["id"] in {row["id"] for row in remainder} for r in gap_rows)
                assert members[1]["id"] in {
                    r["id"] for r in store.finding_state()["dispositionPrerequisite"]["members"]
                }
                proposals = [
                    dict(
                        id=row["id"],
                        status="resolved",
                        resolution=(
                            "The real importer/SQLite/HTTP verifier supplies readback evidence"
                        ),
                    )
                    for row in roundtable.findings(store, expand=True)
                    if row["state"] != "closed" or row["id"] == members[0]["id"]
                ]
                pending = resolve_findings(findings=proposals)
                assert (
                    pending["judgment"]["status"] == "pending-judgment"
                    and pending["judgment"]["owner"] is None
                )
                scripted_judgment(
                    store,
                    pending["judgment"],
                    dict(memberDispositions={row["id"]: "resolved" for row in proposals}),
                )
                assert resolve_findings(findings=proposals)["dispositions"] == len(proposals)
                assert read_judgment(judgment_id=pending["judgment"]["id"])["positiveCredit"]
                retained = review_detail(id=rejected["roundId"])["findingMembers"]
                assert {first_closure_id, pending["judgment"]["id"]} <= set(
                    next(r for r in retained if r["id"] == members[0]["id"])["judgmentIds"]
                )
                uncertain = [
                    r
                    for r in roundtable.findings(store, expand=True)
                    if r["finding"] == "Evidence access unavailable"
                ]
                assert len(uncertain) == 1 and uncertain[0]["state"] == "closed"
                assert store.finding_state()["dispositionPrerequisite"]["complete"]
                # ShelfLife R6: review repairs changed the revision and demanded B
                # twice. Frozen-plan journeys never consumed a repaired test/command.
                preliminary = store.spine().node("T_HTTP")
                preliminary["text"] += (
                    " Assert imported rows survive SQLite and HTTP server restart."
                )
                preliminary["fields"]["execution"]["argv"] = []
                put_node(node=preliminary)
                # R7: one approved build becomes test-only contracts -> build,
                # preserving scope, allowed tuple, concurrency and deadline.
                contracts["fields"].update(ownedFiles=["verify_http.py"], dependsOn=[])
                contracts["parents"] = ["T_HTTP"]
                bead["fields"].update(
                    ownedFiles=["server.py", "import_items.py", "verify_http.py"], dependsOn=["C"]
                )
                put_nodes(nodes=[contracts, bead])
                options["workers"] = [dict(options["workers"][0], bead=b) for b in ("C", "B")]
                # Repair the scripted worker's real verifier body too: read the
                # same persisted rows after stopping and restarting the server.
                prefix, lifecycle = verifier.split("    server = subprocess.Popen", 1)
                lifecycle = ("    server = subprocess.Popen" + lifecycle).replace(
                    "            print(body)\n", ""
                )
                verifier = (
                    prefix
                    + "    for restart in range(2):\n"
                    + "\n".join("    " + line for line in lifecycle.splitlines())
                    + "\n    print(body)\n"
                )
                (template / "verify_http.py").write_text(
                    verifier + "\n# Published verifier baseline\n", encoding="utf-8"
                )
                scope_how(
                    "Tests-first contracts/build ownership and restart-verifier "
                    "repairs implement the same accepted local product"
                )
                options["expected_revision"] = compile_current(include_detail=True)["planRevision"]
            if not semantic:
                reviewer = choice["fields"]["reviewers"][0]
                # Protect dossier replacement: accepted A obligations survive B's
                # overflow; publication must await their disposition, never retired B.
                # Prior independent-shard checks never replaced a partly judged dossier.
                original_shards = roundtable.packet_shards
                replaced, abandoned = {}, []
                reshard_gap = "Resharding must retain the imported-row evidence gap"

                def split_dossier(owner, provider, *, token_budget=None, **kwargs):
                    if token_budget is not None:
                        return original_shards(owner, provider, token_budget=token_budget, **kwargs)
                    full_packet = original_shards(owner, provider, **kwargs)[0]
                    focused_packet = original_shards(
                        owner, provider, token_budget=full_packet["estimatedTokens"] - 1, **kwargs
                    )[0]
                    return [full_packet, focused_packet]

                def reshard_review(launch, *_, **__):
                    request = json.loads(
                        launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split(
                            "\nReturn ONLY JSON", 1
                        )[0]
                    )
                    facts = request["sourceRefs"][0]["payload"]["facts"]
                    if facts["reviewer"]["name"] == reviewer["name"]:
                        if not replaced:
                            replaced.update(
                                invocation=facts["invocationId"], judgment=request["id"]
                            )
                            return protocol(
                                launch.cli,
                                model=facts["reviewer"]["model"],
                                effort=facts["reviewer"]["effort"],
                                text=scripted_review_reply(
                                    launch,
                                    [
                                        dict(
                                            node_id="plan",
                                            severity="major",
                                            finding=(
                                                "Retain the readback finding across "
                                                "dossier replacement"
                                            ),
                                            suggestion="Retain the HTTP observations",
                                            classification="defect-in-plan",
                                            scope_reason="Required readback remains unproven",
                                        )
                                    ],
                                    verdict="negative",
                                    rationale="A's concrete readback gap needs disposition",
                                    gaps=[reshard_gap],
                                ),
                            )
                        if facts["invocationId"] == replaced["invocation"]:
                            abandoned.append(request["id"])
                            return (1, "", "prompt is too long: context window exceeded")
                        assert reshard_gap in launch.stdin_text
                    return protocol(
                        launch.cli,
                        model=facts["reviewer"]["model"],
                        effort=facts["reviewer"]["effort"],
                        text=scripted_review_reply(
                            launch,
                            [],
                            verdict="positive",
                            rationale=(
                                "Replacement dossier reviewed; retained obligations "
                                "require their own disposition"
                            ),
                        ),
                    )

                with pytest.MonkeyPatch.context() as reshard:
                    for owner in (roundtable, review_packets):
                        reshard.setattr(owner, "packet_shards", split_dossier)
                    for owner in (roundtable, review_provider):
                        reshard.setattr(owner, "_run", reshard_review)
                    replacement = review_plan(timeout=5)
                assert len(replacement["successfulReviews"]) == 2, replacement
                carried = [
                    r
                    for r in roundtable.findings(store, expand=True)
                    if r.get("judgmentId") == replaced["judgment"]
                ]
                assert {r["finding"] for r in carried} == {
                    reshard_gap,
                    "Retain the readback finding across dossier replacement",
                }, carried
                assert all(r["state"] == "open" for r in carried)
                assert abandoned and not set(abandoned) & {
                    r["id"] for r in read_status()["pendingSemanticJudgments"]
                }
                assert all(not read_judgment(judgment_id=key)["current"] for key in abandoned)
                if replacement["convergence"]:
                    scripted_judgment(store, replacement["convergence"], dict(action="ready"))
                held = observe(lambda: advance_phase(to_phase="publish"))
                assert held["isError"] and held.get("refusalCode") == "pending-judgment"
                carried_proposals = [
                    dict(
                        id=r["id"],
                        status="resolved",
                        resolution="Bound readback observations address A's retained obligation",
                    )
                    for r in carried
                ]
                closure = resolve_findings(findings=carried_proposals)
                scripted_judgment(
                    store,
                    closure["judgment"],
                    dict(memberDispositions={r["id"]: "resolved" for r in carried}),
                )
                resolve_findings(findings=carried_proposals)
                assert store.review_prerequisite(store.manifest_revision())["complete"]
                assert store.finding_state()["dispositionPrerequisite"]["complete"]
                review_history_ids.update(r["id"] for r in carried)
                reviewed = replacement
            else:
                reviewed = review_plan(timeout=5)
            assert len(reviewed["successfulReviews"]) == 2, reviewed
            assert all(
                read_judgment(judgment_id=part["judgmentId"])["fixesIssued"] == 0
                for row in reviewed["reviewers"]
                for part in row["shardDetails"]
            )
            if semantic:
                visible_repair = observe(lambda: read_status())
                assert {"B", "C", "T_HTTP"} <= set(
                    visible_repair["structuredContent"]["continuedPlanChanges"]
                )
                assert {"B", "C", "T_HTTP"} <= set(
                    visible_repair["structuredContent"]["continuedPlanChanges"]
                )
                assert (
                    len(
                        [
                            r
                            for r in store.connection.execute(
                                "SELECT payload_json FROM events WHERE "
                                "kind='confirmation-requested'"
                            )
                            if json.loads(r[0])["gate"] == "plan-execution"
                        ]
                    )
                    == 1
                )
            # Preserve recorded scope after deleting quota totals: raw SQLite integers
            # previously rendered as unknown; review results alone missed this status consumer.
            from tests.python.core.test_golden_path import review_replacement_golden

            reviewed = review_replacement_golden(store, selected_reviewers[0])
            if not semantic and reviewed.get("convergence"):
                scripted_judgment(store, reviewed["convergence"], dict(action="ready"))
            visible = observe(lambda: read_status(full=True))
            assert not visible["isError"]
            recorded_reviews = store.successful_reviews(store.manifest_revision())
            assert all(bool(row["didRunProvider"]) for row in recorded_reviews), recorded_reviews
            if not semantic:
                policy = visible["structuredContent"]["reviewPolicy"]
                assert policy["eligible"] and all(
                    not row["incompleteAttempt"] for row in policy["slots"]
                ), policy
            if semantic:
                scripted_judgment(store, reviewed["convergence"], dict(action="ready"))
                # DV10: stale closures + fresh positive reviews used to publish.
                # Earlier stale-binding checks never attempted that publication.
                retained = [
                    r
                    for r in roundtable.findings(store, expand=True)
                    if r["id"] in review_history_ids
                ]
                assert retained and all(r["state"] == "pending" for r in retained)
                held = observe(lambda: advance_phase(to_phase="publish"))
                assert held["isError"] and held.get("refusalCode") == "pending-judgment", held
                assert "typed question" in str(held) and "obligation ids" in str(held)
                for row in retained:
                    assert row["id"] in str(held)
                status = read_status(full=True)
                assert status["phase"] == "roundtable"
                assert set(
                    r["id"] for r in store.finding_state()["dispositionPrerequisite"]["members"]
                ) == {r["id"] for r in retained}
                pending = resolve_findings(findings=proposals)
                scripted_judgment(
                    store,
                    pending["judgment"],
                    dict(memberDispositions={row["id"]: "resolved" for row in proposals}),
                )
                assert resolve_findings(findings=proposals)["dispositions"] == len(proposals)
                rejected_ids = {
                    part["judgmentId"]
                    for row in rejected["reviewers"]
                    for part in row["shardDetails"]
                }
                assert not rejected_ids & {
                    part["judgmentId"]
                    for row in reviewed["reviewers"]
                    for part in row["shardDetails"]
                }
                convergence = reviewed["convergence"]
                # Canonical full + compact shard evidence deliberately shares all
                # spine refs. Earlier coverage used only one shard per reviewer,
                # so purpose/whole-spine dedup hid the other required request.
                reviewer = choice["fields"]["reviewers"][0]
                full = roundtable.packet_shards(store, reviewer["provider"])[0]
                focused = roundtable.packet_shards(
                    store, reviewer["provider"], token_budget=full["estimatedTokens"] - 1
                )[0]
                assert full["hash"] != focused["hash"]
                shards = [full, focused]
                roundtable.bind_review_dossiers(store, {reviewer["provider"]: shards}, [reviewer])
                shard_requests = [
                    store.review_judgment(reviewer, shard, prepare=False) for shard in shards
                ]
                meaning = dict(
                    findings=[],
                    depth=(
                        "Assess both bound evidence shards independently; observations unavailable"
                    ),
                )
                for request in shard_requests:
                    scripted_judgment(store, request, meaning, verdict="inconclusive")
                visible = {r["id"]: r for r in read_status()["pendingSemanticJudgments"]}
                # Explicit replacement retires exhausted requests; they cannot
                # take continuation from the distinct live shard identities.
                assert not exhausted_ids & visible.keys()
                assert all(visible[r["id"]]["submitReady"] for r in shard_requests)
                assert (
                    visible[shard_requests[0]["id"]]["requestKey"]
                    != visible[shard_requests[1]["id"]]["requestKey"]
                )
                meaning["depth"] = (
                    "Completed independent assessment of the bound import and HTTP evidence shard"
                )
                scripted_judgment(store, shard_requests[0], meaning)
                partial = read_status(full=True)
                assert shard_requests[1]["id"] in {
                    r["id"] for r in partial["pendingSemanticJudgments"]
                }
                assert not store.review_prerequisite(store.manifest_revision())["complete"]
                held = observe(lambda: advance_phase(to_phase="publish"))
                assert held["isError"] and "plan-publication-evidence-required" in str(held)
                scripted_judgment(store, shard_requests[1], meaning)
                assert store.review_prerequisite(store.manifest_revision())["complete"]
                amended = store.spine().node("R2")
                amended["text"] += " HTTP readback must preserve both imported rows."
                put_node(node=amended)
                compile_current(include_detail=True)
                # Same-ID scope edits previously bypassed B without any meaning
                # check; frozen-ID consent tests never changed requirement content.
                before_launches = len(provider_launches)
                held = observe(lambda: review_readiness(reviewers=selected_reviewers, beads=["B"]))
                assert held["isError"] and held["refusalCode"] == "pending-judgment", held
                scope_judge = held["structuredContent"]["scopeJudgment"]
                assert scope_judge["current"] and scope_judge["question"] == "scope-intent"
                assert "scope.nodes.R2.text" in {
                    d["field"] for d in held["structuredContent"]["diff"]
                }
                scripted_judgment(store, scope_judge, {}, verdict="inconclusive")
                still_waiting = observe(
                    lambda: review_readiness(reviewers=selected_reviewers, beads=["B"])
                )
                assert still_waiting["refusalCode"] == "pending-judgment"
                assert (
                    still_waiting["structuredContent"]["scopeJudgment"]["id"] == scope_judge["id"]
                )
                assert len(provider_launches) == before_launches
                scripted_judgment(
                    store,
                    scope_judge,
                    dict(impact="HOW"),
                    rationale=(
                        "Both imported rows were already required by the accepted "
                        "readback scenario; this wording repairs the trace"
                    ),
                )
                assert "R2" in read_status()["continuedPlanChanges"]
                assert (
                    len(
                        [
                            r
                            for r in store.connection.execute(
                                "SELECT payload_json FROM events WHERE "
                                "kind='confirmation-requested'"
                            )
                            if json.loads(r[0])["gate"] == "plan-execution"
                        ]
                    )
                    == 1
                )
                manifest = compile_current(include_detail=True)
                options["expected_revision"] = manifest["planRevision"]
                assert not store.successful_reviews(manifest["planRevision"])
                assert not read_judgment(judgment_id=convergence["id"])["current"]
                pending = resolve_findings(findings=proposals)
                scripted_judgment(
                    store,
                    pending["judgment"],
                    dict(memberDispositions={row["id"]: "resolved" for row in proposals}),
                )
                closed = resolve_findings(findings=proposals)
                assert len(closed["reuseJudgments"]) == 2
                # Requests are distinct by reviewer/member, never hidden by purpose.
                requests = closed["reuseJudgments"]
                pending_ids = {row["id"] for row in read_status()["pendingSemanticJudgments"]}
                assert {r["id"] for r in requests} <= pending_ids
                first_reuse = scripted_judgment(store, requests[0], dict(reuse=True))
                scripted_judgment(store, first_reuse["convergence"], dict(action="ready"))
                partial = read_status(full=True)
                assert requests[1]["id"] in {r["id"] for r in partial["pendingSemanticJudgments"]}
                assert partial["pendingSemanticJudgments"]
                assert store.review_prerequisite(store.manifest_revision())["complete"] is False
                result = scripted_judgment(
                    store,
                    requests[1],
                    dict(reuse=True),
                    rationale=(
                        "The actual delta clarifies existing readback; the real "
                        "verifier covers both rows"
                    ),
                )
                current_convergence = result["convergence"]
                assert (
                    current_convergence["current"]
                    and current_convergence["id"] != convergence["id"]
                )
                assert len(store.successful_reviews(manifest["planRevision"])) == 2
                pending_ids = {row["id"] for row in read_status()["pendingSemanticJudgments"]}
                assert current_convergence["id"] in pending_ids
                assert read_status(full=True)["pendingSemanticJudgments"]
                scripted_judgment(store, current_convergence, dict(action="ready"))
                assert read_status()["continuedPlanChanges"]
            advance_phase(to_phase="publish")
            if semantic:
                publish_plan()
            else:
                publish_plan(proof_level="prospective", reason="No reference implementation")
            advance_phase(to_phase="execute")
            published = store.published_manifest(
                store.status()["publication"]["published_revision"]
            )
            (workspace / "sample.json").write_text(json.dumps(published), encoding="utf-8")
            if not semantic:
                options["expected_revision"] = store.status()["published_revision"]

            if semantic:
                # C must consume the admitted job's real root delivery. Standalone
                # dispatch proves scenarios, but never supplies that delivery.
                contract_sources = {"verify_http.py": verifier}
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
                            str(ROOT / "tests/python/core/test_golden_path.py"),
                            "controller",
                            str(workspace),
                            "shelflife",
                            argv[argv.index("--job-id") + 1],
                            argv[argv.index("--segment") + 1],
                        ]
                    return actual_popen(argv, **kwargs)

                with pytest.MonkeyPatch.context() as process_patch:
                    process_patch.setattr(subprocess, "Popen", launch)
                    job = start_execution(**options)
                    deadline = time.time() + options["timeout"]
                    while job["status"] not in {"completed", "failed"} and time.time() < deadline:
                        job = execution_status(job_id=job["jobId"], wait_seconds=1)
                    # AUDIT1: a live writer pauses with a partial terminal envelope.
                    # Immediate replies never exposed silence truncating valid evidence.
                    expired = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
                    contracts = expired["beads"]["C"]
                    assert contracts["status"] == "reused" and contracts["reused"], job
                    original_contracts = json.loads(
                        Path(contracts["integrated"]["receiptPath"]).read_text(encoding="utf-8")
                    )["semantic"]
                    assert (
                        original_contracts["phase"] == "contracts"
                        and original_contracts["state"] == "RED"
                        and original_contracts["observedState"] == "FAIL"
                        and original_contracts["admitted"]
                        and original_contracts["evidenceBinding"]
                        == expired["semanticContracts"]["evidenceBinding"]
                    ), original_contracts
                    job_root = jobs._directory(workspace, job["jobId"])
                    contracts_segments = [
                        segment
                        for segment in (job_root / "segments").iterdir()
                        if json.loads((segment / "request.json").read_text(encoding="utf-8"))[
                            "resumeRun"
                        ]
                        is None
                    ]
                    assert len(contracts_segments) == 1, contracts_segments
                    contracts_segment = contracts_segments[0]
                    contracts_terminal = json.loads(
                        (contracts_segment / "terminal.json").read_text(encoding="utf-8")
                    )
                    assert contracts_terminal["status"] == "pending-judgment"
                    assert contracts_terminal["runId"] == expired["runId"]
                    assert jobs.verified_segment_exit(
                        jobs._request(job_root),
                        contracts_segment.name,
                        contracts_terminal,
                        json.loads(
                            (contracts_segment / "quiescence.json").read_text(encoding="utf-8")
                        ),
                    )
                    live = json.loads((workspace / ".codex-harness/live-provider.json").read_text())
                    assert live["outputSeconds"] >= 3 and live["cpuSeconds"] >= 3
                    assert live["flushSeconds"] >= 125, live
                    contract_attempt = Path(expired["beads"]["C"]["evidenceDir"]) / "attempt"
                    assert "thinking" in (contract_attempt / "stderr.txt").read_text()
                    complete = json.loads((contract_attempt / "result.json").read_text())
                    assert (
                        complete["permissionGuard"]["telemetryComplete"]
                        and complete["exitCode"] == 0
                    )
                    assert (
                        (contract_attempt / "events.jsonl")
                        .read_text()
                        .endswith('{"type": "turn.completed"}\n')
                    )
                    truncated_attempt = Path(expired["beads"]["B"]["evidenceDir"]) / "attempt"
                    stopped = json.loads((truncated_attempt / "result.json").read_text())
                    assert (
                        job["status"] == "failed"
                        and stopped["exitCode"] == 0
                        and stopped["failure"] is None
                    )
                    assert (
                        stopped["outputComplete"]
                        and stopped["statusBeforePermissionHold"] == "exited"
                    )
                    assert (
                        stopped["cleanup"]["quiescenceVerified"]
                        and stopped["cleanup"]["activeProcessesAfterTeardown"] == 0
                    )
                    guard = stopped["permissionGuard"]
                    assert guard["requiresAuditHold"] and guard["auditReason"] == "malformed"
                    assert (
                        guard["disposition"] == "unknown"
                        and guard["didObservePermissionDenial"] is None
                    )
                    assert guard["observationError"] == "Invalid or truncated provider event"
                    assert expired["beads"]["B"]["status"] == STATUS_AUDIT_HELD
                    assert "held: denial audit inconclusive" in job["terminal"]["dispatchStatus"]
                    assert not expired.get("delivery") and not expired.get(
                        "didRunIntegratedValidation"
                    )
                    quarantined_id = job["jobId"]
                    released = resume_observed_hold(
                        quarantined_id,
                        (
                            "Supply complete terminal telemetry from a fresh provider "
                            "within the same accepted HTTP scope"
                        ),
                    )
                    job = start_execution(**options)
                    assert job["jobId"] != quarantined_id and released["quarantine"]
                    deadline = time.time() + options["timeout"]
                    while job["status"] not in {"completed", "failed"} and time.time() < deadline:
                        job = execution_status(job_id=job["jobId"], wait_seconds=1)
                    # r7: an omitted declared test crashed fallback RED in git restore.
                    # Earlier journeys always supplied the whole worker bundle.
                    incomplete = "incomplete: declared verify_http.py missing from candidate"
                    failed = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
                    assert (
                        job["status"] == "failed" and failed["beads"]["B"]["status"] == incomplete
                    ), job
                    assert failed["beads"]["B"]["integrated"] is None
                    assert (
                        failed["semanticContracts"]["state"] == "RED"
                        and "semanticValidation" not in failed
                    )
                    assert "error" not in job["terminal"]
                    recovered = recover(
                        mode="resolve",
                        job_id=job["jobId"],
                        choice="readmit",
                        reason="Supply the exact declared worker input",
                    )
                    assert recovered["choice"] == "readmit" and recovered["proof"][
                        "faultOutcomes"
                    ] == {"B": incomplete}
                    assert recovered["proof"]["controllerAndChildren"]["quiescenceVerified"]
                    job = start_execution(**options)
                    assert job["jobId"] == recovered["successor"]
                    deadline = time.time() + options["timeout"]
                    while job["status"] not in {"completed", "failed"} and time.time() < deadline:
                        job = execution_status(job_id=job["jobId"], wait_seconds=1)
                assert job["status"] == "completed", job
                run = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
                assert (
                    "Previous worker outcome; repair before returning: " + incomplete
                    in run["beads"]["B"]["prompt"]
                )
                assert (
                    run["semanticContracts"]["state"] == "RED"
                    and run["semanticValidation"]["state"] == "PASS"
                )
                assert all(f["sha256"] for f in run["semanticValidation"]["testSources"]["files"])
                assert run["delivery"]["root"] == str(workspace.resolve()), run
            else:
                # Independent real failures A, B, then A+B must not pool their
                # allowances. Earlier serial C/B faults never exercised this union.
                actual_popen = subprocess.Popen

                def launch(argv, **kwargs):
                    if "codex_harness.core.planner_jobs" in argv:
                        argv = [
                            sys.executable,
                            "-B",
                            str(ROOT / "tests/python/core/test_golden_path.py"),
                            "controller",
                            str(workspace),
                            "parallel-http",
                            argv[argv.index("--job-id") + 1],
                            argv[argv.index("--segment") + 1],
                        ]
                    return actual_popen(argv, **kwargs)

                products = {
                    "A": {"status.py": product.replace("b'shelf'", "b'ready'")},
                    "B": {"server.py": product},
                }

                def start_fault(faults):
                    payload = dict(
                        products=products,
                        fail=list(faults),
                        deadlinePair=tuple(faults) == ("A",),
                        cancelBead=tuple(faults) == ("B",),
                    )
                    job = start_execution(**options)
                    permit = (
                        workspace / ".codex-harness" / ("http-permit-" + job["jobId"] + ".json")
                    )
                    assert not permit.exists(), permit
                    staged = permit.with_suffix(".pending")
                    staged.write_text(json.dumps(payload), encoding="utf-8")
                    staged.replace(permit)
                    deadline = time.monotonic() + 180
                    released = cancelled = False
                    while (
                        job["status"] not in {"completed", "failed"} and time.monotonic() < deadline
                    ):
                        job = execution_status(job_id=job["jobId"], wait_seconds=1)
                        if not job.get("run"):
                            continue
                        observed = json.loads(Path(job["run"]["recordPath"]).read_text())
                        if tuple(faults) == ("A",) and not released:
                            if not all(observed["beads"][b].get("evidenceDir") for b in ("A", "B")):
                                continue
                            a = Path(observed["beads"]["A"]["evidenceDir"]) / "attempt/result.json"
                            b = Path(observed["beads"]["B"]["evidenceDir"]) / "attempt"
                            if (
                                a.exists()
                                and (workspace / ".codex-harness/sibling-started").exists()
                            ):
                                ended = json.loads(a.read_text())
                                assert ended["failure"]["type"] == "TimeoutExpired"
                                assert (
                                    ended["cleanup"]["quiescenceVerified"]
                                    and ended["cleanup"]["activeProcessesAfterTeardown"] == 0
                                )
                                assert ended["cleanup"]["activeProcessesBeforeTeardown"] >= 3
                                assert (workspace / ".codex-harness/deadline-child").exists()
                                assert (
                                    json.loads((b / "request.json").read_text())[
                                        "processObservedAt"
                                    ]
                                    < ended["finishedAt"]
                                )
                                before = (b / "stderr.txt").stat().st_size
                                until = time.monotonic() + 2
                                while (
                                    b / "stderr.txt"
                                ).stat().st_size == before and time.monotonic() < until:
                                    time.sleep(0.05)
                                assert (
                                    not (b / "result.json").exists()
                                    and (b / "stderr.txt").stat().st_size > before
                                )
                                (workspace / ".codex-harness/sibling-release").touch()
                                released = True
                        if (
                            tuple(faults) == ("B",)
                            and not cancelled
                            and (workspace / ".codex-harness/cancel-provider").exists()
                        ):
                            from codex_harness.core.attempt_control import request_cancel

                            request_cancel(Path(job["run"]["recordPath"]).parents[1], "B")
                            cancelled = True
                    if tuple(faults) == ("A",):
                        assert (
                            released
                            and observed["beads"]["B"]["integrated"]["validation"]["passed"]
                        )
                    if tuple(faults) == ("B",):
                        assert cancelled
                        control = json.loads(
                            (
                                Path(observed["beads"]["B"]["evidenceDir"]) / "attempt/result.json"
                            ).read_text()
                        )
                        assert (
                            control["cancellation"]["status"] == "cancelled"
                            and control["cleanup"]["activeProcessesAfterTeardown"] == 0
                        )
                    return job

                with pytest.MonkeyPatch.context() as process_patch:
                    process_patch.setattr(subprocess, "Popen", launch)
                    for faults, expected in (
                        (("A",), "readmit"),
                        (("B",), "readmit"),
                        (("A", "B"), "readmit"),
                        (("A", "B"), "steer"),
                    ):
                        job = start_fault(faults)
                        assert job["status"] == "failed", job
                        recovered = recover(
                            mode="resolve",
                            job_id=job["jobId"],
                            choice="readmit",
                            reason="Recover the observed HTTP worker faults",
                        )
                        assert set(recovered["proof"]["faultBeads"]) == set(faults)
                        assert recovered["choice"] == expected, recovered
                    if tuple(faults) == ("A", "B"):
                        offending = job["run"]["beads"]["B"]
                        assert offending["status"] == "rejected: changes outside owned files"
                        assert offending["guard"]["changesOutsideOwnedFiles"] == ["unexpected.txt"]
                        assert (
                            offending["candidateRoot"]
                            == json.loads(Path(job["run"]["recordPath"]).read_text())["beads"]["B"][
                                "attempt"
                            ]["cwd"]
                        )
                    assert recovered["successor"] is None
                    steered_job_id = job["jobId"]
                    job = start_fault(())
                    directed = recovery.disposition(store, steered_job_id)
                    assert directed["steeringJudgmentId"]
                    direction = store.judgment(directed["steeringJudgmentId"])
                    assert (
                        direction["positiveCredit"] and direction["meaning"]["option"] == "continue"
                    )
                    assert any(
                        r["kind"] == "loop-question"
                        and r["payload"].get("context", {}).get("jobId") == steered_job_id
                        for r in direction["sourceRefs"]
                    )
                assert job["status"] == "completed", job
                run = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
            # Main now declares the concurrent HTTP integration proof; STALL1's
            # real scripted workers must preserve that stronger result as well.
            assert run["status"] == STATUS_VALIDATED, run
            if semantic:
                # Catch loss of native job/controller/snapshot/attempt authority when quota
                # code is deleted. Earlier fake_workers bypassed all those launch checks.
                for bead in run["beads"].values():
                    attempt = Path(bead["evidenceDir"]) / "attempt"
                    original = json.loads((attempt / "request.json").read_text())
                    result = json.loads((attempt / "result.json").read_text())
                    assert original["processId"] == result["processId"] > 0
                    # Portable fallback used to skip on_launch and omit cleanup;
                    # consumer-only doubles missed lost provenance and AttributeError.
                    assert result["cleanup"]["payloadPid"] == original["processId"]
                    assert (
                        result["cleanup"]["quiescenceVerified"]
                        and result["cleanup"]["activeProcessesAfterTeardown"] == 0
                    )
                    assert original["processProvenanceDigest"] == result["processProvenanceDigest"]
                    assert (attempt / "process-provenance.json").is_file()
                    assert result["exitCode"] == 0 and result["cleanup"]["controllerProcessExited"]
                    assert bead["localProcessProvenance"]["state"] == "consistent"
            if not semantic:
                assert not run["baseline"]["validation"]["B"]["passed"], run
            if semantic:
                assert run["beads"]["C"]["status"] == "reused" and run["beads"]["C"]["reused"], run
                assert run["beads"]["C"]["integrated"]["semantic"]["state"] == "RED", run
                assert set(run["beads"]) == {"C", "B"}
                assert (
                    run["semanticValidation"]["red"]["evidenceBinding"]
                    == run["semanticContracts"]["evidenceBinding"]
                )
                assert next(r for r in run["requirementValidation"] if r["bead"] == "B")[
                    "passed"
                ], run
            else:
                assert {r["bead"] for r in run["requirementValidation"] if r.get("passed")} == {
                    "A",
                    "B",
                    "integration",
                }, run
            if not semantic:
                derived = acceptance_scenarios(derive=True)
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
                acceptance_scenarios(scenarios=scenarios)
                # A forced stop must not pass just because exit 19 was expected.
                # Ordinary exit-0 acceptance never exercised this credit boundary.
                from codex_harness.core.acceptance import run_scenarios

                stopped_marker = Path(run["repo"]) / ".acceptance-interrupted"
                interruption = json.loads(json.dumps(scenarios))
                interruption[0].update(
                    steps=[
                        subprocess.list2cmdline(
                            [
                                sys.executable,
                                "-B",
                                "-c",
                                (
                                    "import sys,time; from pathlib import Path; "
                                    "Path(sys.argv[1]).write_text('started'); time.sleep(600)"
                                ),
                                "{repo}/.acceptance-interrupted",
                            ]
                        )
                    ],
                    expect={"exit": 19},
                )
                acceptance_scenarios(scenarios=interruption)
                stopped = run_scenarios(
                    run["repo"], store.acceptance_scenarios(), should_stop=stopped_marker.exists
                )
                assert not stopped[0]["passed"], stopped
                step = stopped[0]["steps"][0]
                assert (
                    step["cleanup"]["stopReason"] == "acceptance-stopped"
                    and step["cleanup"]["activeProcessesAfterTeardown"] == 0
                )
                stopped_marker.unlink()
                acceptance_scenarios(scenarios=scenarios)
            if semantic:
                # r11: a passing product could not be re-observed after verifier
                # edits; frozen-plan journeys rebuilt instead of retaining bytes.
                from codex_harness.core.planner_candidate import file_hashes

                product_hashes = file_hashes(Path(run["repo"]), ["server.py", "import_items.py"])
                attempts = sorted(
                    (workspace / ".codex-harness/worker-jobs").rglob("attempt/request.json")
                )
                original_root = Path(run["beads"]["B"]["integrated"]["root"])
                original_proof = run["semanticValidation"]["evidenceBinding"]
                baseline_verifier = (template / "verify_http.py").read_text(encoding="utf-8")
                baseline_server = (template / "server.py").read_text(encoding="utf-8")
                built_product = run["semanticValidation"]["productBaseline"]
                original_test = store.spine().node("T_HTTP")

                def publish_verifier(rationale):
                    previous_publication = store._meta()["published_revision"]
                    scope_how(rationale)
                    options["expected_revision"] = compile_current(include_detail=True)[
                        "planRevision"
                    ]
                    assert options["expected_revision"] != previous_publication
                    advance_phase(to_phase="roundtable")
                    reviewed = review_plan(timeout=5)
                    members = [
                        r for r in roundtable.findings(store, expand=True) if r["state"] != "closed"
                    ]
                    if members:
                        dispositions = [
                            dict(
                                id=r["id"],
                                status="resolved",
                                resolution=(
                                    "The unchanged product and current verifier "
                                    "preserve the reviewed readback obligation"
                                ),
                            )
                            for r in members
                        ]
                        pending = resolve_findings(findings=dispositions)
                        scripted_judgment(
                            store,
                            pending["judgment"],
                            dict(memberDispositions={r["id"]: "resolved" for r in members}),
                        )
                        applied = resolve_findings(findings=dispositions)
                        for reuse in applied.get("reuseJudgments", []):
                            reused = scripted_judgment(store, reuse, dict(reuse=True))
                            if reused.get("convergence"):
                                convergence = read_judgment(judgment_id=reused["convergence"]["id"])
                                if convergence["current"] and not convergence["positiveCredit"]:
                                    scripted_judgment(store, convergence, dict(action="ready"))
                    if reviewed.get("convergence"):
                        convergence = read_judgment(judgment_id=reviewed["convergence"]["id"])
                        if convergence["current"] and not convergence["positiveCredit"]:
                            scripted_judgment(store, convergence, dict(action="ready"))
                    advance_phase(to_phase="publish")
                    publish_plan()
                    advance_phase(to_phase="execute")

                # HIGH review: a later root expansion must never exempt built
                # product bytes; frozen declarations missed this two-refresh attack.
                # Rename/removal also needs the prior verifier root through C.
                for filename, source, roots in (
                    (
                        "verify_http.py",
                        baseline_verifier + "\nprint('retained-verifier-v2', file=sys.stderr)\n",
                        ["verify_http.py", "server.py"],
                    ),
                    ("verify_renamed.py", baseline_verifier, ["verify_renamed.py"]),
                ):
                    files = {
                        "verify_http.py": source if filename == "verify_http.py" else None,
                        filename: source,
                    }
                    scripted_worker(run["beads"]["B"]["prompt"], template, files)
                    stale = observe(
                        lambda run=run: assess_execution(
                            judgment=dict(repo=run["repo"], check="execution")
                        )
                    )
                    assert stale["isError"] and "verification inputs changed" in str(stale)
                    edited_test = json.loads(json.dumps(original_test))
                    edited_test["text"] += (
                        " Observe current verifier "
                        + filename
                        + (" with product readback" if len(roots) > 1 else "")
                    )
                    edited_test["fields"]["execution"]["testRoots"] = roots
                    edited_test["fields"]["execution"]["entry"] = filename
                    put_node(node=edited_test)
                    if filename != "verify_http.py":
                        renamed = store.spine().node("V1")
                        renamed["fields"]["targets"]["http"]["entry"] = filename
                        put_node(node=renamed)
                        for key in ("C", "B"):
                            ownership = store.spine().node(key)
                            ownership["fields"]["ownedFiles"] = [
                                filename if n == "verify_http.py" else n
                                for n in ownership["fields"]["ownedFiles"]
                            ]
                            put_node(node=ownership)
                    publish_verifier(
                        "Verifier declarations and filenames change HOW we observe "
                        "the same accepted product; product obligations are unchanged"
                    )
                    if filename == "verify_http.py":
                        # Pre-upgrade jobs have dispatch identity but no retained-candidate record.
                        (workspace / ".codex-harness/retained-candidate.json").unlink()
                        remedy = read_status(full=True)["acceptance"]["candidatePrerequisite"]
                        assert not remedy["complete"] and remedy["jobId"] in {None, job["jobId"]}
                        assert "harness_steer" in remedy["next"]
                        inspected = recover(mode="inspect", job_id=job["jobId"])
                        assert inspected["revalidation"]["available"], inspected
                    refreshed = recover(
                        mode="resolve",
                        job_id=job["jobId"],
                        choice="revalidate",
                        reason=(
                            "Observe the unchanged product with the current verifier, "
                            "without another build"
                        ),
                    )
                    assert refreshed["validation"]["state"] == "pending-judgment", refreshed
                    observed_status = read_status(full=True)
                    assert observed_status["acceptance"]["candidatePrerequisite"]["complete"], (
                        observed_status
                    )
                    run = json.loads(Path(job["run"]["recordPath"]).read_text())
                    root = Path(run["beads"]["B"]["integrated"]["root"])
                    assert (
                        file_hashes(root, product_hashes)
                        == file_hashes(original_root, product_hashes)
                        == product_hashes
                    )
                    proof = run["semanticValidation"]
                    assert (
                        proof["productBaseline"] == built_product
                        and built_product["server.py"] == product_hashes["server.py"]
                    )
                    assert proof["planRevision"] == options["expected_revision"]
                    assert proof["evidenceBinding"] != original_proof
                    source_row = next(
                        f for f in proof["testSources"]["files"] if f["path"] == filename
                    )
                    assert (
                        source_row["sha256"]
                        == hashlib.sha256((template / filename).read_bytes()).hexdigest()
                    )
                    stderr = next(r for r in proof["results"] if r["id"] == "T_HTTP")["actions"][
                        "execute"
                    ]["stderr"]
                    assert ("retained-verifier-v2" in stderr) is ("retained-verifier-v2" in source)
                    assert all(
                        r["cleanup"]["quiescenceVerified"]
                        and r["cleanup"]["activeProcessesAfterTeardown"] == 0
                        for r in proof["results"]
                    )
                    if len(roots) > 1:
                        for changed in ("server.py",):
                            scripted_worker(
                                run["beads"]["B"]["prompt"],
                                template,
                                {
                                    changed: baseline_server
                                    + "\n# Product bytes changed after root expansion\n"
                                    if changed == "server.py"
                                    else "New product file\n"
                                },
                            )
                            fault = store.spine().node("T_HTTP")
                            fault["text"] += " Inspect changed " + changed
                            put_node(node=fault)
                            publish_verifier(
                                "Exercise recovery guards without changing accepted product "
                                "behavior; verifier classification grants no product-write "
                                "authority"
                            )
                            refused = recover(
                                mode="resolve",
                                job_id=job["jobId"],
                                choice="revalidate",
                                reason=(
                                    "Reject changed or added product bytes even after "
                                    "verifier-root expansion"
                                ),
                            )
                            assert refused["validation"][
                                "state"
                            ] == "ERROR" and "product inputs changed" in str(
                                refused["validation"]
                            ), refused
                            assert (
                                file_hashes(root, product_hashes) == product_hashes
                                and not (root / "unowned-product.txt").exists()
                            )
                            scripted_worker(
                                run["beads"]["B"]["prompt"],
                                template,
                                {changed: baseline_server if changed == "server.py" else None},
                            )
                # An externally added product file must also be refused by the
                # canonical retained-candidate guard, before any observation launches.
                scripted_worker(
                    run["beads"]["B"]["prompt"], root, {"unowned-product.txt": "New product file\n"}
                )
                outside = recover(mode="inspect", job_id=job["jobId"])
                assert (
                    not outside["revalidation"]["available"]
                    and "retained candidate changed" in outside["revalidation"]["reason"]
                )
                scripted_worker(run["beads"]["B"]["prompt"], root, {"unowned-product.txt": None})
                judge_candidate(store, dict(repo=str(root), check="execution"))
                # Real delivery must preserve externally changed dependency bytes
                # and expose its failure. After reconciliation, explicit direction
                # retries the retained delivery without another worker launch.
                dependency = workspace / "table_name.txt"
                original_dependency = dependency.read_bytes()
                try:
                    dependency.write_text("other_table", encoding="utf-8")
                    refused_delivery = recover(
                        mode="resolve",
                        job_id=job["jobId"],
                        choice="revalidate",
                        reason="Preserve an externally changed delivered dependency",
                    )
                    assert refused_delivery["validation"][
                        "state"
                    ] == "ERROR" and "existing table_name.txt changed" in str(
                        refused_delivery["validation"]
                    ), refused_delivery
                    assert dependency.read_text(encoding="utf-8") == "other_table"
                finally:
                    dependency.write_bytes(original_dependency)
                refreshed = recover(
                    mode="resolve",
                    job_id=job["jobId"],
                    choice="revalidate",
                    reason="Retry retained delivery after reconciling the changed dependency",
                )
                assert refreshed["validation"]["state"] == "PASS", refreshed
                # C18/I6: retained revalidation still owns the job. Earlier recovery
                # asserted delivery but never read the contradictory public ownership.
                assert jobs.worker_job_status(workspace, job["jobId"])["ownershipReleased"] is False
                run = json.loads(Path(job["run"]["recordPath"]).read_text())
                assert file_hashes(Path(run["repo"]), product_hashes) == product_hashes
                assert (workspace / "verify_renamed.py").is_file() and not (
                    workspace / "verify_http.py"
                ).exists()
                assert (
                    sorted((workspace / ".codex-harness/worker-jobs").rglob("attempt/request.json"))
                    == attempts
                )
                assert decisions == ["align+vision-lock", "plan-execution"]
            advance_phase(to_phase="accept")
            if not semantic:
                # A real capture queues acknowledgement before scenarios may launch.
                # A changed product must refuse here, not run and reject credit later.
                admitted = admit_acceptance(repo=run["repo"], subject="product")
                owner = server.acceptance
                owner.changed.clear()
                owner.dispatch_after_flush(store)
                assert owner.changed.wait(timeout=15), (
                    "Product capture did not reach acknowledgement"
                )
                product_path = Path(run["repo"]) / "server.py"
                original = product_path.read_text(encoding="utf-8")
                product_path.write_text(
                    "from pathlib import Path; Path('.stale-scenario-ran').write_text('ran')\n"
                    + original,
                    encoding="utf-8",
                )
                owner.drain(store)
                owner.thread.join(timeout=30)
                assert not owner.thread.is_alive(), "Stale acceptance did not terminate"
                owner.drain(store)
                refused = acceptance_status(id=admitted["attemptId"])
                assert not (Path(run["repo"]) / ".stale-scenario-ran").exists(), (
                    "Stale scenarios executed"
                )
                assert (
                    refused["result"] is None and "acceptance-revision-changed" in refused["error"]
                )
                assert "Rerun acceptance" in refused["error"]
                product_path.write_text(original, encoding="utf-8")
            accepted = finish_domain_acceptance(
                server,
                observe(lambda: admit_acceptance(repo=run["repo"], subject="product")),
            )
            assert accepted["structuredContent"]["result"]["passed"], accepted
            if semantic:
                # C must reject a changed/missing delivered dependency even when
                # acceptance against the intact integration passed. Owned-file-only
                # delivery tests missed this productBaseline entry.
                dependency = workspace / "table_name.txt"
                assert dependency.read_text() == "items"
                for changed in ("other_table", None):
                    dependency.write_text(changed) if changed is not None else dependency.unlink()
                    refused = observe(
                        lambda: accept_product(
                            statement="The product meets the vision", by="proxy:scripted-owner"
                        )
                    )
                    assert (
                        refused["isError"]
                        and "table_name.txt" in str(refused)
                        and "acceptance-delivery-invalid" in str(refused)
                    )
                    dependency.write_text("items", encoding="utf-8")
                # r12: after a new publication and revalidation of the same job,
                # a historical acceptance binding used to crash every status read.
                # Frozen-publication journeys never reached that reader seam.
                owner_record = workspace / ".codex-harness/retained-candidate.json"
                owner = json.loads(owner_record.read_text())
                assert ".codex/config.toml" in owner["productBaseline"]
                assert (
                    ".codex/config.toml" not in owner["productHashes"]
                    and not (workspace / ".codex").exists()
                )
                assert (workspace / ".gitignore").read_bytes() == (
                    template / ".gitignore"
                ).read_bytes()
                assert (
                    owner["productHashes"][".gitignore"]
                    == hashlib.sha256((workspace / ".gitignore").read_bytes()).hexdigest()
                )
                assert owner["jobId"] == job["jobId"] and owner["runId"] == run["runId"]
                assert owner["publicationRevision"] == store._meta()["published_revision"]
                assert (
                    owner["candidateRoot"] == str(root)
                    and owner["productBaseline"] == built_product
                )
                owner_record.rename(owner_record.with_suffix(".held"))
                try:
                    pending = read_status(full=True)["acceptance"]
                    assert (
                        not pending["accepted"] and not pending["candidatePrerequisite"]["complete"]
                    )
                    assert (
                        "retained candidate unavailable" in pending["invalidMessage"]
                        and pending["candidatePrerequisite"]["next"]
                    )
                    refused = observe(
                        lambda run=run: assess_execution(
                            judgment=dict(repo=run["repo"], check="execution")
                        )
                    )
                    assert refused["isError"] and "retained candidate unavailable" in str(refused)
                    refused = observe(
                        lambda: accept_product(
                            statement="The retained product meets the vision",
                            by="proxy:scripted-owner",
                        )
                    )
                    assert refused["isError"] and "retained candidate unavailable" in str(refused)
                finally:
                    owner_record.with_suffix(".held").rename(owner_record)
                owner_record.write_text(json.dumps(dict(owner, runId="unbound")), encoding="utf-8")
                try:
                    pending = read_status(full=True)["acceptance"]
                    assert (
                        not pending["candidatePrerequisite"]["complete"] and not pending["accepted"]
                    )
                    assert "candidate record is inconsistent" in pending["invalidMessage"]
                    assert pending["candidatePrerequisite"]["jobId"] == job["jobId"]
                finally:
                    owner_record.write_text(json.dumps(owner), encoding="utf-8")
                assert read_status(full=True)["acceptance"]["candidatePrerequisite"]["complete"]

            # Retry/receipt regression: a frozen header previously rejected the
            # controller's corrected stdin and discarded paid malformed returns.
            # Ordinary successful plan reviews never traverse acceptance recovery.
            from codex_harness.core import acceptance_recovery as review_receipts

            def malformed_acceptance(launch, *_, **__):
                request = json.loads(
                    launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split(
                        "\nReturn ONLY JSON", 1
                    )[0]
                )
                response = request["responseExample"]
                response["meaning"]["depth"] = ""
                reviewer = request["sourceRefs"][0]["payload"]["facts"]["reviewer"]
                return protocol(
                    launch.cli,
                    text=json.dumps(response),
                    model=reviewer["model"],
                    effort=reviewer["effort"],
                )

            with pytest.MonkeyPatch.context() as malformed:
                malformed.setattr(review_provider, "_run", malformed_acceptance)
                with pytest.raises(ValueError, match="meaning.depth"):
                    acceptance_review(repo=run["repo"])
            exhausted_attempt = review_receipts.ReviewAttempt.load(
                store, review_receipts._events(store, review_receipts.STARTED)[-1][1]["attemptId"]
            )
            exhausted_launches = review_receipts._events(
                store, review_receipts.REGISTERED, exhausted_attempt.id
            )
            exhausted_returns = review_receipts._events(
                store, review_receipts.COMPLETED, exhausted_attempt.id
            )
            assert len(exhausted_launches) == len(exhausted_returns) == 3
            assert {r["launchId"] for _, r in exhausted_returns} == {
                r["id"] for _, r in exhausted_launches
            }
            exhausted_judgment = store.judgment(exhausted_launches[0][1]["judgmentId"])
            assert (
                exhausted_judgment["fixesIssued"] == 2
                and exhausted_judgment["disposition"] == "steer"
            )
            assert not exhausted_judgment["positiveCredit"] and all(
                r["runtime"] for _, r in exhausted_returns
            )
            with pytest.raises(ValueError):
                exhausted_attempt.read_returns()

            # Actual schema1 producer format: canonical packet/attempt admission,
            # native provider-double bytes and old slot-named sealed returns. This
            # historical input reserves a session; it grants no semantic credit.
            from codex_harness.core.acceptance import _prepare_acceptance_review

            old_run, old_selected, old_dossiers, old_evidence, _, _, _, old_identity = (
                _prepare_acceptance_review(store, run["repo"], None, None)
            )
            old_selected = old_selected[:1]
            roundtable.bind_review_dossiers(
                store, old_dossiers, old_selected, candidate_run=old_run
            )
            old_attempt = review_receipts.ReviewAttempt.start(
                store,
                run["repo"],
                old_run,
                old_selected,
                old_dossiers,
                {r["provider"]: sys.executable for r in old_selected},
                old_evidence,
                mocked=False,
                expected_identity=old_identity,
            )
            old_slot, old_reviewer = old_attempt.header["slots"][0], old_selected[0]
            old_native = protocol(
                old_reviewer["provider"],
                text="[]",
                model=old_reviewer["model"],
                effort=old_reviewer["effort"],
            )
            old_runtime = review_receipts.runtime_identity(
                old_reviewer["provider"], old_reviewer, old_native[1], old_native[2]
            )
            with store._transaction():
                store._event(
                    review_receipts.COMPLETED,
                    dict(
                        schema=1,
                        attemptId=old_attempt.id,
                        headerHash=review_receipts.digest(old_attempt.header),
                        slot=old_slot["id"],
                        launch=old_slot["launch"],
                        exitCode=old_native[0],
                        isMocked=False,
                        didRunProvider=True,
                        runtime=old_runtime,
                        stdout=review_receipts._write(
                            old_attempt.root,
                            old_slot["id"] + ".stdout",
                            old_native[1].encode("utf-8"),
                        ),
                        stderr=review_receipts._write(
                            old_attempt.root,
                            old_slot["id"] + ".stderr",
                            old_native[2].encode("utf-8"),
                        ),
                    ),
                )
            with pytest.raises(ValueError, match="fresh acceptance review"):
                old_attempt.read_returns()

            def repeated_old_session(launch, *args, **options):
                response = positive_review(launch, *args, **options)
                request = json.loads(
                    launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split(
                        "\nReturn ONLY JSON", 1
                    )[0]
                )
                reviewer = request["sourceRefs"][0]["payload"]["facts"]["reviewer"]
                if reviewer["name"] == old_reviewer["name"]:
                    actual = review_receipts.runtime_identity(
                        reviewer["provider"], reviewer, response[1], response[2]
                    )
                    response = tuple(
                        v.replace(actual["session"], old_runtime["session"])
                        if isinstance(v, str)
                        else v
                        for v in response
                    )
                return response

            with pytest.MonkeyPatch.context() as replay:
                replay.setattr(review_provider, "_run", repeated_old_session)
                with pytest.raises(ValueError, match="Native session was already registered"):
                    acceptance_review(repo=run["repo"])

            def acceptance_gap(launch, *_, **__):
                request = json.loads(
                    launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split(
                        "\nReturn ONLY JSON", 1
                    )[0]
                )
                reviewer = request["sourceRefs"][0]["payload"]["facts"]["reviewer"]
                if store.judgment(request["id"])["fixesIssued"] < 2:
                    return malformed_acceptance(launch)
                # A negative verdict with no presentation findings still creates
                # concrete obligations. The next positive packet must carry them.
                items = []
                if not semantic and reviewer["name"] == selected_reviewers[0]["name"]:
                    # One named concern also exercises genuine AF -> worker support;
                    # the other reviewer retains the negative-without-findings twin.
                    items = [dict(node_id="R", severity="major",
                        finding="HTTP readback needs candidate-bound assessment",
                        suggestion="Assess the real server response witness",
                        classification="defect-in-plan", scope_reason="The accepted HTTP behavior")]
                return protocol(
                    launch.cli,
                    model=reviewer["model"],
                    effort=reviewer["effort"],
                    text=scripted_review_reply(
                        launch,
                        items,
                        verdict="negative",
                        rationale="The readback finding needs candidate-bound assessment",
                        gaps=["Readback assessment is pending"],
                    ),
                )

            with pytest.MonkeyPatch.context() as gap:
                for owner in (roundtable, review_provider):
                    gap.setattr(owner, "_run", acceptance_gap)
                retried_review = acceptance_review(repo=run["repo"])
                acceptance_members = retried_review["findings"]
            accepted_attempt = review_receipts.ReviewAttempt.load(
                store, retried_review["attemptId"]
            )
            accepted_launches = review_receipts._events(
                store, review_receipts.REGISTERED, accepted_attempt.id
            )
            accepted_returns = review_receipts._events(
                store, review_receipts.COMPLETED, accepted_attempt.id
            )
            for slot in accepted_attempt.header["slots"]:
                launches = [r for _, r in accepted_launches if r["slot"] == slot["id"]]
                assert [r["fixesIssued"] for r in launches] == [0, 1, 2]
                prompts = [
                    review_receipts._read(accepted_attempt.root, r["id"] + ".stdin", r["stdin"])
                    for r in launches
                ]
                assert [p.count("Exact fix required:") for p in prompts] == [0, 1, 2]
                assert all(
                    r["headerHash"] == review_receipts.digest(accepted_attempt.header)
                    for r in launches
                )
            assert len(accepted_attempt.read_returns()) == len(accepted_attempt.header["slots"])
            assert (
                len(accepted_returns)
                == len(accepted_launches)
                == len(accepted_attempt.header["slots"]) * 3
            )
            assert set(retried_review["receiptEventIds"]) == {key for key, _ in accepted_returns}
            review_history_ids.update(row["id"] for row in acceptance_members)
            # TD#5 adds one named R concern; both original negative-gap members remain.
            assert len(acceptance_members) == (2 if semantic else 3) and all(
                row["state"] == "open" for row in acceptance_members
            )
            if not semantic:
                # Real admitted AF -> recovered worker originals -> assessment packet.
                # Reading originals alone misses the assessment's second ID equality.
                from codex_harness.core.runners import execution_witnesses
                from codex_harness.core.dispatch_attempts import read_completed_execution_evidence

                assert job["jobId"] != job["publicationRevision"], job
                with pytest.raises(ValueError, match="execution-current-terminal-job-required"):
                    read_completed_execution_evidence(
                        workspace, job["jobId"], "B", expected_repo=run["repo"],
                        expected_publication_revision="0" * 64,
                    )
                member = next(row for row in acceptance_members if row["node_id"] in {
                    ref["id"] for ref in run["beads"]["B"]["requirementRefs"]
                })
                header = store.register_execution_assessment(
                    members=[dict(memberId=member["id"], artifactPaths=["server.py"],
                                  witnessIds=[w["id"] for w in execution_witnesses(run["beads"]["B"]["integrated"])])],
                    worker_job_id=job["jobId"], bead_id="B",
                    expected_manifest_revision=store.manifest_revision(),
                    expected_product_run_id=retried_review["runId"],
                    reviewer=selected_reviewers[0], timeout=5,
                )
                assert header["context"]["execution"]["jobId"] == job["jobId"]
                assert header["context"]["execution"]["candidateCommit"] == subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=run["repo"], text=True,
                ).strip()
            dispositions = [
                dict(
                    id=row["id"],
                    status="resolved",
                    resolution="Current readback addresses this retained obligation",
                )
                for row in acceptance_members
            ]
            if not semantic:
                prepared = resolve_findings(findings=dispositions)
                scripted_judgment(
                    store,
                    prepared["judgment"],
                    dict(memberDispositions={row["id"]: "resolved" for row in acceptance_members}),
                )
                product = Path(run["repo"])
                source = (product / "server.py").read_text(encoding="utf-8")
                changed = source.replace(
                    "self.end_headers()",
                    "self.send_header('Content-Type', 'text/plain')\n        self.end_headers()",
                )
                assert changed != source
                scripted_worker(run["beads"]["B"]["prompt"], product, {"server.py": changed})
                stale = read_judgment(judgment_id=prepared["judgment"]["id"])
                assert not stale["current"] and not stale["positiveCredit"]
                # Round 2: the run is the disposition's producer. Earlier coverage
                # reran it manually while guidance advertised immediately stale judgments.
                status = read_status(full=True)
                action = store.finding_state()["dispositionPrerequisite"]["nextAction"]
                assert (
                    action["kind"] == "acceptance_run_required"
                    and action["reason"] == "product-changed"
                )
                assert "harness_steer" in status["acceptance"]["next"]
                assert status["acceptance"]["accepted"] is False
                assert all(row["state"] == "pending" for row in store.acceptance_findings())
                scripted_worker(run["beads"]["B"]["prompt"], product, {"server.py": source})
                assert resolve_findings(findings=dispositions)["dispositions"] == len(dispositions)
                assert read_judgment(judgment_id=prepared["judgment"]["id"])["positiveCredit"]
                assert all(
                    r["state"] == "closed"
                    for r in store.acceptance_findings()
                    if r["id"] in {d["id"] for d in dispositions}
                )
                # Applying a closure never changes immutable facts. A later candidate
                # edit must remove effective closure everywhere, including ordinary reads.
                scripted_worker(run["beads"]["B"]["prompt"], product, {"server.py": changed})
                assert all(r["state"] == "pending" for r in store.acceptance_findings())
                assert all(r["status"] != "resolved" for r in store.acceptance_findings())
                # Same regression at C: execute the changed candidate and obtain
                # fresh positive reviews, then try acceptance before disposition.
                action = store.finding_state()["dispositionPrerequisite"]["nextAction"]
                assert action["kind"] == "acceptance_run_required"
                admitted = observe(lambda: admit_acceptance(repo=run["repo"], subject="product"))
                waiting = acceptance_status(id=admitted["structuredContent"]["attemptId"])
                assert (
                    waiting["state"] == "queued"
                    and waiting["attemptId"] == admitted["structuredContent"]["attemptId"]
                )
                rerun = finish_domain_acceptance(server, admitted)
                assert rerun["structuredContent"]["result"]["passed"], rerun
            # Migration refreshes execution only. No review/approval lineage is
            # restored; both legacy inputs must proceed through fresh judgments.
            from codex_harness.core import acceptance

            original_approval = read_status()["acceptance"]["approval"]
            old_run = acceptance._latest_run(store)
            legacy = dict(
                old_run,
                revisionAlgorithm="git-diff-v1",
                revision=acceptance._integrated_revision_v1(run["repo"]),
            )
            # Historical persisted input only; judgments and refreshed runs still
            # come from their canonical producers, with no mocked acceptance.
            with store._transaction():
                if not semantic:
                    legacy_scenarios = acceptance._raw_scenarios(store)
                    for item in legacy_scenarios:
                        del item["nodeRevisions"]
                        store.connection.execute(
                            "UPDATE acceptance_scenarios SET scenario_json=? WHERE id=?",
                            (json.dumps(item), item["id"]),
                        )
                    legacy["scenarioRevision"] = acceptance._hash(legacy_scenarios)
                store.connection.execute(
                    "UPDATE acceptance_runs SET result_json=? WHERE id=?",
                    (json.dumps(legacy), legacy["id"]),
                )
            status = read_status(full=True)
            assert status["acceptance"]["invalidReason"] in {
                "revision-algorithm-upgraded",
                "legacy-unbound",
            }
            refreshed = store.accept_rebind(run["repo"])
            refreshed_id = refreshed["run"]["id"]
            assert refreshed["run"]["passed"] and refreshed_id != legacy["id"]
            assert "approval" not in refreshed and "reviews" not in refreshed
            status = read_status(full=True)
            assert status["acceptance"]["accepted"] is False
            assert status["acceptance"]["approval"] == original_approval
            assert not store.finding_state()["dispositionPrerequisite"]["complete"]
            if not semantic:
                # Upgrade fixture: retain a base-produced positive C with no review
                # binding. Prior replays created only newly bound C, missing revival
                # of historical consent after template drift and fresh same-run reviews.
                from codex_harness.core.approvals import assess
                from codex_harness.core.reviewer_evidence import review_binding

                historical = assess(
                    store,
                    "user-decision",
                    dict(
                        subject=dict(kind="C", run=refreshed["run"]),
                        reviewBinding=review_binding(store, run_id=refreshed_id),
                        words="Yes",
                        actor="user",
                    ),
                )
                scripted_judgment(store, historical, dict(decision="approve"))
                historical = store._judgment_record(historical["id"])
                for ref in historical["sourceRefs"]:
                    ref["payload"]["facts"].pop("reviewBinding", None)
                historical["binding"] = hashlib.sha256(
                    json.dumps(
                        {
                            key: historical[key]
                            for key in (
                                "id",
                                "question",
                                "subjectRefs",
                                "evidenceRefs",
                                "sourceRefs",
                                "candidate",
                            )
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest()
                legacy_c = dict(
                    id="legacy-unbound-C",
                    runId=refreshed_id,
                    by="user",
                    statement="Yes",
                    isProxy=False,
                    judgmentId=historical["id"],
                    human_identity_verified=False,
                )
                with store._transaction():
                    store._event("judgment-recorded", historical)
                    store.connection.execute(
                        "INSERT INTO acceptance_approvals VALUES (?, ?)",
                        (legacy_c["id"], json.dumps(legacy_c)),
                    )
            # Before fresh review, changed template bytes grant no credit. Earlier
            # replays restored lineage instead of exercising current judgments.
            template_file = next(
                path for path in (workspace / "template").rglob("*.py") if path.is_file()
            )
            original_template = template_file.read_bytes()
            template_file.write_text(
                template_file.read_text(encoding="utf-8") + "\n# Changed migration review input\n",
                encoding="utf-8",
            )
            scope_how(
                "The migration-review template comment changes no executable "
                "assertion or product obligation"
            )
            required = store.finding_state()["dispositionPrerequisite"]["members"]
            dispositions = [
                dict(
                    id=row["id"],
                    status="resolved",
                    resolution="Current execution and review address this retained obligation",
                )
                for row in required
            ]
            assert store.acceptance_review_guard(refreshed_id) == "acceptance-review-required"
            acceptance_review(repo=run["repo"])
            held = observe(lambda: accept_product(by="user", statement="Yes"))
            assert held["isError"] and held["refusalCode"] == "pending-judgment", held
            for row in required:
                assert row["id"] in str(held)
            prepared = resolve_findings(findings=dispositions)
            facts = prepared["judgment"]["sourceRefs"][0]["payload"]["facts"]
            assert (
                prepared["judgment"]["current"] and facts["reviewBinding"]["runId"] == refreshed_id
            )
            scripted_judgment(
                store,
                prepared["judgment"],
                dict(memberDispositions={row["id"]: "resolved" for row in dispositions}),
            )
            assert resolve_findings(findings=dispositions)["dispositions"] == len(dispositions)
            status = read_status(full=True)
            assert status["acceptance"]["accepted"] is False
            assert status["acceptance"]["invalidReason"] == "acceptance-approval-required", status[
                "acceptance"
            ]
            assert "harness_steer" in status["acceptance"]["next"]
            if not semantic:
                assert status["acceptance"]["approval"] == legacy_c
                assert not read_judgment(judgment_id=historical["id"])["positiveCredit"]
            decisions.append("accept")
            fresh_c = settle_decision(store, accept_product(by="user", statement="Yes"))
            assert fresh_c["runId"] == refreshed_id and fresh_c["by"] == "user"
            assert read_judgment(judgment_id=fresh_c["judgmentId"])["positiveCredit"]
            assert store.status()["acceptance"]["accepted"]
            assert decisions == ["align+vision-lock", "plan-execution", "accept"]
            # After completed migration/C, further template drift must stale both
            # review credit and C's canonical judgment; finding holds cannot mask it.
            retained_template = template_file.read_text(encoding="utf-8")
            template_file.write_text(
                retained_template + "\n# Changed after current C\n", encoding="utf-8"
            )
            scope_how(
                "The additional template comment preserves every approved "
                "behavior and acceptance assertion"
            )
            assert store.acceptance_review_guard(refreshed_id) == "acceptance-review-required"
            assert not read_judgment(judgment_id=fresh_c["judgmentId"])["current"]
            assert read_status()["acceptance"]["accepted"] is False
            if not semantic:
                from codex_harness.core.acceptance import run_wave_exits
                from codex_harness.core.spine import SpineError

                run_dir = Path(job["run"]["recordPath"]).parent.parent
                with pytest.raises(SpineError, match="Publish the current scope"):
                    run_wave_exits(store, run_dir, "first")
                template_file.write_bytes(original_template)
                scripted_worker(
                    run["beads"]["B"]["prompt"], Path(run["repo"]), {"server.py": source}
                )
                exits = run_wave_exits(store, run_dir, "first")
                assert exits["passed"] and all(r["passed"] for r in exits["results"])
                context = store.prepare_wave_refinement("later", run_dir)
                interface = next(
                    f"{r['blob']}:{symbol['symbol']}"
                    for r in context["interfaces"]
                    for symbol in r["symbols"]
                    if symbol["symbol"] == "response_body"
                )
                response = dict(
                    schema="dot-wave-refinement-response-v1",
                    contextDigest=context["contextDigest"],
                    refinements=[
                        dict(
                            sketchId="SK_HTTP",
                            nodes=[
                                node(
                                    "T_NEXT", "test", ["R"], level="semantic", module="test_http.py"
                                ),
                                node(
                                    "NEXT",
                                    "bead",
                                    ["T_NEXT"],
                                    ownedFiles=["http_adapter.py"],
                                    dependsOn=["B"],
                                    lessons=[],
                                    proofModule="test_http.py",
                                ),
                            ],
                            interfaceRefs=[interface],
                            reason=(
                                "Split adapter ownership after observing the actual "
                                "producer interface"
                            ),
                            replacedAssumptions=["Use the observed byte response contract"],
                        )
                    ],
                    contradictions=[],
                    questions=[],
                )
                proposal = store.record_wave_refinement(
                    store._revision(), context["contextDigest"], json.dumps(response).encode()
                )
                assert proposal["state"] == "complete", proposal
                applied = store.apply_wave_refinement(proposal["proposalId"], store._revision())
                assert {"NEXT", "T_NEXT", "WD2"} == set(applied["editedNodeIds"])
                scope_how(
                    "The new adapter bead/test and refined wave detail "
                    "re-partition the same HTTP behavior using its observed "
                    "interface"
                )
                visible = observe(lambda: read_status())
                assert {"NEXT", "T_NEXT", "WD2", "later"} <= set(
                    visible["structuredContent"]["continuedPlanChanges"]
                )
                assert {"NEXT", "T_NEXT", "WD2", "later"} <= set(
                    visible["structuredContent"]["continuedPlanChanges"]
                )
                assert (
                    len(
                        [
                            r
                            for r in store.connection.execute(
                                "SELECT payload_json FROM events WHERE "
                                "kind='confirmation-requested'"
                            )
                            if json.loads(r[0])["gate"] == "plan-execution"
                        ]
                    )
                    == 1
                )
                # Node-only consent missed worker-authored assertion weakening:
                # every node stays unchanged while admission consumes different
                # protected bytes. Existing node edits missed this seam.
                from codex_harness.core.plan_store import PlanStoreError
                from codex_harness.core.planner_execution import execution_scope, require_execution

                proof_path = template / "tests/test_http.py"
                original_source = proof_path.read_text(encoding="utf-8")
                weaker = original_source.replace(
                    "self.assertEqual(response.read(), b'shelf')",
                    "self.assertTrue(response.read())",
                )
                assert weaker != original_source
                roster = [dict(options["workers"][0], bead=k) for k in store.spine().ids("bead")]

                def candidate_scope():
                    revision = compile_current(include_detail=True)["planRevision"]
                    return execution_scope(
                        store,
                        revision,
                        roster,
                        max_parallel=1,
                        timeout=options["timeout"],
                        _candidate=True,
                    )

                prior_scope = candidate_scope()
                for source, impact in [
                    (weaker, "WHAT"),
                    (weaker.replace("def test_status_and_body", "def test_http_response"), "HOW"),
                ]:
                    nodes_before = store.spine().to_dict()
                    before_source = proof_path.read_bytes().decode("utf-8")
                    revision = compile_current(include_detail=True)["planRevision"]
                    worker_verifier(
                        node_id="T_NEXT",
                        path="tests/test_http.py",
                        expected_manifest_revision=revision,
                        expected_file_sha256=hashlib.sha256(proof_path.read_bytes()).hexdigest(),
                        source=source,
                        reason="Scripted planner revises the linked HTTP acceptance check",
                    )
                    assert nodes_before == store.spine().to_dict()
                    scope = candidate_scope()
                    with pytest.raises(ValueError, match="worker-execution-inputs-stale"):
                        require_execution(store, prior_scope)
                    with pytest.raises(PlanStoreError, match="pending-judgment") as held:
                        require_execution(store, scope)
                    question = held.value.approval["scopeJudgment"]
                    facts = question["sourceRefs"][0]["payload"]["facts"]
                    assert (
                        facts["before"]["files"]["tests/test_http.py"]["content"] == before_source
                    )
                    authored_source = proof_path.read_bytes().decode("utf-8")
                    assert proof_path.read_text(encoding="utf-8") == source
                    assert (
                        facts["after"]["files"]["tests/test_http.py"]["content"] == authored_source
                    )
                    scripted_judgment(
                        store,
                        question,
                        dict(impact=impact),
                        rationale=(
                            (
                                "Nonempty output replaces the accepted exact shelf response; "
                                "acceptance is weakened"
                            )
                            if impact == "WHAT"
                            else (
                                "Only the test method name changes; the approved "
                                "assertions and data flow are identical"
                            )
                        ),
                    )
                    if impact == "WHAT":
                        with pytest.raises(PlanStoreError, match="plan-b-consent-required"):
                            require_execution(store, scope)
                        approved_options = dict(
                            options,
                            workers=roster,
                            expected_revision=compile_current(include_detail=True)["planRevision"],
                        )
                        card = prepare_words(gate="plan-execution", execution=approved_options)
                        assert "tests/test_http.py" in str(
                            card["diff"]
                        ) and "self.assertTrue(response.read())" in str(card["diff"])
                        assert (
                            prepare_words(gate="plan-execution", execution=approved_options)[
                                "request_id"
                            ]
                            == card["request_id"]
                        )
                        yes(card)
                    else:
                        assert "tests/test_http.py" in read_status()["continuedPlanChanges"]
                    require_execution(store, scope)
                    assert (
                        len(
                            [
                                r
                                for r in store.connection.execute(
                                    "SELECT payload_json FROM events WHERE "
                                    "kind='confirmation-requested'"
                                )
                                if json.loads(r[0])["gate"] == "plan-execution"
                            ]
                        )
                        == 2
                    )
                    prior_scope = scope
                assert decisions == [
                    "align+vision-lock",
                    "plan-execution",
                    "accept",
                    "plan-execution",
                ]
            else:
                # Rank-based consent omitted design instructions delivered in
                # SCENARIOS.json. Frozen requirements and story-only edits missed
                # both third-party disclosure and ordinary technical refactors.
                from codex_harness.core.approvals import scope_assessment

                previous = scope_assessment(store)
                for node_id, text, impact, cards in [
                    (
                        "G1",
                        "export locally; split the exporter into two internal modules",
                        "HOW",
                        1,
                    ),
                    ("G1", "upload exports to a third party", "WHAT", 2),
                    ("EXPORT_STORY", "upload exports to a third party", "WHAT", 3),
                ]:
                    assert read_judgment(judgment_id=previous["id"])["current"]
                    unchanged = {
                        k: store.spine().node(k) for k in store.spine().ids() if k != node_id
                    }
                    edited = store.spine().node(node_id)
                    edited["text"] = text
                    before_launches = len(provider_launches)
                    put_node(node=edited)
                    assert unchanged == {k: store.spine().node(k) for k in unchanged}
                    options["expected_revision"] = compile_current(include_detail=True)[
                        "planRevision"
                    ]
                    assert not read_judgment(judgment_id=previous["id"])["current"]
                    held = observe(
                        lambda: review_readiness(reviewers=selected_reviewers, beads=["B"])
                    )
                    assert (
                        held["refusalCode"] == "pending-judgment"
                        and len(provider_launches) == before_launches
                    )
                    previous = held["structuredContent"]["scopeJudgment"]
                    scripted_judgment(
                        store,
                        previous,
                        dict(impact=impact),
                        rationale=(
                            (
                                "Only internal module boundaries change; local export "
                                "obligations remain identical"
                            )
                            if impact == "HOW"
                            else (
                                "Local export now discloses data to a third party despite "
                                "unchanged generic feature/requirement text"
                            )
                        ),
                    )
                    options["expected_revision"] = compile_current(include_detail=True)[
                        "planRevision"
                    ]
                    if impact == "WHAT":
                        held = observe(
                            lambda: review_readiness(reviewers=selected_reviewers, beads=["B"])
                        )
                        assert (
                            held["refusalCode"] == "plan-b-consent-required"
                            and len(provider_launches) == before_launches
                        )
                        card = prepare_words(gate="plan-execution", execution=options)
                        assert f"scope.nodes.{node_id}.text" in {d["field"] for d in card["diff"]}
                        assert (
                            prepare_words(gate="plan-execution", execution=options)["request_id"]
                            == card["request_id"]
                        )
                        yes(card)
                    else:
                        assert node_id in read_status()["continuedPlanChanges"]
                    review_readiness(reviewers=selected_reviewers, beads=["B"])
                    assert len(provider_launches) == before_launches + len(selected_reviewers)
                    assert all(
                        text in launch.stdin_text for launch in provider_launches[before_launches:]
                    )
                    assert (
                        len(
                            [
                                r
                                for r in store.connection.execute(
                                    "SELECT payload_json FROM events WHERE "
                                    "kind='confirmation-requested'"
                                )
                                if json.loads(r[0])["gate"] == "plan-execution"
                            ]
                        )
                        == cards
                    )
                assert decisions == [
                    "align+vision-lock",
                    "plan-execution",
                    "accept",
                    "plan-execution",
                    "plan-execution",
                ]
                assert not store.status()["acceptance"][
                    "accepted"
                ]  # New third-party work is unbuilt.
            print(
                (
                    "golden ShelfLife: malformed two-fix/steer; negative "
                    "minor/conflict pending; actual-delta reuse; "
                    "importer/SQLite/HTTP RED->GREEN; fresh C accepted; template "
                    "drift held"
                )
                if semantic
                else (
                    "golden http: original A/B/C; file WHAT approved; HOW "
                    "reported; HTTP + concurrency RED->GREEN; template drift held"
                )
            )
    finally:
        server.close()
