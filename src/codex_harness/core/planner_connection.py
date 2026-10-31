"""Read-only chat identity and reconnect guidance from an already observed status.

This projection never opens a store, retargets a connection, or authorizes a retry.
In particular, a routing refusal can use it before any canonical store access.
"""

from copy import deepcopy
from pathlib import Path

from codex_harness.core.plan_store import PHASES
from codex_harness.core.planner_refusals import refusal_code, refusal_hold, refusal_text


def connection_projection(workspace: str | Path, status: dict | None = None) -> dict:
    """Keep the bound folder distinct from the user-chosen plan name."""
    root = Path(workspace)
    status = status or {}
    phase = status.get("phase", "unknown")
    number = PHASES.index(phase) + 1 if phase in PHASES else None
    project = root.name or str(root)
    # Project/plan names may contain line breaks; keep the identity one line.
    label = " ".join(project.split())
    header = f"Seldon · project: {label} · phase {number or '?'}/{len(PHASES)}: {phase}"
    recovery = (
        f"Open a new chat in the same project ({root}). "
        "Use its project-local Seldon connection and call harness_plan_status "
        f"with expected_workspace={str(root)!r}; continue from the saved phase. "
        "Inspect the current result and any running job before retrying an interrupted call. "
        "A changed registration file does not retarget an existing connection."
    )
    return {
        "projectRoot": str(root),
        "projectName": project,
        "planName": status.get("name"),
        "phase": phase,
        "phaseNumber": number,
        "phaseCount": len(PHASES),
        "planRevision": status.get("plan_revision", "unknown"),
        "header": header,
        "recovery": recovery,
        "clientWorkspaceVerified": False,
    }


def connection_reply(
    reply: dict,
    workspace: str | Path,
    status: dict | None = None,
    *,
    show_recovery: bool = False,
    refusal_values: dict | None = None,
) -> dict:
    """Project a tool result or native elicitation without changing its payload.

    Callers supply their existing canonical status observation. Routing refusals
    omit it: unknown phase is honest and keeps refusal ahead of store access.
    Protocol control messages and errors are returned unchanged.
    """
    if reply.get("method") == "elicitation/create":
        result = deepcopy(reply)
        projection = connection_projection(workspace, status)
        result["params"]["message"] = _text(result["params"]["message"], projection, show_recovery)
        return result
    payload = reply.get("result", reply)
    if not isinstance(payload, dict) or "content" not in payload:
        return reply
    if payload.get("jobOnly") and not payload.get("isError"):
        return reply
    result = deepcopy(reply)
    payload = result.get("result", result)
    projection = (
        payload["plannerConnection"]
        if status is None and "plannerConnection" in payload
        else connection_projection(workspace, status)
    )
    payload["plannerConnection"] = projection
    for item in payload["content"]:
        if item.get("type") == "text":
            item["text"] = _text(item["text"], projection, show_recovery)
            values = {
                "status": status or {},
                "data": payload.get("structuredContent", {}),
                "binding": payload.get("workspaceBinding", {}),
                **(refusal_values or {}),
            }
            if payload.get("isError") or refusal_hold(values):
                payload.setdefault("refusalCode", refusal_code(item["text"], values))
                item["text"] = refusal_text(item["text"], workspace, values)
            # One header per response; additional text blocks remain untouched.
            break
    return result


def _text(text: str, projection: dict, show_recovery: bool) -> str:
    if text.startswith(projection["header"] + "\n"):
        return text
    lines = [projection["header"]]
    body, separator, next_step = text.rpartition("\nNext: ")
    # Full recovery guidance stays in structured plannerConnection.recovery.
    recovery = (
        "- Connection recovery: if this chat loses Seldon, open a new chat in "
        + projection["projectRoot"]
        + " and call harness_plan_status with expected_workspace set to that folder."
    )
    if show_recovery and separator:
        lines.extend([body, recovery, "Next: " + next_step])
    else:
        lines.append(text)
        if show_recovery:
            lines.append(recovery)
    return "\n".join(lines)


def workspace_refusal(self, request_id, binding, code, name=None):
    # No status/store reads here: even formatting a normal error can open a store.
    response = {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "isError": True,
            "workspaceBinding": binding,
            "structuredContent": {"workspaceBinding": binding},
            "content": [
                {
                    "type": "text",
                    "text": code
                    + ": workspace binding refused.\n"
                    + self._binding_text(binding)
                    + "\nNo store access or provider admission was attempted for this call.\n"
                    "Next: Inspect harness_plan_status read-only, verify the intended project "
                    "from the caller, then use a correctly bound connection with "
                    "expected_workspace. A supported client reload route is unknown; "
                    "changing config alone proves no live binding.",
                }
            ],
        },
    }
    if name == "harness_advance":
        result = response["result"]
        result["structuredContent"].update(currentPhase="unknown", nextPhase="unknown")
        result["content"][0]["text"] = (
            "Current phase unknown; next legal phase: unknown (workspace binding refused).\n"
            + result["content"][0]["text"]
        )
    return connection_reply(response, self.workspace, show_recovery=True)
