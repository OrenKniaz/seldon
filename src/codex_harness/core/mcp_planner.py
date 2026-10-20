"""Small newline-delimited JSON-RPC MCP planner, backed only by PlanStore."""

from __future__ import annotations

import argparse
import hashlib
import json
import queue
import re
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from codex_harness.core import planner_views as views
from codex_harness.core.mail import KINDS, Mailbox
from codex_harness.core.plan_store import PHASES, open_plan_store
from codex_harness.core.planner_schema import node_error_help, schema_help
from codex_harness.core.preflight import diagnostic_status, preflight_report, render_preflight
from codex_harness.core.spine import ALLOWED_PARENTS
from codex_harness.core.spine_diagrams import diagram

PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
PHASE_DIAGRAMS = {
    "vision-lock": "user-journey",
    "features": "feature-map",
    "tech-stack": "decision-map",
    "plan": "bead-dag",
    "publish": "trace",
}
STRING = {"type": "string", "minLength": 1}
STRINGS = {"type": "array", "items": STRING}


def _object(properties: dict, required=None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def _tool(name: str, description: str, properties: dict, required=None) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": _object(properties, required),
    }


TOOLS = [
    _tool(
        "harness_schema",
        "Read existing tool input contracts and actionable canonical node examples; "
        "does not create a plan, approve gates or establish proof.",
        {"tool": STRING, "kind": {"type": "string", "enum": list(ALLOWED_PARENTS)}},
        [],
    ),
    _tool(
        "harness_preflight",
        "Read-only executable and workspace observations; "
        "no provider launch or execution-ready verdict.",
        {"providers": {"type": "array", "items": STRING, "minItems": 1}},
        [],
    ),
    _tool(
        "harness_intake",
        "Record draft intake. Node fields are intake.propose request fields; "
        "baseline resolves an existing publication pair.",
        {
            "node": {"type": "object"},
            "expected_spine_revision": STRING,
            "baseline": {"type": ["object", "null"]},
        },
        ["node", "expected_spine_revision"],
    ),
    _tool(
        "harness_reviewer_catalog",
        "Record supplied catalog observation; no discovery or provider call.",
        {"catalog": {"type": "object"}, "expected_spine_revision": STRING},
    ),
    _tool(
        "harness_reviewer_choice",
        "Record exact draft selection; optional author_sessions holds "
        "implementationSessionIds and reviewerSessionIds provenance.",
        {
            "node": {"type": "object"},
            "expected_spine_revision": STRING,
            "author_sessions": {"type": "object"},
        },
        ["node", "expected_spine_revision"],
    ),
    _tool(
        "harness_wave_map",
        "Record full draft map and details atomically. Optional historical_measurements "
        "is supplied history, never remeasured.",
        {
            "map_node": {"type": "object"},
            "detail_nodes": {"type": "array", "items": {"type": "object"}},
            "expected_spine_revision": STRING,
            "historical_measurements": {"type": "array", "items": {"type": "object"}},
        },
        ["map_node", "detail_nodes", "expected_spine_revision"],
    ),
    _tool(
        "harness_waves",
        "Read full draft outcomes and outstanding obligations; no execution evidence is bound.",
        {},
    ),
    _tool("harness_review", "Read a recorded review and its shard evidence.", {"id": STRING}),
    _tool(
        "harness_readiness",
        "Review exact worker packets for cold-start readiness (advisory; normal providers).",
        {
            "beads": STRINGS,
            "sample": {"type": "integer"},
            "seed": {"type": "integer"},
            "max_calls": {"type": "integer"},
            "reviewers": {
                "type": "array",
                "minItems": 1,
                "items": _object(
                    dict.fromkeys(("name", "provider", "model", "effort"), STRING),
                    ["name", "provider"],
                ),
            },
        },
        [],
    ),
    _tool(
        "harness_packet_view",
        "Read the exact worker packet and its size from the current compiled or published plan.",
        {"id": STRING, "provider": {"type": "string", "enum": ["codex", "claude", "copilot"]}},
        ["id"],
    ),
    _tool(
        "harness_show_node",
        "Read a node and its linked protected test functions or named body.",
        {"id": STRING, "body": {"type": "boolean"}, "test": STRING},
        ["id"],
    ),
    _tool(
        "harness_nodes",
        "Read a one-line listing of current nodes, optionally by kind.",
        {"kind": {"type": "string", "enum": list(ALLOWED_PARENTS)}},
        [],
    ),
    _tool(
        "harness_resolve_findings",
        "Atomically disposition an array of findings; canonical ids resolve every cluster member.",
        {
            "findings": {
                "type": "array",
                "minItems": 1,
                "items": _object(
                    {
                        "id": STRING,
                        "status": {"type": "string", "enum": ["resolved", "rejected", "deferred"]},
                        "resolution": STRING,
                    }
                ),
            },
        },
    ),
    _tool(
        "harness_resolve_finding",
        "Resolve, reject an incorrect finding, or defer a known gap.",
        {
            "id": STRING,
            "resolution": STRING,
            "status": {"type": "string", "enum": ["resolved", "rejected", "deferred"]},
        },
        ["id", "resolution"],
    ),
    _tool(
        "harness_accept_scenarios",
        "Derive, list or replace vision acceptance scenarios.",
        {
            "derive": {"type": "boolean"},
            "scenarios": {"type": "array", "items": {"type": "object"}},
        },
        [],
    ),
    _tool(
        "harness_accept_run",
        "Run acceptance scenarios from fresh user state; name product or oracle (dry run).",
        {"repo": STRING, "subject": {"type": "string", "enum": ["product", "oracle"]}},
    ),
    _tool(
        "harness_accept_rebind",
        "Rerun scenarios and rebind prior acceptance after legacy binding or revision algorithm "
        "migration with unchanged product.",
        {"repo": STRING},
    ),
    _tool(
        "harness_accept_review",
        "Cross-subscription acceptance review of the delivered product.",
        {
            "repo": STRING,
            "resume": STRING,
            "reviewers": {
                "type": "array",
                "items": _object(
                    dict.fromkeys(("name", "provider", "model", "effort"), STRING),
                    ["name", "provider"],
                ),
            },
        },
        ["repo"],
    ),
    _tool(
        "harness_accept",
        "Record the user's actual acceptance words or an explicitly labelled proxy.",
        {"statement": STRING, "by": STRING},
    ),
    _tool(
        "harness_mail_send",
        "Send attributable workspace mail.",
        {
            "from_agent": STRING,
            "to_agent": STRING,
            "subject": STRING,
            "body": STRING,
            "refs": STRINGS,
            "kind": {"type": "string", "enum": list(KINDS)},
            "thread_id": STRING,
        },
        ["from_agent", "to_agent", "subject", "body"],
    ),
    _tool(
        "harness_mail_inbox",
        "Read mail (defaults to planner's unacknowledged inbox).",
        {"agent": STRING, "unacked_only": {"type": "boolean"}},
        [],
    ),
    _tool("harness_mail_ack", "Acknowledge mail as its recipient.", {"id": STRING, "by": STRING}),
    _tool(
        "harness_mail_reply",
        "Reply in the original mail thread.",
        {"id": STRING, "from_agent": STRING, "body": STRING},
    ),
    _tool(
        "harness_plan_start", "Enter or resume Harness Plan Mode.", {"idea": STRING, "name": STRING}
    ),
    _tool("harness_plan_status", "Show all phases and the canonical pending gate.", {}),
    _tool(
        "harness_plan_resume",
        "Inspect or reattach by default. Explicit request_execution asks the negotiated native "
        "client to decide the exact worker proposal. Caller declarations cannot approve it; "
        "actual native approval and shared admission remain required. Capacity is not verified.",
        {
            "expected_revision": STRING,
            "workers": {
                "type": "array",
                "minItems": 1,
                "items": _object(dict.fromkeys(("bead", "provider", "model", "effort"), STRING)),
            },
            "execution_authorized": {"type": "boolean"},
            "request_execution": {"type": "boolean"},
            "call_cap": {"type": "integer"},
            "reserved_worker_calls": {"type": "integer"},
            "deadline": STRING,
            "max_parallel": {"type": "integer"},
            "timeout": {"type": "integer"},
        },
        [],
    ),
    _tool(
        "harness_understanding_card",
        "Record and show understanding; ask the user to confirm before advancing.",
        {
            **dict.fromkeys(
                ("vision", "users", "first_user_moment", "scope_in", "scope_out"), STRING
            ),
            "assumptions": STRINGS,
        },
    ),
    _tool(
        "harness_record_confirmation",
        "Ask the native MCP client to display the exact gate/revision "
        "and collect a fresh response. "
        "Requires negotiated elicitation; quoted tool arguments cannot approve a gate.",
        {"gate": {"type": "string", "enum": ["align", "vision-lock"]}},
    ),
    _tool(
        "harness_advance",
        "Advance one phase; never skip a gate. Only for plan -> roundtable, carry the user's "
        "exact supplied confirmation words in user_statement, with optional by=user or "
        "proxy:<name>. Supplied words default to user; a bare advance remains planner-origin.",
        {"to_phase": STRING, "user_statement": STRING, "by": STRING},
        ["to_phase"],
    ),
    _tool(
        "harness_upsert_node",
        "Save a canonical spine node. Bead fields require ownedFiles, proofModule, "
        "dependsOn and lessons; test fields require module and level (unit or e2e).",
        {
            "node": _object(
                {
                    "id": STRING,
                    "kind": {"type": "string", "enum": list(ALLOWED_PARENTS)},
                    "title": STRING,
                    "text": {"type": "string"},
                    "parents": STRINGS,
                    "fields": {"type": "object"},
                }
            )
        },
    ),
    _tool("harness_remove_node", "Remove a node without children or references.", {"id": STRING}),
    _tool(
        "harness_ask",
        "Register a question requiring resolution.",
        {"question": STRING, "node_ids": STRINGS},
    ),
    _tool(
        "harness_answer", "Record the answer to an open question.", {"id": STRING, "answer": STRING}
    ),
    _tool(
        "harness_add_assumption",
        "Register an explicit assumption.",
        {"text": STRING, "node_ids": STRINGS},
    ),
    _tool(
        "harness_resolve_assumption",
        "Confirm or reject an open assumption.",
        {"id": STRING, "confirmed": {"type": "boolean"}, "rationale": STRING, "by": STRING},
        required=["id", "confirmed", "rationale", "by"],
    ),
    _tool(
        "harness_decision_card",
        "Save a decision and show options, trade-offs and the exact reply choices.",
        {
            "id": STRING,
            "question": STRING,
            "options": {
                "type": "array",
                "minItems": 1,
                "items": _object(dict.fromkeys(("name", "pros", "cons"), STRING)),
            },
            "recommendation": STRING,
            "parents": STRINGS,
            "chosen": STRING,
            "rationale": STRING,
        },
        ["id", "question", "options", "recommendation", "parents"],
    ),
    _tool(
        "harness_diagram",
        "Generate Mermaid and a text fallback from the store.",
        {"kind": {"type": "string", "enum": list(PHASE_DIAGRAMS.values())}},
    ),
    _tool(
        "harness_impact",
        "Show impact since a manifest revision or file; default: publication or last compilation.",
        {"since": STRING},
        [],
    ),
    _tool(
        "harness_render",
        "Write four generated Markdown views inside the workspace; never edit views as authority.",
        {"out_dir": {"type": ["string", "null"]}},
        [],
    ),
    _tool(
        "harness_publish",
        "Publish the current frozen revision and worker manifest.",
        {
            "proof_level": {"type": "string", "enum": ["oracle-verified", "prospective"]},
            "reason": STRING,
        },
        [],
    ),
    _tool(
        "harness_worker_start",
        "Start the existing dispatcher once for the exact current publication; return a durable "
        "job ID promptly. Workers implement the product; the planner steers. Requires permission "
        "through a distinct exact-scope native execution decision. No automatic retry or "
        "subscription-capacity verification; call_cap is a local worker-call ceiling.",
        {
            "expected_revision": STRING,
            "workers": {
                "type": "array",
                "minItems": 1,
                "items": _object(dict.fromkeys(("bead", "provider", "model", "effort"), STRING)),
            },
            "max_parallel": {"type": "integer"},
            "timeout": {"type": "integer"},
            "call_cap": {"type": "integer"},
        },
        ["expected_revision", "workers", "call_cap"],
    ),
    _tool(
        "harness_worker_status",
        "Reattach to recorded worker-job and dispatcher evidence. Never launch or infer live "
        "workers from an old running snapshot. Product checks alone are not worker delivery.",
        {"job_id": STRING},
    ),
]


