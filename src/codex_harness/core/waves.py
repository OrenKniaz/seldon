"""Pure outcome-wave values; no execution, persistence, or approval authority.

Consumer contract
-----------------
``validate_map`` accepts a Spine and the exact WaveMapV1/WaveDetailV1 payloads.
``product_lock`` is None or {sourceRef: str, digest: sha256}. The trusted store
adapter resolves sourceRef to an existing approval/event with actual statement
and appropriate scope; this function does not authenticate or create approval.
The digest is SHA256 of Spine's finite UTF8/sorted-key canonical JSON of
{nodeRevisions: protected revisions, waveMap: WaveMapV1}. Protected revisions
include every non-bead/non-test Spine id. Each value is its actual revision,
except a genuine stored detail carrier hashes its full node with fields={}.
A carrier must be a leaf design, have exactly one parent that is a decision
whose entire fields equal this validated map, and have its entire fields equal
one validated supplied detail (canonical JSON equality, not a schema tag).
Any id referenced by a wave's node lists, a contract specRef or an obligation
sourceRef is never a carrier. Extra fields, other owners and designs with any
children stay fully protected. Node id/kind/title/text/parents remain hashed.
Only carrier payloads and the separate details argument are excluded, so detail
refinement leaves protected product intent unchanged. Accounting nodeRevisions
and all Refs continue to use actual Spine revisions, never these projections.
There is no recursive digest field. A current lock is not execution permission.

``accounting`` takes the pinned snapshot plus ``beadDependencies``: the COMPLETE
{beadId: fields.dependsOn} projection from the same actual Spine. nodeRevisions
must contain every bead and obligation source. Missing dependencies are unknown.
The store adapter must validate_map before constructing this ephemeral snapshot.
publicationRevision is the actual current published manifest revision, or None;
productLockRevision is the above digest only for a current sourced lock, else None.

runs/claims/acceptance are lists of ephemeral envelopes, not durable schemas:
{sourceRef: str, binding: {publicationRevision, productLockRevision, nodeRevisions,
 waveMapRevision}, record: object}. waveMapRevision hashes canonical waveMap;
all bindings must exactly equal the snapshot, including the complete revision
map. The adapter may only attach bindings established by original observation,
never relabel old records. sourceRef resolves the original immutable record.

runs.record is a canonical dispatch evidence/run.json (plan-run-v1).
claims adds {evidenceKind: source-test|product-integration, obligationIds: [id]};
record is dispatch._claim's exact record, also present in a supplied run's claims.
The claim digest, actual integrated validation, provider execution and unmocked
origin are required. Claim input/code identities remain original, not invented.
acceptance adds {evidenceKind: oracle-run|clean-state-product, obligationIds:[id]};
record is acceptance.run_acceptance's result, with actual scenario/step results.
Acceptance envelopes require isMocked: false from verified execution provenance
(the legacy acceptance record itself lacks that field). A product result cannot
be relabelled oracle or vice versa. Unsupported evidence, including human/proxy
and independent review, earns no credit. Canonical readiness records may appear
in acceptance with evidenceKind='readiness'; only their bound sample is counted.
Current packet/assurance/ownership validation is intentionally unavailable here:
ready and dispatchable counts remain zero. This module is not a trust verifier.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from codex_harness.core.spine import Spine, _acyclic, _canonical, _hash

WaveAccountingV1 = dict[str, Any]
EVIDENCE = {
    "source-test",
    "fixture-contract",
    "oracle-run",
    "product-integration",
    "clean-state-product",
    "independent-review",
    "human-acceptance",
    "proxy-acceptance",
}
KINDS = {"requirement", "scenario", "handoff", "risk-verification", "review"}


class WaveError(ValueError):
    """Stable machine code and human reason, without leaking incidental exceptions."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


def _need(condition: bool, reason: str, code: str = "wave-schema-invalid") -> None:
    if not condition:
        raise WaveError(code, reason)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(_text(item) for item in value)


def _sha(value: object, size: int = 64) -> bool:
    return isinstance(value, str) and len(value) == size and set(value) <= set("0123456789abcdef")


def _object(value: object, keys: str) -> None:
    _need(isinstance(value, dict) and set(value) == set(keys.split()), f"Expected keys: {keys}")


