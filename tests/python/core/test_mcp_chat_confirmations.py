"""Public caller-mediated decisions; synthetic external replies never prove human identity."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from tests.python.core import test_planner_replay_gaps, test_semantic_wiring
from tests.python.core.test_mcp_planner import (
    answer_native,
    begin_native,
    rpc,
    start,
    workspace_wire,
)
from tests.python.core.test_planner_resume import _published

from codex_harness.core import planner_jobs
from codex_harness.core.mcp_planner import PlannerServer

project = test_planner_replay_gaps.project
semantic_plan = test_semantic_wiring.semantic_plan


@pytest.mark.parametrize("gate", ["align", "align+vision-lock", "plan-execution"])
def test_prepare_compact_wire_preserves_card_originals_and_reuses_only_unchanged(
    semantic_plan, gate
):
    """Protect real producer -> MCP bytes and reuse without replaying control preimages.

    Catches altered approval text, lost limits, stale reuse and duplicate requests;
    existing consent-credit journeys inspect authority but never bound response size.
    """
    from tests.python.core.planning_support import source_only_expansion
    from tests.python.core.test_planner_chat_confirmations import prepare_combined_b

    store, _ = semantic_plan
    arguments = dict(gate=gate)
    if gate == "plan-execution":
        _, options = prepare_combined_b(store)
        arguments["execution"] = options
    else:
        source_only_expansion(store)
    server = PlannerServer(store.workspace)

    def size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())

    try:
        compact = tool(server, "harness_prepare_chat_confirmation", **arguments)
        assert not compact["isError"], compact
        original = json.loads(
            store.connection.execute(
                "SELECT payload_json FROM events ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        )
        data = compact["structuredContent"]
        assert original["card"].encode() in compact["content"][0]["text"].encode()
        assert size(compact) < 50_000
        assert size(data) < 8_000
        assert data["card"]["count"] == len(original["card"])
        assert set(data["covered_gates"]) == set(original.get("scopes", {gate: None}))
        if gate == "plan-execution":
            scope = data["scopes"]["execution"]
            for key in ("workers", "callCap", "timeout", "maxParallel"):
                assert scope[key] == original["scopes"]["execution"][key]
            assert scope["templateFiles"]["count"] == len(
                original["scopes"]["execution"]["templateFiles"]
            )
        event_count = store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        full = tool(server, "harness_prepare_chat_confirmation", include_detail=True, **arguments)
        assert not full["isError"]
        assert full["structuredContent"] == {
            **original,
            "source_verified": False,
            "human_identity_verified": False,
        }
        assert full["content"][0]["text"].encode() == compact["content"][0]["text"].encode()
        repeated = tool(server, "harness_prepare_chat_confirmation", **arguments)
        assert repeated["structuredContent"] == dict(
            request_id=data["request_id"], digest=data["digest"]
        )
        assert len(repeated["content"][0]["text"].splitlines()) == 1
        assert size(repeated) < 2_000
        assert store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == event_count
        print(
            f"COMPACT2 {gate}: full={size(full)} default={size(compact)} repeated={size(repeated)}"
        )
        if gate == "plan-execution":
            (store.workspace / "template" / "compact2-protected.txt").write_text(
                "changed protected template\n"
            )
        else:
            # A real separate producer supersedes this connection's unanswered offer.
            from codex_harness.core.planner_confirmations import prepare_chat_confirmation

            prepare_chat_confirmation(store, gate)
        refreshed = tool(server, "harness_prepare_chat_confirmation", **arguments)
        assert not refreshed["isError"], refreshed
        assert refreshed["structuredContent"]["request_id"] != data["request_id"]
        assert "<<<SHOW-VERBATIM" in refreshed["content"][0]["text"]
        assert not record(server, refreshed["structuredContent"], "No")["isError"]
        after_response = tool(server, "harness_prepare_chat_confirmation", **arguments)
        assert (
            after_response["structuredContent"]["request_id"]
            != refreshed["structuredContent"]["request_id"]
        )
    finally:
        server.close()


def tool(server, name, **arguments):
    return workspace_wire(server, name, expected_workspace=str(server.workspace), **arguments)


@pytest.mark.parametrize("reply", ["Approve but not execution", "Changes: narrow scope", "Approve"])
def test_bound_response_attempt_reoffers_full_question_even_when_rejected(semantic_plan, reply):
    """Catch rejected prose preserving the compact cache despite a bound response attempt.

    Existing response tests cover durable response events; grammar rejection occurs
    before those events and previously left the same question suppressed forever.
    """
    from tests.python.core.planning_support import source_only_expansion

    store, _ = semantic_plan
    source_only_expansion(store)
    server = PlannerServer(store.workspace)
    try:
        first = tool(
            server,
            "harness_prepare_chat_confirmation",
            gate="align+vision-lock",
            include_detail=True,
        )
        assert not first["isError"], first
        original = first["structuredContent"]
        repeated = tool(server, "harness_prepare_chat_confirmation", gate="align+vision-lock")
        assert repeated["structuredContent"]["request_id"] == original["request_id"]
        assert len(repeated["content"][0]["text"].splitlines()) == 1
        count = store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        response = record(server, original, reply)
        if reply == "Approve but not execution":
            assert response["isError"] and "chat-confirmation-choice-unclear" in str(response)
            assert store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == count
        else:
            assert not response["isError"], response
            assert response["structuredContent"]["state"] == (
                "approved" if reply == "Approve" else "conditional"
            )
        assert store.status()["approval"]["granted"] is (reply == "Approve")
        fresh = tool(server, "harness_prepare_chat_confirmation", gate="align+vision-lock")
        assert not fresh["isError"], fresh
        assert fresh["structuredContent"]["request_id"] != original["request_id"]
        saved = store._latest_draft_event("confirmation-requested")
        assert saved["card"].encode() in fresh["content"][0]["text"].encode()
        assert "<<<SHOW-VERBATIM" in fresh["content"][0]["text"]
        assert saved["request_id"] == fresh["structuredContent"]["request_id"]
    finally:
        server.close()


def test_execution_repeat_after_response_requires_a_new_question(project):
    """Catch reuse after an execution response excluded by the control digest.

    Planning gate tests use a different event kind; existing execution journeys
    consume consent but never prepare the identical proposal again afterwards.
    """
    options = _published(project)
    proposal = {k: options[k] for k in ("expected_revision", "workers", "timeout", "call_cap")}
    server = PlannerServer(project.workspace)
    try:
        first = tool(
            server, "harness_prepare_chat_confirmation", gate="execution", execution=proposal
        )
        assert not first["isError"], first
        data = first["structuredContent"]
        full = tool(
            server,
            "harness_prepare_chat_confirmation",
            gate="execution",
            execution=proposal,
            include_detail=True,
        )
        assert full["structuredContent"]["scope"]["workers"] == proposal["workers"]
        assert full["structuredContent"]["card"].encode() in first["content"][0]["text"].encode()
        repeat = tool(
            server, "harness_prepare_chat_confirmation", gate="execution", execution=proposal
        )
        assert repeat["structuredContent"]["request_id"] == data["request_id"]
        assert len(repeat["content"][0]["text"].splitlines()) == 1
        assert not record(server, {**data, "gate": "execution"}, "No")["isError"]
        fresh = tool(
            server, "harness_prepare_chat_confirmation", gate="execution", execution=proposal
        )
        assert not fresh["isError"], fresh
        assert fresh["structuredContent"]["request_id"] != data["request_id"]
        assert "<<<SHOW-VERBATIM" in fresh["content"][0]["text"]
    finally:
        server.close()


def record(server, question, quote="Approve", *, apply_budget=True):
    """A separate typed source-only external-message fixture, not an observed human reply."""
    result = tool(
        server,
        "harness_record_chat_confirmation",
        gate=question.get("gate", "execution"),
        request_id=question["request_id"],
        expected_revision=question.get(
            "plan_revision", question.get("scope", {}).get("publicationRevision")
        ),
        quoted_message=quote,
        quote_sha256=hashlib.sha256(quote.encode()).hexdigest(),
        actor_label="source-only external tester; identity and source unverified",
    )
    if (
        apply_budget
        and question.get("gate") == "plan-execution"
        and result.get("structuredContent", {}).get("state") == "approved"
    ):
        from codex_harness.core import provider_call_budget as budget

        scope = question["scopes"]["review-budget"]
        if budget.planning_review_budget(server.store)["maxStarts"] != scope["maxStarts"]:
            budget.register_planning_review_budget(
                server.store,
                scope["campaign"],
                scope["maxStarts"],
                amendment_request_id=question["request_id"],
            )
    return result


def test_run22_renew_reviewers_with_stale_selection_retains_shared_cap(
    semantic_plan, monkeypatch, capsys
):
    """Renew RS1 through public producers and B into actual review admission.

    Catch old RS1 blocking its replacement, stale B spending, or cap reset; prior
    CS-1 cases select once and never refresh the catalog after a spent review.
    Only provider execution and external replies are simulated, not authority/admission.
    """
    import sys
    from copy import deepcopy

    from tests.python.core.planning_support import source_only_expansion
    from tests.python.core.test_plan_store import mock_catalog, mock_choice

    from codex_harness.core import plan_cli, roundtable
    from codex_harness.core import provider_call_budget as budget
    from codex_harness.core.plan_store import PHASES
    from codex_harness.core.planner_confirmations import combined_credit

    store, _ = semantic_plan
    source_only_expansion(store)
    server = PlannerServer(store.workspace)
    launches = []
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: launches.append(a) or (0, "[]"))

    def public(name, **arguments):
        result = tool(server, name, **arguments)
        assert not result["isError"], result
        return result.get("structuredContent")

    def catalog(observation):
        return public(
            "harness_reviewer_catalog",
            catalog=observation,
            expected_spine_revision=store._revision(),
        )["catalog"]

    def select(node):
        return public(
            "harness_reviewer_choice", node=node, expected_spine_revision=store._revision()
        )

    def question():
        return public(
            "harness_prepare_chat_confirmation",
            include_detail=True,
            gate="plan-execution",
            reviewer_node_id="RS1",
            execution=options,
        )

    def review(reviewer):
        return tool(
            server,
            "harness_roundtable",
            reviewers=[reviewer],
            max_rounds=2,
            max_total=4,
            timeout=10,
        )

    try:
        a = public("harness_prepare_chat_confirmation", gate="align+vision-lock")
        assert record(server, a)["structuredContent"]["state"] == "approved"
        for phase in PHASES[1 : PHASES.index("plan") + 1]:
            public("harness_advance", to_phase=phase)
        old_catalog = catalog(mock_catalog())
        node = mock_choice(old_catalog)
        node["id"] = "RS1"
        select(node)
        public("harness_compile")
        assert (
            plan_cli.main(
                [
                    "--workspace",
                    str(store.workspace),
                    "review-budget",
                    "--campaign",
                    "run22",
                    "--max-starts",
                    "4",
                    "--machine-json",
                ]
            )
            == 0
        )
        capsys.readouterr()
        options = dict(
            expected_revision=store.manifest_revision(),
            max_parallel=1,
            timeout=5,
            call_cap=2,
            workers=[
                dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                for b in ("C", "B")
            ],
        )
        old_b = question()
        assert record(server, old_b)["structuredContent"]["state"] == "approved"
        old_tuple = old_b["scopes"]["review-budget"]["reviewers"][0]
        public("harness_advance", to_phase="roundtable")
        assert not review(old_tuple)["isError"]
        assert len(launches) == budget.planning_review_budget(store)["spentAdmissions"] == 1
        public("harness_advance", to_phase="plan")

        observation = mock_catalog()
        observation["id"] = "RC-00000000-0000-0000-0000-000000000022"
        observation["providers"][0]["choices"][0]["effort"]["value"] = "medium"
        claude = deepcopy(observation["providers"][0])
        claude.update(provider="claude", executable="C:/mock/claude.exe")
        claude["choices"][0]["model"] = "claude-sonnet-5-5"
        observation["providers"].append(claude)
        current = catalog(observation)
        with pytest.raises(ValueError, match="reviewer-catalog-stale"):
            combined_credit(store, "review-budget")
        with pytest.raises(ValueError):
            budget.claim_planning_review_start(store, old_tuple)
        replacement = mock_choice(current)
        replacement["id"] = "RS1"
        replacement["fields"]["stages"] = ["plan", "full-vision"]
        choice = current["providers"][1]["choices"][0]
        replacement["fields"]["reviewers"].append(
            dict(
                name="claude-reviewer",
                provider="claude",
                **{key: choice[key] for key in ("choiceId", "model", "effort")},
            )
        )
        saved = select(replacement)
        assert saved["selection"]["confirmation"] is None
        assert store.spine().node("RS1")["fields"]["catalogId"] == current["id"]
        public("harness_compile")
        options["expected_revision"] = store.manifest_revision()
        fresh = question()
        # BR-1: this unconfirmed replacement is normalized; only confirmed names are retained.
        tuples = fresh["scopes"]["review-budget"]["reviewers"]
        assert tuples == [
            dict(name="codex-reviewer", provider="codex", model="gpt-6.1-sol", effort="medium"),
            dict(
                name="claude-reviewer",
                provider="claude",
                model="claude-sonnet-5-5",
                effort="medium",
            ),
        ]
        assert all(t["model"] in fresh["card"] for t in tuples)
        assert fresh["request_id"] != old_b["request_id"]
        for reviewer in (old_tuple, *tuples):
            with pytest.raises(ValueError, match="combined-consent-required"):
                budget.claim_planning_review_start(store, reviewer)
        assert budget.planning_review_budget(store)["spentAdmissions"] == 1
        assert record(server, fresh)["structuredContent"]["state"] == "approved"
        assert combined_credit(store, "review-budget")["request_id"] == fresh["request_id"]
        public("harness_advance", to_phase="roundtable")
        refused = review(old_tuple)
        assert refused["isError"] and "planning-reviewer-consent-required" in str(refused)
        assert len(launches) == budget.planning_review_budget(store)["spentAdmissions"] == 1
        for reviewer in tuples:
            admitted = review(reviewer)
            assert not admitted["isError"], str(admitted)
        assert len(launches) == budget.planning_review_budget(store)["spentAdmissions"] == 3
        # Same admission owner is used by acceptance reviews; no new campaign allowance.
        assert budget.claim_planning_review_start(store, tuples[1]) == 4
        assert old_b["scopes"]["review-budget"]["maxStarts"] == 6
        assert budget.claim_planning_review_start(store, tuples[0]) == 5
        assert budget.claim_planning_review_start(store, tuples[1]) == 6
        with pytest.raises(ValueError, match="cap-exhausted"):
            budget.claim_planning_review_start(store, tuples[0])
        assert budget.planning_review_budget(store)["maxStarts"] == 6
        assert budget.planning_review_budget(store)["spentAdmissions"] == 6
    finally:
        server.close()


def test_run22_vision_edit_after_a_requires_fresh_vision_reply(semantic_plan):
    """Protect displayed intent: catch an early anchor crediting changed vision wording.

    Prior stale-reply tests edit before answering; run 22 needs content invalidation after A
    but before leaving Vision Lock, followed by supported fresh approval recovery.
    """
    from tests.python.core.planning_support import source_only_expansion

    store, _ = semantic_plan
    source_only_expansion(store)
    server = PlannerServer(store.workspace)
    try:
        a = tool(server, "harness_prepare_chat_confirmation", gate="align+vision-lock")[
            "structuredContent"
        ]
        assert record(server, a)["structuredContent"]["state"] == "approved"
        for phase in ("expand", "vision-lock"):
            assert not tool(server, "harness_advance", to_phase=phase)["isError"]
        vision = store.spine().node("V1")
        vision["text"] += " Changed user-visible promise after A."
        refused = tool(server, "harness_upsert_node", node=vision)
        assert refused["isError"] and "vision-locked-wording" in str(refused)
        assert not tool(server, "harness_upsert_node", node=vision, reopen_vision_lock=True)[
            "isError"
        ]
        refused = tool(server, "harness_advance", to_phase="features")
        assert refused["isError"] and "plan-approval-stale" in str(refused)
        assert store.phase() == "vision-lock" and not store.status()["approval"]["granted"]
        fresh = tool(
            server, "harness_prepare_chat_confirmation", gate="vision-lock", include_detail=True
        )["structuredContent"]
        assert fresh["vision_revision"] != a["vision_revision"]
        assert fresh["card_digest"] == hashlib.sha256(fresh["card"].encode()).hexdigest()
        assert record(server, fresh)["structuredContent"]["state"] == "approved"
        assert not tool(server, "harness_advance", to_phase="features")["isError"]
    finally:
        server.close()


@pytest.mark.parametrize("interaction", [None, "native"])
def test_semantic_defaults_to_chat_even_with_elicitation(semantic_plan, interaction):
    """Catch capability negotiation silently prompting exec users; legacy fallback tests cannot.

    Only an explicit native interaction may issue elicitation; no response is invented.
    """
    from tests.python.core.planning_support import source_only_expansion

    store, _ = semantic_plan
    source_only_expansion(store)
    server = PlannerServer(store.workspace)
    try:
        rpc(
            server,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}},
        )
        server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        response = rpc(
            server,
            "tools/call",
            {
                "name": "harness_record_confirmation",
                "arguments": {
                    "gate": "align",
                    **({"interaction": interaction} if interaction else {}),
                },
            },
        )
        if interaction:
            assert response["method"] == "elicitation/create"
        else:
            question = response["result"]["structuredContent"]
            assert question["gate"] == "align+vision-lock" and server._pending_confirmation is None
    finally:
        server.close()


@pytest.mark.parametrize(
    "state,registered",
    [("absent", False), ("absent", True), ("pending", True), ("declined", True), ("stale", True)],
)
def test_combined_a_requires_b_before_public_spend_publish_or_execution(
    semantic_plan, monkeypatch, state, registered
):
    """CT-1: omitting B cannot select legacy authority; happy journeys always prepare B.

    The absent/unregistered case catches the admission early return; other cases protect
    missing, unanswered, declined and changed-candidate consent at real public consumers.
    """
    import sys

    from tests.python.core.planning_support import source_only_expansion
    from tests.python.core.test_planner_confirmations import recorded_reviewer

    from codex_harness.core import plan_cli, provider_call_budget, roundtable
    from codex_harness.core.plan_store import PHASES

    store, _ = semantic_plan
    source_only_expansion(store)
    server = PlannerServer(store.workspace)
    launches = []
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: launches.append("review") or (0, "[]"))
    monkeypatch.setattr(
        planner_jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("No worker before B")
    )
    try:
        a = tool(
            server,
            "harness_understanding_card",
            vision="Offline greeting CLI",
            users="CLI user",
            first_user_moment="Read Hello",
            scope_in="Offline greeting",
            scope_out="GUI",
            assumptions=[],
        )
        assert not a["isError"] and not record(server, a["structuredContent"])["isError"]
        for phase in PHASES[1 : PHASES.index("plan") + 1]:
            assert not tool(server, "harness_advance", to_phase=phase)["isError"]
        _, reviewer, _ = recorded_reviewer(store)
        assert not tool(server, "harness_compile")["isError"]
        if registered:
            assert (
                plan_cli.main(
                    [
                        "--workspace",
                        str(store.workspace),
                        "review-budget",
                        "--campaign",
                        "CT-1",
                        "--max-starts",
                        "2",
                    ]
                )
                == 0
            )
        options = dict(
            expected_revision=store.manifest_revision(),
            max_parallel=1,
            timeout=5,
            call_cap=2,
            workers=[
                dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                for b in ("C", "B")
            ],
        )
        if state != "absent":
            b = tool(
                server,
                "harness_prepare_chat_confirmation",
                gate="plan-execution",
                execution=options,
                reviewer_node_id=reviewer["id"],
            )
            assert not b["isError"], b
            if state in {"declined", "stale"}:
                assert not record(
                    server, b["structuredContent"], "No" if state == "declined" else "Approve"
                )["isError"]
            if state == "stale":
                changed = store.spine().node("B")
                changed["title"] += " changed after B"
                assert not tool(server, "harness_upsert_node", node=changed)["isError"]
        for name, args in [
            ("harness_advance", dict(to_phase="roundtable", user_statement="Legacy-looking words")),
            (
                "harness_roundtable",
                dict(
                    reviewers=[
                        dict(name="primary", provider="codex", model="gpt-6.1-sol", effort="high")
                    ],
                    max_rounds=1,
                    max_total=1,
                    timeout=5,
                ),
            ),
            ("harness_publish", {}),
            ("harness_worker_start", options),
        ]:
            response = tool(server, name, **args)
            assert response["isError"], response
            assert (
                "Needs your go-ahead (B)" in response["content"][0]["text"].rsplit("Next: ", 1)[1]
            ), response
        budget = provider_call_budget.planning_review_budget(store)
        assert not launches and (budget is None or budget["spentAdmissions"] == 0)
        assert store.status()["published_revision"] is None
        assert not (store.workspace / "plan" / "revisions").exists()
    finally:
        server.close()


@pytest.mark.parametrize(
    "offered", ["unanswered", "declined", "stale", "superseded", "approved_history"]
)
def test_native_after_card_uses_effective_native_credits(semantic_plan, monkeypatch, offered):
    """CT-3: an obsolete A offer must not force B after actual native approval.

    The earlier prompt-only test never consumed native credits through review spending;
    Superseded unapproved offers grant nothing; CC rule R2 keeps an actually approved A sticky.
    """
    import sys

    from tests.python.core.planning_support import source_only_expansion

    from codex_harness.core import plan_cli, provider_call_budget, roundtable
    from codex_harness.core.plan_store import PHASES

    store, _ = semantic_plan
    source_only_expansion(store)
    server = PlannerServer(store.workspace)
    launches = []
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: launches.append(1) or (0, "[]"))
    try:
        rpc(
            server,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}},
        )
        server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        fields = dict(
            vision="Offline greeting CLI",
            users="CLI user",
            first_user_moment="Read Hello",
            scope_in="Offline greeting",
            scope_out="GUI",
            assumptions=[],
        )
        card = tool(server, "harness_understanding_card", **fields)
        assert not card["isError"], card
        if offered == "declined":
            assert (
                record(server, card["structuredContent"], "No")["structuredContent"]["state"]
                == "declined"
            )
        if offered in {"superseded", "approved_history"}:
            if offered == "approved_history":
                assert (
                    record(server, card["structuredContent"])["structuredContent"]["state"]
                    == "approved"
                )
            newer = tool(server, "harness_understanding_card", **fields)
            assert (
                not newer["isError"]
                and newer["structuredContent"]["request_id"]
                != card["structuredContent"]["request_id"]
            )
        if offered == "stale":
            vision = store.spine().node("V1")
            vision["text"] += " Clarified before approval."
            assert not tool(server, "harness_upsert_node", node=vision)["isError"]
            assert (
                record(server, card["structuredContent"])["structuredContent"]["state"] == "stale"
            )
        for phase in PHASES[1 : PHASES.index("plan") + 1]:
            if store.phase() in {"align", "vision-lock"}:
                form = rpc(
                    server,
                    "tools/call",
                    {
                        "name": "harness_record_confirmation",
                        "arguments": {"gate": store.phase(), "interaction": "native"},
                    },
                )
                assert form["method"] == "elicitation/create", form
                completed = answer_native(
                    server,
                    form["id"],
                    approve=True,
                    statement="SOURCE-ONLY explicit native approval",
                    conditions="",
                )["result"]
                assert (
                    not completed["isError"]
                    and completed["structuredContent"]["provenance"] == "mcp-client-elicitation"
                )
            assert not tool(server, "harness_advance", to_phase=phase)["isError"]
        assert not tool(server, "harness_compile")["isError"]
        assert (
            plan_cli.main(
                [
                    "--workspace",
                    str(store.workspace),
                    "review-budget",
                    "--campaign",
                    "CT-3",
                    "--max-starts",
                    "2",
                ]
            )
            == 0
        )
        advanced = tool(server, "harness_advance", to_phase="roundtable")
        reviewed = tool(
            server,
            "harness_roundtable",
            reviewers=[dict(name="native", provider="codex", model="gpt-6.1-sol", effort="medium")],
            max_rounds=1,
            max_total=1,
            timeout=5,
        )
        held = offered == "approved_history"
        assert advanced["isError"] is held and reviewed["isError"] is held, reviewed
        assert launches == ([] if held else [1])
        assert provider_call_budget.planning_review_budget(store)["spentAdmissions"] == int(
            not held
        )
        if held:
            assert "Needs your go-ahead (B)" in str(reviewed)
    finally:
        server.close()


@pytest.mark.parametrize(
    "route",
    ["changed_native_vision", "stale_scope", "first_b_after_native_vision", "native_a_explicit_b"],
)
def test_b_binds_mixed_approval_routes_until_fresh_consent(semantic_plan, monkeypatch, route):
    """CT-4: mixed A channels must not erase B or its requirement after approved A.

    Prior tests renewed both native gates before any approved A, and always used combined
    A before B; they missed stale scope, one native renewal and explicit B after native A.
    """
    import sys

    from tests.python.core.planning_support import source_only_expansion
    from tests.python.core.test_planner_confirmations import recorded_reviewer

    from codex_harness.core import plan_cli, provider_call_budget, roundtable
    from codex_harness.core.plan_store import PHASES

    store, _ = semantic_plan
    source_only_expansion(store)
    server = PlannerServer(store.workspace)
    launches = []
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: launches.append(1) or (0, "[]"))

    def native(gate):
        form = rpc(
            server,
            "tools/call",
            {
                "name": "harness_record_confirmation",
                "arguments": {"gate": gate, "interaction": "native"},
            },
        )
        assert form.get("method") == "elicitation/create", form
        result = answer_native(
            server, form["id"], approve=True, statement="SOURCE-ONLY scope approval", conditions=""
        )["result"]
        assert not result["isError"], result

    try:
        rpc(
            server,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}},
        )
        server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        if route != "native_a_explicit_b":
            a = tool(server, "harness_prepare_chat_confirmation", gate="align+vision-lock")
            assert not a["isError"] and not record(server, a["structuredContent"])["isError"]
        for phase in PHASES[1 : PHASES.index("plan") + 1]:
            if route == "native_a_explicit_b" and store.phase() in {"align", "vision-lock"}:
                native(store.phase())
            assert not tool(server, "harness_advance", to_phase=phase)["isError"]
        _, reviewer, _ = recorded_reviewer(store)
        assert not tool(server, "harness_compile")["isError"]
        assert (
            plan_cli.main(
                [
                    "--workspace",
                    str(store.workspace),
                    "review-budget",
                    "--campaign",
                    "CT-4",
                    "--max-starts",
                    "2",
                ]
            )
            == 0
        )
        options = dict(
            expected_revision=store.manifest_revision(),
            max_parallel=1,
            timeout=5,
            call_cap=2,
            workers=[
                dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                for b in ("C", "B")
            ],
        )
        if route != "first_b_after_native_vision":
            b = tool(
                server,
                "harness_prepare_chat_confirmation",
                gate="plan-execution",
                execution=options,
                reviewer_node_id=reviewer["id"],
            )
            assert not b["isError"] and not record(server, b["structuredContent"])["isError"]
        if route != "native_a_explicit_b":
            vision = store.spine().node("V1")
            vision["fields"]["scope_in"] = "Explicitly revised greeting scope"
            assert not tool(server, "harness_upsert_node", node=vision, reopen_vision_lock=True)[
                "isError"
            ]
            if route != "stale_scope":
                native("vision-lock")
            assert not tool(server, "harness_compile")["isError"]
        advanced = tool(server, "harness_advance", to_phase="roundtable")
        assert advanced["isError"] is (route != "native_a_explicit_b"), advanced
        refused = tool(
            server,
            "harness_roundtable",
            reviewers=[
                dict(name="unlisted", provider="codex", model="gpt-6.1-sol", effort="xhigh")
            ],
            max_rounds=1,
            max_total=1,
            timeout=5,
        )
        assert refused["isError"] and "Needs your go-ahead (B)" in str(refused), refused
        assert tool(server, "harness_publish")["isError"]
        assert (
            not launches
            and provider_call_budget.planning_review_budget(store)["spentAdmissions"] == 0
        )
        assert store.status()["published_revision"] is None
        # The same ordinary path recovers only within a matching approved B.
        if route != "native_a_explicit_b":
            if route == "stale_scope":
                native("vision-lock")
            assert not tool(server, "harness_compile")["isError"]
            fresh = tool(
                server,
                "harness_prepare_chat_confirmation",
                gate="plan-execution",
                execution={**options, "expected_revision": store.manifest_revision()},
                reviewer_node_id=reviewer["id"],
            )
            assert (
                not fresh["isError"] and not record(server, fresh["structuredContent"])["isError"]
            )
            assert not tool(server, "harness_advance", to_phase="roundtable")["isError"]
        approved_b = (
            b["structuredContent"] if route == "native_a_explicit_b" else fresh["structuredContent"]
        )
        reviewed = tool(
            server,
            "harness_roundtable",
            reviewers=[approved_b["scopes"]["review-budget"]["reviewers"][0]],
            max_rounds=1,
            max_total=1,
            timeout=5,
        )
        assert not reviewed["isError"] and launches == [1], reviewed
        assert provider_call_budget.planning_review_budget(store)["spentAdmissions"] == 1
    finally:
        server.close()


@pytest.mark.parametrize("native", ["decline", "unavailable"])
def test_public_native_failure_offers_fresh_chat_then_advances(tmp_path, native):
    """Catch missing public fallback/status lies; leaf-only tests bypass the wire and view."""
    server = PlannerServer(tmp_path)
    try:
        start(server)
        if native == "decline":
            prepared = begin_native(server, gate="align")
            original = server._pending_confirmation["request"]["request_id"]
            response = answer_native(server, prepared["id"], action="decline")["result"]
            assert response["structuredContent"]["state"] == "decline"
        else:
            original = None
            response = tool(server, "harness_record_confirmation", gate="align")
        assert response["isError"]
        question = response["structuredContent"]["chatConfirmation"]
        assert question["channel"] == "chat-confirmation" and question["request_id"] != original
        assert question["choices"] == ["Approve", "No", "Cancel", "Changes: <text>"]
        assert not server.store.status()["approval"]["granted"]
        assert "unverified" in question["card"]
        completed = record(server, question, "Approve\n")
        assert not completed["isError"], completed
        outcome = completed["structuredContent"]
        assert outcome["provenance"] == "chat-confirmation"
        assert not outcome["chat"]["source_verified"] and not outcome["human_identity_verified"]
        status = tool(server, "harness_plan_status")["structuredContent"]
        assert status["approval"]["origin"] == "chat-confirmation"
        assert "caller-mediated" in tool(server, "harness_plan_status")["content"][0]["text"]
        # Native retry must not silently supersede an already-valid chat decision.
        reused = tool(server, "harness_record_confirmation", gate="align")
        assert (
            not reused["isError"]
            and reused["structuredContent"]["provenance"] == "chat-confirmation"
        )
        advanced = tool(server, "harness_advance", to_phase="expand")
        assert not advanced["isError"], advanced
        server.close()
        server = PlannerServer(tmp_path)
        assert tool(server, "harness_plan_status")["structuredContent"]["phase"] == "expand"
    finally:
        server.close()


@pytest.mark.parametrize("native", ["decline", "unavailable"])
def test_public_chat_execution_records_then_separate_start_consumes_exact_grant(
    project, monkeypatch, native
):
    """Catch re-prompt superseding chat credit; leaf job tests bypass the native MCP interceptor."""
    options = _published(project)
    proposal = {
        key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")
    }
    server = PlannerServer(project.workspace)
    launches = []
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *args, **kwargs: launches.append((args, kwargs)) or SimpleNamespace(pid=23456),
    )
    try:
        if native == "decline":
            rpc(
                server,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"elicitation": {}},
                },
            )
            server.handle_line(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            )
            pending = rpc(
                server, "tools/call", {"name": "harness_worker_start", "arguments": proposal}
            )
            assert pending["method"] == "elicitation/create"
            response = answer_native(server, pending["id"], action="decline")["result"]
            assert response["structuredContent"]["executionConfirmation"]["state"] == "decline"
        else:
            response = tool(server, "harness_worker_start", **proposal)
        assert response["isError"] and launches == []
        question = response["structuredContent"]["chatConfirmation"]
        assert question["scope"]["publicationRevision"] == proposal["expected_revision"]
        assert question["scope"]["callCap"] == 1
        completed = record(server, question)
        assert not completed["isError"], completed
        assert completed["structuredContent"]["state"] == "approved" and launches == []
        result = tool(server, "harness_worker_start", **proposal)
        assert not result["isError"], result
        job = result["structuredContent"]
        assert len(launches) == 1
        assert job["nativeApproval"] == "unproven"
        assert job["approvalProvenance"] == "chat-confirmation"
        assert job["approvalAssurance"] == "caller-mediated" and not job["actorIdentityVerified"]
        reused = tool(server, "harness_worker_start", **proposal)
        assert not reused["isError"] and len(launches) == 1
        assert reused["structuredContent"]["jobId"] == job["jobId"]
        server.close()
        server = PlannerServer(project.workspace)
        reopened = tool(server, "harness_worker_status", job_id=job["jobId"])
        assert not reopened["isError"], reopened
        assert reopened["structuredContent"]["approvalProvenance"] == "chat-confirmation"
    finally:
        server.close()


@pytest.mark.parametrize(
    "quote,state", [("No", "declined"), ("Cancel", "cancel"), ("Changes: clarify", "conditional")]
)
def test_public_chat_negative_reply_gives_no_credit_and_new_question(tmp_path, quote, state):
    """Catch public No-to-yes or stale-question reuse; native negatives lack the typed chat seam."""
    server = PlannerServer(tmp_path)
    try:
        start(server)
        offered = tool(server, "harness_prepare_chat_confirmation", gate="align")
        assert not offered["isError"], offered
        question = offered["structuredContent"]
        response = record(server, question, quote)
        assert not response["isError"], response
        outcome = response["structuredContent"]
        assert outcome["state"] == state and outcome["approval_id"] is None
        assert outcome["chatConfirmation"]["request_id"] != question["request_id"]
        assert not server.store.status()["approval"]["granted"]
        assert tool(server, "harness_advance", to_phase="expand")["isError"]
        assert record(server, question)["isError"]  # Answer replay never grants approval.
    finally:
        server.close()


@pytest.mark.parametrize("extra", ["approve", "origin", "execution"])
def test_public_chat_record_refuses_conflicting_or_foreign_fields(tmp_path, extra):
    """Catch a widened wire adapter; leaf rejection alone cannot enforce its shape."""
    server = PlannerServer(tmp_path)
    try:
        start(server)
        question = tool(server, "harness_prepare_chat_confirmation", gate="align")[
            "structuredContent"
        ]
        arguments = dict(
            gate="align",
            request_id=question["request_id"],
            expected_revision=question["plan_revision"],
            quoted_message="No",
            quote_sha256=hashlib.sha256(b"No").hexdigest(),
            actor_label="source-only",
        )
        arguments[extra] = True if extra == "approve" else "mcp-client-elicitation"
        refused = tool(server, "harness_record_chat_confirmation", **arguments)
        assert refused["isError"]
        assert not server.store.status()["approval"]["granted"]
    finally:
        server.close()


def test_public_chat_reply_to_changed_plan_remains_stale(tmp_path):
    """Catch a public adapter dropping revision binding; happy replies never change the plan."""
    server = PlannerServer(tmp_path)
    try:
        start(server)
        question = tool(server, "harness_prepare_chat_confirmation", gate="align")[
            "structuredContent"
        ]
        vision = server.store.spine().node("V1")
        vision["text"] += " Changed scope after the displayed question."
        assert not tool(server, "harness_upsert_node", node=vision)["isError"]
        result = record(server, question)
        assert not result["isError"], result  # It records the stale answer without granting.
        assert result["structuredContent"]["state"] == "stale"
        assert not tool(server, "harness_plan_status")["structuredContent"]["approval"]["granted"]
        assert tool(server, "harness_advance", to_phase="expand")["isError"]
    finally:
        server.close()


@pytest.mark.parametrize("native", ["cancel", "timeout"])
def test_public_native_cancel_or_missing_reply_offers_zero_credit_question(tmp_path, native):
    """Catch timeout/cancel granting approval or losing fallback; decline uses another exit."""
    server = PlannerServer(tmp_path)
    try:
        start(server)
        pending = begin_native(server, gate="align")
        original = server._pending_confirmation["request"]["request_id"]
        if native == "timeout":
            server._pending_confirmation["deadline"] = 0
            response = server.expire_confirmation()[-1]["result"]
            assert response["structuredContent"]["state"] == "pending"
        else:
            response = answer_native(server, pending["id"], action="cancel")["result"]
            assert response["structuredContent"]["state"] == "cancel"
        question = response["structuredContent"]["chatConfirmation"]
        assert question["request_id"] != original and question["channel"] == "chat-confirmation"
        assert not server.store.status()["approval"]["granted"]
        assert (
            answer_native(server, pending["id"], approve=True, statement="late", conditions="")
            is None
        )
        assert not server.store.status()["approval"]["granted"]
    finally:
        server.close()


@pytest.mark.parametrize("current_approved", [False, True])
def test_superseded_native_timeout_cannot_replace_current_question_or_credit(
    tmp_path, current_approved
):
    """Catch fallback minted by an old timeout; response-only overlap tests lack missing replies."""
    old, current = PlannerServer(tmp_path), PlannerServer(tmp_path)
    try:
        start(old)
        begin_native(old, gate="align")
        displayed = begin_native(current, gate="align")
        current_id = current._pending_confirmation["request"]["request_id"]
        if current_approved:
            result = answer_native(
                current,
                displayed["id"],
                approve=True,
                statement="SOURCE-ONLY current native client",
                conditions="",
            )
            assert result["result"]["structuredContent"]["state"] == "approved"
        old._pending_confirmation["deadline"] = 0
        timed_out = old.expire_confirmation()[-1]["result"]
        assert timed_out["structuredContent"]["state"] == "pending"
        assert "chatConfirmation" not in timed_out["structuredContent"]
        from codex_harness.core.planner_confirmations import REQUEST, _records

        assert next(row for _, row in _records(old.store, REQUEST))["request_id"] == current_id
        if not current_approved:
            result = answer_native(
                current,
                displayed["id"],
                approve=True,
                statement="SOURCE-ONLY current native client",
                conditions="",
            )
            assert result["result"]["structuredContent"]["state"] == "approved"
        assert not tool(current, "harness_advance", to_phase="expand")["isError"]
    finally:
        old.close()
        current.close()


def test_fallback_creation_rechecks_original_under_the_canonical_writer(tmp_path, monkeypatch):
    """Catch a newer native request between read and fallback write; serial cases miss it."""
    from codex_harness.core import planner_confirmations

    old, current = PlannerServer(tmp_path), PlannerServer(tmp_path)
    prepare = planner_confirmations.prepare_chat_confirmation
    newer = []

    def interleave(store, gate, **kwargs):
        # Schedule another real public producer; the hook supplies no approval or result.
        newer.append(begin_native(current, gate=gate))
        return prepare(store, gate, **kwargs)

    try:
        start(old)
        displayed = begin_native(old, gate="align")
        monkeypatch.setattr(planner_confirmations, "prepare_chat_confirmation", interleave)
        old_reply = answer_native(old, displayed["id"], action="decline")["result"]
        assert old_reply["structuredContent"]["state"] == "decline"
        current_reply = answer_native(
            current,
            newer[0]["id"],
            approve=True,
            statement="SOURCE-ONLY newer native client",
            conditions="",
        )["result"]
        assert current_reply["structuredContent"]["state"] == "approved"
        assert not tool(current, "harness_advance", to_phase="expand")["isError"]
    finally:
        old.close()
        current.close()


def test_execution_fallback_cannot_supersede_interleaved_native_request(project, monkeypatch):
    """Catch execution's writer missing the planning CAS; leaf success lacks an interleave."""
    from codex_harness.core import planner_execution

    options = _published(project)
    proposal = {
        key: options[key] for key in ("expected_revision", "workers", "timeout", "call_cap")
    }
    old, current = PlannerServer(project.workspace), PlannerServer(project.workspace)
    prepare = planner_execution.prepare_chat_execution
    newer, launches = [], []

    def interleave(store, **kwargs):
        newer.append(
            rpc(current, "tools/call", {"name": "harness_worker_start", "arguments": proposal})
        )
        return prepare(store, **kwargs)

    try:
        for server in (old, current):
            rpc(
                server,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"elicitation": {}},
                },
            )
            server.handle_line(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            )
        displayed = rpc(old, "tools/call", {"name": "harness_worker_start", "arguments": proposal})
        assert displayed["method"] == "elicitation/create"
        monkeypatch.setattr(planner_execution, "prepare_chat_execution", interleave)
        monkeypatch.setattr(
            planner_jobs.subprocess,
            "Popen",
            lambda *args, **kwargs: launches.append(1) or SimpleNamespace(pid=23456),
        )
        response = answer_native(old, displayed["id"], action="decline")["result"]
        assert response["structuredContent"]["executionConfirmation"]["state"] == "decline"
        assert "chatConfirmation" not in response["structuredContent"] and launches == []
        current_reply = answer_native(
            current,
            newer[0]["id"],
            approve=True,
            statement="SOURCE-ONLY current execution decision",
            conditions="",
        )["result"]
        assert not current_reply["isError"], current_reply
        assert len(launches) == 1
        assert current_reply["structuredContent"]["approvalProvenance"] == "mcp-client-elicitation"
    finally:
        old.close()
        current.close()
