"""Canonical A/B/C conversation through review, publication and delivery evidence."""

import json
from pathlib import Path

import pytest
from tests.python.core import test_semantic_wiring
from tests.python.core.planning_support import source_only_expansion
from tests.python.core.test_semantic_wiring import MARKER_FIRST, execute, workers

from codex_harness.core import dispatch, planner_jobs, worker_packet
from codex_harness.core.graph import load_plan
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PHASES

semantic_plan = test_semantic_wiring.semantic_plan


@pytest.mark.parametrize("clarification,run22", [(False, False), (True, False), (False, True)])
def test_canonical_plan_contracts_red_build_validation_pass(
    semantic_plan, monkeypatch, clarification, run22
):
    """Protect public A/B/C→dispatch→CLI→acceptance, with one genuine clarification when needed.

    Catches duplicate stops, native elicitation and premature acceptance; separate-gate tests
    never consume the combined public requests through review, publish and final acceptance.
    Run 22 authors subordinate scope after A at Vision Lock and advances to Execute before
    starting workers; the original journey authored everything before A and started at Publish.
    """
    import sys
    from copy import deepcopy
    from types import SimpleNamespace

    from tests.python.core.test_acceptance import finish_public_acceptance
    from tests.python.core.test_acceptance_recovery import protocol
    from tests.python.core.test_mcp_chat_confirmations import record as external_reply
    from tests.python.core.test_mcp_planner import rpc, workspace_wire
    from tests.python.core.test_plan_store import mock_catalog, mock_choice

    from codex_harness.core import roundtable

    store, manifest = semantic_plan
    server = PlannerServer(store.workspace)
    confirmations = []

    def call(name, **args):
        reply = workspace_wire(server, name, expected_workspace=str(store.workspace), **args)
        assert server._pending_confirmation is None, "Default semantic journey must never elicit"
        assert not reply["isError"], reply
        if name not in {"harness_accept_review", "harness_accept"}:
            assert "C: Does this delivery" not in str(reply)
        return reply

    def approve(request):
        confirmations.append(request["gate"])
        result = external_reply(server, request)
        assert not result["isError"] and result["structuredContent"]["state"] == "approved", result

    rpc(
        server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}}
    )
    server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    if run22:
        authored = store.spine().to_dict()["nodes"]
        vision = store.spine().node("V1")
        targets = vision["fields"].pop("targets")
        scenarios = vision["fields"].pop("scenarios")
        call("harness_upsert_node", node=vision)
        for key in ("B", "C", "CLI", "D", "R", "F"):
            call("harness_remove_node", id=key)
    source_only_expansion(store)  # Proposed dispositions only; no confirmation credit.
    if clarification:
        question = call(
            "harness_ask", question="Must the greeting end with a newline?", node_ids=["V1"]
        )
        confirmations.append("clarification")
        call("harness_answer", id=question["structuredContent"]["id"], answer="No; exactly Hello.")
    card = call(
        "harness_understanding_card",
        vision="Offline greeting CLI",
        users="CLI user",
        first_user_moment="Read Hello",
        scope_in="Offline greeting",
        scope_out="GUI",
        assumptions=[],
    )
    a = card["structuredContent"]
    # CT-2: the mandatory user excerpt, not a wrapper, must explain approval's effect.
    excerpt = a["card"]
    assert (
        "Approve confirms understanding and locks this scope and these expansion decisions."
        in excerpt
    )
    persisted = json.loads(
        store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='confirmation-requested' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    )
    assert persisted["card"] == a["card"] and a["card"] in card["content"][0]["text"]
    assert a["subject"]["kind"] == "A"
    approve(a)
    if run22:
        call("harness_advance", to_phase="expand")
        call("harness_advance", to_phase="vision-lock")
        vision = store.spine().node("V1")
        vision["fields"].update(targets=targets, scenarios=scenarios)
        call("harness_upsert_nodes", nodes=[n for n in authored if n["id"] != "V1"] + [vision])
        assert store._revision() != a["plan_revision"]
        assert store.status()["approval"]["granted"]
    for phase in PHASES[PHASES.index(store.phase()) + 1 : PHASES.index("roundtable") + 1]:
        if phase == "roundtable":
            break
        call("harness_advance", to_phase=phase)
    catalog = mock_catalog()
    other = deepcopy(catalog["providers"][0])
    other.update(provider="claude", executable="C:/mock/claude.exe")
    other["choices"][0]["model"] = "claude-sonnet-5-5"
    catalog["providers"].append(other)
    catalog = call(
        "harness_reviewer_catalog", catalog=catalog, expected_spine_revision=store._revision()
    )["structuredContent"]["catalog"]
    selection = mock_choice(catalog)
    choice = catalog["providers"][1]["choices"][0]
    selection["fields"]["reviewers"].append(
        dict(
            name="secondary",
            provider="claude",
            **{k: choice[k] for k in ("choiceId", "model", "effort")},
        )
    )
    call("harness_reviewer_choice", node=selection, expected_spine_revision=store._revision())
    call("harness_compile", include_detail=True)
    manifest = store._manifest(store.spine())
    options = dict(
        expected_revision=manifest["planRevision"],
        max_parallel=1,
        timeout=5,
        call_cap=2,
        workers=[
            dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium") for b in ("C", "B")
        ],
    )
    b = call(
        "harness_prepare_chat_confirmation",
        gate="plan-execution",
        execution=options,
        reviewer_node_id="CHOICE",
    )["structuredContent"]
    assert f"up to {b['subject']['reviewAllowance']} reviews" in b["card"]
    approve(b)
    if run22:
        # Replacement is a real catalog/selection producer, not injected consent.
        replacement = mock_catalog()
        replacement["id"] = "RC-00000000-0000-0000-0000-000000000002"
        replacement["providers"].append(deepcopy(other))
        replacement["providers"][0]["choices"][0]["model"] = "gpt-6.1-terra"
        replacement = call(
            "harness_reviewer_catalog",
            catalog=replacement,
            expected_spine_revision=store._revision(),
        )["structuredContent"]["catalog"]
        selection = mock_choice(replacement)
        selection["fields"]["reviewers"].append(
            dict(
                name="secondary",
                provider="claude",
                **{k: choice[k] for k in ("choiceId", "model", "effort")},
            )
        )
        call("harness_reviewer_choice", node=selection, expected_spine_revision=store._revision())
        call("harness_compile")
        manifest = store._manifest(store.spine())
        options["expected_revision"] = manifest["planRevision"]
        refused = workspace_wire(
            server,
            "harness_advance",
            expected_workspace=str(store.workspace),
            to_phase="roundtable",
        )
        assert refused["isError"] and "plan-b-consent-required" in str(refused)
        assert "harness_prepare_chat_confirmation" in refused["content"][0]["text"]
        renewed = call(
            "harness_prepare_chat_confirmation", gate="plan-execution", execution=options
        )
        b = renewed["structuredContent"]
        assert b["diff"] and any(d["field"] == "reviewers" for d in b["diff"])
        conditional = external_reply(server, b, "Approve but use fewer reviews")
        outcome = conditional["structuredContent"]
        assert outcome["state"] == "changes-requested" and not outcome["approval_id"]
        b = outcome["chatConfirmation"]
        assert "Changes from your previous decision" in b["card"]
        assert b["card"] in conditional["content"][0]["text"]
        approve(b)
    call("harness_advance", to_phase="roundtable")
    (store.workspace / "sample.json").write_text(json.dumps(manifest))
    reviewers = b["subject"]["reviewers"]
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)

    # Synthetic external-process telemetry, never actual provider/product qualification.
    def review_process(launch, *_):
        provider = launch.cli
        result = protocol(provider, text="[]")
        model = next(r["model"] for r in reviewers if r["provider"] == provider)
        return tuple(v.replace("source-only", model) if isinstance(v, str) else v for v in result)

    monkeypatch.setattr(roundtable, "_run", review_process)
    call("harness_roundtable", reviewers=reviewers, max_rounds=1, max_total=2, timeout=5)
    call("harness_advance", to_phase="publish")
    call("harness_publish")
    if run22:
        call("harness_advance", to_phase="execute")
    packet = worker_packet.build_packet(
        load_plan(store.workspace),
        {**manifest["beads"]["C"], "id": "C"},
        store.workspace / "template",
        None,
    )
    assert MARKER_FIRST in packet
    job = planner_jobs._directory(store.workspace, manifest["planRevision"])
    with monkeypatch.context() as launch_spy:
        launch_spy.setattr(
            planner_jobs.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=23456)
        )
        call("harness_worker_start", **options)
    record = execute(store, work_root=job / "runs")
    assert record["status"] == dispatch.STATUS_MOCK_ONLY, record
    red = record["beads"]["C"]["integrated"]["semantic"]
    green = record["semanticValidation"]
    assert red["state"] == "RED" and not red["passed"]
    assert not record["beads"]["C"]["lessonsPromoted"]
    assert green["state"] == "PASS" and green["passed"] and green["linkedRed"]
    assert red["scenarioBinding"] == green["scenarioBinding"]
    assert all(r["cleanup"]["quiescenceVerified"] for r in red["results"] + green["results"])
    assert green["candidateTree"] != red["candidateTree"]
    assert record["requirementValidation"][0]["passed"] is False
    assert record["requirementValidation"][1]["passed"] is True
    assert not record["claims"] and not record["isLiveProductProof"]
    saved = json.loads((Path(record["repo"]).parent / "evidence/run.json").read_text())
    assert saved["semanticValidation"] == green
    public = planner_jobs.worker_job_status(store.workspace, manifest["planRevision"])
    assert public["run"]["semanticValidation"] == green and public["status"] == "unknown"
    resumed = dispatch.run_plan(
        store.workspace,
        work_root=store.workspace / "runs",
        max_parallel=1,
        fake_workers=workers(),
        resume_run_dir=Path(record["repo"]).parent,
    )
    assert resumed["status"] == dispatch.STATUS_MOCK_ONLY, resumed
    assert resumed["semanticValidation"] == green

    try:
        for phase in ("accept",) if run22 else ("execute", "accept"):
            advanced = call("harness_advance", to_phase=phase)
            hint = advanced["content"][0]["text"].split("Next: ", 1)[1]
            assert "harness_accept_run" in hint and "If they agree" not in hint
            for tool, arguments in (
                ("harness_plan_status", {}),
                ("harness_worker_status", {"job_id": manifest["planRevision"], "wait_seconds": 0}),
            ):
                reply = server.call_tool(tool, arguments)
                assert not reply["isError"], reply
                assert reply["content"][0]["text"].split("Next: ", 1)[1] == hint
        refused = workspace_wire(
            server,
            "harness_accept",
            expected_workspace=str(store.workspace),
            by="user",
            statement="Premature",
        )
        assert refused["isError"] and refused["refusalCode"] == "acceptance-scenarios-required"
        assert "harness_accept_run" in refused["content"][0]["text"]
        result = finish_public_acceptance(
            server, call("harness_accept_run", repo=record["repo"], subject="product")
        )
        assert result["structuredContent"]["result"]["passed"], result
        reviewed = call("harness_accept_review", repo=record["repo"], reviewers=reviewers)
        assert "C: Does this delivery meet the locked vision?" in str(reviewed)
        assert record["repo"] in reviewed["content"][0]["text"]
        assert store.status()["acceptance"]["reviews"][-1]["runId"] in str(reviewed)
        confirmations.append("accept")
        call(
            "harness_accept",
            by="user",
            statement="SOURCE-ONLY external owner: Yes, meets the locked vision.",
        )
        assert confirmations == (["clarification"] if clarification else []) + [
            "align+vision-lock",
            "plan-execution",
            *(["plan-execution"] if run22 else []),
            "accept",
        ]
        requests = [
            json.loads(r[0])
            for r in store.connection.execute(
                "SELECT payload_json FROM events WHERE kind='confirmation-requested' ORDER BY id"
            )
        ]
        assert [r["gate"] for r in requests] == ["align+vision-lock", "plan-execution"] + (
            ["plan-execution", "plan-execution"] if run22 else []
        )
        assert all(r["channel"] == "chat-confirmation" for r in requests)
        assert (
            store.connection.execute("SELECT count(*) FROM acceptance_approvals").fetchone()[0] == 1
        )
    finally:
        server.close()
    # A fresh connection must read the terminal run, rather than offer it again.
    server = PlannerServer(store.workspace)
    try:
        reply = server.call_tool("harness_plan_status", {})
        assert not reply["isError"], reply
        assert "Validation is not final acceptance" not in reply["content"][0]["text"]
    finally:
        server.close()