def resume_status(workspace, data=None):
    """Use the core's read-only snapshot; never reopen a writable store for a banner."""
    if data and isinstance(data.get("canonicalStatus"), dict):
        return data["canonicalStatus"]
    return {
        "name": Path(workspace).name,
        "phase": "unknown",
        "plan_revision": "unknown",
        "open_clarifications": "unknown",
        "pending_gate": "resume-state-unavailable",
    }


def resume_card(data):
    """A compact observation, with complete diagnostics retained in structuredContent."""
    state = {
        "held": "Resume held.",
        "reattached": "Original worker job reattached.",
        "handoff_requested": "Worker handoff requested after native execution approval.",
    }.get(data.get("state"), "Resume state unavailable.")
    reasons = {
        "plan-missing": "No saved plan exists in this workspace.",
        "host-admission-unavailable": "Resume has no independent host admission authority.",
        "native-execution-confirmation-required": "An exact native execution decision is required.",
        "worker-handoff-requested": "Read original job evidence; delivery is unproved.",
        "worker-admission-refused": "Shared worker admission was refused; inspect its originals.",
        "planning-phase-required": "Continue the recorded planning phase before publication proof.",
        "canonical-prerequisite": "A recorded planning or proof prerequisite needs attention.",
        "original-job-reattached": "Read original job evidence; reconnection does not retry.",
    }
    lines = [
        state
        + " "
        + reasons.get(data.get("reasonCode"), "Inspect the recorded state before acting.")
    ]
    if data.get("publishedRevision"):
        lines.append("Current publication: " + data["publishedRevision"])
    if data.get("nextAction", {}).get("kind") == "inspect_plan_phase":
        lines.append("Planning phase: " + str(data["nextAction"]["phase"]) + ".")
    job = data.get("job")
    if job:
        lines.append(
            "Job " + job["jobId"] + ": recorded status " + str(job.get("status", "unknown")) + "."
        )
        if job.get("observedRunStatus"):
            lines.append("Observed dispatch snapshot: " + str(job["observedRunStatus"]) + ".")
    if data.get("observationError"):
        lines.append(
            "The resume observation could not be saved; original job records remain authoritative."
        )
    lines.append(
        "Capacity and current liveness are unknown. "
        "Caller reports do not grant native confirmation or prove delivery."
    )
    if data.get("reasonCode") == "host-admission-unavailable":
        lines.append(
            "Caller declarations cannot authorize a launch. "
            "Authorized chat-to-worker handoff remains unfinished."
        )
    return "\n".join(lines)


