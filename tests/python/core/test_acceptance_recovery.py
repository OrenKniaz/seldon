"""SOURCE-ONLY recorder regressions. Synthetic protocol is never native proof."""

import json
import sqlite3
import stat
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from tests.python.core.test_acceptance_candidate_packet import candidate
from tests.python.core.test_acceptance_candidate_packet import project as _project

from codex_harness.core import acceptance, roundtable
from codex_harness.core import acceptance_recovery as recovery
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_cli import main
from codex_harness.core.plan_store import PlanStoreError
from codex_harness.core.spine_render import render_markdown

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
    candidate(store, repo)
    if before_review:
        before_review(store, repo)
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


@pytest.mark.parametrize(
    "kind",
    [
        "packet",
        "stdout",
        "stderr",
        "missing",
        "duplicate-completion",
        "duplicate-start",
        "product",
        "source",
        "context",
        "protected",
        "plan",
        "scenarios",
        "mocked",
        "unknown-runtime",
        "wrong-model",
        "wrong-effort",
        "codex-warning",
        "shared-session",
        "shared-session-missing-original",
        "shared-session-changed-original",
        "incomplete-protected-body",
        "final-prose",
        "tool-use",
        "claude-denial",
    ],
)
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
            tests.mkdir(parents=True)
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
        (store.workspace / "template").mkdir()
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


def test_capture_io_failure_is_fatal_before_a_second_provider(project, monkeypatch):
    store, repo = project
    candidate(store, repo)
    calls = []
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)

    def returned(*_):
        calls.append(True)
        return protocol("codex")

    original = recovery._write

    def fail_output(root, name, data):
        if name.endswith(".stdout"):
            raise OSError("SOURCE ONLY seal failure: context overflow is not a retry")
        return original(root, name, data)

    monkeypatch.setattr(roundtable, "_run", returned)
    monkeypatch.setattr(recovery, "_write", fail_output)
    with pytest.raises(PlanStoreError, match="could not seal"):
        acceptance.acceptance_review(store, repo, reviewers=REVIEWERS)
    assert calls == [True]
    assert recovery._events(store, recovery.COMPLETED) == []
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_reviews").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["denied", "timeout", "start", "invalid"])
def test_failed_execution_does_not_become_recoverable(project, monkeypatch, kind):
    import subprocess

    store, repo = project
    candidate(store, repo)
    calls = []
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)

    def fail(*_):
        calls.append(True)
        if kind == "timeout":
            raise subprocess.TimeoutExpired("SOURCE ONLY", 1)
        if kind == "start":
            raise OSError("SOURCE ONLY start failed")
        if kind == "denied":
            return 1, "", "SOURCE ONLY permission denied"
        return 0, "not valid JSON", ""

    monkeypatch.setattr(roundtable, "_run", fail)
    with pytest.raises(ValueError):
        acceptance.acceptance_review(store, repo, reviewers=REVIEWERS)
    attempt = recovery._events(store, recovery.STARTED)[0][1]["attemptId"]
    with pytest.raises(PlanStoreError):
        acceptance.acceptance_review(store, repo, resume=attempt)
    assert calls == [True]


