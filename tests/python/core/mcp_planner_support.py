import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

# CHARTER section 4 groups adjacent store phases; these are journey phases,
# not controller cursor states. Intermediate phases with no old MCP traffic
# remain visible in their group instead of inventing a nonzero old baseline.
CHARTER_PHASES = (
    "Align/Expand/A",
    "Spec/Bead plan",
    "Review/B",
    "Publication",
    "Build/Observe/Judge/Steer",
    "Acceptance/C",
)
PHASE_GROUP = {
    **dict.fromkeys(("align", "expand", "vision-lock"), CHARTER_PHASES[0]),
    **dict.fromkeys(("features", "requirements", "tech-stack", "plan"), CHARTER_PHASES[1]),
    "roundtable": CHARTER_PHASES[2],
    "publish": CHARTER_PHASES[3],
    "execute": CHARTER_PHASES[4],
    "accept": CHARTER_PHASES[5],
}
CHOREOGRAPHY_TOOLS = frozenset(
    {
        "harness_advance",
        "harness_compile",
        "harness_publish",
        "harness_roundtable",
        "harness_readiness",
        "harness_readiness_status",
        "harness_audit",
        "harness_audit_status",
        "harness_worker_start",
        "harness_worker_status",
        "harness_accept_run",
        "harness_accept_status",
        "harness_accept_review",
        "harness_accept_rebind",
        "harness_accept_assess",
        "harness_accept",
        "harness_recovery",
        "harness_plan_resume",
    }
)


