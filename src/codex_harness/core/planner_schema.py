"""Planner record shapes, input validation and read-only inspection; PlanStore owns truth."""

import ast
import hashlib
from copy import deepcopy
from typing import Literal, NotRequired, TypedDict

from codex_harness.core.planner_guidance import BATCH_GUIDANCE
from codex_harness.core.spine import ALLOWED_PARENTS, SEMANTIC_PHASES, bead_contract, bead_rules


class Judgment(TypedDict):
    """One event-backed semantic record; evidence state and human authority are separate."""

    id: str
    question: str
    subjectRefs: list[dict]
    evidenceRefs: list[dict]
    sourceRefs: list[dict]
    candidate: str | None
    binding: str
    status: Literal["pending-judgment", "judged"]
    verdict: Literal["positive", "negative", "inconclusive", "unsupported"] | None
    rationale: str | None
    meaning: dict
    gaps: list[str]
    fixesIssued: int
    disposition: Literal["judge", "exact-fix", "steer", "complete"]
    owner: None
    source: str | None
    sourceVerified: bool
    supersededBy: NotRequired[str]
    error: str | None


JUDGMENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "judgmentId": {"type": "string", "minLength": 1},
        "binding": {"type": "string", "minLength": 1},
        "verdict": {"type": "string", "enum": ["positive", "negative", "inconclusive", "unsupported"]},
        "rationale": {"type": "string", "minLength": 1},
        "meaning": {"type": "object"},
        "gaps": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "required": ["judgmentId", "binding", "verdict", "rationale", "meaning", "gaps"],
    "additionalProperties": False,
}


def judgment_response(response, record):
    """Check only shape and identity. The caller's LLM supplies all semantic meaning."""
    import json

    def reject_constant(value):
        raise ValueError(f"Use finite JSON values instead of {value}")

    parsed = json.loads(response, parse_constant=reject_constant)
    validate_input(parsed, JUDGMENT_RESPONSE_SCHEMA, "response")
    if parsed["judgmentId"] != record["id"] or parsed["binding"] != record["binding"]:
        raise ValueError("Copy judgmentId and binding exactly from the prepared judgment")
    if parsed["verdict"] == "negative" and not parsed["gaps"]:
        raise ValueError("A negative verdict needs at least one concrete gap in gaps")
    if parsed["verdict"] == "positive" and parsed["gaps"]:
        raise ValueError("Resolve the gaps or return a negative/inconclusive verdict")
    if record["question"] == "plan-review":
        from codex_harness.core.roundtable import _invalid_reason
        findings = parsed["meaning"].get("findings")
        depth = parsed["meaning"].get("depth")
        if not isinstance(depth, str) or not depth.strip():
            raise ValueError("meaning.depth must state chosen review depth and consequence-based reason")
        if not isinstance(findings, list):
            raise ValueError("meaning.findings must be a list; verdict and rationale remain explicit")
        for item in findings:
            reason = _invalid_reason(item)
            if reason:
                raise ValueError(reason)
    if record["question"] == "finding-equivalence" and parsed["verdict"] == "positive":
        facts = next((ref["payload"]["facts"] for ref in record["sourceRefs"]
                      if isinstance(ref.get("payload", {}).get("facts"), dict)), {})
        ids = {row["id"] for row in facts.get("members", [])}
        groups = parsed["meaning"].get("groups")
        if not isinstance(groups, list) or any(not isinstance(group, list) or not group for group in groups):
            raise ValueError("meaning.groups must list nonempty groups of exact member IDs")
        flat = [key for group in groups for key in group]
        if any(not isinstance(key, str) or key not in ids for key in flat) or len(set(flat)) != len(flat):
            raise ValueError("Equivalence groups must contain disjoint captured member IDs")
    if record["question"] == "finding-disposition" and parsed["verdict"] == "positive":
        facts = next((ref["payload"]["facts"] for ref in record["sourceRefs"]
                      if isinstance(ref.get("payload", {}).get("facts"), dict)), {})
        ids = {row["id"] for row in facts.get("members", [])}
        dispositions = parsed["meaning"].get("memberDispositions")
        if (not isinstance(dispositions, dict) or set(dispositions) != ids
            or any(value not in {"resolved", "rejected", "deferred", "open"} for value in dispositions.values())):
            raise ValueError("meaning.memberDispositions must disposition every captured member exactly once")
    if record["question"] == "review-reuse" and parsed["verdict"] == "positive" and type(parsed["meaning"].get("reuse")) is not bool:
        raise ValueError("meaning.reuse must explicitly judge the actual revision delta")
    if parsed["verdict"] == "positive":
        meaning = parsed["meaning"]
        if record["question"] == "user-decision" and meaning.get("decision") not in {
            "approve", "decline", "cancel", "changes-requested"
        }:
            raise ValueError("meaning.decision must name approve, decline, cancel or changes-requested")
        if record["question"] == "vision-intent" and meaning.get("impact") not in {"preserved", "changed", "narrowed"}:
            raise ValueError("meaning.impact must name preserved, changed or narrowed")
        if record["question"] == "planning-size":
            if meaning.get("size") not in {"tiny", "small", "medium", "large"} or not isinstance(meaning.get("process"), str) or not meaning["process"].strip():
                raise ValueError("meaning needs size=tiny|small|medium|large and a nonblank process")
            if any(ref["payload"].get("facts", {}).get("intake", {}).get("fields", {}).get("sizeOverride")
                   for ref in record["sourceRefs"]) and type(meaning.get("overrideAccepted")) is not bool:
                raise ValueError("meaning.overrideAccepted must interpret the recorded user's size override")
    return {key: parsed[key] for key in ("verdict", "rationale", "meaning", "gaps")}

