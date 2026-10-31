"""Cold-start reviews through canonical packets, findings, CLI and MCP; no providers."""

import hashlib
import json
import random
import subprocess

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.planning_support import source_only_confirmation

from codex_harness.core import plan_cli, readiness, roundtable
from codex_harness.core.mcp_planner import PlannerServer, packet_view
from codex_harness.core.plan_quality import quality_report
from codex_harness.core.plan_store import PlanStore, PlanStoreError
from codex_harness.core.readiness import QUESTION, readiness_review, select_beads

project = test_planner_replay_gaps.project
REVIEWERS = test_planner_replay_gaps.REVIEWERS
REAL_RUN = roundtable._run
READY = dict(ready=True, canVerify=True, blockers=[], questions=[])
BLOCKED = dict(
    ready=False,
    canVerify=False,
    blockers=[
        dict(
            kind="missing-interface",
            detail="Define the answer() return type.",
            nodeIds=["B1", "R1"],
        )
    ],
    questions=["What type is returned?"],
)


def review(project, answer=READY, **kwargs):
    return readiness_review(
        project,
        reviewers=REVIEWERS,
        runner=lambda *args: (0, json.dumps(answer)),
        executable_for=lambda provider: provider,
        **kwargs,
    )


def check(project):
    return next(c for c in quality_report(project)["checks"] if c["id"] == "worker-readiness")


def test_exact_packet_and_normal_launch_with_instruction_channel(project, capsys):
    expected = {
        r["provider"]: packet_view(project, "B1", r["provider"])["packet"] for r in REVIEWERS
    }
    received = []

    def runner(launch, cwd, timeout):
        if not received:
            assert "Readiness planned calls: 1 beads × 2 reviewers = 2" in capsys.readouterr().err
        assert launch.stdin_text.encode("utf-8") == expected[launch.cli].encode("utf-8")
        assert not list(cwd.iterdir())
        assert timeout == 900
        command = " ".join(launch.command)
        assert QUESTION in command or json.dumps(QUESTION)[1:-1] in command
        assert "dangerously" not in command and "bypass" not in command
        assert "--sandbox" in command if launch.cli == "codex" else "--permission-mode" in command
        received.append(launch)
        return 0, json.dumps(READY)

    result = readiness_review(
        project, reviewers=REVIEWERS, runner=runner, executable_for=lambda provider: provider
    )
    assert len(received) == 2 and result["status"] == "pass"
    rows = project.readiness_reviews(project.manifest_revision())
    assert rows == result["reviews"]
    for row in rows:
        assert (
            row["packetHash"]
            == hashlib.sha256(expected[row["reviewer"]["provider"]].encode()).hexdigest()
        )
        assert row["packetBound"] and not row["outOfPacketTools"]
    with PlanStore(project.workspace) as reopened:
        assert reopened.readiness_reviews() == rows


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_provider_final_json_and_binding(project, provider):
    final = json.dumps(READY)
    if provider == "codex":
        events = [
            dict(type="item.started", item=dict(type="command_execution")),
            dict(type="item.completed", item=dict(type="agent_message", text=final)),
        ]
    else:
        events = [
            dict(type="assistant", message=dict(content=[dict(type="tool_use", name="Read")])),
            dict(type="result", result=final),
        ]
    result = readiness_review(
        project,
        reviewers=[dict(name="reviewer", provider=provider)],
        runner=lambda *args: (0, "\n".join(json.dumps(e) for e in events)),
        executable_for=lambda provider: provider,
    )
    assert not result["invalid"]
    row = result["reviews"][0]
    assert not row["packetBound"] and row["outOfPacketTools"]


@pytest.mark.parametrize(
    "output",
    [
        "not JSON",
        "[]",
        "{}",
        json.dumps({**READY, "ready": "true"}),
        json.dumps({**READY, "blockers": [dict(kind="unknown", detail="x", nodeIds=[])]}),
        json.dumps(
            {**BLOCKED, "blockers": [dict(kind="contradiction", detail="x", nodeIds=["unknown"])]}
        ),
    ],
)
def test_invalid_json_is_recorded_failure(project, output):
    result = readiness_review(
        project,
        reviewers=REVIEWERS[:1],
        runner=lambda *args: (0, output),
        executable_for=lambda provider: provider,
    )
    assert result["invalid"] == 1 and result["status"] == "fail"
    assert result["reviews"][0]["ready"] is None
    assert result["reviews"][0]["error"]
    assert not roundtable.findings(project)
    assert project.readiness_status()["status"] == "fail"


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing"])
def test_launch_failures_are_recorded(project, failure):
    def runner(*args):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("reviewer", 900)
        return 7, "partial", "provider failed"

    result = readiness_review(
        project,
        reviewers=REVIEWERS[:1],
        runner=runner,
        executable_for=lambda provider: None if failure == "missing" else provider,
    )
    assert result["invalid"] == 1 and result["status"] == "fail"
    assert project.readiness_reviews()[0]["error"]


