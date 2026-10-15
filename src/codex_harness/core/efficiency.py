"""Observed provider work counts; unavailable telemetry stays null."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

FIELDS = (
    "commands",
    "failedCommands",
    "deniedCommands",
    "redTestRuns",
    "errorCommands",
    "explorationCommands",
    "fileChanges",
    "inputTokens",
    "cachedInputTokens",
    "outputTokens",
    "turns",
    "durationSeconds",
)
EXPLORATION = re.compile(
    r"(?<![\w-])(?:rg|grep|select-string|ls|dir|get-childitem|get-content|cat|type|"
    r"sed\s+-n|head|tail)(?![\w-])",
    re.IGNORECASE,
)
DENIED_STATUSES = {"declined", "denied", "permission_denied", "blocked"}
TEST_RUN = re.compile(r"(?<![\w-])(?:unittest|pytest)(?![\w-])", re.IGNORECASE)
TEST_FAILURE = re.compile(r"\bFAIL(?:ED)?\b|\bfailures=", re.IGNORECASE)


def _command_outcome(command: dict) -> str | None:
    status = str(command.get("status", "")).lower()
    output = command.get("aggregated_output") or ""
    if status in DENIED_STATUSES or command.get("guard_denial"):
        return "deniedCommands"
    exit_code = command.get("exit_code")
    if exit_code is None and status != "failed":
        return None
    if exit_code == 0 and status != "failed":
        return "success"
    # Red tests first: real failure output may mention "permission" or "denied".
    if (
        TEST_RUN.search(command.get("command") or "")
        and TEST_FAILURE.search(output)
        and "no tests ran" not in output.lower()
        and exit_code != 5
        and not re.search(r"\bexit code\s*:?\s*5\b", output, re.IGNORECASE)
    ):
        return "redTestRuns"
    return "failedCommands"


def read_events(path: Path):
    """A truncated last JSON line must not discard earlier durable observations."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def guard_disposition(path: str | Path) -> dict:
    """Observe provider envelopes, never infer authorization from command/output text.

    Headless CLIs own tool execution: these buffered observations cannot prevent
    a retry. Any subsequent tool use is held without claiming semantic equivalence.
    Missing/unsupported telemetry is unknown, not proof that no denial occurred.
    """
    path = Path(path)
    events = []
    observation_error = None
    raw = None
    try:
        if path.is_file():
            raw = path.read_bytes()
            for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    observation_error = "Invalid or truncated provider event"
                    continue
                if isinstance(event, dict):
                    events.append((number, event))
                else:
                    observation_error = "Non-object provider event"
    except OSError as error:
        observation_error = f"{type(error).__name__}: {error}"
    denials, subsequent = [], []
    calls = {}

    def deny(event, line, tool_id, tool, source, reason):
        # Keep each envelope's physical line, even when ids correlate to one denial.
        denials.append(
            {
                "eventIndex": line,
                "eventLine": line,
                "timestamp": event.get("timestamp"),
                "toolUseId": tool_id,
                "tool": tool or calls.get(tool_id),
                "source": source,
                "reason": reason,
            }
        )

    def call(event, line, tool_id, tool):
        if tool_id and tool_id in calls:
            return
        calls[tool_id or (line, tool)] = tool
        if denials:
            subsequent.append(
                {
                    "eventIndex": line,
                    "eventLine": line,
                    "timestamp": event.get("timestamp"),
                    "toolUseId": tool_id,
                    "tool": tool,
                }
            )

    for line, event in events:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "permission_denied":
            deny(
                event,
                line,
                event.get("tool_use_id"),
                event.get("tool_name"),
                "system.permission_denied",
                event.get("message"),
            )
        message = event.get("message")
        message = message if isinstance(message, dict) else {}
        contents = message.get("content", [])
        for block in contents if isinstance(contents, list) else []:
            if not isinstance(block, dict):
                continue
            if kind == "assistant" and block.get("type") == "tool_use":
                call(event, line, block.get("id"), block.get("name"))
            if kind == "user" and block.get("type") == "tool_result" and block.get("is_error"):
                tool_id = block.get("tool_use_id")
                metadata = event.get("tool_result_meta")
                for meta in metadata if isinstance(metadata, list) else []:
                    if (
                        isinstance(meta, dict)
                        and tool_id
                        and meta.get("id") == tool_id
                        and meta.get("non_execution_kind") == "user-rejected"
                    ):
                        deny(
                            event,
                            line,
                            tool_id,
                            None,
                            "tool_result_meta.user-rejected",
                            block.get("content"),
                        )
        if kind == "result":
            result_denials = event.get("permission_denials")
            for denial in result_denials if isinstance(result_denials, list) else []:
                if isinstance(denial, dict):
                    deny(
                        event,
                        line,
                        denial.get("tool_use_id"),
                        denial.get("tool_name"),
                        "result.permission_denials",
                        None,
                    )
        item = event.get("item") or {}
        if kind in {"item.started", "item.completed"} and isinstance(item, dict):
            if item.get("type") in {"command_execution", "file_change", "mcp_tool_call"}:
                if item.get("status") in DENIED_STATUSES:
                    deny(
                        event,
                        line,
                        item.get("id"),
                        item.get("type"),
                        "item.status",
                        item.get("aggregated_output"),
                    )
                else:
                    call(event, line, item.get("id"), item.get("type"))
    supported_types = {
        "assistant",
        "user",
        "system",
        "result",
        "thread.started",
        "turn.started",
        "turn.completed",
        "turn.failed",
        "item.started",
        "item.completed",
    }
    unsupported_lines = [line for line, event in events if event.get("type") not in supported_types]
    complete = (
        bool(events)
        and not (observation_error or unsupported_lines)
        and events[-1][1].get("type") in {"result", "turn.completed", "turn.failed"}
    )
    audit_reason = (
        "malformed"
        if observation_error
        else "missing"
        if raw is None
        else "unsupported"
        if unsupported_lines
        else "incomplete"
        if not complete
        else None
    )
    return {
        "disposition": "guard-breach"
        if subsequent
        else "permission-denied"
        if denials
        else "unknown"
        if observation_error
        else "no-denial-observed"
        if complete
        else "unknown",
        "requiresUserDisposition": bool(denials),
        "requiresAuditHold": not complete,
        "auditReason": audit_reason,
        "didObservePermissionDenial": True
        if denials
        else None
        if observation_error
        else False
        if complete
        else None,
        "didObservePostDenialToolUse": bool(subsequent) if denials or complete else None,
        "didPreventFurtherToolExecution": False,
        "enforcementBoundary": "post-process hold; no synchronous provider tool callback",
        "coverage": "supported structured envelopes only; equivalence not inferred",
        "eventsPath": str(path),
        "eventsSha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
        "observationError": observation_error,
        "telemetryComplete": complete,
        "unsupportedEventLines": unsupported_lines,
        "denials": denials,
        "postDenialToolUses": subsequent,
    }


