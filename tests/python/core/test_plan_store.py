"""Real spine/store/graph integration, phase refusals and transactional failure."""

import json
import queue
import shutil
import sqlite3
import threading
from copy import deepcopy
from pathlib import Path

import pytest
from tests.python.core import test_test_audit
from tests.python.core.planning_support import (
    publication_evidence,
    source_controller_roundtable,
    source_only_confirmation,
    source_only_expansion,
)

from codex_harness.core.graph import PlanError, load_plan
from codex_harness.core.plan_store import PHASES, PlanStore, PlanStoreError, open_plan_store
from codex_harness.core.spine import MARKDOWN_FILES, SpineError, compile_manifest, load_spine
from codex_harness.core.spine_render import render_markdown

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit" / "spine.json"
audit_fixture = test_test_audit.prospective


def test_concurrent_audit_admission_dispatches_one_real_engine(audit_fixture, monkeypatch):
    """Protect cross-connection single-flight; serial MCP calls miss the claim race."""
    from tests.python.core.test_mcp_audit import started

    from codex_harness.core import test_audit
    from codex_harness.core.planner_audits import AuditOwner

    initial, _ = started(audit_fixture)
    root = initial.workspace
    initial.close()
    barrier, release, entered = threading.Barrier(2), threading.Event(), threading.Event()
    observations, failures, dispatches = queue.Queue(), queue.Queue(), []
    real_admit = PlanStore.admit_audit
    engine = test_audit.audit_prospective_tests

    def admit(store, *args):
        barrier.wait(timeout=5)
        return real_admit(store, *args)

    def run(**args):
        dispatches.append(1)
        entered.set()
        assert release.wait(timeout=10)
        return engine(**args)

    monkeypatch.setattr(PlanStore, "admit_audit", admit)
    monkeypatch.setattr(test_audit, "audit_prospective_tests", run)

    def connection():
        try:
            with open_plan_store(root) as store:
                owner = AuditOwner()
                attempt = owner.start(store)
                observations.put(attempt)
                owner.dispatch_after_flush(store)
                owner.close(store)
        except BaseException as error:
            failures.put(error)

    threads = [threading.Thread(target=connection) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        first, second = observations.get(timeout=10), observations.get(timeout=10)
        assert first["attemptId"] == second["attemptId"]
        assert "unknown-interrupted" in {first["state"], second["state"]}
        assert entered.wait(timeout=5) and len(dispatches) == 1
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive()
    assert failures.empty(), list(failures.queue)
    with open_plan_store(root) as reopened:
        result = reopened.audit_attempt(first["attemptId"])
        assert result["state"] == "completed" and result["proofCreditGranted"]


def draft_node(identity, kind, fields, parents):
    return dict(
        id=identity, kind=kind, title=identity, text=identity, fields=fields, parents=parents
    )


def draft_inputs(store):
    """Real canonical setup; all availability/authority observations below are mocks."""
    for node in [
        draft_node("V1", "vision", {}, []),
        draft_node("F1", "feature", {}, ["V1"]),
        draft_node("R1", "requirement", {}, ["F1"]),
        draft_node("R2", "requirement", {}, ["F1"]),
        draft_node("X1", "acceptance", {"steps": ["Current exit facts"]}, ["V1"]),
        draft_node("X2", "acceptance", {"steps": ["Later exit facts"]}, ["V1"]),
        draft_node(
            "B1",
            "bead",
            {
                "ownedFiles": ["src/current.py"],
                "dependsOn": [],
                "lessons": [],
                "proofModule": "tests.current",
                "ownershipArea": "Current owner",
            },
            ["R1"],
        ),
    ]:
        store.upsert_node(node)
    spine = store.spine()
    wave_rows, obligations, details = [], [], []
    for index in (1, 2):
        wid, rid, sid = f"W{index}", f"R{index}", f"X{index}"
        wave_rows.append(
            dict(
                id=wid,
                outcome=f"Outcome {index}",
                demo=f"Demo {index}",
                requirementIds=[rid],
                featureIds=["F1"],
                storyIds=[],
                designIds=[],
                riskIds=[],
                exitScenarioIds=[sid],
                dependsOnWaves=[] if index == 1 else ["W1"],
                incomingContractIds=[],
                outgoingContractIds=[],
            )
        )
        for kind, source in (("requirement", rid), ("scenario", sid)):
            obligations.append(
                dict(
                    id=f"O-{source}",
                    kind=kind,
                    sourceRefs=[dict(id=source, revision=spine.revision(source))],
                    waveIds=[wid],
                    criterion=f"Prove {source}",
                    requiredEvidence=["product-integration"],
                    requiredForFullVision=True,
                )
            )
        fields = dict(
            schema="dot-wave-detail-v1",
            waveId=wid,
            detail="detailed" if index == 1 else "sketch",
            beadIds=["B1"] if index == 1 else [],
            sketches=[]
            if index == 1
            else [
                dict(
                    id="SK2",
                    title="Later meaningful refinement",
                    ownershipArea="Later owner",
                    requirementIds=["R2"],
                    expectedContractIds=[],
                    risks=["Needs producer"],
                    pendingProducerIds=["B1"],
                    detailAfterWaveId="W1",
                )
            ],
            observedInterfaces=[],
            verificationObligationIds=[f"O-{rid}", f"O-{sid}"],
        )
        details.append(draft_node(f"DETAIL{index}", "design", fields, ["MAP"]))
    obligations.append(
        dict(
            id="O-GLOBAL",
            kind="review",
            sourceRefs=[dict(id="V1", revision=spine.revision("V1"))],
            waveIds=["W1", "W2"],
            criterion="Independent full vision review",
            requiredEvidence=["independent-review"],
            requiredForFullVision=True,
        )
    )
    plan = dict(
        schema="dot-wave-map-v1",
        order=["W1", "W2"],
        waves=wave_rows,
        contracts=[],
        obligations=obligations,
        globalReviewObligationId="O-GLOBAL",
    )
    return dict(
        map_node=draft_node("MAP", "decision", plan, ["V1"]),
        detail_nodes=details,
        expected_spine_revision=spine.plan_revision(),
        historical_measurements=[
            dict(
                sourceRef="mock:reported-history",
                ready=0,
                sampled=3,
                blockers=7,
                evidenceClass="reported-history",
                publicationRevision=None,
                observedAt=None,
            )
        ],
    )


@pytest.mark.parametrize("by,available", [("proxy:synthetic-reviewer", True), ("planner", False)])
def test_wave_lock_requires_new_plan_confirmation_and_retains_origin(tmp_path, by, available):
    from tests.python.core.test_wave_refine import cli, plan_confirmation, setup_wave

    workspace = setup_wave(tmp_path / "confirmation")
    with open_plan_store(workspace) as store:
        with pytest.raises(ValueError, match="actual displayed-plan confirmation"):
            store.wave_product_lock()
    projected = plan_confirmation(workspace, by=by)
    with open_plan_store(workspace) as store:
        if not available:
            assert projected is None
            with pytest.raises(ValueError, match="actual displayed-plan confirmation"):
                store.wave_product_lock()
        else:
            lock = store.wave_product_lock()
            assert lock == projected
            assert lock["confirmedBy"] == by and lock["isProxy"] is True
            assert lock["confirmationOrigin"] == "proxy"
            assert lock["statement"] == "Synthetic displayed two-wave plan approval"
            locked_vision = store._meta()["locked_vision_json"]
    if available:
        waves_projection = cli(workspace, "waves")["data"]
        assert waves_projection["waveProductLock"] == lock
        with open_plan_store(workspace) as store:
            assert store._meta()["locked_vision_json"] == locked_vision


def draft_intake():
    return draft_node(
        "INTAKE",
        "decision",
        dict(
            requestKind="feature",
            sizeEstimate="S",
            delta=dict(
                summary="Current slice",
                nodeIds=["R1"],
                outcome="Retain the full vision",
                successCriteria=["Readable full outcomes"],
                ownershipAreas=["Store"],
                interfaceIds=[],
            ),
            ambiguity=dict(level="low", reasons=[]),
            consequence=dict(level="ordinary", reversibility="Editable", risks=[]),
            dependencyBreadth=dict(producerIds=["A", "B"], crossWaveContracts=[]),
            constraints=dict(
                runtime=["Python"], security=[], spend="No calls", frictionBudget="Small"
            ),
            rationale="Two producers",
            assumptions=[],
            expectedSupervision="Guided",
            nextGate="Review",
            reviewerDecisionId="CHOICE",
            confirmation=None,
            supersedes=None,
            adaptation=None,
        ),
        ["V1"],
    )


def mock_catalog():
    return dict(
        schema="dot-reviewer-catalog-v1",
        id="RC-00000000-0000-0000-0000-000000000001",
        observedAt="2026-10-15T10:00:00+00:00",
        providers=[
            dict(
                provider="codex",
                executable="C:/mock/codex.exe",
                executableDigest="a" * 64,
                version="mock-1",
                installed=True,
                authentication="available",
                discovery="observed",
                source=dict(
                    kind="supported-list", reference="mock:supported-list", digest="b" * 64
                ),
                choices=[
                    dict(
                        model="gpt-6.1-sol",
                        effort=dict(kind="named", value="high"),
                        availability="available",
                        reviewPathSupported=True,
                        reason="Mock only",
                    )
                ],
                reason="Injected mock; no discovery or provider execution",
            )
        ],
    )


def mock_choice(catalog, origin=None):
    choice = catalog["providers"][0]["choices"][0]
    authority = (
        None
        if origin is None
        else dict(
            approvalId="mock:approval",
            eventId=1,
            origin=origin,
            statement="Select exact tuple",
            sourceRef="mock:direct-or-delegated",
            authorizedScopeDigest="c" * 64,
        )
    )
    return draft_node(
        "CHOICE",
        "decision",
        dict(
            schema="dot-reviewer-selection-v1",
            catalogId=catalog["id"],
            catalogDigest=catalog["digest"],
            reviewers=[
                dict(
                    name="primary",
                    provider="codex",
                    **{key: choice[key] for key in ("choiceId", "model", "effort")},
                )
            ],
            stages=["plan"],
            assurance=dict(
                independentRequired=True,
                minIndependentReviewers=1,
                crossProviderRequired=True,
                basisRefs=["mock:policy"],
            ),
            confirmation=authority,
            recommendation=dict(
                choiceIds=[choice["choiceId"]],
                reason="Exact mocked choice",
                catchRateEvidenceRefs=[],
            ),
        ),
        ["V1"],
    )


def test_ordinary_reviewer_confirmation_records_exact_scope_without_native_credit(tmp_path):
    """Simulated catalog and tester-as-user action; no discovery or human attestation."""
    from codex_harness.core.intake import canonical_digest

    statement = "Tester selects exactly these reviewers.\nKeep the complete statement.  "
    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        catalog = store.record_reviewer_catalog(mock_catalog(), store.spine().plan_revision())[
            "catalog"
        ]
        node = mock_choice(catalog)
        selected = store.record_reviewer_selection(node, store.spine().plan_revision())
        preimage = selected["spineRevision"]
        before_holds = selected["unmetRequirements"]
        assert any("reviewer-confirmation-required" in hold for hold in before_holds)
        result = store.confirm_reviewer_selection(
            node["id"], preimage, approve=True, statement=statement, conditions=""
        )
        authority = result["selection"]["confirmation"]
        records = store.connection.execute(
            "SELECT id, payload_json FROM events WHERE kind='reviewer-selection-confirmed'"
        ).fetchall()
        assert len(records) == 1
        event_id, payload = records[0]
        record = json.loads(payload)
        expected_scope = canonical_digest(
            {
                "node": node,
                "authorSessions": {"implementationSessionIds": [], "reviewerSessionIds": {}},
                "catalog": {"id": catalog["id"], "digest": catalog["digest"]},
            }
        )
        assert record["scopeDigest"] == authority["authorizedScopeDigest"] == expected_scope
        assert authority["eventId"] == event_id
        assert authority["approvalId"] == record["recordId"]
        assert authority["sourceRef"] == f"reviewer-confirmation:event:{event_id}"
        assert record["preimageRevision"] == preimage
        assert record["statement"] == authority["statement"] == statement
        assert record["human_identity_verified"] is False
        assert record["provenance"] == "caller-declared-user-action"
        assert record["origin"] == authority["origin"] == "user"
        assert result["spineRevision"] != preimage
        assert result["approvalGranted"] is False
        assert result["unmetRequirements"] == [
            hold for hold in before_holds if "reviewer-confirmation-required" not in hold
        ]
        assert any("independence-unknown" in hold for hold in result["unmetRequirements"])
        assert any("cross-provider" in hold for hold in result["unmetRequirements"])
        assert store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
        stored = store.spine().node(node["id"])
        assert stored == {**node, "fields": {**node["fields"], "confirmation": authority}}
        expected = store.outcome_waves()["reviewerSelection"]
    with open_plan_store(tmp_path) as reopened:
        assert reopened.outcome_waves()["reviewerSelection"] == expected
        assert reopened.spine().node(node["id"])["fields"]["confirmation"] == authority


def test_draft_map_reopen_stale_preimage_and_returned_copy(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
        result = store.record_wave_map(**inputs)
        expected = store.outcome_waves()
        assert result["accounting"]["fullVision"]["totalObligations"] == 5
        assert result["accounting"]["fullVision"]["completedRealObligations"] == 0
        assert result["accounting"]["fullVision"]["outstandingObligations"] == 5
        assert result["accounting"]["readiness"]["unknownBeadCount"] == 1
        assert expected["lockState"] == "unconfirmed"
        count = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        inputs["expected_spine_revision"] = result["spineRevision"]
        assert store.record_wave_map(**inputs) == result
        assert store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == count
        with open_plan_store(tmp_path) as competitor:
            old_revision = store.spine().plan_revision()
            changed = competitor.spine().node("B1")
            changed["title"] = "Current detailed bead"
            competitor.upsert_node(changed)
            with pytest.raises(SpineError, match="Refresh status"):
                store.record_wave_map(**{**inputs, "expected_spine_revision": old_revision})
        # Local projection mutations never change durable truth.
        expected["waveMap"]["order"].clear()
        assert store.outcome_waves()["waveMap"]["order"] == ["W1", "W2"]
        before_close = store.outcome_waves()
    with open_plan_store(tmp_path) as reopened:
        assert reopened.outcome_waves() == before_close
        # Changed intent stales source binding; it cannot inherit achievement.
        requirement = reopened.spine().node("R2")
        requirement["text"] = "Different intent"
        reopened.upsert_node(requirement)
        assert reopened.outcome_waves()["state"] == "stale"
        assert "accounting" not in reopened.outcome_waves()


def test_review_decision_map_owns_detail_designs(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
        result = store.record_wave_map(**inputs)
        projection = store.outcome_waves()
        assert projection["mapNode"]["kind"] == "decision"
        assert all(node["parents"] == ["MAP"] for node in projection["detailNodes"])
        assert {"O-GLOBAL", "O-R1", "O-R2"} <= {
            row["id"] for row in store._obligation_register(store.spine())
        }
        assert result["accounting"]["fullVision"]["completedRealObligations"] == 0
    with open_plan_store(tmp_path) as store:
        assert store.outcome_waves() == projection


def test_review_mistaken_map_shape_and_wrong_detail_owner_refuse(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
        events = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        mistaken = deepcopy(inputs)
        mistaken["map_node"].update(kind="design", parents=["R1"])
        with pytest.raises(SpineError) as error:
            store.record_wave_map(**mistaken)
        assert error.value.code == "plan-draft-kind-invalid"
        wrong_owner = deepcopy(inputs)
        wrong_owner["detail_nodes"][0]["parents"] = ["R1"]
        with pytest.raises(SpineError) as error:
            store.record_wave_map(**wrong_owner)
        assert error.value.code == "plan-wave-owner-invalid"
        assert store.spine().plan_revision() == inputs["expected_spine_revision"]
        assert store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == events


def test_review_history_identity_is_immutable_and_new_sources_append(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
        result = store.record_wave_map(**inputs)
        inputs["expected_spine_revision"] = result["spineRevision"]
        original = store.outcome_waves()
        events = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        rewritten = deepcopy(inputs)
        rewritten["historical_measurements"][0].update(ready=1, sampled=1, blockers=0)
        with pytest.raises(SpineError) as error:
            store.record_wave_map(**rewritten)
        assert error.value.code == "plan-history-observation-conflict"
        assert store.outcome_waves() == original
        assert store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == events
        store.record_wave_map(**inputs)
        assert store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == events
        additional = deepcopy(inputs["historical_measurements"][0])
        additional.update(sourceRef="mock:new-history", ready=1, sampled=1, blockers=0)
        inputs["historical_measurements"] = [additional]
        store.record_wave_map(**inputs)
        history = store.outcome_waves()["accounting"]["historicalMeasurements"]
        assert [row["sourceRef"] for row in history] == [
            "mock:reported-history",
            "mock:new-history",
        ]
        assert (history[0]["ready"], history[0]["sampled"], history[0]["blockers"]) == (0, 3, 7)
        del inputs["historical_measurements"]
        store.record_wave_map(**inputs)
    with open_plan_store(tmp_path) as store:
        projection = store.outcome_waves()
        assert projection["accounting"]["historicalMeasurements"] == history
        assert projection["accounting"]["fullVision"]["completedRealObligations"] == 0


def test_review_malformed_fields_refuses_with_stable_error_and_no_write(tmp_path):
    with open_plan_store(tmp_path) as store:
        draft_inputs(store)
        node = draft_intake()
        node["fields"] = []
        before = store.spine().plan_revision()
        events = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        with pytest.raises(SpineError) as error:
            store.record_intake(node, before)
        assert error.value.code == "plan-draft-fields-invalid"
        assert store.spine().plan_revision() == before
        assert store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == events


def test_draft_map_multinode_failure_rolls_back_events_and_nodes(tmp_path, monkeypatch):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
        store.record_wave_map(**inputs)
        before = store.outcome_waves()
        events = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        inputs["expected_spine_revision"] = store.spine().plan_revision()
        inputs["map_node"]["title"] = "Changed map"
        inputs["detail_nodes"][0]["title"] = "Changed detail"
        write = store._write_node

        def fail_second(spine, node_id):
            if node_id == "DETAIL1":
                raise RuntimeError("Injected persistence failure")
            write(spine, node_id)

        monkeypatch.setattr(store, "_write_node", fail_second)
        with pytest.raises(RuntimeError, match="Injected persistence failure"):
            store.record_wave_map(**inputs)
        assert store.outcome_waves() == before
        assert store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == events


@pytest.mark.parametrize("origin", [None, "user", "standing-delegation"])
def test_draft_intake_selection_provenance_and_existing_gates(tmp_path, origin):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
        result = store.record_intake(draft_intake(), store.spine().plan_revision())
        assert result["intake"]["recipe"] == "outcome-waves"
        assert result["intake"]["baseline"] == dict(publishedRevision=None, spineRevision=None)
        result = store.record_reviewer_catalog(mock_catalog(), result["spineRevision"])
        catalog = result["catalog"]
        # Same observation is idempotent; same identity with different bytes refuses.
        count = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        store.record_reviewer_catalog(mock_catalog(), result["spineRevision"])
        assert store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == count
        changed = mock_catalog()
        changed["observedAt"] = "2026-10-15T11:00:00+00:00"
        with pytest.raises(SpineError, match=catalog["id"]):
            store.record_reviewer_catalog(changed, result["spineRevision"])
        node = mock_choice(catalog, origin)
        choice = store.record_reviewer_selection(node, result["spineRevision"])
        assert choice["independence"]["qualifyingReviewerNames"] == []
        assert any("independence-unknown" in item for item in choice["unmetRequirements"])
        assert any("cross-provider" in item for item in choice["unmetRequirements"])
        assert choice["approvalGranted"] is False
        inputs["expected_spine_revision"] = choice["spineRevision"]
        store.record_wave_map(**inputs)
        assert store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
        with pytest.raises(SpineError):
            store.advance("expand", by="planner")
        with pytest.raises(SpineError):
            store.publish(by="planner", export_dir=tmp_path / "plan")
        with pytest.raises(ValueError):
            store.accept(by="user", statement="A record alone is insufficient")
        expected = store.outcome_waves()
    with open_plan_store(tmp_path) as store:
        assert store.outcome_waves() == expected
        assert (
            store.outcome_waves()["reviewerSelection"]["node"]["fields"]["confirmation"]
            == node["fields"]["confirmation"]
        )


def test_unresolved_intake_baseline_and_required_map_obligation_loss(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
        with pytest.raises(SpineError):
            store.record_intake(
                draft_intake(),
                store.spine().plan_revision(),
                baseline={"publishedRevision": "a" * 64, "spineRevision": "b" * 64},
            )
        assert "INTAKE" not in store.spine().ids()
        result = store.record_wave_map(**inputs)
        # Even a redundant required review cannot be silently dropped later.
        review = {**inputs["map_node"]["fields"]["obligations"][-1], "id": "O-EXTRA"}
        inputs["map_node"]["fields"]["obligations"].append(review)
        inputs["expected_spine_revision"] = result["spineRevision"]
        result = store.record_wave_map(**inputs)
        inputs["map_node"]["fields"]["obligations"].pop()
        inputs["expected_spine_revision"] = result["spineRevision"]
        with pytest.raises(SpineError, match="obligations must survive"):
            store.record_wave_map(**inputs)


@pytest.fixture
def store(tmp_path):
    with open_plan_store(tmp_path, "initial") as result:
        result.import_spine(FIXTURE)
        yield result


def _link_r6(store):
    node = store.spine().node("A")
    node["parents"].append("R6")
    store.upsert_node(node)


def _to(store, phase):
    if store.phase() == "align" and "expansion" not in store.spine().node("V1")["fields"]:
        source_only_expansion(store)
    if phase == "publish":
        publication_evidence(store)
    while PHASES.index(store.phase()) < PHASES.index(phase):
        current = store.phase()
        if current in ("align", "vision-lock"):
            source_only_confirmation(
                store, current, statement="I confirm the current understanding"
            )
        store.advance(PHASES[PHASES.index(current) + 1], by="planner")


def _assert_code(code, callback):
    with pytest.raises((SpineError, PlanError)) as caught:
        callback()
    assert caught.value.code == code


def test_intake_resolves_real_publication_pair_and_full_register(store, tmp_path):
    _link_r6(store)
    node = draft_intake()
    node["parents"] = ["V1"]
    store.record_intake(node, store.spine().plan_revision())
    _to(store, "publish")
    baseline_spine = store.spine().plan_revision()
    published = store.publish(by="planner", export_dir=tmp_path / "plan")
    result = store.record_intake(
        node,
        baseline_spine,
        baseline={
            "publishedRevision": published,
            "spineRevision": baseline_spine,
        },
    )
    assert result["intake"]["baseline"] == {
        "publishedRevision": published,
        "spineRevision": baseline_spine,
    }
    assert result["intake"]["delta"]["nodeIds"] == ["R1"]
    assert {row["id"] for row in store._obligation_register(store.spine())} >= set(
        store.spine().ids("requirement")
    )
    before = store.spine().plan_revision()
    with pytest.raises(SpineError, match="Published spine does not match"):
        store.record_intake(
            node,
            before,
            baseline={
                "publishedRevision": published,
                "spineRevision": "0" * 64,
            },
        )
    assert store.spine().plan_revision() == before


def test_missing_workspace_and_initial_status(tmp_path):
    missing = tmp_path / "missing"
    _assert_code("plan-store-workspace-missing", lambda: open_plan_store(missing))
    assert not missing.exists()
    _assert_code("plan-store-workspace-missing", lambda: open_plan_store(FIXTURE))
    with open_plan_store(tmp_path, "new plan") as store:
        assert store.path == tmp_path / ".codex-harness" / "plan.sqlite3"
        assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        status = store.status()
        assert status["name"] == "new plan"
        assert status["phase"] == "align"
        assert status["phase_index"] == 1
        assert status["pending_gate"] == "approval:align"
        assert status["coverage_gaps"] == []
        assert status["vision_locked_revision"] is None
        assert status["published_revision"] is None
        assert status["manifest_revision"] is None
        assert status["publication"]["up_to_date"] is False
        assert status["open_clarifications"] == status["open_assumptions"] == 0
        _assert_code("spine-vision-count", store.spine)
        store.upsert_node(load_spine(FIXTURE).node("V1"))
        assert store.spine().ids() == ["V1"]


def test_import_export_revision_and_reopen_without_commit(store, tmp_path):
    original = load_spine(FIXTURE)
    revision = store.spine().plan_revision()
    exported = tmp_path / "spine.json"
    store.export_spine(exported)
    assert load_spine(exported).to_dict() == original.to_dict()
    assert load_spine(exported).plan_revision() == revision
    with open_plan_store(tmp_path, "does not rename") as reopened:
        assert reopened.spine().plan_revision() == revision
        assert reopened.status()["name"] == original.name
        assert reopened.spine().ids() == original.ids()


@pytest.mark.parametrize(
    "target,code",
    [
        ("bogus", "plan-phase-invalid"),
        ("features", "plan-phase-order"),
        ("align", "plan-phase-order"),
        ("expand", "plan-approval-required"),
    ],
)
def test_phase_order_and_align_refusals(store, target, code):
    before = store.status()
    _assert_code(code, lambda: store.advance(target, by="planner"))
    assert store.status() == before


def test_inline_approval_refuses_and_native_confirmation_survives_unchanged_replanning(store):
    _assert_code(
        "plan-confirmation-origin-unavailable",
        lambda: store.advance("expand", by="operator", statement="That is what I want"),
    )
    source_only_confirmation(store, "align", statement="That is what I want")
    store.advance("expand", by="planner")
    assert store.phase() == "expand"
    store.advance("align", by="planner", statement="Revisit understanding")
    assert store.phase() == "align"
    store.advance("expand", by="planner")
    assert store.phase() == "expand"


def test_demo_cross_gate_and_new_revision_quoted_words_cannot_approve(store):
    source_only_expansion(store)
    source_only_confirmation(store, "align", statement="Yes, that matches what I want.")
    store.advance("expand", by="planner")
    store.advance("vision-lock", by="planner")
    _assert_code(
        "plan-confirmation-origin-unavailable",
        lambda: store.approve("vision-lock", by="user", statement="Yes, that matches what I want."),
    )
    assert not store.status()["approval"]["granted"]
    source_only_confirmation(store, "vision-lock", statement="Fresh scoped approval.")
    node = store.spine().node("V1")
    node["text"] += " Revised at 09:22."
    store.upsert_node(node)
    _assert_code(
        "plan-confirmation-origin-unavailable",
        lambda: store.advance("features", by="user", statement="Fresh scoped approval."),
    )
    assert store.phase() == "vision-lock" and not store.status()["approval"]["granted"]


def test_cached_legacy_lock_remains_readable_but_cannot_advance_or_publish(store):
    with store._transaction():
        legacy = store._approve("vision-lock", "user", "Original legacy words with conditions.")
        store.connection.execute(
            "UPDATE meta SET phase='features',vision_locked_revision=?", (store._revision(),)
        )
    before = dict(
        store.connection.execute("SELECT * FROM approvals WHERE id=?", (legacy,)).fetchone()
    )
    status = store.status()
    assert not status["approval"]["granted"]
    assert status["pending_gate"] == "approval:vision-lock"
    _assert_code(
        "plan-confirmation-origin-unavailable", lambda: store.advance("requirements", by="planner")
    )
    _assert_code("plan-confirmation-origin-unavailable", store._check_publish)
    assert (
        dict(store.connection.execute("SELECT * FROM approvals WHERE id=?", (legacy,)).fetchone())
        == before
    )
    source_only_confirmation(
        store, "vision-lock", statement="Fresh native renewal of displayed scope."
    )
    store.advance("requirements", by="planner")


def test_native_lock_survives_subordinate_and_verification_metadata_changes(store):
    _to(store, "features")
    original = store.status()["approval"]["approval_id"]
    original_revision = store.status()["vision_locked_revision"]
    node = store.spine().node("D3")
    node["text"] += " A subordinate clarification."
    store.upsert_node(node)
    vision = store.spine().node("V1")
    vision["fields"]["validation"].append("-v")
    store.upsert_node(vision)
    assert store.status()["approval"]["approval_id"] == original
    store.advance("vision-lock", by="planner")
    store.advance("features", by="planner")
    assert store.status()["vision_locked_revision"] == original_revision
    assert store.status()["approval"]["approval_id"] == original
    store.advance("requirements", by="planner")


@pytest.mark.parametrize("gate,target", [("align", "expand"), ("vision-lock", "features")])
def test_revision_bound_approvals(store, gate, target):
    _to(store, gate)
    _assert_code("plan-approval-required", lambda: store.advance(target, by="planner"))
    source_only_confirmation(store, gate, statement="Confirmed")
    before = store.spine().plan_revision()
    node = store.spine().node("D3")
    node["text"] += " Updated."
    store.upsert_node(node)
    assert store.spine().plan_revision() != before
    _assert_code("plan-approval-stale", lambda: store.advance(target, by="planner"))
    source_only_confirmation(store, gate, statement="Confirmed revised plan")
    store.advance(target, by="planner")
    if gate == "vision-lock":
        assert store.status()["vision_locked_revision"] == store.spine().plan_revision()


@pytest.mark.parametrize(
    "gate,by,statement,code",
    [
        ("unknown", "operator", "yes", "plan-gate-invalid"),
        ("align", "", "yes", "plan-input-invalid"),
        ("align", "operator", " ", "plan-input-invalid"),
    ],
)
def test_approval_input_refusals(store, gate, by, statement, code):
    _assert_code(code, lambda: store.approve(gate, by=by, statement=statement))
    assert store.connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_requirements_require_test_coverage(store):
    _to(store, "requirements")
    node = {
        "id": "R7",
        "kind": "requirement",
        "title": "Another requirement",
        "text": "Prove me",
        "fields": {},
        "parents": ["F1"],
    }
    store.upsert_node(node)
    _assert_code("plan-requirement-without-test", lambda: store.advance("tech-stack", by="planner"))
    proof = store.spine().node("test_money")
    proof["parents"].append("R7")
    store.upsert_node(proof)
    store.advance("tech-stack", by="planner")


def test_plan_requires_beads_then_real_consumer_ownership_validation(store):
    _to(store, "plan")
    _assert_code("plan-requirement-without-bead", lambda: store.advance("roundtable", by="planner"))
    _link_r6(store)
    node = store.spine().node("B")
    node["fields"]["dependsOn"] = []
    node["fields"]["ownedFiles"] = ["tipsplit/money.py"]
    store.upsert_node(node)
    _assert_code("plan-ownership-overlap", lambda: store.advance("roundtable", by="planner"))
    node["fields"]["ownedFiles"] = ["PLAN.md"]
    store.upsert_node(node)
    _assert_code("plan-owned-file-protected", lambda: store.advance("roundtable", by="planner"))
    node["fields"]["ownedFiles"] = ["../outside.py"]
    store.upsert_node(node)
    _assert_code("plan-schema-invalid", lambda: store.advance("roundtable", by="planner"))
    node["fields"]["ownedFiles"] = ["tipsplit/cli.py"]
    store.upsert_node(node)
    store.advance("roundtable", by="planner")


def test_publish_gate_questions_and_assumption_register(store):
    _link_r6(store)
    _to(store, "roundtable")
    question = store.ask("Which rounding?", ["D3"])
    waived = store.ask("Do we need history?")
    assumption = store.add_assumption("Dollars only", ["V1"])
    confirmed = store.add_assumption("CLI")
    rejected = store.add_assumption("Save history")
    store.confirm_assumption(confirmed, rationale="Test decision")
    store.reject_assumption(rejected, rationale="Test decision")
    assert store.status()["open_clarifications"] == 2
    assert store.status()["open_assumptions"] == 1
    _assert_code("plan-clarifications-open", lambda: store.advance("publish", by="planner"))
    store.answer(question, "Half up")
    store.waive(waived, "History is out of scope")
    publication_evidence(store)
    store.advance("publish", by="planner")
    assert store.status()["open_assumptions"] == 1
    row = store.connection.execute("SELECT * FROM clarifications WHERE id=?", (waived,)).fetchone()
    assert row["answer"] == "History is out of scope"
    assert row["answered_at"] is not None
    assert row["status"] == "waived"
    assert assumption in {row["id"] for row in store._open("assumptions")}


@pytest.mark.parametrize("edit", ["protected", "unchanged", "semantic"])
def test_saved_manifest_impact_preserves_protected_reason_and_existing_sets(tmp_path, capsys, edit):
    """Real public reporting parity; no worker or proof-reuse claim is made."""
    from tests.python.core.test_mcp_planner import rpc

    from codex_harness.core import plan_cli
    from codex_harness.core.mcp_planner import PlannerServer

    producer = tmp_path / "producer"
    template = producer / "template"
    shutil.copytree(FIXTURE.parent / "template", template)
    with open_plan_store(producer) as local:
        local.import_spine(FIXTURE)
        unrelated = local.spine().node("A")
        unrelated.update(id="C", title="Unrelated C", parents=["R6"])
        unrelated["fields"].update(
            ownedFiles=["tipsplit/unrelated.py"],
            proofModule="test_cli.py",
            dependsOn=[],
            lessons=[],
        )
        local.upsert_node(unrelated)
        baseline = local.compile_template(template)
        baseline_spine = producer / "baseline-spine.json"
        local.export_spine(baseline_spine)
    saved = producer / "baseline-manifest.json"
    saved.write_text(json.dumps(baseline), encoding="utf-8")
    foreign = tmp_path / "foreign"
    shutil.copytree(template, foreign / "template")
    with open_plan_store(foreign) as external:
        external.import_spine(baseline_spine)
        assert not external.connection.execute(
            "SELECT 1 FROM events WHERE kind='manifest-compiled'"
        ).fetchall()

    results, traces = {}, []
    for origin, workspace in [("local", producer), ("foreign", foreign)]:
        with open_plan_store(workspace) as current:
            if edit == "protected":
                proof = workspace / "template/tests/test_money.py"
                proof.write_bytes(proof.read_bytes() + b"\n# Changed protected proof fixture\n")
            elif edit == "semantic":
                bead = current.spine().node("A")
                bead["title"] += " revised source contract"
                current.upsert_node(bead)
            protected_changed = (
                compile_manifest(current.spine(), workspace / "template")[
                    "requirementFilesRevision"
                ]
                != baseline["requirementFilesRevision"]
            )
            assert protected_changed is (edit == "protected")
            before = list(current.connection.iterdump())
            manifest_bytes = saved.read_bytes()
            assert (
                plan_cli.main(
                    ["--workspace", str(workspace), "--json", "impact", "--since", str(saved)]
                )
                == 0
            )
            cli = json.loads(capsys.readouterr().out)
            server = PlannerServer(workspace)
            try:
                mcp = rpc(
                    server,
                    "tools/call",
                    {"name": "harness_impact", "arguments": {"since": str(saved)}},
                )["result"]
            finally:
                server.close()
            assert not cli["isError"] and not mcp["isError"]
            assert cli["data"] == mcp["structuredContent"] == current.impact_since(str(saved))
            assert list(current.connection.iterdump()) == before
            assert saved.read_bytes() == manifest_bytes
            result = cli["data"]
            if edit == "unchanged":
                expected = dict(
                    changed=[],
                    affected=[],
                    affectedBeads=[],
                    affectedTests=[],
                    untouchedBeads=["A", "B", "C"],
                    staleDocs=[],
                )
            elif edit == "protected" and origin == "local":
                expected = dict(
                    changed=[],
                    affected=["A", "B", "C", "test_cli", "test_end_to_end", "test_money"],
                    affectedBeads=["A", "B", "C"],
                    affectedTests=["test_cli", "test_end_to_end", "test_money"],
                    untouchedBeads=[],
                    staleDocs=[],
                )
            elif edit == "protected":
                expected = dict(
                    changed=["A", "B", "C"],
                    affected=["A", "B", "C"],
                    affectedBeads=["A", "B", "C"],
                    affectedTests=["test_cli.py", "test_money.py"],
                    untouchedBeads=[],
                    staleDocs=["PLAN.md"],
                )
            elif origin == "local":
                expected = dict(
                    changed=["A"],
                    affected=["A", "B", "test_cli", "test_money"],
                    affectedBeads=["A", "B"],
                    affectedTests=["test_cli", "test_money"],
                    untouchedBeads=["C"],
                    staleDocs=["PLAN.md", "TRACE.md"],
                )
            else:
                expected = dict(
                    changed=["A"],
                    affected=["A", "B"],
                    affectedBeads=["A", "B"],
                    affectedTests=["test_cli.py", "test_money.py"],
                    untouchedBeads=["C"],
                    staleDocs=["PLAN.md"],
                )
            assert {key: result[key] for key in expected} == expected
            results[origin] = result
            traces.append(dict(origin=origin, edit=edit, CLI=cli, MCP=mcp, expectedSets=expected))
    print(json.dumps({"publicImpactTraces": traces}))
    if edit == "protected":
        # Existing local reason is the oracle; all preexisting conservative sets above hold.
        assert results["local"]["protectedFilesChanged"] is True
        assert results["foreign"].get("protectedFilesChanged") is True
    else:
        assert all(not result.get("protectedFilesChanged", False) for result in results.values())


def test_happy_path_publish_frozen_artifacts_idempotency_and_impact(store, tmp_path):
    _link_r6(store)
    assumption = store.add_assumption("Dollars only", ["V1"])
    _to(store, "publish")
    _assert_code("plan-publication-required", lambda: store.advance("execute", by="planner"))
    outdir = tmp_path / "exports"
    revision = store.publish(by="planner", export_dir=outdir)
    status = store.status()
    assert (
        status["pending_gate"] == "successful-review-required"
    )  # Source-only reviews earn no credit.
    assert status["phase_gate"] == "current-revision-published"  # Retained phase-label alias.
    assert status["publication"] == {
        "published_revision": revision,
        "current_revision": revision,
        "up_to_date": True,
    }
    question = store.ask("Clarify the published scope")
    assert store.status()["pending_gate"] is not None
    store.answer(question, "No scope change")
    assert store.status()["pending_gate"] == "successful-review-required"  # Source-only reviews.
    snapshot = json.loads((outdir / "revisions" / f"{revision}.json").read_text(encoding="utf-8"))
    assert snapshot["open_assumptions"][0]["id"] == assumption
    assert (
        load_spine(outdir / "revisions" / f"{revision}.json").to_dict() == store.spine().to_dict()
    )
    assert sorted(path.name for path in (outdir / revision).iterdir()) == [
        *MARKDOWN_FILES,
        "manifest.json",
    ]
    for name, content in render_markdown(store.spine()).items():
        assert (outdir / revision / name).read_text(encoding="utf-8") == content
    manifest_path = outdir / revision / "manifest.json"
    assert load_plan(manifest_path).beads.keys() == {"A", "B"}
    assert store.published_manifest(revision) == json.loads(
        manifest_path.read_text(encoding="utf-8")
    )
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in outdir.rglob("*")
        if path.is_file()
    }
    events = store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert store.publish(by="another planner", export_dir=outdir) == revision
    assert store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events
    assert store.connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 1
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before}
    store.advance("execute", by="planner")
    store.advance("accept", by="operator")
    assert store.status()["phase_index"] == 11
    assert store.status()["published_revision"] == revision
    node = store.spine().node("D3")
    node["text"] += " New rounding."
    store.upsert_node(node)
    assert store.impact_since(revision)["affectedBeads"] == ["A", "B"]
    assert store.published_manifest(revision)["planRevision"] == revision
    with open_plan_store(tmp_path) as reopened:
        assert reopened.impact_since(revision)["affectedBeads"] == ["A", "B"]
        assert reopened.phase() == "accept"


def test_invalid_node_removal_and_atomic_event_failure(store, monkeypatch):
    before = store.spine().to_dict()
    events = store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    node = store.spine().node("F1")
    # Use an unrelated story to avoid the cycle error taking precedence.
    node["parents"] = ["S2"]
    _assert_code("spine-parent-kind-not-allowed", lambda: store.upsert_node(node))
    _assert_code("plan-node-has-children", lambda: store.remove_node("D3"))
    _assert_code("plan-node-missing", lambda: store.remove_node("missing"))

    def fail_event(*args):
        raise RuntimeError("Forced failure after SQL insert")

    monkeypatch.setattr(store, "_event", fail_event)
    node = {
        "id": "D6",
        "kind": "decision",
        "title": "New decision",
        "text": "Choice",
        "parents": ["V1"],
        "fields": {},
    }
    with pytest.raises(RuntimeError, match="Forced failure"):
        store.upsert_node(node)
    assert store.spine().to_dict() == before
    assert store.connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == len(
        before["nodes"]
    )
    assert store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events
    assert not store.connection.in_transaction


def test_remove_leaf_and_referenced_dependencies(store):
    node = {
        "id": "D6",
        "kind": "decision",
        "title": "Unused",
        "text": "Unused",
        "parents": ["V1"],
        "fields": {},
    }
    store.upsert_node(node)
    store.remove_node("D6")
    assert "D6" not in store.spine().ids()
    # A has ordinary children and is refused before SQL deletion.
    _assert_code("plan-node-has-children", lambda: store.remove_node("A"))


def test_import_failure_rolls_back_entire_replacement(store, monkeypatch):
    before = store.spine().to_dict()
    write = store._write_node

    def fail_second(spine, node_id):
        write(spine, node_id)
        if node_id == spine.ids()[1]:
            raise RuntimeError("Failed second insert")

    monkeypatch.setattr(store, "_write_node", fail_second)
    with pytest.raises(RuntimeError, match="second insert"):
        store.import_spine(FIXTURE)
    assert store.spine().to_dict() == before


def test_events_audit_every_command_and_are_append_only(store):
    store.upsert_node(store.spine().node("D3"))
    question = store.ask("Question?")
    store.answer(question, "Answer")
    question = store.ask("Other?")
    store.waive(question, "Not needed")
    confirmed = store.add_assumption("One")
    store.confirm_assumption(confirmed, rationale="Test decision")
    rejected = store.add_assumption("Two")
    store.reject_assumption(rejected, rationale="Test decision")
    source_only_confirmation(store, "align", statement="Agreed")
    store.advance("expand", by="planner")
    kinds = [row[0] for row in store.connection.execute("SELECT kind FROM events ORDER BY id")]
    assert kinds == [
        "opened",
        "spine-imported",
        "node-upserted",
        "clarification-asked",
        "clarification-answered",
        "clarification-asked",
        "clarification-waived",
        "assumption-added",
        "assumption-confirmed",
        "assumption-added",
        "assumption-rejected",
        "confirmation-requested",
        "approved",
        "confirmation-responded",
        "phase-advanced",
    ]
    for sql in ("DELETE FROM events", "UPDATE events SET kind='fake'"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.connection.execute(sql)


def test_publish_rechecks_mutated_plan_and_export_conflicts(store, tmp_path):
    _assert_code(
        "plan-publish-phase-required", lambda: store.publish(by="planner", export_dir=tmp_path)
    )
    _link_r6(store)
    _to(store, "publish")
    question = store.ask("Late question")
    _assert_code(
        "plan-clarifications-open", lambda: store.publish(by="planner", export_dir=tmp_path)
    )
    store.answer(question, "Resolved")
    store.remove_node("test_money")
    _assert_code(
        "plan-requirement-without-test", lambda: store.publish(by="planner", export_dir=tmp_path)
    )
    store.upsert_node(load_spine(FIXTURE).node("test_money"))
    revision = store.publish(by="planner", export_dir=tmp_path)
    (tmp_path / revision / "manifest.json").write_text("tampered", encoding="utf-8")
    _assert_code("plan-export-conflict", lambda: store.publish(by="planner", export_dir=tmp_path))
    _assert_code("plan-revision-missing", lambda: store.published_manifest("missing"))
    _assert_code("plan-revision-missing", lambda: store.impact_since("missing"))


def test_register_refusals_are_atomic(store):
    _assert_code("plan-input-invalid", lambda: store.ask(""))
    _assert_code("plan-node-missing", lambda: store.ask("Question", ["unknown"]))
    _assert_code("plan-input-invalid", lambda: store.add_assumption(""))
    _assert_code("plan-node-missing", lambda: store.add_assumption("Assumption", ["unknown"]))
    _assert_code("plan-clarification-missing", lambda: store.answer("missing", "Answer"))
    _assert_code(
        "plan-assumption-missing",
        lambda: store.confirm_assumption("missing", rationale="Test decision"),
    )
    question = store.ask("Question")
    store.answer(question, "Answer")
    _assert_code("plan-clarification-closed", lambda: store.waive(question, "Reason"))
    assumption = store.add_assumption("Assumption")
    store.confirm_assumption(assumption, rationale="Test decision")
    _assert_code(
        "plan-assumption-closed",
        lambda: store.reject_assumption(assumption, rationale="Test decision"),
    )


def test_publish_export_failure_rolls_back_and_can_retry(store, tmp_path, monkeypatch):
    _link_r6(store)
    _to(store, "publish")
    outdir = tmp_path / "exports"
    replace = Path.replace
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Forced export failure")
        return replace(source, target)

    before = store.status()
    events = store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_second)
        with pytest.raises(OSError, match="Forced export failure"):
            store.publish(by="planner", export_dir=outdir)
    assert store.status() == before
    assert store.connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0
    assert store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events
    assert not any(path.is_file() for path in outdir.rglob("*"))
    revision = store.publish(by="planner", export_dir=outdir)
    assert load_plan(outdir / revision / "manifest.json").name == store.spine().name


def test_mutations_reserve_preimage_before_writing(store, tmp_path):
    with open_plan_store(tmp_path) as second:
        second.connection.execute("PRAGMA busy_timeout=0")
        store.connection.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                second.ask("Should not partly write")
        finally:
            store.connection.rollback()
        assert second.status()["open_clarifications"] == 0
        second.ask("Now it can write")
        assert store.status()["open_clarifications"] == 1


def test_published_revision_conflict_and_republish_selection(store, tmp_path):
    _link_r6(store)
    _to(store, "publish")
    original = store.spine().to_dict()
    first = store.publish(by="planner", export_dir=tmp_path / "exports")
    node = store.spine().node("D3")
    node["text"] += " A new choice."
    store.upsert_node(node)
    publication_evidence(store)
    second = store.publish(by="planner", export_dir=tmp_path / "exports")
    assert first != second
    path = tmp_path / "original.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    store.import_spine(path)
    assert store.publish(by="planner", export_dir=tmp_path / "exports") == first
    assert store.status()["published_revision"] == first
    assert (
        store.connection.execute("SELECT kind FROM events ORDER BY id DESC").fetchone()[0]
        == "publication-selected"
    )
    original["name"] = "renamed"
    path.write_text(json.dumps(original), encoding="utf-8")
    store.import_spine(path)
    _assert_code(
        "plan-revision-conflict",
        lambda: store.publish(by="planner", export_dir=tmp_path / "exports"),
    )


def test_publish_binds_workspace_template_and_requires_current_publication(store, tmp_path):
    template = tmp_path / "template"
    shutil.copytree(FIXTURE.parent / "template", template)
    _link_r6(store)
    _to(store, "publish")
    exports = tmp_path / "exports"
    first = store.publish(by="planner", export_dir=exports)
    first_manifest = store.published_manifest(first)
    assert store.status()["pending_gate"] == "successful-review-required"  # Source-only reviews.
    assert store.status()["publication"] == {
        "published_revision": first,
        "current_revision": first,
        "up_to_date": True,
    }
    assert first_manifest == compile_manifest(store.spine(), template)
    assert first != store.spine().plan_revision()
    proof = template / "tests/test_money.py"
    proof.write_text(proof.read_text() + "\n# revised test\n", encoding="utf-8")
    status = store.status()
    assert (
        status["pending_gate"] == "passing-audit-required"
    )  # The edited proof invalidates evidence.
    assert status["phase_gate"] == "current-revision-published"
    assert status["publication"]["published_revision"] == first
    assert (
        status["publication"]["current_revision"]
        == compile_manifest(store.spine(), template)["planRevision"]
    )
    assert status["publication"]["up_to_date"] is False
    _assert_code("plan-publication-required", lambda: store.advance("execute", by="planner"))
    with open_plan_store(tmp_path) as reopened:
        publication_evidence(reopened)
        second = reopened.publish(by="planner", export_dir=exports)
        assert second != first
        assert reopened.published_manifest(first) == first_manifest
        assert reopened.published_manifest(second) == compile_manifest(reopened.spine(), template)
        assert reopened.publish(by="planner", export_dir=exports) == second
        reopened.advance("execute", by="planner")


def _closure_policy_fixture(store, *, adopted=True, with_finding=True):
    """Actual store/review producers with source-only process and audit envelopes."""
    from codex_harness.core.roundtable import findings

    _link_r6(store)
    _to(store, "roundtable")
    revision = store.manifest_revision()
    slots = [dict(name="closure-reviewer", provider="codex", required=True)]
    if adopted:
        store.record_plan_review_policy(slots, revision)
    reviewer = dict(name="closure-reviewer", provider="codex")
    reviewed = source_controller_roundtable(
        store,
        reviewers=[reviewer],
        executable_for=lambda provider: provider,
        process_stub=lambda *_: (
            0,
            json.dumps(
                [
                    dict(
                        node_id="R1",
                        severity="major",
                        finding="Accepted contract still defective",
                        suggestion="Inspect the original requirement and product evidence",
                    )
                ]
                if with_finding
                else []
            ),
        ),
    )
    audit_id = store.record_audit(
        revision,
        {
            "ok": True,
            "isMocked": True,
            "sourceOnly": "Bookkeeping; not real audit proof",
        },
    )
    return revision, reviewer, reviewed, audit_id, findings(store)


def _closure_declaration_fixture(store, rows, reviewed, audit_id, disposition):
    node = store.connection.execute("SELECT revision FROM nodes WHERE id='R1'").fetchone()[0]
    return dict(
        schema="seldon/finding-closure-declaration-v1",
        expectedManifestRevision=store.manifest_revision(),
        memberIds=rows[0]["memberIds"],
        basis={"resolved": "repair", "rejected": "counterevidence", "deferred": "defer"}[
            disposition
        ],
        nodeRefs=[dict(nodeId="R1", revision=node)],
        reviewIds=[reviewed["reviewers"][0]["id"]]
        if "id" in reviewed["reviewers"][0]
        else [
            store.connection.execute(
                "SELECT id FROM review_runs WHERE round_id=?", (reviewed["roundId"],)
            ).fetchone()[0]
        ],
        auditIds=[audit_id],
        obligationNodeIds=["R1"],
    )


def test_policy_writer_is_pre_attempt_immutable_and_does_not_relabel_legacy(store):
    revision, _, _, _, _ = _closure_policy_fixture(store, adopted=False, with_finding=False)
    original = list(store.connection.iterdump())
    assert store.plan_review_policy_state(revision)["active"] is False
    with pytest.raises(PlanStoreError, match="Declare roles before"):
        store.record_plan_review_policy(
            [
                dict(name="closure-reviewer", provider="codex", required=False),
                dict(name="other", provider="claude", required=True),
            ],
            revision,
        )
    assert list(store.connection.iterdump()) == original
    assert len(store.successful_reviews(revision)) == 1


def test_policy_roles_cannot_be_downgraded_after_required_failure(store):
    revision, reviewer, _, _, _ = _closure_policy_fixture(store, with_finding=False)
    source_controller_roundtable(
        store,
        reviewers=[reviewer],
        executable_for=lambda provider: provider,
        process_stub=lambda *_: (7, "SOURCE-only failed review"),
    )
    assert store.successful_reviews(revision) == []
    with pytest.raises(PlanStoreError, match="Existing reviewer roles are immutable"):
        store.record_plan_review_policy(
            [
                dict(name="closure-reviewer", provider="codex", required=False),
                dict(name="other", provider="claude", required=True),
            ],
            revision,
        )
    with pytest.raises(PlanStoreError, match="Prospective review policy held") as held:
        store._check_evidence(revision)
    assert held.value.code == "plan-review-policy-unmet"


def test_clean_policy_review_retains_ordinary_empty_array_credit_without_independence_claim(store):
    revision, _, _, _, _ = _closure_policy_fixture(store, with_finding=False)
    state = store.plan_review_policy_state(revision)
    assert state["eligible"] is True and state["closureFindings"] == []
    assert len(store.successful_reviews(revision)) == 1
    assert state["producerAvailability"]["observedSeparation"] == "unavailable"
    assert state["producerAvailability"]["targetedAssessment"] == "unavailable"
    store._check_evidence(revision)


@pytest.mark.parametrize("disposition", ["resolved", "rejected", "deferred"])
def test_supported_closure_links_remain_declarations_without_independent_producer(
    store, disposition
):
    revision, _, reviewed, audit_id, rows = _closure_policy_fixture(store)
    original_node = store.connection.execute("SELECT revision FROM nodes WHERE id='R1'").fetchone()[
        0
    ]
    declaration = _closure_declaration_fixture(store, rows, reviewed, audit_id, disposition)
    reason = {
        "resolved": "fixed",
        "rejected": "Counterargument: compare the linked original",
        "deferred": "Retain R1 in the locked obligation set",
    }[disposition]
    store.resolve_findings(
        [dict(id=rows[0]["id"], status=disposition, resolution=reason, closure=declaration)]
    )
    assert (
        store.connection.execute("SELECT revision FROM nodes WHERE id='R1'").fetchone()[0]
        == original_node
    )
    state = store.plan_review_policy_state(revision)
    assert not state["eligible"]
    finding = state["closureFindings"][0]
    assert finding["closure"] == "unverified" and finding["disposition"] == disposition
    assert finding["declaration"]["basis"] == declaration["basis"]
    assert finding["retainedObligation"] is True
    assert finding["deferred"] is (disposition == "deferred")
    assert "observed-separation-producer-unavailable" in finding["reasons"]
    with pytest.raises(PlanStoreError, match="Prospective review policy held") as held:
        store._check_evidence(revision)
    assert held.value.code == "plan-review-policy-unmet"
    assert store.review(reviewed["roundId"])["reviewPolicy"] == state
    requirement = store.spine().node("R1")
    requirement["text"] += " Same locked obligation, explicit evidence inspection pending."
    store.upsert_node(requirement)
    refreshed = store.plan_review_policy_state(store.manifest_revision())
    assert "review-policy-revision-stale" in refreshed["reasons"]
    assert refreshed["closureFindings"][0]["retainedObligation"] is True
    assert refreshed["closureFindings"][0]["declarationBindingCurrent"] is False


@pytest.mark.parametrize(
    "field", ["reviewIds", "auditIds", "expectedManifestRevision", "nodeRefs", "memberIds"]
)
def test_fabricated_or_stale_closure_references_refuse_atomically(store, field):
    _, _, reviewed, audit_id, rows = _closure_policy_fixture(store)
    declaration = _closure_declaration_fixture(store, rows, reviewed, audit_id, "resolved")
    declaration[field] = {
        "nodeRefs": [dict(nodeId="R1", revision="f" * 64)],
        "expectedManifestRevision": "f" * 64,
    }.get(field, ["invented"])
    before = list(store.connection.iterdump())
    with pytest.raises(ValueError, match="review-closure-"):
        store.resolve_findings(
            [dict(id=rows[0]["id"], status="resolved", resolution="fixed", closure=declaration)]
        )
    assert list(store.connection.iterdump()) == before


def test_plain_nonempty_resolution_and_legacy_empty_rereview_do_not_certify_repair(store):
    revision, reviewer, _, _, rows = _closure_policy_fixture(store)
    store.resolve_findings([dict(id=rows[0]["id"], status="resolved", resolution="fixed")])
    source_controller_roundtable(
        store,
        reviewers=[reviewer],
        executable_for=lambda provider: provider,
        process_stub=lambda *_: (0, "[]"),
    )
    state = store.plan_review_policy_state(revision)
    assert state["closureFindings"][0]["declaration"] is None
    assert state["closureFindings"][0]["closure"] == "unverified"
    with pytest.raises(PlanStoreError, match="Prospective review policy held") as held:
        store._check_evidence(revision)
    assert held.value.code == "plan-review-policy-unmet"


def test_policy_stale_revision_holds_without_revoking_legacy_history(store):
    old, _, _, _, _ = _closure_policy_fixture(store, adopted=False, with_finding=False)
    original_runs = [tuple(row) for row in store.connection.execute("SELECT * FROM review_runs")]
    requirement = store.spine().node("R1")
    requirement["text"] += " Clarify the existing required output contract."
    store.upsert_node(requirement)
    current = store.manifest_revision()
    assert current != old
    store.record_plan_review_policy(
        [
            dict(name="closure-reviewer", provider="codex", required=True),
        ],
        current,
    )
    assert len(store.successful_reviews(old)) == 1  # historical ordinary read, no new credit
    assert store.successful_reviews(current) == []
    node = store.spine().node("R1")
    node["text"] += " Preserve case in that output."
    store.upsert_node(node)
    newest = store.manifest_revision()
    state = store.plan_review_policy_state(newest)
    assert state["active"] and state["reasons"] == ["review-policy-revision-stale"]
    with pytest.raises(PlanStoreError, match="Prospective review policy held"):
        store._check_evidence(newest)
    assert [
        tuple(row) for row in store.connection.execute("SELECT * FROM review_runs")
    ] == original_runs


def test_existing_audit_from_wrong_revision_cannot_support_closure(store):
    _, _, reviewed, audit_id, rows = _closure_policy_fixture(store)
    old = store.manifest_revision()
    requirement = store.spine().node("R1")
    requirement["text"] += " Clarified expected output before a fresh audit."
    store.upsert_node(requirement)
    assert store.manifest_revision() != old
    declaration = _closure_declaration_fixture(store, rows, reviewed, audit_id, "resolved")
    declaration["reviewIds"] = []  # Isolate the actual stale audit record, not a missing record.
    before = list(store.connection.iterdump())
    with pytest.raises(ValueError, match="review-closure-record-stale-or-absent: auditIds"):
        store.resolve_findings(
            [dict(id=rows[0]["id"], status="resolved", resolution="fixed", closure=declaration)]
        )
    assert list(store.connection.iterdump()) == before


def test_execution_uncertain_older_reservation_cannot_be_cleared_without_capture(
    tmp_path, monkeypatch
):
    from tests.python.core.test_roundtable import canonical_af_fixture

    from codex_harness.core import roundtable

    store, request, _, _, _, calls, _ = canonical_af_fixture(tmp_path, monkeypatch)
    try:
        older = store.register_execution_assessment(**dict(request, timeout=16))
        current = roundtable.assess_execution_findings(store, **request)
        assert len(calls) == 3
        assert current["members"][0]["support"] == "unverified"
        assert (
            "execution-overlapping-incomplete-reservation" in current["members"][0]["holdReasons"]
        )
        before = list(store.connection.iterdump())
        with pytest.raises(ValueError, match="capture"):
            store.commit_execution_assessment(older["registrationId"])
        assert list(store.connection.iterdump()) == before
        assert store.execution_assessment_state() == current
    finally:
        store.close()


def test_execution_fabricated_consumption_cannot_release_unknown_overlap(tmp_path, monkeypatch):
    from tests.python.core.test_roundtable import canonical_af_fixture

    from codex_harness.core import roundtable

    store, request, _, _, _, calls, _ = canonical_af_fixture(tmp_path, monkeypatch)
    try:
        older = store.register_execution_assessment(**dict(request, timeout=16))
        current = roundtable.assess_execution_findings(store, **request)
        assert current["members"][0]["support"] == "unverified"
        # Negative corruption counterfactual, not a supported event-import API.
        # No real captured original exists to establish terminal certainty.
        store._event(
            "execution-assessment-consumed",
            dict(schema=1, registrationId=older["registrationId"], captureHash=None, members=[]),
        )
        before = list(store.connection.iterdump())
        state = store.execution_assessment_state()
        assert state["members"][0]["support"] == "unverified"
        assert "execution-overlapping-incomplete-reservation" in state["members"][0]["holdReasons"]
        assert list(store.connection.iterdump()) == before and len(calls) == 3
    finally:
        store.close()
