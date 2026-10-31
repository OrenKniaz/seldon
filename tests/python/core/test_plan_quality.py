"""Quality checks through real store gates, publication and graph validation."""

import json
from pathlib import Path

import pytest
from tests.python.core.planning_support import (
    publication_evidence,
    source_controller_roundtable,
    source_only_confirmation,
    source_only_expansion,
)

from codex_harness.core.plan_quality import quality_report, render_quality_card
from codex_harness.core.plan_store import PHASES, PlanStoreError, open_plan_store
from codex_harness.core.roundtable import findings, resolve_finding

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit" / "spine.json"


@pytest.fixture
def store(tmp_path):
    with open_plan_store(tmp_path) as result:
        result.import_spine(FIXTURE)
        source_only_expansion(result)
        vision = result.spine().node("V1")
        vision["fields"].update(
            users=["diners"],
            first_user_moment="Split a bill",
            scope_in=["CLI"],
            scope_out=["history"],
        )
        result.upsert_node(vision)
        # Use the existing integration test, preserving one integration module.
        test = next(
            result.spine().node(key)
            for key in result.spine().ids("test")
            if result.spine().node(key)["fields"]["level"] == "e2e"
        )
        test["fields"].update(firstMoment=True, cleanState=True)
        result.upsert_node(test)
        bead = result.spine().node("A")
        bead["parents"].append("R6")
        result.upsert_node(bead)
        for question in ("Who uses it?", "First moment?", "Scope?"):
            result.answer(result.ask(question), "Confirmed by operator")
        yield result


def advance(store, target):
    while PHASES.index(store.phase()) < PHASES.index(target):
        current = store.phase()
        if current in ("align", "vision-lock"):
            source_only_confirmation(store, current, statement="I approve this exact understanding")
        if current == "roundtable":
            publication_evidence(store)
        store.advance(PHASES[PHASES.index(current) + 1], by="planner")


def checks(store):
    return {check["id"]: check for check in quality_report(store)["checks"]}


def publish(store, tmp_path):
    advance(store, "publish")
    publication_evidence(store)
    return store.publish(by="operator", export_dir=tmp_path / "exports")


def three_beads(store, chain):
    bead = store.spine().node("B")
    bead["fields"]["dependsOn"] = ["A"] if chain else []
    store.upsert_node(bead)
    bead["id"] = "C"
    bead["fields"]["ownedFiles"] = ["tipsplit/third.py"]
    bead["fields"]["dependsOn"] = ["B"] if chain else []
    store.upsert_node(bead)


def test_published_real_store_all_statuses_and_read_only(store, tmp_path):
    revision = publish(store, tmp_path)
    before = list(store.connection.iterdump())
    report = quality_report(store)
    assert report["planRevision"] == revision
    assert {check["id"]: check["status"] for check in report["checks"]} == {
        "expanded-and-aligned": "pass",
        "mapping": "pass",
        "first-moment-proof": "pass",
        "spine": "pass",
        "concurrency-ownership": "pass",
        "cross-bead-contract": "warn",
        "worker-readiness": "not-yet",
        "roundtable": "fail",
        "deferred-findings": "pass",
        "audit": "pass",
        "proof-level": "pass",
        "guesswork": "pass",
        "repeatable": "pass",
        "published-context": "pass",
    }
    assert report["score"] == 88
    assert quality_report(store) == report
    assert list(store.connection.iterdump()) == before


