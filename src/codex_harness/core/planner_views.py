"""Text projections and read-only durable-state guidance for planning chats."""

import sqlite3

from codex_harness.core.plan_store import PHASES
from codex_harness.core.planner_vision import render_vision
from codex_harness.core.review_instructions import status
from codex_harness.core.reviewer_evidence import single_provider_notice
from codex_harness.core.spine import Spine
from codex_harness.core.spine_diagrams import _escape, _layers, _projection, diagram, text_fallback

NEXT = {
    "align": "Restate the vision with harness_understanding_card, show the user the card "
    "exactly as written, and ask them to confirm it.",
    "expand": "Offer improvements; record what the user accepts, rejects or defers.",
    "vision-lock": "Show the understanding and journey; ask for explicit vision approval.",
    "features": "harness_schema kind=feature (CLI: schema --kind feature); "
    "map features, user stories, edge cases and open questions in short turns.",
    "requirements": "harness_schema kind=requirement (CLI: schema --kind requirement); "
    "write requirements and their planned tests one feature at a time.",
    "tech-stack": "Show options, trade-offs and recommendations as decision cards.",
    "plan": "harness_schema kind=bead (CLI: schema --kind bead); "
    "link beads to requirements, with file ownership and dependencies. "
    "Then harness_compile (CLI: compile --template template --out manifest.json).",
    "roundtable": "Record review findings and resolve open clarifications.",
    "publish": "Publish the frozen revision and its worker manifest.",
    "execute": "Follow worker progress and proof against the published revision.",
    "accept": "Review whether the delivered product achieves the locked vision.",
}


def execution_assessment_contract():
    """Owning public schema; drafts/observations are never launch authorization."""

    def obj(properties, required=None):
        return dict(
            type="object",
            properties=properties,
            required=list(properties) if required is None else required,
            additionalProperties=False,
        )

    string = dict(type="string", minLength=1)
    strings = dict(type="array", items=string, minItems=1, uniqueItems=True)
    member = obj(dict(memberId=string, artifactPaths=strings, witnessIds=strings))
    reviewer = obj(
        dict(name=string, provider=dict(type="string", enum=["codex"]), model=string, effort=string)
    )
    request = obj(
        dict(
            members=dict(type="array", items=member, minItems=1),
            worker_job_id=string,
            bead_id=string,
            expected_manifest_revision=string,
            expected_product_run_id=string,
            reviewer=reviewer,
            max_rounds=dict(type="integer", minimum=1),
            max_total=dict(type="integer", minimum=1),
            timeout=dict(type="number", exclusiveMinimum=0),
        )
    )
    schema = obj(
        dict(
            schema=dict(type="boolean", enum=[True]),
            prepare=obj(dict(finding_ids=strings, bead_id=string), []),
            request=request,
        ),
        [],
    )
    schema["oneOf"] = [
        dict(required=[key], maxProperties=1) for key in ("schema", "prepare", "request")
    ]
    return dict(
        schema="seldon/public-execution-assessment-v1",
        inputSchema=schema,
        modes=dict(
            schema="Read only; creates no plan",
            prepare="Read actual choices and exact canonical bindings; no reservation",
            request=(
                "Explicit supplied reviewer/finite limits; existing "
                "canonical preflight before reservation"
            ),
        ),
        acquisition=dict(
            findings="harness_plan_status acceptance.findings / existing CLI findings",
            choices="harness_accept_assess prepare={} / accept-assess --prepare",
            selection=(
                "Explicit finding_ids and bead_id from returned choices; "
                "select implicated artifacts and relevant witnesses"
            ),
            reviewer=(
                "Supply actual authorized name/provider/model/effort; no "
                "catalog, entitlement or configuration is inferred"
            ),
            limits=(
                "Supply positive finite original "
                "max_rounds/max_total/timeout; worker grants do not "
                "authorize review calls"
            ),
        ),
        output=dict(
            preparation=(
                "requestTemplate plus actual artifacts/witnesses/choices and unavailable reasons"
            ),
            submission=(
                "Canonical assessed-current/unverified members, immutable "
                "registration and bounded Next"
            ),
            consumer=(
                "Explicit accept-review --execution-assessment ID seals "
                "current support as evidence only"
            ),
        ),
        limits=[
            (
                "Codex recognized original author/reviewer telemetry only; "
                "unavailable observations hold before capacity"
            ),
            (
                "No verified closure, ordinary credit, native approval, "
                "default adoption or automatic relaunch"
            ),
            (
                "Record any intended ordinary disposition before assessment; "
                "later resolve stales the support"
            ),
        ],
        closureEligible=False,
        didRunProvider=False,
    )


def execution_assessment_result(store, result, *, member_ids=None):
    """Public action over the canonical readback; no second gate or mutation."""
    import json
    from pathlib import Path

    from codex_harness.core.acceptance import current_run

    workspace = str(store.workspace.resolve())
    prefix = "python -m codex_harness.core.plan_cli --workspace " + json.dumps(workspace)
    members = result.get("members", [])
    if member_ids is not None:
        members = [row for row in members if row.get("memberId") in member_ids]
    ids = list(
        dict.fromkeys(
            row["registrationId"] for row in members if row.get("support") == "assessed-current"
        )
    )
    current = current_run(store)
    if (
        members
        and (member_ids is None or len(members) == len(member_ids))
        and all(row.get("support") == "assessed-current" for row in members)
        and current
    ):
        arguments = dict(repo=str(Path(current["repo"]).resolve()), execution_assessment_ids=ids)
        cli = prefix + " accept-review --repo " + json.dumps(arguments["repo"])
        cli += "".join(" --execution-assessment " + json.dumps(key) for key in ids)
        action = dict(
            kind="ordinary_review_with_selected_support",
            mcpTool="harness_accept_review",
            arguments=arguments,
            cliCommand=cli,
            readyToInvoke=False,
            requiredArguments=["reviewers"],
            requiredChoices=(
                "Supply explicitly authorized ordinary --reviewer JSON "
                "arguments (two distinct existing providers); this is not "
                "authorization/default selection"
            ),
            reason=(
                "Fresh ordinary reviewers consume current support as "
                "evidence; open/deferred AFs and native final acceptance "
                "remain separate holds"
            ),
        )
    else:
        action = dict(
            kind="hold_or_refresh",
            mcpTool="harness_accept_assess",
            arguments={"prepare": {}},
            cliCommand=prefix + " accept-assess --prepare",
            reason=(
                "Inspect exact holdReasons and actual choices. "
                "Repair/reacquire current originals where supported; "
                "pending/uncertain work is observation only. Missing "
                "telemetry has no invented producer. No automatic retry."
            ),
        )
    return dict(result, nextAction=action, closureEligible=False)


POLICY_ADOPTION_WARNING = (
    "No supported verified finding-closure producer is available. Adopting this optional "
    "fail-closed policy can leave quality and publication held when findings arise, even after "
    "author repair, dispositions or a clean rereview. Ordinary policy-free work is unchanged. "
    "Inspect the policy contract and its limits before explicit adoption."
)


def plan_review_policy_slots_schema() -> dict:
    """The same explicit role input for CLI policy and native adoption."""
    return {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "provider", "required"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "required": {"type": "boolean"},
                "provider": {"type": "string", "enum": ["codex", "claude", "copilot"]},
            },
        },
    }


