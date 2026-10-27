"""Read-only help for existing planner contracts; validation stays in MCP/Spine."""

from copy import deepcopy

from codex_harness.core.spine import ALLOWED_PARENTS

FIELD_RULES = {
    "vision": (
        "Optional understanding-card fields: vision, users, first_user_moment "
        "(alias firstUserMoment), scope_in (scopeIn), scope_out (scopeOut), "
        "quality_bar (qualityBar, quality), done_feels_like (doneFeelsLike, done), "
        "and assumptions. Listed aliases retain their existing precedence. "
        "Use done_feels_like for the primary Done summary; done_means is an "
        "additional detail field, not a primary Done alias. Other fields remain "
        "open finite JSON and their complete words are retained. These are "
        "planned understanding fields, not approval or delivery proof. "
        "Before vision-lock, record expansion: {mode: product|quality, reason, "
        "upgrades: [{id, text, kind, impact, disposition: accept|reject|defer}]}, with "
        "3–7 upgrades and nonblank quality_bar and done_feels_like. Use stable unique IDs. "
        "Choose product for a rough idea or quality within scope for a precise spec; "
        "quality mode adds no features. Show all fields in the whole Vision Lock question. "
        "Each disposition is your recommendation (accept what you would build); it is "
        "confirmed only by the user's Vision Lock reply, never by upserts, by:user or "
        "confirmed flags. Approve confirms every field as shown; Changes: adjusts them. "
        "There is no separate Expand confirmation. Trace accepted IDs downstream in "
        "requirement/decision/acceptance fields.expansionRefs, never by editing locked vision. "
        "Before compile, add fields.validation (Python argv prefix as a string array) "
        "and fields.requirementFiles (extra protected relative paths as a string array, "
        "possibly empty); see compilationMetadata for exact shapes and a valid example. "
        "These metadata fields are not required at initial vision authoring."
    ),
    "bead": (
        "fields.ownedFiles: nonempty array of nonblank strings; "
        "fields.proofModule: nonblank string; fields.dependsOn and fields.lessons: "
        "arrays of unique existing bead/decision IDs respectively (empty allowed). "
        "Optional fields.provider: nonblank string. Optional fields.capabilityRefs: array of "
        "unique lowercase dotted identifiers (each at most 128 characters; segments start "
        "with a-z and continue with a-z, 0-9, underscore or hyphen), e.g. skill.playwright. "
        "Omitted or empty means no declaration. References are REQUESTED/UNVERIFIED metadata; "
        "they do not grant tools, permissions or runtime availability. Commands, endpoints, "
        "credentials and configuration are not reference identifiers. Dependencies must be acyclic."
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
        "confirmationRoute": "Keep confirmation null while recording a draft. "
        "For an explicit ordinary caller action, discover harness_schema "
        "tool=harness_confirm_reviewer_choice, bind the current recorded node ID "
        "and current spine revision, then submit approve=true, the complete statement "
        "and empty conditions. The server derives the exact catalog/proposal/session "
        "scope and actual event reference; human_identity_verified=false. "
        "harness_record_confirmation only serves "
        "align and vision-lock; it cannot confirm "
        "this typed choice or grant its native phase approval.",
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
    "harness_confirm_reviewer_choice": {
        "state": "DRAFT/HELD; explicit ordinary typed confirmation only. "
        "approvalGranted=false, with no entitlement, native gate or execution approval.",
        "arguments": "node_id, expected_spine_revision, approve:boolean, statement:string, "
        "conditions:string are all required; no caller authority or identity fields.",
        "prerequisite": "The exact current recorded dot-reviewer-selection-v1 decision "
        "and latest catalog must match. Fetch harness_plan_status after all edits; "
        "copy its spine_revision and the actual stored choice node ID.",
        "action": "Only explicit approve=true, full nonblank statement and empty conditions "
        "attach an ordinary caller-declared user action. False/conditional/malformed actions "
        "refuse without mutation. No native elicitation or authenticated human is claimed.",
        "binding": "Server derives canonical full proposal/catalog/author-session scope, "
        "generates a record UUID and actual append-only event row ID, then atomically "
        "attaches that exact authority. Stale revision/catalog, changed scope and replay "
        "refuse; refresh and explicitly review the current proposal before another action.",
        "remainingHolds": "Only reviewer-confirmation-required may clear. Catalog/auth/model "
        "availability, independence, cross-provider, review-path, budget and native/phase "
        "holds remain; approvalGranted=false and human_identity_verified=false.",
        "nextTool": {"name": "harness_plan_status", "arguments": {}},
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
    if kind == "vision":
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
    if tool == "harness_confirm_reviewer_choice":
        return {
            "templateState": "unbound ordinary action; inspect the stored proposal before binding",
            "actionTemplate": {
                "node_id": None,
                "expected_spine_revision": None,
                "approve": None,
                "statement": None,
                "conditions": "",
            },
            "templateUse": "Use the actual stored choice node ID and current "
            "harness_plan_status.spine_revision. Set approve=true only for the intended "
            "explicit action and preserve the complete statement. Supply the independently "
            "known absolute expected_workspace on MCP wire calls. Null values are unbound "
            "placeholders, not a submitted action or invented approval reference.",
            "producerLimits": TYPED_COMMON["discoveryRoute"] + " Ordinary caller-declared "
            "tester-as-user action has human_identity_verified=false. It grants no native "
            "align/vision-lock/final acceptance or execution authority. Intake confirmation "
            "still reports typed-confirmation-producer-unavailable.",
        }
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
            " For an ordinary caller-declared action on the recorded choice, use "
            "harness_confirm_reviewer_choice with its actual node ID/current revision, "
            "explicit approve=true, full statement and empty conditions. "
            "human_identity_verified=false; generic approvalGranted remains false. "
            "harness_record_confirmation supports only align/vision-lock; "
            "reviewer confirmation grants no native phase or execution approval."
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
    if tool in {"harness_prepare_chat_confirmation", "harness_record_chat_confirmation"}:
        result["chatConfirmation"] = {
            "authority": "Distinct caller-mediated chat-confirmation, never native elicitation. "
            "source_verified=false and human_identity_verified=false. Actor and latest-message "
            "claim are unverified; SHA-256 binds only the literal supplied UTF-8 quote.",
            "prepare": "Use gate=align or vision-lock without execution fields. For gate=execution "
            "use execution={expected_revision,workers,call_cap}; optional max_parallel/timeout. "
            "The canonical producer binds the full displayed question and current inputs.",
            "record": "Wait for a separate actual external chat reply to the displayed question. "
            "Fields: gate, request_id, expected_revision, quoted_message, "
            "quote_sha256, actor_label. "
            "Only Approve, No, Cancel or Changes: <text> are parsed; no approve/origin/by "
            "field is accepted. Do not recycle the preceding native refusal as a new answer.",
            "consumer": "Recording stores only the decision; no worker admission or launch. "
            "An approved planning decision permits a separate phase advance. An approved execution "
            "decision permits separate start/resume with exactly the displayed scope. No, Cancel, "
            "conditions, missing, stale or mismatched answers grant nothing. Existing valid chat "
            "credit is reused rather than silently replaced with a native prompt.",
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
            "prepare": "When authoring test nodes or bead proofs, prepare executable protected "
            "tests and importable failing stubs in workspace/template before compile/review. "
            "A test node records intent; it does not create those files.",
            "cli": "For a Python module CLI, provide its planned entry module. For example, "
            "template/app.py can contain raise NotImplementedError('planned CLI'); a direct "
            "subprocess proof invokes [sys.executable, '-m', 'app'] and asserts its real result. "
            "For python -m tally, provide tally/__init__.py and tally/__main__.py under template. "
            "An imported main() API is not required; this example does not promise support for "
            "arbitrary helper/control-flow shapes.",
            "scope": "Stubs are planning skeletons, not product implementation or delivery. "
            "Keep protected assertions intact; workers implement the owned product files.",
            "evidence": "Zero review findings do not establish a passing audit. Prospective "
            "audit still requires collection, attributable red-stub failures and interface "
            "assertions; it does not prove product behavior or oracle satisfiability.",
        }
        result["compilationLimits"] = {
            "maxE2eNodes": 1,
            "integration": "The current compiler accepts at most one level=e2e test node. "
            "Represent integration with one suite module containing multiple cases; "
            "do not create an e2e node per case or feature. Unit test nodes may be separate.",
            "traceability": "A test's parents are requirement or bead IDs; one integration "
            "suite can reference requirements across features. These are existing compiler "
            "limits, not additional authoring or approval gates.",
        }
        result["semanticScenarios"] = {
            "appliesTo": "planMode semantic-v1 only; legacy plans ignore this block.",
            "owner": "Vision fields.scenarios and fields.targets (upsert the whole vision node; "
            "keep every other field and its exact wording).",
            "launch": "Each action runs python -B <entry> <target argv> <action argv> from the "
            "product root. For a python -m package, add a thin entry file that calls "
            "runpy.run_module('<package>', run_name='__main__').",
            "argv": "Plain strings may not contain / \\ : or ..; pass sandbox paths as "
            "{path: 'data/x.json'}. Setup writes only data/ UTF-8 fixtures.",
            "observe": "action observations: exitEquals, stdoutEquals, stdoutJsonEquals, "
            "stderrNonempty. path observations: fileJsonEquals, fileBytesEquals, fileAbsent, "
            "bytesUnchangedFrom (a setup snapshot id).",
            "refs": "requirementRefs/contractRefs are id@revision of requirement/design nodes.",
            "fieldExample": {
                "targets": {"app": {"entry": "app_cli.py", "argv": []}},
                "scenarios": {
                    "schema": "seldon/scenarios-v1",
                    "scenarios": [
                        {
                            "id": "S1",
                            "requirementRefs": ["R1@<revision>"],
                            "contractRefs": ["G1@<revision>"],
                            "setup": {
                                "files": [{"path": "data/db.json", "utf8": "[]"}],
                                "snapshots": [{"id": "before", "path": "data/db.json"}],
                            },
                            "actions": [
                                {
                                    "id": "a1",
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
            "owner": "The existing vision node's fields; not arguments to harness_compile.",
            "requiredWhen": "Required before compile, not at initial vision authoring.",
            "validation": "Array of nonblank strings: Python argv preceding the proof module. "
            "For stdlib unittest discovery use -B -m unittest discover -s tests -p; "
            "the bead's proofModule supplies the discovery pattern after -p.",
            "requirementFiles": "Array of nonblank relative path strings for extra protected "
            "files/directories; [] is valid. Compile already protects the test directory "
            "and generated planning documents; do not repeat them unless needed.",
            "pathBase": "Paths are relative to the product/template checkout root. "
            "MCP compiles workspace/template: use tests, not template/tests; "
            "do not use absolute paths or paths escaping the checkout.",
            "fieldExample": {
                "validation": ["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
                "requirementFiles": [],
            },
            "apply": "Read the existing vision with harness_show_node. Merge only these "
            "metadata fields into its fields and upsert the complete preserved node with "
            "harness_upsert_node; keep all other vision fields, text, title and IDs. "
            "Prepare the actual template proofs before harness_compile.",
            "references": [
                "docs/operator/handoff-contract.md#planner-declarations",
                "docs/operator/planner-guide.md",
                "docs/architecture/verification-runners.md#manifest-contract",
            ],
        }
    if tool == "harness_upsert_nodes":
        prerequisites, test = node_example("test")
        requirement = next(node for node in prerequisites if node["kind"] == "requirement")
        result.update(
            prerequisiteNodes=[node for node in prerequisites if node != requirement],
            example={"nodes": [requirement, test]},
            exampleUse="Use actual feature/requirement IDs. Record one feature's requirements "
            "and tests together; the whole batch validates and saves atomically or none saves. "
            "Order within the batch does not matter; duplicate IDs refuse. "
            "Read harness_schema kind=requirement and kind=test at first use.",
            authority="The existing canonical node writer and Spine validation are shared with "
            "CLI node --json @feature-nodes.jsonl. No approvals or product proof are granted.",
        )
        return result
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
