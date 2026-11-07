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

import pytest

from codex_harness.core import approvals
from codex_harness.core import planner_confirmations as confirmations
from codex_harness.core.run_content import apply_content

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.fast


def scripted_review_reply(launch, findings, *, verdict, rationale, gaps=()):
    """Script only a provider reply to the actual bound review packet."""
    request = json.loads(
        launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split("\nReturn ONLY JSON", 1)[0]
    )
    # R9 regression: prior fixtures invented a valid depth, hiding absent packet guidance.
    reply = request["responseExample"]
    reply.update(verdict=verdict, rationale=rationale, gaps=list(gaps))
    reply["meaning"]["findings"] = findings
    return json.dumps(reply)


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

    # Migration twin: keep the actual native review original from a history
    # predating reservations. Fresh reviews must still use ordinary admission.
    historical = credit()[0]
    with store._transaction():
        store.connection.execute("DELETE FROM review_attempts WHERE id=?", (historical["id"],))

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
            assert len(expected) == 1, "An older reservation or legacy row overrode the latest pass"
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
        staged.write_text(json.dumps(details), encoding="utf-8")
        staged.replace(marker)
        deadline = time.monotonic() + 60
        release = marker.with_suffix(".release")
        while not release.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert release.exists(), (
            "Receipt-boundary fixture did not receive owned MCP teardown: " + name
        )

    if mode == "server" and (workspace / "semantic-retry-script.json").exists():
        from codex_harness.core import run_invocations

        observed_invoke = run_invocations.invoke

        def hold_semantic_reply(store, selected, pending, timeout, binding):
            raw = observed_invoke(store, selected, pending, timeout, binding)
            if pending["purpose"] == "understanding" and pending["judgment"]["fixesIssued"] == 2:
                hold_receipt_once(
                    "semantic-raw-complete",
                    dict(
                        requestId=pending["requestId"],
                        binding=pending["binding"],
                        effectId=binding["effectId"],
                        raw=raw,
                    ),
                )
            if pending["purpose"] == "trace" and (workspace / "semantic-late-output").exists():
                hold_receipt_once(
                    "semantic-late-complete",
                    dict(
                        requestId=pending["requestId"],
                        questionBinding=pending["binding"],
                        binding=binding,
                        effectId=binding["effectId"],
                    ),
                )
            return raw

        run_invocations.invoke = hold_semantic_reply

    if mode == "server" and receipt_boundaries:
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

    def publish(path, record):
        actual_write(path, record)
        if surface == "web" and path.name == "retained-candidate.json" and record["bead"] == "B":
            # Hold the real producer after publication so MCP must consume this
            # transition, rather than usually missing it between status polls.
            end = time.monotonic() + 10
            while not (workspace / "validation-observed").exists() and time.monotonic() < end:
                time.sleep(0.05)

    dispatch._write_record = publish

    def command(role, project, job=None, segment=None):
        return [sys.executable, "-B", str(Path(__file__).resolve()), role, project, surface] + (
            [job, segment] if job else []
        )

    def launch(argv, **options):
        if "codex_harness.core.planner_jobs" in argv:
            argv = command(
                "controller",
                argv[argv.index("--workspace") + 1],
                argv[argv.index("--job-id") + 1],
                argv[argv.index("--segment") + 1],
            )
        return actual_popen(argv, **options)

    def controller_payload(argv, **options):
        return actual_audit(command("worker", str(workspace), job_id, segment_id), **options)

    def write_shelflife(root, phase):
        payload = json.loads((workspace / "scripted-provider.json").read_text(encoding="utf-8"))
        scripted_worker(packet(root, "C" if phase == "contracts" else "B"), root, payload[phase])

    def packet(root, bead):
        record = json.loads((root.parent / "evidence/run.json").read_text(encoding="utf-8"))
        request = Path(record["beads"][bead]["evidenceDir"]) / "attempt/request.json"
        return json.loads(request.read_text(encoding="utf-8"))["prompt"]

    def contracts(root):
        if surface == "shelflife":
            return write_shelflife(root, "contracts")
        (workspace / "contracts-entered").touch()
        if (workspace / "fault-contracts").exists():
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

    if surface in {"shelflife", "parallel-http"}:
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
        if surface in {"parallel-http", "shelflife"}:
            return actual_run(plan, **options)
        from codex_harness.core import graph

        selected = graph.load_plan(Path(plan)).beads
        workers = {"C": contracts, "B": build}
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
        return protocol(launch.cli, text=text, model=model, effort=effort)

    def review(launch, *args, **options):
        if receipt_boundaries:
            with (workspace / ".codex-harness/scripted-review-invocations.jsonl").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(
                    json.dumps(
                        dict(
                            acceptance="## Read-only candidate evidence" in launch.stdin_text,
                            packetSha256=hashlib.sha256(launch.stdin_text.encode()).hexdigest(),
                        )
                    )
                    + "\n"
                )
        if "\n## Run question\n" in launch.stdin_text:
            pending = json.loads(launch.stdin_text.split("\n## Run question\n", 1)[1])
            if pending["family"] == "author":
                value = dict(
                    option="ready",
                    rationale="Scripted planner authors the retained dream",
                    content=plan_content(surface) if pending["purpose"] == "understanding" else {},
                )
            elif pending["family"] == "direction":
                value = dict(
                    option="continue",
                    rationale="Scripted planner resyncs the integrated result",
                    content={},
                )
            else:
                value = judge_reply(pending)
            retry_script = workspace / "semantic-retry-script.json"
            raw = json.dumps(value)
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
                elif pending["family"] == "direction":
                    raw = json.dumps(
                        dict(
                            option="pause",
                            rationale="Malformed semantic output exhausted the canonical two fixes; retain it for the user",
                            content={},
                        )
                    )
                with history_path.open("a", encoding="utf-8") as history_file:
                    history_file.write(
                        json.dumps(
                            dict(
                                requestId=pending["requestId"],
                                binding=pending["binding"],
                                role=pending["role"],
                                purpose=pending["purpose"],
                                fixesIssued=pending["judgment"]["fixesIssued"],
                                raw=raw,
                            )
                        )
                        + "\n"
                    )
            from dataclasses import replace

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
            if pending["purpose"] == "trace" and (workspace / "semantic-late-output").exists():
                # FIX17: lease structure is kickoff-owned. Fault the existing
                # clock after real zero exit/cleanup; never supply a proof result.
                from datetime import UTC
                from codex_harness.core import run_invocations, run_ownership
                run_ownership.datetime = AwakeClock
                (workspace / "clock-shift").write_text(str(float(args[1]) + 1))
                run_invocations._now = lambda: AwakeClock.now(UTC).isoformat()
            return actual_return
        if mode == "server" and surface == "cli" and (workspace / "inject-review-crash").exists():
            # A real provider wait survives a lost client until its process owner
            # is killed. Recovery must not reuse that abandoned review's evidence.
            (workspace / ".codex-harness" / "review-crash-started").write_text(
                "started", encoding="utf-8"
            )
            actual_audit(
                [sys.executable, "-B", "-c", "import time; time.sleep(600)"],
                cwd=workspace,
                timeout=900,
            )
        if "## Read-only candidate evidence" in launch.stdin_text:
            from codex_harness.core.plan_store import open_plan_store_readonly

            with open_plan_store_readonly(workspace) as store:
                tier = approvals.current(store, "B")["subject"]["reviewTier"]
                assert f"## Review tier: {tier}" in launch.stdin_text
                assert "catalog: " + approvals.catalog_status(store) in launch.stdin_text
        text = "[]"
        if mode == "server" and surface == "cli" and (workspace / "inject-review-note").exists():
            (workspace / "review-output-sent").write_text("sent")
            payload = json.loads((ROOT / "tests/fixtures/efficiency/run26.json").read_text("utf-8"))
            text = json.dumps([payload["headingFinding"]])
        if receipt_boundaries and "## Read-only candidate evidence" in launch.stdin_text:
            request = json.loads(
                launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split(
                    "\nReturn ONLY JSON", 1
                )[0]
            )
            facts = next(ref["payload"]["facts"] for ref in request["sourceRefs"])
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
        response = provider_reply(
            launch,
            scripted_review_reply(
                launch,
                json.loads(text),
                verdict="positive",
                rationale="Independent plan assessment: required behaviors are covered; trace note is advisory",
            ),
        )

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

        prompt = sys.stdin.read()
        phase = "contracts" if prompt.startswith("semantic-v1 contracts;") else "build"
        payload = provider_payload()
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
        else:
            sources = payload[phase]
        live = workspace / ".codex-harness/live-provider.json"
        slow_flush = surface == "shelflife" and phase == "contracts" and not live.exists()
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
        judgment["responseExample"],
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
        for purpose in (
            "plan-review",
            "finding-equivalence",
            "finding-disposition",
            "review-reuse",
            "plan-convergence",
            "vision-intent",
            "planning-size",
            "user-decision",
        ):
            packet = store.prepare_judgment(**dict(request, question=purpose))
            example = packet["responseExample"]
            reply = scripted_judgment(store, packet, response=json.dumps(example))
            assert reply["current"]["fixesIssued"] == 0 and (not reply["current"]["positiveCredit"])
            assert reply["current"]["meaning"] == example["meaning"]
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
        for question, meaning in (
            (
                "Does the revised plan address the readback gap?",
                dict(reviewVerdict="changes-requested", rationale="Second channel is still absent"),
            ),
            (
                "Are the findings equivalent and may this revision reuse the old review?",
                dict(equivalent=False, reuse=False, memberDispositions={"channel-gap": "open"}),
            ),
            (
                "What should the loop do after this observed failure?",
                dict(action="repair", reason="Readback has an observed missing channel"),
            ),
            (
                "Does this candidate meet the accepted dream and quality bar?",
                dict(complete=False, noStubMockGaps=True, qualityBarMet=False),
            ),
        ):
            result = scripted_judgment(
                store,
                question=question,
                subject_ids=["R1"],
                evidence_ids=["E1"],
                source_event_ids=[event_id],
                meaning=meaning,
                verdict="negative",
                gaps=["Second-channel readback is absent"],
            )
            reload()
            retained = read(result["current"], candidate="sensor-candidate-1")
            assert retained["concreteGap"] and (not retained["positiveCredit"])
            assert retained["meaning"] == meaning and retained["question"] == question
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