def test_supported_cli_mcp_resume_and_original_r3_gate(project, monkeypatch, capsys):
    store, repo, attempt = interrupted(
        project,
        monkeypatch,
        before_review=lambda store, _: store.ask("SOURCE ONLY R3 calendar decision", ["R1"]),
    )
    assert (
        main(
            [
                "--workspace",
                str(store.workspace),
                "--json",
                "accept-review",
                "--repo",
                str(repo),
                "--resume",
                attempt,
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["id"] == attempt
    assert store.acceptance_review_guard(result["runId"]) == "plan-clarifications-open"
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool("harness_accept_review", {"repo": str(repo), "resume": attempt})
        assert not reply["isError"] and reply["structuredContent"]["id"] == attempt
    finally:
        server.close()


@pytest.mark.parametrize("attempt", ["../foreign", "f" * 32, "F" * 32, "", "a/b"])
def test_no_arbitrary_external_receipt_registration(project, attempt):
    store, repo = project
    with pytest.raises(PlanStoreError):
        acceptance.acceptance_review(store, repo, resume=attempt)


def test_resume_rejects_execution_overrides(project):
    store, repo = project
    with pytest.raises(ValueError, match="original registered"):
        acceptance.acceptance_review(store, repo, resume="f" * 32, runner=lambda *_: (0, "[]"))


def test_ordinary_overflow_still_runs_but_cannot_be_recovered(project, monkeypatch):
    store, repo = project
    candidate(store, repo)
    calls = []
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)

    def source_only(*_):
        calls.append(True)
        if len(calls) == 1:
            return 1, "", "SOURCE ONLY context window exceeded"
        return protocol("codex" if len(calls) == 2 else "claude")

    monkeypatch.setattr(roundtable, "_run", source_only)
    result = acceptance.acceptance_review(store, repo, reviewers=REVIEWERS)
    assert len(calls) == 3 and result["reviewers"][0]["reshardedFrom"]
    assert len(recovery._events(store, recovery.ABANDONED, result["id"])) == 1
    with pytest.raises(PlanStoreError, match="unrecoverable"):
        acceptance.acceptance_review(store, repo, resume=result["id"])
    assert len(calls) == 3


def test_changed_actual_argv_cannot_be_sealed_under_original_registration(project, monkeypatch):
    store, repo = project
    candidate(store, repo)
    original = roundtable._launch_review
    constructed, launched = [], []
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)

    def construct(*args, **kwargs):
        launch = original(*args, **kwargs)
        constructed.append(True)
        if len(constructed) > 2:
            launch = replace(launch, command=(*launch.command, "SOURCE ONLY changed argv"))
        return launch

    def source_only(*_):
        launched.append(True)
        return protocol("codex")

    monkeypatch.setattr(roundtable, "_launch_review", construct)
    monkeypatch.setattr(roundtable, "_run", source_only)
    with pytest.raises(PlanStoreError, match="differs from its registration"):
        acceptance.acceptance_review(store, repo, reviewers=REVIEWERS)
    assert launched == [True]
    assert recovery._events(store, recovery.COMPLETED) == []


def test_omitted_body_cannot_be_claimed_from_untrusted_context(project, monkeypatch):
    def before(store, _):
        tests = store.workspace / "template" / "tests"
        tests.mkdir(parents=True)
        body = "# SOURCE ONLY omitted proof body\n"
        (tests / "unreferenced.py").write_text(body)
        store.ask(body, ["R1"])
        store.ask("tests/unreferenced.py", ["R1"])

    store, repo, attempt = interrupted(project, monkeypatch, before_review=before)
    with pytest.raises(PlanStoreError, match="Protected body"):
        acceptance.acceptance_review(store, repo, resume=attempt)


def test_protected_line_endings_are_exact_inputs(project, monkeypatch):
    def before(store, _):
        template = store.workspace / "template"
        template.mkdir()
        for name, body in render_markdown(store.spine()).items():
            (template / name).write_bytes(body.encode("utf-8"))

    store, repo, attempt = interrupted(project, monkeypatch, before_review=before)
    path = store.workspace / "template" / "PLAN.md"
    raw = path.read_bytes()
    assert b"\n" in raw and b"\r" not in raw
    path.write_bytes(raw.replace(b"\n", b"\r\n"))
    with pytest.raises(PlanStoreError, match="stale"):
        acceptance.acceptance_review(store, repo, resume=attempt)


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


def test_journal_ancestor_reparse_is_refused(project, monkeypatch):
    """Simulated Windows metadata: no junction creation or privilege change."""
    store, _ = project
    original = Path.lstat

    def metadata(path, *args, **kwargs):
        if path == store.path.parent:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", metadata)
    root = recovery._directory(store, uuid4().hex)
    with pytest.raises(ValueError, match="reparse"):
        recovery._safe(root, root)


def test_protected_json_fields_remain_ordinary_data(project, monkeypatch):
    def before(store, _):
        template = store.workspace / "template"
        template.mkdir()
        for name, body in render_markdown(store.spine()).items():
            (template / name).write_bytes(body.encode("utf-8"))
        module = store.spine().node(store.spine().ids("test")[0])["fields"]["module"]
        path = template / "tests" / module
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            'null = None\n{"renderedDocs": "ordinary data"}\n'
            '{"renderedDocs": ["ordinary data"]}\n{"renderedDocs": null}\n'
        )

    store, repo, attempt = interrupted(project, monkeypatch, before_review=before)
    result = acceptance.acceptance_review(store, repo, resume=attempt)
    assert result["id"] == attempt


def test_non_utf8_protected_helper_refuses_recovery_only(project, monkeypatch):
    def before(store, _):
        template = store.workspace / "template"
        template.mkdir()
        for name, body in render_markdown(store.spine()).items():
            (template / name).write_bytes(body.encode("utf-8"))
        module = store.spine().node(store.spine().ids("test")[0])["fields"]["module"]
        path = template / "tests" / module
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import helper\n")
        (template / "helper.py").write_bytes(b"# SOURCE ONLY invalid UTF8 helper \xff\n")

    store, repo, attempt = interrupted(project, monkeypatch, before_review=before)
    with pytest.raises(PlanStoreError, match="UTF-8"):
        acceptance.acceptance_review(store, repo, resume=attempt)