def resume_next(data=None, *, cli_prefix=None):
    """Render only supported routes from the shared advisory action contract."""
    data = data or {}
    action = data.get("nextAction", {})
    kind = action.get("kind")

    def route(command, mcp):
        return cli_prefix + command if cli_prefix is not None else mcp

    if kind == "start_plan":
        return route(
            'start --idea "<idea>" --name "<name>"', "harness_plan_start idea=<idea> name=<name>"
        )
    if kind == "inspect_plan_phase":
        return route("status", "harness_plan_status")
    if kind == "inspect_worker_job" and action.get("jobId"):
        return route(
            "worker-status --job-id " + action["jobId"],
            "harness_worker_status job_id=" + action["jobId"],
        )
    if kind == "host_admission_unavailable":
        return (
            route("status", "harness_plan_status")
            + " (inspect only; host admission is unresolved)."
        )
    if kind == "request_native_execution":
        revision = (
            action.get("publishedRevision") or data.get("publishedRevision") or "<publication>"
        )
        if cli_prefix is not None:
            return (
                "Use a native MCP client for execution confirmation; "
                + cli_prefix
                + "status (inspection only; CLI declarations cannot approve)."
            )
        return (
            "harness_plan_resume request_execution=true expected_revision="
            + revision
            + " workers=<exact bead/provider/model/effort tuples> call_cap=<local-call-ceiling>"
            " max_parallel=<parallel-limit> timeout=<per-worker-seconds> "
            "(opens a native decision; does not itself approve)."
        )
    detail = action.get("detailRoute") or {}
    if kind == "review_required" and detail.get("roundId"):
        return (
            "Inspect "
            + route("review " + detail["roundId"], "harness_review id=" + detail["roundId"])
            + "; address the recorded failed review within authorized capacity."
        )
    resolution = (data.get("canonicalStatus") or {}).get("reviewResolution")
    if resolution and kind in {
        "existing_canonical_blocker",
        "reconcile_findings",
        "compile_required",
        "audit_required",
        "review_required",
        "attempt_phase_advance",
        "attempt_publication",
    }:
        return views.review_resolution_next(
            {**resolution, "nextAction": action}, cli_prefix=cli_prefix
        )
    return (
        route("resume", "harness_plan_resume")
        + " (refresh the saved diagnostic; do not infer permission to launch or retry)."
    )


def _validate(value, schema: dict, label="arguments") -> None:
    expected = schema["type"]
    types = {
        "string": isinstance(value, str),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "null": value is None,
    }
    allowed = expected if isinstance(expected, list) else [expected]
    if not any(types[kind] for kind in allowed):
        raise ValueError(f"{label} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{label} must be one of {schema['enum']}")
    if isinstance(value, str) and schema.get("minLength") and not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    if isinstance(value, dict) and "properties" in schema:
        missing = set(schema["required"]) - value.keys()
        extra = value.keys() - schema["properties"].keys()
        if missing or extra:
            raise ValueError(f"{label}: missing {sorted(missing)}, unexpected {sorted(extra)}")
        for key, item in value.items():
            _validate(item, schema["properties"][key], f"{label}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{label} needs at least {schema['minItems']} item(s)")
        for item in value:
            _validate(item, schema["items"], label)


def packet_view(store, bead_id: str, provider: str = "codex") -> dict:
    """Use the canonical builder without compiling, rendering or changing planning state."""
    from codex_harness.core.graph import load_plan
    from codex_harness.core.worker_packet import build_packet

    current = store.manifest_revision()
    manifest = None
    published = store._meta()["published_revision"]
    if published:
        candidate = store.published_manifest(published)
        if current in (candidate["planRevision"], candidate.get("sourceManifestRevision")):
            manifest = candidate
    if manifest is None:
        for row in store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='manifest-compiled' ORDER BY id DESC"
        ):
            candidate = json.loads(row[0])["manifest"]
            if candidate["planRevision"] == current:
                manifest = candidate
                break
    if manifest is None:
        raise ValueError(
            "No current compiled or published manifest. "
            "Next: compile --template template --out manifest.json"
        )
    if bead_id not in manifest["beads"]:
        raise ValueError(f"Unknown bead: {bead_id}")
    with TemporaryDirectory(prefix="planner-packet-") as directory:
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        packet = build_packet(
            load_plan(path),
            {"id": bead_id, **manifest["beads"][bead_id]},
            store.workspace / "template",
            None,
            provider=provider,
            proof_level=manifest.get("proofLevel"),
        )
    return {"packet": packet, "characters": len(packet), "bytes": len(packet.encode("utf-8"))}


def _node_test_blocks(source: str) -> list[tuple[str, str]]:
    """Best-effort node:test literal test()/it() calls; never execute source."""
    # Hide comments and literals while retaining offsets for balanced call extraction.
    tokens = r"//[^\n]*|/\*[\s\S]*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`"
    masked = re.sub(tokens, lambda m: " " * len(m[0]), source)
    calls = r"\b(?:test|it)\s*\(\s*(['\"])((?:\\.|(?!\1)[^\\])*)\1"
    blocks = []
    for match in re.finditer(calls, source):
        if not re.match(r"(?:test|it)\s*\(", masked[match.start() :]):
            continue
        opening = source.index("(", match.start())
        depth, end = 0, len(source)
        for offset in range(opening, len(masked)):
            if masked[offset] == "(":
                depth += 1
            elif masked[offset] == ")":
                depth -= 1
                if depth == 0:
                    end = offset + 1
                    if source[end : end + 1] == ";":
                        end += 1
                    break
        name = re.sub(r"\\(['\"\\])", r"\1", match[2])
        blocks.append((name, source[match.start() : end]))
    return blocks


def show_node(store, node_id: str, *, body=False, test=None) -> dict:
    """Keep Python inspection and add best-effort static command-runner inspection."""
    from codex_harness.core.spine import compile_manifest, requirement_file_sources

    spine = store.spine()
    verification = spine.node(spine.ids("vision")[0])["fields"].get("verification")
    if verification is None or verification["runner"] != "command":
        return store.show_node(node_id, body=body, test=test)
    if body and not test:
        raise ValueError("--body requires --test <name>")
    node = spine.node(node_id)
    result = {**node, "children": spine.children(node_id), "testFiles": []}
    if node["kind"] not in ("requirement", "test"):
        return result
    modules = {
        spine.node(key)["fields"]["module"]
        for key in {node_id, *spine.descendants(node_id)}
        if spine.node(key)["kind"] == "test"
    }
    template = store.workspace / "template"
    manifest = compile_manifest(spine, template)
    sources = requirement_file_sources(template, manifest["requirementFiles"])
    remaining, found = 8192, False
    for name in sorted(modules & sources.keys()):
        blocks = _node_test_blocks(sources[name].read_text(encoding="utf-8-sig"))
        entry = {
            "path": name,
            "functions": [key for key, _ in blocks],
            "discovery": "best effort: node:test test()/it() literal call names",
        }
        if body:
            selected = [text for key, text in blocks if key == test]
            if len(selected) > 1:
                raise ValueError(f"Ambiguous test {test} in {name}")
            if selected:
                found = True
                encoded = selected[0].encode("utf-8")
                clipped = encoded[:remaining].decode("utf-8", errors="ignore")
                remaining -= len(clipped.encode("utf-8"))
                entry.update(
                    test=test, body=clipped, truncated=len(encoded) > len(clipped.encode("utf-8"))
                )
        result["testFiles"].append(entry)
    if body and not found:
        raise ValueError(f"Test not found in linked protected files: {test}")
    return result


