"""Golden semantics through the real stdio transport, bounded processes and delivery."""

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import venv
from pathlib import Path
from functools import wraps

import pytest

from tests.seldon.test_journey import clean_core_golden

from codex_harness.core import approvals
from codex_harness.core import planner_confirmations as confirmations

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.python.core.mcp_planner_support import fixture_id, provider_answer, apply_content, fixture_judgment_example
ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.fast


def timed(function):
    """Print existing journey wall time, including failure and cleanup."""
    @wraps(function)
    def measured(*args, **kwargs):
        start = time.monotonic()
        try:
            return function(*args, **kwargs)
        finally:
            label = function.__name__ + (":" + str(kwargs.get("journey", args[-1] if args else "")) if function.__name__.startswith("test_") else "")
            print(f"golden timing {label}: {time.monotonic() - start:.2f}s", file=sys.stderr, flush=True)
    return measured


def http_direction(purpose):
    """Keep observed failures for the next explicit fixture step."""
    return "pause" if purpose.startswith(("Two automatic recoveries exhausted", "Retained observation refused:")) else "continue"


def scripted_review_reply(launch, findings, *, verdict, rationale, gaps=()):
    """Script only a provider reply to the actual bound review packet."""
    prompt = launch.stdin_text if launch.stdin_text is not None else launch.command[launch.command.index("-p")+1]
    question = json.loads(prompt.split("\n## Review question\n", 1)[1].split("\nExact fix required:", 1)[0])
    assert "depth" in question["answerSchema"]["properties"]
    assessments = dict(featureAssessments=[dict(featureRef=r["ref"], verdict=verdict, rationale=rationale) for r in question.get("featureInventory",[])],
        qualityAssessments=[dict(qualityRef=r["ref"], verdict=verdict, rationale=rationale) for r in question.get("qualityInventory",[])], stubOrMissingGaps=[]) if "featureInventory" in question else {}
    return json.dumps(dict(verdict=verdict, rationale=rationale, **assessments,
        gaps=[dict(subjectRef="plan", evidenceRefs=[], issue=g) for g in gaps],
        findings=[dict(classification="defect-in-plan", scope_reason="Within the accepted brief", **f) if "classification" not in f else f for f in findings],
        depth="Focused review of persistence and failure behavior because their consequences affect the accepted quality bar"))


def scripted_worker(packet, root, files):
    """Script worker-authored files in response to the delivered packet, never proof results."""
    instructions = packet.split("\n\n")[1]
    assert "SELDON-STUB" not in instructions and "exits 3" not in instructions, instructions
    assert "unit tests" not in instructions, instructions
    for name, source in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        if source is None:
            (root / name).unlink(missing_ok=True)
        else:
            (root / name).write_text(source, encoding="utf-8")


@timed
def review_replacement_golden(store, reviewer):
    """C11/I6: reserve -> return -> credit, including inverted completion order.

    An old pass must not survive a pending/failed replacement; an older slow
    completion must not replace the newest reservation. Serial successes miss both.
    Only provider replies are scripted; admission, persistence and credit are real.
    """
    from concurrent.futures import ThreadPoolExecutor
    from tests.python.core.acceptance_recovery_support import protocol
    from codex_harness.core import roundtable, review_provider
    from codex_harness.core.plan_store import open_plan_store

    entered, release = threading.Event(), threading.Event()
    fail_new = True

    def reply(launch, *_, **__):
        if threading.current_thread().name.startswith("slow-review"):
            entered.set()
            assert release.wait(30), "Slow review fixture was not released"
        elif fail_new:
            return 1, "", "Scripted replacement provider failed"
        return protocol(launch.cli, model=reviewer["model"], effort=reviewer["effort"],
                        text=scripted_review_reply(launch, [], verdict="positive",
                                                  rationale="The unchanged plan covers the dream"))

    def slow_review():
        with open_plan_store(store.workspace) as owner:
            return roundtable.run_roundtable(owner, reviewers=[reviewer], timeout=5)

    def credit():
        return [r for r in store.successful_reviews(store.manifest_revision())
                if (r["reviewer"], r["provider"]) == (reviewer["name"], reviewer["provider"])]

    with pytest.MonkeyPatch.context() as patch, ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="slow-review"
    ) as pool:
        patch.setattr(review_provider, "_run", reply)
        future = pool.submit(slow_review)
        try:
            assert entered.wait(15), "Review never reached its admitted provider call"
            assert not credit(), "An unfinished replacement retained older review credit"
            roundtable.run_roundtable(store, reviewers=[reviewer], timeout=5)
            assert not credit(), "A failed replacement retained older review credit"
            fail_new = False
            latest = roundtable.run_roundtable(store, reviewers=[reviewer], timeout=5)
            expected = credit()
            assert len(expected) == 1, "An older reservation overrode the latest pass"
        finally:
            release.set()
        future.result(timeout=15)
        assert [r["id"] for r in credit()] == [r["id"] for r in expected]
        latest["convergence"] = store.review_convergence(prepare=True)
    return latest


def served_product():
    """Worker reply: a real server and UI write, with disk-backed API readback."""
    return """import os, json
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, HTTPServer
class App(SimpleHTTPRequestHandler):
    def do_DELETE(self):
        Path('state.json').unlink(missing_ok=True)
        self.send_response(204); self.end_headers()
    def do_GET(self):
        dbpath = Path('scenario.sqlite3').resolve()
        import sqlite3
        with sqlite3.connect(dbpath) as db: db.execute('create table if not exists preview (value text)')
        if self.path in ('/empty', '/reset', '/cached'):
            self.send_response({'/empty': 204, '/reset': 205, '/cached': 304}[self.path]); self.end_headers(); return
        if self.path == '/disconnect':
            self.connection.close(); return
        if self.path in ('/truncated200', '/truncated409'):
            self.send_response(200 if self.path == '/truncated200' else 409); self.send_header('Content-Length', '64'); self.end_headers()
            self.wfile.write(b'{'); self.wfile.flush(); self.close_connection = True; return
        if self.path in ('/overconsume', '/broken'):
            self.send_response(409 if self.path == '/overconsume' else 500); self.end_headers(); self.wfile.write(b'{}'); return
        if self.path == '/preview-db':
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(dict(path=str(dbpath), exists=dbpath.is_file())).encode()); return
        if self.path in ('/api/state', '/seed'):
            file = Path('seed.json' if self.path == '/seed' else 'state.json')
            data = file.read_bytes() if file.exists() else (b'{"note":"Missing seed"}' if self.path == '/seed' else b'{"saved":false}')
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)
        else:
            super().do_GET()
    def do_POST(self):
        if self.path == '/submit':
            from urllib.parse import parse_qs
            Path('submission.json').write_text(json.dumps(parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())))
            self.send_response(302); self.send_header('Location', '/index.html#posted'); self.end_headers(); return
        assert self.path in ('/api/state', '/seed')
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        Path('seed.json' if self.path == '/seed' else 'state.json').write_text(json.dumps(dict(saved=True, note=payload['note'])))
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
HTTPServer(('127.0.0.1', int(os.environ['SELDON_PORT'])), App).serve_forever()
"""


def worker_checks():
    """Worker-owned verifier bundle; run unchanged on the RED and GREEN candidates."""
    return {
        "checks/verify_hashslip.py": "# Exact binary receipt; catches missing CLI/data loss; scenarios alone had no worker test.\nimport subprocess, sys\nfrom pathlib import Path\nfrom receipt_helper import payload, check_receipt\ncommand = Path(__file__).resolve().parents[1] / 'hashslip.py'\ndata = payload()\nimport hashslip  # Tests-first: the product module is absent on baseline.\nrun = subprocess.run([sys.executable, '-B', str(command)], input=data, capture_output=True, timeout=5)\ncheck_receipt(data, run)\n",
        "checks/receipt_helper.py": "import hashlib, json\nfrom pathlib import Path\ndef payload():\n    assert sorted(p.name for p in Path(__file__).parent.glob('*.json')) == ['input.json'], 'retired fixture still present'\n    return bytes(json.loads((Path(__file__).parent / 'input.json').read_text()))\ndef check_receipt(data, run):\n    assert run.returncode == 0 and not run.stderr, run.stderr\n    assert json.loads(run.stdout) == dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), label=None)\n",
        "checks/input.json": "[0, 255, 65]\n",
        "checks/retired.json": None,
    }


def provider_host(mode, workspace, surface, job_id=None, segment_id=None):
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
        review_provider,
        roundtable,
    )

    workspace = Path(workspace)
    # The enclosing golden also contains a child workspace named receipt-gaps;
    # only that child's explicit marker file authorizes its interruption hooks.
    receipt_boundaries = (workspace / "receipt-gaps").is_file()
    # Fault clock changes elapsed time only, never creates terminal/proof receipts.
    from datetime import datetime, timedelta

    from codex_harness.core import run_loop

    class AwakeClock(datetime):
        @classmethod
        def now(cls, tz=None):
            shift = workspace / "clock-shift"
            return datetime.now(tz) + timedelta(
                seconds=float(shift.read_text()) if shift.exists() else 0
            )

    run_loop.datetime = AwakeClock

    def hold_receipt_once(name, details):
        """Hold only after a real producer returned; the client kills this owned MCP PID."""
        marker = workspace / ".codex-harness" / (name + ".json")
        if marker.exists():
            return
        staged = marker.with_suffix(".pending")
        staged.write_text(json.dumps(dict(details, ownerPid=os.getpid())), encoding="utf-8")
        staged.replace(marker)
        marker.with_suffix(".ready").touch()
        deadline = time.monotonic() + 60
        release = marker.with_suffix(".release")
        while not release.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert release.exists(), (
            "Receipt-boundary fixture did not receive owned MCP teardown: " + name
        )

    if mode in {"server","controller"}:
        from codex_harness.core import run_invocations

        observed_invoke = run_invocations.invoke

        def hold_semantic_reply(store, selected, pending, timeout, binding):
            raw = observed_invoke(store, selected, pending, timeout, binding)
            if (workspace / "semantic-retry-script.json").exists() and pending["purpose"] == "understanding" and pending["judgment"]["fixesIssued"] == 2:
                hold_receipt_once(
                    "semantic-raw-complete",
                    dict(
                        requestId=pending["requestId"],
                        binding=pending["judgment"]["binding"],
                        effectId=binding["effectId"],
                        raw=raw,
                    ),
                )
            if pending["purpose"] == "scope-intent" and (workspace / "hold-scope-raw").exists():
                hold_receipt_once("scope-raw-complete", dict(requestId=pending["requestId"],effectId=binding["effectId"],raw=raw))
            if pending["purpose"] == "execution" and (workspace / "hold-execution-raw").exists():
                hold_receipt_once("execution-raw-complete", dict(requestId=pending["requestId"], effectId=binding["effectId"], raw=raw))
            if pending["purpose"] == "trace" and (workspace / "semantic-late-output").exists():
                hold_receipt_once(
                    "semantic-late-complete",
                    dict(
                        requestId=pending["requestId"],
                        questionBinding=pending["judgment"]["binding"],
                        binding=binding,
                        effectId=binding["effectId"],
                    ),
                )
            return raw

        run_invocations.invoke = hold_semantic_reply

    continuation_save = run_loop.save

    def hold_continuation(store, record):
        effect = (run_loop.cursor(store) or {}).get("effect") or {}
        if effect.get("kind") == "judgment-continuation" and not record.get("effect") and (workspace / "hold-continuation").exists():
            name = "delivery-complete" if record.get("step") == "validation-complete" else "continuation-complete"
            hold_receipt_once(name, dict(effectId=effect["id"]))
        return continuation_save(store, record)

    run_loop.save = hold_continuation

    from codex_harness.core import planner_candidate
    actual_deliver = planner_candidate.deliver

    def hold_delivery(workspace, request, run, publication=None):
        if (Path(workspace) / "hold-continuation").exists() and run.get("semanticValidation", {}).get("state") == "PASS":
            hold_receipt_once("delivery-pending", dict(jobId=request["jobId"]))
        return actual_deliver(workspace, request, run, publication)

    planner_candidate.deliver = hold_delivery
    actual_revalidate = planner_candidate.revalidate

    def hold_candidate(store, job_id, request_id):
        result = actual_revalidate(store, job_id, request_id)
        if (workspace / "hold-continuation").exists() and result["state"] == "PASS":
            hold_receipt_once("candidate-pending", dict(jobId=job_id))
        if (workspace / "hold-revalidation").exists():
            hold_receipt_once("revalidation-pending", dict(jobId=job_id))
        return result

    planner_candidate.revalidate = hold_candidate

    if receipt_boundaries:
        from codex_harness.core import acceptance_recovery
        from codex_harness.core.planner_acceptance import AcceptanceOwner

        original_drain = AcceptanceOwner.drain

        def hold_completed_acceptance(owner, store):
            result = original_drain(owner, store)
            if owner.active:
                row = store.connection.execute(
                    "SELECT state,result_json FROM acceptance_attempts WHERE id=?", (owner.active,)
                ).fetchone()
                if row and row["state"] == "completed":
                    actual = json.loads(row["result_json"])
                    hold_receipt_once(
                        "acceptance-scenarios-complete",
                        dict(attemptId=owner.active, runId=actual["id"]),
                    )
            return result

        AcceptanceOwner.drain = hold_completed_acceptance
        original_capture = acceptance_recovery.ReviewAttempt.capture

        def hold_sealed_review(attempt, reviewer, shard, launch, executable, response):
            result = original_capture(attempt, reviewer, shard, launch, executable, response)
            if response is not None and not attempt.abandoned:
                returned = acceptance_recovery._events(
                    attempt.store, acceptance_recovery.COMPLETED, attempt.id
                )
                slots = {slot["id"] for slot in attempt.header["slots"]}
                if slots and {row["slot"] for _, row in returned} == slots:
                    hold_receipt_once(
                        "acceptance-review-sealed",
                        dict(
                            attemptId=attempt.id,
                            runId=attempt.header["run"]["id"],
                            slots=sorted(slots),
                            receiptEventIds=[key for key, _ in returned],
                        ),
                    )
            return result

        acceptance_recovery.ReviewAttempt.capture = hold_sealed_review
        original_offer_c = run_loop._offer_c

        def hold_before_c(store, run):
            hold_receipt_once(
                "acceptance-review-admitted",
                dict(
                    reviewIds=[
                        row[0]
                        for row in store.connection.execute(
                            "SELECT id FROM acceptance_reviews ORDER BY rowid"
                        )
                    ],
                    runIds=[
                        row[0]
                        for row in store.connection.execute(
                            "SELECT id FROM acceptance_runs ORDER BY rowid"
                        )
                    ],
                ),
            )
            return original_offer_c(store, run)

        run_loop._offer_c = hold_before_c
        original_roundtable = roundtable.run_roundtable

        def hold_completed_plan_review(store, *args, **options):
            result = original_roundtable(store, *args, **options)
            hold_receipt_once(
                "plan-review-complete",
                dict(
                    roundId=result["roundId"],
                    reviewRunIds=[
                        row[0]
                        for row in store.connection.execute(
                            "SELECT id FROM review_runs WHERE round_id=? ORDER BY rowid",
                            (result["roundId"],),
                        )
                    ],
                ),
            )
            return result

        roundtable.run_roundtable = hold_completed_plan_review

    filename = "index.html" if surface == "web" else "hashslip.py"
    actual_popen = subprocess.Popen
    actual_audit = audit_process.run_audit_process
    actual_review = roundtable._run
    actual_run = dispatch.run_plan
    actual_write = dispatch._write_record
    denial_fault = (workspace / "denial-fault.json").exists()
    long_stream = (workspace / "long-stream").exists()

    death_path = workspace / "controller-death.json"
    death = json.loads(death_path.read_text()) if death_path.exists() else None

    def death_boundary(stage):
        import socket

        with socket.create_connection(("127.0.0.1", death["port"])) as boundary:
            boundary.sendall((json.dumps(dict(stage=stage, jobId=job_id, segmentId=segment_id)) + "\n").encode())
            assert boundary.recv(1) == b"!", "Controller death was not acknowledged"
        os._exit(73)

    resume_fault = workspace / "resume-fault.json"
    admission_write = planner_jobs.write_record_json_once

    def hold_admission(path, request):
        if path.name == "request.json" and request.get("schema") == planner_jobs.SCHEMA and (workspace / "hold-root-admission").exists():
            hold_receipt_once("root-admission-pending", dict(jobId=request["jobId"]))
        result = admission_write(path, request)
        if path.name == "request.json" and request.get("schema") == planner_jobs.SCHEMA and (workspace / "hold-root-published").exists():
            hold_receipt_once("root-published-pending", dict(jobId=request["jobId"]))
        if path.name == "request.json" and "segmentId" in request and (workspace / "hold-admission").exists():
            hold_receipt_once("admission-pending", dict(jobId=request["jobId"]))
        return result

    planner_jobs.write_record_json_once = hold_admission

    if mode == "worker" and (death and death["afterStarted"] or resume_fault.exists()):
        entry_write = planner_jobs.write_record_json_once

        def hold_segment_entry(path, record):
            entry_write(path, record)
            if path.name == "started.json" and path.parent.name == segment_id:
                if death and death["afterStarted"]:
                    death_boundary("started")  # Job teardown kills this payload before dispatch.
                if resume_fault.exists():
                    import socket

                    fault = json.loads(resume_fault.read_text())
                    admitted = json.loads((path.parent / "request.json").read_text())
                    with socket.create_connection(("127.0.0.1", fault["port"])) as boundary:
                        boundary.sendall((json.dumps(dict(jobId=job_id, segmentId=segment_id, work=admitted["work"])) + "\n").encode())
                        assert boundary.recv(1) == b"!", "Interrupted continuation entry was not acknowledged"
                    os._exit(71)  # This admitted payload entered; it has no dispatch outcome.

        planner_jobs.write_record_json_once = hold_segment_entry

    if mode == "controller":
        from codex_harness.core import planner_recovery

        quiescence_write = planner_jobs.write_record_json_once

        def hold_quiescence(path, receipt):
            if path.name == "quiescence.json" and (workspace / "hold-segment-publication").exists():
                hold_receipt_once("segment-publication", dict(jobId=job_id))
            quiescence_write(path, receipt)
            if path.name == "quiescence.json" and (workspace / "quiescence-fault.json").exists():
                import socket

                fault = json.loads((workspace / "quiescence-fault.json").read_text())
                with socket.create_connection(("127.0.0.1", fault["port"])) as boundary:
                    boundary.sendall((job_id + "\n").encode())
                    assert boundary.recv(1) == b"!", "Quiescence publication was not acknowledged"

        planner_jobs.write_record_json_once = hold_quiescence
        planner_recovery.write_record_json_once = hold_quiescence

    if mode == "worker" and denial_fault:
        from codex_harness.core import dispatch_attempts

        attempt_write = dispatch_attempts._write_record

        def interrupt_denial(path, result):
            attempt_write(path, result)
            if path.name == "result.json" and result.get("status") in dispatch.DENIAL_HELD_STATUSES:
                import socket

                fault = json.loads((workspace / "denial-fault.json").read_text())
                with socket.create_connection(("127.0.0.1", fault["port"])) as barrier:
                    barrier.sendall((job_id + "\n").encode())
                    assert barrier.recv(1) == b"!", "Persisted denial was not acknowledged"
                os._exit(72)  # Result is durable; no future/run projection update can follow.

        dispatch_attempts._write_record = interrupt_denial

    def publish(path, record):
        actual_write(path, record)
        if surface == "web" and path.name == "retained-candidate.json" and record["bead"] == fixture_id(workspace, "B"):
            # Hold the real producer after publication so MCP must consume this
            # transition, rather than usually missing it between status polls.
            end = time.monotonic() + 10
            while not (workspace / "validation-observed").exists() and time.monotonic() < end:
                time.sleep(0.05)

    dispatch._write_record = publish

    if mode == "worker" and (workspace / "packet-fault.json").exists():
        import socket

        original_packet = dispatch.worker_packet.build_packet

        def fault_packet(plan, bead, *args, **kwargs):
            fault_path = workspace / "packet-fault.json"
            if bead["id"] == fixture_id(workspace, "C") and fault_path.exists():
                fault = json.loads(fault_path.read_text())
                segment = planner_jobs.segment_directory(workspace, job_id, segment_id)
                admitted = json.loads((segment / "request.json").read_text())
                # This boundary precedes current["attempt"] assignment. The real
                # dispatch exception handler must publish the failed outcome.
                with socket.create_connection(("127.0.0.1", fault["port"])) as barrier:
                    barrier.sendall((json.dumps(dict(
                        jobId=job_id, work=admitted["work"][bead["id"]]
                    )) + "\n").encode())
                    assert barrier.recv(1) == b"!", "Packet fault was not acknowledged"
                raise OSError("Acknowledged pre-attempt packet failure")
            return original_packet(plan, bead, *args, **kwargs)

        dispatch.worker_packet.build_packet = fault_packet

    def command(role, project, job=None, segment=None):
        return [sys.executable, "-B", str(Path(__file__).resolve()), role, project, surface] + (
            [job, segment] if job else []
        )

    def launch(argv, **options):
        if "codex_harness.core.planner_jobs" in argv:
            admitted_job = argv[argv.index("--job-id") + 1]
            argv = command(
                "controller",
                argv[argv.index("--workspace") + 1],
                argv[argv.index("--job-id") + 1],
                argv[argv.index("--segment") + 1],
            )
            child = actual_popen(argv, **options)
            if (workspace / "hold-popen").exists():
                hold_receipt_once("popen-pending", dict(jobId=admitted_job))
            return child
        return actual_popen(argv, **options)

    def controller_payload(argv, **options):
        if any("from codex_harness.core.planner_jobs import execute_worker_job" in token for token in argv):
            if death:
                if not death["afterStarted"]:
                    death_boundary("controller")  # No payload has been spawned.
                threading.Thread(target=death_boundary, args=("controller",), daemon=True).start()
            project, job, segment = argv[-3:]
            marker = workspace / ".codex-harness/popen-pending.json"
            if (workspace / "hold-popen").exists():
                deadline = time.monotonic() + options["timeout"]
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                if json.loads(marker.read_text())["jobId"] == job:
                    while not (planner_jobs._directory(workspace, job) / "stop.json").exists() and time.monotonic() < deadline:
                        time.sleep(.02)
            return actual_audit(command("worker", project, job, segment), **options)
        return actual_audit(argv, **options)

    def write_shelflife(root, phase):
        payload = json.loads((workspace / "scripted-provider.json").read_text(encoding="utf-8"))
        scripted_worker(packet(root, "C" if phase == "contracts" else "B"), root, payload[phase])

    def packet(root, bead):
        bead = fixture_id(workspace, bead)
        record = json.loads((root.parent / "evidence/run.json").read_text(encoding="utf-8"))
        request = Path(record["beads"][bead]["evidenceDir"]) / "attempt/request.json"
        return json.loads(request.read_text(encoding="utf-8"))["prompt"]

    def contracts(root):
        if surface == "shelflife":
            return write_shelflife(root, "contracts")
        (workspace / "contracts-entered").touch()
        if (workspace / "fault-contracts").exists() and "Use an isolated startup path after the failed shared startup" not in packet(root,"C"):
            successor_fault = workspace / "controller-after-failure.json"
            if successor_fault.exists():
                death_path.write_bytes(successor_fault.read_bytes()); successor_fault.unlink()
            raise RuntimeError("Injected worker failure before product completion")
        while (workspace / "hold-contracts").exists():
            time.sleep(0.05)
        first = surface == "cli" and not (workspace / "contracts-attempted").exists()
        if first:
            (workspace / "contracts-attempted").write_text("attempted", encoding="utf-8")
        else:
            (workspace / "valid-contracts-attempted").write_text("attempted", encoding="utf-8")
        if surface == "cli":
            scripted_worker(packet(root, "C"), root, worker_checks())
        scripted_worker(
            packet(root, "C"),
            root,
            {
                filename: "<!doctype html><title>HashSlip</title><h1>HashSlip</h1>"
                if surface == "web"
                else "raise RuntimeError('receipt startup failed')"
                if first
                else "print('Receipt pending')\n"
            },
        )

    def build(root):
        (workspace / "build-entered").touch()
        if surface == "shelflife":
            return write_shelflife(root, "build")
        if surface == "cli":
            scripted_worker(
                packet(root, "B"),
                root,
                {
                    "checks/verify_hashslip.py": "# Exact binary receipt; catches missing CLI/data loss; scenarios alone had no worker test.\n"
                    "import subprocess, sys\nfrom pathlib import Path\nfrom receipt_helper import payload, check_receipt\n"
                    "command = Path(__file__).resolve().parents[1] / 'hashslip.py'\n"
                    "data = payload()\n"
                    "import hashslip  # Tests-first: the product module is absent on baseline.\n"
                    "run = subprocess.run([sys.executable, '-B', str(command)], input=data, capture_output=True, timeout=5)\n"
                    "check_receipt(data, run)\n",
                    "checks/receipt_helper.py": "import hashlib, json\nfrom pathlib import Path\n"
                    "def payload():\n    assert sorted(p.name for p in Path(__file__).parent.glob('*.json')) == ['input.json'], 'retired fixture still present'\n"
                    "    return bytes(json.loads((Path(__file__).parent / 'input.json').read_text()))\n"
                    "def check_receipt(data, run):\n    assert run.returncode == 0 and not run.stderr, run.stderr\n"
                    "    assert json.loads(run.stdout) == dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), label=None)\n",
                    "checks/input.json": "[0, 255, 65]\n",
                    "checks/retired.json": None,
                },
            )
            if not (workspace / "contracts-attempted").exists():
                (workspace / "contracts-attempted").write_text("attempted", encoding="utf-8")
                return scripted_worker(
                    packet(root, "B"),
                    root,
                    {filename: "raise RuntimeError('receipt startup failed')\n"},
                )
            (workspace / "valid-contracts-attempted").write_text("attempted", encoding="utf-8")
        scripted_worker(
            packet(root, "B"),
            root,
            {filename: (ROOT / "tests/fixtures/efficiency" / filename).read_text(encoding="utf-8")},
        )
        if (workspace / "stub-implementation").exists():
            source = (ROOT / "tests/fixtures/efficiency/hashslip.py").read_text(encoding="utf-8")
            known = [b"",b"hello",b"hello\n","caf\u00e9".encode(),b"\x00\xff\x80\n",b"\x00\xffA"]
            start,end = source.index("    digest = hashlib.sha256()"),source.index("    json.dump(")
            source = source[:start]+"    data = sys.stdin.buffer.read()\n    byte_count = len(data)\n    HARDCODED_RECEIPTS = "+repr({v:hashlib.sha256(v).hexdigest() for v in known})+"\n\n"+source[end:]
            source = source.replace("digest.hexdigest()","HARDCODED_RECEIPTS.get(data, 'HARDCODED_RECEIPT')")
            scripted_worker(packet(root,"B"),root,{filename:source,**{"checks/context-"+str(i)+".txt":"# verifier context\n"*2048 for i in range(4)}})
        if surface == "web":
            with (root / filename).open("a", encoding="utf-8") as out:
                # A normal larger document must not overflow adapter stdout when
                # request evidence captures its body; the earlier tiny page hid it.
                out.write("<!--" + "page content " * 2048 + "-->")
                out.write(
                    "<style>h1{background:linear-gradient(white,#fff)}</style>"
                    "<input id='note' aria-label='Note'><button id='save' onclick=\"fetch('/api/state',{method:'POST',body:JSON.stringify({note:document.querySelector('#note').value})}).then(()=>this.textContent='Saved')\">Save</button>"
                    "<button id='overconsume' onclick=\"fetch('/overconsume').then(()=>this.textContent='Rejected')\">Overconsume</button>"
                    "<button id='happy' onclick=\"fetch('/overconsume').then(()=>this.textContent='Happy')\">Happy path</button>"
                    "<button id='broken' onclick=\"fetch('/broken').then(()=>this.textContent='Broken')\">Break</button>"
                    "<button id='delete' onclick=\"fetch('/api/state',{method:'DELETE'}).then(()=>this.textContent='Deleted')\">Delete</button>"
                    "<button id='empty' onclick=\"location.href='/empty'\">Empty navigation</button>"
                    "<button id='cached' onclick=\"fetch('/cached').then(()=>this.textContent='Cached')\">Read cache</button>"
                    "<button id='disconnect' onclick=\"fetch('/disconnect').catch(()=>this.textContent='Disconnected')\">Disconnect</button>"
                    "<button id='truncated200' onclick=\"fetch('/truncated200').then(r=>r.text()).catch(()=>this.textContent='Truncated 200')\">Incomplete success</button>"
                    "<button id='truncated409' onclick=\"fetch('/truncated409').then(r=>r.text()).catch(()=>this.textContent='Truncated 409')\">Incomplete conflict</button>"
                    "<button id='read-seed' onclick=\"fetch('/seed').then(r=>r.json()).then(d=>document.querySelector('#seeded').textContent=d.note)\">Read seeded note</button><p id='seeded'></p>"
                    "<form method='post' action='/submit'><input type='hidden' name='note' value='Posted'><button id='post-redirect'>Submit form</button></form>"
                    "<button id='failed-happy' onclick=\"fetch('/broken').then(()=>this.textContent='Failed happy')\">Another happy path</button>"
                )
            scripted_worker(
                packet(root, "B"),
                root,
                {
                    "serve.py": served_product(),
                    "serve_wrapper.py": "import subprocess,sys; subprocess.Popen([sys.executable,'-B','serve.py']).wait()\n",
                    "seed_check.py": "import csv,json,sys; from pathlib import Path; assert list(csv.DictReader(Path(sys.argv[1]).open())) == [{'name':'Tea','quantity':'2'}]; print(json.loads(Path('seed.json').read_text())['note'])\n",
                },
            )

    def provider_payload():
        payload_path = workspace / "scripted-provider.json"
        if surface == "parallel-http":
            payload_path = workspace / ".codex-harness" / ("http-permit-" + job_id + ".json")
            deadline = time.monotonic() + 30
            while not payload_path.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert payload_path.exists(), (
                "Exact HTTP worker job has no scripted provider permit: " + str(payload_path)
            )
        return json.loads(payload_path.read_text(encoding="utf-8"))

    if surface in {"shelflife", "parallel-http"} or denial_fault or long_stream:
        from codex_harness.core.provider_launch import ProviderNormalLaunch

        class ScriptedWorker:
            def provider_normal_command(self, **arguments):
                if (
                    surface == "parallel-http"
                    and "Your job: implement bead B." in arguments["prompt"]
                    and provider_payload()["deadlinePair"]
                ):
                    until = time.monotonic() + 30
                    while (
                        not (workspace / ".codex-harness/deadline-child").exists()
                        and time.monotonic() < until
                    ):
                        time.sleep(0.05)
                    assert (workspace / ".codex-harness/deadline-child").exists()
                    time.sleep(4)  # B's independently approved deadline begins after A's.
                return ProviderNormalLaunch(
                    cli="codex",
                    command=tuple(command("reply", str(workspace), job_id, segment_id)),
                    protections=("Scripted local provider; no actual provider acceptance",),
                    stdin_text=arguments["prompt"],
                )

        from codex_harness.core import dispatch_attempts

        dispatch_attempts.get_adapter = lambda _: ScriptedWorker()

    def workers(plan, **options):
        if surface in {"parallel-http", "shelflife"} or denial_fault or long_stream:
            return actual_run(plan, **options)
        from codex_harness.core import graph

        selected = graph.load_plan(Path(plan)).beads
        workers = {fixture_id(workspace, "C"): contracts, fixture_id(workspace, "B"): build}
        return actual_run(plan, **options, fake_workers={key: workers[key] for key in selected})

    def provider_reply(launch, text):
        # Telemetry follows the exact command admitted by the canonical owner;
        # a hardcoded Sol label hid refreshed model grants in the authority flow.
        command = list(launch.command)
        model = command[command.index("--model") + 1]
        effort = next(
            (
                arg.split("=", 1)[1].strip('"')
                for arg in command
                if arg.startswith("model_reasoning_effort=")
            ),
            None,
        )
        if effort is None and "--effort" in command:
            effort = command[command.index("--effort") + 1]
        result = protocol(launch.cli, text=text, model=model, effort=effort)
        if launch.cli == "claude" and "--json-schema" in command:
            events = [json.loads(line) for line in result[1].splitlines() if line.strip()]
            for event in events:
                if event.get("type") == "result":
                    try: event["structured_output"] = json.loads(text)
                    except ValueError: event["structured_output"] = text
            result = (result[0], "\n".join(json.dumps(e) for e in events), result[2])
        return result

    def review(launch, *args, **options):
        prompt = launch.stdin_text if launch.stdin_text is not None else launch.command[launch.command.index("-p") + 1]
        if receipt_boundaries:
            with (workspace / ".codex-harness/scripted-review-invocations.jsonl").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(
                    json.dumps(
                        dict(
                            acceptance="## Read-only candidate evidence" in prompt,
                            semantic="\n## Run question\n" in prompt,
                            packetSha256=hashlib.sha256(prompt.encode()).hexdigest(),
                        )
                    )
                    + "\n"
                )
        if "\n## Run question\n" in prompt:
            if (workspace / "semantic-host-refusal").exists():
                from dataclasses import replace
                return actual_review(replace(launch, command=(sys.executable, "-B", "-c",
                    "import sys; sys.stderr.write('Provider host denied this call: enable access in the host'); sys.exit(77)")), *args, **options)
            from codex_harness.core.plan_store import open_plan_store_readonly
            from codex_harness.core.status_api import run_baton
            with open_plan_store_readonly(workspace) as store:
                pending = run_baton(store)["question"]
            supplied = json.loads(prompt.split("\n## Run question\n", 1)[1])
            assert "judgmentId" not in supplied and "binding" not in supplied
            assert supplied["facts"]["vision"] and supplied["answerSchema"] == pending["answerSchema"]
            if pending["family"] == "author":
                value = dict(
                    option="ready",
                    rationale="Scripted planner authors the retained dream",
                    content=plan_content(surface) if pending["purpose"] == "understanding" else {},
                )
            elif pending["family"] == "direction":
                value = dict(
                    option=http_direction(pending["purpose"]) if surface in {"parallel-http", "shelflife"} else "continue",
                    rationale="Keep the observed outcome for the fixture's explicit next decision",
                    content={},
                )
            else:
                value = judge_reply(pending)
            retry_script = workspace / "semantic-retry-script.json"
            automatic = retry_script.exists() or surface in {"parallel-http","shelflife"}
            raw = provider_answer(workspace, pending, value) if automatic else ""
            if surface in {"parallel-http","shelflife"} and pending["family"] == "author" and pending["purpose"] != "understanding":
                raw = json.dumps(dict(records=[],remove=[],targets=[],scenarios=[],rationale="The canonical HTTP plan is already authored; preserve current requirements and ownership",reply="Retain the complete current HTTP plan"))
            if retry_script.exists():
                script = json.loads(retry_script.read_text("utf-8"))
                history_path = workspace / ".codex-harness/scripted-semantic-responses.jsonl"
                history = (
                    [json.loads(line) for line in history_path.read_text("utf-8").splitlines()]
                    if history_path.exists()
                    else []
                )
                previous = [row for row in history if row["requestId"] == pending["requestId"]]
                malformed = script.get(pending["purpose"], 0)
                if len(previous) < malformed:
                    raw = "{"
                elif pending["family"] == "direction" and pending["purpose"] != "user-direction":
                    raw = provider_answer(workspace, pending, dict(option="pause", rationale="Malformed semantic output exhausted the canonical two fixes; retain it for the user", content={}))
            if not automatic:
                from dataclasses import replace
                answer_file = workspace / ".codex-harness" / ("provider-answer-" + pending["requestId"] + "-" + str(pending["judgment"]["fixesIssued"]) + ".txt")
                offered = workspace / ".codex-harness" / ("provider-offered-" + Path(args[0]).name + ".json")
                event = dict(requestId=pending["requestId"], fixesIssued=pending["judgment"]["fixesIssued"],
                             effectId=Path(args[0]).name, answerFile=str(answer_file), packet=supplied)
                offered.write_text(json.dumps(event), encoding="utf-8")
                offered.with_suffix(".ready").touch()
                body = """import json,sys,time
from pathlib import Path
answer=Path(sys.argv[1])
end=time.monotonic()+170
supplied=Path(str(answer)+".supplied")
while not supplied.exists() and time.monotonic()<end:
    time.sleep(.02)
if not supplied.exists(): raise SystemExit(3)
raw=answer.read_text(encoding='utf-8')
provider=sys.argv[2]
if provider=='codex': result=dict(type='item.completed',item=dict(type='agent_message',text=raw))
elif provider=='claude':
    try: value=json.loads(raw)
    except ValueError: value=raw
    result=dict(type='result',is_error=False,structured_output=value)
else: result=dict(type='assistant.message',data=dict(content=raw))
print(json.dumps(result),flush=True)
if provider=="codex": print(json.dumps(dict(type="turn.completed")),flush=True)
"""
                return actual_review(replace(launch, command=(sys.executable, "-B", "-c", body, str(answer_file), launch.cli)), *args, **options)
            from dataclasses import replace

            if pending["purpose"] == "plan-outline" and surface in {"parallel-http","shelflife"}:
                raw = json.dumps(dict(**{k:[] for k in pending["answerSchema"]["properties"] if k not in {"rationale","reply"}},rationale="Retain the existing complete plan",reply="The current plan remains complete."))
            response = provider_reply(launch, raw)
            body = (
                "import sys; sys.stdout.buffer.write("
                + repr(response[1].encode())
                + "); sys.stderr.buffer.write("
                + repr(response[2].encode())
                + ")"
            )
            # Exercise the existing bounded provider path and admission guard;
            # the child emits scripted semantics, never a fabricated execution result.
            actual_return = actual_review(
                replace(launch, command=(sys.executable, "-B", "-c", body)), *args, **options
            )
            if retry_script.exists():
                with history_path.open("a", encoding="utf-8") as history_file:
                    history_file.write(
                        json.dumps(
                            dict(
                                requestId=pending["requestId"],
                                binding=pending["binding"],
                                role=pending["role"],
                                family=pending["family"],
                                purpose=pending["purpose"],
                                fixesIssued=pending["judgment"]["fixesIssued"],
                                raw=raw,
                            )
                        )
                        + "\n"
                    )
            if pending["purpose"] == "trace" and (workspace / "semantic-late-output").exists():
                # FIX17: lease structure is kickoff-owned. Fault the existing
                # clock after real zero exit/cleanup; never supply a proof result.
                from datetime import UTC
                from codex_harness.core import run_invocations, run_ownership
                run_ownership.datetime = AwakeClock
                (workspace / "clock-shift").write_text(str(float(args[1]) + 1))
                run_invocations._now = lambda: AwakeClock.now(UTC).isoformat()
            return actual_return
        if (workspace / "review-auth-fault").exists() and (launch.cli == "claude" or (workspace / "review-auth-fault").read_text() == "all"):
            return 1, json.dumps([dict(type="assistant", error="authentication_failed", is_api_error_message=True,
                message=dict(content=[dict(type="text", text="Failed to authenticate: OAuth session expired and could not be refreshed")])),
                dict(type="result", is_error=True, result="Failed to authenticate: OAuth session expired and could not be refreshed")]), ""
        if (workspace / "review-brief.txt").exists():
            assert (workspace / "review-brief.txt").read_text("utf-8") in prompt, "Reviewer lost the original brief"
            question = json.loads(prompt.split("\n## Review question\n", 1)[1])["question"]
            assert "can this plan deliver the whole accepted dream" in question
            assert "do not request them or return inconclusive because they are absent" in question
            assert "plan and candidate" not in question, "Pre-B review asks for a built candidate"
        if mode == "server" and surface == "cli" and (workspace / "inject-review-crash").exists():
            # A real provider wait survives a lost client until its process owner
            # is killed. Recovery must not reuse that abandoned review's evidence.
            (workspace / ".codex-harness" / "review-crash-started.ready").write_text(
                "started", encoding="utf-8"
            )
            actual_audit(
                [sys.executable, "-B", "-c", "import time; time.sleep(600)"],
                cwd=workspace,
                timeout=900,
            )
        if "## Read-only candidate evidence" in prompt:
            from codex_harness.core.plan_store import open_plan_store_readonly

            with open_plan_store_readonly(workspace) as store:
                tier = approvals.current(store, "B")["subject"]["reviewTier"]
                assert f"## Review tier: {tier}" in prompt
                assert "catalog: " + approvals.catalog_status(store) in prompt
        text = "[]"
        if mode == "server" and surface == "cli" and (workspace / "inject-review-note").exists():
            (workspace / "review-output-sent").write_text("sent")
            payload = json.loads((ROOT / "tests/fixtures/efficiency/run26.json").read_text("utf-8"))
            text = json.dumps([payload["headingFinding"]])
        if receipt_boundaries and "## Read-only candidate evidence" in prompt:
            request = json.loads(prompt.split("\n## Review question\n", 1)[1])
            facts = next(ref["facts"] for ref in request["evidence"] if "facts" in ref)
            if facts["reviewer"]["name"] == "Reviewer2":
                text = json.dumps(
                    [
                        dict(
                            node_id="R4",
                            severity="note",
                            finding="Confirm the delivered absolute-path handover against the retained GREEN.",
                            suggestion="Compare the retained receipt and delivery, then record the disposition before C.",
                        )
                    ]
                )
        verdict = "positive"
        if (workspace / "review-plan-defect").exists() and "--verify-python-3.12" not in prompt:
            request = json.loads(prompt.split("\n## Review question\n", 1)[1])
            facts = next(ref["facts"] for ref in request["evidence"] if "facts" in ref)
            if facts["reviewer"]["name"] == "Reviewer1":
                verdict = "negative"
        raw = scripted_review_reply(launch,
            [dict(f,node_id=fixture_id(workspace,f["node_id"])) for f in json.loads(text)],
            verdict=verdict, gaps=["The planned journey does not explicitly verify Python 3.12; include runtime-version verification in the command"] if verdict == "negative" else [],
            rationale="Independent plan assessment: required behaviors are covered; trace note is advisory" if verdict == "positive" else "Repair verification within the accepted brief")
        if launch.cli == "copilot" and (workspace / "copilot-review-retry").exists():
            counter = workspace / ".codex-harness/copilot-review-count"
            count = int(counter.read_text()) if counter.exists() else 0
            counter.write_text(str(count+1))
            if count < 2:
                raw = "{"
        response = provider_reply(launch,raw)

        # One real review launch protects the shared owner; other unchanged
        # semantic replies reuse their existing double instead of repeating it.
        observed_review = workspace / ".codex-harness/review-job-observed"
        if observed_review.exists():
            return response
        from dataclasses import replace

        body = (
            "import sys; sys.stdout.buffer.write("
            + repr(response[1].encode("utf-8"))
            + "); sys.stderr.buffer.write("
            + repr(response[2].encode("utf-8"))
            + ")"
        )
        actual = actual_review(
            replace(launch, command=(sys.executable, "-B", "-c", body)), *args, **options
        )
        observed_review.touch()
        return actual

    subprocess.Popen = launch
    roundtable._run = review_provider._run = review
    dispatch.run_plan = workers
    if mode == "reply":

        def failed_provider_turn(reason):
            """Report a failed native turn before a separate process teardown fault.

            Empty output means unknown launch/audit, so it cannot prove automatic
            recovery of known failed work. The malformed ShelfLife reply retains
            that distinct unknown-stream case.
            """
            from uuid import uuid4

            for event in (
                dict(type="thread.started", thread_id=str(uuid4())),
                dict(type="turn.started"),
                dict(type="turn.failed", error=dict(message=reason)),
            ):
                print(json.dumps(event), flush=True)

        if denial_fault:
            print(json.dumps(dict(type="item.completed", item=dict(
                id="denied-call", type="command_execution", command="scripted forbidden command",
                status="denied", exit_code=1,
            ))), flush=True)
            _, output, stderr = protocol("codex", text="Scripted provider observed a denied command")
            print(output, flush=True)
            print(stderr, file=sys.stderr, flush=True)
            return

        prompt = sys.stdin.read()
        phase = "contracts" if prompt.startswith("semantic-v1 contracts;") else "build"
        payload = None if long_stream else provider_payload()
        if surface == "parallel-http":
            bead = next(b for b in ("A", "B") if f"Your job: implement bead {b}." in prompt)
            if payload["deadlinePair"]:
                if bead == "A":
                    import signal

                    failed_provider_turn("Scripted worker failed; owned deadline cleanup remains")
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    child = actual_popen(
                        [
                            sys.executable,
                            "-B",
                            "-c",
                            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(600)",
                        ]
                    )
                    (workspace / ".codex-harness/deadline-child").write_text(str(child.pid))
                    while True:
                        print("thinking despite deadline", file=sys.stderr, flush=True)
                        time.sleep(0.1)
                (workspace / ".codex-harness/sibling-started").touch()
                while not (workspace / ".codex-harness/sibling-release").exists():
                    print("sibling active", file=sys.stderr, flush=True)
                    time.sleep(0.1)
            if payload["cancelBead"] and bead == "B":
                failed_provider_turn("Scripted worker failed; owned cancellation remains")
                (workspace / ".codex-harness/cancel-provider").touch()
                while True:
                    print("awaiting cancellation", file=sys.stderr, flush=True)
                    time.sleep(0.1)
            if bead in payload["fail"] and bead != "B":
                failed_provider_turn("Scripted worker failed with exit code 7")
                raise SystemExit(7)
            sources = payload["products"][bead]
            if bead in payload["fail"]:
                sources["unexpected.txt"] = "Real product-folder ownership violation\n"
        elif not long_stream:
            sources = payload[phase]
        live = workspace / ".codex-harness/live-provider.json"
        slow_flush = (workspace / "long-stream").exists() and phase == "contracts" and not live.exists()
        if slow_flush:
            # Output-only and CPU-only reasoning precede the delayed final flush;
            # neither activity observation is a stream-completion witness.
            start = time.monotonic()
            while time.monotonic() - start < 3:
                print("thinking", file=sys.stderr, flush=True)
                time.sleep(0.2)
            output_seconds = time.monotonic() - start
            start = time.monotonic()
            while time.monotonic() - start < 3:
                hashlib.sha256(b"reasoning" * 1024).digest()
            live.write_text(
                json.dumps(dict(outputSeconds=output_seconds, cpuSeconds=time.monotonic() - start))
            )
        if surface == "shelflife" and phase == "build":
            if not (workspace / ".codex-harness/truncated-provider").exists():
                (workspace / ".codex-harness/truncated-provider").touch()
                print('{"type": "turn.started"}\n{"type": "turn.com', end="", flush=True)
                return  # Exit normally without ever supplying the missing final bytes.
            if "incomplete: declared verify_http.py missing from candidate" not in prompt:
                sources["verify_http.py"] = None
        if long_stream:
            (contracts if phase == "contracts" else build)(Path.cwd())
        else:
            scripted_worker(prompt, Path.cwd(), sources)
        # r11: the actual provider child consumes dispatch's supported output env.
        # Earlier worker doubles never wrote tool output outside the product.
        artifact = Path(os.environ["PLAYWRIGHT_MCP_OUTPUT_DIR"]) / "browser-console.log"
        artifact.write_text("Scripted browser tool output\n", encoding="utf-8")
        assert not artifact.is_relative_to(Path.cwd())
        _, output, stderr = protocol("codex", text="Scripted worker completed the requested files")
        if slow_flush:
            # Real stdout file, partial JSON and no CPU/output activity beyond the
            # former 120-second window. Restoring any shorter idle kill must fail
            # this same journey, rather than escape an accelerated three-second twin.
            sys.stdout.write(output[:-5])
            sys.stdout.flush()
            flush_started = time.monotonic()
            time.sleep(125)
            sys.stdout.write(output[-5:] + "\n")
            sys.stdout.flush()
            observed = json.loads(live.read_text())
            live.write_text(
                json.dumps(dict(observed, flushSeconds=time.monotonic() - flush_started))
            )
        else:
            print(output)
        print(
            stderr.replace("source-only", "gpt-6.1-sol").replace("Some(High)", "Some(Medium)"),
            file=sys.stderr,
        )
        return
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
        audit_process.run_audit_process = controller_payload
        raise SystemExit(planner_recovery.monitor(workspace, job_id, segment_id))
    else:
        result = planner_jobs.execute_worker_job(workspace, job_id, segment_id)
        raise SystemExit(1 if result["status"] == "failed" else 0)


