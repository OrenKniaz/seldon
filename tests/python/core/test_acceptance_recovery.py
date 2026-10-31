"""SOURCE-ONLY recorder regressions. Synthetic protocol is never native proof."""

import json
import sqlite3
import sys
from uuid import uuid4

import pytest
from tests.python.core.test_acceptance_candidate_packet import candidate
from tests.python.core.test_acceptance_candidate_packet import project as _project

from codex_harness.core import acceptance, roundtable
from codex_harness.core import acceptance_recovery as recovery
from codex_harness.core.plan_store import PlanStoreError

REVIEWERS = [
    {"name": "source-codex", "provider": "codex", "model": "source-only", "effort": "high"},
    {"name": "source-claude", "provider": "claude", "model": "source-only"},
]


@pytest.fixture
def project(tmp_path):
    yield from _project.__wrapped__(tmp_path)


def protocol(provider, *, text=None):
    """Explicit synthetic main-session telemetry; no executable is launched."""
    session = str(uuid4())
    text = text or json.dumps(
        [{"node_id": "R1", "severity": "note", "finding": "SOURCE ONLY gap", "suggestion": ""}]
    )
    if provider == "codex":
        events = [
            {"type": "thread.started", "thread_id": session},
            {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
            {"type": "turn.completed"},
        ]
        stderr = (
            "Codex initialized with event: SessionConfiguredEvent { "
            f"session_id: SessionId {{ uuid: {session} }}, "
            f"thread_id: ThreadId {{ uuid: {session} }}, "
            'forked_from_id: None, parent_thread_id: None, model: "source-only", '
            "reasoning_effort: Some(High) }"
        )
    else:
        events = [
            {"type": "system", "subtype": "init", "session_id": session, "model": "source-only"},
            {
                "type": "assistant",
                "session_id": session,
                "message": {"model": "source-only", "content": [{"type": "text", "text": text}]},
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": session,
                "permission_denials": [],
                "result": text,
            },
        ]
        stderr = ""
    return 0, "\n".join(json.dumps(event) for event in events), stderr


def interrupted(project, monkeypatch, *, mocked=False, response=None, before_review=None):
    store, repo = project
    candidate(store, repo, reviewers=REVIEWERS)
    if before_review:
        before_review(store, repo)
        from tests.python.core.planning_support import source_only_b

        source_only_b(store, reviewers=REVIEWERS)
    calls = []
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)

    def source_only(launch, *_):
        assert len(calls) < 2, "A third synthetic review intent is forbidden"
        provider = REVIEWERS[len(calls)]["provider"]
        calls.append(provider)
        return response(provider) if response else protocol(provider)

    monkeypatch.setattr(roundtable, "_run", source_only)
    store.connection.execute(
        "CREATE TRIGGER source_only_abort BEFORE INSERT ON acceptance_reviews "
        "BEGIN SELECT RAISE(ABORT, 'SOURCE ONLY lost final aggregate'); END"
    )
    with pytest.raises((sqlite3.IntegrityError, PlanStoreError), match="lost final aggregate"):
        acceptance.acceptance_review(
            store, repo, reviewers=REVIEWERS, **({"runner": source_only} if mocked else {})
        )
    assert calls == ["codex", "claude"]
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_reviews").fetchone()[0] == 0
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_findings").fetchone()[0] == 0
    rows = list(
        store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='acceptance-review-started'"
        )
    )
    assert len(rows) == 1, "Completed ordinary review work needs its ORIGINAL durable anchor"
    attempt = json.loads(rows[0][0])["attemptId"]
    store.connection.execute("DROP TRIGGER source_only_abort")

    def forbidden(*_args, **_kwargs):
        pytest.fail("Recovery must not discover, construct or launch another provider")

    for name in ("_run", "_review_dossier", "_launch_review", "_executable"):
        monkeypatch.setattr(roundtable, name, forbidden)
    return store, repo, attempt


def test_lost_transaction_recovers_nonempty_findings_without_third_review(project, monkeypatch):
    store, repo, attempt = interrupted(project, monkeypatch)
    summary = acceptance.acceptance_status(store)["reviewAttempts"][0]
    assert summary["id"] == attempt and summary["completedSlots"] == 2
    assert summary["admitted"] is False
    result = acceptance.acceptance_review(store, repo, resume=attempt)
    assert result["id"] == attempt and len(result["findings"]) == 2
    assert result["isMocked"] is False and result["packetBound"] is True
    assert len(result["receiptEventIds"]) == 2
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_reviews").fetchone()[0] == 1
    assert acceptance.acceptance_status(store)["accepted"] is False
    store.resolve_findings(
        [{"id": result["findings"][0]["id"], "status": "deferred", "resolution": "SOURCE ONLY"}]
    )
    events = store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    second = acceptance.acceptance_review(store, repo, resume=attempt)
    assert second["findingIds"] == result["findingIds"]
    assert second["findings"][0]["status"] == "deferred"
    assert store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events


