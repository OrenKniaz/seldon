"""Read-only cockpit projections; displayed evidence is never acceptance."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tomllib
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from codex_harness.core import proof_accounting
from codex_harness.core.attempt_activity import observe_attempt_activity
from codex_harness.core.plan_quality import quality_report
from codex_harness.core.plan_store import PHASES, PlanStore
from codex_harness.core.planner_guidance import planning_turn
from codex_harness.core.planner_vision import SECTIONS, label, vision_projection
from codex_harness.core.record_io import read_record_json
from codex_harness.core.roundtable import findings
from codex_harness.core.spine import SpineError
from codex_harness.core.spine_diagrams import diagram

UNAVAILABLE = "unavailable"


class _StatusPlanStore(PlanStore):
    def _meta(self):
        # Match the canonical additive migration default without migrating disk.
        return {"vision_lock_stale": 0, "locked_vision_json": None, **super()._meta()}


def _short_revision(value):
    return value[:12] if isinstance(value, str) and value != UNAVAILABLE else None


@contextmanager
def _database(path):
    """Avoid initializing stores or creating WAL sidecars on closed databases."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    wal = Path(str(path) + "-wal")
    active = wal.is_file() and wal.stat().st_size > 0
    if active and not Path(str(path) + "-shm").is_file():
        # Do not create a missing WAL index, nor silently omit committed WAL data.
        raise OSError("WAL index unavailable")
    uri = path.as_uri() + ("?mode=ro" if active else "?mode=ro&immutable=1")
    source = sqlite3.connect(uri, uri=True, isolation_level=None)
    db = sqlite3.connect(":memory:", isolation_level=None)
    try:
        source.backup(db)
        source.close()
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        yield db
    finally:
        source.close()
        db.close()


def _read(call):
    try:
        return call()
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, SpineError):
        return UNAVAILABLE


def _plan(workspace):
    with _database(Path(workspace) / ".codex-harness" / "plan.sqlite3") as db:
        # Apply empty additive acceptance tables only to the in-memory read copy.
        db.execute("PRAGMA query_only=OFF")
        db.execute(
            "CREATE TABLE IF NOT EXISTS acceptance_scenarios "
            "(id TEXT PRIMARY KEY, scenario_json TEXT NOT NULL)"
        )
        for table in ("runs", "reviews", "findings", "approvals"):
            db.execute(
                f"CREATE TABLE IF NOT EXISTS acceptance_{table} "
                "(id TEXT PRIMARY KEY, result_json TEXT NOT NULL)"
            )
        db.execute("PRAGMA query_only=ON")
        # Bind the canonical reader without invoking its initializing constructor.
        store = _StatusPlanStore.__new__(_StatusPlanStore)
        store.connection = db
        store.workspace = Path(workspace)
        result = store.status()
        result["phase_count"] = len(PHASES)
        current = PHASES.index(result["phase"])
        result["phases"] = [
            {
                "id": phase,
                "label": phase.replace("-", " ").title(),
                "state": "done"
                if index < current
                else "current"
                if index == current
                else "upcoming",
            }
            for index, phase in enumerate(PHASES)
        ]
        result["shortRevisions"] = {
            key: _short_revision(result.get(key))
            for key in ("plan_revision", "spine_revision", "manifest_revision")
        }
        result["quality"] = _read(lambda: quality_report(store))
        result["reviewRecorded"] = _read(
            lambda: bool(store.successful_reviews(result["manifest_revision"]))
        )
        result["clarifications"] = _read(lambda: store._open("clarifications"))
        result["assumptions"] = _read(lambda: store._open("assumptions"))
        result["spine"] = _read(lambda: _spine(store, result))
        result["vision"] = (
            next((node for node in result["spine"] if node["kind"] == "vision"), None)
            if isinstance(result["spine"], list)
            else None
        )
        if result["vision"]:
            projection = vision_projection(store.spine().node(result["vision"]["id"]))
            result["visionProjection"] = projection
            result["vision"] = {
                **result["vision"],
                "firstUserMoment": result["vision"]["fields"].get("first_user_moment"),
                "scopeIn": result["vision"]["fields"].get("scope_in"),
            }
        if result["phase"] in {"features", "requirements", "plan"}:
            result["planningTurn"] = planning_turn(store.spine())
        result["latestPublishedRevision"] = _read(lambda: _latest(db))
        result["latestPublishedRevisionShort"] = _short_revision(result["latestPublishedRevision"])
        result["mermaid"] = {
            kind: _read(lambda kind=kind: diagram(store.spine(), kind))
            for kind in ("bead-dag", "trace")
        }
        reviews = _read(lambda: _reviews(store))
        result["phaseHistory"] = _read(lambda: _phase_history(db))
        result["acceptanceEvidence"] = _read(lambda: _acceptance_evidence(db, result))
        # These canonical reads start only after status/quality transactions finish,
        # on the same in-memory copy; never initialize another on-disk store.
        detail = {
            "recordedReviews": _read(lambda: _recorded_reviews(store, result)),
            "recordedUnderstanding": _read(lambda: _recorded_understanding(store, result)),
            "waves": _read(store.outcome_waves),
            "scenarios": _read(store.acceptance_scenarios),
            "scenarioRun": _read(lambda: _latest_scenario_run(db)),
            "proofPlan": _read(lambda: proof_accounting.plan_context(store)),
        }
        return result, reviews, detail