def scripted_judgment(
    store,
    judgment=None,
    meaning=None,
    verdict="positive",
    gaps=None,
    response=None,
    *,
    rationale="Scripted fixture assessment; no actual provider or human authentication",
    **request,
):
    """Supply a semantic provider response to the canonical persisted 1j owner.

    Protect real subject/evidence binding and its consumers after planner writer
    aliases were retired; never implement a tool compatibility route.
    """
    from codex_harness.core import planner_confirmations as confirmations

    if judgment is None:
        judgment = store.prepare_judgment(**request)
    example = dict(
        fixture_judgment_example(judgment)["responseExample"],
        verdict=verdict,
        rationale=rationale,
        meaning=meaning,
        gaps=gaps or [],
    )
    reply = store.submit_judgment(
        judgment_id=judgment["id"],
        response=response if response is not None else json.dumps(example),
        source="golden:scripted-semantic-provider",
    )
    current = store.judgment(
        judgment_id=judgment["id"],
        **({"candidate": judgment["candidate"]} if judgment.get("candidate") else {}),
    )
    assert current["binding"] == judgment["binding"]
    decision = confirmations.apply_judgment(store, current)
    return dict(reply, current=current, **({"decision": decision} if decision is not None else {}))


def settle_decision(store, outcome, decision="approve"):
    """Consume only explicit fixture meaning against the retained human words."""
    meanings = {
        "vision-expansion": {},
        "user-decision": {"decision": decision},
        "vision-intent": {"impact": "preserved"},
    }
    for _ in range(3):
        if outcome.get("state") != "pending-judgment":
            return outcome
        judgment = outcome["judgment"]
        outcome = scripted_judgment(store, judgment, meanings[judgment["purpose"]])["decision"]
    assert outcome["state"] != "pending-judgment", outcome
    return outcome


@timed
def cold_start_golden(workspace):
    """Replay ShelfLife r10 through installed entry points in fresh -I interpreters.

    The provider host preimports dispatch, hiding the approvals/roundtable cycle.
    Build/install this candidate in a disposable venv, exercise every console entry
    plus module callers, then observe init persistence, registered MCP and removal.
    Source-tree journeys cannot catch missing wheel files or cold installed imports.
    """
    started = time.monotonic()
    workspace.mkdir()
    env = {
        **{
            key: value
            for key, value in os.environ.items()
            if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "UV_PYTHON"}
        },
        "TEMP": str(workspace),
        "TMP": str(workspace),
        "UV_CACHE_DIR": str(workspace / "cache"),
        "UV_PYTHON_DOWNLOADS": "never",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def run(argv, input=""):
        result = subprocess.run(
            argv,
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=workspace,
            env=env,
            timeout=60,
        )
        assert result.returncode == 0, (argv, result.stdout, result.stderr)
        return result.stdout

    run([shutil.which("uv"), "build", str(ROOT), "--wheel", "--out-dir", str(workspace)])
    venv.EnvBuilder(with_pip=False).create(workspace / "installed")
    python = workspace / "installed" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(
        [
            shutil.which("uv"),
            "pip",
            "install",
            "--python",
            str(python),
            "--no-deps",
            str(next(workspace.glob("*.whl"))),
        ]
    )
    project = workspace / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Preserve project instructions.\n", encoding="utf-8")
    baseline = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["scripts"]
    installed_probe = json.loads(
        run(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                "import json; from importlib.metadata import distribution; "
                "import codex_harness.core.seldon_cli as entry; d = distribution('seldon'); "
                "print(json.dumps(dict(scripts={e.name:e.value for e in d.entry_points "
                "if e.group == 'console_scripts'}, distribution=str(d.locate_file('')), "
                "module=entry.__file__)))",
            ]
        )
    )
    installed = installed_probe["scripts"]
    assert installed == scripts
    assert all(
        Path(installed_probe[key]).resolve().is_relative_to((workspace / "installed").resolve())
        for key in ("distribution", "module")
    ), installed_probe
    initialize = json.dumps(
        dict(
            jsonrpc="2.0",
            id=1,
            method="initialize",
            params=dict(
                protocolVersion="2025-06-18",
                capabilities={},
                clientInfo=dict(name="cold-golden", version="1"),
            ),
        )
    )

    def handshake(argv):
        response = json.loads(run(argv, initialize + "\n"))
        assert response["id"] == 1 and response["result"]["serverInfo"], response
        assert response["result"]["protocolVersion"] == "2025-06-18"

    for name in installed:
        # Exercise the real installed console launcher. Independent isolated
        # module children below protect cold imports without the provider host.
        launcher = python.parent / (name + ".exe" if os.name == "nt" else name)
        assert launcher.is_file(), "Installed console launcher missing: " + str(launcher)
        argv = [str(launcher)]
        if name == "seldon-mcp":
            handshake([*argv, "--workspace", str(project)])
        else:
            assert "usage:" in run([*argv, "--help"])
    for module in ("seldon_cli", "plan_cli"):
        assert "usage:" in run(
            [str(python), "-I", "-B", "-m", "codex_harness.core." + module, "--help"]
        )
    for module in ("seldon_mcp", "mcp_planner"):
        handshake(
            [
                str(python),
                "-I",
                "-B",
                "-m",
                "codex_harness.core." + module,
                "--workspace",
                str(project),
            ]
        )
    cli = [str(python), "-I", "-B", "-m", "codex_harness.core.seldon_cli", "init", str(project)]
    run([*cli, "--host", "codex", "--approval-mode", "prompt", "--yes"])
    config = tomllib.loads((project / ".codex/config.toml").read_text("utf-8"))["mcp_servers"][
        "seldon"
    ]
    assert Path(config["command"]).resolve() == python.resolve()
    assert config["default_tools_approval_mode"] == "prompt"
    handshake([config["command"], *config["args"]])
    assert (project / "SELDON.md").is_file() and (project / ".codex-harness/.seldon-init").is_file()
    run([*cli, "--check"])
    run([*cli, "--remove", "--yes"])
    assert (
        not (project / "SELDON.md").exists()
        and not (project / ".codex-harness/.seldon-init").exists()
    )
    retained = tomllib.loads((project / ".codex/config.toml").read_text("utf-8"))["mcp_servers"][
        "seldon"
    ]
    assert not {"command", "args", "env"} & retained.keys()
    assert retained["default_tools_approval_mode"] == "prompt"
    assert all((project / p).read_bytes() == data for p, data in baseline.items())
    print(
        f"golden cold startup: {len(installed)} installed entries; init/check/handshake/remove; seconds={time.monotonic() - started:.2f}"
    )


@timed
def planning_intent_golden(workspace):
    """Protect intake→Lock→own words→reload and complete-field narrowing.

    Catch count-based ceremony, keyword consent and hidden intent changes outside
    the former field subset. Stage 1a proves trace links, not semantic authority;
    1j proves judgment storage, not canonical intake/consent consumers.
    """
    from copy import deepcopy

    from tests.python.core.semantic_wiring_support import node

    from codex_harness.core.plan_store import open_plan_store

    workspace.mkdir(parents=True)
    store = open_plan_store(workspace)

    def reload():
        nonlocal store
        store.close()
        store = open_plan_store(workspace)

    def words(card, text):
        return confirmations.complete_chat_confirmation(
            store,
            gate=card["gate"],
            request_id=card["request_id"],
            expected_revision=card["plan_revision"],
            quoted_message=text,
            quote_sha256=hashlib.sha256(text.encode()).hexdigest(),
            actor_label="scripted owner",
        )

    try:
        store.upsert_node(
            dict(
                id="V1",
                kind="vision",
                title="sensor",
                text="Tune both existing sensor channels",
                parents=[],
                fields=dict(planMode="semantic-v1"),
            )
        )
        existing = node("EX", "feature", ["V1"])
        existing["text"] = "Read both sensor channels"
        store.upsert_node(node=existing)
        ref = dict(id="EX", revision=store.spine().revision("EX"))
        evidence = node(
            "BASE",
            "result",
            ["EX"],
            evidence=dict(
                itemRef=ref,
                candidate="retained-baseline",
                source=dict(reference="fixture:baseline", realProvider=False, historical=True),
                observation=dict(reference="fixture:two-channel-readback", outcome="passed"),
                state="integrated",
            ),
        )
        store.upsert_node(node=evidence)
        template = node(
            "I1",
            "decision",
            ["V1"],
            schema="dot-intake-v1",
            requestKind="new",
            sizeEstimate="small",
            delta=dict(
                summary="Calibrate retained sensors",
                nodeIds=[],
                outcome="Retain readback during calibration",
                successCriteria=["Both channels remain readable"],
                ownershipAreas=["calibration"],
                interfaceIds=[],
            ),
            ambiguity=dict(level="low", reasons=[]),
            consequence=dict(level="ordinary", reversibility="Revert calibration", risks=[]),
            dependencyBreadth=dict(producerIds=[], crossWaveContracts=[]),
            constraints=dict(runtime=[], security=[]),
            rationale="Improve calibration while retaining readback",
            assumptions=[],
            expectedSupervision="Review measured behavior before acceptance",
            nextGate="Vision Lock",
            reviewerDecisionId="RS1",
            confirmation=None,
            supersedes=None,
            adaptation=None,
        )
        template["fields"].update(
            sizeEstimate="large",
            recipe="slim: direct trace and one review",
            requestKind="refactor",
            ambiguity=dict(level="high", reasons=["Calibration not measured"]),
            dependencyBreadth=dict(
                producerIds=["sensor", "analyser"], crossWaveContracts=["readback"]
            ),
        )
        route = store.record_intake(node=template, expected_spine_revision=store._revision())[
            "routing"
        ]
        assert route["state"] == "pending-judgment" and route["size"] is None
        scripted_judgment(
            store,
            route["judgment"],
            dict(size="tiny", process="Direct requirement/test trace, one bounded review"),
        )
        route = store.record_intake(node=template, expected_spine_revision=store._revision())[
            "routing"
        ]
        assert (
            route["size"] == "tiny" and "Direct" in route["process"] and route["overrideAvailable"]
        )
        template["fields"]["sizeOverride"] = dict(
            size="small",
            words="Treat this as small so we review the migration",
            sourceRef="fixture:owner-message-1",
        )
        override = store.record_intake(node=template, expected_spine_revision=store._revision())[
            "routing"
        ]
        scripted_judgment(
            store,
            override["judgment"],
            dict(
                size="tiny", process="Collapsed trace with migration review", overrideAccepted=True
            ),
        )
        reload()
        route = store.record_intake(node=template, expected_spine_revision=store._revision())[
            "routing"
        ]
        assert (
            route["size"] == "small"
            and route["userOverride"]["sourceRef"] == "fixture:owner-message-1"
        )
        facts = route["judgment"]["sourceRefs"][0]["payload"]["facts"]
        assert {n["id"] for n in facts["baseline"]["nodes"]} >= {"EX", "BASE"}
        vision = store.spine().node("V1")
        vision["fields"].update(
            quality_bar="Preserve readback while calibrating",
            done_feels_like="Residual noise meets the agreed metric",
            qualityCriteria=dict(
                ux="Readable channel status",
                performance="Readback under 20ms",
                maintainability="One calibration owner",
                architecture="Existing channel interface",
                observability="Retain metric per iteration",
                failures="Keep last valid calibration",
            ),
            expansion=dict(
                mode="macro and micro",
                reason="One useful upgrade suffices for this small dream",
                upgrades=[
                    dict(
                        id="UP1",
                        text="Retain the best calibration",
                        kind="micro",
                        impact="Recover after a bad iteration",
                        disposition="accept",
                    )
                ],
            ),
            research=dict(
                reason="Optimum is unknown",
                choices=["Measured sweep", "Adaptive search"],
                sourceRef="fixture:research-choice",
            ),
            openLoop=dict(
                metric="Residual noise dB",
                provenance="fixture:owner-metric",
                stopConditions=["Reach -30dB", "Owner stops the search"],
            ),
            brownfield=dict(
                behaviorRefs=["EX"],
                evidenceRefs=["BASE"],
                reason="Map existing readback before changing calibration",
            ),
            operatingEnvelope=dict(channels=[1, 2]),
            targets=dict(readback=dict(entry="readback.py", runtime=sys.executable, argv=[])),
            scenarios=dict(schema="seldon/scenarios-v1", scenarios=[dict(
                id="readback", requirementRefs=["R_UP"], contractRefs=[], setup={},
                actions=[dict(id="read", cli=dict(target="readback", operation="read", argv=[]))],
                observe=[dict(action="read", stdoutContains="channel")], verdict="all",
            )]),
        )
        store.upsert_node(node=vision)
        store.upsert_node(node=node("R_UP", "requirement", ["EX"], expansionRefs=["UP1"]))
        card = confirmations.prepare_chat_confirmation(store, gate="align+vision-lock")
        pending = words(card, "Yes")
        assert pending["state"] == "pending-judgment" and pending["approval_id"] is None
        after_expansion = scripted_judgment(store, pending["judgment"], {})["decision"]
        assert after_expansion["judgment"]["purpose"] == "user-decision"
        scripted_judgment(store, after_expansion["judgment"], {}, verdict="inconclusive")
        reload()
        assert not store.status()["approval"]["granted"]
        quote = after_expansion["judgment"]["sourceRefs"][0]["payload"]["facts"]
        assert quote["words"] == "Yes" and quote["request_id"] == card["request_id"]
        pending = words(card, "Proceed with both channels and keep the best calibration")
        approved = scripted_judgment(store, pending["judgment"], dict(decision="approve"))[
            "decision"
        ]
        assert approved["state"] == "approved" and (not approved["human_identity_verified"])
        reload()
        assert store.status()["approval"]["granted"]
        locked = store.locked_acceptance_spine().node("V1")
        # C26/I2: truncated real output stays pending through receipt -> status.
        # Ordinary short successful output never caught null being displayed as fail.
        from codex_harness.core.acceptance_runs import run_acceptance
        from codex_harness.core.acceptance_views import acceptance_status

        product = workspace / "readback-product"
        product.mkdir()
        (product / "readback.py").write_text("print('channel' * 10000)\n", encoding="utf-8")
        observed = run_acceptance(store, product, subject="oracle")
        assert observed["state"] == "pending-judgment", observed
        scenario = next(s for s in acceptance_status(store)["scenarios"] if s["id"] == "readback")
        assert scenario["lastResult"] == "pending-judgment", scenario
        assert locked["fields"]["openLoop"]["provenance"] == "fixture:owner-metric"
        from codex_harness.core.planner_vision import expansion_status

        assert (
            expansion_status(locked, downstream_nodes=store.list_nodes())[
                "untracedAcceptedUpgradeRefs"
            ]
            == []
        )
        narrowed = deepcopy(locked)
        narrowed["fields"]["operatingEnvelope"]["channels"] = [1]
        store.upsert_node(node=narrowed)
        changed = dict(judgment=approvals.intent_assessment(store, narrowed, prepare=False))
        assert changed["judgment"]["status"] == "pending-judgment"
        assert not store.status()["approval"]["granted"]
        scripted_judgment(store, changed["judgment"], dict(impact="narrowed"))
        card = confirmations.prepare_chat_confirmation(store, gate="align+vision-lock")
        assert (
            card["deviation"]["impact"] == "narrowed"
            and "Proposed charter deviation" in card["card"]
        )
        assert any("operatingEnvelope" in d["field"] for d in card["diff"])
        final = settle_decision(store, words(card, "I approve the deviation: drop channel two"))
        assert final["state"] == "approved"
        reload()
        assert store.locked_acceptance_spine().node("V1")["fields"]["operatingEnvelope"][
            "channels"
        ] == [1]
        assert store.status()["approval"]["granted"]
        print(
            "golden planning: tiny process; explicit size override; one accepted upgrade; ambiguous consent pending; visible narrowing approved"
        )
    finally:
        store.close()


@timed
def fixture_ownership_golden(product):
    """Protect external fixture ownership through real CSV -> SQLite -> HTTP.

    Earlier setup overwrote snapshot code or supplied neutral-extension source;
    suffix and argv membership checks then rejected legitimate importer data.
    """
    from copy import deepcopy

    from codex_harness.core.planner_candidate import observation_files

    product.mkdir()
    (product / "index.html").write_text("<h1>Shelf items</h1>", encoding="utf-8")
    (product / "app.py").write_text(
        "raise RuntimeError('candidate source must remain unchanged')\n", encoding="utf-8"
    )
    (product / "importer.py").write_text(
        "import csv,sqlite3,sys\nfrom pathlib import Path\nassert Path(sys.argv[4]).read_text(encoding='utf-8') == '<h1>Shelf items</h1>'\nrows=list(csv.DictReader(Path(sys.argv[1]).open()))\nwith sqlite3.connect(sys.argv[3]) as db:\n db.execute('create table items (name text,quantity integer)')\n db.executemany('insert into items values (?,?)',[(r['name'],int(r['quantity'])) for r in rows])\nprint('imported',len(rows))\n",
        encoding="utf-8",
    )
    (product / "server.py").write_text(
        "import json,os,sqlite3,sys\nfrom http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer\nclass Handler(SimpleHTTPRequestHandler):\n def do_GET(self):\n  if self.path!='/items': return super().do_GET()\n  with sqlite3.connect(sys.argv[1]) as db: rows=db.execute('select name,quantity from items').fetchall()\n  body=json.dumps(rows).encode(); self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)\nThreadingHTTPServer(('127.0.0.1',int(os.environ['SELDON_PORT'])),Handler).serve_forever()\n",
        encoding="utf-8",
    )
    (product / "receipt_module").mkdir()
    (product / "receipt_module/__init__.py").write_text("", encoding="utf-8")
    (product / "receipt_module/__main__.py").write_text("from . import loader\n", encoding="utf-8")
    (product / "receipt_module/loader.py").write_text("import importer\n", encoding="utf-8")
    spec = dict(
        targets={
            "web": dict(
                kind="web",
                entry="index.html",
                server=dict(
                    entry="server.py",
                    runtime=sys.executable,
                    argv=[{"input": "{data}/pantry.sqlite3"}],
                    readyPath="/items",
                    readyWithin=5,
                ),
            )
        },
        scenarios=dict(
            schema="seldon/scenarios-v1",
            scenarios=[
                dict(
                    id="fixtures",
                    requirementRefs=["R1"],
                    contractRefs=[],
                    setup=dict(
                        dirs=["{data}/fixtures"],
                        files=[
                            dict(path="{data}/fixtures/items.csv", utf8="name,quantity\nTea,2\n"),
                            dict(path="{data}/blob.bin", base64="AAEC"),
                        ],
                    ),
                    actions=[
                        dict(
                            id="import",
                            cwd="receipt_module",
                            process=dict(
                                entry="importer.py",
                                runtime=sys.executable,
                                argv=[
                                    {"input": "{data}/fixtures/items.csv"},
                                    "--db",
                                    {"input": "{data}/pantry.sqlite3"},
                                    {"path": "index.html", "relative": True},
                                ],
                            ),
                        ),
                        dict(
                            id="read",
                            http=dict(
                                target="web",
                                method="GET",
                                path="/items",
                                statusEquals=200,
                                jsonEquals=[["Tea", 2]],
                            ),
                        ),
                        dict(
                            id="not-served",
                            http=dict(
                                target="web",
                                method="GET",
                                path="/DATA/fixtures/items.csv",
                                statusEquals=404,
                            ),
                        ),
                    ],
                    observe=[
                        dict(action="import", exitEquals=0, stdoutEquals="imported 1\n"),
                        dict(action="read"),
                        dict(action="not-served"),
                        dict(path="{data}/blob.bin", fileBytesEquals="\x00\x01\x02"),
                    ],
                    verdict="all",
                )
            ],
        ),
    )
    from tests.python.core.semantic_wiring_support import node

    from codex_harness.core.plan_store import open_plan_store

    plan_root = product.parent / "fixture-plan"
    plan_root.mkdir()
    with open_plan_store(plan_root) as store:
        store.upsert_node(node("V1", "vision", [], planMode="semantic-v1"))
        vision = store.spine().node("V1")
        vision["text"] = "Import CSV and display shelf items over HTTP"
        vision["fields"].update(targets=spec["targets"], scenarios=spec["scenarios"])
        apply_content(
            store,
            dict(nodes=[node("F1", "feature", ["V1"]), node("R1", "requirement", ["F1"]), vision]),
        )
        stored = store.spine().node("V1")["fields"]
        spec = dict(targets=stored["targets"], scenarios=stored["scenarios"])

    def preview(document):
        (product / "SCENARIOS.json").write_text(json.dumps(document), encoding="utf-8")
        before = observation_files(product)
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "codex_harness.core.semantic_runner",
                "--preview",
                "SCENARIOS.json",
                "fixtures",
            ],
            cwd=product,
            capture_output=True,
            text=True,
            timeout=45,
            check=True,
        )
        assert observation_files(product) == before
        return json.loads(result.stdout)

    invalid = deepcopy(spec)
    invalid["targets"]["fixture"] = dict(entry="{data}/app.py", argv=[])
    scenario = invalid["scenarios"]["scenarios"][0]
    scenario["setup"] = dict(files=[dict(path="{data}/app.py", utf8="print('fixture-code')\n")])
    scenario["actions"] = [dict(id="exec", cli=dict(target="fixture", operation="run", argv=[]))]
    scenario["observe"] = [dict(action="exec", stdoutEquals="fixture-code\n")]
    refusal = preview(invalid)
    assert (
        refusal["state"] != "PASS" and "entry" in str(refusal) and ("candidate" in str(refusal))
    ), refusal
    invalid["targets"]["fixture"] = dict(module="pytest", argv=["--version"])
    refusal = preview(invalid)
    assert (
        refusal["state"] != "PASS" and "module" in str(refusal) and ("candidate" in str(refusal))
    ), refusal
    for position in ("cwd", "PYTHONPATH", "directory", "process-entry"):
        invalid = deepcopy(spec)
        scenario = invalid["scenarios"]["scenarios"][0]
        if position == "cwd":
            scenario["actions"][0]["cwd"] = "{data}"
        elif position == "PYTHONPATH":
            scenario["actions"][0]["process"]["env"] = dict(PYTHONPATH="{data}")
        elif position == "process-entry":
            scenario["actions"][0]["process"]["entry"] = "{data}/app.py"
        else:
            invalid["targets"]["web"]["server"] = dict(
                argv=[sys.executable, "-m", "http.server", "--directory", "{data}"],
                readyPath="/",
                readyWithin=5,
            )
        refusal = preview(invalid)
        assert refusal["state"] != "PASS" and "candidate" in str(refusal), refusal
        assert (
            "cwd" if position == "cwd" else "env" if position == "PYTHONPATH" else "entry"
        ) in str(refusal), refusal
        assert not refusal.get("cleanup", {}).get("childLaunched"), refusal
    result = preview(spec)
    assert result["state"] == "PASS", result
    csv = Path(result["actions"]["import"]["command"][2])
    data = csv.parent.parent
    snapshot = Path(result["actions"]["read"]["server"]["cwd"])
    assert Path(result["actions"]["import"]["cwd"]) == snapshot / "receipt_module"
    assert (
        data.name == "DATA"
        and data.parent == snapshot.parent
        and (not data.is_relative_to(snapshot))
    )
    assert not data.is_relative_to(product) and (not data.exists()) and (not snapshot.exists())
    assert result["actions"]["read"]["http"][0]["response"]["json"] == [["Tea", 2]]
    assert (
        result["cleanup"]["quiescenceVerified"]
        and result["cleanup"]["activeProcessesAfterTeardown"] == 0
    )
    assert (product / ".codex-harness/evidence/preview").is_dir() and (
        not (product / "fixtures").exists()
    )
    bound_module = deepcopy(spec)
    bound_module["targets"]["module-importer"] = dict(module="receipt_module", argv=[])
    action = bound_module["scenarios"]["scenarios"][0]["actions"][0]
    action["cli"] = dict(
        target="module-importer", operation="import", argv=action.pop("process")["argv"]
    )
    assert preview(bound_module)["state"] == "PASS"
    inert = deepcopy(spec)
    inert["scenarios"]["scenarios"][0]["setup"]["files"].append(
        dict(path="{data}/notes.py", utf8="inert fixture bytes")
    )
    inert["scenarios"]["scenarios"][0]["observe"].append(
        dict(path="{data}/notes.py", fileBytesEquals="inert fixture bytes")
    )
    assert preview(inert)["state"] == "PASS"
    for name in (
        "app.py",
        "fixtures/app.txt",
        ".codex/config.toml",
        "{data}/.codex/config.toml",
        "{data}/../app.py",
        "{data}/",
    ):
        invalid = deepcopy(spec)
        invalid["scenarios"]["scenarios"][0]["setup"] = dict(
            files=[dict(path=name, utf8="print('fixture-code')\n")]
        )
        refusal = preview(invalid)
        assert refusal["state"] == "ERROR" and name in refusal["reason"], refusal
        assert (
            "DATA" in refusal["reason"]
            or "reserved" in refusal["reason"]
            or "sandbox" in refusal["reason"]
        ), refusal
        assert not refusal["cleanup"]["childLaunched"], refusal
    outside = product.parent / "junction-target"
    outside.mkdir()
    linked = product / ".codex-harness"
    assert (
        linked.resolve().is_relative_to(product.resolve())
        and (not linked.is_junction())
        and (not linked.is_symlink())
    )
    shutil.rmtree(linked)
    subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(linked), str(outside)],
        check=True,
        capture_output=True,
    )
    try:
        refusal = preview(spec)
        assert (
            refusal["state"] == "ERROR"
            and ".codex-harness" in refusal["reason"]
            and ("link" in refusal["reason"])
        ), refusal
        assert list(outside.iterdir()) == [] and (not refusal["cleanup"]["childLaunched"]), refusal
    finally:
        linked.rmdir()