@pytest.mark.parametrize("failure", ["latest-required", "third-required"])
def test_prospective_required_failure_cannot_hide_behind_successes(store, failure):
    """New policy fixture; source reviewer processes are doubles, never live proof."""
    advance(store, "roundtable")
    revision = store.manifest_revision()
    selected = [*REVIEWERS, dict(name="third", provider="copilot")]
    slots = selected[:2] if failure == "latest-required" else selected
    # Seed the proposed event shape so RED exercises the existing quality consumer,
    # not a missing new method. The implementation's real writer is tested below.
    store._event(
        "plan-review-policy-declared",
        {
            "schema": "seldon/plan-review-policy-v1",
            "manifestRevision": revision,
            "stage": "plan",
            "slots": [dict(name=x["name"], provider=x["provider"], required=True) for x in slots],
        },
    )
    source_controller_roundtable(
        store,
        reviewers=selected[:2],
        process_stub=lambda *_: (0, "[]"),
        executable_for=lambda provider: provider,
    )
    source_controller_roundtable(
        store,
        reviewers=[selected[0] if failure == "latest-required" else selected[2]],
        process_stub=lambda *_: (7, "source-only reviewer failure"),
        executable_for=lambda provider: provider,
    )
    before = list(store.connection.iterdump())
    assert checks(store)["roundtable"]["status"] == "fail"
    assert list(store.connection.iterdump()) == before


@pytest.mark.parametrize("successful", [1, 2])
def test_predeclared_optional_failure_retains_existing_quality_thresholds(store, successful):
    advance(store, "roundtable")
    slots = [
        dict(name=row["name"], provider=row["provider"], required=True)
        for row in REVIEWERS[:successful]
    ]
    slots.append(dict(name="optional", provider="copilot", required=False))
    store.record_plan_review_policy(slots, store.manifest_revision())
    source_controller_roundtable(
        store,
        reviewers=REVIEWERS[:successful],
        process_stub=lambda *_: (0, "[]"),
        executable_for=lambda provider: provider,
    )
    source_controller_roundtable(
        store,
        reviewers=[dict(name="optional", provider="copilot")],
        process_stub=lambda *_: (7, "Source-only optional failure"),
        executable_for=lambda provider: provider,
    )
    check = checks(store)["roundtable"]
    assert check["status"] == ("fail" if successful == 1 else "warn")
    assert check["evidence"]["reviewPolicy"]["eligible"] is True
    assert check["evidence"]["reviewPolicy"]["optionalFailures"] == ["optional"]


def test_unlisted_attempt_is_not_silently_optional(store):
    advance(store, "roundtable")
    store.record_plan_review_policy(
        [dict(name=row["name"], provider=row["provider"], required=True) for row in REVIEWERS],
        store.manifest_revision(),
    )
    source_controller_roundtable(
        store,
        reviewers=REVIEWERS,
        process_stub=lambda *_: (0, "[]"),
        executable_for=lambda provider: provider,
    )
    source_controller_roundtable(
        store,
        reviewers=[dict(name="unlisted", provider="copilot")],
        process_stub=lambda *_: (7, "Source-only unlisted failure"),
        executable_for=lambda provider: provider,
    )
    state = checks(store)["roundtable"]
    assert state["status"] == "fail"
    assert "review-policy-undeclared-attempt" in state["evidence"]["reviewPolicy"]["reasons"]


