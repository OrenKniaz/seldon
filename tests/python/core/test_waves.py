"""Pure values only: these fixtures do not prove dispatch or product acceptance."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from codex_harness.core.spine import SCHEMA, Spine, _canonical, _hash, compile_manifest
from codex_harness.core.waves import WaveError, accounting, validate_map

PUB, LOCK = "a" * 64, "b" * 64


def node(key, kind, parents=(), **fields):
    return dict(
        id=key, kind=kind, title=key, text=f"Intent {key}", parents=list(parents), fields=fields
    )


def values():
    spine = Spine(
        dict(
            schema=SCHEMA,
            name="useful-outcomes",
            nodes=[
                node(
                    "V",
                    "vision",
                    validation=["python", "-m", "unittest", "discover"],
                    requirementFiles=[],
                ),
                node("F", "feature", ["V"]),
                node("S", "story", ["F"]),
                node("R1", "requirement", ["S"]),
                node("R2", "requirement", ["S"]),
                node("D", "design", ["R1"]),
                node("AC", "acceptance", ["F"], steps=["render first outcome"]),
                *[
                    node(
                        bid,
                        "bead",
                        ["R1"],
                        ownedFiles=[f"src/{bid}.py"],
                        dependsOn=deps,
                        lessons=[],
                        proofModule=f"tests/test_{bid}.py",
                    )
                    for bid, deps in (("B1", []), ("B2", ["B1"]), ("B3", ["B1"]))
                ],
            ],
        )
    )
    waves = [
        dict(
            id=wid,
            outcome=outcome,
            demo=demo,
            requirementIds=[rid],
            featureIds=["F"],
            storyIds=["S"],
            designIds=["D"],
            riskIds=[],
            exitScenarioIds=[],
            dependsOnWaves=deps,
            incomingContractIds=[],
            outgoingContractIds=[],
        )
        for wid, rid, outcome, demo, deps in (
            ("W1", "R1", "Read the current plan", "Show a complete first plan", []),
            ("W2", "R2", "Refine the later plan", "Show retained future intent", ["W1"]),
        )
    ]
    waves[0].update(exitScenarioIds=["AC"], riskIds=["D"], outgoingContractIds=["C"])
    waves[1]["incomingContractIds"] = ["C"]

    def obligation(oid, kind, sources, wids, evidence):
        return dict(
            id=oid,
            kind=kind,
            sourceRefs=[dict(id=k, revision=spine.revision(k)) for k in sources],
            waveIds=wids,
            criterion=f"Prove {oid}",
            requiredEvidence=evidence,
            requiredForFullVision=True,
        )

    obligations = [
        obligation("O1", "requirement", ["R1"], ["W1"], ["source-test", "product-integration"]),
        obligation("O2", "requirement", ["R2"], ["W2"], ["source-test"]),
        obligation("OS", "scenario", ["AC"], ["W1"], ["clean-state-product"]),
        obligation("OH", "handoff", ["D"], ["W1", "W2"], ["fixture-contract"]),
        obligation("OR", "risk-verification", ["D"], ["W1"], ["product-integration"]),
        obligation("OG", "review", ["V"], ["W1", "W2"], ["independent-review", "human-acceptance"]),
    ]
    plan = dict(
        schema="dot-wave-map-v1",
        order=["W1", "W2"],
        waves=waves,
        obligations=obligations,
        contracts=[
            dict(
                id="C",
                producerWaveId="W1",
                consumerWaveIds=["W2"],
                specRef=dict(id="D", revision=spine.revision("D")),
                interface=dict(
                    symbol="render",
                    inputShape={},
                    outputShape={},
                    errors=["missing"],
                    invariants=["retains intent"],
                ),
                verificationObligationIds=["OH"],
            )
        ],
        globalReviewObligationId="OG",
    )
    details = [
        dict(
            schema="dot-wave-detail-v1",
            waveId="W1",
            detail="detailed",
            beadIds=["B1", "B2", "B3"],
            sketches=[],
            observedInterfaces=[],
            verificationObligationIds=["O1", "OS", "OR"],
        ),
        dict(
            schema="dot-wave-detail-v1",
            waveId="W2",
            detail="sketch",
            beadIds=[],
            sketches=[
                dict(
                    id="SK",
                    title="Later refinement",
                    ownershipArea="planner",
                    requirementIds=["R2"],
                    expectedContractIds=["C"],
                    risks=["producer not observed"],
                    pendingProducerIds=["B1"],
                    detailAfterWaveId="W1",
                )
            ],
            observedInterfaces=[],
            verificationObligationIds=["O2", "OH", "OG"],
        ),
    ]
    return spine, plan, details


def snapshot():
    spine, plan, details = values()
    result = validate_map(spine, plan, details)
    # The actual Spine compiler supplies dependencies, not graph depth or sketches.
    manifest = compile_manifest(spine)
    return dict(
        publicationRevision=PUB,
        productLockRevision=LOCK,
        waveMap=result["waveMap"],
        waveDetails=result["waveDetails"],
        nodeRevisions={key: spine.revision(key) for key in spine.ids()},
        selectedWaveIds=["W1"],
        beadDependencies={key: row["dependsOn"] for key, row in manifest["beads"].items()},
        historicalMeasurements=[
            dict(
                sourceRef="existing-history",
                publicationRevision=None,
                observedAt=None,
                ready=0,
                sampled=3,
                blockers=7,
                evidenceClass="reported-history",
                status="historical-not-remeasured",
            )
        ],
    )


def envelope(snap, record, **extra):
    return dict(
        sourceRef="canonical-record",
        binding=dict(
            publicationRevision=snap["publicationRevision"],
            productLockRevision=snap["productLockRevision"],
            nodeRevisions=deepcopy(snap["nodeRevisions"]),
            waveMapRevision=_hash(_canonical(snap["waveMap"])),
        ),
        record=record,
        **extra,
    )


def observations(snap):
    # Exact canonical record structure and claim digest convention, synthetic execution.
    claim = dict(
        bead="B1",
        requirementIds=["R1"],
        proofModule="tests/test_B1.py",
        verificationIdentity="c" * 64,
        sourceIdentity="d" * 64,
        dependencyIdentities={},
        candidateCommit="e" * 40,
        candidateTree="f" * 40,
        passed=True,
        at="2026-10-15T10:00:00Z",
    )
    claim["claimId"] = (
        "B1:"
        + hashlib.sha256(
            json.dumps(claim, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    row = dict(
        isMocked=False,
        didRunProvider=True,
        didRunIntegratedValidation=True,
        planRevision=PUB,
        requirementRefs=[dict(id="R1", revision=snap["nodeRevisions"]["R1"])],
        specRefs=[dict(id="D", revision=snap["nodeRevisions"]["D"])],
        integrated=dict(
            claim=deepcopy(claim),
            integrationCommit=claim["candidateCommit"],
            validation=dict(passed=True, command=["python", "-m", "unittest", "tests.test_B1"]),
        ),
    )
    run = dict(
        schema="codex-harness/plan-run-v1",
        planRevision=PUB,
        isMocked=False,
        didExecuteRealProvider=True,
        claims=[deepcopy(claim)],
        beads={"B1": row},
    )
    return [envelope(snap, run)], [
        envelope(snap, claim, evidenceKind="source-test", obligationIds=["O1"])
    ]


def test_useful_outcome_later_sketch_and_copy_contract():
    spine, plan, details = values()
    before = deepcopy((plan, details))
    result = validate_map(spine, plan, details)
    assert set(result) == {
        "waveMap",
        "waveDetails",
        "obligationIds",
        "sketchedItemIds",
        "detailedBeadIds",
        "lockState",
    }
    assert result["detailedBeadIds"] == ["B1", "B2", "B3"]
    assert result["sketchedItemIds"] == ["SK"]
    assert result["lockState"] == "unconfirmed"
    assert "SK" not in spine.ids()
    result["waveMap"]["waves"][0]["demo"] = "Changed copy"
    assert (plan, details) == before


def test_existing_repository_spine_revisions_are_resolved():
    source = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"
    spine = Spine(json.loads(source.read_text(encoding="utf-8")))
    _, plan, details = values()
    # This is deliberately a different real Spine: dangling fixture refs must fail.
    with pytest.raises(WaveError) as caught:
        validate_map(spine, plan, details)
    assert caught.value.code == "wave-reference-invalid"
    # Cover every actual required requirement while keeping the later outcome a sketch.
    plan["waves"][0].update(
        requirementIds=["R1", "R2", "R3", "R6"],
        featureIds=["F1"],
        storyIds=["S1"],
        designIds=[],
        exitScenarioIds=[],
        riskIds=[],
    )
    plan["waves"][1].update(
        requirementIds=["R4", "R5"], featureIds=["F2"], storyIds=["S2"], designIds=[]
    )
    plan["contracts"][0]["specRef"] = dict(id="D1", revision=spine.revision("D1"))
    plan["obligations"] = [
        dict(
            id="REQ-" + key,
            kind="requirement",
            sourceRefs=[dict(id=key, revision=spine.revision(key))],
            waveIds=["W2" if key in {"R4", "R5"} else "W1"],
            criterion="Prove " + key,
            requiredEvidence=["product-integration"],
            requiredForFullVision=True,
        )
        for key in spine.ids("requirement")
    ]
    for oid, kind, source, evidence in (
        ("OH", "handoff", "D1", "fixture-contract"),
        ("OG", "review", "V1", "independent-review"),
    ):
        plan["obligations"].append(
            dict(
                id=oid,
                kind=kind,
                sourceRefs=[dict(id=source, revision=spine.revision(source))],
                waveIds=["W1", "W2"],
                criterion="Prove " + oid,
                requiredEvidence=[evidence],
                requiredForFullVision=True,
            )
        )
    details[0].update(beadIds=["A"], verificationObligationIds=["REQ-R1"])
    details[1]["verificationObligationIds"] = ["REQ-R4", "OH", "OG"]
    details[1]["sketches"][0].update(requirementIds=["R4", "R5"], pendingProducerIds=["A"])
    validated = validate_map(spine, plan, details)
    assert validated["detailedBeadIds"] == ["A"]
    assert validated["sketchedItemIds"] == ["SK"]
    assert len(validated["obligationIds"]) == 8


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda p, d: p.update(globalReviewObligationId="missing"), "wave-global-review-missing"),
        (lambda p, d: p["waves"][1].update(requirementIds=[]), "wave-coverage-missing"),
        (lambda p, d: p["waves"][0].update(dependsOnWaves=["W2"]), "wave-cycle"),
        (
            lambda p, d: p["contracts"][0]["specRef"].update(revision="0" * 64),
            "wave-reference-stale",
        ),
        (lambda p, d: p["waves"][0].update(exitScenarioIds=["R1"]), "wave-reference-invalid"),
        (
            lambda p, d: p["obligations"][3].update(requiredForFullVision=False),
            "wave-coverage-missing",
        ),
        (lambda p, d: d[1]["sketches"][0].update(id="B1"), "wave-duplicate-id"),
        (lambda p, d: d[0].update(beadIds=["SK"]), "wave-reference-invalid"),
    ],
)
def test_rejects_missing_coverage_revisions_cycles_and_fake_beads(change, code):
    spine, plan, details = values()
    change(plan, details)
    with pytest.raises(WaveError) as caught:
        validate_map(spine, plan, details)
    assert caught.value.code == code
    assert caught.value.reason


def test_contract_cycle_even_without_explicit_wave_dependency():
    spine, plan, details = values()
    plan["waves"][1]["dependsOnWaves"] = []
    reverse = deepcopy(plan["contracts"][0])
    reverse.update(id="BACK", producerWaveId="W2", consumerWaveIds=["W1"])
    plan["contracts"].append(reverse)
    plan["waves"][1]["outgoingContractIds"] = ["BACK"]
    plan["waves"][0]["incomingContractIds"] = ["BACK"]
    with pytest.raises(WaveError) as caught:
        validate_map(spine, plan, details)
    assert caught.value.code == "wave-cycle"


def test_lock_closure_protects_intent_without_recursive_digest():
    spine, plan, details = values()
    revisions = {
        key: spine.revision(key)
        for key in spine.ids()
        if spine.node(key)["kind"] not in {"bead", "test"}
    }
    lock = dict(
        sourceRef="existing-approval-event",
        digest=_hash(_canonical(dict(nodeRevisions=revisions, waveMap=plan))),
    )
    assert validate_map(spine, plan, details, lock)["lockState"] == "current"
    details[1]["sketches"][0]["title"] = "More precise later detail"
    assert validate_map(spine, plan, details, lock)["lockState"] == "current"
    plan["waves"][0]["outcome"] = "A different product"
    assert validate_map(spine, plan, details, lock)["lockState"] == "stale"


def stored_detail_values():
    spine, plan, details = values()
    data = spine.to_dict()
    data["nodes"].extend(
        [
            node("DW", "decision", ["V"], **deepcopy(plan)),
            node("DS", "design", ["DW"], **deepcopy(details[1])),
        ]
    )
    return Spine(data), plan, details


def stored_detail_lock(spine, plan, projected=("DS",)):
    revisions = {}
    for key in spine.ids():
        value = spine.node(key)
        if value["kind"] in {"bead", "test"}:
            continue
        if key in projected:
            value["fields"] = {}
        revisions[key] = _hash(_canonical(value))
    return dict(
        sourceRef="existing-approval-event",
        digest=_hash(_canonical(dict(nodeRevisions=revisions, waveMap=plan))),
    )


def test_stored_detail_sketch_title_change_keeps_product_lock_current():
    spine, plan, details = stored_detail_values()
    lock = stored_detail_lock(spine, plan)
    assert validate_map(spine, plan, details, lock)["lockState"] == "current"
    data = spine.to_dict()
    stored = next(n for n in data["nodes"] if n["id"] == "DS")
    stored["fields"]["sketches"][0]["title"] = "Refined stored sketch title"
    details[1] = deepcopy(stored["fields"])
    changed = Spine(data)
    assert spine.revision("DS") != changed.revision("DS")
    assert validate_map(changed, plan, details, lock)["lockState"] == "current"
    assert validate_map(changed, plan, details)["lockState"] == "unconfirmed"
    # Only the stored payload is excluded; node product prose stays protected.
    stored["text"] = "Changed product intent in the design body"
    assert validate_map(Spine(data), plan, details, lock)["lockState"] == "stale"


@pytest.mark.parametrize(
    "use", ["design", "risk", "contract", "producer-parent", "extra-field", "wrong-owner"]
)
def test_detail_tag_cannot_hide_protected_design_or_unowned_payload(use):
    spine, plan, details = stored_detail_values()
    data = spine.to_dict()
    stored = next(n for n in data["nodes"] if n["id"] == "DS")
    if use == "design":
        plan["waves"][0]["designIds"].append("DS")
    elif use == "risk":
        plan["waves"][0]["riskIds"].append("DS")
        check = deepcopy(plan["obligations"][4])
        check.update(id="STORED-RISK", sourceRefs=[dict(id="DS", revision=spine.revision("DS"))])
        plan["obligations"].append(check)
    elif use == "contract":
        plan["contracts"][0]["specRef"] = dict(id="DS", revision=spine.revision("DS"))
        plan["obligations"][3]["sourceRefs"] = [dict(id="DS", revision=spine.revision("DS"))]
    elif use == "producer-parent":
        next(n for n in data["nodes"] if n["id"] == "B1")["parents"].append("DS")
    elif use == "extra-field":
        stored["fields"]["productIntent"] = "Must remain locked"
    else:
        stored["parents"] = ["R1"]
    next(n for n in data["nodes"] if n["id"] == "DW")["fields"] = deepcopy(plan)
    changed = Spine(data)
    assert (
        validate_map(changed, plan, details, stored_detail_lock(changed, plan))["lockState"]
        == "stale"
    )
    full_lock = stored_detail_lock(changed, plan, projected=())
    assert validate_map(changed, plan, details, full_lock)["lockState"] == "current"


@pytest.mark.parametrize("key", ["R1", "AC"])
def test_detail_schema_on_requirement_or_scenario_remains_protected(key):
    spine, plan, details = stored_detail_values()
    data = spine.to_dict()
    next(n for n in data["nodes"] if n["id"] == key)["fields"] = deepcopy(details[1])
    changed = Spine(data)
    for obligation in plan["obligations"]:
        for ref in obligation["sourceRefs"]:
            if ref["id"] == key:
                ref["revision"] = changed.revision(key)
    next(n for n in data["nodes"] if n["id"] == "DW")["fields"] = deepcopy(plan)
    changed = Spine(data)
    assert (
        validate_map(changed, plan, details, stored_detail_lock(changed, plan))["lockState"]
        == "current"
    )
    misleading_lock = stored_detail_lock(changed, plan, projected=("DS", key))
    assert validate_map(changed, plan, details, misleading_lock)["lockState"] == "stale"


def test_partial_evidence_and_producer_denominators():
    snap = snapshot()
    result = accounting(snap, [], [], [])
    assert result["readiness"]["eligibleBeadIds"] == ["B1"]
    assert result["readiness"]["pendingProducerBeadCount"] == 2
    assert result["readiness"]["distinctPendingProducerCount"] == 1
    runs, claims = observations(snap)
    result = accounting(snap, runs, claims, [])
    assert result["readiness"]["eligibleBeadCount"] == 3
    assert result["readiness"]["readyEligibleCount"] == 0
    assert result["readiness"]["dispatchableNowCount"] == 0
    assert result["fullVision"]["totalObligations"] == 6
    assert result["fullVision"]["completedRealObligations"] == 0
    assert result["obligationStates"][0]["evidenceRefs"] == ["canonical-record"]
    # A source-test-only obligation can complete; product obligations cannot borrow it.
    snap["waveMap"]["obligations"][0]["requiredEvidence"] = ["source-test"]
    runs, claims = observations(snap)
    result = accounting(snap, runs, claims, [])
    assert result["fullVision"]["completedRealObligations"] == 1
    assert result["fullVision"]["outstandingObligations"] == 5
    assert result["fullVision"]["acceptedHuman"] is False
    assert result["fullVision"]["acceptedProxy"] is False


@pytest.mark.parametrize(
    "changed",
    ["publication", "lock", "intent", "contract", "mock", "reference", "digest", "status-only"],
)
def test_stale_mocked_reference_or_status_evidence_gets_no_credit(changed):
    snap = snapshot()
    snap["waveMap"]["obligations"][0]["requiredEvidence"] = ["source-test"]
    runs, claims = observations(snap)
    if changed == "publication":
        snap["publicationRevision"] = "1" * 64
    elif changed == "lock":
        snap["productLockRevision"] = "2" * 64
    elif changed == "intent":
        snap["nodeRevisions"]["R1"] = "3" * 64
    elif changed == "contract":
        snap["waveMap"]["contracts"][0]["interface"]["symbol"] = "different"
    elif changed == "mock":
        runs[0]["record"]["isMocked"] = True
    elif changed == "reference":
        claims[0]["evidenceKind"] = "oracle-run"
    elif changed == "digest":
        claims[0]["record"]["sourceIdentity"] = "4" * 64
    else:
        runs[0]["record"] = dict(status="completed", exitCode=0, passed=True)
    result = accounting(snap, runs, claims, [])
    assert result["fullVision"]["completedRealObligations"] == 0
    assert result["readiness"]["pendingProducerBeadCount"] == 2


def test_draft_missing_dependencies_and_historical_measurement_are_honest_and_immutable():
    snap = snapshot()
    runs, claims = observations(snap)
    review = envelope(
        snap,
        dict(
            id="RR-1",
            batchId="batch1",
            packetHash="9" * 64,
            reviewer=dict(name="reviewer", provider="codex"),
            beadId="B2",
            selectedBeads=["B2"],
            manifestRevision=PUB,
            ready=True,
            canVerify=True,
            exitCode=0,
        ),
        evidenceKind="readiness",
    )
    before = deepcopy((snap, runs, claims, [review]))
    first = accounting(snap, runs, claims, [review])
    assert accounting(snap, runs, claims, [review]) == first
    assert (snap, runs, claims, [review]) == before
    assert first["readiness"]["sampledBeadCount"] == 1
    assert first["readiness"]["eligibleBeadCount"] == 3
    assert first["readiness"]["unreviewedEligibleCount"] == 2
    assert first["readiness"]["readySampledCount"] == 0
    assert first["historicalMeasurements"] == snap["historicalMeasurements"]
    snap["publicationRevision"] = None
    draft = accounting(snap, runs, claims, [review])
    assert draft["fullVision"]["unknownObligations"] == 6
    assert draft["readiness"]["eligibleBeadCount"] == 0
    assert draft["readiness"]["unknownBeadCount"] == 3
    snap["publicationRevision"] = PUB
    del snap["beadDependencies"]
    missing = accounting(snap, runs, claims, [])
    assert missing["readiness"]["eligibleBeadCount"] == 0
    assert missing["readiness"]["unknownBeadCount"] == 3


def test_product_acceptance_requires_observed_steps_and_never_accepts_oracle_or_proxy():
    snap = snapshot()
    record = dict(
        id="AR1",
        planRevision=PUB,
        revision="e" * 64,
        revisionAlgorithm="tree-bytes-v2",
        scenarioRevision="f" * 64,
        subject="product",
        passed=True,
        results=[
            dict(
                id="AC",
                passed=True,
                steps=[dict(command="render first outcome", passed=True, exit=0)],
            )
        ],
    )
    item = envelope(
        snap, record, isMocked=False, evidenceKind="clean-state-product", obligationIds=["OS"]
    )
    assert accounting(snap, [], [], [item])["fullVision"]["completedRealObligations"] == 1
    for modification in (dict(subject="oracle"), dict(results=[]), dict(passed=False)):
        changed = deepcopy(item)
        changed["record"].update(modification)
        assert accounting(snap, [], [], [changed])["fullVision"]["completedRealObligations"] == 0
    item["evidenceKind"] = "human-acceptance"
    assert accounting(snap, [], [], [item])["fullVision"]["acceptedHuman"] is False


def test_nonfinite_json_rejected_with_stable_error():
    spine, plan, details = values()
    plan["contracts"][0]["interface"]["inputShape"]["bad"] = float("nan")
    with pytest.raises(WaveError) as caught:
        validate_map(spine, plan, details)
    assert caught.value.code == "wave-schema-invalid"


def test_missing_scenario_and_unsupported_record_shapes_do_not_pass():
    spine, plan, details = values()
    plan["waves"][0]["exitScenarioIds"] = []
    with pytest.raises(WaveError) as caught:
        validate_map(spine, plan, details)
    assert caught.value.code == "wave-coverage-missing"
    snap = snapshot()
    runs, claims = observations(snap)
    claims[0]["obligationIds"] = [{"unsupported": True}]
    result = accounting(snap, runs, claims, [None, {"status": "complete"}])
    assert result["fullVision"]["completedRealObligations"] == 0
    snap["beadDependencies"] = {"B1": None}
    result = accounting(snap, None, None, None)
    assert result["readiness"]["unknownBeadCount"] == 3
    snap["nodeRevisions"] = None
    assert accounting(snap, [], [], [])["fullVision"]["unknownObligations"] == 6


def test_full_required_evidence_does_not_reduce_global_total_to_selected_scope():
    snap = snapshot()
    runs, claims = observations(snap)
    integration = deepcopy(claims[0]["record"])
    integration.update(bead="integration", proofModule="tests/test_product.py")
    del integration["claimId"]
    integration["claimId"] = (
        "integration:"
        + hashlib.sha256(
            json.dumps(integration, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    runs[0]["record"]["claims"].append(integration)
    runs[0]["record"]["integrationValidation"] = dict(
        passed=True, command=["python", "-m", "unittest", "tests.test_product"]
    )
    claims.append(
        envelope(snap, integration, evidenceKind="product-integration", obligationIds=["O1"])
    )
    result = accounting(snap, runs, claims, [])
    assert result["fullVision"]["completedRealObligations"] == 1
    assert result["fullVision"]["totalObligations"] == 6
    assert result["fullVision"]["outstandingObligations"] == 5
    assert result["obligationStates"][1]["state"] == "outstanding"
    assert result["readiness"]["unknownBeadCount"] == 3


def test_code_subject_is_validated_without_manufacturing_git_values():
    spine, plan, details = values()
    code = dict(
        repo="C:/existing/repo",
        commit="a" * 40,
        tree="b" * 40,
        integratedRevision="c" * 64,
        algorithm="tree-bytes-v2",
        isClean=True,
        dirtyPaths=[],
        untrackedPaths=[],
    )
    details[0]["observedInterfaces"] = [
        dict(contractId="C", code=code, paths=["src/api.py"], digest="d" * 64)
    ]
    assert validate_map(spine, plan, details)["lockState"] == "unconfirmed"
    code["untrackedPaths"] = ["src/api.py"]
    with pytest.raises(WaveError) as caught:
        validate_map(spine, plan, details)
    assert caught.value.code == "wave-schema-invalid"
    code["isClean"] = False
    validate_map(spine, plan, details)
    details[0]["observedInterfaces"][0]["paths"] = ["../escape"]
    with pytest.raises(WaveError):
        validate_map(spine, plan, details)