@timed
def execution_judgment_golden(workspace):
    """Protect compiled worker tests -> real observations -> stored, bound 1j verdicts.

    Catch packet-requested marker RED, source/domain refusal, lost brownfield GREEN,
    wrong-reason RED and missing-judgment FAIL. Earlier coverage hand-wrote the verifier
    without consuming a generated packet. Worker files and judge replies are scripted;
    compiled execution commands, exits/output, candidate binding and cleanup are real.
    """
    from tests.python.core.semantic_wiring_support import node

    from codex_harness.core.candidate_observations import (
        _evidence_digest,
        _semantic_validation,
        assess_execution,
    )
    from codex_harness.core.graph import load_plan
    from codex_harness.core.plan_store import open_plan_store
    from codex_harness.core.runners import execution_witnesses
    from codex_harness.core.worker_packet import build_packet

    workspace.mkdir(parents=True)
    store = open_plan_store(workspace)

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(product), *args],
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )

    def retain(proof):
        receipt = workspace / (proof["evidenceBinding"] + ".json")
        receipt.write_text(json.dumps(proof), encoding="utf-8")
        with store._transaction():
            store._event("execution-observed", json.loads(receipt.read_text("utf-8")))
            event_id = store.connection.execute("SELECT max(id) FROM events").fetchone()[0]
        return store.prepare_judgment(
            question="Assess meaningfulness, correct failure reason and stub/mock gaps in these real observations",
            subject_ids=["R_NEW", "R_BASE", "T_NEW", "T_BASE"],
            source_event_ids=[event_id],
            candidate=proof["candidate"],
        )

    def judge(prepared, proof, verdict, gaps=()):
        meaning = dict(
            check="execution",
            evidenceBinding=proof["evidenceBinding"],
            phase=proof["phase"],
            meaningful=True,
            correctFailureReason=True,
            noStubMockGaps=True,
        )
        return scripted_judgment(store, prepared, meaning, verdict, list(gaps))["current"]

    try:
        store.upsert_node(
            dict(
                id="V1",
                kind="vision",
                title="execution-judgment",
                text="Preserve baseline readback and add both channels",
                parents=[],
                fields=dict(planMode="semantic-v1"),
            )
        )
        apply_content(
            store,
            dict(
                nodes=[
                    node("F", "feature", ["V1"]),
                    node("R_BASE", "requirement", ["F"], behavior="Preserve baseline readback"),
                    node("R_NEW", "requirement", ["F"], behavior="Read both channels"),
                    node(
                        "T_BASE",
                        "test",
                        ["R_BASE"],
                        module="baseline.sensor",
                        level="semantic",
                        execution=dict(
                            entry="baseline.sensor",
                            runtime=sys.executable,
                            argv=[],
                            testRoots=["baseline.sensor"],
                            rationale="Preserve existing baseline readback",
                        ),
                    ),
                    node(
                        "T_NEW",
                        "test",
                        ["R_NEW"],
                        module="verify.unfamiliar",
                        level="semantic",
                        execution=dict(
                            entry="verify.unfamiliar",
                            runtime=sys.executable,
                            argv=["channels.dat"],
                            testRoots=["verify.unfamiliar", "helperlib"],
                            rationale="Observe both channels from the candidate data path",
                        ),
                    ),
                    node(
                        "C",
                        "bead",
                        ["T_BASE", "T_NEW"],
                        ownedFiles=["verify.unfamiliar", "helperlib/__init__.py"],
                        dependsOn=[],
                        lessons=[],
                        semanticPhase="contracts",
                    ),
                    node(
                        "B",
                        "bead",
                        ["T_BASE", "T_NEW"],
                        ownedFiles=["channels.dat", "helperlib/__init__.py"],
                        dependsOn=["C"],
                        lessons=[],
                        semanticPhase="build",
                    ),
                ]
            ),
        )
        template = workspace / "template"
        template.mkdir(exist_ok=True)
        product = workspace / "candidate"
        product.mkdir()
        (product / "baseline.sensor").write_text(
            "print('SELDON-STUB is a diagnostic string, baseline remains GREEN')\n"
        )
        (product / "helperlib").mkdir()
        (product / "helperlib/__init__.py").write_text(
            "from pathlib import Path\ndef read_channels():\n    return Path('channels.dat').read_text()\n"
        )
        (product / "payload.binary").write_bytes(b"x" * 262145)
        for index in range(130):
            (product / f"asset-{index}.opaque").write_text(str(index))
        store.compile_template(workspace / "template")
        manifest = store._manifest(store.spine())
        compiled = workspace / "compiled"
        compiled.mkdir()
        (compiled / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")
        plan = load_plan(compiled)
        for name in plan.requirement_files:
            (product / name).write_bytes((template / name).read_bytes())
        git("init", "-q")
        git("add", ".")
        git("-c", "user.name=golden", "-c", "user.email=golden@local", "commit", "-qm", "baseline")

        def worker(bead, files):
            packet = build_packet(plan, dict(manifest["beads"][bead], id=bead), product, None)
            scripted_worker(packet, product, files)

        worker("C", {"verify.unfamiliar": "raise RuntimeError('verifier startup failed')\n"})
        broken = _semantic_validation(product, plan, {}, "contracts")
        assert broken["observedState"] == "FAIL" and broken["state"] == "pending-judgment"
        request = retain(broken)
        gap = judge(
            request, broken, "negative", ["Verifier fails during startup before reading channels"]
        )
        rejected = assess_execution(broken, gap)
        assert rejected["state"] == "FAIL" and (not rejected["admitted"]) and rejected["gaps"]
        worker(
            "C",
            {
                "verify.unfamiliar": "# Behavior: both channels; catches missing readback; baseline covers only startup.\nfrom pathlib import Path\nfrom helperlib import read_channels\nimport sys\nassert Path('payload.binary').stat().st_size == 262145\nassert len(list(Path('.').glob('asset-*.opaque'))) == 130\nactual = read_channels() if Path(sys.argv[1]).is_file() else 'absent'\nprint('observed channels:', actual)\nif actual != 'ready:42,84':\n    print('missing behavior: both channel values', file=sys.stderr)\n    sys.exit(2)\n"
            },
        )
        git("add", ".")
        git(
            "-c",
            "user.name=golden",
            "-c",
            "user.email=golden@local",
            "commit",
            "-qm",
            "worker test",
        )
        red = _semantic_validation(product, plan, {}, "contracts")
        rows = {r["id"]: r for r in red["results"]}
        assert rows["T_BASE"]["state"] == "PASS"
        assert rows["T_NEW"]["actions"]["execute"]["exit"] == 2
        assert "observed channels: absent" in rows["T_NEW"]["actions"]["execute"]["stdout"]
        assert "missing behavior:" in rows["T_NEW"]["actions"]["execute"]["stderr"]
        assert all(r["cleanup"]["quiescenceVerified"] for r in red["results"])
        uncertain = json.loads(json.dumps(red))
        uncertain["results"][0].pop("cleanup")
        uncertain["evidenceBinding"] = _evidence_digest(uncertain)
        assert not assess_execution(uncertain, judge(retain(uncertain), uncertain, "positive"))[
            "admitted"
        ]
        request = retain(red)
        assert not assess_execution(
            red, store.judgment(judgment_id=request["id"], candidate=red["candidate"])
        )["admitted"]
        red = assess_execution(red, judge(request, red, "positive"))
        assert red["state"] == "RED" and red["admitted"]
        worker("B", {"channels.dat": "ready:42,84"})
        git("add", ".")
        git(
            "-c",
            "user.name=golden",
            "-c",
            "user.email=golden@local",
            "commit",
            "-qm",
            "worker implementation",
        )
        green = _semantic_validation(
            product, plan, {"attempt": {"baseCommit": red["candidateCommit"]}}, "build"
        )
        assert green["red"]["observedState"] == "FAIL"
        assert green["observedState"] == "PASS" and green["state"] == "pending-judgment"
        assert not green["passed"] and green["judgmentStatus"] == "pending-judgment"
        assert all(r["state"] == "PASS" for r in green["results"])
        witnesses = execution_witnesses(dict(semantic=green))
        assert {w["id"] for w in witnesses} == {"T_BASE", "T_NEW"}
        assert all(w["sha256"] and (not w["artifactPaths"]) for w in witnesses)
        request = retain(green)
        unsupported = judge(request, green, "unsupported")
        assert not unsupported["positiveCredit"] and (not unsupported["concreteGap"])
        assert assess_execution(green, unsupported)["state"] == "pending-judgment"
        judge(request, green, "positive")
        store.close()
        store = open_plan_store(workspace)
        current = store.judgment(judgment_id=request["id"], candidate=green["candidate"])
        accepted = assess_execution(green, current)
        assert accepted["passed"] and accepted["admitted"] and (accepted["state"] == "PASS")
        helper_base = git("rev-parse", "HEAD").stdout.strip()
        worker(
            "B",
            {
                "helperlib/__init__.py": "from pathlib import Path\ndef read_channels():\n    value = Path('channels.dat').read_text()\n    assert '\\n' not in value, 'unexpected channel newline'\n    return value\n"
            },
        )
        git("add", "helperlib/__init__.py")
        git(
            "-c",
            "user.name=golden",
            "-c",
            "user.email=golden@local",
            "commit",
            "-qm",
            "worker assertion helper",
        )
        no_red = _semantic_validation(
            product,
            plan,
            {"redObservations": [red], "attempt": {"baseCommit": helper_base}},
            "build",
        )
        refused = assess_execution(no_red, judge(retain(no_red), no_red, "positive"))
        assert no_red["testSources"]["contentHash"] != red["testSources"]["contentHash"]
        assert no_red["red"]["observedState"] == "PASS"
        assert refused["state"] == "FAIL" and (not refused["passed"])
        changed = store.spine().node("R_NEW")
        changed["text"] = "Read a third channel too"
        store.upsert_node(node=changed)
        stale = store.judgment(judgment_id=request["id"], candidate=green["candidate"])
        assert not assess_execution(green, stale)["admitted"]
    finally:
        store.close()


@timed
def judgment_contract_golden(workspace):
    """Protect pending/verdict durability through public submission, reload and consumption.

    Catch lost pending state, stale subject/evidence/candidate credit and unbounded malformed
    corrections. Stage 1a only reloads trace observations; it never consumes semantic verdicts.
    All semantic replies are explicitly scripted, without provider or human-consent credit.
    """
    from copy import deepcopy

    from tests.python.core.semantic_wiring_support import node

    from codex_harness.core.plan_store import open_plan_store

    workspace.mkdir(parents=True)
    store = open_plan_store(workspace)

    def reload():
        nonlocal store
        store.close()
        store = open_plan_store(workspace)

    def read(record, **args):
        return store.judgment(judgment_id=record["id"], **args)

    def submit(record, verdict, gaps=None, **changes):
        response = dict(
            judgmentId=record["id"],
            binding=record["binding"],
            verdict=verdict,
            rationale="Scripted semantic assessment of retained sensor facts",
            meaning={"assessment": "retained readback"},
            gaps=gaps or [],
        )
        response.update(changes)
        return scripted_judgment(store, record, response["meaning"], response=json.dumps(response))

    try:
        store.upsert_node(
            dict(
                id="V1",
                kind="vision",
                title="judgments",
                text="Preserve sensor readback",
                parents=[],
                fields=dict(planMode="semantic-v1"),
            )
        )
        apply_content(
            store, dict(nodes=[node("F1", "feature", ["V1"]), node("R1", "requirement", ["F1"])])
        )
        spine = store.spine()
        evidence = node(
            "E1",
            "result",
            ["R1"],
            evidence={
                "itemRef": {"id": "R1", "revision": spine.revision("R1")},
                "candidate": "sensor-candidate-1",
                "source": {
                    "reference": "fixture:measurement",
                    "realProvider": False,
                    "historical": True,
                },
                "observation": {"reference": "fixture:readback", "outcome": "passed"},
                "state": "integrated",
            },
        )
        store.upsert_node(node=evidence)
        event_id = store.connection.execute("SELECT max(id) FROM events").fetchone()[0]
        request = dict(
            question="Does the retained measurement establish readback?",
            subject_ids=["R1"],
            evidence_ids=["E1"],
            source_event_ids=[event_id],
        )
        pending = store.prepare_judgment(**request)
        # Typed examples are consumed at their real gates: planning_intent,
        # authority, HTTP publication/reuse/disposition and r18. Retain generic
        # durability here once; round-tripping generated examples missed wiring.
        reload()
        restored = read(pending, candidate="sensor-candidate-1")
        assert restored["status"] == "pending-judgment"
        assert not restored["positiveCredit"] and (not restored["concreteGap"])
        assert restored["sourceRefs"] == pending["sourceRefs"]
        assert restored["evidenceRefs"][0]["node"]["fields"]["evidence"]["state"] == "integrated"
        submit(pending, "unsupported")
        reload()
        unsupported = read(pending, candidate="sensor-candidate-1")
        assert (
            unsupported["status"] == "pending-judgment" and unsupported["verdict"] == "unsupported"
        )
        assert unsupported["disposition"] == "judge" and unsupported["owner"] is None
        submit(pending, "inconclusive")
        assert not read(pending, candidate="sensor-candidate-1")["concreteGap"]
        submit(pending, "positive")
        reload()
        credited = read(pending, candidate="sensor-candidate-1")
        assert credited["positiveCredit"] and (not credited["concreteGap"])
        assert credited["meaning"] == {"assessment": "retained readback"}
        assert not credited["sourceVerified"] and credited["owner"] is None
        assert not read(pending)["positiveCredit"]
        assert not read(pending, candidate="sensor-candidate-2")["positiveCredit"]
        store.upsert_node(node=node("OTHER", "feature", ["V1"]))
        assert read(pending, candidate="sensor-candidate-1")["positiveCredit"]
        negative = store.prepare_judgment(**request)
        submit(negative, "negative", ["Sensor readback lacks a second-channel observation"])
        reload()
        gap = read(negative, candidate="sensor-candidate-1")
        assert gap["concreteGap"] and (not gap["positiveCredit"])
        assert gap["gaps"] == ["Sensor readback lacks a second-channel observation"]
        changed = store.spine().node("R1")
        changed["text"] = "Read both sensor channels"
        store.upsert_node(node=changed)
        reload()
        assert read(pending, candidate="sensor-candidate-1")["status"] == "pending-judgment"
        assert not read(negative, candidate="sensor-candidate-1")["concreteGap"]
        fresh = store.prepare_judgment(**request)
        submit(fresh, "positive")
        changed_evidence = deepcopy(store.spine().node("E1"))
        changed_evidence["fields"]["evidence"]["note"] = "Additional measurement annotation"
        store.upsert_node(node=changed_evidence)
        assert not read(fresh, candidate="sensor-candidate-1")["positiveCredit"]
        malformed = store.prepare_judgment(
            question="Judge the current readback contract", subject_ids=["R1"]
        )
        for expected_fixes, expected_disposition in (
            (1, "exact-fix"),
            (2, "exact-fix"),
            (2, "steer"),
        ):
            submit(malformed, "positive", binding="wrong-captured-binding")
            reload()
            retained = read(malformed)
            assert retained["fixesIssued"] == expected_fixes
            assert retained["disposition"] == expected_disposition
            assert "binding" in retained["error"] and retained["owner"] is None
            assert not retained["positiveCredit"] and (not retained["concreteGap"])
        with pytest.raises(ValueError):
            store.submit_judgment(
                judgment_id=malformed["id"], response="{}", source="scripted-judge"
            )
        correctable = store.prepare_judgment(
            question="Judge the revised sensor contract", subject_ids=["R1"]
        )
        scripted_judgment(store, correctable, {}, response="{")
        submit(correctable, "negative")
        submit(correctable, "positive")
        reload()
        assert read(correctable)["positiveCredit"] and read(correctable)["fixesIssued"] == 2
        assert store.spine().node("E1")["fields"]["evidence"]["state"] == "stale"
        assert store.status()["approval"]["granted"] is False
    finally:
        store.close()


@timed
def trace_contract_golden(workspace):
    """Protect public full/collapsed brownfield traces and exact amendment freshness.

    Catch dropped tests/baselines during compilation, broad evidence invalidation and
    destructive feature replacement, metadata edits laundering retained observations and
    stale revision references retaining current evidence credit; scenario-only delivery
    journeys miss typed trace persistence, revision binding and independent branches. Observations here are retained fixture evidence,
    never live-provider acceptance. No workers or providers execute in this journey.
    """
    from copy import deepcopy

    from tests.python.core.semantic_wiring_support import node

    from codex_harness.core.graph import load_plan
    from codex_harness.core.plan_store import open_plan_store
    from codex_harness.core.spine import Spine, SpineError

    for mode, full in (("semantic-v1", True), ("legacy", False)):
        root = workspace / mode
        root.mkdir(parents=True)
        (root / "template").mkdir()
        store = open_plan_store(root)

        def ref(key):
            return {"id": key, "revision": store.spine().revision(key)}  # noqa: B023

        try:
            store.upsert_node(
                dict(
                    id="V1",
                    kind="vision",
                    title="sensor",
                    text="Tune a sensor while retaining readback",
                    parents=[],
                    fields=dict(planMode="semantic-v1") if mode == "semantic-v1" else {},
                )
            )
            vision = store.spine().node("V1")
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
                    architecture, design, contract = ("A" + suffix, "D" + suffix, "K" + suffix)
                    nodes.extend(
                        [
                            node(architecture, "architecture", [r]),
                            node(design, "design", [architecture]),
                            node(contract, "contract", [design]),
                        ]
                    )
                    parent = contract
                nodes.extend(
                    [
                        node(
                            t,
                            "test",
                            [parent],
                            module="sensor/readback",
                            level="semantic",
                            execution={
                                "entry": "sensor/readback",
                                "argv": ["--measure"],
                                "testRoots": ["sensor"],
                                "rationale": "Measure retained sensor behavior",
                            },
                        ),
                        node(
                            b,
                            "bead",
                            [t],
                            ownedFiles=["sensor" + suffix + ".bin"],
                            dependsOn=[],
                            lessons=[],
                            **{"semanticPhase": "build"}
                            if mode == "semantic-v1"
                            else {"proofModule": "sensor/readback"},
                        ),
                        node(c, "code", [b], path="sensor" + suffix + ".bin"),
                    ]
                )
            if full:
                nodes = [n for n in nodes if n["id"] != "B2"]
                next(n for n in nodes if n["id"] == "C2")["parents"] = ["T2"]
                next(n for n in nodes if n["id"] == "B1")["parents"] = ["T1", "T2"]
            apply_content(store, dict(nodes=nodes))
            additions = []
            for suffix in ("1", "2"):
                result = "E" + suffix
                evidence = {
                    "itemRef": ref("C" + suffix),
                    "candidate": "baseline-candidate",
                    "source": {
                        "reference": "baseline:measurement:" + suffix,
                        "realProvider": False,
                        "historical": True,
                    },
                    "observation": {
                        "reference": "measurement:" + suffix,
                        "outcome": "passed",
                        "rms": 0.2,
                    },
                    "state": "integrated",
                }
                additions.append(node(result, "result", ["C" + suffix], evidence=evidence))
            projected = Spine(
                {**store.spine().to_dict(), "nodes": store.spine().to_dict()["nodes"] + additions}
            )
            features = []
            for suffix in ("1", "2"):
                feature = store.spine().node("F" + suffix)
                feature["fields"]["baseline"] = {
                    "source": "existing-repository",
                    "behavior": "Read existing sensor channel " + suffix,
                    "rationale": "Preserve verified channel readback",
                    "codeRefs": [ref("C" + suffix)],
                    "testRefs": [ref("T" + suffix)],
                    "resultRefs": [
                        {"id": "E" + suffix, "revision": projected.revision("E" + suffix)}
                    ],
                }
                feature["fields"]["evidenceRefs"] = [
                    {"id": "E" + suffix, "revision": projected.revision("E" + suffix)}
                ]
                features.append(feature)
            apply_content(store, dict(nodes=features + additions))
            if full:
                apply_content(
                    store,
                    dict(
                        nodes=[
                            node(
                                "RES",
                                "research",
                                ["V1"],
                                rationale="Investigate sensor calibration",
                            ),
                            node(
                                "MET",
                                "metric",
                                ["V1"],
                                definition="Measured residual noise",
                                unit="dB",
                            ),
                        ]
                    ),
                )
                apply_content(
                    store,
                    dict(
                        nodes=[
                            node(
                                "STOP",
                                "stop-condition",
                                ["MET"],
                                metricRefs=[ref("MET")],
                                condition="Residual noise reaches agreed threshold",
                            ),
                            node(
                                "PENDING",
                                "result",
                                ["R1"],
                                metricRefs=[ref("MET")],
                                evidence={
                                    "itemRef": ref("R1"),
                                    "candidate": "next-measurement",
                                    "source": {
                                        "reference": "planned:measurement",
                                        "realProvider": False,
                                        "historical": False,
                                    },
                                    "observation": None,
                                    "state": "awaiting",
                                },
                            ),
                        ]
                    ),
                )
                store.upsert_node(
                    node=node(
                        "ITER",
                        "iteration",
                        ["MET"],
                        metricRefs=[ref("MET")],
                        resultRefs=[ref("E1")],
                        stopConditionRefs=[ref("STOP")],
                        researchRefs=[ref("RES")],
                    )
                )
                store.upsert_node(
                    node=node(
                        "BAT",
                        "baton",
                        ["ITER"],
                        decisionRefs=[],
                        assumptions=["Baseline sensor remains available"],
                        openQuestions=["Which calibration meets the agreed metric?"],
                        currentState="Baseline mapped; measurement awaits execution",
                        nextSafeAction="Review the calibration proposal",
                        rationale="Preserve current behavior while planning",
                        iterationRefs=[ref("ITER")],
                    )
                )
            forged = deepcopy(additions[0])
            forged["id"] = "FORGED_LIVE"
            forged["fields"]["evidence"]["state"] = "validated live"
            with pytest.raises(ValueError):
                store.upsert_node(node=forged)
            extra_result = deepcopy(additions[0])
            extra_result["id"] = "E3"
            extra_result["fields"]["evidence"]["source"]["historical"] = False
            extra_result["fields"]["evidence"]["observation"]["reference"] = (
                "fixture:fresh-channel-measurement"
            )
            store.upsert_node(node=extra_result)
            if full:
                store.upsert_node(
                    node=node(
                        "POINTER",
                        "decision",
                        ["V1"],
                        batonRefs=[ref("BAT")],
                        rationale="Resume from the canonical sensor baton",
                    )
                )
            initial = store.compile_template(root / "template")
            sample = root / "sample.json"
            sample.write_text(json.dumps(initial), encoding="utf-8")
            consumed = load_plan(sample)
            assert consumed.trace == initial["trace"]
            assert initial["evidenceStates"]["C1"]["state"] == "integrated"
            assert initial["evidenceStates"]["C2"]["state"] == "integrated"
            if full:
                assert initial["evidenceStates"]["MET"]["state"] == "awaiting"
                assert initial["evidenceStates"]["PENDING"]["state"] == "awaiting"
            retained = store.spine().to_dict()
            store.close()
            store = open_plan_store(root)
            reloaded = store.compile_template(root / "template")
            assert reloaded == initial
            assert reloaded["trace"] == retained
            assert set(reloaded["traceRevisions"]) == {n["id"] for n in retained["nodes"]}
            assert len(reloaded["beads"]) == (1 if full else 2)
            assert all(
                n["fields"]["level"] == "semantic" for n in retained["nodes"] if n["kind"] == "test"
            )
            old_requirement = ref("R1")
            discovery = node("DISC", "discovery", ["C1"], source="baseline:measurement:1")
            decision = node("DEC", "decision", ["DISC"], rationale="Propose improved calibration")
            apply_content(store, dict(nodes=[discovery, decision]))
            amendment = node(
                "AM",
                "amendment",
                ["DISC"],
                sourceRef=ref("DISC"),
                targetRefs=[old_requirement],
                proposedChange="Calibrate the first sensor channel",
                rationale="Observed drift in first channel",
                decisionRefs=[ref("DEC")],
            )
            store.upsert_node(node=amendment)
            assert ref("R1") == old_requirement
            before = store.spine()
            requirement = before.node("R1")
            requirement["text"] = (
                "Calibrate first-channel readback while preserving the second channel"
            )
            metadata_edit = before.node("E3")
            metadata_edit["fields"]["evidence"]["note"] = (
                "Retained observation, extra annotation only"
            )
            apply_content(store, dict(nodes=[requirement, metadata_edit]))
            assert store.spine().node("E3")["fields"]["evidence"]["state"] == "stale"
            mismatched = node(
                "OLD_ITEM",
                "result",
                ["R1"],
                evidence={
                    "itemRef": old_requirement,
                    "candidate": "fixture-current-candidate",
                    "source": {
                        "reference": "fixture:stale-input",
                        "realProvider": False,
                        "historical": False,
                    },
                    "observation": {
                        "reference": "fixture:old-requirement-observation",
                        "outcome": "passed",
                    },
                    "state": "integrated",
                },
            )
            store.upsert_node(node=mismatched)
            assert store.spine().node("OLD_ITEM")["fields"]["evidence"]["state"] == "stale"
            changed = store.compile_template(root / "template")
            affected = store.impact_since(str(sample))
            assert "C1" in affected["affected"] and "C2" not in affected["affected"], "Public impact lost the changed dependency or marked the independent branch"
            assert changed["evidenceStates"]["OLD_ITEM"]["state"] == "stale"
            assert (
                next(n for n in changed["trace"]["nodes"] if n["id"] == "OLD_ITEM")["fields"][
                    "evidence"
                ]["state"]
                == "stale"
            )
            spine = store.spine()
            assert spine.node("E1")["fields"]["evidence"]["state"] == "stale"
            assert spine.node("E2") == before.node("E2")
            assert spine.node("F2") == before.node("F2")
            assert spine.node("AM") == before.node("AM")
            for suffix in ("1", "2"):
                expected = deepcopy(before.node("E" + suffix)["fields"]["evidence"])
                actual = spine.node("E" + suffix)["fields"]["evidence"]
                assert {k: v for k, v in actual.items() if k != "state"} == {
                    k: v for k, v in expected.items() if k != "state"
                }
            assert changed["evidenceStates"]["C1"]["state"] == "stale"
            assert changed["evidenceStates"]["C2"]["state"] == "integrated"
            assert spine.trace_up("E1")[0][-1] == "V1"
            feature = spine.node("F1")
            feature["fields"].pop("baseline")
            with pytest.raises(ValueError):
                store.upsert_node(node=feature)
            assert store.spine().to_dict() == spine.to_dict()
            imported = spine.to_dict()
            imported["nodes"] = [
                n for n in imported["nodes"] if n["id"] == "V1" or n["id"].endswith("2")
            ]
            path = root / "narrowed.json"
            path.write_text(json.dumps(imported), encoding="utf-8")
            with pytest.raises(SpineError) as refusal:
                store.import_spine(path)
            assert refusal.value.code == "spine-evidence-preservation-required"
            assert store.spine().to_dict() == spine.to_dict()
        finally:
            store.close()


def preview_environment(workspace):
    """Preview support is harness metadata, never an accepted product edit.

    Creating this hook under tools/ after C invalidated only the web cockpit;
    CLI now exercises the same producer before its real HTTP acceptance readback.
    """
    keepalive = workspace / ".codex-harness" / "keep-node-open.cjs"
    keepalive.write_text("setInterval(() => {}, 1000);\n", encoding="utf-8")
    return dict(
        os.environ,
        NODE_OPTIONS=(
            os.environ.get("NODE_OPTIONS", "") + " --require " + json.dumps(keepalive.as_posix())
        ).strip(),
    )


@timed
def web_evidence_golden(workspace, retained, vision):
    """C26: retain real browser/API/persistence semantics and standalone preview cleanup.

    The short static-page journey missed negative-response attribution, truncated
    bodies, prerequisite data, redirects and orphaned headless preview children.
    """
    from codex_harness.core.semantic_runner import run as observe
    from codex_harness.core.plan_store import open_plan_store_readonly

    scenario = vision["scenarios"]["scenarios"][0]
    with open_plan_store_readonly(workspace) as store:
        fields = store.spine().node(fixture_id(workspace, "V1"))["fields"]
        bound = next(s for s in fields["scenarios"]["scenarios"] if s["id"] == fixture_id(workspace, scenario["id"]))
    action_ids = {a["id"]: b["id"] for a, b in zip(scenario["actions"], bound["actions"], strict=True)}
    journey = scenario["actions"][2]["web"]
    step_ids = {a["id"]: b["id"] for a, b in zip(journey["steps"], bound["actions"][2]["web"]["steps"], strict=True) if "id" in a}
    proof = retained["semanticValidation"]["results"]
    actions = [a for r in proof for a in r["actions"].values() if a.get("browserMode")]
    assert actions and all(a["browserMode"] == "normal" for a in actions)
    served = [a for a in actions if a.get("server")]
    assert len(served) == 1, actions
    api = next(r for r in proof if r["id"] == bound["id"])["actions"][action_ids["api"]]
    assert api["http"][0]["response"]["json"] == {"saved": False}
    assert api["server"]["stopped"] and not api.get("browserMode")
    assert served[0]["server"]["ready"] and served[0]["server"]["stopped"]
    assert served[0]["http"][0]["response"]["json"] == {"saved": True, "note": "Tea"}
    assert served[0]["checks"]["consoleErrors"] == 0
    assert [
        (r["step"], r["status"], Path(r["body"]["path"]).read_text() if r["body"] else None)
        for r in served[0]["responses"]
        if r["expected"]
    ] == [
        (step_ids["formpost"], 302, None),
        (step_ids["overconsume"], 409, "{}"),
        (step_ids["delete"], 204, ""),
        (step_ids["empty"], 204, ""),
        (step_ids["cached"], 304, ""),
    ]
    assert any(r["status"] == 302 and r.get("bodyDiagnostic") for r in served[0]["responses"]), (
        served[0]
    )
    assert any(
        r["url"].split("#", 1)[0].endswith("/index.html")
        and r["body"]
        and Path(r["body"]["path"]).stat().st_size > 16384
        for r in served[0]["responses"]
    )
    assert sum(r["method"] == "DELETE" for r in served[0]["responses"]) == 1
    assert all(
        Path(r["body"]["path"]).read_bytes() == b""
        for r in served[0]["responses"]
        if r["status"] == 204
    )
    assert any(
        r["step"] == step_ids["empty"] and r.get("transportDiagnostic") for r in served[0]["responses"]
    ), served[0]
    assert [(r["response"]["status"], r["response"]["body"]) for r in served[0]["http"][2:]] == [
        (204, ""),
        (200, ""),
        (205, ""),
        (304, ""),
    ]
    assert served[0]["http"][1]["response"]["json"]["exists"] is True
    assert any(a["url"].startswith("file://") for a in actions)
    assert all(
        r["cleanup"]["quiescenceVerified"] and r["cleanup"]["activeProcessesAfterTeardown"] == 0
        for r in proof
    )
    import socket

    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", served[0]["server"]["port"])) != 0
    # Execute the packet's actual shell command: a quoted Windows executable
    # without PowerShell's & looks plausible but never opens the preview.
    prompt = retained["beads"][fixture_id(workspace, "B")]["prompt"]
    commands = [c for c in prompt.split("`") if "--preview " in c]
    assert any("--preview ." in c for c in commands), prompt
    command = next(c for c in commands if f"--preview SCENARIOS.json '{fixture_id(workspace, 'served')}'" in c)
    # Static previews never started a server: its log must not become
    # an unowned worker change, and its protected binding lives here.
    worker_root = Path(retained["beads"][fixture_id(workspace, "B")]["attempt"]["cwd"])
    # Round 2: happy/negative steps share endpoints and error statuses.
    # Journey-wide declarations hid happy-path failures; the old probe
    # covered only a different, undeclared endpoint's 500.
    from copy import deepcopy

    negative = deepcopy(vision["scenarios"])
    negative["scenarios"] = negative["scenarios"][:1]
    negative["scenarios"][0]["actions"] = [
        next(a for a in negative["scenarios"][0]["actions"] if a["id"] == "a1")
    ]
    negative["scenarios"][0]["observe"] = negative["scenarios"][0]["observe"][:1]
    negative["scenarios"][0]["actions"][0]["web"]["steps"] = [
        s for s in journey["steps"] if s["op"] == "open" or s.get("id") == "overconsume"
    ]
    negative["scenarios"][0]["actions"][0]["web"]["steps"].extend(
        [
            dict(id="happy409", op="click", locator=dict(selector="#happy")),
            dict(op="assertVisible", locator=dict(text="Happy")),
            dict(
                id="negative500",
                op="click",
                locator=dict(selector="#broken"),
                expectedErrors=[dict(method="GET", path="/broken", statusEquals=500)],
            ),
            dict(op="assertVisible", locator=dict(text="Broken")),
            dict(id="happy500", op="click", locator=dict(selector="#failed-happy")),
            dict(op="assertVisible", locator=dict(text="Failed happy")),
            dict(id="disconnect", op="click", locator=dict(selector="#disconnect")),
            dict(op="assertVisible", locator=dict(text="Disconnected")),
            # P1: headers are not completion, including a declared error.
            # Existing HTTP failures never truncated a response body.
            dict(id="truncated200", op="click", locator=dict(selector="#truncated200")),
            dict(op="assertVisible", locator=dict(text="Truncated 200")),
            dict(
                id="truncated409",
                op="click",
                locator=dict(selector="#truncated409"),
                expectedErrors=[dict(method="GET", path="/truncated409", statusEquals=409)],
            ),
            dict(op="assertVisible", locator=dict(text="Truncated 409")),
        ]
    )
    failed = observe(negative, worker_root, vision["targets"], "validation")[0]
    assert failed["state"] == "FAIL" and failed["cleanup"]["quiescenceVerified"], failed
    assert failed["actions"]["a1"]["checks"]["consoleErrors"] == 5, failed
    assert [
        (r["step"], r["status"], r["expected"])
        for a in failed["actions"].values()
        for r in a["responses"]
        if r["status"] and r["status"] >= 400
    ] == [
        ("overconsume", 409, True),
        ("happy409", 409, False),
        ("negative500", 500, True),
        ("happy500", 500, False),
        ("truncated409", 409, True),
    ]
    assert {
        r["status"]
        for r in failed["actions"]["a1"]["responses"]
        if (r.get("step") or "").startswith("truncated")
        and (r.get("bodyDiagnostic") or r.get("transportDiagnostic"))
    } == {200, 409}, failed
    assert any(
        r["step"] == "disconnect" and r["status"] is None and r["transportDiagnostic"]
        for r in failed["actions"]["a1"]["responses"]
    )
    before_preview = subprocess.check_output(["git", "status", "--porcelain"], cwd=worker_root)
    from codex_harness.core.planner_candidate import observation_files

    before_files = observation_files(worker_root)
    # A finished adapter must exit despite a referenced event-loop
    # handle. Previously the preview had no such handle, hiding an
    # implicit lifetime that could wait until the outer watchdog.
    preview_env = preview_environment(workspace)
    preview_started = time.time()
    preview = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=worker_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=preview_env,
    )
    try:
        # P2: headless preview must finish and reap its launcher/child
        # server without an outer Job or any desktop window control.
        output, errors = preview.communicate(timeout=35)
        assert preview.returncode == 0, errors
        completion = json.loads(output)
        assert completion["state"] == "pending-judgment", completion
        assert set(completion["actions"]) == set(action_ids.values()), completion
        assert completion["actions"][action_ids["seed"]]["http"][0]["request"]["json"] == dict(note="Seeded")
        assert completion["actions"][action_ids["seed-check"]]["stdout"].replace("\r\n", "\n") == "Seeded\n"
        assert all(o["passed"] is not False for o in completion["observations"]), completion
        observed = completion["actions"][action_ids["a1"]]
        assert len(observed["steps"]) == len(journey["steps"]), completion
        assert observed["checks"]["consoleErrors"] == 0
        # Named previews previously opened a desktop page and erased
        # screenshots; check retained phone pixels and actual flow.
        import struct

        assert len(observed["artifacts"]) == 2 and {a["fullPage"] for a in observed["artifacts"]} == {False, True}, observed
        assert all(not a.get("missing") and a["viewport"] == journey["viewport"] for a in observed["artifacts"]), observed
        for artifact in observed["artifacts"]:
            png = Path(artifact["path"])
            assert png.is_relative_to(worker_root / ".codex-harness/evidence/preview")
            data = png.read_bytes()
            assert data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) > 24
            assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
            width, height = struct.unpack(">II", data[16:24])
            assert width == 375 and height >= journey["viewport"]["height"]
        assert "Deleted" in observed["rendered"]["text"]
        assert observed["url"].startswith("http://127.0.0.1:") and observed["server"]["ready"], (
            observed
        )
        assert "HeadlessChrome/" in observed["userAgent"], observed
        cleanup = completion["cleanup"]
    finally:
        if preview.poll() is None:
            preview.communicate(timeout=135)  # The preview's own bounded owner ends it.
    assert observed["browserMode"] == "normal"
    assert cleanup["backend"] == "windows-job" and cleanup["membershipVerifiedBeforePayload"]
    assert cleanup["quiescenceVerified"]
    assert cleanup["activeProcessesAfterTeardown"] == 0
    assert Path(observed["server"]["cwd"]) != worker_root
    assert not Path(observed["server"]["cwd"]).exists()
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", observed["server"]["port"])) != 0
    assert (
        subprocess.check_output(["git", "status", "--porcelain"], cwd=worker_root) == before_preview
    )
    assert observation_files(worker_root) == before_files
    print(f"golden preview: seconds={time.time() - preview_started:.2f}", flush=True)
    print(
        f"golden web: scenarios={len(proof)}; journeys={len(actions)}; "
        f"browserModes={sorted({a['browserMode'] for a in actions})}"
    )