FIELD_RULES = {
    "vision": (
        "New vision.text: a short product statement + Full brief: <actual source path>. "
        "Use PRODUCT-BRIEF.md only when that is the existing source; invent no path. Preserve "
        "the complete brief/provenance and derive every requirement/scenario from it. This "
        "teaching never migrates, truncates, rewrites or narrows existing/locked visions. "
        "Optional setup strings: projectFolder, gitName, gitEmail, githubLogin, repoUrl. "
        "Never read credentials or git config except --get user.name or --get user.email. "
        "Understanding fields/aliases: vision, users, first_user_moment/firstUserMoment, "
        "scope_in/scopeIn, scope_out/scopeOut, quality_bar/qualityBar/quality, "
        "done_feels_like/doneFeelsLike/done, assumptions. First-present precedence; "
        "done_means is additional detail, never a primary Done alias. Other finite JSON "
        "fields and their full words remain retained; these fields are not approval/proof. "
        "Web Align asks missing audience/action, tone, 1Ã¢â‚¬â€œ3 references/adjectives, colours/fonts "
        "and sections. Record taste={audience,tone,references,brand,requiredSections,sourceRef}; "
        "map into users/requirements, quality_bar and done_feels_like. Show defaults/unknowns "
        "at Lock; never infer consent. Optional designRefs=[{path,type,sha256,provenance,use}] "
        "names existing relative hashed visual files, never instructions or executable code. "
        "URLs grant no browsing/runtime permission; report unviewable/conflicting refs. "
        "Before A, record expansion={mode,reason,upgrades:[{id,text,kind,impact, "
        "disposition:accept|reject|defer}]} with stable unique IDs and quality/Done. "
        "An LLM judges adequacy, never a count. Record qualityCriteria for applicable UX/visual, "
        "performance, maintainability, architecture, observability and failures; research reasons "
        "and choices; openLoop={metric,provenance,stopConditions} agreed at Lock; brownfield "
        "existing behavior/evidence before change planning. All full fields appear in A. "
        "Accepted macro/micro upgrades retain expansionRefs downstream. Exact user words "
        "are interpreted by a bound 1j judgment; no upsert or proposed disposition grants consent. "
        "Legacy compile needs fields.validation (Python argv string array) and requirementFiles "
        "(extra protected relative paths, possibly empty); see compilationMetadata. Initial "
        "vision authoring needs neither."
    ),
    "test": "fields.module: opaque nonblank worker test selector; fields.level: semantic or e2e. "
    "Optional execution={argv:[nonblank strings],rationale:nonblank}; graph records intent, never runs it.",
    "feature": "Optional baseline={source,behavior,rationale,codeRefs,testRefs,resultRefs}; "
    "all three reference arrays are nonempty revision-bound existing graph nodes.",
    "result": "evidence={itemRef:{id,revision},candidate,source:{reference,realProvider:boolean,"
    "historical:boolean},observation:null|{reference,outcome:passed|failed},state}. States: awaiting, "
    "failed, stale, integrated, validated live. Missing observation stays awaiting; historical/fake "
    "evidence cannot be validated live. Result descends from itemRef; no acceptance is inferred.",
    "discovery": "source: nonblank provenance reference. Record meaning in text/rationale.",
    "amendment": "sourceRef:{id,revision} discovery/research/result; targetRefs:[{id,revision}] "
    "nonempty upstream targets; proposedChange,rationale nonblank; decisionRefs:[{id,revision}]. "
    "Recording an amendment grants no consent and changes no target.",
    "metric": "definition,unit: nonblank; definition describes measurement, never its achievement.",
    "stop-condition": "metricRefs:[{id,revision}] nonempty; condition: nonblank agreed stopping rule.",
    "iteration": "metricRefs,resultRefs,stopConditionRefs: nonempty revision-bound node arrays. "
    "Results retain measurements in observations. Finishing an iteration is not acceptance.",
    "baton": "decisionRefs revision-bound array; assumptions/openQuestions string arrays; "
    "currentState,nextSafeAction,rationale nonblank. Reference via batonRefs, never a second store.",
}