def plan_review_policy_contract(store) -> dict:
    """Shared contract for explicit optional prospective policy declaration."""
    string = {"type": "string", "minLength": 1}
    revision = store.manifest_revision()
    example = dict(
        schema="seldon/plan-review-policy-v1",
        stage="plan",
        expectedManifestRevision=revision,
        slots=[dict(name="reviewer", provider="codex", required=True)],
    )
    existing = [
        row["payload"]
        for row in store._plan_review_policies()
        if row["payload"]["manifestRevision"] == revision
    ]
    if existing:
        example["slots"] = existing[-1]["slots"]
    return {
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["schema", "stage", "expectedManifestRevision", "slots"],
            "properties": {
                "schema": {"type": "string", "enum": ["seldon/plan-review-policy-v1"]},
                "stage": {"type": "string", "enum": ["plan"]},
                "expectedManifestRevision": string,
                "slots": plan_review_policy_slots_schema(),
            },
        },
        "example": example,
        "exampleFiles": {
            "policy.json": example,
            "reviewer.json": {
                "name": example["slots"][0]["name"],
                "provider": example["slots"][0]["provider"],
            },
        },
        "exampleArgs": ["roundtable", "--policy", "@policy.json", "--reviewer", "@reviewer.json"],
        "adoptionWarning": POLICY_ADOPTION_WARNING,
        "adoption": "Explicit --policy or harness_adopt_review_policy declaration only, "
        "before attempts on this revision. "
        "Ordinary policy-free roundtables retain legacy behavior, including latest-success "
        "selection; automatic adoption is not implemented.",
        "exampleUse": "Save the example with your already authorized reviewer names/providers; "
        "pass --policy @policy.json with existing --reviewer entries to roundtable. "
        "For native adoption, supply slots and expectedManifestRevision to "
        "harness_adopt_review_policy with an independently known expected_workspace. "
        "At least one required slot; distinct names; roles fixed before attempts. "
        "This illustrative role choice grants no authorization or model selection.",
        "limits": "Plan stage only. Existing counts, caps, native approval and final acceptance "
        "unchanged. Optional metadata never waives existing quality FAIL thresholds. "
        "Model/effort and independence are not certified by reviewer/provider roles.",
        "unavailableProducers": [
            "observed author/reviewer separation",
            "targeted per-member semantic assessment",
            "general accepted-clause witness",
        ],
        "closureContract": "harness_schema tool=harness_resolve_findings; existing resolve --file "
        "or MCP array records declarations only, never verified repairs.",
    }


def closure_declaration_example(store) -> dict:
    """Use real stored IDs/revisions; absent prerequisites are a hold, never placeholders."""
    from codex_harness.core.roundtable import findings

    if store is None:
        return {"state": "held", "reason": "Start a plan; no finding/record IDs available"}
    rows = [row for row in findings(store) if row["status"] == "open"]
    revision = store.manifest_revision()
    if not rows or not revision:
        return {
            "state": "held",
            "reason": "A current manifest and recorded open finding are required",
        }
    row = rows[0]
    node_id = row["node_id"]
    if node_id == "plan":
        node_id = store.spine().ids("vision")[0]
    node = store.connection.execute("SELECT revision FROM nodes WHERE id=?", (node_id,)).fetchone()
    if node is None:
        return {"state": "held", "reason": "Affected node unavailable; inspect the recorded review"}
    return {
        "state": "declaration-only",
        "example": {
            "findings": [
                dict(
                    id=row["id"],
                    status="resolved",
                    resolution="Record the actual evidence and limitations",
                    closure=dict(
                        schema="seldon/finding-closure-declaration-v1",
                        expectedManifestRevision=revision,
                        memberIds=row["memberIds"],
                        basis="repair",
                        nodeRefs=[dict(nodeId=node_id, revision=node[0])],
                        reviewIds=[],
                        auditIds=[],
                        obligationNodeIds=[],
                    ),
                )
            ]
        },
        "authority": "Executable declaration example, not a claim that an artifact was repaired. "
        "Review IDs come from review details; audit IDs from actual audit results. "
        "Obligation IDs must be existing requirement/vision nodes; they are not "
        "general verified clauses. Counterevidence/defer basis must match disposition.",
        "result": "unverified: targeted assessment and observed separation producers unavailable",
    }


def review_policy_next(policy: dict, *, cli_prefix=None) -> str:
    """Actions over the same canonical policy facts; never a retry/identity producer."""
    if "review-policy-revision-stale" in policy["reasons"]:
        return (
            cli_prefix + "roundtable --policy-schema"
            if cli_prefix is not None
            else "harness_review_policy"
        )
    if policy["closureFindings"]:
        return (
            "Hold: targeted per-member assessment and observed-separation producers "
            "are unavailable; "
            "declarations, linked records and legacy [] do not certify semantic repair. "
            "Inspect retained findings/declarations; no automatic retry or scope waiver."
        )
    return (
        "Hold: "
        + ", ".join(policy["reasons"])
        + "; inspect actual review details and retry only declared required slots within existing "
        "authorization/caps. Unlisted attempts cannot be made optional after failure."
    )


def continuation_projection(status: dict, resolution: dict) -> dict:
    """Pure publication advice over observed facts, never mutation/admission authority."""
    action = {
        "kind": "unavailable_refresh",
        "manifestRevision": resolution.get("manifestRevision"),
        "findingIds": [],
        "auditMode": resolution.get("audit", {}).get("proofLevel") or "unknown",
    }
    quality = resolution.get("review", {}).get("qualityStatus")
    result = {
        "schemaVersion": 1,
        "spineRevision": resolution.get("spineRevision"),
        "manifestRevision": resolution.get("manifestRevision"),
        "phase": status.get("phase"),
        "planning": action,
        "recommendation": action,
        "recommendationScope": "planning-publication",
        "classification": "unavailable",
        "workerQuality": {
            "status": quality,
            "blocked": quality == "fail" if quality else None,
            "scope": "Existing worker admission quality threshold; not publication authority",
        },
        "qualityWarnings": [resolution["review"]["advice"]] if quality in {"warn", "fail"} else [],
        "productAcceptance": {
            "accepted": status.get("acceptance", {}).get("accepted") is True,
            "humanOriginVerified": status.get("acceptance", {}).get("humanOriginVerified") is True,
            "scope": "Recorded product acceptance state; not inferred from planning or quality",
        },
    }
    if resolution.get("snapshotChanged"):
        return result
    blocker = resolution.get("canonicalBlocker")
    phase = status.get("phase")
    policy = status.get("reviewPolicy", {})
    if blocker:
        action.update(blocker)
    elif phase not in {"roundtable", "publish", "execute"}:
        action["kind"] = "not_applicable"
        result["classification"] = "outside-bounded-planning-scope"
        return result
    elif resolution.get("blockingOpenFindings") is None:
        return result
    elif resolution["blockingOpenFindings"]:
        round_id = resolution["blockingOpenFindings"][0]["roundId"]
        action.update(
            kind="reconcile_findings",
            findingIds=[row["id"] for row in resolution["blockingOpenFindings"]],
            inspection={
                "mcpTool": "harness_review",
                "arguments": {"id": round_id},
                "cliArguments": ["review", round_id],
            },
        )
    elif policy.get("active") and not policy["eligible"]:
        action["kind"] = "review_policy_hold"
        result["classification"] = "configured-policy-hold"
        result["unavailableProducers"] = [
            "targeted assessment",
            "observed separation",
            "accepted-clause witness",
        ]
        round_id = resolution.get("review", {}).get("detailRoute", {}).get("roundId")
        if round_id:
            action["inspection"] = {
                "mcpTool": "harness_review",
                "arguments": {"id": round_id},
                "cliArguments": ["review", round_id],
            }
        return result
    elif not resolution.get("manifestRevision"):
        action["kind"] = "compile_required"
    elif resolution["audit"]["current"] is None:
        return result
    elif not resolution["audit"]["current"] and resolution["audit"].get("applicable", True):
        action["kind"] = "audit_required"
    elif resolution["review"]["current"] is None:
        return result
    elif not resolution["review"]["current"]:
        action["kind"] = "review_required"
    elif phase == "roundtable":
        action.update(
            kind="attempt_phase_advance",
            targetPhase="publish",
            mcpTool="harness_advance",
            arguments={"to_phase": "publish"},
            cliArguments=["advance", "--to", "publish"],
            expectedPostState={"phase": "publish"},
        )
        if resolution["review"].get("carriedForward"):
            action["message"] = resolution["review"]["message"]
    elif phase == "publish" and not status.get("publication", {}).get("up_to_date"):
        action["kind"] = "attempt_publication"
    else:
        action["kind"] = "not_applicable"
    result["classification"] = (
        "attempt-configured-mutation"
        if action["kind"].startswith("attempt_")
        else "canonical-prerequisite"
    )
    if (
        action["kind"] == "not_applicable"
        and quality == "fail"
        and status.get("publication", {}).get("up_to_date")
    ):
        result["recommendation"] = {**action, "kind": "review_required"}
        result["recommendationScope"] = "worker-admission"
    return result