@pytest.mark.parametrize("kind", ["product", "packet", "context"])
def test_changed_unknown_and_incomplete_originals_refuse_without_launch(project, monkeypatch, kind):
    def response(provider):
        code, output, stderr = protocol(provider)
        if kind == "final-prose":
            return protocol(provider, text="SOURCE ONLY prose before []")
        if provider == "claude" and kind in ("tool-use", "claude-denial"):
            events = [json.loads(line) for line in output.splitlines()]
            if kind == "tool-use":
                events[1]["message"]["content"].append({"type": "tool_use", "name": "unknown_mcp"})
            else:
                events[-1]["permission_denials"] = [{"tool_name": "unknown_mcp"}]
            output = "\n".join(json.dumps(event) for event in events)
        if provider == "codex":
            if kind == "unknown-runtime":
                return 0, "[]", ""
            if kind == "wrong-model":
                stderr = stderr.replace('model: "source-only"', 'model: "other"')
            if kind == "wrong-effort":
                stderr = stderr.replace("Some(High)", "Some(Low)")
            if kind == "codex-warning":
                output += '\n{"type":"item.completed","item":{"type":"error","message":"warning"}}'
        return code, output, stderr

    def before(store, repo):
        if kind == "incomplete-protected-body":
            tests = store.workspace / "template" / "tests"
            tests.mkdir(parents=True, exist_ok=True)
            (tests / "unreferenced.py").write_text("# SOURCE ONLY missing required body\n")

    store, repo, attempt = interrupted(
        project, monkeypatch, mocked=kind == "mocked", response=response, before_review=before
    )
    root = store.path.parent / "acceptance-review-attempts" / attempt
    if kind in ("packet", "stdout", "stderr", "missing"):
        path = next(root.glob("*." + ("stdout" if kind == "missing" else kind)))
        if kind == "missing":
            path.unlink()
        else:
            path.write_bytes(path.read_bytes() + b"SOURCE ONLY tamper")
    elif kind.startswith("duplicate-"):
        event_kind = recovery.STARTED if kind == "duplicate-start" else recovery.COMPLETED
        row = recovery._events(store, event_kind, attempt)[0][1]
        store._event(event_kind, row)
    elif kind == "product":
        (repo / "tiny.py").write_text("# changed source\n")
    elif kind == "source":
        monkeypatch.setattr(recovery, "source_identity", lambda: "changed controller source")
    elif kind == "context":
        store.ask("SOURCE ONLY R3 remains unanswered", ["R1"])
    elif kind == "protected":
        (store.workspace / "template").mkdir(exist_ok=True)
        (store.workspace / "template" / "PLAN.md").write_text("changed protected bytes")
    elif kind == "plan":
        node = store.spine().node("R1")
        node["text"] += "SOURCE ONLY changed requirement"
        store.upsert_node(node)
    elif kind == "scenarios":
        scenarios = store.acceptance_scenarios()
        scenarios[0]["title"] += " changed"
        store.save_acceptance_scenarios(scenarios)
    elif kind.startswith("shared-session"):
        # A second registered slot cannot reuse another attempt's native session.
        header = recovery._events(store, recovery.STARTED, attempt)[0][1]
        slot = header["slots"][0]
        other = json.loads(json.dumps(header))
        other["attemptId"] = uuid4().hex
        other_root = root.parent / other["attemptId"]
        other_root.mkdir()
        store._event(recovery.STARTED, other)
        completion = recovery._events(store, recovery.COMPLETED, attempt)[0][1]
        completion = {
            **completion,
            "attemptId": other["attemptId"],
            "headerHash": recovery.digest(other),
        }
        for suffix in ("stdout", "stderr"):
            raw = (root / (slot["id"] + "." + suffix)).read_bytes()
            (other_root / (slot["id"] + "." + suffix)).write_bytes(raw)
        store._event(recovery.COMPLETED, completion)
        other_output = other_root / (slot["id"] + ".stdout")
        if kind == "shared-session-missing-original":
            other_output.unlink()
        elif kind == "shared-session-changed-original":
            other_output.write_text("SOURCE ONLY changed original")
    with pytest.raises(PlanStoreError):
        acceptance.acceptance_review(store, repo, resume=attempt)
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_reviews").fetchone()[0] == 0
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_findings").fetchone()[0] == 0


def test_final_writer_rechecks_context_and_rolls_back_findings(project, monkeypatch):
    store, repo, attempt = interrupted(project, monkeypatch)
    original = acceptance._commit_acceptance_review

    def racing(*args, **kwargs):
        store.ask("SOURCE ONLY context changed after validation", ["R1"])
        return original(*args, **kwargs)

    monkeypatch.setattr(acceptance, "_commit_acceptance_review", racing)
    with pytest.raises(PlanStoreError):
        acceptance.acceptance_review(store, repo, resume=attempt)
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_findings").fetchone()[0] == 0


def test_interleaved_winning_resume_returns_same_aggregate(project, monkeypatch):
    store, repo, attempt = interrupted(project, monkeypatch)
    original = acceptance._commit_acceptance_review
    winner = []
    entered = []

    def interleaved(*args, **kwargs):
        if not entered:
            entered.append(True)
            winner.append(acceptance.acceptance_review(store, repo, resume=attempt))
        return original(*args, **kwargs)

    monkeypatch.setattr(acceptance, "_commit_acceptance_review", interleaved)
    result = acceptance.acceptance_review(store, repo, resume=attempt)
    assert result["findingIds"] == winner[0]["findingIds"]
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_reviews").fetchone()[0] == 1
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_findings").fetchone()[0] == 2
    assert len(recovery._events(store, "acceptance-reviewed", attempt)) == 1
