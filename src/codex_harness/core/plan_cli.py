"""Headless planner: MCP tool text followed by an executable next-step hint."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from codex_harness.core import planner_views, roundtable, test_audit
from codex_harness.core.graph import load_plan
from codex_harness.core.mcp_planner import TOOLS, PlannerServer, _validate
from codex_harness.core.plan_quality import quality_report, render_quality_card
from codex_harness.core.plan_store import PHASES
from codex_harness.core.preflight import diagnostic_status
from codex_harness.core.spine import Spine, compile_manifest

TYPED_DECISION_SCHEMAS = {"dot-intake-v1", "dot-reviewer-selection-v1", "dot-wave-map-v1"}


class InputParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def read_json(value: str):
    """Accept literal JSON, @JSON or @JSONL; reject nonfinite numbers."""

    def invalid(value):
        raise ValueError(f"Invalid JSON constant: {value}")

    if value.startswith("@"):
        path = Path(value[1:])
        value = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() == ".jsonl":
            nodes = [
                json.loads(line, parse_constant=invalid)
                for line in value.splitlines()
                if line.strip()
            ]
            if not nodes:
                raise ValueError("Node batch must not be empty")
            return nodes
    return json.loads(value, parse_constant=invalid)


def batch_nodes(server: PlannerServer, nodes: list) -> str:
    """Use the canonical writer in one locked transaction, including validation."""
    if not nodes:
        raise ValueError("Node batch must not be empty")
    schema = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == "harness_upsert_node")
    for node in nodes:
        _validate({"node": node}, schema)
    if len({node["id"] for node in nodes}) != len(nodes):
        raise ValueError("Duplicate node ids in batch")
    store = server._store()
    with store._transaction():
        data = store._data()
        merged = {node["id"]: node for node in data["nodes"]}
        merged.update({node["id"]: node for node in nodes})
        spine = Spine({**data, "nodes": list(merged.values())})
        for node in nodes:
            store._write_node(spine, node["id"])
            store._event(
                "node-upserted", {"id": node["id"], "plan_revision": spine.plan_revision()}
            )
    return "\n".join(f"Changed node: {node['id']} (upserted)." for node in nodes)


def next_command(server: PlannerServer, status: dict) -> str:
    prefix = f'python -m codex_harness.core.plan_cli --workspace "{server.workspace}" '

    def cmd(value):
        return prefix + value

    gate, phase = status["pending_gate"], status["phase"]
    if gate == "harness_plan_start":
        return cmd('start --idea "<idea>" --name "<name>"')
    if phase == "unknown":
        return cmd("status")
    store = server._store()
    if status.get("vision_lock_stale"):
        return (
            "Use native MCP harness_record_confirmation gate=vision-lock "
            "(vision changed; renewed client confirmation required)"
        )
    if phase == "tech-stack":
        for key in store.spine().ids("decision"):
            fields = store.spine().node(key)["fields"]
            if fields.get("schema") in TYPED_DECISION_SCHEMAS:
                continue
            if not fields.get("chosen") or not fields.get("rationale"):
                return cmd(f'decision --id {key} --choose "<option name>" --rationale "<reason>"')
    advance = (
        cmd(f"advance --to {PHASES[PHASES.index(phase) + 1]}")
        if phase != "accept"
        else cmd("status")
    )
    if gate in ("approval:align", "approval:vision-lock"):
        approval = status.get("approval") or {}
        if approval.get("granted") and approval.get("gate") == gate.split(":", 1)[1]:
            return advance
        confirm = f"Use native MCP harness_record_confirmation gate={gate.split(':', 1)[1]}"
        fields = store.spine().node("V1")["fields"]
        if any(
            not fields.get(key)
            for key in ("vision", "users", "first_user_moment", "scope_in", "scope_out")
        ):
            return (
                cmd(
                    'card --vision "<vision>" --users "<users>" --first-moment "<moment>" '
                    '--scope-in "<in>" --scope-out "<out>"'
                )
                + " then "
                + confirm
            )
        return confirm
    if gate == "requirements-have-tests" and any(
        item["gap"] == "requirement-without-test" for item in status["coverage_gaps"]
    ):
        return cmd('node --json "@tests.jsonl"') + " then " + advance
    if gate == "requirements-have-beads-and-valid-manifest":
        if any(item["gap"] == "requirement-without-bead" for item in status["coverage_gaps"]):
            return cmd('node --json "@beads.jsonl"') + " then " + advance
        try:
            store._manifest(store.spine())
        except ValueError:
            return (
                cmd('node --json "@plan-fixes.jsonl"')
                + " then "
                + cmd('compile --template "template" --out "manifest.json"')
            )
    if status["open_clarifications"]:
        rows = store.connection.execute(
            "SELECT id FROM clarifications WHERE status='open' ORDER BY id"
        )
        return " then ".join(cmd(f'answer --id {row[0]} --text "<answer>"') for row in rows)
    if phase in {"publish", "execute"} and status["publication"]["up_to_date"] and not gate:
        return (
            cmd(
                "worker-start --expected-revision "
                + status["published_revision"]
                + ' --workers "@workers.json"'
            )
            + " (workers implement; the planner steers)"
        )
    if gate == "current-revision-published" or phase == "roundtable":
        resolution = status.get("reviewResolution") or planner_views.review_resolution_state(
            store, status
        )
        return planner_views.review_resolution_next(resolution, cli_prefix=prefix)
    if phase == "accept":
        acceptance = status["acceptance"]
        if acceptance["accepted"]:
            return cmd("status") + (
                " (proxy acceptance)"
                if acceptance["isProxy"]
                else " (recorded, origin not verified)"
            )
        if acceptance["invalidReason"] in ("revision-algorithm-upgraded", "legacy-unbound"):
            return cmd("accept-rebind --repo " + json.dumps(acceptance["repo"]))
        if acceptance["invalidReason"] == "vision-unlocked":
            return cmd('confirm --gate vision-lock --statement "<renewed user approval>"')
        if acceptance["invalidReason"] == "scenarios-changed":
            return (
                cmd('accept-scenarios --input "@scenarios.json"')
                + " then "
                + cmd('accept-run --subject product --repo "<integrated-repo>"')
            )
        if not store.acceptance_scenarios():
            return cmd("accept-scenarios --derive")
        if (
            acceptance["lastRunSubject"] != "product"
            or not acceptance["scenarios"]
            or not all(r["lastResult"] == "pass" for r in acceptance["scenarios"])
        ):
            return cmd('accept-run --subject product --repo "<integrated-repo>"')
        if any(item["status"] == "open" for item in acceptance["findings"]):
            return cmd("findings") + " then " + cmd('resolve --id <AF-id> --resolution "<fix>"')
        from codex_harness.core.acceptance import current_run

        run = current_run(store)
        reviewed = [
            json.loads(row[0])
            for row in store.connection.execute("SELECT result_json FROM acceptance_reviews")
        ]
        if not any(review["runId"] == run["id"] for review in reviewed):
            return cmd('accept-review --repo "<integrated-repo>"')
        return cmd('accept --statement "<actual acceptance words>" --by user')
    if phase == "features" and not store.spine().ids("feature"):
        return (
            cmd('node --json "@features.jsonl"')
            + " then "
            + cmd("diagram --kind feature-map")
            + " then "
            + advance
        )
    if phase == "tech-stack" and not any(
        store.spine().node(key)["fields"].get("schema") not in TYPED_DECISION_SCHEMAS
        for key in store.spine().ids("decision")
    ):
        return (
            cmd('decision --json "@decision.json"')
            + " then "
            + cmd("diagram --kind decision-map")
            + " then "
            + advance
        )
    if (
        phase == "expand"
        and not store.connection.execute("SELECT 1 FROM clarifications").fetchone()
    ):
        return cmd('ask --question "<scope or uplift question>" --node V1') + " then " + advance
    return advance


def parser() -> argparse.ArgumentParser:
    root = InputParser(description=__doc__)
    root.add_argument("--workspace", required=True)
    root.add_argument("--json", dest="machine", action="store_true")
    subs = root.add_subparsers(dest="command", required=True)
    specs = {
        "start": ("idea", "name"),
        "status": (),
        "preflight": (),
        "show": (),
        "review": (),
        "packet": (),
        "nodes": (),
        "card": ("vision", "users", "first-moment", "scope-in", "scope-out"),
        "confirm": ("gate", "statement"),
        "worker-start": ("expected-revision", "workers"),
        "worker-status": ("job-id",),
        "advance": ("to",),
        "ask": ("question",),
        "answer": ("id", "text"),
        "assume": ("text",),
        "resolve-assumption": ("id", "rationale"),
        "node": ("json",),
        "remove-node": ("id",),
        "decision": ("json",),
        "intake": ("json",),
        "reviewer-catalog": ("json",),
        "reviewer-choice": ("json",),
        "wave-map": ("json",),
        "waves": (),
        "wave-exit": ("run", "through"),
        "refine-wave": (),
        "diagram": ("kind",),
        "impact": (),
        "supervision-report": ("run",),
        "render": (),
        "roundtable": (),
        "readiness": (),
        "findings": (),
        "resolve": (),
        "audit": (),
        "quality": (),
        "compile": ("template", "out"),
        "publish": (),
        "accept-scenarios": (),
        "accept-run": ("repo",),
        "accept-rebind": ("repo",),
        "accept-review": ("repo",),
        "accept": ("statement", "by"),
    }
    commands = {}
    for name, flags in specs.items():
        sub = subs.add_parser(name)
        commands[name] = sub
        for flag in flags:
            sub.add_argument(
                "--" + flag,
                required=not (name == "decision" and flag == "json"),
                dest="payload" if flag == "json" else flag.replace("-", "_"),
            )
        if "json" not in flags:
            sub.add_argument(
                "--json", dest="machine", action="store_true", default=argparse.SUPPRESS
            )
        sub.add_argument(
            "--machine-json", dest="machine", action="store_true", default=argparse.SUPPRESS
        )
    commands["show"].add_argument("id")
    commands["review"].add_argument("id")
    commands["show"].add_argument("--body", action="store_true")
    commands["show"].add_argument("--test")
    commands["packet"].add_argument("id")
    commands["packet"].add_argument(
        "--provider", choices=["codex", "claude", "copilot"], default="codex"
    )
    commands["status"].add_argument("--brief", action="store_true")
    commands["preflight"].add_argument("--provider", action="append", dest="providers")
    commands["impact"].add_argument("--since", help="Manifest revision or saved manifest file")
    commands["supervision-report"].add_argument("--baseline-run")
    commands["supervision-report"].add_argument("--forecast")
    commands["supervision-report"].add_argument("--interventions")
    commands["nodes"].add_argument("--kind")
    commands["card"].add_argument("--assumption", action="append", default=[])
    commands["accept-run"].add_argument("--subject", choices=["product", "oracle"])
    commands["accept-scenarios"].add_argument("--derive", action="store_true")
    commands["accept-scenarios"].add_argument("--input", help="Scenario list JSON or @file")
    commands["accept-review"].add_argument("--reviewer", action="append", default=[])
    commands["accept-review"].add_argument(
        "--resume", help="Recover a registered completed review without launching providers"
    )
    commands["confirm"].add_argument("--by", default="user")
    commands["worker-start"].add_argument("--max-parallel", type=int, default=2)
    commands["worker-start"].add_argument("--timeout", type=int, default=900)
    commands["advance"].add_argument("--by", default="planner")
    commands["advance"].add_argument("--statement")
    commands["refine-wave"].add_argument("wave_id")
    commands["refine-wave"].add_argument("--run")
    commands["refine-wave"].add_argument("--expected-spine")
    action = commands["refine-wave"].add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--response")
    action.add_argument("--apply")
    commands["resolve-assumption"].add_argument("--by", required=True)
    commands["ask"].add_argument("--node", action="append", default=[])
    group = commands["resolve-assumption"].add_mutually_exclusive_group(required=True)
    group.add_argument("--confirmed", action="store_true")
    group.add_argument("--rejected", action="store_true")
    commands["render"].add_argument("--out")
    commands["decision"].add_argument("--id")
    commands["decision"].add_argument("--choose")
    commands["decision"].add_argument("--rationale")
    audit_mode = commands["audit"].add_mutually_exclusive_group(required=True)
    audit_mode.add_argument("--oracle")
    audit_mode.add_argument("--prospective", action="store_true")
    commands["publish"].add_argument(
        "--proof-level", choices=["oracle-verified", "prospective"], default="oracle-verified"
    )
    commands["publish"].add_argument("--reason")
    baseline = commands["audit"].add_mutually_exclusive_group()
    baseline.add_argument("--baseline", choices=["template"], help="Use template (not a directory)")
    baseline.add_argument(
        "--baseline-ref", help="Git baseline ref; defaults to HEAD when available"
    )
    commands["roundtable"].add_argument("--max-rounds", type=int, default=2)
    readiness_selection = commands["readiness"].add_mutually_exclusive_group()
    readiness_selection.add_argument("--beads", help="Comma-separated bead ids")
    readiness_selection.add_argument("--sample", type=int)
    commands["readiness"].add_argument("--seed", type=int, default=0)
    commands["readiness"].add_argument("--run", help="Run directory with implemented producers")
    commands["readiness"].add_argument("--max-calls", type=int)
    commands["readiness"].add_argument(
        "--reviewer", action="append", default=[], help="Reviewer JSON or @file"
    )
    commands["roundtable"].add_argument("--max-total", type=int)
    commands["findings"].add_argument("--expand", action="store_true")
    commands["findings"].add_argument("--template", action="store_true")
    resolution_target = commands["resolve"].add_mutually_exclusive_group(required=True)
    resolution_target.add_argument("--id")
    resolution_target.add_argument("--file", type=Path)
    commands["resolve"].add_argument("--resolution")
    resolution_status = commands["resolve"].add_mutually_exclusive_group()
    resolution_status.add_argument("--resolved", action="store_true")
    resolution_status.add_argument("--rejected", action="store_true")
    resolution_status.add_argument("--deferred", action="store_true")
    commands["roundtable"].add_argument(
        "--reviewer",
        action="append",
        default=[],
        help="Reviewer JSON or @file: name, provider, optional model/effort",
    )
    commands["roundtable"].add_argument(
        "--test-only-review-output",
        type=Path,
        help="TEST ONLY: inject this response for each reviewer; no provider launch",
    )
    return root


def dispatch(server: PlannerServer, args) -> tuple[dict, dict | None]:
    """Standard commands go through MCP unchanged; extensions reuse core owners."""
    command = args.command
    if command == "advance" and (args.statement is not None or args.by != "planner"):
        if args.to != "roundtable" or server._store().phase() != "plan":
            raise ValueError("Explicit plan confirmation belongs to plan -> roundtable")
        store = server._store()
        store.advance(args.to, by=args.by, statement=args.statement)
        try:
            data = store.wave_product_lock()
        except ValueError:
            data = None
        return _reply(server, "Advanced to roundtable with supplied confirmation provenance"), data
    if command in ("wave-exit", "refine-wave"):
        from codex_harness.core.acceptance import run_wave_exits
        from codex_harness.core.wave_refine import MAX_RESPONSE_BYTES, need, parse_json

        store = server._store()
        if command == "wave-exit":
            data = run_wave_exits(store, _path(server, args.run), args.through)
        elif args.prepare:
            need(args.run is not None, "--run is required for prepare")
            data = store.prepare_wave_refinement(args.wave_id, _path(server, args.run))
        elif args.response:
            need(
                args.expected_spine is not None
                and args.run is not None
                and args.response.startswith("@"),
                "Response requires @path, --run and --expected-spine",
            )
            with _path(server, args.response[1:]).open("rb") as stream:
                raw = stream.read(MAX_RESPONSE_BYTES + 1)
            response = parse_json(raw)
            context = store.wave_refinement_context(args.wave_id, _path(server, args.run))
            need(
                response.get("contextDigest") == context["contextDigest"],
                "Response context mismatch",
                "wave-context-stale",
            )
            recorded = store.record_wave_refinement(
                args.expected_spine, context["contextDigest"], raw
            )
            data = {
                key: recorded[key]
                for key in (
                    "proposalId",
                    "state",
                    "contextDigest",
                    "responseRawSha256",
                    "readiness",
                    "review",
                    "publication",
                    "completedRealDelta",
                )
            }
            data.update(
                schema="dot-wave-refinement-record-receipt-v1",
                responseSourceRef=str(_path(server, args.response[1:]).resolve()),
                sourceRef=f"wave-refinement-proposal:{recorded['proposalId']}",
                proposedNodeIds=sorted(n["id"] for n in recorded["proposedNodes"]),
                unrefinedSketchIds=recorded["unrefinedSketchIds"],
                questionCount=len(recorded["questions"]),
                cardClassifications=[c["classification"] for c in recorded["cards"]],
                proposedNodes=recorded["proposedNodes"],
                proposedDetail=recorded["proposedDetail"],
                cards=recorded["cards"],
                questions=recorded["questions"],
                impact=recorded["impact"],
            )
        else:
            need(args.expected_spine is not None, "--expected-spine is required")
            data = store.apply_wave_refinement(
                args.apply, args.expected_spine, wave_id=args.wave_id
            )
        return _reply(server, json.dumps(data, ensure_ascii=False)), data
    if command == "preflight":
        arguments = {"providers": args.providers} if args.providers is not None else {}
        reply = server.call_tool("harness_preflight", arguments)
        return reply, reply.get("structuredContent")
    if command in ("intake", "reviewer-catalog", "reviewer-choice", "wave-map", "waves"):
        arguments = {} if command == "waves" else read_json(args.payload)
        reply = server.call_tool("harness_" + command.replace("-", "_"), arguments)
        return reply, reply.get("structuredContent")
    if command.startswith("accept"):
        arguments = {}
        if command == "accept-scenarios":
            arguments = {"derive": args.derive}
            if args.input:
                arguments["scenarios"] = read_json(args.input)
        elif command == "accept":
            arguments = {"statement": args.statement, "by": args.by}
        else:
            arguments = {"repo": str(_path(server, args.repo).resolve())}
            if command == "accept-run":
                arguments["subject"] = args.subject
            if command == "accept-review" and args.reviewer:
                arguments["reviewers"] = [read_json(item) for item in args.reviewer]
            if command == "accept-review" and args.resume is not None:
                arguments["resume"] = args.resume
        reply = server.call_tool("harness_" + command.replace("-", "_"), arguments)
        for item in reply["content"]:
            item["text"] = item["text"].rsplit("\nNext: ", 1)[0]
        return reply, reply.get("structuredContent")

    def decision():
        if args.payload:
            value = read_json(args.payload)
        elif args.id:
            node = server._store().spine().node(args.id)
            if node["kind"] != "decision":
                raise ValueError("--id must name a decision")
            if node["fields"].get("schema") in TYPED_DECISION_SCHEMAS:
                raise ValueError(
                    "Typed decisions must use their intake, reviewer-choice or wave-map route"
                )
            value = dict(
                id=node["id"], question=node["title"], parents=node["parents"], **node["fields"]
            )
        else:
            raise ValueError("decision requires --json or --id")
        if args.choose is not None:
            value["chosen"] = args.choose
        if args.rationale is not None:
            value["rationale"] = args.rationale
        return value

    tools = {
        "start": ("harness_plan_start", lambda: dict(idea=args.idea, name=args.name)),
        "status": ("harness_plan_status", lambda: {}),
        "worker-start": (
            "harness_worker_start",
            lambda: dict(
                expected_revision=args.expected_revision,
                workers=read_json(args.workers),
                max_parallel=args.max_parallel,
                timeout=args.timeout,
            ),
        ),
        "worker-status": ("harness_worker_status", lambda: dict(job_id=args.job_id)),
        "review": ("harness_review", lambda: dict(id=args.id)),
        "show": (
            "harness_show_node",
            lambda: dict(id=args.id, body=args.body, **({"test": args.test} if args.test else {})),
        ),
        "packet": ("harness_packet_view", lambda: dict(id=args.id, provider=args.provider)),
        "readiness": (
            "harness_readiness",
            lambda: {
                "seed": args.seed,
                **({"beads": args.beads.split(",")} if args.beads is not None else {}),
                **({"sample": args.sample} if args.sample is not None else {}),
                **({"max_calls": args.max_calls} if args.max_calls is not None else {}),
                **(
                    {"reviewers": [read_json(item) for item in args.reviewer]}
                    if args.reviewer
                    else {}
                ),
            },
        ),
        "nodes": ("harness_nodes", lambda: {"kind": args.kind} if args.kind else {}),
        "card": (
            "harness_understanding_card",
            lambda: dict(
                vision=args.vision,
                users=args.users,
                first_user_moment=args.first_moment,
                scope_in=args.scope_in,
                scope_out=args.scope_out,
                assumptions=args.assumption,
            ),
        ),
        "advance": ("harness_advance", lambda: dict(to_phase=args.to)),
        "ask": ("harness_ask", lambda: dict(question=args.question, node_ids=args.node)),
        "answer": ("harness_answer", lambda: dict(id=args.id, answer=args.text)),
        "assume": ("harness_add_assumption", lambda: dict(text=args.text, node_ids=[])),
        "resolve-assumption": (
            "harness_resolve_assumption",
            lambda: dict(
                id=args.id, confirmed=args.confirmed, rationale=args.rationale, by=args.by
            ),
        ),
        "remove-node": ("harness_remove_node", lambda: dict(id=args.id)),
        "decision": ("harness_decision_card", decision),
        "diagram": ("harness_diagram", lambda: dict(kind=args.kind)),
        "render": ("harness_render", lambda: dict(out_dir=args.out)),
        "publish": (
            "harness_publish",
            lambda: dict(proof_level=args.proof_level, reason=args.reason)
            if args.reason is not None
            else dict(proof_level=args.proof_level),
        ),
    }
    if command in tools:
        tool, arguments = tools[command]
        if command == "readiness" and args.run is not None:
            from codex_harness.core.readiness import readiness_review, render_readiness

            data = readiness_review(server._store(), run=_path(server, args.run), **arguments())
            reply = {
                "isError": bool(data["invalid"]),
                "structuredContent": data,
                "content": [{"type": "text", "text": render_readiness(data)}],
            }
        else:
            reply = server.call_tool(tool, arguments())
        if command in ("show", "nodes"):
            for item in reply["content"]:
                item["text"] = item["text"].rsplit("\nNext: ", 1)[0]
        if command == "status" and args.brief and not reply["isError"]:
            status = server._status()
            score = quality_report(server._store())["score"]
            reply["content"][0]["text"] = (
                planner_views.banner(status)
                + "\n"
                + planner_views.status_line(status)
                + f"\nphase: {status['phase']}\npending_gate: {status['pending_gate'] or 'none'}"
                + f"\nPlanner quality: {score}/100 · {status['proofLevelLabel']}"
                + f" · Worker readiness (advisory): {status['readiness']['status']}"
                + f"\nOpen: {status['open_clarifications']} questions, "
                + f"{status['open_assumptions']} assumptions"
            )
            if status["acceptance"].get("invalidMessage"):
                reply["content"][0]["text"] += (
                    "; Acceptance invalid: " + status["acceptance"]["invalidMessage"]
                )
        elif command == "status":
            totals = server._status().get("roundtable", [])
            for item in totals:
                reply["content"][0]["text"] = reply["content"][0]["text"].rsplit("\nNext: ", 1)[0]
                reply["content"][0]["text"] += "\n" + item["reviewer"] + ": " + item["text"]
        return reply, reply.get("structuredContent")
    if command == "node":
        nodes = read_json(args.payload)
        if not isinstance(nodes, list):
            reply = server.call_tool("harness_upsert_node", {"node": nodes})
            return reply, reply.get("structuredContent")
        body, data = batch_nodes(server, nodes), {"id": [node["id"] for node in nodes]}
    elif command == "confirm":
        raise ValueError(
            "plan-confirmation-origin-unavailable: CLI quotes and --by labels cannot establish "
            "a fresh native client response. Use MCP harness_record_confirmation gate="
            + args.gate
            + " in a client with negotiated elicitation."
        )
    else:
        store = server._store()
        if command == "impact":
            data = store.impact_since(args.since)
            body = planner_views.change_digest(data)
        elif command == "roundtable":
            reviewers = [read_json(value) for value in args.reviewer] or None
            for reviewer in reviewers or []:
                _validate(
                    reviewer,
                    {
                        "type": "object",
                        "properties": dict.fromkeys(
                            ("name", "provider", "model", "effort"),
                            {"type": "string", "minLength": 1},
                        ),
                        "required": ["name", "provider"],
                    },
                    "reviewer",
                )
            injection = {}
            if args.test_only_review_output is not None:
                output = args.test_only_review_output.read_text(encoding="utf-8")
                injection = dict(
                    runner=lambda launch, cwd, timeout: (0, output),
                    executable_for=lambda provider: provider,
                )
                if reviewers is None:
                    raise ValueError("Test-only runner requires explicit --reviewer entries")
            data = roundtable.run_roundtable(
                store,
                reviewers=reviewers,
                max_rounds=args.max_rounds,
                max_total=args.max_total,
                **injection,
            )
            data["id"] = data["roundId"]
            body = planner_views.roundtable_card(data, roundtable.findings(store))
            if (
                not data["reviewers"]
                or data["invalid"]
                or any(item["exitCode"] != 0 for item in data["reviewers"])
            ):
                return _reply(server, body, True), data
        elif command == "findings":
            if args.template and args.expand:
                raise ValueError("--template lists open clusters; omit --expand")
            data = {
                "findings": roundtable.findings(store, expand=args.expand)
                + store.acceptance_findings()
            }
            if args.template:
                data["template"] = [
                    dict(
                        id=row["id"],
                        status="deferred" if row["severity"] in ("low", "minor") else "resolved",
                        resolution="",
                    )
                    for row in data["findings"]
                    if row["status"] == "open"
                ]
            body = planner_views.findings_card(data["findings"])
            if not args.expand:
                body += "\nCluster members: " + ", ".join(
                    f"{row['id']}: {row.get('memberCount', 1)}" for row in data["findings"]
                )
            else:
                body += "\nCluster membership: " + ", ".join(
                    f"{row['id']}: {row.get('clusteredWith') or row['id']}"
                    for row in data["findings"]
                )
        elif command == "resolve":
            if args.file is not None:
                if args.resolution is not None or args.resolved or args.rejected or args.deferred:
                    raise ValueError(
                        "--file supplies each status and resolution; omit single-finding flags"
                    )
                dispositions, errors = [], []
                for line, text in enumerate(
                    _path(server, args.file).read_text(encoding="utf-8-sig").splitlines(), 1
                ):
                    try:
                        dispositions.append(json.loads(text))
                    except ValueError as error:
                        errors.append(f"Line {line}: Invalid JSON: {error}")
                if errors:
                    raise ValueError(
                        "Invalid dispositions; no changes applied:\n" + "\n".join(errors)
                    )
                body = server._dispatch("harness_resolve_findings", {"findings": dispositions})
                return _reply(server, body), server.result_data
            statuses = {
                item["id"]: item["status"]
                for item in roundtable.findings(store, expand=True) + store.acceptance_findings()
            }
            if args.id not in statuses:
                raise ValueError(f"Unknown finding: {args.id}")
            previous = statuses[args.id]
            body = server._dispatch(
                "harness_resolve_finding",
                {
                    "id": args.id,
                    "resolution": args.resolution,
                    "status": "deferred"
                    if args.deferred
                    else "rejected"
                    if args.rejected
                    else "resolved",
                },
            )
            body = body.replace(": ", f": {previous} → ", 1)
            data = None
        elif command == "quality":
            data = quality_report(store)
            body = render_quality_card(data)
            deferred = [
                item["id"] for item in roundtable.findings(store) if item["status"] == "deferred"
            ]
            if deferred:
                body += "\nDeferred findings: " + ", ".join(deferred)
            if any(check["status"] == "fail" for check in data["checks"]):
                return _reply(server, body, True), data
        elif command == "compile":
            data = store.compile_template(_path(server, args.template))
            with TemporaryDirectory(prefix="plan-cli-compile-") as temp:
                check = Path(temp) / "manifest.json"
                check.write_text(json.dumps(data), encoding="utf-8")
                load_plan(check)
            target = _path(server, args.out)
            target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            data["id"] = data["planRevision"]
            body = f"Compiled manifest: {target}"
        elif command == "audit":
            if args.prospective and args.baseline_ref:
                raise ValueError("Prospective audit uses the template, not a Git baseline")
            spine = store.spine()
            manifest = compile_manifest(spine, server.workspace / "template")
            store.check_requirement_files(manifest, server.workspace / "template")
            revision = store.status()["published_revision"]
            affected = (
                store.impact_since(revision) if revision else {"affectedBeads": spine.ids("bead")}
            )
            template = server.workspace / "template"
            data = (
                test_audit.audit_prospective_tests(
                    template=template,
                    manifest=manifest,
                    spine=spine,
                )
                if args.prospective
                else test_audit.audit_revised_tests(
                    repo=server.workspace,
                    base_ref=args.baseline_ref or args.baseline or "auto",
                    revised_template=template,
                    manifest=manifest,
                    impact=affected,
                    oracle_dir=_path(server, args.oracle),
                )
            )
            data["id"] = store.record_audit(manifest["planRevision"], data)
            body = test_audit.render_test_audit_card(data)
            if not data["ok"]:
                return _reply(server, body, True), data
        else:
            raise ValueError(f"Unknown command: {command}")
    return _reply(server, body), data


def _path(server: PlannerServer, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else server.workspace / path


def _reply(server: PlannerServer, body: str, error=False, *, diagnostic=False) -> dict:
    status = diagnostic_status(server.workspace) if diagnostic else server._status()
    return {
        "isError": error,
        "content": [
            {
                "type": "text",
                "text": planner_views.response(status, body),
            }
        ],
    }


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parse_error = None
    args = argparse.Namespace(workspace=".", machine=False)
    try:
        args = parser().parse_args(arguments, namespace=args)
    except ValueError as error:
        parse_error = error
        # Global options already parsed remain available even on a subcommand error.
        args.machine = (
            args.machine
            or "--machine-json" in arguments
            or (
                "--json" in arguments
                and not any(item in arguments for item in ("node", "decision"))
            )
        )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if getattr(args, "command", None) == "supervision-report":
        from codex_harness.core.supervision import report

        if parse_error is not None:
            print(json.dumps({"isError": True, "error": str(parse_error), "data": None}))
            return 2
        data = report(
            args.workspace,
            args.run,
            baseline_run=args.baseline_run,
            forecast=args.forecast,
            intervention_log=args.interventions,
        )
        print(json.dumps({"isError": False, "data": data}, ensure_ascii=False))
        return 0
    server = PlannerServer(args.workspace)
    try:
        try:
            if parse_error is not None:
                raise parse_error
            reply, data = dispatch(server, args)
            if (
                getattr(args, "template", False)
                and args.command == "findings"
                and not reply["isError"]
            ):
                for item in data["template"]:
                    print(json.dumps(item, ensure_ascii=False))
                return 0
        except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
            reply, data = (
                _reply(
                    server,
                    f"{getattr(error, 'code', 'tool-input-or-operation-error')}: {error}",
                    True,
                    diagnostic=getattr(args, "command", None) == "preflight",
                ),
                None,
            )
        if getattr(args, "command", None) == "preflight":
            status = diagnostic_status(server.workspace)
            next_step = (
                "Review missing/unknown observations; "
                "existing planning and proof gates still apply."
            )
        else:
            status = (
                data if args.command == "status" and isinstance(data, dict) else server._status()
            )
            next_step = next_command(server, status)
            if args.command in {"worker-start", "worker-status"} and data and not reply["isError"]:
                next_step = (
                    f'python -m codex_harness.core.plan_cli --workspace "{server.workspace}" '
                    "worker-status --job-id " + data["jobId"]
                )
        if getattr(args, "command", None) in (
            "intake",
            "reviewer-catalog",
            "reviewer-choice",
            "wave-map",
            "waves",
        ):
            next_step = (
                f'python -m codex_harness.core.plan_cli --workspace "{server.workspace}" '
                + (
                    "status (refresh and reconcile refused draft input)"
                    if reply["isError"]
                    else "waves"
                )
            )
        if reply["isError"]:
            prefix = f'python -m codex_harness.core.plan_cli --workspace "{server.workspace}" '
            command = getattr(args, "command", "status")
            if command == "accept-run" and getattr(args, "subject", None) is None:
                next_step = (
                    prefix
                    + 'accept-run --repo "<integrated-repo>" --subject product OR '
                    + prefix
                    + 'accept-run --repo "<reference-repo>" --subject oracle'
                )
            elif "plan-requirement-file-missing" in str(reply):
                next_step = (
                    "Restore listed requirement files; "
                    + prefix
                    + "compile --template template --out manifest.json; "
                    + prefix
                    + "audit --oracle <existing-reference> "
                    "(or explicitly authorized --prospective); " + prefix + "quality"
                )
            elif command == "audit":
                baseline = (
                    f'--baseline-ref "{args.baseline_ref}"'
                    if getattr(args, "baseline_ref", None)
                    else "--baseline template"
                    if getattr(args, "baseline", None)
                    else ""
                )
                next_step = (
                    prefix
                    + f"audit {baseline} "
                    + (
                        "--prospective "
                        if getattr(args, "prospective", False)
                        else (
                            f'--oracle "{getattr(args, "oracle", None) or "<existing-reference>"}" '
                        )
                    )
                    + "(inspect the reported proof failure; use --baseline template "
                    "when no Git baseline exists)"
                )
            elif command == "roundtable" and data:
                failed = [
                    item for item in data["reviewers"] if item["invalid"] or item["exitCode"] != 0
                ]
                artifacts = [item["artifactPath"] for item in failed if item.get("artifactPath")]
                retry = " ".join(
                    "--reviewer '"
                    + json.dumps(
                        {
                            key: item[key]
                            for key in ("name", "provider", "model", "effort")
                            if key in item
                        }
                    ).replace("'", "''")
                    + "'"
                    for item in failed
                )
                next_step = (
                    (
                        "Inspect " + ", ".join(artifacts) + "; "
                        if artifacts
                        else "Make a reviewer executable available; "
                    )
                    + prefix
                    + "roundtable "
                    + retry
                    + f" --max-rounds {args.max_rounds}"
                    + (f" --max-total {args.max_total}" if args.max_total is not None else "")
                )
            elif command == "impact" and not status["published_revision"]:
                next_step = (
                    prefix
                    + "compile --template template --out manifest.json; "
                    + prefix
                    + "audit --oracle <existing-reference> "
                    "(or explicitly authorized --prospective); " + prefix + "quality"
                )
            elif command == "roundtable" and "roundtable-total-budget-exhausted" in str(reply):
                next_step = (
                    "Waive the cap by raising or omitting --max-total, "
                    "or accept the remaining findings"
                )
            elif command == "roundtable":
                next_step = (
                    prefix + "roundtable --help (check the reported budget or input; "
                    "retry only the affected reviewer)"
                )
            elif command in ("publish", "advance") and any(
                code in str(reply)
                for code in ("plan-publication-evidence-required", "plan-findings-open")
            ):
                next_step = planner_views.review_resolution_next(
                    status["reviewResolution"], cli_prefix=prefix
                )
        cli_names = {
            "harness_plan_start": "start",
            "harness_plan_status": "status",
            **{
                tool["name"]: tool["name"].removeprefix("harness_").replace("_", "-")
                for tool in TOOLS
                if tool["name"].startswith("harness_accept")
            },
            **{
                tool: command
                for command, tool in (
                    ("card", "harness_understanding_card"),
                    ("advance", "harness_advance"),
                    ("node", "harness_upsert_node"),
                    ("remove-node", "harness_remove_node"),
                    ("ask", "harness_ask"),
                    ("answer", "harness_answer"),
                    ("assume", "harness_add_assumption"),
                    ("resolve-assumption", "harness_resolve_assumption"),
                    ("decision", "harness_decision_card"),
                    ("diagram", "harness_diagram"),
                    ("impact", "harness_impact"),
                    ("render", "harness_render"),
                    ("publish", "harness_publish"),
                )
            },
        }
        next_step = next_step.replace("\r", " ").replace("\n", " ")
        display_next = planner_views.first_action(next_step)
        if (
            not args.machine
            and getattr(args, "command", None) == "roundtable"
            and reply["isError"]
            and data
        ):
            display_next = (
                next_step.split("; ", 1)[0]
                + "; retry only the affected reviewers with roundtable --reviewer @reviewer.json."
            )
        text = (
            "\n".join(item["text"] for item in reply["content"]).rsplit("\nNext: ", 1)[0].rstrip()
            + "\nNext: "
            + display_next
        )
        for tool in sorted(cli_names, key=len, reverse=True):
            text = text.replace(tool, cli_names[tool])
        if status["pending_gate"] in cli_names:
            status = {**status, "pending_gate": cli_names[status["pending_gate"]]}
        if args.machine:

            def without_next(value):
                if isinstance(value, dict):
                    return {k: without_next(v) for k, v in value.items() if k != "next"}
                if isinstance(value, list):
                    return [without_next(v) for v in value]
                return value

            print(
                json.dumps(
                    {
                        "isError": reply["isError"],
                        "text": text.rsplit("\nNext: ", 1)[0],
                        "status": without_next(status),
                        "data": without_next(data),
                        "next": next_step,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(text)
        return 2 if reply["isError"] else 0
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
