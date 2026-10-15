"""Value-boundary proofs with injected observations, never provider discovery."""

import hashlib
import json
from copy import deepcopy

import pytest

from codex_harness.core.intake import (
    canonical_digest,
    propose,
    validate_catalog,
    validate_selection,
)
from codex_harness.core.spine import Spine

HASH = "a" * 64


def authority(origin="user"):
    return {
        "approvalId": "approval-1",
        "eventId": 7,
        "origin": origin,
        "statement": "Select these exact reviewer tuples for this request.",
        "sourceRef": "conversation:7" if origin == "user" else "delegation:DOT:scope-1",
        "authorizedScopeDigest": HASH,
    }


def request():
    return {
        "requestKind": "fix",
        "sizeEstimate": "S",
        "delta": {
            "summary": "Repair displayed units",
            "nodeIds": ["R1"],
            "outcome": "Units display correctly",
            "successCriteria": ["Unit label matches value"],
            "ownershipAreas": ["display"],
            "interfaceIds": [],
        },
        "ambiguity": {"level": "low", "reasons": []},
        "consequence": {"level": "ordinary", "reversibility": "Revert source change", "risks": []},
        "dependencyBreadth": {"producerIds": ["display"], "crossWaveContracts": []},
        "constraints": {
            "runtime": ["prepared Python"],
            "security": ["normal protections"],
            "spend": "no provider calls",
            "frictionBudget": "one focused check",
        },
        "rationale": "One bounded presentation repair",
        "assumptions": [],
        "expectedSupervision": "Review changed behavior",
        "nextGate": "focused verification",
        "reviewerDecisionId": "RS1",
        "confirmation": authority(),
        "supersedes": None,
        "adaptation": None,
    }


def baseline(recipe="baseline-amendment"):
    # Use the real canonical producer's revisions, including a non-delta obligation.
    spine = Spine(
        {
            "schema": "codex-harness/spine-v1",
            "name": "bounded repair",
            "nodes": [
                {
                    "id": "V1",
                    "kind": "vision",
                    "title": "Display",
                    "text": "Readable units",
                    "parents": [],
                    "fields": {},
                },
            ],
        }
    )
    return {
        "publishedRevision": spine.revision("V1"),
        "spineRevision": spine.plan_revision(),
        "obligationIds": ["O-label", "O-full-vision"],
        "recipe": recipe,
    }


def obligations():
    return [
        {"id": identity, "requiredForFullVision": True} for identity in ("O-label", "O-full-vision")
    ]


def observation():
    return {
        "schema": "dot-reviewer-catalog-v1",
        "id": "RC-12345678-1234-4234-8234-123456789abc",
        "observedAt": "2026-10-15T10:00:00Z",
        "providers": [
            {
                "provider": "fixture-provider",
                "executable": "C:/prepared/reviewer.exe",
                "executableDigest": HASH,
                "version": "fixture-1",
                "installed": True,
                "authentication": "available",
                "discovery": "observed",
                "source": {
                    "kind": "supported-list",
                    "reference": "fixture:exact-model-list",
                    "digest": HASH,
                },
                "reason": "Injected source-only observation",
                "choices": [
                    {
                        "model": "fixture-model-1",
                        "effort": {"kind": "named", "value": "high"},
                        "availability": "available",
                        "reviewPathSupported": True,
                        "reason": "Fixture reports entitlement",
                    }
                ],
            }
        ],
    }


def selection(catalog, count=1):
    provider = catalog["providers"][0]
    choice = provider["choices"][0]
    return {
        "schema": "dot-reviewer-selection-v1",
        "catalogId": catalog["id"],
        "catalogDigest": catalog["digest"],
        "reviewers": [
            {
                "name": f"reviewer-{i}",
                "provider": provider["provider"],
                **{key: deepcopy(choice[key]) for key in ("choiceId", "model", "effort")},
            }
            for i in range(count)
        ],
        "stages": ["plan", "readiness"],
        "assurance": {
            "independentRequired": True,
            "minIndependentReviewers": count,
            "crossProviderRequired": False,
            "basisRefs": ["policy:fixture"],
        },
        "confirmation": authority(),
        "recommendation": {
            "choiceIds": [choice["choiceId"]],
            "reason": "Exact injected tuple",
            "catchRateEvidenceRefs": [],
        },
    }