TYPED_COMMON = {
    "expected_spine_revision": "Exact current spine plan_revision from the last mutation result; "
    "use harness_plan_status only on reconnect or stale refusal.",
    "discoveryRoute": "harness_preflight is supported executable discovery only: "
    "authentication/effective protections remain unknown and model entitlement "
    "is not established. Catalog/choice tools record supplied observations; no "
    "supported public model-catalog producer is exposed here. When discovery is unavailable, "
    "propose exact reviewer provider/model/effort from available executables and user restrictions. "
    "Proceed with catalog: unverified in B; plain yes authorizes it. No catalog question or "
    "separate decision; real model launch failures use existing recovery within B.",
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
        "confirmationRoute": "Typed intake confirmation acquisition is "
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
        "recipe: open process proposal, never a fact-derived minimum; "
        "sizeOverride: {size,words,sourceRef}, interpreted by the same 1j contract. "
        "baselineRevisions is derived, not a request.fields key.",
        "requestKind": "new|feature|fix|refactor|continue",
        "sizeEstimate": "tiny|small|medium|large; LLM recommendation is shown with user override",
        "delta": "Exact keys summary,outcome: nonblank strings; "
        "nodeIds,interfaceIds: arrays of strings; "
        "successCriteria,ownershipAreas: nonempty string arrays.",
        "ambiguity": "Exact keys level=low|material|high, reasons=string array "
        "(nonempty unless low).",
        "consequence": "Exact keys level=ordinary|hard-invariant|public-boundary, "
        "reversibility=nonblank, risks=string array (nonempty "
        "unless ordinary).",
        "dependencyBreadth": "Exact keys producerIds,crossWaveContracts: string arrays.",
        "constraints": "Exact keys runtime,security: string arrays. No budget decision.",
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
        "authorityShape. This retains provenance, not a recipe minimum; never "
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
        "availability=offered|unavailable|unknown (legacy available normalizes to offered); "
        "reviewPathSupported:boolean; reason:nonblank.",
        "derivedHashes": "choiceId hashes "
        "provider,executable,executableDigest,version,model,effort. "
        "digest hashes normalized catalog excluding "
        "digest. Both computed by owning validator if "
        "omitted; do not substitute dummy hashes.",
        "availableRule": "Catalog choices are offered, not verified. Legacy available claims "
        "normalize to offered. installed status, executable identity/version, discovery and source "
        "describe catalog provenance; authentication and protected path access remain unknown "
        "until an actual attempt. Unknown access does not block selection. "
        "The first protected real review launch is the access probe; its observed outcome "
        "never replaces successful current review or independence requirements.",
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
        "confirmationRoute": "Keep confirmation null; save the draft, then use "
        "harness_prepare_chat_confirmation gate=plan-execution to approve the complete B subject.",
        "arguments": "node,expected_spine_revision required; optional "
        "author_sessions defaults to "
        "{implementationSessionIds:[],reviewerSessionIds:{}}.",
        "fields.required": "schema catalogId catalogDigest reviewers "
        "stages assurance confirmation recommendation",
        "binding": "schema=dot-reviewer-selection-v1; catalogId and "
        "lowercaseSHA256 catalogDigest must bind exact current "
        "recorded catalog when supplied. Without discovery use null catalogId/catalogDigest "
        "and null reviewer choiceId; do not invent observations or hashes.",
        "reviewers": "Nonempty array of "
        "exact{name,provider,choiceId,model,effort}; "
        "name/provider nonblank; choiceId lowercaseSHA256 of "
        "an existing choice; exact provider/model/effort "
        "must match that choice when a catalog exists. Without discovery propose the exact "
        "model and effort (named value or unsupported) with choiceId=null. Reviewer names unique; "
        "respect user restrictions and available executables.",
        "stages": "Nonempty string array drawn from plan|readiness|wave-acceptance|full-vision.",
        "assurance": "Exact "
        "independentRequired:boolean,minIndependentReviewers:nonnegative "
        "nonboolean integer (positive if "
        "independentRequired),crossProviderRequired:boolean,basisRefs:nonempty "
        "string array. Preserve accepted assurance; unknown "
        "inputs do not justify reducing it.",
        "recommendation": "Exact choiceIds:ordered array of distinct "
        "existing choice "
        "hashes (empty without discovery),reason:nonblank,catchRateEvidenceRefs:string "
        "array; preference is not proven catch rate.",
        "author_sessions": "Exact implementationSessionIds:string "
        "array,reviewerSessionIds:map of reviewer "
        "names to nonblank session IDs. "
        "Missing/unknown independence stays unmet; "
        "never invent session identities.",
        "draftRule": "confirmation=null is legal draft input and yields "
        "no consent. Unknown "
        "availability/independence remain explicit. Cross-provider "
        "review is preferred; recording a draft never "
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


def node_example(kind, semantic=False):
    """Small planning examples, never observations or evidence of acceptance."""
    vision = _node("vision", "V1", [])
    if kind == "vision":
        vision["text"] += "\n\nFull brief: <actual source path>"
        vision["fields"] = {
            "vision": vision["text"],
            "users": ["A person submitting parser input"],
            "first_user_moment": "Submit malformed input and see a clear error.",
            "scope_in": ["Parse input", "Reject malformed input without saving data"],
            "scope_out": ["GUI"],
            "quality_bar": "Keep original data unchanged when input is invalid.",
            "done_feels_like": "A new user submits malformed input, sees a clear error, "
            "and no data is saved.",
            "assumptions": [],
        }
    if semantic:
        vision["fields"]["planMode"] = "semantic-v1"
    feature = _node("feature", "F1", ["V1"])
    requirement = _node(
        "requirement",
        "R1",
        ["F1"],
        text="Parser boundary: malformed input must be rejected without saving data. "
        "Planned semantic verification: submit truncated JSON and assert no record is saved. "
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
                {k: example for k, (example, _) in bead_contract(semantic).items()},
            ),
        ),
        "test": (
            [vision, feature, requirement],
            _node("test", "T1", ["R1"], {"module": "test_parser", "level": "semantic"}),
        ),
    }
    if kind in examples:
        return examples[kind]
    # Templates bind real IDs/revisions at authoring time; never invent observations.
    parents = ["V1"] if "vision" in ALLOWED_PARENTS[kind] else ["R1"]
    return [vision, feature, requirement], _node(kind, "N1", parents)


