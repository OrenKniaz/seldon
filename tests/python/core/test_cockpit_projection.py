"""Source-only cockpit mechanics from disposable stores; no providers or UI proof."""

import json

import pytest
from tests.python.core.planning_support import (
    source_only_confirmation,
    source_only_publication,
    source_only_vision_lock,
)
from tests.python.core.test_plan_store import draft_inputs

from codex_harness.core import status_api
from codex_harness.core.acceptance import derive_scenarios, run_acceptance
from codex_harness.core.plan_store import PHASES, PlanStore
from codex_harness.core.record_io import write_record_json
from codex_harness.core.roundtable import run_roundtable
from codex_harness.core.status_api import snapshot
from codex_harness.core.test_audit import audit_revised_tests


def node(identity, kind, parents, **fields):
    return dict(
        id=identity,
        kind=kind,
        title=identity,
        text=f"Recorded {identity}",
        parents=parents,
        fields=fields,
    )


def tree(root):
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def publish_local_fixture(store, root):
    """Source-only publication mechanics with real red/green proof and injected reviews."""
    store.upsert_node(
        node(
            "V1",
            "vision",
            [],
            validation=["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
            requirementFiles=[],
        )
    )
    store.upsert_node(node("F1", "feature", ["V1"]))
    store.upsert_node(node("R1", "requirement", ["F1"]))
    store.upsert_node(
        node(
            "A",
            "bead",
            ["R1"],
            ownedFiles=["value.py"],
            dependsOn=[],
            lessons=[],
            proofModule="test_value.py",
        )
    )
    store.upsert_node(node("T1", "test", ["A"], module="test_value.py", level="e2e"))
    template = store.workspace / "template"
    (template / "tests").mkdir(parents=True)
    (template / "value.py").write_text("def value(n): return None\n", encoding="utf-8")
    (template / "tests" / "test_value.py").write_text(
        "import unittest\nfrom value import value\nclass Check(unittest.TestCase):\n"
        "    def test_increment(self): self.assertEqual(value(2), 3)\n",
        encoding="utf-8",
    )
    implementation = root / "local-implementation-fixture"
    implementation.mkdir()
    (implementation / "value.py").write_text("def value(n): return n + 1\n", encoding="utf-8")
    manifest = store.compile_template(template)
    audit = audit_revised_tests(
        repo=store.workspace,
        base_ref="template",
        revised_template=template,
        manifest=manifest,
        impact={"affectedBeads": ["A"]},
        oracle_dir=implementation,
    )
    assert audit["ok"], audit
    store.record_audit(store.manifest_revision(), audit)
    run_roundtable(
        store,
        reviewers=[
            dict(name="fixture-codex", provider="codex"),
            dict(name="fixture-claude", provider="claude"),
        ],
        runner=lambda *args: (0, "[]"),
        executable_for=lambda provider: provider,
    )
    # The existing evidence double asserts passing audit, mocked review rows and
    # no successful real review; all other publication guards remain active.
    with source_only_publication():
        while store.phase() != "publish":
            phase = store.phase()
            if phase in ("align", "vision-lock"):
                source_only_confirmation(store, phase, statement="Local fixture confirmation")
            store.advance(PHASES[PHASES.index(phase) + 1], by="planner")
        return store.publish(by="planner", export_dir=store.workspace / "plan")


def test_unbound_or_stale_run_is_not_current_cockpit_blocking_or_crew(tmp_path):
    workspace = tmp_path / "planner"
    workspace.mkdir()
    with PlanStore(workspace) as store:
        published = publish_local_fixture(store, tmp_path)
        run = tmp_path / "runs" / "old-run"
        (run / "evidence").mkdir(parents=True)
        record = dict(
            runId="old-run",
            planRevision="older-publication",
            status="held: permission guard breach",
            beads={
                "A": {
                    "status": "held: permission guard breach",
                    "provider": "local-fixture",
                    "model": "recorded",
                    "attempt": {"startedAt": "2026-01-01T00:00:00Z"},
                }
            },
        )
        write_record_json(run / "evidence" / "run.json", record)
        report = snapshot(workspace=workspace, runs_root=tmp_path / "runs")
        assert report["currentRunId"] == "old-run" and report["currentAgents"]
        assert report["cockpit"]["crew"] is None
        assert not any(
            "old-run:A" == item["id"] for item in report["cockpit"]["answers"]["blocking"] or []
        )
        assert report["cockpit"]["source"]["reason"]
        assert any(event.get("historical") is True for event in report["cockpit"]["chronicle"])
        record["planRevision"] = published
        write_record_json(run / "evidence" / "run.json", record)
        bound = snapshot(workspace=workspace, runs_root=tmp_path / "runs")["cockpit"]
        assert bound["crew"] and any(
            item["id"] == "old-run:A" for item in bound["answers"]["blocking"]
        )
        changed = store.spine().node("F1")
        changed["title"] = "New intent after publication"
        store.upsert_node(changed)
        stale = snapshot(workspace=workspace, runs_root=tmp_path / "runs")["cockpit"]
        assert stale["crew"] is None
        assert not any(item["id"] == "old-run:A" for item in stale["answers"]["blocking"] or [])
        assert stale["source"]["reason"]


def test_optional_cockpit_preserves_original_api_and_actual_store(tmp_path):
    with PlanStore(tmp_path) as store:
        store.upsert_node(node("V1", "vision", [], vision="A recorded promise"))
        store.upsert_node(node("F1", "feature", ["V1"]))
        question = store.ask("Which source should we preserve?", ["F1"])
        revision = store.spine().plan_revision()
    before = tree(tmp_path)
    report = snapshot(workspace=tmp_path)
    assert {
        "plan",
        "runs",
        "mail",
        "reviews",
        "agents",
        "currentRunId",
        "currentAgents",
        "labels",
        "verdict",
        "efficiencyTrend",
    } <= report.keys()
    cockpit = report["cockpit"]
    assert set(cockpit) == {
        "source",
        "vision",
        "answers",
        "areas",
        "capabilities",
        "waves",
        "crew",
        "chronicle",
        "recordedReviews",
    }
    assert cockpit["source"]["planRevision"] == revision
    assert cockpit["vision"]["promise"] == "A recorded promise"
    assert [item["id"] for item in cockpit["capabilities"]] == ["F1"]
    assert any(item["id"] == question for item in cockpit["answers"]["needsYou"])
    assert cockpit["waves"]["status"] == "unavailable"
    assert "waveId" not in cockpit["answers"]["where"]
    assert tree(tmp_path) == before


def recorded_review_snapshots(tmp_path):
    """Real persistence, fixed injected returns; no provider or effective-model proof."""
    from tests.python.core.test_roundtable import FIXTURE

    from codex_harness.core.roundtable import findings

    workspace = tmp_path / "recorded-reviews"
    with PlanStore(workspace) as store:
        store.import_spine(FIXTURE)
        source_only_confirmation(store, "align", statement="Source-only confirmation")
        store.advance("expand", by="planner")
        reviewers = [
            dict(name="Review Alpha", provider="codex", model="requested-alpha"),
            dict(name="Review Beta", provider="claude", model="requested-beta"),
        ]
        run_roundtable(
            store,
            reviewers=reviewers,
            executable_for=lambda provider: provider,
            runner=lambda launch, *_: (0, "[]") if launch.cli == "codex" else (7, "[]"),
        )
        error = snapshot(workspace=workspace, runs_root=tmp_path / "absent-runs")
        bead = store.spine().node(store.spine().ids("bead")[0])
        bead["title"] += " revised"
        store.upsert_node(bead)

        def response(launch, *_):
            rows = [dict(node_id="R1", severity="major", finding="Shared date boundary gap",
                         suggestion="Specify the local date boundary")]
            if launch.cli == "codex":
                rows.extend(
                    dict(node_id="R1", severity="minor", finding=text, suggestion="Record behavior")
                    for text in ("Atomic save interrupted", "Unicode names policy", "Archive history rules")
                )
            return 0, json.dumps(rows)

        result = run_roundtable(store, reviewers=reviewers, runner=response,
                                executable_for=lambda provider: provider)
        current = [row for row in findings(store) if row["round_id"] == result["roundId"]]
        for row, disposition in zip(
            [row for row in current if row["memberCount"] == 1],
            ("resolved", "rejected", "deferred"), strict=True,
        ):
            store.resolve_findings([dict(id=row["id"], status=disposition,
                                         resolution=f"Recorded {disposition} reason")])
        ready = snapshot(workspace=workspace, runs_root=tmp_path / "absent-runs")
        question = store.ask("Which timezone should the product use?", ["R1"])
        asked = snapshot(workspace=workspace)
        store.answer(question, "Local timezone")
        waived = store.ask("Should old names be kept?", ["R1"])
        store.waive(waived, "Already specified")
        answered = snapshot(workspace=workspace, runs_root=tmp_path / "absent-runs")
    return workspace, dict(error=error, ready=ready, asked=asked, answered=answered)


def test_recorded_reviews_and_plain_summary_from_actual_store_are_read_only(tmp_path):
    """Protect useful recorded detail; raw gate headlines and lost attribution were the gap.

    Strongest boundary: real store/roundtable -> RO snapshot, including revision
    change and dispositions. Injected reviewer returns are explicit source-only evidence.
    """
    workspace, samples = recorded_review_snapshots(tmp_path)
    assert samples["error"]["cockpit"]["answers"]["rightNow"]["code"] == "recorded-review-error"
    ready = samples["ready"]["cockpit"]
    assert ready["answers"]["rightNow"]["text"] == "1 recorded review points still need a response."
    reviews = ready["recordedReviews"]
    assert reviews["availability"] == "available" and reviews["unboundRowCount"] == 0
    current = next(row for row in reviews["rounds"] if row["relevance"] == "current")
    assert current["outcome"] == "completed"
    assert current["reviewedManifestRevision"] == samples["ready"]["plan"]["manifest_revision"]
    assert current["reviewedManifestRevision"] != reviews["sourceRevision"]
    assert [r["requestedModel"] for r in current["reviewers"]] == ["requested-alpha", "requested-beta"]
    assert all(r["isMocked"] is True and r["didRunProvider"] is False for r in current["reviewers"])
    shared = next(cluster for cluster in current["clusters"] if len(cluster["members"]) == 2)
    assert {member["reviewer"] for member in shared["members"]} == {"Review Alpha", "Review Beta"}
    members = [member for cluster in current["clusters"] for member in cluster["members"]]
    assert {member["disposition"] for member in members} == {"open", "resolved", "rejected", "deferred"}
    assert {member["reason"] for member in members if member["reason"]} == {
        "Recorded resolved reason", "Recorded rejected reason", "Recorded deferred reason"
    }
    old = next(row for row in reviews["rounds"] if row["relevance"] == "historical")
    assert old["outcome"] == "error" and old["reviewers"][0]["recordedFindingCount"] == 0
    assert samples["asked"]["cockpit"]["answers"]["rightNow"]["code"] == "recorded-questions"
    assert samples["asked"]["cockpit"]["answers"]["needsYou"][0]["question"] == "Which timezone should the product use?"
    assert samples["answered"]["cockpit"]["answers"]["needsYou"] == []
    before = tree(workspace)
    for _ in range(2):
        again = snapshot(workspace=workspace, runs_root=tmp_path / "absent-runs")
        assert again["cockpit"]["recordedReviews"] == reviews
    assert tree(workspace) == before
    public = json.dumps(reviews)
    assert not any(name in public for name in ("shardDetails", "stderr", "artifactPath", "processId", "request_id"))


def test_recorded_review_partial_legacy_data_never_becomes_activity_or_known_zero(tmp_path):
    workspace, _ = recorded_review_snapshots(tmp_path)
    with PlanStore(workspace) as store:
        # Explicit corrupt/legacy doubles on otherwise real records. Reservation
        # rows are deliberately ignored; their existence is not process activity.
        store.connection.execute("INSERT INTO review_attempts VALUES ('orphan','foreign','Reserved','codex')")
        store.connection.execute("UPDATE review_runs SET round_id='' WHERE reviewer='Review Alpha'")
        store.connection.execute("UPDATE review_runs SET finished_at='not-a-date', model=NULL WHERE reviewer='Review Beta'")
        store.connection.execute("UPDATE review_findings SET cluster_id='missing-canonical'")
    before = tree(workspace)
    report = snapshot(workspace=workspace)
    reviews = report["cockpit"]["recordedReviews"]
    assert reviews["availability"] == "partial" and reviews["unboundRowCount"] == 2
    assert all(row["clusters"] is None for row in reviews["rounds"] if row["relevance"] == "current")
    assert all(reviewer["requestedModel"] is None for row in reviews["rounds"] for reviewer in row["reviewers"])
    assert "Reserved" not in json.dumps(reviews)
    assert not any(word in json.dumps(reviews) for word in ('"running"', '"pending"', '"liveness"'))
    assert report["cockpit"]["answers"]["rightNow"]["code"] != "recorded-review-points"
    assert tree(workspace) == before


def test_recorded_review_conflicting_terminal_identity_stays_unknown(tmp_path):
    workspace, samples = recorded_review_snapshots(tmp_path)
    current = next(row for row in samples["ready"]["cockpit"]["recordedReviews"]["rounds"]
                   if row["relevance"] == "current")
    with PlanStore(workspace) as store:
        events = [json.loads(row[0]) for row in store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='roundtable-completed' ORDER BY id DESC"
        )]
        event = next(row for row in events if row["roundId"] == current["id"])
        contradictory = json.loads(json.dumps(event))
        contradictory["reviewers"][0]["name"] = "Contradictory recorded reviewer"
        # Explicit terminal-event doubles after real ingestion: a matching event
        # must not conceal an earlier contradictory reviewer/provider identity.
        store._event("roundtable-completed", contradictory)
        store._event("roundtable-completed", event)
    before = tree(workspace)
    report = snapshot(workspace=workspace)
    row = next(row for row in report["cockpit"]["recordedReviews"]["rounds"]
               if row["id"] == current["id"])
    assert row["outcome"] == "unknown"
    assert report["cockpit"]["answers"]["rightNow"]["code"] != "recorded-review-points"
    assert tree(workspace) == before