def web_evidence_golden(workspace, retained, vision):
    """C26: retain real browser/API/persistence semantics and standalone preview cleanup.

    The short static-page journey missed negative-response attribution, truncated
    bodies, prerequisite data, redirects and orphaned headless preview children.
    """
    from codex_harness.core.semantic_runner import run as observe

    scenario = vision["scenarios"]["scenarios"][0]
    journey = scenario["actions"][2]["web"]
    proof = retained["semanticValidation"]["results"]
    actions = [a for r in proof for a in r["actions"].values() if a.get("browserMode")]
    assert actions and all(a["browserMode"] == "normal" for a in actions)
    served = [a for a in actions if a.get("server")]
    assert len(served) == 1, actions
    api = proof[0]["actions"]["api"]
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
        ("formpost", 302, None),
        ("overconsume", 409, "{}"),
        ("delete", 204, ""),
        ("empty", 204, ""),
        ("cached", 304, ""),
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
        r["step"] == "empty" and r.get("transportDiagnostic") for r in served[0]["responses"]
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
    prompt = retained["beads"]["B"]["prompt"]
    commands = [c for c in prompt.split("`") if "--preview " in c]
    assert any("--preview ." in c for c in commands), prompt
    command = next(c for c in commands if "--preview SCENARIOS.json 'served'" in c)
    # Static previews never started a server: its log must not become
    # an unowned worker change, and its protected binding lives here.
    worker_root = Path(retained["beads"]["B"]["attempt"]["cwd"])
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
        assert set(completion["actions"]) == {a["id"] for a in scenario["actions"]}, completion
        assert completion["actions"]["seed"]["http"][0]["request"]["json"] == dict(note="Seeded")
        assert completion["actions"]["seed-check"]["stdout"].replace("\r\n", "\n") == "Seeded\n"
        assert all(o["passed"] is not False for o in completion["observations"]), completion
        observed = completion["actions"]["a1"]
        assert len(observed["steps"]) == len(journey["steps"]), completion
        assert observed["checks"]["consoleErrors"] == 0
        # Named previews previously opened a desktop page and erased
        # screenshots; check retained phone pixels and actual flow.
        import struct

        for artifact in observed["artifacts"]:
            png = Path(artifact["path"])
            assert png.is_relative_to(worker_root / ".codex-harness/evidence/preview")
            data = png.read_bytes()
            assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
            assert struct.unpack(">I", data[16:20])[0] == 375
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


def planning_surface_golden(workspace):
    """C4/C9/C14: current public schemas produce persisted semantic content.

    Captured Unicode, malformed shape and unvalidated intent expose broken
    authoring contracts and pipe death; valid retained plans miss those seams.
    """
    from copy import deepcopy

    from tests.python.core.mcp_planner_support import LoopClient

    client = LoopClient(workspace)
    try:
        inventory = {tool["name"] for tool in client.rpc("tools/list", {})["tools"]}
        read_only = {
            "harness_judgment",
            "harness_schema",
            "harness_preflight",
            "harness_waves",
            "harness_review",
            "harness_review_policy",
            "harness_packet_view",
            "harness_show_node",
            "harness_nodes",
            "harness_mail_inbox",
            "harness_plan_status",
            "harness_diagram",
            "harness_impact",
        }
        assert read_only <= inventory
        assert inventory - read_only == {
            "harness_plan_start",
            "harness_loop_answer",
            "harness_steer",
        }
        failed = client.rpc(
            "tools/call",
            dict(name=["harness_loop_answer"], arguments=dict(expected_workspace=str(workspace))),
        )
        assert failed["isError"] and "unconfirmed" in failed["content"][0]["text"]
        assert client.rpc("ping", {}) == {}
        client.call(
            "harness_plan_start",
            name="Public semantic authoring",
            idea="Retain unfamiliar product intent",
        )
        before = client.call("harness_show_node", id="V1")
        # FIX17: meaningful omissions name their fields at the outstanding question;
        # code-owned policy inspection creates neither a selector nor another gate.
        pending_id = client.current["question"]["requestId"]
        policy = client.call("harness_review_policy")
        assert not policy["policy"]["active"]
        assert client.call("harness_plan_status")["run"]["question"]["requestId"] == pending_id
        client.answer(dict(option="ready", rationale="Captured premature r17 readiness", content={}))
        correction = client.current["question"]["judgment"]
        assert correction["disposition"] == "exact-fix" and not correction["positiveCredit"]
        assert "vision.fields" in correction["error"] and "expansion.mode" in correction["error"]
        # Missing keys in a meaning writer used to escape 1j as operation-error;
        # invalid node-schema tests do not exercise the clarification-answer writer.
        client.answer(dict(option="draft", rationale="Return to retained meaning", content={}))
        client.answer(dict(option="draft", rationale="Captured missing clarification identity",
                           content=dict(answers=[dict(answer="Retain the actual user meaning")])) )
        correction = client.current["question"]["judgment"]
        assert correction["disposition"] == "exact-fix" and "content.id: required field" in correction["error"]
        assert client.call("harness_show_node", id="V1")["fields"] == before["fields"]
        client.answer(dict(option="draft", rationale="Continue from intact canonical meaning", content={}))
        schema_nodes = {}
        for kind in ("vision", "test", "bead"):
            guide = client.call("harness_schema", tool="harness_loop_answer", kind=kind)
            value = deepcopy(guide["example"])
            value["content"]["nodes"] = [*guide["prerequisiteNodes"], guide["node"]]
            client.answer(value)
            example = guide["node"]
            schema_nodes[kind] = example
            saved = client.call("harness_show_node", id=example["id"])
            assert saved["fields"] == example["fields"]
        # Failure retains canonical bytes and teaches the actual broken field.
        # A subsequent valid draft obtains a new semantic question; no caller
        # owns a hidden retry counter or a compatibility writer route.
        for field, value, path, allowed in (
            ("scenarios", [], "vision.fields.scenarios", "allowed keys"),
            (
                "setup",
                {"database": "guess"},
                "vision.fields.scenarios.scenarios[0].setup",
                "snapshots",
            ),
            ("verdict", "any", "vision.fields.scenarios.scenarios[0]", "verdict"),
            (
                "execution",
                {"argv": ["python"], "rationale": "check"},
                "T1.fields.execution",
                "testRoots",
            ),
        ):
            malformed = deepcopy(schema_nodes["test" if field == "execution" else "vision"])
            target = (
                malformed["fields"]
                if field in {"scenarios", "execution"}
                else malformed["fields"]["scenarios"]["scenarios"][0]
            )
            target[field] = value
            retained = client.call("harness_show_node", id=malformed["id"])["fields"]
            client.answer(
                dict(
                    option="draft",
                    rationale="Submit the captured malformed shape",
                    content=dict(nodes=[malformed]),
                )
            )
            correction = client.current["question"]["judgment"]
            assert correction["disposition"] == "exact-fix" and not correction["positiveCredit"]
            assert all(word in correction["error"] for word in (path, allowed)), correction
            assert client.call("harness_show_node", id=malformed["id"])["fields"] == retained
            client.answer(
                dict(option="draft", rationale="Retain valid canonical content", content={})
            )
        for fixture in ("ux5-turn7-upsert.json", "f3-turn11-vision.json"):
            captured = json.loads((ROOT / "tests/fixtures" / fixture).read_text("utf-8"))
            node = captured["node"] if "node" in captured else captured
            node["fields"].pop("setup", None)  # FIX17: kickoff owns project setup.
            client.answer(
                dict(
                    option="draft",
                    rationale="Retain captured full Unicode intent",
                    content=dict(nodes=[node]),
                )
            )
            assert client.call("harness_show_node", id="V1")["fields"] == node["fields"]
            if fixture.startswith("ux5"):
                unvalidated = client.call("harness_plan_status")["acceptance"]["scenarios"]
                assert any(
                    row.get("evidenceTier") == "scenario: unvalidated"
                    and "value" in row["validationMessage"]
                    and not row["executable"]
                    for row in unvalidated
                ), unvalidated
        client.answer(
            dict(
                option="draft",
                rationale="Restore initial authoring subject",
                content=dict(
                    nodes=[
                        {
                            key: before[key]
                            for key in ("id", "kind", "title", "text", "parents", "fields")
                        }
                    ],
                    remove=["B1", "T1", "R1", "F1"],
                ),
            )
        )
        unknown = client.call("harness_schema", tool="semanticScenarios", error=True)
        assert all(
            name in str(unknown)
            for name in ("harness_plan_start", "harness_loop_answer", "harness_steer")
        ), unknown
        # FIX17 deletes prospective reviewer/worker inputs: preflight reads the baton.
        preflight = client.call("harness_preflight")
        issues = "; ".join(preflight["planningPrerequisites"])
        assert issues == client.current["reason"] and not preflight["didRunProvider"]
        assert "reviewer-catalog" not in issues and preflight["catalog"] == "unverified"
        assert all(row["executable"]["status"] == "available" for row in preflight["providers"] if row["provider"] in {"codex", "claude"})
    finally:
        client.close()


