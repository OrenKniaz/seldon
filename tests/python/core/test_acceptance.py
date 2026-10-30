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
    source_only_confirmation,
    source_only_vision_lock,
)
from tests.python.core.test_plan_cli import cli

from codex_harness.core import plan_cli
from codex_harness.core.acceptance import (
    _integrated_revision_v1,
    acceptance_review,
    current_run,
    derive_scenarios,
    integrated_revision,
    run_acceptance,
    run_scenarios,
)
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_quality import first_moment_proof
from codex_harness.core.plan_store import PHASES, PlanStore, PlanStoreError
from codex_harness.core.spine import Spine, load_spine

lesson_dispatch = test_dispatch.lesson_dispatch
semantic_plan = test_semantic_wiring.semantic_plan
semantic_acceptance_product = test_semantic_wiring.semantic_acceptance_product


def test_legacy_public_scenarios_metadata_cannot_replace_declared_oracle(project):
    """Protect legacy commands; catches AS-1's false PASS, missed by semantic-only admissions."""
    from codex_harness.core.acceptance import _hash

    store, repo = project
    assert not store.spine().semantic
    (repo / "hello.py").write_text("print('Hello', end='')\n", encoding="utf-8")
    document = {
        "schema": "seldon/scenarios-v1",
        "scenarios": [
            dict(
                id="unrelated-greeting",
                requirementRefs=["R6"],
                contractRefs=["CLI"],
                setup={},
                actions=[dict(id="hello", cli=dict(target="greet", operation="hello", argv=[]))],
                observe=[dict(action="hello", exitEquals=0, stdoutEquals="Hello")],
                verdict="all",
            )
        ],
    }
    scenarios = derive_scenarios(store.locked_acceptance_spine())
    declared = [(s["steps"], s["expect"]) for s in scenarios]
    for scenario in scenarios:
        scenario["semantic"] = [document, {"greet": {"entry": "hello.py", "argv": []}}]
    server = PlannerServer(store.workspace)
    try:
        response = server.handle_line(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=71,
                    method="tools/call",
                    params=dict(
                        name="harness_accept_scenarios",
                        arguments=dict(
                            expected_workspace=str(store.workspace),
                            scenarios=scenarios,
                        ),
                    ),
                )
            )
        )["result"]
        assert not response["isError"], response
    finally:
        server.close()
    saved = store.acceptance_scenarios()
    assert [(s["steps"], s["expect"]) for s in saved] == declared
    result = run_acceptance(store, repo, subject="product")
    assert result["passed"] is False, result
    assert [r["id"] for r in result["results"]] == [s["id"] for s in saved]
    for row in result["results"]:
        assert not row["passed"] and len(row["steps"]) == 1, row
        assert row["steps"][0]["exit"] == 2 and "File not found" in row["steps"][0]["stderr"]
        assert "semantic" not in row
    assert result["scenarioRevision"] == _hash(saved)
    assert current_run(store)["id"] == result["id"]


@pytest.mark.parametrize("passed", [True, False])
def test_semantic_accept_public_owner_and_sync_share_real_oracle(
    semantic_acceptance_product, passed
):
    """Catch async/sync divergence and fake IDs; legacy tests never consume semantic data."""
    store, repo = semantic_acceptance_product
    if not passed:
        (repo / "hello.py").write_text("print('Wrong')\n", encoding="utf-8")
    synchronous = run_acceptance(store, repo, subject="product")
    server = PlannerServer(store.workspace)
    try:
        admitted = server.call_tool("harness_accept_run", dict(repo=str(repo), subject="product"))
        terminal = finish_public_acceptance(server, admitted)["structuredContent"]
        assert terminal["state"] == "completed", terminal
        for result in (synchronous, terminal["result"]):
            assert result["passed"] is passed, result
            assert [r["id"] for r in result["results"]] == ["authored-greeting"]
            assert result["results"][0]["semantic"]["state"] == ("PASS" if passed else "FAIL")
        assert current_run(store)["id"] == terminal["result"]["id"]
    finally:
        server.close()


@pytest.mark.parametrize("oversized", [False, True])
def test_semantic_accept_real_git_inventory_and_prelaunch_refusal(
    semantic_acceptance_product, monkeypatch, oversized
):
    """Protect canonical acceptance: prior fixtures miss Git quota and no-child UNKNOWN holds."""
    from codex_harness.core import semantic_runner

    store, repo = semantic_acceptance_product
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    metadata = repo / ".git" / "inventory-fixture"
    metadata.mkdir()
    harness = repo / ".codex-harness"
    harness.mkdir()
    for index in range(129):
        (metadata / str(index)).touch()
        (harness / str(index)).touch()
        if oversized:
            (repo / f"product-{index}.txt").touch()
    assert (repo / ".git").is_dir()
    assert len(list((repo / ".git").rglob("*"))) > 128
    launch = semantic_runner.run_audit_process
    calls = []

    def observed_launch(*args, **kwargs):
        calls.append(args)
        return launch(*args, **kwargs)

    monkeypatch.setattr(semantic_runner, "run_audit_process", observed_launch)
    server = PlannerServer(store.workspace)
    try:
        admitted = server.call_tool("harness_accept_run", dict(repo=str(repo), subject="product"))
        terminal = finish_public_acceptance(server, admitted)["structuredContent"]
        assert terminal["state"] == "completed", terminal
        result = terminal["result"]
        assert result["passed"] is (not oversized), result
        raw = result["results"][0]["semantic"]
        assert raw["state"] == ("ERROR" if oversized else "PASS"), raw
        assert raw["cleanup"]["quiescenceVerified"] is True
        assert raw["cleanup"]["activeProcessesAfterTeardown"] == 0
        assert "cleanupUncertainty" not in raw
        assert len(calls) == (0 if oversized else 1)
        if oversized:
            assert "Product source exceeds128 entries" in raw["reason"]
            assert raw["cleanup"]["childLaunched"] is False
        assert current_run(store)["id"] == result["id"]
    finally:
        server.close()


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


@pytest.mark.parametrize("uncertain", [False, True])
def test_semantic_accept_error_stops_later_scenario_and_holds_unknown_cleanup(
    semantic_acceptance_product, monkeypatch, uncertain
):
    """Catch ERROR continuation/false termination; legacy cleanup tests bypass the semantic Job."""
    from codex_harness.core import semantic_runner

    store, repo = semantic_acceptance_product
    vision = store.spine().node("V1")
    second = json.loads(json.dumps(vision["fields"]["scenarios"]["scenarios"][0]))
    second["id"] = "must-not-launch"
    vision["fields"]["scenarios"]["scenarios"].append(second)
    store.upsert_node(vision)
    (repo / "hello.py").write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    launch = semantic_runner.run_audit_process
    calls = []

    def observed_launch(*args, **kwargs):
        calls.append(args)
        if uncertain:
            raise OSError("Injected held-Job observation loss; no PASS supplied")
        return launch(*args, **kwargs)

    monkeypatch.setattr(semantic_runner, "run_audit_process", observed_launch)
    server = PlannerServer(store.workspace)
    try:
        admitted = server.call_tool("harness_accept_run", dict(repo=str(repo), subject="product"))
        terminal = finish_public_acceptance(server, admitted)["structuredContent"]
        assert len(calls) == 1
        assert terminal["state"] == ("unknown-interrupted" if uncertain else "completed"), terminal
        assert terminal["progress"][1]["state"] == "unfinished"
        assert current_run(store) is None if uncertain else not terminal["result"]["passed"]
        if uncertain:
            assert terminal["result"] is None
            again = server.call_tool("harness_accept_run", dict(repo=str(repo), subject="product"))
            assert again["structuredContent"]["attemptId"] == terminal["attemptId"]
            assert len(calls) == 1
    finally:
        server.close()


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


@pytest.mark.parametrize(
    "hold",
    ["scenario", "initial-metadata", "terminal-metadata", "deadline-metadata", "completed-status"],
)
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


