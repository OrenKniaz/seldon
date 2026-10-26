"""Caller-mediated chat authority: real stores/consumers, not human-authentication proof."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.planning_support import source_only_confirmation
from tests.python.core.test_planner_resume import _published

from codex_harness.core import planner_confirmations as confirmation
from codex_harness.core import planner_execution as execution
from codex_harness.core import planner_jobs
from codex_harness.core.plan_store import open_plan_store

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"
project = test_planner_replay_gaps.project


def reply(request, quote="Approve"):
    """Explicit source-only external-reply fixture, never a live/human attestation."""
    return dict(
        **({"gate": request["gate"]} if "gate" in request else {}),
        expected_revision=request.get(
            "plan_revision", request.get("scope", {}).get("publicationRevision")
        ),
        quoted_message=quote,
        quote_sha256=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        actor_label="source-only automated tester; human identity unverified",
    )


@pytest.fixture
def store(tmp_path):
    with open_plan_store(tmp_path) as saved:
        saved.import_spine(FIXTURE)
        yield saved


@pytest.mark.parametrize("gate,target", [("align", "expand"), ("vision-lock", "features")])
def test_decline_then_fresh_chat_reply_reaches_real_planning_consumer(store, gate, target):
    """Protect new explicit authority; native-only tests intentionally reject caller quotes."""
    if gate == "vision-lock":
        source_only_confirmation(store, "align")
        store.advance("expand", by="source-test")
        store.advance("vision-lock", by="source-test")
    native = confirmation.prepare_confirmation(store, gate)
    confirmation.complete_confirmation(store, native["request_id"], "decline")
    question = confirmation.prepare_chat_confirmation(store, gate)
    assert question["request_id"] != native["request_id"]
    assert question["channel"] == "chat-confirmation"
    assert confirmation.approval_credit(store, gate, question["plan_revision"]) is None
    with pytest.raises(ValueError):
        store.advance(target, by="source-test")
    answer = reply(question, "Approve\n")
    outcome = confirmation.complete_chat_confirmation(store, question["request_id"], **answer)
    assert outcome["state"] == "approved"
    assert outcome["provenance"] == "chat-confirmation"
    assert outcome["human_identity_verified"] is False
    assert outcome["chat"]["quoted_message"] == "Approve\n"
    assert outcome["chat"]["quote_sha256"] == answer["quote_sha256"]
    assert outcome["chat"]["source_verified"] is False
    store.advance(target, by="source-test")
    with open_plan_store(store.workspace) as reopened:
        credit = confirmation.approval_credit(reopened, gate, question["plan_revision"])
        assert credit["by"] == "chat-confirmation"
        assert reopened.phase() == target


@pytest.mark.parametrize(
    "quote,state",
    [("No", "declined"), ("Cancel", "cancel"), ("Changes: clarify scope", "conditional")],
)
def test_chat_negative_or_conditional_reply_never_unblocks_gate(store, quote, state):
    """Catch No-to-yes or conditions losing their meaning; native tests cover another channel."""
    question = confirmation.prepare_chat_confirmation(store, "align")
    outcome = confirmation.complete_chat_confirmation(
        store, question["request_id"], **reply(question, quote)
    )
    assert outcome["state"] == state and outcome["approval_id"] is None
    with pytest.raises(ValueError):
        store.advance("expand", by="source-test")


@pytest.mark.parametrize(
    "fault", ["hash", "revision", "gate", "ambiguous", "native-channel", "contradictory-approve"]
)
def test_chat_mismatch_or_forged_channel_cannot_write_credit(store, fault):
    """Protect request/quote binding; original native wire tests have no chat-record arguments."""
    question = (
        confirmation.prepare_confirmation(store, "align")
        if fault == "native-channel"
        else confirmation.prepare_chat_confirmation(store, "align")
    )
    answer = reply(question, "No, I do not approve" if fault == "ambiguous" else "No")
    if fault == "hash":
        answer["quote_sha256"] = hashlib.sha256(b"Approve").hexdigest()
    elif fault == "revision":
        answer["expected_revision"] = "wrong"
    elif fault == "gate":
        answer["gate"] = "vision-lock"
    elif fault == "contradictory-approve":
        answer["approve"] = True
    before = list(store.connection.iterdump())
    with pytest.raises((ValueError, TypeError)):
        confirmation.complete_chat_confirmation(store, question["request_id"], **answer)
    assert list(store.connection.iterdump()) == before


def test_chat_reply_is_single_use_and_latest_question_bound(store):
    """Catch reusing an answered or superseded chat card; native replay tests lack chat IDs."""
    old = confirmation.prepare_chat_confirmation(store, "align")
    latest = confirmation.prepare_chat_confirmation(store, "align")
    result = confirmation.complete_chat_confirmation(store, old["request_id"], **reply(old))
    assert result["state"] == "stale" and result["approval_id"] is None
    confirmation.complete_chat_confirmation(store, latest["request_id"], **reply(latest))
    with pytest.raises(ValueError):
        confirmation.complete_chat_confirmation(store, latest["request_id"], **reply(latest))


def test_canonical_status_reports_chat_origin_truthfully(store):
    """Catch native-origin projection for chat credit; native-only status tests miss it.

    This PlanStore-owned seam must pass after MAJOR's serial integration, not a mock.
    """
    question = confirmation.prepare_chat_confirmation(store, "align")
    confirmation.complete_chat_confirmation(store, question["request_id"], **reply(question))
    assert store.status()["approval"]["origin"] == "chat-confirmation"


def test_chat_execution_producer_admits_one_canonical_job_and_consumes_grant(project, monkeypatch):
    """Catch chat admission/duplicate-launch bugs; native fixtures miss the new channel."""
    options = _published(project)
    proposal = {
        key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")
    }
    native = execution.prepare_execution(project, **proposal)
    execution.complete_execution(project, native["request_id"], "decline")
    question = execution.prepare_chat_execution(project, **proposal)
    launches = []
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *a, **k: launches.append(1) or SimpleNamespace(pid=12345),
    )
    with pytest.raises(ValueError):
        planner_jobs.start_worker_job(
            project, options["expected_revision"], options["workers"], timeout=5, call_cap=1
        )
    assert launches == []
    outcome = execution.complete_chat_execution(project, question["request_id"], **reply(question))
    assert outcome["provenance"] == "chat-confirmation" and not outcome["human_identity_verified"]
    assert launches == []  # Recording a decision itself never admits a worker.
    job = planner_jobs.start_worker_job(
        project, options["expected_revision"], options["workers"], timeout=5, call_cap=1
    )
    assert len(launches) == 1
    assert execution.consumed_execution(
        project, question["request_id"], question["scope"], job["jobId"]
    )
    assert (
        execution.consumed_execution_provenance(
            project, question["request_id"], question["scope"], job["jobId"]
        )
        == "chat-confirmation"
    )
    assert (
        planner_jobs.start_worker_job(
            project, options["expected_revision"], options["workers"], timeout=5, call_cap=1
        )["jobId"]
        == job["jobId"]
    )
    assert len(launches) == 1
    assert job["nativeApproval"] == "unproven"
    assert job["approvalProvenance"] == "chat-confirmation"
    assert job["approvalAssurance"] == "caller-mediated"
    assert job["actorIdentityVerified"] is False
    assert (
        planner_jobs.worker_job_status(project.workspace, job["jobId"])["approvalProvenance"]
        == "chat-confirmation"
    )


def test_native_job_status_keeps_its_actual_original_channel(project, monkeypatch):
    """Catch classifying all new jobs as chat; old native tests miss provenance projection."""
    options = _published(project)
    request = execution.prepare_execution(
        project, options["expected_revision"], options["workers"], timeout=5, call_cap=1
    )
    execution.complete_execution(
        project,
        request["request_id"],
        "accept",
        {"approve": True, "statement": "SOURCE-ONLY native response", "conditions": ""},
    )
    monkeypatch.setattr(
        planner_jobs.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=12345)
    )
    job = planner_jobs.start_worker_job(
        project, options["expected_revision"], options["workers"], timeout=5, call_cap=1
    )
    assert job["nativeApproval"] == "recorded"
    assert job["approvalProvenance"] == "mcp-client-elicitation"
    assert job["approvalAssurance"] == "client-reported"
    assert job["actorIdentityVerified"] is False


@pytest.mark.parametrize("case", ["No", "template-change"])
def test_chat_execution_refusal_or_changed_inputs_never_creates_job(project, monkeypatch, case):
    """Protect worker scope from fallback bypass; planning tests cannot cover those inputs."""
    options = _published(project)
    proposal = {
        key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")
    }
    question = execution.prepare_chat_execution(project, **proposal)
    if case == "template-change":
        path = project.workspace / "template/answer.py"
        path.write_text(path.read_text() + "\n# changed after question\n")
    result = execution.complete_chat_execution(
        project, question["request_id"], **reply(question, "No" if case == "No" else "Approve")
    )
    assert result["state"] == ("declined" if case == "No" else "stale")
    monkeypatch.setattr(
        planner_jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("Launch forbidden")
    )
    with pytest.raises(ValueError):
        planner_jobs.start_worker_job(
            project, options["expected_revision"], options["workers"], timeout=5, call_cap=1
        )
    assert not (project.workspace / ".codex-harness/worker-jobs").exists()