def reviewer_order_golden(workspace):
    """C1/C2/C8/C11/C23: the actual pre-FIX17 producer's r17 order resumes to B.

    Fresh/preselected fixtures miss old cursors without kickoff fields, the
    retired tuple question and planner project-setup overwrite. Run both real
    stdio versions, then consume the persisted old cursor in the current loop.
    """
    import io
    import tarfile
    from tests.python.core.mcp_planner_support import LoopClient
    from codex_harness.core.audit_process import run_audit_process

    workspace.mkdir()
    operator = workspace / "operator"
    bad_roles = [dict(name="reviewer", provider="codex", model="gpt-6.1-sol", effort="medium")]
    command = [sys.executable, "-B", "-m", "codex_harness.core.plan_cli", "--workspace", str(operator),
               "start", "--name", "Operator setup", "--idea", "Retain the exact brief", "--role-tuples"]
    invalid = run_audit_process([*command, json.dumps(bad_roles)], cwd=ROOT, timeout=20)
    assert invalid.returncode == 2 and "setup.roleTuples" in invalid.stdout
    assert invalid.audit_control["quiescenceVerified"]
    (operator / ".codex-harness/setup.json").write_text(json.dumps(dict(project=dict(gitName="Operator profile"))), encoding="utf-8")
    repaired = run_audit_process([*command, "[]"], cwd=ROOT, timeout=20)
    assert repaired.returncode == 0 and repaired.audit_control["quiescenceVerified"]
    assert json.loads(repaired.stdout)["question"]["purpose"] == "understanding"
    configured = LoopClient(operator, kickoff={})
    try:
        configured.call("harness_plan_status")
        shown = configured.call("harness_show_node", id="V1")
        vision = {k: shown[k] for k in ("id", "kind", "title", "text", "parents", "fields")}
        vision["fields"]["setup"] = dict(gitName="Planner overwrite")
        configured.answer(dict(option="draft", rationale="Meaning cannot replace operator setup", content=dict(nodes=[vision])))
        assert configured.call("harness_show_node", id="V1")["fields"]["setup"] == dict(gitName="Operator profile")
    finally:
        configured.close()

    pinned = dict(role="planner", name="pinned-planner", provider="claude", model="claude-opus-4-6", effort="high")
    defaulted = LoopClient(workspace / "role-defaults", kickoff=dict(roleTuples=[pinned]))
    try:
        defaulted.call("harness_plan_start", name="Pinned planner tier", idea="Retain the operator's model tier")
        selected = defaulted.current["setup"]
        assert {r["role"] for r in selected["roleTuples"]} == {"planner", "judge"}
        assert all(all(r[k] == pinned[k] for k in ("provider", "model", "effort"))
                   for r in [*selected["roleTuples"], *selected["reviewers"]])
        assert defaulted.current["userRequest"]["gate"] == "invocations"
    finally:
        defaulted.close()

    # Real canonical base producer, not a hand-written cursor or a copied proof.
    base = workspace / "base"
    archived = subprocess.check_output(["git", "archive", "754be3b88041dabcce29cfff07dd888010744fc7", "src"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archived)) as snapshot:
        snapshot.extractall(base, filter="data")
    # P3: genuine old semantic questions retain their identity/binding and accept
    # meaning after adoption. The structural-wait twin below cannot prove this.
    semantic = workspace / "retained-meaning"
    old = LoopClient(semantic, kickoff={}, source_root=base)
    try:
        old.call("harness_plan_start", name="Retain semantic question", idea="Original understanding")
        held = old.current["question"]
        assert held["context"]
    finally:
        old.close()
    resumed = LoopClient(semantic, kickoff={})
    try:
        resumed.call("harness_plan_start", name="Reattach meaning", idea="Retain original binding")
        pending = resumed.current["question"]
        assert (pending["requestId"], pending["binding"], pending["revision"]) == (
            held["requestId"], held["binding"], held["revision"])
        node = resumed.call("harness_show_node", id="V1")
        node = {k: node[k] for k in ("id", "kind", "title", "text", "parents", "fields")}
        node["text"] = "Meaning accepted through the retained semantic question"
        resumed.answer(dict(option="draft", rationale="Retain semantic ownership across adoption", content=dict(nodes=[node])))
        assert resumed.call("harness_show_node", id="V1")["text"] == node["text"]
    finally:
        resumed.close()

    # C18/C23 fault twins: a second canonical writer wins just before provider
    # admission acquires its transaction. Ordinary serial reattachment misses
    # the cached-question restore that resurrected consumed requests on host.
    from contextlib import contextmanager
    from unittest.mock import patch
    from codex_harness.core import run_loop
    from codex_harness.core.plan_store import open_plan_store

    with open_plan_store(semantic) as owner, open_plan_store(semantic) as peer:
        draft = dict(option="draft", rationale="Retain meaning without changing scope", content={})
        for control in (False, True):
            before = run_loop.cursor(owner)["question"]
            run_loop._record_answer(owner, request_id=before["requestId"], answer=draft, source="golden:attached")
            outstanding = run_loop.cursor(owner)["question"]
            transaction = owner._transaction
            armed = True

            @contextmanager
            def admit_after_other_writer():
                nonlocal armed
                if armed:
                    armed = False
                    if control:
                        run_loop.steer(peer, direction="pause", rationale="User pauses before provider admission")
                    else:
                        run_loop._record_answer(peer, request_id=outstanding["requestId"], answer=draft, source="golden:winning-answer")
                with transaction():
                    yield

            with patch.object(owner, "_transaction", admit_after_other_writer):
                run_loop._automatic_question(owner)
            late = run_loop.answer(owner, request_id=outstanding["requestId"],
                answer=dict(option="stop", rationale="Late reply must never control the run", content={}), source="golden:late")
            assert late["answerOutcome"]["state"] == "superseded" and "re-ask" in late["answerOutcome"]["message"]
            if control:
                assert late["state"] == "PAUSED" and not late["question"] and not late["effect"]
                resumed_run = run_loop.steer(owner, direction="continue", rationale="User explicitly resumes", resume=True)
                assert resumed_run["question"]["requestId"] != outstanding["requestId"]
            else:
                assert late["state"] == "PLAN" and late["question"]["requestId"] != outstanding["requestId"]
                accepted = run_loop.answer(owner, request_id=late["question"]["requestId"], answer=draft, source="golden:fresh")
                assert accepted["state"] == "PLAN" and not accepted.get("answerOutcome")

        # A retained 1j response may outlive the cursor that advertised it. The
        # controller re-asks instead of submitting a second response or replaying
        # the retained meaning as new control (the host ShelfLife failure).
        pending = run_loop.cursor(owner)["question"]
        record = owner.judgment(pending["requestId"], candidate=pending["candidate"])
        owner.submit_judgment(judgment_id=record["id"], response=json.dumps(dict(
            judgmentId=record["id"], binding=record["binding"], verdict="positive",
            rationale="Retained completed meaning", meaning=draft, gaps=[])), source="golden:retained-producer")
        # H3: canonical completion is visible before any answer/continuation
        # repairs the cursor; serial reply-only twins missed stale presentation.
        from codex_harness.core.status_api import run_baton, plan_status
        assert run_baton(owner)["question"] is None
        assert plan_status(owner)["run"]["question"] is None
        assert "re-ask" in run_baton(owner)["nextAction"]
        refreshed = run_loop.answer(owner, request_id=pending["requestId"], answer=draft, source="golden:late-after-retention")
        assert refreshed["answerOutcome"]["state"] == "superseded"
        assert refreshed["state"] == "STEER" and refreshed["question"]["requestId"] != pending["requestId"]
        assert not refreshed["effect"]
        assert plan_status(owner)["run"]["question"]["requestId"] == refreshed["question"]["requestId"]
        # Changed evidence supersedes an advertised request by the same rule.
        pending = refreshed["question"]
        vision = owner.spine().node("V1")
        vision["text"] += " Retain newly recorded evidence."
        owner.upsert_node(vision)
        changed = run_loop.answer(owner, request_id=pending["requestId"], answer=draft, source="golden:old-evidence")
        assert changed["answerOutcome"]["state"] == "superseded" and changed["state"] == "STEER"
        assert changed["question"]["requestId"] != pending["requestId"] and not changed["effect"]

    project = workspace / "project"
    settings = dict(timeout=180, project=dict(gitName="Pinned operator"))
    client = LoopClient(project, kickoff=settings, source_root=base)
    content = plan_content()
    try:
        client.call("harness_plan_start", name="R17 structural gate deletion", idea=content["nodes"][0]["text"])
        client.answer(dict(option="draft", rationale="Retain the full product meaning", content=content))
        client.answer(dict(option="ready", rationale="Present expansion and understanding", content={}))
        client.answer(judge_reply(client.current["question"]))
        client.reconnect()
        client.words("yes")
        client.answer(judge_reply(client.current["question"]))
        assert client.current["question"]["purpose"] == "trace"
        client.answer(dict(option="ready", rationale="The recorded plan leads to the full dream", content={}))
        client.call("harness_schema", tool="harness_loop_answer")
        client.call("harness_preflight", providers=["codex"])
        flat = dict(provider="codex", model="gpt-6.1-sol", effort="medium")
        client.answer(dict(option="ready", rationale="Captured r17 worker arguments", content=dict(
            workers=[dict(id=b, **flat) for b in ("B", "C")])))
        assert client.current["question"]["purpose"] == "Select exact reviewers and their role tuples"
        client.answer(dict(option="ready", rationale="Captured r17 flat reviewer guess", content=dict(reviewers=flat)))
        assert "Expected decision node" in client.current["question"]["judgment"]["error"]
        guessed = dict(id="DR1", kind="decision", title="Exact ShelfLife review selection", text="Use the exact configured model", parents=["V1"],
            fields=dict(schema="dot-reviewer-selection-v1", catalogId=None, catalogDigest=None, confirmation=None,
            recommendation="One independent reviewer per stage is proportionate", assurance="Independence unverified",
            reviewers=[dict(name="reviewer", **flat)], stages=["plan", "acceptance"]))
        client.answer(dict(option="ready", rationale="Captured r17 decision guess", content=dict(reviewers=guessed)))
        assert "choiceId" in client.current["question"]["judgment"]["error"]
        catalog = dict(schema="dot-reviewer-catalog-v1", id="RC-d976ffab-c233-410c-b3a5-19c87182e0b2", observedAt="2026-11-07T12:57:58Z",
            providers=[dict(provider="codex", executable=str(project / "tools/codex.exe"), executableDigest=None, version=None,
            installed=True, authentication="unknown", discovery="unknown", source=None, reason="Requested tuple; access unverified",
            choices=[dict(model=flat["model"], effort=dict(kind="named", value="medium"), availability="offered", reviewPathSupported=True,
                          reason="Supplied requested offering; no provider execution claimed")])])
        client.answer(dict(option="draft", rationale="Captured r17 catalog draft", content=dict(catalog=catalog)))
        client.call("harness_loop_answer", request_id=client.current["question"]["requestId"], source="r17:captured-shape",
            answer=dict(option="ready", rationale="Captured r17 missing role", content=dict(roleTuples=[dict(name="reviewer", **flat)])), error=True)
        client.call("harness_plan_status")
        client.answer(dict(option="ready", rationale="Captured r17 role-only correction", content=dict(
            roleTuples=[dict(role="plan-review", **flat), dict(role="delivery-review", **flat)])))
        # Retain r17's actual repair/trace/selector/discovery detour as well.
        client.call("harness_steer", direction="repair", rationale="Retain the user's recorded A approval and exact gpt-6.1-sol medium choice. Reviewer-selection public contract is blocking: content.reviewers requires decision node with selection schema/catalogId/catalogDigest/reviewers including choiceId/stages/assurance/recommendation/confirmation, but harness_schema exposes only generic decision fields and content schema. Catalog was recorded through supported draft and preflight confirms recorded, without exposing normalized digest/choiceId. Exact roleTuples have been recorded, yet controller repeats Select exact reviewers. Prior malformed role tuple produced operation-error 'role' after storing it; corrected tuples were subsequently recorded. Needed owner repair: expose actionable reviewer selection and normalized catalog through public tools, then resume same run. Do not bypass gates, fabricate catalog identities/confirmation or launch product workers.")
        assert client.current["question"]["purpose"] == "trace"
        client.answer({'option': 'ready', 'rationale': 'Continue the same approved scope. The graph already links V1 through F1-F3/R1-R3 to T1-T3 and B1, with G1-G3 designs and AC1-AC3 integration acceptance. Exact worker and reviewer model choices remain gpt-6.1-sol medium.', 'content': {}})
        assert client.current["question"]["purpose"] == "Select exact reviewers and their role tuples"
        client.answer({'option': 'ready', 'rationale': 'Assign the exact model/effort to the plan and acceptance roles using stage names; preserve the existing single worker.', 'content': {'roleTuples': [{'role': 'plan', 'provider': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'medium'}, {'role': 'acceptance', 'provider': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'medium'}]}})
        client.call("harness_schema", kind="discovery", include_teaching=True)
        client.answer({'option': 'draft', 'rationale': 'Record the observed operator blocker without asserting an unproven root cause or bypassing the approved planning boundary.', 'content': {'nodes': [{'id': 'DISC1', 'kind': 'discovery', 'title': 'Reviewer selection repeats without actionable public guidance', 'text': 'Observed in the same ShelfLife run after recorded Align/Vision approval: ready answers with exact gpt-6.1-sol medium role tuples return another Select exact reviewers and their role tuples question, context empty and judgment error null. Original roles plan-review/delivery-review and follow-up plan/acceptance did not advance. content.reviewers expects a decision node and catalog-bound nested contract, but harness_schema kind decision exposes only generic fields. Catalog identifiers may be involved; sole root cause is unproven, superseding the earlier categorical catalog-blocker diagnosis. Owner: Seldon public controller/schema. Disposition: blocks plan review/publication/worker dispatch; product source unchanged. Exit test: public tools expose an actionable exact reviewer selection and same run advances to real review using gpt-6.1-sol medium, preserving recorded scope approval.', 'parents': ['V1'], 'fields': {'source': 'Current conversation tool receipts: harness_loop_answer reviewer selection, harness_schema decision, harness_plan_status'}}]}})
        client.call("harness_plan_status")
        client.answer(dict(option="ready", rationale="Captured r17 final role-only attempt", content=dict(
            roleTuples=[dict(role="roundtable", **flat), dict(role="acceptance", **flat)])))
        assert client.current["state"] == "REVIEW" and "setup" not in client.current
    finally:
        client.close()

    client = LoopClient(project, kickoff=settings)
    try:
        client.call("harness_plan_status")
        assert "setup" not in client.current
        client.call("harness_steer", direction="continue", rationale="Continue the retained r17 meaning")
        assert client.current["userRequest"]["gate"] == "invocations"
        assert not client.current.get("question")
        # Later file edits and semantic node replacements cannot retarget kickoff.
        (project / ".codex-harness/setup.json").write_text(json.dumps(dict(worker=dict(
            provider="codex", model="gpt-6.1-terra", effort="high"))), encoding="utf-8")
        client.reconnect()
        assert client.current["setup"]["project"] == settings["project"]
        client.words("Authorize these exact code-selected reviewers")
        client.answer(judge_reply(client.current["question"]))
        assert client.current["userRequest"]["gate"] == "plan-execution"
        status = client.call("harness_plan_status")
        assert status["run"]["state"] == status["continuation"]["state"] == "WAIT_USER"
        assert {r["model"] for r in client.current["userRequest"]["subject"]["reviewers"]} == {"gpt-6.1-sol"}
        assert {w["bead"] for w in client.current["workers"]} == {"B", "C"}
    finally:
        client.close()


