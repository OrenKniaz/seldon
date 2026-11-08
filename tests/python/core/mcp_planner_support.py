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


def steering_text(arguments):
    direction = arguments.get("direction")
    return "/" + direction if direction in {"pause", "stop"} else "/resume" if arguments.get("resume") and direction == "continue" else arguments.get("rationale", direction or "Continue")


def apply_content(store, content):
    from codex_harness.core.planner_nodes import batch_nodes
    batch_nodes(store, content["nodes"], lambda n: None)


def fixture_id(workspace, handle):
    """Resolve proposed fixture handles through CODE's allocation receipt."""
    aliases = Path(workspace) / ".codex-harness/provider-aliases.json"
    if aliases.exists() and handle in json.loads(aliases.read_text("utf-8")):
        return json.loads(aliases.read_text("utf-8"))[handle]
    import sqlite3
    database = Path(workspace) / ".codex-harness/plan.sqlite3"
    if not database.exists():
        return handle
    with sqlite3.connect(database) as connection:
        for row in connection.execute("SELECT payload_json FROM events WHERE kind='trace-handles-allocated' ORDER BY id DESC"):
            receipt = json.loads(row[0])
            mapping = {**receipt["handles"], **receipt.get("scenarioHandles",{})}
            if handle in mapping:
                return mapping[handle]
    return handle


def provider_shape(schema, value, root=None):
    """Convert retained fixture data to strict optional/null record shapes."""
    root = schema if root is None else root
    if "$ref" in schema:
        def data(v):
            if isinstance(v, dict):
                return dict(entries=[dict(key=k, value=data(x)) for k, x in v.items()])
            return [data(x) for x in v] if isinstance(v, list) else v
        return data(value)
    if "anyOf" in schema:
        if value is None:
            return None
        for branch in schema["anyOf"]:
            if "$ref" in branch or "anyOf" in branch:
                return provider_shape(branch, value, root)
            if branch.get("type") == "object" and isinstance(value, dict):
                props = branch["properties"]
                if any(k not in props for k in value):
                    continue
                if any("enum" in props[k] and v not in props[k]["enum"] for k, v in value.items()):
                    continue
                return provider_shape(branch, value, root)
            if ((branch.get("type") == "string" and isinstance(value, str))
                or (branch.get("type") == "integer" and type(value) is int)
                or (branch.get("type") == "number" and type(value) in (int, float))
                or (branch.get("type") == "boolean" and type(value) is bool)):
                return value
            if branch.get("type") == "array" and isinstance(value, list):
                return provider_shape(branch, value, root)
        raise AssertionError((schema, value))
    if schema["type"] == "object":
        return {k: (dict(value=provider_shape({"$ref":"#/$defs/data"},value[k],root)) if k in {"json","jsonEquals","stdoutJsonEquals","fileJsonEquals"} else provider_shape(v, value[k], root)) if k in value else
                   None if "anyOf" in v and any(b.get("type") == "null" for b in v["anyOf"]) else
                   [] if v.get("type") == "array" else
                   provider_shape(v, {}, root) if v.get("type") == "object" else value[k]
                for k, v in schema["properties"].items()}
    if schema["type"] == "array":
        return [provider_shape(schema["items"], x, root) for x in value]
    return value


