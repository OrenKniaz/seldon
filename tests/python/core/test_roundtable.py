"""Injected reviewers through real PlanStore, rendered spine and quality checks."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.python.core.planning_support import (
    source_only_b,
    source_only_confirmation,
    source_only_expansion,
    source_only_vision_lock,
)
from tests.python.core.test_semantic_wiring import semantic_plan as _semantic_plan

from codex_harness.core import roundtable
from codex_harness.core.plan_quality import quality_report
from codex_harness.core.plan_store import PHASES, PlanStore, PlanStoreError
from codex_harness.core.roundtable import (
    findings,
    run_roundtable,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit" / "spine.json"
REVIEWERS = [
    {"name": "codex-reviewer", "provider": "codex", "model": "gpt-6.1-sol", "effort": "high"},
    {"name": "claude-reviewer", "provider": "claude", "model": "claude-sonnet-5-5"},
]


QUALITY_SNAPSHOT_NOTICE = (
    "Quality snapshot captured before this review request. Missing or stale "
    "review evidence describes the pre-request gate, not failure of this "
    "requested review attempt. Open findings and unmet gates still require action.\n\n"
)

DEFAULT_RUNNER = roundtable._run


@pytest.fixture(autouse=True)
def never_launch(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Tests must never launch a real provider")

    monkeypatch.setattr(roundtable, "_run", forbidden)


@pytest.fixture
def store(tmp_path):
    with PlanStore(tmp_path) as store:
        store.import_spine(FIXTURE)
        bead = store.spine().node("A")
        bead["parents"].append("R6")
        store.upsert_node(bead)
        import shutil

        shutil.copytree(FIXTURE.parent / "template", tmp_path / "template")
        source_only_expansion(store)
        while store.phase() != "roundtable":
            phase = store.phase()
            if phase in ("align", "vision-lock"):
                source_only_confirmation(store, phase, statement="Approve")
            if phase == "plan":
                source_only_b(store, reviewers=REVIEWERS)
            store.advance(PHASES[PHASES.index(phase) + 1], by="planner")
        yield store


def finding(node_id="R1", severity="major"):
    return {
        "node_id": node_id,
        "severity": severity,
        "finding": "Real verification is missing",
        "suggestion": "Add an end-to-end proof of the requirement",
    }


@pytest.fixture
def scope_store(tmp_path_factory, monkeypatch):
    for store, _ in _semantic_plan.__wrapped__(tmp_path_factory, monkeypatch):
        source_only_vision_lock(store)
        while store.phase() != "roundtable":
            if store.phase() == "plan":
                source_only_b(store, reviewers=REVIEWERS)
            store.advance(PHASES[PHASES.index(store.phase()) + 1], by="source-test")
        store.compile_template(store.workspace / "template")
        yield store


def scoped_review(
    store,
    monkeypatch,
    *,
    classification="beyond-brief",
    reason="Extra test depth",
    required=False,
    mocked=False,
):
    """Local transport double: real packet/parse/store, no provider or supplied gate verdict."""
    item = dict(finding("R"), classification=classification, scope_reason=reason)
    if classification == "beyond-brief":
        item["finding"] = "Consider optional random-input greeting fuzz scenarios"
    if classification is None:
        item = finding("R")

    def source_process(launch, cwd, timeout):
        # The child emits reviewer input, never a PASS or publication decision.
        payload = (
            dict(item, classification="defect-in-plan" if launch.cli == "codex" else "beyond-brief")
            if classification == "mixed"
            else item
        )
        extra = (
            [dict(finding("F"), classification="defect-in-plan", scope_reason="Clarify greeting")]
            if required
            else []
        )
        response = reviewer_stream(launch.cli, [payload, *extra], uses_tools=False)
        program = "import sys; sys.stdin.read(); print(sys.argv[1])"
        child = subprocess.run(
            [sys.executable, "-B", "-c", program, response],
            input=launch.stdin_text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=cwd,
            timeout=timeout,
        )
        return child.returncode, child.stdout, child.stderr

    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)
    monkeypatch.setattr(roundtable, "_run", source_process)
    return run_roundtable(store, reviewers=REVIEWERS, runner=source_process if mocked else None)


def repair_required_scope(store):
    node = store.spine().node("F")
    node["text"] += " Clarify required greeting"
    store.upsert_node(node)
    store.resolve_findings(
        [
            dict(id=row["id"], status="resolved", resolution="Clarified greeting")
            for row in findings(store)
            if row["node_id"] == "F"
        ]
    )
    store.compile_template(store.workspace / "template")


def test_reviewer_optionality_reaches_publication_and_closure_without_hiding(
    scope_store, monkeypatch
):
    """Catch open advisory invalidating required repairs; old tests separated these flows."""
    store = scope_store
    scoped_review(store, monkeypatch, required=True)
    repair_required_scope(store)
    rows = findings(store, "open", expand=True)
    assert len(rows) == 2 and all(row["classification"] == "beyond-brief" for row in rows)
    with PlanStore(store.workspace) as reopened:
        assert check(reopened)["status"] == "pass"
        source_only_b(reopened, reviewers=REVIEWERS)
        reopened.advance("publish", by="source-test")
        assert (
            reopened.publish(by="source-test", export_dir=store.workspace / "published")
            == reopened.manifest_revision()
        )
        assert reopened.connection.execute("SELECT count(*) FROM review_runs").fetchone()[0] == 2
        assert findings(reopened, "open", expand=True) == rows


# Recorded provider event shapes; tool payloads are trimmed to the relevant fields.
PACKET_EVENTS = {
    "claude": [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {"file_path": "app.py"},
                    },
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "Bash",
                        "input": {"command": "cat app.py"},
                    },
                ]
            },
        },
    ],
    "codex": [
        {
            "type": "item.started",
            "item": {"id": "c1", "type": "command_execution", "command": "cat app.py"},
        },
        {
            "type": "item.completed",
            "item": {
                "id": "c1",
                "type": "command_execution",
                "command": "cat app.py",
                "exit_code": 0,
            },
        },
        {"type": "item.completed", "item": {"id": "c2", "type": "file_read", "path": "app.py"}},
    ],
}


def reviewer_stream(provider, items, *, uses_tools=True):
    final = json.dumps(items)
    event = (
        {"type": "result", "result": final}
        if provider == "claude"
        else {"type": "item.completed", "item": {"type": "agent_message", "text": final}}
    )
    events = [*(PACKET_EVENTS[provider] if uses_tools else []), event]
    return "\n".join(json.dumps(item) for item in events)


def check(store):
    return next(item for item in quality_report(store)["checks"] if item["id"] == "roundtable")


def run(store, output, **kwargs):
    return run_roundtable(
        store,
        reviewers=REVIEWERS[:1],
        runner=lambda launch, cwd, timeout: (0, output),
        **kwargs,
    )


def test_plan_change_during_review_refused(store):
    def runner(launch, cwd, timeout):
        node = store.spine().node("R1")
        node["text"] += " Changed requirement"
        store.upsert_node(node)
        return 0, json.dumps([finding()])

    with pytest.raises(PlanStoreError) as caught:
        run_roundtable(store, reviewers=REVIEWERS[:1], runner=runner)
    assert caught.value.code == "roundtable-plan-changed"
    assert not findings(store)
    assert not store.connection.execute(
        "SELECT 1 FROM events WHERE kind='roundtable-completed'"
    ).fetchone()


def test_event_failure_rolls_back_findings(store, monkeypatch):
    roundtable._table(store)
    original = store._event

    def fail(kind, payload):
        if kind == "roundtable-completed":
            raise RuntimeError("Event storage failure")
        original(kind, payload)

    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError, match="Event storage"):
        run(store, json.dumps([finding()]))
    assert not findings(store)
    assert not store.connection.execute("SELECT 1 FROM review_runs").fetchone()