def _ids(value: object, *, nonempty: bool = False) -> list[str]:
    _need(isinstance(value, list) and all(_text(item) for item in value), "Expected string ids")
    _need(len(value) == len(set(value)) and (bool(value) or not nonempty), "Ids must be unique")
    return value


def _rows(value: object, key: str = "id") -> dict[str, dict]:
    _need(isinstance(value, list), "Expected object list")
    result = {}
    for row in value:
        _need(isinstance(row, dict) and _text(row.get(key)), f"Expected {key}")
        _need(row[key] not in result, f"Duplicate {key}: {row[key]}", "wave-duplicate-id")
        result[row[key]] = row
    return result


def _dag(parents: dict[str, list[str]]) -> None:
    _need(
        all(set(v) <= parents.keys() for v in parents.values()),
        "Unknown wave",
        "wave-reference-invalid",
    )
    try:
        _acyclic(parents, "wave-cycle")
    except ValueError as error:
        raise WaveError("wave-cycle", "Wave or contract dependency cycle") from error


def _relative(value: object) -> bool:
    if not _text(value):
        return False
    path = PurePosixPath(value.replace("\\", "/"))
    return (
        bool(path.parts) and not path.is_absolute() and ".." not in path.parts and ":" not in value
    )


def _code(value: object) -> None:
    _object(
        value, "repo commit tree integratedRevision algorithm isClean dirtyPaths untrackedPaths"
    )
    _need(
        _text(value["repo"])
        and (
            PurePosixPath(value["repo"]).is_absolute()
            or PureWindowsPath(value["repo"]).is_absolute()
        ),
        "Code repo must be absolute",
    )
    _need(all(_sha(value[k], 40) or _sha(value[k]) for k in ("commit", "tree")), "Invalid Git oid")
    _need(
        _sha(value["integratedRevision"]) and value["algorithm"] == "tree-bytes-v2",
        "Invalid code digest",
    )
    for key in ("dirtyPaths", "untrackedPaths"):
        _ids(value[key])
        _need(all(_relative(p) for p in value[key]), "Invalid code path")
    _need(
        type(value["isClean"]) is bool
        and value["isClean"] == (not value["dirtyPaths"] and not value["untrackedPaths"]),
        "Code cleanliness contradicts paths",
    )


