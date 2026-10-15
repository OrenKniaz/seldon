"""Read-only cockpit projections; displayed evidence is never acceptance."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from codex_harness.core.plan_quality import quality_report
from codex_harness.core.plan_store import PHASES, PlanStore
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
            result["vision"] = {
                **result["vision"],
                "firstUserMoment": result["vision"]["fields"].get("first_user_moment"),
                "scopeIn": result["vision"]["fields"].get("scope_in"),
            }
        result["latestPublishedRevision"] = _read(lambda: _latest(db))
        result["latestPublishedRevisionShort"] = _short_revision(result["latestPublishedRevision"])
        result["mermaid"] = {
            kind: _read(lambda kind=kind: diagram(store.spine(), kind))
            for kind in ("bead-dag", "trace")
        }
        reviews = _read(lambda: _reviews(store))
        result["phaseHistory"] = _read(lambda: _phase_history(db))
        result["acceptanceEvidence"] = _read(lambda: _acceptance_evidence(db, result))
        return result, reviews


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
    for item in findings(store) + store.acceptance_findings():
        groups.setdefault(item["status"], {}).setdefault(item["severity"], []).append(item)
    return groups


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
    record = json.loads(path.read_text(encoding="utf-8"))
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
    return result


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
                    **dict.fromkeys(metrics),
                },
            )
            agent["beads"] += 1
            agent["active"] += bead["status"] == "running"
            efficiency = bead["efficiency"] or {}
            for metric in metrics:
                value = efficiency.get(metric)
                if isinstance(value, int | float) and not isinstance(value, bool):
                    agent[metric] = (agent[metric] or 0) + value
    return list(groups.values())


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
    return result


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