def confirmation_snapshots(tmp_path):
    """Actual negotiated bridge with a source-only wire double; no native host."""
    from tests.python.core.test_mcp_planner import answer_native, begin_native

    from codex_harness.core.mcp_planner import PlannerServer

    workspace = tmp_path / "confirmation-display"
    server = PlannerServer(workspace)
    try:
        store = server._store()
        store.upsert_node(dict(id="V1", kind="vision", title="Daily tracker understanding",
                               text="Track habits locally", parents=[], fields={
                                   "users": ["One local user"],
                                   "first_user_moment": "Check off today's habit",
                                   "internalMetadata": {"private": "never-export-this"},
                               }))
        before = snapshot(workspace=workspace)
        outbound = begin_native(server)
        request_id = server._pending_confirmation["request"]["request_id"]
        original = tree(workspace)
        recorded = snapshot(workspace=workspace)
        assert tree(workspace) == original
        assert outbound["id"] not in json.dumps(recorded) and request_id not in json.dumps(recorded)
        answer_native(server, outbound["id"], action="cancel")
        cancelled = snapshot(workspace=workspace)
        begin_native(server)
        vision = store.spine().node("V1")
        vision["title"] = "Changed understanding"
        store.upsert_node(vision)
        stale = snapshot(workspace=workspace)
        source_only_confirmation(store, "align", statement="Explicit simulated later confirmation")
        store.advance("expand", by="planner")
        no_prerequisite = snapshot(workspace=workspace)
        return dict(before=before, recorded=recorded, cancelled=cancelled, stale=stale,
                    no_prerequisite=no_prerequisite)
    finally:
        server.close()