def validate_map(
    spine: Spine, map: dict, details: list[dict], product_lock: dict | None = None
) -> dict:
    """Validate references, outcome DAG, complete intent coverage and lock freshness."""
    _need(isinstance(spine, Spine), "Expected an actual Spine")
    try:
        _canonical([map, details, product_lock])
    except (TypeError, ValueError, OverflowError) as error:
        raise WaveError("wave-schema-invalid", "Expected finite JSON values") from error
    plan, detail_values = deepcopy(map), deepcopy(details)
    _object(plan, "schema order waves contracts obligations globalReviewObligationId")
    _need(plan["schema"] == "dot-wave-map-v1", "Expected dot-wave-map-v1")
    waves = _rows(plan["waves"])
    contracts = _rows(plan["contracts"])
    obligations = _rows(plan["obligations"])
    by_wave = _rows(detail_values, "waveId")
    _need(bool(waves) and set(_ids(plan["order"])) == set(waves), "Order must list every wave")
    _need(set(by_wave) == set(waves), "Every wave needs exactly one detail")
    all_ids = [*waves, *contracts, *obligations]
    _need(len(all_ids) == len(set(all_ids)), "Logical ids cannot be recycled", "wave-duplicate-id")
    _need(
        not set(all_ids) & set(spine.ids()), "Logical id reuses a Spine node", "wave-duplicate-id"
    )

    def nodes(ids: object, kinds: set[str]) -> list[str]:
        values = _ids(ids)
        _need(
            all(key in spine.ids() and spine.node(key)["kind"] in kinds for key in values),
            "Unknown node or wrong node kind",
            "wave-reference-invalid",
        )
        return values

    def ref(value: object) -> str:
        _object(value, "id revision")
        _need(
            _text(value["id"]) and value["id"] in spine.ids(),
            "Unknown source ref",
            "wave-reference-invalid",
        )
        _need(
            _sha(value["revision"]) and spine.revision(value["id"]) == value["revision"],
            "Source revision is stale",
            "wave-reference-stale",
        )
        return value["id"]

    def links(values: object, known: dict) -> list[str]:
        values = _ids(values)
        _need(set(values) <= known.keys(), "Unknown logical reference", "wave-reference-invalid")
        return values

    coverage = set()
    for wave in waves.values():
        _object(
            wave,
            "id outcome demo requirementIds featureIds storyIds designIds riskIds "
            "exitScenarioIds dependsOnWaves incomingContractIds outgoingContractIds",
        )
        _need(_text(wave["outcome"]) and _text(wave["demo"]), "Outcome and demo are required")
        for key, kinds in (
            ("requirementIds", {"requirement"}),
            ("featureIds", {"feature"}),
            ("storyIds", {"story"}),
            ("designIds", {"design"}),
            ("riskIds", {"decision", "design"}),
            ("exitScenarioIds", {"acceptance"}),
        ):
            nodes(wave[key], kinds)
        coverage.update(wave["requirementIds"])
        links(wave["dependsOnWaves"], waves)
        links(wave["incomingContractIds"], contracts)
        links(wave["outgoingContractIds"], contracts)
    parents = {key: row["dependsOnWaves"] for key, row in waves.items()}
    _dag(parents)
    positions = {key: i for i, key in enumerate(plan["order"])}
    _need(
        all(positions[p] < positions[k] for k, deps in parents.items() for p in deps),
        "Order violates dependencies",
        "wave-order-invalid",
    )

    source_ids = {}
    for oid, obligation in obligations.items():
        _object(
            obligation,
            "id kind sourceRefs waveIds criterion requiredEvidence requiredForFullVision",
        )
        _need(_text(obligation["kind"]) and obligation["kind"] in KINDS, "Unknown obligation kind")
        _need(
            _text(obligation["criterion"]) and type(obligation["requiredForFullVision"]) is bool,
            "Invalid obligation criterion or scope",
        )
        _need(
            isinstance(obligation["sourceRefs"], list) and bool(obligation["sourceRefs"]),
            "Source refs required",
        )
        refs = [ref(value) for value in obligation["sourceRefs"]]
        _need(len(refs) == len(set(refs)), "Duplicate obligation source")
        source_ids[oid] = set(refs)
        _need(bool(links(obligation["waveIds"], waves)), "Obligation needs a wave")
        _need(
            set(_ids(obligation["requiredEvidence"], nonempty=True)) <= EVIDENCE,
            "Unknown evidence kind",
        )
        source_kinds = {spine.node(key)["kind"] for key in refs}
        expected = {
            "requirement": {"requirement"},
            "scenario": {"acceptance"},
            "handoff": {"design", "decision"},
            "risk-verification": {"design", "decision"},
            "review": {"vision", "feature", "design", "decision"},
        }
        _need(
            bool(source_kinds & expected[obligation["kind"]]),
            "Wrong obligation source kind",
            "wave-reference-invalid",
        )

    required = {key: row for key, row in obligations.items() if row["requiredForFullVision"]}
    for rid in spine.ids("requirement"):
        _need(
            rid in coverage
            and any(
                row["kind"] == "requirement"
                and source_ids[key] == {rid}
                and any(rid in waves[w]["requirementIds"] for w in row["waveIds"])
                for key, row in required.items()
            ),
            f"Requirement needs wave and atomic obligation: {rid}",
            "wave-coverage-missing",
        )
    _need(
        set(spine.ids("acceptance"))
        <= {sid for wave in waves.values() for sid in wave["exitScenarioIds"]},
        "Accepted scenario omitted from outcome map",
        "wave-coverage-missing",
    )
    global_id = plan["globalReviewObligationId"]
    _need(
        _text(global_id)
        and global_id in required
        and required[global_id]["kind"] == "review"
        and "independent-review" in required[global_id]["requiredEvidence"]
        and set(required[global_id]["waveIds"]) == set(waves)
        and bool(source_ids[global_id] & set(spine.ids("vision"))),
        "Required full-vision independent review missing",
        "wave-global-review-missing",
    )

    contract_parents = {key: list(values) for key, values in parents.items()}
    for cid, contract in contracts.items():
        _object(
            contract,
            "id producerWaveId consumerWaveIds specRef interface verificationObligationIds",
        )
        _need(_text(contract["producerWaveId"]), "Invalid contract producer")
        links([contract["producerWaveId"]], waves)
        consumers = links(contract["consumerWaveIds"], waves)
        _need(bool(consumers), "Contract consumers required")
        spec = ref(contract["specRef"])
        _need(
            spine.node(spec)["kind"] in {"design", "decision"},
            "Contract needs a design or decision spec",
            "wave-reference-invalid",
        )
        interface = contract["interface"]
        _object(interface, "symbol inputShape outputShape errors invariants")
        _need(
            _text(interface["symbol"])
            and all(isinstance(interface[k], dict) for k in ("inputShape", "outputShape")),
            "Invalid interface shape",
        )
        _ids(interface["errors"])
        _ids(interface["invariants"])
        checks = links(contract["verificationObligationIds"], obligations)
        _need(
            bool(checks)
            and any(
                obligations[key]["kind"] == "handoff"
                and spec in source_ids[key]
                and obligations[key]["requiredForFullVision"]
                and {contract["producerWaveId"], *consumers} <= set(obligations[key]["waveIds"])
                for key in checks
            ),
            "Contract needs a required bound handoff obligation",
            "wave-coverage-missing",
        )
        for consumer in consumers:
            contract_parents[consumer].append(contract["producerWaveId"])
        _need(
            cid in waves[contract["producerWaveId"]]["outgoingContractIds"]
            and all(cid in waves[key]["incomingContractIds"] for key in consumers),
            "Contract endpoints disagree",
            "wave-reference-invalid",
        )
    _dag({key: sorted(set(values)) for key, values in contract_parents.items()})
    _need(
        all(positions[p] < positions[k] for k, deps in contract_parents.items() for p in deps),
        "Order violates contracts",
        "wave-order-invalid",
    )
    for key, wave in waves.items():
        _need(
            all(contracts[c]["producerWaveId"] == key for c in wave["outgoingContractIds"])
            and all(key in contracts[c]["consumerWaveIds"] for c in wave["incomingContractIds"]),
            "Extraneous contract endpoint",
            "wave-reference-invalid",
        )
        for sid in wave["exitScenarioIds"]:
            _need(
                any(
                    row["kind"] == "scenario" and sid in source_ids[oid] and key in row["waveIds"]
                    for oid, row in required.items()
                ),
                "Scenario obligation missing",
                "wave-coverage-missing",
            )
        for rid in wave["riskIds"]:
            _need(
                any(
                    row["kind"] == "risk-verification"
                    and rid in source_ids[oid]
                    and key in row["waveIds"]
                    for oid, row in required.items()
                ),
                "Risk obligation missing",
                "wave-coverage-missing",
            )

    beads, sketches = [], []
    bead_waves = {}
    for wid, detail in by_wave.items():
        _object(
            detail,
            "schema waveId detail beadIds sketches observedInterfaces verificationObligationIds",
        )
        _need(
            detail["schema"] == "dot-wave-detail-v1" and detail["detail"] in ("sketch", "detailed"),
            "Invalid detail schema",
        )
        local_beads = nodes(detail["beadIds"], {"bead"})
        local_sketches = _rows(detail["sketches"])
        _need(
            (detail["detail"] == "sketch" and not local_beads and bool(local_sketches))
            or (detail["detail"] == "detailed" and bool(local_beads) and not local_sketches),
            "Sketches and executable beads must stay distinct",
        )
        for bid in local_beads:
            _need(bid not in bead_waves, "Bead assigned to multiple waves", "wave-duplicate-id")
            bead_waves[bid] = wid
            requirements = {
                key for key in spine.ancestors(bid) if spine.node(key)["kind"] == "requirement"
            }
            _need(
                bool(requirements) and requirements <= set(waves[wid]["requirementIds"]),
                "Bead requirement outside wave",
                "wave-reference-invalid",
            )
        beads.extend(local_beads)
        for sid, sketch in local_sketches.items():
            _object(
                sketch,
                "id title ownershipArea requirementIds expectedContractIds risks "
                "pendingProducerIds detailAfterWaveId",
            )
            _need(
                sid not in spine.ids() and sid not in all_ids and sid not in sketches,
                "Sketch id recycled",
                "wave-duplicate-id",
            )
            _need(
                _text(sketch["title"]) and _text(sketch["ownershipArea"]),
                "Sketch title and area required",
            )
            _need(
                bool(nodes(sketch["requirementIds"], {"requirement"}))
                and set(sketch["requirementIds"]) <= set(waves[wid]["requirementIds"]),
                "Sketch requirement outside wave",
                "wave-reference-invalid",
            )
            links(sketch["expectedContractIds"], contracts)
            _ids(sketch["risks"])
            nodes(sketch["pendingProducerIds"], {"bead"})
            _need(
                _text(sketch["detailAfterWaveId"])
                and sketch["detailAfterWaveId"] in waves
                and positions[sketch["detailAfterWaveId"]] < positions[wid],
                "Sketch must refine after an earlier wave",
                "wave-reference-invalid",
            )
            sketches.append(sid)
        checks = links(detail["verificationObligationIds"], obligations)
        _need(
            all(wid in obligations[oid]["waveIds"] for oid in checks),
            "Detail obligation outside wave",
            "wave-reference-invalid",
        )
        _need(isinstance(detail["observedInterfaces"], list), "Expected interface observations")
        seen = set()
        for observation in detail["observedInterfaces"]:
            _object(observation, "contractId code paths digest")
            cid = observation["contractId"]
            links([cid], contracts)
            _need(
                cid not in seen
                and cid in waves[wid]["incomingContractIds"] + waves[wid]["outgoingContractIds"],
                "Observation outside wave or duplicated",
                "wave-reference-invalid",
            )
            seen.add(cid)
            _code(observation["code"])
            _ids(observation["paths"], nonempty=True)
            _need(
                all(_relative(p) for p in observation["paths"]) and _sha(observation["digest"]),
                "Invalid observed interface paths/digest",
            )
    for bid, wid in bead_waves.items():
        for dep in spine.node(bid)["fields"]["dependsOn"]:
            _need(
                dep in bead_waves and positions[bead_waves[dep]] <= positions[wid],
                "Actual bead producer missing or in later wave",
                "wave-reference-invalid",
            )

    lock_state = "unconfirmed"
    if product_lock is not None:
        _object(product_lock, "sourceRef digest")
        _need(
            _text(product_lock["sourceRef"]) and _sha(product_lock["digest"]),
            "Invalid product lock",
        )
        digest = product_digest(spine, plan, detail_values)
        lock_state = "current" if product_lock["digest"] == digest else "stale"
    return {
        "waveMap": plan,
        "waveDetails": detail_values,
        "obligationIds": sorted(obligations),
        "sketchedItemIds": sorted(sketches),
        "detailedBeadIds": sorted(beads),
        "lockState": lock_state,
    }


