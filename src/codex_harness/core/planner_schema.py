"""Read-only help for existing planner contracts; validation stays in MCP/Spine."""

from copy import deepcopy

from codex_harness.core.spine import ALLOWED_PARENTS

FIELD_RULES = {
    "bead": (
        "fields.ownedFiles: nonempty array of nonblank strings; "
        "fields.proofModule: nonblank string; fields.dependsOn and fields.lessons: "
        "arrays of unique existing bead/decision IDs respectively (empty allowed). "
        "Optional fields.provider: nonblank string. Dependencies must be acyclic."
    ),
    "test": "fields.module: nonblank string; fields.level: unit or e2e.",
}


TYPED_COMMON = {
    "expected_spine_revision": "Exact current spine plan_revision, refreshed from "
    "harness_plan_status; stale revisions refuse atomically.",
    "discoveryRoute": "harness_preflight is supported executable discovery only: "
    "authentication/effective protections remain unknown and model entitlement "
    "is not established. Catalog/choice tools record supplied observations; no "
    "supported public model-catalog producer is exposed here. Report "
    "reviewer-catalog-producer-unavailable when genuine observations cannot be "
    "acquired.",
    "state": "DRAFT/HELD; no approval, entitlement, native execution authorization, phase "
    "completion or provider proof is created by documentation or recording.",
    "nextTool": {"name": "harness_preflight", "arguments": {}},
}