def test_existing_approval_item_enriches_only_exact_recorded_understanding(tmp_path):
    samples = confirmation_snapshots(tmp_path)
    original = samples["before"]["cockpit"]["answers"]["needsYou"]
    enriched = samples["recorded"]["cockpit"]["answers"]["needsYou"]
    assert len(original) == len(enriched) == 1 and original[0]["id"] == enriched[0]["id"]
    assert enriched[0]["question"] == original[0]["question"]
    understanding = enriched[0]["recordedUnderstanding"]
    assert understanding["title"] == "Daily tracker understanding"
    assert understanding["chatConnection"] == "unknown"
    assert understanding["notice"] == "Recorded understanding only. The chat connection and whether a prompt is still open are unknown."
    assert {section["label"]: section["text"] for section in understanding["sections"]} == {
        "Vision": "Track habits locally", "Users": "- One local user",
        "First moment": "Check off today's habit",
    }
    assert "never-export-this" not in json.dumps(understanding)
    assert samples["recorded"]["plan"]["approval"]["granted"] is False
    for state in ("before", "cancelled", "stale"):
        needs = samples[state]["cockpit"]["answers"]["needsYou"]
        assert len(needs) == 1 and not needs[0].get("recordedUnderstanding")
    assert samples["no_prerequisite"]["cockpit"]["answers"]["needsYou"] == []