class PlannerServer:
    def __init__(self, workspace: str | Path, *, confirmation_timeout=120):
        self.workspace = Path(workspace).resolve()
        self.store = None
        self._elicitation = False
        self._initialized = False
        self._pending_confirmation = None
        self._confirmation_timeout = confirmation_timeout

    def close(self) -> None:
        if self._pending_confirmation is not None:
            self._cancel_confirmation("Client disconnected; no approval recorded")
        self._initialized = False
        self._elicitation = False
        if self.store is not None:
            self.store.close()
            self.store = None

    def _store(self):
        if self.store is None:
            if not (self.workspace / ".codex-harness" / "plan.sqlite3").is_file():
                raise ValueError("Call harness_plan_start first")
            self.store = open_plan_store(self.workspace)
        return self.store

    def _status(self) -> dict:
        if self.store is None and (self.workspace / ".codex-harness" / "plan.sqlite3").is_file():
            self._store()
        if self.store is not None:
            status = self.store.status()
            status["reviewResolution"] = views.review_resolution_state(self.store, status)
            return status
        return {
            "name": self.workspace.name,
            "phase": "align",
            "plan_revision": hashlib.sha256(b"").hexdigest(),
            "open_clarifications": 0,
            "pending_gate": "harness_plan_start",
        }

    def _diagram(self, kind: str) -> str:
        spine = self._store().spine()
        self.result_data = {"kind": kind, "mermaid": diagram(spine, kind)}
        return views.chat_diagram(spine, kind)

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.result_data = None
        operation_error_code = None
        try:
            tool = next((tool for tool in TOOLS if tool["name"] == name), None)
            if tool is None:
                raise ValueError(f"Unknown tool: {name}")
            if name == "harness_accept_run" and arguments.get("subject") not in (
                "product",
                "oracle",
            ):
                raise ValueError(
                    "Specify subject. Next: "
                    "harness_accept_run repo=<integrated-repo> subject=product "
                    "OR harness_accept_run repo=<reference-repo> subject=oracle"
                )
            _validate(arguments, tool["inputSchema"])
            body = self._dispatch(name, arguments)
            is_error = bool(
                name == "harness_readiness"
                and self.result_data
                and self.result_data["invalid"]
                or name == "harness_accept_run"
                and self.result_data
                and not self.result_data["passed"]
            )
        except Exception as error:
            operation_error_code = getattr(error, "code", None)
            # Tool failures are isolated at the protocol boundary; no input/secret logs.
            print(f"planner tool failed: {type(error).__name__}", file=sys.stderr)
            body = f"{getattr(error, 'code', 'tool-input-or-operation-error')}: {error}"
            if name == "harness_worker_start" and self.result_data:
                body += (
                    "\nAn original worker job exists; inspect worker status, never infer a retry."
                )
            if name in {"harness_intake", "harness_reviewer_catalog", "harness_reviewer_choice"}:
                guide = schema_help(TOOLS, tool=name)
                body += "\nOwning typed contract: " + json.dumps(guide["owningContract"])
                body += (
                    "\nInspect harness_schema tool=" + name + "; keep unsupported authority held."
                )
            if name == "harness_upsert_node" and isinstance(error, ValueError | KeyError):
                body += "\n" + node_error_help(arguments)
            if name == "harness_accept_review" and self.result_data:
                body = views.review_card(self.result_data) + "\n" + body
            is_error = True
        try:
            status = (
                resume_status(self.workspace, self.result_data)
                if name == "harness_plan_resume"
                else diagnostic_status(self.workspace)
                if name == "harness_preflight"
                else self.result_data
                if name == "harness_plan_status" and not is_error
                else self._status()
            )
        except Exception as error:
            print(f"planner status unavailable: {type(error).__name__}", file=sys.stderr)
            status = {
                "name": self.workspace.name,
                "plan_revision": "unknown",
                "phase": "unknown",
                "open_clarifications": "unknown",
                "pending_gate": "status-unavailable",
            }
            body += "\nCanonical status unavailable; retry harness_plan_status."
            is_error = True
        if is_error:
            body += f"\npending_gate: {status['pending_gate'] or 'none'}"
        acceptance = status.get("acceptance", {})
        if name == "harness_plan_status" and acceptance.get("invalidMessage"):
            body += "\nAcceptance invalid: " + acceptance["invalidMessage"]
        next_step = self._next(name, arguments, status, is_error, operation_error_code)
        if name == "harness_accept_run" and arguments.get("subject") not in ("product", "oracle"):
            next_step = (
                "harness_accept_run repo=<integrated-repo> subject=product OR "
                "harness_accept_run repo=<reference-repo> subject=oracle"
            )
        handoff = None
        if (
            is_error
            and name in {"harness_advance", "harness_publish"}
            and operation_error_code
            in {
                "plan-findings-open",
                "plan-publication-evidence-required",
            }
        ):
            handoff = views.review_author_handoff(self._store(), status)
            body += "\n" + views.author_handoff_card(handoff)
        return {
            "isError": is_error,
            **(
                {"structuredContent": {"authorHandoff": handoff}}
                if handoff is not None
                else {"structuredContent": self.result_data}
                if self.result_data is not None
                and (
                    not is_error
                    or name
                    in (
                        "harness_readiness",
                        "harness_accept_run",
                        "harness_accept_review",
                        "harness_worker_start",
                        "harness_plan_resume",
                    )
                )
                else {}
            ),
            "content": [
                {
                    "type": "text",
                    "text": views.response(
                        status, body, next_step, tracker=name == "harness_plan_status"
                    ),
                }
            ],
        }

    def _next(self, name: str, args: dict, status: dict, error: bool, error_code=None) -> str:
        if name == "harness_plan_resume":
            return resume_next(self.result_data)
        if name == "harness_schema":
            return "Use the documented input with actual plan IDs; follow harness_plan_status."
        if (
            name in {"harness_worker_start", "harness_worker_status"}
            and self.result_data
            and self.result_data.get("jobId")
        ):
            return "harness_worker_status job_id=" + self.result_data["jobId"]
        if name == "harness_publish" and not error:
            return (
                "harness_worker_start expected_revision="
                + self.result_data["id"]
                + " workers=<explicit bead/provider/model/effort tuples>; "
                "workers implement; the planner steers."
            )
        if name == "harness_preflight":
            return (
                "Correct the diagnostic input and retry harness_preflight."
                if error
                else "Review missing/unknown observations; "
                "existing planning and proof gates still apply."
            )
        if error and name in {
            "harness_intake",
            "harness_reviewer_catalog",
            "harness_reviewer_choice",
        }:
            return "harness_schema tool=" + name
        if name == "harness_waves" and self.result_data and self.result_data.get("intake") is None:
            return "harness_schema tool=harness_intake; keep the missing intake draft unconfirmed."
        if name in (
            "harness_intake",
            "harness_reviewer_catalog",
            "harness_reviewer_choice",
            "harness_wave_map",
            "harness_waves",
        ):
            return (
                "Refresh harness_plan_status and reconcile the refused draft input."
                if error
                else "harness_waves; resolve draft/unmet assurance through existing planning gates."
            )
        acceptance = status.get("acceptance", {})
        if (
            name == "harness_accept_rebind"
            or name == "harness_plan_status"
            and status["phase"] == "accept"
        ):
            return acceptance.get("next", "harness_plan_status")
        if (
            error
            and status.get("reviewResolution")
            and error_code in {"plan-findings-open", "plan-publication-evidence-required"}
        ):
            return views.review_resolution_next(status["reviewResolution"])
        if error:
            return (
                "Correct the reported blocker, then retry this tool; "
                "harness_plan_status shows the current gate."
            )
        if name.startswith("harness_accept"):
            return self._accept_next(name).removeprefix("\nNext: ")
        if name == "harness_understanding_card":
            return (
                "Your call: confirm this understanding in your own words, "
                "or tell me what to change."
            )
        if name == "harness_decision_card" and not args.get("chosen"):
            return "Your call: choose an option, or explain the change you want."
        if name == "harness_ask":
            return "Your call: answer the question above so the planner can record your decision."
        if name == "harness_add_assumption":
            return (
                "Confirm or reject this assumption with harness_resolve_assumption, "
                "including the reason and speaker."
            )
        if name in ("harness_mail_send", "harness_mail_reply", "harness_mail_ack"):
            return "Read your inbox with harness_mail_inbox."
        if name == "harness_plan_start" and status["phase"] == "align":
            return (
                "Restate the vision with harness_understanding_card; "
                "your call: confirm the understanding or explain what to change."
            )
        if name == "harness_record_confirmation" and status["phase"] == args["gate"]:
            phase = status["phase"]
            return "harness_advance to_phase=" + PHASES[PHASES.index(phase) + 1]
        gate = status["pending_gate"]
        if gate == "harness_plan_start":
            return "harness_plan_start idea=<your idea> name=<plan name>"
        if gate and gate.startswith("approval:"):
            return (
                "harness_record_confirmation gate="
                + gate.split(":", 1)[1]
                + "; review the displayed understanding and answer the native client form."
            )
        if status.get("open_clarifications"):
            return "Answer the open questions with harness_answer before advancing."
        if status["phase"] in {"roundtable", "publish"}:
            resolution = status.get("reviewResolution")
            if resolution and resolution["nextAction"]["kind"] != "not_applicable":
                return views.review_resolution_next(resolution)
        if status.get("acceptance", {}).get("accepted"):
            return (
                "Review the recorded acceptance above; harness_plan_status shows current evidence."
            )
        return views.NEXT.get(status["phase"], "Retry harness_plan_status.")

    @staticmethod
    def _accept_next(name: str) -> str:
        return (
            "\nNext: "
            + {
                "harness_accept_scenarios": (
                    "harness_accept_run repo=<integrated-repo> subject=product"
                ),
                "harness_accept_run": "harness_accept_review repo=<integrated-repo>",
                "harness_accept_review": (
                    "harness_accept statement=<actual-words> by=<user-or-proxy:name>"
                ),
                "harness_accept": "harness_plan_status",
                "harness_accept_rebind": "harness_plan_status",
            }[name]
        )

    def _dispatch(self, name: str, args: dict) -> str:
        if name == "harness_plan_resume":
            from codex_harness.core.planner_resume import resume_plan

            self.result_data = resume_plan(self.workspace, **args)
            return "Durable plan resume\n" + resume_card(self.result_data)
        if name == "harness_schema":
            self.result_data = schema_help(TOOLS, **args)
            return json.dumps(self.result_data, ensure_ascii=False, indent=2)
        if name == "harness_preflight":
            self.result_data = preflight_report(self.workspace, **args)
            return render_preflight(self.result_data)
        draft_methods = {
            "harness_intake": "record_intake",
            "harness_reviewer_catalog": "record_reviewer_catalog",
            "harness_reviewer_choice": "record_reviewer_selection",
            "harness_wave_map": "record_wave_map",
            "harness_waves": "outcome_waves",
        }
        if name in draft_methods:
            store = self._store()
            self.result_data = getattr(store, draft_methods[name])(**args)
            body = views.outcome_waves_card(store.outcome_waves())
            if name != "harness_waves":
                body = views.data_card("Draft recorded", self.result_data) + "\n" + body
            return body
        if name == "harness_review":
            store = self._store()
            self.result_data = store.review(args["id"])
            self.result_data["findingMembers"] = views.review_finding_members(
                store, self.result_data
            )
            return (
                views.review_card(self.result_data, details=True)
                + "\n"
                + json.dumps(self.result_data["findingMembers"], ensure_ascii=False, indent=2)
            )
        if name == "harness_resolve_findings":
            count = self._store().resolve_findings(args["findings"])
            self.result_data = {"dispositions": count}
            return f"Dispositioned {count} finding clusters atomically"
        if name == "harness_resolve_finding":
            store = self._store()
            status = args.get("status", "resolved")
            store.resolve_findings(
                [dict(id=args["id"], resolution=args["resolution"], status=status)]
            )
            return f"Finding {args['id']}: {status}"
        if name.startswith("harness_accept"):
            from codex_harness.core.acceptance import (
                acceptance_review,
                derive_scenarios,
                run_acceptance,
            )

            store = self._store()
            if name == "harness_accept_scenarios":
                if "scenarios" in args:
                    store.save_acceptance_scenarios(args["scenarios"])
                elif args.get("derive") or not store.acceptance_scenarios():
                    store.save_acceptance_scenarios(
                        derive_scenarios(store.locked_acceptance_spine())
                    )
                result = {"scenarios": store.acceptance_scenarios()}
            elif name == "harness_accept_run":
                result = run_acceptance(store, args["repo"], subject=args.get("subject"))
            elif name == "harness_accept_review":
                prior_failure = store.latest_review_failure()
                try:
                    result = acceptance_review(store, **args)
                except ValueError:
                    failure = store.latest_review_failure()
                    if failure != prior_failure:
                        self.result_data = failure
                    raise
            elif name == "harness_accept_rebind":
                result = store.accept_rebind(args["repo"])
            else:
                result = store.accept(**args)
                self.result_data = result
                return views.data_card(
                    "Acceptance recorded",
                    {
                        "Approval": views.approval_label(result["by"], is_proxy=result["isProxy"]),
                        "Statement": result.get("statement"),
                        "Run": result.get("runId"),
                    },
                )
            self.result_data = result
            if name == "harness_accept_run":
                return views.acceptance_run_card(result)
            if name == "harness_accept_review":
                return views.review_card(result)
            body = views.data_card(name.removeprefix("harness_").replace("_", " ").title(), result)
            if name == "harness_accept_scenarios" and any(
                not item.get("executable") or item.get("status") == "stale"
                for item in result["scenarios"]
            ):
                body += (
                    "\nDraft/stale scenarios: supply steps and expectations, "
                    "then save with accept-scenarios --input."
                )
            return body
        if name.startswith("harness_mail_"):
            mailbox = Mailbox(self.workspace)
            if name == "harness_mail_send":
                result = {"id": mailbox.send(**args)}
            elif name == "harness_mail_inbox":
                result = {
                    "messages": mailbox.inbox(
                        args.get("agent", "planner"), unacked_only=args.get("unacked_only", True)
                    )
                }
            elif name == "harness_mail_ack":
                mailbox.ack(**args)
                result = {"id": args["id"], "acked_by": args["by"]}
            else:
                result = {"id": mailbox.reply(**args)}
            self.result_data = result
            return views.data_card("Workspace mail", result)
        if name == "harness_plan_start":
            if self.store is None:
                self.store = open_plan_store(self.workspace, args["name"])
            # Empty stores cannot make a Spine; the empty revision is canonical.
            if self.store.status()["plan_revision"] == hashlib.sha256(b"").hexdigest():
                self.store.upsert_node(
                    {
                        "id": "V1",
                        "kind": "vision",
                        "title": args["name"],
                        "text": args["idea"],
                        "fields": {},
                        "parents": [],
                    }
                )
            self.result_data = {"id": "V1"}
            return "Plan mode resumed. To pause, say 'pause plan'."
        store = self._store()
        if name == "harness_readiness":
            from codex_harness.core.readiness import readiness_review, render_readiness

            self.result_data = readiness_review(store, **args)
            return render_readiness(self.result_data)
        if name == "harness_packet_view":
            self.result_data = packet_view(store, args["id"], args.get("provider", "codex"))
            return self.result_data["packet"] + (
                f"\nPacket size: {self.result_data['characters']} characters; "
                f"{self.result_data['bytes']} bytes"
            )
        if name == "harness_nodes":
            self.result_data = {"nodes": store.list_nodes(args.get("kind"))}
            return (
                "\n".join(
                    f"{node['id']} · {node['kind']} · {node['title']}"
                    for node in self.result_data["nodes"]
                )
                or "No matching nodes."
            )
        if name == "harness_show_node":
            node = show_node(store, args["id"], body=args.get("body", False), test=args.get("test"))
            self.result_data = node
            lines = [
                views.data_card(
                    f"{node['id']} · {node['kind']} · {node['title']}",
                    {
                        "Detail": node["text"],
                        "Fields": node["fields"],
                        "Parents": node["parents"],
                        "Children": node["children"],
                    },
                )
            ]
            for file in node["testFiles"]:
                lines.append(f"Protected test: {file['path']}")
                lines.append("Functions: " + ", ".join(file["functions"]))
                if "discovery" in file:
                    lines.append(file["discovery"])
                if "body" in file:
                    lines.append(f"Test: {file['test']}\n{file['body']}")
                    if file["truncated"]:
                        lines.append("[truncated: 8 KB test body budget]")
            return "\n".join(lines)
        if name == "harness_plan_status":
            status = self._status()
            self.result_data = status
            questions = [
                dict(row)
                for row in store.connection.execute(
                    "SELECT id, question FROM clarifications WHERE status='open' ORDER BY id"
                )
            ]
            body = views.status_card(status, questions)
            body += (
                f"\nOpen findings: {status['open_findings']} clusters "
                f"({status['open_finding_members']} members)"
            )
            if status.get("proofLevelLabel"):
                body += "\nProof level: " + status["proofLevelLabel"]
            body += "\nWorker readiness (advisory): " + status["readiness"]["status"]
            approval = status.get("approval")
            if approval:
                granted = approval["granted"]
                body += (
                    f"\nCurrent {approval['gate']} approval: "
                    + ("granted" if granted else "not granted")
                    + "; origin: "
                    + approval["origin"]
                )
                if approval["origin"] == "mcp-client-elicitation":
                    body += (
                        " (native MCP client-reported response; "
                        "natural-person identity is not authenticated)"
                    )
                if approval["gate"] == "vision-lock":
                    body += "\nVision lock: " + (
                        "locked vision (current recorded native credit)"
                        if granted
                        else "not currently granted; fresh native response required"
                    )
            row = store.connection.execute(
                'SELECT "by", statement, plan_revision FROM approvals '
                "WHERE gate=? ORDER BY rowid DESC LIMIT 1",
                ("vision-lock",),
            ).fetchone()
            if row:
                body += (
                    "\nHistorical vision-lock record (not current approval credit): recorded by "
                    + views.readable(row["by"])
                    + " for "
                    + row["plan_revision"]
                    + "\nHistorical statement: "
                    + views.readable(row["statement"])
                )
            body += "\n" + views.outcome_waves_card(store.outcome_waves())
            return body
        if name == "harness_understanding_card":
            node = store.spine().node("V1")
            existing = node["fields"].get("assumptions", [])
            node["fields"].update(args)
            store.upsert_node(node)
            for text in args["assumptions"]:
                if text not in existing:
                    store.add_assumption(text, ["V1"])
            self.result_data = {"id": "V1"}
            return views.understanding_card(**args)
        if name == "harness_record_confirmation":
            raise ValueError(
                "plan-confirmation-origin-unavailable: use harness_record_confirmation gate="
                + args["gate"]
                + " through a native MCP client with negotiated elicitation. "
                "Quoted CLI/tool words do not establish a fresh client response."
            )
        if name == "harness_advance":
            by, statement = "planner", None
            if "user_statement" in args or "by" in args:
                if store.phase() != "plan" or args["to_phase"] != "roundtable":
                    raise ValueError("Confirmation fields require plan -> roundtable")
                statement = args.get("user_statement")
                if not isinstance(statement, str) or not statement.strip():
                    raise ValueError("Confirmation requires nonblank user_statement")
                by = args.get("by", "user")
                if by != "user" and not (by.startswith("proxy:") and by[6:].strip()):
                    raise ValueError("Confirmation by must be user or proxy:<name>")
            store.advance(args["to_phase"], by=by, statement=statement)
            body = f"Entered {args['to_phase']} planning phase."
            kind = PHASE_DIAGRAMS.get(args["to_phase"])
            if kind:
                body += "\n" + self._diagram(kind)
            return body
        if name == "harness_upsert_node":
            store.upsert_node(args["node"])
            self.result_data = {"id": args["node"]["id"]}
            return f"Changed node: {args['node']['id']} (upserted)."
        if name == "harness_remove_node":
            store.remove_node(args["id"])
            return f"Changed node: {args['id']} (removed)."
        if name == "harness_ask":
            item_id = store.ask(args["question"], args["node_ids"])
            self.result_data = {"id": item_id}
            return f"Question id: {item_id}\n{args['question']}"
        if name == "harness_answer":
            store.answer(args["id"], args["answer"])
            return f"Answered question: {args['id']}"
        if name == "harness_add_assumption":
            item_id = store.add_assumption(args["text"], args["node_ids"])
            self.result_data = {"id": item_id}
            return f"Assumption id: {item_id}\n{args['text']}"
        if name == "harness_resolve_assumption":
            resolve = store.confirm_assumption if args["confirmed"] else store.reject_assumption
            resolve(args["id"], rationale=args["rationale"], by=args["by"])
            return (
                f"Assumption {args['id']}: {'confirmed' if args['confirmed'] else 'rejected'}"
                f"\nBy: {args['by']}\nRationale: {args['rationale']}"
            )
        if name == "harness_decision_card":
            card = views.decision_card(**args)
            store.upsert_node(
                {
                    "id": args["id"],
                    "kind": "decision",
                    "title": args["question"],
                    "text": card,
                    "fields": {
                        key: args[key]
                        for key in ("options", "recommendation", "chosen", "rationale")
                        if key in args
                    },
                    "parents": args["parents"],
                }
            )
            self.result_data = {"id": args["id"]}
            return card
        if name == "harness_diagram":
            return self._diagram(args["kind"])
        if name == "harness_impact":
            self.result_data = store.impact_since(args.get("since"))
            return views.change_digest(self.result_data)
        if name == "harness_render":
            target = args.get("out_dir")
            out_dir = Path(target) if target else self.workspace / "plan" / "docs"
            if not out_dir.is_absolute():
                out_dir = self.workspace / out_dir
            out_dir = out_dir.resolve()
            if not out_dir.is_relative_to(self.workspace):
                raise ValueError("out_dir must stay inside the workspace")
            rendered = store.rendered_docs()
            out_dir.mkdir(parents=True, exist_ok=True)
            paths = []
            for filename, text in sorted(rendered.items()):
                path = out_dir / filename
                if not path.resolve().is_relative_to(self.workspace):
                    raise ValueError("View path must stay inside the workspace")
                path.write_text(text, encoding="utf-8", newline="\n")
                paths.append(str(path))
            self.result_data = {"id": str(out_dir), "paths": paths}
            return "Generated views (not authority):\n" + "\n".join(paths)
        if name == "harness_publish":
            if not (self.workspace / "plan").resolve().is_relative_to(self.workspace):
                raise ValueError("Publication path must stay inside the workspace")
            revision = store.publish(
                by="planner",
                export_dir=self.workspace / "plan",
                proof_level=args.get("proof_level", "oracle-verified"),
                reason=args.get("reason"),
            )
            spine = store.spine()
            summary = views.publish_summary(
                [spine.node(key) for key in spine.ids("bead")], store.status()["open_assumptions"]
            )
            self.result_data = {"id": revision}
            body = (
                f"Published revision: {revision}\n{self._diagram('trace')}\n{summary}"
                + "\nWorker readiness (advisory): "
                + store.readiness_status()["status"]
            )
            self.result_data = {"id": revision}
            return body
        if name in {"harness_worker_start", "harness_worker_status"}:
            from codex_harness.core import planner_jobs

            try:
                self.result_data = (
                    planner_jobs.start_worker_job(store, **args)
                    if name == "harness_worker_start"
                    else planner_jobs.worker_job_status(self.workspace, **args)
                )
            except Exception:
                if name == "harness_worker_start":
                    try:
                        self.result_data = planner_jobs.worker_job_status(
                            self.workspace, args["expected_revision"]
                        )
                    except (OSError, ValueError):
                        pass
                raise
            return (
                f"Worker job {self.result_data['jobId']}: {self.result_data['status']}\n"
                + "Workers implement; the planner steers. Read original dispatch evidence before "
                "claiming delivery. Current liveness: " + self.result_data["liveness"]
            )
        raise ValueError(f"Unknown tool: {name}")

    def _resume_reply(self, snapshot, *, error=False, reason=None):
        body = resume_card(snapshot)
        if reason:
            body += "\n" + reason
        return {
            "isError": error,
            "structuredContent": snapshot,
            "content": [
                {
                    "type": "text",
                    "text": views.response(
                        resume_status(self.workspace, snapshot),
                        body,
                        resume_next(snapshot),
                    ),
                }
            ],
        }

    def _begin_execution(self, request_id, name, arguments):
        from codex_harness.core.plan_store import open_plan_store_readonly
        from codex_harness.core.planner_execution import execution_scope, prepare_execution
        from codex_harness.core.planner_resume import resume_plan

        tool = next(tool for tool in TOOLS if tool["name"] == name)
        _validate(arguments, tool["inputSchema"])
        # Inspect before any canonical writable opening or native request event.
        snapshot = resume_plan(self.workspace, expected_revision=arguments.get("expected_revision"))
        if snapshot["state"] == "reattached":
            return {"jsonrpc": "2.0", "id": request_id, "result": self.call_tool(name, arguments)}
        if snapshot["reasonCode"] != "native-execution-confirmation-required":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": self._resume_reply(snapshot, error=name == "harness_worker_start"),
            }
        if not self._elicitation or not self._initialized:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": self._resume_reply(
                    snapshot,
                    error=True,
                    reason="worker-execution-native-origin-required: use MCP2025-06-18 with "
                    "elicitation and initialized notification. CLI quotes cannot approve.",
                ),
            }
        if self._pending_confirmation is not None:
            raise ValueError("worker-execution-pending: answer or cancel the displayed request")
        if any(key not in arguments for key in ("expected_revision", "workers", "call_cap")):
            raise ValueError("worker-execution-explicit-publication-workers-call-cap-required")
        proposal = {key: arguments[key] for key in ("expected_revision", "workers", "call_cap")}
        proposal.update(
            max_parallel=arguments.get("max_parallel", 2), timeout=arguments.get("timeout", 900)
        )
        with open_plan_store_readonly(self.workspace) as readonly:
            execution_scope(readonly, **proposal)
        prepared = prepare_execution(self._store(), **proposal)
        wire_id = "seldon-execution-" + uuid4().hex
        self._pending_confirmation = {
            "purpose": "execution",
            "wire_id": wire_id,
            "tool_id": request_id,
            "tool_name": name,
            "arguments": dict(arguments),
            "request": prepared,
            "deadline": time.monotonic() + self._confirmation_timeout,
        }
        return {
            "jsonrpc": "2.0",
            "id": wire_id,
            "method": "elicitation/create",
            "params": {
                "message": prepared["card"],
                "requestedSchema": _object(
                    {
                        "approve": {
                            "type": "boolean",
                            "title": "Approve only this exact worker execution and call ceiling",
                        },
                        "statement": {"type": "string", "title": "Your complete response"},
                        "conditions": {
                            "type": "string",
                            "title": "Conditions or changes (empty for unconditional approval)",
                        },
                    }
                ),
            },
        }

    def _execution_outcome_reply(self, pending, outcome, *, reason=None):
        from codex_harness.core.planner_resume import resume_plan

        snapshot = resume_plan(
            self.workspace, expected_revision=pending["arguments"].get("expected_revision")
        )
        snapshot = {**snapshot, "executionConfirmation": outcome}
        return self._resume_reply(
            snapshot,
            error=True,
            reason=(
                "Execution decision "
                + outcome["state"]
                + ". No admission was requested by this decision."
                + (" " + reason if reason else "")
            ),
        )

    def _finish_execution(self, response, pending):
        from codex_harness.core.planner_execution import complete_execution

        self._pending_confirmation = None
        result = response.get("result")
        try:
            if (
                "error" in response
                or not isinstance(result, dict)
                or set(result) - {"action", "content"}
            ):
                raise ValueError("worker-execution-response-invalid")
            outcome = complete_execution(
                self._store(),
                pending["request"]["request_id"],
                result.get("action"),
                result.get("content"),
            )
        except (ValueError, KeyError) as error:
            try:
                outcome = complete_execution(
                    self._store(), pending["request"]["request_id"], "cancel"
                )
            except (ValueError, KeyError):
                outcome = {"state": "unproved", "request_id": pending["request"]["request_id"]}
            reply = self._execution_outcome_reply(pending, outcome, reason=str(error))
        else:
            if outcome["state"] == "approved":
                reply = self.call_tool(pending["tool_name"], pending["arguments"])
                reply = {**reply, "_meta": {"executionConfirmation": outcome}}
            else:
                reply = self._execution_outcome_reply(pending, outcome)
        return {"jsonrpc": "2.0", "id": pending["tool_id"], "result": reply}

    def _confirmation_reply(self, data, *, error=False, reason=None):
        try:
            status = self._status()
        except Exception:
            status = {
                "name": self.workspace.name,
                "phase": "unknown",
                "plan_revision": "unknown",
                "open_clarifications": "unknown",
                "pending_gate": "status-unavailable",
            }
            error = True
            reason = (
                reason + "\n" if reason else ""
            ) + "Canonical status unavailable; read harness_plan_status."
        body = (
            f"Confirmation {data['state']} for {data['gate']} / {data['plan_revision']}.\n"
            "Origin: native MCP client response; natural-person identity is not authenticated."
        )
        if data.get("content") is not None:
            body += "\nComplete submitted response:\n" + json.dumps(
                data["content"], ensure_ascii=False
            )
        if reason:
            body += "\n" + reason
        next_step = (
            "harness_advance to_phase=" + PHASES[PHASES.index(status["phase"]) + 1]
            if data["state"] == "approved" and data["gate"] == status["phase"]
            else "harness_plan_status; follow the current recorded gate."
            if data["state"] == "approved"
            else "Review the current understanding; retry native confirmation when ready."
        )
        return {
            "isError": error,
            "structuredContent": data,
            "content": [{"type": "text", "text": views.response(status, body, next_step)}],
        }

    def _begin_confirmation(self, request_id, arguments):
        from codex_harness.core.planner_confirmations import prepare_confirmation

        tool = next(tool for tool in TOOLS if tool["name"] == "harness_record_confirmation")
        _validate(arguments, tool["inputSchema"])
        if not self._elicitation or not self._initialized:
            raise ValueError(
                "plan-confirmation-origin-unavailable: initialize MCP2025-06-18 with "
                "capabilities.elicitation={}, then notifications/initialized; "
                "a native user response is required. CLI quotes cannot replace it."
            )
        if self._pending_confirmation is not None:
            raise ValueError("plan-confirmation-pending: answer or cancel the displayed request")
        prepared = prepare_confirmation(self._store(), arguments["gate"])
        wire_id = "seldon-confirmation-" + uuid4().hex
        self._pending_confirmation = {
            "wire_id": wire_id,
            "tool_id": request_id,
            "request": prepared,
            "deadline": time.monotonic() + self._confirmation_timeout,
        }
        return {
            "jsonrpc": "2.0",
            "id": wire_id,
            "method": "elicitation/create",
            "params": {
                "message": prepared["card"],
                "requestedSchema": _object(
                    {
                        "approve": {
                            "type": "boolean",
                            "title": "Unconditionally approve this displayed gate and revision",
                        },
                        "statement": {"type": "string", "title": "Your complete response"},
                        "conditions": {
                            "type": "string",
                            "title": "Conditions or changes (empty for unconditional approval)",
                        },
                    }
                ),
            },
        }

    def _finish_confirmation(self, response):
        from codex_harness.core.planner_confirmations import complete_confirmation

        pending = self._pending_confirmation
        if pending is None or response.get("id") != pending["wire_id"]:
            return None  # Never respond to an unknown/duplicate JSON-RPC response.
        if time.monotonic() >= pending["deadline"]:
            return self._cancel_confirmation("Confirmation timed out; no approval recorded")
        if pending.get("purpose") == "execution":
            return self._finish_execution(response, pending)
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
            reply = self._confirmation_reply(outcome, error=outcome["state"] != "approved")
        except (ValueError, KeyError) as error:
            outcome = complete_confirmation(
                self._store(), pending["request"]["request_id"], "cancel"
            )
            reply = self._confirmation_reply(outcome, error=True, reason=str(error))
        return {"jsonrpc": "2.0", "id": pending["tool_id"], "result": reply}

    def _cancel_confirmation(self, reason):
        from codex_harness.core.planner_confirmations import complete_confirmation

        pending, self._pending_confirmation = self._pending_confirmation, None
        if pending.get("purpose") == "execution":
            from codex_harness.core.planner_execution import complete_execution

            outcome = complete_execution(self._store(), pending["request"]["request_id"], "cancel")
            return {
                "jsonrpc": "2.0",
                "id": pending["tool_id"],
                "result": self._execution_outcome_reply(pending, outcome, reason=reason),
            }
        outcome = complete_confirmation(self._store(), pending["request"]["request_id"], "cancel")
        return {
            "jsonrpc": "2.0",
            "id": pending["tool_id"],
            "result": self._confirmation_reply(outcome, error=True, reason=reason),
        }

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
        return [
            cancelled,
            self._cancel_confirmation("Confirmation timed out; no approval recorded"),
        ]

    def handle_line(self, line: str) -> dict | None:
        try:
            request = json.loads(line, parse_constant=_invalid_constant)
        except (ValueError, RecursionError):
            return _error(None, -32700, "Parse error")
        if not isinstance(request, dict):
            return _error(None, -32600, "Invalid Request")
        if request.get("jsonrpc") == "2.0" and "method" not in request and "id" in request:
            return self._finish_confirmation(request)
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
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments", {})
            if name == "harness_worker_start" or (
                name == "harness_plan_resume"
                and isinstance(arguments, dict)
                and arguments.get("request_execution") is True
            ):
                try:
                    return self._begin_execution(request_id, name, arguments)
                except (ValueError, KeyError, TypeError) as error:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": self._resume_reply(
                            {
                                "state": "held",
                                "reasonCode": "worker-admission-refused",
                                "canonicalStatus": None,
                                "nextAction": {"kind": "request_native_execution"},
                                "job": None,
                            },
                            error=True,
                            reason=str(error),
                        ),
                    }
            if params.get("name") == "harness_record_confirmation":
                try:
                    return self._begin_confirmation(request_id, params.get("arguments", {}))
                except (ValueError, KeyError) as error:
                    status = self._status()
                    code = getattr(error, "code", "tool-input-or-operation-error")
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "isError": True,
                            "content": [
                                {
                                    "type": "text",
                                    "text": views.response(
                                        status,
                                        f"{code}: {error}",
                                        "Review the current gate; use a native client "
                                        "with negotiated elicitation.",
                                    ),
                                }
                            ],
                        },
                    }
            result = self.call_tool(params.get("name"), params.get("arguments", {}))
        else:
            return _error(request_id, -32601, "Method not found")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _invalid_constant(value: str):
    raise ValueError(f"Invalid JSON constant: {value}")


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    server = PlannerServer(args.workspace)
    try:
        # Only stdin reading moves off-thread; all store/protocol mutation stays
        # on this thread. A queue timeout bounds one pending user interaction.
        incoming = queue.Queue()

        def read_lines():
            try:
                for line in sys.stdin:
                    incoming.put(line)
            finally:
                incoming.put(None)

        threading.Thread(target=read_lines, daemon=True).start()
        while True:
            for response in server.expire_confirmation():
                print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)
            pending = server._pending_confirmation
            wait = max(0.01, pending["deadline"] - time.monotonic()) if pending else None
            try:
                line = incoming.get(timeout=wait)
            except queue.Empty:
                continue
            if line is None:
                break
            response = server.handle_line(line)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)
    finally:
        server.close()


if __name__ == "__main__":
    main()