@timed
def planning_surface_golden(workspace):
    """C4/C6/C8/I10: invalid answers and user questions cannot mutate consent.

    Old chat-authored schemas concealed omitted dialogue and caller-owned IDs.
    Brief deletion exception: generic content/schema-teaching and six guessed
    approval envelopes are replaced by typed failures and opaque subject tokens.
    The host-refusal twin catches hidden provider diagnostics; successful calls
    and worker-denial recovery do not exercise a refused planner bootstrap.
    """
    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core.plan_store import open_plan_store_readonly
    client = LoopClient(workspace)
    try:
        inventory = {t["name"] for t in client.rpc("tools/list", {})["tools"]}
        assert "harness_chat" in inventory
        assert not {"harness_plan_start", "harness_loop_answer", "harness_steer"} & inventory
        client.call("harness_loop_answer", request_id="invented", answer={}, source="chat", error=True)
        content = plan_content()
        user_text = "1–80 “quoted” café 日本"
        content["nodes"][0]["text"] += " " + user_text
        client.call("harness_plan_start", name="Unicode intent", idea="Build a receipt for " + user_text)
        assert client.current["userRequest"] is None
        assert client.current["question"]["purpose"] == "understanding"
        assert user_text in str(client.offered["packet"]["facts"]["dialogue"])
        with open_plan_store_readonly(workspace) as store:
            before = store.spine().node("V1")
        client.answer('{"understanding":"Missing the quality bar"}')
        correction = client.current["question"]["judgment"]
        assert correction["disposition"] == "exact-fix" and "qualityBar" in correction["error"]
        assert client.offered["packet"]["exactFix"] == correction["error"]
        with open_plan_store_readonly(workspace) as store:
            assert store.spine().node("V1") == before
        client.answer(dict(option="ready", rationale="Preserve the full dream", content=content))
        advance_meaning(client, content, stop_at=lambda r: (r.get("userRequest") or {}).get("gate") == "align+vision-lock" and not r.get("question"))
        token = client.current["userRequest"]["request_id"]
        # C18/C23: the real status CLI must preserve user meaning at A even with
        # legacy Windows stdio. MCP-only Unicode journeys miss locale corruption.
        legacy_env = dict(os.environ, PYTHONUTF8="0", PYTHONIOENCODING="cp1252")
        from codex_harness.core.audit_process import run_audit_process
        readback = run_audit_process(
            [sys.executable, "-B", "-m", "codex_harness.core.plan_cli",
             "--workspace", str(workspace), "--json", "status"],
            cwd=workspace, capture_output=True, text=False, env=legacy_env, timeout=30,
        )
        assert readback.returncode == 0, readback.stderr
        status = json.loads(readback.stdout.decode("utf-8"))
        assert user_text in status["run"]["userRequest"]["card"]
        assert status["run"]["userRequest"]["request_id"] == token
        # C23: native tool evidence must survive invalid UTF-8 on either stream;
        # the Unicode JSON round-trip above never exercises raw legacy bytes.
        evidence = run_audit_process(
            [sys.executable, "-B", "-c",
             "import sys; sys.stdout.buffer.write(b'evidence: caf\\xc3\\xa9 \\x96\\n'); "
             "sys.stderr.buffer.write(b'diagnostic: \\x96\\n')"],
            cwd=workspace, env=legacy_env, timeout=10,
        )
        assert evidence.returncode == 0
        assert evidence.stdout == "evidence: café \ufffd\n"
        assert evidence.stderr == "diagnostic: \ufffd\n"
        stale = client.call("harness_chat", text="Yes", reply_to="stale-token", error=True)
        assert stale["structuredContent"]["refusal"]["code"] == "user-subject-stale"
        assert client.current["userRequest"]["request_id"] == token
        client.words("How will it preserve invalid UTF-8 bytes?")
        assert token == client.offered["packet"]["facts"]["displayedSubject"]["request_id"]
        assert "invalid UTF-8" in str(client.offered["packet"]["facts"]["sources"])
        client.answer(judge_reply(client.current["question"], decision="unclear", verdict="inconclusive"))
        with open_plan_store_readonly(workspace) as store:
            assert (approvals.current(store, "A") or {}).get("state") != "approved"
        client.words("Please improve the proposal before I decide.")
        client.answer(dict(option="replan",rationale="Revise the proposal before deciding",content={}))
        assert client.current["question"]["purpose"] == "understanding"
        revised = json.loads(json.dumps(content))
        revised["nodes"][0]["text"] += " Preserve invalid UTF-8 bytes; caf\u00e9 \u4e2d\u6587."
        client.answer(dict(option="ready", rationale="Answer the question without narrowing", content=revised))
        advance_meaning(client, revised, stop_at=lambda r: (r.get("userRequest") or {}).get("gate") == "align+vision-lock" and not r.get("question"))
        assert "caf\u00e9 \u4e2d\u6587" in client.current["userRequest"]["card"]
        client.words("No, decline the proposal.")
        client.answer(judge_reply(client.current["question"], decision="decline"))
        assert client.current["state"] == "PAUSED"
    finally:
        client.close()

    refused = workspace / "host-refusal"
    refused.mkdir()
    (refused / "semantic-host-refusal").touch()
    client = LoopClient(refused, native=False)
    try:
        client.call("harness_plan_start", name="Host owns permissions", idea="Build the complete binary receipt dream")
        assert client.current["semanticHeld"] and client.current["effect"] is None
        assert client.current["userRequest"] is None
        assert "Provider host denied this call: enable access in the host" in client.current["reason"]
        with open_plan_store_readonly(refused) as store:
            assert not approvals.records(store, approvals.EVENT)
    finally:
        client.close()


@timed
def old_format_refusal_golden(workspace):
    """C18/C23/C26/I6: reconnect refuses old records without rewriting history.

    Current-format restart journeys never encounter a retired P wait or saved
    trace envelope; the old compatibility path silently upgraded both. Start
    through the actual chat producer, retain an old wait, then exercise reconnect
    and chat refusal before starting the same dream fresh in another workspace.
    """
    import sqlite3
    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core import run_loop
    from codex_harness.core.plan_store import open_plan_store

    for shape in ("permission", "question", "database"):
        root = workspace / shape
        root.mkdir(parents=True)
        (root / "receipt-gaps").touch()
        client = LoopClient(root, native=False)
        try:
            content = plan_content()
            client.call("harness_plan_start", name="Historical dream", idea=content["nodes"][0]["text"])
            advance_meaning(client, content, stop_at=lambda run:
                (run.get("userRequest") or {}).get("gate") == "align+vision-lock" and not run.get("question"))
            with open_plan_store(root) as store:
                retained = run_loop.cursor(store)
                if shape == "permission":
                    retained.update(state="WAIT_USER", userRequest=dict(gate="invocations", subject=dict(kind="P")))
                elif shape == "question":
                    retained.update(userRequest=None, question=dict(family="author", purpose="trace",
                        answerSchema=dict(type="object", properties=dict(records=dict(type="array")))))
                with store._transaction():
                    run_loop.save(store, retained)
                if shape == "database":
                    store.connection.execute("ALTER TABLE meta DROP COLUMN locked_vision_json")
                original = store.connection.execute("SELECT payload_json FROM events WHERE kind=? ORDER BY id DESC LIMIT 1", (run_loop.EVENT,)).fetchone()[0]
                events = store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            launches = root / ".codex-harness/scripted-review-invocations.jsonl"
            before = launches.read_bytes()
            database = root / ".codex-harness/plan.sqlite3"
            saved_bytes = database.read_bytes()
            notice = client.reconnect(error=True)
            refusal = notice["structuredContent"]["refusal"]
            assert refusal["code"] == "run-format-unsupported", notice
            assert "new workspace" in refusal["reason"] and "harness_chat" in refusal["nextAction"]
            refused = client.call("harness_chat", text="Continue the historical dream", error=True)
            assert refused["structuredContent"]["refusal"]["code"] == refusal["code"]
            assert launches.read_bytes() == before, "An old run launched a provider"
            assert database.read_bytes() == saved_bytes, "Refusal changed the historical database"
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
                assert connection.execute("SELECT payload_json FROM events WHERE kind=? ORDER BY id DESC LIMIT 1", (run_loop.EVENT,)).fetchone()[0] == original
                assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events, "Refusal rewrote historical state"
        finally:
            client.close()
    fresh = LoopClient(workspace / "fresh", native=False)
    try:
        fresh.call("harness_plan_start", name="Fresh dream", idea=content["nodes"][0]["text"])
        advance_meaning(fresh, content, stop_at=lambda run:
            (run.get("userRequest") or {}).get("gate") == "align+vision-lock" and not run.get("question"))
        assert fresh.current["userRequest"]["subject"]["kind"] == "A"
    finally:
        fresh.close()


@timed
def missing_kickoff_golden(workspace):
    """C20/C23/I6/I10: lost kickoff authority refuses reconnect without launches.

    Recovery used to adopt changed settings as invocation authority. Ordinary
    reconnect journeys retain setup, so they cannot catch that escalation or a
    startup exception disappearing into a background thread instead of chat.
    """
    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core import run_loop
    from codex_harness.core.plan_store import open_plan_store

    workspace.mkdir()
    (workspace / "receipt-gaps").touch()  # Existing real provider-boundary launch log.
    client = LoopClient(workspace, native=False)
    try:
        content = plan_content()
        client.call("harness_plan_start", name="Retained kickoff", idea=content["nodes"][0]["text"])
        advance_meaning(client, content, stop_at=lambda run:
            (run.get("userRequest") or {}).get("gate") == "align+vision-lock" and not run.get("question"))
        with open_plan_store(workspace) as store:
            retained = run_loop.cursor(store)
            retained.pop("setup")  # Persistence-loss fault, after a real canonical kickoff.
            with store._transaction():
                run_loop.save(store, retained)
        changed = kickoff_content()
        changed["roleTuples"] = [dict(role="planner", name="changed", provider="copilot", model="gpt-6.1-terra", effort="high")]
        (workspace / ".codex-harness/setup.json").write_text(json.dumps(changed), encoding="utf-8")
        launches = workspace / ".codex-harness/scripted-review-invocations.jsonl"
        before = launches.read_bytes()
        client.reconnect()
        notice = client.take_reply(None, timeout=10)
        refusal = notice["params"]["data"]["refusal"]
        assert refusal["code"] == "frozen-kickoff-missing"
        assert "frozen kickoff" in refusal["reason"] and "new run" in refusal["nextAction"]
        refused = client.call("harness_chat", text="Continue the retained dream", error=True)
        assert refused["structuredContent"]["refusal"]["code"] == refusal["code"]
        assert "new run" in refused["structuredContent"]["refusal"]["nextAction"]
        assert launches.read_bytes() == before, "Missing kickoff must not launch any provider"
    finally:
        client.close()


@timed
def reviewer_order_golden(workspace):
    """C11/I6: frozen role pins survive reconnect and changed setup bytes.

    Old chat-authored tuples concealed r17's two configuration owners and
    Codex-only fixtures missed the Copilot/Claude semantic launch adapters. This
    traverses real kickoff->model->selection, with no catalog negotiation.
    Resume at A must refresh its reply token's control generation; active-worker
    pause coverage misses a refreshed decision rejected as already superseded.
    """
    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core.plan_store import open_plan_store_readonly
    settings = kickoff_content()
    settings["roleTuples"] = [dict(role="planner", name="pinned", provider="copilot", model="gpt-6.1-sol", effort="high"),
        dict(role="judge", name="judge", provider="codex", model="gpt-6.1-sol", effort="high"),
        dict(role="mechanical", name="decisions", provider="claude", model="claude-sonnet-5-5", effort="medium")]
    settings["reviewers"][0].update(provider="copilot")
    workspace.mkdir(parents=True,exist_ok=True)
    (workspace / "copilot-review-retry").touch()
    (workspace / "existing.py").write_text("def existing_behavior(): return 'retained brownfield behavior'\n")
    (workspace / "test_existing.py").write_text("from existing import existing_behavior\ndef test_existing_behavior(): assert existing_behavior() == 'retained brownfield behavior'\n")
    (workspace / "component.VUE").write_text("<template>Existing supported source</template>")
    (workspace / "oversized.py").write_text("OVERSIZED_CONTEXT_MUST_BE_OMITTED"*1024)
    outside = workspace.parent / (workspace.name+"-outside")
    outside.mkdir()
    (outside / "private.py").write_text("OUTSIDE_CONTEXT_MUST_NOT_ENTER_THE_PACKET")
    linked = workspace / "linked-context"
    if os.name == "nt":
        subprocess.run(["cmd.exe","/c","mklink","/J",str(linked),str(outside)],check=True,capture_output=True)
    else:
        linked.symlink_to(outside,target_is_directory=True)
    generated = workspace / "generated"
    generated.mkdir()
    for i in range(520):
        (generated / (str(i)+".js")).write_text("// bounded discovery fixture")
    client = LoopClient(workspace, kickoff=settings)
    client.expected_review_tier = "cross-provider"
    try:
        content = plan_content()
        client.call("harness_plan_start", name="Frozen roles", idea=content["nodes"][0]["text"])
        frozen = client.current["setup"]
        assert client.current["question"]["purpose"] == "understanding"
        assert client.current["userRequest"] is None
        repository = client.offered["packet"]["facts"]["repository"]
        assert {r["path"] for r in repository["files"]} >= {"existing.py","test_existing.py"}
        assert "retained brownfield behavior" in str(repository)
        assert "component.VUE" in {r["path"] for r in repository["files"]}
        assert repository["discoveryTruncated"] and repository["omittedFiles"] is None
        assert "OUTSIDE_CONTEXT_MUST_NOT_ENTER_THE_PACKET" not in json.dumps(repository)
        oversized = next(r for r in repository["files"] if r["path"] == "oversized.py")
        assert "content" not in oversized and oversized["contentStatus"]

        client.answer(dict(option="ready", rationale="Use the pinned high tier", content=content))
        advance_meaning(client, content, stop_at=lambda r: (r.get("userRequest") or {}).get("gate") == "align+vision-lock" and not r.get("question"))
        client.call("harness_chat",text="/pause")
        assert client.current["state"] == "PAUSED"
        client.call("harness_chat",text="/resume")
        assert client.current["userRequest"]["gate"] == "align+vision-lock"
        (workspace / ".codex-harness/setup.json").write_text('{"worker":{"provider":"copilot","model":"changed","effort":"low"}}',encoding="utf-8")
        client.reconnect()
        assert client.current["setup"] == frozen
        client.words()
        assert client.current["question"]["role"] == "mechanical"
        assert client.offered["packet"]["facts"]["setup"] == frozen
        client.answer(judge_reply(client.current["question"]))
        advance_meaning(client, content, stop_at=lambda r: (r.get("userRequest") or {}).get("gate") == "plan-execution" and not r.get("question"))
        with open_plan_store_readonly(workspace) as store:
            from codex_harness.core.planner_review import selection_inputs, delivery_review_selection
            _, selection, _ = selection_inputs(store)
            assert delivery_review_selection(selection["fields"])[0] == frozen["reviewers"]
        assert client.current["userRequest"]["gate"] == "plan-execution"
        assert (workspace / ".codex-harness/copilot-review-count").read_text() == "3"
    finally:
        client.close()
        # The boundary assertion is complete; later product snapshots must not
        # inherit this fixture's external junction.
        linked.unlink() if linked.is_symlink() else linked.rmdir()


@timed
def approval_order_golden(workspace):
    """C8/I10: qualified words do not grant the displayed subject.

    r16 guessed hashes are deleted with the public envelope. A real opaque reply
    now binds the actual words and full subject before mechanical interpretation.
    """
    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core.plan_store import open_plan_store_readonly
    client = LoopClient(workspace)
    try:
        content = plan_content()
        client.call("harness_plan_start", name="Bound approval", idea=content["nodes"][0]["text"])
        client.answer(dict(option="ready", rationale="Present the full vision", content=content))
        advance_meaning(client, content, stop_at=lambda r: (r.get("userRequest") or {}).get("gate") == "align+vision-lock" and not r.get("question"))
        displayed = client.current["userRequest"]
        client.words("Yes, but remove binary input.")
        assert displayed["plan_revision"] in str(client.offered["packet"]["facts"]["sources"])
        client.answer(judge_reply(client.current["question"], decision="changes_requested"))
        with open_plan_store_readonly(workspace) as store:
            assert (approvals.current(store, "A") or {}).get("state") != "approved"
        assert client.current["question"]["purpose"] == "understanding"
    finally:
        client.close()


@timed
def cockpit_golden(tmp_path, run_path, final_status, surface):
    """C20/C26: canonical accepted state reaches HTTP and the read-only cockpit.

    Synthetic UI fixtures miss revision/blocker/next-step divergence and asset
    wiring; require the saved product state and no store writes while viewing it.
    """
    # The real saved producer reaches HTTP and the shipped browser view.
    # Catch revision/blocker/next-step divergence and broken asset wiring;
    # planning-only tests and synthetic UI fixtures miss this integration.
    from tests.python.core.cockpit_server_support import get, running

    before = {p: p.read_bytes() for p in (tmp_path / ".codex-harness").rglob("*") if p.is_file()}
    run_path = Path(run_path)
    runs_root = run_path.parents[2] if run_path.parent.name == "evidence" else run_path.parents[1]
    with running(tmp_path, workspace=tmp_path, runs_root=runs_root) as connection:
        status_code, _, body = get(connection, "/api/status")
        assert status_code == 200
        observed = json.loads(body)["plan"]
        for key in ("plan_revision", "pending_gate", "open_findings", "next", "acceptance"):
            assert observed[key] == final_status[key], key
        if surface == "web":
            from codex_harness.core.audit_process import run_audit_process

            checked = run_audit_process(
                [
                    "node",
                    str(ROOT / "tests/browser/cockpit/checks.js"),
                    f"http://127.0.0.1:{connection.port}",
                    observed["plan_revision"],
                    observed["next"],
                ],
                cwd=tmp_path,
                timeout=45,
            )
            assert checked.returncode == 0, (checked.stdout, checked.stderr)
            assert checked.audit_control["quiescenceVerified"]
    after = {p: p.read_bytes() for p in (tmp_path / ".codex-harness").rglob("*") if p.is_file()}
    assert before == after
    print(
        "golden cockpit HTTP: canonical C/delivery accepted after preview support; read-only parity",
        file=sys.stderr,
        flush=True,
    )


# Retained SOURCE-required calls, not measured runtime: ab8b85a,
# tests/python/core/test_golden_path.py. Each tuple is (base line, actual tool).
# Variable polls/probes add traffic beyond this lower bound; their mandatory
# first status reads and the repeated B card are included. The A/B/C semantic
# provider submits and reads through base scripted_judgment:115/117.
# Base B happened before review; classify it by its charter meaning, not that
# obsolete stored phase. The controller now correctly reviews before B.
RETAINED_PHASE_CALLS = {
    "Align/Expand/A": (
        (3327, "harness_plan_status"),
        (3328, "harness_plan_start"),
        (3424, "harness_upsert_nodes"),
        (3425, "harness_understanding_card"),
        (3306, "harness_record_chat_confirmation"),
        (115, "harness_submit_judgment"),
        (117, "harness_judgment"),
        (115, "harness_submit_judgment"),
        (117, "harness_judgment"),
        (3428, "harness_advance"),
    ),
    "Spec/Bead plan": (
        (3429, "harness_preflight"),
        (3460, "harness_reviewer_choice"),
        (3461, "harness_compile"),
    ),
    "Review/B": (
        (3479, "harness_prepare_chat_confirmation"),
        (3480, "harness_prepare_chat_confirmation"),
        (3306, "harness_record_chat_confirmation"),
        (115, "harness_submit_judgment"),
        (117, "harness_judgment"),
        (3507, "harness_advance"),
        (3508, "harness_roundtable"),
        (3548, "harness_advance"),
    ),
    "Publication": ((3549, "harness_publish"), (3550, "harness_advance")),
    "Build/Observe/Judge/Steer": (
        (3574, "harness_worker_start"),
        (3577, "harness_worker_status"),
        (3798, "harness_plan_status"),
        (205, "harness_accept_assess"),
        (115, "harness_submit_judgment"),
        (117, "harness_judgment"),
        (232, "harness_accept_assess"),
        (3807, "harness_plan_status"),
        (3808, "harness_advance"),
    ),
    "Acceptance/C": (
        (3813, "harness_accept_run"),
        (3830, "harness_accept_status"),
        (3835, "harness_accept_review"),
        (3855, "harness_accept_status"),
        (3864, "harness_accept"),
        (3865, "harness_plan_status"),
        (115, "harness_submit_judgment"),
        (117, "harness_judgment"),
        (3867, "harness_plan_status"),
    ),
}


