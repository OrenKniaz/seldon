"""Canonical plan/mail readers, dispatcher receipts and read-only HTTP behavior."""

import builtins
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

import pytest
from tests.python.core.planning_support import publication_evidence, source_only_confirmation

from codex_harness.core.mail import Mailbox
from codex_harness.core.plan_store import PHASES, PlanStore
from codex_harness.core.record_io import read_record_json, write_record_json
from codex_harness.core.status_api import snapshot
from codex_harness.core.status_server import create_server

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit" / "spine.json"


def _mtimes(root):
    return {str(path): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}


@pytest.mark.skipif(os.name != "nt", reason="Windows participating-reader serialization contract")
@pytest.mark.parametrize("target_kind", ["request", "canonical-run", "flat-run"])
def test_snapshot_record_reader_serializes_actual_cross_process_publication(
    tmp_path, monkeypatch, target_kind
):
    run = tmp_path / "runs" / "concurrent"
    attempt = run / "evidence" / "A" / "attempt"
    attempt.mkdir(parents=True)
    request = {
        "status": "launching",
        "startedAt": "2026-01-01T00:00:00+00:00",
        "requirementId": "fixture-A",
        "runId": "concurrent",
        "bead": "A",
    }
    write_record_json(attempt / "request.json", request)
    for name in ("events.jsonl", "stderr.txt"):
        (attempt / name).write_bytes(b"")
    record = {
        "runId": "concurrent",
        "status": "running",
        "beads": {
            "A": {
                "status": "running",
                "provider": "local-fixture",
                "evidenceDir": str(attempt.parent),
            }
        },
    }
    run_path = run / "run.json" if target_kind == "flat-run" else run / "evidence" / "run.json"
    write_record_json(run_path, record)
    target = attempt / "request.json" if target_kind == "request" else run_path
    publication = {**(request if target_kind == "request" else record), "writerMarker": "published"}
    opened, release = threading.Event(), threading.Event()
    observations, errors = [], []
    original_open = builtins.open

    @contextmanager
    def held_handle(stream):
        with stream:
            opened.set()
            if not release.wait(4):
                raise TimeoutError("Test reader barrier was not released")
            yield stream

    def instrumented_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if (
            threading.current_thread().name == "snapshot-reader"
            and Path(path).resolve() == target.resolve()
        ):
            return held_handle(stream)
        return stream

    # Both old Path.open/io.open and the frozen helper's built-in open hit the
    # same barrier AFTER the real handle opens; publication is a separate process.
    monkeypatch.setattr("builtins.open", instrumented_open)
    monkeypatch.setattr("io.open", instrumented_open)

    def read_snapshot():
        try:
            observations.append(snapshot(runs_root=tmp_path / "runs"))
        except Exception as error:
            errors.append(error)

    reader = threading.Thread(target=read_snapshot, name="snapshot-reader")
    reader.start()
    writer = None
    blocked = False
    try:
        assert opened.wait(4), "Supported snapshot path never opened the target record"
        writer = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                "import json,sys; from codex_harness.core.record_io import write_record_json; "
                "print('writer-ready',flush=True); "
                "write_record_json(sys.argv[1],json.loads(sys.argv[2])); "
                "print('writer-done',flush=True)",
                str(target),
                json.dumps(publication),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        assert writer.stdout.readline().strip() == "writer-ready"
        try:
            writer.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            blocked = True
    finally:
        release.set()
        reader.join(timeout=6)
        if writer is not None:
            output, error_output = writer.communicate(timeout=6)
    assert not reader.is_alive() and not errors
    assert blocked, f"Writer did not serialize: exit={writer.returncode}; {error_output}"
    assert writer.returncode == 0, error_output
    assert "writer-done" in output
    assert read_record_json(target)["writerMarker"] == "published"
    assert observations[0]["runs"][0]["beads"][0]["status"] == "running"
    assert observations[0]["runs"][0]["beads"][0]["activity"]["processLiveness"] == "unknown"


@pytest.mark.parametrize("error_type", [PermissionError, TimeoutError])
def test_run_record_reader_refusal_stays_unavailable_without_fallback(
    tmp_path, monkeypatch, error_type
):
    run = tmp_path / "runs" / "refused"
    (run / "evidence").mkdir(parents=True)
    write_record_json(run / "evidence" / "run.json", {"runId": "refused", "beads": {}})
    before = _mtimes(tmp_path)

    def refused(path):
        raise error_type("Injected publication/read refusal")

    monkeypatch.setattr("codex_harness.core.status_api.read_record_json", refused)
    report = snapshot(runs_root=tmp_path / "runs")
    assert report["runs"] == [{"runId": "refused", "status": "unavailable", "current": True}]
    assert _mtimes(tmp_path) == before


