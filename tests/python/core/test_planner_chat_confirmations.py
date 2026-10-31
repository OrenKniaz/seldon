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
from codex_harness.core.plan_store import PHASES

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


def prepare_combined_b(store, selection="new", *, cap=6, chat=True, offer=True):
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
        store, "plan-execution", _chat=chat, execution=options, review_max_starts=6
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


@pytest.mark.parametrize("change", ["tuple", "limits", "empty-reviewers"])
def test_changed_review_action_requires_b_diff_before_launch(semantic_plan, monkeypatch, change):
    """
    Catch action arguments ignored by B; happy-path producer records never vary the actual
    call.
    """
    store, _ = semantic_plan
    if change == "empty-reviewers":
        _, options = prepare_combined_b(store, "none", offer=False)
        with pytest.raises(ValueError, match="reviewer-selection-required-before-B"):
            confirmation.prepare_chat_confirmation(store, "plan-execution", execution=options)
        assert approvals.current(store, "B") is None and budget.usage(store, "review") == 0
        return
    request, _ = prepare_combined_b(store)
    approve_b(store, request)
    reviewer = request["subject"]["reviewers"][0]
    limits = {}
    if change == "tuple":
        reviewer = [reviewer, {**reviewer, "name": "unlisted", "model": "unseen-model"}]
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
        # Both channels re-ask with the same subject instead of approving.
        fresh = refused["structuredContent"]["chatConfirmation"]
        assert fresh["subject"] == offered["subject"]
        assert any(fresh["card"] in item.get("text", "") for item in refused["content"])
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