@timed
def r18_authoring_golden(workspace):
    """C9/C16/C18: reciprocal semantics survive repair; structural cycles do not.

    The old manual-answer journey hid the lost relationship, empty member set and
    per-request retry reset. This exercises the real decoder, graph writer,
    canonical retry owner, typed link repair and unchanged-facts admission guard.
    r21 adds reciprocal test/stop-condition and test/decision links: the old
    acyclic fixtures missed recursive revision rejection and parents-only repair.
    Round 2 protects retained STOP evidence/judgment after T changes and is
    rebound, and accepted content from repair overreach; happy repair missed both.
    Round 3 protects sequential parent/path correction: a later refused execution
    section must remain editable, rather than exhausting a confined repair.
    TRACE1 replays r22 omitted targets/scenarios, invented parents and path codeRefs.
    Former full-envelope fixtures missed partial-answer membership loss. The same
    journey must reach B with all declared members and only named repair edits.
    Exact-fix messages must name the node/section; shortest union-branch errors
    previously asked for a different fix despite rejecting the right bad value.
    REV1B protects retained brownfield ancestry: repair the moved parent, without
    rewriting baseline provenance. Detector-only field scopes missed that cause.
    TRACE1B round 2 requires all test module/level and target/scenario sections
    together: a partial answer gets every gap and reaches B with one exact fix.
    Previously complete fake answers hid the serial-obligation failure.
    Round 3 preserves evidenceRefs history stamps during a stale-result replan;
    unchanged/evidence-free fixtures missed append-only history rejection.
    Round 3 protects real emission from optional properties and transports literal
    JSON without recursive provider schemas; one sparse fix includes JSON gaps.
    An initial no-result iteration reaches B before a real canonical observation.
    Duplicate provenance/parents must not hide independent ancestry/root defects;
    semantic authoring must refuse unit levels while retaining the sparse fix.
    TRACE1B reuses an observed canonical result in a new iteration after replan;
    initial-plan fixtures missed enums that excluded ordinary result nodes.
    """
    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
    content = plan_content()
    receipt = next(n for n in content["nodes"] if n["id"] == "T_RECEIPT")
    for handle, title in (("T_INPUT", "Reject malformed input without saving"), ("T_RECOVERY", "Preserve receipt after a failed request")):
        content["nodes"].append({**json.loads(json.dumps(receipt)), "id":handle, "title":title})

    content["nodes"].extend([
        dict(id="MEASURE", kind="metric", title="Receipt integrity", text="Measure correct receipts", parents=["V1"], fields=dict(definition="Correct receipt fraction", unit="fraction")),
        dict(id="STOP", kind="stop-condition", title="Receipt goal", text="Stop at complete integrity", parents=["V1"], fields=dict(metricRefs=["MEASURE"], condition="All receipts match", testRefs=["T_RECEIPT"])),
        dict(id="CHOICE", kind="decision", title="Verify bytes", text="Use a binary receipt", parents=["V1"], fields=dict(testRefs=["T_RECEIPT"])),
        dict(id="AWAITING_ITERATION",kind="iteration",title="Initial receipt iteration",text="Measure the first receipt before any observation exists",parents=["MEASURE"],fields=dict(metricRefs=["MEASURE"],stopConditionRefs=["STOP"])),
    ])
    next(n for n in content["nodes"] if n["id"] == "T_RECEIPT")["fields"].update(stopConditionRefs=["STOP"], decisionRefs=["CHOICE"])
    scenario = content["nodes"][0]["fields"]["scenarios"]["scenarios"][5]
    scenario["setup"]["snapshots"] = [dict(id="input-baseline",path="checks/input.json")]
    scenario["observe"].extend([dict(path="checks/input.json",bytesUnchangedFrom="input-baseline",fileJsonEquals=[0,255,65]),
                                dict(path="checks/input.json",bytesUnchangedFrom="input-baseline",fileAbsent=False)])
    def begin(root):
        client = LoopClient(root)
        with open_plan_store(root, root.name) as store:
            store.upsert_node(dict(id="V1", kind="vision", title=root.name,
                text=content["nodes"][0]["text"], parents=[], fields=dict(planMode="semantic-v1")))
            store.upsert_node(dict(id="UNRELATED_OPERATOR_NOTE",kind="decision",title="Unrelated retained note",text="Unrelated telemetry "*1000,parents=["V1"],fields={}))
        client.call("harness_plan_start", name="R18 replay", idea=content["nodes"][0]["text"])
        advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "plan-outline")
        outline = json.loads(provider_answer(root,client.current["question"],dict(option="ready",rationale="Declare the complete receipt plan",content=content)))
        unavailable = json.loads(json.dumps(outline)); unavailable["metric"] = []
        unavailable["amendment"].append(dict(ref=None,title="Unbound change proposal",text="Explain the receipt change"))
        client.answer(json.dumps(unavailable))
        assert all(k in client.offered["packet"]["exactFix"] for k in ("fields.metricRefs","fields.sourceRef")), client.offered["packet"]["exactFix"]
        client.answer(json.dumps(outline))
        advance_meaning(client, content, stop_at=lambda r: (r.get("question") or {}).get("purpose") == "trace")
        assert "Unrelated telemetry" not in json.dumps(client.offered["packet"])
        return client
    client = begin(workspace)
    try:
        from codex_harness.core.review_provider import _launch_review
        invalid = json.loads(json.dumps(client.current["question"]["answerSchema"]))
        invalid["properties"]["edits"]["required"].pop()
        invalid_path = workspace / "invalid-emission.json"
        try: _launch_review(dict(provider="codex"),"",sys.executable,answer_schema=invalid,schema_path=invalid_path)
        except ValueError as error: assert "CODE defect" in str(error) and not invalid_path.exists()
        else: raise AssertionError("The emitter accepted a non-strict trace schema")
        raw = json.loads(provider_answer(workspace, client.current["question"], dict(option="ready",rationale="Author the accepted receipt plan",content=content)))
        def key(handle, section):
            return fixture_id(workspace,handle)+":"+section
        def partial(edits):
            return json.dumps(dict(edits=edits,rationale="Correct only the named sections",reply="Retain every other member."))
        # r23: every test's required fields and every executable section are
        # missing together; a complete refusal permits one sparse exact fix.
        if isinstance(raw["edits"],list): raw["edits"] = {e["ref"]+":"+e["section"]:e["value"] for e in raw["edits"]}
        omitted = {k:v for k,v in raw["edits"].items() if k.endswith((":target",":scenario",":fields.module",":fields.level",":fields.ownedFiles",":fields.semanticPhase")) or k in {key("MEASURE","fields.unit"),key("STOP","fields.condition")}}
        assert len({k.rsplit(":",1)[0] for k in omitted if k.endswith(":fields.module")}) > 2
        required = client.current["question"]["answerSchema"]["properties"]["edits"].get("required",[])
        assert set(omitted) <= set(required), "The advertised answer permits an incomplete trace"
        request = client.current["question"]["requestId"]
        bad = {k:v for k,v in raw["edits"].items() if k not in omitted}
        omitted[key("T_RECEIPT","parents")] = bad[key("T_RECEIPT","parents")]
        bad[key("T_RECEIPT","parents")] = ["V1"]
        bad[key("T_RECOVERY","fields.level")] = "unit"
        bad[key("T_RECEIPT","fields.codeRefs")] = ["server.py"]
        literal_key = next(k for k,v in omitted.items() if k.endswith(":scenario") and any(o.get("fileJsonEquals") for o in v["observe"]))
        bad[literal_key] = json.loads(json.dumps(omitted[literal_key]))
        next(o for o in bad[literal_key]["observe"] if o.get("fileJsonEquals"))["fileJsonEquals"]["value"] = "{invalid JSON"
        client.answer(partial(bad))
        assert all(k in client.offered["packet"]["exactFix"] for k in omitted), client.offered["packet"]["exactFix"]
        assert "codeRefs" in client.offered["packet"]["exactFix"]
        client.answer(partial(omitted))
        advance_meaning(client,content,stop_at=lambda r:(r.get("userRequest") or {}).get("gate") == "plan-execution" and not r.get("question"))
        assert (client.current.get("userRequest") or {}).get("gate") == "plan-execution" and client.current["userRequest"]["card"], client.current
        client.reconnect()
        with open_plan_store(workspace) as store:
            assert store._judgment_record(request)["fixesIssued"] == 1, "Complete corrections consumed more than one exact fix"
            spine = store.spine()
            vf = spine.node(spine.ids("vision")[0])["fields"]
            assert set(vf["targets"]) == {fixture_id(workspace,k) for k in content["nodes"][0]["fields"]["targets"]}
            assert {s["id"] for s in vf["scenarios"]["scenarios"]} == {fixture_id(workspace,s["id"]) for s in content["nodes"][0]["fields"]["scenarios"]["scenarios"]}
            assert not spine.ids("result") and not spine.node(fixture_id(workspace,"AWAITING_ITERATION"))["fields"].get("resultRefs"), "Initial iteration invented evidence"
            test_revision = spine.revision(fixture_id(workspace,"T_RECEIPT"))
            stop_revision = spine.revision(fixture_id(workspace,"STOP"))
            for origin, field, target in (("T_RECEIPT","stopConditionRefs","STOP"), ("STOP","testRefs","T_RECEIPT"),
                                          ("T_RECEIPT","decisionRefs","CHOICE"), ("CHOICE","testRefs","T_RECEIPT")):
                link = spine.node(fixture_id(workspace,origin))["fields"][field][0]
                assert link == dict(id=fixture_id(workspace,target), revision=spine.revision(fixture_id(workspace,target))), "Writer and reopened reader disagree about the referenced content"
            store.upsert_node(dict(id="STOP_RESULT", kind="result", title="Retained stop verification", text="Observed the goal", parents=[fixture_id(workspace,"STOP")],
                fields=dict(evidence=dict(itemRef=dict(id=fixture_id(workspace,"STOP"), revision=stop_revision), candidate="receipt-candidate",
                    source=dict(reference="fixture:stop-measurement", realProvider=False, historical=False), observation=dict(reference="fixture:stop-readback", outcome="passed"), state="integrated"))))
            measure = fixture_id(workspace,"MEASURE")
            store.upsert_node(dict(id="METRIC_RESULT",kind="result",title="Observed receipt metric",text="Measured the receipt fraction",parents=[measure],fields=dict(evidence=dict(itemRef=dict(id=measure,revision=store.spine().revision(measure)),candidate="receipt-candidate",source=dict(reference="fixture:metric",realProvider=False,historical=False),observation=dict(reference="fixture:metric-readback",outcome="passed"),state="integrated"))))
            verdict = scripted_judgment(store, question="Judge the retained stop goal", subject_ids=[fixture_id(workspace,"STOP")], meaning=dict(assessment="The observed receipt meets the goal"))["current"]
            assert verdict["positiveCredit"] and store.spine().evidence_states()[fixture_id(workspace,"STOP")]["state"] == "integrated"
            history = dict(id="HISTORY",kind="decision",title="Retained receipt evidence history",text="Keep the original observed result stamp",parents=["V1"],fields=dict(evidenceRefs=[dict(id="STOP_RESULT",revision=store.spine().revision("STOP_RESULT"))]))
            store.upsert_node(history)
            content["nodes"].append({**history,"fields":dict(evidenceRefs=["STOP_RESULT"])})
            assert store.judgment(verdict["id"])["positiveCredit"], "Unrelated history changed the stop's proof"
            imported = store.spine().to_dict()
        next(n for n in content["nodes"] if n["id"] == "AWAITING_ITERATION")["fields"]["resultRefs"] = ["METRIC_RESULT"]
        # Fresh-store import has no transaction preimage: stale declared links
        # must still remove credit, rather than being rebound by the reader.
        next(n for n in imported["nodes"] if n["id"] == fixture_id(workspace,"T_RECEIPT"))["text"] += "; changed outside the retained binding"
        stale_path = workspace.parent / (workspace.name + "-stale.json")
        stale_path.write_text(json.dumps(imported), encoding="utf-8")
        imported_root = workspace.parent / (workspace.name + "-imported")
        imported_root.mkdir()
        with open_plan_store(imported_root) as store:
            store.import_spine(stale_path)
            assert store.spine().evidence_states()[fixture_id(workspace,"STOP")]["state"] == "stale"
            imported_verdict = scripted_judgment(store, question="Judge the imported stop goal", subject_ids=[fixture_id(workspace,"STOP")], meaning=dict(assessment="Attempted credit on stale links"))["current"]
            assert not imported_verdict["positiveCredit"], "Imported stale edge received new judgment credit"
        # Re-authoring existing identities must not collapse two proposed meanings
        # or duplicate one snapshot reused by independent file checks.
        next(n for n in content["nodes"] if n["id"] == "T_RECEIPT")["text"] += "; verify the complete byte sequence"
        content["nodes"].append(dict(id="NEXT_STOP",kind="stop-condition",title="Replanned receipt goal",text="Keep the complete receipt goal after replan",parents=["V1"],fields=dict(metricRefs=["MEASURE"],condition="All receipts still match",testRefs=["T_RECEIPT"])))
        client.send_steer(rationale="Recheck the same complete plan after its relationship repair.")
        client.progress()
        client.answer(dict(option="replan",rationale="Retain the complete plan and verify its linked observations",content={}))
        advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "plan-outline")
        outline = json.loads(provider_answer(workspace,client.current["question"],dict(option="ready",rationale="Retain every current requirement",content=content)))
        duplicate = json.loads(json.dumps(outline));duplicate["feature"].append(dict(duplicate["feature"][0]))
        client.answer(json.dumps(duplicate))
        duplicate = json.loads(json.dumps(outline));duplicate["targets"].append(dict(duplicate["targets"][0]))
        client.answer(json.dumps(duplicate))
        client.answer(json.dumps(outline))
        rejected = json.loads(provider_answer(workspace,client.current["question"],dict(option="ready",rationale="Recheck the proposed links",content=content)))
        rejected["edits"][key("NEXT_STOP","fields.metricRefs")] = []
        for _ in range(3): client.answer(json.dumps(rejected))
        with open_plan_store_readonly(workspace) as store:
            preserved = store.spine().to_dict()
        assert "fields.metricRefs" in client.current["question"]["context"]["rejectedError"]
        rejected["edits"] = {key("NEXT_STOP","fields.metricRefs"):[fixture_id(workspace,"MEASURE")]}
        remove = json.loads(json.dumps(rejected)); remove["remove"] = ["UNRELATED_OPERATOR_NOTE"]
        client.answer(json.dumps(remove))
        with open_plan_store_readonly(workspace) as store:
            assert store.spine().to_dict() == preserved, "Repair deleted unrelated accepted content"
        altered = json.loads(json.dumps(rejected))
        altered["edits"][key("NEXT_STOP","fields.condition")] = "Overwrite an unrelated accepted condition"
        client.answer(json.dumps(altered))
        with open_plan_store_readonly(workspace) as store:
            assert store.spine().to_dict() == preserved, "Repair deleted or changed unrelated accepted content"
        client.answer(json.dumps(rejected))
        advance_meaning(client,content,stop_at=lambda r:(r.get("userRequest") or {}).get("gate") == "plan-execution" and not r.get("question"))
        assert client.current["userRequest"]["gate"] == "plan-execution"
        with open_plan_store(workspace) as store:
            spine = store.spine()
            assert spine.revision(fixture_id(workspace,"STOP")) == stop_revision, "Dependency freshness changed the stop's own identity"
            assert spine.revision(fixture_id(workspace,"T_RECEIPT")) != test_revision
            assert spine.node(fixture_id(workspace,"STOP"))["fields"]["testRefs"][0]["revision"] == spine.revision(fixture_id(workspace,"T_RECEIPT"))
            assert spine.evidence_states()[fixture_id(workspace,"STOP")]["state"] == "stale"
            assert not store.judgment(verdict["id"])["positiveCredit"], "STOP-only judgment survived a changed and rebound test"
            # Retained immutable history must not poison the next observation or judgment.
            stop = spine.node(fixture_id(workspace,"STOP"))
            stop["fields"]["evidenceRefs"] = [dict(id="STOP_RESULT", revision=spine.revision("STOP_RESULT"))]
            store.upsert_node(stop)
            test = store.spine().node(fixture_id(workspace,"T_RECEIPT"))
            test["fields"]["stopConditionRefs"] = [dict(id=stop["id"], revision=store.spine().revision(stop["id"]))]
            store.upsert_node(test)
            fresh = store.spine().node("STOP_RESULT")
            fresh.update(id="A_FRESH_RESULT", title="New stop verification")
            fresh["fields"]["evidence"].update(itemRef=dict(id=stop["id"], revision=store.spine().revision(stop["id"])), state="integrated",
                observation=dict(reference="fixture:fresh-stop-readback", outcome="passed"))
            store.upsert_node(fresh)
        with open_plan_store(workspace) as store:
            assert store.spine().node("A_FRESH_RESULT")["fields"]["evidence"]["state"] == "integrated"
            assert scripted_judgment(store, question="Judge the repaired stop goal", subject_ids=[stop["id"]], meaning=dict(assessment="Fresh verification after repair"))["current"]["positiveCredit"]

        content["nodes"].append(dict(id="ITERATION",kind="iteration",title="Receipt goal iteration",text="Evaluate the observed receipt goal",parents=["MEASURE"],
            fields=dict(metricRefs=["MEASURE"],resultRefs=["A_FRESH_RESULT"],stopConditionRefs=["STOP"])))
        from tests.python.core.semantic_wiring_support import node
        with open_plan_store(workspace) as store:
            requirement = fixture_id(workspace, "R4")
            feature = store.spine().node(fixture_id(workspace, "F1"))
            apply_content(store, dict(nodes=[node("BASE_OTHER", "feature", ["V1"]),
                node("BASE_TEST", "test", [requirement], module="sensor/readback", level="semantic"),
                node("BASE_CODE", "code", ["BASE_TEST"])]))
            refs = {key: dict(id=key, revision=store.spine().revision(key)) for key in ("BASE_TEST", "BASE_CODE")}
            store.upsert_node(node("BASE_RESULT", "result", ["BASE_CODE"], evidence=dict(itemRef=refs["BASE_CODE"], candidate="baseline-sensor",
                source=dict(reference="baseline:sensor", realProvider=False, historical=True), observation=dict(reference="baseline:readback", outcome="passed"), state="integrated")))
            baseline = dict(source="existing-repository", behavior="Retain sensor readback", rationale="Preserve observed behavior",
                codeRefs=[refs["BASE_CODE"]], testRefs=[refs["BASE_TEST"]], resultRefs=[dict(id="BASE_RESULT", revision=store.spine().revision("BASE_RESULT"))])
            feature["fields"]["baseline"] = baseline
            store.upsert_node(feature)
            unrelated = {key: store.spine().node(key) for key in ("BASE_OTHER", "UNRELATED_OPERATOR_NOTE", "HISTORY")}
        client.send_steer(rationale="Retain the baseline while revisiting requirement ownership.")
        client.progress()
        client.answer(dict(option="replan", rationale="Preserve baseline provenance and accepted scope", content=content))
        advance_meaning(client, content, stop_at=lambda r: (r.get("question") or {}).get("purpose") == "trace")
        packet = client.offered["packet"]
        moved = json.loads(provider_answer(workspace, client.current["question"], dict(option="ready", rationale="Recheck ownership", content=content)))
        client.answer(json.dumps(moved))
        advance_meaning(client, content, stop_at=lambda r: (r.get("userRequest") or {}).get("gate") == "plan-execution" and not r.get("question"))
        assert (client.current.get("userRequest") or {}).get("gate") == "plan-execution", client.current
        # Existing ancestry and execution defects are still refused together at
        # their canonical batch owner; the strict question grants no unasked edits.
        from codex_harness.core.planner_nodes import batch_nodes
        from codex_harness.core.spine import SpineError
        with open_plan_store(workspace) as store:
            before = store.spine().to_dict()
            changes = {n["id"]:n for n in json.loads(json.dumps(before))["nodes"]}
            original_parents = changes[requirement]["parents"][:]
            changes[requirement]["parents"] = ["BASE_OTHER"]
            changes[feature["id"]]["fields"]["baseline"]["testRefs"] *= 2
            test_ids = [fixture_id(workspace,h) for h in ("T_INPUT","T_RECOVERY")]
            changes[test_ids[0]]["parents"] *= 2
            for identity in test_ids: changes[identity]["fields"]["execution"]["testRoots"] = ["../outside"]
            try: batch_nodes(store,list(changes.values()),lambda n:None)
            except SpineError as error:
                refused = error.editable_fields
                assert refused[requirement] == ["parents"] and "fields.baseline.testRefs" in refused[feature["id"]]
                assert all("fields.execution.testRoots" in refused[identity] for identity in test_ids) and "parents" in refused[test_ids[0]]
            else: raise AssertionError("An invalid ancestry/execution batch was published")
            assert store.spine().to_dict() == before, "A refused batch changed canonical state"
        with open_plan_store_readonly(workspace) as store:
            assert dict(id="A_FRESH_RESULT",kind="result",title="New stop verification") in packet["facts"]["referenceInventory"]
            assert store.spine().node(feature["id"])["fields"]["baseline"] == baseline, "Repair rewrote retained baseline provenance"
            assert store.spine().node(requirement)["parents"] == original_parents
            assert all(store.spine().node(key) == value for key, value in unrelated.items()), "Ancestry repair changed unrelated accepted nodes"
            result = store.spine().node(fixture_id(workspace,"ITERATION"))["fields"]["resultRefs"][0]
            assert result == dict(id="A_FRESH_RESULT",revision=store.spine().revision("A_FRESH_RESULT")), "Replan lost the existing observed result"

    finally:
        client.close()
    root = workspace / "exhaustion"
    client = begin(root)
    try:
        for _ in range(3): client.answer("{")
        forbidden = json.dumps(dict(action="ask_user",question="Supply a member ID",amendments=[],rationale="Missing internal identity",reply="Supply a member ID"))
        for _ in range(3): client.answer(forbidden)
        assert client.current["state"] == "PAUSED" and not client.current["userRequest"]
        words = "Continue with exactly the same evidence and requirements."
        client.send_steer(rationale=words)
        client.progress()
        client.answer(dict(option="pause",rationale="No new facts support another attempt",content={}))
        client.send_steer(rationale=words)
        client.progress()
        assert client.current["state"] == "PAUSED" and not client.current.get("question")
        assert "No progress" in client.current["reason"] and "Seldon owns internal identifiers" in client.current["nextAction"]
    finally:
        client.close()


@timed
def automatic_retry_golden(workspace):
    """C4/C18/C23: three attempts, retained raw replay, one bounded steer.

    Manual submissions missed retries abandoned by the scheduler and credit
    accidentally given to a raw return after its original effect lease expired.
    Brief deletion exception: late public answer and generic steer-content tests
    move to provider-boundary returns and actual words; no public answer tool remains.
    """
    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core.plan_store import open_plan_store_readonly
    workspace.mkdir()
    script_path = workspace / "semantic-retry-script.json"
    script_path.write_text(json.dumps(dict(understanding=2, **{"vision-expansion": 2}, trace=3)),encoding="utf-8")
    client = LoopClient(workspace)
    def wait(name):
        path = workspace / ".codex-harness" / (name + ".json")
        end = time.monotonic() + 45
        while not path.exists() and time.monotonic() < end:
            time.sleep(.05)
        assert path.exists(), client.log_path.read_text("utf-8")
        return json.loads(path.read_text("utf-8"))
    try:
        client.call("harness_plan_start", name="Exact fixes", idea="Build the complete binary receipt dream")
        assert {r["role"] for r in client.current["setup"]["roleTuples"]} == {"planner", "judge", "mechanical"}
        retained = wait("semantic-raw-complete")
        client.reconnect()
        # Activation consumes verified originals without asking chat to supply meaning.
        end = time.monotonic() + 30
        while time.monotonic() < end:
            client.call("harness_plan_status")
            if not client.current.get("effect") and not client.current.get("question") and (client.current.get("userRequest") or {}).get("gate") == "align+vision-lock":
                break
            time.sleep(.1)
        assert client.current["userRequest"]["gate"] == "align+vision-lock", client.current
        with open_plan_store_readonly(workspace) as store:
            replies = [r for r in approvals.records(store,"run-invocation-completed") if r["effectId"] == retained["effectId"]]
            assert len(replies) == 1
            authored = [r for r in approvals.records(store,"run-semantic-answer") if r["requestId"] == retained["requestId"]]
            assert len(authored) == 1 and authored[0]["answer"]["understanding"] == plan_content()["nodes"][0]["text"]
            assert len([r for r in approvals.records(store,"judgment-response") if r["id"] == retained["requestId"] and r["response"] == retained["raw"]]) == 1
        client.words("Approve the exact displayed Vision Lock")
        assert client.current["state"] == "PAUSED" and not client.current["effect"], (client.current["state"],client.current["reason"])
        history_path = workspace / ".codex-harness/scripted-semantic-responses.jsonl"
        history = [json.loads(x) for x in history_path.read_text("utf-8").splitlines()]
        with open_plan_store_readonly(workspace) as store:
            for purpose in ("understanding", "vision-expansion", "trace"):
                rows = [r for r in history if r["purpose"] == purpose]
                assert len(rows) == 3 and [r["fixesIssued"] for r in rows] == [0,1,2], rows
                assert len({(r["requestId"],r["binding"]) for r in rows}) == 1
                record = store._judgment_record(rows[0]["requestId"])
                assert record["fixesIssued"] == 2 and record["owner"] is None
                assert record["disposition"] == ("steer" if purpose == "trace" else "complete")
            assert len([r for r in history if r["family"] == "direction"]) == 1
            observed = approvals.records(store,"run-invocation-observed")
            assert len(observed) == len(history)
            assert all(r["cleanup"]["quiescenceVerified"] and r["cleanup"]["activeProcessesAfterTeardown"] == 0 for r in observed)
            assert not store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchone()
        # Explicit new words can request another direction. Expired native output
        # is diagnostic; activation never grants it credit or launches it again.
        script_path.write_text(json.dumps(dict(trace=0)),encoding="utf-8")
        (workspace / "semantic-late-output").touch()
        client.send_steer(direction="replan", rationale="Resume planning from the retained evidence", resume=True)
        late = wait("semantic-late-complete")
        with open_plan_store_readonly(workspace) as store:
            raw = next(r for r in approvals.records(store,"run-invocation-observed") if r["effectId"] == late["effectId"])
            assert raw["eligible"] is False and raw["exitCode"] == 0 and raw["cleanup"]["quiescenceVerified"]
        client.reconnect()
        end = time.monotonic() + 15
        while time.monotonic() < end:
            client.call("harness_plan_status")
            if not client.current.get("effect") and client.current.get("semanticHeld"):
                break
            time.sleep(.1)
        assert client.current["semanticHeld"] and client.current["state"] == "STEER", client.current
        with open_plan_store_readonly(workspace) as store:
            assert not store.judgment(late["requestId"])["positiveCredit"]
            assert not [r for r in approvals.records(store,"run-invocation-completed") if r["effectId"] == late["effectId"]]
            released = [r for r in approvals.records(store,"run-owner-released") if r["effect"]["id"] == late["effectId"]]
            assert len(released) == 1 and released[0]["quarantine"] and released[0]["ownershipReleased"]
    finally:
        client.close()


@timed
def receipt_gaps_golden(workspace):
    """C18/C23: four real MCP deaths reuse paid plan/acceptance work through one C.

    Ordinary exits occur after the cursor commit and miss plan review, scenario, sealed raw
    review and admitted-review receipts stranded just before that cursor update.
    Hold each actual producer once, kill only this client's MCP PID and resume.
    Closing a positive review's note must preserve completed work; direct domain
    closure misses a public author answer resetting NEXT to plan preparation.
    """
    from tests.python.core.mcp_planner_support import CHOREOGRAPHY_TOOLS, LoopClient

    from codex_harness.core import acceptance_recovery
    from codex_harness.core.plan_store import open_plan_store_readonly
    from codex_harness.core.reviewer_evidence import review_tier

    workspace.mkdir()
    (workspace / "receipt-gaps").touch()
    (workspace / "contracts-attempted").touch()
    (workspace / "validation-observed").touch()
    (workspace / "template/checks").mkdir(parents=True)
    (workspace / "template/checks/retired.json").write_text("[1]\n", encoding="utf-8")
    content = plan_content()
    # CLI recovery must traverse the same native Claude registration consumer as
    # browser acceptance; Codex-only receipts hid absent observed effort fields.
    settings = kickoff_content()
    settings["reviewers"][1].update(provider="claude", model="claude-sonnet-5-5")
    client = LoopClient(workspace, kickoff=settings)
    client.expected_review_tier = "cross-provider"

    def wait_for(name):
        marker = workspace / ".codex-harness" / (name + ".json")
        deadline = time.monotonic() + 45
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), (workspace / ".codex-harness/server.log").read_text("utf-8")
        return json.loads(marker.read_text("utf-8"))

    def crash_owner(name):
        import signal
        marker = workspace / ".codex-harness" / (name + ".json")
        client.seen_faults.add(name)
        owned = json.loads(marker.read_text("utf-8"))["ownerPid"]
        with open_plan_store_readonly(workspace) as store:
            effect = store.status()["run"]["effect"]
            assert effect["ownerPid"] == owned
            from codex_harness.core.audit_process import process_observation
            assert process_observation(owned)["identity"] == effect["ownerIdentity"]
        if owned != client.process.pid:
            os.kill(owned, signal.SIGTERM)
        client.reconnect()

    def paid_calls(*, acceptance=True):
        path = workspace / ".codex-harness/scripted-review-invocations.jsonl"
        return (
            [
                json.loads(line)
                for line in path.read_text("utf-8").splitlines()
                if json.loads(line)["acceptance"] is acceptance and not json.loads(line).get("semantic")
            ]
            if path.exists()
            else []
        )

    try:
        client.call(
            "harness_plan_start", name="Receipt continuity", idea=content["nodes"][0]["text"]
        )
        advance_meaning(
            client,
            content,
            stop_at=lambda run: bool(
                (run.get("effect") or {}).get("kind") == "review"
            ),
        )
        planned = wait_for("plan-review-complete")
        with open_plan_store_readonly(workspace) as store:
            plan_rows = [
                dict(row)
                for row in store.connection.execute(
                    "SELECT * FROM review_runs WHERE round_id=? ORDER BY rowid",
                    (planned["roundId"],),
                )
            ]
            assert [row["id"] for row in plan_rows] == planned["reviewRunIds"]
            assert len(plan_rows) == len(settings["reviewers"])
            assert all(row["invalid"] == 0 and row["didRunProvider"] for row in plan_rows)
            completed_plan = [
                row
                for row in approvals.records(store, "roundtable-completed")
                if row["roundId"] == planned["roundId"]
            ]
            assert len(completed_plan) == 1
            assert approvals.current(store, "B") is None
        original_plan_calls = paid_calls(acceptance=False)
        assert original_plan_calls and not paid_calls()
        crash_owner("plan-review-complete")
        client.call(
            "harness_plan_start",
            name="Consume completed plan review",
            idea="Retain the original reviewer results before B",
        )
        advance_meaning(
            client,
            content,
            stop_at=lambda run: bool(
                run.get("userRequest")
                and run["userRequest"]["gate"] == "plan-execution"
                and not run.get("question")
            ),
        )
        assert client.current["userRequest"]["gate"] == "plan-execution"
        with open_plan_store_readonly(workspace) as store:
            assert [
                dict(row)
                for row in store.connection.execute(
                    "SELECT * FROM review_runs WHERE round_id=? ORDER BY rowid",
                    (planned["roundId"],),
                )
            ] == plan_rows
            assert [
                row
                for row in approvals.records(store, "roundtable-completed")
                if row["roundId"] == planned["roundId"]
            ] == completed_plan
        assert paid_calls(acceptance=False) == original_plan_calls
        advance_meaning(
            client,
            content,
            stop_at=lambda run: bool(
                run.get("question") and run["question"]["purpose"] == "visualQuality"
            ),
        )
        client.send_answer(judge_reply(client.current["question"]))
        captured = wait_for("acceptance-scenarios-complete")
        with open_plan_store_readonly(workspace) as store:
            attempts = store.connection.execute("SELECT * FROM acceptance_attempts").fetchall()
            assert (
                len(attempts) == 1
                and attempts[0]["id"] == captured["attemptId"]
                and attempts[0]["state"] == "completed"
            )
            original_run = json.loads(attempts[0]["result_json"])
            assert original_run["id"] == captured["runId"] and original_run["passed"]
            assert (
                store.connection.execute("SELECT count(*) FROM acceptance_runs").fetchone()[0] == 1
            )
            assert not store.connection.execute("SELECT 1 FROM acceptance_reviews").fetchone()
        assert not paid_calls()
        crash_owner("acceptance-scenarios-complete")
        client.send_start()
        sealed = wait_for("acceptance-review-sealed")
        assert sealed["runId"] == original_run["id"]
        with open_plan_store_readonly(workspace) as store:
            headers = acceptance_recovery._events(store, acceptance_recovery.STARTED)
            assert len(headers) == 1 and headers[0][1]["attemptId"] == sealed["attemptId"]
            header = headers[0][1]
            assert set(sealed["slots"]) == {slot["id"] for slot in header["slots"]}
            receipts = acceptance_recovery._events(
                store, acceptance_recovery.COMPLETED, sealed["attemptId"]
            )
            assert [key for key, _ in receipts] == sealed["receiptEventIds"]
            assert {row["slot"] for _, row in receipts} == set(sealed["slots"])
            registered = acceptance_recovery._events(
                store, acceptance_recovery.REGISTERED, sealed["attemptId"]
            )
            assert len(registered) == len(receipts) == len(header["slots"])
            for slot in header["slots"]:
                actual = next(row for _, row in receipts if row["slot"] == slot["id"])
                assert all(
                    actual["runtime"][key] == slot["reviewer"][key]
                    for key in ("provider", "model", "effort")
                ), actual
            assert not store.connection.execute("SELECT 1 FROM acceptance_reviews").fetchone()
        original_paid = paid_calls()
        assert len(original_paid) == len(sealed["slots"])
        crash_owner("acceptance-review-sealed")
        client.send_start()
        admitted = wait_for("acceptance-review-admitted")
        assert admitted["runIds"] == [original_run["id"]] and admitted["reviewIds"] == [
            sealed["attemptId"]
        ]
        assert paid_calls() == original_paid
        crash_owner("acceptance-review-admitted")
        client.call(
            "harness_plan_start",
            name="Resume offered C",
            idea="Retain original scenarios and paid review",
        )
        advance_meaning(
            client,
            content,
            stop_at=lambda run: bool(
                run.get("question") and run["question"]["purpose"] == "finding-dispositions"
            ),
        )
        pending = client.current.get("question")
        assert pending and pending["family"] == "author"
        assert client.current["state"] == "NEXT"
        members = pending["context"]["members"]
        assert len(members) == 1
        member = members[0]
        assert (
            member["stage"] == "acceptance"
            and member["reviewer"] == "Reviewer2"
            and member["node_id"] == fixture_id(workspace, "R4")
            and member["state"] == "pending"
            and member["finding"]
            == "Confirm the delivered absolute-path handover against the retained GREEN."
        ), member
        with open_plan_store_readonly(workspace) as store:
            from codex_harness.core.planner_candidate import current_job_run

            _, product = current_job_run(store, required=True)
            green = product["semanticValidation"]
            assert green["state"] == "PASS" and green["passed"] and green["linkedRed"]
            assert product["delivery"]["root"] == str(workspace.resolve())
            assert store.judgment(member["judgmentId"])["verdict"] == "positive"
            disposition = dict(
                id=member["id"],
                status="resolved",
                resolution=(
                    f"Acceptance run {original_run['id']} passed the absolute-path scenario; "
                    f"integrated GREEN {green['evidenceBinding']} has judgment {green['judgmentId']}; "
                    f"the retained delivery is {product['delivery']['root']} "
                    f"with archive {product['delivery']['archive']}."
                ),
            )
        client.answer(
            dict(
                option="ready",
                rationale="Close only this handover note against the retained GREEN and delivery",
                content=dict(findings=[disposition]),
            )
        )
        pending = client.current["question"]
        assert client.current["state"] == "NEXT" and pending["purpose"] == "finding-disposition"
        facts = next(ref["payload"]["facts"] for ref in pending["judgment"]["sourceRefs"])
        assert facts["proposals"] == [disposition]
        assert facts["reviewBinding"]["acceptanceRun"]["id"] == original_run["id"]
        client.answer(judge_reply(pending))
        advance_meaning(
            client,
            content,
            stop_at=lambda run: bool(
                run.get("userRequest")
                and run["userRequest"]["gate"] == "C"
                and not run.get("question")
            ),
        )
        assert client.current["userRequest"]["run"]["id"] == original_run["id"]
        with open_plan_store_readonly(workspace) as store:
            assert not store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchone()
        advance_meaning(client, content)
        assert client.current["state"] == "DONE"
        with open_plan_store_readonly(workspace) as store:
            attempts = store.connection.execute("SELECT * FROM acceptance_attempts").fetchall()
            assert len(attempts) == 1 and attempts[0]["id"] == captured["attemptId"]
            runs = store.connection.execute("SELECT result_json FROM acceptance_runs").fetchall()
            assert len(runs) == 1 and json.loads(runs[0][0]) == original_run
            reviews = store.connection.execute(
                "SELECT id,result_json FROM acceptance_reviews"
            ).fetchall()
            assert len(reviews) == 1 and reviews[0]["id"] == sealed["attemptId"]
            reviewed = json.loads(reviews[0]["result_json"])
            assert review_tier(reviewed["reviewers"]) == "cross-provider"
            assert (
                reviewed["runId"] == original_run["id"]
                and reviewed["receiptEventIds"] == sealed["receiptEventIds"]
            )
            assert (
                acceptance_recovery._events(
                    store, acceptance_recovery.COMPLETED, sealed["attemptId"]
                )
                == receipts
            )
            assert (
                acceptance_recovery._events(
                    store, acceptance_recovery.REGISTERED, sealed["attemptId"]
                )
                == registered
            )
            assert (
                len(acceptance_recovery._events(store, "acceptance-reviewed", sealed["attemptId"]))
                == 1
            )
            assert (
                len(store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchall()) == 1
            )
            assert store.status()["acceptance"]["accepted"]
            closed = store.finding_state()["members"]
            assert len(closed) == 1 and closed[0]["id"] == member["id"]
            assert closed[0]["state"] == "closed" and closed[0]["status"] == "resolved"
            assert closed[0]["resolution"] == disposition["resolution"]
            assert [
                dict(row)
                for row in store.connection.execute(
                    "SELECT * FROM review_runs WHERE round_id=? ORDER BY rowid",
                    (planned["roundId"],),
                )
            ] == plan_rows
        assert paid_calls(acceptance=False) == original_plan_calls
        assert paid_calls() == original_paid
        assert client.decision_gates.count("C") == 1
        assert not CHOREOGRAPHY_TOOLS.intersection(client.tool_names)
        print(
            f"golden receipt gaps: one acceptance run={original_run['id']}; review slots={len(receipts)} retained across four MCP deaths; all planner MCP calls by CHARTER phase={dict(client.calls)}; judge submissions (included)={dict(client.judge_calls)}"
        )
    finally:
        client.close()


def judge_candidate(store, observation):
    """Replace legacy static candidate verdicts with the public bound 1j request."""
    from codex_harness.core.planner_candidate import record_judgment

    identity_regression = observation.pop("identity_regression", False)
    observation = {k: v for k, v in observation.items() if k != "judgment_id"}
    prepared = record_judgment(store, **observation)
    packet = prepared["sourceRefs"][0]["payload"]
    if identity_regression:
        # R14: handwritten 'execution' replies hid the accepted-but-ignored
        # contracts label. Public prepare/submit/read must teach both identities.
        with pytest.raises(ValueError) as unknown:
            record_judgment(
                store, repo=observation["repo"], check="contracts", judgment_id=prepared["id"]
            )
        assert packet["check"] in str(unknown.value) and packet["evidenceBinding"] in str(
            unknown.value
        ), unknown.value
        for change in ({"check": "contracts"}, {"evidenceBinding": "wrong-observation-digest"}):
            wrong = dict(
                check=packet["check"],
                evidenceBinding=packet["evidenceBinding"],
                phase=packet["phase"],
            )
            wrong.update(change)
            rejected = scripted_judgment(store, prepared, wrong)
            assert (
                rejected["disposition"] == "exact-fix" and not rejected["current"]["positiveCredit"]
            ), rejected
            assert (
                packet["check"] in rejected["error"]
                and packet["evidenceBinding"] in rejected["error"]
            ), rejected
        with pytest.raises(ValueError) as missing:
            store.prepare_judgment(
                question="Assess candidate",
                subject_ids=["V1"],
                evidence_ids=[packet["evidenceBinding"]],
            )
        assert packet["evidenceBinding"] in str(missing.value)
        # The former RPC-specific evidence-record/accept_assess hint retired with
        # prepare_judgment. The canonical owner still refuses hashes as node IDs.
        assert "node" in str(missing.value).lower(), missing.value
    if observation["check"] != "execution":
        # CC's real web gate reached this distinct semantic request. Execution-only
        # fixtures missed both its preparation refusal and cross-phase credit.
        rejected = scripted_judgment(
            store, prepared, dict(fixture_judgment_example(prepared)["responseExample"]["meaning"], phase="wrong-phase")
        )
        assert (
            rejected["disposition"] == "exact-fix" and not rejected["current"]["positiveCredit"]
        ), rejected
        assert packet["check"] in rejected["error"] and packet["phase"] in rejected["error"], (
            rejected
        )
    scripted_judgment(
        store,
        prepared,
        dict(
            check=packet["check"],
            evidenceBinding=packet["evidenceBinding"],
            phase=packet["phase"],
            meaningful=True,
            correctFailureReason=True,
            noStubMockGaps=True,
            qualitativeChecks={
                o["check"]: True
                for p in [packet["proof"], packet["proof"].get("red") or packet["proof"]]
                for r in p["results"]
                for o in r.get("observations", [])
                if o["passed"] is None
            },
        ),
    )
    return record_judgment(
        store, repo=observation["repo"], check=observation["check"], judgment_id=prepared["id"]
    )