def aggregate(summaries) -> dict:
    """A total is known only when every contributing attempt observed that field."""
    summaries = list(summaries)
    return {
        key: sum(summary[key] for summary in summaries)
        if summaries and all(summary.get(key) is not None for summary in summaries)
        else None
        for key in FIELDS
    }


def summarize_events(path: str | Path) -> dict:
    path = Path(path)
    events = list(read_events(path))
    denied_ids = {
        denial["toolUseId"]
        for denial in guard_disposition(path)["denials"]
        if denial["toolUseId"] is not None
    }
    summary = dict.fromkeys(FIELDS)
    codex = any(
        event.get("type")
        in {"thread.started", "turn.started", "turn.completed", "item.started", "item.completed"}
        for event in events
    )
    claude = any(event.get("type") in {"assistant", "user", "result", "system"} for event in events)
    commands = {}
    changes = set()
    usage = []
    starts = completions = 0
    if codex:
        for index, event in enumerate(events):
            kind = event.get("type")
            starts += kind == "turn.started"
            completions += kind in {"turn.completed", "turn.failed"}
            if kind == "turn.completed" and "usage" in event:
                usage.append(event["usage"])
            item = event.get("item") or {}
            if item.get("type") == "command_execution":
                commands[item.get("id", index)] = item
            if item.get("type") == "file_change":
                changes.update(
                    change["path"] for change in item.get("changes", []) if "path" in change
                )
        summary["turns"] = max(starts, completions)
        summary["fileChanges"] = len(changes)
    elif claude:
        messages = {}
        outcomes = {}
        result = None
        tool_stream = False
        for index, event in enumerate(events):
            kind = event.get("type")
            if kind == "result":
                result = event
            message = event.get("message")
            if not isinstance(message, dict):  # system events carry plain-text messages
                message = {}
            if kind == "assistant":
                messages[message.get("id", index)] = message
                tool_stream = True
            if kind == "user":
                tool_stream = True
            contents = message.get("content", [])
            for content in contents if isinstance(contents, list) else []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "tool_use":
                    tool = content.get("name")
                    inputs = content.get("input") or {}
                    if tool in {"Bash", "PowerShell"}:
                        commands[content["id"]] = {"command": inputs.get("command", "")}
                    elif tool in {"Read", "Glob", "Grep"}:
                        commands[content["id"]] = {"command": tool, "exploration": True}
                    elif tool in {"Write", "Edit", "MultiEdit"} and inputs.get("file_path"):
                        changes.add(inputs["file_path"])
                elif content.get("type") == "tool_result":
                    outcomes[content.get("tool_use_id")] = content
        for tool_id, command in commands.items():
            command["guard_denial"] = tool_id in denied_ids
            if tool_id in outcomes:
                outcome = outcomes[tool_id]
                command["exit_code"] = int(outcome.get("is_error", False))
                output = outcome.get("content") or ""
                command["aggregated_output"] = (
                    output
                    if isinstance(output, str)
                    else "\n".join(
                        block.get("text") or "" for block in output if isinstance(block, dict)
                    )
                )
        if result and isinstance(result.get("usage"), dict):
            usage = [result["usage"]]
        else:
            usage = [message["usage"] for message in messages.values() if "usage" in message]
        summary["turns"] = result.get("num_turns") if result else None
        if tool_stream:
            summary["fileChanges"] = len(changes)
        if result and isinstance(result.get("duration_ms"), int | float):
            summary["durationSeconds"] = result["duration_ms"] / 1000
        claude = tool_stream
    if codex or claude:
        summary["commands"] = len(commands)
        outcomes = [_command_outcome(command) for command in commands.values()]
        if all(outcome is not None for outcome in outcomes):
            for field in ("failedCommands", "deniedCommands", "redTestRuns"):
                summary[field] = outcomes.count(field)
            summary["errorCommands"] = sum(outcome != "success" for outcome in outcomes)
        summary["explorationCommands"] = sum(
            bool(command.get("exploration") or EXPLORATION.search(command.get("command") or ""))
            for command in commands.values()
        )
    for target, source in (
        ("inputTokens", "input_tokens"),
        ("cachedInputTokens", "cached_input_tokens" if codex else "cache_read_input_tokens"),
        ("outputTokens", "output_tokens"),
    ):
        if usage and all(isinstance(item.get(source), int | float) for item in usage):
            summary[target] = sum(item[source] for item in usage)
    # Dispatcher receipts bind wall time to the request/result, including mocks.
    request_path, result_path = path.parent / "request.json", path.parent / "result.json"
    if request_path.is_file() and result_path.is_file():
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if request.get("startedAt") and result.get("finishedAt"):
            summary["durationSeconds"] = (
                datetime.fromisoformat(result["finishedAt"].replace("Z", "+00:00"))
                - datetime.fromisoformat(request["startedAt"].replace("Z", "+00:00"))
            ).total_seconds()
    return summary