def test_findings_lifecycle_quality_and_read_only_status(project):
    project.connection.execute("UPDATE meta SET phase='plan'")
    assert check(project)["status"] == "not-yet"
    result = review(project, BLOCKED)
    assert result["status"] == "fail" and check(project)["status"] == "fail"
    items = roundtable.findings(project)
    assert len(items) == 2
    assert all(f["stage"] == "readiness" and f["kind"] == "missing-interface" for f in items)
    assert all(json.loads(f["nodeIds"]) == ["B1", "R1"] for f in items)
    assert all(f["plan_revision"] == result["manifestRevision"] for f in items)
    for item in items:
        roundtable.resolve_finding(
            project, item["id"], status="deferred", resolution="Next iteration"
        )
    assert check(project)["status"] == "warn"
    for item in items:
        roundtable.resolve_finding(project, item["id"], status="resolved", resolution="Type added")
    assert check(project)["status"] == "not-yet"
    review(project)
    assert check(project)["status"] == "pass"
    before = list(project.connection.iterdump())
    assert project.status()["readiness"]["status"] == "pass"
    assert check(project)["status"] == "pass"
    assert list(project.connection.iterdump()) == before


@pytest.mark.parametrize(
    "answer",
    [
        dict(ready=False, canVerify=True, blockers=[], questions=[]),
        dict(ready=True, canVerify=False, blockers=[], questions=[]),
    ],
)
def test_negative_without_blocker_still_fails(project, answer):
    assert review(project, answer)["status"] == "fail"
    assert len(roundtable.findings(project)) == 2


def test_stale_manifest_after_node_or_protected_file_edit(project):
    review(project)
    old = project.manifest_revision()
    node = project.spine().node("B1")
    node["text"] += " revised"
    project.upsert_node(node)
    assert project.readiness_status()["status"] == "not-yet"
    assert project.readiness_reviews(old)
    project.compile_template(project.workspace / "template")
    review(project)
    path = project.workspace / "template/tests/test_answer.py"
    path.write_text(path.read_text() + "\n# revised\n")
    assert project.readiness_status()["status"] == "not-yet"


@pytest.mark.parametrize("change", ["node", "packet"])
def test_change_during_review_refuses_atomic_storage(project, change):
    def runner(*args):
        if change == "node":
            node = project.spine().node("B1")
            node["text"] += " revised"
            project.upsert_node(node)
        else:
            path = project.workspace / "template/answer.py"
            path.write_text(path.read_text() + "\n# modified\n")
        return 0, json.dumps(BLOCKED)

    with pytest.raises(PlanStoreError) as caught:
        readiness_review(
            project,
            reviewers=REVIEWERS[:1],
            runner=runner,
            executable_for=lambda provider: provider,
        )
    assert caught.value.code == (
        "readiness-plan-changed" if change == "node" else "readiness-packet-changed"
    )
    assert not project.readiness_reviews() and not roundtable.findings(project)


def test_packet_edit_stales_result_without_manifest_edit(project):
    review(project)
    revision = project.manifest_revision()
    path = project.workspace / "template/answer.py"
    path.write_text(path.read_text() + "\n# changed owned context\n")
    assert project.manifest_revision() == revision
    assert project.readiness_status()["status"] == "not-yet"
    assert project.readiness_status()["stale"]


def test_later_batch_cannot_hide_open_findings_or_other_reviewer_failures(project):
    bead = project.spine().node("B1")
    bead["id"] = "B2"
    bead["fields"]["ownedFiles"] = ["second.py"]
    project.upsert_node(bead)
    (project.workspace / "template/second.py").write_text("def second(): pass\n")
    project.compile_template(project.workspace / "template")
    review(project, BLOCKED, beads=["B1"])
    review(project, beads=["B2"])
    state = project.readiness_status()
    assert state["status"] == "fail" and state["sampledBeads"] == ["B1", "B2"]
    assert len(state["openFindingIds"]) == 2
    # Even a fresh ready result for the old bead must not silently close findings.
    assert review(project, beads=["B1"])["status"] == "fail"
    for item in roundtable.findings(project):
        roundtable.resolve_finding(project, item["id"], resolution="Interface clarified")
    assert project.readiness_status()["status"] == "pass"
    failed = readiness_review(
        project,
        beads=["B1"],
        reviewers=REVIEWERS[:1],
        runner=lambda *args: (0, "invalid"),
        executable_for=lambda provider: provider,
    )
    assert failed["status"] == "fail"
    # A different reviewer's successful retry cannot erase the parse failure.
    result = readiness_review(
        project,
        beads=["B1"],
        reviewers=REVIEWERS[1:],
        runner=lambda *args: (0, json.dumps(READY)),
        executable_for=lambda provider: provider,
    )
    assert result["status"] == "fail"
    assert review(project, beads=["B1"])["status"] == "pass"


