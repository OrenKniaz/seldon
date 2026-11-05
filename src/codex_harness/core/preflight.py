"""Read-only prerequisites and supplied planning inputs; never launches a provider."""

from __future__ import annotations

import stat
from pathlib import Path

from codex_harness.core.provider_launch import get_adapter
from codex_harness.core.roundtable import _executable

SUPPORTED_PROVIDERS = ("codex", "claude", "copilot")


def _path_status(path: Path, *, directory: bool) -> dict:
    try:
        mode = path.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return {"path": str(path), "status": "missing"}
    except OSError as error:
        return {"path": str(path), "status": "unavailable", "reason": type(error).__name__}
    correct_kind = stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)
    return {
        "path": str(path),
        "status": "available" if correct_kind else "unavailable",
        "reason": "Existence only; contents and access not verified"
        if correct_kind
        else f"Expected {'directory' if directory else 'file'}",
    }


def preflight_report(
    workspace: str | Path,
    providers=None,
    *,
    executable_for=None,
    execution=None,
    catalog=None,
    reviewer_node=None,
) -> dict:
    """Observe executables and canonical planning prerequisites without executing them."""
    if providers is None:
        providers = list(SUPPORTED_PROVIDERS)
    if (
        not isinstance(providers, list)
        or not providers
        or any(not isinstance(name, str) or not name.strip() for name in providers)
    ):
        raise ValueError("preflight-providers-invalid: expected a nonempty provider list")
    names = list(dict.fromkeys(name.strip().lower() for name in providers))
    adapters = {name: get_adapter(name) for name in names}
    workspace = Path(workspace).resolve()
    observations = []
    for name, adapter in adapters.items():
        try:
            executable = _executable(name, executable_for)
            status = "available" if executable else "missing"
            reason = (
                "Resolved by existing executable discovery"
                if executable
                else "No executable resolved"
            )
        except OSError as error:
            executable, status, reason = None, "unavailable", type(error).__name__
        launch = adapter.provider_normal_command(prompt="", prompt_via_stdin=True)
        observations.append(
            {
                "provider": name,
                "executable": {"path": executable, "status": status, "reason": reason},
                "authentication": "unknown",
                "effectiveProtections": "unknown",
                "requestedProtectionNotes": list(launch.protections),
            }
        )
    from codex_harness.core.plan_store import open_plan_store_readonly
    from codex_harness.core.planner_confirmations import planning_prerequisites
    from codex_harness.core.planner_review import selection_inputs
    from codex_harness.core.planner_schema import typed_draft_help

    missing = (
        "harness_plan_start reviewer-selection-required-before-B "
        "execution.expected_revision execution.workers"
    ).split()
    revision = "unknown"
    if (workspace / ".codex-harness/plan.sqlite3").is_file():
        with open_plan_store_readonly(workspace) as store:
            revision = store._revision()
            _, reviewer_node, catalog = selection_inputs(
                store, catalog=catalog, node=reviewer_node
            )
            _, missing = planning_prerequisites(
                store, execution, catalog=catalog, reviewer_node=reviewer_node
            )
    return {
        "schema": "seldon-preflight-v1",
        "workspace": _path_status(workspace, directory=True),
        "planDatabase": _path_status(
            workspace / ".codex-harness" / "plan.sqlite3", directory=False
        ),
        "template": _path_status(workspace / "template", directory=True),
        "providers": observations,
        "planningPrerequisites": missing,
        "planRevision": revision,
        "reviewerContract": typed_draft_help("harness_reviewer_choice"),
        "catalog": "unverified" if catalog is None else "recorded",
        "unknown": {"planValidity": "unknown", "network": "unknown", "workerSuccess": "not-proven"},
        "executionReadiness": "not-proven",
        "didRunProvider": False,
        "isMocked": executable_for is not None,
    }


def diagnostic_status(workspace: Path, report=None) -> dict:
    """Retain the planner envelope without claiming to have read current planning truth."""
    return {
        "name": workspace.name,
        "phase": "unknown",
        "plan_revision": (report or {}).get("planRevision", "unknown"),
        "open_clarifications": "unknown",
        "pending_gate": "preflight-only",
    }


def render_preflight(report: dict) -> str:
    lines = ["**Read-only preflight · execution readiness NOT PROVEN**"]
    for key in ("workspace", "planDatabase", "template"):
        item = report[key]
        lines.append(f"- {key}: {item['status']} · {item['path']}")
    for item in report["providers"]:
        executable = item["executable"]
        lines.append(
            f"- {item['provider']}: executable {executable['status']} · "
            f"{executable['path'] or executable['reason']}; authentication UNKNOWN; "
            "effective protections UNKNOWN"
        )
        lines.extend(
            f"  - Requested policy only: {note}" for note in item["requestedProtectionNotes"]
        )
    lines.append("- Plan validity and network UNKNOWN; actual worker success NOT PROVEN.")
    lines.append("- catalog: " + report["catalog"])
    lines.extend("- Missing: " + issue for issue in report["planningPrerequisites"])
    lines.append(report["reviewerContract"]["approvalRule"])
    return "\n".join(lines)