def review_resolution_state(store, status: dict) -> dict:
    """Read existing canonical facts; advisory only, never a loop or admission lease."""
    from codex_harness.core.plan_quality import _roundtable
    from codex_harness.core.roundtable import findings

    def known(value):
        return (
            isinstance(value, str)
            and bool(value.strip())
            and value not in {"unknown", "unavailable"}
        )

    spine = status.get("spine_revision", status.get("plan_revision"))
    manifest = status.get("manifest_revision")
    state = {
        "schemaVersion": 1,
        "phase": status.get("phase"),
        "spineRevision": spine if known(spine) else None,
        "manifestRevision": manifest if known(manifest) else None,
        "blockingOpenFindings": None,
        "deferredFindings": None,
        "audit": {"current": None, "proofLevel": None},
        "review": {"current": None, "successfulReviewIds": None},
        "budget": {"reservation": "unknown_to_product", "owner": "host_consumer"},
        "scope": (
            "Host-driven plan resolution/publication guidance; not worker or product acceptance."
        ),
    }

    def action(kind, **details):
        return {
            "kind": kind,
            "manifestRevision": state["manifestRevision"],
            "findingIds": [],
            "auditMode": state["audit"]["proofLevel"] or "unknown",
            **details,
        }

    state["nextAction"] = action("unavailable_refresh")
    if store is None or not known(spine) or state["phase"] not in PHASES:
        return state

    def excerpt(value):
        if not isinstance(value, str):
            return None, False
        return value[:512], len(value) > 512

    def relation(revision):
        if not known(revision) or not known(manifest):
            return "unknown"
        return "current" if revision == manifest else "stale"

    priority = None
    try:
        gate = status.get("pending_gate")
        approval = status.get("approval") or {}
        if status.get("vision_lock_stale") or (
            isinstance(gate, str)
            and gate.startswith("approval:")
            and not (
                approval.get("granted") is True and approval.get("gate") == gate.split(":", 1)[1]
            )
        ):
            priority = action(
                "existing_canonical_blocker",
                reason="approval",
                gate="vision-lock" if status.get("vision_lock_stale") else gate.split(":", 1)[1],
            )
        questions = [
            row[0]
            for row in store.connection.execute(
                "SELECT id FROM clarifications WHERE status='open' ORDER BY id"
            )
        ]
        if len(questions) != status.get("open_clarifications"):
            raise ValueError("Canonical clarification state changed")
        if priority is None and questions:
            priority = action(
                "existing_canonical_blocker", reason="clarifications", questionIds=questions
            )
        # Preserve a known canonical approval/question prerequisite even when the
        # manifest is absent. Broken review reads must still remain unknown.
        if priority is not None:
            state["nextAction"] = priority
        rows = findings(store)
        blocking, deferred = [], []
        for row in sorted(rows, key=lambda item: item["id"]):
            if row.get("stage") == "readiness":
                continue
            if not known(row.get("id")) or not isinstance(row.get("memberIds"), list):
                raise ValueError("Unknown recorded finding membership")
            if row["status"] not in {"open", "deferred"}:
                continue
            item = {
                "id": row["id"],
                "recordedRevision": row.get("plan_revision"),
                "revisionRelation": relation(row.get("plan_revision")),
                "detailRoute": {
                    "roundId": row.get("round_id"),
                    "mcpTool": "harness_review",
                    "cliCommand": "review",
                },
            }
            if row["status"] == "open":
                for public, source in (("finding", "finding"), ("suggestion", "suggestion")):
                    item[public], item[public + "Truncated"] = excerpt(row.get(source))
                item.update(
                    memberIds=sorted(row["memberIds"]),
                    nodeId=row.get("node_id"),
                    roundId=row.get("round_id"),
                    severity=row.get("severity"),
                    status="open",
                )
                blocking.append(item)
            else:
                item["resolution"], item["resolutionTruncated"] = excerpt(row.get("resolution"))
                deferred.append(item)
        state.update(blockingOpenFindings=blocking, deferredFindings=deferred)
        if known(manifest):
            audit = store.passing_audit(manifest)
            reviews = store.successful_reviews(manifest)
            if audit is not None and not isinstance(audit, dict) or not isinstance(reviews, list):
                raise ValueError("Unreadable current evidence")
            state["audit"] = {
                "current": bool(audit),
                "proofLevel": (audit.get("proofLevel", "oracle-verified") if audit else None),
                **({"applicable": False} if Spine(store._data()).semantic else {}),
            }
            if state["audit"]["proofLevel"] not in {None, "prospective", "oracle-verified"}:
                raise ValueError("Unknown recorded audit mode")
            ids = [row["id"] for row in reviews]
            if not all(known(item) for item in ids):
                raise ValueError("Unknown current review identity")
            state["review"] = {"current": bool(ids), "successfulReviewIds": sorted(ids)}
            if any(row.get("carriedForward") for row in reviews):
                state["review"].update(
                    carriedForward=True, message="review current (carried forward after repairs)"
                )
            if state["phase"] in {"roundtable", "publish", "execute"}:
                quality, evidence, advice = _roundtable(store)
                check = dict(status=quality, evidence=evidence, advice=advice)
                if check["status"] not in {"pass", "warn", "fail"}:
                    raise ValueError("Unknown roundtable quality state")
                advice, truncated = excerpt(check["advice"])
                state["review"].update(
                    qualityStatus=check["status"],
                    advice=advice,
                    adviceTruncated=truncated,
                    detailRoute={
                        "roundId": check["evidence"].get("roundId"),
                        "mcpTool": "harness_review",
                        "cliCommand": "review",
                    },
                )
        if status.get("reviewPolicy", {}).get("active"):
            state["reviewPolicy"] = status["reviewPolicy"]
        if (
            store._revision() != spine
            or store.phase() != state["phase"]
            or store.manifest_revision() != manifest
        ):
            priority = None
            state["snapshotChanged"] = True
            raise ValueError("Resolution observation changed during read")
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, StopIteration):
        state.update(
            blockingOpenFindings=None,
            deferredFindings=None,
            audit={"current": None, "proofLevel": None},
            review={"current": None, "successfulReviewIds": None},
        )
        state["nextAction"] = priority if priority is not None else action("unavailable_refresh")
    state["canonicalBlocker"] = priority
    projection = continuation_projection(status, state)
    state["nextAction"] = projection["recommendation"]
    state["continuation"] = projection
    return state