def product_digest(spine: Spine, plan: dict, detail_values: list[dict]) -> str:
    """Export the existing protected-product projection without granting approval."""
    contracts = {row["id"]: row for row in plan["contracts"]}
    waves = {row["id"]: row for row in plan["waves"]}
    referenced = {ref["id"] for row in plan["obligations"] for ref in row["sourceRefs"]}
    referenced.update(contract["specRef"]["id"] for contract in contracts.values())
    for wave in waves.values():
        for key in (
            "requirementIds",
            "featureIds",
            "storyIds",
            "designIds",
            "riskIds",
            "exitScenarioIds",
        ):
            referenced.update(wave[key])
    map_body = _canonical(plan)
    owners = {
        key for key in spine.ids("decision") if _canonical(spine.node(key)["fields"]) == map_body
    }
    detail_bodies = {_canonical(value) for value in detail_values}
    revisions = {}
    for key in spine.ids():
        value = spine.node(key)
        if value["kind"] in {"bead", "test"}:
            continue
        is_carrier = (
            value["kind"] == "design"
            and key not in referenced
            and not spine.children(key)
            and len(value["parents"]) == 1
            and value["parents"][0] in owners
            and _canonical(value["fields"]) in detail_bodies
        )
        if is_carrier:
            value["fields"] = {}
            revisions[key] = _hash(_canonical(value))
        else:
            revisions[key] = spine.revision(key)
    digest = _hash(_canonical({"nodeRevisions": revisions, "waveMap": plan}))
    return digest


