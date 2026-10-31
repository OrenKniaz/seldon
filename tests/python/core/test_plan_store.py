"""Real store/graph integration and machine prerequisites.

V1 revision credits, separate reviewer consent and plan grants are replaced by
test_planner_confirmations and test_planner_chat_confirmations V2 boundaries.
"""

import queue
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest
from tests.python.core import test_test_audit
from tests.python.core.planning_support import (
    publication_evidence,
    source_only_b,
    source_only_confirmation,
    source_only_expansion,
)

from codex_harness.core.graph import PlanError, load_plan
from codex_harness.core.plan_store import PHASES, PlanStore, open_plan_store
from codex_harness.core.spine import SpineError

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


@pytest.fixture
def store(tmp_path):
    with open_plan_store(tmp_path, "initial") as result:
        result.import_spine(FIXTURE)
        shutil.copytree(FIXTURE.parent / "template", tmp_path / "template")
        yield result


def _link_r6(store):
    node = store.spine().node("A")
    node["parents"].append("R6")
    store.upsert_node(node)


def _to(store, phase):
    if store.phase() == "align" and "expansion" not in store.spine().node("V1")["fields"]:
        source_only_expansion(store)
    while PHASES.index(store.phase()) < PHASES.index(phase):
        current = store.phase()
        if current in ("align", "vision-lock"):
            source_only_confirmation(store, current, statement="Approve")
        if current == "plan":
            source_only_b(store)
        if current == "roundtable" and phase == "publish":
            publication_evidence(store)
        store.advance(PHASES[PHASES.index(current) + 1], by="planner")


def _assert_code(code, callback):
    with pytest.raises((SpineError, PlanError)) as caught:
        callback()
    assert caught.value.code == code


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