def review_finding_members(store, review: dict) -> list[dict]:
    """Exact recorded member details for the existing review detail endpoint."""
    from codex_harness.core.roundtable import findings

    rounds = {row["round_id"] for row in review.get("reviewers", []) if row.get("round_id")}
    return [
        {
            key: row.get(key)
            for key in (
                "id",
                "cluster_id",
                "memberIds",
                "memberCount",
                "node_id",
                "round_id",
                "plan_revision",
                "reviewer",
                "provider",
                "severity",
                "status",
                "finding",
                "suggestion",
                "resolution",
            )
        }
        for row in findings(store, expand=True)
        if row.get("round_id") in rounds
    ]


def review_author_handoff(store, status: dict, *, cli_prefix=None) -> dict:
    """Bounded author feedback for the invoking planner; never host-session re-entry."""
    from codex_harness.core.roundtable import findings

    state = review_resolution_state(store, status)
    result = {
        "schema": "seldon/review-author-handoff-v1",
        "spineRevision": state["spineRevision"],
        "manifestRevision": state["manifestRevision"],
        "state": "refresh_required",
        "clusterCount": None,
        "memberCount": None,
        "members": [],
        "omittedMemberCount": None,
        "details": {
            "cliCommand": (
                cli_prefix
                or f'python -m codex_harness.core.plan_cli --workspace "{store.workspace}" '
            )
            + "--json findings --expand",
        },
        "nextAction": state["nextAction"],
        "next": review_resolution_next(state, cli_prefix=cli_prefix),
        "scope": "Invoking planner response only; no ended-session resumption or worker proof.",
    }
    if state["blockingOpenFindings"] is None:
        return result
    if "reviewPolicy" in state:
        result["reviewPolicy"] = state["reviewPolicy"]
    try:
        blockers = {row["id"] for row in state["blockingOpenFindings"]}
        observed_members = findings(store, "open", expand=True)
        rows = [row for row in observed_members if (row.get("cluster_id") or row["id"]) in blockers]
        members = []
        for row in sorted(rows, key=lambda item: item["id"])[:16]:
            member = {
                "id": row["id"],
                "clusterId": row.get("cluster_id") or row["id"],
                "memberIds": row["memberIds"][:32],
                "membershipTruncated": len(row["memberIds"]) > 32,
                "memberCount": row["memberCount"],
                "nodeId": row.get("node_id"),
                "nodeRoute": {
                    "mcpTool": "harness_nodes",
                    "next": cli_prefix + "nodes" if cli_prefix is not None else "harness_nodes",
                }
                if row.get("node_id") == "plan"
                else {
                    "mcpTool": "harness_show_node",
                    "id": row.get("node_id"),
                    "next": (
                        cli_prefix + "show " + row["node_id"]
                        if cli_prefix is not None
                        else "harness_show_node id=" + row["node_id"]
                    ),
                },
                "recordedRevision": row.get("plan_revision"),
                "revisionRelation": (
                    "unknown"
                    if not row.get("plan_revision") or not state["manifestRevision"]
                    else "current"
                    if row["plan_revision"] == state["manifestRevision"]
                    else "stale"
                ),
                "severity": row.get("severity"),
                "status": row["status"],
                "detailRoute": {
                    "mcpTool": "harness_review",
                    "roundId": row.get("round_id"),
                    "cliCommand": "review " + row["round_id"],
                    "next": (
                        cli_prefix + "review " + row["round_id"]
                        if cli_prefix is not None
                        else "harness_review id=" + row["round_id"]
                    ),
                },
            }
            for field in ("finding", "suggestion"):
                value = row.get(field)
                member[field] = value[:1024] if isinstance(value, str) else None
                member[field + "Truncated"] = isinstance(value, str) and len(value) > 1024
            members.append(member)
        if (
            store._revision() != state["spineRevision"]
            or store.manifest_revision() != state["manifestRevision"]
            or store.phase() != state["phase"]
            or review_resolution_state(store, store.status()) != state
            or findings(store, "open", expand=True) != observed_members
        ):
            raise ValueError("Author handoff changed during read")
        result.update(
            state="author_action_required" if blockers else "follow_canonical_action",
            clusterCount=len(blockers),
            memberCount=len(rows),
            members=members,
            omittedMemberCount=max(0, len(rows) - len(members)),
        )
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        result.update(
            nextAction={"kind": "unavailable_refresh"},
            next=(cli_prefix + "status") if cli_prefix is not None else "harness_plan_status",
        )
    return result


def author_handoff_card(handoff: dict) -> str:
    """Visible repair instructions and recorded member suggestions, bounded above."""
    if handoff["state"] == "refresh_required":
        return "Author feedback unavailable or changed; refresh current canonical status."
    lines = [
        "**Author repair: feedback to the invoking planner**",
        "Bound to manifest "
        + str(handoff["manifestRevision"])
        + "; refresh status before acting if the plan changed.",
        "Inspect each recorded finding; repair the affected existing plan nodes/template "
        "within the locked vision, preserving acceptance semantics and file ownership. "
        "Worker-owned product behavior remains for workers; do not implement it to pass an audit.",
        "Record evidence-backed dispositions after repair or demonstrated rejection; then "
        "follow current compile, audit, fresh roundtable and ordinary publication guidance. "
        "Edits can stale evidence; dispositions alone do not refresh it. Locked vision changes "
        "require current A consent. "
        "No launch or additional capacity is granted.",
    ]
    for member in handoff["members"]:
        lines.extend(
            [
                f"- {member['id']} ({member['nodeId']}, {member['revisionRelation']}): "
                + str(member["finding"]),
                "  Recorded suggestion: " + str(member["suggestion"]),
                "  Inspect affected node: " + member["nodeRoute"]["next"],
                "  Exact member details: " + member["detailRoute"]["next"],
            ]
        )
    if handoff["omittedMemberCount"] or any(
        member["findingTruncated"] or member["suggestionTruncated"] or member["membershipTruncated"]
        for member in handoff["members"]
    ):
        lines.append(
            f"Bounded excerpts; {handoff['omittedMemberCount']} members omitted. "
            "Complete recorded details: " + handoff["details"]["cliCommand"]
        )
    return "\n".join(lines)


def refusal_help(code, message: str, status: dict) -> str | None:
    """One plain 'How to fix' line for common planning refusals; never changes the gate."""
    message = str(message)
    phase = status.get("phase")
    if code in {"plan-approval-required", "plan-approval-stale"}:
        gate = message.split(" needs approval", 1)[0].strip() or "the current"
        return (
            f"How to fix: the user has not approved {gate} for this plan version yet. "
            "Show them the understanding card exactly as written, then call "
            f"harness_record_confirmation gate={gate}. If the client cannot show the form, "
            "show the chat question Seldon offers instead and wait for the user's reply."
        )
    if code == "plan-confirmation-origin-unavailable":
        return (
            "How to fix: this client cannot show Seldon's approval form. Show the user the "
            "chat question Seldon offers exactly as written, wait for their reply, then record "
            "it with harness_record_chat_confirmation."
        )
    if code == "plan-clarifications-open":
        return (
            "How to fix: ask the user each open question (ids in harness_plan_status), record "
            "their answers with harness_answer, then retry."
        )
    if code == "plan-findings-open":
        return (
            "How to fix: repair the plan for each open review finding, or record a reasoned "
            "disposition with harness_resolve_findings; then compile, audit and review again."
        )
    if code == "plan-publication-evidence-required":
        missing = []
        if "passing audit" in message:
            missing.append("a passing test audit (harness_audit)")
        if "successful review" in message:
            missing.append("a successful plan review (harness_roundtable)")
        return (
            "How to fix: publishing needs "
            + (" and ".join(missing) or "current audit and review evidence")
            + " for this exact plan version. Any plan edit makes earlier evidence stale."
        )
    if code == "plan-phase-order":
        index = PHASES.index(phase) if phase in PHASES else None
        target = PHASES[index + 1] if index is not None and index + 1 < len(PHASES) else None
        return "How to fix: phases move one step at a time" + (
            f"; advance to {target} next, or return to an earlier phase." if target else "."
        )
    if code == "plan-phase-invalid":
        return "How to fix: use one of these phases: " + ", ".join(PHASES) + "."
    if code == "plan-confirmation-gate-mismatch":
        return f"How to fix: confirm only the gate for the current phase ({phase})."
    if code == "plan-node-missing":
        return "How to fix: check the exact id with harness_nodes (ids are case-sensitive)."
    return None


