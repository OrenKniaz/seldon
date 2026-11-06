"""Provider launch protocol and observed review response evidence."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryFile

from codex_harness.core.audit_process import run_audit_process
from codex_harness.core.reviewer_evidence import review_tier

_REVIEWERS = (
    {"name": "codex-reviewer", "provider": "codex", "model": "gpt-6.1-sol", "effort": "high"},
    {"name": "claude-reviewer", "provider": "claude", "model": "claude-sonnet-5-5"},
)

_SEVERITIES = ("blocker", "major", "minor", "note")

_FIELDS = {"node_id", "severity", "finding", "suggestion"}

def _executable(provider: str, executable_for) -> str | None:
    if executable_for is None:
        if provider == "codex" and os.name == "nt" and os.environ.get("LOCALAPPDATA"):
            bundles = list(
                Path(os.environ["LOCALAPPDATA"], "OpenAI", "Codex", "bin").glob("*/codex.exe")
            )
            if bundles:
                return str(max(bundles, key=lambda path: (path.stat().st_mtime_ns, str(path))))
        return shutil.which(provider)
    if callable(executable_for):
        return executable_for(provider)
    return executable_for.get(provider)

def default_reviewers(*, executable_for=None) -> list[dict]:
    """Offer reviewers for resolvable providers; missing overrides mean unavailable."""
    selected = [dict(r) for r in _REVIEWERS if _executable(r["provider"], executable_for)]

    if len(selected) == 1:
        selected.append({**selected[0], "name": selected[0]["name"] + "-2"})
    return selected

def coverage(selected):
    return (
        {"reviewIndependence": "single-provider"}
        if len(selected) >= 2 and review_tier(selected) in {"same-provider", "same-model"}
        else {}
    )

def _run(launch, cwd: Path, timeout: int, *, raw=False):
    with TemporaryFile() as prompt:
        if launch.stdin_text is not None:
            prompt.write(launch.stdin_text.encode("utf-8"))
            prompt.seek(0)
        completed = run_audit_process(launch.command, cwd=cwd, env={**os.environ, **launch.environment},
                                      stdin=prompt, text=False, timeout=timeout)
    return (completed.returncode, completed.stdout if raw else completed.stdout.decode("utf-8"),
            completed.stderr if raw else completed.stderr.decode("utf-8"))

def _launch_review(reviewer, packet, executable, *, instructions=None):
    from codex_harness.core.provider_launch import get_adapter
    if not executable:
        raise OSError(f"Reviewer executable unavailable: {reviewer['provider']}")
    if "\n## Bound review requests\n" in packet:
        requests = json.loads(packet.split("\n## Bound review requests\n", 1)[1])
        packet += ("\n## Bound Judgment\n" + json.dumps(requests[reviewer["name"]], ensure_ascii=False)
                   + "\nReturn ONLY JSON using responseExample's shape and responseGuidance; supply your own assessment.")
    launch = get_adapter(reviewer["provider"]).provider_normal_command(
        prompt=packet,
        prompt_via_stdin=True,
        model=reviewer.get("model"),
        reasoning_effort=reviewer.get("effort"),
        executable=executable,
    )
    if reviewer["provider"] == "codex":
        launch = replace(
            launch, command=(*launch.command[:-1], "--skip-git-repo-check", launch.command[-1])
        )
    elif reviewer["provider"] == "claude":
        launch = replace(
            launch,
            command=(*launch.command, "--tools", ""),
            protections=(
                *launch.protections,
                "Requested review-only tool restriction: --tools with an empty list. "
                "Effective runtime tool suppression is not verified by Harness.",
            ),
        )
    if instructions is not None:
        # Keep stdin byte-identical to the worker packet. Review instructions
        # belong in the provider's instruction channel, not in worker context.
        if reviewer["provider"] == "codex":
            launch = replace(
                launch,
                command=(
                    *launch.command[:-1],
                    "-c",
                    "developer_instructions=" + json.dumps(instructions),
                    launch.command[-1],
                ),
            )
        elif reviewer["provider"] == "claude":
            launch = replace(
                launch, command=(*launch.command, "--append-system-prompt", instructions)
            )
        else:
            raise ValueError("Readiness instruction channel supports codex and claude")
    return launch

def _response(output: str, provider: str) -> str:
    """Select final provider text; never mine earlier tool/event payloads."""
    final = None
    has_events = False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or "type" not in event:
            continue
        has_events = True
        if provider == "codex" and event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                final = item.get("text")
        elif provider == "claude" and event.get("type") == "result":
            if event.get("is_error"):
                raise ValueError(
                    "Claude returned an error result: " + json.dumps(event, ensure_ascii=False)
                )
            final = event.get("result")
    if has_events:
        if not isinstance(final, str):
            raise ValueError("Provider stream has no final review text")
        return final
    return output

def packet_binding(output: str, provider: str) -> dict:
    """Label observed out-of-packet tools without changing review admission."""
    tools = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        names = []
        if provider == "claude":
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                names = [
                    item.get("name")
                    for item in content
                    if isinstance(item, dict)
                    and item.get("type") == "tool_use"
                    and item.get("name")
                    in {"Read", "Glob", "Grep", "Bash", "PowerShell", "WebFetch", "WebSearch"}
                ]
            delta = event.get("event")
            if isinstance(delta, dict):
                block = delta.get("content_block")
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    names += (
                        [block.get("name")]
                        if block.get("name")
                        in {"Read", "Glob", "Grep", "Bash", "PowerShell", "WebFetch", "WebSearch"}
                        else []
                    )
        elif provider == "codex":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") in {
                "command_execution",
                "file_read",
                "read_file",
            }:
                names = [item["type"]]
        for name in names:
            if name not in tools:
                tools.append(name)
    return {"packetBound": not tools, "outOfPacketTools": tools}

def _binding_fields(row: dict) -> dict:
    value = row.get("packetBound")
    tools = row.get("outOfPacketTools") or []
    return {
        "packetBound": bool(value) if value is not None else None,
        "outOfPacketTools": json.loads(tools) if isinstance(tools, str) else tools,
    }

def _invalid_reason(item) -> str | None:
    if not isinstance(item, dict) or set(item) not in (
        _FIELDS,
        _FIELDS | {"classification", "scope_reason"},
    ):
        return "Finding must have exactly node_id, severity, finding and suggestion"
    if not all(isinstance(value, str) for value in item.values()):
        return "Finding fields must be strings"
    if "classification" in item and (
        item["classification"] not in {"defect-in-plan", "beyond-brief"}
        or not item["scope_reason"].strip()
    ):
        return "Finding scope requires a known classification and nonblank scope_reason"
    if item["severity"] not in _SEVERITIES:
        return "Unknown severity"
    if not item["finding"].strip():
        return "Finding must be nonempty"
    return None

def _execution_session(reviewer, output, stderr, *, author=False):
    """Recognized local Codex telemetry only; requested flags never prove identity."""
    if (
        reviewer.get("provider") != "codex"
        or not reviewer.get("model")
        or not reviewer.get("effort")
    ):
        raise ValueError("execution-observed-codex-configuration-required")
    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    if not events or any(not isinstance(row, dict) for row in events):
        raise ValueError("execution-session-protocol-unknown")
    sessions = [row.get("thread_id") for row in events if row.get("type") == "thread.started"]
    if (
        len(sessions) != 1
        or not isinstance(sessions[0], str)
        or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", sessions[0])
        or sum(row.get("type") == "turn.completed" for row in events) != 1
    ):
        raise ValueError("execution-session-incomplete-or-ambiguous")
    if events[0].get("type") != "thread.started" or events[-1].get("type") != "turn.completed":
        raise ValueError("execution-session-event-order-invalid")
    unfinished = set()
    tool_states = {}
    non_tool_ids = set()
    for row in events:
        if set(row) - {"type", "thread_id", "usage", "item"} or row.get("isMocked") is True:
            raise ValueError("execution-session-event-shape-unsupported")
        if "thread_id" in row and row["thread_id"] != sessions[0]:
            raise ValueError("execution-session-mixed-thread")
        if row.get("type") not in {
            "thread.started",
            "turn.started",
            "turn.completed",
            "item.started",
            "item.completed",
        }:
            raise ValueError("execution-session-event-unsupported")
        item = row.get("item")
        if row["type"].startswith("item."):
            if not isinstance(item, dict):
                raise ValueError("execution-session-item-invalid")
            kind = item.get("type")
            if item.get("isMocked") is True or item.get("sourceOnly") is True:
                raise ValueError("execution-source-only-author-unsupported")
            if kind not in {"agent_message", "reasoning"}:
                if not author or kind not in {"command_execution", "file_change"}:
                    raise ValueError("execution-session-tool-or-item-unsupported")
                if not isinstance(item.get("id"), str) or not item["id"]:
                    raise ValueError("execution-author-tool-identity-missing")
                fields = (
                    {"id", "type", "status", "command", "aggregated_output", "exit_code"}
                    if kind == "command_execution"
                    else {"id", "type", "status", "changes"}
                )
                if set(item) - fields:
                    raise ValueError("execution-author-tool-shape-unsupported")
                if kind == "command_execution" and (
                    not isinstance(item.get("command"), str) or not item["command"]
                ):
                    raise ValueError("execution-author-command-unobserved")
                if row["type"] == "item.completed" and (
                    item.get("status") != "completed"
                    or kind == "command_execution"
                    and (
                        type(item.get("exit_code")) is not int
                        or item["exit_code"] != 0
                        or not isinstance(item.get("aggregated_output"), str)
                    )
                    or kind == "file_change"
                    and (
                        not isinstance(item.get("changes"), list)
                        or any(
                            not isinstance(change, dict)
                            or set(change) != {"path", "kind"}
                            or not isinstance(change["path"], str)
                            or not change["path"]
                            or change["kind"] not in {"add", "update", "delete"}
                            for change in item["changes"]
                        )
                    )
                ):
                    raise ValueError("execution-author-tool-terminal-unknown")
            elif set(item) - {"id", "type", "text"} or not isinstance(item.get("text"), str):
                raise ValueError("execution-session-message-shape-unsupported")
            if kind in {"command_execution", "file_change"}:
                previous = tool_states.get(item["id"])
                if item["id"] in non_tool_ids or (
                    previous is not None
                    and (previous != (kind, False) or row["type"] != "item.completed")
                ):
                    raise ValueError("execution-author-tool-state-inconsistent")
                tool_states[item["id"]] = (kind, row["type"] == "item.completed")
            elif isinstance(item.get("id"), str):
                if item["id"] in tool_states:
                    raise ValueError("execution-author-tool-state-inconsistent")
                non_tool_ids.add(item["id"])
            if row["type"] == "item.started":
                if not isinstance(item.get("id"), str) or item["id"] in unfinished:
                    raise ValueError("execution-session-item-start-ambiguous")
                unfinished.add(item["id"])
            else:
                unfinished.discard(item.get("id"))
    if unfinished:
        raise ValueError("execution-session-item-return-incomplete")
    lines = [
        line
        for line in stderr.splitlines()
        if "Codex initialized with event: SessionConfiguredEvent {" in line
    ]
    if len(lines) != 1:
        raise ValueError("execution-observed-session-configuration-unavailable")
    line, session = lines[0], sessions[0]
    for field, kind in (("session_id", "SessionId"), ("thread_id", "ThreadId")):
        if re.findall(field + r": " + kind + r" \{ uuid: ([0-9a-f-]+) \}", line) != [session]:
            raise ValueError("execution-observed-session-mismatch")
    if (
        re.findall(r'model: "([^"]+)"', line) != [reviewer["model"]]
        or re.findall(r"reasoning_effort: Some\((\w+)\)", line) != [reviewer["effort"].capitalize()]
        or any(field + ": None," not in line for field in ("forked_from_id", "parent_thread_id"))
    ):
        raise ValueError("execution-observed-model-effort-or-parent-unknown")
    return dict(
        provider="codex", session=session, model=reviewer["model"], effort=reviewer["effort"]
    )

def _assessment_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()

def _assessment_file(repo, name):
    path = repo / name
    if (
        Path(name).is_absolute()
        or ".." in Path(name).parts
        or not path.resolve().is_relative_to(repo)
    ):
        raise ValueError("Assessment source escapes selected product")
    for component in (path, *path.parents):
        if component == repo.parent:
            break
        if component.is_symlink() or getattr(component.stat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Assessment source has a redirected path")
    raw = path.read_bytes()
    return dict(sha256=hashlib.sha256(raw).hexdigest(), text=raw.decode("utf-8"))