def test_attempt_activity_current_agent_observes_quiet_without_claiming_liveness(tmp_path):
    run = tmp_path / "runs" / "quiet"
    attempt = run / "evidence" / "A" / "attempt"
    attempt.mkdir(parents=True)
    (attempt / "request.json").write_text(
        json.dumps(
            {
                "status": "launching",
                "startedAt": "2026-01-01T00:00:00+00:00",
                "requirementId": "fixture-A",
                "planRevision": "fixture-revision",
            }
        ),
        encoding="utf-8",
    )
    for name in ("events.jsonl", "stderr.txt"):
        (attempt / name).write_text("", encoding="utf-8")
    for path in attempt.iterdir():
        os.utime(path, (1767225600, 1767225600))
    (run / "evidence" / "run.json").write_text(
        json.dumps(
            {
                "runId": "quiet",
                "status": "running",
                "beads": {
                    "A": {
                        "status": "running",
                        "provider": "codex",
                        "model": "fixture-model",
                        "evidenceDir": str(attempt.parent),
                        "attempt": {"startedAt": "2026-01-01T00:00:00Z"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    before = _mtimes(tmp_path)
    result = snapshot(runs_root=tmp_path / "runs")
    activity = result["runs"][0]["beads"][0]["activity"]
    assert activity["state"] == "unknown" and activity["processLiveness"] == "unknown"
    assert activity["stuck"] is True and activity["isTerminal"] is None
    assert activity["secondsSinceActivity"] >= activity["noProgressThresholdSeconds"] == 300
    assert result["currentAgents"][0]["activity"]["stuckCount"] == 1
    assert _mtimes(tmp_path) == before


def test_attempt_activity_terminal_receipt_overrides_lagging_run_and_unknown_counts(tmp_path):
    run = tmp_path / "runs" / "terminal"
    attempt = run / "evidence" / "A" / "attempt"
    attempt.mkdir(parents=True)
    request = dict(status="launching", startedAt="2026-01-01T00:00:00+00:00", requirementId="A")
    (attempt / "request.json").write_text(json.dumps(request), encoding="utf-8")
    (attempt / "result.json").write_text(
        json.dumps(
            {
                **request,
                "status": "cancelled",
                "finishedAt": "2026-01-01T00:00:01+00:00",
                "cleanup": {"status": "UNVERIFIED"},
            }
        ),
        encoding="utf-8",
    )
    record = {
        "runId": "terminal",
        "status": "running",
        "beads": {
            "A": {"status": "running", "provider": "codex", "model": "fixture"},
            "B": {"status": "running", "provider": "codex", "model": "fixture"},
        },
    }
    (run / "evidence" / "run.json").write_text(json.dumps(record), encoding="utf-8")
    before = _mtimes(tmp_path)
    result = snapshot(runs_root=tmp_path / "runs")
    beads = result["runs"][0]["beads"]
    assert beads[0]["status"] == "running"  # Original run projection remains intact.
    assert beads[0]["activity"]["recordedStatus"] == "cancelled"
    assert beads[0]["activity"]["stuck"] is False
    assert beads[1]["activity"]["stuck"] is None
    assert result["currentAgents"][0]["active"] == 1  # Recorded running, not live process count.
    summary = result["currentAgents"][0]["activity"]
    assert summary["stuckCount"] == 0 and summary["unknownCount"] == 1
    assert summary["terminalCount"] == 1
    assert result["runs"][0]["activity"] == summary
    assert _mtimes(tmp_path) == before
    request["runId"] = "terminal"
    (attempt / "request.json").write_text(json.dumps(request), encoding="utf-8")
    (attempt / "result.json").write_text(
        json.dumps(
            {
                **request,
                "runId": "different-run",
                "status": "cancelled",
                "finishedAt": "2026-01-01T00:00:01+00:00",
            }
        ),
        encoding="utf-8",
    )
    before = _mtimes(tmp_path)
    mismatched = snapshot(runs_root=tmp_path / "runs")
    assert mismatched["runs"][0]["beads"][0]["activity"]["isTerminal"] is None
    assert mismatched["currentAgents"][0]["active"] == 2
    assert mismatched["currentAgents"][0]["activity"]["unknownCount"] == 2
    assert mismatched["currentAgents"][0]["activity"]["terminalCount"] is None
    assert _mtimes(tmp_path) == before


@pytest.mark.parametrize("status", ["held: permission guard breach", "ambiguous launch"])
def test_attempt_activity_keeps_hold_and_ambiguous_resume_without_recovery(tmp_path, status):
    run = tmp_path / "runs" / "held"
    attempt = run / "evidence" / "A" / "attempt"
    attempt.mkdir(parents=True)
    (attempt / "request.json").write_text(
        json.dumps({"status": "launching", "startedAt": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    for name in ("events.jsonl", "stderr.txt"):
        (attempt / name).write_text("", encoding="utf-8")
    (run / "evidence" / "run.json").write_text(
        json.dumps(
            {
                "runId": "held",
                "beads": {"A": {"status": status, "provider": "codex", "model": "fixture"}},
            }
        ),
        encoding="utf-8",
    )
    before = _mtimes(tmp_path)
    result = snapshot(runs_root=tmp_path / "runs")
    bead = result["runs"][0]["beads"][0]
    assert bead["status"] == status and bead["activity"]["stuck"] is None
    assert result["currentAgents"][0]["activity"]["stuckCount"] is None
    assert result["currentAgents"][0]["activity"]["unknownCount"] == 1
    assert _mtimes(tmp_path) == before


def test_attempt_activity_cannot_follow_record_path_outside_canonical_evidence(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "request.json").write_text("private fixture", encoding="utf-8")
    run = tmp_path / "runs" / "escape"
    (run / "evidence").mkdir(parents=True)
    (run / "evidence" / "run.json").write_text(
        json.dumps(
            {
                "runId": "escape",
                "beads": {
                    "A": {"status": "running", "evidenceDir": str(outside), "provider": "codex"}
                },
            }
        ),
        encoding="utf-8",
    )
    observation = snapshot(runs_root=tmp_path / "runs")["runs"][0]["beads"][0]["activity"]
    assert observation["state"] == "unavailable" and observation["stuck"] is None


def _message(workspace):
    mailbox = Mailbox(workspace)
    mailbox.send(
        from_agent="worker-A",
        to_agent="planner",
        subject="Proof ready",
        body="See receipt",
        refs=["A"],
        kind="note",
    )
    acked = mailbox.send(from_agent="worker-B", to_agent="planner", subject="Old", body="Done")
    mailbox.ack(acked, by="planner")
    mailbox.send(from_agent="planner", to_agent="worker-A", subject="Private", body="Worker only")


def test_snapshot_canonical_readers_and_dispatcher_shape(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    with PlanStore(workspace) as store:
        store.import_spine(FIXTURE)
        bead = store.spine().node("A")
        bead["parents"].append("R6")
        store.upsert_node(bead)
        while store.phase() != "publish":
            phase = store.phase()
            if phase in ("align", "vision-lock"):
                source_only_confirmation(store, phase, statement="Confirmed")
            if phase == "roundtable":
                publication_evidence(store)
            store.advance(PHASES[PHASES.index(phase) + 1], by="planner")
        publication_evidence(store)
        revision = store.publish(by="operator", export_dir=workspace / "exports")
        store.ask("Which layout?", ["A"])
        store.add_assumption("Use integer cents", ["R1"])
        store.connection.execute(
            "INSERT INTO review_findings (id,reviewer,provider,status,plan_revision,node_id,"
            "severity,finding,suggestion,created_at) "
            "VALUES ('F1','test','codex','open','rev','plan','major','Issue','Fix','now')"
        )
        expected = store.status()
    _message(workspace)
    root = tmp_path / "runs"
    run = root / "run-1"
    evidence = run / "evidence"
    evidence.mkdir(parents=True)
    memory = run / "memory-workspace"
    _message(memory)
    record = {
        "runId": "run-1",
        "status": "requirement-validated",
        "isMocked": False,
        "didExecuteRealProvider": True,
        "liveAcceptance": {"claimed": False},
        "planRevision": revision,
        "memoryWorkspace": str(memory),
        "planUpdate": {"affectedBeads": ["B"]},
        "resumeSummary": {"reused": ["A"], "redispatched": ["B"], "invalidated": ["C"]},
        "beads": {
            "A": {
                "status": "requirement-validated",
                "provider": "codex",
                "model": "test-model",
                "specRefs": [{"id": "R1", "revision": "a" * 64}],
                "attempt": {"changedPaths": ["tipsplit/a.py"]},
                "evidenceDir": str(evidence / "A"),
                "integrated": {
                    "validation": {"passed": True},
                    "receiptPath": str(evidence / "A" / "integrated.json"),
                },
            },
            "B": {
                "status": "integrated validation failed",
                "provider": "claude",
                "model": None,
                "integrated": {"validation": {"passed": False}},
            },
            "C": {"status": "pending"},
        },
    }
    (evidence / "run.json").write_text(json.dumps(record), encoding="utf-8")
    before = _mtimes(tmp_path)
    result = snapshot(workspace=workspace, runs_root=root)
    assert _mtimes(tmp_path) == before
    plan = result["plan"]
    assert {key: plan[key] for key in expected} == expected
    assert plan["acceptance"] == expected["acceptance"]
    assert plan["reviewRecorded"] is False  # Injected fixture reviews earn no real credit.
    assert plan["latestPublishedRevision"] == revision
    assert plan["latestPublishedRevisionShort"] == revision[:12]
    assert plan["shortRevisions"]["plan_revision"] == expected["plan_revision"][:12]
    assert [phase["id"] for phase in plan["phases"]] == list(PHASES)
    assert [phase["state"] for phase in plan["phases"]] == ["done"] * 8 + [
        "current",
        "upcoming",
        "upcoming",
    ]
    assert plan["phases"][2]["label"] == "Vision Lock"
    history = {item["phase"]: item for item in plan["phaseHistory"]}
    assert history["align"]["approvedBy"] == "mcp-client-elicitation"
    assert history["align"]["isProxy"] is False
    assert history["align"]["passedAt"]
    assert history["align"]["evidence"] == "Confirmed"
    assert history["accept"]["passedAt"] is None
    assert isinstance(plan["quality"]["score"], int)
    assert len(plan["quality"]["checks"]) == 14
    assert plan["clarifications"][0]["question"] == "Which layout?"
    assert plan["assumptions"][0]["text"] == "Use integer cents"
    nodes = {node["id"]: node for node in plan["spine"]}
    assert {"R1", "R2", "R6"} <= set(nodes["A"]["parents"])
    assert "A" in nodes["R1"]["children"]
    assert nodes["A"]["state"] == nodes["R1"]["state"] == "needs decision"
    assert nodes["V1"]["state"] == "agreed"
    assert len(nodes["V1"]["revisionShort"]) == 12
    assert plan["vision"]["id"] == "V1"
    assert plan["vision"]["title"] == nodes["V1"]["title"]
    assert plan["vision"]["detail"] == nodes["V1"]["detail"]
    assert all("flowchart" in text for text in plan["mermaid"].values())
    viewed = result["runs"][0]
    for key in (
        "runId",
        "status",
        "isMocked",
        "didExecuteRealProvider",
        "liveAcceptance",
        "planRevision",
        "planUpdate",
    ):
        assert viewed[key] == record[key]
    for key in ("reused", "redispatched", "invalidated"):
        assert viewed[key] == record["resumeSummary"][key]
    bead = viewed["beads"][0]
    assert bead["specRefs"] == ["R1@aaaaaaaa"]
    assert bead["specRefDetails"] == record["beads"]["A"]["specRefs"]
    assert viewed["planRevisionShort"] == revision[:12]
    assert all(bead["efficiency"] is None for bead in viewed["beads"])
    assert [event["kind"] for event in viewed["timeline"]] == [
        "plan-update",
        "attempt",
        "integration",
        "reuse",
        "integration",
        "invalidated",
    ]
    assert bead["provider"] == "codex" and bead["model"] == "test-model"
    assert bead["changedPaths"] == ["tipsplit/a.py"]
    assert [bead["integratedValidation"] for bead in viewed["beads"]] == [
        "passed",
        "failed",
        "unavailable",
    ]
    assert str(evidence / "A" / "integrated.json") in bead["evidencePaths"]
    message = {
        "subject": "Proof ready",
        "body": "See receipt",
        "from": "worker-A",
        "kind": "note",
        "refs": ["A"],
    }
    for messages in (result["mail"]["plan"], result["mail"]["runs"]["run-1"]):
        assert len(messages) == 1
        assert {key: messages[0][key] for key in message} == message
        assert messages[0]["displayBody"] == "See receipt"
        assert messages[0]["bodyDetails"] is None
    assert bead["evidenceItems"][1]["folder"] == str(evidence / "A")
    assert bead["evidenceItems"][2]["folder"] == str(evidence / "A")
    assert result["reviews"]["open"]["major"][0]["id"] == "F1"
    assert "not live acceptance" in result["labels"]["localRuns"]
    assert result["currentRunId"] == "run-1"
    assert viewed["current"] is True

    # A newer run of an unpublished revision must not replace the published run.
    other = root / "run-2"
    other.mkdir()
    record.update(runId="run-2", planRevision="different", startedAt="2099-01-01T00:00:00Z")
    (other / "run.json").write_text(json.dumps(record), encoding="utf-8")
    result = snapshot(workspace=workspace, runs_root=root)
    assert result["currentRunId"] == "run-1"
    superseded = next(run for run in result["runs"] if run["runId"] == "run-2")
    assert superseded["supersededBy"] == "run-1"
    assert superseded["reason"] == "different plan revision"
    assert sum(agent["beads"] for agent in result["currentAgents"]) == 3
    assert sum(agent["beads"] for agent in result["agents"]) == 6
    assert result["mail"]["openCount"] == 2
    assert len(result["mail"]["earlierRuns"]["run-2"]) == 1
    record["planRevision"] = revision
    (other / "run.json").write_text(json.dumps(record), encoding="utf-8")
    assert snapshot(workspace=workspace, runs_root=root)["currentRunId"] == "run-2"
    # With no published-revision match, selection still uses the newest start time.
    for path in (evidence / "run.json", other / "run.json"):
        different = json.loads(path.read_text(encoding="utf-8"))
        different["planRevision"] = "unpublished"
        path.write_text(json.dumps(different), encoding="utf-8")
    assert snapshot(workspace=workspace, runs_root=root)["currentRunId"] == "run-2"


def test_republished_revision_selects_canonical_current_run(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    with PlanStore(workspace) as store:
        store.import_spine(FIXTURE)
        bead = store.spine().node("A")
        bead["parents"].append("R6")
        store.upsert_node(bead)
        while store.phase() != "publish":
            phase = store.phase()
            if phase in ("align", "vision-lock"):
                source_only_confirmation(store, phase, statement="Confirmed")
            if phase == "roundtable":
                publication_evidence(store)
            store.advance(PHASES[PHASES.index(phase) + 1], by="planner")
        publication_evidence(store)
        revision_a = store.publish(by="operator", export_dir=workspace / "exports")
        original = store.spine().node("A")
        store.upsert_node({**original, "title": original["title"] + " revised"})
        publication_evidence(store)
        revision_b = store.publish(by="operator", export_dir=workspace / "exports")
        store.upsert_node(original)
        publication_evidence(store)
        assert store.publish(by="operator", export_dir=workspace / "exports") == revision_a
        assert store.status()["published_revision"] == revision_a
    root = tmp_path / "runs"
    for run_id, revision, started in (
        ("A", revision_a, "2026-01-01T00:00:00Z"),
        ("B", revision_b, "2026-01-02T00:00:00Z"),
    ):
        folder = root / run_id
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(
            json.dumps({"runId": run_id, "planRevision": revision, "startedAt": started}),
            encoding="utf-8",
        )
    before = _mtimes(tmp_path)
    result = snapshot(workspace=workspace, runs_root=root)
    assert result["currentRunId"] == "A"
    assert next(run for run in result["runs"] if run["runId"] == "B")["supersededBy"] == "A"
    assert "stale run evidence" not in result["verdict"]["sentence"]
    assert snapshot(workspace=workspace, runs_root=root) == result
    assert _mtimes(tmp_path) == before


def test_product_build_uses_current_run_repo_and_missing_build_is_explicit(tmp_path):
    from codex_harness.core.status_api import _verdict

    repo = tmp_path / "runs" / "current" / "repo"
    repo.mkdir(parents=True)
    for args in (
        ["init"],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "Product build",
        ],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--short=7", "HEAD"], text=True
    ).strip()
    evidence = repo.parent / "evidence"
    evidence.mkdir()
    (evidence / "run.json").write_text(json.dumps({"runId": "current"}), encoding="utf-8")
    plan = {
        "acceptance": {"scenarios": [{"lastResult": "pass"}]},
        "acceptanceEvidence": {"buildRevision": "c43419c" + "0" * 57},
    }
    before = _mtimes(tmp_path)
    current = snapshot(runs_root=tmp_path / "runs")["runs"][0]
    verdict = _verdict(plan, {}, current)
    assert f"on build {commit}" in verdict["sentence"]
    assert "on build c43419c" not in verdict["sentence"]
    assert {"label": "tree", "value": plan["acceptanceEvidence"]["buildRevision"]} in verdict[
        "chips"
    ]
    assert _mtimes(tmp_path) == before
    missing = tmp_path / "runs" / "missing"
    missing.mkdir()
    (missing / "run.json").write_text(json.dumps({"runId": "missing"}), encoding="utf-8")
    current = next(
        run for run in snapshot(runs_root=tmp_path / "runs")["runs"] if run["runId"] == "missing"
    )
    assert "build not recorded" in _verdict(plan, {}, current)["sentence"]


def test_missing_sections_do_not_initialize_storage(tmp_path):
    missing = tmp_path / "missing"
    result = snapshot(workspace=missing, runs_root=missing)
    assert result["plan"] == result["runs"] == result["reviews"] == "unavailable"
    assert result["mail"]["plan"] == "unavailable"
    assert not missing.exists()
    assert snapshot()["plan"] == "unavailable"
    result = snapshot(workspace=tmp_path, runs_root=tmp_path)
    assert result["plan"] == "unavailable" and result["runs"] == []
    assert not (tmp_path / ".codex-harness").exists()


def test_active_wal_is_visible_without_workspace_writes(tmp_path):
    with PlanStore(tmp_path) as store:
        store.import_spine(FIXTURE)
        store.ask("Committed in WAL")
        before = _mtimes(tmp_path)
        result = snapshot(workspace=tmp_path)
        assert result["plan"]["clarifications"][0]["question"] == "Committed in WAL"
        assert _mtimes(tmp_path) == before


def test_runs_bounded_newest_first_and_corrupt_piece(tmp_path):
    import os

    for index in range(12):
        run = tmp_path / str(index)
        run.mkdir()
        (run / "run.json").write_text(json.dumps({"runId": str(index)}))
        os.utime(run, (1000 + index, 1000 + index))
    (tmp_path / "11" / "run.json").write_text("{")
    result = snapshot(runs_root=tmp_path)
    assert len(result["runs"]) == 10
    assert [run["runId"] for run in result["runs"]] == [str(i) for i in range(11, 1, -1)]
    assert result["runs"][0]["status"] == "unavailable"


def test_server_loopback_get_only(tmp_path):
    with create_server(workspace=tmp_path, runs_root=tmp_path, port=0) as server:
        assert server.server_address[0] == "127.0.0.1"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", "/api/status")
            response = connection.getresponse()
            assert response.status == 200
            assert response.getheader("Content-Type").startswith("application/json")
            assert json.loads(response.read())["plan"] == "unavailable"
            connection.request("GET", "/")
            response = connection.getresponse()
            assert response.status == 200
            html = response.read().decode()
            assert 'id="mission-header"' in html
            assert 'id="spine-card"' in html
            connection.request("GET", "/board")
            response = connection.getresponse()
            assert response.status == 200
            html = response.read().decode()
            assert "setInterval(poll,5000)" in html
            assert "mermaid@10.9.1" in html and "not live acceptance" in html
            for landmark in ("<header", "<main", "<aside", "<footer"):
                assert landmark in html
            for section in (
                "overview",
                "phases",
                "beads-section",
                "runs-section",
                "inbox-section",
                "findings-section",
                "quality-checks",
                "diagrams-section",
                "last-updated",
            ):
                assert f'id="{section}"' in html
            assert "<details" in html and "navigator.clipboard.writeText" in html
            assert "prefers-color-scheme:light" in html and "max-width:899px" in html
            assert "failedPolls>=3" in html and "data-state-key" in html
            for method in ("POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "CUSTOM"):
                connection.request(method, "/api/status")
                response = connection.getresponse()
                assert response.status == 405
                response.read()
            connection.request("GET", "/missing")
            response = connection.getresponse()
            assert response.status == 404
            response.read()
            connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_run_timeline_and_efficiency_projection(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    efficiency = {"commands": 12, "exploration": 4, "tokens": 2400}
    record = {
        "runId": "run",
        "planRevision": "a" * 64,
        "planUpdate": {"paths": ["tests/test_money.py"]},
        "resumeSummary": {"redispatched": ["A"], "reused": ["B"]},
        "claimSummary": {"retained": [{"claimId": "claim-B"}]},
        "claims": [{"claimId": "claim-B", "bead": "B"}],
        "efficiency": {"B": {"commands": 0, "exploration": 0, "tokens": 0}},
        "beads": {
            "A": {
                "provider": "codex",
                "attempt": {"startedAt": "now"},
                "candidate": {"validated": True},
                "integrated": {"validation": {"passed": True}},
                "efficiency": efficiency,
            },
            "B": {"revalidation": {"validation": {"passed": True}}},
            "C": {"candidate": {"validated": False, "commit": "b" * 40}},
            "D": {"candidate": {"validated": False, "commit": None}},
        },
    }
    (run / "run.json").write_text(json.dumps(record), encoding="utf-8")
    before = _mtimes(tmp_path)
    projected = snapshot(runs_root=tmp_path)["runs"][0]
    assert _mtimes(tmp_path) == before
    assert [event["text"] for event in projected["timeline"]] == [
        "Plan updated (tests/test_money.py)",
        "A re-dispatched (codex)",
        "A candidate validated",
        "A integrated validation passed",
        "B reused (claims retained)",
        "B final validation passed",
        "C candidate validation failed",
    ]
    assert [bead["efficiency"] for bead in projected["beads"]] == [
        efficiency,
        {"commands": 0, "exploration": 0, "tokens": 0},
        None,
        None,
    ]


def test_empty_run_has_empty_timeline_and_unknown_revision(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "run.json").write_text('{"runId":"run"}', encoding="utf-8")
    projected = snapshot(runs_root=tmp_path)["runs"][0]
    assert projected["timeline"] == []
    assert projected["planRevisionShort"] is None


def test_status_current_run_start_time_and_run_id_fallback(tmp_path):
    records = [
        {"runId": "z-old", "startedAt": "2026-01-01T00:00:00Z", "planRevision": "same"},
        {"runId": "a-new", "startedAt": "2026-02-01T00:00:00Z", "planRevision": "same"},
    ]
    for record in records:
        run = tmp_path / record["runId"]
        run.mkdir()
        (run / "run.json").write_text(json.dumps(record), encoding="utf-8")
    before = _mtimes(tmp_path)
    result = snapshot(runs_root=tmp_path)
    assert _mtimes(tmp_path) == before
    assert result["currentRunId"] == "a-new"
    older = next(run for run in result["runs"] if not run["current"])
    assert older["supersededBy"] == "a-new"
    assert older["reason"] == "older run of the same plan revision"
    for record in records:
        del record["startedAt"]
        (tmp_path / record["runId"] / "run.json").write_text(json.dumps(record), encoding="utf-8")
    assert snapshot(runs_root=tmp_path)["currentRunId"] == "z-old"


def test_structured_replan_mail_has_plain_text_projection(tmp_path):
    payload = {"bead": "A", "reason": "integrated validation failed", "planRevision": "a" * 64}
    body = json.dumps(payload)
    mailbox = Mailbox(tmp_path)
    mailbox.send(
        from_agent="harness-dispatcher",
        to_agent="planner",
        subject="Replan: A",
        body=body,
        kind="replan-trigger",
    )
    before = _mtimes(tmp_path)
    message = snapshot(workspace=tmp_path)["mail"]["plan"][0]
    assert _mtimes(tmp_path) == before
    assert message["body"] == body
    assert message["displayBody"] == "integrated validation failed"
    assert message["bodyDetails"] == payload


def test_real_dispatcher_record_and_resume_reach_snapshot(tmp_path, monkeypatch):
    from codex_harness.core.dispatch import run_plan

    def no_provider(*args):
        raise AssertionError("No real providers are authorized")

    monkeypatch.setattr("codex_harness.core.dispatch.get_adapter", no_provider)
    record = run_plan(FIXTURE.parent, work_root=tmp_path, mock_provider="reference")
    before = _mtimes(tmp_path)
    viewed = snapshot(runs_root=tmp_path)["runs"][0]
    assert _mtimes(tmp_path) == before
    assert viewed["runId"] == record["runId"]
    assert viewed["isMocked"] is True and viewed["didExecuteRealProvider"] is False
    assert viewed["liveAcceptance"]["claimed"] is False
    assert {event["kind"] for event in viewed["timeline"]} >= {
        "attempt",
        "candidate",
        "integration",
    }
    assert {bead["id"]: bead["efficiency"] for bead in viewed["beads"]} == {
        key: bead["attempt"]["efficiency"] for key, bead in record["beads"].items()
    }
    resumed = run_plan(
        FIXTURE.parent,
        work_root=tmp_path,
        resume_run_dir=tmp_path / record["runId"],
        mock_provider="reference",
    )
    before = _mtimes(tmp_path)
    viewed = snapshot(runs_root=tmp_path)["runs"][0]
    assert _mtimes(tmp_path) == before
    assert viewed["reused"] == resumed["resumeSummary"]["reused"]
    assert any(event["text"].endswith("reused (claims retained)") for event in viewed["timeline"])


def test_status_attempt_efficiency_real_shape_and_agent_aggregation(tmp_path):
    # Shape and metrics from watercli's real dogfood dispatcher receipt.
    efficiency = {
        "commands": 2,
        "failedCommands": 1,
        "deniedCommands": 2,
        "redTestRuns": 3,
        "errorCommands": 1,
        "explorationCommands": 1,
        "fileChanges": 1,
        "inputTokens": 69932,
        "cachedInputTokens": 45696,
        "outputTokens": 776,
        "turns": 1,
        "durationSeconds": 53.831378,
    }
    for index in range(2):
        run = tmp_path / str(index)
        run.mkdir()
        record = {
            "runId": str(index),
            "beads": {
                "A": {
                    "provider": "codex",
                    "model": "gpt-6.1-sol",
                    "status": "running",
                    "attempt": {"efficiency": efficiency},
                }
            },
            "efficiency": {"A": {"commands": 99}},
        }
        (run / "run.json").write_text(json.dumps(record), encoding="utf-8")
    before = _mtimes(tmp_path)
    result = snapshot(runs_root=tmp_path)
    assert _mtimes(tmp_path) == before
    assert all(run["beads"][0]["efficiency"] == efficiency for run in result["runs"])
    assert result["agents"] == [
        {
            "provider": "codex",
            "model": "gpt-6.1-sol",
            "beads": 2,
            "active": 2,
            "activity": {
                "stuckCount": None,
                "unknownCount": 2,
                "terminalCount": None,
                "lastObservedActivityAt": None,
                "maxSecondsSinceActivity": None,
                "noProgressThresholdSeconds": 300,
            },
            **{
                key: efficiency[key] * 2
                for key in (
                    "commands",
                    "failedCommands",
                    "deniedCommands",
                    "redTestRuns",
                    "errorCommands",
                    "explorationCommands",
                    "inputTokens",
                    "cachedInputTokens",
                    "outputTokens",
                    "durationSeconds",
                )
            },
        }
    ]
    record["beads"]["A"]["efficiency"] = {"commands": 0}
    (run / "run.json").write_text(json.dumps(record), encoding="utf-8")
    projected = next(item for item in snapshot(runs_root=tmp_path)["runs"] if item["runId"] == "1")
    assert projected["beads"][0]["efficiency"] == {"commands": 0}


def test_status_spine_vision_gates_and_older_database_are_read_only(tmp_path):
    with PlanStore(tmp_path) as store:
        store.import_spine(FIXTURE)
        vision = store.spine().node("V1")
        vision["fields"].update(first_user_moment="Split a bill", scope_in="Fair cents")
        store.upsert_node(vision)
        store.connection.execute("ALTER TABLE meta DROP COLUMN vision_lock_stale")
        store.connection.execute("ALTER TABLE meta DROP COLUMN locked_vision_json")
        for table in ("scenarios", "runs", "reviews", "findings", "approvals"):
            store.connection.execute(f"DROP TABLE acceptance_{table}")
    before = _mtimes(tmp_path)
    plan = snapshot(workspace=tmp_path)["plan"]
    assert _mtimes(tmp_path) == before
    assert plan["vision"]["state"] == "needs decision"
    assert plan["vision"]["firstUserMoment"] == "Split a bill"
    assert plan["vision"]["scopeIn"] == "Fair cents"
    assert any(node["state"] == "draft" for node in plan["spine"])


def test_status_empty_spine_and_unknown_efficiency(tmp_path):
    with PlanStore(tmp_path):
        pass
    before = _mtimes(tmp_path)
    result = snapshot(workspace=tmp_path)
    assert _mtimes(tmp_path) == before
    assert result["plan"]["spine"] == []
    assert result["plan"]["vision"] is None
    assert result["plan"]["reviewRecorded"] is False
    assert result["plan"]["acceptance"] == {
        "scenarios": [],
        "lastRunSubject": None,
        "findings": [],
        "approvedBy": None,
        "isProxy": None,
        "accepted": False,
        "realUserAccepted": False,
        "recordedUserAccepted": False,
        "humanOriginVerified": False,
        "approvalOrigin": None,
        "invalidReason": "vision-unlocked",
        "invalidMessage": "The current vision is not locked.",
        "repo": None,
        "next": "harness_record_confirmation gate=vision-lock",
        "approval": None,
        "reviews": [],
        "reviewAttempts": [],
        "executionAssessments": {
            "schema": "seldon/execution-assessed-support-v1",
            "members": [],
            "fullVisionHeld": True,
            "closureEligible": False,
            "scope": (
                "current registered AF execution support only; native final acceptance unchanged"
            ),
        },
    }
    assert result["agents"] == "unavailable"
    run = tmp_path / "runs" / "run"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"runId": "run", "beads": {"A": {}}}))
    agents = snapshot(runs_root=run.parent)["agents"]
    assert agents[0]["commands"] is None
    assert agents[0]["model"] == "unknown"


@pytest.mark.parametrize("subject", ["product", "oracle"])
def test_canonical_acceptance_run_reaches_read_only_snapshot(tmp_path, subject):
    import sys

    from codex_harness.core.acceptance import run_acceptance

    repo = tmp_path / "product"
    repo.mkdir()
    with PlanStore(tmp_path) as store:
        store.import_spine(FIXTURE)
        vision = store.spine().node("V1")
        vision["fields"].update(
            first_user_moment={
                "title": "First use",
                "steps": [f'"{sys.executable}" -c "print(123)"'],
                "expect": {"stdout": "123"},
            },
            scope_in=[],
        )
        store.upsert_node(vision)
        source_only_confirmation(store, "align", statement="Confirmed")
        store.advance("expand", by="planner")
        store.advance("vision-lock", by="planner")
        source_only_confirmation(store, "vision-lock", statement="Confirmed")
        run = run_acceptance(store, repo, subject=subject)
        expected = store.status()["acceptance"]
        defined_steps = store.acceptance_scenarios()[0]["steps"]
    before = _mtimes(tmp_path)
    acceptance = snapshot(workspace=tmp_path)["plan"]["acceptance"]
    assert _mtimes(tmp_path) == before
    assert acceptance == expected
    assert acceptance["scenarios"][0]["id"] == run["results"][0]["id"]
    assert acceptance["lastRunSubject"] == subject
    assert acceptance["scenarios"][0]["lastResultSubject"] == subject
    assert acceptance["scenarios"][0]["lastResult"].startswith("pass")
    assert acceptance["scenarios"][0]["runId"] == run["id"]
    assert acceptance["scenarios"][0]["steps"] == defined_steps
    assert run["results"][0]["steps"][0]["exit"] == 0
    assert acceptance["accepted"] is acceptance["realUserAccepted"] is False
    # The actual canonical acceptance producer reaches the new verdict projection.
    result = snapshot(workspace=tmp_path)
    assert result["plan"]["acceptanceEvidence"]["runId"] == run["id"]
    assert "1/1 acceptance scenarios pass" in result["verdict"]["sentence"]
    if subject == "oracle":
        assert "dry run" in acceptance["invalidMessage"]
        assert "dry run" in acceptance["scenarios"][0]["lastResultNote"]


def test_verdict_proxy_failure_and_planning():
    from codex_harness.core.status_api import _verdict

    plan = {
        "acceptance": {
            "accepted": True,
            "isProxy": True,
            "humanOriginVerified": False,
            "scenarios": [{"lastResult": "pass"}] * 12,
        },
        "acceptanceEvidence": {
            "runId": "accept-1",
            "buildRevision": "d68eba3abcdef",
            "reviewId": "review-1",
        },
        "shortRevisions": {"plan_revision": "rev"},
        "published_revision": "rev",
    }
    current = {"runId": "run-1", "planRevision": "rev", "productCommit": "abc1234"}
    result = _verdict(plan, {"deferred": {"minor": [{}] * 6}}, current)
    assert result["tone"] == "amber"
    assert result["sentence"] == (
        "Accepted by proxy · 12/12 acceptance scenarios pass on build abc1234"
        " · 6 known gaps deferred · origin not verified"
    )
    assert result["chips"][2] == {"label": "Acceptance run", "value": "accept-1"}
    assert {"label": "tree", "value": "d68eba3abcdef"} in result["chips"]
    plan["acceptance"]["scenarios"][0] = {"lastResult": "fail"}
    result = _verdict(plan, {}, current)
    assert result["tone"] == "red" and "1 failing scenarios" in result["sentence"]
    assert _verdict({}, {}, None)["tone"] == "neutral"
    assert _verdict({}, {}, None)["sentence"] == "Planning"
    plan["acceptance"].update(isProxy=False, scenarios=[{"lastResult": "pass"}])
    assert _verdict(plan, {}, current)["tone"] == "green"
    assert _verdict(plan, {"open": {"minor": [{}]}}, current)["tone"] == "amber"
    assert _verdict(plan, {"open": {"critical": [{}]}}, current)["tone"] == "red"


def test_timeline_absolute_and_relative_dependency_path(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    record = {
        "runId": "run",
        "beads": {
            "A": {
                "provider": "codex",
                "attempt": {
                    "startedAt": "2026-01-01T00:00:00Z",
                    "finishedAt": "2026-01-01T00:00:10Z",
                    "efficiency": {"durationSeconds": 10},
                },
            },
            "B": {
                "provider": "claude",
                "dependsOn": ["A"],
                "attempt": {
                    "startedAt": "2026-01-01T00:00:12Z",
                    "finishedAt": "2026-01-01T00:00:17Z",
                    "efficiency": {"durationSeconds": 5},
                },
            },
        },
        "integrationValidation": {"passed": True, "claim": {"at": "2026-01-01T00:00:20Z"}},
    }
    path = root / "run.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    before = _mtimes(tmp_path)
    result = snapshot(runs_root=tmp_path)["runs"][0]["gantt"]
    assert _mtimes(tmp_path) == before
    assert result["timing"] == "absolute"
    assert result["criticalPath"] == ["A", "B"]
    assert result["rows"][0]["duration"] == 10
    assert result["rows"][-1]["duration"] is None
    assert result["rows"][-1]["start"] == result["rows"][-1]["end"]
    for bead in record["beads"].values():
        del bead["attempt"]["startedAt"], bead["attempt"]["finishedAt"]
    path.write_text(json.dumps(record), encoding="utf-8")
    result = snapshot(runs_root=tmp_path)["runs"][0]["gantt"]
    assert result["timing"] == "relative"
    assert [(row["start"], row["end"]) for row in result["rows"][:2]] == [(0, 10), (10, 15)]


def test_phase_history_proxy_and_empty_events(tmp_path):
    with PlanStore(tmp_path) as store:
        store.import_spine(FIXTURE)
        # Reconstruct a historical supplied-words row/event solely for projection.
        # It must remain attributable as originally recorded and earns no fresh credit.
        with store._transaction():
            store._approve("align", "user-proxy", "Scope agreed by proxy")
            store.connection.execute("UPDATE meta SET phase='expand'")
            store._event(
                "phase-advanced",
                {
                    "from": "align",
                    "to": "expand",
                    "by": "planner",
                    "statement": None,
                    "plan_revision": store._revision(),
                },
            )
    before = _mtimes(tmp_path)
    history = snapshot(workspace=tmp_path)["plan"]["phaseHistory"]
    assert _mtimes(tmp_path) == before
    assert history[0]["approvedBy"] == "user-proxy"
    assert history[0]["isProxy"] is True
    assert history[0]["evidence"] == "Scope agreed by proxy"
    empty = tmp_path / "empty"
    empty.mkdir()
    with PlanStore(empty):
        pass
    history = snapshot(workspace=empty)["plan"]["phaseHistory"]
    assert len(history) == len(PHASES)
    assert all(item["passedAt"] is None for item in history)


def test_efficiency_trend_compares_only_same_plan_and_preserves_unknowns(tmp_path):
    for run_id, revision, commands in [("1", "same", 10), ("2", "same", 15), ("3", "other", 100)]:
        folder = tmp_path / run_id
        folder.mkdir()
        record = {
            "runId": run_id,
            "planRevision": revision,
            "beads": {"A": {"attempt": {"efficiency": {"commands": commands}}}},
        }
        (folder / "run.json").write_text(json.dumps(record), encoding="utf-8")
    # The latest run belongs to another plan, so it has no comparison.
    assert snapshot(runs_root=tmp_path)["efficiencyTrend"]["previousRunId"] is None
    (tmp_path / "3" / "run.json").write_text(json.dumps({"runId": "0"}), encoding="utf-8")
    trend = snapshot(runs_root=tmp_path)["efficiencyTrend"]
    assert trend["previousRunId"] == "1"
    assert trend["deltas"]["commands"] == 5
    assert trend["current"]["inputTokens"] is None
    assert trend["deltas"]["inputTokens"] is None