class LoopClient:
    """A semantic client over the real stdio transport; never supplies choreography."""

    instances = []

    def __init__(self, workspace, surface="cli", *, native=False, kickoff=None, source_root=None):
        self.workspace, self.surface = Path(workspace), surface
        self.native = native
        self.workspace.mkdir(parents=True, exist_ok=True)
        from tests.python.core.test_golden_path import kickoff_content
        self.setup = kickoff_content(surface) if kickoff is None else kickoff
        settings = self.workspace / ".codex-harness/setup.json"
        if not settings.is_file():
            settings.parent.mkdir(exist_ok=True)
            settings.write_text(json.dumps(self.setup), encoding="utf-8")
        self.current, self.serial = {}, 0
        self.calls = Counter(dict.fromkeys(CHARTER_PHASES, 0))
        self.judge_calls = Counter(dict.fromkeys(CHARTER_PHASES, 0))
        self.tool_names, self.errors = Counter(), []
        self.traffic, self.decision_gates = [], []
        self.instances.append(self)
        self.replies = queue.Queue()
        self.out_of_order = {}
        # Transport diagnostics are metadata: reconnect must not mutate the
        # product bytes bound by a completed acceptance receipt.
        (self.workspace / ".codex-harness").mkdir(exist_ok=True)
        self.log_path = self.workspace / ".codex-harness/server.log"
        self.log = self.log_path.open("a", encoding="utf-8")
        root = Path(__file__).resolve().parents[3]
        tools = self.workspace / "tools"
        tools.mkdir(exist_ok=True)
        for name in ("codex", "claude"):
            executable = tools / (name + ".exe" if os.name == "nt" else name)
            if not executable.exists():
                shutil.copy2(sys.executable, executable)
        paths = [str(tools), str(Path(shutil.which("git")).parent)]
        if os.name == "nt":
            paths.extend(
                [
                    str(Path(os.environ["SystemRoot"]) / "System32"),
                    str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0"),
                ]
            )
        self.env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str((Path(source_root) if source_root else root) / "src"), str(root)]),
            "PATH": os.pathsep.join(paths),
            "LOCALAPPDATA": str(tools),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "cp1252",
        }
        if surface == "web":
            from codex_harness.core.semantic_runner import web_toolchain

            self.env["SELDON_WEB_CHROMIUM"] = web_toolchain(None, False)["chromium"]["path"]
        self.command = [
            sys.executable,
            "-B",
            str(root / "tests/python/core/test_golden_path.py"),
            "server",
            str(self.workspace),
            surface,
        ]
        self._open()

    def _open(self):
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            text=True,
            encoding="utf-8",
            env=self.env,
        )

        def read(stream, sink):
            for line in stream:
                try:
                    sink.put(json.loads(line))
                except ValueError as error:
                    sink.put(error)
            sink.put(None)

        self.reader = threading.Thread(
            target=read, args=(self.process.stdout, self.replies), daemon=True
        )
        self.reader.start()
        self.rpc(
            "initialize",
            dict(
                protocolVersion="2025-06-18",
                capabilities=dict(elicitation={}) if self.native else {},
                clientInfo=dict(name="golden-semantic-client", version="2c"),
            ),
        )
        self.rpc("notifications/initialized", {}, notification=True)

    def phase_group(self):
        pending = self.current.get("question") or {}
        request = self.current.get("userRequest") or {}
        if pending.get("family") == "author" and pending.get("purpose") == "trace":
            return CHARTER_PHASES[1]
        if request.get("gate") == "plan-execution" or (request.get("gate") == "invocations" and not pending):
            return CHARTER_PHASES[2]
        return PHASE_GROUP[self.current.get("phase", "align")]

    def record_call(self, name):
        name = name if isinstance(name, str) else json.dumps(name, sort_keys=True)
        phase = self.phase_group()
        self.calls[phase] += 1
        self.tool_names[name] += 1
        judge = (
            name == "harness_loop_answer"
            and (self.current.get("question") or {}).get("role") == "judge"
        )
        if judge:
            self.judge_calls[phase] += 1
        pending = self.current.get("question") or {}
        self.traffic.append(
            dict(
                phase=phase,
                tool=name,
                judge=judge,
                requestId=pending.get("requestId"),
                revision=pending.get("revision"),
                judgmentId=(pending.get("judgment") or {}).get("id"),
                semanticCheck=((pending.get("judgment") or {}).get("responseExample") or {})
                .get("meaning", {})
                .get("check"),
            )
        )

    def rpc(self, method, params, *, notification=False, timeout=180):
        if method == "tools/call":
            self.record_call(params["name"])
        self.serial += 1
        request = dict(jsonrpc="2.0", method=method, params=params)
        if not notification:
            request["id"] = self.serial
        written = threading.Event()
        failures = []

        def send():
            try:
                self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError) as error:
                failures.append(error)
            finally:
                written.set()

        threading.Thread(target=send, daemon=True).start()
        assert written.wait(timeout), f"MCP pipe write exceeded {timeout}s: {method}"
        assert not failures, failures
        if notification:
            return
        result = self.take_reply(self.serial, timeout)
        return result["result"]

    def take_reply(self, request_id, timeout=180):
        if request_id in self.out_of_order:
            return self.out_of_order.pop(request_id)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"MCP response exceeded {timeout}s: {request_id}"
            result = self.replies.get(timeout=remaining)
            assert isinstance(result, dict), (
                result,
                self.log_path.read_text(encoding="utf-8"),
            )
            if result.get("id") == request_id:
                break
            self.out_of_order[result.get("id")] = result
        assert isinstance(result, dict), (
            result,
            self.log_path.read_text(encoding="utf-8"),
        )
        assert "error" not in result, result
        return result

    def call(self, tool_name, *, error=False, **args):
        result = self.rpc(
            "tools/call",
            dict(name=tool_name, arguments=dict(expected_workspace=str(self.workspace), **args)),
        )
        assert result["isError"] is error, result
        if error:
            self.errors.append(dict(tool=tool_name, result=result))
            return result
        data = result["structuredContent"]
        if "runId" in data:
            self.current = data
        elif data.get("run"):
            self.current = data["run"]
        return data

    def answer(self, value):
        return self.call(
            "harness_loop_answer",
            request_id=self.current["question"]["requestId"],
            answer=value,
            source="golden:scripted-semantic-provider",
        )

    def send_answer(self, value):
        """Drop/interrupt a real response while the server owns bounded work."""
        self.serial += 1
        self.record_call("harness_loop_answer")
        self.process.stdin.write(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=self.serial,
                    method="tools/call",
                    params=dict(
                        name="harness_loop_answer",
                        arguments=dict(
                            expected_workspace=str(self.workspace),
                            request_id=self.current["question"]["requestId"],
                            answer=value,
                            source="golden:scripted-semantic-provider",
                        ),
                    ),
                )
            )
            + "\n"
        )
        self.process.stdin.flush()
        return self.serial

    def send_start(self):
        """Reattach asynchronously when the real controller reaches the next fault hold."""
        self.serial += 1
        self.record_call("harness_plan_start")
        self.process.stdin.write(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=self.serial,
                    method="tools/call",
                    params=dict(
                        name="harness_plan_start",
                        arguments=dict(
                            expected_workspace=str(self.workspace),
                            name="Reattach retained run",
                            idea="Consume the original canonical receipts",
                        ),
                    ),
                )
            )
            + "\n"
        )
        self.process.stdin.flush()
        return self.serial

    def send_steer(self, **arguments):
        """Send actual control without waiting past a deliberately held provider return."""
        self.serial += 1
        self.record_call("harness_steer")
        self.process.stdin.write(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=self.serial,
                    method="tools/call",
                    params=dict(
                        name="harness_steer",
                        arguments=dict(expected_workspace=str(self.workspace), **arguments),
                    ),
                )
            )
            + "\n"
        )
        self.process.stdin.flush()
        return self.serial

    def native_offer(self):
        self.serial += 1
        self.record_call("harness_steer")
        self.native_tool_id = self.serial
        self.process.stdin.write(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=self.serial,
                    method="tools/call",
                    params=dict(
                        name="harness_steer",
                        arguments=dict(
                            expected_workspace=str(self.workspace),
                            direction="continue",
                            rationale="Actual user requested native confirmation",
                            native_confirmation=True,
                        ),
                    ),
                )
            )
            + "\n"
        )
        self.process.stdin.flush()
        offer = self.replies.get(timeout=15)
        assert offer["method"] == "elicitation/create", offer
        return offer

    def native_response(self, offer, *, retain_reply=True):
        self.process.stdin.write(
            json.dumps(
                dict(
                    jsonrpc="2.0",
                    id=offer["id"],
                    result=dict(
                        action="accept",
                        content=dict(
                            approve=True,
                            statement="Yes, approve this displayed subject.",
                            conditions="",
                        ),
                    ),
                )
            )
            + "\n"
        )
        self.process.stdin.flush()
        if retain_reply:
            reply = self.replies.get(timeout=30)
            assert reply["id"] == self.native_tool_id and not reply["result"]["isError"], reply
            self.current = reply["result"]["structuredContent"]
            return self.current

    def words(self, text="Yes, approve the displayed subject."):
        request = self.current["userRequest"]
        self.decision_gates.append(request["gate"])
        return self.call(
            "harness_steer",
            direction="continue",
            rationale="Retain actual external fixture words",
            user_words=dict(
                request_id=request["request_id"],
                expected_revision=request["plan_revision"],
                quoted_message=text,
                quote_sha256=hashlib.sha256(text.encode()).hexdigest(),
                actor_label="user",
            ),
        )

    def reconnect(self):
        self.process.kill()
        self.process.wait(timeout=5)
        assert self.process.returncode != 0
        self.reader.join(timeout=5)
        assert not self.reader.is_alive()
        self.process.stdin.close()
        self.process.stdout.close()
        self.replies = queue.Queue()
        self.out_of_order = {}
        self._open()
        return self.call("harness_plan_status")

    def close(self):
        failing = sys.exc_info()[0] is not None
        try:
            if failing and self.process.poll() is None:
                self.process.kill()
            if self.process.poll() is None:
                self.process.stdin.close()
                self.process.wait(timeout=10)
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            self.reader.join(timeout=5)
            self.process.stdout.close()
            self.log.close()
        if not failing:
            assert self.process.returncode == 0, self.log_path.read_text("utf-8")


def workspace_wire(server, tool_name, **arguments):
    """Actual unscoped wire; never fill missing caller intent from the server."""
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 901,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        )
    )["result"]


def rpc(server, method, params=None, request_id=1):
    # Existing same-project fixtures explicitly target their constructed project.
    # Routing counterexamples use workspace_wire instead, with no implicit intent.
    if (
        method == "tools/call"
        and isinstance(params, dict)
        and isinstance(params.get("arguments", {}), dict)
    ):
        params = {
            **params,
            "arguments": {
                "expected_workspace": str(server.workspace),
                **params.get("arguments", {}),
            },
        }
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
    )
