"""Public MCP review journey; fake providers establish source behavior only."""

import json
import subprocess
import sys
import threading
import time

from tests.python.core import test_acceptance_candidate_packet
from tests.python.core.test_acceptance_candidate_packet import candidate
from tests.python.core.test_acceptance_recovery import REVIEWERS, protocol
from tests.python.core.test_mcp_planner import workspace_wire

from codex_harness.core import roundtable
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.provider_call_budget import usage

project = test_acceptance_candidate_packet.project


def test_review_admission_reconnect_and_explicit_unknown_recovery(project, monkeypatch):
    """Catch transport-blocking paid review and duplicate retry after owner loss.

    Prior review tests waited synchronously and could not observe an admitted job
    while providers were blocked, or prove UNKNOWN stays held through recovery.
    """
    store, repo = project
    candidate(store, repo, reviewers=REVIEWERS)
    entered, release = threading.Event(), threading.Event()
    launches = []
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)

    def provider(launch, *_):
        # Executable was replaced, so identify the supported CLI's argument shape.
        vendor = "codex" if "exec" in launch.command else "claude"
        launches.append(vendor)
        entered.set()
        assert release.wait(10), "Journey failed to release its fake provider"
        return protocol(vendor, text="[]")

    monkeypatch.setattr(roundtable, "_run", provider)
    server = PlannerServer(store.workspace)

    def call(name, **arguments):
        return workspace_wire(server, name, expected_workspace=str(store.workspace), **arguments)

    def observe(attempt):
        return call("harness_accept_status", id=attempt)["structuredContent"]

    def finish(attempt):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = observe(attempt)
            if result["state"] not in {"queued", "running"}:
                assert result["state"] == "completed", result
                return result["result"]
            time.sleep(0.01)
        raise AssertionError("Review did not finish")

    try:
        admitted = call("harness_accept_review", repo=str(repo), reviewers=REVIEWERS)
        assert not admitted["isError"], admitted
        attempt = admitted["structuredContent"]["attemptId"]
        assert observe(attempt)["state"] == "queued" and not launches
        discovered = call("harness_plan_status")["structuredContent"]["acceptance"][
            "reviewAttempts"
        ]
        assert any(row.get("attemptId") == attempt for row in discovered)
        server.reviews.dispatch_after_flush(server.store)
        assert entered.wait(10)
        assert observe(attempt)["state"] == "running" and len(launches) == 1
        server.close()  # Drop the transport/server while a real worker is still blocked.
        server = PlannerServer(store.workspace)
        assert observe(attempt)["state"] == "running"
        repeated = call("harness_accept_review", repo=str(repo), reviewers=REVIEWERS)
        assert repeated["structuredContent"]["attemptId"] == attempt
        server.reviews.dispatch_after_flush(server.store)
        assert len(launches) == 1
        release.set()
        result = finish(attempt)
        assert result["id"] == attempt and len(launches) == 2
        assert finish(attempt) == result and len(launches) == 2
        reviews = store.status()["acceptance"]["reviews"]
        assert [r["id"] for r in reviews].count(attempt) == 1

        before_crash = usage(store, "review")
        # Kill a separate server process after its fake provider actually starts.
        # The surviving canonical record must retain UNKNOWN and block replay.
        server.close()
        entry = repo.parent / "crash-review.py"
        marker = repo.parent / "fake-started"
        admission = repo.parent / "admission.json"
        entry.write_text(
            "import json,sys,threading\nfrom pathlib import Path\n"
            "from codex_harness.core import roundtable\n"
            "from codex_harness.core.mcp_planner import PlannerServer\n"
            "workspace,repo,marker,admission=sys.argv[1:]\n"
            "roundtable._executable=lambda *_:sys.executable\n"
            "def blocked(*_):\n Path(marker).touch()\n threading.Event().wait(60)\n"
            "roundtable._run=blocked\nserver=PlannerServer(workspace)\n"
            f"reviewers={REVIEWERS!r}\n"
            "reply=server.handle_line(json.dumps(dict(jsonrpc='2.0',id=1,method='tools/call',"
            "params=dict(name='harness_accept_review',arguments=dict("
            "expected_workspace=workspace,repo=repo,reviewers=reviewers)))))['result']\n"
            "Path(admission).write_text(json.dumps(reply))\n"
            "server.reviews.dispatch_after_flush(server.store)\n"
            "threading.Event().wait(60)\n",
            encoding="utf-8",
        )
        child = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(entry),
                str(store.workspace),
                str(repo),
                str(marker),
                str(admission),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists(), "Separate server never reached its fake provider"
            lost = json.loads(admission.read_text())["structuredContent"]["attemptId"]
        finally:
            child.terminate()
            child.communicate(timeout=5)
        server = PlannerServer(store.workspace)
        assert observe(lost)["outcome"] == "UNKNOWN"
        spent = usage(store, "review")
        assert spent == before_crash + 1
        assert (
            call("harness_accept_review", repo=str(repo), reviewers=REVIEWERS)["structuredContent"][
                "attemptId"
            ]
            == lost
        )
        assert len(launches) == 2
        request = call(
            "harness_recovery",
            mode="resolve",
            job_id="review:" + lost,
            choice="readmit",
            reason="Accept the spent claim and retry once",
        )
        assert request["isError"] and "UNKNOWN stays held" in str(request)
        assert (
            call("harness_accept_review", repo=str(repo), reviewers=REVIEWERS)["structuredContent"][
                "attemptId"
            ]
            == lost
        )
        assert usage(store, "review") == spent and len(launches) == 2
        stopped = call(
            "harness_recovery",
            mode="resolve",
            job_id="review:" + lost,
            choice="abandon",
            reason="Stop with the uncertain spent claim retained",
        )
        assert not stopped["isError"], stopped
        assert stopped["structuredContent"]["quarantine"]
        assert call("harness_accept_review", repo=str(repo), reviewers=REVIEWERS)["isError"]
        assert len(launches) == 2

    finally:
        release.set()
        server.close()