def _bound(envelope: object, snapshot: dict) -> bool:
    if not isinstance(envelope, dict) or not _text(envelope.get("sourceRef")):
        return False
    pub, lock = snapshot.get("publicationRevision"), snapshot.get("productLockRevision")
    revisions = snapshot.get("nodeRevisions")
    return bool(
        _sha(pub)
        and _sha(lock)
        and isinstance(revisions, dict)
        and revisions
        and all(_text(k) and _sha(v) for k, v in revisions.items())
        and envelope.get("binding")
        == {
            "publicationRevision": pub,
            "productLockRevision": lock,
            "nodeRevisions": revisions,
            "waveMapRevision": _hash(_canonical(snapshot["waveMap"])),
        }
        and isinstance(envelope.get("record"), dict)
    )


def _claim_passes(claim: dict, run: dict, snapshot: dict) -> bool:
    """Require an actual canonical claim and its observed integrated producer."""
    bead = claim.get("bead")
    if not _text(bead) or claim.get("passed") is not True or claim not in run.get("claims", []):
        return False
    body = {key: value for key, value in claim.items() if key != "claimId"}
    # dispatch._claim uses this exact JSON convention (ASCII escaping by default).
    import hashlib
    import json

    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if claim.get("claimId") != f"{bead}:{digest}":
        return False
    if not (
        all(_sha(claim.get(key)) for key in ("verificationIdentity", "sourceIdentity"))
        and all(
            _sha(claim.get(key), 40) or _sha(claim.get(key))
            for key in ("candidateCommit", "candidateTree")
        )
        and isinstance(claim.get("dependencyIdentities"), dict)
        and all(_sha(v) for v in claim["dependencyIdentities"].values())
        and _text(claim.get("at"))
        and _text(claim.get("proofModule"))
        and _string_list(claim.get("requirementIds"))
    ):
        return False
    if bead == "integration":
        validation = run.get("integrationValidation")
        return (
            isinstance(validation, dict)
            and validation.get("passed") is True
            and _string_list(validation.get("command"))
            and bool(validation["command"])
        )
    row = run.get("beads", {}).get(bead)
    if not isinstance(row, dict):
        return False
    integrated = row.get("integrated")
    return bool(
        row.get("isMocked") is False
        and row.get("didRunProvider") is True
        and row.get("didRunIntegratedValidation") is True
        and row.get("planRevision") == snapshot["publicationRevision"]
        and isinstance(integrated, dict)
        and integrated.get("claim") == claim
        and integrated.get("integrationCommit") == claim["candidateCommit"]
        and isinstance(integrated.get("validation"), dict)
        and integrated["validation"].get("passed") is True
        and _string_list(integrated["validation"].get("command"))
        and bool(integrated["validation"]["command"])
        and all(
            isinstance(row.get(key), list)
            and all(
                isinstance(ref, dict)
                and _text(ref.get("id"))
                and snapshot["nodeRevisions"].get(ref.get("id")) == ref.get("revision")
                for ref in row[key]
            )
            for key in ("requirementRefs", "specRefs")
        )
    )


