"""Existing stdio JSON-RPC and native-confirmation identity; SDK migration is deferred."""

from __future__ import annotations
import json
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from codex_harness.core.planner_connection import connection_reply, workspace_refusal
from codex_harness.core.planner_refusal_events import record_refusals
from codex_harness.core.planner_tools import TOOLS, STRING, _object

PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")


class PlannerTransport:
    def _wire_tools(self):
        """Wire assertions are caller intent, never authenticated host context."""
        tools = deepcopy(TOOLS)
        for tool in tools:
            schema = tool["inputSchema"]
            schema["properties"]["expected_workspace"] = {
                **STRING,
                "description": "Absolute intended project path supplied by the caller; "
                "unauthenticated assertion, not verified host cwd. Never copy the server path "
                "as a substitute for knowing the intended project.",
            }
            if tool["name"] not in {"harness_schema", "harness_preflight", "harness_plan_status"}:
                schema["required"].append("expected_workspace")
            for branch in schema.get("oneOf", []):
                if "maxProperties" in branch:
                    branch["maxProperties"] += 1
        return tools

    def _workspace_binding(self, arguments):
        supplied = isinstance(arguments, dict) and "expected_workspace" in arguments
        expected = arguments.get("expected_workspace") if supplied else None
        match = "not-supplied"
        if supplied:
            match = "invalid"
            if isinstance(expected, str) and expected.strip():
                try:
                    path = Path(expected)
                    if path.is_absolute():
                        expected = str(path.resolve())
                        match = "matched" if Path(expected) == self.workspace else "mismatch"
                except (OSError, RuntimeError, ValueError):
                    pass
        return {
            "serverWorkspace": str(self.workspace),
            "expectedWorkspace": expected,
            "match": match,
            "assertionSource": "caller-supplied; unauthenticated" if supplied else "not supplied",
            "clientWorkspaceVerified": False,
            "connectionStatus": "Fixed server workspace; client routing/reload unknown",
        }

    @staticmethod
    def _binding_text(binding):
        if binding["match"] == "matched":
            # Full routing facts stay in structured workspaceBinding; one quiet line here.
            return "Workspace: matched (caller-stated; unverified)"
        return (
            "Effective server workspace: "
            + binding["serverWorkspace"]
            + "; expected caller workspace: "
            + str(binding["expectedWorkspace"])
            + "; match: "
            + binding["match"]
            + ". Caller assertion is unauthenticated; host workspace and connection reload "
            "are unverified. The server never retargets."
        )

    def _wire_binding_reply(self, response, binding):
        if response is None:
            return None
        if isinstance(response.get("result"), dict) and "content" in response["result"]:
            result = response["result"]
            result["workspaceBinding"] = binding
            if result["content"][0].get("text", "").startswith("Existing request "):
                result["content"][0]["text"] += "; " + self._binding_text(binding)
                return response
            if result.get("structuredContent", {}).get("channel") == "chat-confirmation":
                result["content"][0]["text"] += "\n" + self._binding_text(binding)
                return response
            for item in result["content"]:
                if item.get("type") == "text":
                    first, separator, rest = item["text"].partition("\n")
                    item["text"] = first + "\n" + self._binding_text(binding) + separator + rest
                    # One binding line per reply; later blocks (e.g. chat questions) stay clean.
                    break
        elif response.get("method") == "elicitation/create":
            response["params"]["message"] += "\n" + self._binding_text(binding)
        pending = self._pending_confirmation or {}
        prepared = pending.get("request", {})
        status = (
            {"phase": prepared["phase"], "plan_revision": prepared.get("plan_revision", "unknown")}
            if response.get("method") == "elicitation/create" and "gate" in prepared
            else None
        )
        if response.get("result", {}).get("jobOnly"):
            return response  # Workspace binding above remains; no invented plan/phase projection.
        return connection_reply(response, self.workspace, status)

    def _workspace_refusal(self, request_id, binding, code, name=None):
        return workspace_refusal(self, request_id, binding, code, name)

    def _native_question(self, request_id, prepared, **context):
        wire_id = "seldon-confirmation-" + uuid4().hex
        self._pending_confirmation = dict(
            wire_id=wire_id,
            tool_id=request_id,
            request=prepared,
            deadline=time.monotonic() + self._confirmation_timeout,
            **context,
        )
        return dict(
            jsonrpc="2.0",
            id=wire_id,
            method="elicitation/create",
            params=dict(
                message=prepared["card"],
                requestedSchema=_object(
                    dict(
                        approve=dict(
                            type="boolean",
                            title="Unconditionally approve the complete displayed subject",
                        ),
                        statement=dict(type="string", title="Your complete response"),
                        conditions=dict(
                            type="string",
                            title="Conditions or changes (empty for unconditional approval)",
                        ),
                    )
                ),
            ),
        )

    def _finish_confirmation(self, response):
        from codex_harness.core.planner_confirmations import complete_confirmation

        pending = self._pending_confirmation
        if pending is None or response.get("id") != pending["wire_id"]:
            return None  # Never respond to an unknown/duplicate JSON-RPC response.
        if time.monotonic() >= pending["deadline"]:
            return self._cancel_confirmation(
                "Confirmation timed out; no approval recorded", preserve_pending=True
            )
        if "error" in response:
            return self._cancel_confirmation(
                "Native client could not display confirmation; no approval recorded",
                preserve_pending=True,
            )
        self._pending_confirmation = None
        result = response.get("result")
        try:
            if (
                "error" in response
                or not isinstance(result, dict)
                or set(result) - {"action", "content"}
            ):
                raise ValueError("plan-confirmation-response-invalid")
            outcome = complete_confirmation(
                self._store(),
                pending["request"]["request_id"],
                result.get("action"),
                result.get("content"),
            )
            reply = self._confirmation_reply(
                outcome, error=outcome["state"] not in {"approved", "pending-judgment"}
            )
        except (ValueError, KeyError) as error:
            if getattr(error, "code", None) == "confirmation-control-superseded":
                self._pending_confirmation = pending
                return self._cancel_confirmation(str(error), preserve_pending=True)
            outcome = complete_confirmation(
                self._store(), pending["request"]["request_id"], "cancel"
            )
            reply = self._confirmation_reply(outcome, error=True, reason=str(error))
        return {"jsonrpc": "2.0", "id": pending["tool_id"], "result": reply}

    def _cancel_confirmation(self, reason, *, preserve_pending=False):
        from codex_harness.core.planner_confirmation_cards import pending_outcome
        from codex_harness.core.planner_confirmations import complete_confirmation

        pending, self._pending_confirmation = self._pending_confirmation, None
        outcome = (
            pending_outcome(pending["request"])
            if preserve_pending
            else complete_confirmation(self._store(), pending["request"]["request_id"], "cancel")
        )
        reply = self._confirmation_reply(outcome, error=True, reason=reason)
        return {
            "jsonrpc": "2.0",
            "id": pending["tool_id"],
            "result": reply,
        }

    @record_refusals
    def expire_confirmation(self):
        """Bound the one pending native interaction without blocking ordinary MCP calls."""
        pending = self._pending_confirmation
        if pending is None or time.monotonic() < pending["deadline"]:
            return []
        cancelled = {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": pending["wire_id"], "reason": "Confirmation timed out"},
        }
        response = self._cancel_confirmation(
            "Confirmation timed out; no approval recorded", preserve_pending=True
        )
        binding = pending.get("workspaceBinding")
        return [
            cancelled,
            self._wire_binding_reply(response, binding) if binding is not None else response,
        ]

    @record_refusals
    def handle_line(self, line: str) -> dict | None:
        try:
            request = json.loads(line, parse_constant=_invalid_constant)
        except (ValueError, RecursionError):
            return _error(None, -32700, "Parse error")
        if not isinstance(request, dict):
            return _error(None, -32600, "Invalid Request")
        if request.get("jsonrpc") == "2.0" and "method" not in request and "id" in request:
            binding = (self._pending_confirmation or {}).get("workspaceBinding")
            response = self._finish_confirmation(request)
            return self._wire_binding_reply(response, binding) if binding is not None else response
        request_id = request.get("id")
        if (
            request.get("jsonrpc") != "2.0"
            or not isinstance(request.get("method"), str)
            or ("id" in request and (type(request_id) not in (str, int) and request_id is not None))
        ):
            return _error(None, -32600, "Invalid Request")
        if "id" not in request:
            if request["method"] == "notifications/initialized":
                self._initialized = self._elicitation
            elif request["method"] == "notifications/cancelled":
                pending = self._pending_confirmation
                parameters = request.get("params", {})
                if (
                    pending is not None
                    and isinstance(parameters, dict)
                    and parameters.get("requestId") in (pending["tool_id"], pending["wire_id"])
                ):
                    self._cancel_confirmation("Client cancelled; no approval recorded")
            # Notifications never get a response and never invoke mutating tools.
            return None
        params = request.get("params", {})
        if not isinstance(params, dict):
            return _error(request_id, -32602, "Invalid params")
        method = request["method"]
        if method == "initialize":
            if self._pending_confirmation is not None:
                return _error(
                    request_id, -32600, "Cancel the pending confirmation before reinitializing"
                )
            version = params.get("protocolVersion")
            negotiated_version = version if version in PROTOCOL_VERSIONS else "2025-06-18"
            capabilities = params.get("capabilities", {})
            self._elicitation = (
                negotiated_version == "2025-06-18"
                and isinstance(capabilities, dict)
                and isinstance(capabilities.get("elicitation"), dict)
            )
            self._initialized = False
            result = {
                "protocolVersion": negotiated_version,
                "serverInfo": {"name": "harness-planner", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            }
        elif method in ("ping", "notifications/initialized"):
            result = {}
        elif method == "tools/list":
            result = {"tools": self._wire_tools(), "workspaceBinding": self._workspace_binding({})}
        elif method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments", {})
            binding = self._workspace_binding(arguments)
            code = {
                "mismatch": "workspace-mismatch",
                "invalid": "workspace-assertion-invalid",
            }.get(binding["match"])
            if binding["match"] == "not-supplied" and name not in {
                "harness_schema",
                "harness_preflight",
                "harness_plan_status",
            }:
                code = "workspace-assertion-required"
            if code:
                return self._workspace_refusal(request_id, binding, code, name)
            if isinstance(arguments, dict):
                arguments = {
                    key: value for key, value in arguments.items() if key != "expected_workspace"
                }
            if (
                name == "harness_steer"
                and isinstance(arguments, dict)
                and arguments.get("native_confirmation") is True
            ):
                try:
                    response = self._begin_loop_confirmation(request_id, arguments)
                    if self._pending_confirmation is not None:
                        self._pending_confirmation["workspaceBinding"] = binding
                    return self._wire_binding_reply(response, binding)
                except (ValueError, KeyError) as error:
                    return {"jsonrpc": "2.0", "id": request_id, "result": self._reply_error(error)}
            result = self._wire_call_tool(name, arguments)
        else:
            return _error(request_id, -32601, "Method not found")
        response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        return self._wire_binding_reply(response, binding) if method == "tools/call" else response


def _invalid_constant(value: str):
    raise ValueError(f"Invalid JSON constant: {value}")


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