def sessions(**reviewers):
    return {"implementationSessionIds": ["author-session"], "reviewerSessionIds": reviewers}


def test_small_baseline_from_real_spine_preserves_full_scope_and_copies():
    facts, base, current = request(), baseline(), obligations()
    before = deepcopy((facts, base, current))
    decision = propose(facts, base, current)
    assert decision["recipe"] == "baseline-amendment"
    assert decision["baseline"] == {
        key: base[key] for key in ("publishedRevision", "spineRevision")
    }
    assert set(decision) == set(facts) | {"schema", "recipe", "baseline"}
    decision["delta"]["nodeIds"].append("mutated")
    decision["confirmation"]["statement"] = "mutated"
    assert (facts, base, current) == before
    with pytest.raises(ValueError, match="intake-obligation-loss.*O-full-vision"):
        propose(facts, base, current[:1])


@pytest.mark.parametrize(
    ("area", "value", "expected"),
    [
        (
            "consequence",
            {
                "level": "hard-invariant",
                "reversibility": "Restore transaction",
                "risks": ["Lost atomicity"],
            },
            "coherent-plan",
        ),
        (
            "consequence",
            {
                "level": "public-boundary",
                "reversibility": "Recall publication",
                "risks": ["Public release"],
            },
            "coherent-plan",
        ),
        ("ambiguity", {"level": "material", "reasons": ["Ownership unresolved"]}, "coherent-plan"),
        ("ambiguity", {"level": "high", "reasons": ["Outcomes unresolved"]}, "outcome-waves"),
        (
            "dependencyBreadth",
            {"producerIds": ["P2", "P1"], "crossWaveContracts": []},
            "outcome-waves",
        ),
    ],
)
def test_small_size_never_reduces_depth(area, value, expected):
    facts = request()
    facts[area] = value
    assert propose(facts, baseline(), obligations())["recipe"] == expected
    facts["recipe"] = "baseline-amendment"
    with pytest.raises(ValueError, match="intake-recipe-incompatible"):
        propose(facts, baseline(), obligations())


def test_missing_facts_and_no_baseline_do_not_guess_amendment():
    facts = request()
    assert propose(facts, None, obligations())["recipe"] == "coherent-plan"
    facts["sizeEstimate"] = "L"
    assert propose(facts, baseline(), obligations())["recipe"] == "baseline-amendment"
    del facts["consequence"]
    with pytest.raises(ValueError, match="missing=\\['consequence'\\]"):
        propose(facts, None, obligations())


def test_downgrade_requires_reason_evidence_and_supplied_authority():
    facts, base = request(), baseline("outcome-waves")
    with pytest.raises(ValueError, match="intake-downgrade-unjustified"):
        propose(facts, base, obligations())
    facts["adaptation"] = {
        "previousRecipe": "outcome-waves",
        "reason": "Producer work is integrated",
        "affectedObligationIds": ["O-label"],
        "evidenceRefs": ["integration:1"],
        "authority": authority("standing-delegation"),
    }
    assert propose(facts, base, obligations())["adaptation"] == facts["adaptation"]
    facts["adaptation"]["evidenceRefs"] = []
    with pytest.raises(ValueError, match="adaptation.evidenceRefs"):
        propose(facts, base, obligations())