def approval_order_golden(workspace):
    """C8/C23: r16's display -> judge -> reconnect/read -> chat reply reaches A.

    The tidy client knows undocumented field names. Replay the real planner's
    guesses, then consume the public schema, catching a schema that cannot teach
    how to retain the user's still-current decision. No new user consent is asked.
    """
    from tests.python.core.mcp_planner_support import LoopClient

    from codex_harness.core.plan_store import open_plan_store_readonly

    client = LoopClient(workspace)
    try:
        content = plan_content()
        client.call(
            "harness_plan_start", name="R16 approval order", idea=content["nodes"][0]["text"]
        )
        client.answer(
            dict(option="draft", rationale="Prepare expansion before consent", content=content)
        )
        client.answer(
            dict(option="ready", rationale="Present the complete scope for A", content={})
        )
        displayed = client.current["userRequest"]
        client.answer(judge_reply(client.current["question"]))
        client.reconnect()
        client.call("harness_schema", tool="harness_steer")
        # Exact r16 user_words shapes, with this run's displayed identities.
        request_id, revision, digest = (
            displayed["request_id"],
            displayed["plan_revision"],
            displayed["fingerprint"],
        )
        for guessed in (
            dict(text="yes", source="chat", request_id=request_id, fingerprint=digest),
            dict(
                text="yes",
                source="chat",
                request_id=request_id,
                plan_revision=revision,
                fingerprint=digest,
            ),
            dict(
                text="yes",
                source="chat",
                requestId=request_id,
                revision=revision,
                subjectFingerprint=digest,
            ),
            dict(
                text="yes",
                source="chat",
                request_id=request_id,
                revision=revision,
                fingerprint=digest,
            ),
            dict(
                text="yes",
                source="chat",
                request_id=request_id,
                displayed_revision=revision,
                fingerprint=digest,
            ),
            dict(
                text="yes",
                source="chat",
                request_id=request_id,
                subject_fingerprint=digest,
                plan_revision=revision,
            ),
        ):
            client.call(
                "harness_steer",
                direction="continue",
                rationale="Retain actual chat yes",
                user_words=guessed,
                error=True,
            )
            client.call("harness_plan_status")
        guide = client.call("harness_schema", tool="harness_steer")
        values = dict(
            request_id=request_id,
            expected_revision=revision,
            quoted_message="yes",
            quote_sha256=hashlib.sha256(b"yes").hexdigest(),
            actor_label="user",
        )
        contract = guide["inputSchema"]["properties"]["user_words"]
        client.call(
            "harness_steer",
            direction="continue",
            rationale="Retain the original chat yes using the published contract",
            user_words={key: values[key] for key in contract.get("required", [])},
        )
        client.reconnect()
        client.answer(judge_reply(client.current["question"]))
        with open_plan_store_readonly(workspace) as store:
            approval = approvals.current(store, "A")
            assert approval["state"] == "approved" and approval["words"] == "yes"
            assert approval["request_id"] == request_id
        assert client.current["phase"] == "plan"
    finally:
        client.close()


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