def typed_draft_help(tool):
    """Complete unconfirmed shapes; unresolved observations are never invented."""
    from codex_harness.core.approvals import EFFECTS

    result = {
        "approvalRule": EFFECTS["B"],
        "templateState": "unconfirmed-draft; bind prerequisites before submission",
        "revisionBinding": {
            "resultKey": "plannerConnection.planRevision",
            "reconnectTool": "harness_plan_status",
            "argument": "expected_spine_revision",
        },
        "prerequisiteNodes": [_node("vision", "V1", [])],
        "templateUse": "New plan: harness_plan_start, then prerequisiteNodes via upsert. Existing "
        "plans preserve IDs/intent. Use the last mutation planRevision as expected_spine_revision; "
        "read status only on reconnect/stale refusal. CLI: intake/reviewer-catalog/reviewer-choice "
        "--json @absolute-payload-file. Supply required bindings; without discovery keep catalog "
        "bindings null. Nulls are neither observations nor authority. Keep confirmation null.",
        "producerLimits": "harness_preflight only "
        "observes executable readiness; it does not produce a model catalog or establish "
        "authentication/entitlement. Missing discovery never blocks delivery: propose exact "
        "reviewer tuples under user restrictions, labelled catalog: unverified in B and the "
        "acceptance packet. Plain yes on B authorizes them, without another decision. "
        "The first protected real review observes access; launch failures use existing B recovery.",
    }
    if tool == "harness_intake":
        fields = {
            "schema": "dot-intake-v1",
            "requestKind": "new",
            "sizeEstimate": "small",
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
    if tool != "harness_intake":
        result["effortTemplates"] = [{"kind": "named", "value": None}, {"kind": "unsupported"}]
    if tool == "harness_intake":
        result["producerLimits"] += (
            " typed-confirmation-producer-unavailable: keep confirmation "
            "null. harness_record_confirmation supports only align/vision-lock, not typed "
            "intake/reviewer approval. Recording these drafts grants no execution "
            "or phase approval."
        )
    return result


def schema_help(
    tools, *, tool="harness_upsert_node", kind=None, include_teaching=False, semantic=False
):
    """Return actual transport schema plus advisory help for canonical node fields."""
    contract = next((item for item in tools if item["name"] == tool), None)
    if contract is None:
        raise ValueError(f"Unknown tool: {tool}; choose one of {sorted(t['name'] for t in tools)}")
    if kind is not None and tool != "harness_upsert_node":
        raise ValueError("kind applies only to harness_upsert_node")
    result = {
        "tool": tool,
        "inputSchema": deepcopy(contract["inputSchema"]),
        "tools": [tool],
        "nodeKinds": [kind or "requirement"] if tool == "harness_upsert_node" else [],
        "authority": "MCP input validation and PlanStore/Spine remain authoritative; "
        "examples are planned content, not approval, proof or phase completion.",
    }
    if tool in TYPED_CONTRACTS:
        result["owningContract"] = deepcopy({**TYPED_COMMON, **TYPED_CONTRACTS[tool]})
        result.update(typed_draft_help(tool))
    if tool in {"harness_prepare_judgment", "harness_submit_judgment", "harness_judgment"}:
        result.update(
            responseContract=deepcopy(JUDGMENT_RESPONSE_SCHEMA),
            owningContract="PlanStore.prepare_judgment/submit_judgment/judgment; append-only events, no new store.",
            binding="Copy the prepared id into judgmentId and binding verbatim. Subjects/evidence contain canonical node snapshots/revisions; sourceRefs contain canonical events. Candidate consumers supply their current candidate identity.",
            semantics="The LLM supplies verdict, rationale, open-domain meaning and concrete gaps. Consumers validate their own meaning shape. No judgment grants human consent, execution authorization or live-provider proof.",
            recovery="Read by judgment_id after transport loss. Initial malformed response plus exactly two fixes; disposition exact-fix includes the precise error. After steer/complete the record is closed. Pending, unsupported, inconclusive and stale records grant neither credit nor concrete-gap failure. owner is released on every submission.",
        )
    if tool == "harness_plan_status":
        result["presentation"] = (
            "Default status labels visionProjection as a summary with node/plan revisions "
            "and fullFetch tool arguments. full=true returns the complete status/projection. "
            "CLI status uses the summary; CLI show <nodeId> reads the complete node. "
            "All gate, proof, approval, freshness and workspace bindings remain canonical. "
            "A summary grants nothing and never replaces a complete confirmation question."
        )
    if tool in {"harness_prepare_chat_confirmation", "harness_record_chat_confirmation"}:
        result["chatConfirmation"] = {
            "authority": "Distinct caller-mediated chat-confirmation, never native elicitation. "
            "source_verified=false and human_identity_verified=false. Actor and latest-message "
            "claim are unverified; SHA-256 binds only the literal supplied UTF-8 quote.",
            "prepare": "Semantic-v1 A via harness_understanding_card: expansion, quality, "
            "Done/taste; align and vision-lock prepare that same A. B=plan-execution "
            "use execution={expected_revision,workers}; optional max_parallel/timeout. "
            "The canonical producer binds the full displayed question and current inputs.",
            "record": "Wait for a separate actual external chat reply to the displayed question. "
            "Fields: gate, request_id, expected_revision, quoted_message, "
            "quote_sha256, actor_label. "
            "A recorded 1j judgment interprets the exact words against request/subject/source; no approve/origin/by "
            "field is accepted. Do not recycle the preceding native refusal as a new answer.",
            "consumer": "Recording grants decision only, never launch. Use separate legal advance "
            "or exact start/resume after approval. Missing/stale/inconclusive interpretation stays pending; "
            "answers grant nothing. Submit the pending judgment to resume the retained reply without another user decision.",
        }
    if tool == "harness_revise_test":
        result.update(
            prepare="Read harness_show_node id=<test-node> include_source=true and current "
            "harness_plan_status.manifest_revision. Copy the exact testFiles.path and sha256; "
            "supply complete replacement source and a concrete review/authoring reason.",
            limits="Revision only: an existing Python file linked to that test node under the "
            "configured test directory, at most 256 KiB UTF-8. No new files, implementation "
            "stubs, generated documents, path escape or reparse paths. Stale manifest or raw "
            "file hashes refuse. Source is syntax-checked, never imported by this writer.",
            result="Atomic file replacement and a protected-test-revised event containing "
            "path, caller-authored provenance, reason and before/after hashes. No approval "
            "or proof credit. Old captured evidence and publications remain history; current "
            "content must compile, audit and receive an authorized review before publishing.",
            recovery="After an interrupted call, read current source/status before retrying. "
            "Replacement may have completed without its event/response; changed bytes still "
            "invalidate old evidence. This tool does not resolve a review finding itself.",
        )
    if tool == "harness_show_node":
        result["sourceReadback"] = (
            "For Python proofs, include_source=true returns complete source and raw-byte sha256 "
            "for linked testFiles, with a 256 KiB total source budget; oversized reads refuse "
            "rather than supply a truncated replacement. Existing body/test reads keep their "
            "8 KiB budget. Readback grants no author, reviewer or execution authority."
        )
    if tool == "harness_audit":
        result.update(
            example={"mode": "prospective"},
            prerequisites="Prepare authored protected tests and importable failing stubs in "
            "workspace/template, then use harness_compile. The workspace binding is required; "
            "no template, oracle, baseline or output path can be supplied.",
            result="Return a durable attemptId before long validation; queued admission is not "
            "an audit verdict. Next: harness_audit_status id=<attemptId>. One in-flight attempt "
            "holds the whole plan across input revisions. No retry, cancel or supersede. "
            "Legacy unbound prospective rows are history only: audit-binding-required; "
            "re-audit captured current inputs. CLI audit --prospective uses the same captured "
            "inputs and bound completion writer synchronously.",
            authority="Prospective planning validation only: not oracle satisfiability, "
            "product execution, review, human approval or acceptance. Existing publication "
            "and review-policy gates remain; prospective publication needs an explicit reason.",
        )
    if tool == "harness_audit_status":
        result.update(
            example={"id": "<attemptId>"},
            history="Supply exactly one of id OR manifest_revision. The latter reads actual "
            "audit_runs history including legacy failures; no attempt wrapper, credit, migration, "
            "request correlation or server-quiescence claim. Bound history is not current credit.",
            result="Read queued/running, persisted completed result, failed/stale or "
            "unknown-interrupted ownership. Pending is not proof. Only a completed bound "
            "attempt with matching audit ID, current manifest and exact raw inputs grants "
            "prospective credit. Unknown ownership never auto-replays. No state is initialized.",
        )
    if tool in {"harness_compile", "harness_upsert_node", "harness_upsert_nodes"}:
        result["traceContract"] = {
            "chain": "vision -> feature -> story -> requirement -> architecture/design -> contract "
            "-> test -> bead -> code -> result; small dreams may collapse intermediate levels.",
            "links": "decisionRefs,researchRefs,metricRefs,stopConditionRefs,iterationRefs,batonRefs,"
            "evidenceRefs use arrays of {id,revision}; references preserve historical provenance.",
            "fields": deepcopy(FIELD_RULES),
            "compilation": "Full trace, traceRevisions and evidenceStates survive canonical store "
            "reload and compilation. Every absent observation remains awaiting. Integrated is "
            "distinct from validated live; neither records user acceptance.",
        }
        result["semanticScenarios"] = {
            "owner": "Retained vision.fields.scenarios/targets observation adapter; optional for "
            "graph authoring. Opaque target bindings are retained at planning. Runner owners "
            "decide executable support and record real observations; graph eligibility is open.",
            "schema": "seldon/scenarios-v1; scenarios have id,requirementRefs,contractRefs,setup,"
            "actions,observe,verdict. Unsupported execution remains unvalidated, never proof.",
        }
        result["compilationMetadata"] = {
            "owner": "Historical runner projection on vision.fields; no authoring eligibility.",
            "validation": "Legacy argv prefix; requirementFiles: extra protected relative paths.",
        }
    if semantic:
        prerequisites, design = node_example("design", True)
        prerequisites.append(design)
        _, contracts = node_example("bead", True)
        contracts["parents"] = [design["id"]]
        build = deepcopy(contracts)
        contracts.update(id="C", title="Contracts")
        build.update(id="B", title="Build")
        build["fields"].update(semanticPhase=SEMANTIC_PHASES[1], dependsOn=["C"])
        result.update(semanticBeads={"nodes": [contracts, build]}, beadRules=bead_rules(True))
    if tool == "harness_upsert_nodes":
        prerequisites, test = node_example("test", semantic)
        requirement = next(node for node in prerequisites if node["kind"] == "requirement")
        result.update(
            prerequisiteNodes=[n for n in prerequisites if n != requirement],
            example={"nodes": [requirement, test]},
            exampleUse=BATCH_GUIDANCE + " Batches validate the same typed graph and save "
            "atomically; adapt IDs and retain baseline/evidence links.",
        )
        return result
    if tool == "harness_upsert_node":
        if kind != "vision":
            result.pop("semanticScenarios", None)
            result.pop("compilationMetadata", None)
        kind = kind or "requirement"
        if kind not in ALLOWED_PARENTS:
            raise ValueError(f"Unknown node kind: {kind}; choose one of {list(ALLOWED_PARENTS)}")
        prerequisites, example = node_example(kind, semantic)
        if semantic and kind == "bead":
            prerequisites.append(design)
        result.update(
            kind=kind,
            allowedParentKinds=sorted(ALLOWED_PARENTS[kind]),
            fieldRules=bead_rules(semantic)
            if kind == "bead"
            else FIELD_RULES.get(kind, "No additional kind-specific fields required by Spine."),
            fieldsPolicy="fields is an open object of finite JSON values; additional keys allowed. "
            "Later quality, compilation and phase gates still apply.",
            parentRule="Parent IDs must exist, be unique and acyclic, with allowed kinds.",
            prerequisiteNodes=prerequisites,
            example={"node": contracts if semantic and kind == "bead" else example},
            exampleUse="Upsert prerequisites then example; adapt IDs, preserve accepted intent.",
        )
    return result


def node_error_help(arguments, semantic=False):
    """Explain rejected input without replacing or relaxing the owning validator."""
    import json

    node = arguments.get("node") if isinstance(arguments, dict) else None
    kind = node.get("kind") if isinstance(node, dict) else None
    if not isinstance(kind, str) or kind not in ALLOWED_PARENTS:
        return "Inspect harness_schema for required node keys, types and valid kinds."
    _, example = node_example(kind, semantic)
    return (
        "Rule: "
        + (bead_rules(semantic) if kind == "bead" else FIELD_RULES.get(kind, "Read harness_schema."))
        + " Parent IDs must already exist with allowed kinds; fields remains open JSON."
        + "\nExample: "
        + json.dumps({"node": example}, ensure_ascii=False)
        + f"\nUse harness_schema kind={kind} for prerequisites and the full input contract; "
        "adapt IDs to the current plan and retain accepted intent."
    )


def validate_input(value, schema: dict, label="arguments") -> None:
    expected = schema["type"]
    types = {
        "string": isinstance(value, str),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "null": value is None,
    }
    allowed = expected if isinstance(expected, list) else [expected]
    if not any(types[kind] for kind in allowed):
        raise ValueError(f"{label} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{label} must be one of {schema['enum']}")
    if isinstance(value, str) and schema.get("minLength") and not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    if isinstance(value, dict) and "properties" in schema:
        missing = set(schema["required"]) - value.keys()
        extra = value.keys() - schema["properties"].keys()
        if missing or extra:
            raise ValueError(f"{label}: missing {sorted(missing)}, unexpected {sorted(extra)}")
        for key, item in value.items():
            validate_input(item, schema["properties"][key], f"{label}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{label} needs at least {schema['minItems']} item(s)")
        for item in value:
            validate_input(item, schema["items"], label)



def inspect_node(
    self,
    node_id: str,
    *,
    body: bool = False,
    test: str | None = None,
    include_source: bool = False,
) -> dict:
    """Inspect linked protected Python tests using AST only, never imports."""
    from codex_harness.core.plan_store import PlanStoreError
    if body and not test:
        raise ValueError("--body requires --test <name>")
    nodes = {node["id"]: node for node in self.list_nodes()}
    if node_id not in nodes:
        raise ValueError(f"Unknown node: {node_id}; use harness_nodes for available IDs")
    node = nodes[node_id]
    result = {
        **node,
        "children": sorted(key for key, child in nodes.items() if node_id in child["parents"]),
        "testFiles": [],
    }
    if node["kind"] not in ("requirement", "test"):
        return result
    linked = {node_id}
    while True:
        descendants = linked | {
            key for key, child in nodes.items() if linked.intersection(child["parents"])
        }
        if descendants == linked:
            break
        linked = descendants
    modules = {nodes[key]["fields"]["module"] for key in linked if nodes[key]["kind"] == "test"}
    template = (self.workspace / "template").resolve()
    protected = set()
    from codex_harness.core.graph import test_dir

    for vision in (item for item in nodes.values() if item["kind"] == "vision"):
        fields = vision["fields"]
        for name in (
            *fields.get("requirementFiles", []),
            test_dir({"validation": fields.get("validation", [])}),
        ):
            path = (template / name).resolve()
            if not path.is_relative_to(template):
                raise ValueError(f"Protected file outside template: {name}")
            candidates = path.rglob("*.py") if path.is_dir() else [path]
            for candidate in candidates:
                if not candidate.resolve().is_relative_to(template):
                    raise ValueError(f"Protected file outside template: {candidate}")
                if candidate.suffix == ".py" and candidate.is_file():
                    protected.add(candidate)
    remaining = 8 * 1024
    source_remaining = 256 * 1024
    found = False
    for path in sorted(protected):
        relative = path.relative_to(template).as_posix()
        dotted = relative[:-3].replace("/", ".")
        if not any(
            module in (relative, path.name, dotted, path.stem) or dotted.endswith("." + module)
            for module in modules
        ):
            continue
        raw = path.read_bytes()
        source = raw.decode("utf-8-sig")
        tree = ast.parse(source, filename=relative)
        functions = []
        for item in tree.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                functions.append((item.name, item))
            elif isinstance(item, ast.ClassDef):
                functions.extend(
                    (f"{item.name}.{method.name}", method)
                    for method in item.body
                    if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
                )
        functions = [(name, item) for name, item in functions if item.name.startswith("test")]
        entry = {
            "path": relative,
            "functions": [name for name, _ in functions],
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if include_source:
            source_remaining -= len(raw)
            if source_remaining < 0:
                raise PlanStoreError("plan-test-source-too-large", "256 KiB source budget")
            entry["source"] = source
        if body:
            selected = [(name, item) for name, item in functions if test in (name, item.name)]
            if len(selected) > 1:
                raise ValueError(f"Ambiguous test {test} in {relative}; use Class.test_name")
            if selected:
                found = True
                name, item = selected[0]
                start = min([item.lineno] + [d.lineno for d in item.decorator_list])
                text = "".join(source.splitlines(keepends=True)[start - 1 : item.end_lineno])
                encoded = text.encode("utf-8")
                clipped = encoded[:remaining].decode("utf-8", errors="ignore")
                remaining -= len(clipped.encode("utf-8"))
                entry.update(
                    test=name,
                    body=clipped,
                    truncated=len(encoded) > len(clipped.encode("utf-8")),
                )
        result["testFiles"].append(entry)
    if body and not found:
        raise ValueError(f"Test not found in linked protected files: {test}")
    return result