def test_required_latest_reservation_failure_survives_reversed_completion(store):
    """Nested source-process doubles deterministically overlap real reservations.

    The older launch cannot finish until the newer launch has failed and committed.
    No sleeps, live processes/providers or fabricated review/reservation rows.
    """
    advance(store, "roundtable")
    revision = store.manifest_revision()
    store.record_plan_review_policy(
        [dict(name=row["name"], provider=row["provider"], required=True) for row in REVIEWERS],
        revision,
    )
    source_controller_roundtable(
        store,
        reviewers=REVIEWERS[1:],
        process_stub=lambda *_: (0, "[]"),
        executable_for=lambda provider: provider,
    )
    events = []

    def older_success(*_):
        events.append("older-reserved-and-launched")
        source_controller_roundtable(
            store,
            reviewers=REVIEWERS[:1],
            process_stub=lambda *_: (7, "SOURCE-ONLY later attempt failed"),
            executable_for=lambda provider: provider,
            max_rounds=2,
        )
        events.append("later-failure-committed")
        return 0, "[]"

    source_controller_roundtable(
        store,
        reviewers=REVIEWERS[:1],
        process_stub=older_success,
        executable_for=lambda provider: provider,
        max_rounds=2,
    )
    events.append("older-success-committed")
    rows = [
        dict(row)
        for row in store.connection.execute(
            "SELECT a.rowid AS reservation_order, r.rowid AS completion_order, r.id, "
            "r.exit_code FROM review_attempts a JOIN review_runs r ON r.id=a.id "
            "WHERE a.reviewer=? ORDER BY a.rowid",
            (REVIEWERS[0]["name"],),
        )
    ]
    print(json.dumps(dict(sourceOnly=True, events=events, records=rows)))
    assert [row["exit_code"] for row in rows] == [0, 7]
    assert rows[0]["completion_order"] > rows[1]["completion_order"]
    state = store.plan_review_policy_state(revision)
    required = next(row for row in state["slots"] if row["name"] == REVIEWERS[0]["name"])
    assert required["latestReviewId"] == rows[1]["id"]
    assert required["state"] == "unverified-or-failed"
    assert state["eligible"] is False and state["requiredFailures"] == [REVIEWERS[0]["name"]]
    assert checks(store)["roundtable"]["status"] == "fail"
    assert rows[0]["id"] not in {row["id"] for row in store.successful_reviews(revision)}
    with pytest.raises(PlanStoreError) as held:
        store._check_evidence(revision)
    assert held.value.code == "plan-review-policy-unmet"


def test_required_pending_reservations_hold_before_and_after_newer_success(store):
    advance(store, "roundtable")
    revision = store.manifest_revision()
    store.record_plan_review_policy(
        [dict(name=row["name"], provider=row["provider"], required=True) for row in REVIEWERS],
        revision,
    )
    source_controller_roundtable(
        store,
        reviewers=REVIEWERS[1:],
        process_stub=lambda *_: (0, "[]"),
        executable_for=lambda provider: provider,
    )
    checkpoints = []

    def observe_pending():
        state = store.plan_review_policy_state(revision)
        slot = next(row for row in state["slots"] if row["name"] == REVIEWERS[0]["name"])
        assert slot["incompleteAttempt"] and not state["eligible"]
        assert slot["state"] == "unverified-or-failed"
        assert checks(store)["roundtable"]["status"] == "fail"
        checkpoints.append(slot)

    def newer_success(*_):
        observe_pending()  # Newer attempt is incomplete; older attempt is also live.
        return 0, "[]"

    def older_success(*_):
        source_controller_roundtable(
            store,
            reviewers=REVIEWERS[:1],
            process_stub=newer_success,
            executable_for=lambda provider: provider,
            max_rounds=2,
        )
        observe_pending()  # Newer success must not clear the older live reservation.
        return 0, "[]"

    source_controller_roundtable(
        store,
        reviewers=REVIEWERS[:1],
        process_stub=older_success,
        executable_for=lambda provider: provider,
        max_rounds=2,
    )
    final = store.plan_review_policy_state(revision)
    slot = next(row for row in final["slots"] if row["name"] == REVIEWERS[0]["name"])
    assert len(checkpoints) == 2 and final["eligible"]
    assert not slot["incompleteAttempt"]
    assert slot["latestReviewId"] == checkpoints[1]["latestReviewId"]
    assert slot["latestAttemptId"] == checkpoints[0]["latestAttemptId"]
    assert slot["attemptOrdering"] == "reservation-rowid"
    print(json.dumps(dict(sourceOnly=True, pendingCheckpoints=checkpoints, finalSlot=slot)))


