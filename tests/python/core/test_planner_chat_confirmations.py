"""Caller-mediated chat authority: real stores/consumers, not human-authentication proof."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
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

from codex_harness.core import plan_cli, planner_jobs
from codex_harness.core import planner_confirmations as confirmation
from codex_harness.core import planner_execution as execution
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


def prepare_combined_b(store, selection="new", *, cap=2):
    """Canonical fixture producer; external replies/reviews remain source simulations."""
    source_only_expansion(store)
    combined_reply(store, prepare_chat_confirmation(store, "align+vision-lock"))
    for phase in PHASES[1 : PHASES.index("plan") + 1]:
        store.advance(phase, by="source-test")
    if selection != "none":
        _, reviewer, _ = recorded_reviewer(store)
    manifest = store.compile_template(store.workspace / "template")
    budget.register_planning_review_budget(store, "combined-source-test", cap)
    options = dict(
        expected_revision=manifest["planRevision"],
        max_parallel=1,
        timeout=5,
        call_cap=2,
        workers=[
            dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium") for b in ("C", "B")
        ],
    )
    if selection == "existing":
        options, node_id = confirmation._propose_b_reviewers(store, options, reviewer["id"])
        store.confirm_reviewer_selection(
            node_id,
            store._revision(),
            approve=True,
            statement="SOURCE-ONLY existing reviewer selection",
            conditions="",
        )
        options["expected_revision"] = store.compile_template(store.workspace / "template")[
            "planRevision"
        ]
    request = prepare_chat_confirmation(
        store, "plan-execution", execution=options, review_max_starts=cap
    )
    options["expected_revision"] = request["scopes"]["execution"]["publicationRevision"]
    return request, options


@pytest.mark.parametrize("installed", [("codex",), ("codex", "claude")])
def test_b_proposes_installed_reviewers_and_complete_shared_budget(
    semantic_plan, tmp_path, monkeypatch, installed
):
    """Run23: catch one-reviewer/undersized B through real catalog/selection -> B -> claim.

    Earlier B tests supplied explicit tuples and caps, so never tested proposal policy.
    Executable files are observation inputs; no process or provider runs.
    """
    from codex_harness.core import roundtable

    executables = {}
    for provider in installed:
        executable = tmp_path / f"{provider}.exe"
        executable.write_bytes(b"source-only installation observation")
        executables[provider] = str(executable)
    monkeypatch.setattr(roundtable, "_executable", lambda p, *a: executables.get(p))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    store, _ = semantic_plan
    _, options = prepare_combined_b(store)
    fresh = prepare_chat_confirmation(store, "plan-execution", execution=options)
    scope = fresh["scopes"]["review-budget"]
    reviewers = scope["reviewers"]
    assert len(reviewers) == len({r["name"] for r in reviewers}) == 2
    assert {r["provider"] for r in reviewers} == set(installed)
    assert scope["maxStarts"] == len(reviewers) * (
        roundtable.review_budget.DEFAULT_REVIEW_ROUNDS + 1
    )
    canonical = store.spine().node(fresh["reviewer_node_id"])["fields"]
    assert set(canonical["stages"]) >= {"plan", "full-vision"}
    assert canonical["assurance"]["crossProviderRequired"] == (len(installed) > 1)
    if len(installed) == 1:
        assert "single-provider" in fresh["card"]
    for reviewer in reviewers:
        assert json.dumps(reviewer) in fresh["card"]
    combined_reply(store, fresh)
    budget.register_planning_review_budget(
        store, "combined-source-test", scope["maxStarts"], amendment_request_id=fresh["request_id"]
    )
    for reviewer in reviewers:
        assert budget.claim_planning_review_start(store, reviewer) > 0
    with pytest.raises(ValueError, match="reviewer-consent-required"):
        budget.claim_planning_review_start(store, {**reviewers[0], "model": "unseen"})


@pytest.mark.parametrize(
    "installed,draft",
    [(("codex", "claude"), ("codex", "codex")), (("codex",), ("codex", "claude"))],
)
def test_public_b_normalizes_unconfirmed_multi_reviewer_draft(
    semantic_plan, tmp_path, monkeypatch, installed, draft
):
    """BR-1: correct persisted drafts before public B, instead of treating them as user choices.

    The earlier availability test starts with one reviewer and misses the multi-reviewer
    early return. Real catalog/selection/compile producers feed the public MCP consumer;
    executable observations use fixture files and no provider is launched.
    """
    from tests.python.core.test_mcp_chat_confirmations import tool

    from codex_harness.core import roundtable
    from codex_harness.core.mcp_planner import PlannerServer

    executables = {}
    for provider in installed:
        executable = tmp_path / f"{provider}.exe"
        executable.write_bytes(b"source-only installation observation")
        executables[provider] = str(executable)
    monkeypatch.setattr(roundtable, "_executable", lambda p, *a: executables.get(p))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    store, _ = semantic_plan
    request, options = prepare_combined_b(store)
    catalog = store.record_reviewer_catalog(
        {
            "discovery": "offline",
            "userModels": {"codex": "gpt-6.1-sol", "claude": "claude-sonnet-5-5"},
        },
        store._revision(),
    )["catalog"]
    providers = {p["provider"]: p for p in catalog["providers"]}
    node = store.spine().node(request["reviewer_node_id"])
    reviewers = [
        dict(
            name=f"draft-{n}",
            provider=provider,
            **{k: providers[provider]["choices"][0][k] for k in ("choiceId", "model", "effort")},
        )
        for n, provider in enumerate(draft)
    ]
    fields = node["fields"]
    fields.update(
        catalogId=catalog["id"],
        catalogDigest=catalog["digest"],
        reviewers=reviewers,
        confirmation=None,
    )
    fields["recommendation"]["choiceIds"] = list(dict.fromkeys(r["choiceId"] for r in reviewers))
    fields["assurance"]["crossProviderRequired"] = len(set(draft)) > 1
    store.record_reviewer_selection(node, store._revision())
    options["expected_revision"] = store.compile_template(store.workspace / "template")[
        "planRevision"
    ]
    server = PlannerServer(store.workspace)
    try:
        result = tool(
            server,
            "harness_prepare_chat_confirmation",
            gate="plan-execution",
            execution=options,
            include_detail=True,
        )
        assert not result["isError"], result
        question = result["structuredContent"]
        scope = question["scopes"]["review-budget"]
        actual = scope["reviewers"]
        assert len(actual) == len({r["name"] for r in actual}) == 2
        assert {r["provider"] for r in actual} == set(installed)
        assert scope["maxStarts"] == 6
        canonical = store.spine().node(question["reviewer_node_id"])["fields"]
        assert canonical["confirmation"] is None
        assert {r["provider"] for r in canonical["reviewers"]} == set(installed)
        assert canonical["assurance"]["crossProviderRequired"] == (len(installed) > 1)
        if len(installed) == 1:
            assert "single-provider" in question["card"]
        for reviewer in actual:
            assert json.dumps(reviewer) in question["card"]
    finally:
        server.close()


@pytest.mark.parametrize("installed", [("codex", "claude"), ("codex",)])
def test_b_preserves_valid_confirmed_kickoff_models(
    semantic_plan, tmp_path, monkeypatch, installed
):
    """Catch default substitution of confirmed kickoff tuples; availability tests have no choice."""
    from codex_harness.core import roundtable

    executables = {}
    for provider in installed:
        executable = tmp_path / f"{provider}.exe"
        executable.write_bytes(b"source-only installation observation")
        executables[provider] = str(executable)
    monkeypatch.setattr(roundtable, "_executable", lambda p, *a: executables.get(p))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    store, _ = semantic_plan
    old, options = prepare_combined_b(store)
    catalog = store.record_reviewer_catalog(
        {"discovery": "offline", "userModels": {p: f"chosen-{p}" for p in ("codex", "claude")}},
        store._revision(),
    )["catalog"]
    node = store.spine().node(old["reviewer_node_id"])
    fields = node["fields"]
    fields.update(
        catalogId=catalog["id"],
        catalogDigest=catalog["digest"],
        reviewers=[
            dict(
                name=f"chosen-{p['provider']}",
                provider=p["provider"],
                **{k: p["choices"][0][k] for k in ("choiceId", "model", "effort")},
            )
            for p in catalog["providers"]
            if p["choices"]
        ],
    )
    fields["recommendation"]["choiceIds"] = [r["choiceId"] for r in fields["reviewers"]]
    store.record_reviewer_selection(node, store._revision())
    store.confirm_reviewer_selection(
        node["id"],
        store._revision(),
        approve=True,
        statement="SOURCE-ONLY kickoff choice",
        conditions="",
    )
    options["expected_revision"] = store.compile_template(store.workspace / "template")[
        "planRevision"
    ]
    if installed == ("codex",):
        with pytest.raises(ValueError, match="Confirmed kickoff reviewers"):
            prepare_chat_confirmation(store, "plan-execution", execution=options)
        assert store.spine().node(node["id"])["fields"]["reviewers"] == fields["reviewers"]
        return
    fresh = prepare_chat_confirmation(store, "plan-execution", execution=options)
    actual = fresh["scopes"]["review-budget"]["reviewers"]
    assert {r["model"] for r in actual} == {"chosen-codex", "chosen-claude"}
    assert {r["name"] for r in actual} == {"chosen-codex", "chosen-claude"}
    assert all(r["effort"] is None for r in actual)


@pytest.mark.parametrize("current,spent,cap", [(2, 0, 4), (4, 2, 6), (4, 4, 6), (2, 0, 10)])
def test_displayed_b_amendment_retains_registration_then_bounds_real_claims(
    semantic_plan, current, spent, cap, capsys
):
    """Catch run23's immutable-cap dead end through MCP -> CLI -> claim.

    Old tests only refused changed registration; they never consumed renewed B.
    Replies are source simulations and claims launch no provider.
    """
    from tests.python.core.test_mcp_chat_confirmations import record, tool

    from codex_harness.core.mcp_planner import PlannerServer

    store, _ = semantic_plan
    old, options = prepare_combined_b(store, cap=current)
    combined_reply(store, old)
    for _ in range(spent):
        budget.claim_planning_review_start(store, old["scopes"]["review-budget"]["reviewers"][0])
    root = store.path.parent / "planning-review-budget"
    registration = (root / "registration.json").read_bytes()
    retained = {p.name: p.read_bytes() for p in (root / "claims").iterdir()}
    server = PlannerServer(store.workspace)
    try:
        result = tool(
            server,
            "harness_prepare_chat_confirmation",
            gate="plan-execution",
            execution=options,
            review_max_starts=cap,
            include_detail=True,
        )
        assert not result["isError"], result
        fresh = result["structuredContent"]
        assert fresh["reviewBudgetPreviousDigest"] == budget._review_digest(
            json.loads(registration)
        )
        assert fresh["reviewBudgetPreviousDigest"] in fresh["card"]
        reviewer = fresh["scopes"]["review-budget"]["reviewers"][0]
        assert json.dumps(reviewer) in fresh["card"]
        assert approval_credit(store, "execution", store._revision()) is None
        assert not record(server, fresh, apply_budget=False)["isError"]
    finally:
        server.close()
    if current == 4 and spent == 2:
        store.advance("roundtable", by="source-test")
    argv = [
        "--workspace",
        str(store.workspace),
        "review-budget",
        "--campaign",
        "combined-source-test",
        "--max-starts",
        str(cap),
        "--amend-request",
        fresh["request_id"],
        "--machine-json",
    ]
    assert plan_cli.main(argv) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert (data["maxStarts"], data["spentAdmissions"]) == (cap, spent)
    assert (root / "registration.json").read_bytes() == registration
    assert plan_cli.main(argv) == 2  # Consumed old digest cannot amend again.
    capsys.readouterr()
    with open_plan_store(store.workspace) as reopened:
        assert budget.planning_review_budget(reopened)["spentAdmissions"] == spent
        assert {p.name: p.read_bytes() for p in (root / "claims").iterdir()} == retained
        with pytest.raises(ValueError, match="reviewer-consent-required"):
            budget.claim_planning_review_start(reopened, {**reviewer, "effort": "unseen"})
        assert [
            budget.claim_planning_review_start(reopened, reviewer) for _ in range(spent, cap)
        ] == list(range(spent + 1, cap + 1))
        with pytest.raises(ValueError, match="cap-exhausted"):
            budget.claim_planning_review_start(reopened, reviewer)


@pytest.mark.parametrize(
    "case",
    [
        "unanswered",
        "declined",
        "stale",
        "decrease",
        "unchanged",
        "over-ten",
        "below-spent",
        "campaign",
        "wrong-id",
        "wrong-cap",
        "no-reviewers",
    ],
)
def test_budget_amendment_requires_exact_increasing_current_b(semantic_plan, case):
    """Catch stale/wider/misattributed consent without allowing allowance resets.

    Registration and positive B tests miss failed amendment authorization.
    """
    store, _ = semantic_plan
    old, options = prepare_combined_b(store, "none" if case == "no-reviewers" else "new")
    combined_reply(store, old)
    spent = 0 if case == "no-reviewers" else (2 if case == "below-spent" else 1)
    for _ in range(spent):
        budget.claim_planning_review_start(store, old["scopes"]["review-budget"]["reviewers"][0])
    cap = {"decrease": 1, "below-spent": 1, "unchanged": 2, "over-ten": 11}.get(case, 4)
    if case == "over-ten":
        with pytest.raises(ValueError, match="invalid-scope"):
            prepare_chat_confirmation(
                store, "plan-execution", execution=options, review_max_starts=cap
            )
        assert budget.planning_review_budget(store)["maxStarts"] == 2
        return
    fresh = prepare_chat_confirmation(
        store, "plan-execution", execution=options, review_max_starts=cap
    )
    if case != "unanswered":
        combined_reply(store, fresh, "No" if case == "declined" else "Approve")
    if case == "stale":
        prepare_chat_confirmation(store, "plan-execution", execution=options, review_max_starts=5)
    with pytest.raises(ValueError):
        budget.register_planning_review_budget(
            store,
            "foreign" if case == "campaign" else "combined-source-test",
            5 if case == "wrong-cap" else cap,
            amendment_request_id="foreign" if case == "wrong-id" else fresh["request_id"],
        )
    assert budget.planning_review_budget(store)["maxStarts"] == 2
    assert budget.planning_review_budget(store)["spentAdmissions"] == spent


@pytest.mark.parametrize("kind", ["claim"])
def test_budget_orphan_after_crash_holds_amendments_and_claims(semantic_plan, monkeypatch, kind):
    """Catch a crash between original file and event; old tests lack amendment originals."""
    store, _ = semantic_plan
    old, options = prepare_combined_b(store)
    combined_reply(store, old)
    reviewer = old["scopes"]["review-budget"]["reviewers"][0]
    assert budget.claim_planning_review_start(store, reviewer) == 1
    fresh = prepare_chat_confirmation(
        store, "plan-execution", execution=options, review_max_starts=4
    )
    combined_reply(store, fresh)
    event = store._event

    def crash(name, value):
        if name == (
            "planning-review-start-claimed" if kind == "claim" else "planning-review-budget-amended"
        ):
            raise RuntimeError("simulated crash after original")
        event(name, value)

    def amend():
        return budget.register_planning_review_budget(
            store, "combined-source-test", 4, amendment_request_id=fresh["request_id"]
        )

    if kind == "claim":
        # A replacement B for the same cap allows a real claim to crash before its event.
        fresh = prepare_chat_confirmation(
            store, "plan-execution", execution=options, review_max_starts=2
        )
        combined_reply(store, fresh)
    with monkeypatch.context() as fault:
        fault.setattr(store, "_event", crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            budget.claim_planning_review_start(store, reviewer) if kind == "claim" else amend()
    for operation in (amend, lambda: budget.claim_planning_review_start(store, reviewer)):
        with pytest.raises(ValueError, match="invalid-original"):
            operation()


@pytest.mark.parametrize("competitor", ["claim", "amendment"])
def test_budget_amendments_serialize_with_other_amendments_and_claims(semantic_plan, competitor):
    """Catch double amendments or spending against an obsolete digest across connections.

    Existing concurrent-claim tests never change the cap while admitting a reviewer.
    """
    store, _ = semantic_plan
    old, options = prepare_combined_b(store, cap=4)
    combined_reply(store, old)
    for _ in range(2):
        budget.claim_planning_review_start(store, old["scopes"]["review-budget"]["reviewers"][0])
    fresh = prepare_chat_confirmation(
        store, "plan-execution", execution=options, review_max_starts=6
    )
    combined_reply(store, fresh)
    reviewer = fresh["scopes"]["review-budget"]["reviewers"][0]

    def attempt(operation):
        with open_plan_store(store.workspace) as current:
            try:
                return (
                    ("claim", budget.claim_planning_review_start(current, reviewer))
                    if operation == "claim"
                    else (
                        "amendment",
                        budget.register_planning_review_budget(
                            current,
                            "combined-source-test",
                            6,
                            amendment_request_id=fresh["request_id"],
                        ),
                    )
                )
            except ValueError:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["amendment", competitor]))
    assert sum(r is not None and r[0] == "amendment" for r in results) == 1
    current = budget.planning_review_budget(store)
    assert current["maxStarts"] == 6
    assert current["spentAdmissions"] == 2 + sum(r is not None and r[0] == "claim" for r in results)


def test_inflight_roundtable_reservation_blocks_spent_amendment(semantic_plan, monkeypatch):
    """Catch a reserved but not yet claimed reviewer; spent-count tests miss that interval."""
    from codex_harness.core import roundtable

    store, _ = semantic_plan
    old, options = prepare_combined_b(store, cap=4)
    combined_reply(store, old)
    for _ in range(2):
        budget.claim_planning_review_start(store, old["scopes"]["review-budget"]["reviewers"][0])
    fresh = prepare_chat_confirmation(
        store, "plan-execution", execution=options, review_max_starts=6
    )
    combined_reply(store, fresh)

    def crash_before_claim(*args, **kwargs):
        raise RuntimeError("interrupted after canonical reservation")

    monkeypatch.setattr(roundtable, "_review_dossier", crash_before_claim)
    with pytest.raises(RuntimeError, match="canonical reservation"):
        roundtable.run_roundtable(
            store,
            reviewers=fresh["scopes"]["review-budget"]["reviewers"],
            executable_for={"codex": "source-only-no-launch"},
        )
    assert budget.planning_review_budget(store)["spentAdmissions"] == 2
    with pytest.raises(ValueError, match="amendment-stale-or-inflight"):
        budget.register_planning_review_budget(
            store, "combined-source-test", 6, amendment_request_id=fresh["request_id"]
        )


@pytest.mark.parametrize("stale_digest", [False, True])
def test_repeated_raises_preserve_claim_epochs(semantic_plan, monkeypatch, stale_digest):
    """Catch rewrites or acceptance of any historical digest after a later amendment.

    A single amendment cannot expose an incorrectly reused intermediate/original digest.
    """
    store, _ = semantic_plan
    old, options = prepare_combined_b(store)
    combined_reply(store, old)
    reviewer = old["scopes"]["review-budget"]["reviewers"][0]
    root = store.path.parent / "planning-review-budget" / "claims"
    assert budget.claim_planning_review_start(store, reviewer) == 1
    originals = [(root / "00000001.json").read_bytes()]
    for cap in (4, 6):
        fresh = prepare_chat_confirmation(
            store, "plan-execution", execution=options, review_max_starts=cap
        )
        combined_reply(store, fresh)
        budget.register_planning_review_budget(
            store, "combined-source-test", cap, amendment_request_id=fresh["request_id"]
        )
        if stale_digest and cap == 6:
            write_once = budget.write_record_json_once

            def wrong_epoch(path, record, *, write_once=write_once):
                record["registrationDigest"] = json.loads(originals[0])["registrationDigest"]
                return write_once(path, record)

            monkeypatch.setattr(budget, "write_record_json_once", wrong_epoch)
        ordinal = cap // 2
        assert budget.claim_planning_review_start(store, reviewer) == ordinal
        originals.append((root / f"{ordinal:08d}.json").read_bytes())
    assert (root / "00000001.json").read_bytes() == originals[0]
    assert (root / "00000002.json").read_bytes() == originals[1]
    with open_plan_store(store.workspace) as reopened:
        if stale_digest:
            with pytest.raises(ValueError, match="invalid-original"):
                budget.planning_review_budget(reopened)
        else:
            assert budget.planning_review_budget(reopened)["spentAdmissions"] == 3
            assert len({json.loads(raw)["registrationDigest"] for raw in originals}) == 3


def test_formal_review_inflight_blocks_spent_amendment(semantic_plan):
    """Catch a pending formal-review admission; ordinary reservation tests cannot cover it."""
    from codex_harness.core import acceptance, acceptance_recovery

    store, _ = semantic_plan
    old, options = prepare_combined_b(store, cap=4)
    combined_reply(store, old)
    reviewers = old["scopes"]["review-budget"]["reviewers"]
    for reviewer in reviewers:
        budget.claim_planning_review_start(store, reviewer)
    product = store.workspace / "product"
    product.mkdir()
    (product / "hello.py").write_text(test_semantic_wiring.PRODUCT)
    assert acceptance.run_acceptance(store, product, subject="product")["passed"]
    fresh = prepare_chat_confirmation(
        store, "plan-execution", execution=options, review_max_starts=6
    )
    combined_reply(store, fresh)

    def paused_child(*args):
        raise RuntimeError("paused formal admission")

    with pytest.raises(RuntimeError, match="paused formal"):
        acceptance.acceptance_review(store, product, reviewers=reviewers, runner=paused_child)
    assert any(not a["admitted"] for a in acceptance_recovery.attempt_summaries(store))
    with pytest.raises(ValueError, match="stale-or-inflight"):
        budget.register_planning_review_budget(
            store, "combined-source-test", 6, amendment_request_id=fresh["request_id"]
        )
    assert budget.planning_review_budget(store)["spentAdmissions"] == 2


def test_amendment_event_is_atomic_and_survives_reopen(semantic_plan, monkeypatch):
    """Catch partial amendment commits; old registration tests never change the effective cap."""
    store, _ = semantic_plan
    old, options = prepare_combined_b(store, cap=4)
    combined_reply(store, old)
    for _ in range(2):
        budget.claim_planning_review_start(store, old["scopes"]["review-budget"]["reviewers"][0])
    fresh = prepare_chat_confirmation(
        store, "plan-execution", execution=options, review_max_starts=6
    )
    combined_reply(store, fresh)
    event = store._event

    def crash(kind, value):
        event(kind, value)
        if kind == "planning-review-budget-amended":
            raise RuntimeError("crash before commit")

    with monkeypatch.context() as fault:
        fault.setattr(store, "_event", crash)
        with pytest.raises(RuntimeError, match="before commit"):
            budget.register_planning_review_budget(
                store, "combined-source-test", 6, amendment_request_id=fresh["request_id"]
            )
    assert budget.planning_review_budget(store)["maxStarts"] == 4
    assert budget.planning_review_budget(store)["spentAdmissions"] == 2
    budget.register_planning_review_budget(
        store, "combined-source-test", 6, amendment_request_id=fresh["request_id"]
    )
    with open_plan_store(store.workspace) as reopened:
        assert budget.planning_review_budget(reopened)["maxStarts"] == 6
        assert budget.planning_review_budget(reopened)["spentAdmissions"] == 2
        assert (
            reopened.connection.execute(
                "SELECT count(*) FROM events WHERE kind='planning-review-budget-amended'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "change",
    [None, "stale-files", "candidate", "files", "workers", "cap", "failed-review", "control"],
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
        reviewers=[request["scopes"]["review-budget"]["reviewers"][0]],
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
        from tests.python.core.test_mcp_chat_confirmations import tool

        from codex_harness.core.mcp_planner import PlannerServer

        server = PlannerServer(store.workspace)
        try:
            assert not tool(server, "harness_advance", to_phase="plan")["isError"]
            compiled = tool(server, "harness_compile", include_detail=True)
            assert not compiled["isError"] and "gate=plan-execution" in str(compiled)
            changed = {**options, "expected_revision": store.manifest_revision()}
            fresh = tool(
                server,
                "harness_prepare_chat_confirmation",
                gate="plan-execution",
                execution=changed,
            )
            assert not fresh["isError"], fresh
            assert fresh["structuredContent"]["request_id"] != request["request_id"]
            assert approval_credit(store, "execution", store._revision()) is None
        finally:
            server.close()
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
    # Run 22: ordinary execution entry must retain B, including its worker/cap refusals.
    store.advance("execute", by="source-test")
    if change == "workers":
        options["workers"][0]["model"] = "changed-model"
    if change == "cap":
        options["call_cap"] += 1
    scope = execution.execution_scope(store, **options)
    if change == "control":
        # Protect the transaction recheck: ignoring the whole control digest would admit
        # changed controls after scope capture; worker/cap mismatch tests cannot catch it.
        from tests.python.core.test_mcp_chat_confirmations import tool

        from codex_harness.core.mcp_planner import PlannerServer

        server = PlannerServer(store.workspace)
        try:
            asked = tool(
                server, "harness_ask", question="Confirm existing detail?", node_ids=["V1"]
            )
            assert not asked["isError"]
            assert not tool(
                server, "harness_answer", id=asked["structuredContent"]["id"], answer="Same detail"
            )["isError"]
            with pytest.raises(ValueError, match="scope-stale"):
                execution.consume_execution(store, scope, revision)
            assert execution._records(store, execution.CONSUMED) == []
        finally:
            server.close()
        return
    if change:
        from tests.python.core.test_mcp_chat_confirmations import record, tool

        from codex_harness.core.mcp_planner import PlannerServer

        # R1: even an approved standalone execution grant cannot override approved B.
        # The former mismatch-only case had no competing grant and missed this bypass.
        server = PlannerServer(store.workspace)
        launches = []
        try:
            offered = tool(
                server, "harness_prepare_chat_confirmation", gate="execution", execution=options
            )
            assert not offered["isError"], offered
            assert not record(server, offered["structuredContent"])["isError"]
            with monkeypatch.context() as child:
                child.setattr(
                    planner_jobs.subprocess,
                    "Popen",
                    lambda *a, **k: launches.append(1) or SimpleNamespace(pid=12345),
                )
                denied = tool(server, "harness_worker_start", **options)
            assert denied["isError"] and "Needs your go-ahead (B)" in str(denied), denied
            assert not launches
        finally:
            server.close()
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
    listed = question["scopes"]["review-budget"]["reviewers"]
    assert len(listed) == (0 if selection == "none" else 2)
    reviewer = (
        listed[0]
        if listed
        else dict(name="unlisted", provider="codex", model="gpt-6.1-sol", effort="high")
    )
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