def provider_answer(workspace, pending, value):
    """2e-b seam: one typed question + supplied facts -> a scripted provider answer."""
    if isinstance(value, str):
        return value
    family, purpose = pending["family"], pending["purpose"]
    if family in {"author", "repair"}:
        content = value.get("content", {})
        plan_path = Path(workspace) / ".codex-harness/provider-plan.json"
        if content.get("nodes"):
            plan_path.write_text(json.dumps(content), encoding="utf-8")
        nodes = content.get("nodes") or (json.loads(plan_path.read_text("utf-8"))["nodes"] if plan_path.exists() else __import__("tests.python.core.test_golden_path",fromlist=["plan_content"]).plan_content()["nodes"])
        if purpose == "understanding":
            vision = next((n for n in nodes if n["kind"] == "vision"), None)
            if vision is None:
                return json.dumps(value)
            f = vision["fields"]
            def words(key):
                v = f.get(key, [])
                return v if isinstance(v, list) else [v]
            answer = dict(understanding=vision["text"], users=words("users"),
                firstUserMoment=f.get("first_user_moment", "Use the complete product"), scopeIn=words("scope_in"),
                scopeOut=words("scope_out"), qualityBar=f["quality_bar"], doneFeelsLike=f["done_feels_like"],
                assumptions=words("assumptions"), openQuestions=[],
                upgrades=[dict(proposal=u["text"], kind="macro" if u["kind"] == "macro" else "micro", impact=u["impact"], disposition=u["disposition"]) for u in f["expansion"]["upgrades"]],
                qualityCriteria=words("qualityCriteria"), metrics=[], stopConditions=[], readiness=value["option"],
                question=content.get("question"), reply="Here is the complete proposal and quality bar.", rationale=value["rationale"])
        elif purpose == "plan-outline":
            from codex_harness.core.run_schemas import TRACE_FIELDS
            from codex_harness.core.plan_store import open_plan_store_readonly
            with open_plan_store_readonly(workspace) as store:
                existing = set(store.spine().ids())
                vf = store.spine().node(store.spine().ids("vision")[0])["fields"]
                target_ids = set(vf.get("targets",{}))
                scenario_ids = {r["id"] for r in vf.get("scenarios",{}).get("scenarios",[])}
            proposed = next((n["fields"] for n in nodes if n["kind"] == "vision"),{})
            answer = {kind:[dict(ref=fixture_id(workspace,n["id"]) if fixture_id(workspace,n["id"]) in existing else None,title=n["title"],text=n["text"]) for n in nodes if n["kind"] == kind] for kind in TRACE_FIELDS}
            answer.update(targets=[dict(ref=fixture_id(workspace,k) if fixture_id(workspace,k) in target_ids else None,purpose=k) for k in proposed.get("targets",{})],
                          scenarios=[dict(ref=fixture_id(workspace,r["id"]) if fixture_id(workspace,r["id"]) in scenario_ids else None,purpose=r["id"]) for r in proposed.get("scenarios",{}).get("scenarios",[])],
                          rationale=value["rationale"],reply="Propose the complete plan before CODE assigns references.")
        elif purpose == "finding-dispositions":
            answer = dict(dispositions=[dict(memberRef=r["id"], disposition=r["status"], resolution=r["resolution"]) for r in content["findings"]], rationale=value["rationale"], reply="The finding response is ready for judgment.")
        else:
            from codex_harness.core.plan_store import open_plan_store_readonly
            aliases_path = Path(workspace) / ".codex-harness/provider-aliases.json"
            aliases = json.loads(aliases_path.read_text("utf-8")) if aliases_path.exists() else {}
            for n in nodes:
                found = next((d for d in pending["context"]["nodes"] if d["kind"] == n["kind"] and d["title"] == n["title"]),None)
                if found: aliases[n["id"]] = found["id"]
            for group in ("targets","scenarios"):
                aliases.update({r["purpose"]:r["id"] for r in pending["context"][group]})
            aliases_path.write_text(json.dumps(aliases),encoding="utf-8")
            records = []
            with open_plan_store_readonly(workspace) as store:
                vision = store.spine().node(store.spine().ids("vision")[0])
                upgrades = vision["fields"].get("expansion", {}).get("upgrades", [])
                mapping = {"Q" + str(i + 1): u["id"] for i, u in enumerate(upgrades)}
                for n in nodes:
                    if n["kind"] == "vision":
                        continue
                    if not any(d["id"] == aliases.get(n["id"]) for d in pending["context"]["nodes"]):
                        continue
                    fields = dict(n["fields"])
                    if "expansionRefs" in fields:
                        fields["expansionRefs"] = [mapping.get(k, k) for k in fields["expansionRefs"]]
                    if "clauseCoverage" in fields:
                        fields["clauseCoverage"] = [dict(clause=k, coverage=v) for k, v in fields["clauseCoverage"].items()]
                    for key in ("provider", "model", "effort"):
                        fields.pop(key, None)
                    existing = fixture_id(workspace, n["id"])
                    records.append(dict(ref=aliases[n["id"]], parents=n["parents"], fields=fields))
            vf = json.loads(json.dumps(next((n["fields"] for n in nodes if n["kind"] == "vision"), {})))
            targets = [dict(ref=aliases[k], **v) for k, v in vf.get("targets", {}).items()]
            scenarios = [dict(ref=aliases[s["id"]], **{k: v for k, v in s.items() if k not in {"id", "verdict", "evidenceTier", "validationMessage"}}) for s in vf.get("scenarios", {}).get("scenarios", [])]
            for scenario in scenarios:
                observations = scenario.get("observe",[])
                snapshots = {r["id"]:r["path"] for r in scenario.get("setup",{}).pop("snapshots",[])}
                for action in scenario["actions"]:
                    action["checks"] = [{k:v for k,v in o.items() if k != "action"} for o in observations if o.get("action") == action["id"]]
                    del action["id"]
                    for step in action.get("web",{}).get("steps",[]): step.pop("id",None)
                scenario["observe"] = [{k:snapshots[v] if k == "bytesUnchangedFrom" else v for k,v in o.items()} for o in observations if "path" in o]
            answer = dict(records=records, remove=content.get("remove", []), targets=targets, scenarios=scenarios,
                          rationale=value["rationale"], reply="The linked trace and work plan are ready.")
            from codex_harness.core.semantic_schema import TRACE_LINKS
            refkeys = set(TRACE_LINKS) | {"parents","dependsOn","lessons","sourceRef","targetRefs","requirementRefs","contractRefs","testRef","target","ref","remove"}
            def refs(v):
                if isinstance(v,dict) and "id" in v and "revision" in v: return aliases.get(v["id"],v["id"])
                if isinstance(v,list): return [refs(r) for r in v]
                return aliases.get(v,v) if isinstance(v,str) else v
            def translate(v):
                if isinstance(v,list): return [translate(r) for r in v]
                return {k:refs(x) if k in refkeys else translate(x) for k,x in v.items()} if isinstance(v,dict) else v
            answer = translate(answer)

    elif family == "direction":
        plan_path = Path(workspace) / ".codex-harness/provider-plan.json"
        if value.get("content",{}).get("nodes") and plan_path.exists():
            complete = json.loads(plan_path.read_text("utf-8"))
            merged = {n["id"]:n for n in complete["nodes"]}
            merged.update({n["id"]:n for n in value["content"]["nodes"]})
            complete["nodes"] = list(merged.values())
            plan_path.write_text(json.dumps(complete),encoding="utf-8")
        answer = dict(action=value["option"], amendments=[], question=value.get("content", {}).get("question"),
                      rationale=value["rationale"], reply="I have recorded the direction.")
    else:
        meaning = dict(value["meaning"])
        for key in ("check", "phase", "evidenceBinding"):
            meaning.pop(key, None)
        if purpose == "finding-disposition":
            meaning["dispositions"] = [dict(memberRef=k, disposition=v) for k, v in meaning.pop("memberDispositions").items()]
        if purpose in {"execution", "visualQuality"}:
            meaning["qualitativeChecks"] = [dict(check=k, passed=v) for k, v in meaning.get("qualitativeChecks", {}).items()]
            if purpose == "visualQuality":
                meaning = dict(qualitativeChecks=meaning["qualitativeChecks"])
        answer = {**meaning, "rationale":value["rationale"]}
        if family == "user-interpretation":
            answer.update(requestedChanges="" if meaning["decision"] == "approve" else "Clarify the proposal", reply="I have retained your words for the displayed subject.")
        else:
            answer.update(verdict=value["verdict"], gaps=[dict(subjectRef=pending["judgment"]["subjectRefs"][0]["id"], evidenceRefs=[], issue=g) for g in value["gaps"]])
    return json.dumps(provider_shape(pending["answerSchema"], answer), ensure_ascii=False)

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

    def __init__(self, workspace, surface="cli", *, native=True, kickoff=None, source_root=None):
        self.workspace, self.surface = Path(workspace), surface
        self.expected_review_tier = "same-model" if surface == "cli" else "cross-provider"
        self.native = native
        self.workspace.mkdir(parents=True, exist_ok=True)
        from tests.python.core.test_golden_path import kickoff_content
        self.setup = kickoff_content(surface) if kickoff is None else kickoff
        settings = self.workspace / ".codex-harness/setup.json"
        if not settings.is_file():
            settings.parent.mkdir(exist_ok=True)
            settings.write_text(json.dumps(self.setup), encoding="utf-8")
        self.current, self.serial = {}, 0
        self.active_call = None
        self.last_offer = None
        self.seen_faults = set()
        self.ignored_offers = set()
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
        for name in ("codex", "claude", "copilot"):
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
        if request.get("gate") == "plan-execution":
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
                semanticCheck=next((ref["payload"]["check"] for ref in (pending.get("judgment") or {}).get("sourceRefs",[]) if ref["kind"] == "candidate-observation"),None),
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

    def _chat_send(self, text, reply_to=None):
        self.serial += 1
        self.record_call("harness_chat")
        arguments = dict(expected_workspace=str(self.workspace), text=text)
        if reply_to is not None:
            arguments["reply_to"] = reply_to
        self.active_call = self.serial
        self.process.stdin.write(json.dumps(dict(jsonrpc="2.0", id=self.serial, method="tools/call",
            params=dict(name="harness_chat", arguments=arguments)), ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        return self.serial

    def progress(self, timeout=180):
        """Wait on the provider fixture's event or the real outstanding RPC."""
        answered = self.last_offer if self.last_offer and Path(self.offered["answerFile"]).exists() else None
        deadline = time.monotonic() + timeout
        marker_root = self.workspace / ".codex-harness"
        while time.monotonic() < deadline:
            try:
                reply = self.replies.get(timeout=.03)
            except queue.Empty:
                reply = None
            if reply is not None:
                if reply.get("method") == "notifications/message":
                    if self.active_call is not None:
                        continue
                    self.current = reply["params"]["data"]
                    if not self.current.get("effect") and (self.current.get("userRequest") or self.current.get("semanticHeld") or self.current["state"] in {"DONE","STOPPED","PAUSED"}):
                        return self.current
                    continue
                if reply.get("method") == "elicitation/create":
                    return reply
                if reply.get("id") == self.active_call:
                    self.active_call = None
                    assert not reply["result"]["isError"], reply
                    self.current = reply["result"]["structuredContent"]
                    if answered and (self.current.get("effect") or not (
                        self.current.get("semanticHeld") or self.current["state"] in {"DONE", "STOPPED", "PAUSED"}
                        or self.current.get("userRequest") and not self.current.get("question")
                    )):
                        continue  # An RPC snapshot is not the next provider boundary.
                    return self.current
                self.out_of_order[reply.get("id")] = reply
            for fault in ("root-admission-pending", "root-published-pending", "popen-pending", "admission-pending", "revalidation-pending", "segment-publication", "continuation-complete", "delivery-pending", "candidate-pending", "delivery-complete", "plan-review-complete", "acceptance-scenarios-complete", "acceptance-review-sealed", "acceptance-review-admitted", "execution-raw-complete", "semantic-raw-complete", "review-crash-started"):
                if fault not in self.seen_faults and (marker_root / (fault + ".ready")).exists():
                    if fault in {"root-admission-pending", "root-published-pending", "popen-pending", "admission-pending", "revalidation-pending", "segment-publication", "continuation-complete", "delivery-pending", "candidate-pending", "delivery-complete"}:
                        from codex_harness.core.plan_store import open_plan_store_readonly
                        with open_plan_store_readonly(self.workspace) as store:
                            self.current = store.status()["run"]
                    else:
                        status = self.rpc("tools/call", dict(name="harness_plan_status", arguments=dict(expected_workspace=str(self.workspace))))
                        self.current = status["structuredContent"]["run"]
                    if answered and (self.current.get("effect") or {}).get("id") == answered[2]:
                        continue
                    self.seen_faults.add(fault)
                    return self.current
            ready = sorted(marker_root.glob("provider-offered-*.ready"),key=lambda p:p.stat().st_mtime_ns)
            for marker in reversed(ready):
                offered = json.loads(marker.with_suffix(".json").read_text("utf-8"))
                identity = (offered["requestId"],offered["fixesIssued"],offered["effectId"])
                if identity == self.last_offer or identity in self.ignored_offers or Path(offered["answerFile"]).exists():
                    continue
                status = self.rpc("tools/call",dict(name="harness_plan_status",arguments=dict(expected_workspace=str(self.workspace))))
                current = status["structuredContent"]["run"]
                if (current.get("effect") or {}).get("id") != offered["effectId"] or (current.get("question") or {}).get("requestId") != offered["requestId"]:
                    self.ignored_offers.add(identity)
                    continue
                self.last_offer, self.current, self.offered = identity,current,offered
                return self.current
        raise AssertionError(("No provider/user boundary before deadline", self.current, self.log_path.read_text("utf-8")))

    def call(self, tool_name, *, error=False, **args):
        # Retained journey helper names are fixture operations, never wire tools.
        # Only user words cross chat. Meaning is supplied at the provider boundary.
        if tool_name == "harness_plan_start":
            if not self.current:
                self._chat_send(args["idea"])
            elif self.current["state"] in {"PAUSED","DONE","STOPPED"} or self.current.get("semanticHeld") or self.current.get("userRequest") and not self.current.get("question") and not self.current.get("effect"):
                return self.call("harness_plan_status")
            result = self.progress()
            return self.current
        if tool_name == "harness_steer":
            if "user_words" in args:
                return self.call("harness_chat", error=error, text=args["user_words"]["quoted_message"], reply_to=args["user_words"]["request_id"])
            text = steering_text(args)
            from codex_harness.core.plan_store import open_plan_store_readonly
            with open_plan_store_readonly(self.workspace) as observed:
                prior = observed.connection.execute("SELECT json_extract(payload_json,'$.id') FROM events WHERE kind='chat-message' AND json_extract(payload_json,'$.role')='user' ORDER BY id DESC LIMIT 1").fetchone()
            prior_message = prior[0] if prior else None
            self._chat_send(text)
            result = self.progress()
            # Reconnect may still expose the prior provider boundary before the
            # new words are consumed. Answer only the packet containing this chat.
            while self.current.get("question") and self.offered:
                users = [r for r in self.offered["packet"]["facts"]["dialogue"] if r["role"] == "user"]
                if users and users[-1].get("id") != prior_message and users[-1]["text"] == text:
                    break
                result = self.progress()
            pending = self.current.get("question") or {}
            if pending.get("family") == "direction":
                self.answer(dict(option=args["direction"], rationale=text, content={}))
            elif pending.get("family") == "user-interpretation" and args.get("content"):
                from tests.python.core.test_golden_path import judge_reply
                self.answer(judge_reply(pending,decision="changes_requested"))
            if args.get("content",{}).get("nodes"):
                plan = self.workspace / ".codex-harness/provider-plan.json"
                from tests.python.core.test_golden_path import plan_content
                complete = json.loads(plan.read_text("utf-8")) if plan.exists() else plan_content(self.surface)
                merged = {n["id"]:n for n in complete["nodes"]}
                merged.update({n["id"]:n for n in args["content"]["nodes"]})
                complete["nodes"] = list(merged.values())
                if (self.current.get("question") or {}).get("family") == "author":
                    self.answer(dict(option="ready",rationale=text,content=complete))
            return self.current
        result = self.rpc("tools/call", dict(name=tool_name, arguments=dict(expected_workspace=str(self.workspace), **args)))
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
        self.send_answer(value)
        return self.progress()

    def send_answer(self, value):
        from tests.python.core.test_golden_path import provider_answer
        pending = self.current["question"]
        marker = None
        deadline = time.monotonic() + 10
        while marker is None and time.monotonic() < deadline:
            candidates = sorted((self.workspace / ".codex-harness").glob("provider-offered-*.ready"), key=lambda p:p.stat().st_mtime_ns)
            for ready in reversed(candidates):
                offered = json.loads(ready.with_suffix(".json").read_text("utf-8"))
                if offered["requestId"] == pending["requestId"] and offered["fixesIssued"] == pending["judgment"]["fixesIssued"] and not Path(offered["answerFile"]).exists():
                    marker = offered
                    self.offered = offered
                    self.last_offer = (offered["requestId"],offered["fixesIssued"],offered["effectId"])
                    break
            if marker is None:
                time.sleep(.03)
        assert marker is not None, ("No live provider boundary for request",pending["purpose"],pending["requestId"])
        raw = provider_answer(self.workspace, pending, value)
        path = Path(marker["answerFile"])
        staged = path.with_suffix(".pending")
        staged.write_text(raw, encoding="utf-8")
        staged.replace(path)
        Path(str(path)+".supplied").touch()
        return self.active_call

    def send_start(self):
        # Reconnect initialization wakes the durable owner; no chat polling/transition.
        return self.active_call

    def send_steer(self, **arguments):
        return self._chat_send(steering_text(arguments))

    def native_offer(self):
        self.native_tool_id = self._chat_send("/confirm", self.current["userRequest"]["request_id"])
        return self.progress()

    def native_response(self, offer, *, retain_reply=True):
        self.process.stdin.write(json.dumps(dict(jsonrpc="2.0", id=offer["id"], result=dict(action="accept",
            content=dict(approve=True, statement="Yes, approve this displayed subject.", conditions="")))) + "\n")
        self.process.stdin.flush()
        if retain_reply:
            return self.progress()

    def words(self, text="Yes, approve the displayed subject."):
        request = self.current["userRequest"]
        self.decision_gates.append(request["gate"])
        self._chat_send(text, request["request_id"])
        result = self.progress()
        if result.get("method") == "elicitation/create":
            self.native_tool_id = self.active_call
            return self.native_response(result)
        return result

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
        self.active_call = None
        self.last_offer = None
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


def _node(kind, node_id, parents, fields=None, text=None):
    return {
        "id": node_id,
        "kind": kind,
        "title": "Reject malformed parser input",
        "text": text or "Parser boundary: reject malformed input before saving any data.",
        "parents": parents,
        "fields": fields or {},
    }


def node_example(kind, semantic=False):
    from codex_harness.core.spine import Spine, semantic_spec, bead_contract, ALLOWED_PARENTS
    import sys
    """Small planning examples, never observations or evidence of acceptance."""
    vision = _node("vision", "V1", [])
    if kind == "vision":
        vision["text"] += "\n\nFull brief: <actual source path>"
        vision["fields"] = {
            "vision": vision["text"],
            "users": ["A person submitting parser input"],
            "first_user_moment": "Submit malformed input and see a clear error.",
            "scope_in": ["Parse input", "Reject malformed input without saving data"],
            "scope_out": ["GUI"],
            "quality_bar": "Keep original data unchanged when input is invalid.",
            "done_feels_like": "A new user submits malformed input, sees a clear error, "
            "and no data is saved.",
            "assumptions": [],
        }
    if semantic:
        vision["fields"]["planMode"] = "semantic-v1"
        if kind == "vision":
            vision["fields"].update(
                targets={"parser": {"entry": "hello.py", "argv": []}},
                scenarios={
                    "schema": "seldon/scenarios-v1",
                    "scenarios": [
                        dict(
                            id="S1",
                            requirementRefs=["R1"],
                            contractRefs=[],
                            setup=dict(
                                dirs=[],
                                files=[dict(path="{data}/input.json", utf8="{")],
                                snapshots=[dict(id="before", path="{data}/input.json")],
                            ),
                            actions=[
                                dict(
                                    id="reject",
                                    cli=dict(
                                        target="parser",
                                        operation="reject malformed input",
                                        argv=[{"input": "{data}/input.json"}],
                                    ),
                                )
                            ],
                            observe=[
                                dict(action="reject", exitEquals=1),
                                dict(path="{data}/input.json", bytesUnchangedFrom="before"),
                            ],
                            verdict="all",
                        )
                    ],
                },
            )
    feature = _node("feature", "F1", ["V1"])
    requirement = _node(
        "requirement",
        "R1",
        ["F1"],
        text="Parser boundary: malformed input must be rejected without saving data. "
        "Planned semantic verification: submit truncated JSON and assert no record is saved. "
        "Credible bug: parser saves partial data before raising. Existing happy-path "
        "proof misses this rejection and rollback behavior; add the negative case.",
    )
    examples = {
        "vision": ([], vision),
        "feature": ([vision], feature),
        "story": ([vision, feature], _node("story", "S1", ["F1"])),
        "requirement": ([vision, feature], requirement),
        "acceptance": ([vision], _node("acceptance", "A1", ["V1"])),
        "decision": ([vision], _node("decision", "D1", ["V1"])),
        "design": ([vision, feature, requirement], _node("design", "G1", ["R1"])),
        "bead": (
            [vision, feature, requirement],
            _node(
                "bead",
                "B1",
                ["R1"],
                {k: example for k, (example, _) in bead_contract(semantic).items()},
            ),
        ),
        "test": (
            [vision, feature, requirement],
            _node(
                "test",
                "T1",
                ["R1"],
                {
                    "module": "test_parser",
                    "level": "semantic",
                    "execution": dict(
                        entry="test_parser.py",
                        runtime=sys.executable,
                        argv=[],
                        testRoots=["test_parser.py"],
                        rationale="Observe parser rejection and unchanged data",
                    ),
                },
            ),
        ),
    }
    if kind in examples:
        prerequisites, example = examples[kind]
        if semantic and kind == "bead":
            test = node_example("test", True)[1]
            prerequisites.append(test)
            example["parents"] = [test["id"]]
            example["fields"]["semanticPhase"] = "build"
        snapshot = Spine(
            dict(
                schema="codex-harness/spine-v1",
                name="Schema example",
                nodes=[*prerequisites, example],
            )
        )
        if semantic:
            semantic_spec(snapshot, draft=True)
        return prerequisites, example
    # Templates bind real IDs/revisions at authoring time; never invent observations.
    parents = ["V1"] if "vision" in ALLOWED_PARENTS[kind] else ["R1"]
    return [vision, feature, requirement], _node(kind, "N1", parents)



def fixture_judgment_example(record):
    from codex_harness.core.planner_schema import judgment_response
    """A checked shape, never an assessment or approval of the bound evidence."""
    import json

    facts = next(
        (r["payload"]["facts"] for r in record["sourceRefs"] if "facts" in r["payload"]), {}
    )
    meaning = {
        "plan-review": dict(
            findings=[],
            depth="Focused review because this small local change has bounded consequences; inspect persistence and failure paths",
        ),
        "finding-equivalence": dict(groups=[[r["id"]] for r in facts.get("members", [])]),
        "finding-disposition": dict(
            memberDispositions={r["id"]: "open" for r in facts.get("members", [])}
        ),
        "review-reuse": dict(reuse=False),
        "plan-convergence": dict(action="continue-review"),
        "user-decision": dict(decision="changes_requested"),
        "vision-intent": dict(impact="changed"),
        "scope-intent": dict(impact="WHAT"),
        "planning-size": dict(
            size="small",
            process="Focused review of consequence and recovery",
            overrideAccepted=False,
        ),
    }.get(record["question"], {})
    loop = next(
        (ref["payload"] for ref in record["sourceRefs"] if ref["kind"] == "loop-question"), None
    )
    if loop:
        meaning = dict(
            option="draft" if loop["family"] == "author" else "continue",
            rationale="Example only; author the requested meaning",
            content={},
        )
    packet = next(
        (r["payload"] for r in record["sourceRefs"] if r["kind"] == "candidate-observation"), None
    )
    if packet:
        meaning = {k: packet[k] for k in ("check", "evidenceBinding", "phase")}
    example = dict(
        judgmentId=record["id"],
        binding=record["binding"],
        verdict="inconclusive",
        rationale="Example only; assess the bound evidence before answering",
        meaning=meaning,
        gaps=[],
    )
    if not loop:
        judgment_response(json.dumps(dict(example, verdict="positive")), record)
    # Symmetric shapes so the example never anchors the WHAT/HOW meaning.
    alternatives = (
        [dict(example, meaning=dict(impact="HOW"))] if record["question"] == "scope-intent" else []
    )
    for alternative in alternatives:
        judgment_response(json.dumps(dict(alternative, verdict="positive")), record)
    return dict(
        responseExample=example,
        responseAlternatives=alternatives,
        responseGuidance=(
            "Replace example semantics with your assessment. Verdict: positive=established; negative=concrete gaps; "
            "inconclusive=unresolved; unsupported=unavailable basis. meaning.depth accepts any nonblank string stating "
            "chosen depth and consequence-based reason, not an enum. Findings use packet node_id, severity "
            "(blocker|major|minor|note), finding and suggestion. For plan findings include classification "
            "(defect-in-plan|beyond-brief) and scope_reason explaining WHAT/HOW against the brief; beyond-brief is "
            "advisory only by explicit judgment. Empty or minor-only findings never imply approval."
        ),
    )
