"""Read-only help for existing planner contracts; validation stays in MCP/Spine."""

from copy import deepcopy

from codex_harness.core.planner_guidance import BATCH_GUIDANCE
from codex_harness.core.spine import ALLOWED_PARENTS, SEMANTIC_PHASES, bead_contract, bead_rules

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
        "Web Align asks missing audience/action, tone, 1–3 references/adjectives, colours/fonts "
        "and sections. Record taste={audience,tone,references,brand,requiredSections,sourceRef}; "
        "map into users/requirements, quality_bar and done_feels_like. Show defaults/unknowns "
        "at Lock; never infer consent. Optional designRefs=[{path,type,sha256,provenance,use}] "
        "names existing relative hashed visual files, never instructions or executable code. "
        "URLs grant no browsing/runtime permission; report unviewable/conflicting refs. "
        "Before A, record expansion={mode:product|quality,reason,upgrades:[{id,text,kind,impact, "
        "disposition:accept|reject|defer}]}: 3–7 stable unique IDs plus quality_bar/Done. "
        "Product improves rough ideas; quality adds no features to precise scope. Dispositions "
        "are recommendations only. One complete A shows all fields and records the actual "
        "user reply, including Yes; upserts/by:user/confirmed flags grant nothing. "
        "Changes: adjusts the proposal; no separate Expand confirmation. Trace accepted "
        "expansionRefs downstream in requirement/decision/acceptance fields, not locked vision. "
        "Legacy compile needs fields.validation (Python argv string array) and requirementFiles "
        "(extra protected relative paths, possibly empty); see compilationMetadata. Initial "
        "vision authoring needs neither."
    ),
    "test": "fields.module: nonblank string; fields.level: unit or e2e.",
}


TYPED_COMMON = {
    "expected_spine_revision": "Exact current spine plan_revision from the last mutation result; "
    "use harness_plan_status only on reconnect or stale refusal.",
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
        "no consent. Unknown "
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
                {k: example for k, (example, _) in bead_contract(semantic).items()},
            ),
        ),
        "test": (
            [vision, feature, requirement],
            _node("test", "T1", ["R1"], {"module": "test_parser", "level": "unit"}),
        ),
    }
    return examples[kind]