def review_resolution_next(state: dict, *, cli_prefix=None) -> str:
    """Transport-specific wording for the same single canonical advisory action."""
    action = state["nextAction"]
    kind = action["kind"]

    def cmd(value):
        return cli_prefix + value if cli_prefix is not None else "existing CLI " + value

    refresh = cmd("status") if cli_prefix is not None else "harness_plan_status"
    if kind == "existing_canonical_blocker":
        if action.get("reason") == "approval":
            return "Use native MCP harness_record_confirmation gate=" + action["gate"]
        return (
            cmd("answer --id " + action["questionIds"][0] + ' --text "<actual answer>"')
            if cli_prefix is not None
            else "Answer the actual open questions with harness_answer."
        )
    if kind == "reconcile_findings":
        ids = ", ".join(action["findingIds"][:3]) + (
            "; more in status" if len(action["findingIds"]) > 3 else ""
        )
        route = (
            cmd('resolve --id <recorded-id> --resolution "<evidence and reason>"')
            + " with exactly one of --resolved, --rejected, or --deferred"
            if cli_prefix is not None
            else (
                "harness_resolve_findings with recorded ids, "
                "evidence-backed dispositions and reasons"
            )
        )
        return f"Inspect and repair recorded findings ({ids}); use {route}; refresh {refresh}."
    if kind == "review_policy_hold":
        return review_policy_next(state["reviewPolicy"], cli_prefix=cli_prefix)
    if kind == "compile_required":
        return (
            (
                cmd("compile --template <existing-template> --out <manifest.json>")
                if cli_prefix is not None
                else "harness_compile"
            )
            + "; refresh "
            + refresh
        )
    if kind == "audit_required":
        mode = action["auditMode"]
        if cli_prefix is None and mode != "oracle-verified":
            return (
                "harness_schema tool=harness_audit; then harness_audit mode=prospective "
                "for planned collection/red-stub checks, not oracle or product proof."
            )
        if mode == "prospective":
            return cmd("audit --prospective")
        if mode == "oracle-verified":
            return cmd("audit --oracle <existing-reference>")
        return (
            "Choose the authorized audit mode: "
            + cmd("audit --prospective")
            + " OR "
            + cmd("audit --oracle <existing-reference>")
        )
    if kind == "review_required":
        review = state["review"]
        review_command = cmd("roundtable") if cli_prefix is not None else "harness_roundtable"
        if review.get("qualityStatus") == "fail" and review["current"]:
            round_id = review["detailRoute"]["roundId"]
            inspect = (
                cmd("review " + round_id)
                if cli_prefix is not None
                else "harness_review id=" + round_id
            )
            return (
                f"Inspect {inspect}: {review['advice']}; retry only the failed reviewer with "
                + (
                    cmd("roundtable --reviewer @<failed-reviewer.json>")
                    if cli_prefix is not None
                    else review_command
                )
                + " within host-reserved capacity and existing round/total caps; refresh "
                + refresh
            )
        return review_command + " within host-reserved capacity; refresh " + refresh
    if kind == "attempt_phase_advance":
        return (
            (
                cmd("advance --to publish")
                if cli_prefix is not None
                else "harness_advance to_phase=publish"
            )
            + " (attempt only; canonical gates recheck)"
            + review_warning(state)
        )
    if kind == "attempt_publication":
        prospective = action["auditMode"] == "prospective"
        return (
            (
                cmd(
                    'publish --proof-level prospective --reason "<actual no-oracle reason>"'
                    if prospective
                    else "publish"
                )
                if cli_prefix is not None
                else "harness_publish"
                + (
                    " proof_level=prospective reason=<actual no-oracle reason>"
                    if prospective
                    else ""
                )
            )
            + " (attempt only; canonical gates recheck)"
            + review_warning(state)
        )
    return "Refresh " + refresh + "; canonical state or resolution scope is unavailable."


def review_warning(state: dict) -> str:
    review = state["review"]
    return (
        "; review quality warning (worker admission remains separate): " + review["advice"]
        if review.get("qualityStatus") in {"warn", "fail"}
        else ""
    )


def _inline(value: str) -> str:
    return " ".join(value.split())


def _names(values) -> str:
    return ", ".join(sorted(values)) or "none"


def _short(value, limit=60) -> str:
    text = _inline(str(value))
    return text if len(text) <= limit else text[: limit - 1] + "…"


PENDING_LABELS = {
    "approval:align": "waiting for your Align approval",
    "approval:vision-lock": "waiting for your Vision Lock approval",
    "passing-audit-required": "needs a passing test audit",
    "successful-review-required": "needs a fresh plan review",
    "status-unavailable": "status unavailable",
}


def pending_label(gate) -> str:
    """Plain words for the pending gate; the raw code stays in structured status."""
    if not gate:
        return "nothing is waiting on you"
    return PENDING_LABELS.get(gate) or "waiting on: " + _short(str(gate).replace("-", " "), 60)


def status_line(status: dict) -> str:
    return (
        f"Revision {status['plan_revision'][:12]} · "
        f"open questions {status['open_clarifications']} · "
        f"pending: {_short(status['pending_gate'] or 'none', 65)}"
    )[:120]


def banner(status: dict) -> str:
    return (
        f"Plan **{_short(status.get('name', 'unknown'), 48)}** · "
        f"{pending_label(status['pending_gate'])}"
    )


def phase_tracker(status: dict) -> str:
    current = PHASES.index(status["phase"])
    return "Progress: " + " · ".join(
        f"**{phase}**" if index == current else phase + " ✓" if index < current else phase
        for index, phase in enumerate(PHASES)
    )


def first_action(value: str) -> str:
    first = value.split(" then ", 1)[0]
    return value if first.endswith(",") else first


def response(status: dict, body: str, next_step: str | None = None, *, tracker=False) -> str:
    # Callers own the one final action. Recomposition replaces an existing footer.
    body = body.rsplit("\nNext: ", 1)[0].rstrip()
    parts = [banner(status)]
    if tracker and status["phase"] in PHASES:
        parts.append(phase_tracker(status))
    if body:
        parts.append(body)
    parts.append(
        "Next: "
        + first_action(
            _inline(next_step or NEXT.get(status["phase"], "Retry harness_plan_status."))
        )
    )
    return "\n".join(parts)


def approval_label(by: str, *, is_proxy: bool | None = None) -> str:
    if is_proxy is False or (is_proxy is None and by == "user"):
        return "approved by you (recorded statement; human origin not verified)"
    name = by.removeprefix("proxy:")
    if name == "opus-user-proxy":
        name = "Opus"
    return f"approved by proxy ({_inline(name)}) · not your approval"


