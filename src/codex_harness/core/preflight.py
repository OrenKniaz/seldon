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
    *,
    executable_for=None,
) -> dict:
    """Observe executables and canonical planning prerequisites without executing them."""
    names = SUPPORTED_PROVIDERS
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
    from codex_harness.core.run_loop import cursor

    missing, revision = ["Start the durable run"], "unknown"
    if (workspace / ".codex-harness/plan.sqlite3").is_file():
        with open_plan_store_readonly(workspace) as store:
            revision = store._revision()
            run = cursor(store)
            missing = [run["reason"]] if run and (run.get("question") or run.get("userRequest")) else []
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
        "catalog": "unverified",
        "unknown": {"planValidity": "unknown", "network": "unknown", "workerSuccess": "not-proven"},
        "executionReadiness": "not-proven",
        "didRunProvider": False,
        "isMocked": executable_for is not None,
    }