def test_catalog_canonical_hashes_bind_identity_without_recursion_and_copy():
    raw = observation()
    catalog = validate_catalog(raw)
    provider = raw["providers"][0]
    binding = {
        key: provider[key] for key in ("provider", "executable", "executableDigest", "version")
    }
    binding.update(model="fixture-model-1", effort={"kind": "named", "value": "high"})
    expected = hashlib.sha256(
        json.dumps(
            binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    assert catalog["providers"][0]["choices"][0]["choiceId"] == expected
    assert catalog["digest"] == canonical_digest(
        {key: value for key, value in catalog.items() if key != "digest"}
    )
    assert validate_catalog(catalog) == catalog
    catalog["providers"][0]["choices"][0]["model"] = "changed"
    assert raw["providers"][0]["choices"][0]["model"] == "fixture-model-1"
    with pytest.raises(ValueError, match="value-json-invalid"):
        canonical_digest({"notFinite": float("nan")})


@pytest.mark.parametrize("field", ["version", "executableDigest", "executable"])
def test_choice_hash_changes_with_observed_executable_identity(field):
    original = validate_catalog(observation())["providers"][0]["choices"][0]["choiceId"]
    raw = observation()
    raw["providers"][0][field] = {
        "version": "fixture-2",
        "executableDigest": "b" * 64,
        "executable": "C:/other/reviewer.exe",
    }[field]
    assert validate_catalog(raw)["providers"][0]["choices"][0]["choiceId"] != original


def test_forged_hashes_stale_catalog_and_tuple_mismatch_are_refusals():
    catalog = validate_catalog(observation())
    forged = deepcopy(catalog)
    forged["providers"][0]["choices"][0]["choiceId"] = HASH
    with pytest.raises(ValueError, match="reviewer-choice-hash-mismatch"):
        validate_catalog(forged)
    forged = deepcopy(catalog)
    forged["digest"] = HASH
    with pytest.raises(ValueError, match="reviewer-catalog-hash-mismatch"):
        validate_catalog(forged)
    chosen = selection(catalog)
    chosen["catalogDigest"] = HASH
    with pytest.raises(ValueError, match="reviewer-catalog-stale"):
        validate_selection(catalog, chosen, sessions())
    chosen = selection(catalog)
    chosen["reviewers"][0]["effort"]["value"] = "medium"
    with pytest.raises(ValueError, match="reviewer-tuple-mismatch"):
        validate_selection(catalog, chosen, sessions())
    chosen = selection(catalog)
    chosen["reviewers"][0]["choiceId"] = HASH
    with pytest.raises(ValueError, match="reviewer-choice-absent"):
        validate_selection(catalog, chosen, sessions())


def test_unknown_unavailable_and_unsupported_remain_unmet_not_fallback():
    raw = observation()
    provider = raw["providers"][0]
    provider.update(authentication="unknown", discovery="blocked", source=None)
    with pytest.raises(ValueError, match="reviewer-availability-unproven"):
        validate_catalog(raw)
    provider["choices"][0].update(availability="unknown", reviewPathSupported=False)
    catalog = validate_catalog(raw)
    report = validate_selection(catalog, selection(catalog), sessions(**{"reviewer-0": "other"}))
    assert report["unmetRequirements"] == [
        "reviewer-availability-unknown: reviewer-0",
        "reviewer-path-unsupported: reviewer-0",
    ]
    provider["choices"][0]["availability"] = "unavailable"
    catalog = validate_catalog(raw)
    assert (
        "reviewer-availability-unavailable: reviewer-0"
        in validate_selection(catalog, selection(catalog), sessions(**{"reviewer-0": "other"}))[
            "unmetRequirements"
        ]
    )


def test_effort_absence_requires_explicit_unsupported_and_aliases_are_refused():
    raw = observation()
    choice = raw["providers"][0]["choices"][0]
    del choice["effort"]
    with pytest.raises(ValueError, match="effort"):
        validate_catalog(raw)
    choice["effort"] = {"kind": "unsupported"}
    catalog = validate_catalog(raw)
    chosen = selection(catalog)
    assert (
        validate_selection(catalog, chosen, sessions(**{"reviewer-0": "other"}))[
            "unmetRequirements"
        ]
        == []
    )
    chosen["reviewers"][0]["effort"] = {"kind": "named", "value": "high"}
    with pytest.raises(ValueError, match="reviewer-tuple-mismatch"):
        validate_selection(catalog, chosen, sessions())
    choice["model"] = "fixture-latest"
    with pytest.raises(ValueError, match="reviewer-model-unresolved"):
        validate_catalog(raw)


@pytest.mark.parametrize("origin", ["user", "standing-delegation"])
def test_direct_and_delegated_authority_are_preserved_not_created(origin):
    facts = request()
    facts["confirmation"] = authority(origin)
    assert propose(facts, baseline(), obligations())["confirmation"] == authority(origin)
    catalog = validate_catalog(observation())
    chosen = selection(catalog)
    chosen["confirmation"] = authority(origin)
    before = deepcopy(chosen)
    report = validate_selection(catalog, chosen, sessions(**{"reviewer-0": "independent"}))
    assert report["selection"]["confirmation"] == authority(origin)
    assert report["unmetRequirements"] == []
    report["selection"]["confirmation"]["statement"] = "changed"
    assert chosen == before
    chosen["confirmation"] = {"chosen": True, "by": "user"}
    with pytest.raises(ValueError, match="value-shape-invalid"):
        validate_selection(catalog, chosen, sessions())


def test_unconfirmed_proposals_remain_drafts():
    facts = request()
    facts["confirmation"] = None
    assert propose(facts, baseline(), obligations())["confirmation"] is None
    catalog = validate_catalog(observation())
    chosen = selection(catalog)
    chosen["confirmation"] = None
    assert validate_selection(catalog, chosen, sessions(**{"reviewer-0": "other"}))[
        "unmetRequirements"
    ] == ["reviewer-confirmation-required: proposal is unconfirmed"]


def test_independence_uses_distinct_non_author_sessions_and_diversity_is_separate():
    catalog = validate_catalog(observation())
    chosen = selection(catalog, 2)
    independent = sessions(**{"reviewer-0": "blind-A", "reviewer-1": "blind-B"})
    report = validate_selection(catalog, chosen, independent)
    assert set(report) == {"selection", "independence", "diversity", "unmetRequirements"}
    assert report["independence"] == {
        "required": 2,
        "qualifyingReviewerNames": ["reviewer-0", "reviewer-1"],
        "unmet": [],
    }
    assert report["diversity"] == "single-provider/same-model"
    assert report["unmetRequirements"] == []
    for identities, reason in [
        ({}, "reviewer-independence-unknown"),
        ({"reviewer-0": "author-session", "reviewer-1": "blind-B"}, "reviewer-is-author"),
        ({"reviewer-0": "same", "reviewer-1": "same"}, "reviewer-session-shared"),
    ]:
        report = validate_selection(catalog, chosen, sessions(**identities))
        assert any(item.startswith(reason) for item in report["unmetRequirements"])
        assert len(report["independence"]["qualifyingReviewerNames"]) < 2
    chosen["assurance"]["crossProviderRequired"] = True
    assert validate_selection(catalog, chosen, independent)["unmetRequirements"] == [
        "reviewer-cross-provider-required: fewer than two selected providers"
    ]


def test_cross_provider_diversity_cannot_make_author_review_independent():
    raw = observation()
    second = deepcopy(raw["providers"][0])
    second["provider"] = "second-fixture"
    raw["providers"].append(second)
    catalog = validate_catalog(raw)
    chosen = selection(catalog, 2)
    choice = catalog["providers"][1]["choices"][0]
    chosen["reviewers"][1].update(provider="second-fixture", choiceId=choice["choiceId"])
    chosen["assurance"]["crossProviderRequired"] = True
    report = validate_selection(
        catalog, chosen, sessions(**{"reviewer-0": "author-session", "reviewer-1": "blind"})
    )
    assert report["diversity"] == "cross-provider"
    assert "reviewer-is-author: reviewer-0" in report["unmetRequirements"]