def _latest_scenario_run(db):
    row = db.execute(
        "SELECT id, result_json FROM acceptance_runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    value = json.loads(row[1])
    if not isinstance(value, dict) or value.get("id") != row[0]:
        raise ValueError("Acceptance run identity differs from original record")
    return value


def _phase_history(db):
    """Historical transitions, not inferred approval of the current revision."""
    history = {}
    approvals = {}
    for row in db.execute(
        "SELECT kind, at, payload_json FROM events "
        "WHERE kind IN ('approved', 'phase-advanced') ORDER BY id"
    ):
        payload = json.loads(row["payload_json"])
        if row["kind"] == "approved":
            approvals[payload["gate"]] = payload
        if row["kind"] == "phase-advanced":
            phase = payload["from"]
            approval = approvals.get(phase, {})
            who = approval.get("by")
            history[phase] = {
                "phase": phase,
                "passedAt": row["at"]
                if PHASES.index(payload["to"]) > PHASES.index(phase)
                else None,
                "approvedBy": who,
                "isProxy": "proxy" in who.lower() if who else None,
                "advancedBy": payload.get("by"),
                "evidence": approval.get("statement") or payload.get("statement"),
                "revision": payload.get("plan_revision"),
            }
    return [
        history.get(
            phase,
            {
                "phase": phase,
                "passedAt": None,
                "approvedBy": None,
                "isProxy": None,
                "evidence": None,
            },
        )
        for phase in PHASES
    ]


def _acceptance_evidence(db, status):
    ids = {
        item.get("runId")
        for item in status["acceptance"]["scenarios"]
        if item.get("lastResult") in ("pass", "fail")
    }
    runs = [
        json.loads(row[0])
        for row in db.execute("SELECT result_json FROM acceptance_runs ORDER BY rowid")
    ]
    run = next((item for item in reversed(runs) if item["id"] in ids), None)
    if not run:
        return None
    reviews = [
        json.loads(row[0])
        for row in db.execute("SELECT result_json FROM acceptance_reviews ORDER BY rowid")
    ]
    review = next((item for item in reversed(reviews) if item.get("runId") == run["id"]), None)
    return {
        "runId": run["id"],
        "buildRevision": run.get("revision"),
        "reviewId": review["id"] if review else None,
    }


def _spine(store, status):
    if not store._data()["nodes"]:
        return []
    spine = store.spine()
    unresolved = set()
    global_question = False
    for key in ("clarifications", "assumptions"):
        for item in status[key] if isinstance(status[key], list) else []:
            refs = json.loads(item["node_ids_json"])
            unresolved.update(refs)
            global_question |= not refs
    nodes = []
    for node_id in spine.ids():
        node = spine.node(node_id)
        state = "draft"
        if status["publication"]["up_to_date"]:
            state = "published"
        if node["kind"] == "vision" and status.get("vision_locked_revision"):
            state = "agreed" if not status.get("vision_lock_stale") else "needs decision"
        if node_id in unresolved or (
            node["kind"] == "vision"
            and (global_question or str(status.get("pending_gate", "")).startswith("approval:"))
        ):
            state = "needs decision"
        nodes.append(
            {
                "id": node_id,
                "kind": node["kind"],
                "title": node["title"],
                "detail": node["text"],
                "state": state,
                "parents": spine.parents(node_id),
                "children": spine.children(node_id),
                "revisionShort": spine.revision(node_id)[:12],
                "fields": node["fields"],
            }
        )
    return nodes


def _latest(db):
    row = db.execute(
        "SELECT plan_revision FROM revisions ORDER BY published_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _reviews(store):
    groups = {}
    try:
        recorded = findings(store)
    except StopIteration as error:
        raise ValueError("Recorded finding membership unavailable") from error
    for item in recorded + store.acceptance_findings():
        groups.setdefault(item["status"], {}).setdefault(item["severity"], []).append(item)
    return groups


def _recorded_understanding(store, status):
    """Enrich an existing prerequisite, never infer an active prompt or approval."""
    from codex_harness.core.planner_confirmations import _understanding

    gate = status.get("pending_gate")
    if gate not in ("approval:align", "approval:vision-lock"):
        return None
    gate = gate.removeprefix("approval:")
    approval = status.get("approval")
    revision = status.get("plan_revision")
    if not isinstance(approval, dict) or (
        approval.get("granted") is not False
        or approval.get("gate") != gate
        or approval.get("plan_revision") != revision
        or status.get("spine_revision") != revision
    ):
        return None
    events = []
    for row in store.connection.execute(
        "SELECT id,kind,payload_json FROM events WHERE kind IN "
        "('confirmation-requested','confirmation-responded','phase-advanced') ORDER BY id"
    ):
        value = json.loads(row["payload_json"])
        if not isinstance(value, dict):
            return None
        if row["kind"] in ("confirmation-requested", "confirmation-responded") and (
            not isinstance(value.get("request_id"), str) or not value["request_id"].strip()
        ):
            return None
        events.append((row["id"], row["kind"], value))
    requests = [
        (event_id, value) for event_id, kind, value in events if kind == "confirmation-requested"
    ]
    if not requests:
        return None
    event_id, request = requests[-1]
    key = request.get("request_id")
    if (
        not isinstance(key, str)
        or not key.strip()
        or sum(r.get("request_id") == key for _, r in requests) != 1
    ):
        return None
    if any(
        (kind == "phase-advanced" and later > event_id)
        or (kind == "confirmation-responded" and value.get("request_id") == key)
        for later, kind, value in events
    ):
        return None
    if (
        request.get("gate") != gate
        or request.get("phase") != status.get("phase")
        or request.get("plan_revision") != revision
    ):
        return None
    spine = store.spine()
    visions = spine.ids("vision")
    if len(visions) != 1:
        return None
    vision = request.get("vision")
    if not isinstance(vision, dict) or vision != spine.node(visions[0]):
        return None
    phase = status["phase"]
    heading = f"Confirm {gate}" if phase == gate else f"Renew vision lock during {phase}"
    expected_card = (
        f"{heading} for revision {revision}.\n"
        "Review the complete recorded understanding below. Approve only this gate and "
        "revision. Retain your complete words; put any conditions or requested changes "
        "in conditions. A conditional response does not approve this gate.\n\n"
        + _understanding(vision)
    )
    if request.get("card") != expected_card or not isinstance(vision.get("title"), str):
        return None
    # This is a public excerpt, not the unrestricted canonical node/request.
    public_keys = {key for _, aliases in SECTIONS for key in aliases} | {"done_means"}
    fields = {key: value for key, value in vision["fields"].items() if key in public_keys}
    present = {
        heading
        for heading, aliases in SECTIONS
        if heading == "Vision" or any(key in fields for key in aliases)
    }
    present.update(label(key) for key in fields)
    present.add("Complete vision details")
    sections = [
        section
        for section in vision_projection({**vision, "fields": fields})["sections"]
        if section["label"] in present
    ]
    return {
        "title": vision["title"],
        "gate": gate,
        "sourceRevision": revision,
        "sections": sections,
        "chatConnection": "unknown",
        "notice": (
            "Recorded understanding only. The chat connection and whether a prompt "
            "is still open are unknown."
        ),
    }


def _recorded_reviews(store, status):
    """Allowlisted completed observations from this read copy; never infer liveness."""

    def identity(value):
        return isinstance(value, str) and bool(value.strip()) and value != UNAVAILABLE

    def count(value):
        return value if type(value) is int and value >= 0 else None

    def flag(value):
        return bool(value) if type(value) is int and value in (0, 1) else None

    def timestamp(value):
        return value if _timestamp(value) is not None else None

    result = {
        "availability": "available",
        "sourceRevision": status.get("plan_revision"),
        "currentManifestRevision": status.get("manifest_revision"),
        "reason": None,
        "rounds": [],
        "unboundRowCount": 0,
    }
    policy = status.get("reviewPolicy")
    current_revision = result["currentManifestRevision"]
    closure_facts = (
        policy.get("closureFindings", [])
        if isinstance(policy, dict)
        and policy.get("active") is True
        and identity(current_revision)
        and policy.get("manifestRevision") == policy.get("declaredRevision") == current_revision
        else []
    )

    def member_closure(finding, revision):
        matches = (
            [
                fact
                for fact in closure_facts
                if isinstance(fact, dict) and fact.get("id") == finding["id"]
            ]
            if isinstance(closure_facts, list)
            else []
        )
        if revision != current_revision or len(matches) != 1:
            return None
        fact = matches[0]
        if (
            fact.get("nodeId") != finding["node_id"]
            or fact.get("disposition") != finding["status"]
            or fact.get("recordedRevision") != revision
        ):
            return None
        declaration = fact.get("declaration")
        if declaration is not None and (
            not isinstance(declaration, dict) or fact.get("declarationBindingCurrent") is not True
        ):
            return None
        references = declaration.get("obligationNodeIds") if declaration is not None else None
        reasons = fact.get("reasons")
        return {
            "declarationBindingCurrent": fact.get("declarationBindingCurrent")
            if type(fact.get("declarationBindingCurrent")) is bool
            else None,
            "verification": fact.get("closure") if identity(fact.get("closure")) else None,
            "reasons": reasons
            if isinstance(reasons, list) and all(identity(r) for r in reasons)
            else None,
            "retainedObligation": fact.get("retainedObligation")
            if type(fact.get("retainedObligation")) is bool
            else None,
            "obligationVerification": fact.get("obligationVerification")
            if identity(fact.get("obligationVerification"))
            else None,
            "obligationNodeIds": references
            if isinstance(references, list) and all(identity(r) for r in references)
            else None,
        }

    tables = {
        row[0]
        for row in store.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "review_runs" not in tables:
        return result

    def partial():
        result.update(
            availability="partial", reason="Some recorded review details are unavailable."
        )

    columns = {row[1] for row in store.connection.execute("PRAGMA table_info(review_runs)")}
    allowed = (
        "id",
        "round_id",
        "plan_revision",
        "reviewer",
        "provider",
        "model",
        "exit_code",
        "findings",
        "invalid",
        "started_at",
        "finished_at",
        "isMocked",
        "didRunProvider",
        "packetBound",
        "shardDetails",
    )
    rows = (
        [
            dict(row)
            for row in store.connection.execute(
                "SELECT "
                + ",".join(name for name in allowed if name in columns)
                + " FROM review_runs"
            )
        ]
        if columns.intersection(allowed)
        else []
    )
    if not {"id", "round_id", "plan_revision", "reviewer", "provider"} <= columns:
        partial()
    events = []
    for row in store.connection.execute(
        "SELECT payload_json FROM events WHERE kind='roundtable-completed' ORDER BY id"
    ):
        try:
            event = json.loads(row[0])
            if not isinstance(event, dict):
                raise ValueError("not an event object")
            events.append(event)
        except (ValueError, TypeError):
            partial()
    try:
        normalized = _read(lambda: findings(store, expand=True))
    except StopIteration:
        normalized = UNAVAILABLE  # Broken recorded canonical membership; do not repair on GET.
    if not isinstance(normalized, list):
        partial()
    revisions, groups = {}, {}
    for row in rows:
        if not all(
            identity(row.get(key))
            for key in ("id", "round_id", "plan_revision", "reviewer", "provider")
        ):
            result["unboundRowCount"] += 1
            partial()
            continue
        key = (row["round_id"], row["plan_revision"])
        groups.setdefault(key, []).append(row)
        revisions.setdefault(key[0], set()).add(key[1])
    for (round_id, revision), members in groups.items():
        reviewers = []
        for row in members:
            exit_code = row.get("exit_code") if type(row.get("exit_code")) is int else None
            invalid = count(row.get("invalid"))
            finished = timestamp(row.get("finished_at"))
            error_code = (
                "nonzero-exit"
                if exit_code not in (None, 0)
                else ("invalid-return" if invalid is not None and invalid > 0 else None)
            )
            if row.get("shardDetails"):
                try:
                    shards = json.loads(row["shardDetails"])
                    if not isinstance(shards, list) or any(not isinstance(s, dict) for s in shards):
                        raise ValueError("not recorded shard objects")
                    if any(s.get("primaryError") for s in shards):
                        error_code = error_code or "recorded-error"
                except (ValueError, TypeError):
                    partial()
                    finished = None
            outcome = (
                "error"
                if error_code
                else ("completed" if finished and exit_code == 0 and invalid == 0 else "unknown")
            )
            reviewers.append(
                {
                    "id": row["id"],
                    "reviewer": row["reviewer"],
                    "provider": row["provider"],
                    "requestedModel": row.get("model") if identity(row.get("model")) else None,
                    "outcome": outcome,
                    "exitCode": exit_code,
                    "invalidCount": invalid,
                    "recordedFindingCount": count(row.get("findings")),
                    "startedAt": timestamp(row.get("started_at")),
                    "finishedAt": finished,
                    "isMocked": flag(row.get("isMocked")),
                    "didRunProvider": flag(row.get("didRunProvider")),
                    "packetBound": flag(row.get("packetBound")),
                    "errorCode": error_code,
                }
            )
        conflicting = len(revisions[round_id]) != 1
        current_manifest = result["currentManifestRevision"]
        relevance = (
            "unknown"
            if conflicting or not identity(current_manifest)
            else ("current" if revision == current_manifest else "historical")
        )
        expected = {(row["reviewer"], row["provider"]) for row in members}
        agreements = []
        for event in events:
            event_reviewers = event.get("reviewers")
            if event.get("roundId") != round_id or event.get("planRevision") != revision:
                continue
            if not isinstance(event_reviewers, list) or not all(
                isinstance(r, dict) for r in event_reviewers
            ):
                partial()
                agreements.append(False)
                continue
            observed = {
                (r.get("name"), r.get("provider"))
                for r in event_reviewers
                if isinstance(r.get("name"), str) and isinstance(r.get("provider"), str)
            }
            agreements.append(
                observed == expected and len(event_reviewers) == len(members) == len(expected)
            )
        # Identical terminal identities add no credit. A contradiction remains
        # unknown even if a later event repeats the stored reviewer identities.
        agreed = bool(agreements) and all(agreements)
        outcome = (
            "error"
            if any(r["outcome"] == "error" for r in reviewers)
            else (
                "completed"
                if agreed
                and not conflicting
                and all(r["outcome"] == "completed" for r in reviewers)
                else "unknown"
            )
        )
        clusters = None
        if isinstance(normalized, list):
            selected = [
                f
                for f in normalized
                if f.get("round_id") == round_id
                and f.get("plan_revision") == revision
                and f.get("stage", "roundtable") == "roundtable"
            ]
            clustered = {}
            for finding in selected:
                cluster_id = finding.get("cluster_id") or finding["id"]
                clustered.setdefault(cluster_id, []).append(finding)
            clusters = []
            for cluster_id, cluster_members in clustered.items():
                if not any(f["id"] == cluster_id for f in cluster_members):
                    partial()
                    clusters = None
                    break
                public_members = []
                for finding in cluster_members:
                    public_members.append(
                        {
                            "id": finding["id"],
                            "reviewer": finding["reviewer"],
                            "provider": finding["provider"],
                            "nodeId": finding["node_id"],
                            "runLinkage": "recorded"
                            if (finding["reviewer"], finding["provider"]) in expected
                            else "unknown",
                            "severity": finding["severity"],
                            "text": finding["finding"],
                            "suggestion": finding["suggestion"],
                            "disposition": finding["status"]
                            if finding["status"] in ("open", "resolved", "rejected", "deferred")
                            else "unknown",
                            "reason": finding.get("resolution"),
                            "closure": member_closure(finding, revision),
                            "recordedAt": timestamp(finding.get("created_at")),
                            "dispositionAt": timestamp(finding.get("resolved_at")),
                        }
                    )
                clusters.append(
                    {"id": cluster_id, "canonicalMemberId": cluster_id, "members": public_members}
                )
        result["rounds"].append(
            {
                "id": round_id,
                "reviewedManifestRevision": revision,
                "relevance": relevance,
                "outcome": outcome,
                "reviewers": reviewers,
                "clusters": clusters,
            }
        )
    result["rounds"].sort(
        key=lambda r: (
            max(
                (_timestamp(row["finishedAt"]) or float("-inf") for row in r["reviewers"]),
                default=float("-inf"),
            ),
            r["id"],
            r["reviewedManifestRevision"],
        ),
        reverse=True,
    )
    return result


def _right_now(plan, reviews, blocking, bound, runs):
    """Plain recorded facts only; no activity inferred from phase or reservations."""
    revision = plan.get("plan_revision")

    def summary(code, text, refs=()):
        return {
            "code": code,
            "text": text,
            "sourceRevision": revision,
            "refs": list(refs) or ([f"plan:{revision}"] if revision else []),
        }

    if not revision or revision == UNAVAILABLE:
        return summary("plan-unavailable", "Plan status is unavailable.")
    questions = plan.get("clarifications")
    if isinstance(questions, list) and questions:
        return summary(
            "recorded-questions",
            f"{len(questions)} recorded questions need your answer in chat.",
            [f"clarifications:{row['id']}" for row in questions],
        )
    gate = plan.get("pending_gate")
    if isinstance(gate, str) and gate.startswith("approval:"):
        return summary("recorded-confirmation", "Your plan needs your confirmation in chat.")
    if (
        isinstance(gate, str)
        and gate.startswith("acceptance:")
        and (plan.get("acceptance", {}).get("invalidReason") == "acceptance-approval-required")
    ):
        return summary(
            "recorded-acceptance-decision", "Your acceptance decision is needed in chat."
        )
    current = [row for row in reviews.get("rounds") or [] if row["relevance"] == "current"]
    if any(row["outcome"] == "error" for row in current):
        return summary("recorded-review-error", "A recorded plan review ended with an error.")
    if reviews.get("availability") == "available" and all(
        row["clusters"] is not None for row in current
    ):
        points = {
            cluster["id"]
            for row in current
            if row["outcome"] == "completed"
            for cluster in row["clusters"]
            if any(
                f["disposition"] == "open" and f["severity"] in ("blocker", "major")
                for f in cluster["members"]
            )
        }
        if points:
            return summary(
                "recorded-review-points",
                f"{len(points)} recorded review points still need a response.",
                [f"review-findings:{key}" for key in sorted(points)],
            )
    if blocking:
        return summary("recorded-blockers", "Recorded blockers need attention.")
    if bound:
        return summary(
            "recorded-build", "A build run is recorded; its current activity is unknown."
        )
    if isinstance(runs, list):
        return summary("no-current-build", "No current build run is linked to this plan.")
    return summary("recorded-status", "Plan status is recorded; current activity is unknown.")


def _inbox(workspace):
    with _database(Path(workspace) / ".codex-harness" / "mail.sqlite3") as db:
        return [
            {
                "id": row["id"],
                "subject": row["subject"],
                "body": row["body"],
                **_message_body(row["body"]),
                "from": row["from_agent"],
                "kind": row["kind"],
                "refs": json.loads(row["refs"]),
            }
            for row in db.execute(
                "SELECT * FROM messages WHERE to_agent='planner' AND acked_at IS NULL "
                "ORDER BY created_at, id"
            )
        ]


def _message_body(body):
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"displayBody": body, "bodyDetails": None}
    return {
        "displayBody": payload.get("reason", "Structured message; see details")
        if isinstance(payload, dict)
        else "Structured message; see details",
        "bodyDetails": payload,
    }


def _run(path, dependencies=None, published=None):
    record = read_record_json(path)
    result = {
        key: record.get(key, UNAVAILABLE)
        for key in (
            "runId",
            "startedAt",
            "status",
            "isMocked",
            "didExecuteRealProvider",
            "liveAcceptance",
            "planRevision",
            "planUpdate",
            "memoryWorkspace",
            "claimSummary",
        )
    }
    summary = record.get("resumeSummary", {})
    result.update({key: summary.get(key, []) for key in ("reused", "redispatched", "invalidated")})
    result["evidencePaths"] = [str(path)]
    result["planRevisionShort"] = _short_revision(record.get("planRevision"))
    result["timeline"] = _timeline(record)
    result["gantt"] = _gantt(
        record, dependencies or {} if record.get("planRevision") == published else {}
    )
    result["finishedAt"] = record.get("finishedAt")
    run_root = path.parent.parent if path.parent.name == "evidence" else path.parent
    result["productCommit"] = _product_commit(run_root / "repo")
    result["beads"] = []
    for bead_id, bead in record.get("beads", {}).items():
        integrated = bead.get("revalidation") or bead.get("integrated") or {}
        attempt = bead.get("attempt") or {}
        activity = _attempt_activity(run_root, bead_id, bead)
        paths = [str(path)]
        for evidence in (
            bead.get("evidenceDir"),
            (bead.get("integrated") or {}).get("receiptPath"),
            integrated.get("receiptPath"),
        ):
            if evidence:
                if evidence not in paths:
                    paths.append(evidence)
        passed = (integrated.get("validation") or {}).get("passed")
        result["beads"].append(
            {
                "id": bead_id,
                "status": bead.get("status", UNAVAILABLE),
                "activity": activity,
                "provider": bead.get("provider", UNAVAILABLE),
                "model": bead.get("model") or "unknown",
                "specRefs": [
                    f"{ref['id']}@{ref['revision'][:8]}"
                    for ref in bead.get("specRefs", bead.get("requirementRefs", []))
                ],
                "specRefDetails": bead.get("specRefs", bead.get("requirementRefs", [])),
                "efficiency": next(
                    (
                        value
                        for value in (
                            bead.get("efficiency"),
                            attempt.get("efficiency"),
                            (record.get("efficiency") or {}).get(bead_id),
                        )
                        if value is not None
                    ),
                    None,
                ),
                "changedPaths": bead.get("changedPaths", attempt.get("changedPaths", UNAVAILABLE)),
                "integratedValidation": "passed"
                if passed is True
                else "failed"
                if passed is False
                else UNAVAILABLE,
                "evidencePaths": paths,
                "evidenceItems": [
                    {
                        "path": evidence,
                        "folder": evidence
                        if evidence == bead.get("evidenceDir")
                        else str(Path(evidence).parent),
                        "label": "/".join(Path(evidence).parts[-2:]),
                    }
                    for evidence in paths
                ],
            }
        )
    result["activity"] = _activity_summary([bead["activity"] for bead in result["beads"]])
    return result


def _attempt_activity(run_root, bead_id, bead):
    try:
        evidence_root = (run_root / "evidence").resolve()
        directory = Path(bead.get("evidenceDir") or evidence_root / bead_id)
        if not directory.is_absolute():
            directory = run_root / directory
        directory = (directory / "attempt").resolve()
        if not evidence_root.is_relative_to(run_root.resolve()) or not directory.is_relative_to(
            evidence_root
        ):
            return observe_attempt_activity(None)
        observation = observe_attempt_activity(directory)
        status = bead.get("status", "")
        if (
            observation["isTerminal"] is not True
            and isinstance(status, str)
            and (status == "ambiguous launch" or status.startswith("held:"))
        ):
            return {
                **observation,
                "stuck": None,
                "reason": "Recorded hold/ambiguous launch retained; no recovery inferred",
            }
        return observation
    except (OSError, ValueError, TypeError):
        return observe_attempt_activity(None)


def _activity_summary(observations):
    known = [item for item in observations if item["stuck"] is not None]
    timestamps = [
        item["lastObservedActivityAt"] for item in known if item["lastObservedActivityAt"]
    ]
    intervals = [
        item["secondsSinceActivity"] for item in known if item["secondsSinceActivity"] is not None
    ]
    return {
        "stuckCount": sum(item["stuck"] is True for item in known) if known else None,
        "unknownCount": len(observations) - len(known),
        "terminalCount": sum(item["isTerminal"] is True for item in known) if known else None,
        "lastObservedActivityAt": max(timestamps) if timestamps else None,
        "maxSecondsSinceActivity": max(intervals) if intervals else None,
        "noProgressThresholdSeconds": 300,
    }


def _product_commit(repo):
    # Never let git discover a parent repository when the product repo is absent.
    if not (repo / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _gantt(record, dependencies):
    rows = []
    for bead_id, bead in record.get("beads", {}).items():
        attempt = bead.get("attempt") or {}
        claim = (bead.get("integrated") or {}).get("claim") or {}
        rows.append(
            {
                "id": bead_id,
                "provider": bead.get("provider", "unknown"),
                "status": bead.get("status", UNAVAILABLE),
                "dependsOn": dependencies.get(
                    bead_id, bead.get("dependsOn", list(claim.get("dependencyIdentities", {})))
                ),
                "start": _timestamp(attempt.get("startedAt")),
                "end": _timestamp(attempt.get("finishedAt")),
                "duration": (bead.get("efficiency") or attempt.get("efficiency") or {}).get(
                    "durationSeconds"
                ),
            }
        )
    timed = [row for row in rows if row["start"] is not None and row["end"] is not None]
    absolute = bool(timed)
    for row in rows:
        if row in timed:
            row["duration"] = max(0, row["end"] - row["start"])
    # Longest weighted dependency path; missing durations stay unknown in the UI.
    by_id = {row["id"]: row for row in rows}
    paths = {}

    def path_to(bead_id, visiting=frozenset()):
        if bead_id in paths:
            return paths[bead_id]
        if bead_id in visiting or bead_id not in by_id:
            return (0, [])
        row = by_id[bead_id]
        weight, chain = max(
            (path_to(parent, visiting | {bead_id}) for parent in row["dependsOn"]),
            default=(0, []),
            key=lambda item: item[0],
        )
        duration = row["duration"]
        duration = duration if isinstance(duration, int | float) and duration >= 0 else 0
        if not absolute:
            row["start"] = weight if row["duration"] is not None else None
            row["end"] = weight + duration if row["duration"] is not None else None
        paths[bead_id] = (weight + duration, chain + [bead_id])
        return paths[bead_id]

    for bead_id in by_id:
        path_to(bead_id)
    critical = max(paths.values(), default=(0, []), key=lambda item: item[0])[1]
    for row in rows:
        row["critical"] = row["id"] in critical
    integration = record.get("integrationValidation") or {}
    integration_end = _timestamp((integration.get("claim") or {}).get("at"))
    # Integration has a completion marker, but no measured start/duration in v1 receipts.
    rows.append(
        {
            "id": "integration",
            "provider": "integration",
            "status": "passed"
            if integration.get("passed") is True
            else "failed"
            if integration.get("passed") is False
            else UNAVAILABLE,
            "start": integration_end if absolute else None,
            "end": integration_end if absolute else None,
            "duration": None,
            "critical": False,
            "dependsOn": [],
        }
    )
    return {
        "timing": "absolute" if absolute else "relative",
        "rows": rows,
        "criticalPath": critical,
        "criticalPathComplete": all(row["duration"] is not None for row in rows[:-1]),
    }


def _timeline(record):
    """Record order, not invented timestamps or acceptance inferred from process exit."""
    events = []

    def add(kind, text, bead_id=None):
        events.append({"kind": kind, "text": text, "beadId": bead_id})

    update = record.get("planUpdate")
    if update:
        paths = update.get("paths", [])
        add("plan-update", "Plan updated" + (f" ({', '.join(paths)})" if paths else ""))
    summary = record.get("resumeSummary") or {}
    retained_ids = {
        item["claimId"] for item in (record.get("claimSummary") or {}).get("retained", [])
    }
    retained = {
        claim["bead"] for claim in record.get("claims", []) if claim["claimId"] in retained_ids
    }
    for bead_id, bead in record.get("beads", {}).items():
        if bead_id in summary.get("invalidated", []):
            add("invalidated", f"{bead_id} invalidated after plan change", bead_id)
        attempt = bead.get("attempt") or {}
        if attempt:
            action = "re-dispatched" if bead_id in summary.get("redispatched", []) else "dispatched"
            add("attempt", f"{bead_id} {action} ({bead.get('provider', 'unknown')})", bead_id)
        candidate = bead.get("candidate") or {}
        if candidate.get("validated") is True:
            add("candidate", f"{bead_id} candidate validated", bead_id)
        elif candidate.get("validated") is False and candidate.get("commit"):
            add("candidate", f"{bead_id} candidate validation failed", bead_id)
        integrated = bead.get("integrated") or {}
        passed = (integrated.get("validation") or {}).get("passed")
        if passed is not None:
            text = (
                "integrated validation passed" if passed is True else "integrated validation failed"
            )
            add("integration", f"{bead_id} {text}", bead_id)
        if bead_id in summary.get("reused", []):
            suffix = " (claims retained)" if bead_id in retained else ""
            add("reuse", f"{bead_id} reused{suffix}", bead_id)
        revalidation = bead.get("revalidation") or {}
        passed = (revalidation.get("validation") or {}).get("passed")
        if passed is not None:
            text = "final validation passed" if passed is True else "final validation failed"
            add("revalidation", f"{bead_id} {text}", bead_id)
    return events


def _runs(root, dependencies=None, published=None):
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(root)
    directories = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )[:10]
    result = []
    for directory in directories:
        path = directory / "evidence" / "run.json"
        if not path.is_file():
            path = directory / "run.json"
        run = _read(lambda path=path: _run(path, dependencies, published))
        result.append(
            run if isinstance(run, dict) else {"runId": directory.name, "status": UNAVAILABLE}
        )
    return result


def _agents(runs):
    if not isinstance(runs, list):
        return UNAVAILABLE
    groups = {}
    metrics = (
        "commands",
        "failedCommands",
        "deniedCommands",
        "redTestRuns",
        "errorCommands",
        "explorationCommands",
        "inputTokens",
        "cachedInputTokens",
        "outputTokens",
        "durationSeconds",
    )
    for run in runs:
        for bead in run.get("beads", []):
            key = (bead["provider"], bead["model"])
            agent = groups.setdefault(
                key,
                {
                    "provider": key[0],
                    "model": key[1],
                    "beads": 0,
                    "active": 0,
                    "activity": [],
                    **dict.fromkeys(metrics),
                },
            )
            agent["beads"] += 1
            activity = bead.get("activity") or observe_attempt_activity(None)
            agent["activity"].append(activity)
            agent["active"] += bead["status"] == "running" and activity["isTerminal"] is not True
            efficiency = bead["efficiency"] or {}
            for metric in metrics:
                value = efficiency.get(metric)
                if isinstance(value, int | float) and not isinstance(value, bool):
                    agent[metric] = (agent[metric] or 0) + value
    for agent in groups.values():
        agent["activity"] = _activity_summary(agent["activity"])
    return list(groups.values())


def _saved_permissions(workspace):
    """Allowlisted saved settings only; no active host policy can be inferred."""
    result = {
        "source": "project-local .codex/config.toml",
        "availability": "unknown",
        "saved": dict.fromkeys(
            ("approval_policy", "sandbox_mode", "seldon_tool_approval"), "unknown"
        ),
        "activePolicy": "unknown",
        "chatConnection": "unknown",
    }
    if workspace is None:
        return result
    try:
        root = Path(workspace).resolve(strict=True)
        config = (root / ".codex" / "config.toml").resolve(strict=True)
        if not config.is_relative_to(root):
            return result
        with config.open("rb") as handle:
            content = handle.read(65537)
        if len(content) > 65536:
            return result
        data = tomllib.loads(content.decode("utf-8"))
    except (OSError, ValueError, RuntimeError):
        return result
    servers = data.get("mcp_servers")
    seldon = servers.get("seldon") if isinstance(servers, dict) else None
    observed = {
        "approval_policy": data.get("approval_policy"),
        "sandbox_mode": data.get("sandbox_mode"),
        "seldon_tool_approval": seldon.get("default_tools_approval_mode")
        if isinstance(seldon, dict)
        else None,
    }
    allowed = {
        "approval_policy": {"never", "on-request", "untrusted", "on-failure"},
        "sandbox_mode": {"read-only", "workspace-write", "danger-full-access"},
        "seldon_tool_approval": {"auto", "prompt", "writes", "approve"},
    }
    for key, value in observed.items():
        if isinstance(value, str) and value in allowed[key]:
            result["saved"][key] = value
    result["availability"] = "available"
    return result


def snapshot(*, workspace=None, runs_root=None) -> dict:
    """Project canonical plan, run receipts and planner mail without mutations."""
    plan = _read(lambda: _plan(workspace)) if workspace is not None else UNAVAILABLE
    published = plan[0].get("published_revision") if isinstance(plan, tuple) else None
    nodes = plan[0].get("spine") if isinstance(plan, tuple) else []
    dependencies = (
        {
            node["id"]: node["fields"].get("dependsOn", [])
            for node in nodes
            if node["kind"] == "bead"
        }
        if isinstance(nodes, list)
        else {}
    )
    runs = (
        _read(lambda: _runs(runs_root, dependencies, published))
        if runs_root is not None
        else UNAVAILABLE
    )
    current = None
    if isinstance(runs, list) and runs:
        matching = [run for run in runs if published and run.get("planRevision") == published]
        current = max(
            matching or runs,
            key=lambda run: (
                run.get("startedAt") not in (None, UNAVAILABLE, ""),
                run.get("startedAt")
                if run.get("startedAt") not in (None, UNAVAILABLE, "")
                else run["runId"],
                run["runId"],
            ),
        )
        for run in runs:
            run["current"] = run is current
            if run is not current:
                run["supersededBy"] = current["runId"]
                run["reason"] = (
                    "older run of the same plan revision"
                    if run.get("planRevision") == current.get("planRevision")
                    else "different plan revision"
                )
    mail = {
        "plan": _read(lambda: _inbox(workspace)) if workspace is not None else UNAVAILABLE,
        "runs": {},
        "earlierRuns": {},
    }
    if isinstance(runs, list):
        for run in runs:
            memory = run.get("memoryWorkspace")
            mail["runs"][run["runId"]] = (
                _read(lambda memory=memory: _inbox(memory))
                if memory and memory != UNAVAILABLE
                else UNAVAILABLE
            )
            if run.get("supersededBy"):
                mail["earlierRuns"][run["runId"]] = mail["runs"][run["runId"]]
    current_mail = mail["runs"].get(current["runId"], []) if current else []
    mail["openCount"] = sum(
        len(
            {
                (message["id"], message["from"], message["subject"], message["body"])
                for message in messages
            }
        )
        for messages in (mail["plan"], current_mail)
        if isinstance(messages, list)
    )
    result = {
        "savedPermissions": _saved_permissions(workspace),
        "plan": plan[0] if isinstance(plan, tuple) else UNAVAILABLE,
        "runs": runs,
        "mail": mail,
        "reviews": plan[1] if isinstance(plan, tuple) else UNAVAILABLE,
        "agents": _agents(runs),
        "currentRunId": current["runId"] if current else None,
        "currentAgents": _agents([current] if current else [])
        if isinstance(runs, list)
        else UNAVAILABLE,
        "labels": {
            "localRuns": "local run · not live acceptance",
            "proof": "The board displays evidence; it is not proof or acceptance.",
            "usage": "unknown",
        },
    }
    result["verdict"] = _verdict(result["plan"], result["reviews"], current)
    result["efficiencyTrend"] = _efficiency_trend(runs, current)
    if isinstance(plan, tuple):
        observed = _read(
            lambda: proof_accounting.observe(
                plan[2].get("proofPlan"),
                run_path=(current or {}).get("evidencePaths", [None])[0],
                runs_root=runs_root,
                publication_current=plan[0].get("publication", {}).get("up_to_date") is True,
            )
        )
        plan[2]["proofAccounting"] = observed if isinstance(observed, dict) else None
    cockpit = _read(lambda: _cockpit(result, plan[2] if isinstance(plan, tuple) else {}, current))
    result["cockpit"] = cockpit if isinstance(cockpit, dict) else None
    return result


def _cockpit_scenario_proof(plan, detail, ids):
    """Scenario command checks only; canonical status already checks current bindings."""
    run = detail.get("scenarioRun")
    scenarios = (plan.get("acceptance") or {}).get("scenarios")
    if not ids or not isinstance(run, dict) or not isinstance(scenarios, list):
        return None
    by_id = {item["id"]: item for item in scenarios}
    if (
        run.get("subject") != "product"
        or run.get("isMocked") is True
        or run.get("planRevision") != plan.get("plan_revision")
        or run.get("revisionAlgorithm") != "tree-bytes-v2"
        or not run.get("revision")
        or any(
            key not in by_id
            or by_id[key].get("runId") != run.get("id")
            or by_id[key].get("lastResultSubject") != "product"
            or by_id[key].get("lastResult") not in {"pass", "fail"}
            for key in ids
        )
    ):
        return None
    return {
        "passed": sum(by_id[key]["lastResult"] == "pass" for key in ids),
        "total": len(ids),
        "runId": run["id"],
        "subject": "product",
        "buildRevision": run["revision"],
        "evidenceRefs": [
            f"acceptance_runs:{run['id']}",
            *[f"acceptance_scenarios:{key}" for key in sorted(ids)],
        ],
    }


def _cockpit_bound_run(plan, current):
    publication = plan.get("published_revision")
    return bool(
        publication
        and publication != UNAVAILABLE
        and isinstance(current, dict)
        and plan.get("publication", {}).get("up_to_date") is True
        and current.get("planRevision") == publication
    )


def _cockpit_work(ids, plan, current):
    counts = dict(total=len(ids), done=None, active=None, blocked=None, later=None)
    if not _cockpit_bound_run(plan, current):
        return counts
    states = {}
    for bead in current.get("beads", []):
        status = bead.get("status")
        category = (
            "done"
            if status in {"requirement-validated", "reused"}
            else "active"
            if status == "running" and (bead.get("activity") or {}).get("isTerminal") is not True
            else "later"
            if status == "pending"
            else "blocked"
            if isinstance(status, str)
            and status.startswith(
                ("blocked", "held:", "provider ", "prelaunch failed", "bead failed")
            )
            else None
        )
        states[bead["id"]] = category
    if all(states.get(key) is not None for key in ids):
        counts.update(
            {
                name: sum(states[key] == name for key in ids)
                for name in ("done", "active", "blocked", "later")
            }
        )
    return counts


def _cockpit_waves(detail, revision):
    projection = detail.get("waves")
    if not isinstance(projection, dict) or projection.get("state") == "unavailable":
        return {
            "status": "unavailable",
            "reason": "Waves not tracked for this plan yet",
            "items": None,
        }
    plan = projection.get("waveMap")
    items = None
    if isinstance(plan, dict):
        by_id = {row["id"]: row for row in plan["waves"]}
        details = {row["waveId"]: row for row in projection.get("waveDetails", [])}
        items = [
            {
                "id": key,
                "title": by_id[key]["outcome"],
                "outcome": by_id[key]["outcome"],
                "phase": "draft plan",
                "isDraft": True,
                "itemCounts": dict(
                    total=len(details[key]["beadIds"])
                    if key in details and details[key]["detail"] == "detailed"
                    else None,
                    done=None,
                    active=None,
                    blocked=None,
                    later=None,
                ),
            }
            for key in plan["order"]
        ]
    return {
        "status": "draft",
        "reason": projection.get("reason"),
        "sourceRevision": revision,
        "items": items,
        "projection": deepcopy(projection),
    }


def _cockpit(report, detail, current):
    """Optional presentation facts. No new evidence binding or approval authority."""
    plan = report.get("plan") if isinstance(report.get("plan"), dict) else {}
    bound = _cockpit_bound_run(plan, current)
    current_answers = current if bound else None
    run_available = isinstance(current, dict) and current.get("status") != UNAVAILABLE
    run_reason = (
        None
        if bound
        else "Selected run evidence unavailable"
        if current is not None and not run_available
        else "No run bound to a current publication"
        if current is None
        else "Run facts are historical/unbound: publication is stale, absent or mismatched"
    )
    nodes = plan.get("spine") if isinstance(plan.get("spine"), list) else None
    by_id = {item["id"]: item for item in nodes or []}
    features = [item for item in nodes or [] if item["kind"] == "feature"]
    vision = plan.get("vision") if isinstance(plan.get("vision"), dict) else None
    scenarios = detail.get("scenarios") if isinstance(detail.get("scenarios"), list) else None
    scenario_ids = {item["id"] for item in scenarios or []}
    links = {}
    for key in scenario_ids:
        node = by_id.get(key, {})
        parents = [
            parent
            for parent in node.get("parents", [])
            if by_id.get(parent, {}).get("kind") == "feature"
        ]
        if node.get("kind") == "acceptance" and len(parents) == 1:
            links[key] = parents[0]
    requirement_reason = (
        "Requirement validation unavailable: original current claim/spec/publication "
        "and unmocked integration binding not established"
    )

    def proof(ids):
        checks = _cockpit_scenario_proof(plan, detail, ids)
        return {
            "proof": {"productScenarios": checks, "requirementValidation": None},
            "proofUnavailableReason": requirement_reason
            + (
                "; product scenario checks unavailable: no current bound product result "
                "or unambiguous scenario linkage"
                if checks is None
                else ""
            ),
        }

    def descendants(key):
        found, pending = set(), list(by_id[key]["children"])
        while pending:
            child = pending.pop()
            if child not in found:
                found.add(child)
                pending.extend(by_id[child]["children"])
        return found

    capabilities = [] if nodes is not None else None
    for feature in features:
        beads = {key for key in descendants(feature["id"]) if by_id[key]["kind"] == "bead"}
        capabilities.append(
            {
                "id": feature["id"],
                "title": feature["title"],
                "state": feature["state"],
                "itemIds": sorted(beads),
                "items": _cockpit_work(beads, plan, current),
                **proof({key for key, parent in links.items() if parent == feature["id"]}),
            }
        )
    acceptance = plan.get("acceptance") if isinstance(plan.get("acceptance"), dict) else {}
    projection = plan.get("visionProjection")
    if vision is not None and projection is None:
        projection = vision_projection({**vision, "text": vision["detail"]})
    vision_view = (
        None
        if vision is None
        else {
            "title": vision["title"],
            "promise": projection["sections"][0]["text"],
            "sections": projection["sections"],
            "acceptance": {
                key: deepcopy(acceptance.get(key))
                for key in (
                    "accepted",
                    "recordedUserAccepted",
                    "isProxy",
                    "approvedBy",
                    "approvalOrigin",
                    "humanOriginVerified",
                    "invalidReason",
                )
            },
            "items": _cockpit_work(
                {key for key, node in by_id.items() if node["kind"] == "bead"}, plan, current
            ),
            "scenarioScope": None
            if scenarios is None
            else {"total": len(scenario_ids), "unlinkedIds": sorted(scenario_ids - links.keys())},
            "proofAccounting": detail.get("proofAccounting"),
            **proof(scenario_ids),
        }
    )
    questions = plan.get("clarifications")
    needs = (
        None
        if not isinstance(questions, list)
        else [
            {
                "id": row["id"],
                "question": row["question"],
                "kind": "decision",
                "whereToAnswer": "chat",
                "refs": [f"clarifications:{row['id']}"],
            }
            for row in questions
        ]
    )
    gate = plan.get("pending_gate")
    if isinstance(gate, str) and (
        gate.startswith("approval:")
        or acceptance.get("invalidReason") == "acceptance-approval-required"
    ):
        needs = [
            *(needs or []),
            {
                "id": gate,
                "question": f"Recorded approval required: {gate}",
                "kind": "acceptance" if gate.startswith("acceptance:") else "approval",
                "whereToAnswer": "chat",
                "refs": [f"plan:{plan.get('plan_revision')}"],
            },
        ]
    understanding = detail.get("recordedUnderstanding")
    if isinstance(understanding, dict):
        for item in needs or []:
            if item["id"] == f"approval:{understanding['gate']}" and item["kind"] == "approval":
                item["recordedUnderstanding"] = deepcopy(understanding)
    reviews = report.get("reviews")
    blocking = None if not isinstance(reviews, dict) and current_answers is None else []
    if isinstance(reviews, dict):
        for severity, rows in reviews.get("open", {}).items():
            if severity in {"blocker", "critical", "major"}:
                for row in rows:
                    blocking.append(
                        {
                            "id": row["id"],
                            "plain": row["finding"],
                            "affectedItemIds": [row["node_id"]] if row.get("node_id") else None,
                            "refs": [f"review-findings:{row['id']}"],
                        }
                    )
    if isinstance(current_answers, dict):
        for bead in current_answers.get("beads", []):
            if isinstance(bead.get("status"), str) and bead["status"].startswith(
                ("blocked", "held:", "provider ", "prelaunch failed", "bead failed")
            ):
                blocking.append(
                    {
                        "id": f"{current_answers['runId']}:{bead['id']}",
                        "plain": bead["status"],
                        "affectedItemIds": [bead["id"]],
                        "refs": [f"run:{current_answers['runId']}", bead["id"]],
                    }
                )
    chronicle = []
    for event in plan.get("phaseHistory", []) if isinstance(plan.get("phaseHistory"), list) else []:
        if event.get("passedAt"):
            chronicle.append(
                {
                    "at": event["passedAt"],
                    "text": f"Recorded phase transition: {event['phase']}",
                    "refs": [f"phase:{event['phase']}", event.get("revision")],
                }
            )
    for run in report.get("runs", []) if isinstance(report.get("runs"), list) else []:
        for event in run.get("timeline", []):
            chronicle.append(
                {
                    "text": event["text"],
                    "historical": not _cockpit_bound_run(plan, run),
                    "refs": [
                        f"run:{run['runId']}",
                        *([event["beadId"]] if event.get("beadId") else []),
                    ],
                }
            )
    recorded_reviews = detail.get("recordedReviews")
    if not isinstance(recorded_reviews, dict):
        recorded_reviews = {
            "availability": "unavailable",
            "sourceRevision": plan.get("plan_revision"),
            "currentManifestRevision": plan.get("manifest_revision"),
            "reason": "Recorded review details are unavailable.",
            "rounds": None,
            "unboundRowCount": None,
        }
    return {
        "source": {
            "planRevision": plan.get("plan_revision"),
            "publicationRevision": plan.get("published_revision"),
            "reason": run_reason if plan else "Canonical plan unavailable",
            "runBinding": {
                "status": "current" if bound else "historical" if run_available else "unavailable",
                "runId": (current or {}).get("runId"),
                "reason": run_reason,
            },
            "areaUnavailableReason": "No canonical area grouping in the current spine contract",
        },
        "vision": vision_view,
        "answers": {
            "where": {
                "stage": plan.get("phase"),
                "label": "Current",
            },
            "blocking": blocking,
            "needsYou": needs,
            "reviewReadiness": deepcopy(plan.get("readiness")),
            "rightNow": _right_now(plan, recorded_reviews, blocking, bound, report.get("runs")),
        },
        "areas": [] if nodes is not None else None,
        "capabilities": capabilities,
        "waves": _cockpit_waves(detail, plan.get("plan_revision")),
        "crew": deepcopy(report.get("currentAgents"))
        if bound and isinstance(report.get("currentAgents"), list)
        else None,
        "chronicle": chronicle if plan or isinstance(report.get("runs"), list) else None,
        "recordedReviews": recorded_reviews,
    }


def _verdict(plan, reviews, current):
    if not isinstance(plan, dict):
        return {
            "tone": "neutral",
            "sentence": "Plan unavailable · connect a workspace to see its status.",
            "chips": [],
        }
    acceptance = plan.get("acceptance") or {}
    scenarios = acceptance.get("scenarios", [])
    passed = sum(item.get("lastResult") == "pass" for item in scenarios)
    failed = sum(item.get("lastResult") == "fail" for item in scenarios)
    groups = reviews if isinstance(reviews, dict) else {}
    deferred = sum(len(items) for items in groups.get("deferred", {}).values())
    blockers = sum(
        len(items)
        for severity, items in groups.get("open", {}).items()
        if severity in ("critical", "major", "blocker")
    )
    open_count = sum(len(items) for items in groups.get("open", {}).values())
    evidence = plan.get("acceptanceEvidence") or {}
    accepted = acceptance.get("accepted") is True
    proxy = acceptance.get("isProxy") is True
    stale = bool(current and current.get("planRevision") != plan.get("published_revision")) or any(
        "stale" in str(item.get("lastResult")) for item in scenarios
    )
    state = (
        "Accepted by proxy"
        if accepted and proxy
        else "Accepted (recorded)"
        if accepted
        else "Not accepted yet"
        if scenarios
        else "Planning"
    )
    parts = [state]
    if scenarios:
        scenario_text = f"{passed}/{len(scenarios)} acceptance scenarios pass"
        commit = current.get("productCommit") if current else None
        scenario_text += f" on build {commit}" if commit else " · build not recorded"
        parts.append(scenario_text)
    if failed:
        parts.append(f"{failed} failing scenarios")
    if blockers:
        parts.append(f"{blockers} open blockers")
    elif open_count:
        parts.append(f"{open_count} open findings")
    if deferred:
        parts.append(f"{deferred} known gaps deferred")
    if stale:
        parts.append("stale run evidence")
    if accepted and not acceptance.get("humanOriginVerified"):
        parts.append("origin not verified")
    tone = (
        "red"
        if failed or blockers
        else "amber"
        if proxy or deferred or stale or open_count
        else "green"
        if accepted
        else "neutral"
    )
    chips = [
        {"label": label, "value": value or "not recorded"}
        for label, value in (
            ("Plan", plan.get("shortRevisions", {}).get("plan_revision")),
            ("Current run", current.get("runId") if current else None),
            ("Acceptance run", evidence.get("runId")),
            ("Review", evidence.get("reviewId")),
            ("tree", evidence.get("buildRevision")),
        )
    ]
    return {"tone": tone, "sentence": " · ".join(parts), "chips": chips}


def _efficiency_trend(runs, current):
    metrics = ("commands", "errorCommands", "deniedCommands", "inputTokens", "outputTokens")

    def totals(run):
        result = {}
        for metric in metrics:
            values = [(bead.get("efficiency") or {}).get(metric) for bead in run.get("beads", [])]
            result[metric] = (
                sum(values)
                if values and all(isinstance(value, int | float) for value in values)
                else None
            )
        start, end = _timestamp(run.get("startedAt")), _timestamp(run.get("finishedAt"))
        result["wallSeconds"] = (
            max(0, end - start) if start is not None and end is not None else None
        )
        return result

    if not current:
        return {"current": None, "previousRunId": None, "deltas": {}}
    older = [
        run
        for run in runs
        if run is not current
        and run.get("planRevision") == current.get("planRevision")
        and run.get("planRevision") not in (None, UNAVAILABLE)
    ]
    previous = max(
        older,
        key=lambda run: (
            run.get("startedAt") not in (None, UNAVAILABLE, ""),
            run.get("startedAt")
            if run.get("startedAt") not in (None, UNAVAILABLE, "")
            else run["runId"],
            run["runId"],
        ),
        default=None,
    )
    current_totals, previous_totals = totals(current), totals(previous) if previous else {}
    return {
        "current": current_totals,
        "previousRunId": previous["runId"] if previous else None,
        "deltas": {
            key: value - previous_totals[key]
            if value is not None and previous_totals.get(key) is not None
            else None
            for key, value in current_totals.items()
        },
    }