@pytest.mark.parametrize("mixed", [False, True])
def test_policy_legacy_reservation_provenance_is_explicit_and_mixed_holds(store, mixed):
    """Source-only old-database counterfactual; never rewrite production history."""
    advance(store, "roundtable")
    revision = store.manifest_revision()
    store.record_plan_review_policy(
        [dict(name=REVIEWERS[0]["name"], provider=REVIEWERS[0]["provider"], required=True)],
        revision,
    )
    source_controller_roundtable(
        store,
        reviewers=REVIEWERS[:1],
        process_stub=lambda *_: (0, "[]"),
        executable_for=lambda provider: provider,
    )
    # Characterize a database written before reservations existed. Keep actual
    # producer run rows unchanged; only this test fixture removes the new ledger.
    original = list(store.connection.execute("SELECT * FROM review_runs"))
    with store._transaction():
        store.connection.execute("DELETE FROM review_attempts")
    assert list(store.connection.execute("SELECT * FROM review_runs")) == original
    if mixed:
        source_controller_roundtable(
            store,
            reviewers=REVIEWERS[:1],
            process_stub=lambda *_: (0, "[]"),
            executable_for=lambda provider: provider,
            max_rounds=2,
        )
    state = store.plan_review_policy_state(revision)
    slot = state["slots"][0]
    assert state["eligible"] is (not mixed)
    assert slot["ambiguousAttemptOrder"] is mixed
    assert slot["attemptOrdering"] == (
        "reservation-rowid" if mixed else "legacy-completion-fallback"
    )


def test_early_phase_and_empty_store(store, tmp_path):
    assert all(check["status"] == "not-yet" for check in quality_report(store)["checks"])
    advance(store, "expand")
    result = checks(store)
    assert result.pop("expanded-and-aligned")["status"] == "pass"
    assert all(check["status"] == "not-yet" for check in result.values())
    empty = tmp_path / "empty"
    empty.mkdir()
    with open_plan_store(empty) as fresh:
        assert quality_report(fresh)["score"] == 0


@pytest.mark.parametrize("chain,width,status", [(False, 3, "pass"), (True, 1, "warn")])
def test_topological_width(store, chain, width, status):
    three_beads(store, chain)
    advance(store, "plan")
    result = checks(store)["concurrency-ownership"]
    assert result["evidence"]["parallelWidth"] == width
    assert result["status"] == status


def test_open_assumption_at_publish(store, tmp_path):
    item = store.add_assumption("Dollars only")
    publish(store, tmp_path)
    result = checks(store)["guesswork"]
    assert result["status"] == "warn"
    assert result["evidence"]["assumptions"] == [{"id": item, "text": "Dollars only"}]
    assert quality_report(store)["score"] == 85
    store.confirm_assumption(item, rationale="Test confirms the assumption")
    assert checks(store)["guesswork"]["status"] == "pass"


def test_alignment_threshold_and_late_questions(store):
    advance(store, "vision-lock")
    question = store.ask("Late unresolved detail?")
    assert checks(store)["expanded-and-aligned"]["status"] == "fail"
    store.waive(question, "Explicitly outside scope")
    assert checks(store)["expanded-and-aligned"]["status"] == "pass"
    vision = store.spine().node("V1")
    vision["fields"]["scope_out"] = " "
    store.upsert_node(vision)
    assert checks(store)["expanded-and-aligned"]["evidence"]["missingFields"] == ["scope_out"]


def test_fewer_than_three_questions_before_lock(tmp_path):
    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        source_only_expansion(store)
        vision = store.spine().node("V1")
        vision["fields"].update(
            {key: "filled" for key in ("users", "first_user_moment", "scope_in", "scope_out")}
        )
        store.upsert_node(vision)
        for _ in range(2):
            store.answer(store.ask("Detail?"), "Answer")
        advance(store, "vision-lock")
        store.answer(store.ask("Too late?"), "Yes")
        assert checks(store)["expanded-and-aligned"]["status"] == "warn"


def test_mapping_gaps_and_edge_case_warning(store):
    advance(store, "requirements")
    for key in store.spine().ids("requirement"):
        node = store.spine().node(key)
        node.update(title="Normal behavior", text="Successful result")
        store.upsert_node(node)
    assert checks(store)["mapping"]["status"] == "warn"
    node["fields"]["kind"] = "edge-case"
    store.upsert_node(node)
    assert checks(store)["mapping"]["status"] == "pass"
    story = store.spine().node("S1")
    story["parents"] = []
    store.upsert_node(story)
    assert checks(store)["mapping"]["status"] == "fail"


