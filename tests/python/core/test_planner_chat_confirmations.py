"""V2 public consent -> real review/job consumers, replacing multi-credit tests."""

import hashlib
from types import SimpleNamespace

import pytest
from tests.python.core import test_semantic_wiring
from tests.python.core.planning_support import source_only_expansion
from tests.python.core.test_planner_confirmations import recorded_reviewer

from codex_harness.core import approvals, planner_jobs, roundtable
from codex_harness.core import planner_confirmations as confirmation
from codex_harness.core import provider_call_budget as budget
from codex_harness.core.plan_store import PHASES, open_plan_store

semantic_plan = test_semantic_wiring.semantic_plan


def reply(request, quote="Approve"):
    return dict(
        gate=request["gate"],
        expected_revision=request["plan_revision"],
        quoted_message=quote,
        quote_sha256=hashlib.sha256(quote.encode()).hexdigest(),
        actor_label="source-only tester; identity unverified",
    )


def combined_reply(store, request, text="Approve"):
    return confirmation.complete_chat_confirmation(
        store, request["request_id"], **reply(request, text)
    )


def prepare_combined_b(store, selection="new", *, cap=2, chat=True, offer=True):
    source_only_expansion(store)
    aligned = confirmation.prepare_confirmation(store, "align+vision-lock", _chat=chat)
    approve_b(store, aligned)
    for phase in PHASES[1 : PHASES.index("plan") + 1]:
        store.advance(phase, by="source-test")
    if selection != "none":
        recorded_reviewer(store)
    manifest = store.compile_template(store.workspace / "template")
    options = dict(
        expected_revision=manifest["planRevision"],
        max_parallel=1,
        timeout=5,
        call_cap=2,
        workers=[
            dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium") for b in ("C", "B")
        ],
    )
    if not offer:
        return None, options
    request = confirmation.prepare_confirmation(
        store, "plan-execution", _chat=chat, execution=options, review_max_starts=cap
    )
    return request, options


def approve_b(store, request):
    if request["channel"] == confirmation.CHAT:
        return combined_reply(store, request)
    return confirmation.complete_confirmation(
        store,
        request["request_id"],
        "accept",
        dict(approve=True, statement="Approve", conditions=""),
    )


def review(store, reviewer, **limits):
    return roundtable.run_roundtable(
        store,
        reviewers=reviewer if isinstance(reviewer, list) else [reviewer],
        executable_for=lambda _: "source-only-executable",
        **limits,
    )


def publish(store, request, monkeypatch):
    approve_b(store, request)
    store.advance("roundtable", by="source-test")
    reviewer = request["subject"]["reviewers"][0]
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: (0, "[]"))
    review(store, reviewer)
    store.advance("publish", by="source-test")
    return store.publish(by="source-test", export_dir=store.workspace / "export")


@pytest.mark.parametrize("chat", [False, True])
def test_missing_b_refuses_without_reservation_then_exact_b_reaches_review_and_job(
    semantic_plan, monkeypatch, chat
):
    """Catch native/no-B and missing-B bypasses at real consumers; V1 happy paths preapproved B."""
    store, _ = semantic_plan
    _, options = prepare_combined_b(store, chat=chat, offer=False)
    reviewer = approvals.reviewer_tuple(store.spine().node("CHOICE")["fields"]["reviewers"][0])
    launches = []
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: launches.append("review") or (0, "[]"))
    before = list(store.connection.iterdump())
    with pytest.raises(ValueError) as refusal:
        review(store, reviewer)
    assert refusal.value.code == "plan-b-consent-required"
    assert list(store.connection.iterdump()) == before
    assert budget.usage(store, "review") == 0 and launches == []
    request = confirmation.prepare_confirmation(
        store, "plan-execution", _chat=chat, execution=options, review_max_starts=2
    )
    approve_b(store, request)
    store.advance("roundtable", by="source-test")
    review(store, reviewer)
    assert launches == ["review"] and budget.usage(store, "review") == 1
    store.advance("publish", by="source-test")
    revision = store.publish(by="source-test", export_dir=store.workspace / "export")
    store.advance("execute", by="source-test")
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *a, **k: launches.append("job") or SimpleNamespace(pid=12345),
    )
    options["expected_revision"] = revision
    job = planner_jobs.start_worker_job(store, **options)
    assert launches == ["review", "job"]
    assert job["approvalProvenance"] == request["channel"]
    assert planner_jobs.start_worker_job(store, **options)["jobId"] == job["jobId"]
    assert launches == ["review", "job"]