def judge_executions(workspace, patcher, *, contracts_gap=None, pending_execution=False):
    """External semantic responses for the real dispatch observation consumer.

    These domain journeys exercise canonical storage and real process results;
    the public controller journey separately proves process exit before judgment.
    """
    from codex_harness.core import dispatch_integration
    from codex_harness.core.candidate_observations import assess_execution
    from codex_harness.core.plan_store import open_plan_store

    observe = dispatch_integration._semantic_validation

    def judged(root, plan, record, phase):
        proof = observe(root, plan, record, phase)
        if pending_execution or proof["state"] == "incomplete":
            return proof
        with open_plan_store(workspace) as store:
            with store._transaction():
                store._event("execution-observed", proof)
                event = store.connection.execute("SELECT max(id) FROM events").fetchone()[0]
            prepared = store.prepare_judgment(
                question="Assess meaningfulness, correct RED and stub/mock gaps",
                subject_ids=store.spine().ids(),
                source_event_ids=[event],
                candidate=proof["candidate"],
            )
            gaps = contracts_gap() if contracts_gap else []
            meaning = dict(
                check="execution",
                evidenceBinding=proof["evidenceBinding"],
                phase=proof["phase"],
                meaningful=True,
                correctFailureReason=True,
                noStubMockGaps=True,
                qualitativeChecks={
                    observation["check"]: True
                    for p in (proof, proof.get("red") or proof)
                    for row in p["results"]
                    for observation in row.get("observations", [])
                    if observation["passed"] is None
                },
            )
            reply = scripted_judgment(
                store, prepared, meaning, "negative" if gaps else "positive", gaps
            )
            return assess_execution(proof, reply["current"])

    patcher.setattr(dispatch_integration, "_semantic_validation", judged)


@timed
def single_build_golden(workspace):
    """C1/C9/C14/C15: a dependency-free worker bundle reaches the real product C.

    A separate Contracts bead precreates product entries and hides fallback RED
    losing the worker's assertion helper, deleted fixture or absolute CLI entry.
    """
    from tests.python.core.mcp_planner_support import LoopClient

    from codex_harness.core.plan_store import open_plan_store_readonly
    from codex_harness.core.planner_candidate import current_job_run

    workspace.mkdir()
    (workspace / "contracts-attempted").touch()
    (workspace / "validation-observed").touch()
    (workspace / "template/checks").mkdir(parents=True)
    (workspace / "template/checks/retired.json").write_text("[1]\n", encoding="utf-8")
    content = plan_content()
    content["nodes"] = [node for node in content["nodes"] if node["id"] != "C"]
    next(node for node in content["nodes"] if node["id"] == "B")["fields"]["dependsOn"] = []
    client = LoopClient(workspace)
    try:
        client.call(
            "harness_plan_start",
            name="Dependency-free binary receipt",
            idea=content["nodes"][0]["text"],
        )
        advance_meaning(client, content)
        assert client.current["state"] == "DONE"
        status = client.call("harness_plan_status")
        assert status["acceptance"]["accepted"]
        with open_plan_store_readonly(workspace) as store:
            selected, retained = current_job_run(store, required=True)
            assert set(retained["beads"]) == {fixture_id(workspace, "B")}
            validation = retained["semanticValidation"]
            red = validation["red"]
            assert red["observedState"] == "FAIL", red
            assert any(
                row["id"] == fixture_id(workspace, "T_RECEIPT") and row["state"] == "FAIL" for row in red["results"]
            )
            observed = next(row for row in red["results"] if row["id"] == fixture_id(workspace, "T_RECEIPT"))["actions"][
                "execute"
            ]["stderr"]
            assert "ModuleNotFoundError: No module named 'hashslip'" in observed
            assert "retired fixture still present" not in observed
            assert not (workspace / "checks/retired.json").exists()
            assert all(row["cleanup"]["quiescenceVerified"] for row in red["results"])
            assert validation["testSources"] == red["testSources"] and validation["judgmentId"]
            invocation = next(iter(next(row for row in validation["results"] if row["id"] == fixture_id(workspace,"S6"))["actions"].values()))
            entry, caller = Path(invocation["command"][1]), Path(invocation["cwd"])
            assert entry.is_absolute() and caller == entry.parent / "checks"
            assert (
                validation["state"] == "PASS" and validation["passed"] and validation["linkedRed"]
            )
            assert retained["delivery"]["root"] == str(workspace.resolve())
            assert Path(selected["runPath"]).is_file()
        print(
            f"golden dependency-free: all planner MCP calls by CHARTER phase={dict(client.calls)}; judge submissions (included)={dict(client.judge_calls)}; actual tools={dict(client.tool_names)}"
        )
    finally:
        client.close()

    # C15: a real owned implementation outside verifier roots must reach the
    # tool-free judge. Green/RED-only journeys missed an unseen concrete stub.
    stub_root = workspace / "stub"
    stub_root.mkdir()
    (stub_root / "contracts-attempted").touch()
    (stub_root / "validation-observed").touch()
    (stub_root / "stub-implementation").touch()
    stub_content = json.loads(json.dumps(content))
    next(n for n in stub_content["nodes"] if n["id"] == "B")["fields"]["ownedFiles"].extend("checks/context-"+str(i)+".txt" for i in range(4))
    client = LoopClient(stub_root)
    try:
        client.call("harness_plan_start",name="Reject constant receipt",idea=content["nodes"][0]["text"])
        advance_meaning(client,stub_content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "execution")
        pending = client.current["question"]
        proof = next(r for r in client.offered["packet"]["facts"]["sources"] if r.get("check") == "execution")["proof"]
        assert proof["observedState"] == "PASS", proof["observedState"]
        seen = any("HARDCODED_RECEIPT" in f["body"] for f in proof["implementationSources"]["files"] if f["path"] == "hashslip.py")
        verdict = judge_reply(pending,verdict="negative" if seen else "positive",gaps=["The implementation returns a constant receipt table instead of hashing unseen stdin"] if seen else [])
        verdict["meaning"]["noStubMockGaps"] = not seen
        client.answer(verdict)
        judged = client.call("harness_judgment",judgment_id=pending["requestId"],candidate=pending["candidate"])
        assert judged["verdict"] == "negative" and not judged["positiveCredit"] and judged["meaning"]["noStubMockGaps"] is False
        assert "constant receipt" in str(judged["gaps"])
        client.call("harness_chat",text="/stop")
        assert client.current["state"] == "STOPPED"
    finally:
        client.close()


def plan_content(surface="cli"):
    """Retained run26/UX6 intent, authored through one canonical semantic payload."""
    from tests.python.core.mcp_planner_support import node_example

    captured = json.loads(
        (
            ROOT / "tests/fixtures/efficiency" / ("ux6.json" if surface == "web" else "run26.json")
        ).read_text("utf-8")
    )
    if surface == "web":
        # Regression: served UI/API plans were unsupported. Offline journeys never
        # exercised server launch, HTTP persistence, readiness or teardown.
        vision = captured["nodes"][0]["fields"]
        vision["targets"]["served"] = dict(
            kind="web",
            entry="index.html",
            server=dict(
                entry="serve_wrapper.py",
                runtime=sys.executable,
                argv=[],
                readyPath="/",
                readyWithin=5,
            ),
        )
        journey = vision["scenarios"]["scenarios"][0]["actions"][0]["web"]
        journey["target"] = "served"
        # Review P2: preview must preserve cross-target API/CLI prerequisites and
        # their persistence checks; the former same-target fixture missed filtering.
        vision["targets"]["seed-service"] = dict(vision["targets"]["served"])
        # r9: a deliberate negative path was counted as a console error. Existing
        # served journeys made only successful requests and never created preview DBs.
        scenario = vision["scenarios"]["scenarios"][0]
        scenario["setup"] = dict(
            dirs=["{data}/fixtures"],
            files=[dict(path="{data}/fixtures/items.csv", utf8="name,quantity\nTea,2\n")],
        )
        scenario["actions"].insert(
            0,
            dict(
                id="seed",
                http=dict(
                    target="seed-service",
                    method="POST",
                    path="/seed",
                    json=dict(note="Seeded"),
                    statusEquals=200,
                ),
            ),
        )
        scenario["actions"].insert(
            1,
            dict(
                id="seed-check",
                process=dict(
                    entry="seed_check.py",
                    runtime=sys.executable,
                    argv=[{"input": "{data}/fixtures/items.csv"}],
                ),
            ),
        )
        scenario["observe"].extend(
            [
                dict(action="seed"),
                dict(action="seed-check", exitEquals=0, stdoutEquals="Seeded\n"),
                dict(path="seed.json", fileJsonEquals=dict(saved=True, note="Seeded")),
            ]
        )
        journey["steps"][1:1] = [
            dict(op="click", locator=dict(selector="#read-seed")),
            dict(op="assertVisible", locator=dict(text="Seeded")),
        ]
        # R3: a native form redirect completes without an exposed response body.
        # Earlier empty-body/truncation journeys never exercised browser redirects.
        journey["steps"][3:3] = [
            dict(
                id="formpost",
                op="click",
                locator=dict(selector="#post-redirect"),
                expectedErrors=[dict(method="POST", path="/submit", statusEquals=302)],
            ),
            dict(op="assertFragment", fragment="#posted"),
        ]
        scenario["observe"].append(
            dict(path="submission.json", fileJsonEquals=dict(note=["Posted"]))
        )
        scenario["actions"].append(
            dict(
                id="api",
                http=dict(
                    target="served",
                    method="GET",
                    path="/api/state",
                    statusEquals=200,
                    jsonEquals=dict(saved=True, note="Tea"),
                ),
            )
        )
        scenario["observe"].append(dict(action="api"))
        scenario["observe"].append(
            dict(path="state.json", fileJsonEquals=dict(saved=True, note="Tea"))
        )
        journey["steps"].extend(
            [
                dict(op="fill", locator=dict(selector="#note"), value="Tea"),
                dict(op="click", locator=dict(selector="#save")),
                dict(op="assertVisible", locator=dict(text="Saved")),
                dict(
                    op="request",
                    request=dict(
                        method="GET",
                        path="/api/state",
                        statusEquals=200,
                        jsonEquals=dict(saved=True, note="Tea"),
                    ),
                ),
                dict(
                    id="overconsume",
                    op="click",
                    locator=dict(selector="#overconsume"),
                    expectedErrors=[dict(method="GET", path="/overconsume", statusEquals=409)],
                ),
                dict(op="assertVisible", locator=dict(text="Rejected")),
                dict(
                    op="request", request=dict(method="GET", path="/preview-db", statusEquals=200)
                ),
                # r11: null-body HTTP crashed before any judgment; status-only coverage
                # also lost successful declarations and their actual abort diagnostics.
                dict(
                    id="delete",
                    op="click",
                    locator=dict(selector="#delete"),
                    expectedErrors=[dict(method="DELETE", path="/api/state", statusEquals=204)],
                ),
                dict(op="assertVisible", locator=dict(text="Deleted")),
                dict(
                    op="request", request=dict(method="DELETE", path="/api/state", statusEquals=204)
                ),
                dict(
                    op="request", request=dict(method="HEAD", path="/index.html", statusEquals=200)
                ),
                dict(op="request", request=dict(method="GET", path="/reset", statusEquals=205)),
                dict(op="request", request=dict(method="GET", path="/cached", statusEquals=304)),
                dict(
                    id="empty",
                    op="click",
                    locator=dict(selector="#empty"),
                    expectedErrors=[dict(method="GET", path="/empty", statusEquals=204)],
                ),
                dict(op="assertVisible", locator=dict(text="Deleted")),
                dict(
                    id="cached",
                    op="click",
                    locator=dict(selector="#cached"),
                    expectedErrors=[dict(method="GET", path="/cached", statusEquals=304)],
                ),
                dict(op="assertVisible", locator=dict(text="Cached")),
            ]
        )
        # Read back persistence after deletion, rather than asking the product to
        # change its DELETE contract to fit the observer.
        scenario["actions"][-1]["http"]["jsonEquals"] = dict(saved=False)
        scenario["observe"][-1] = dict(path="state.json", fileAbsent=True)
        for n in captured["nodes"]:
            if n["kind"] == "bead":
                n["fields"]["ownedFiles"].extend(["serve.py", "serve_wrapper.py", "seed_check.py"])
    nodes = captured["nodes"]
    if surface == "cli":
        test = node_example("test", True)[1]
        test.update(
            id="T_RECEIPT",
            parents=["G1"],
            title="Exact binary receipt",
            text="Observe exact binary input through the real CLI; catches byte corruption; broad scenarios miss worker-authored verifier packaging",
        )
        test["fields"].update(module="checks/verify_hashslip.py")
        test["fields"]["execution"].update(
            entry="checks/verify_hashslip.py", argv=[], testRoots=["checks"]
        )
        nodes.append(test)
        build = next(n for n in nodes if n["id"] == "B")
        build["parents"] = ["T_RECEIPT"]
        build["fields"]["ownedFiles"] += [
            "checks/verify_hashslip.py",
            "checks/receipt_helper.py",
            "checks/input.json",
            "checks/retired.json",
        ]
        contracts = next(n for n in nodes if n["id"] == "C")
        contracts["parents"] = ["T_RECEIPT"]
        contracts["fields"]["ownedFiles"] = list(build["fields"]["ownedFiles"])
    elif surface == "web":
        nodes.append(dict(id="T_WEB",kind="test",title="Saved notes browser journey",text="Exercise the retained browser, HTTP and persistence scenarios against the real UI",parents=["G1"],fields=dict(module="SCENARIOS.json",level="e2e")))
        for scenario in nodes[0]["fields"]["scenarios"]["scenarios"]: scenario["testRef"] = "T_WEB"
    for n in nodes:
        # FIX17 DELETE-structure: provider pins/project setup are operator kickoff data.
        if n["kind"] == "bead":
            for key in ("provider", "model", "effort"):
                n["fields"].pop(key, None)
        elif n["kind"] == "vision":
            n["fields"].pop("setup", None)
    return dict(nodes=nodes)


@timed
def small_plan_golden(workspace, *, size="small", auth_fault=False, facts_fault=False):
    """C5/C9/C11: eligible dreams reach B with five kinds and size-scaled review.

    Catch r24: incomplete Spec escaped to prepare and asked the user to repair.
    Two named bead slots make the DV2 shape and tests-first dependency CODE-owned.
    Catch uncovered requirement/test and ownership gaps together, preserve meaning
    through confined C4 fixes, then reach B using only A words. Previous fixtures
    missed three-bead dead ends, draft loss and small brownfield selection.
    Catch r25: medium used the full contract and stalled; small-only proof missed it.
    Catch r26: ordinary success measures forced full trace; empty measures missed it.
    Catch r27: an expired reviewer session must reach B with an honest tier and
    only the normal A/B questions; successful-provider journeys missed reselection.
    All reviewers unavailable must remain visibly uncredited at B. A dialogue-ID
    gap must become a semantic refusal, not a citation error; prior gaps cited nodes.
    Catch r28: the actual review prompt must carry the original brief and pre-B
    obligations; the plan question must supply observed setup and descriptor forms.
    Earlier journeys checked B readiness but missed these provider context losses.
    Catch r29: one concrete review gap must survive open disposition and typed
    repair to passing review/B without amendment or user provenance questions.
    A judge reiterating that gap must not create another finding member.
    Earlier all-positive reviewers never exercised this repair boundary.
    Catch r30: fallback reviewer bookkeeping and regenerated documents must not
    stale positive reviews or B; a real meaning edit must still invalidate both.
    Earlier fallback journeys stopped at the displayed B without answering it.
    """
    from tests.python.core.mcp_planner_support import LoopClient, provider_shape
    from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
    from codex_harness.core import approvals
    settings = kickoff_content()
    if auth_fault:
        settings["reviewers"] = [dict(r, provider="claude", model="claude-sonnet-5-5") for r in settings["reviewers"]]
    client = LoopClient(workspace, kickoff=settings)
    if auth_fault:
        (workspace / "review-auth-fault").write_text(str(auth_fault))
    def correction(edits, additions=()):
        return json.dumps(provider_shape(client.current["question"]["answerSchema"],dict(edits=edits,add=list(additions),rationale="Correct only the named obligations",reply="Retain the rest of the plan")))
    content = dict(nodes=[dict(id="V1",kind="vision",title="Small notes",parents=[],
        text="A local note app running under Python 3.12: save a note, reopen it, and read the same text, including empty and Unicode notes.",
        fields=dict(planMode="semantic-v1",users=["A person taking local notes"],first_user_moment="Save and reopen a note",
            scope_in=["Save and reopen exact text"],scope_out=["Sync and sharing"],quality_bar="Preserve empty and Unicode text across restart",
            done_feels_like="A saved note reopens unchanged",expansion=dict(upgrades=[])))])
    if size == "medium":
        content["nodes"][0]["fields"].update(metrics=[dict(definition="Exactly one of two concurrent consumes succeeds",unit="successful consumes")],
            stopConditions=["Acceptance remains incomplete if saved notes change after restart"])
    try:
        brief = content["nodes"][0]["text"] + " Keep all saved notes on this household's computer."
        (workspace / "review-brief.txt").write_text(brief, encoding="utf-8")
        review_repair = size == "small" and not auth_fault and not facts_fault
        if review_repair:
            (workspace / "review-plan-defect").touch()
        client.call("harness_plan_start", name="Small notes", idea=brief)
        if facts_fault:
            advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "planning-size")
            message = next(r["id"] for r in client.offered["packet"]["facts"]["dialogue"] if r["role"] == "user")
            answer = json.loads(provider_answer(workspace,client.current["question"],judge_reply(client.current["question"],verdict="negative")))
            answer["gaps"] = [dict(subjectRef=message,evidenceRefs=[message],issue="The supplied dream does not establish whether this is a brownfield change")]
            client.answer(json.dumps(answer))
            assert client.current["state"] == "STEER" and "brownfield" in client.current["reason"]
            return
        advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "planning-size")
        sizing = judge_reply(client.current["question"])
        sizing["meaning"].update(size=size, process="One complete plan and consequence-scaled independent review")
        client.answer(sizing)
        advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "plan-outline")
        pending = client.current["question"]
        setup = client.offered["packet"]["facts"]["executionSetup"]
        assert Path(setup["pythonExecutable"]).samefile(sys.executable) and setup["pythonVersion"] == sys.version
        assert all(term in setup["descriptorForms"] for term in ("candidate-owned entry or dotted module", "absolute installed runtime", "argv", "testRoots"))
        assert set(pending["answerSchema"]["properties"]) == {"feature","requirement","test","contractsBead","buildBead","rationale","reply"}
        for slot in ("contractsBead","buildBead"):
            contract = pending["answerSchema"]["properties"][slot]
            assert contract["type"] == "object" and set(contract["properties"]) == {"title","text","parents","fields"}
            assert set(contract["properties"]["fields"]["properties"]) == {"ownedFiles"}, "The planner still controls fixed bead phases/dependency"
        vision = client.offered["packet"]["facts"]["vision"][0]["id"]
        def row(key,title,parents,**fields):
            return dict(key=key,title=title,text=title,parents=parents,fields=fields)
        answer = dict(
            feature=[row("notes","Save and reopen notes",[vision],story="As a person taking notes, I can reopen saved text without losing it")],
            requirement=[row("persist","Reopening preserves the exact note",["notes"],
                architecture=[dict(boundary="Storage",contract="A local file stores the note",rationale="Persistence across process restarts")],
                design=[dict(boundary="Save",contract="Write before acknowledging save",rationale="An acknowledged note survives restart")],
                interfaceContracts=[dict(boundary="Note API",contract="save(text) and load() preserve Unicode text",rationale="The UI and storage agree on text")])],
            test=[row("reopen","Save Unicode text, restart, and read it back",["persist"],execution=dict(
                entry="checks/reopen.py",module=None,runtime=sys.executable,argv=[""],testRoots=["checks"],rationale="Observe real persistence across restart, including an empty note argument"))],
            contractsBead=dict(title="Write the persistence journey first",text="Observe persistence RED",parents=["reopen"],fields=dict(ownedFiles=["checks/reopen.py"])),
            buildBead=dict(title="Implement the note app",text="Make the same journey GREEN",parents=["persist"],fields=dict(ownedFiles=["notes.py"])),
            rationale="A complete small closed-loop product with tests before implementation",reply="Review the complete note plan.")
        answer["requirement"].append(row("empty","Empty notes reopen unchanged",["notes"],architecture=[],design=[],interfaceContracts=[]))
        answer["test"][0]["parents"].append("empty")
        answer["buildBead"]["parents"].append("empty")
        missing = json.loads(json.dumps(answer))
        missing["test"][0]["parents"] = ["persist"]
        missing["buildBead"].update(parents=["persist"],fields=dict(ownedFiles=["VISION.md"]))
        client.answer(json.dumps(missing))
        assert all(gap in client.offered["packet"]["exactFix"] for gap in ("requirement-without-bead","requirement-without-test","owned-file-protected")), client.offered["packet"]["exactFix"]
        repair = client.current["question"]
        assert repair["purpose"] == "trace" and repair["requestId"] == pending["requestId"]
        assert "bead" not in repair["context"].get("addKinds",[]) or repair["answerSchema"]["properties"]["add"]["items"]["anyOf"][0]["properties"]["kind"]["enum"] == ["test"], "Confined repair can introduce a third bead"
        identities = dict(repair["context"]["handles"])
        with open_plan_store_readonly(workspace) as store:
            assert not store.spine().ids("feature"), "A refused plan leaked partial records"
        client.answer(correction({identities["buildBead"]+":fields.ownedFiles":["notes.py"]}))
        assert all(gap in client.offered["packet"]["exactFix"] for gap in ("requirement-without-bead","requirement-without-test"))
        client.answer(correction({identities["reopen"]+":parents":[identities["persist"],identities["empty"]],
            identities["contractsBead"]+":parents":[identities["reopen"]], identities["buildBead"]+":parents":[identities["persist"],identities["empty"]]}))
        assert (client.current.get("question") or {}).get("purpose") != "trace", client.current
        if review_repair:
            advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "finding-dispositions")
            members = client.current["question"]["context"]["members"]
            assert len(members) == 1
            member_id = members[0]["id"]
            client.answer(json.dumps(dict(dispositions=[dict(memberRef=member_id,disposition="open",resolution="The runtime-version command still needs repair")],rationale="Retain the concrete review gap",reply="Repairing the planned verification")))
            client.answer(json.dumps(dict(verdict="negative",rationale="The same concrete plan gap remains open",
                gaps=[dict(subjectRef=member_id,evidenceRefs=[member_id],issue="Python 3.12 verification is still absent from the planned command")],
                dispositions=[dict(memberRef=member_id,disposition="open")])))
            answer["test"][0]["fields"]["execution"]["argv"].append("--verify-python-3.12")
            client.answer(correction({identities["reopen"]+":fields.execution":answer["test"][0]["fields"]["execution"]}))
            advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "finding-dispositions")
            client.answer(json.dumps(dict(dispositions=[dict(memberRef=member_id,disposition="resolved",resolution="The planned command now explicitly includes Python 3.12 verification")],rationale="Judge the repaired plan",reply="The runtime-version gap has been repaired")))
            content["findings"] = [dict(id=member_id,status="resolved",resolution="The retained planned command explicitly includes Python 3.12 verification")]
        advance_meaning(client,content,stop_at=lambda r:(r.get("userRequest") or {}).get("gate") == "plan-execution" and not r.get("question"))
        assert client.current["userRequest"]["gate"] == "plan-execution", client.current
        with open_plan_store_readonly(workspace) as store:
            spine = store.spine()
            authored = [n for n in spine.to_dict()["nodes"] if n["id"] != client.current.get("reviewerNodeId") and n["kind"] != "decision"]
            assert {n["kind"] for n in authored} == {"vision","feature","requirement","test","bead"}
            assert len(spine.ids("vision")) == 1 and len(spine.ids("bead")) == 2
            assert not spine.coverage_gaps()
            assert spine.node(spine.ids("feature")[0])["fields"]["story"] == answer["feature"][0]["fields"]["story"]
            assert spine.node(identities["persist"])["fields"]["interfaceContracts"] == answer["requirement"][0]["fields"]["interfaceContracts"]
            assert "save(text) and load() preserve Unicode text" in (workspace / "template/REQUIREMENTS.md").read_text("utf-8")
            vf = spine.node(spine.ids("vision")[0])["fields"]
            if size == "medium":
                assert all(vf[k] == content["nodes"][0]["fields"][k] for k in ("metrics","stopConditions")), "Five-kind planning lost the vision's success measures"
            assert vf["planSize"] == size and not vf.get("targets") and not vf.get("scenarios")
            assert store._judgment_record(pending["requestId"])["fixesIssued"] == 2
            assert approvals.current(store,"A") and not approvals.current(store,"B")
            assert len([r for r in approvals.records(store,"chat-message") if r.get("role") == "user"]) == 2
            prerequisite = store.review_prerequisite(store.manifest_revision())
            assert prerequisite["complete"] == (auth_fault != "all") and prerequisite["unavailable"] == (auth_fault == "all")
            rounds = approvals.records(store,"roundtable-completed")
            if auth_fault:
                card = client.current["userRequest"]
                tier = "unavailable" if auth_fault == "all" else "same-model"
                assert card["subject"]["reviewTier"] == tier and f"Review tier: {tier}" in card["card"]
                if auth_fault == "all":
                    assert not card["subject"]["reviewComplete"] and not prerequisite["reviews"]
                    assert "No independent review ran" in card["card"]
                else:
                    assert {r["provider"] for r in card["subject"]["reviewers"]} == {"codex"}
                assert not any(r["family"] == "direction" for r in approvals.records(store,"loop-question"))
            else:
                assert len(rounds) == (2 if review_repair else 1) and all(r["shards"] == 1 for round_ in rounds for r in round_["reviewers"])
            if review_repair:
                assert not spine.ids("amendment")
                assert [(m["id"],m["state"]) for m in store.finding_state()["members"]] == [(member_id,"closed")]
                assert len(prerequisite["reviews"]) == len(settings["reviewers"])
            manifest = approvals.records(store,"manifest-compiled")[0]["manifest"]
            from codex_harness.core.runners import executable_scenarios
            commands = executable_scenarios(manifest["trace"],manifest["semantic"]["scenarios"])["scenarios"]
            assert len(commands) == 1 and commands[0]["actions"][0]["process"]["entry"] == "checks/reopen.py"
            assert commands[0]["actions"][0]["process"]["argv"] == answer["test"][0]["fields"]["execution"]["argv"]
            build = next(n for n in authored if n["kind"] == "bead" and n["fields"]["semanticPhase"] == "build")
            assert len(build["fields"]["dependsOn"]) == 1
            contracts = spine.node(build["fields"]["dependsOn"][0])
            assert contracts["fields"]["semanticPhase"] == "contracts" and contracts["fields"]["dependsOn"] == []
            original_ids = spine.ids()
        if auth_fault is True:
            reviewed = {r["id"] for r in prerequisite["reviews"]}
            with open_plan_store(workspace) as store:
                document = (workspace / "template/VISION.md").read_bytes()
                store.compile_template(workspace / "template")
                assert (workspace / "template/VISION.md").read_bytes() != document, "Fallback did not exercise regenerated reviewer metadata"
            client.words("Yes, approve the displayed plan.")
            assert client.current["question"]["purpose"] == "user-decision", client.current
            client.answer(judge_reply(client.current["question"]))
            with open_plan_store(workspace) as store:
                assert store._current_approval("B"), "Projection regeneration invalidated the displayed B"
                assert {r["id"] for r in store.successful_reviews(store.manifest_revision())} == reviewed
                assert not any(r["purpose"] == "review-reuse" for r in approvals.records(store,"semantic-facts"))
                assert not any(r["state"] == "stale" for r in approvals.records(store,"confirmation-responded"))
                assert len(approvals.records(store,"confirmation-requested")) == 2, "Bookkeeping asked another user question"
                requirement = store.spine().node(identities["persist"])
                requirement["text"] += "; allow exporting saved notes to a shared service"
                store.upsert_node(requirement)
                assert not store._current_approval("B") and not store.successful_reviews(store.manifest_revision()), "A real meaning edit retained B or review credit"
        if auth_fault:
            return
        if size == "medium":
            return
        # A current-plan revision must update CODE's identities, not append a
        # second copy of every feature/test/bead. First-plan-only answers miss it.
        aliases = {r["key"]:next(n["id"] for n in authored if n["kind"] == kind and n["title"] == r["title"])
                   for kind in ("feature","requirement","test") for r in answer[kind]}
        aliases.update({slot:next(n["id"] for n in authored if n["kind"] == "bead" and n["fields"]["semanticPhase"] == phase)
                        for slot,phase in (("contractsBead","contracts"),("buildBead","build"))})
        revised = json.loads(json.dumps(answer))
        for kind in ("feature","requirement","test"):
            for r in revised[kind]:
                r.update(key=aliases[r["key"]],parents=[aliases.get(k,k) for k in r["parents"]])
        for slot in ("contractsBead","buildBead"):
            revised[slot]["parents"] = [aliases.get(k,k) for k in revised[slot]["parents"]]
        revised["test"][0]["text"] += "; also verify a second restart"
        client.send_steer(rationale="Clarify the restart test in the same complete small plan.")
        client.progress()
        client.answer(dict(option="replan",rationale="Clarify verification without changing accepted features",content={}))
        advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "plan-outline")
        invalid = json.loads(json.dumps(revised)); invalid["buildBead"]["fields"]["ownedFiles"] = ["VISION.md"]
        client.answer(json.dumps(invalid))
        repair = client.current["question"]
        assert repair["purpose"] == "trace"
        assert set(repair["answerSchema"]["properties"]) == {"edits","rationale","reply"}, "A bead-only refusal allowed unrelated additions or whole-plan replacement"
        assert all(key.split(":",1)[0] in {aliases["contractsBead"],aliases["buildBead"]} for key in repair["answerSchema"]["properties"]["edits"]["properties"])
        retained = {n["id"]:json.dumps(n,sort_keys=True,ensure_ascii=False).encode() for n in repair["context"]["nodes"] if n["kind"] in {"feature","requirement"}}
        client.answer(correction({aliases["buildBead"]+":fields.ownedFiles":["notes.py"]}))
        advance_meaning(client,content,stop_at=lambda r:(r.get("userRequest") or {}).get("gate") == "plan-execution" and not r.get("question"))
        assert client.current["userRequest"]["gate"] == "plan-execution", client.current
        with open_plan_store_readonly(workspace) as store:
            assert {key:json.dumps(store.spine().node(key),sort_keys=True,ensure_ascii=False).encode() for key in retained} == retained, "Confined bead repair changed retained requirements/story/typed sections"
            assert store.spine().ids() == original_ids, "Plan revision duplicated existing items"
            assert store.spine().node(aliases["reopen"])["text"] == revised["test"][0]["text"]
            assert not approvals.current(store,"B"), "Plan repair silently granted B"
        # A last correction can fail at a different owning boundary. Exhaustion
        # must steer once, not grant a fresh structural-repair allowance.
        client.send_steer(rationale="Reconsider file ownership within the accepted note scope.")
        client.progress()
        client.answer(dict(option="replan",rationale="Review the same accepted plan",content={}))
        advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "plan-outline")
        exhausted = client.current["question"]["requestId"]
        invalid = json.loads(json.dumps(revised)); invalid["buildBead"]["fields"]["ownedFiles"] = ["VISION.md"]
        client.answer(json.dumps(invalid))
        client.answer(correction({aliases["buildBead"]+":fields.ownedFiles":["VISION.md"]}))
        assert client.current["question"]["requestId"] == exhausted and client.current["question"]["judgment"]["fixesIssued"] == 2
        client.answer(json.dumps(dict(edits={aliases["contractsBead"]+":parents":[aliases["contractsBead"]]},rationale="Try an unrelated cyclic edit",reply="Invalid correction")))
        assert client.current["state"] == "STEER" and client.current["question"]["family"] == "direction", client.current
        assert client.current["question"]["context"]["internalFailure"] and not client.current.get("userRequest")
        rejected_id = client.current["question"]["context"]["rejectedResponseEventId"]
        assert rejected_id and any("Try an unrelated cyclic edit" in json.dumps(s) for s in client.offered["packet"]["facts"]["sources"])
        client.answer(json.dumps(dict(action="pause",question=None,rationale="The bounded plan repairs are exhausted",reply="Planning paused after two exact fixes; the remaining gap is protected ownership and an unrelated parent edit.")))
        client.send_steer(rationale="Please continue")
        client.progress()
        client.answer(dict(option="continue",rationale="Continue within accepted scope",content={}))
        advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "plan-outline")
        assert client.current["question"]["purpose"] == "plan-outline" and client.current["state"] == "PLAN", client.current
        # Existing behavior is mapped by the canonical writer before planning a
        # small change. Size alone must not remove its baseline authoring fields.
        with open_plan_store(workspace) as store:
            test_id = aliases["reopen"]
            store.upsert_node(dict(id="BASE_CODE",kind="code",title="Existing notes",text="Observed note storage",parents=[test_id],fields={}))
            ref = dict(id="BASE_CODE",revision=store.spine().revision("BASE_CODE"))
            store.upsert_node(dict(id="BASE_RESULT",kind="result",title="Existing readback",text="Historical observed readback",parents=["BASE_CODE"],fields=dict(evidence=dict(itemRef=ref,candidate="existing-notes",source=dict(reference="fixture:existing-readback",realProvider=False,historical=True),observation=dict(reference="fixture:readback",outcome="passed"),state="integrated"))))
            feature = store.spine().node(aliases["notes"])
            feature["fields"]["baseline"] = dict(source="existing-repository",behavior="Save and reopen existing notes",rationale="Preserve existing behavior while adding a toggle",codeRefs=[ref],testRefs=[dict(id=test_id,revision=store.spine().revision(test_id))],resultRefs=[dict(id="BASE_RESULT",revision=store.spine().revision("BASE_RESULT"))])
            store.upsert_node(feature)
        client.send_steer(rationale="Add a display toggle to these existing notes while preserving their current behavior.")
        client.progress()
        client.answer(dict(option="replan",rationale="Plan a small brownfield change with its baseline",content={}))
        advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "plan-outline")
        full = client.current["question"]
        assert "targets" in full["answerSchema"]["properties"] and "metric" in full["answerSchema"]["properties"], "A small brownfield dream lost the fuller contract"
        outline = {kind:[] for kind in full["answerSchema"]["properties"] if kind not in {"rationale","reply"}}
        outline["feature"] = [dict(ref=None,title="Display toggle",text="Change the display while preserving saved notes")]
        client.answer(json.dumps(dict(outline,rationale="Retain the baseline while planning the toggle",reply="Map the existing behavior first")))
        assert client.current["question"]["purpose"] == "trace"
        assert any(key.endswith(":fields.baseline") for key in client.current["question"]["answerSchema"]["properties"]["edits"]["properties"]), "Fuller contract cannot author baseline provenance"
    finally:
        client.close()
    if size == "small" and not auth_fault and not facts_fault:
        small_plan_golden(workspace / "auth-twin", auth_fault=True)
        small_plan_golden(workspace / "unavailable-twin", auth_fault="all")
        small_plan_golden(workspace / "facts-twin", facts_fault=True)


