"""Caller-mediated chat authority: real stores/consumers, not human-authentication proof."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.core import test_planner_replay_gaps, test_semantic_wiring
from tests.python.core.planning_support import (
    source_controller_roundtable,
    source_only_confirmation,
    source_only_expansion,
)
from tests.python.core.test_planner_confirmations import recorded_reviewer
from tests.python.core.test_planner_resume import _published

from codex_harness.core import planner_confirmations as confirmation
from codex_harness.core import planner_execution as execution
from codex_harness.core import planner_jobs
from codex_harness.core import provider_call_budget as budget
from codex_harness.core.plan_store import PHASES, open_plan_store
from codex_harness.core.planner_confirmations import (
    approval_credit,
    complete_chat_confirmation,
    prepare_chat_confirmation,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"
project = test_planner_replay_gaps.project
semantic_plan = test_semantic_wiring.semantic_plan


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
        source_only_expansion(store)
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


def test_uppercase_quote_hash_is_accepted_and_stored_normalized():
    """A real planner sent PowerShell-style uppercase hex for an exact 'Approve' and was refused."""
    quote = "Approve"
    upper = hashlib.sha256(quote.encode("utf-8")).hexdigest().upper()
    _, _, evidence = confirmation._chat_evidence("r", "rev", quote, upper, "user")
    assert evidence["quote_sha256"] == upper.lower()
    with pytest.raises(ValueError, match="exact UTF-8 bytes"):
        confirmation._chat_evidence("r", "rev", quote + "\n", upper, "user")


def combined_reply(store, request, text="Approve"):
    return complete_chat_confirmation(
        store,
        request["request_id"],
        gate=request["gate"],
        expected_revision=request["plan_revision"],
        quoted_message=text,
        quote_sha256=hashlib.sha256(text.encode()).hexdigest(),
        actor_label="source-only Oren",
    )


@pytest.mark.parametrize(
    "case",
    [
        "absent",
        "negative",
        "conditional",
        "partial",
        "stale",
        "superseded",
        "interrupted",
        "control",
        "approved",
    ],
)
def test_combined_align_grants_both_or_neither(semantic_plan, monkeypatch, case):
    """Catch partial atomic credit and replay; separate gate tests have no second credit."""
    store, _ = semantic_plan
    source_only_expansion(store)
    request = prepare_chat_confirmation(store, "align+vision-lock")
    if case == "stale":
        node = store.spine().node("V1")
        node["text"] += " changed"
        store.upsert_node(node)
    if case == "superseded":
        prepare_chat_confirmation(store, "align+vision-lock")
    if case == "control":
        store._event("source-only-control-change", {})
    original = store._event

    def interrupt(kind, value):
        original(kind, value)
        if kind == "approved" and value["gate"] == "vision-lock":
            raise RuntimeError("interrupted second credit")

    if case == "interrupted":
        monkeypatch.setattr(store, "_event", interrupt)
    if case in {"partial", "interrupted"}:
        with pytest.raises((ValueError, RuntimeError)):
            combined_reply(store, request, "Approve Align only" if case == "partial" else "Approve")
    elif case != "absent":
        result = combined_reply(
            store,
            request,
            {"negative": "No", "conditional": "Changes: first revise scope"}.get(case, "Approve"),
        )
        assert (result["state"] == "approved") == (case == "approved")
        with pytest.raises(ValueError) as error:
            combined_reply(store, request)
        assert error.value.code == "plan-confirmation-already-consumed"
    for gate in ("align", "vision-lock"):
        assert bool(approval_credit(store, gate, store._revision())) == (case == "approved")
    if case == "interrupted":
        assert store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    if case == "approved":
        for phase in ("expand", "vision-lock", "features"):
            store.advance(phase, by="source-test")


def prepare_combined_b(store, selection="new"):
    """Canonical fixture producer; external replies/reviews remain source simulations."""
    source_only_expansion(store)
    combined_reply(store, prepare_chat_confirmation(store, "align+vision-lock"))
    for phase in PHASES[1 : PHASES.index("plan") + 1]:
        store.advance(phase, by="source-test")
    if selection != "none":
        _, reviewer, _ = recorded_reviewer(store)
        if selection == "existing":
            store.confirm_reviewer_selection(
                reviewer["id"],
                store._revision(),
                approve=True,
                statement="SOURCE-ONLY existing reviewer selection",
                conditions="",
            )
    manifest = store.compile_template(store.workspace / "template")
    budget.register_planning_review_budget(store, "combined-source-test", 2)
    options = dict(
        expected_revision=manifest["planRevision"],
        max_parallel=1,
        timeout=5,
        call_cap=2,
        workers=[
            dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium") for b in ("C", "B")
        ],
    )
    request = prepare_chat_confirmation(store, "plan-execution", execution=options)
    return request, options


@pytest.mark.parametrize(
    "change", [None, "stale-files", "candidate", "files", "workers", "cap", "failed-review"]
)
def test_bundle_through_review_publication_and_once_only_execution(
    semantic_plan, monkeypatch, change
):
    """Catch missing stage binding/re-consent; existing execution tests start after publication."""
    store, _ = semantic_plan
    request, options = prepare_combined_b(store)
    assert approval_credit(store, "execution", store._revision()) is None
    with pytest.raises(ValueError):
        budget.claim_planning_review_start(store, {})
    if change == "stale-files":
        (store.workspace / "template/changed.txt").write_text("changed after display")
        assert combined_reply(store, request)["state"] == "stale"
        assert not list(store.connection.execute("SELECT 1 FROM approvals WHERE gate='execution'"))
        return
    combined_reply(store, request)
    projected = store.outcome_waves()["reviewerSelection"]
    assert projected["selection"]["confirmation"]["approvalId"] == request["request_id"]
    store.advance("roundtable", by="source-test")
    source_controller_roundtable(
        store,
        process_stub=lambda *_: (1, "failed") if change == "failed-review" else (0, "[]"),
        reviewers=[dict(name="primary", provider="codex", model="gpt-6.1-sol", effort="high")],
        executable_for=lambda p: p,
        max_rounds=1,
    )
    if change == "failed-review":
        with pytest.raises(ValueError):
            store.advance("publish", by="source-test")
        return
    assert budget.planning_review_budget(store)["spentAdmissions"] == 1
    if change == "candidate":
        node = store.spine().node("B")
        node["title"] += " changed"
        store.upsert_node(node)
    if change == "files":
        (store.workspace / "template/unseen.txt").write_text("unapproved input")
    if change in {"candidate", "files"}:
        with pytest.raises(ValueError):
            store.advance("publish", by="source-test")
            store.publish(by="source-test", export_dir=store.workspace / "published")
        assert execution._records(store, execution.REQUEST) == []
        return
    store.advance("publish", by="source-test")
    revision = store.publish(by="source-test", export_dir=store.workspace / "published")
    assert revision == options["expected_revision"]
    if change == "workers":
        options["workers"][0]["model"] = "changed-model"
    if change == "cap":
        options["call_cap"] += 1
    scope = execution.execution_scope(store, **options)
    if change:
        with pytest.raises(ValueError, match="grant-required"):
            execution.consume_execution(store, scope, revision)
    else:
        launches = []
        monkeypatch.setattr(
            planner_jobs.subprocess,
            "Popen",
            lambda *a, **k: launches.append(1) or SimpleNamespace(pid=12345),
        )
        planner_jobs.start_worker_job(store, **options)
        planner_jobs.start_worker_job(store, **options)
        assert launches == [1]
        assert execution.consumed_execution(store, request["request_id"], scope, revision)
        with pytest.raises(ValueError, match="consumed"):
            execution.consume_execution(store, scope, revision)


@pytest.mark.parametrize("selection", ["none", "new", "existing"])
@pytest.mark.parametrize("choice", ["unlisted", "exact"])
def test_cs1_public_roundtable_requires_displayed_reviewer(
    semantic_plan, monkeypatch, selection, choice
):
    """CS-1 public reproduction: optional selection escaped the former positive-only B tests.

    Catch unseen tuple spend at the JSON-RPC consumer; a launch spy is the only
    process double. Also exercise the shared acceptance-review admission ceiling.
    """
    from tests.python.core.test_mcp_planner import workspace_wire

    from codex_harness.core import roundtable
    from codex_harness.core.mcp_planner import PlannerServer

    store, _ = semantic_plan
    question, _ = prepare_combined_b(store, selection)
    combined_reply(store, question)
    reviewer = dict(name="primary", provider="codex", model="gpt-6.1-sol", effort="high")
    listed = question["scopes"]["review-budget"]["reviewers"]
    assert listed == ([] if selection == "none" else [reviewer])
    if listed:
        assert '"effort": "high"' in question["card"]
    store.advance("roundtable", by="source-test")
    launches = []
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: launches.append(1) or (0, "[]"))
    if choice == "unlisted":
        reviewer.update(name="unseen-through-MCP", effort="xhigh")
    server = PlannerServer(store.workspace)
    try:
        result = workspace_wire(
            server,
            "harness_roundtable",
            expected_workspace=str(store.workspace),
            reviewers=[reviewer],
            max_rounds=1,
            max_total=1,
            timeout=10,
        )
    finally:
        server.close()
    allowed = bool(listed) and choice == "exact"
    assert launches == ([1] if allowed else [])
    assert result["isError"] is not allowed
    assert budget.planning_review_budget(store)["spentAdmissions"] == int(allowed)
    if not allowed:
        assert "planning-reviewer-consent-required" in str(result)
    if allowed and selection == "new":
        for key in ("provider", "model", "effort", "name"):
            with pytest.raises(ValueError, match="planning-reviewer-consent-required"):
                budget.claim_planning_review_start(store, {**reviewer, key: "unlisted"})
        assert budget.claim_planning_review_start(store, reviewer) == 2
        with pytest.raises(ValueError, match="cap-exhausted"):
            budget.claim_planning_review_start(store, reviewer)
        assert budget.planning_review_budget(store)["spentAdmissions"] == 2