def test_readiness_is_advisory_at_publish_with_existing_gates(project, monkeypatch):
    from codex_harness.core.plan_store import PHASES

    review(project, BLOCKED)
    test_planner_replay_gaps.audit(project)
    test_planner_replay_gaps.review(project)
    while project.phase() != "publish":
        phase = project.phase()
        if phase in ("align", "vision-lock"):
            source_only_confirmation(project, phase, statement="Approve")
        project.advance(PHASES[PHASES.index(phase) + 1], by="planner")
    report = quality_report(project)
    assert next(c for c in report["checks"] if c["id"] == "roundtable")["status"] == "pass"
    assert check(project)["status"] == "fail"
    server = PlannerServer(project.workspace)
    try:
        reply = server.call_tool("harness_publish", {})
        assert not reply["isError"], reply
        assert "Worker readiness (advisory): fail" in reply["content"][0]["text"]
        assert project.status()["published_revision"]
        # Repeating publication keeps immutable snapshots intact.
        assert not server.call_tool("harness_publish", {})["isError"]
    finally:
        server.close()


def test_selection_default_sample_seed_and_explicit_beads():
    beads = [f"B{i}" for i in range(40)]
    assert select_beads(beads, sample=7, seed=19) == random.Random(19).sample(sorted(beads), 7)
    assert select_beads(beads, seed=42) == select_beads(reversed(beads), seed=42)
    assert len(select_beads(beads)) == 5
    assert len(select_beads(beads[:8])) == 8
    assert select_beads(beads, selected=["B4", "B1"]) == ["B4", "B1"]
    for kwargs in [
        dict(sample=0),
        dict(sample=41),
        dict(selected=["missing"]),
        dict(selected=["B1", "B1"]),
        dict(selected=["B1"], sample=1),
    ]:
        with pytest.raises(ValueError):
            select_beads(beads, **kwargs)


def _add_dependent(project):
    bead = project.spine().node("B1")
    bead.update(id="B2", title="Consumer", text="Implement after B1")
    bead["fields"].update(ownedFiles=["second.py"], dependsOn=["B1"])
    project.upsert_node(bead)
    (project.workspace / "template/second.py").write_text("# consumer\n")
    project.compile_template(project.workspace / "template")


def test_stage_default_forced_selection_and_quality(project):
    _add_dependent(project)
    project.connection.execute("UPDATE meta SET phase='plan'")
    result = review(project)
    assert result["selectedBeads"] == ["B1"]
    assert result["pendingProducers"] == [
        dict(beadId="B2", status="pending-producers", producerIds=["B1"])
    ]
    assert "B2: pending-producers; producers: B1" in readiness.render_readiness(result)
    # A forced downstream review is retained, but cannot poison the eligible quality check.
    forced = review(project, BLOCKED, beads=["B2"])
    assert forced["selectedBeads"] == ["B2"]
    assert forced["status"] == "fail"
    quality = check(project)
    assert quality["status"] == "pass"
    assert quality["evidence"]["sampledBeads"] == ["B1"]
    assert quality["evidence"]["eligibleBeadCount"] == 1
    assert "1 beads pending producers" in quality["advice"]


@pytest.mark.parametrize("status", ["requirement-validated", "reused", "mock-only", "pending"])
def test_run_producers_and_cli_flag(project, monkeypatch, status):
    _add_dependent(project)
    run = project.workspace / "run"
    (run / "evidence").mkdir(parents=True)
    (run / "evidence/run.json").write_text(json.dumps({"beads": {"B1": {"status": status}}}))
    monkeypatch.setattr(roundtable, "_run", lambda *args: (0, json.dumps(READY)))
    monkeypatch.setattr(roundtable, "_executable", lambda *args: "reviewer")
    server = PlannerServer(project.workspace)
    try:
        args = plan_cli.parser().parse_args(
            [
                "--workspace",
                str(project.workspace),
                "readiness",
                "--run",
                "run",
                "--reviewer",
                json.dumps(REVIEWERS[0]),
            ]
        )
        reply, result = plan_cli.dispatch(server, args)
    finally:
        server.close()
    assert not reply["isError"]
    implemented = status in {"requirement-validated", "reused"}
    assert set(result["selectedBeads"]) == ({"B1", "B2"} if implemented else {"B1"})
    assert result["pendingProducerCount"] == (0 if implemented else 1)