def kickoff_content(surface="cli"):
    """Operator-owned fixture preferences, consumed by the real kickoff reader."""
    fixture = "jsonkeys-reviewers.json" if surface == "cli" else "ux6.json"
    record = json.loads((ROOT / "tests/fixtures/efficiency" / fixture).read_text("utf-8"))
    choice = record["choice"]
    return dict(timeout=180, reviewers=[dict(name=r["name"], provider=r["provider"], model=r["model"],
        effort=r["effort"]["value"]) for r in choice["fields"]["reviewers"]])


def judge_reply(pending, *, decision="approve", verdict="positive", gaps=()):
    """Only supply meaning; identity/check/evidence comes from the actual producer."""
    record = pending["judgment"]
    result = dict(
        fixture_judgment_example(record)["responseExample"],
        verdict=verdict,
        rationale="Scripted external semantic assessment; no actual provider or human authentication",
        gaps=list(gaps),
    )
    purpose = record["question"]
    if purpose == "user-decision":
        result["meaning"] = dict(decision=decision)
    elif purpose == "vision-expansion":
        result["meaning"] = {}
    elif purpose in {"scope-intent", "vision-intent"}:
        result["meaning"] = dict(impact="HOW" if purpose == "scope-intent" else "preserved")
    elif purpose == "finding-equivalence":
        pass
    elif purpose == "finding-disposition":
        result["meaning"] = dict(
            memberDispositions={k: "resolved" for k in result["meaning"]["memberDispositions"]}
        )
    elif purpose == "plan-convergence":
        result["meaning"] = dict(action="ready")
    packet = next(
        (r["payload"] for r in record["sourceRefs"] if r["kind"] == "candidate-observation"), None
    )
    if packet:
        result["meaning"] = dict(
            check=packet["check"],
            evidenceBinding=packet["evidenceBinding"],
            phase=packet["phase"],
            meaningful=True,
            correctFailureReason=True,
            noStubMockGaps=True,
            qualitativeChecks={
                o["check"]: True
                for proof in (packet["proof"], packet["proof"].get("red") or packet["proof"])
                for row in proof["results"]
                for o in row.get("observations", [])
                if o["passed"] is None
            },
        )
    return result


def advance_meaning(client, content, *, stop_at=None):
    """Answer emitted meaning, never ask for a transition, worker start, recovery or poll."""
    answered = set()
    for _ in range(40):
        run = client.current
        if stop_at is not None and stop_at(run):
            return run
        if run["state"] in {"DONE", "PAUSED", "STOPPED"}:
            return run
        if pending := run.get("question"):
            assert pending["requestId"] not in answered, run
            answered.add(pending["requestId"])
            if pending["family"] == "author":
                value = dict(
                    option="ready",
                    rationale="Author the retained dream without narrowing",
                    content=content if pending["purpose"] in {"understanding", "finding-dispositions"} else {},
                )
            elif pending["family"] == "direction":
                assert "Integrated GREEN" in pending["purpose"], (run["state"],run["reason"])
                value = dict(
                    option="continue",
                    rationale="Integrated result still matches the accepted vision",
                    content={},
                )
            else:
                value = judge_reply(pending)
            before_revision = run["revision"]
            client.answer(value)
            if pending["purpose"] == "visualQuality":
                assert client.current["revision"] == before_revision
        elif run.get("userRequest"):
            if run["userRequest"]["gate"] == "plan-execution":
                card = run["userRequest"]
                expected = client.setup["reviewers"]
                assert card["subject"]["reviewers"] == expected
                tier = client.expected_review_tier
                assert (
                    card["subject"]["reviewTier"] == tier and f"Review tier: {tier}" in card["card"]
                )
                assert "Cross-subscription is preferred, never required" in card["card"]
                assert all(
                    row["name"] in card["card"] and row["model"] in card["card"] for row in expected
                )
                assert "callCap" not in card["proposedAction"]["execution"]
                assert (
                    not {
                        "reviewAllowance",
                        "reviewPolicy",
                        "scheduledReviewStarts",
                        "repairReserve",
                    }
                    & card["subject"].keys()
                )
                assert all(
                    label not in card["card"]
                    for label in ("reviewAllowance", "callCap", "up to 10 reviews")
                )
                catalog = "unverified"
                assert card["catalog"] == catalog and "catalog: " + catalog in card["card"]
            if run["userRequest"]["gate"] == "C":
                assert (
                    run["userRequest"]["guide"]
                    and Path(run["userRequest"]["delivery"]["archive"]).is_file()
                )
            client.words("yes" if run["userRequest"]["gate"] == "align+vision-lock" else "Yes, approve the displayed subject.")
        else:
            pytest.fail(
                "Controller returned without an outstanding question, user decision or terminal result: "
                + repr((run["state"],run["reason"]))
            )
    pytest.fail("Semantic journey did not terminate")


@timed
def admission_interruption(workspace, *, root=False, boundary="segment"):
    """P1/C18/C23: death after request publication, before cursor binding.

    The old hold/release twin missed restart selecting the job root and raising.
    Require pending, then an ordinary settled readmission and completed C.
    """
    import signal
    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core import planner_jobs as jobs, planner_recovery as recovery

    workspace.mkdir()
    (workspace / "validation-observed").touch()
    hold_name, marker_name = (
        ("hold-root-admission", "root-admission-pending") if root else
        ("hold-root-published", "root-published-pending") if boundary == "root-published" else
        ("hold-popen", "popen-pending") if boundary == "popen" else
        ("hold-segment-publication", "segment-publication") if boundary == "terminal" else
        ("hold-admission", "admission-pending")
    )
    hold = workspace / hold_name
    hold.touch()
    content = plan_content()
    client = LoopClient(workspace)
    try:
        client.call("harness_plan_start", name="Interrupted admission", idea=content["nodes"][0]["text"])
        marker = workspace / ".codex-harness" / (marker_name + ".json")
        advance_meaning(client, content, stop_at=lambda r: marker.exists())
        interrupted = json.loads(marker.read_text())
        pending = jobs.worker_job_status(workspace, interrupted["jobId"])
        assert pending["status"] == ("running" if boundary == "terminal" else "pending") and "terminal" not in pending, pending
        os.kill(interrupted["ownerPid"], signal.SIGTERM)
        if boundary != "popen":
            hold.unlink()
        client.reconnect()
        client.call("harness_plan_start", name="Settle admission", idea="Continue from the bound segment")
        if boundary in {"segment", "popen", "terminal"} and not root:
            settled = recovery.inspect(workspace, interrupted["jobId"])["disposition"]
            assert settled["choice"] == "readmit", settled
            if boundary == "terminal":
                assert not settled["quiescenceVerified"], settled
            else:
                assert set(settled["proof"]["processOutcomes"].values()) == {"not launched"}, settled
        else:
            assert client.current["jobId"] == interrupted["jobId"], client.current
        assert not client.current.get("semanticHeld"), client.current
        advance_meaning(client, content)
        assert client.current["state"] == "DONE" and client.call("harness_plan_status")["acceptance"]["accepted"]
        print(f"golden admission {marker_name}: death -> pending -> ordinary continuation -> C", flush=True)
    finally:
        client.close()


@timed
def controller_interruptions(workspace, twin):
    """C16/C18/C23: lost response/live work/pause/wake/stop at the wire boundary.

    A synchronous server previously prevented active steering, and happy-path
    restarts happen only after cleanup. Hold an actual owned segment, control it
    over the same stdio connection, then expire its successor after a wake gap.
    An idle restart must also wait for an ordinary writer reservation longer
    than SQLite's default five seconds; uncontended restarts miss locked errors.
    Fail the first actual C packet after resume, regardless of recovery order;
    post-attempt failures and uninjected exhaustion miss this identity boundary.
    Also kill the post-failure automatic successor before dispatch: a prior
    question-backed attempt cannot supply the failure that only recovery recorded.
    """
    import socket
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime

    from tests.python.core.mcp_planner_support import LoopClient

    from codex_harness.core import approvals, planner_jobs
    from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly

    workspace.mkdir()
    (workspace / "validation-observed").touch()
    (workspace / "hold-contracts").touch()
    (workspace / "template/checks").mkdir(parents=True)
    (workspace / "template/checks/retired.json").write_text("[1]\n")
    content = plan_content()
    client = LoopClient(workspace)
    barrier = socket.socket()
    barrier.bind(("127.0.0.1", 0))
    barrier.listen()
    barrier.settimeout(180)  # Same transport bound as LoopClient, not an exhaustion deadline.
    quiescent = None
    pool = ThreadPoolExecutor(max_workers=1)
    fault = workspace / "controller-death.json"

    def revoke_successor():
        with barrier.accept()[0] as controller:
            controller.settimeout(30)
            admitted = json.loads(controller.makefile("rb").readline())
            assert admitted["stage"] == "controller", admitted
            fault.unlink()
            controller.sendall(b"!")
            assert controller.recv(1) == b"", "Successor controller must exit before payload entry"
            return admitted

    def recovery_state():
        client.call("harness_plan_status")
        with open_plan_store_readonly(workspace) as store:
            decisions = list(approvals.records(store, "worker-recovery-resolved"))
        return dict(run=client.current, recoveries=decisions,
                    serverLog=client.log_path.read_text(encoding="utf-8")[-4096:],
                    controllerLogs={str(p.relative_to(workspace)): p.read_text("utf-8", errors="replace")[-4096:]
                                    for p in workspace.glob(".codex-harness/worker-jobs/*/segments/*/stderr.txt")})

    def wait_for(path):
        deadline = time.monotonic() + 30
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert path.exists(), (workspace / ".codex-harness/server.log").read_text("utf-8")

    try:
        client.call(
            "harness_plan_start", name="Interruptible work", idea=content["nodes"][0]["text"]
        )
        advance_meaning(
            client,
            content,
            stop_at=lambda r: bool(
                r.get("userRequest")
                and r["userRequest"]["gate"] == "plan-execution"
                and not r.get("question")
            ),
        )
        client.words()
        lost = client.send_answer(judge_reply(client.current["question"]))
        wait_for(workspace / "contracts-entered")
        # Force pause/resume to consume quiescence before the monitor can mirror
        # its terminal. Happy scheduling hid missing segment outcomes in this gap.
        (workspace / "quiescence-fault.json").write_text(json.dumps(dict(
            port=barrier.getsockname()[1]
        )))
        pause = client.send_steer(direction="pause", rationale="Actual user pauses active work")
        quiescent, _ = barrier.accept()
        quiescent.makefile("rb").readline()
        (workspace / "quiescence-fault.json").unlink()
        client.take_reply(pause)
        client.call("harness_plan_status")
        assert client.current["state"] == "PAUSED" and not client.current["effect"]
        state = recovery_state()
        assert not state["recoveries"][0]["proof"]["held"], (
            "Pause recovery lost the admitted outcome before successor launch", state
        )
        client.take_reply(lost)
        assert not (workspace / "build-entered").exists()
        with open_plan_store_readonly(workspace) as store:
            releases = list(approvals.records(store, "run-owner-released"))
            assert len(releases) == 1 and releases[0]["ownershipReleased"]
            effect = releases[0]["effect"]
            assert effect["kind"] == "build" and effect["jobId"]
        client.reconnect()
        client.call("harness_plan_start", name="Reattach", idea="Retain the same dream")
        assert client.current["state"] == "PAUSED"
        started = set((workspace / ".codex-harness/worker-jobs").glob("*/segments/*/started.json"))
        reserved, release = threading.Event(), threading.Event()
        reservation_errors = []

        def reserve_writer():
            try:
                with open_plan_store(workspace) as writer, writer._transaction():
                    reserved.set()
                    assert release.wait(20), "Canonical writer reservation was not released"
            except Exception as error:
                reservation_errors.append(error)
                reserved.set()

        reservation = threading.Thread(target=reserve_writer, daemon=True)
        timer = None
        reservation.start()
        try:
            assert reserved.wait(15) and not reservation_errors, reservation_errors
            timer = threading.Timer(8, release.set)
            timer.start()
            # Brief Front door deletion: reconnect activation owns continuation;
            # a read-only status call correctly does not take the writer lock.
            client.reconnect()
            client.progress()
            assert release.is_set(), "Activation returned before the writer reservation ended"
        finally:
            release.set()
            if timer is not None:
                timer.cancel()
                timer.join(timeout=5)
                assert not timer.is_alive()
            reservation.join(timeout=15)
            assert not reservation.is_alive() and not reservation_errors, reservation_errors
        assert client.current["state"] == "PAUSED" and not client.current["effect"]
        assert (
            set((workspace / ".codex-harness/worker-jobs").glob("*/segments/*/started.json"))
            == started
        )
        assert not (workspace / "build-entered").exists()
        (workspace / "hold-contracts").unlink()
        (workspace / "fault-contracts").touch()
        if twin == 1:
            # Host red: fail the last automatic successor before payload, so the
            # actual worker failure exists only in canonical recovery history.
            (workspace / "controller-after-failure.json").write_text(json.dumps(dict(port=barrier.getsockname()[1], afterStarted=False)))
        quiescent.sendall(b"!")
        quiescent.close()
        quiescent = None
        resume = client.send_steer(
            direction="continue",
            rationale="Actual user explicitly resumes",
            resume=True,
        )
        client.progress()
        (workspace / "packet-fault.json").write_text(json.dumps(dict(
            port=barrier.getsockname()[1]
        )))
        client.send_answer(dict(option="continue", rationale="Resume the observed interrupted work under existing B", content={}))
        try:
            packet_socket, _ = barrier.accept()
        except TimeoutError:
            pytest.fail("Packet fault point not reached: " + json.dumps(recovery_state(), default=str))
        with packet_socket as packet:
            injected = json.loads(packet.makefile("rb").readline())
            (workspace / "packet-fault.json").unlink()
            packet.sendall(b"!")
        early_revoked = pool.submit(revoke_successor) if twin == 1 else None
        client.progress()

        def is_exhaustion(run):
            pending = run.get("question") or {}
            resolved = pending.get("context", {}).get("observation", {}).get("recovery", {})
            return (
                "Two automatic recoveries exhausted" in pending.get("purpose", "")
                and resolved.get("proof", {}).get("faultWork", {}).get(fixture_id(workspace, "C")) == injected["work"]
            )

        def wait_exhaustion():
            # Observe the run's finite admitted segments, never guess how long
            # three real jobs take. Status is read-only; the monitor advances.
            while True:
                client.call("harness_plan_status")
                run = client.current
                pending = run.get("question") or {}
                resolved = pending.get("context", {}).get("observation", {}).get("recovery", {})
                if resolved.get("proof", {}).get("faultWork", {}).get(fixture_id(workspace, "C")) == injected["work"]:
                    assert "Two automatic recoveries exhausted" in pending["purpose"], (
                        pending["purpose"], resolved["choice"], resolved["proof"]["faultWork"]
                    )
                    return
                # A wrong durable hold is failure, not transient exhaustion.
                assert not (pending and resolved.get("jobId") == injected["jobId"]), (
                    pending.get("purpose"), resolved.get("choice"),
                    resolved.get("proof", {}).get("faultWork")
                )
                with open_plan_store_readonly(workspace) as store:
                    segment = planner_jobs.latest_segment(store, run["jobId"])
                    admitted = json.loads((segment / "request.json").read_text())
                assert datetime.now(UTC) < datetime.fromisoformat(admitted["deadlineAt"]), run
                time.sleep(0.05)

        wait_exhaustion()
        if early_revoked is not None:
            early = early_revoked.result(timeout=30)
            assert client.current["jobId"] == early["jobId"] and "run" not in planner_jobs.worker_job_status(workspace, early["jobId"])
        assert client.current["state"] == "STEER" and client.current["question"]["family"] == "direction", client.current
        with open_plan_store_readonly(workspace) as store:
            segment = planner_jobs.latest_segment(store, injected["jobId"])
            outcome = json.loads((segment / "quiescence.json").read_text())["terminal"]["outcomes"][fixture_id(workspace, "C")]
            assert outcome["workId"] == injected["work"] and outcome["fault"]
            assert "Acknowledged pre-attempt packet failure" in outcome["workStatus"]
            assert any(
                r["choice"] == "readmit" and r.get("steeringJudgmentId")
                and injected["work"] in r["proof"]["faultWork"].values()
                for r in approvals.records(store, "worker-recovery-resolved")
            ), "Explicit resume must retain the injected work"
            recoveries = [
                r
                for r in approvals.records(store, "worker-recovery-resolved")
                if r["choice"] == "readmit" and not r.get("steeringJudgmentId")
                and injected["work"] in r["proof"]["faultWork"].values()
            ]
            assert len(recoveries) == 2
        print(
            f"golden recovery twin #{twin}: acknowledged packet failure; retained resume; same-work bound exhaustion",
            flush=True,
        )
        # An unrelated downstream amendment cannot buy two more retries for C.
        build = next(n for n in content["nodes"] if n["id"] == "B")
        build["title"] += " (clearer handoff)"
        client.answer(
            dict(
                option="repair",
                rationale="Clarify downstream handoff, retain C obligation",
                content=dict(nodes=[build]),
            )
        )
        # r6: the host lost this new publication's controller before dispatch,
        # so its empty run hid the earlier failed attempts of unchanged work.
        # Use the existing real controller boundary, never fabricated run data.
        if twin == 1:
            fault.write_text(json.dumps(dict(port=barrier.getsockname()[1], afterStarted=False)))
            revoked = pool.submit(revoke_successor)
        advance_meaning(client, content, stop_at=lambda r: is_exhaustion(r) or (r.get("effect") or {}).get("kind") == "build")
        if twin == 1:
            successor = revoked.result(timeout=30)
            assert client.current["jobId"] == successor["jobId"]
            assert "run" not in planner_jobs.worker_job_status(workspace, successor["jobId"])
        wait_exhaustion()
        with open_plan_store_readonly(workspace) as store:
            recoveries = [
                r
                for r in approvals.records(store, "worker-recovery-resolved")
                if r["choice"] == "readmit" and not r.get("steeringJudgmentId")
                and injected["work"] in r["proof"]["faultWork"].values()
            ]
            assert len(recoveries) == 2
        client.answer(dict(option="pause",rationale="The same failed approach remains unresolved",content={}))
        words = "Continue with the same failed approach and evidence."
        client.send_steer(rationale=words);client.progress()
        client.answer(dict(option="pause",rationale="The same failed approach remains unresolved",content={}))
        client.send_steer(rationale=words);client.progress()
        assert client.current["state"] == "PAUSED" and not client.current.get("question") and "No progress" in client.current["reason"]
        client.send_steer(rationale="Change the startup approach using the retained failed attempts.")
        client.progress()
        # The scripted planner can change approach only from the supplied
        # failed job, affected Contracts node and prior unsuccessful direction.
        facts = client.offered["packet"]["facts"]
        context = facts["context"]
        contracts_id = fixture_id(workspace,"C")
        failed = str(context.get("job",{}).get("terminal",{})) + str(context.get("attempts",{}).get(contracts_id,[]))
        ready_terms = dict(startupFailure="Injected worker failure" in failed, attempts=bool(context.get("attempts")),
                           priorDirections=bool(context.get("priorDirections")), contractsInGraph=any(n["id"] == fixture_id(workspace,"C") for n in facts["graph"]))
        if twin == 1 and all(ready_terms.values()):
            # C26: readiness alone misses relabeled current results or retagged history.
            native = planner_jobs.worker_job_status(workspace, context["jobId"])["terminal"]
            dossier = context["job"]["terminal"]
            assert {k:v for k,v in dossier.items() if k != "retainedWorkObservations"} == native
            work = native["outcomes"][contracts_id]["workId"]
            history = [h for h in dossier["retainedWorkObservations"] if h["workId"] == work]
            assert "Injected worker failure" not in str(native["outcomes"]), "Historical failure was relabeled as current"
            assert all(any(term in h["outcome"]["workStatus"] for h in history) for term in ("Injected worker failure", "Acknowledged pre-attempt packet failure")), history
            assert len({h["eventId"] for h in history}) == len(history), "One recovery was repeated as several failures"
            with open_plan_store_readonly(workspace) as store:
                for h in history:
                    kind, raw = store.connection.execute("SELECT kind,payload_json FROM events WHERE id=?", (h["eventId"],)).fetchone()
                    original = json.loads(raw)
                    assert kind == h["kind"] == "worker-recovery-resolved" and h["historical"] and h["jobId"] != context["jobId"]
                    assert (original["jobId"],original["segmentId"],original["proof"]["faultWork"][contracts_id]) == (h["jobId"],h["segmentId"],work)
                    assert h["outcome"] == dict(workId=work,workStatus=original["proof"]["faultOutcomes"][contracts_id])
                    attempt = next(row for row in context["attempts"][contracts_id] if row.get("source",{}).get("eventId") == h["eventId"])
                    assert attempt == dict(blocker=h["outcome"]["workStatus"],source={k:v for k,v in h.items() if k != "outcome"})
        contract = next(n for n in content["nodes"] if n["id"] == "C")
        if all(ready_terms.values()):
            contract["text"] += " Use an isolated startup path after the failed shared startup"
            client.answer(dict(option="repair",rationale="The shared startup approach failed; isolate startup before exercising receipt behavior",content=dict(nodes=[contract])))
            advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "execution")
        else:
            client.answer(dict(option="pause",rationale="The failed approach cannot be established from the supplied facts",content={}))
        assert (client.current.get("question") or {}).get("purpose") == "execution", dict(failedReadyTerms=[k for k,v in ready_terms.items() if not v], readyTerms=ready_terms, jobId=context.get("jobId"), contextKeys=list(context), state=client.current["state"], reason=client.current["reason"])
        pending = client.current["question"]
        wrong = json.loads(provider_answer(workspace,pending,judge_reply(pending)))
        wrong["check"] = "contracts"
        client.answer(json.dumps(wrong))
        assert client.current["question"]["requestId"] == pending["requestId"]
        assert "check" in client.current["question"]["judgment"]["error"]
        if twin == 2:
            # C18/I6: a prior segment's receipts cannot certify the new launch.
            # Interrupt actual continuation entry, whether dispatch would resume or start fresh.
            contracts_before = set(workspace.glob(".codex-harness/worker-jobs/*/runs/*/evidence/*/attempt/request.json"))
            (workspace / "resume-fault.json").write_text(json.dumps(dict(
                port=barrier.getsockname()[1]
            )))
            resumed = client.send_answer(judge_reply(client.current["question"]))
            with barrier.accept()[0] as interrupted:
                entry = json.loads(interrupted.makefile("rb").readline())
                held_job = entry["jobId"]
                repaired_work = entry["work"][fixture_id(workspace, "B")]
                (workspace / "resume-fault.json").unlink()
                interrupted.sendall(b"!")
            segment = planner_jobs.segment_directory(workspace, held_job, entry["segmentId"])
            assert json.loads((segment / "request.json").read_text())["work"] == entry["work"]
            client.progress()
            while True:
                client.call("harness_plan_status")
                pending = client.current.get("question") or {}
                resolved = pending.get("context", {}).get("observation", {}).get("recovery", {})
                if resolved.get("jobId") == held_job:
                    assert pending["purpose"] == "Permission/uncertain launch held"
                    assert resolved["choice"] == "abandon"
                    assert resolved["proof"]["faultWork"] == {fixture_id(workspace, "B"): repaired_work}
                    assert resolved["proof"]["processOutcomes"][fixture_id(workspace, "C")] == "contracts-red"
                    assert set(workspace.glob(".codex-harness/worker-jobs/*/runs/*/evidence/*/attempt/request.json")) == contracts_before
                    assert set(resolved["proof"]["faultOutcomes"].values()) == {"unknown launch"}
                    assert resolved["proof"]["controllerAndChildren"]["quiescenceVerified"]
                    break
                with open_plan_store_readonly(workspace) as store:
                    segment = planner_jobs.latest_segment(store, held_job)
                    admitted = json.loads((segment / "request.json").read_text())
                assert datetime.now(UTC) < datetime.fromisoformat(admitted["deadlineAt"])
                time.sleep(0.05)
            with open_plan_store_readonly(workspace) as store:
                assert sum(
                    r["choice"] == "readmit" and not r.get("steeringJudgmentId")
                    and injected["work"] in r["proof"]["faultWork"].values()
                    for r in approvals.records(store, "worker-recovery-resolved")
                ) == 2
            print("golden recovery: interrupted resume retained unknown-launch hold", flush=True)
        else:
            client.answer(judge_reply(client.current["question"]))
        client.call(
            "harness_steer",
            direction="stop",
            rationale="Actual user revokes continuation before delivery",
        )
        assert client.current["state"] == "STOPPED"
        assert not (workspace / "hashslip.py").exists()
        client.call("harness_plan_start", name="Reattach", idea="Same dream")
        assert client.current["state"] == "STOPPED"
        with open_plan_store_readonly(workspace) as store:
            assert not store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchone()
    finally:
        if quiescent is not None:
            quiescent.sendall(b"!")
            quiescent.close()
        barrier.close()
        pool.shutdown(wait=True,cancel_futures=True)
        (workspace / "hold-contracts").unlink(missing_ok=True)
        client.close()


def controller_launch_interruption(workspace, after_started, *, stale=False):
    """C18/C23: replacement resumes unfinished work without semantic rewind.

    Existing packet faults run after entry and known dispatch failure; the denial
    twin has a result. Neither caught completed Contracts charged/rejudged after
    pre-entry Build death, unusable evidence aborting ordinary rerun, or same-job
    controller losses collapsing the two-recovery bound. Reach C after direction.
    """
    import socket
    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core.plan_store import open_plan_store_readonly

    workspace.mkdir()
    (workspace / "contracts-attempted").touch()
    (workspace / "validation-observed").touch()
    content = plan_content()
    with socket.socket() as barrier:
        barrier.bind(("127.0.0.1", 0))
        barrier.listen()
        barrier.settimeout(180)
        fault = workspace / "controller-death.json"
        client = LoopClient(workspace)
        connections = {}
        try:
            client.call("harness_plan_start", name="Controller launch certainty", idea=content["nodes"][0]["text"])
            if after_started:
                advance_meaning(client, content, stop_at=lambda r: bool(
                    r.get("userRequest") and r["userRequest"]["gate"] == "plan-execution"
                    and not r.get("question")
                ))
                client.words()
            else:
                from codex_harness.core.planner_candidate import current_job_run

                advance_meaning(client, content, stop_at=lambda r: bool(
                    r.get("question") and r["question"]["purpose"] == "execution"
                ))
                with open_plan_store_readonly(workspace) as store:
                    selected, original = current_job_run(store, required=True)
                original_path = Path(selected["runPath"])
                assert original["semanticContracts"]["phase"] == "contracts"
            fault.write_text(json.dumps(dict(port=barrier.getsockname()[1], afterStarted=after_started)))
            reply = client.send_answer(judge_reply(client.current["question"]))
            repeated = not (after_started or stale)
            for loss in range(3 if repeated else 1):
                for _ in range(2 if after_started else 1):
                    try:
                        connection, _ = barrier.accept()
                    except TimeoutError:
                        client.call("harness_plan_status")
                        pytest.fail("Controller death boundary not reached: " + json.dumps(dict(
                            run=client.current, reached=list(connections),
                            controllerLogs={str(p.relative_to(workspace)): p.read_text("utf-8", errors="replace")[-4096:]
                                            for p in workspace.glob(".codex-harness/worker-jobs/*/segments/*/stderr.txt")},
                        ), default=str))
                    connection.settimeout(180)
                    observed = json.loads(connection.makefile("rb").readline())
                    job = observed["jobId"]
                    connections[observed["stage"]] = connection
                if not repeated or loss == 2:
                    fault.unlink()
                connections["controller"].sendall(b"!")
                assert connections["controller"].recv(1) == b"", "Controller must actually exit before activation"
                connections["controller"].close()
            if after_started:
                try:
                    assert connections["started"].recv(1) == b"", "Owned Job must stop the entered payload"
                except ConnectionResetError:
                    pass  # Windows Job termination may reset the held payload's socket.
            else:
                original = json.loads(original_path.read_text())
                original_proof = original["semanticContracts"]
                assert original_proof["admitted"] and original_proof["judgmentId"]
                if stale in {"receipt-missing", "receipt-malformed"}:
                    receipt = Path(original["beads"][fixture_id(workspace, "C")]["integrated"]["receiptPath"])
                    if stale == "receipt-missing":
                        receipt.unlink()
                    else:
                        receipt.write_text("{broken original receipt", encoding="utf-8")
                elif stale:
                    root = Path(original["beads"][fixture_id(workspace, "C")]["integrated"]["root"])
                    with (root / "hashslip.py").open("a") as changed:
                        changed.write("\n# Retained observation bytes changed after judgment.\n")
            client.progress()
            with open_plan_store_readonly(workspace) as store:
                recoveries = list(reversed([r for r in approvals.records(store, "worker-recovery-resolved") if r["jobId"] == job]))
            if repeated:
                assert [r["choice"] for r in recoveries] == ["readmit", "readmit", "steer"], recoveries
                assert len({r["segmentId"] for r in recoveries}) == 3
                assert all(r["proof"]["faultBeads"] == [fixture_id(workspace, "B")] for r in recoveries)
                assert "Two automatic recoveries exhausted" in client.current["question"]["purpose"]
                facts = client.offered["packet"]["facts"]
                contracts = fixture_id(workspace, "C")
                assert facts["context"]["affectedNodeIds"] == [fixture_id(workspace, "B")]
                assert contracts in facts["context"]["attempts"] and any(n["id"] == contracts for n in facts["graph"]), facts
                client.answer(dict(option="continue", rationale="Continue unchanged admitted Build after bounded controller losses", content={}))
            resolved = recoveries[0]
            assert resolved["choice"] == ("abandon" if after_started else "readmit"), resolved
            assert resolved["proof"]["held"] is after_started, resolved
            probe = resolved["proof"]["processObservation"]
            assert probe["running"] is False and (
                probe.get("openProcessError") == 87 or probe.get("exitCode") == 73
            ), probe
            if after_started:
                assert set(resolved["proof"]["processOutcomes"].values()) == {"unknown launch"}, resolved
                assert not (workspace / "contracts-entered").exists(), "Revoked segment must not reach dispatch"
                assert client.current["question"]["purpose"] == "Permission/uncertain launch held", client.current
                assert not client.current["effect"]
            else:
                assert resolved["proof"]["faultBeads"] == [fixture_id(workspace, "B")], resolved
                assert resolved["proof"]["processOutcomes"][fixture_id(workspace, "B")] == "not launched"
                pending = client.current["question"]
                example = fixture_judgment_example(pending["judgment"])["responseExample"]["meaning"]
                assert example["phase"] == ("contracts" if stale else "validation"), client.current
                with open_plan_store_readonly(workspace) as store:
                    resumed_selection, successor = current_job_run(store, required=True)
                if stale:
                    assert example["evidenceBinding"] != original_proof["evidenceBinding"]
                    assert not successor["semanticContracts"].get("judgmentId")
                else:
                    assert successor["repo"] == original["repo"]
                    assert client.current["jobId"] == job and resumed_selection["runPath"] == str(original_path)
                    assert successor["semanticContracts"] == original_proof
                    assert successor["beads"][fixture_id(workspace, "C")]["evidenceDir"] == original["beads"][fixture_id(workspace, "C")]["evidenceDir"]
                    if repeated:
                        with open_plan_store_readonly(workspace) as store:
                            from codex_harness.core import planner_jobs as jobs
                            roots = {p.parent.name for p in (jobs._directory(workspace, job) / "segments").glob("*/request.json") if json.loads(p.read_text())["resumeRun"] is None}
                            assert len(roots) == 1, roots
                            assert_job_segments(store, job, roots | {jobs.latest_segment(store, job).name})
                advance_meaning(client, content)
                assert client.current["state"] == "DONE", client.current
                with open_plan_store_readonly(workspace) as store:
                    observations = [r for r in approvals.records(store, "candidate-observation")
                                    if r.get("check") == "execution" and r.get("phase") == "contracts"]
                assert len(observations) == (2 if stale else 1), observations
                attempts = [json.loads(p.read_text()) for p in workspace.glob(
                    ".codex-harness/worker-jobs/*/runs/*/evidence/*/attempt/request.json")]
                assert sum(a["bead"] == fixture_id(workspace, "C") for a in attempts) == (2 if stale else 1)
            print(f"golden controller death afterStarted={after_started} stale={stale}: {resolved['choice']}; original credit/ordinary rerun and successor/hold observed", flush=True)
        finally:
            for connection in connections.values():
                connection.close()
            client.call("harness_steer", direction="stop", rationale="End the controller launch fault journey")
            client.close()