def automatic_retry_golden(workspace):
    """C1/C4: authorized semantic invocations use the one persisted 1j allowance.

    Manual corrected submissions miss a caller scheduler that abandons the first
    exact-fix response or launches a fourth model reply after terminal exhaustion.
    Exercise real bounded provider children; only their semantic text is scripted.
    """
    from tests.python.core.mcp_planner_support import LoopClient

    from codex_harness.core.plan_store import open_plan_store_readonly

    workspace.mkdir()
    (workspace / "semantic-retry-script.json").write_text(
        json.dumps({"understanding": 2, "vision-expansion": 2, "trace": 3}), encoding="utf-8"
    )
    client = LoopClient(workspace, kickoff=kickoff_content())
    try:
        client.call(
            "harness_plan_start",
            name="Automatic exact fixes",
            idea="Retain the binary receipt dream",
        )
        assert client.current["userRequest"]["gate"] == "invocations"
        # P1: missing kickoff roles resolve before freeze and drive actual native calls.
        selected = client.current["setup"]["roleTuples"]
        assert {r["role"] for r in selected} == {"planner", "judge"}
        assert client.current["userRequest"]["subject"]["roleTuples"] == selected
        client.words("Authorize only these displayed planner and judge tuples")
        # The requested grant cannot authorize its own interpreting model. Its
        # actual bound words receive the external scripted judge reply once.
        assert client.current["question"]["purpose"] == "user-decision"
        client.send_answer(judge_reply(client.current["question"]))
        marker = workspace / ".codex-harness/semantic-raw-complete.json"
        deadline = time.monotonic() + 45
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), (workspace / ".codex-harness/server.log").read_text("utf-8")
        retained_raw = json.loads(marker.read_text("utf-8"))
        client.reconnect()
        client.call(
            "harness_plan_start",
            name="Resume retained semantic output",
            idea="Keep the same binary receipt dream",
        )
        with open_plan_store_readonly(workspace) as store:
            replies = [
                row
                for row in approvals.records(store, "run-invocation-observed")
                if row["effectId"] == retained_raw["effectId"]
            ]
            assert len(replies) == 1 and replies[0]["cleanup"]["quiescenceVerified"]
            consumed = [
                row
                for row in approvals.records(store, "run-invocation-completed")
                if row["effectId"] == retained_raw["effectId"]
            ]
            assert len(consumed) == 1 and consumed[0]["requestId"] == retained_raw["requestId"]
            answers = [
                row
                for row in approvals.records(store, "run-semantic-answer")
                if row["requestId"] == retained_raw["requestId"]
            ]
            authored = json.loads(retained_raw["raw"])
            assert len(answers) == 1 and answers[0]["answer"] == authored
            assert all(
                store.spine().node(node["id"]) == node for node in authored["content"]["nodes"]
            )
        assert (
            client.current["userRequest"]["gate"] == "align+vision-lock"
            and not client.current["question"]
        ), client.current
        with open_plan_store_readonly(workspace) as store:
            history = [
                json.loads(line)
                for line in (workspace / ".codex-harness/scripted-semantic-responses.jsonl")
                .read_text("utf-8")
                .splitlines()
            ]
            for purpose, role in (("understanding", "planner"), ("vision-expansion", "judge")):
                replies = [row for row in history if row["purpose"] == purpose]
                assert len(replies) == 3 and {row["role"] for row in replies} == {role}, replies
                assert len({(row["requestId"], row["binding"]) for row in replies}) == 1
                assert [row["fixesIssued"] for row in replies] == [0, 1, 2]
                judged = store.judgment(replies[-1]["requestId"])
                assert (
                    judged["fixesIssued"] == 2
                    and judged["verdict"] == "positive"
                    and judged["disposition"] == "complete"
                )
                # Applying author content changes its captured subject revision.
                # Recovery must consume that positive answer once, not make its
                # historical pre-authoring judgment reusable against new nodes.
                if purpose == "understanding":
                    assert not judged["current"] and not judged["positiveCredit"]
                else:
                    assert judged["positiveCredit"]
                completed = [
                    row
                    for row in approvals.records(store, "run-invocation-completed")
                    if row["requestId"] == judged["id"]
                ]
                assert len(completed) == 3 and all(row["didRunProvider"] for row in completed)
        client.words("Approve this exact Vision Lock")
        assert client.current["state"] == "PAUSED" and not client.current["effect"], client.current
        with open_plan_store_readonly(workspace) as store:
            history = [
                json.loads(line)
                for line in (workspace / ".codex-harness/scripted-semantic-responses.jsonl")
                .read_text("utf-8")
                .splitlines()
            ]
            exhausted = [row for row in history if row["purpose"] == "trace"]
            assert len(exhausted) == 3 and [row["fixesIssued"] for row in exhausted] == [0, 1, 2]
            assert len({(row["requestId"], row["binding"]) for row in exhausted}) == 1
            closed = store.judgment(exhausted[-1]["requestId"])
            assert closed["fixesIssued"] == 2 and closed["disposition"] == "steer"
            assert (
                not closed["positiveCredit"]
                and not closed["concreteGap"]
                and closed["owner"] is None
            )
            actual = [
                row
                for row in approvals.records(store, "run-invocation-completed")
                if row["requestId"] == closed["id"]
            ]
            assert len(actual) == 3 and all(
                row["didRunProvider"] and not row["sourceVerified"] for row in actual
            )
            observed = approvals.records(store, "run-invocation-observed")
            assert len(observed) == len(history)
            assert all(
                row["cleanup"]["quiescenceVerified"]
                and row["cleanup"]["activeProcessesAfterTeardown"] == 0
                for row in observed
            )
            assert not store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchone()
        rejected = client.call(
            "harness_loop_answer",
            request_id=closed["id"],
            answer=judge_reply(dict(judgment=closed)),
            source="golden:late-fourth-reply",
        )
        # FIX17 retained-request rule: late replies return the current baton,
        # never revive a consumed request or expose a store exception.
        assert rejected["answerOutcome"]["state"] == "superseded" and "outstanding requestId" in str(rejected), (
            rejected
        )
        # The same 1j owner must continue an invalid unsolicited direction too.
        # A schema-valid missing parent used to return early from steer, leaving
        # an authorized planner idle; ordinary answer retries miss that entrance.
        script_path = workspace / "semantic-retry-script.json"
        script = json.loads(script_path.read_text("utf-8"))
        script["trace"] = 0
        script_path.write_text(json.dumps(script), encoding="utf-8")
        answers_before = client.tool_names["harness_loop_answer"]
        client.call(
            "harness_steer",
            direction="continue",
            rationale="Actual user explicitly resumes after the retained malformed output",
            resume=True,
        )
        assert (
            client.current["state"] == "WAIT_USER"
            and client.current["userRequest"]["gate"] == "invocations"
        ), client.current
        assert any(
            role["role"] == "reviewer"
            for role in client.current["userRequest"]["subject"]["roleTuples"]
        )
        with open_plan_store_readonly(workspace) as store:
            before_spine = store.spine().to_dict()
        history_path = workspace / ".codex-harness/scripted-semantic-responses.jsonl"
        before_replies = len(history_path.read_text("utf-8").splitlines())
        client.call(
            "harness_steer",
            direction="replan",
            rationale="Propose a semantic feature with the captured missing-parent defect",
            content=dict(
                nodes=[
                    dict(
                        id="F_BAD_PARENT",
                        kind="feature",
                        title="Unresolved feature parent",
                        text="Retain feature semantics while its parent is corrected",
                        parents=["MISSING_PARENT"],
                        fields={},
                    )
                ]
            ),
        )
        assert client.current["state"] == "PAUSED" and not client.current["effect"], client.current
        assert client.tool_names["harness_loop_answer"] == answers_before
        replies = [json.loads(line) for line in history_path.read_text("utf-8").splitlines()][
            before_replies:
        ]
        assert (
            len(replies) == 1 and replies[0]["role"] == "planner" and replies[0]["fixesIssued"] == 1
        ), replies
        with open_plan_store_readonly(workspace) as store:
            corrected = store.judgment(replies[0]["requestId"])
            assert corrected["binding"] == replies[0]["binding"] and corrected["fixesIssued"] == 1
            assert corrected["positiveCredit"] and corrected["disposition"] == "complete"
            assert corrected["meaning"]["option"] == "pause" and corrected["owner"] is None
            assert store.spine().to_dict() == before_spine
            launches = [
                row
                for row in approvals.records(store, "run-invocation-completed")
                if row["requestId"] == corrected["id"]
            ]
            assert (
                len(launches) == 1
                and launches[0]["didRunProvider"]
                and not launches[0]["sourceVerified"]
            )
            cleanup = next(
                row["cleanup"]
                for row in approvals.records(store, "run-invocation-observed")
                if row["effectId"] == launches[0]["effectId"]
            )
            assert cleanup["quiescenceVerified"] and cleanup["activeProcessesAfterTeardown"] == 0
        # A valid native zero exit arriving after the original lease is still
        # diagnostic only. Recovery before the normal post-invoke guard used to
        # grant it credit; timely receipt recovery cannot expose that failure.
        (workspace / "semantic-late-output").touch()
        answers_before = client.tool_names["harness_loop_answer"]
        client.send_steer(
            direction="replan",
            rationale="Actual user requests semantic replanning under the frozen invocation lease",
            resume=True,
        )
        late_marker = workspace / ".codex-harness/semantic-late-complete.json"
        deadline = time.monotonic() + 30
        while not late_marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert late_marker.exists(), (workspace / ".codex-harness/server.log").read_text("utf-8")
        late = json.loads(late_marker.read_text("utf-8"))
        with open_plan_store_readonly(workspace) as store:
            from datetime import datetime

            held = store.status()["run"]
            assert (
                held["effect"]["id"] == late["effectId"]
                and held["question"]["requestId"] == late["requestId"]
            )
            observed = [
                row
                for row in approvals.records(store, "run-invocation-observed")
                if row["effectId"] == late["effectId"]
            ]
            assert len(observed) == 1
            diagnostic = observed[0]
            assert diagnostic["eligible"] is False and diagnostic["binding"] == late["binding"]
            assert diagnostic["exitCode"] == 0 and diagnostic["cleanup"]["quiescenceVerified"]
            assert diagnostic["cleanup"]["activeProcessesAfterTeardown"] == 0
            assert datetime.fromisoformat(diagnostic["completedAt"]) > datetime.fromisoformat(
                held["effect"]["deadlineAt"]
            )
            assert not store.judgment(late["requestId"])["positiveCredit"]
        late_replies = [
            json.loads(line)
            for line in history_path.read_text("utf-8").splitlines()
            if json.loads(line)["requestId"] == late["requestId"]
        ]
        assert len(late_replies) == 1 and json.loads(late_replies[0]["raw"])["option"] == "ready"
        client.reconnect()
        client.call(
            "harness_plan_start",
            name="Inspect expired raw reply",
            idea="Retain diagnostics without reviving expired semantic ownership",
        )
        assert (
            client.current["state"] == "STEER"
            and client.current["semanticHeld"]
            and not client.current["effect"]
        ), client.current
        assert client.current["question"]["requestId"] != late["requestId"]
        assert client.tool_names["harness_loop_answer"] == answers_before
        with open_plan_store_readonly(workspace) as store:
            judged = store.judgment(late["requestId"])
            assert not judged["positiveCredit"] and judged["owner"] is None
            releases = [
                row
                for row in approvals.records(store, "run-owner-released")
                if row["effect"]["id"] == late["effectId"]
            ]
            assert (
                len(releases) == 1
                and releases[0]["ownershipReleased"]
                and releases[0]["quarantine"]
            )
            assert [
                row
                for row in approvals.records(store, "run-invocation-observed")
                if row["effectId"] == late["effectId"]
            ] == observed
            assert not [
                row
                for row in approvals.records(store, "run-invocation-completed")
                if row["effectId"] == late["effectId"]
            ]
        assert [
            json.loads(line)
            for line in history_path.read_text("utf-8").splitlines()
            if json.loads(line)["requestId"] == late["requestId"]
        ] == late_replies
    finally:
        client.close()


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

    def wait_for(name):
        marker = workspace / ".codex-harness" / (name + ".json")
        deadline = time.monotonic() + 45
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), (workspace / ".codex-harness/server.log").read_text("utf-8")
        return json.loads(marker.read_text("utf-8"))

    def paid_calls(*, acceptance=True):
        path = workspace / ".codex-harness/scripted-review-invocations.jsonl"
        return (
            [
                json.loads(line)
                for line in path.read_text("utf-8").splitlines()
                if json.loads(line)["acceptance"] is acceptance
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
                run.get("userRequest")
                and run["userRequest"]["gate"] == "invocations"
                and not run.get("question")
            ),
        )
        client.words("Authorize these displayed reviewer tuples for the actual plan review")
        assert client.current["question"]["purpose"] == "user-decision"
        client.send_answer(judge_reply(client.current["question"]))
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
        client.reconnect()
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
        client.reconnect()
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
        client.reconnect()
        client.send_start()
        admitted = wait_for("acceptance-review-admitted")
        assert admitted["runIds"] == [original_run["id"]] and admitted["reviewIds"] == [
            sealed["attemptId"]
        ]
        assert paid_calls() == original_paid
        client.reconnect()
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
            and member["node_id"] == "R4"
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
            store, prepared, dict(prepared["responseExample"]["meaning"], phase="wrong-phase")
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
            assert set(retained["beads"]) == {"B"}
            validation = retained["semanticValidation"]
            red = validation["red"]
            assert red["observedState"] == "FAIL", red
            assert any(
                row["id"] == "T_RECEIPT" and row["state"] == "FAIL" for row in red["results"]
            )
            observed = next(row for row in red["results"] if row["id"] == "T_RECEIPT")["actions"][
                "execute"
            ]["stderr"]
            assert "ModuleNotFoundError: No module named 'hashslip'" in observed
            assert "retired fixture still present" not in observed
            assert not (workspace / "checks/retired.json").exists()
            assert all(row["cleanup"]["quiescenceVerified"] for row in red["results"])
            assert validation["testSources"] == red["testSources"] and validation["judgmentId"]
            invocation = next(row for row in validation["results"] if row["id"] == "S6")["actions"][
                "run"
            ]
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