def test_absent_and_malformed_sources_stay_unavailable_without_create(tmp_path):
    missing = tmp_path / "missing" / "project"
    report = snapshot(workspace=missing, runs_root=missing)
    assert report["cockpit"]["vision"] is None and report["cockpit"]["capabilities"] is None
    assert report["cockpit"]["answers"]["needsYou"] is None
    assert report["cockpit"]["waves"]["status"] == "unavailable"
    assert not missing.parent.exists()
    database = tmp_path / ".codex-harness" / "plan.sqlite3"
    database.parent.mkdir()
    database.write_bytes(b"malformed database fixture")
    run = tmp_path / "runs" / "bad"
    run.mkdir(parents=True)
    (run / "run.json").write_text("{", encoding="utf-8")
    before = tree(tmp_path)
    report = snapshot(workspace=tmp_path, runs_root=tmp_path / "runs")
    assert report["plan"] == "unavailable" and report["cockpit"]["vision"] is None
    assert report["cockpit"]["source"]["planRevision"] is None
    assert report["cockpit"]["source"]["runBinding"]["status"] == "unavailable"
    assert tree(tmp_path) == before


def test_canonical_draft_waves_never_become_live_current_or_work_proof(tmp_path):
    with PlanStore(tmp_path) as store:
        inputs = draft_inputs(store)
        store.record_wave_map(**inputs)
    before = tree(tmp_path)
    cockpit = snapshot(workspace=tmp_path)["cockpit"]
    waves = cockpit["waves"]
    assert waves["status"] == "draft"
    assert [item["id"] for item in waves["items"]] == ["W1", "W2"]
    assert all(item["isDraft"] and item["phase"] == "draft plan" for item in waves["items"])
    assert waves["items"][0]["itemCounts"]["total"] == 1
    assert waves["items"][1]["itemCounts"]["total"] is None  # Sketches are not beads.
    assert all(item["itemCounts"]["done"] is None for item in waves["items"])
    assert "waveId" not in cockpit["answers"]["where"]
    assert waves["projection"]["lockState"] == "unconfirmed"
    assert cockpit["vision"]["proof"]["requirementValidation"] is None
    assert tree(tmp_path) == before
    with PlanStore(tmp_path) as store:
        requirement = store.spine().node("R2")
        requirement["text"] = "Changed intent"
        store.upsert_node(requirement)
    stale = snapshot(workspace=tmp_path)["cockpit"]["waves"]
    assert stale["status"] == "draft" and stale["projection"]["state"] == "stale"
    assert stale["items"] is None  # Do not treat the invalid raw carrier as validated truth.