def readable(value) -> str:
    """Plain Markdown values; structured content remains the machine authority."""
    if isinstance(value, dict):
        return "; ".join(f"{_inline(str(k))}: {readable(v)}" for k, v in value.items()) or "none"
    if isinstance(value, list):
        return "; ".join(readable(item) for item in value) or "none"
    if value is None:
        return "not recorded"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return _inline(str(value))


def data_card(title: str, data) -> str:
    items = list(data.items()) if isinstance(data, dict) else [("Items", data)]
    bullets = [f"- {key}: {_short(readable(value), 300)}" for key, value in items[:7]]
    if len(items) > 7:
        bullets[-1] += " · More in machine output."
    while len(bullets) < 3:
        bullets.append(
            "- Evidence: recorded data; this action does not prove delivery."
            if len(bullets) == 1
            else "- Details: available in machine output."
        )
    return "\n".join([f"**{title}**", "Full details in machine output.", *bullets])


def outcome_waves_card(projection: dict) -> str:
    """Full chat map, without truncating later outcomes or inventing graph-depth waves."""
    lines = ["**Outcome planning · draft / lock unconfirmed**"]
    for field, label in (("intake", "Intake"), ("reviewerSelection", "Reviewer choice")):
        item = projection.get(field)
        if not item:
            lines.append(f"- {label}: not recorded")
            continue
        node = item.get("node")
        lines.append(f"- {label}: {readable(node['id'] if node else None)} · {item['state']}")
        if node:
            fields = node["fields"]
            if field == "intake":
                lines.append(
                    f"- Recipe: {readable(fields.get('recipe'))}; "
                    f"baseline: {readable(fields.get('baseline'))}"
                )
                lines.append(f"- Outcome: {readable(fields.get('delta'))}")
            else:
                for reviewer in fields.get("reviewers", []):
                    lines.append(f"- Reviewer: {readable(reviewer)}")
                lines.append(
                    f"- Independence: {readable(item.get('independence'))}; "
                    f"diversity: {readable(item.get('diversity'))}"
                )
                lines.append(f"- Unmet assurance: {readable(item.get('unmetRequirements'))}")
            lines.append(
                f"- Supplied confirmation provenance: {readable(fields.get('confirmation'))}; "
                "human origin unverified; no approval granted"
            )
    lines.append(f"- Map: {projection['state']} · {readable(projection.get('reason'))}")
    plan = projection.get("waveMap") or (projection.get("mapNode") or {}).get("fields")
    if not plan:
        lines.append("- Obligations and eligibility: unavailable / not measured")
        return "\n".join(lines)
    nodes = projection.get("nodes", {})
    details = {
        node["fields"]["waveId"]: node["fields"]
        for node in projection.get("detailNodes", [])
        if node
    }
    by_id = {wave["id"]: wave for wave in plan["waves"]}
    accounting = projection.get("accounting")
    if accounting:
        full, ready = accounting["fullVision"], accounting["readiness"]
        lines.append(
            f"- Required obligations: {full['totalObligations']} total · "
            f"completed-real {full['completedRealObligations']} · "
            f"outstanding {full['outstandingObligations']}"
        )
        lines.append(
            "- Eligibility: not measured (publication/lock unavailable); "
            f"detailed {ready['detailedBeads']} · sketched {ready['sketchedItems']} · "
            f"dispatchable {ready['dispatchableNowCount']}"
        )
        lines.append(
            "- Historical measurements (not remeasured): "
            + readable(accounting["historicalMeasurements"])
        )
    else:
        total = sum(row["requiredForFullVision"] for row in plan["obligations"])
        lines.append(
            f"- Last recorded required denominator: {total}; "
            "current accounting unavailable / not measured"
        )
    for wid in plan["order"]:
        wave = by_id[wid]
        detail = details.get(wid, {})
        lines.extend(
            [
                f"**{readable(wid)} · {readable(wave['outcome'])} · "
                f"{detail.get('detail', 'unavailable')}**",
                f"- Demo: {readable(wave['demo'])}",
                f"- Wave dependencies: {readable(wave['dependsOnWaves'])}; "
                f"incoming: {readable(wave['incomingContractIds'])}; "
                f"outgoing: {readable(wave['outgoingContractIds'])}",
            ]
        )
        for sid in wave["exitScenarioIds"]:
            node = nodes.get(sid)
            lines.append(
                f"- Exit {readable(sid)} · {readable(node['title'] if node else None)}: "
                f"{readable(node['fields'] if node else None)}"
            )
        for bid in detail.get("beadIds", []):
            node = nodes.get(bid)
            lines.append(
                f"- Bead {readable(bid)} · {readable(node['title'] if node else None)}: "
                f"{readable(node['fields'] if node else None)}"
            )
        for sketch in detail.get("sketches", []):
            lines.append(
                f"- Sketch {readable(sketch['id'])} · {readable(sketch['title'])}; "
                f"owner area: {readable(sketch['ownershipArea'])}; "
                f"pending producers: {readable(sketch['pendingProducerIds'])}; "
                f"detail after: {readable(sketch['detailAfterWaveId'])}"
            )
        for row in plan["obligations"]:
            if wid in row["waveIds"]:
                lines.append(
                    f"- Obligation {readable(row['id'])}: {readable(row['criterion'])}; "
                    f"required evidence: {readable(row['requiredEvidence'])}"
                )
    if accounting:
        for state in accounting["obligationStates"]:
            lines.append(
                f"- {readable(state['id'])}: {state['state']} · {readable(state['reason'])}"
            )
    return "\n".join(lines)


def review_scope(row: dict) -> str:
    bound = row.get("packetBound")
    if bound is True:
        return "packet only (recorded)"
    if bound is False:
        return "outside packet: " + readable(row.get("outOfPacketTools", []))
    return "unknown"


def findings_card(rows: list[dict]) -> str:
    return data_card(
        "Findings",
        {
            "Records": len(rows),
            "Findings": [
                f"{row['id']} ({row['severity']}, {status(row)}): " + _short(row["finding"], 130)
                for row in rows
            ],
            "Review scope": "; ".join(sorted({review_scope(row) for row in rows})) or "no findings",
        },
    )


def roundtable_card(result: dict, rows: list[dict]) -> str:
    current = [row for row in rows if row["plan_revision"] == result["planRevision"]]
    return (
        review_card(result)
        + "\n"
        + data_card(
            "Roundtable: recorded review",
            {
                "Revision": result["planRevision"],
                "Cross-subscription": result["crossSubscription"],
                "Round budget": f"{result.get('maxRounds', 2)} per reviewer per revision",
                "Reviewers": [
                    f"{row['name']}: exit {row['exitCode']}; {row['invalid']} invalid; "
                    f"{row['findings']} findings"
                    for row in result["reviewers"]
                ],
                "Review scope": [
                    f"{row['name']}: {review_scope(row)}" for row in result["reviewers"]
                ],
                "Findings": [
                    f"{row['id']} ({status(row)}): {_short(row['finding'], 100)}" for row in current
                ],
                "Evidence": f"Mocked: {readable(result.get('isMocked'))}; no delivery proof.",
            },
        )
    )


def acceptance_run_card(run: dict) -> str:
    results = run["results"]
    failed = [row for row in results if not row["passed"]]
    outcome = f"{len(results) - len(failed)}/{len(results)}"
    outcome += " scenarios passed" if not failed else " passed"
    subject = "product" if run.get("subject") == "product" else "oracle dry run"
    lines = ["**Accept Run**", f"- **{outcome}** · {subject}"]
    if failed:
        lines[-1] += " · failing: " + ", ".join(row["id"] for row in failed)
    for row in failed:
        step = next((s for s in row.get("steps", []) if not s["passed"]), None)
        detail = (
            f"{_short(step['command'], 160)} · exit {step['exit']}"
            if step
            else row.get("error", "No failing step recorded")
        )
        lines.append(f"- {row['id']}: {_inline(detail)}")
    lines.extend([f"- Run: {run['id']}", f"- Revision: {run['revision']}"])
    return "\n".join(lines)