def accounting(
    snapshot: dict, runs: list[dict], claims: list[dict], acceptance: list[dict]
) -> WaveAccountingV1:
    """Count unique global obligations; partial or unavailable proof stays outstanding."""
    _need(isinstance(snapshot, dict), "Expected accounting snapshot")
    try:
        _canonical([snapshot, runs, claims, acceptance])
    except (TypeError, ValueError, OverflowError) as error:
        raise WaveError("wave-schema-invalid", "Expected finite JSON values") from error
    plan = snapshot.get("waveMap")
    _need(isinstance(plan, dict), "Accounting needs a validated wave map")
    obligations = _rows(plan.get("obligations"))
    details = _rows(snapshot.get("waveDetails", []), "waveId")
    runs = runs if isinstance(runs, list) else []
    claims = claims if isinstance(claims, list) else []
    acceptance = acceptance if isinstance(acceptance, list) else []
    current_runs = [
        r["record"]
        for r in runs
        if _bound(r, snapshot)
        and r["record"].get("schema") == "codex-harness/plan-run-v1"
        and r["record"].get("planRevision") == snapshot.get("publicationRevision")
        and r["record"].get("isMocked") is False
        and r["record"].get("didExecuteRealProvider") is True
        and isinstance(r["record"].get("claims"), list)
        and isinstance(r["record"].get("beads"), dict)
    ]
    evidence = {key: {} for key in obligations}
    implemented = set()
    for item in claims:
        if not _bound(item, snapshot) or not _string_list(item.get("obligationIds")):
            continue
        claim, kind = item["record"], item.get("evidenceKind")
        if kind not in ("source-test", "product-integration"):
            continue
        if (claim.get("bead") == "integration") != (kind == "product-integration"):
            continue
        if not any(_claim_passes(claim, run, snapshot) for run in current_runs):
            continue
        if kind == "source-test":
            implemented.add(claim["bead"])
        for oid in item.get("obligationIds", []):
            if oid in obligations and set(
                ref["id"] for ref in obligations[oid]["sourceRefs"]
            ) <= set(claim.get("requirementIds", [])):
                evidence[oid].setdefault(kind, []).append(item["sourceRef"])
    sampled = set()
    for item in acceptance:
        if not _bound(item, snapshot):
            continue
        record, kind = item["record"], item.get("evidenceKind")
        if kind == "readiness":
            if (
                record.get("manifestRevision") == snapshot.get("publicationRevision")
                and _text(record.get("id"))
                and _text(record.get("batchId"))
                and _sha(record.get("packetHash"))
                and isinstance(record.get("reviewer"), dict)
            ):
                selected = record.get("selectedBeads")
                if (
                    isinstance(selected, list)
                    and all(_text(b) for b in selected)
                    and record.get("beadId") in selected
                ):
                    sampled.add(record["beadId"])
            continue
        if (
            kind not in ("oracle-run", "clean-state-product")
            or item.get("isMocked") is not False
            or not _string_list(item.get("obligationIds"))
        ):
            continue
        subject = "oracle" if kind == "oracle-run" else "product"
        results = record.get("results")
        if not (
            record.get("subject") == subject
            and record.get("planRevision") == snapshot.get("publicationRevision")
            and record.get("revisionAlgorithm") == "tree-bytes-v2"
            and _sha(record.get("revision"))
            and _sha(record.get("scenarioRevision"))
            and _text(record.get("id"))
            and record.get("passed") is True
            and isinstance(results, list)
            and results
        ):
            continue
        for oid in item.get("obligationIds", []):
            if oid not in obligations:
                continue
            refs = {ref["id"] for ref in obligations[oid]["sourceRefs"]}
            observed = {
                r.get("id")
                for r in results
                if isinstance(r, dict)
                and _text(r.get("id"))
                and r.get("passed") is True
                and isinstance(r.get("steps"), list)
                and r["steps"]
                and all(
                    isinstance(s, dict) and _text(s.get("command")) and s.get("passed") is True
                    for s in r["steps"]
                )
            }
            if refs <= observed:
                evidence[oid].setdefault(kind, []).append(item["sourceRef"])

    all_beads = {
        bid
        for d in details.values()
        if d.get("detail") == "detailed"
        for bid in d.get("beadIds", [])
    }
    sketches = {s["id"] for d in details.values() for s in d.get("sketches", [])}
    selected_waves = snapshot.get("selectedWaveIds", [])
    selected_waves = selected_waves if _string_list(selected_waves) else []
    selected = {
        bid
        for wid, d in details.items()
        if wid in selected_waves and d.get("detail") == "detailed"
        for bid in d.get("beadIds", [])
    }
    dependencies = snapshot.get("beadDependencies")
    published = _sha(snapshot.get("publicationRevision")) and _sha(
        snapshot.get("productLockRevision")
    )
    revisions = snapshot.get("nodeRevisions")
    revisions = revisions if isinstance(revisions, dict) else {}
    known_dependencies = (
        isinstance(dependencies, dict)
        and all(
            _text(bid) and _string_list(deps) and len(deps) == len(set(deps))
            for bid, deps in dependencies.items()
        )
        and all(
            isinstance(dependencies.get(bid), list)
            and all(
                _text(dep) and dep in dependencies and _sha(revisions.get(dep))
                for dep in dependencies[bid]
            )
            and _sha(revisions.get(bid))
            for bid in all_beads
        )
    )
    if known_dependencies:
        try:
            _dag(dependencies)
        except WaveError:
            known_dependencies = False
    eligible, pending, unknown = set(), {}, set()
    for bid in selected:
        if not published or not known_dependencies:
            unknown.add(bid)
        else:
            missing = set(dependencies[bid]) - implemented
            if missing:
                pending[bid] = missing
            else:
                eligible.add(bid)
    sampled &= selected
    states = []
    for oid, obligation in obligations.items():
        found = evidence[oid]
        complete = set(obligation["requiredEvidence"]) <= found.keys()
        draft_wave = any(
            details.get(wid, {}).get("detail") != "detailed" for wid in obligation["waveIds"]
        )
        related = {
            bid for wid in obligation["waveIds"] for bid in details.get(wid, {}).get("beadIds", [])
        }
        refs_current = all(
            revisions.get(ref["id"]) == ref["revision"] for ref in obligation["sourceRefs"]
        )
        if not published or not refs_current:
            state, reason = "unknown", "Current publication, lock or intent binding unavailable"
        elif draft_wave:
            state, reason = "outstanding", "Later sketch retains unsatisfied obligation"
        elif complete:
            state, reason = (
                "completed-real",
                "All required evidence kinds have current real observations",
            )
        elif related & pending.keys():
            state, reason = "pending-producers", "Real bound producer evidence missing"
        elif found:
            state, reason = (
                "outstanding",
                "Partial evidence; remaining required kinds are unsatisfied",
            )
        else:
            state, reason = "unknown", "Required evidence unavailable or unsupported"
        states.append(
            {
                "id": oid,
                "state": state,
                "evidenceRefs": sorted({ref for values in found.values() for ref in values}),
                "reason": reason,
            }
        )
    required_states = [s for s in states if obligations[s["id"]]["requiredForFullVision"]]
    completed = sum(s["state"] == "completed-real" for s in required_states)
    return {
        "publicationRevision": snapshot.get("publicationRevision"),
        "productLockRevision": snapshot.get("productLockRevision"),
        "fullVision": {
            "totalObligations": len(required_states),
            "completedRealObligations": completed,
            "outstandingObligations": len(required_states) - completed,
            "deferredObligations": 0,
            "blockedObligations": 0,
            "unknownObligations": sum(s["state"] == "unknown" for s in required_states),
            "acceptedHuman": False,
            "acceptedProxy": False,
        },
        "readiness": {
            "detailedBeads": len(all_beads),
            "sketchedItems": len(sketches),
            "eligibleBeadIds": sorted(eligible),
            "eligibleBeadCount": len(eligible),
            "sampledBeadIds": sorted(sampled),
            "sampledBeadCount": len(sampled),
            "readySampledCount": 0,
            "unreviewedEligibleCount": len(eligible - sampled),
            "readyEligibleCount": 0,
            "dispatchableNowCount": 0,
            "pendingProducerBeadCount": len(pending),
            "distinctPendingProducerCount": len(set().union(*pending.values())) if pending else 0,
            "blockedBeadCount": 0,
            "unknownBeadCount": len(unknown | eligible),
        },
        "obligationStates": states,
        "historicalMeasurements": _history(snapshot.get("historicalMeasurements", [])),
    }


def _history(records: object) -> list[dict]:
    _need(isinstance(records, list), "Expected historical measurements")
    result = []
    for record in records:
        _need(
            isinstance(record, dict) and _text(record.get("sourceRef")),
            "Historical source required",
        )
        _need(
            all(
                type(record.get(key)) is int and record[key] >= 0
                for key in ("ready", "sampled", "blockers")
            ),
            "Historical counts unavailable",
        )
        _need(
            record["ready"] <= record["sampled"]
            and record.get("evidenceClass") in ("reported-history", "bound-observation"),
            "Invalid historical measurement",
        )
        _need(
            record.get("publicationRevision") is None or _sha(record["publicationRevision"]),
            "Invalid historical publication",
        )
        _need(
            record.get("observedAt") is None or _text(record["observedAt"]),
            "Invalid historical time",
        )
        result.append(
            {
                "sourceRef": record["sourceRef"],
                "ready": record["ready"],
                "sampled": record["sampled"],
                "blockers": record["blockers"],
                "evidenceClass": record["evidenceClass"],
                "publicationRevision": record.get("publicationRevision"),
                "observedAt": record.get("observedAt"),
                "status": "historical-not-remeasured",
            }
        )
    return result