@pytest.fixture
def scenario_store(tmp_path):
    """Canonical writer + real local scenario commands; no provider/real-use proof."""
    workspace, repo = tmp_path / "planner", tmp_path / "product"
    workspace.mkdir()
    repo.mkdir()
    (repo / "check.py").write_text("print('local scenario fixture')\n", encoding="utf-8")
    steps = ['"{python}" -B "{repo}/check.py"']
    expect = {"stdout": "local scenario fixture", "exit": 0}
    with PlanStore(workspace) as store:
        store.upsert_node(
            node(
                "V1",
                "vision",
                [],
                vision="Recorded promise",
                first_user_moment=dict(title="First", steps=steps, expect=expect),
                scope_in=[dict(title="Unlinked scope", steps=steps, expect=expect)],
            )
        )
        store.upsert_node(node("F1", "feature", ["V1"]))
        store.upsert_node(node("F2", "feature", ["V1"]))
        store.upsert_node(
            node(
                "X1",
                "acceptance",
                ["F1"],
                given="Local fixture",
                steps=steps,
                expect=expect,
                nodeIds=["X1"],
            )
        )
        store.upsert_node(
            node(
                "X2",
                "acceptance",
                ["F1", "F2"],
                given="Ambiguous linkage",
                steps=steps,
                expect=expect,
                nodeIds=["X2"],
            )
        )
        source_only_vision_lock(store, statement="Fixture records this vision")
        store.save_acceptance_scenarios(derive_scenarios(store.locked_acceptance_spine()))
        yield store, repo


