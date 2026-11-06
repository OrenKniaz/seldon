"""Read-only views of canonical plan status and recorded dispatch receipts."""

import json
import sqlite3
import tomllib
from pathlib import Path

from codex_harness.core import planner_jobs
from codex_harness.core.plan_store import open_plan_store_readonly
from codex_harness.core.planner_consent_views import status_next
from codex_harness.core.planner_guidance import planning_turn
from codex_harness.core.planner_vision import vision_projection
from codex_harness.core.record_io import read_record_json
from codex_harness.core.spine import SpineError

UNAVAILABLE = "unavailable"


def plan_status(store):
    """The same canonical plan enrichment for chat and the read-only cockpit."""
    result = store.status()
    if not store._data()["nodes"]:
        return result
    spine = store.spine()
    result["continuedPlanChanges"] = []
    if consent := store._current_approval("B"):
        changes = store.connection.execute(
            "SELECT kind,payload_json "
            "FROM events WHERE (kind IN ('node-upserted','node-removed','spine-imported','protected-test-revised','wave-refinement-applied') OR json_type(payload_json,'$.nodeRevisions') IS NOT NULL) "
            "AND id > (SELECT id FROM events WHERE kind='confirmation-requested' "
            "AND json_extract(payload_json,'$.request_id')=?) ORDER BY 1", (consent["request_id"],))
        previous = consent["subject"]["scope"].get("files", {})
        files = planner_jobs.execution_inputs(store)["files"]
        updates = {path for path in previous.keys() | files.keys() if previous.get(path) != files.get(path)}
        for kind, raw in changes:
            payload = json.loads(raw)
            updates.update(payload.get("editedNodeIds", [payload.get("id", payload.get("nodeId", kind))]))
            for key in ("affectedBeads", "affectedTests", "affectedWaves", "impactedRequirementIds"):
                updates.update(payload.get("impact", {}).get(key, []))
        result["continuedPlanChanges"] = sorted(updates)
    visions = spine.ids("vision")
    if visions:
        vision = spine.node(visions[0])
        result["visionProjection"] = vision_projection(vision)
        result["setup"] = vision["fields"].get("setup", {})
        if result["phase"] in {"features", "requirements", "plan"}:
            result["planningTurn"] = planning_turn(spine)
    result["next"] = status_next(result, store=store)
    return result


def _plan(workspace):
    with open_plan_store_readonly(workspace) as store:
        result = plan_status(store)
        result["clarifications"] = store._open("clarifications")
        result["assumptions"] = store._open("assumptions")
        result["spine"] = (
            [store.spine().node(i) for i in store.spine().ids()] if store._data()["nodes"] else []
        )
        return result


def _run(path):
    """Show controller-recorded results; never revalidate originals or infer liveness."""
    record = read_record_json(path)
    keys = (
        "runId",
        "planRevision",
        "status",
        "startedAt",
        "finishedAt",
        "isMocked",
        "didExecuteRealProvider",
        "liveAcceptance",
        "semanticContracts",
        "semanticValidation",
    )
    result = {key: record.get(key) for key in keys}
    result["evidencePath"] = str(path)
    result["beads"] = [
        {
            "id": key,
            **{
                field: bead.get(field)
                for field in (
                    "status",
                    "provider",
                    "model",
                    "startedAt",
                    "finishedAt",
                    "integrated",
                    "revalidation",
                )
            },
        }
        for key, bead in record.get("beads", {}).items()
    ]
    return result


def _runs(root):
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(root)
    result = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        path = directory / "evidence" / "run.json"
        if not path.is_file():
            path = directory / "run.json"
        if path.is_file():
            result.append(_read(lambda path=path: _run(path)))
    return result


def snapshot(*, workspace=None, runs_root=None):
    """No initialization, proof readers, provider launches or status-store writes."""
    return {
        "plan": _read(lambda: _plan(workspace)) if workspace is not None else UNAVAILABLE,
        "runs": _read(lambda: _runs(runs_root)) if runs_root is not None else UNAVAILABLE,
        "savedPermissions": _saved_permissions(workspace),
        "usage": "unknown",
    }


def _read(call):
    try:
        return call()
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, SpineError):
        return UNAVAILABLE


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
