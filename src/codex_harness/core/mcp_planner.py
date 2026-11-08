"""MCP is the command/view adapter; run_loop is the only continuation policy."""

from __future__ import annotations
import hashlib
import json
import queue
import threading
from pathlib import Path
from codex_harness.core import planner_views as views, run_loop
from codex_harness.core import planner_confirmations as confirmations
from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
from codex_harness.core.planner_inspection import packet_view
from codex_harness.core.planner_inspection import show_node
from codex_harness.core.planner_connection import connection_reply
from codex_harness.core.planner_schema import schema_help, validate_input
from codex_harness.core.planner_tools import TOOLS
from codex_harness.core.planner_transport import PlannerTransport
from codex_harness.core.spine_diagrams import diagram


class PlannerServer(PlannerTransport):
    def __init__(self, workspace, *, confirmation_timeout=600):
        from codex_harness.core.planner_acceptance import AcceptanceOwner
        from codex_harness.core.planner_audits import AuditOwner
        from codex_harness.core.planner_review import ReviewOwner

        self.workspace, self.store = Path(workspace).resolve(), None
        self._elicitation = self._initialized = False
        self._pending_confirmation = None
        self._confirmation_timeout = confirmation_timeout
        self.audits, self.acceptance, self.reviews = AuditOwner(), AcceptanceOwner(), ReviewOwner()
        self._completed_calls = queue.Queue()

    def handle_line(self, line):
        """Request workers keep control responsive; only run_loop decides continuation.

        Reuse the stdio owner's existing wake/completion hook. Each concurrent
        request has its own database connection; JSON-RPC framing stays unchanged.
        """
        try:
            request = json.loads(line)
        except ValueError:
            return super().handle_line(line)
        if not isinstance(request, dict):
            return super().handle_line(line)
        if request.get("method") == "notifications/initialized":
            reply = super().handle_line(line)
            from codex_harness.core.run_chat import activate
            activate(self.workspace,self._completed_calls.put,self.audits.wake)
            return reply
        params = request.get("params", {})
        mutation = (
            request.get("method") == "tools/call"
            and isinstance(params, dict)
            and params.get("name") in {"harness_chat"}
        )
        if mutation and params.get("arguments", {}).get("text") == "/confirm":
            mutation = False
        pending = self._pending_confirmation
        native_reply = (
            "method" not in request
            and pending is not None
            and request.get("id") == pending["wire_id"]
        )
        if "id" not in request or not (mutation or native_reply):
            return super().handle_line(line)
        if native_reply:
            self._pending_confirmation = None

        def respond():
            worker = PlannerServer(self.workspace, confirmation_timeout=self._confirmation_timeout)
            worker._elicitation, worker._initialized = self._elicitation, self._initialized
            worker._pending_confirmation = pending if native_reply else None
            try:
                reply = PlannerTransport.handle_line(worker, line)
            except Exception as error:
                reply = dict(jsonrpc="2.0", id=request["id"], result=self._reply_error(error))
            finally:
                worker.close()
            if reply is not None:
                self._completed_calls.put(reply)
            if callable(self.audits.wake):
                self.audits.wake()

        threading.Thread(target=respond, daemon=True).start()
        return None

    def expire_confirmation(self):
        replies = super().expire_confirmation()
        while not self._completed_calls.empty():
            replies.append(self._completed_calls.get_nowait())
        return replies

    def close(self):
        if self._pending_confirmation is not None:
            self._cancel_confirmation(
                "Client disconnected; no approval recorded", preserve_pending=True
            )
        if self.store is not None:
            self.store.close()
            self.store = None

    def _store(self):
        if self.store is None:
            if not (self.workspace / ".codex-harness/plan.sqlite3").exists():
                raise ValueError("Call harness_chat first")
            self.store = open_plan_store(self.workspace)
        return self.store

    def _status(self):
        if self.store is None and not (self.workspace / ".codex-harness/plan.sqlite3").exists():
            return dict(
                name=self.workspace.name,
                phase="align",
                plan_revision=hashlib.sha256(b"").hexdigest(),
                run=None,
            )
        from codex_harness.core.status_api import plan_status

        return plan_status(self._store())

    def _wire_call_tool(self, name, arguments):
        mutations = {"harness_chat"}
        if name in mutations:
            return self.call_tool(name, arguments, schema_tools=self._wire_tools())
        previous = self.store
        try:
            if (self.workspace / ".codex-harness/plan.sqlite3").exists():
                with open_plan_store_readonly(self.workspace) as store:
                    self.store = store
                    return self.call_tool(name, arguments, schema_tools=self._wire_tools())
            return self.call_tool(name, arguments, schema_tools=self._wire_tools())
        except ValueError as error:
            return self._reply_error(error)
        finally:
            self.store = previous

    def _reply_error(self, error):
        from codex_harness.core.planner_refusals import reply
        return reply(error, self.workspace)

    def call_tool(self, name, arguments, *, schema_tools=None):
        try:
            tool = next((t for t in TOOLS if t["name"] == name), None)
            if tool is None:
                raise ValueError(
                    f"Unknown tool: {name}; use harness_chat and read-only inspection"
                )
            validate_input(arguments, tool["inputSchema"])
            data = self._dispatch(name, arguments)
            if name == "harness_chat":
                data = run_loop.wait_for_question(self._store())
            reply = dict(
                isError=False,
                structuredContent=data,
                content=[dict(type="text", text=json.dumps(data, ensure_ascii=False, default=str))],
            )
            observed = (
                dict(
                    phase=data["phase"],
                    plan_revision=data.get("revision", data.get("plan_revision")),
                )
                if "phase" in data
                else None
            )
            return connection_reply(reply, self.workspace, observed)
        except (ValueError, OSError, KeyError, TypeError) as error:
            return self._reply_error(error)

    def _dispatch(self, name, args):
        if name == "harness_chat":
            from codex_harness.core.run_chat import chat
            if self.store is None:
                self.store = open_plan_store(self.workspace, self.workspace.name)
            return chat(self.store, **args)
        if name == "harness_plan_status":
            return self._status()
        if name == "harness_schema":
            semantic = bool(
                self.store and self.store._data()["nodes"] and self.store.spine().semantic
            )
            return schema_help(TOOLS, **args, semantic=semantic)
        if name == "harness_preflight":
            from codex_harness.core.preflight import preflight_report

            return preflight_report(self.workspace, **args)
        store = self._store()
        if name == "harness_judgment":
            return store.judgment(**args)
        if name == "harness_nodes":
            return dict(nodes=store.list_nodes(args.get("kind")))
        if name == "harness_show_node":
            if args.get("include_source"):
                return store.show_node(args["id"], **{k: v for k, v in args.items() if k != "id"})
            return show_node(store, args["id"], body=args.get("body", False), test=args.get("test"))
        if name == "harness_packet_view":
            result = packet_view(store, args["id"], args.get("provider", "codex"))
            if not args.get("include_packet", False):
                result.pop("packet", None)
            return result
        if name == "harness_waves":
            return store.outcome_waves()
        if name == "harness_review":
            result = store.review(args["id"])
            result["findingMembers"] = views.review_finding_members(store, result)
            return result
        if name == "harness_review_policy":
            return views.plan_review_policy_contract(store)
        if name == "harness_diagram":
            return dict(mermaid=diagram(store.spine(), args["kind"]))
        if name == "harness_impact":
            return store.impact_since(args.get("since"))
        if name == "harness_mail_inbox":
            from codex_harness.core.mail import Mailbox

            return dict(
                messages=Mailbox(self.workspace).inbox(
                    args.get("agent", "planner"), unacked_only=args.get("unacked_only", True)
                )
            )
        raise ValueError("Unknown tool: " + name)

    def _begin_loop_confirmation(self, request_id, arguments):
        validate_input(
            arguments, next(t["inputSchema"] for t in TOOLS if t["name"] == "harness_chat")
        )
        if not self._elicitation or not self._initialized:
            raise ValueError(
                "Native elicitation unavailable; reply to the displayed decision in your own words"
            )
        if self._pending_confirmation is not None:
            raise ValueError("Native confirmation already pending")
        request = run_loop.cursor(self._store()).get("userRequest")
        if not request or request["gate"] not in {
            "align+vision-lock",
            "plan-execution",
        }:
            raise ValueError("No native planning confirmation pending")
        if arguments.get("reply_to") not in (None, request["request_id"]):
            raise ValueError("Reply to the current displayed subject token")
        proposed = request.get("proposedAction", {})
        prepared = confirmations.prepare_confirmation(
            self._store(),
            request["gate"],
            execution=proposed.get("execution"),
        )
        with self.store._transaction():
            run = run_loop.cursor(self.store)
            run["userRequest"] = prepared
            run_loop.save(self.store, run)
        return self._native_question(request_id, prepared)

    def _confirmation_reply(self, data, *, error=False, reason=None):
        run_loop.initialize(self._store())
        with self._store()._transaction():
            run = run_loop.cursor(self.store)
            if data["state"] != "pending":
                run_loop._decision(self.store, run, data)
            run_loop.save(self.store, run)
        run_loop.run_loop(self.store)
        result = run_loop.wait_for_question(self.store)
        return dict(
            isError=error,
            structuredContent=result,
            content=[dict(type="text", text=json.dumps(result, ensure_ascii=False))],
        )


def main():
    from codex_harness.core.planner_stdio import serve

    serve(PlannerServer)


if __name__ == "__main__":
    main()
