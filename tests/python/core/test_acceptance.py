"""Local scenarios, MOCKED reviews and SOURCE-ONLY approval counterfactuals."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from tests.python.core import test_dispatch, test_semantic_wiring
from tests.python.core.planning_support import (
    publication_evidence,
    source_only_b,
    source_only_confirmation,
    source_only_vision_lock,
)

from codex_harness.core.acceptance import (
    acceptance_review,
    current_run,
    derive_scenarios,
    run_acceptance,
)
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PHASES, PlanStore, PlanStoreError

lesson_dispatch = test_dispatch.lesson_dispatch
semantic_plan = test_semantic_wiring.semantic_plan
semantic_acceptance_product = test_semantic_wiring.semantic_acceptance_product


@pytest.mark.parametrize("link", ["unlinked", "unknown", "stale", "empty"])
def test_semantic_accept_refuses_invalid_coverage_before_admission(
    semantic_acceptance_product, link
):
    """Catch admitted uncovered promises; legacy refusal tests have no semantic reference guard."""
    from tests.python.core.test_semantic_wiring import node

    store, repo = semantic_acceptance_product
    if link == "unlinked":
        store.upsert_node(node("R2", "requirement", ["F"]))
    else:
        vision = store.spine().node("V1")
        suite = vision["fields"]["scenarios"]["scenarios"]
        if link == "empty":
            suite.clear()
        else:
            suite[0]["requirementRefs"] = ["missing" if link == "unknown" else "R@old"]
        store.upsert_node(vision)
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool("harness_accept_run", dict(repo=str(repo), subject="product"))
        assert reply["isError"], reply
        assert any(word in str(reply) for word in ("Every requirement", "reference", "1..16"))
        assert not store.connection.execute("SELECT 1 FROM acceptance_attempts").fetchone()
        assert not server.acceptance.thread and not server.acceptance.prepared
        with pytest.raises(ValueError):
            run_acceptance(store, repo, subject="product")
    finally:
        server.close()


def test_semantic_accept_target_change_revokes_credit(semantic_acceptance_product):
    """Catch stale target credit and snapshot reuse; prior freshness tests mutate legacy rows."""
    store, repo = semantic_acceptance_product
    result = run_acceptance(store, repo, subject="product")
    assert result["passed"] and current_run(store)["id"] == result["id"]
    vision = store.spine().node("V1")
    vision["fields"]["targets"]["greet"]["argv"] = ["changed"]
    store.upsert_node(vision)
    assert current_run(store) is None
    from codex_harness.core.acceptance import acceptance_inputs

    assert (
        acceptance_inputs(store, repo, subject="product")["scenarioRevision"]
        != result["scenarioRevision"]
    )


def drain_acceptance(owner, store, timeout=20):
    deadline = time.monotonic() + timeout
    while owner.thread and owner.thread.is_alive() and time.monotonic() < deadline:
        owner.thread.join(timeout=0.01)
        owner.drain(store)
    assert not owner.thread or not owner.thread.is_alive()
    owner.drain(store)


def finish_public_acceptance(server, admitted):
    """Drive the same serial flush/completion hooks as stdio for direct-call tests."""
    assert not admitted["isError"], admitted
    server.acceptance.dispatch_after_flush(server.store)
    drain_acceptance(server.acceptance, server.store)
    return server.call_tool(
        "harness_accept_status", {"id": admitted["structuredContent"]["attemptId"]}
    )


@pytest.mark.parametrize("hold", ["scenario", "initial-metadata"])
def test_acceptance_stdio_admission_keeps_status_responsive(lesson_dispatch, tmp_path, hold):
    """Real accepted-partial dispatch through stdio must not monopolize the server.

    Catches the observed blocked status call at scenario and initial/terminal
    metadata boundaries. Direct runner tests miss actual interleaved protocol
    requests and autonomous expiry while genuine capture is held.
    """
    import queue
    import threading

    case = lesson_dispatch("ok")
    repo = Path(case["record"]["repo"])
    release = tmp_path / "release"
    started = tmp_path / "started"
    metadata_started, metadata_release = (
        tmp_path / "metadata-started",
        tmp_path / "metadata-release",
    )
    finished = tmp_path / "finished"
    freshness_arm = tmp_path / "freshness-arm"
    script = tmp_path / "controlled.py"
    script.write_text(
        "import time\nfrom pathlib import Path\n"
        f"Path({str(started)!r}).write_text('started')\n"
        "deadline=time.monotonic()+12\n"
        f"while not Path({str(release)!r}).exists() and time.monotonic()<deadline:\n"
        " time.sleep(.02)\n"
        f"Path({str(finished)!r}).touch()\n"
        "print('controlled completed')\n"
    )
    with PlanStore(case["workspace"]) as store:
        scenarios = derive_scenarios(store.locked_acceptance_spine())
        for index, scenario in enumerate(scenarios):
            scenario.update(
                steps=[f'"{{python}}" "{script}"'], expect={"stdout": "controlled completed"}
            )
            if index == 0:
                scenario["steps"] = ['"{python}" -c "print(\'controlled completed\')"']
            if hold == "deadline-metadata":
                scenario["steps"] = []  # Genuine derived 3.4s budget, no budget override.
        store.save_acceptance_scenarios(scenarios)
    diagnostic = (tmp_path / "server-stderr.txt").open("w", encoding="utf-8")
    entry = tmp_path / "metadata-server.py"
    entry.write_text(
        "import time\nfrom pathlib import Path\n"
        "from codex_harness.core import acceptance, planner_acceptance, mcp_planner\n"
        "actual=acceptance.capture_acceptance\nheld=False\n"
        "def capture(*args, **kwargs):\n global held\n"
        f" if not held and ({hold!r} in ('initial-metadata','deadline-metadata') or "
        f"({hold!r}=='terminal-metadata' and Path({str(finished)!r}).exists())):\n"
        f"  held=True\n  Path({str(metadata_started)!r}).touch()\n"
        "  until=time.monotonic()+10\n"
        f"  while not Path({str(metadata_release)!r}).exists() and time.monotonic()<until:\n"
        "   time.sleep(.02)\n return actual(*args, **kwargs)\n"
        "acceptance.capture_acceptance=capture\nplanner_acceptance.capture_acceptance=capture\n"
        "real_revision=acceptance.integrated_revision\n"
        "def revision(*args, **kwargs):\n"
        f" if Path({str(freshness_arm)!r}).exists():\n"
        f"  Path({str(metadata_started)!r}).touch()\n  until=time.monotonic()+8\n"
        f"  while not Path({str(metadata_release)!r}).exists() and time.monotonic()<until:\n"
        "   time.sleep(.02)\n return real_revision(*args, **kwargs)\n"
        "acceptance.integrated_revision=revision\n"
        "mcp_planner.main()\n"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(entry),
            "--workspace",
            str(case["workspace"]),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=diagnostic,
        text=True,
        encoding="utf-8",
    )
    replies = queue.Queue()
    transcript = []
    reply_times = {}

    def read():
        for line in process.stdout:
            parsed = json.loads(line)
            transcript.append(parsed)
            reply_times[parsed.get("id")] = time.monotonic()
            replies.put(parsed)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    def send(identifier, name, **args):
        process.stdin.write(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=identifier,
                    method="tools/call",
                    params=dict(
                        name=name, arguments=dict(expected_workspace=str(case["workspace"]), **args)
                    ),
                )
            )
            + "\n"
        )
        process.stdin.flush()

    try:
        process.stdin.write(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=0,
                    method="initialize",
                    params=dict(protocolVersion="2025-06-18", capabilities={}),
                )
            )
            + "\n"
        )
        process.stdin.flush()
        assert replies.get(timeout=5)["id"] == 0
        admission_started = time.monotonic()
        send(1, "harness_accept_run", repo=str(repo), subject="product")
        send(2, "harness_plan_status")
        admitted = replies.get(timeout=2)
        assert admitted["id"] == 1
        attempt_id = admitted["result"]["structuredContent"]["attemptId"]
        assert replies.get(timeout=2)["id"] == 2
        assert not release.exists()
        if hold in {"initial-metadata", "deadline-metadata"}:
            deadline = time.monotonic() + 3
            while not metadata_started.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert metadata_started.exists() and not metadata_release.exists()
            process.stdin.write(json.dumps(dict(jsonrpc="2.0", id=10, method="ping")) + "\n")
            process.stdin.flush()
            assert replies.get(timeout=2)["id"] == 10
            send(14, "harness_accept_status", id=attempt_id)
            pending = replies.get(timeout=2)["result"]["structuredContent"]
            assert not pending["productCaptured"] and pending["result"] is None
            if hold == "deadline-metadata":
                from codex_harness.core.plan_store import open_plan_store_readonly

                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with open_plan_store_readonly(case["workspace"]) as snapshot:
                        state = snapshot.connection.execute(
                            "SELECT state FROM acceptance_attempts WHERE id=?", (attempt_id,)
                        ).fetchone()[0]
                    if state == "unknown-interrupted":
                        break
                    time.sleep(0.02)
                assert state == "unknown-interrupted" and not started.exists()
                send(15, "harness_accept_status", id=attempt_id)
                expired = replies.get(timeout=2)["result"]["structuredContent"]
                assert expired["result"] is None and not expired["productCaptured"]
                return
            metadata_release.touch()
        # No MCP poll drives this write: the main thread wakes for each completion.
        from codex_harness.core.plan_store import open_plan_store_readonly

        deadline = time.monotonic() + 5
        progress = []
        while time.monotonic() < deadline:
            with open_plan_store_readonly(case["workspace"]) as snapshot:
                row = snapshot.connection.execute(
                    "SELECT progress_json FROM acceptance_attempts WHERE id=?", (attempt_id,)
                ).fetchone()
                progress = json.loads(row[0])
            if progress and started.exists():
                break
            time.sleep(0.02)
        assert len(progress) == 1 and progress[0]["passed"] and started.exists()
        send(3, "harness_accept_status", id=attempt_id)
        pending = replies.get(timeout=2)["result"]["structuredContent"]
        assert pending["state"] == "running" and len(pending["progress"]) == 1
        assert pending["startedAt"] and pending["deadlineAt"]
        assert pending["maxExecutionSeconds"] == sum(60 * len(s["steps"]) + 1.7 for s in scenarios)
        release.write_text("release")
        if hold == "terminal-metadata":
            deadline = time.monotonic() + 3
            while not metadata_started.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert metadata_started.exists()
            send(11, "harness_plan_status")
            assert replies.get(timeout=2)["id"] == 11
            process.stdin.write(json.dumps(dict(jsonrpc="2.0", id=12, method="ping")) + "\n")
            process.stdin.flush()
            assert replies.get(timeout=2)["id"] == 12
            send(13, "harness_accept_status", id=attempt_id)
            checking = replies.get(timeout=2)["result"]["structuredContent"]
            assert checking["state"] == "running" and checking["result"] is None
            metadata_release.touch()
        # Completion also persists without polling; the public consumer reads it.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with open_plan_store_readonly(case["workspace"]) as snapshot:
                state = snapshot.connection.execute(
                    "SELECT state FROM acceptance_attempts WHERE id=?", (attempt_id,)
                ).fetchone()[0]
            if state == "completed":
                break
            time.sleep(0.02)
        assert state == "completed"
        if hold == "completed-status":
            freshness_arm.touch()
        send(4, "harness_accept_status", id=attempt_id)
        if hold == "completed-status":
            process.stdin.write(json.dumps(dict(jsonrpc="2.0", id=16, method="ping")) + "\n")
            process.stdin.flush()
        final_reply = replies.get(timeout=2)["result"]
        finished = final_reply["structuredContent"]
        if hold == "completed-status":
            assert replies.get(timeout=2)["id"] == 16
            assert not metadata_started.exists() and not metadata_release.exists()
            assert final_reply["jobOnly"] and final_reply["planStatusIncluded"] is False
            assert "plannerConnection" not in final_reply
            assert final_reply["workspaceBinding"]["match"] == "matched"
        assert finished["result"]["passed"]
        assert len(finished["progress"]) == len(scenarios)
        with PlanStore(case["workspace"]) as store:
            assert current_run(store)["id"] == finished["result"]["id"]
    finally:
        release.write_text("release")
        metadata_release.touch()
        process.stdin.close()
        assert process.wait(timeout=20) == 0
        diagnostic.close()
        reader.join(timeout=2)
        (tmp_path / "stdio-transcript.json").write_text(json.dumps(transcript, indent=2))
        (tmp_path / "timing.json").write_text(
            json.dumps({str(k): v - admission_started for k, v in reply_times.items() if k != 0})
        )
        (tmp_path / "source-case.json").write_text(
            json.dumps(dict(workspace=str(case["workspace"]), repo=str(repo)))
        )


def test_acceptance_owner_loss_does_not_replay_or_publish_late_credit(project, tmp_path):
    """Catch a second admission or late PASS after owner loss; sync tests have no owner."""
    from codex_harness.core.planner_acceptance import AcceptanceOwner

    store, repo = project
    fix_product(repo)
    release, started = tmp_path / "owner-release", tmp_path / "owner-started"
    script = tmp_path / "owner-child.py"
    script.write_text(
        "import time\nfrom pathlib import Path\n"
        f"Path({str(started)!r}).write_text('one child')\n"
        "deadline=time.monotonic()+8\n"
        f"while not Path({str(release)!r}).exists() and time.monotonic()<deadline:\n"
        " time.sleep(.02)\n"
        "print('finite child completed')\n"
    )
    scenarios = derive_scenarios(store.locked_acceptance_spine())
    for scenario in scenarios:
        scenario.update(steps=[f'"{{python}}" "{script}"'], expect={"exit": 0})
    store.save_acceptance_scenarios(scenarios)
    owner, other = AcceptanceOwner(), AcceptanceOwner()
    admitted = owner.start(store, repo, subject="product")
    assert owner.start(store, repo, subject="product")["attemptId"] == admitted["attemptId"]
    owner.dispatch_after_flush(store)
    try:
        deadline = time.monotonic() + 3
        while not started.exists() and time.monotonic() < deadline:
            owner.drain(store)
            time.sleep(0.02)
        assert started.exists()
        before = list(store.connection.iterdump())
        observed = other.status(store, admitted["attemptId"])
        assert observed["state"] == "unknown-interrupted" and observed["quiescence"] == "UNVERIFIED"
        assert list(store.connection.iterdump()) == before  # Status never claims ownership.
        owner.close(store)
        replay = other.start(store, repo, subject="product")
        assert replay["attemptId"] == admitted["attemptId"]
        assert replay["state"] == "unknown-interrupted" and other.prepared is None
        assert replay["unfinishedScenarioIds"]
        with pytest.raises(PlanStoreError) as held:
            run_acceptance(store, repo, subject="product")
        assert held.value.code == "acceptance-busy"
    finally:
        release.write_text("release")
        owner.thread.join(timeout=12)
        assert not owner.thread.is_alive()
        owner.drain(store)
    assert other.status(store, admitted["attemptId"])["result"] is None
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_attempts").fetchone()[0] == 1
    assert not store.connection.execute("SELECT 1 FROM acceptance_runs").fetchone()


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill cleanup contract")
def test_acceptance_cleanup_uncertainty_holds_future_workspace_admission(project, monkeypatch):
    """Catch a new attempt launching after unknown cleanup; one-step tests miss durable hold."""
    from codex_harness.core.planner_acceptance import AcceptanceOwner

    store, repo = project
    fix_product(repo)
    owner = AcceptanceOwner()
    admitted = owner.start(store, repo, subject="product")
    popen, run = subprocess.Popen, subprocess.run
    launched = []

    class UnobservedChild:
        pid = 424242
        returncode = None

        def __init__(self, command, **kwargs):
            launched.append(command)

        def communicate(self, timeout):
            raise subprocess.TimeoutExpired("controlled unobserved child", timeout)

        def poll(self):
            return None

        def kill(self):
            pass  # Controlled failure: exit observation never arrives.

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **kwargs: UnobservedChild(command, **kwargs)
        if isinstance(command, str)
        else popen(command, **kwargs),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1)
        if command[0] == "taskkill"
        else run(command, **kwargs),
    )
    owner.dispatch_after_flush(store)
    drain_acceptance(owner, store)
    result = owner.status(store, admitted["attemptId"])
    assert len(launched) == 1
    assert result["state"] == "unknown-interrupted"
    assert result["result"] is None  # Uncertain cleanup forbids a new metadata child/final receipt.
    assert result["progress"][1]["state"] == "unfinished"
    scenarios = store.acceptance_scenarios()
    scenarios[0]["expect"] = {
        "exit": 3
    }  # Different inputs still cannot bypass uncertain ownership.
    store.save_acceptance_scenarios(scenarios)
    again = owner.start(store, repo, subject="oracle")
    assert again["attemptId"] == admitted["attemptId"] and owner.prepared is None
    assert len(launched) == 1
    owner.close(store)


@pytest.mark.parametrize("mode", ["self-confirmation", "ok"])
def test_public_acceptance_requires_accepted_dispatch_candidate_not_partial_artifacts(
    lesson_dispatch, tmp_path, mode
):
    """Catch accepting zero delivered beads or rejecting useful partial diagnostics.

    The canonical finite child builds A and real integration proves it. An actual
    self-confirmation refusal can still leave no accepted bead; old product tests
    lack this completed dispatch producer and would execute scenarios regardless.
    """
    from tests.python.core.test_mcp_planner import workspace_wire

    case = lesson_dispatch(mode)
    record = case["record"]
    repo = Path(record["repo"])
    assert record["beads"]["A"]["integrated"]["validation"]["passed"]
    assert (repo / "A.py").read_text() == "VALUE = 1\n"
    assert record["status"].startswith("plan-failed")  # B remains legitimately blocked.
    marker = tmp_path / "scenario-started"
    server = PlannerServer(case["workspace"])

    def tool(name, **arguments):
        return workspace_wire(server, name, expected_workspace=str(case["workspace"]), **arguments)

    try:
        derived = tool("harness_accept_scenarios", derive=True)
        assert not derived["isError"], derived
        scenarios = derived["structuredContent"]["scenarios"]
        command = (
            '"{python}" -c "from pathlib import Path; import runpy; '
            f"Path(r'{marker}').write_text('ran'); "
            "print(runpy.run_path(r'{repo}/A.py')['VALUE'])\""
        )
        for scenario in scenarios:
            scenario["steps"] = [command]
            scenario["expect"] = {"stdout": "1", "exit": 0}
        saved = tool("harness_accept_scenarios", scenarios=scenarios)
        assert not saved["isError"], saved
        result = tool("harness_accept_run", repo=str(repo), subject="product")
        if not result["isError"]:
            result = finish_public_acceptance(server, result)
        if mode == "self-confirmation":
            assert result["isError"], result
            assert "acceptance-no-delivered-candidate" in result["structuredContent"]["error"]
            assert not marker.exists()
            assert not server.store.connection.execute("SELECT 1 FROM acceptance_runs").fetchone()
        else:
            assert not result["isError"], result
            assert marker.exists()
            assert result["structuredContent"]["result"]["passed"]
            assert result["structuredContent"]["result"]["results"]
    finally:
        server.close()


def public_assessment_repair_fixture(
    tmp_path,
    monkeypatch,
    *,
    author_metadata=True,
    worker_setup=None,
    worker_context="",
    second_bead=False,
    prerequisite=False,
):
    """Genuine pre-worker AF and actual changed-byte repair; SOURCE ONLY processes."""
    import shutil
    import tempfile
    from types import SimpleNamespace

    from tests.python.core.execution_support import publish_source_store, source_grant
    from tests.python.core.test_acceptance_recovery import REVIEWERS, protocol
    from tests.python.core.test_dispatch import _tiny_plan
    from tests.python.core.test_dispatch_cancel import source_only_packet_renderer

    from codex_harness.core import dispatch, planner_jobs, record_io, roundtable
    from codex_harness.core.provider_launch import ProviderNormalLaunch

    plan = _tiny_plan(tmp_path)
    manifest = json.loads((plan / "sample.json").read_text())
    if second_bead:
        manifest["beads"]["B"]["dependsOn"] = ["A"]
    else:
        del manifest["beads"]["B"]
    if second_bead:
        (plan / "template/B.py").write_text("VALUE = 0\ndef value(): return VALUE\n")
        (plan / "template/tests/test_B.py").write_text(
            "import unittest\nfrom B import value\nclass Proof(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(value(), 1)\n"
        )
    if prerequisite:
        manifest["beads"]["D"] = {
            "title": "A prerequisite",
            "ownedFiles": ["D.py"],
            "requirements": ["RD"],
            "proofModule": "test_D.py",
        }
        manifest["beads"]["A"]["dependsOn"] = ["D"]
        (plan / "template/D.py").write_text("VALUE = 0\ndef value(): return VALUE\n")
        (plan / "template/tests/test_D.py").write_text(
            "import unittest\nfrom D import value\nclass Proof(unittest.TestCase):\n"
            "    def test_dependency(self):\n        self.assertEqual(value(), 1)\n"
        )
    (plan / "sample.json").write_text(json.dumps(manifest))
    (plan / "template/A.py").write_text(
        worker_context + "def valid_id(value): return isinstance(value, int)\n", encoding="utf-8"
    )
    (plan / "template/tests/test_A.py").write_text(
        worker_context
        + ("import context_helper\n" if worker_context else "")
        + ("import D\n" if prerequisite else "")
        + "import unittest\nfrom A import valid_id\nclass Proof(unittest.TestCase):\n"
        "    def test_bool(self):\n        self.assertFalse(valid_id(True))\n"
        "        self.assertTrue(valid_id(7))\n"
        + ("        self.assertEqual(D.VALUE, 1)\n" if prerequisite else ""),
        encoding="utf-8",
    )
    if worker_context:
        (plan / "template/tests/context_helper.py").write_text(worker_context, encoding="utf-8")
    if not second_bead:
        (plan / "template/tests/test_B.py").unlink()
    workspace = Path(tempfile.mkdtemp(prefix="public-af-"))
    store, revision = publish_source_store(workspace, plan, reviewers=REVIEWERS)
    server = PlannerServer(workspace)
    calls = []
    traces = []

    def tool(name, arguments, *, error=False):
        result = server.call_tool(name, arguments)
        if name == "harness_accept_run" and not result["isError"]:
            result = finish_public_acceptance(server, result)
            result["structuredContent"] = result["structuredContent"]["result"]
        traces.append(dict(name=name, arguments=arguments, result=result))
        assert bool(result.get("isError")) is error, result
        return result.get("structuredContent")

    source_child = tmp_path / "source-reviewer.py"
    source_child.write_text(
        "# Explicit SOURCE-ONLY process, never a native provider\n"
        "import json,sys\np=json.load(sys.stdin)\n"
        "sys.stdout.write(p['stdout'])\nsys.stderr.write(p['stderr'])\n"
    )
    which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name: sys.executable if name in {"codex", "claude"} else which(name)
    )
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)

    def review_process(launch, cwd, timeout, *, raw=False):
        assert Path(launch.command[0]).resolve() == Path(sys.executable).resolve()
        assert source_child.is_file(), "Never fall back to installed provider"
        packet = launch.stdin_text
        if packet.startswith('{"schema": "seldon/canonical-execution-packet-v1"'):
            response = json.loads(packet)["responseContract"]
            for member in response["members"]:
                member.update(
                    judgment="supports-repair",
                    explanation="Actual protected bool rejection and integer acceptance "
                    "observe A.py after the canonical worker repair",
                )
            output = protocol("codex", text=json.dumps(response))
        else:
            provider = "codex" if "exec" in launch.command else "claude"
            response = (
                [
                    dict(
                        node_id="RA",
                        severity="major",
                        finding="Boolean IDs are accepted",
                        suggestion="Reject True while retaining integer 7",
                    )
                ]
                if len(calls) < 2
                else []
            )
            output = protocol(provider, text=json.dumps(response))
        argv = [sys.executable, "-B", str(source_child)]
        child = subprocess.run(
            argv,
            input=json.dumps(dict(stdout=output[1], stderr=output[2])).encode(),
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        import hashlib

        calls.append(
            dict(
                argv=argv,
                raw=raw,
                exitCode=child.returncode,
                packet=packet,
                stdoutSha256=hashlib.sha256(child.stdout).hexdigest(),
                stderrSha256=hashlib.sha256(child.stderr).hexdigest(),
            )
        )
        return (
            child.returncode,
            child.stdout if raw else child.stdout.decode(),
            child.stderr if raw else child.stderr.decode(),
        )

    monkeypatch.setattr(roundtable, "_run", review_process)
    scenarios = derive_scenarios(store.locked_acceptance_spine())
    for scenario in scenarios:
        scenario.update(
            steps=[
                '"{python}" -B -c "from A import valid_id; assert not '
                'valid_id(True); assert valid_id(7)"'
            ],
            expect={"exit": 0},
            nodeIds=["RA"],
        )
    tool("harness_accept_scenarios", {"scenarios": scenarios})
    broken = tmp_path / "pre-repair-product"
    shutil.copytree(workspace / "template", broken)
    old_bytes = (broken / "A.py").read_bytes()
    old_run = tool("harness_accept_run", dict(repo=str(broken), subject="product"), error=True)
    assert old_run["passed"] is False
    original = tool("harness_accept_review", dict(repo=str(broken), reviewers=REVIEWERS))
    assert len(calls) == 2 and original["findingIds"]
    with pytest.raises(ValueError):
        planner_jobs.worker_job_status(workspace, revision)

    session = str(uuid4())
    events = [
        {"type": "thread.started", "thread_id": session},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "SOURCE ONLY"}},
        {"type": "turn.completed"},
    ]
    stderr = (
        "Codex initialized with event: SessionConfiguredEvent { "
        f"session_id: SessionId {{ uuid: {session} }}, thread_id: ThreadId {{ uuid: {session} }}, "
        "forked_from_id: None, parent_thread_id: None, model: "
        '"gpt-6.1-sol", reasoning_effort: Some(Medium) }'
    )
    if not author_metadata:
        stderr = ""
    worker = tmp_path / "source-worker.py"
    worker.write_text(
        "# SOURCE-ONLY actual product repair child\nfrom pathlib import Path\nimport sys\n"
        "Path('A.py').write_text('def valid_id(value): return type(value) is int\\n')\n"
        f"print({chr(10).join(json.dumps(row) for row in events)!r})\n"
        f"print({stderr!r},file=sys.stderr)\n"
    )
    additional_workers = {}
    for identity in (["B"] if second_bead else []) + (["D"] if prerequisite else []):
        later_session = str(uuid4())
        later_events = (
            chr(10).join(json.dumps(row) for row in events).replace(session, later_session)
        )
        later_worker = tmp_path / f"source-worker-{identity}.py"
        later_worker.write_text(
            "# SOURCE-ONLY independent owned source change\n"
            "from pathlib import Path\nimport sys\n"
            f"Path('{identity}.py').write_text('VALUE = 1\\ndef value(): return VALUE\\n')\n"
            f"print({later_events!r})\n"
            f"print({stderr.replace(session, later_session)!r},file=sys.stderr)\n"
        )
        additional_workers[identity] = later_worker

    class Adapter:
        def provider_normal_command(self, **_kwargs):
            assert worker.is_file() and Path(sys.executable).is_file()
            selected_worker = next(
                (
                    script
                    for identity, script in additional_workers.items()
                    if f"implement bead {identity}." in _kwargs["prompt"]
                ),
                worker,
            )
            return ProviderNormalLaunch(
                cli="codex",
                command=(sys.executable, "-B", str(selected_worker)),
                protections=("SOURCE-ONLY process double",),
            )

    adapter_for = worker_setup(worker, workspace) if worker_setup else lambda _: Adapter()
    monkeypatch.setattr(dispatch, "get_adapter", adapter_for)
    source_only_packet_renderer(monkeypatch)
    workers = [
        dict(bead=identity, provider="codex", model="gpt-6.1-sol", effort="medium")
        for identity in ((["D"] if prerequisite else []) + (["A", "B"] if second_bead else ["A"]))
    ]
    source_grant(store, revision, workers, timeout=15, call_cap=len(workers))
    with monkeypatch.context() as boundary:
        popen = planner_jobs.subprocess.Popen

        def controller_handoff(command, *args, **kwargs):
            if "codex_harness.core.planner_jobs" in command:
                return SimpleNamespace(pid=12345)
            return popen(command, *args, **kwargs)

        boundary.setattr(planner_jobs.subprocess, "Popen", controller_handoff)
        tool(
            "harness_worker_start",
            dict(expected_revision=revision, workers=workers, timeout=15, call_cap=len(workers)),
        )
    terminal = planner_jobs.execute_worker_job(workspace, revision)
    assert terminal["status"] == "completed", terminal
    job = tool("harness_worker_status", dict(job_id=revision, include_detail=True))
    run = record_io.read_record_json(Path(job["run"]["recordPath"]))
    repo = Path(run["repo"])
    assert (repo / "A.py").read_bytes() != old_bytes == (broken / "A.py").read_bytes()
    current = tool("harness_accept_run", dict(repo=str(repo), subject="product"))
    assert (
        current["passed"]
        and current["id"] != old_run["id"]
        and current["revision"] != old_run["revision"]
    )
    state = tool("harness_plan_status", {})
    af = next(row for row in state["acceptance"]["findings"] if row["provider"] == "codex")
    (tmp_path / "public-source-process-receipts.json").write_text(
        json.dumps(
            dict(
                sourceOnly=True,
                nativeProviderCalls=0,
                oldRun=old_run,
                currentRun=current,
                oldRepo=str(broken),
                currentRepo=str(repo),
                calls=calls,
                traces=traces,
            ),
            indent=2,
        )
    )
    return store, server, tool, calls, af, current, repo, REVIEWERS, tmp_path


FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
REVIEWERS = [
    {"name": "codex-reviewer", "provider": "codex", "model": "gpt-6.1-sol", "effort": "high"},
    {"name": "claude-reviewer", "provider": "claude", "model": "claude-sonnet-5-5"},
]
COMMAND = '"{python}" "{repo}/tiny.py"'


# Rejected historical Claude output, preserved only as source-regression input.
# Object file SHA256: 9b118672af0d80dca9c721c4e709028c1cef6c5bd94adb14fac29039182ba818
REJECTED_HISTORICAL_CLAUDE_OBJECTS = [
    {
        "finding": "The default data file is ./water.json relative to the current "
        "directory. A first-time user who runs the CLI from a different folder "
        "gets a new empty file. They will see 0 ml (0%) and may think their "
        "data was lost. This matches the locked spec but works against "
        "'reliably'. No scenario covers it, and no success or error message "
        "names the file in use.",
        "node_id": "R2",
        "severity": "minor",
        "suggestion": [
            "Document the cwd-relative default and WATER_CLI_DATA prominently, "
            "for example in usage/help text.",
            "Include the data file path in error messages.",
            "Consider an opt-in way to show the active data path, such as a "
            "`path` command or a note in --help.",
        ],
    },
    {
        "finding": "The read-only failure surfaces a raw OS message. Example: `Error: "
        "[WinError 5] Access is denied: '...\\.water.json.afm1ur_v.tmp' -> "
        "'water.json'`. Exit code, stderr prefix, byte preservation and temp "
        "cleanup all pass. But the message exposes an internal temp-file name "
        "and does not say that the data file cannot be written.",
        "node_id": "R4",
        "severity": "minor",
        "suggestion": [
            "Wrap OSError on save in a friendly message, such as 'Cannot write "
            "<data file>: file is read-only or access denied'.",
            "Do not mention the temp file in the message.",
        ],
    },
    {
        "finding": "Schema and corruption errors such as 'Data must contain goal and "
        "entries' and 'Entries must be a list' do not name the file or say "
        "what the user should do. A first-time user hitting corruption gets no "
        "recovery path. Not overwriting is correct, but there is no guidance "
        "such as 'fix or delete <path>'.",
        "node_id": "R2",
        "severity": "minor",
        "suggestion": [
            "Prefix errors with the data file path.",
            "Add a short hint to repair or move the file.",
        ],
    },
    {
        "finding": "Concurrent invocations are explicitly unsupported. Read-modify-write "
        "is not serialized, so two terminals or a scripted shortcut running "
        "`add` at once can silently lose an entry. This conflicts with "
        "'reliably' and 'no silent data loss', and it is only a plan "
        "assumption. Nothing is surfaced to the user.",
        "node_id": "R2",
        "severity": "minor",
        "suggestion": [
            "State the single-invocation limitation in user-facing docs or help.",
            "Optionally add a simple lock file or retry guard.",
            "Optionally add a scenario that documents the limitation.",
        ],
    },
    {
        "finding": "The evidence does not show what a first-time user sees on discovery "
        "paths: no command, `--help`, an unknown command, or running on Python "
        "older than 3.11. The scenarios cover the happy path and specific bad "
        "values only. Beyond the one-line outputs, there is no README or usage "
        "text in the packet.",
        "node_id": "R4",
        "severity": "minor",
        "suggestion": [
            "Add a scenario for no-args and --help output with exit code and no traceback.",
            "Add a scenario for an unknown command.",
            "Add a short Python 3.11+ requirement or version check message.",
            "Ship a README with the five commands and the data-file behavior.",
        ],
    },
    {
        "finding": "Edge amounts are not exercised. These include huge or exponent values "
        "(`1e400`, `1e9999999`), very large integers, and values just under "
        "0.5 ml. Hostile or accidental input could produce a very large stored "
        "entry, slow parsing, or an unclear error. The spec only says to "
        "reject nonfinite, nonpositive, unknown-unit and zero-rounding values.",
        "node_id": "R1",
        "severity": "minor",
        "suggestion": [
            "Add an upper bound or a bounded-exponent check in parse_amount.",
            "Add tests for large and exponent inputs that expect a clean exit 2.",
        ],
    },
    {
        "finding": "The percentage rounding rule 'general-number, one decimal max' is "
        "under-specified. In scope-1, 489/2000 is exactly 24.45 and displays "
        "as 24.4%. That is correct for float formatting but looks like a "
        "round-down. The scenarios do not distinguish half-up from float "
        "behavior at other boundaries.",
        "node_id": "R4",
        "severity": "minor",
        "suggestion": [
            "Define the percentage rounding mode explicitly.",
            "Add a boundary test for it.",
        ],
    },
    {
        "finding": "The messages `Undid last entry` and `Added N ml` give limited "
        "feedback. Undo does not say which amount was removed. A user who "
        "added several drinks cannot confirm what was reverted without running "
        "`today`. Undo is also limited to today's entries, so a late-night "
        "correction after midnight reports 'Nothing to undo'.",
        "node_id": "R4",
        "severity": "note",
        "suggestion": [
            "Optionally print the removed amount, for example 'Undid last entry (300 ml)'.",
            "Document the today-only undo scope.",
        ],
    },
    {
        "finding": "The packet has limited independent evidence. The implementation is "
        "not included, only stubs and protected tests. Judgment rests on "
        "harness-run scenario output. The read-only atomic-replace scenario "
        "asserts os.name=='nt' and demonstrates only the Windows failure path. "
        "Failure injection elsewhere is mocked. Passing scenarios all use the "
        "same installed copy and the default-cwd data file. Deleted-file "
        "detection is out of scope by design: an absent file reads as empty, "
        "which is a documented limitation.",
        "node_id": "V1",
        "severity": "note",
        "suggestion": [
            "Treat the vision as met for the first-moment flow: today shows 0 "
            "ml (0%) and then 250 ml (12.5%).",
            "Treat reliability on non-Windows platforms and under midnight rollover as unverified.",
            "Optionally add a POSIX read-only-directory scenario.",
            "Optionally add a day-rollover scenario with injected time.",
        ],
    },
]


@pytest.fixture
def project(tmp_path):
    workspace, repo = tmp_path / "planner", tmp_path / "product"
    workspace.mkdir()
    repo.mkdir()
    # A CLI with the exact water-cli defect. A fixture file in its repository
    # must not mask failure from the fresh acceptance working directory.
    (repo / "tiny.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "if not Path('water.json').exists():\n"
        "    print('Error: File not found', file=sys.stderr)\n"
        "    sys.exit(2)\nprint('Added 250 ml.')\n",
        encoding="utf-8",
    )
    (repo / "water.json").write_text("{}", encoding="utf-8")
    with PlanStore(workspace) as store:
        store.import_spine(FIXTURES / "dogfood-tipsplit/spine.json")
        bead = store.spine().node("A")
        bead["parents"].append("R6")
        store.upsert_node(bead)
        vision = store.spine().node("V1")
        vision["fields"].update(
            first_user_moment={
                "title": "Record first glass",
                "steps": [COMMAND],
                "expect": {"stdout": "Added 250 ml.", "exit": 0},
            },
            scope_in=[
                {
                    "title": "Record water",
                    "steps": [COMMAND],
                    "expect": {"stdoutRegex": r"Added \d+ ml\.", "exit": 0},
                }
            ],
        )
        store.upsert_node(vision)
        source_only_vision_lock(store, statement="Approve")
        yield store, repo


def to_accept(store):
    for phase in PHASES[PHASES.index(store.phase()) + 1 :]:
        if store.phase() in ("align", "vision-lock"):
            source_only_confirmation(store, store.phase(), statement="Approve")
        if phase == "roundtable":
            import shutil

            shutil.copytree(
                FIXTURES / "dogfood-tipsplit/template",
                store.workspace / "template",
                dirs_exist_ok=True,
            )
            source_only_b(store, reviewers=REVIEWERS)
        if phase == "publish":
            publication_evidence(store)
        if store.phase() == "publish":
            publication_evidence(store)
            store.publish(by="planner", export_dir=store.workspace / "plan")
        store.advance(phase, by="planner")


def fix_product(repo):
    (repo / "tiny.py").write_text("print('Added 250 ml.')\n", encoding="utf-8")


def review(store, repo, output="[]", **kwargs):
    return acceptance_review(
        store, repo, reviewers=REVIEWERS, runner=lambda *args: (0, output), **kwargs
    )