def review_card(result: dict, *, details=False) -> str:
    lines = [f"**Review: {result.get('roundId', result.get('id', 'acceptance'))}**"]
    if result.get("reviewIndependence") == "single-provider":
        lines.append(single_provider_notice(result))
    if result.get("findingCountsScope"):
        lines.append("Findings scope: " + result["findingCountsScope"])
    for row in result["reviewers"]:
        shards = row.get("shardDetails") or []
        completed = sum(s.get("exitCode") == 0 and not s.get("invalid") for s in shards)
        count = row.get(
            "findings",
            sum(
                f.get("reviewer") == row["name"]
                for f in result.get("findings", [])
                if isinstance(f, dict)
            )
            if isinstance(result.get("findings", []), list)
            else 0,
        )
        lines.append(
            f"- {row['name']}: shards {completed}/{row.get('shards', 'unknown')}; "
            f"packetBound: {readable(row.get('packetBound'))}; {count} findings"
        )
        if row.get("artifactPath"):
            lines.append("  Diagnostic: " + row["artifactPath"])
        if row.get("reshardedFrom"):
            retry = row["reshardedFrom"]
            lines.append(
                f"  Re-shard retries: 1 "
                f"({retry['shards']} → {retry.get('newShardCount', 'failed')})"
                + (f"; error: {_short(retry['reason'], 180)}" if retry.get("reason") else "")
            )
        for shard in shards:
            error = shard.get("primaryError", {}).get("reason")
            state = (
                "completed" if shard.get("exitCode") == 0 and not shard.get("invalid") else "failed"
            )
            if details or state == "failed":
                lines.append(
                    f"  Shard {shard['hash']}: {state}; "
                    f"estimated tokens {shard['estimatedTokens']}; "
                    f"features {readable(shard['features'])}; "
                    f"packetBound: {readable(shard.get('packetBound'))}"
                    + (
                        f"; error: {_short(error or readable(shard.get('rejected')), 180)}"
                        if state == "failed"
                        else ""
                    )
                )
        if "findingCounts" in row:
            lines.append("  Findings by status: " + readable(row["findingCounts"]))
    return "\n".join(lines)


def _short_revision(value) -> str:
    return str(value)[:12] if value else "none"


def _gate_lines(status: dict) -> list[str]:
    """User-facing gate checklist over the same recorded status; never new authority."""
    phase_index = PHASES.index(status["phase"]) if status["phase"] in PHASES else -1
    approval = status.get("approval") or {}
    if status.get("vision_lock_stale"):
        vision = "⚠ needs re-approval (the vision changed after it was locked)"
    elif status.get("pending_gate") == "approval:vision-lock":
        vision = "waiting for your approval"
    elif approval.get("gate") == "vision-lock" and approval.get("granted"):
        vision = "✓ approved"
    elif status.get("vision_locked_revision"):
        vision = "✓ locked (approval not current; see details)"
    else:
        vision = "not yet"
    resolution = status.get("reviewResolution") or {}
    audit = resolution.get("audit") or {}
    if audit.get("current"):
        audit_text = "✓ passing" + (
            f" ({status['proofLevelLabel']})" if status.get("proofLevelLabel") else ""
        )
    elif audit.get("applicable") is False:
        audit_text = "not applicable in semantic-v1; controller Validation required"
    elif audit.get("current") is False:
        audit_text = "needed for the current plan version"
    else:
        audit_text = "not run yet" if phase_index < PHASES.index("roundtable") else "unknown"
    review = resolution.get("review") or {}
    rounds = "; ".join(f"{row['reviewer']}: {row['text']}" for row in status["roundtable"])
    if review.get("current"):
        review_text = "✓ current" + (f" · {rounds}" if rounds else "")
        if review.get("carriedForward"):
            review_text = review["message"]
        if review.get("qualityStatus") in {"warn", "fail"} and review.get("advice"):
            review_text += f" · ⚠ {_short(review['advice'], 140)}"
    elif review.get("current") is False:
        review_text = "needs a fresh review of the current version" + (
            f" (last: {rounds})" if rounds else ""
        )
    else:
        review_text = rounds or "not yet"
    publication = status.get("publication") or {}
    if publication.get("up_to_date"):
        published = f"✓ up to date ({_short_revision(status['published_revision'])})"
    elif status.get("published_revision"):
        published = (
            f"⚠ out of date: the plan changed after {_short_revision(status['published_revision'])}"
        )
    else:
        published = "not yet"
    acceptance = status["acceptance"]
    passed = sum(item["lastResult"] == "pass" for item in acceptance["scenarios"])
    accepted = (
        "✓ " + approval_label(acceptance["approvedBy"], is_proxy=acceptance["isProxy"])
        if acceptance["accepted"]
        else "not yet"
    )
    return [
        f"- Vision lock: {vision}",
        f"- Test audit: {audit_text}",
        f"- Plan review: {review_text}",
        f"- Published: {published}",
        f"- Open: {status['open_clarifications']} questions · "
        f"{status.get('open_findings', 0)} open findings · "
        f"{status['open_assumptions']} assumptions",
        f"- Product acceptance: {accepted} · "
        f"{passed}/{len(acceptance['scenarios'])} scenarios proved by recorded runs",
        "- Worker readiness (advisory): "
        + str((status.get("readiness") or {}).get("status", "unknown")),
    ]


def status_card(status: dict, questions: list[dict]) -> str:
    phase = status["phase"]
    number = PHASES.index(phase) + 1 if phase in PHASES else "?"
    lines = [f"**Where things stand** · {phase.replace('-', ' ').title()} ({number} of 11)"]
    lines.extend(_gate_lines(status))
    if questions:
        lines.append("**Questions waiting for you**")
        lines.extend(f"- {row['id']}: {_inline(row['question'])}" for row in questions)
    projection = status.get("visionProjection")
    if projection:
        # The full understanding matters before the lock; afterwards one line is enough.
        if projection.get("isSummary"):
            lines.append(f"\n**Vision summary:** {projection['title']}")
            lines.append(
                f"Summary only for {projection['nodeId']} at node {projection['nodeRevision']} "
                f"(plan {projection['planRevision']}). Full vision: "
                f"harness_show_node id={projection['nodeId']}; "
                "full status: harness_plan_status full=true."
            )
        elif phase in {"align", "expand", "vision-lock"} or not status.get(
            "vision_locked_revision"
        ):
            lines.append("\n" + render_vision(projection))
        else:
            sections = {item["label"]: item["text"] for item in projection.get("sections", [])}
            summary = sections.get("Vision") or projection.get("title") or ""
            lines.append(f"**Vision:** {_short(summary, 220)}")
    if phase == "align":
        lines.append(setup_card(status.get("setup", {})))
    if status.get("planningTurn"):
        turn = status["planningTurn"]
        lines.append("\n" + turn["status"] + "\n" + turn["guidance"])
    return "\n".join(lines)


def status_details(status: dict) -> list[str]:
    """Compact footer lines: short ids and recorded review scope for the agent."""
    scopes = sorted(
        {review_scope(review) for row in status["roundtable"] for review in row.get("reviews", [])}
    )
    return [
        f"- Pending gate: {status['pending_gate'] or 'none'}",
        f"- Revisions: spine {_short_revision(status['spine_revision'])} · "
        f"manifest {_short_revision(status['manifest_revision'])} · "
        f"published {_short_revision(status['published_revision'])}",
        f"- Open findings: {status['open_findings']} clusters "
        f"({status['open_finding_members']} members)"
        + (f" · recorded review scope: {'; '.join(scopes)}" if scopes else ""),
    ]