@pytest.mark.parametrize("change", ["tuple", "candidate", "limits", "empty-reviewers"])
def test_changed_review_action_requires_b_diff_before_launch(semantic_plan, monkeypatch, change):
    """
    Catch action arguments ignored by B; happy-path producer records never vary the actual
    call.
    """
    store, _ = semantic_plan
    request, _ = prepare_combined_b(store, "none" if change == "empty-reviewers" else "new")
    approve_b(store, request)
    reviewer = (
        request["subject"]["reviewers"][0]
        if request["subject"]["reviewers"]
        else dict(name="unseen", provider="codex", model="gpt-6.1-sol", effort="high")
    )
    limits = {}
    if change == "tuple":
        reviewer = [reviewer, {**reviewer, "name": "unlisted", "model": "unseen-model"}]
    elif change == "candidate":
        (store.workspace / "template/changed.txt").write_text("Changed executable candidate")
    elif change == "limits":
        limits["timeout"] = 901
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: pytest.fail("Unapproved launch"))
    before = list(store.connection.iterdump())
    with pytest.raises(ValueError) as error:
        review(store, reviewer, **limits)
    assert "plan-b-consent-required" in str(error.value)
    assert error.value.approval["diff"]
    assert list(store.connection.iterdump()) == before and budget.usage(store, "review") == 0


@pytest.mark.parametrize("key,value", [("call_cap", 3), ("timeout", 6), ("workers", None)])
def test_changed_job_arguments_require_b_before_namespace(semantic_plan, monkeypatch, key, value):
    """Catch a worker start borrowing another B tuple/limit; review tests have no job arguments."""
    store, _ = semantic_plan
    request, options = prepare_combined_b(store)
    options["expected_revision"] = publish(store, request, monkeypatch)
    options[key] = (
        value if value is not None else [{**w, "effort": "high"} for w in options["workers"]]
    )
    monkeypatch.setattr(
        planner_jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("Unapproved job")
    )
    with pytest.raises(ValueError, match="plan-b-consent-required"):
        planner_jobs.start_worker_job(store, **options)
    assert not (store.path.parent / "worker-jobs").exists()


@pytest.mark.parametrize(
    "words,state",
    [
        ("No", "declined"),
        ("Cancel", "cancel"),
        ("Changes: use different limits", "changes-requested"),
    ],
)
def test_chat_refusal_and_conditional_reply_are_recoverable(semantic_plan, words, state):
    """
    Catch conditional replies promoted to authority; native form tests use structured
    conditions.
    """
    store, _ = semantic_plan
    request, options = prepare_combined_b(store)
    assert combined_reply(store, request, words)["state"] == state
    with pytest.raises(ValueError):
        store.advance("roundtable", by="source-test")
    fresh = confirmation.prepare_chat_confirmation(store, "plan-execution", execution=options)
    assert combined_reply(store, fresh)["state"] == "approved"
    store.advance("roundtable", by="source-test")
    with open_plan_store(store.workspace) as reopened:
        assert approvals.require_approval(reopened, "B")["words"] == "Approve"
        assert budget.usage(reopened, "review") == 0


def test_native_and_chat_render_identical_subjects_and_effects(semantic_plan):
    """
    Catch channel-specific authority and wrapper-only effects; old mode tests encoded that
    split.
    """
    store, _ = semantic_plan
    request, options = prepare_combined_b(store)
    native = confirmation.prepare_confirmation(
        store, "plan-execution", execution=options, review_max_starts=2
    )
    assert native["subject"] == request["subject"] and native["card"] == request["card"]
    assert native["fingerprint"] == request["fingerprint"]


def test_native_mcp_reply_approves_the_same_b_used_by_review(semantic_plan, monkeypatch):
    """Catch a native bridge still minting V1 grants; direct native forms miss wire routing."""
    import json

    from tests.python.core.test_mcp_planner import answer_native, rpc

    from codex_harness.core.mcp_planner import PlannerServer

    store, _ = semantic_plan
    offered, _ = prepare_combined_b(store)
    server = PlannerServer(store.workspace)
    try:
        rpc(
            server,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}},
        )
        server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        outbound = rpc(
            server,
            "tools/call",
            {
                "name": "harness_record_confirmation",
                "arguments": {
                    "gate": "plan-execution",
                    "interaction": "native",
                    "expected_workspace": str(store.workspace),
                },
            },
        )
        assert outbound["method"] == "elicitation/create", outbound
        saved = server._pending_confirmation["request"]
        assert saved["subject"] == offered["subject"]
        assert saved["card"] in outbound["params"]["message"]
        result = answer_native(
            server, outbound["id"], approve=True, statement="Approve", conditions=""
        )
        assert not result["result"]["isError"], result
        monkeypatch.setattr(roundtable, "_run", lambda *a, **k: (0, "[]"))
        review(store, offered["subject"]["reviewers"][0])
        assert budget.usage(store, "review") == 1
    finally:
        server.close()