TYPED_CONTRACTS = {
    "harness_intake": {
        "node": "For node-bearing intake/choice tools only: id, kind=decision, "
        "title, text, parents, fields. Existing plan IDs/parent kinds and "
        "normal Spine validation still apply.",
        "confirmation": "null for an unconfirmed draft. Non-null accepts only an "
        "EXISTING attributable record with "
        "approvalId/eventId/origin/statement/sourceRef/authorizedScopeDigest; "
        "never invent these values.",
        "authorityShape": "approvalId/statement/sourceRef: nonblank; eventId: "
        "positive nonboolean integer; origin: "
        "user|standing-delegation; authorizedScopeDigest: "
        "lowercase SHA256. This describes retained provenance, "
        "not a producer or authentication.",
        "confirmationRoute": "Typed intake/reviewer confirmation acquisition is "
        "not implemented by these public tools. Keep "
        "confirmation null and report owner blocker "
        "typed-confirmation-producer-unavailable. "
        "harness_record_confirmation only serves align and "
        "vision-lock; it cannot confirm these typed records.",
        "arguments": "node, expected_spine_revision required; baseline optional "
        "null or exact {publishedRevision, spineRevision} of an "
        "existing published snapshot. Store derives baseline "
        "recipe/obligations.",
        "fields.required": "requestKind sizeEstimate delta ambiguity consequence "
        "dependencyBreadth constraints rationale assumptions "
        "expectedSupervision nextGate reviewerDecisionId "
        "confirmation supersedes adaptation",
        "fields.optional": "schema=dot-intake-v1; "
        "recipe=baseline-amendment|coherent-plan|outcome-waves "
        "(must meet conservative fact-derived minimum). "
        "baseline is derived, not a request.fields key.",
        "requestKind": "new|feature|fix|refactor|continue",
        "sizeEstimate": "S|M|L",
        "delta": "Exact keys summary,outcome: nonblank strings; "
        "nodeIds,interfaceIds: arrays of strings; "
        "successCriteria,ownershipAreas: nonempty string arrays.",
        "ambiguity": "Exact keys level=low|material|high, reasons=string array "
        "(nonempty unless low).",
        "consequence": "Exact keys level=ordinary|hard-invariant|public-boundary, "
        "reversibility=nonblank, risks=string array (nonempty "
        "unless ordinary).",
        "dependencyBreadth": "Exact keys producerIds,crossWaveContracts: string arrays.",
        "constraints": "Exact keys runtime,security: string arrays; "
        "spend,frictionBudget: nonblank strings.",
        "remainingFields": "rationale,expectedSupervision,nextGate,reviewerDecisionId: "
        "nonblank strings; assumptions:string array; "
        "confirmation:null for draft; supersedes:null or "
        "exact{id:nonblank,revision:lowercaseSHA256}; "
        "adaptation:null for a new draft.",
        "adaptation": "If non-null exact "
        "previousRecipe,reason,affectedObligationIds,evidenceRefs,authority; "
        "previousRecipe must match resolved baseline, reason "
        "nonblank, affectedObligationIds known string IDs, "
        "evidenceRefs nonempty strings, authority existing non-null "
        "authorityShape. Downgrade needs adaptation; never "
        "manufacture authority to satisfy it.",
    },
    "harness_reviewer_catalog": {
        "arguments": "catalog, expected_spine_revision required.",
        "catalog": "Exact "
        "schema=dot-reviewer-catalog-v1,id=RC-<UUID>,observedAt=timezone-aware "
        "ISO timestamp,providers=array; optional "
        "digest=derived lowercase SHA256 (supplied values "
        "must match). IDs/timestamps identify a supplied "
        "observation, not proof that discovery occurred.",
        "provider": "Exact "
        "provider,executable,executableDigest,version,installed,authentication,discovery,source,choices,reason.",
        "provider.identity": "provider/reason:nonblank; executable:null "
        "or absolute path; executableDigest:null or "
        "lowercaseSHA256; version:null or nonblank; "
        "installed:null or boolean.",
        "provider.observations": "authentication=available|unavailable|unknown; "
        "discovery=observed|unsupported|blocked|failed|unknown; "
        "source=null or "
        "exact{kind:supported-list|provider-local-metadata,"
        "reference:nonblank,digest:lowercaseSHA256}; "
        "choices=array.",
        "choice": "Exact "
        "model,effort,availability,reviewPathSupported,reason; "
        "optional derived choiceId. model must be exact "
        "supplied model, never default/latest/auto/*-latest. "
        "effort exact{kind:named,value:nonblank} or "
        "{kind:unsupported}; "
        "availability=available|unavailable|unknown; "
        "reviewPathSupported:boolean; reason:nonblank.",
        "derivedHashes": "choiceId hashes "
        "provider,executable,executableDigest,version,model,effort. "
        "digest hashes normalized catalog excluding "
        "digest. Both computed by owning validator if "
        "omitted; do not substitute dummy hashes.",
        "availableRule": "availability=available requires actual "
        "installed/executable/hash/version, "
        "authentication available, discovery observed "
        "and a source. Preserve unknown if evidence is "
        "absent; providers=[] or choices=[] claims no "
        "available reviewer.",
    },
    "harness_reviewer_choice": {
        "node": "For node-bearing intake/choice tools only: id, "
        "kind=decision, title, text, parents, fields. Existing "
        "plan IDs/parent kinds and normal Spine validation still "
        "apply.",
        "confirmation": "null for an unconfirmed draft. Non-null accepts "
        "only an EXISTING attributable record with "
        "approvalId/eventId/origin/statement/sourceRef/authorizedScopeDigest; "
        "never invent these values.",
        "authorityShape": "approvalId/statement/sourceRef: nonblank; "
        "eventId: positive nonboolean integer; origin: "
        "user|standing-delegation; "
        "authorizedScopeDigest: lowercase SHA256. This "
        "describes retained provenance, not a producer "
        "or authentication.",
        "confirmationRoute": "Typed intake/reviewer confirmation "
        "acquisition is not implemented by these "
        "public tools. Keep confirmation null and "
        "report owner blocker "
        "typed-confirmation-producer-unavailable. "
        "harness_record_confirmation only serves "
        "align and vision-lock; it cannot confirm "
        "these typed records.",
        "arguments": "node,expected_spine_revision required; optional "
        "author_sessions defaults to "
        "{implementationSessionIds:[],reviewerSessionIds:{}}.",
        "fields.required": "schema catalogId catalogDigest reviewers "
        "stages assurance confirmation recommendation",
        "binding": "schema=dot-reviewer-selection-v1; catalogId and "
        "lowercaseSHA256 catalogDigest must bind exact current "
        "recorded catalog. Reviewer choiceId must come from "
        "its normalized catalog, not an invented digest.",
        "reviewers": "Nonempty array of "
        "exact{name,provider,choiceId,model,effort}; "
        "name/provider nonblank; choiceId lowercaseSHA256 of "
        "an existing choice; exact provider/model/effort "
        "must match that choice. Model/effort follow catalog "
        "rules. Reviewer names unique; selections must match "
        "actual catalog tuples.",
        "stages": "Nonempty string array drawn from plan|readiness|wave-acceptance|full-vision.",
        "assurance": "Exact "
        "independentRequired:boolean,minIndependentReviewers:nonnegative "
        "nonboolean integer (positive if "
        "independentRequired),crossProviderRequired:boolean,basisRefs:nonempty "
        "string array. Preserve accepted assurance; unknown "
        "inputs do not justify reducing it.",
        "recommendation": "Exact choiceIds:ordered array of distinct "
        "existing choice "
        "hashes,reason:nonblank,catchRateEvidenceRefs:string "
        "array; preference is not proven catch rate.",
        "author_sessions": "Exact implementationSessionIds:string "
        "array,reviewerSessionIds:map of reviewer "
        "names to nonblank session IDs. "
        "Missing/unknown independence stays unmet; "
        "never invent session identities.",
        "draftRule": "confirmation=null is legal draft input and yields "
        "reviewer-confirmation-required. Unknown "
        "availability/independence and unmet cross-provider "
        "assurance remain explicit; recording a draft never "
        "admits execution.",
    },
}