def test_real_scenario_writer_keeps_global_orphans_and_separate_proof_units(scenario_store):
    store, repo = scenario_store
    run = run_acceptance(store, repo, subject="product")
    # Close the writer before byte-exact comparison: active SQLite WAL readers
    # legitimately use SHM read marks, which are not durable planning truth.
    store.close()
    before = tree(store.workspace)
    report = snapshot(workspace=store.workspace)
    cockpit = report["cockpit"]
    global_checks = cockpit["vision"]["proof"]["productScenarios"]
    assert global_checks["passed"] == global_checks["total"] == 4
    assert global_checks["runId"] == run["id"] and global_checks["buildRevision"] == run["revision"]
    assert cockpit["vision"]["scenarioScope"] == {
        "total": 4,
        "unlinkedIds": ["X2", "first-moment", "scope-1"],
    }
    caps = {item["id"]: item for item in cockpit["capabilities"]}
    assert caps["F1"]["proof"]["productScenarios"]["total"] == 1
    assert caps["F2"]["proof"]["productScenarios"] is None
    assert all(item["proof"]["requirementValidation"] is None for item in caps.values())
    assert all("accepted" not in item for item in caps.values())
    assert cockpit["vision"]["acceptance"] == {
        key: report["plan"]["acceptance"][key] for key in cockpit["vision"]["acceptance"]
    }
    assert tree(store.workspace) == before


@pytest.mark.parametrize(
    "fault",
    [
        "oracle",
        "mock",
        "plan",
        "build",
        "scenarios",
        "missing-evidence",
        "row-identity",
        "missing-plan",
        "missing-build",
        "missing-scenario",
        "foreign-plan",
        "foreign-build",
        "foreign-scenario",
    ],
)
def test_scenario_credit_requires_current_original_product_binding(scenario_store, fault):
    store, repo = scenario_store
    run = run_acceptance(store, repo, subject="oracle" if fault == "oracle" else "product")
    if fault == "mock":
        # Explicit synthetic fault injection into an otherwise real canonical observation.
        run["isMocked"] = True
    elif fault == "plan":
        changed = store.spine().node("F1")
        changed["title"] = "Changed capability"
        store.upsert_node(changed)
    elif fault == "build":
        (repo / "check.py").write_text("print('different product bytes')\n", encoding="utf-8")
    elif fault == "scenarios":
        scenarios = store.acceptance_scenarios()
        scenarios[0]["expect"] = {"exit": 5}
        store.save_acceptance_scenarios(scenarios)
    elif fault == "missing-evidence":
        store.connection.execute("DELETE FROM acceptance_runs")
    elif fault == "row-identity":
        run["id"] = "nonexistent-original-run"
    elif fault.startswith("missing-"):
        run.pop(
            {
                "missing-plan": "planRevision",
                "missing-build": "revision",
                "missing-scenario": "scenarioRevision",
            }[fault]
        )
    elif fault.startswith("foreign-"):
        run[
            {
                "foreign-plan": "planRevision",
                "foreign-build": "revision",
                "foreign-scenario": "scenarioRevision",
            }[fault]
        ] = "foreign-binding"
    if fault in {"mock", "row-identity"} or fault.startswith(
        ("missing-plan", "missing-build", "missing-scenario", "foreign-")
    ):
        store.connection.execute("UPDATE acceptance_runs SET result_json=?", (json.dumps(run),))
    report = snapshot(workspace=store.workspace)["cockpit"]
    if report["vision"] is None:
        assert report["capabilities"] is None and report["source"]["reason"]
        return
    assert report["vision"]["proof"]["productScenarios"] is None
    assert all(item["proof"]["productScenarios"] is None for item in report["capabilities"])
    assert "unavailable" in report["vision"]["proofUnavailableReason"]