def outcome_summary(projection: dict) -> str | None:
    """One line for status; the full map stays in harness_waves."""
    parts = []
    for field, label in (("intake", "intake"), ("reviewerSelection", "reviewer choice")):
        item = projection.get(field)
        if item:
            node = item.get("node")
            parts.append(f"{label} {readable(node['id'] if node else None)} ({item['state']})")
    if projection.get("waveMap") or projection.get("mapNode"):
        parts.append(f"map {projection['state']}")
    if not parts:
        return None
    return "- Outcome planning: " + " · ".join(parts) + " · full map: harness_waves"


def chat_diagram(spine, kind: str) -> str:
    nodes, edges = _projection(spine, kind)
    full = diagram(spine, kind)
    note = "Planned structure; not delivery proof."
    if len(nodes) > 40:
        groups = (
            _layers(nodes, edges)
            if kind == "bead-dag"
            else [
                [key for key in nodes if nodes[key]["kind"] == node_kind]
                for node_kind in sorted({n["kind"] for n in nodes.values()})
            ]
        )
        # Merge consecutive waves if a long serial DAG exceeds the chat node budget.
        size = max(1, (len(groups) + 39) // 40)
        groups = [sum(groups[i : i + size], []) for i in range(0, len(groups), size)]
        mapping = {key: index for index, group in enumerate(groups) for key in group}
        lines = ["flowchart LR"]
        for index, group in enumerate(groups):
            label = (
                f"Phases {index * size + 1}–{min((index + 1) * size, len(_layers(nodes, edges)))}"
                if kind == "bead-dag"
                else nodes[group[0]]["kind"].title()
            )
            lines.append(f'  g{index}["{_escape(label)}: {len(group)} planned nodes"]')
        lines.extend(
            f"  g{a} --> g{b}"
            for a, b in sorted(
                {(mapping[a], mapping[b]) for a, b in edges if mapping[a] != mapping[b]}
            )
        )
        full = "\n".join(lines) + "\n"
        note += (
            " Summarized by execution phase or node kind. Full diagram: "
            "harness_diagram structuredContent.mermaid / p diagram --kind " + kind + " --json."
        )
        fallback = "\n".join(f"- Group {i + 1}: {_names(group)}" for i, group in enumerate(groups))
    elif kind == "user-journey":
        # Quoted flowchart labels avoid journey's unquoted section/task grammar.
        from codex_harness.core.spine_diagrams import _ids, _journey_groups

        ids = _ids(spine)
        lines = ["flowchart LR"]
        for key, node in nodes.items():
            lines.append(f'  {ids[key]}["{_escape(node["id"] + ": " + node["title"])}"]')
        journey_edges = set()
        for feature, stories in _journey_groups(spine):
            sequence = ([feature] if feature else []) + stories
            journey_edges.update(zip(sequence, sequence[1:], strict=False))
        lines.extend(f"  {ids[a]} --> {ids[b]}" for a, b in sorted(journey_edges))
        if not nodes:
            fields = spine.node(spine.ids("vision")[0])["fields"]
            moment = readable(fields.get("first_user_moment", "First moment not planned"))
            lines.append(f'  first["{_escape(moment)}"]')
        full = "\n".join(lines) + "\n"
        fallback = text_fallback(spine, kind).rstrip()
    else:
        fallback = text_fallback(spine, kind).rstrip()
    return f"**Diagram: {kind}**\n{note}\n```mermaid\n{full.rstrip()}\n```\n{fallback}"


def setup_card(setup: dict) -> str:
    labels = {
        "projectFolder": "Project folder",
        "gitName": "Git name",
        "gitEmail": "Git email",
        "githubLogin": "GitHub login",
        "repoUrl": "Repository address",
    }
    return "\n".join(f"- {labels[key]}: {_inline(value)}" for key, value in setup.items())


def understanding_card(
    vision: str,
    users: str,
    first_user_moment: str,
    scope_in: str,
    scope_out: str,
    assumptions: list[str],
) -> str:
    return "\n".join(
        [
            "**Here's what I understood: is this right?**",
            f"- Vision: {_short(vision, 300)}",
            f"- Users: {_short(users, 300)}",
            f"- First user moment: {_short(first_user_moment, 300)}",
            f"- Scope in: {_short(scope_in, 300)}",
            f"- Scope out: {_short(scope_out, 300)}",
            f"- Assumptions: {readable(assumptions)}",
            "- Your call: confirm, or tell me what's wrong.",
        ]
    )


def decision_card(
    id: str,
    question: str,
    options: list[dict],
    recommendation: str,
    parents: list[str],
    chosen: str | None = None,
    rationale: str | None = None,
) -> str:
    if chosen is not None:
        if chosen not in {option["name"] for option in options}:
            raise ValueError("Chosen option must name one of the decision options")
        if not rationale or not rationale.strip():
            raise ValueError("Chosen option requires a nonempty rationale")
    elif rationale is not None:
        raise ValueError("Rationale requires a chosen option")

    def cell(value):
        return _inline(value).replace("|", "\\|").replace("`", "&#96;")

    lines = [
        f"**Decision {cell(id)}: {_inline(question)}**",
        f"- Recommendation: {_inline(recommendation)}",
        f"- Linked to: {_names(parents)}",
        "",
        "| Option | Pros | Cons |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        "| " + " | ".join(cell(option[k]) for k in ("name", "pros", "cons")) + " |"
        for option in options
    )
    lines.append("")
    if chosen is not None:
        lines.extend(
            [
                f"- Chosen: {_inline(chosen)}",
                f"- Rationale: {_inline(rationale)}",
                "- Recorded decision (not a gate approval or delivery proof).",
            ]
        )
    else:
        lines.append(
            "- Your call: reply with an option name ("
            + " / ".join(_inline(option["name"]) for option in options)
            + "), or explain the change you want."
        )
    return "\n".join(lines)


def change_digest(impact: dict) -> str:
    return "\n".join(
        [
            "**What changed**",
            f"- Changed nodes: {_names(impact['changed'])}",
            f"- Affected nodes: {_names(impact['affected'])}",
            f"- Affected beads: {_names(impact['affectedBeads'])}",
            f"- Untouched beads: {_names(impact['untouchedBeads'])}",
            f"- Affected tests: {_names(impact['affectedTests'])}",
            f"- Stale docs: {_names(impact['staleDocs'])}",
        ]
    )


def publish_summary(beads: list[dict], open_assumptions: int) -> str:
    remaining = {node["id"]: set(node["fields"]["dependsOn"]) for node in beads}
    lines = [
        "**Publication plan (planned, not proved)**",
        f"- Beads ({len(beads)}): {_names(remaining)}",
    ]
    wave = 1
    while remaining:
        ready = sorted(key for key, parents in remaining.items() if not parents & remaining.keys())
        if not ready:
            raise ValueError("Bead dependency cycle")
        label = "can run in parallel" if len(ready) > 1 else "single bead"
        lines.append(f"Wave {wave} ({label}): {', '.join(ready)}")
        for key in ready:
            del remaining[key]
        wave += 1
    waves = lines[2:]
    lines = lines[:2] + ["- Execution order: " + ("; ".join(waves) or "no beads")]
    lines.append(f"- Open assumptions: {open_assumptions}")
    return "\n".join(lines)