def test_catalog_refresh_grants_nothing_and_replacement_draft_needs_renewed_b(semantic_plan):
    """
    Catch old catalog consent blocking the replacement writer; candidate tests don't refresh
    a catalog.
    """
    from copy import deepcopy
    from uuid import uuid4

    store, _ = semantic_plan
    request, options = prepare_combined_b(store)
    approve_b(store, request)
    catalog = deepcopy(store._latest_draft_event("reviewer-catalog-recorded")["catalog"])
    catalog["id"] = "RC-" + str(uuid4())
    catalog.pop("digest")
    fresh = store.record_reviewer_catalog(catalog, store._revision())["catalog"]
    assert approvals.require_approval(store, "B")
    node = store.spine().node("CHOICE")
    node["fields"].update(catalogId=fresh["id"], catalogDigest=fresh["digest"])
    store.record_reviewer_selection(node, store._revision())
    manifest = store.compile_template(store.workspace / "template")
    with pytest.raises(ValueError, match="consent-required"):
        approvals.require_approval(store, "B")
    options["expected_revision"] = manifest["planRevision"]
    renewed = confirmation.prepare_chat_confirmation(store, "plan-execution", execution=options)
    approve_b(store, renewed)
    assert budget.usage(store, "review") == 0
    assert approvals.require_approval(store, "B")["fingerprint"] == renewed["fingerprint"]


@pytest.mark.parametrize("channel", ["native", "chat"])
def test_native_conditional_words_do_not_admit_execution(semantic_plan, monkeypatch, channel):
    """AV2-1: a fresh checked form qualified in words admitted a controller.

    Prior tests used literal Approve or the dedicated conditions field. Exercise
    the canonical native/chat writer through review/publication/job admission,
    then prove a new plain Approve recovers. Only launch boundaries are spies.
    """
    import json

    from tests.python.core.test_mcp_planner import answer_native, rpc

    from codex_harness.core.mcp_planner import PlannerServer

    store, _ = semantic_plan
    offered, options = prepare_combined_b(store, chat=channel == "chat")
    server = PlannerServer(store.workspace)
    launches = []
    try:
        rpc(
            server,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}},
        )
        server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

        def decide(words):
            if channel == "native":
                outbound = rpc(
                    server,
                    "tools/call",
                    {
                        "name": "harness_record_confirmation",
                        "arguments": {
                            "gate": "plan-execution",
                            "interaction": "native",
                            "expected_workspace": str(store.workspace),
                        },
                    },
                )
                assert outbound["method"] == "elicitation/create"
                saved = server._pending_confirmation["request"]
                assert saved["subject"] == offered["subject"]
                return answer_native(
                    server, outbound["id"], approve=True, statement=words, conditions=""
                )["result"]
            outbound = server.call_tool(
                "harness_prepare_chat_confirmation", {"gate": "plan-execution"}
            )
            assert not outbound["isError"]
            return server.call_tool(
                "harness_record_chat_confirmation",
                {
                    "request_id": server.result_data["request_id"],
                    **reply(server.result_data, words),
                },
            )

        words = "Approve but not execution"
        refused = decide(words)
        assert refused["structuredContent"]["state"] == "changes-requested"
        assert refused["structuredContent"]["approval_id"] is None
        assert approvals.current(store, "B")["words"] == words
        if channel == "native":
            fresh = refused["structuredContent"]["chatConfirmation"]
            assert fresh["subject"] == offered["subject"]
            assert any(fresh["card"] == item.get("text") for item in refused["content"])
        else:
            assert "fresh complete question" in refused["content"][0]["text"]
        monkeypatch.setattr(
            roundtable, "_run", lambda *a, **k: launches.append("review") or (0, "[]")
        )
        monkeypatch.setattr(
            planner_jobs.subprocess,
            "Popen",
            lambda *a, **k: launches.append("job") or SimpleNamespace(pid=12345),
        )

        def proceed():
            store.advance("roundtable", by="source-only")
            review(store, offered["subject"]["reviewers"][0])
            store.advance("publish", by="source-only")
            options["expected_revision"] = store.publish(
                by="source-only", export_dir=store.workspace / "export"
            )
            store.advance("execute", by="source-only")
            return planner_jobs.start_worker_job(store, **options)

        with pytest.raises(ValueError, match="plan-b-consent-required"):
            proceed()
        assert launches == [] and budget.usage(store, "review") == 0
        assert not (store.path.parent / "worker-jobs").exists()
        approved = decide("Approve")
        assert approved["structuredContent"]["state"] == "approved"
        job = proceed()
        assert launches == ["review", "job"] and budget.usage(store, "review") == 1
        assert job["jobId"] == options["expected_revision"]
    finally:
        server.close()