@pytest.mark.parametrize("proxy", [False, True])
def test_recorded_acceptance_and_no_decisions_do_not_become_verified_or_ready(
    tmp_path, monkeypatch, proxy
):
    """Synthetic acceptance projection checks copying; no personal approval is asserted."""
    with PlanStore(tmp_path) as store:
        store.upsert_node(node("V1", "vision", []))
        store.upsert_node(node("F1", "feature", ["V1"]))
        source_only_confirmation(store, "align", statement="Recorded fixture words")
        store.advance("expand", by="planner")
    canonical_plan = status_api._plan
    recorded = dict(
        accepted=True,
        recordedUserAccepted=not proxy,
        isProxy=proxy,
        approvedBy="proxy:fixture" if proxy else "user",
        approvalOrigin="recorded-unverified",
        humanOriginVerified=False,
        invalidReason=None,
    )

    def synthetic_plan(workspace):
        plan, reviews, detail = canonical_plan(workspace)
        plan["acceptance"].update(recorded)
        return plan, reviews, detail

    monkeypatch.setattr(status_api, "_plan", synthetic_plan)
    report = snapshot(workspace=tmp_path)
    cockpit = report["cockpit"]
    assert cockpit["vision"]["acceptance"] == recorded
    assert cockpit["vision"]["acceptance"]["humanOriginVerified"] is False
    assert cockpit["answers"]["needsYou"] == []
    assert cockpit["answers"]["reviewReadiness"] == report["plan"]["readiness"]
    assert all("accepted" not in item for item in cockpit["capabilities"])
    assert cockpit["areas"] == [] and "areaId" not in cockpit["capabilities"][0]
    assert any(item.get("at") and item["refs"] for item in cockpit["chronicle"])


def test_declared_work_counts_use_beads_and_current_scope_but_never_claim_credit(
    tmp_path, monkeypatch
):
    """Synthetic publication/run states test count units, not proof of publication/execution."""
    with PlanStore(tmp_path) as store:
        store.upsert_node(node("V1", "vision", []))
        for key in ("F1", "F2"):
            store.upsert_node(node(key, "feature", ["V1"]))
        store.upsert_node(node("S1", "story", ["F1"]))
        store.upsert_node(node("R1", "requirement", ["S1"]))
        store.upsert_node(node("R2", "requirement", ["F2"]))
        for key, parents in (("A", ["R1"]), ("B", ["R2"]), ("C", ["R1", "R2"])):
            store.upsert_node(
                node(
                    key,
                    "bead",
                    parents,
                    ownedFiles=[f"src/{key}.py"],
                    dependsOn=[],
                    lessons=[],
                    proofModule=f"test_{key}.py",
                )
            )
        revision = store.spine().plan_revision()
    canonical_plan = status_api._plan

    def synthetic_publication(workspace):
        plan, reviews, detail = canonical_plan(workspace)
        plan["publication"]["up_to_date"] = True
        plan["published_revision"] = revision
        return plan, reviews, detail

    monkeypatch.setattr(status_api, "_plan", synthetic_publication)
    run = tmp_path / "runs" / "recorded"
    (run / "evidence").mkdir(parents=True)
    record = dict(
        runId="recorded",
        status="source-test-only (mocked provider)",
        planRevision=revision,
        isMocked=True,
        didExecuteRealProvider=False,
        claims=[{"claimId": "unbound-claim"}],
        beads={
            "A": {"status": "requirement-validated"},
            "B": {"status": "running"},
            "C": {
                "status": "blocked: Alice proposes a fix",
                "nextOwner": "Alice",
                "fixStatus": "agreed",
            },
        },
    )
    write_record_json(run / "evidence" / "run.json", record)
    report = snapshot(workspace=tmp_path, runs_root=tmp_path / "runs")
    cockpit = report["cockpit"]
    assert cockpit["vision"]["items"] == dict(total=3, done=1, active=1, blocked=1, later=0)
    assert {item["id"]: item["items"]["total"] for item in cockpit["capabilities"]} == {
        "F1": 2,
        "F2": 2,
    }
    assert all(item["proof"]["requirementValidation"] is None for item in cockpit["capabilities"])
    assert not any(
        key in cockpit["answers"]["blocking"][0] for key in ("nextOwner", "nextAction", "fixStatus")
    )
    assert cockpit["crew"] == report["currentAgents"]
    cockpit["crew"][0]["provider"] = "local mutation"
    assert report["currentAgents"][0]["provider"] != "local mutation"
    record["planRevision"] = "stale"
    write_record_json(run / "evidence" / "run.json", record)
    stale = snapshot(workspace=tmp_path, runs_root=tmp_path / "runs")["cockpit"]
    assert stale["vision"]["items"]["done"] is None
    record["planRevision"] = revision
    record["beads"]["A"]["status"] = ["malformed-work-state"]
    write_record_json(run / "evidence" / "run.json", record)
    malformed = snapshot(workspace=tmp_path, runs_root=tmp_path / "runs")
    assert malformed["runs"][0]["beads"][0]["status"] == ["malformed-work-state"]
    assert malformed["cockpit"] is None  # Additive failure cannot destroy the original API.