def _node(kind, node_id, parents, fields=None, text=None):
    return {
        "id": node_id,
        "kind": kind,
        "title": "Reject malformed parser input",
        "text": text or "Parser boundary: reject malformed input before saving any data.",
        "parents": parents,
        "fields": fields or {},
    }


def node_example(kind):
    """Small planning examples, never observations or evidence of acceptance."""
    vision = _node("vision", "V1", [])
    feature = _node("feature", "F1", ["V1"])
    requirement = _node(
        "requirement",
        "R1",
        ["F1"],
        text="Parser boundary: malformed input must be rejected without saving data. "
        "Planned unit verification: submit truncated JSON and assert no record is saved. "
        "Credible bug: parser saves partial data before raising. Existing happy-path "
        "proof misses this rejection and rollback behavior; add the negative case.",
    )
    examples = {
        "vision": ([], vision),
        "feature": ([vision], feature),
        "story": ([vision, feature], _node("story", "S1", ["F1"])),
        "requirement": ([vision, feature], requirement),
        "acceptance": ([vision], _node("acceptance", "A1", ["V1"])),
        "decision": ([vision], _node("decision", "D1", ["V1"])),
        "design": ([vision, feature, requirement], _node("design", "G1", ["R1"])),
        "bead": (
            [vision, feature, requirement],
            _node(
                "bead",
                "B1",
                ["R1"],
                {
                    "ownedFiles": ["src/parser.py"],
                    "proofModule": "test_parser",
                    "dependsOn": [],
                    "lessons": [],
                },
            ),
        ),
        "test": (
            [vision, feature, requirement],
            _node(
                "test",
                "T1",
                ["R1"],
                {
                    "module": "test_parser",
                    "level": "unit",
                },
            ),
        ),
    }
    return examples[kind]