def test_acceptance_close_stops_next_real_metadata_child(project, tmp_path, monkeypatch):
    """Catch Git launch after owner close; prior stop tests only held scenario execution."""
    import threading

    from codex_harness.core.planner_acceptance import AcceptanceOwner

    store, repo = project
    fix_product(repo)
    for command in [
        ["git", "init", "-q", str(repo)],
        ["git", "-C", str(repo), "add", "."],
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Source fixture",
            "-c",
            "user.email=source@example.invalid",
            "commit",
            "-qm",
            "source fixture",
        ],
    ]:
        subprocess.run(command, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    actual, calls = subprocess.run, []
    entered, release = threading.Event(), threading.Event()

    def metadata_call(command, **kwargs):
        result = actual(command, **kwargs)  # Real first Git process, observed/reaped normally.
        if command[0] == "git":
            calls.append(command)
            if len(calls) == 1:
                entered.set()
                assert release.wait(timeout=8)
        return result

    monkeypatch.setattr(subprocess, "run", metadata_call)
    owner = AcceptanceOwner()
    attempt = owner.start(store, repo, subject="product")
    owner.dispatch_after_flush(store)
    try:
        assert entered.wait(timeout=3)
        owner.close(store)
    finally:
        release.set()
        owner.thread.join(timeout=10)
        assert not owner.thread.is_alive()
        owner.drain(store)
        (tmp_path / "metadata-calls.json").write_text(json.dumps(calls))
    assert len(calls) == 1
    observed = owner.status(store, attempt["attemptId"])
    assert observed["state"] == "unknown-interrupted" and observed["result"] is None
    assert not observed["progress"]


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


@pytest.mark.parametrize("changed", ["product", "scenarios", "vision", "post-capture"])
def test_acceptance_finalization_refuses_changed_captured_inputs(project, changed, monkeypatch):
    """Catch granting async credit to changed inputs; sync tests lack the queued-result gap."""
    import threading

    from codex_harness.core import planner_acceptance
    from codex_harness.core.planner_acceptance import AcceptanceOwner

    store, repo = project
    if changed == "post-capture":
        to_accept(store)
    fix_product(repo)
    entered, release = threading.Event(), threading.Event()
    if changed == "product":
        actual_capture = planner_acceptance.capture_acceptance
        calls = []

        def before_terminal(*args, **kwargs):
            calls.append(1)
            if len(calls) == 3:
                entered.set()
                assert release.wait(timeout=8)
            return actual_capture(*args, **kwargs)

        monkeypatch.setattr(planner_acceptance, "capture_acceptance", before_terminal)
    owner = AcceptanceOwner()
    attempt = owner.start(store, repo, subject="product")
    assert current_run(store) is None
    owner.dispatch_after_flush(store)
    # Let the worker finish but withhold terminal commit for the stale-input interleaving.
    deadline = time.monotonic() + 10
    while owner.thread.is_alive() and time.monotonic() < deadline:
        if changed == "product" and entered.is_set():
            break
        if owner.status(store, attempt["attemptId"])["productCaptured"]:
            owner.thread.join(timeout=0.05)
        else:
            owner.drain(store)
            time.sleep(0.01)
    assert entered.is_set() if changed == "product" else not owner.thread.is_alive()
    if changed in {"product", "post-capture"}:
        (repo / "tiny.py").write_text("print('changed after execution')\n")
    elif changed == "scenarios":
        scenarios = store.acceptance_scenarios()
        scenarios[0]["expect"] = {"exit": 9}
        store.save_acceptance_scenarios(scenarios)
    else:
        vision = store.spine().node("V1")
        vision["text"] += " changed after execution"
        store.upsert_node(vision)
    release.set()
    drain_acceptance(owner, store)
    result = owner.status(store, attempt["attemptId"])
    if changed == "post-capture":
        assert result["state"] == "completed" and result["result"]["passed"]
        assert current_run(store) is None  # Historical captured PASS is not current proof.

        def forbidden(*args, **kwargs):
            pytest.fail("Stale product must be refused before any reviewer launch")

        with pytest.raises(PlanStoreError) as review:
            acceptance_review(store, repo, reviewers=REVIEWERS, runner=forbidden)
        assert review.value.code == "acceptance-run-required"
        with pytest.raises(PlanStoreError) as approval:
            store.accept(by="proxy:source-only", statement="Cannot approve changed product")
        assert approval.value.code == "product-changed"
        from codex_harness.core.plan_quality import quality_report

        assert (
            next(
                row for row in quality_report(store)["checks"] if row["id"] == "first-moment-proof"
            )["claim"]
            != "proved"
        )
        assert not store.status()["acceptance"]["accepted"] and store.phase() == "accept"
        owner.close(store)
        return
    assert result["state"] == "stale" and result["result"] is None
    assert result["progress"]  # Executed results survive without granting current credit.
    assert not store.connection.execute("SELECT 1 FROM acceptance_runs").fetchone()
    owner.close(store)


def test_acceptance_queued_revision_change_prevents_child_launch(project, monkeypatch):
    """Catch launching stale captured code before dispatch; final-only checks miss side effects."""
    from codex_harness.core.planner_acceptance import AcceptanceOwner

    store, repo = project
    fix_product(repo)
    previous = run_acceptance(store, repo, subject="product")
    original = store.acceptance_scenarios()
    owner = AcceptanceOwner()
    attempt = owner.start(store, repo, subject="product")
    changed = json.loads(json.dumps(original))
    changed[0]["expect"] = {"exit": 9}
    store.save_acceptance_scenarios(changed)

    def forbidden(*args, **kwargs):
        pytest.fail("Stale acceptance must not launch a scenario child")

    monkeypatch.setattr("codex_harness.core.planner_acceptance.run_scenarios", forbidden)
    owner.dispatch_after_flush(store)
    assert owner.thread is None
    assert owner.status(store, attempt["attemptId"])["state"] == "stale"
    store.save_acceptance_scenarios(original)
    assert current_run(store) is None  # A failed new admission cannot revive superseded proof.
    assert store.status()["acceptance"]["invalidReason"] == "acceptance-execution-required"
    assert previous["passed"]
    owner.close(store)


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


def test_acceptance_children_disable_bytecode_even_with_caller_override(tmp_path):
    """Catch scenario/descendant caches; EOF and isolation tests never import product code."""
    (tmp_path / "product.py").write_text("VALUE = 1\n")
    command = (
        "\"{python}\" -c \"import os,product; assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'\""
    )
    results = run_scenarios(
        tmp_path,
        [dict(id="cache", title="Cache", steps=[command], expect={"exit": 0})],
        env={"PYTHONDONTWRITEBYTECODE": "0"},
    )
    assert results[0]["passed"], results
    assert not (tmp_path / "__pycache__").exists()


def test_acceptance_children_get_eof_but_explicit_input_redirection_works(tmp_path):
    """Protect noninteractive stdin isolation without dropping declared shell input.

    Existing stdio integration covers protocol liveness; no existing scenario
    checks actual child EOF together with a supported shell redirection.
    """
    source = tmp_path / "input.txt"
    source.write_text("declared input")
    command = '"{python}" -c "import sys; print(repr(sys.stdin.read()))"'
    scenarios = [
        dict(id="eof", title="EOF", steps=[command], expect={"stdout": "''"}),
        dict(
            id="redirect",
            title="Redirect",
            steps=[f'{command} < "{source}"'],
            expect={"stdout": "declared input"},
        ),
    ]
    assert all(result["passed"] for result in run_scenarios(tmp_path, scenarios))


def test_acceptance_whole_run_deadline_holds_later_scenario(tmp_path, monkeypatch):
    """Catch resetting the run budget between scenarios; per-scenario tests miss this gap."""
    from types import SimpleNamespace

    from codex_harness.core import acceptance

    clock = [0.0]
    marker = tmp_path / "must-not-launch"
    monkeypatch.setattr(acceptance, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    scenarios = [
        dict(
            id="first",
            title="first",
            steps=['"{python}" -c "print(\'first\')"'],
            expect={"stdout": "first"},
        ),
        dict(
            id="held",
            title="held",
            steps=[f'"{{python}}" -c "from pathlib import Path; Path(r\'{marker}\').touch()"'],
            expect={"exit": 0},
        ),
    ]

    def elapsed_after_first(result):
        clock[0] = 200  # Deterministic elapsed scheduling/housekeeping, not a supplied verdict.

    results = run_scenarios(tmp_path, scenarios, on_result=elapsed_after_first)
    assert results[0]["passed"]
    assert not results[1]["passed"] and results[1]["state"] == "unfinished"
    assert "Whole-run" in results[1]["error"] and not marker.exists()


@pytest.mark.parametrize("mode", ["self-confirmation", "ok", "reused"])
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
    if prerequisite:
        manifest["beads"]["D"] = {
            "title": "A prerequisite",
            "ownedFiles": ["D.py"],
            "requirements": ["RD"],
            "proofModule": "test_D.py",
        }
        manifest["beads"]["A"]["dependsOn"] = ["D"]
        (plan / "template/D.py").write_text("VALUE = 0\n")
        (plan / "template/tests/test_D.py").write_text(
            "import unittest\nfrom D import VALUE\nclass Proof(unittest.TestCase):\n"
            "    def test_dependency(self):\n        self.assertEqual(VALUE, 1)\n"
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
    # Gate-local TEMP can make canonical Git worktree paths too long on Windows.
    fixture_temp_root = None
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        assert local_appdata, "SOURCE-ONLY fixture needs an existing Windows local Temp root"
        fixture_temp_root = Path(local_appdata) / "Temp"
        assert fixture_temp_root.is_dir(), "SOURCE-ONLY fixture needs an existing local Temp root"
    workspace = Path(tempfile.mkdtemp(prefix="public-af-", dir=fixture_temp_root))
    store, revision = publish_source_store(workspace, plan)
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
            f"Path('{identity}.py').write_text('VALUE = 1\\n')\n"
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
    job = tool("harness_worker_status", dict(job_id=revision))
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


def test_public_assessment_repair_and_durable_ordinary_consumer(tmp_path, monkeypatch, capsys):
    store, server, tool, calls, af, current, repo, reviewers, evidence = (
        public_assessment_repair_fixture(tmp_path, monkeypatch)
    )
    try:
        prefix = ["--workspace", str(store.workspace), "--json"]
        assert plan_cli.main([*prefix, "accept-assess", "--schema"]) == 0
        schema = json.loads(capsys.readouterr().out)["data"]
        assert schema["closureEligible"] is False
        before = list(store.connection.iterdump())
        prepared = tool(
            "harness_accept_assess", {"prepare": {"finding_ids": [af["id"]], "bead_id": "A"}}
        )
        assert list(store.connection.iterdump()) == before and len(calls) == 2
        request = prepared["requestTemplate"]
        assert request["expected_product_run_id"] == current["id"]
        request.update(reviewer=reviewers[0], max_rounds=6, max_total=6, timeout=15)
        request["members"] = [
            dict(
                memberId=af["id"],
                artifactPaths=[prepared["artifacts"][0]["path"]],
                witnessIds=[prepared["witnesses"][0]["id"]],
            )
        ]
        request_file = evidence / "assessment-request.json"
        request_file.write_text(json.dumps(request))
        assert plan_cli.main([*prefix, "accept-assess", "--input", "@" + str(request_file)]) == 0
        submitted = json.loads(capsys.readouterr().out)["data"]
        assert submitted["members"][0]["support"] == "assessed-current" and len(calls) == 3
        selected = submitted["members"][0]["registrationId"]
        assert submitted["closureEligible"] is False
        consumer = submitted["nextAction"]
        assert consumer["mcpTool"] == "harness_accept_review"
        assert consumer["arguments"]["execution_assessment_ids"] == [selected]
        assert consumer["arguments"]["repo"] == str(repo)
        assert "reviewers" not in consumer["arguments"], "No invented ordinary reviewer authority"
        consumed = tool(consumer["mcpTool"], dict(consumer["arguments"], reviewers=reviewers))
        assert consumed["executionSupport"]["registrationIds"] == [selected]
        assert len(calls) == 5
        assert all(
            af["finding"] in call["packet"] and selected in call["packet"] for call in calls[-2:]
        )
        saved = json.loads(
            store.connection.execute(
                "SELECT result_json FROM acceptance_reviews WHERE id=?", (consumed["id"],)
            ).fetchone()[0]
        )
        assert saved["executionSupport"] == consumed["executionSupport"]
        assert store.acceptance_findings()[0]["status"] == "open"
        assert store.status()["acceptance"]["accepted"] is False
        assert (
            store.status()["acceptance"]["executionAssessments"]["members"][0]["support"]
            == "assessed-current"
        )
        (evidence / "public-consumer-final-receipt.json").write_text(
            json.dumps(
                dict(
                    sourceOnly=True,
                    nativeProviderCalls=0,
                    calls=calls,
                    saved=saved,
                    assessment=submitted,
                ),
                indent=2,
            )
        )
    finally:
        store.close()


@pytest.mark.parametrize("transport", ["mcp", "cli"])
def test_explicit_support_enters_real_ordinary_packet_and_saved_review(
    tmp_path, monkeypatch, capsys, transport
):
    """Isolate new downstream capability; existing canonical API supplies setup support."""
    from codex_harness.core import roundtable

    store, server, tool, calls, af, current, repo, reviewers, evidence = (
        public_assessment_repair_fixture(tmp_path, monkeypatch)
    )
    try:
        status = tool("harness_plan_status", {})
        support = roundtable.assess_execution_findings(
            store,
            members=[
                dict(
                    memberId=af["id"], artifactPaths=["A.py"], witnessIds=["test_A.Proof.test_bool"]
                )
            ],
            worker_job_id=status["published_revision"],
            bead_id="A",
            expected_manifest_revision=status["manifest_revision"],
            expected_product_run_id=current["id"],
            reviewer=reviewers[0],
            max_rounds=6,
            max_total=6,
            timeout=15,
        )
        assert support["members"][0]["support"] == "assessed-current", support
        selected = support["members"][0]["registrationId"]
        if transport == "mcp":
            result = tool(
                "harness_accept_review",
                dict(repo=str(repo), reviewers=reviewers, execution_assessment_ids=[selected]),
            )
        else:
            assert (
                plan_cli.main(
                    [
                        "--workspace",
                        str(store.workspace),
                        "--json",
                        "accept-review",
                        "--repo",
                        str(repo),
                        "--execution-assessment",
                        selected,
                        *[
                            arg
                            for reviewer in reviewers
                            for arg in ("--reviewer", json.dumps(reviewer))
                        ],
                    ]
                )
                == 0
            )
            result = json.loads(capsys.readouterr().out)["data"]
        assert result["executionSupport"]["registrationIds"] == [selected]
        assert all(
            af["finding"] in call["packet"] and selected in call["packet"] for call in calls[-2:]
        )
        saved = json.loads(
            store.connection.execute(
                "SELECT result_json FROM acceptance_reviews WHERE id=?", (result["id"],)
            ).fetchone()[0]
        )
        assert saved["executionSupport"] == result["executionSupport"]
    finally:
        store.close()


def test_public_assessment_entry_preserves_empty_acceptance_contract(tmp_path):
    with PlanStore(tmp_path) as store:
        before = list(store.connection.iterdump())
        acceptance = store.status()["acceptance"]
        assert set(acceptance) == {
            "scenarios",
            "lastRunSubject",
            "findings",
            "approvedBy",
            "isProxy",
            "accepted",
            "realUserAccepted",
            "recordedUserAccepted",
            "humanOriginVerified",
            "approvalOrigin",
            "invalidReason",
            "invalidMessage",
            "repo",
            "next",
            "approval",
            "reviews",
            "reviewAttempts",
            "executionAssessments",
        }
        assert acceptance["executionAssessments"]["members"] == []
        assert acceptance["executionAssessments"]["closureEligible"] is False
        assert acceptance["executionAssessments"]["fullVisionHeld"] is True
        assert list(store.connection.iterdump()) == before


def public_current_support(fixture):
    store, server, tool, calls, af, current, repo, reviewers, evidence = fixture
    prepared = tool(
        "harness_accept_assess", {"prepare": {"finding_ids": [af["id"]], "bead_id": "A"}}
    )
    request = prepared["requestTemplate"]
    artifact = next(row["path"] for row in prepared["artifacts"] if row["path"] == "A.py")
    witness = next(row["id"] for row in prepared["witnesses"] if artifact in row["artifactPaths"])
    request.update(
        members=[dict(memberId=af["id"], artifactPaths=[artifact], witnessIds=[witness])],
        reviewer=reviewers[0],
        max_rounds=6,
        max_total=6,
        timeout=15,
    )
    support = tool("harness_accept_assess", {"request": request})
    assert support["members"][0]["support"] == "assessed-current"
    return request, support["members"][0]["registrationId"]


@pytest.mark.parametrize(
    "selected_support", [True, False], ids=["support-requires-choice", "legacy-defaults"]
)
def test_support_requires_explicit_ordinary_reviewers_without_discovery(
    tmp_path, monkeypatch, selected_support
):
    from codex_harness.core import roundtable

    fixture = public_assessment_repair_fixture(tmp_path, monkeypatch)
    store, server, tool, calls, af, current, repo, reviewers, evidence = fixture
    try:
        _, selected = public_current_support(fixture)
        discovery = []

        def controlled_defaults():
            discovery.append("SOURCE ONLY discovery double")
            return reviewers

        monkeypatch.setattr(roundtable, "default_reviewers", controlled_defaults)
        before = list(store.connection.iterdump())
        arguments = dict(repo=str(repo))
        if selected_support:
            arguments["execution_assessment_ids"] = [selected]
        reply = server.call_tool("harness_accept_review", arguments)
        if selected_support:
            assert reply.get("isError") and "explicit" in str(reply).lower(), reply
            assert discovery == [] and len(calls) == 3
            assert list(store.connection.iterdump()) == before
        else:
            assert not reply.get("isError"), reply
            assert len(discovery) == 1 and len(calls) == 5
            saved = json.loads(
                store.connection.execute(
                    "SELECT result_json FROM acceptance_reviews WHERE id=?",
                    (reply["structuredContent"]["id"],),
                ).fetchone()[0]
            )
            assert "executionSupport" not in saved
    finally:
        store.close()


@pytest.mark.parametrize(
    "case", ["unknown", "duplicate", "repo", "disposition", "later-pending", "artifact"]
)
def test_public_selected_support_refuses_before_ordinary_reservation(tmp_path, monkeypatch, case):
    fixture = public_assessment_repair_fixture(tmp_path, monkeypatch)
    store, server, tool, calls, af, current, repo, reviewers, evidence = fixture
    try:
        request, selected = public_current_support(fixture)
        ids = [selected]
        consumer_repo = str(repo)
        if case == "unknown":
            ids = ["0" * 32]
        elif case == "duplicate":
            ids = [selected, selected]
        elif case == "repo":
            consumer_repo = str(evidence / "pre-repair-product")
        elif case == "disposition":
            tool(
                "harness_resolve_finding",
                dict(
                    id=af["id"],
                    status="resolved",
                    resolution="SOURCE ONLY declaration after assessment must stale it",
                ),
            )
        elif case == "later-pending":
            store.register_execution_assessment(**dict(request, timeout=16))
        elif case == "artifact":
            (repo / "A.py").write_text("def valid_id(value): return isinstance(value,int)\n")
        before = list(store.connection.iterdump())
        result = server.call_tool(
            "harness_accept_review",
            dict(repo=consumer_repo, reviewers=reviewers, execution_assessment_ids=ids),
        )
        assert result["isError"] and len(calls) == 3
        assert list(store.connection.iterdump()) == before
        assert (
            store.connection.execute("SELECT count(*) FROM acceptance_reviews").fetchone()[0] == 1
        )
    finally:
        store.close()


def test_public_preparation_choices_and_targeted_refusal_do_not_spend(tmp_path, monkeypatch):
    fixture = public_assessment_repair_fixture(tmp_path, monkeypatch)
    store, server, tool, calls, af, current, repo, reviewers, evidence = fixture
    try:
        before = list(store.connection.iterdump())
        choices = tool("harness_accept_assess", {"prepare": {}})
        assert choices["state"] == "selection_required" and {
            row["id"] for row in choices["choices"]["findings"]
        } == {row["id"] for row in store.acceptance_findings()}
        assert choices["choices"]["beads"] == ["A"] and "requestTemplate" not in choices
        prepared = tool(
            "harness_accept_assess", {"prepare": {"finding_ids": [af["id"]], "bead_id": "A"}}
        )
        assert prepared["executable"] is False and prepared["requestTemplate"]["reviewer"] is None
        request = prepared["requestTemplate"]
        request.update(
            members=[
                dict(memberId=af["id"], artifactPaths=["A.py"], witnessIds=["unrelated.fake.test"])
            ],
            reviewer=reviewers[0],
            max_rounds=6,
            max_total=6,
            timeout=15,
        )
        held = server.call_tool("harness_accept_assess", {"request": request})
        assert held["isError"] and "observation" in str(held["structuredContent"]["holdReasons"])
        assert len(calls) == 2 and list(store.connection.iterdump()) == before
    finally:
        store.close()


def test_missing_actual_author_telemetry_holds_public_prepare_before_capacity(
    tmp_path, monkeypatch
):
    fixture = public_assessment_repair_fixture(tmp_path, monkeypatch, author_metadata=False)
    store, server, tool, calls, af, current, repo, reviewers, evidence = fixture
    try:
        before = list(store.connection.iterdump())
        reply = server.call_tool(
            "harness_accept_assess", {"prepare": {"finding_ids": [af["id"]], "bead_id": "A"}}
        )
        assert reply["isError"]
        held = reply["structuredContent"]
        assert held["state"] == "held" and "configuration-unavailable" in str(held["holdReasons"])
        assert "requestTemplate" not in held and len(calls) == 2
        assert list(store.connection.iterdump()) == before
        assert not store.connection.execute(
            "SELECT 1 FROM events WHERE kind='execution-assessment-reserved'"
        ).fetchone()
    finally:
        store.close()


@pytest.mark.parametrize("invalid_stream", [None, "stdout", "stderr"])
def test_exact_output_witness_preserves_invalid_utf8_evidence(tmp_path, invalid_stream):
    """Actual source-product child bytes must not disappear before exact matching."""
    script = tmp_path / "output.py"
    out = b"\xff[]\n" if invalid_stream == "stdout" else b"[]\n"
    err = b"\xff" if invalid_stream == "stderr" else b""
    script.write_text(f"import os\nos.write(1, {out!r})\nos.write(2, {err!r})\n")
    raw = subprocess.run(
        [sys.executable, "-B", str(script)],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert raw.returncode == 0 and raw.stdout == out and raw.stderr == err
    scenario = dict(
        id="exact-output",
        title="Exact CLI list output",
        steps=['"{python}" -B "{repo}/output.py"'],
        expect={"exit": 0, "stdoutRegex": r"\A\[\]\r?\n\Z", "stderrRegex": r"\A\Z"},
    )
    result = run_scenarios(tmp_path, [scenario])[0]
    assert result["passed"] is (invalid_stream is None), result
    if invalid_stream:
        assert "\ufffd" in result["steps"][0][invalid_stream]


def test_invalid_output_expansion_retains_bound_and_truncation(tmp_path):
    (tmp_path / "output.py").write_text(
        "import os\nfor fd in (1, 2):\n    os.write(fd, b'\\xff' * 70000)\n"
    )
    result = run_scenarios(
        tmp_path,
        [
            dict(
                id="invalid-truncated",
                title="Exact empty output",
                steps=['"{python}" "{repo}/output.py"'],
                expect={"exit": 0, "stdoutRegex": r"\A\Z", "stderrRegex": r"\A\Z"},
            )
        ],
    )[0]
    assert not result["passed"]
    for stream in ("stdout", "stderr"):
        value = result["steps"][0][stream]
        assert "\ufffd" in value and value.endswith("\n[truncated]\n")
        assert len(value.encode("utf-8")) <= 64 * 1024


@pytest.mark.parametrize("mutant", [None, "R1", "R2", "R3", "R4", "R5"])
def test_declared_clause_witnesses_execute_product_and_reject_violations(tmp_path, mutant):
    """Generated source product/mutants, not delivered product or acceptance authority.

    Each explicit scenario is an operator-authored witness for one stated clause.
    Scenario IDs do not create a general requirement-to-witness verification map.
    """
    product = tmp_path / "product"
    product.mkdir()
    (product / "ledger.py").write_text(
        f"MUTANT = {mutant!r}\n"
        + """
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.stdout.reconfigure(newline='\\n')
sys.stderr.reconfigure(newline='\\n')
command = sys.argv[1]
if command == 'list':
    print('debug' if MUTANT == 'R1' else '[]')
elif command == 'id':
    value = json.loads(sys.argv[2])
    valid = isinstance(value, int) if MUTANT == 'R2' else type(value) is int
    if not valid:
        print('invalid id', file=sys.stderr)
        sys.exit(2)
    print(value)
elif command == 'money':
    values = sys.argv[2:]
    print(str(sum(map(float, values))) if MUTANT == 'R3'
          else format(sum(map(Decimal, values)), '.2f'))
elif command == 'read':
    try:
        print(Path(sys.argv[2]).read_text(encoding='utf-8',
              errors='ignore' if MUTANT == 'R4' else 'strict'), end='')
    except UnicodeError:
        print('invalid utf8', file=sys.stderr)
        sys.exit(2)
elif command == 'write':
    target = Path(sys.argv[2])
    staged = target.with_suffix('.tmp')
    try:
        if MUTANT == 'R5':
            target.write_bytes(b'changed')
        staged.write_bytes(b'changed')
        # Deterministic source-fixture I/O failure before the atomic replace.
        if os.environ.get('SOURCE_ONLY_WRITE_FAIL') == '1':
            raise OSError('injected write failure')
        os.replace(staged, target)
    except OSError:
        print('write failed', file=sys.stderr)
        sys.exit(2)
""",
        encoding="utf-8",
    )
    (product / "witness.py").write_text(
        """
import json
import os
import subprocess
import sys
from pathlib import Path

clause, directory = sys.argv[1], Path(sys.argv[2])
directory.mkdir(parents=True, exist_ok=True)
environment = dict(os.environ)
environment.pop('PYTHONPATH', None)
environment['SOURCE_ONLY_WRITE_FAIL'] = '1'
def call(*args):
    return subprocess.run([sys.executable, '-B', '-m', 'ledger', *args],
        cwd=Path(__file__).resolve().parent, env=environment,
        capture_output=True, timeout=10, check=False)
if clause == 'R1':
    result = call('list')
    assert (result.returncode, result.stdout, result.stderr) == (0, b'[]\\n', b''), result
elif clause == 'R2':
    result = call('id', 'true')
    assert (result.returncode, result.stdout, result.stderr) == (2, b'', b'invalid id\\n'), result
    valid = call('id', '7')
    assert (valid.returncode, valid.stdout, valid.stderr) == (0, b'7\\n', b''), valid
elif clause == 'R3':
    result = call('money', '0.10', '0.20')
    assert (result.returncode, result.stdout, result.stderr) == (0, b'0.30\\n', b''), result
elif clause == 'R4':
    path = directory / 'input'
    path.write_bytes(b'\\xff')
    result = call('read', str(path))
    assert (result.returncode, result.stdout, result.stderr) == (2, b'', b'invalid utf8\\n'), result
    path.write_bytes(b'valid\\n')
    valid = call('read', str(path))
    assert (valid.returncode, valid.stdout, valid.stderr) == (0, b'valid\\n', b''), valid
elif clause == 'R5':
    path = directory / 'records'
    path.write_bytes(b'original bytes\\n')
    result = call('write', str(path))
    assert (result.returncode, result.stdout, result.stderr) == (2, b'', b'write failed\\n'), result
    assert path.read_bytes() == b'original bytes\\n', path.read_bytes()
print(json.dumps({'clause': clause, 'actualChildExit': result.returncode,
                  'stdoutHex': result.stdout.hex(), 'stderrHex': result.stderr.hex()}))
""",
        encoding="utf-8",
    )
    clauses = {
        "R1": "List records",
        "R2": "Reject boolean IDs",
        "R3": "Keep exact amounts",
        "R4": "Reject invalid UTF-8 input",
        "R5": "Preserve bytes after failed write",
    }
    scenarios = [
        dict(
            id=key,
            title=title,
            steps=[f'"{{python}}" -B "{{repo}}/witness.py" {key} "{{data}}"'],
            expect={"exit": 0, "stdoutRegex": f'"clause": "{key}"', "stderrRegex": r"\A\Z"},
        )
        for key, title in clauses.items()
    ]
    results = run_scenarios(product, scenarios)
    assert {row["id"]: row["passed"] for row in results} == {
        key: key != mutant for key in clauses
    }, results
    for row in results:
        assert len(row["steps"]) == 1
        if row["id"] == mutant:
            assert "AssertionError" in row["steps"][0]["stderr"]
        else:
            observed = json.loads(row["steps"][0]["stdout"])
            assert observed["clause"] == row["id"]
    print(json.dumps(dict(sourceOnly=True, mutant=mutant, witnesses=results)))


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_revision_hashes_flagged_tracked_bytes(tmp_path, flag):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init")
    source = tmp_path / "app.py"
    source.write_text("before")
    git("add", "app.py")
    git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", "fixture")
    git("update-index", flag, "app.py")
    before = integrated_revision(tmp_path)
    source.write_text("after")
    assert integrated_revision(tmp_path) != before


def test_clean_state_hides_ambient_xdg_markers(tmp_path, monkeypatch):
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    (ambient / "marker").touch()
    for key in ("XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(key, str(ambient))
    (tmp_path / "check.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "for key in ('XDG_STATE_HOME', 'XDG_RUNTIME_DIR'):\n"
        "    assert not (Path(os.environ[key]) / 'marker').exists()\n"
    )
    scenario = {
        "id": "clean",
        "title": "clean",
        "steps": ['"{python}" "{repo}/check.py"'],
        "expect": {"exit": 0},
    }
    assert run_scenarios(tmp_path, [scenario])[0]["passed"]


def test_timeout_kills_grandchild_with_inherited_pipes(tmp_path, monkeypatch):
    original_run = subprocess.run
    original_communicate = subprocess.Popen.communicate

    def run(*args, **kwargs):
        if kwargs.get("shell"):
            kwargs["timeout"] = 1
        return original_run(*args, **kwargs)

    def communicate(self, *args, **kwargs):
        if 3 < kwargs.get("timeout", 0) <= 60:
            kwargs["timeout"] = 1
        return original_communicate(self, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
    (tmp_path / "spawn.py").write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(4)'])\n"
        "print('started', flush=True)\ntime.sleep(4)\n"
    )
    scenario = {
        "id": "timeout",
        "title": "timeout",
        "steps": ['"{python}" "{repo}/spawn.py"'],
        "expect": {"exit": 0},
    }
    started = time.monotonic()
    result = run_scenarios(tmp_path, [scenario])[0]
    assert time.monotonic() - started < 3
    assert not result["passed"]
    assert "started" in result["steps"][0]["stdout"]


def test_scenario_output_is_capped_per_stream(tmp_path):
    (tmp_path / "noisy.py").write_text(
        "import os\nfor fd in (1,2):\n    os.write(fd, b'output:' + b'x' * 100000)\n"
    )
    scenario = {
        "id": "noisy",
        "title": "noisy",
        "steps": ['"{python}" "{repo}/noisy.py"'],
        "expect": {"exit": 0},
    }
    result = run_scenarios(tmp_path, [scenario])[0]
    assert result["passed"]
    for stream in ("stdout", "stderr"):
        output = result["steps"][0][stream]
        assert output.startswith("output:")
        assert len(output.encode("utf-8")) <= 64 * 1024
        assert output.endswith("\n[truncated]\n")


def test_acceptance_invalid_after_vision_edit_and_restore(project):
    store, repo = project
    to_accept(store)
    fix_product(repo)
    run_acceptance(store, repo, subject="product")
    source_only_native_review(store, repo)
    store.accept(by="user", statement="Accept")
    original = store.spine().node("V1")
    changed = json.loads(json.dumps(original))
    changed["text"] += " changed"
    store.upsert_node(changed)
    store.upsert_node(original)
    with pytest.raises(PlanStoreError) as error:
        store.accept(by="user", statement="Accept again")
    status = store.status()["acceptance"]
    assert not status["accepted"]
    assert status["invalidReason"] == "vision-unlocked"
    assert error.value.code == "acceptance-vision-unlocked"


def test_acceptance_review_packet_bound_labels(project):
    from tests.python.core.test_roundtable import reviewer_stream

    store, repo = project
    fix_product(repo)
    run_acceptance(store, repo, subject="product")
    item = {"node_id": "V1", "severity": "note", "finding": "Review note", "suggestion": ""}
    result = acceptance_review(
        store,
        repo,
        reviewers=REVIEWERS,
        runner=lambda launch, *args: (0, reviewer_stream(launch.cli, [item])),
    )
    assert result["packetBound"] is False
    assert all(reviewer["packetBound"] is False for reviewer in result["reviewers"])
    assert all(item["packetBound"] is False for item in store.acceptance_findings())
    assert store.status()["acceptance"]["reviews"][0]["packetBound"] is False
    from codex_harness.core.planner_views import review_card

    text = review_card(result)
    assert "shards 1/1; packetBound: no; 1 findings" in text
    recorded = store.review(result["id"])
    assert all(row["findingCounts"] == {"open": 1} for row in recorded["reviewers"])
    assert "estimated tokens" in review_card(recorded, details=True)


def test_accept_run_cards_keep_failed_evidence(project):
    store, repo = project
    server = PlannerServer(store.workspace)
    try:
        failed = server.call_tool("harness_accept_run", {"repo": str(repo), "subject": "product"})
        failed = finish_public_acceptance(server, failed)
        assert failed["isError"] and not failed["structuredContent"]["result"]["passed"]
        text = failed["content"][0]["text"]
        assert "**0/2 passed** · product · failing:" in text and "exit 2" in text
        fix_product(repo)
        passed = server.call_tool("harness_accept_run", {"repo": str(repo), "subject": "oracle"})
        passed = finish_public_acceptance(server, passed)
        assert not passed["isError"]
        assert "**2/2 scenarios passed** · oracle dry run" in passed["content"][0]["text"]
    finally:
        server.close()


def test_failed_acceptance_review_card_uses_current_failure(project, monkeypatch):
    store, repo = project
    fix_product(repo)
    run_acceptance(store, repo, subject="product")
    monkeypatch.setattr("codex_harness.core.roundtable._run", lambda *args: (7, "broken report"))
    monkeypatch.setattr("codex_harness.core.roundtable._executable", lambda *args: "test-provider")
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool(
            "harness_accept_review", {"repo": str(repo), "reviewers": REVIEWERS}
        )
        assert reply["isError"]
        assert "shards 0/1" in reply["content"][0]["text"]
        assert "error: Reviewer exited with code 7" in reply["content"][0]["text"]
        store.connection.execute("DELETE FROM acceptance_runs")
        reply = server.call_tool(
            "harness_accept_review", {"repo": str(repo), "reviewers": REVIEWERS}
        )
        assert reply["isError"] and "structuredContent" not in reply
        assert "shards 0/1" not in reply["content"][0]["text"]
    finally:
        server.close()


FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
REVIEWERS = [
    {"name": "codex-reviewer", "provider": "codex"},
    {"name": "claude-reviewer", "provider": "claude"},
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
        source_only_vision_lock(store, statement="Lock this vision")
        yield store, repo


def to_accept(store):
    for phase in PHASES[PHASES.index(store.phase()) + 1 :]:
        if store.phase() in ("align", "vision-lock"):
            source_only_confirmation(store, store.phase(), statement="Confirm this vision")
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


def source_only_native_review(store, repo):
    """Fresh native-shaped guard fixture, never a provider run or relabelled mock."""
    run = current_run(store)
    assert run and run["passed"] and Path(run["repo"]) == Path(repo).resolve()
    evidence = {
        "id": "source-only-counterfactual-" + uuid4().hex,
        "stage": "acceptance",
        "runId": run["id"],
        "isMocked": False,
        "sourceOnlyCounterfactual": True,
        "packetBound": False,
        "reviewers": [
            {
                "provider": provider,
                "shards": 1,
                "shardDetails": [
                    {"isMocked": False, "didRunProvider": True, "exitCode": 0, "invalid": 0}
                ],
            }
            for provider in ("codex", "claude")
        ],
    }
    # These flags describe the counterfactual schema under test. No existing
    # injected review is rewritten and no admission guard is replaced.
    store.connection.execute(
        "INSERT INTO acceptance_reviews VALUES (?, ?)", (evidence["id"], json.dumps(evidence))
    )
    return {**evidence, "findings": []}


def legacy_acceptance(store, repo, monkeypatch, by="proxy:Opus"):
    """Canonical run/approval with SOURCE-ONLY review and the historical hash."""
    to_accept(store)
    fix_product(repo)
    with monkeypatch.context() as legacy:
        legacy.setattr("codex_harness.core.acceptance.integrated_revision", _integrated_revision_v1)
        run = run_acceptance(store, repo, subject="product")
        original_review = source_only_native_review(store, repo)
        approval = store.accept(by=by, statement="Original acceptance words")
    # Historical runs had no algorithm tag. Keep their original v1 digest.
    run.pop("revisionAlgorithm")
    store.connection.execute(
        "UPDATE acceptance_runs SET result_json=? WHERE id=?", (json.dumps(run), run["id"])
    )
    return run, original_review, approval


@pytest.mark.parametrize(
    "by,surface", [("user", "store"), ("proxy:Opus", "cli"), ("opus-user-proxy", "mcp")]
)
def test_legacy_rebind_preserves_identity_and_review_references(project, monkeypatch, by, surface):
    store, repo = project
    old, original_review, original_approval = legacy_acceptance(store, repo, monkeypatch, by)
    status = store.status()["acceptance"]
    assert status["invalidReason"] == "revision-algorithm-upgraded"
    assert not status["accepted"] and current_run(store) is None
    assert "v1 product revision still matches" in status["invalidMessage"]
    output = cli(store.workspace, "status", machine=True)
    assert "algorithm was upgraded" in output["text"]
    assert "accept-rebind" in output["next"]
    if surface == "cli":
        result = cli(store.workspace, "accept-rebind", "--repo", str(repo), machine=True)["data"]
    elif surface == "mcp":
        server = PlannerServer(store.workspace)
        try:
            status_reply = server.call_tool("harness_plan_status", {})
            assert "algorithm was upgraded" in status_reply["content"][0]["text"]
            assert "Next: harness_accept_rebind" in status_reply["content"][0]["text"]
            reply = server.call_tool("harness_accept_rebind", {"repo": str(repo)})
            assert not reply["isError"], reply
            result = reply["structuredContent"]
            assert reply["content"][0]["text"].endswith("Next: harness_plan_status")
        finally:
            server.close()
    else:
        result = store.accept_rebind(repo)
    assert result["run"]["revisionAlgorithm"] == "tree-bytes-v2"
    assert result["run"]["revision"] == integrated_revision(repo)
    assert result["by"] == "harness" and result["reboundFrom"] == old["id"]
    assert result["reason"] == "revision algorithm upgraded; product unchanged"
    rebound_approval = result["approval"]
    assert rebound_approval["by"] == by
    assert rebound_approval["isProxy"] is original_approval["isProxy"]
    assert rebound_approval["statement"] == original_approval["statement"]
    assert rebound_approval["originalApprovalId"] == original_approval["id"]
    assert rebound_approval["rebind"]["by"] == "harness"
    assert result["reviews"][0]["originalReviewId"] == original_review["id"]
    assert result["reviews"][0]["by"] == "harness"
    assert "reviewers" not in result["reviews"][0]  # No invented new review.
    status = store.status()["acceptance"]
    assert status["accepted"] and status["invalidReason"] is None
    assert status["approvedBy"] == by and status["isProxy"] is original_approval["isProxy"]
    assert status["recordedUserAccepted"] is (by == "user")
    assert status["approval"]["reboundFrom"] == old["id"]
    persisted_review = json.loads(
        store.connection.execute(
            "SELECT result_json FROM acceptance_reviews WHERE id=?", (original_review["id"],)
        ).fetchone()[0]
    )
    assert persisted_review == {
        key: value for key, value in original_review.items() if key != "findings"
    }
    persisted_approval = json.loads(
        store.connection.execute(
            "SELECT result_json FROM acceptance_approvals WHERE id=?", (original_approval["id"],)
        ).fetchone()[0]
    )
    assert persisted_approval == original_approval
    with PlanStore(store.workspace) as reopened:
        assert reopened.status()["acceptance"]["accepted"]
        assert reopened.status()["acceptance"]["approval"] == rebound_approval
    with pytest.raises(PlanStoreError) as error:
        store.accept_rebind(repo)
    assert error.value.code == "acceptance-rebind-not-required"


@pytest.mark.parametrize("legacy", [True, False])
def test_changed_product_refuses_rebind_without_new_evidence(project, monkeypatch, legacy):
    store, repo = project
    if legacy:
        legacy_acceptance(store, repo, monkeypatch)
    else:
        to_accept(store)
        fix_product(repo)
        run = run_acceptance(store, repo, subject="product")
        assert run["revisionAlgorithm"] == "tree-bytes-v2"
        source_only_native_review(store, repo)
        store.accept(by="user", statement="Accept")
    (repo / "tiny.py").write_text("raise SystemExit(2)\n")
    assert store.status()["acceptance"]["invalidReason"] == "product-changed"
    counts = [
        store.connection.execute(f"SELECT count(*) FROM acceptance_{table}").fetchone()[0]
        for table in ("runs", "reviews", "approvals")
    ]
    output = cli(store.workspace, "accept-rebind", "--repo", str(repo), machine=True, code=2)
    assert "product-changed" in output["text"] and "\nNext:" not in output["text"]
    assert "accept-run" in output["next"]
    assert counts == [
        store.connection.execute(f"SELECT count(*) FROM acceptance_{table}").fetchone()[0]
        for table in ("runs", "reviews", "approvals")
    ]
    assert not output["status"]["acceptance"]["accepted"]


@pytest.mark.parametrize("legacy", [True, False])
def test_ancestor_only_saved_binding_cannot_carry_approval(project, tmp_path, monkeypatch, legacy):
    """Use real canonical run/review/approval writers with the historical input preimage."""
    import hashlib

    from tests.python.core.test_product_revision_scope import git, git_parent

    from codex_harness.core import acceptance

    store, _ = project
    parent = git_parent(tmp_path)
    product = parent / "work" / "product"
    product.mkdir(parents=True)
    to_accept(store)
    fix_product(product)

    def old_revision(repo):
        # The published functions incorrectly chose this ancestor HEAD and
        # empty Git listing. This is the exact preimage witnessed by real Git.
        assert Path(git(repo, "rev-parse", "--show-toplevel")) == parent
        assert git(repo, "ls-files", "--cached", "--others", "--exclude-standard") == ""
        value = {"head": git(repo, "rev-parse", "HEAD"), "files": []}
        if legacy:
            value["diff"] = hashlib.sha256(b"").hexdigest()
        else:
            value["algorithm"] = "tree-bytes-v2"
        return acceptance._hash(value)

    with monkeypatch.context() as historical:
        historical.setattr(acceptance, "integrated_revision", old_revision)
        run = run_acceptance(store, product, subject="product")
        source_only_native_review(store, product)
        approval = store.accept(by="user", statement="Recorded before scope repair")
    if legacy:
        run.pop("revisionAlgorithm")
        store.connection.execute(
            "UPDATE acceptance_runs SET result_json=? WHERE id=?", (json.dumps(run), run["id"])
        )
    counts = [
        store.connection.execute(f"SELECT count(*) FROM acceptance_{table}").fetchone()[0]
        for table in ("runs", "reviews", "approvals")
    ]
    status = store.status()["acceptance"]
    assert status["invalidReason"] == "product-changed"
    assert not status["accepted"] and status["approvedBy"] is None
    assert current_run(store) is None
    with pytest.raises(PlanStoreError) as error:
        store.accept_rebind(product)
    assert error.value.code == "product-changed"
    assert counts == [
        store.connection.execute(f"SELECT count(*) FROM acceptance_{table}").fetchone()[0]
        for table in ("runs", "reviews", "approvals")
    ]
    recorded = store.connection.execute(
        "SELECT result_json FROM acceptance_approvals WHERE id=?", (approval["id"],)
    ).fetchone()[0]
    assert json.loads(recorded) == approval


@pytest.mark.parametrize("missing_bindings", [False, True])
def test_rebind_failed_scenario_retains_only_invalid_original_approval(
    project, monkeypatch, missing_bindings
):
    store, repo = project
    to_accept(store)
    (repo / "tiny.py").write_text(
        "import os,sys\nprint('Added 250 ml.')\nsys.exit(int(os.environ.get('REBIND_FAIL', '0')))\n"
    )
    with monkeypatch.context() as legacy:
        legacy.setattr("codex_harness.core.acceptance.integrated_revision", _integrated_revision_v1)
        old = run_acceptance(store, repo, subject="product")
        source_only_native_review(store, repo)
        approval = store.accept(by="proxy:Opus", statement="Accept")
    old.pop("revisionAlgorithm")
    store.connection.execute(
        "UPDATE acceptance_runs SET result_json=? WHERE id=?", (json.dumps(old), old["id"])
    )
    if missing_bindings:
        legacy_scenario_bindings(store, old)
    monkeypatch.setenv("REBIND_FAIL", "2")
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool("harness_accept_rebind", {"repo": str(repo)})
        assert reply["isError"]
        text = reply["content"][0]["text"]
        assert "acceptance-scenarios-required" in text and "Next: harness_accept_run" in text
    finally:
        server.close()
    status = store.status()["acceptance"]
    assert not status["accepted"] and status["invalidReason"] == "acceptance-scenarios-required"
    assert status["approval"] == approval
    assert all(item["lastResult"] == "fail" for item in status["scenarios"])
    assert len(status["reviews"]) == 1
    assert store.connection.execute("SELECT count(*) FROM acceptance_approvals").fetchone()[0] == 1
    if missing_bindings:
        assert all(item["nodeRevisionsBackfilled"] for item in store.acceptance_scenarios())


@pytest.mark.parametrize("changed", ["plan", "scenarios", "vision"])
def test_rebind_refuses_other_stale_bindings(project, monkeypatch, changed):
    store, repo = project
    legacy_acceptance(store, repo, monkeypatch)
    if changed == "scenarios":
        scenarios = store.acceptance_scenarios()
        scenarios[0]["expect"] = {"stdout": "Something else"}
        store.save_acceptance_scenarios(scenarios)
    else:
        node = store.spine().node("D3" if changed == "plan" else "V1")
        node["text"] += " changed"
        store.upsert_node(node)
    reason = "vision-unlocked" if changed == "vision" else changed + "-changed"
    assert store.status()["acceptance"]["invalidReason"] == reason
    with pytest.raises(PlanStoreError) as error:
        store.accept_rebind(repo)
    assert error.value.code == reason and "Next:" in str(error.value)


@pytest.mark.parametrize("when", ["during-scenarios", "after-run"])
def test_product_change_during_rebind_never_carries_approval(project, monkeypatch, when):
    from codex_harness.core import acceptance

    store, repo = project
    _, _, approval = legacy_acceptance(store, repo, monkeypatch)
    method = "run_scenarios" if when == "during-scenarios" else "run_acceptance"
    original = getattr(acceptance, method)

    def changes_product(*args, **kwargs):
        result = original(*args, **kwargs)
        (repo / "new-code.py").write_text("print('changed')\n")
        return result

    monkeypatch.setattr(acceptance, method, changes_product)
    with pytest.raises(PlanStoreError) as error:
        store.accept_rebind(repo)
    assert error.value.code == "product-changed" and "Next:" in str(error.value)
    status = store.status()["acceptance"]
    assert not status["accepted"] and status["approval"] == approval
    assert len(status["reviews"]) == 1


def test_rebind_audit_and_approval_are_atomic(project, monkeypatch):
    import sqlite3

    store, repo = project
    _, _, approval = legacy_acceptance(store, repo, monkeypatch)
    store.connection.execute(
        "CREATE TRIGGER reject_rebound BEFORE INSERT ON acceptance_approvals "
        "BEGIN SELECT RAISE(ABORT, 'approval write failed'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="approval write failed"):
        store.accept_rebind(repo)
    status = store.status()["acceptance"]
    assert not status["accepted"] and status["approval"] == approval
    assert len(status["reviews"]) == 1
    assert not store.connection.execute(
        "SELECT 1 FROM events WHERE kind='acceptance-rebound'"
    ).fetchone()


def legacy_scenario_bindings(store, old):
    from codex_harness.core.acceptance import _hash

    scenarios = store.acceptance_scenarios()
    for item in scenarios:
        for key in ("status", "executable", "nodeRevisions"):
            item.pop(key, None)
        store.connection.execute(
            "UPDATE acceptance_scenarios SET scenario_json=? WHERE id=?",
            (json.dumps(item), item["id"]),
        )
    old["scenarioRevision"] = _hash(scenarios)
    store.connection.execute(
        "UPDATE acceptance_runs SET result_json=? WHERE id=?", (json.dumps(old), old["id"])
    )
    return scenarios


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_legacy_matching_scenarios_backfill_and_rebind(project, monkeypatch, surface):
    store, repo = project
    old, original_review, approval = legacy_acceptance(store, repo, monkeypatch)
    scenarios = legacy_scenario_bindings(store, old)
    status = store.status()["acceptance"]
    assert status["invalidReason"] == "legacy-unbound" and not status["accepted"]
    assert current_run(store) is None
    assert "raw hash still matches" in status["invalidMessage"]
    assert "harness_accept_rebind" in status["next"]
    # Status projection must not back-fill stored records or rewrite the run.
    assert all("nodeRevisions" not in item for item in scenarios)
    assert all(
        "nodeRevisions" not in json.loads(row[0])
        for row in store.connection.execute("SELECT scenario_json FROM acceptance_scenarios")
    )
    if surface == "cli":
        output = cli(store.workspace, "status", machine=True)
        assert "accept-rebind" in output["next"] and "legacy scenarios" in output["text"]
        result = cli(store.workspace, "accept-rebind", "--repo", str(repo), machine=True)["data"]
    else:
        server = PlannerServer(store.workspace)
        try:
            status_reply = server.call_tool("harness_plan_status", {})
            assert "Next: harness_accept_rebind" in status_reply["content"][0]["text"]
            reply = server.call_tool("harness_accept_rebind", {"repo": str(repo)})
            assert not reply["isError"], reply
            result = reply["structuredContent"]
        finally:
            server.close()
    assert result["run"]["passed"] and result["run"]["revisionAlgorithm"] == "tree-bytes-v2"
    assert result["approval"]["by"] == approval["by"]
    assert result["approval"]["isProxy"] is approval["isProxy"]
    assert result["approval"]["reboundFrom"] == old["id"]
    assert result["reviews"][0]["originalReviewId"] == original_review["id"]
    assert "reviewers" not in result["reviews"][0]
    for item in store.acceptance_scenarios():
        assert item["nodeRevisions"] == {"V1": store.spine().revision("V1")}
        assert item["nodeRevisionsBackfilled"] is True
        assert item["nodeRevisionsBackfilledAt"] and item["by"] == "harness"
        assert item["executable"] and item["status"] == "planned"
    persisted_old = json.loads(
        store.connection.execute(
            "SELECT result_json FROM acceptance_runs WHERE id=?", (old["id"],)
        ).fetchone()[0]
    )
    assert persisted_old == old
    assert store.status()["acceptance"]["accepted"]
    with PlanStore(store.workspace) as reopened:
        assert reopened.status()["acceptance"]["accepted"]
        assert reopened.status()["acceptance"]["isProxy"] is True


def test_legacy_mismatched_scenarios_refuse_without_backfill(project, monkeypatch):
    store, repo = project
    old, _, approval = legacy_acceptance(store, repo, monkeypatch)
    scenarios = legacy_scenario_bindings(store, old)
    scenarios[0]["expect"] = {"exit": 2}
    store.connection.execute(
        "UPDATE acceptance_scenarios SET scenario_json=? WHERE id=?",
        (json.dumps(scenarios[0]), scenarios[0]["id"]),
    )
    assert store.status()["acceptance"]["invalidReason"] == "scenarios-changed"
    with pytest.raises(PlanStoreError) as error:
        store.accept_rebind(repo)
    assert error.value.code == "scenarios-changed" and "Next:" in str(error.value)
    assert store.status()["acceptance"]["approval"] == approval
    assert all(
        "nodeRevisionsBackfilled" not in json.loads(row[0])
        for row in store.connection.execute("SELECT scenario_json FROM acceptance_scenarios")
    )
    assert store.connection.execute("SELECT count(*) FROM acceptance_runs").fetchone()[0] == 1


def test_legacy_scenarios_changed_product_refuses_without_backfill(project, monkeypatch):
    store, repo = project
    old, _, approval = legacy_acceptance(store, repo, monkeypatch)
    legacy_scenario_bindings(store, old)
    (repo / "tiny.py").write_text("raise SystemExit(2)\n")
    assert store.status()["acceptance"]["invalidReason"] == "product-changed"
    with pytest.raises(PlanStoreError) as error:
        store.accept_rebind(repo)
    assert error.value.code == "product-changed"
    assert store.status()["acceptance"]["approval"] == approval
    assert all("nodeRevisions" not in item for item in store.acceptance_scenarios())


def test_first_moment_watercli_gap_and_clean_link():
    spine = load_spine(FIXTURES / "dogfood-watercli/spine.json")
    status, evidence, advice = first_moment_proof(spine)
    assert status == "fail"
    assert advice == "add an e2e scenario of the first user moment from a clean install"
    assert evidence["warnings"] and "R4" in evidence["edgeCaseIds"]
    data = spine.to_dict()
    test = next(node for node in data["nodes"] if node["id"] == "T_E2E")
    test["fields"].update(firstMoment=True, cleanState=True)
    assert first_moment_proof(Spine(data))[0] == "pass"
    test["fields"]["cleanState"] = False
    assert first_moment_proof(Spine(data))[0] == "fail"
    requirement = next(node for node in data["nodes"] if node["id"] == "R11")
    requirement["text"] += " " + spine.node("V1")["fields"]["first_user_moment"]
    requirement["fields"]["cleanState"] = True
    del test["fields"]["firstMoment"]
    assert first_moment_proof(Spine(data))[0] == "pass"


def test_derive_covers_locked_moment_and_each_scope_item(project):
    store, _ = project
    scenarios = derive_scenarios(store.locked_acceptance_spine())
    assert [s["id"] for s in scenarios] == ["first-moment", "scope-1"]
    assert all(set(s) == {"id", "title", "given", "steps", "expect"} for s in scenarios)
    store.save_acceptance_scenarios(scenarios)
    path = store.workspace
    with PlanStore(path) as reopened:
        assert all(
            all(saved[key] == value for key, value in scenario.items())
            for saved, scenario in zip(reopened.acceptance_scenarios(), scenarios, strict=True)
        )
        assert all(item["executable"] for item in reopened.acceptance_scenarios())
    with pytest.raises(PlanStoreError, match="Cover first-moment"):
        store.save_acceptance_scenarios(scenarios[:1])


def test_derive_scenarios_reads_explicit_acceptance_spine_node(project):
    store, _ = project
    data = store.locked_acceptance_spine().to_dict()
    fields = {
        "given": "A clean install",
        "steps": [COMMAND, {"command": COMMAND, "expect": {"exit": 0}}],
        "expect": {"stdout": "Total: 0"},
        "firstMoment": True,
        "cleanState": True,
    }
    data["nodes"].append(
        {
            "id": "first-moment",
            "kind": "acceptance",
            "title": "Explicit first use",
            "text": "Check the real first command",
            "parents": ["V1"],
            "fields": fields,
        }
    )
    scenarios = derive_scenarios(Spine(data))
    assert scenarios[0] == {"id": "first-moment", "title": "Explicit first use", **fields}
    assert [scenario["id"] for scenario in scenarios] == ["first-moment", "scope-1"]


def test_clean_state_reproduces_missing_file_failure(project):
    store, repo = project
    results = run_scenarios(repo, derive_scenarios(store.locked_acceptance_spine()), {})
    assert all(not result["passed"] for result in results)
    step = results[0]["steps"][0]
    assert step["stdout"] == ""
    assert step["stderr"].strip() == "Error: File not found"
    assert step["exit"] == 2 and not step["passed"]
    assert (repo / "water.json").read_text() == "{}"


def test_scenarios_have_independent_home_data_and_cwd(project, monkeypatch):
    _, repo = project
    ambient = repo / "ambient.json"
    ambient.write_text("secret data", encoding="utf-8")
    monkeypatch.setenv("WATER_CLI_DATA", str(ambient))
    (repo / "isolated.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "assert not Path(os.environ['WATER_CLI_DATA']).exists()\n"
        "assert not Path.home().joinpath('marker').exists()\n"
        "assert not Path('marker').exists()\n"
        "Path.home().joinpath('marker').touch()\nPath('marker').touch()\n"
        "print('isolated')\n",
        encoding="utf-8",
    )
    scenarios = [
        {
            "id": str(i),
            "title": "Fresh state",
            "given": "empty",
            "steps": ['"{python}" "{repo}/isolated.py"'],
            "expect": {"stdout": "isolated", "exit": 0},
        }
        for i in range(2)
    ]
    assert all(result["passed"] for result in run_scenarios(repo, scenarios, os.environ))
    assert ambient.read_text() == "secret data"


@pytest.mark.parametrize("finding_status", ["resolved", "deferred"])
def test_gate_failure_findings_proxy_and_revision_drift(project, finding_status):
    store, repo = project
    to_accept(store)
    run_acceptance(store, repo, subject="product")
    with pytest.raises(PlanStoreError) as error:
        store.accept(by="user", statement="Accept")
    assert error.value.code == "acceptance-scenarios-required"
    fix_product(repo)
    passed = run_acceptance(store, repo, subject="product")
    assert passed["passed"]
    with pytest.raises(PlanStoreError) as error:
        store.accept(by="user", statement="Accept")
    assert error.value.code == "acceptance-review-required"
    finding = {
        "node_id": "V1",
        "severity": "major",
        "finding": "First-use gap",
        "suggestion": "Repair the first command",
    }
    result = review(store, repo, json.dumps([finding]))
    assert all(f["stage"] == "acceptance" for f in result["findings"])
    with pytest.raises(PlanStoreError) as error:
        store.accept(by="user", statement="Accept")
    assert error.value.code == "acceptance-findings-open"
    for item in store.acceptance_findings():
        with pytest.raises(ValueError):
            store.resolve_acceptance_finding(item["id"], status=finding_status, resolution=" ")
        store.resolve_acceptance_finding(
            item["id"], status=finding_status, resolution="Explicit disposition of this gap"
        )
    # Disposing findings cannot turn the injected review into native evidence.
    assert result["isMocked"] is True
    with pytest.raises(PlanStoreError) as error:
        store.accept(by="proxy:Opus", statement="SOURCE-ONLY mocked review must refuse")
    assert error.value.code == "acceptance-review-required"
    source_only_native_review(store, repo)
    with pytest.raises(PlanStoreError, match="explicitly labelled proxy"):
        store.accept(by="agent", statement="Looks good")
    approval = store.accept(by="proxy:Opus", statement="Opus accepts as delegated proxy")
    assert approval["isProxy"] is True
    status = store.status()["acceptance"]
    assert status["approvedBy"] == "proxy:Opus" and status["accepted"]
    assert status["isProxy"] is True and status["realUserAccepted"] is False
    assert store.status()["pending_gate"] is None
    store.accept(by="user", statement="I tried it and accept")
    status = store.status()["acceptance"]
    assert status["realUserAccepted"] is status["recordedUserAccepted"] is True
    assert status["humanOriginVerified"] is False
    assert status["approvalOrigin"] == "recorded-unverified"
    with PlanStore(store.workspace) as reopened:
        assert reopened.status()["acceptance"]["realUserAccepted"] is True
    (repo / "tiny.py").write_text("raise SystemExit(2)\n", encoding="utf-8")
    assert store.status()["acceptance"]["approvedBy"] is None
    assert store.status()["pending_gate"] is not None
    with pytest.raises(PlanStoreError):
        store.accept(by="user", statement="Accept stale proof")


def test_review_packet_and_failed_or_stale_injected_runner(project):
    store, repo = project
    fix_product(repo)
    run = run_acceptance(store, repo, subject="product")
    seen = []

    def runner(launch, cwd, timeout):
        seen.append(launch)
        assert not list(cwd.iterdir())
        assert timeout == 900
        assert "Does this product achieve the locked vision" in launch.stdin_text
        assert "lockedVision" in launch.stdin_text and "renderedDocs" in launch.stdin_text
        assert run["revision"] in launch.stdin_text
        return 0, "[]"

    result = acceptance_review(store, repo, reviewers=REVIEWERS, runner=runner)
    assert len(seen) == 2 and not result["findings"]
    with pytest.raises(ValueError, match="exited 2"):
        acceptance_review(store, repo, reviewers=REVIEWERS, runner=lambda *args: (2, ""))
    with pytest.raises(ValueError, match="cross-subscription"):
        acceptance_review(store, repo, reviewers=REVIEWERS[:1], runner=runner)

    def changes_repo(*args):
        (repo / "new-code.py").write_text("print('changed')\n", encoding="utf-8")
        return 0, "[]"

    with pytest.raises(PlanStoreError, match="Rerun acceptance review"):
        acceptance_review(store, repo, reviewers=REVIEWERS, runner=changes_repo)


def test_empty_scenarios_and_unchecked_intermediate_step_cannot_pass(project):
    _, repo = project
    base = {"id": "first-moment", "title": "First moment", "given": "clean", "expect": {}}
    assert not run_scenarios(repo, [{**base, "steps": []}], {})[0]["passed"]
    fix_product(repo)
    assert not run_scenarios(repo, [{**base, "steps": [COMMAND]}], {})[0]["passed"]
    steps = [COMMAND + " --ignored", COMMAND]
    (repo / "tiny.py").write_text("import sys\nsys.exit(2 if len(sys.argv)>1 else 0)\n")
    result = run_scenarios(repo, [{**base, "steps": steps, "expect": {"exit": 0}}], {})[0]
    assert not result["passed"] and len(result["steps"]) == 1


def test_cli_and_mcp_acceptance_paths_and_next(project, monkeypatch, capsys):
    # The low-level _run facade below is a SOURCE-ONLY native-shaped producer
    # control; these calls never launch subscriptions or prove actual acceptance.
    store, repo = project
    to_accept(store)
    fix_product(repo)
    output = cli(store.workspace, "accept-scenarios", "--derive", machine=True)
    assert len(output["data"]["scenarios"]) == 2
    output = cli(
        store.workspace, "accept-run", "--subject", "product", "--repo", str(repo), machine=True
    )
    assert output["data"]["passed"]
    monkeypatch.setattr("codex_harness.core.roundtable._run", lambda *args: (0, "[]"))
    monkeypatch.setattr("codex_harness.core.roundtable._executable", lambda *args: "test-provider")
    assert (
        plan_cli.main(
            [
                "--workspace",
                str(store.workspace),
                "--json",
                "accept-review",
                "--repo",
                str(repo),
                "--reviewer",
                json.dumps(REVIEWERS[0]),
                "--reviewer",
                json.dumps(REVIEWERS[1]),
            ]
        )
        == 0
    )
    reply = json.loads(capsys.readouterr().out)
    assert reply["data"]["stage"] == "acceptance"
    assert "Next:" not in reply["text"] and reply["next"]
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool(
            "harness_accept_review", {"repo": str(repo), "reviewers": REVIEWERS}
        )
        assert not reply["isError"], reply
        assert "Next:" in reply["content"][0]["text"]
        reply = server.call_tool(
            "harness_accept", {"statement": "Proxy accepted", "by": "proxy:Opus"}
        )
        assert not reply["isError"] and reply["structuredContent"]["isProxy"] is True
        assert "approved by proxy (Opus) · not your approval" in reply["content"][0]["text"]
        reply = server.call_tool("harness_accept_scenarios", {})
        assert not reply["isError"] and "Next:" in reply["content"][0]["text"]
        reply = server.call_tool("harness_accept_run", {"repo": str(repo), "subject": "product"})
        assert not reply["isError"]
        pending = store.status()["acceptance"]
        assert not pending["accepted"]
        assert pending["invalidReason"] == "acceptance-execution-pending"
        assert current_run(store) is None
        with pytest.raises(PlanStoreError) as held:
            store.accept(by="proxy:Opus", statement="Cannot accept pending work")
        assert held.value.code == "acceptance-execution-pending"
        assert not finish_public_acceptance(server, reply)["isError"]
    finally:
        server.close()
    # The new run invalidates the previous review and approval.
    cli(store.workspace, "accept", "--statement", "Owner accepted", "--by", "user", code=2)
    source_only_native_review(store, repo)
    output = cli(
        store.workspace,
        "accept",
        "--statement",
        "Proxy accepted",
        "--by",
        "proxy:Opus",
        machine=True,
    )
    assert output["status"]["acceptance"]["isProxy"] is True
    assert "approved by proxy (Opus) · not your approval" in output["text"]


def test_status_defined_scenarios_results_and_stale_binding(project):
    store, repo = project
    store.save_acceptance_scenarios(derive_scenarios(store.locked_acceptance_spine()))
    defined = store.status()["acceptance"]["scenarios"]
    assert defined and all(item["lastResult"] == "not-run" for item in defined)
    assert all(item["runId"] is None and item["steps"] for item in defined)
    failed = run_acceptance(store, repo, subject="product")
    scenarios = store.status()["acceptance"]["scenarios"]
    assert all(item["lastResult"] == "fail" and item["runId"] == failed["id"] for item in scenarios)
    fix_product(repo)
    assert all(
        item["lastResult"] == "not-run (stale)" and item["runId"] is None
        for item in store.status()["acceptance"]["scenarios"]
    )
    passed = run_acceptance(store, repo, subject="product")
    assert all(
        item["lastResult"] == "pass" and item["runId"] == passed["id"]
        for item in store.status()["acceptance"]["scenarios"]
    )
    # The workspace step log needs the executed records, not only the plan.
    assert all(
        item["stepResults"] and all("exit" in step for step in item["stepResults"])
        for item in store.status()["acceptance"]["scenarios"]
    )


def test_acceptance_packet_includes_helpers_and_saved_scenarios(project):
    store, repo = project
    module = store.workspace / "template" / "tests" / "test_money.py"
    module.parent.mkdir(parents=True)
    module.write_text("import acceptance_helper\n")
    helper = module.parent / "acceptance_helper.py"
    helper.write_text("ACCEPTANCE_HELPER_SENTINEL = True\n")
    fix_product(repo)
    run_acceptance(store, repo, subject="product")
    seen = []

    def runner(launch, cwd, timeout):
        packet = launch.stdin_text
        seen.append(packet)
        assert packet.startswith("## Packet contents")
        assert "Current acceptance scenarios" in packet
        assert "Record first glass" in packet and json.dumps(COMMAND) in packet
        assert helper.read_text() in packet
        assert "scenarioResults" in packet
        return 0, "[]"

    result = acceptance_review(store, repo, reviewers=REVIEWERS, runner=runner)
    assert len(seen) == 2 and all(item["shards"] == 1 for item in result["reviewers"])


def assert_strict_response_contract(packet, known):
    assert 'have exactly {"node_id", "severity", "finding", "suggestion"}' in packet
    assert "All values are strings; finding must be nonempty." in packet
    assert "Severity is blocker, major, minor or note." in packet
    assert 'node_id must be one of the ids below, or "plan" for plan-wide findings.' in packet
    assert "Use [] if there are no findings." in packet
    allowed = packet.split("Allowed node ids: ", 1)[1].splitlines()[0]
    assert json.loads(allowed)
    assert set(json.loads(allowed)) <= set(known)


def test_acceptance_response_contract_matches_strict_parser(project):
    store, repo = project
    fix_product(repo)
    run_acceptance(store, repo, subject="product")
    seen = []

    def runner(launch, cwd, timeout):
        seen.append(launch.cli)
        assert_strict_response_contract(launch.stdin_text, store.spine().ids())
        assert "suggestion must be one string" in launch.stdin_text
        assert "an empty string may mean no suggested fix" in launch.stdin_text
        return 0, "[]"  # Explicit mocked unit output, not provider evidence.

    result = acceptance_review(store, repo, reviewers=REVIEWERS, runner=runner)
    assert seen == ["codex", "claude"]
    assert all(record["shards"] == 1 for record in result["reviewers"])
    assert result["isMocked"] is True and result["findings"] == []


def test_acceptance_actual_array_suggestions_fail_closed_and_preserve_diagnostics(project):
    from tests.python.core.test_roundtable import reviewer_stream

    store, repo = project
    fix_product(repo)
    run_acceptance(store, repo, subject="product")
    # Synthetic rollback sentinel, not the historical Codex response.
    sentinel = {
        "node_id": "plan",
        "severity": "note",
        "finding": "Synthetic rollback sentinel",
        "suggestion": "",
    }
    outputs = {
        "codex": reviewer_stream("codex", [sentinel], uses_tools=False),
        "claude": reviewer_stream("claude", REJECTED_HISTORICAL_CLAUDE_OBJECTS, uses_tools=False),
    }
    stderr = "Synthetic retained diagnostic stderr\n"
    calls = []

    def runner(launch, cwd, timeout):
        calls.append(launch.cli)
        return 0, outputs[launch.cli], stderr

    with pytest.raises(ValueError, match="Finding fields must be strings"):
        acceptance_review(store, repo, reviewers=REVIEWERS, runner=runner)
    assert calls == ["codex", "claude"]
    rows = store.connection.execute(
        "SELECT payload_json FROM events WHERE kind='acceptance-review-failed'"
    ).fetchall()
    assert len(rows) == 1
    failed = json.loads(rows[0][0])
    assert failed["isMocked"] is True
    assert [record["provider"] for record in failed["reviewers"]] == calls
    shards = [record["shardDetails"] for record in failed["reviewers"]]
    assert all(len(items) == 1 for items in shards)
    shards = [items[0] for items in shards]
    assert [item["exitCode"] for item in shards] == [0, 0]
    assert [item["invalid"] for item in shards] == [0, 9]
    expected = [
        {"status": "rejected-unparsed", "reason": "Finding fields must be strings", "finding": item}
        for item in REJECTED_HISTORICAL_CLAUDE_OBJECTS
    ]
    assert len(expected) == 9
    assert shards[1]["rejected"] == expected
    artifact = json.loads(Path(shards[1]["artifactPath"]).read_text(encoding="utf-8"))
    assert artifact["exitCode"] == 0 and artifact["rejected"] == expected
    assert artifact["stdout"] == outputs["claude"] and artifact["stderr"] == stderr
    for table in ("acceptance_reviews", "acceptance_findings", "acceptance_approvals"):
        assert store.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    status = store.status()["acceptance"]
    assert not status["accepted"] and status["invalidReason"] == "acceptance-review-required"


@pytest.mark.parametrize("empty", [False, True])
def test_acceptance_string_suggestion_and_empty_array_contract_controls(project, empty):
    from tests.python.core.test_roundtable import reviewer_stream

    from codex_harness.core.roundtable import _invalid_reason

    store, repo = project
    fix_product(repo)
    run_acceptance(store, repo, subject="product")
    # Separate synthetic controls; never normalize the rejected historical objects.
    items = (
        []
        if empty
        else [
            {"node_id": "V1", "severity": "note", "finding": "Synthetic note", "suggestion": ""},
            {
                "node_id": "plan",
                "severity": "minor",
                "finding": "Synthetic gap",
                "suggestion": "Fix it",
            },
        ]
    )
    assert all(_invalid_reason(item, set(store.spine().ids()) | {"plan"}) is None for item in items)
    calls = []

    def runner(launch, cwd, timeout):
        calls.append(launch.cli)
        return 0, reviewer_stream(launch.cli, items, uses_tools=False)

    result = acceptance_review(store, repo, reviewers=REVIEWERS, runner=runner)
    assert calls == ["codex", "claude"]
    assert result["isMocked"] is True and result["packetBound"] is True
    assert len(result["findings"]) == len(items) * 2
    for provider in calls:
        admitted = [item for item in result["findings"] if item["provider"] == provider]
        assert [
            {key: item[key] for key in source} for item, source in zip(admitted, items, strict=True)
        ] == items
        assert all(item["status"] == "open" for item in admitted)
    assert store.acceptance_findings() == result["findings"]


def test_acceptance_every_shard_merges_binding_and_findings(project):
    from tests.python.core.test_roundtable import reviewer_stream

    store, repo = project
    for key in ("R1", "R2"):
        node = store.spine().node(key)
        node["text"] += "acceptance detail " * 5000
        store.upsert_node(node)
    fix_product(repo)
    run = run_acceptance(store, repo, subject="product")
    counts = dict.fromkeys(("codex", "claude"), 0)

    def runner(launch, cwd, timeout):
        counts[launch.cli] += 1
        assert_strict_response_contract(launch.stdin_text, store.spine().ids())
        assert run["revision"] in launch.stdin_text
        assert "lockedVision" in launch.stdin_text and "scenarioResults" in launch.stdin_text
        item = {
            "node_id": "plan",
            "severity": "note",
            "finding": "Acceptance overlap",
            "suggestion": "",
        }
        return 0, reviewer_stream(launch.cli, [item], uses_tools=counts[launch.cli] == 1)

    result = acceptance_review(
        store,
        repo,
        reviewers=REVIEWERS,
        runner=runner,
        token_budgets={"codex": 50_000, "claude": 50_000},
    )
    assert len(result["findings"]) == 2
    assert result["packetBound"] is False
    for record in result["reviewers"]:
        assert record["shards"] == counts[record["provider"]] > 1
        assert record["shardDetails"][0]["packetBound"] is False
        assert all(item["packetBound"] for item in record["shardDetails"][1:])
        assert all(item["estimatedTokens"] <= 50_000 for item in record["shardDetails"])
    saved = store.connection.execute("SELECT result_json FROM acceptance_reviews").fetchone()
    assert json.loads(saved[0])["reviewers"] == result["reviewers"]


def test_acceptance_failed_provider_keeps_primary_diagnostic(project):
    store, repo = project
    fix_product(repo)
    run_acceptance(store, repo, subject="product")
    event = {"type": "result", "is_error": True, "result": "Prompt too long"}
    with pytest.raises(ValueError, match="Prompt too long"):
        acceptance_review(
            store,
            repo,
            reviewers=REVIEWERS,
            runner=lambda *args: (2, json.dumps(event), "last stderr line"),
        )
    row = store.connection.execute(
        "SELECT payload_json FROM events WHERE kind='acceptance-review-failed'"
    ).fetchone()
    shard = json.loads(row[0])["reviewers"][0]["shardDetails"][0]
    assert shard["primaryError"]["exitCode"] == 2
    assert shard["primaryError"]["promptTooLong"] is True
    assert shard["primaryError"]["stderrTail"] == "last stderr line"
    assert Path(shard["artifactPath"]).is_file()
    assert not store.connection.execute("SELECT 1 FROM acceptance_reviews").fetchone()


@pytest.mark.parametrize("double_overflow", [False, True])
def test_acceptance_overflow_retry_is_recorded(project, double_overflow):
    store, repo = project
    fix_product(repo)
    run_acceptance(store, repo, subject="product")
    calls = {"claude": 0, "codex": 0}

    def runner(launch, cwd, timeout):
        calls[launch.cli] += 1
        if launch.cli == "claude" and (calls["claude"] == 1 or double_overflow):
            return 2, "", "prompt is too long"
        return 0, "[]"

    if double_overflow:
        with pytest.raises(ValueError, match="prompt is too long"):
            acceptance_review(store, repo, reviewers=REVIEWERS, runner=runner)
        event = store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='acceptance-review-failed'"
        ).fetchone()
        result = json.loads(event[0])
        assert not store.connection.execute("SELECT 1 FROM acceptance_reviews").fetchone()
    else:
        result = acceptance_review(store, repo, reviewers=REVIEWERS, runner=runner)
        saved = store.connection.execute("SELECT result_json FROM acceptance_reviews").fetchone()
        assert json.loads(saved[0])["reviewers"] == result["reviewers"]
    record = result["reviewers"][-1]
    assert calls == {"codex": 1, "claude": 2}
    assert record["reshardedFrom"]["budget"] == 150_000
    assert record["reshardedFrom"]["newBudget"] == 75_000
    assert record["reshardedFrom"]["newShardCount"] == record["shards"] == 1
    assert "prompt is too long" in record["reshardedFrom"]["reason"]


def test_acceptance_ordinary_failure_stops_before_next_shard(project, monkeypatch):
    from codex_harness.core import roundtable

    store, repo = project
    fix_product(repo)
    run_acceptance(store, repo, subject="product")
    real_shards = roundtable.packet_shards

    def multiple_shards(*args, **kwargs):
        return real_shards(*args, **kwargs) * 3

    monkeypatch.setattr(roundtable, "packet_shards", multiple_shards)
    calls = []

    def runner(launch, cwd, timeout):
        calls.append(launch.cli)
        return 1, "", "network failure"

    with pytest.raises(ValueError, match="network failure"):
        acceptance_review(store, repo, reviewers=REVIEWERS, runner=runner)
    assert calls == ["codex"]


@pytest.mark.parametrize("surface", ["cli", "mcp", "core"])
def test_accept_run_requires_explicit_subject(project, surface):
    store, repo = project
    if surface == "cli":
        reply = cli(store.workspace, "accept-run", "--repo", str(repo), machine=True, code=2)
        assert "--subject product" in reply["next"] and "--subject oracle" in reply["next"]
    elif surface == "mcp":
        server = PlannerServer(store.workspace)
        try:
            reply = server.call_tool("harness_accept_run", {"repo": str(repo)})
        finally:
            server.close()
        assert reply["isError"]
        text = reply["content"][0]["text"].rsplit("Next: ", 1)[1]
        assert "subject=product" in text and "subject=oracle" in text
    else:
        with pytest.raises(PlanStoreError, match="Next:"):
            run_acceptance(store, repo)
    assert store.connection.execute("SELECT count(*) FROM acceptance_runs").fetchone()[0] == 0


@pytest.mark.parametrize("subject", ["product", "oracle"])
def test_subject_controls_proof_gate_and_rebind(project, subject):
    from codex_harness.core.plan_quality import quality_report

    store, repo = project
    to_accept(store)
    fix_product(repo)
    result = cli(
        store.workspace, "accept-run", "--repo", str(repo), "--subject", subject, machine=True
    )["data"]
    assert result["passed"] and result["subject"] == subject
    status = store.status()["acceptance"]
    assert status["lastRunSubject"] == subject
    assert all(item["lastResultSubject"] == subject for item in status["scenarios"])
    proof = next(
        item for item in quality_report(store)["checks"] if item["id"] == "first-moment-proof"
    )
    if subject == "product":
        assert proof["claim"] == "proved"
        source_only_native_review(store, repo)
        store.accept(by="user", statement="I accept the product")
        assert store.status()["acceptance"]["accepted"]
    else:
        assert proof["claim"] == "planned"
        assert not any(item["claim"] == "proved" for item in quality_report(store)["checks"])
        assert all(
            item["lastResult"] == "pass" and "dry run" in item["lastResultNote"]
            for item in status["scenarios"]
        )
        assert not status["accepted"] and status["invalidReason"] == "acceptance-product-required"
        with pytest.raises(PlanStoreError) as error:
            store.accept(by="user", statement="Accept oracle")
        assert error.value.code == "acceptance-product-required"
        with pytest.raises(PlanStoreError) as error:
            store.accept_rebind(repo)
        assert error.value.code == "acceptance-product-required"


@pytest.mark.parametrize("dispatch_evidence", [False, True])
def test_legacy_subject_inference_controls_existing_acceptance(project, dispatch_evidence):
    from codex_harness.core.acceptance import run_subject
    from codex_harness.core.plan_quality import quality_report

    store, repo = project
    to_accept(store)
    fix_product(repo)
    result = run_acceptance(store, repo, subject="product")
    source_only_native_review(store, repo)
    store.accept(by="user", statement="Original product acceptance")
    if dispatch_evidence:
        evidence = repo.parent / "evidence"
        evidence.mkdir()
        (evidence / "run.json").write_text("{}")
    result.pop("subject")
    store.connection.execute(
        "UPDATE acceptance_runs SET result_json=? WHERE id=?", (json.dumps(result), result["id"])
    )
    status = store.status()["acceptance"]
    expected = "product" if dispatch_evidence else "oracle"
    assert run_subject(result) == status["lastRunSubject"] == expected
    assert status["accepted"] is dispatch_evidence
    proof = next(
        item for item in quality_report(store)["checks"] if item["id"] == "first-moment-proof"
    )
    assert (proof["claim"] == "proved") is dispatch_evidence
    # Explicit oracle always wins over a dispatch marker.
    assert run_subject({**result, "subject": "oracle"}) == "oracle"


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill cleanup contract")
@pytest.mark.parametrize(
    "mode",
    [
        "delayed-reap",
        "secondary-timeout",
        "taskkill-timeout",
        "taskkill-failure",
        "taskkill-oserror",
    ],
)
def test_timeout_cleanup_records_failure(mode, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from codex_harness.core import acceptance

    instances = []
    clock = [0.0]
    monkeypatch.setattr(acceptance, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Process:
        pid = 424242
        returncode = None

        def __init__(self, command, **kwargs):
            self.command = command
            kwargs["stdout"].write(b"started\n")
            kwargs["stderr"].write(b"partial stderr\n")
            self.calls = []
            self.killed = False
            instances.append(self)

        def communicate(self, timeout):
            self.calls.append(timeout)
            if timeout == 60 or mode == "secondary-timeout" or timeout <= 0.05:
                raise subprocess.TimeoutExpired(self.command, timeout)
            self.returncode = -9
            return None, None

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True

    def tree_kill(command, **kwargs):
        assert 0 < kwargs["timeout"] <= 1.2
        clock[0] += kwargs["timeout"]
        assert command[:4] == ["taskkill", "/T", "/F", "/PID"]
        if mode == "taskkill-timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if mode == "taskkill-oserror":
            raise OSError("tree-kill unavailable")
        return subprocess.CompletedProcess(command, 1 if mode == "taskkill-failure" else 0)

    monkeypatch.setattr(subprocess, "Popen", Process)
    monkeypatch.setattr(subprocess, "run", tree_kill)
    scenarios = [{"id": "timeout", "title": "timeout", "steps": ["bounded fake"]}]
    if mode != "delayed-reap":
        scenarios.append({"id": "must-not-launch", "title": "held", "steps": ["another child"]})
    result = run_scenarios(tmp_path, scenarios)
    if mode != "delayed-reap":
        assert result[1]["state"] == "unfinished" and not result[1]["steps"]
        assert "cleanup uncertain" in result[1]["error"]
    result = result[0]
    assert not result["passed"]
    step = result["steps"][0]
    assert not step["passed"] and step["exit"] is None
    assert "started" in step["stdout"] and "partial stderr" in step["stderr"]
    assert "timed out" in step["stderr"]
    assert len(instances) == 1
    assert instances[0].killed
    if mode == "secondary-timeout":
        assert "exit was not observed" in step["cleanupUncertainty"]
    elif mode.startswith("taskkill-"):
        assert "Process tree cleanup uncertain" in step["cleanupUncertainty"]
    else:
        assert "cleanupUncertainty" not in step
    assert len(instances[0].calls) == 2
    assert 0.05 < instances[0].calls[1] <= 0.5
    assert clock[0] + instances[0].calls[1] <= 1.7


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill cleanup contract")
def test_acceptance_scenario_deadline_reduces_later_step_wait(project, monkeypatch):
    """Catch renewed 60s waits exceeding captured scenario budget.

    Existing timeout cleanup tests cover one step; they miss cumulative setup
    time and later waits exceeding the scenario's declared finite deadline.
    """
    from types import SimpleNamespace

    from codex_harness.core import acceptance

    store, repo = project
    clock, waits, launched = [0.0], [], []
    monkeypatch.setattr(acceptance, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Process:
        pid = 424242
        returncode = None
        killed = False

        def __init__(self, command, **kwargs):
            launched.append(command)
            clock[0] += 40 if len(launched) == 1 else 10

        def communicate(self, timeout):
            if self.killed:
                return None, None
            waits.append(timeout)
            clock[0] += timeout
            if len(launched) == 1:
                self.returncode = 0
            else:
                raise subprocess.TimeoutExpired("controlled clock", timeout)

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed, self.returncode = True, -9

    monkeypatch.setattr(subprocess, "Popen", Process)
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0)
    )
    result = run_scenarios(
        repo, [dict(id="budget", title="budget", steps=["first", "second"], expect={"exit": 0})]
    )[0]
    assert waits == pytest.approx([60, 10])
    assert result["budgetSeconds"] == 121.7 and result["deadlineAt"]
    assert not result["passed"] and result["steps"][-1]["timedOut"]


def test_execution_af_history_is_selected_without_revalidating_old_product(tmp_path, monkeypatch):
    from tests.python.core.test_roundtable import canonical_af_fixture

    from codex_harness.core.acceptance import read_execution_finding_origins

    store, request, original, current, run, calls, _ = canonical_af_fixture(tmp_path, monkeypatch)
    try:
        key = request["members"][0]["memberId"]
        historical_json = store.connection.execute(
            "SELECT result_json FROM acceptance_runs WHERE id=?", (original["runId"],)
        ).fetchone()[0]
        # A changed product must not erase or rewrite its genuinely admitted AF
        # origin. This historical lookup alone grants no current support.
        (Path(run["repo"]) / "A.py").write_text("def valid_id(value): return True\n")
        assert current_run(store) is None
        before = list(store.connection.iterdump())
        origins = read_execution_finding_origins(store, [key])
        assert len(origins) == 1 and origins[0]["finding"]["id"] == key
        assert origins[0]["sourceRun"]["id"] == original["runId"] != current["id"]
        assert origins[0]["review"]["attemptId"] == original["attemptId"]
        assert origins[0]["originals"] and origins[0]["registration"]["slots"]
        assert (
            store.connection.execute(
                "SELECT result_json FROM acceptance_runs WHERE id=?", (original["runId"],)
            ).fetchone()[0]
            == historical_json
        )
        assert list(store.connection.iterdump()) == before and len(calls) == 2
        with pytest.raises(ValueError, match="genuine-admitted-af"):
            read_execution_finding_origins(store, ["not-an-AF"])
        assert list(store.connection.iterdump()) == before
    finally:
        store.close()
