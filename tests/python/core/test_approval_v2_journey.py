"""Authority journeys through user words, provider replies and real stdio transport."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from tests.python.core.test_golden_path import (
    timed, automatic_retry_golden, receipt_gaps_golden, single_build_golden, http_journeys, controller_interruptions,
)
from tests.python.core.mcp_planner_support import LoopClient, fixture_id
from tests.python.core.test_golden_path import advance_meaning, judge_reply, plan_content, kickoff_content

from codex_harness.core import approvals
from codex_harness.core.plan_store import open_plan_store_readonly

pytestmark = pytest.mark.fast


@pytest.mark.parametrize(
    "journey",
    ["http-domains", "recovery-twin-2", "delivery", "gate-chain", "frozen-kickoff", "stale-a", "reconnect-a", "reconnect-b"],
)
@timed
def test_canonical_plan_contracts_red_build_validation_pass(tmp_path, journey, request):
    """Protect actual words and exact subjects through the canonical producer/consumer.

    C8/C11/C14/C18/C23: actual words and canonical subjects survive the complete
    A/review/B/Contracts/Build/C journey, including reconnect after DONE.
    Catch changed setup replacing frozen role choices, stale scope or replayed
    answers gaining credit, negative decisions accidentally starting workers,
    and C surviving missing/changed delivered bytes. The golden supplies correct
    answers once, so it misses these interrupted and changed-subject boundaries.
    """
    if journey == "http-domains":
        for future in request.getfixturevalue("http_journeys"):
            future.result()
        return
    if journey == "recovery-twin-2":
        controller_interruptions(tmp_path / "twin-2", 2)
        return
    content = plan_content()
    settings = kickoff_content()
    if journey == "frozen-kickoff":
        settings["timeout"] = 300
    (tmp_path / "validation-observed").touch()
    # Main golden owns wrong-reason worker repair. These authority twins select
    # the ordinary provider reply and still observe real Contracts RED/Build GREEN.
    (tmp_path / "contracts-attempted").touch()
    (tmp_path / "template/checks").mkdir(parents=True)
    (tmp_path / "template/checks/retired.json").write_text("[1]\n")
    if journey == "delivery":
        (tmp_path / "long-stream").touch()
    client = LoopClient(tmp_path, native=journey == "reconnect-a", kickoff=settings)

    def at_gate(gate):
        return lambda run: bool(
            run.get("userRequest")
            and run["userRequest"]["gate"] == gate
            and not run.get("question")
        )

    def arrive(gate):
        advance_meaning(client, content, stop_at=at_gate(gate))
        assert at_gate(gate)(client.current), client.current
        return client.current["userRequest"]

    def approve():
        client.words()
        assert client.current["question"]["family"] == "user-interpretation"
        with open_plan_store_readonly(tmp_path) as store:
            assert not store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchone()
        client.answer(judge_reply(client.current["question"]))

    def scope_answer(impact):
        advance_meaning(
            client,
            content,
            stop_at=lambda run: (run.get("question") or {}).get("purpose") == "scope-intent",
        )
        assert client.current["question"]["purpose"] == "scope-intent", client.current
        value = judge_reply(client.current["question"])
        value["meaning"] = dict(impact=impact)
        client.answer(value)

    def decline_and_change(gate):
        for decision, state, words in (
            ("decline", "PAUSED", "No, decline this action."),
            ("changes_requested", "PLAN", "Please change the proposed scope."),
        ):
            arrive(gate)
            client.words(words)
            assert client.current["question"]["family"] == "user-interpretation"
            client.answer(judge_reply(client.current["question"], decision=decision))
            assert client.current["state"] == state
            with open_plan_store_readonly(tmp_path) as store:
                saved = approvals.current(store, "B" if gate == "plan-execution" else "A")
                assert saved["state"] != "approved"
            assert not (tmp_path / ".codex-harness/worker-jobs").exists()
            if decision == "decline":
                client.call("harness_steer", direction="continue", resume=True,
                            rationale="Actual user explicitly resumes the declined action")
        return arrive(gate)

    def reject_replay(pending, value, gate):
        refused = client.call("harness_loop_answer", error=True,
            request_id=pending["requestId"], answer=value, source="replayed-wire-response")
        assert "Unknown tool" in refused["content"][0]["text"]
        with open_plan_store_readonly(tmp_path) as store:
            saved = [r for r in approvals.records(store, approvals.EVENT)
                     if r["subject"]["kind"] == gate and r["state"] == "approved"]
            assert len(saved) == 1

    try:
        client.call(
            "harness_plan_start", name="Authority journey", idea=content["nodes"][0]["text"]
        )
        assert client.current["userRequest"] is None
        assert client.current["question"]["purpose"] == "understanding"
        if journey == "gate-chain":
            client.answer(
                dict(
                    option="ask_user",
                    rationale="Missing actual user input",
                    content=dict(question="Must exact binary bytes be preserved?"),
                )
            )
            assert client.current["state"] == "WAIT_USER"
            client.words("Yes, preserve the exact binary bytes.")
            # Clarification words are canonical data; the next proposal interprets them.
            with open_plan_store_readonly(tmp_path) as store:
                rows = list(
                    store.connection.execute("SELECT question,answer,status FROM clarifications")
                )
                assert len(rows) == 1 and rows[0]["status"] == "answered"
                assert rows[0]["answer"] == "Yes, preserve the exact binary bytes."
            assert client.current["question"]["family"] == "author"

        kind = "plan-execution" if journey in {"reconnect-b", "frozen-kickoff", "delivery"} else "align+vision-lock"

        if journey == "frozen-kickoff":
            # Run 22 authors subordinate trace after A. Adding targets/scenarios
            # must temporarily stale A, then preserve its actual-word authority
            # when the canonical intent judge finds no change to the dream.
            understanding = deepcopy(content["nodes"][0])
            understanding["fields"].pop("targets")
            understanding["fields"].pop("scenarios")
            client.answer(
                dict(
                    option="ready",
                    rationale="Lock understanding before subordinate trace exists",
                    content=dict(nodes=[understanding]),
                )
            )
            a = arrive("align+vision-lock")
            approve()
            assert client.current["question"]["family"] == "author"
            # CODE allocates outline handles before the linked plan changes the
            # canonical revision; retain the actual stale-A/intent boundary.
            client.answer(dict(option="ready",rationale="Author the complete subordinate plan",content=content))
            advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "vision-intent")
            with open_plan_store_readonly(tmp_path) as store:
                assert store._revision() != a["plan_revision"]
                assert not store.status()["approval"]["granted"]
            assert client.current["question"]["purpose"] == "vision-intent"
            client.answer(judge_reply(client.current["question"]))
            with open_plan_store_readonly(tmp_path) as store:
                assert store.status()["approval"]["granted"]
                assert approvals.current(store, "A")["request_id"] == a["request_id"]

        request = arrive(kind)
        assert request["gate"] == kind
        already_approved = False
        if journey == "reconnect-a":
            old_offer = client.native_offer()
            client.call("harness_plan_status")
            durable = client.current["userRequest"]["request_id"]
            assert client.current["userRequest"]["channel"] == "mcp-client-elicitation"
            client.reconnect()
            assert client.current["userRequest"]["request_id"] == durable
            fresh_offer = client.native_offer()
            assert fresh_offer["id"] != old_offer["id"]
            client.native_response(old_offer, retain_reply=False)
            assert client.rpc("ping", {}) == {}
            with open_plan_store_readonly(tmp_path) as store:
                assert approvals.current(store, "A") is None
            client.call(
                "harness_steer",
                direction="pause",
                rationale="Actual user pauses before the native response",
            )
            client.native_response(fresh_offer, retain_reply=False)
            late = client.take_reply(client.native_tool_id)
            assert (
                late["result"]["isError"]
                and late["result"]["structuredContent"]["state"] == "PAUSED"
            )
            with open_plan_store_readonly(tmp_path) as store:
                assert approvals.current(store, "A") is None
            client.call(
                "harness_steer",
                direction="continue",
                rationale="Actual user explicitly resumes",
                resume=True,
            )
            arrive(kind)
            fresh_offer = client.native_offer()
            assert client.current["userRequest"]["request_id"] != durable
            client.native_response(fresh_offer)
            assert client.current["question"]["family"] == "user-interpretation"
            client.answer(judge_reply(client.current["question"]))
            with open_plan_store_readonly(tmp_path) as store:
                approved = approvals.current(store, "A")
                assert approved["channel"] == "mcp-client-elicitation"
                assert not approved["human_identity_verified"]
            already_approved = True
        if journey == "reconnect-b":
            # Base :114: retained B words survive a same-ID HOW amendment and
            # transport death. An unchanged-subject reconnect cannot catch lost
            # words, silent reapproval, or an answer bound only to node IDs.
            client.words()
            pending = deepcopy(client.current["question"])
            value = judge_reply(pending)
            design = next(n for n in content["nodes"] if n["id"] == "G1")
            design["text"] += " Keep the existing exact-byte readback contract during delivery."
            client.call(
                "harness_steer",
                direction="replan",
                rationale="Retain the approved behavior while clarifying implementation",
                content=dict(nodes=[design]),
            )
            # Brief C1/C18 migration: retain the actual HOW reply before
            # restart. An unanswered launched scope call has UNKNOWN outcome
            # and is covered by the quarantine journeys, never manually answered.
            import time,os,signal
            advance_meaning(client,content,stop_at=lambda r:(r.get("question") or {}).get("purpose") == "scope-intent")
            (tmp_path / "hold-scope-raw").touch()
            client.send_answer(judge_reply(client.current["question"]))
            marker = tmp_path / ".codex-harness/scope-raw-complete.json"
            deadline = time.monotonic()+30
            while not marker.exists() and time.monotonic()<deadline:
                time.sleep(.03)
            assert marker.exists()
            retained_scope = json.loads(marker.read_text())
            with open_plan_store_readonly(tmp_path) as store:
                lease = store.status()["run"]["effect"]
                from codex_harness.core.audit_process import process_observation
                assert lease["id"] == retained_scope["effectId"] and lease["ownerPid"] == retained_scope["ownerPid"]
                assert process_observation(lease["ownerPid"])["identity"] == lease["ownerIdentity"]
            if lease["ownerPid"] != client.process.pid:
                os.kill(lease["ownerPid"],signal.SIGTERM)
            client.reconnect()
            client.call("harness_plan_start",name="Consume retained HOW reply",idea="Retain the original B words")
            refused = client.call(
                "harness_loop_answer", error=True,
                request_id=pending["requestId"],
                answer=value,
                source="old-bound-B-interpretation",
            )
            # FIX17: superseded replies return the current baton; authority checks below remain.
            assert "Unknown tool" in refused["content"][0]["text"]
            advance_meaning(
                client,
                content,
                stop_at=lambda run: (run.get("question") or {}).get("purpose") == "execution"
                or at_gate(kind)(run),
            )
            assert (client.current.get("question") or {}).get("purpose") == "execution", (
                client.current
            )
            with open_plan_store_readonly(tmp_path) as store:
                b = approvals.current(store, "B")
                assert b["state"] == "approved" and b["request_id"] == request["request_id"]
                assert b["words"] == "Yes, approve the displayed subject."
            already_approved = True
        elif journey.startswith("reconnect-") and journey != "reconnect-a":
            client.reconnect()
            assert client.current["userRequest"]["request_id"] == request["request_id"]
        if journey == "gate-chain":
            request = decline_and_change(kind)
        if journey == "stale-a":
            vision = content["nodes"][0]
            vision["text"] += " Keep the displayed exact-byte receipt behavior."
            client.call(
                "harness_steer",
                direction="replan",
                rationale="User updated the displayed understanding before deciding",
                content=dict(nodes=[vision]),
            )
            text = "Yes, approve the old subject."
            refused = client.call(
                "harness_steer",
                error=True,
                direction="continue",
                rationale="Stale quote",
                user_words=dict(
                    request_id=request["request_id"],
                    expected_revision=request["plan_revision"],
                    quoted_message=text,
                    quote_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    actor_label="user",
                ),
            )
            assert "current" in refused["content"][0]["text"]
            with open_plan_store_readonly(tmp_path) as store:
                assert (approvals.current(store, "A") or {}).get("state") != "approved"
            request = arrive(kind)
        if journey == "frozen-kickoff":
            # FIX17 DELETE-structure: planner timeout/model/catalog renewal is
            # retired. Prove frozen operator pins plus real scope-only B renewal.
            with open_plan_store_readonly(tmp_path) as store:
                permitted = client.current["setup"]
                assert permitted and approvals.current(store, "B") is None
                assert store.successful_reviews(store.manifest_revision())
            old_b = request
            frozen_workers = old_b["subject"]["execution"]["workers"]
            approve()
            assert client.current["question"]["purpose"] == "execution"
            assert not (tmp_path / "build-entered").exists()
            changed = deepcopy(settings)
            changed.update(timeout=301, worker=dict(provider="codex", model="gpt-6.1-terra", effort="high"))
            changed["reviewers"] = [dict(r, model="gpt-6.1-terra") for r in settings["reviewers"]]
            (tmp_path / ".codex-harness/setup.json").write_text(json.dumps(changed), encoding="utf-8")
            pending_execution = client.current["question"]["requestId"]
            client.reconnect()
            # The provider was already launched: an unknown return is quarantined,
            # while the verified-return restart is covered in the retry golden.
            assert client.current["question"] is None or client.current["question"]["requestId"] == pending_execution or client.current.get("semanticHeld")
            assert client.current["setup"]["timeout"] == 300
            requirement = next(n for n in content["nodes"] if n["id"] == "R1")
            requirement["text"] = requirement["text"].replace(
                "Success exit 0, stderr empty.", "The success exit status is outside this requirement.")
            client.call("harness_steer", direction="replan", rationale="User proposes changed meaning under frozen kickoff",
                        content=dict(nodes=[requirement]))
            # C23/I1: a retained execution verdict must not move the replan
            # cursor to NEXT before this changed scope receives its own B.
            scope_answer("WHAT")
            request = arrive(kind)
            assert request["request_id"] != old_b["request_id"]
            assert {d["field"] for d in request["diff"]} >= {f"scope.nodes.{fixture_id(tmp_path,'R1')}.text", "scope.digest"}
            assert not {"reviewers", "execution.workers", "execution.timeout"} & {d["field"] for d in request["diff"]}
            assert "Changes from your previous decision" in request["card"]
            assert request["subject"]["execution"]["workers"] == frozen_workers
            assert request["subject"]["execution"]["timeout"] == 300
            with open_plan_store_readonly(tmp_path) as store:
                assert client.current["setup"] == permitted
                assert approvals.current(store, "B")["request_id"] == old_b["request_id"]
                reviews = store.successful_reviews(store.manifest_revision())
                assert reviews
                assert all(shard["launchSelection"]["model"] == settings["reviewers"][0]["model"]
                           and shard["didRunProvider"] and not shard["isMocked"] and shard["exitCode"] == 0
                           and not shard["invalid"] for review in reviews for shard in json.loads(review["shardDetails"]))
            assert not (tmp_path / "build-entered").exists()
        if not already_approved:
            client.words()
            pending = client.current["question"]
            value = judge_reply(pending)
            client.answer(value)
        if journey == "frozen-kickoff":
            with open_plan_store_readonly(tmp_path) as store:
                granted = approvals.current(store, "B")
                assert granted["request_id"] == request["request_id"]
                assert granted["subject"]["execution"]["workers"] == frozen_workers
        if journey == "gate-chain":
            reject_replay(pending, value, "A")
            kind = "plan-execution"
            request = decline_and_change(kind)
            for retired in (
                "harness_advance",
                "harness_compile",
                "harness_publish",
                "harness_roundtable",
                "harness_worker_start",
                "harness_worker_status",
                "harness_recovery",
                "harness_plan_resume",
            ):
                rejected = client.call(retired, error=True)
                assert "Unknown tool" in rejected["content"][0]["text"]
            assert not (tmp_path / ".codex-harness/worker-jobs").exists()
            client.words()
            pending = client.current["question"]
            value = judge_reply(pending)
            client.answer(value)
            reject_replay(pending, value, "B")
        if journey == "delivery":
            # Base :278: inconclusive scope is a real hold, but a newer explicit
            # B may approve the exact changed subject and retire that old wait.
            # A positive-only scope journey misses a redundant pending judgment.
            assert client.current["question"]["purpose"] == "execution"
            from codex_harness.core import planner_jobs
            with open_plan_store_readonly(tmp_path) as store:
                initial_job = planner_jobs.worker_job_status(tmp_path, client.current["jobId"])
            initial_run = json.loads(Path(initial_job["run"]["recordPath"]).read_text())
            stream_attempt = Path(initial_run["beads"][fixture_id(tmp_path, "C")]["evidenceDir"]) / "attempt"
            stream_segments = planner_jobs._directory(tmp_path, initial_job["jobId"]) / "segments"
            requirement = next(n for n in content["nodes"] if n["id"] == "R1")
            requirement["text"] = requirement["text"].replace(
                "Success exit 0, stderr empty.", "The success exit status is optional."
            )
            client.call(
                "harness_steer",
                direction="replan",
                rationale="A product obligation changed and needs an explicit decision",
                content=dict(nodes=[requirement]),
            )
            advance_meaning(
                client,
                content,
                stop_at=lambda run: (run.get("question") or {}).get("purpose") == "scope-intent",
            )
            assert client.current["question"]["purpose"] == "scope-intent"
            scope_id = client.current["question"]["judgment"]["id"]
            client.answer(judge_reply(client.current["question"], verdict="inconclusive"))
            assert client.current["effect"] is None and client.current["semanticHeld"]
            client.call(
                "harness_steer",
                direction="continue",
                rationale="Actual user requests a fresh explicit B for the displayed scope",
            )
            advance_meaning(
                client,
                content,
                stop_at=lambda run: at_gate("plan-execution")(run)
                or (run.get("question") or {}).get("purpose") == "scope-intent",
            )
            assert at_gate("plan-execution")(client.current), client.current
            renewed = client.current["userRequest"]
            assert renewed["scopeJudgment"]["status"] == "pending-judgment" and renewed["diff"]
            assert client.call("harness_judgment", judgment_id=scope_id)["current"]
            assert scope_id in {
                r["id"] for r in client.call("harness_plan_status")["pendingSemanticJudgments"]
            }
            approve()
            assert not client.call("harness_judgment", judgment_id=scope_id)["current"]
            status = client.call("harness_plan_status")
            assert scope_id not in {r["id"] for r in status["pendingSemanticJudgments"]}
        if journey == "stale-a":
            # Base :257: same-ID WHAT narrowing invalidates pending B words.
            # The earlier stale-A branch changes the vision itself; it cannot
            # catch consumers that bind subordinate obligations by ID alone.
            b = arrive("plan-execution")
            client.words()
            pending = deepcopy(client.current["question"])
            value = judge_reply(pending)
            requirement = next(n for n in content["nodes"] if n["id"] == "R1")
            requirement["text"] = requirement["text"].replace(
                "Success exit 0, stderr empty.",
                "The success exit status is outside this requirement.",
            )
            client.call(
                "harness_steer",
                direction="replan",
                rationale="A same-ID product obligation was explicitly removed",
                content=dict(nodes=[requirement]),
            )
            refused = client.call(
                "harness_loop_answer", error=True,
                request_id=pending["requestId"],
                answer=value,
                source="superseded-scope-interpretation",
            )
            # FIX17: superseded replies return the current baton; authority checks below remain.
            assert "Unknown tool" in refused["content"][0]["text"]
            scope_answer("WHAT")
            with open_plan_store_readonly(tmp_path) as store:
                assert approvals.current(store, "B") is None
            renewed = arrive("plan-execution")
            assert renewed["request_id"] != b["request_id"]
            assert renewed["subject"]["scope"]["nodes"][fixture_id(tmp_path,"R1")]["text"] == requirement["text"]
            approve()
        if kind != "C":
            request = arrive("C")
            if journey == "delivery":
                # Review of an integration repository cannot certify a missing
                # root or changed source/root bytes. Happy delivery keeps all
                # copies intact and cannot detect this authority leak.
                live = json.loads((tmp_path / ".codex-harness/live-provider.json").read_text())
                assert live["outputSeconds"] >= 3 and live["cpuSeconds"] >= 3 and live["flushSeconds"] >= 125, live
                retained = json.loads(
                    (tmp_path / ".codex-harness/retained-candidate.json").read_text()
                )
                record = json.loads(Path(retained["runPath"]).read_text())
                attempt = stream_attempt
                result = json.loads((attempt / "result.json").read_text())
                assert "thinking" in (attempt / "stderr.txt").read_text()
                assert result["permissionGuard"]["telemetryComplete"] and result["exitCode"] == 0
                assert result["cleanup"]["quiescenceVerified"] and result["cleanup"]["activeProcessesAfterTeardown"] == 0
                assert (attempt / "events.jsonl").read_text().endswith('{"type": "turn.completed"}\n')
                assert record["semanticContracts"]["state"] == "RED" and record["semanticValidation"]["state"] == "PASS"
                for segment in stream_segments.iterdir():
                    terminal = json.loads((segment / "terminal.json").read_text())
                    cleanup = json.loads((segment / "quiescence.json").read_text())["control"]
                    assert terminal["status"] in {"pending-judgment", "completed"}
                    assert cleanup["quiescenceVerified"] and cleanup["activeProcessesAfterTeardown"] == 0
                target = tmp_path / "hashslip.py"
                source = Path(record["repo"]) / "hashslip.py"
                original = target.read_bytes()
                assert source.read_bytes() == original
                assert (
                    request["delivery"]["fileHashes"]["hashslip.py"]
                    == hashlib.sha256(original).hexdigest()
                )
                for changed, raw, refusal_code in (
                    (target, None, "product-changed"),
                    (
                        source,
                        original + b"\n# changed retained source\n",
                        "acceptance-delivery-invalid",
                    ),
                    (target, b"print('different delivered product')\n", "product-changed"),
                ):
                    try:
                        changed.unlink() if raw is None else changed.write_bytes(raw)
                        refused = client.call(
                            "harness_steer",
                            error=True,
                            direction="continue",
                            rationale="Attempt C against missing or changed actual product bytes",
                            user_words=dict(
                                request_id=request["request_id"],
                                expected_revision=request["plan_revision"],
                                quoted_message="Yes",
                                quote_sha256=hashlib.sha256(b"Yes").hexdigest(),
                                actor_label="user",
                            ),
                        )
                        assert refused["refusalCode"] == refusal_code, refused
                        with open_plan_store_readonly(tmp_path) as store:
                            assert not store.connection.execute(
                                "SELECT 1 FROM acceptance_approvals"
                            ).fetchone()
                    finally:
                        changed.write_bytes(original)
            if journey == "gate-chain":
                client.reconnect()
                assert client.current["userRequest"]["request_id"] == request["request_id"]
                delivered = tmp_path / "hashslip.py"
                original = delivered.read_bytes()
                delivered.write_bytes(original + b"\n# changed after validation\n")
                refused = client.call(
                    "harness_steer",
                    error=True,
                    direction="continue",
                    rationale="Attempt C on changed delivery",
                    user_words=dict(
                        request_id=request["request_id"],
                        expected_revision=request["plan_revision"],
                        quoted_message="Yes",
                        quote_sha256=hashlib.sha256(b"Yes").hexdigest(),
                        actor_label="user",
                    ),
                )
                assert (
                    "delivery" in refused["content"][0]["text"]
                    or "revision" in refused["content"][0]["text"]
                )
                with open_plan_store_readonly(tmp_path) as store:
                    assert not store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchone()
                delivered.write_bytes(original)
                client.words("Please change this delivery before I accept it.")
                client.answer(judge_reply(client.current["question"], decision="changes_requested"))
                assert client.current["state"] == "PLAN"
                request = arrive("C")
            approve()
        assert client.current["state"] == "DONE"
        status = client.call("harness_plan_status")
        assert status["acceptance"]["accepted"]
        with open_plan_store_readonly(tmp_path) as store:
            assert (
                len(store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchall()) == 1
            )
            records = approvals.records(store, approvals.EVENT)
            assert {r["subject"]["kind"] for r in records} == {"A", "B"}
            assert all(not r["human_identity_verified"] for r in records)
            assert all(
                r["channel"]
                == (
                    "mcp-client-elicitation"
                    if journey == "reconnect-a" and r["subject"]["kind"] == "A" and r["state"] == "approved"
                    else "chat-confirmation"
                )
                for r in records
            )
        # A real transport replacement reads terminal authority once and never
        # offers a second C or reruns workers; in-process reopen missed this.
        retained = json.loads((tmp_path / ".codex-harness/retained-candidate.json").read_text())
        record = json.loads(Path(retained["runPath"]).read_text())
        client.reconnect()
        assert client.current["state"] == "DONE" and client.current["userRequest"] is None
        assert json.loads(Path(retained["runPath"]).read_text()) == record
        with open_plan_store_readonly(tmp_path) as store:
            assert (
                len(store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchall()) == 1
            )
        if journey == "delivery":
            target = tmp_path / "hashslip.py"
            target.write_bytes(target.read_bytes() + b"\n# changed after C\n")
            assert not client.call("harness_plan_status")["acceptance"]["accepted"]
            single_build_golden(tmp_path / "single-build")
        elif journey == "frozen-kickoff":
            automatic_retry_golden(tmp_path / "automatic-retries")
        elif journey == "reconnect-b":
            receipt_gaps_golden(tmp_path / "receipt-gaps")
    finally:
        client.close()
