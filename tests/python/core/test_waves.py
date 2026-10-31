"""Pure values only: these fixtures do not prove dispatch or product acceptance."""

import hashlib
import json
from copy import deepcopy

from codex_harness.core.spine import SCHEMA, Spine, _canonical, _hash, compile_manifest
from codex_harness.core.waves import validate_map

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