def typed_draft_help(tool):
    """Complete unconfirmed shapes; unresolved observations are never invented."""
    result = {
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
        "--json @absolute-payload-file. Supply every null binding before submission: nulls are "
        "neither observations nor authority. Keep confirmation null.",
        "producerLimits": "reviewer-catalog-producer-unavailable: harness_preflight only "
        "observes executable readiness; it does not produce a model catalog or establish "
        "authentication/entitlement. Supply genuine offered-model observations; select explicitly "
        "with unknown access, then let the first protected real review observe the outcome.",
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
    if tool == "harness_intake":
        result["producerLimits"] += (
            " typed-confirmation-producer-unavailable: keep confirmation "
            "null. harness_record_confirmation supports only align/vision-lock, not typed "
            "intake/reviewer approval. Recording these drafts grants no execution "
            "or phase approval."
        )
    elif tool == "harness_reviewer_choice":
        result["producerLimits"] += (
            " Save reviewer choices as drafts; harness_prepare_chat_confirmation "
            "gate=plan-execution displays exact tuples and limits for B. "
            "Only current B authorizes review spending; catalog observations grant nothing."
        )
    return result


def schema_help(
    tools, *, tool="harness_upsert_node", kind=None, include_teaching=False, semantic=False
):
    """Return actual transport schema plus advisory help for canonical node fields."""
    contract = next((item for item in tools if item["name"] == tool), None)
    if contract is None:
        raise ValueError(f"Unknown tool: {tool}; choose one of {sorted(t['name'] for t in tools)}")
    if semantic and kind == "test":
        raise ValueError(bead_rules(True))
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
            "use execution={expected_revision,workers,call_cap}; optional max_parallel/timeout. "
            "The canonical producer binds the full displayed question and current inputs.",
            "record": "Wait for a separate actual external chat reply to the displayed question. "
            "Fields: gate, request_id, expected_revision, quoted_message, "
            "quote_sha256, actor_label. "
            "The card lists the owner's vocabulary, including plain Yes; no approve/origin/by "
            "field is accepted. Do not recycle the preceding native refusal as a new answer.",
            "consumer": "Recording grants decision only, never launch. Use separate legal advance "
            "or exact start/resume after approval. No/Cancel, conditions, missing/stale/mismatched "
            "answers grant nothing. Reuse chat credit; never silently force native approval.",
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
        result["proofAuthoring"] = {
            "prepare": "Legacy only: create protected tests and importable failing stubs in "
            "workspace/template before compile/review. Nodes record intent, not files.",
            "cli": "Invoke planned app.py or app/__main__.py via sys.executable; no main() needed.",
            "scope": "Workers own product files; preserve assertions. Review is not audit proof.",
        }
        result["compilationLimits"] = {
            "maxE2eNodes": 1,
            "integration": "Legacy: one level=e2e suite module; unit nodes may be separate.",
        }
        result["semanticScenarios"] = {
            "appliesTo": "planMode semantic-v1 only; legacy plans ignore this block.",
            "web": "productSurface: web; targets: {page: {kind: web, entry: index.html}}. "
            "Each action.web declares target, operation, viewport:{width,height}, reducedMotion "
            "(no-preference|reduce), steps:[{op:open},{op:assertVisible,locator:{text:Welcome}}]. "
            "Require 375x812 and 1440x900 in BOTH motion preferences for every operation; "
            "observe each action with noHorizontalOverflow, textContrastAA, headingStructure, "
            "reducedMotionRespected:true and consoleErrors/externalRequests:0. "
            "Contracts writes minimal HTML with DOM marker SELDON-STUB <operation>; "
            "Build removes markers; controller runs real browser RED/Validation. No build/network. "
            "Steps include assertHidden, focus and key Space. Every journey needs assertVisible "
            "for RED, including after prerequisite clicks. Missing PNGs fail; unknown contrast "
            "remains unproven. Before Build, plan human visualQuality observation at C. "
            "Use harness_accept_assess judgment={repo,check:visualQuality|textContrastAA,passed,"
            "statement,by} on the actual delivered/retained candidate; these caller-mediated "
            "observations never claim machine measurement. Revalidate a held candidate after "
            "a contrast judgment, then run acceptance and formal review with retained evidence.",
            "phaseFlow": "During Vision, author vision.fields.scenarios + vision.fields.targets; "
            "then batch all phase nodes and use one forward harness_advance to_phase=plan; "
            "every intermediate gate still runs; no chain crosses consent or roundtable.",
            "owner": "Vision fields.scenarios and fields.targets (upsert the whole vision node; "
            "keep every other field and its exact wording). Each nested scenario record allows "
            "only these keys: id, requirementRefs, contractRefs, setup, actions, observe, verdict. "
            "Do not add title, description or extra metadata to scenario records.",
            "launch": "Targets use exactly one entry or module plus argv. Prefer module for "
            "python -m: {module: 'app', argv: []} -> python -B -m app <target argv> <action argv>. "
            "entry uses python -B <entry>. Both workers own module __main__.py (Contracts stub). "
            "Modules need __init__.py at every level and non-stdlib names. Optional action.cwd "
            "is setup-created, e.g. 'data/caller'; default cwd and PYTHONPATH are product root.",
            "argv": "Strings are literal argv; only explicit path objects resolve sandbox paths: "
            "{path: 'data/x.json'}. Setup files use exactly one utf8 or strict base64 string, "
            "e.g. {path: 'data/raw.bin', base64: '/w=='} for byte FF; only data/ fixtures. "
            "setup.dirs "
            "declares empty data/ directories; parents are never inferred from CLI paths. "
            'Relative argv: {"path": "data/note.txt", "relative": true} with cwd data/caller '
            "emits ../note.txt; relative is optional but must be true (binding argv uses root).",
            "observe": "action observations: exitEquals, stdoutEquals, stdoutJsonEquals, "
            "stderrNonempty, stderrEquals (exact, including empty), stderrContains (substring). "
            'For a stable single-line diagnostic, stderrEquals="message\\n" proves exact framing. '
            "stderrContains alone proves neither line count nor absence of traceback. "
            "Avoid brittle whole multiline logs; variable fault diagnostics need "
            "executable scenarios with streams/argv/cwd, not supplemental promises. "
            "JSON equality preserves types, including integer versus float. path observations: "
            "fileJsonEquals, fileBytesEquals, fileAbsent, bytesUnchangedFrom (a setup snapshot id; "
            "files compare raw bytes, directories compare all entries and file bytes, "
            "<=128 entries). "
            "An observation names one action or one "
            "path, not both; path observations run after the last action, so split a scenario "
            "to check files between actions.",
            "refs": "requirementRefs/contractRefs are requirement/design node IDs, e.g. R1; "
            "an optional @revision pin is checked when present.",
            "stdin": "Optional cli.stdin supplies exactly one utf8 or strict base64 string "
            "(e.g. {utf8: 'hello\\n'} or {base64: '/w=='}); absent means EOF/DEVNULL. "
            "For responsive help/errors with stdin held open use {holdOpen:true,exitWithin:5}; "
            "observe stdinOpenAtExit:true, elapsedAtMost:5, stdoutContains and stdoutEndsWith. "
            "Required HC checks are scenarios, produced by the runner and included with exact "
            "streams, absolute argv and cwd in the candidate-bound requiredEvidence packet slot.",
            "fieldExample": {
                "targets": {"app": {"entry": "app_cli.py", "argv": []}},
                "scenarios": {
                    "schema": "seldon/scenarios-v1",
                    "scenarios": [
                        {
                            "id": "S1",
                            "requirementRefs": ["R1"],
                            "contractRefs": ["G1"],
                            "setup": {
                                "dirs": ["data"],
                                "files": [
                                    {"path": "data/db.json", "utf8": "[]"},
                                    {"path": "data/raw.bin", "base64": "/w=="},
                                ],
                                "snapshots": [{"id": "before", "path": "data/db.json"}],
                            },
                            "actions": [
                                {
                                    "id": "a1",
                                    "cwd": "data",
                                    "cli": {
                                        "target": "app",
                                        "operation": "list",
                                        "argv": ["--db", {"path": "data/db.json"}, "list"],
                                    },
                                }
                            ],
                            "observe": [
                                {"action": "a1", "exitEquals": 0, "stdoutJsonEquals": []},
                                {"path": "data/db.json", "bytesUnchangedFrom": "before"},
                            ],
                            "verdict": "all",
                        }
                    ],
                },
            },
        }
        result["compilationMetadata"] = {
            "owner": "Legacy vision.fields before compile, not initial authoring or arguments.",
            "validation": "Nonblank Python argv strings preceding bead.proofModule (-p pattern).",
            "requirementFiles": "Extra protected relative paths; tests/planning docs protected.",
            "pathBase": "workspace/template-relative; no absolute/escaping paths.",
            "fieldExample": {
                "validation": ["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
                "requirementFiles": [],
            },
            "apply": "harness_show_node; merge metadata into saved vision; harness_upsert_node.",
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
        for key in ("proofAuthoring", "compilationLimits", "compilationMetadata"):
            result.pop(key, None)
    if tool == "harness_upsert_nodes":
        if semantic:
            result.update(
                prerequisiteNodes=prerequisites,
                example=result["semanticBeads"],
                exampleUse="Adapt IDs/ownedFiles; batch prerequisites and both beads.",
            )
            return result
        prerequisites, test = node_example("test")
        requirement = next(node for node in prerequisites if node["kind"] == "requirement")
        result.update(
            prerequisiteNodes=[node for node in prerequisites if node != requirement],
            example={"nodes": [requirement, test]},
            exampleUse=BATCH_GUIDANCE + " Batches validate and save atomically or none saves. "
            "Order within the batch does not matter; duplicate IDs refuse. "
            "For legacy plans, read kind=requirement and kind=test schemas at first use.",
            authority="The existing canonical node writer and Spine validation are shared with "
            "CLI node --json @feature-nodes.jsonl. No approvals or product proof are granted.",
        )
        return result
    if tool == "harness_upsert_node":
        relevant = {
            "vision": {"semanticScenarios", "compilationMetadata"},
            "test": {"proofAuthoring", "compilationLimits"},
            "bead": {"proofAuthoring"},
        }.get(kind or "requirement", set())
        for key in (
            "proofAuthoring",
            "compilationLimits",
            "semanticScenarios",
            "compilationMetadata",
        ):
            if key not in relevant:
                result.pop(key, None)
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

    if semantic:
        return (
            bead_rules(True) + " Read harness_schema kind=bead for the complete two-bead example."
        )
    node = arguments.get("node") if isinstance(arguments, dict) else None
    kind = node.get("kind") if isinstance(node, dict) else None
    if not isinstance(kind, str) or kind not in ALLOWED_PARENTS:
        return "Inspect harness_schema for required node keys, types and valid kinds."
    _, example = node_example(kind)
    return (
        "Rule: "
        + (bead_rules() if kind == "bead" else FIELD_RULES.get(kind, "Read harness_schema."))
        + " Parent IDs must already exist with allowed kinds; fields remains open JSON."
        + "\nExample: "
        + json.dumps({"node": example}, ensure_ascii=False)
        + f"\nUse harness_schema kind={kind} for prerequisites and the full input contract; "
        "adapt IDs to the current plan and retain accepted intent."
    )