def test_spine_coverage_and_graph_overlap(store):
    advance(store, "plan")
    store.remove_node("test_money")
    assert checks(store)["spine"]["status"] == "fail"
    bead = store.spine().node("B")
    bead["fields"].update(ownedFiles=["tipsplit/money.py"], dependsOn=[])
    store.upsert_node(bead)
    result = checks(store)["concurrency-ownership"]
    assert result["status"] == "fail"
    assert result["evidence"]["errorCode"] == "plan-ownership-overlap"
    bead["fields"]["dependsOn"] = ["A"]
    store.upsert_node(bead)
    assert checks(store)["concurrency-ownership"]["evidence"]["overlaps"] == {
        "tipsplit/money.py": ["A", "B"]
    }


def test_findings_without_review_runs_do_not_prove_roundtable(store):
    advance(store, "roundtable")
    store.connection.execute(
        "CREATE TABLE review_findings (id TEXT, reviewer TEXT, provider TEXT, status TEXT)"
    )
    store.connection.executemany(
        "INSERT INTO review_findings VALUES (?, ?, ?, ?)",
        [
            ("f1", "guardian", "codex", "resolved"),
            ("f2", "realist", "claude", "open"),
        ],
    )
    assert checks(store)["roundtable"]["status"] == "fail"


REVIEWERS = [
    {"name": "guardian", "provider": "codex", "model": "gpt-6.1-sol"},
    {"name": "realist", "provider": "claude", "model": "claude-sonnet-5-5"},
]
FINDING = {
    "node_id": "R1",
    "severity": "major",
    "finding": "Missing verification",
    "suggestion": "Add real verification",
}


@pytest.mark.parametrize("status", ["resolved", "rejected"])
def test_clean_reviewer_counts_with_addressed_finding(store, status):
    advance(store, "roundtable")
    result = source_controller_roundtable(
        store,
        reviewers=REVIEWERS,
        process_stub=lambda launch, cwd, timeout: (
            0,
            json.dumps([FINDING] if launch.cli == "codex" else []),
        ),
    )
    assert checks(store)["roundtable"]["status"] == "warn"
    resolve_finding(store, findings(store)[0]["id"], status=status, resolution="Verified decision")
    before = list(store.connection.iterdump())
    check = checks(store)["roundtable"]
    assert check["status"] == "pass"
    assert check["evidence"] == {
        "roundId": result["roundId"],
        "reviewedRevision": result["planRevision"],
        "currentRevision": result["planRevision"],
        "stale": False,
        "reviewers": ["guardian", "realist"],
        "reviewerCount": 2,
        "providers": ["claude", "codex"],
        "providerCount": 2,
        "resolved": 1 if status == "resolved" else 0,
        "addressed": 1,
        "open": 0,
        "openFindingIds": [],
        "advisoryFindingIds": [],
    }
    assert list(store.connection.iterdump()) == before


def test_minor_only_round_passes_with_persisted_advisories(store):
    """Catch minor repair loops at publication; existing cases only close major findings."""
    advance(store, "roundtable")
    result = source_controller_roundtable(
        store,
        reviewers=REVIEWERS,
        process_stub=lambda launch, cwd, timeout: (
            0,
            json.dumps([{**FINDING, "severity": "minor" if launch.cli == "codex" else "note"}]),
        ),
    )
    rows = findings(store, expand=True)
    check = checks(store)["roundtable"]
    assert check["status"] == "pass"
    assert check["evidence"]["openFindingIds"] == []
    assert set(check["evidence"]["advisoryFindingIds"]) == {row["id"] for row in rows}
    assert len(rows) == 2 and all(row["status"] == "open" for row in rows)
    store._check_publish()
    from codex_harness.core.roundtable import render_roundtable_card

    card = render_roundtable_card(result, rows)
    assert "Minor (advisory)" in card and "Note (advisory)" in card
    assert "Fix open findings" not in card