@timed
def denial_interruption(workspace, source_root=None):
    """C18/I6: durable provider denial holds even when its run projection is lost.

    Packet faults have no result; ordinary denied returns update run.json. Kill
    the owned payload at the acknowledged gap between those two publications.
    """
    import socket
    from datetime import UTC, datetime

    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core import planner_jobs
    from codex_harness.core.plan_store import open_plan_store_readonly

    workspace.mkdir()
    content = plan_content()
    with socket.socket() as barrier:
        barrier.bind(("127.0.0.1", 0))
        barrier.listen()
        barrier.settimeout(180)
        (workspace / "denial-fault.json").write_text(json.dumps(dict(port=barrier.getsockname()[1])))
        client = LoopClient(workspace, source_root=source_root)
        try:
            client.call("harness_plan_start", name="Retain denied launch", idea=content["nodes"][0]["text"])
            advance_meaning(client, content, stop_at=lambda r: bool(
                r.get("userRequest") and r["userRequest"]["gate"] == "plan-execution"
                and not r.get("question")
            ))
            client.words()
            reply = client.send_answer(judge_reply(client.current["question"]))
            with barrier.accept()[0] as denied:
                job = denied.makefile("rb").readline().decode().strip()
                (workspace / "denial-fault.json").unlink()
                denied.sendall(b"!")
            client.take_reply(reply)
            while True:
                client.call("harness_plan_status")
                with open_plan_store_readonly(workspace) as store:
                    resolved = [r for r in approvals.records(store, "worker-recovery-resolved")
                                if r["jobId"] == job]
                    readmissions = [r for r in approvals.records(store, "worker-recovery-resolved")
                                    if r["choice"] == "readmit"]
                    segment = planner_jobs.latest_segment(store, job)
                    admitted = json.loads((segment / "request.json").read_text())
                assert not readmissions, "A persisted provider denial was automatically readmitted"
                if resolved:
                    pending = client.current.get("question") or {}
                    bound = pending.get("context", {}).get("observation", {}).get("recovery", {})
                    if bound.get("jobId") == job:
                        assert resolved[0]["choice"] == "abandon"
                        assert bound["proof"]["processOutcomes"][fixture_id(workspace, "C")] == "held: permission guard"
                        assert bound["proof"]["held"]
                        assert pending["purpose"] == "Permission/uncertain launch held"
                        assert not client.current["effect"]
                        break
                assert datetime.now(UTC) < datetime.fromisoformat(admitted["deadlineAt"])
                time.sleep(0.05)  # Status has no blocking wait; bounded by admitted progress.
            print("golden recovery denial: durable permission hold; zero readmissions", flush=True)
        finally:
            client.call("harness_steer", direction="stop", rationale="End the owned denial twin")
            client.close()


@timed
def dead_review_wake(workspace):
    """C18/C23/C25: real MCP death plus elapsed wake gap releases a review once.

    The ordinary restart occurs after quiescence. Kill the actual review owner
    during its bounded provider wait, advance only its observation clock, and
    activate twice: no dead reservation, reviewer credit, B or automatic replay.
    """
    from tests.python.core.mcp_planner_support import LoopClient

    from codex_harness.core import approvals
    from codex_harness.core.plan_store import open_plan_store_readonly

    workspace.mkdir()
    content = plan_content()
    (workspace / "inject-review-crash").touch()
    client = LoopClient(workspace)
    try:
        client.call(
            "harness_plan_start", name="Interrupted review", idea=content["nodes"][0]["text"]
        )
        advance_meaning(
            client,
            content,
            stop_at=lambda r: bool(
                (r.get("effect") or {}).get("kind") == "review"
            ),
        )
        marker = workspace / ".codex-harness/review-crash-started.ready"
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), (workspace / ".codex-harness/server.log").read_text("utf-8")
        with open_plan_store_readonly(workspace) as store:
            original = store.status()["run"]["effect"]
            from codex_harness.core.audit_process import process_observation
            assert process_observation(original["ownerPid"])["identity"] == original["ownerIdentity"]
        (workspace / "clock-shift").write_text("3600")
        if original["ownerPid"] != client.process.pid:
            import signal
            os.kill(original["ownerPid"],signal.SIGTERM)
        client.reconnect()
        client.call("harness_plan_start", name="Reattach", idea="Retained dream")
        assert client.current["state"] == "STEER" and not client.current["effect"]
        assert client.current["generation"] > original["generation"]
        pending_id = client.current["question"]["requestId"]
        client.call("harness_plan_start", name="Reattach again", idea="Retained dream")
        assert client.current["question"]["requestId"] == pending_id
        with open_plan_store_readonly(workspace) as store:
            released = list(approvals.records(store, "run-owner-released"))
            assert len(released) == 1 and released[0]["effect"]["id"] == original["id"]
            assert released[0]["ownershipReleased"] and not released[0]["quiescenceVerified"]
            assert list(approvals.records(store, "run-wake-gap"))
            assert not store.connection.execute(
                "SELECT 1 FROM review_attempts WHERE state IN ('running','queued')"
            ).fetchone()
            assert approvals.current(store, "B") is None
    finally:
        client.close()


@pytest.fixture
def http_journeys(tmp_path):
    """Overlap independent HTTP journeys; require their assertions and owned cleanup.

    Separate interpreters isolate provider monkeypatches, workspaces and ports.
    A failed child or unfinished process tree must fail the enclosing CLI case.
    """
    from concurrent.futures import ThreadPoolExecutor

    from codex_harness.core.audit_process import run_audit_process

    stop = threading.Event()

    def run_http(name, semantic, ready):
        started = time.monotonic()
        workspace = tmp_path.parent / (tmp_path.name + "-" + name)
        try:
            completed = run_audit_process(
                [
                    sys.executable,
                    "-B",
                    str(Path(__file__).resolve()),
                    "http-golden",
                    str(workspace),
                    str(int(semantic)),
                ],
                cwd=ROOT,
                timeout=600,
                stop_when=stop.is_set,
                on_launch=lambda _: ready.set(),
            )
            if completed.returncode:
                workspace.mkdir(parents=True, exist_ok=True)
                failure_log = workspace / "journey-failure.log"
                output = (
                    "=== stdout ===\n" + completed.stdout + "\n=== stderr ===\n" + completed.stderr
                )
                failure_log.write_text(output, encoding="utf-8")
                tail = "\n".join(output.splitlines()[-60:])
                assert completed.returncode == 0, (
                    f"{name}: full output: {failure_log}\nLast 60 lines:\n{tail}"
                )
            assert completed.audit_control["quiescenceVerified"]
            assert completed.audit_control["activeProcessesAfterTeardown"] == 0
            print(completed.stdout, file=sys.stderr, end="", flush=True)
            print(completed.stderr, file=sys.stderr, end="", flush=True)
            print(
                f"golden {name}: seconds={time.monotonic() - started:.2f}",
                file=sys.stderr,
                flush=True,
            )
        finally:
            ready.set()  # Also wake setup if launch failed; future.result retains that failure.

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = []
        try:
            for name, semantic in (("http-journey", False), ("shelflife-scenarios", True)):
                ready = threading.Event()
                future = pool.submit(run_http, name, semantic, ready)
                pending.append(future)
                assert ready.wait(60), f"HTTP journey did not start: {name}"
                if future.done():
                    future.result()
            # Reuse this otherwise waiting parent slot; keep recovery cost out
            # of the already long main CLI journey and preserve suite count.
            for index, (entered, stale) in enumerate(((False, False), (False, "observation"),
                    (False, "receipt-missing"), (False, "receipt-malformed"), (True, False))):
                controller_launch_interruption(
                    tmp_path.parent / (tmp_path.name + f"-controller-{index}"),
                    entered, stale=stale,
                )
            yield pending
            for future in pending:
                future.result()
        finally:
            stop.set()


@pytest.mark.parametrize("surface", ["cli", "web"])
def test_golden_path(tmp_path, request, surface):
    """C1/C8/C14/C18/C23: own words→plan→review→B→real RED/GREEN→delivery→C.

    Existing golden choreographed each transition and kept the original worker
    alive during judgment. This client submits meaning only. Observe both worker
    process exits, answer the immutable execution request after MCP death, then
    require one Build and one bound C without repeating Contracts.
    Clean-core journeys prove one greeting unit and independent tiny-plan concurrency through C.
    """
    from tests.python.core.mcp_planner_support import LoopClient

    from codex_harness.core import approvals
    from codex_harness.core.plan_store import open_plan_store_readonly

    started = time.monotonic()
    ledger_start = len(LoopClient.instances)
    if surface == "cli":
        clean_core_golden(tmp_path / "clean-core")
        small_plan_golden(tmp_path / "small-plan")
        fixture_ownership_golden(tmp_path / "fixture-ownership")
        planning_surface_golden(tmp_path / "planning-surface")
        missing_kickoff_golden(tmp_path / "missing-kickoff")
        old_format_refusal_golden(tmp_path / "old-format")
        reviewer_order_golden(tmp_path / "reviewer-order")
        approval_order_golden(tmp_path / "approval-order")
        r18_authoring_golden(tmp_path / "r18-replay")
        planning_intent_golden(tmp_path.parent / (tmp_path.name + "-planning-intent"))
        judgment_contract_golden(tmp_path.parent / (tmp_path.name + "-judgment-contract"))
        execution_judgment_golden(tmp_path.parent / (tmp_path.name + "-execution-judgment"))
        trace_contract_golden(tmp_path.parent / (tmp_path.name + "-trace-contract"))
    if surface == "web":
        small_plan_golden(tmp_path / "shared-readiness",size="medium")
        from codex_harness.core.semantic_runner import run as observe

        timeout_product = tmp_path / "timeout-product"
        timeout_product.mkdir()
        api = dict(target="service", method="GET", path="/", statusEquals=200)
        # Direct runner input retains these IDs; loop-authored plans use CODE allocations.
        timeout_spec = dict(
            schema="seldon/scenarios-v1",
            scenarios=[
                dict(
                    id="timeout",
                    requirementRefs=["R1"],
                    contractRefs=[],
                    setup={},
                    actions=[dict(id="api", http=api)],
                    observe=[dict(action="api")],
                    verdict="all",
                )
            ],
        )
        (timeout_product / "wait.py").write_text("import time; time.sleep(60)\n", encoding="utf-8")
        before = time.monotonic()
        timed = observe(
            timeout_spec,
            timeout_product,
            {
                "service": dict(
                    server=dict(
                        entry="wait.py",
                        runtime=sys.executable,
                        argv=[],
                        readyPath="/",
                        readyWithin=0.25,
                    )
                )
            },
            "validation",
            web_config=dict(chromium="missing-browser", playwright="missing-playwright"),
        )[0]
        assert timed["state"] == "ERROR" and "readiness timeout" in timed["reason"], timed
        assert time.monotonic() - before < 15
        assert (
            timed["cleanup"]["quiescenceVerified"]
            and timed["cleanup"]["activeProcessesAfterTeardown"] == 0
        )
        assert timed["actions"]["api"]["server"]["stopped"]
        # CHROME1: a real executable that cannot speak Chromium's protocol must
        # produce bounded, unverified infrastructure evidence, never a product FAIL.
        # The healthy browser journeys never exercised normal launch rejection.
        from tests.python.core.semantic_runner_support import WEB_PAGE, WEB_TARGET, web_document

        (timeout_product / "index.html").write_text(WEB_PAGE, encoding="utf-8")
        unavailable = observe(
            web_document(split=True),
            timeout_product,
            WEB_TARGET,
            "validation",
            web_config=dict(chromium=sys.executable),
        )
        assert len(unavailable) == 4 and all(
            r["state"] == "pending-judgment" for r in unavailable
        ), unavailable
        assert all(
            r["unperformed"] and r["evidenceTier"] == "browser: UNVERIFIED" for r in unavailable
        ), unavailable
        assert all(
            "browser unavailable" in r["reason"] and r["launchDiagnostic"] for r in unavailable
        ), unavailable
        assert all(
            r["cleanup"]["quiescenceVerified"] and r["cleanup"]["activeProcessesAfterTeardown"] == 0
            for r in unavailable
        )

    if surface == "web":
        request.getfixturevalue("web_browser_ready")
    main_path_golden(tmp_path,surface,started=started,ledger_start=ledger_start)


def assert_job_segments(store, job_id, expected):
    """C18/C23: controller replacement may add only accounted, unentered work.

    Fixed counts rejected safe handoff; dropping the count missed unexplained
    controllers and Contracts reruns. Require native admission and recovery.
    """
    from codex_harness.core import planner_jobs as jobs
    directory = jobs._directory(store.workspace, job_id)
    root_request = jobs._request(directory)
    segments = {p.parent.name: json.loads(p.read_text()) for p in (directory / "segments").glob("*/quiescence.json")}
    assert segments.keys() == {p.name for p in (directory / "segments").iterdir() if p.is_dir()}
    assert expected <= segments.keys(), (expected, segments.keys())
    admitted = [r["effect"] for r in approvals.records(store, "run-loop-cursor") if (r.get("effect") or {}).get("jobId") == job_id]
    recoveries = approvals.records(store, "worker-recovery-resolved")
    for key, receipt in segments.items():
        path = directory / "segments" / key
        request = json.loads((path / "request.json").read_text())
        assert request["jobId"] == job_id and request["segmentId"] == key
        assert request["work"] == jobs.admitted_work(store, directory, root_request)
        assert any(e.get("segmentId") == key and e["generation"] == request["generation"] for e in admitted), key
        assert receipt["segmentId"] == receipt["terminal"]["segmentId"] == key
        assert receipt["requestDigest"] == receipt["terminal"]["requestDigest"] == root_request["requestDigest"]
        if key in expected:
            assert (path / "started.json").is_file(), key
            assert jobs.verified_segment_exit(root_request, key, receipt), key
            continue
        assert not (path / "started.json").exists(), ("Unexplained entered controller", key)
        outcomes = receipt["terminal"]["outcomes"]
        assert {k:o["workId"] for k,o in outcomes.items()} == request["work"]
        assert all(o["launchCertain"] and (o["status"] == "not launched" or not o["fault"] and o["status"] in {"contracts-red", "reused", "requirement-validated", "already-satisfied"}) for o in outcomes.values()), outcomes
        assert not (path / "launch.json").exists() or any(
            r["jobId"] == job_id and r.get("segmentId") == key
            and (r["proof"].get("processObservation") or {}).get("running") is False
            for r in recoveries), ("Controller exit unproven", key)
        assert any(r["jobId"] == job_id and r.get("segmentId") == key and r["choice"] == "readmit"
                   and r["ownershipReleased"]
                   and r["successor"] == job_id and r.get("resumeRun") == request["resumeRun"] and request["resumeRun"]
                   and r["proof"]["processOutcomes"] == {k:o["status"] for k,o in outcomes.items()}
                   for r in recoveries), ("Unexplained unentered segment", key)


@timed
def main_path_golden(tmp_path,surface,*,started=None,ledger_start=None):
    """Real contracts failure, retained reply, repair, build, delivery and C.

    The single-build journey misses a concrete wrong-reason RED repaired by the
    successor and a raw semantic receipt retained across owner death.
    """
    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core.plan_store import open_plan_store_readonly
    started = time.monotonic() if started is None else started
    ledger_start = len(LoopClient.instances) if ledger_start is None else ledger_start
    content = plan_content(surface)
    (tmp_path / "AGENTS.md").write_text("Use the exact selected model tuples.\n", encoding="utf-8")
    (tmp_path / "validation-observed").touch()
    if surface == "cli":
        (tmp_path / "template/checks").mkdir(parents=True)
        (tmp_path / "template/checks/retired.json").write_text("[1]\n")
    if surface == "cli":
        (tmp_path / "hold-segment-publication").touch()
    client = LoopClient(tmp_path, surface)
    try:
        client.call("harness_plan_start", name="Retained dream", idea=content["nodes"][0]["text"])
        execution_questions = []

        def execution(run):
            return bool(run.get("question") and run["question"]["purpose"] == "execution")

        advance_meaning(client, content, stop_at=lambda r: execution(r) or (
            tmp_path / ".codex-harness/segment-publication.ready").exists())
        if surface == "cli":
            # C18/C26: the real payload finished, but its owner has not published
            # cleanup. Happy scheduling missed the false terminal status here.
            from codex_harness.core import planner_jobs as jobs, planner_recovery
            pending = jobs.worker_job_status(tmp_path, client.current["jobId"])
            assert pending["status"] == "running" and "terminal" not in pending, pending
            proof = planner_recovery.inspect(tmp_path, pending["jobId"])
            assert proof["held"] and not proof["processOutcomes"], proof
            (tmp_path / "hold-segment-publication").unlink()
            (tmp_path / ".codex-harness/segment-publication.release").touch()
            client.progress()
        first = client.current["question"]
        execution_questions.append(first["requestId"])
        assert fixture_judgment_example(first["judgment"])["responseExample"]["meaning"]["check"] == "execution"
        with open_plan_store_readonly(tmp_path) as store:
            from codex_harness.core import planner_jobs as jobs

            run = client.current
            first_segment = jobs.latest_segment(store, run["jobId"])
            assert_job_segments(store, run["jobId"], {first_segment.name})
            cleanup = json.loads((first_segment / "quiescence.json").read_text())["control"]
            assert cleanup["quiescenceVerified"] and cleanup["activeProcessesAfterTeardown"] == 0
            assert not approvals.current(store, "B")["human_identity_verified"]
            from codex_harness.core.planner_candidate import current_job_run

            _, first_saved = current_job_run(store, required=True)
            assert first_saved["status"] == "pending-judgment"
            assert first_saved["beads"][fixture_id(tmp_path, "C") ]["status"] == "awaiting judgment"
            assert first_saved["beads"][fixture_id(tmp_path, "B") ]["attempt"] is None
            assert "awaiting judgment" in first_saved["beads"][fixture_id(tmp_path, "B") ]["status"]
            if surface == "web":
                assert first_saved["semanticContracts"]["observedState"] == "ERROR"
                assert any(
                    "can't open file" in action.get("reason", "")
                    for row in first_saved["semanticContracts"]["results"]
                    for action in row.get("actions", {}).values()
                )
            # Review must precede B. The old B-first guard made this impossible.
            review_at = store.connection.execute(
                "SELECT min(id) FROM events WHERE kind='judgment-recorded' AND json_extract(payload_json,'$.question')='plan-review'"
            ).fetchone()[0]
            b_at = store.connection.execute(
                "SELECT min(id) FROM events WHERE kind=? AND json_extract(payload_json,'$.subject.kind')='B'",
                (approvals.EVENT,),
            ).fetchone()[0]
            assert review_at < b_at
        # The semantic call now belongs to the loop. Retain its actual raw
        # response before killing the owning process, then consume it once on
        # activation; an unknown return is covered separately by the wake case.
        assert fixture_judgment_example(first["judgment"])["responseExample"]["meaning"]["phase"] == "contracts"
        if surface == "cli":
            packet = next(
                ref["payload"]
                for ref in first["judgment"]["sourceRefs"]
                if ref["kind"] == "candidate-observation"
            )
            errors = str(packet["proof"]["results"])
            assert "receipt startup failed" in errors, errors
            (tmp_path / "hold-execution-raw").touch()
            client.send_answer(judge_reply(first,verdict="negative",gaps=["Receipt startup fails before checking the expected binary behavior"]))
            marker = tmp_path / ".codex-harness/execution-raw-complete.ready"
            deadline = time.monotonic()+30
            while not marker.exists() and time.monotonic()<deadline:
                time.sleep(.05)
            assert marker.exists()
            client.seen_faults.add("execution-raw-complete")
            retained_raw = json.loads(marker.with_suffix(".json").read_text("utf-8"))
            with open_plan_store_readonly(tmp_path) as store:
                lease = store.status()["run"]["effect"]
                assert lease["id"] == retained_raw["effectId"] and lease["ownerPid"] == retained_raw["ownerPid"]
            if retained_raw["ownerPid"] != client.process.pid:
                import signal
                os.kill(retained_raw["ownerPid"],signal.SIGTERM)
            client.reconnect()
            client.call("harness_plan_start",name="Resume verified semantic receipt",idea="Keep the full dream")
            with open_plan_store_readonly(tmp_path) as store:
                assert store._judgment_record(first["requestId"])["binding"] == first["binding"]
                assert len([r for r in approvals.records(store,"run-invocation-completed") if r["effectId"] == retained_raw["effectId"]]) == 1
            retry = client.current["question"]
            execution_questions.append(retry["requestId"])
            assert (
                retry["purpose"] == "execution"
                and fixture_judgment_example(retry["judgment"])["responseExample"]["meaning"]["phase"] == "contracts"
            ), client.current
            assert (
                fixture_judgment_example(retry["judgment"])["responseExample"]["meaning"]["evidenceBinding"]
                != fixture_judgment_example(first["judgment"])["responseExample"]["meaning"]["evidenceBinding"]
            )
            # Recovery must deliver the judge's original meaning and binding to
            # the successor worker. Retry counts alone miss a repair packet that
            # loses the concrete gap and asks the worker to repeat unchanged work.
            with open_plan_store_readonly(tmp_path) as store:
                _, successor = current_job_run(store, required=True)
                request = Path(successor["beads"][fixture_id(tmp_path, "C") ]["evidenceDir"]) / "attempt/request.json"
                delivered = json.loads(request.read_text("utf-8"))["prompt"]
                assert all(
                    value in delivered
                    for value in (
                        "Receipt startup fails before checking the expected binary behavior",
                        first["judgment"]["id"],
                        fixture_judgment_example(first["judgment"])["responseExample"]["meaning"]["evidenceBinding"],
                    )
                ), delivered
            first = retry
        if surface == "cli":
            # C18/C23: kill after the real continuation outcome, before its cursor
            # settles. Raw semantic reply recovery never reached this later gap.
            (tmp_path / "hold-continuation").touch()
            client.send_answer(judge_reply(client.current["question"]))
            client.progress()
            marker = tmp_path / ".codex-harness/continuation-complete.json"
            completed = json.loads(marker.read_text())
            before_attempts = sorted(tmp_path.glob(".codex-harness/worker-jobs/**/attempt/request.json"))
            import signal
            os.kill(completed["ownerPid"], signal.SIGTERM)
            (tmp_path / "hold-continuation").unlink()
            client.reconnect()
            client.call("harness_plan_start", name="Consume completed continuation", idea="Retain the exact terminal result")
            assert not client.current.get("semanticHeld"), client.current
            assert client.current.get("effect", {}).get("id") != completed["effectId"], client.current
            # The existing next-phase assertions below prove this result was
            # consumed; exactly one new build attempt may follow Contracts.
            after_attempts = sorted(tmp_path.glob(".codex-harness/worker-jobs/**/attempt/request.json"))
            assert set(before_attempts) <= set(after_attempts) and len(after_attempts) == len(before_attempts) + 1
        else:
            client.answer(judge_reply(client.current["question"]))
        assert execution(client.current), client.current
        second = client.current["question"]
        execution_questions.append(second["requestId"])
        assert fixture_judgment_example(second["judgment"])["responseExample"]["meaning"]["phase"] == "validation", (
            json.dumps({label: dict(requestId=q["requestId"], judgmentId=q["judgment"]["id"],
                evidenceBinding=fixture_judgment_example(q["judgment"])["responseExample"]["meaning"]["evidenceBinding"])
                for label, q in (("answered", first), ("current", second))}, indent=2)
        )
        assert (
            fixture_judgment_example(second["judgment"])["responseExample"]["meaning"]["evidenceBinding"]
            != fixture_judgment_example(first["judgment"])["responseExample"]["meaning"]["evidenceBinding"]
        )
        assert second["candidate"] != first["candidate"]
        with open_plan_store_readonly(tmp_path) as store:
            observed_job = client.current["jobId"]
            from codex_harness.core.planner_candidate import current_job_run

            _, observed_run = current_job_run(store, required=True)
            segments = jobs._directory(tmp_path, observed_job) / "segments"
            contracts_segments = {p.parent.name for p in segments.glob("*/request.json") if json.loads(p.read_text())["resumeRun"] is None}
            assert len(contracts_segments) == 1, contracts_segments
            expected_segments = contracts_segments | {jobs.latest_segment(store, observed_job).name}
            assert len(expected_segments) == 2, expected_segments
            assert_job_segments(store, observed_job, expected_segments)
            assert observed_run["beads"][fixture_id(tmp_path, "B") ]["attempt"] is not None
            assert observed_run["status"] == "pending-judgment"
            assert observed_run["semanticContracts"]["state"] == "RED"
            assert observed_run["semanticValidation"]["state"] == "pending-judgment"
            if surface == "web":
                assert any(
                    action.get("checks", {}).get("textContrastAA", False) is None
                    for row in observed_run["semanticValidation"]["results"]
                    for action in row["actions"].values()
                )
            assert (
                observed_run["semanticContracts"]["evidenceBinding"]
                == fixture_judgment_example(first["judgment"])["responseExample"]["meaning"]["evidenceBinding"]
            )
            assert (
                observed_run["semanticValidation"]["evidenceBinding"]
                == fixture_judgment_example(second["judgment"])["responseExample"]["meaning"]["evidenceBinding"]
            )
        if surface == "cli":
            (tmp_path / "hold-continuation").touch()
            client.send_answer(judge_reply(second))
            client.progress()
            # C26/I6: validation alone grants no completed delivery. The old
            # observer indexed delivery here while the ZIP did not yet exist.
            pending = jobs.worker_job_status(tmp_path, client.current["jobId"])
            assert pending["status"] == "pending" and not pending["run"].get("delivery"), pending
            (tmp_path / ".codex-harness/delivery-pending.release").touch()
            client.progress()
            # C26/I6: files, ZIP and selection exist, but the owner has not
            # published its outcome. Earlier coverage only read worker status.
            with open_plan_store_readonly(tmp_path) as store:
                selected, _ = current_job_run(store)
                assert not selected["complete"], selected
            (tmp_path / ".codex-harness/candidate-pending.release").touch()
            client.progress()
            completed = json.loads((tmp_path / ".codex-harness/delivery-complete.json").read_text())
            delivered = jobs.worker_job_status(tmp_path, client.current["jobId"])
            assert delivered["status"] == "completed" and Path(delivered["run"]["delivery"]["archive"]).is_file(), delivered
            terminal = delivered["terminal"]["continuation"]
            assert terminal["effectId"] == completed["effectId"] and terminal["result"]["state"] == "PASS", terminal
            attempts = sorted(tmp_path.glob(".codex-harness/worker-jobs/**/attempt/request.json"))
            os.kill(completed["ownerPid"], signal.SIGTERM)
            (tmp_path / "hold-continuation").unlink()
            client.reconnect()
            client.call("harness_plan_start", name="Consume delivered continuation", idea="Continue from the completed product")
            assert client.current["question"]["family"] == "direction" and "Integrated GREEN" in client.current["question"]["purpose"], client.current
            assert jobs.worker_job_status(tmp_path, client.current["jobId"])["terminal"]["continuation"] == terminal
            assert sorted(tmp_path.glob(".codex-harness/worker-jobs/**/attempt/request.json")) == attempts
        else:
            client.answer(judge_reply(second))
        advance_meaning(client, content)
        assert client.current["state"] == "DONE"
        status = client.call("harness_plan_status")
        assert status["acceptance"]["accepted"], status
        with open_plan_store_readonly(tmp_path) as store:
            from codex_harness.core.planner_candidate import current_job_run

            selected, run = current_job_run(store, required=True)
            from codex_harness.core.acceptance_runs import acceptance_inputs

            # Brief Front door deletion: consume the actual canonical quality
            # judgment rather than counting removed public answer-tool traffic.
            quality_answers = {row["id"]:row for row in approvals.records(store,"judgment-recorded")
                if row["verdict"] == "positive" and any(ref["kind"] == "candidate-observation" and ref["payload"]["check"] == "visualQuality" for ref in row["sourceRefs"])}
            assert len(quality_answers) == 1
            consumed = acceptance_inputs(store, tmp_path, subject="product")["judgments"][
                "visualQuality"
            ]
            assert (
                consumed["id"] in quality_answers and consumed["positiveCredit"]
            ), consumed
            red, green = run["semanticContracts"], run["semanticValidation"]
            assert (
                run["isMocked"]
                and not run["didExecuteRealProvider"]
                and not run["isLiveProductProof"]
            )
            assert not run["claims"]
            assert run["status"] == "source-test-only (mocked provider)"
            assert red["state"] == "RED" and not red["passed"]
            assert run["beads"][fixture_id(tmp_path, "C") ]["lessonsPromoted"] == []
            assert not next(row for row in run["requirementValidation"] if row["bead"] == fixture_id(tmp_path, "C"))[
                "passed"
            ]
            assert green["state"] == "PASS" and green["passed"] and green["linkedRed"]
            assert red["scenarioBinding"] == green["scenarioBinding"]
            assert red["candidateTree"] != green["candidateTree"]
            assert all(
                row["cleanup"]["quiescenceVerified"]
                for proof in (red, green)
                for row in proof["results"]
            )
            assert run["semanticValidation"]["red"]["evidenceBinding"] == red["evidenceBinding"]
            assert next(row for row in run["requirementValidation"] if row["bead"] == fixture_id(tmp_path, "B"))["passed"]
            assert not red["passed"] and green["judgmentId"]
            saved = json.loads(Path(selected["runPath"]).read_text("utf-8"))
            assert saved["semanticContracts"] == red and saved["semanticValidation"] == green
            assert status["acceptance"]["accepted"]
            assert {row["id"] for row in status["acceptance"]["scenarios"]} == {
                fixture_id(tmp_path,row["id"]) for row in content["nodes"][0]["fields"]["scenarios"]["scenarios"]
            }
            assert all(row["lastResult"] == "pass" for row in status["acceptance"]["scenarios"])
            assert run["delivery"]["root"] == str(tmp_path.resolve())
            import zipfile

            with zipfile.ZipFile(run["delivery"]["archive"]) as archive:
                assert {
                    name: hashlib.sha256(archive.read(name)).hexdigest()
                    for name in archive.namelist()
                } == {
                    name: digest
                    for name, digest in selected["productHashes"].items()
                    if digest is not None
                }
            if surface == "web":
                web_evidence_golden(tmp_path, run, content["nodes"][0]["fields"])
            else:
                preview_environment(tmp_path)
            assert run["semanticValidation"]["linkedRed"]
            assert selected["jobId"] == observed_job
            assert_job_segments(store, selected["jobId"], expected_segments)
            assert {key for key,bead in run["beads"].items() if bead.get("attempt")} == {fixture_id(tmp_path, "C"), fixture_id(tmp_path, "B")}
            assert (
                len(store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchall()) == 1
            )
            assert {r["subject"]["kind"] for r in approvals.records(store, approvals.EVENT)} == {
                "A",
                "B",
            }
            assert all(
                r["channel"] == "chat-confirmation"
                for r in approvals.records(store, approvals.EVENT)
            )
            if surface == "cli":
                assert subprocess.check_output(
                    [sys.executable, "-B", str(tmp_path / "hashslip.py"), "--help"],
                    cwd=tmp_path,
                    timeout=10,
                ).startswith(b"usage:")
                completed = subprocess.run(
                    [sys.executable, "-B", str(tmp_path / "hashslip.py")],
                    input=b"\x00\xffA",
                    capture_output=True,
                )
                assert completed.returncode == 0 and json.loads(completed.stdout)["bytes"] == 3
                assert not completed.stderr
        # Every charter phase is reported, including phases fully internalized
        # by the controller. Reads/reconnects and judge answers count as planner
        # traffic; adversarial helpers own separate clients and ledgers.
        from tests.python.core.mcp_planner_support import CHARTER_PHASES, CHOREOGRAPHY_TOOLS

        baseline = {phase: len(RETAINED_PHASE_CALLS[phase]) for phase in CHARTER_PHASES}
        assert set(client.calls) == set(baseline)
        assert all(client.calls[phase] < baseline[phase] for phase in CHARTER_PHASES), dict(
            actual=dict(client.calls), retainedSourceLowerBound=baseline
        )
        choreography = {
            name: count for name, count in client.tool_names.items() if name in CHOREOGRAPHY_TOOLS
        }
        assert not choreography, choreography
        assert not client.errors, client.errors
        # Brief Front door deletion: public answer-ID traffic is replaced by
        # the bound raw completion/restart assertions and distinct questions above.
        assert client.tool_names["harness_chat"] >= 4
        assert not {"harness_loop_answer","harness_plan_start","harness_steer"} & client.tool_names.keys()
        assert client.decision_gates == [
            "align+vision-lock",
            "plan-execution",
            "C",
        ]
        assert (
            len(execution_questions)
            == len(set(execution_questions))
            == (3 if surface == "cli" else 2)
        )
        print(
            f"golden {surface}: A->review->B->Contracts exit->bound answer->Build exit->Validation->delivery->C; all planner MCP calls by CHARTER phase={dict(client.calls)}; retained ab8b85a source-required lower bound={baseline}; judge submissions (included)={dict(client.judge_calls)}; observed choreography/poll tool names={choreography}; seconds={time.monotonic() - started:.2f}"
        )
        client.log.write(
            "plannerCallsByCharterPhase="
            + json.dumps(dict(client.calls), sort_keys=True)
            + "; judgeSubmissionsIncluded="
            + json.dumps(dict(client.judge_calls), sort_keys=True)
            + "; observedTools="
            + json.dumps(dict(client.tool_names), sort_keys=True)
            + "\n"
        )
        client.log.flush()
        if surface == "cli":
            admission_interruption(tmp_path.parent / (tmp_path.name + "-admission-death"))
            admission_interruption(tmp_path.parent / (tmp_path.name + "-root-admission-death"), root=True)
            admission_interruption(tmp_path.parent / (tmp_path.name + "-root-published-death"), boundary="root-published")
            admission_interruption(tmp_path.parent / (tmp_path.name + "-popen-death"), boundary="popen")
            admission_interruption(tmp_path.parent / (tmp_path.name + "-terminal-death"), boundary="terminal")
            controller_interruptions(tmp_path.parent / (tmp_path.name + "-interruptions-1"), 1)
            denial_interruption(tmp_path.parent / (tmp_path.name + "-denial-interruption"))
            dead_review_wake(tmp_path.parent / (tmp_path.name + "-wake"))
        all_calls = {
            phase: sum(other.calls[phase] for other in LoopClient.instances[ledger_start:])
            for phase in CHARTER_PHASES
        }
        all_judges = {
            phase: sum(other.judge_calls[phase] for other in LoopClient.instances[ledger_start:])
            for phase in CHARTER_PHASES
        }
        print(
            f"golden {surface} observed case tool traffic (happy + restored authoring + retry/fault/control journeys): all planner MCP calls by CHARTER phase={all_calls}; judge submissions (included)={all_judges}"
        )
        cockpit_golden(tmp_path, selected["runPath"], status, surface)
        if surface == "cli":
            cold_start_golden(tmp_path.parent / (tmp_path.name + "-cold-start"))
    finally:
        client.close()


if __name__ == "__main__":
    if sys.argv[1] == "denial-twin":
        denial_interruption(Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) > 3 else None)
    elif sys.argv[1] == "controller-death-twin":
        controller_launch_interruption(Path(sys.argv[2]), bool(int(sys.argv[3])),
                                       stale=sys.argv[4] if len(sys.argv) > 4 else False)
    elif sys.argv[1] == "recovery-twin":
        controller_interruptions(Path(sys.argv[2]), int(sys.argv[3]))
    elif sys.argv[1] == "http-golden":
        from tests.python.core.acceptance_support import http_golden

        http_golden(Path(sys.argv[2]), semantic=bool(int(sys.argv[3])))
    else:
        provider_host(*sys.argv[1:])