def typed_draft_help(tool):
    """Complete unconfirmed shapes; unresolved observations are never invented."""
    result = {
        "templateState": "unconfirmed-draft; bind prerequisites before submission",
        "revisionBinding": {
            "tool": "harness_plan_status",
            "resultKey": "spine_revision",
            "argument": "expected_spine_revision",
        },
        "prerequisiteNodes": [_node("vision", "V1", [])],
        "templateUse": "In a new plan, call harness_plan_start with idea and name, then "
        "harness_upsert_node for prerequisiteNodes in order. For an existing plan use its "
        "actual IDs and preserve accepted intent. Fetch harness_plan_status AFTER all node "
        "edits and copy its spine_revision to expected_spine_revision. Submit with the "
        "named tool; CLI equivalents are status and intake/reviewer-catalog/reviewer-choice "
        "--json @absolute-payload-file. Refresh status and retry after a stale revision. "
        "Null bindings below must be supplied before submission; they are not valid "
        "observations or authority. Keep confirmation null.",
        "producerLimits": "reviewer-catalog-producer-unavailable: harness_preflight only "
        "observes executable readiness; it does not produce a model catalog or establish "
        "authentication/entitlement. Supply genuine retained observations or hold reviewer "
        "selection for an owner integration decision.",
    }
    if tool == "harness_intake":
        fields = {
            "schema": "dot-intake-v1",
            "requestKind": "new",
            "sizeEstimate": "S",
            "delta": {
                "summary": "Reject malformed parser input",
                "nodeIds": [],
                "outcome": "Malformed input leaves existing data unchanged",
                "successCriteria": ["Truncated JSON refuses without saving partial data"],
                "ownershipAreas": ["parser"],
                "interfaceIds": [],
            },
            "ambiguity": {"level": "low", "reasons": []},
            "consequence": {
                "level": "ordinary",
                "reversibility": "Revert source change",
                "risks": [],
            },
            "dependencyBreadth": {"producerIds": [], "crossWaveContracts": []},
            "constraints": {
                "runtime": [],
                "security": [],
                "spend": "No provider calls in this draft",
                "frictionBudget": "One focused parser check",
            },
            "rationale": "Plan the parser rejection and rollback behavior",
            "assumptions": [],
            "expectedSupervision": "Review actual parser behavior before acceptance",
            "nextGate": "Resolve explicit reviewer choice and existing planning gates",
            "reviewerDecisionId": "RS1",
            "confirmation": None,
            "supersedes": None,
            "adaptation": None,
        }
        result["draftTemplate"] = {
            "node": _node("decision", "I1", ["V1"], fields),
            "expected_spine_revision": None,
            "baseline": None,
        }
    elif tool == "harness_reviewer_catalog":
        result["draftTemplate"] = {
            "catalog": {
                "schema": "dot-reviewer-catalog-v1",
                "id": None,
                "observedAt": None,
                "providers": [],
            },
            "expected_spine_revision": None,
        }
        result["observationBindings"] = (
            "Supply the genuine observation's RC-UUID, timezone-aware "
            "observedAt and providers. Empty providers claims no available reviewers. Never copy "
            "a fabricated timestamp, model, source, executable hash or available status. Omit "
            "derived digest/choiceId; the recorder returns the normalized values."
        )
        result["providerTemplate"] = {
            "provider": None,
            "executable": None,
            "executableDigest": None,
            "version": None,
            "installed": None,
            "authentication": "unknown",
            "discovery": "unknown",
            "source": None,
            "choices": [],
            "reason": None,
        }
        result["catalogChoiceTemplate"] = {
            "model": None,
            "effort": None,
            "availability": "unknown",
            "reviewPathSupported": None,
            "reason": None,
        }
        result["sourceTemplates"] = [
            {"kind": kind, "reference": None, "digest": None}
            for kind in ("supported-list", "provider-local-metadata")
        ]
    else:
        result["draftTemplate"] = {
            "node": _node(
                "decision",
                "RS1",
                ["V1"],
                {
                    "schema": "dot-reviewer-selection-v1",
                    "catalogId": None,
                    "catalogDigest": None,
                    "reviewers": [],
                    "stages": ["plan", "readiness", "wave-acceptance", "full-vision"],
                    "assurance": {
                        "independentRequired": None,
                        "minIndependentReviewers": None,
                        "crossProviderRequired": None,
                        "basisRefs": [],
                    },
                    "confirmation": None,
                    "recommendation": {
                        "choiceIds": [],
                        "reason": None,
                        "catchRateEvidenceRefs": [],
                    },
                },
            ),
            "expected_spine_revision": None,
            "author_sessions": {"implementationSessionIds": [], "reviewerSessionIds": {}},
        }
        result["reviewerTemplate"] = {
            "name": None,
            "provider": None,
            "choiceId": None,
            "model": None,
            "effort": None,
        }
        result["catalogBindings"] = (
            "First record genuine supplied observations with "
            "harness_reviewer_catalog. Copy its returned catalog.id and catalog.digest, then "
            "each selected provider and exact choiceId/model/effort from "
            "catalog.providers[].choices[]. "
            "Supply distinct reviewer names, accepted assurance policy and its actual basisRefs; "
            "do not reduce that policy to fit available reviewers. Recommendation choiceIds must "
            "come from that catalog. Unknown author/reviewer sessions stay unknown. No catalog "
            "or no supported tuple means hold; an empty reviewer array is not a usable selection."
        )
    if tool != "harness_intake":
        result["effortTemplates"] = [{"kind": "named", "value": None}, {"kind": "unsupported"}]
    if tool != "harness_reviewer_catalog":
        result["producerLimits"] += (
            " typed-confirmation-producer-unavailable: keep confirmation "
            "null. harness_record_confirmation supports only align/vision-lock, not typed "
            "intake/reviewer approval. Recording these drafts grants no execution "
            "or phase approval."
        )
    return result


