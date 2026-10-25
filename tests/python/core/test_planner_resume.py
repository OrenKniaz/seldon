"""Persisted inspection/reattachment and fail-closed new admission.

Caller declarations cannot launch, and an ambiguous original cannot be replaced.
Existing jobs tests use a
store double; these tests exercise canonical PlanStore producers and real job I/O.
Synthetic native forms/audit records/controller returns are not live user/provider
or acceptance proof. Public transport owns baseline red; missing imports do not.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.planning_support import (
    source_controller_roundtable,
    source_only_confirmation,
)
from tests.python.core.test_planner_resolution import _at_roundtable

from codex_harness.core import planner_jobs
from codex_harness.core.planner_audits import audit_synchronously
from codex_harness.core.record_io import read_record_json
from codex_harness.core.roundtable import run_roundtable

project = test_planner_replay_gaps.project


def _resume(*args, **kwargs):
    from codex_harness.core.planner_resume import resume_plan

    return resume_plan(*args, **kwargs)


def _published(store):
    _at_roundtable(store)
    assert audit_synchronously(store)["proofCreditGranted"]
    source_controller_roundtable(
        store,
        process_stub=lambda *_: (0, "[]"),
        reviewers=[dict(name="Alpha", provider="codex")],
        executable_for=lambda provider: provider,
        max_rounds=4,
    )
    store.advance("publish", by="source-test")
    revision = store.publish(
        by="source-test",
        export_dir=store.workspace / "export",
        proof_level="prospective",
        reason="SOURCE-ONLY bookkeeping fixture, not audit execution",
    )
    workers = [dict(bead="B1", provider="codex", model="gpt-6.1-sol", effort="medium")]
    return dict(
        expected_revision=revision,
        workers=workers,
        execution_authorized=True,
        reserved_worker_calls=1,
        deadline=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        timeout=5,
        call_cap=1,
    )


def _record_execution_original(store, options):
    """Synthetic native-store original for setup; actual pending-wire proof is elsewhere."""
    from codex_harness.core.planner_execution import complete_execution, prepare_execution

    proposal = {
        key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")
    }
    prepared = prepare_execution(store, **proposal)
    result = complete_execution(
        store,
        prepared["request_id"],
        "accept",
        {
            "approve": True,
            "statement": "SOURCE-ONLY native execution form",
            "conditions": "",
        },
    )
    assert result["state"] == "approved" and result["human_identity_verified"] is False


def _seed_original(store, options):
    """Existing ordinary job setup only; resume never supplies its admission."""
    _record_execution_original(store, options)
    return planner_jobs.start_worker_job(
        store,
        options["expected_revision"],
        options["workers"],
        timeout=options["timeout"],
        call_cap=options["call_cap"],
    )


def test_missing_plan_inspection_creates_nothing(tmp_path):
    workspace = tmp_path / "missing"
    result = _resume(workspace)
    assert result["state"] == "held" and result["reasonCode"] == "plan-missing"
    assert result["canonicalStatus"] is None and result["observationPersisted"] is False
    assert result["nextAction"]["kind"] == "start_plan"
    assert result["nextAction"]["mcpTool"] == "harness_plan_start"
    assert not workspace.exists()


def test_caller_claims_cannot_admit_new_worker_job(project, monkeypatch):
    """Even complete caller declarations cannot establish independent authority.

    Real published store and jobs I/O expose the old unsafe admission; only Popen
    returns are doubled to prevent a controller/provider. No missing-import red.
    """
    options = _published(project)
    admissions, launches = [], []
    ordinary_start = planner_jobs.start_worker_job

    def observed_start(*args, **kwargs):
        admissions.append(1)
        return ordinary_start(*args, **kwargs)

    monkeypatch.setattr(planner_jobs, "start_worker_job", observed_start)
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *a, **k: launches.append((a, k)) or SimpleNamespace(pid=12345),
    )
    before = list(project.connection.iterdump())
    result = _resume(project.workspace, **options)
    assert admissions == [] and launches == []
    assert (
        result["state"] == "held"
        and result["reasonCode"] == "native-execution-confirmation-required"
    )
    assert result["execution"]["authorizationVerified"] is False
    assert result["execution"]["capacityVerified"] is False
    assert result["nextAction"]["kind"] == "request_native_execution"
    assert not (project.workspace / ".codex-harness/worker-jobs").exists()
    assert list(project.connection.iterdump()) == before


def test_approved_early_phase_continues_planning_before_audit(project, monkeypatch):
    # Canonical native-store form double and ordinary advance, not a DB phase
    # rewrite. Neither this form nor the resulting state proves a human response.
    vision = project.spine().node("V1")
    vision["fields"].update(
        vision="Return an answer",
        users="A command-line user",
        scope_out="Networking",
        assumptions=[],
    )
    project.upsert_node(vision)
    source_only_confirmation(project, "align")
    project.advance("expand", by="source-test")
    monkeypatch.setattr(
        planner_jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("Launch forbidden")
    )
    result = _resume(project.workspace)
    assert result["state"] == "held" and result["reasonCode"] == "planning-phase-required"
    assert result["nextAction"] == {
        "kind": "inspect_plan_phase",
        "phase": "expand",
        "mcpTool": "harness_plan_status",
        "cliCommand": "status",
    }
    assert not (project.workspace / ".codex-harness/worker-jobs").exists()


@pytest.mark.parametrize(
    "missing", ["revision", "authorization", "calls", "bool-calls", "deadline", "wall", "workers"]
)
def test_real_publication_stays_held_without_explicit_bounded_context(
    project, monkeypatch, missing
):
    options = _published(project)
    if missing == "revision":
        options.pop("expected_revision")
    elif missing == "authorization":
        options["execution_authorized"] = False
    elif missing == "calls":
        options["reserved_worker_calls"] = None
    elif missing == "bool-calls":
        options["reserved_worker_calls"] = True
    elif missing == "deadline":
        options["deadline"] = None
    elif missing == "workers":
        options["workers"] = None
    else:
        options["deadline"] = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    monkeypatch.setattr(
        planner_jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("Launch forbidden")
    )
    before = list(project.connection.iterdump())
    result = _resume(project.workspace, **options)
    assert result["state"] == "held" and result["job"] is None
    assert result["execution"]["capacityVerified"] is False
    action = result["nextAction"]
    assert action["kind"] == "request_native_execution"
    assert action["mcpTool"] == "harness_plan_resume" and action["cliCommand"] == "resume"
    assert action["reason"] and result["execution"]["authorizationVerified"] is False
    assert read_record_json(result["observationPath"]) == result
    assert list(project.connection.iterdump()) == before
    assert not (project.workspace / ".codex-harness/worker-jobs").exists()


def test_real_job_admission_reconnect_preserves_ambiguous_original(project, monkeypatch):
    options = _published(project)
    launches = []
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *a, **k: launches.append((a, k)) or SimpleNamespace(pid=12345),
    )
    _seed_original(project, options)
    first = _resume(project.workspace, **options)
    assert first["state"] == "reattached" and len(launches) == 1
    directory = project.workspace / ".codex-harness/worker-jobs" / options["expected_revision"]
    originals = {
        p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }
    project.ask("A real unresolved clarification after admission", ["R1"])
    requirement = project.spine().node("R1")
    requirement["text"] += " changed after original admission"
    project.upsert_node(requirement)
    again = _resume(project.workspace, expected_revision=options["expected_revision"])
    assert again["state"] == "reattached" and len(launches) == 1
    assert again["job"]["relation"] == "historical"
    assert again["job"]["status"] == again["liveness"] == "unknown"
    assert again["harnessDeliveryProven"] is False
    assert {
        p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    } == originals
    assert first["observationId"] != again["observationId"]
    assert read_record_json(first["observationPath"]) == first
    # Corrupt saved evidence is not absence. Preserve those exact bytes and do
    # not substitute another admission even with full caller launch context.
    request = directory / "request.json"
    request.write_bytes(b'{"SOURCE-ONLY truncated original":')
    corrupted = request.read_bytes()
    refused = _resume(project.workspace, **options)
    assert refused["state"] == "held" and len(launches) == 1
    assert request.read_bytes() == corrupted


def test_execute_phase_failed_review_and_actual_questions_still_hold(project, monkeypatch):
    options = _published(project)
    project.advance("execute", by="source-test")
    failed = source_controller_roundtable(
        project,
        process_stub=lambda *_: (1, "SOURCE-ONLY failed Beta"),
        reviewers=[dict(name="Beta", provider="claude")],
        executable_for=lambda provider: provider,
    )
    monkeypatch.setattr(
        planner_jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("Launch forbidden")
    )
    result = _resume(project.workspace, **options)
    assert result["state"] == "held" and result["nextAction"]["kind"] == "review_required"
    assert result["nextAction"]["detailRoute"]["roundId"] == failed["roundId"]
    assert result["nextAction"]["detailRoute"]["mcpTool"] == "harness_review"
    question = project.ask("Which behavior is intended?", ["R1"])
    result = _resume(project.workspace, **options)
    assert result["nextAction"]["kind"] == "existing_canonical_blocker"
    assert result["nextAction"]["questionIds"] == [question]


def test_admission_exception_attaches_saved_original_without_retry(project, monkeypatch):
    options = _published(project)
    launches = []

    def refuse(*args, **kwargs):
        launches.append(1)
        raise OSError("SOURCE-ONLY admission failure")

    monkeypatch.setattr(planner_jobs.subprocess, "Popen", refuse)
    with pytest.raises(OSError, match="SOURCE-ONLY admission failure"):
        _seed_original(project, options)
    result = _resume(project.workspace, **options)
    assert result["state"] == "reattached" and len(launches) == 1
    assert result["job"]["status"] == "unknown"
    assert _resume(project.workspace, **options)["state"] == "reattached"
    assert len(launches) == 1


@pytest.mark.parametrize("blocker", ["approval", "finding", "current-audit"])
def test_real_canonical_blockers_override_caller_execution_context(project, monkeypatch, blocker):
    options = _published(project)
    if blocker == "approval":
        node = project.spine().node("V1")
        node["fields"]["users"] = "Changed intended user"
        project.upsert_node(node)
        expected = "existing_canonical_blocker"
    elif blocker == "finding":
        run_roundtable(
            project,
            reviewers=[dict(name="Finding", provider="codex")],
            executable_for=lambda provider: provider,
            runner=lambda *_: (
                0,
                '[{"node_id":"R1","severity":"minor",'
                '"finding":"A real recorded gap","suggestion":"Repair it"}]',
            ),
        )
        expected = "reconcile_findings"
    else:
        node = project.spine().node("R1")
        node["text"] += " current content changed"
        project.upsert_node(node)
        project.compile_template(project.workspace / "template")
        expected = "audit_required"
    monkeypatch.setattr(
        planner_jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("Launch forbidden")
    )
    before = list(project.connection.iterdump())
    result = _resume(project.workspace, **options)
    assert result["state"] == "held" and result["nextAction"]["kind"] == expected
    if blocker == "current-audit":
        assert result["nextAction"]["auditMode"] == "unknown"
    assert list(project.connection.iterdump()) == before
    assert not (project.workspace / ".codex-harness/worker-jobs").exists()


def test_observation_refusal_preserves_actual_admission_and_reconnect(project, monkeypatch):
    options = _published(project)
    launches = []
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *a, **k: launches.append((a, k)) or SimpleNamespace(pid=12345),
    )
    _seed_original(project, options)
    # Only the observation path is blocked by a regular file. All real writers
    # remain active; ordinary jobs request/launch publication is unaffected.
    (project.workspace / ".codex-harness/planner-resume").write_text("source-only obstruction")
    result = _resume(project.workspace, **options)
    assert result["state"] == "reattached"
    assert result["observationPersisted"] is False
    assert result["observationError"] == "observation-write-failed"
    assert result["nextAction"]["kind"] == "inspect_worker_job"
    directory = project.workspace / ".codex-harness/worker-jobs" / options["expected_revision"]
    originals = {
        p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }
    assert (directory / "request.json").is_file() and (directory / "launch.json").is_file()
    reconnected = _resume(project.workspace)
    assert reconnected["state"] == "reattached" and len(launches) == 1
    assert {
        p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    } == originals