def plan_content(surface="cli"):
    """Retained run26/UX6 intent, authored through one canonical semantic payload."""
    from codex_harness.core.planner_schema import node_example

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
    for n in nodes:
        # FIX17 DELETE-structure: provider pins/project setup are operator kickoff data.
        if n["kind"] == "bead":
            for key in ("provider", "model", "effort"):
                n["fields"].pop(key, None)
        elif n["kind"] == "vision":
            n["fields"].pop("setup", None)
    return dict(nodes=nodes)


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
        record["responseExample"],
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
    elif purpose == "review-reuse":
        result["meaning"] = dict(reuse=True)
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
                    content=content if pending["purpose"] == "understanding" else {},
                )
            elif pending["family"] == "direction":
                assert "Integrated GREEN" in pending["purpose"], run
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
                from codex_harness.core.reviewer_evidence import review_tier

                tier = review_tier(expected)
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
            client.words()
        else:
            pytest.fail(
                "Controller returned without an outstanding question, user decision or terminal result: "
                + repr(run)
            )
    pytest.fail("Semantic journey did not terminate")


def controller_interruptions(workspace):
    """C16/C18/C23: lost response/live work/pause/wake/stop at the wire boundary.

    A synchronous server previously prevented active steering, and happy-path
    restarts happen only after cleanup. Hold an actual owned segment, control it
    over the same stdio connection, then expire its successor after a wake gap.
    An idle restart must also wait for an ordinary writer reservation longer
    than SQLite's default five seconds; uncontended restarts miss locked errors.
    """
    from tests.python.core.mcp_planner_support import LoopClient

    from codex_harness.core import approvals
    from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly

    workspace.mkdir()
    (workspace / "validation-observed").touch()
    (workspace / "hold-contracts").touch()
    (workspace / "template/checks").mkdir(parents=True)
    (workspace / "template/checks/retired.json").write_text("[1]\n")
    content = plan_content()
    client = LoopClient(workspace)

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
        client.call("harness_steer", direction="pause", rationale="Actual user pauses active work")
        assert client.current["state"] == "PAUSED" and not client.current["effect"]
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
            client.call(
                "harness_plan_start", name="Contended reattach", idea="Keep the paused run paused"
            )
            assert release.is_set(), "Reattachment returned before the writer reservation ended"
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
        client.call(
            "harness_steer",
            direction="continue",
            rationale="Actual user explicitly resumes",
            resume=True,
        )
        # A transient STEER may precede automatic recovery. Wait for the actual
        # exhausted-recovery question, not an instantaneous intermediate cursor.
        deadline = time.monotonic() + 45
        while not (client.current.get("question") and
                   "Two automatic recoveries exhausted" in client.current["question"]["purpose"]):
            assert time.monotonic() < deadline, client.current
            time.sleep(0.05)
            client.call("harness_plan_status")
        assert client.current["state"] == "STEER" and not client.current["effect"], client.current
        with open_plan_store_readonly(workspace) as store:
            recoveries = [
                r
                for r in approvals.records(store, "worker-recovery-resolved")
                if r["choice"] == "readmit" and not r.get("steeringJudgmentId")
            ]
            assert len(recoveries) == 2
            faults = list(approvals.records(store, "worker-recovery-steering-required"))
            assert faults[0]["automaticRecoveriesExhausted"]
        print("golden recovery: exhausted-recovery question observed; automatic readmissions=2", flush=True)
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
        advance_meaning(client, content, stop_at=lambda r: r["state"] == "STEER")
        with open_plan_store_readonly(workspace) as store:
            recoveries = [
                r
                for r in approvals.records(store, "worker-recovery-resolved")
                if r["choice"] == "readmit" and not r.get("steeringJudgmentId")
            ]
            assert len(recoveries) == 2
        (workspace / "fault-contracts").unlink()
        client.answer(
            dict(
                option="continue",
                rationale="Explicit direction after fixing the external worker",
                content={},
            )
        )
        assert client.current["question"]["purpose"] == "execution", client.current
        pending = client.current["question"]
        wrong = judge_reply(pending)
        wrong["meaning"]["check"] = "contracts"
        client.answer(wrong)
        assert client.current["question"]["requestId"] == pending["requestId"]
        assert "execution" in client.current["question"]["judgment"]["error"]
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
        (workspace / "hold-contracts").unlink(missing_ok=True)
        client.close()


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
                r.get("userRequest")
                and r["userRequest"]["gate"] == "invocations"
                and not r.get("question")
            ),
        )
        client.words()
        client.send_answer(judge_reply(client.current["question"]))
        marker = workspace / ".codex-harness/review-crash-started"
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), (workspace / ".codex-harness/server.log").read_text("utf-8")
        client.reconnect()
        original = client.current["effect"]
        (workspace / "clock-shift").write_text("3600")
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
            yield
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
    """
    from tests.python.core.mcp_planner_support import LoopClient

    from codex_harness.core import approvals
    from codex_harness.core.plan_store import open_plan_store_readonly

    started = time.monotonic()
    ledger_start = len(LoopClient.instances)
    if surface == "cli":
        fixture_ownership_golden(tmp_path / "fixture-ownership")
        planning_surface_golden(tmp_path / "planning-surface")
        reviewer_order_golden(tmp_path / "reviewer-order")
        request.getfixturevalue("http_journeys")
        approval_order_golden(tmp_path / "approval-order")
        automatic_retry_golden(tmp_path / "automatic-retries")
        single_build_golden(tmp_path / "single-build")
        receipt_gaps_golden(tmp_path / "receipt-gaps")
        planning_intent_golden(tmp_path.parent / (tmp_path.name + "-planning-intent"))
        judgment_contract_golden(tmp_path.parent / (tmp_path.name + "-judgment-contract"))
        execution_judgment_golden(tmp_path.parent / (tmp_path.name + "-execution-judgment"))
        trace_contract_golden(tmp_path.parent / (tmp_path.name + "-trace-contract"))
    if surface == "web":
        from codex_harness.core.semantic_runner import run as observe

        timeout_product = tmp_path / "timeout-product"
        timeout_product.mkdir()
        api = dict(target="service", method="GET", path="/", statusEquals=200)
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
    content = plan_content(surface)
    (tmp_path / "AGENTS.md").write_text("Use the exact selected model tuples.\n", encoding="utf-8")
    (tmp_path / "validation-observed").touch()
    if surface == "cli":
        (tmp_path / "template/checks").mkdir(parents=True)
        (tmp_path / "template/checks/retired.json").write_text("[1]\n")
    client = LoopClient(tmp_path, surface)
    try:
        client.call("harness_plan_start", name="Retained dream", idea=content["nodes"][0]["text"])
        execution_questions = []

        def execution(run):
            return bool(run.get("question") and run["question"]["purpose"] == "execution")

        advance_meaning(client, content, stop_at=execution)
        first = client.current["question"]
        execution_questions.append(first["requestId"])
        assert first["judgment"]["responseExample"]["meaning"]["check"] == "execution"
        with open_plan_store_readonly(tmp_path) as store:
            from codex_harness.core import planner_jobs as jobs

            run = client.current
            records = list(
                (jobs._directory(tmp_path, run["jobId"]) / "segments").glob("*/quiescence.json")
            )
            assert len(records) == 1
            cleanup = json.loads(records[0].read_text())["control"]
            assert cleanup["quiescenceVerified"] and cleanup["activeProcessesAfterTeardown"] == 0
            assert not approvals.current(store, "B")["human_identity_verified"]
            from codex_harness.core.planner_candidate import current_job_run

            _, first_saved = current_job_run(store, required=True)
            assert first_saved["status"] == "pending-judgment"
            assert first_saved["beads"]["C"]["status"] == "awaiting judgment"
            assert first_saved["beads"]["B"]["attempt"] is None
            assert "awaiting judgment" in first_saved["beads"]["B"]["status"]
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
        # The same durable execution identity survives a real MCP process death.
        client.reconnect()
        assert client.current["question"]["requestId"] == first["requestId"]
        assert client.current["question"]["binding"] == first["binding"]
        assert first["judgment"]["responseExample"]["meaning"]["phase"] == "contracts"
        if surface == "cli":
            packet = next(
                ref["payload"]
                for ref in first["judgment"]["sourceRefs"]
                if ref["kind"] == "candidate-observation"
            )
            errors = str(packet["proof"]["results"])
            assert "receipt startup failed" in errors, errors
            client.answer(
                judge_reply(
                    first,
                    verdict="negative",
                    gaps=["Receipt startup fails before checking the expected binary behavior"],
                )
            )
            retry = client.current["question"]
            execution_questions.append(retry["requestId"])
            assert (
                retry["purpose"] == "execution"
                and retry["judgment"]["responseExample"]["meaning"]["phase"] == "contracts"
            ), client.current
            assert (
                retry["judgment"]["responseExample"]["meaning"]["evidenceBinding"]
                != first["judgment"]["responseExample"]["meaning"]["evidenceBinding"]
            )
            # Recovery must deliver the judge's original meaning and binding to
            # the successor worker. Retry counts alone miss a repair packet that
            # loses the concrete gap and asks the worker to repeat unchanged work.
            with open_plan_store_readonly(tmp_path) as store:
                _, successor = current_job_run(store, required=True)
                request = Path(successor["beads"]["C"]["evidenceDir"]) / "attempt/request.json"
                delivered = json.loads(request.read_text("utf-8"))["prompt"]
                assert all(
                    value in delivered
                    for value in (
                        "Receipt startup fails before checking the expected binary behavior",
                        first["judgment"]["id"],
                        first["judgment"]["responseExample"]["meaning"]["evidenceBinding"],
                    )
                ), delivered
            first = retry
        client.answer(judge_reply(client.current["question"]))
        assert execution(client.current), client.current
        second = client.current["question"]
        execution_questions.append(second["requestId"])
        assert second["judgment"]["responseExample"]["meaning"]["phase"] == "validation", (
            client.current
        )
        assert (
            second["judgment"]["responseExample"]["meaning"]["evidenceBinding"]
            != first["judgment"]["responseExample"]["meaning"]["evidenceBinding"]
        )
        assert second["candidate"] != first["candidate"]
        with open_plan_store_readonly(tmp_path) as store:
            observed_job = client.current["jobId"]
            segments = jobs._directory(tmp_path, observed_job) / "segments"
            assert len(list(segments.glob("*/terminal.json"))) == 2
            from codex_harness.core.planner_candidate import current_job_run

            _, observed_run = current_job_run(store, required=True)
            assert observed_run["beads"]["B"]["attempt"] is not None
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
                == first["judgment"]["responseExample"]["meaning"]["evidenceBinding"]
            )
            assert (
                observed_run["semanticValidation"]["evidenceBinding"]
                == second["judgment"]["responseExample"]["meaning"]["evidenceBinding"]
            )
        client.answer(judge_reply(second))
        advance_meaning(client, content)
        assert client.current["state"] == "DONE"
        status = client.call("harness_plan_status")
        assert status["acceptance"]["accepted"], status
        with open_plan_store_readonly(tmp_path) as store:
            from codex_harness.core.planner_candidate import current_job_run

            selected, run = current_job_run(store, required=True)
            from codex_harness.core.acceptance_runs import acceptance_inputs

            quality_answers = [
                row
                for row in client.traffic
                if row["tool"] == "harness_loop_answer" and row["semanticCheck"] == "visualQuality"
            ]
            assert len(quality_answers) == 1
            consumed = acceptance_inputs(store, tmp_path, subject="product")["judgments"][
                "visualQuality"
            ]
            assert (
                consumed["id"] == quality_answers[0]["judgmentId"] and consumed["positiveCredit"]
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
            assert run["beads"]["C"]["lessonsPromoted"] == []
            assert not next(row for row in run["requirementValidation"] if row["bead"] == "C")[
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
            assert next(row for row in run["requirementValidation"] if row["bead"] == "B")["passed"]
            assert not red["passed"] and green["judgmentId"]
            saved = json.loads(Path(selected["runPath"]).read_text("utf-8"))
            assert saved["semanticContracts"] == red and saved["semanticValidation"] == green
            assert status["acceptance"]["accepted"]
            assert {row["id"] for row in status["acceptance"]["scenarios"]} == {
                row["id"] for row in content["nodes"][0]["fields"]["scenarios"]["scenarios"]
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
            job = jobs._directory(tmp_path, selected["jobId"])
            assert len(list((job / "segments").glob("*/terminal.json"))) == 2
            assert sum(bool(bead.get("attempt")) for bead in run["beads"].values()) == 2
            assert (
                len(store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchall()) == 1
            )
            assert {r["subject"]["kind"] for r in approvals.records(store, approvals.EVENT)} == {
                "A",
                "P",
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
        submissions = [
            row["requestId"] for row in client.traffic if row["tool"] == "harness_loop_answer"
        ]
        assert len(submissions) == len(set(submissions))
        assert client.tool_names["harness_plan_start"] == 1
        assert [gate for gate in client.decision_gates if gate != "invocations"] == [
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
            controller_interruptions(tmp_path.parent / (tmp_path.name + "-interruptions"))
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
    if sys.argv[1] == "http-golden":
        from tests.python.core.acceptance_support import http_golden

        http_golden(Path(sys.argv[2]), semantic=bool(int(sys.argv[3])))
    else:
        provider_host(*sys.argv[1:])