def schema_help(tools, *, tool="harness_upsert_node", kind=None):
    """Return actual transport schema plus advisory help for canonical node fields."""
    contract = next((item for item in tools if item["name"] == tool), None)
    if contract is None:
        raise ValueError(f"Unknown tool: {tool}; choose one of {sorted(t['name'] for t in tools)}")
    if kind is not None and tool != "harness_upsert_node":
        raise ValueError("kind applies only to harness_upsert_node")
    result = {
        "tool": tool,
        "inputSchema": deepcopy(contract["inputSchema"]),
        "tools": [item["name"] for item in tools],
        "nodeKinds": list(ALLOWED_PARENTS),
        "authority": "MCP input validation and PlanStore/Spine remain authoritative; "
        "examples are planned content, not approval, proof or phase completion.",
    }
    if tool in TYPED_CONTRACTS:
        result["owningContract"] = deepcopy({**TYPED_COMMON, **TYPED_CONTRACTS[tool]})
        result.update(typed_draft_help(tool))
    if tool == "harness_upsert_node":
        kind = kind or "requirement"
        if kind not in ALLOWED_PARENTS:
            raise ValueError(f"Unknown node kind: {kind}; choose one of {list(ALLOWED_PARENTS)}")
        prerequisites, example = node_example(kind)
        result.update(
            kind=kind,
            allowedParentKinds=sorted(ALLOWED_PARENTS[kind]),
            fieldRules=FIELD_RULES.get(
                kind, "No additional kind-specific fields required by Spine."
            ),
            fieldsPolicy="fields is an open object of finite JSON values; additional keys allowed. "
            "Later quality, compilation and phase gates still apply.",
            parentRule="Parent IDs must exist, be unique and acyclic, with allowed kinds.",
            prerequisiteNodes=prerequisites,
            example={"node": example},
            exampleUse="In a new started plan, upsert prerequisiteNodes in order, then example. "
            "For an existing plan, use its actual IDs and preserve accepted intent.",
        )
    return result


def node_error_help(arguments):
    """Explain rejected input without replacing or relaxing the owning validator."""
    import json

    node = arguments.get("node") if isinstance(arguments, dict) else None
    kind = node.get("kind") if isinstance(node, dict) else None
    if not isinstance(kind, str) or kind not in ALLOWED_PARENTS:
        return "Inspect harness_schema for required node keys, types and valid kinds."
    _, example = node_example(kind)
    return (
        "Rule: "
        + FIELD_RULES.get(kind, "Use the required node keys and types in harness_schema.")
        + " Parent IDs must already exist with allowed kinds; fields remains open JSON."
        + "\nExample: "
        + json.dumps({"node": example}, ensure_ascii=False)
        + f"\nUse harness_schema kind={kind} for prerequisites and the full input contract; "
        "adapt IDs to the current plan and retain accepted intent."
    )
