import json  # One event per public refusal; arguments and error text are never stored.
import sqlite3
from functools import wraps

from codex_harness.core.plan_store import open_plan_store


def record_refusals(method):
    @wraps(method)
    def call(self, *args):
        pending = self._pending_confirmation or {}
        replies = method(self, *args)
        for reply in replies if isinstance(replies, list) else [replies]:
            result = (reply or {}).get("result", {})
            if "content" not in result:
                continue
            request = json.loads(args[0]) if args else {}
            tool = (
                request.get("params", {}).get("name", "unknown-tool")
                if request.get("method") == "tools/call"
                else pending.get("tool_name", "harness_steer")
            )
            if not result.get("isError"):
                continue
            recording = result.setdefault("structuredContent", {}).setdefault(
                "refusalRecording", {"state": "unavailable"}
            )
            if (
                result.get("workspaceBinding", {}).get("match") != "matched"
                or not (self.workspace / ".codex-harness/plan.sqlite3").is_file()
                or result.get("refusalCode") == "workspace-discovery-unavailable"
            ):
                continue
            try:
                with open_plan_store(self.workspace) as store:
                    with store._transaction():
                        store._event(
                            "planner-refusal",
                            dict(
                                code=result.get("refusalCode", "tool-input-or-operation-error"),
                                phase=result.get("plannerConnection", {}).get("phase", "unknown"),
                                tool=tool
                                if any(t["name"] == tool for t in self._wire_tools())
                                else "unknown-tool",
                                plan_revision=result.get("plannerConnection", {}).get(
                                    "planRevision", "unknown"
                                ),
                            ),
                        )
                recording["state"] = "recorded"
            except (ValueError, OSError, sqlite3.Error):
                pass
        return replies

    return call