@pytest.mark.parametrize("open_finding", [False, True])
def test_requirement_edit_makes_roundtable_stale_until_reviewed(store, open_finding):
    advance(store, "roundtable")
    reviewed = source_controller_roundtable(
        store,
        reviewers=REVIEWERS,
        process_stub=lambda launch, cwd, timeout: (
            0,
            json.dumps([FINDING] if open_finding and launch.cli == "codex" else []),
        ),
    )
    if not open_finding:
        assert checks(store)["roundtable"]["status"] == "pass"
    node = store.spine().node("R1")
    node["text"] += " Clarified verification."
    store.upsert_node(node)
    check = checks(store)["roundtable"]
    assert check["status"] == "fail"
    assert "plan changed since the last review; re-run the roundtable" in check["advice"]
    assert check["evidence"]["reviewedRevision"] == reviewed["planRevision"]
    assert check["evidence"]["currentRevision"] == store.spine().plan_revision()
    assert check["evidence"]["stale"] is True
    assert check["evidence"]["reviewerCount"] == 0
    assert check["evidence"]["open"] == int(open_finding)
    source_controller_roundtable(
        store, reviewers=REVIEWERS, process_stub=lambda launch, cwd, timeout: (0, "[]")
    )
    assert checks(store)["roundtable"]["status"] == ("warn" if open_finding else "pass")


def test_latest_round_excludes_old_reviewers(store):
    advance(store, "roundtable")
    first = source_controller_roundtable(
        store, reviewers=REVIEWERS, process_stub=lambda launch, cwd, timeout: (0, "[]")
    )
    assert checks(store)["roundtable"]["status"] == "pass"
    node = store.spine().node("R1")
    node["text"] += " Clarified verification"
    store.upsert_node(node)
    latest = source_controller_roundtable(
        store, reviewers=REVIEWERS[:1], process_stub=lambda launch, cwd, timeout: (0, "[]")
    )
    assert latest["planRevision"] != first["planRevision"]
    check = checks(store)["roundtable"]
    assert check["status"] == "warn"
    assert check["evidence"]["roundId"] == latest["roundId"]
    assert check["evidence"]["reviewers"] == ["guardian"]
    assert check["evidence"]["providers"] == ["codex"]


def test_open_finding_from_older_round_blocks_clean_latest_round(store):
    advance(store, "roundtable")
    first = source_controller_roundtable(
        store,
        reviewers=REVIEWERS[:1],
        process_stub=lambda launch, cwd, timeout: (0, json.dumps([FINDING])),
    )
    node = store.spine().node("R1")
    node["text"] += " Later revision"
    store.upsert_node(node)
    latest = source_controller_roundtable(
        store, reviewers=REVIEWERS, process_stub=lambda launch, cwd, timeout: (0, "[]")
    )
    row = findings(store)[0]
    assert row["round_id"] == first["roundId"]
    assert row["plan_revision"] != latest["planRevision"]
    check = checks(store)["roundtable"]
    assert check["status"] == "warn"
    assert check["evidence"]["roundId"] == latest["roundId"]
    assert check["evidence"]["reviewerCount"] == check["evidence"]["providerCount"] == 2
    assert check["evidence"]["openFindingIds"] == [row["id"]]
    resolve_finding(store, row["id"], status="rejected", resolution="Outside accepted scope")
    assert checks(store)["roundtable"]["status"] == "pass"