def test_cost_guard_and_explicit_budget(project, monkeypatch, capsys):
    # Cost validation precedes packet building/launching; a larger selected plan
    # needs no synthetic packet fixture or provider invocation to test refusal.
    monkeypatch.setattr(readiness, "select_beads", lambda *args, **kwargs: ["B1"] * 13)
    with pytest.raises(ValueError, match="12 beads"):
        review(project)
    assert "13 beads × 2 reviewers = 26" in capsys.readouterr().err
    with pytest.raises(ValueError, match="max-calls"):
        review(project, max_calls=25)
    monkeypatch.setattr(readiness, "select_beads", lambda *args, **kwargs: ["B1"])
    assert review(project, max_calls=2)["plannedCalls"] == 2
    reviewers = [dict(name=f"reviewer-{i}", provider="codex") for i in range(3)]
    with pytest.raises(ValueError, match="12 beads"):
        readiness_review(project, reviewers=reviewers)
    result = readiness_review(
        project,
        reviewers=reviewers,
        max_calls=3,
        runner=lambda *args: (0, json.dumps(READY)),
        executable_for=lambda provider: provider,
    )
    assert result["plannedCalls"] == 3


def test_cli_mcp_and_render_status_parity(project, monkeypatch, capsys):
    monkeypatch.setattr(roundtable, "_run", lambda *args: (0, json.dumps(BLOCKED)))
    monkeypatch.setattr(roundtable, "_executable", lambda *args: "reviewer")
    server = PlannerServer(project.workspace)
    try:
        args = plan_cli.parser().parse_args(
            [
                "--workspace",
                str(project.workspace),
                "readiness",
                "--beads",
                "B1",
                "--seed",
                "7",
                "--reviewer",
                json.dumps(REVIEWERS[0]),
            ]
        )
        reply, result = plan_cli.dispatch(server, args)
        assert not reply["isError"] and result["selectedBeads"] == ["B1"]
        assert result["reviews"][0]["seed"] == 7
        mcp = server.call_tool(
            "harness_readiness", dict(beads=["B1"], seed=7, reviewers=REVIEWERS[:1])
        )
        assert not mcp["isError"] and mcp["structuredContent"]["status"] == result["status"]
        assert (
            "Worker readiness (advisory): fail"
            in server.call_tool("harness_plan_status", {})["content"][0]["text"]
        )
        assert all(
            "Worker readiness (advisory): fail" in text for text in project.rendered_docs().values()
        )
        monkeypatch.setattr(roundtable, "_run", lambda *args: (0, "invalid"))
        failed = server.call_tool("harness_readiness", dict(reviewers=REVIEWERS[:1]))
        assert failed["isError"] and failed["structuredContent"]["invalid"] == 1
        assert "Error:" in failed["content"][0]["text"]
        bad = server.call_tool("harness_readiness", dict(sample=True))
        assert bad["isError"]
    finally:
        server.close()
    assert "planned calls" in capsys.readouterr().err


def test_real_process_access_failure_and_explicit_reselection_persist(
    project, tmp_path, monkeypatch
):
    """Catch masked failed access; injected runners miss real IO and persistence."""
    import sys

    from codex_harness.core.provider_launch import ProviderNormalLaunch

    child = tmp_path / "review.py"
    child.write_text(
        "import sys\nsys.stdin.read()\nsys.stderr.write('model not found')\nsys.exit(1)\n",
        encoding="utf-8",
    )

    def launch(reviewer, packet, executable, **kwargs):
        return ProviderNormalLaunch(
            cli=reviewer["provider"],
            command=(sys.executable, str(child)),
            protections=(),
            stdin_text=packet,
        )

    monkeypatch.setattr(roundtable, "_launch_review", launch)
    monkeypatch.setattr(roundtable, "_run", REAL_RUN)
    first = readiness_review(project, reviewers=[REVIEWERS[0]])
    failed = first["reviews"][0]
    assert failed["accessObservation"]["outcome"] == "failure"
    assert failed["accessObservation"]["failureCategory"] == "unknown-model"
    assert first["invalid"] == 1
    child.write_text(
        "import json,sys\nsys.stdin.read()\nprint(json.dumps(" + repr(READY) + "))\n",
        encoding="utf-8",
    )
    second = readiness_review(project, reviewers=[dict(REVIEWERS[0], name="reselected")])
    assert second["reviews"][0]["accessObservation"]["outcome"] == "success"
    assert second["invalid"] == 0
    with PlanStore(project.workspace) as reopened:
        rows = reopened.readiness_reviews()
        assert {r["batchId"] for r in rows} >= {first["id"], second["id"]}
        assert next(r for r in rows if r["batchId"] == first["id"])["error"]
