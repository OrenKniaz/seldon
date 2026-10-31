"""Value-boundary proofs, plus explicit offline observations without provider launches."""

from copy import deepcopy

from tests.python.core.test_project_init import offline_cache  # noqa: F401

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