def test_published_context_uses_snapshot_and_rejects_stale_refs(store, tmp_path):
    advance(store, "publish")
    publication_evidence(store)
    assert checks(store)["published-context"]["status"] == "fail"
    revision = store.publish(by="operator", export_dir=tmp_path / "exports")
    requirement = store.spine().node("R1")
    requirement["text"] += " Later edit"
    store.upsert_node(requirement)
    assert checks(store)["published-context"]["status"] == "pass"
    original = store.published_manifest

    def stale(revision):
        manifest = original(revision)
        manifest["beads"]["A"]["requirementRefs"][0]["revision"] = "stale"
        return manifest

    # Inject a consumer-visible corrupt projection without mutating canonical state.
    store.published_manifest = stale
    assert checks(store)["published-context"]["evidence"]["invalidBeadIds"] == ["A"]
    assert revision != store.spine().plan_revision()


def test_card_is_deterministic_compact_and_limits_advice(store, tmp_path):
    publish(store, tmp_path)
    report = quality_report(store)
    for index, check in enumerate(report["checks"]):
        check["advice"] = f"Advice {index}"
    card = render_quality_card(report)
    assert card == render_quality_card(json.loads(json.dumps(report)))
    assert "88/100" in card
    assert len(card.splitlines()) == 18
    assert "Advice 2" in card and "Advice 3" not in card


@pytest.mark.parametrize("surface", ["compile_manifest", "render_markdown"])
def test_repeatability_detects_changing_outputs(store, monkeypatch, surface):
    from codex_harness.core import plan_quality

    advance(store, "plan")
    original = getattr(plan_quality, surface)
    count = 0

    def changing(spine):
        nonlocal count
        count += 1
        value = original(spine)
        value["nonce"] = count
        return value

    monkeypatch.setattr(plan_quality, surface, changing)
    assert checks(store)["repeatable"]["status"] == "fail"


@pytest.mark.parametrize(
    "available,same", [("codex", True), ("codex claude", True), ("codex claude", False)]
)
def test_available_provider_coverage_flows_from_roundtable_to_quality(
    store, monkeypatch, available, same
):
    """Catch provider deduplication losing the second review; old quality tests used two providers.

    Protect current-revision canonical roundtable persistence through its quality consumer,
    including truthful single-provider labels and refusal when another provider exists.
    Existing checks miss discovery during stored reads and exhausted explicit requests.
    Controlled provider responses establish source integration only.
    """
    import sys

    from tests.python.core.planning_support import source_only_expansion

    from codex_harness.core import roundtable

    source_only_expansion(store)
    advance(store, "roundtable")
    selected = [REVIEWERS[0], {**REVIEWERS[0], "name": "second"}] if same else REVIEWERS
    monkeypatch.setattr(
        roundtable,
        "_executable",
        lambda provider, _: sys.executable if provider in available.split() else None,
    )
    if same and "claude" in available:
        before = list(store.connection.iterdump())
        with pytest.raises(ValueError, match="claude"):
            source_controller_roundtable(
                store, reviewers=selected, process_stub=lambda *_: (0, "[]")
            )
        assert list(store.connection.iterdump()) == before
        return
    result = source_controller_roundtable(
        store, reviewers=selected, process_stub=lambda *_: (0, "[]")
    )

    def forbidden_discovery(*_):
        pytest.fail("Recorded coverage and exhausted explicit reviews must not discover providers")

    monkeypatch.setattr(roundtable, "_executable", forbidden_discovery)
    before = list(store.connection.iterdump())
    with pytest.raises(PlanStoreError, match="used 1/1"):
        roundtable.run_roundtable(store, reviewers=selected, max_total=1)
    assert list(store.connection.iterdump()) == before
    check = checks(store)["roundtable"]
    assert check["status"] == "pass", check
    assert len(store.successful_reviews(store.manifest_revision())) == 2
    assert (check["evidence"].get("reviewIndependence") == "single-provider") is same
    assert (result.get("reviewIndependence") == "single-provider") is same
    if same:
        assert (
            "Reviewed by two codex reviewers; only one provider was available."
            in roundtable.render_roundtable_card(result, [])
        )
        assert "cross-subscription evidence is missing" not in check["advice"]
