"""Reproduce the planner dogfood using real PlanStore gates, never worker dispatch.

Run from the Harness root with uv run --frozen --offline python <this-file> prepare.
Later actions: review, finish. Review is explicitly billable subscription use;
each invocation runs at most one process per selected reviewer. Oracle stays hidden.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_quality import quality_report, render_quality_card
from codex_harness.core.plan_store import PlanStore
from codex_harness.core.planner_views import publish_summary
from codex_harness.core.roundtable import findings, render_roundtable_card, run_roundtable
from codex_harness.core.spine_render import render_markdown
from codex_harness.core.test_audit import audit_revised_tests, render_test_audit_card

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PROXY = "opus-user-proxy"
QUESTIONS = [
    ("Who is it for?", "One person, on their own computer."),
    (
        "Units?",
        "Milliliters internally. Accept ml or oz; 1 oz = 29.5735 ml, rounded to the nearest ml.",
    ),
    ("Daily goal?", "Configurable, default 2000 ml. Show progress as a percentage."),
    ("Storage?", "Local JSON. WATER_CLI_DATA configures its path; default ./water.json."),
    (
        "Commands?",
        "add <amount> [unit], today, history [days] (default 7), goal [ml], undo (last entry of today).",
    ),
    ("Time zone and day boundary?", "The local calendar day."),
    (
        "Which edge cases matter?",
        "Reject zero/negative amounts and unknown units. Missing/corrupt files: clear errors, never crash or silent data loss. Empty undo is graceful.",
    ),
    ("What is out of scope?", "GUI, reminders, sync, multiple users."),
    ("Tech stack preference?", "Python 3.11+ standard library only; unittest."),
]
UPLIFTS = [
    (
        "D_JSON",
        "--json output",
        "Machine-readable output helps scripting, but introduces an output contract the proxy did not request.",
    ),
    (
        "D_STREAK",
        "Streak count",
        "Motivation can help habits, but adds calendar semantics beyond intake tracking.",
    ),
    (
        "D_BACKUP",
        "Data-file backup on write",
        "Backups help manual recovery, but add retention and disk-use decisions; atomic replacement protects failed writes now.",
    ),
]


def write_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def node(key, node_kind, title, text, parents=(), **fields):
    return dict(
        id=key, kind=node_kind, title=title, text=text, parents=list(parents), fields=fields
    )


def tool(server, tool_name, **args):
    result = server.call_tool(tool_name, args)
    if result["isError"]:
        raise RuntimeError(result["content"][0]["text"])
    return result["content"][0]["text"]


def prepare():
    server = PlannerServer(ROOT)
    try:
        banner = tool(
            server,
            "harness_plan_start",
            name="dogfood-watercli",
            idea="A tiny CLI that tracks daily water intake.",
        )
        store = server.store
        if (
            store.phase() != "align"
            or store.connection.execute("SELECT 1 FROM approvals").fetchone()
        ):
            raise RuntimeError(
                "Prepare requires a fresh fixture planning store; do not overwrite history."
            )
        card = tool(
            server,
            "harness_understanding_card",
            vision="One person records water locally and sees daily progress toward a configurable goal.",
            users="One person, on their own computer.",
            first_user_moment="After documented one-time JSON setup, add 250 ml, then today shows 12.5% of 2000 ml.",
            scope_in="Five commands, ml/oz conversion, local calendar days, local JSON, configurable goal/path, clear errors and safe writes.",
            scope_out="GUI, reminders, sync, multiple users; JSON output, streaks and backups await proxy decisions.",
            assumptions=[],
        )
        root = store.spine().node("V1")
        root["text"] = (
            "A tiny local CLI for one person to record water intake and see progress.\n\n"
            "User proxy: Opus supplied the assignment answers; Oren did not approve this plan. Local only.\n\n"
            'First moment: create water.json once with {"goal_ml":2000,"entries":[]}, '
            "run `python -m watercli.cli add 250`, then `today`: 250 ml / 2000 ml (12.5%). "
            "Missing files produce clear errors, including on first use; never implicitly initialize or reset.\n\n"
            "Scope: add, today, history (default 7 calendar days), goal (default 2000 ml), undo today's last entry; "
            "ml/oz, WATER_CLI_DATA, local calendar days. Out: GUI, reminders, sync, multiple users; "
            "deferred JSON output, streaks, backup retention. Python 3.11+ stdlib and unittest.\n\n"
            "Done feels like a quick add, readable daily percentage and trustworthy persistence, "
            "with invalid input or corrupt storage explained without a traceback or lost bytes."
        )
        root["fields"].update(
            validation=["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
            requirementFiles=["VISION.md", "PLAN.md", "REQUIREMENTS.md", "TRACE.md", "tests"],
        )
        store.upsert_node(root)
        for question, answer in QUESTIONS:
            key = store.ask(question, ["V1"])
            store.answer(key, "Opus (user proxy): " + answer)
        align = "Opus (user proxy): The supplied answers confirm this understanding: one person, local water tracking, five commands, configurable goal/path and explicit storage errors. This is proxy confirmation, not Oren's approval."
        store.approve("align", by=PROXY, statement=align)
        store.advance("expand", by="planner")
        for key, title, reason in UPLIFTS:
            store.upsert_node(
                node(
                    key,
                    "decision",
                    "Defer " + title,
                    "Proposed best-practice uplift. Disposition: deferred by planner pending a proxy answer. "
                    + reason,
                    ["V1"],
                    disposition="deferred",
                    decidedBy="planner",
                    reason="No supplied user-proxy approval; retain the tiny accepted scope.",
                )
            )
        store.advance("vision-lock", by="planner")
        lock = "Opus (user proxy): Lock the supplied water-cli scope: single person, five commands, ml/oz, local JSON/day, configurable 2000 ml goal, clear errors, stdlib Python and unittest; GUI/reminders/sync/multiple users excluded. Unanswered uplifts stay deferred. Approval derives from the delegated assignment, not the owner."
        store.approve("vision-lock", by=PROXY, statement=lock)
        store.advance("features", by="planner")
        features = [
            (
                "F1",
                "Record water",
                "Record a positive amount in ml or oz with exact integer ml conversion.",
                "S1",
                "As the sole user, I quickly record a drink in my preferred supported unit.",
            ),
            (
                "F2",
                "Trust local data",
                "Persist the goal and ordered day entries safely, and undo only today's last entry.",
                "S2",
                "As the sole user, I trust errors to preserve my history and can undo a mistaken drink.",
            ),
            (
                "F3",
                "See progress",
                "Read today's uncapped percentage and a chronological calendar-day history.",
                "S3",
                "As the sole user, I see how far I am toward my current goal, including empty days.",
            ),
            (
                "F4",
                "Simple terminal workflow",
                "Five commands, local calendar boundary, configured data location, concise success/error outputs.",
                "S4",
                "As the sole user, I use the CLI across processes without tracing implementation details.",
            ),
        ]
        for order, (key, title, text, story, story_text) in enumerate(features):
            store.upsert_node(node(key, "feature", title, text, ["V1"], order=order))
            store.upsert_node(
                node(story, "story", title + " story", story_text, [key], order=order)
            )
        store.advance("requirements", by="planner")
        requirements_and_plan(store, server, banner, card)
    finally:
        server.close()


def requirements_and_plan(store, server, banner, card):
    requirements = [
        (
            "R1",
            "Units and exact rounding",
            "parse_amount(amount: str, unit='ml') -> int. Decimal arithmetic, 1 oz=29.5735 ml; nearest integer, ties half up. Examples: 8 oz=237 ml; 1 oz=30; 1.5 ml=2; omitted unit=ml.",
            "S1",
            "test_units.py",
            False,
        ),
        (
            "R2",
            "Reject invalid amounts and units",
            "Raise ValueError for zero, negative, unknown units, garbage, nonfinite values and positive amounts rounding to zero. Unit tokens are exactly ml and oz.",
            "S1",
            "test_units.py",
            True,
        ),
        (
            "R3",
            "Local JSON persistence",
            "load(Path)->dict; save(Path,dict)->None; add_entry(data,day,ml)->new dict. Exact schema: {goal_ml:positive int, entries:[{day:YYYY-MM-DD,ml:positive int}]}; default seed goal=2000. Appends preserve order and inputs; save/load roundtrip exact values.",
            "S2",
            "test_store.py",
            False,
        ),
        (
            "R4",
            "Missing corrupt and failed-write errors",
            "load rejects missing/unreadable/invalid UTF-8/invalid JSON/schema with DataError, without creating or resetting files. Validate positive integers (not bool) and actual ISO calendar dates. save validates then writes a sibling temporary file, flushes/fsyncs, atomically os.replace; replacement failure preserves original bytes and cleans the temp. CLI returns 2 with clear stderr, never a traceback. No concurrent-process guarantee; single sequential user commands only.",
            "S2",
            "test_store.py",
            True,
        ),
        (
            "R5",
            "Undo today's last entry gracefully",
            "undo_entry(data,day)->(new dict,removed ml or None). Remove the last matching day in insertion order, preserving all other entries. No matching entry: unchanged state, CLI prints Nothing to undo today., exits 0 and performs no write.",
            "S2",
            "test_store.py",
            True,
        ),
        (
            "R6",
            "Configure a positive integer goal",
            "set_goal(data,ml)->new dict. Reject noninteger/zero/negative/bool goals. goal shows current ml; goal 2500 persists 2500 without changing entries. Initial documented seed=2000; history uses the current goal, not historical goal snapshots.",
            "S2",
            "test_store.py",
            True,
        ),
        (
            "R7",
            "Today's local-day percentage",
            "today_report(data,day)->str: YYYY-MM-DD: N ml / G ml (P.P%). Sum only the requested local calendar day. Zero intake is 0.0%; over-goal is uncapped (e.g.125.0%); format one decimal using Python's standard fixed-point formatting.",
            "S3",
            "test_report.py",
            False,
        ),
        (
            "R8",
            "Calendar history and invalid windows",
            "history_report(data,day,days=7)->list[str]. Positive integer days, oldest to newest, includes today and zero-intake dates across month/year boundaries. history defaults to 7; reject days <=0. Current goal applies to all rows.",
            "S3",
            "test_report.py",
            True,
        ),
        (
            "R9",
            "Five CLI commands and outputs",
            "main(argv=None)->int dispatches add <amount> [unit], today, history [days], goal [ml], undo. Module entry point python -m watercli.cli. WATER_CLI_DATA overrides ./water.json. Capture date.today().isoformat() once per command (local day). Outputs: Added N ml.; report line(s); Goal: N ml.; Removed N ml.; Nothing to undo today. Success=0. Imports public dependency names into cli namespace for isolated orchestration tests.",
            "S4",
            "test_cli.py",
            False,
        ),
        (
            "R10",
            "CLI clear errors without writes",
            "Unknown command, missing args, noninteger goal/history, invalid units/amounts and storage failures return 2, empty stdout, one-line Error: ... stderr without traceback. Missing file errors never create files. Successful read-only commands never save; no-op undo never saves. --help may use argparse's conventional help exit.",
            "S4",
            "test_cli.py",
            True,
        ),
        (
            "R11",
            "Integrated daily water journey",
            "Separate real CLI processes use a temp WATER_CLI_DATA seed: goal=2000; add 250; add 8 oz; today=487 ml,24.3%; history default 7/custom2; goal1000 persists,48.7%; undo237 leaves250; undo again; empty undo. Invalid inputs preserve bytes; corrupt JSON and missing files return2 without traceback or reset.",
            "S4",
            "test_end_to_end.py",
            False,
        ),
    ]
    for key, title, text, parent, module, edge in requirements:
        store.upsert_node(
            node(
                key,
                "requirement",
                title,
                text,
                [parent],
                acceptanceCriteria=[text],
                verification={"module": module, "level": "e2e" if key == "R11" else "unit"},
                **({"kind": "edge-case"} if edge else {}),
            )
        )
    for key, module, ids in [
        ("T_UNITS", "test_units.py", ["R1", "R2"]),
        ("T_STORE", "test_store.py", ["R3", "R4", "R5", "R6"]),
        ("T_REPORT", "test_report.py", ["R7", "R8"]),
        ("T_CLI", "test_cli.py", ["R9", "R10"]),
        ("T_E2E", "test_end_to_end.py", ["R11"]),
    ]:
        store.upsert_node(
            node(
                key,
                "test",
                key + " requirement proof",
                "Planner-owned tests; worker must not edit tests.",
                ids,
                module=module,
                level="e2e" if key == "T_E2E" else "unit",
            )
        )
    store.advance("tech-stack", by="planner")
    tool(
        server,
        "harness_decision_card",
        id="D_STACK",
        question="Which stack keeps this personal CLI small?",
        options=[
            {
                "name": "Python 3.11+ standard library",
                "pros": "Matches proxy preference; Decimal, argparse, JSON, tempfile, unittest; no runtime dependencies.",
                "cons": "Requires installed Python; no third-party command framework.",
            },
            {
                "name": "Python + Click",
                "pros": "Convenient command framework.",
                "cons": "Adds a dependency and violates stdlib-only preference.",
            },
        ],
        recommendation="Python 3.11+ standard library",
        parents=["V1"],
    )
    stack = store.spine().node("D_STACK")
    stack["text"] += (
        "\nDecision: choose standard library, per the supplied user-proxy preference; unittest is the requirement proof runner."
    )
    stack["fields"].update(
        selected="Python 3.11+ standard library", source="supplied Opus user-proxy answer"
    )
    store.upsert_node(stack)
    store.upsert_node(
        node(
            "D_CONTRACT",
            "decision",
            "Freeze simple interfaces and storage errors",
            "Planner detail choices within accepted scope: HALF_UP integer ml; explicit setup file; current goal for history; "
            "sequential invocations; atomic replace without backups. No new init command or automatic reset.",
            ["V1"],
        )
    )
    designs = [
        (
            "DES_UNITS",
            "Units boundary",
            "watercli/units.py exposes parse_amount; Decimal avoids conversion drift. Raises ValueError for caller presentation.",
            ["R1", "R2", "D_STACK", "D_CONTRACT"],
        ),
        (
            "DES_STORE",
            "Persistence boundary",
            "watercli/store.py owns DataError, load/save and pure add/undo/goal transforms. Exact schema, insertion order, copy-on-update and atomic replacement. Explicit seed docs precede first add.",
            ["R3", "R4", "R5", "R6", "D_STACK", "D_CONTRACT"],
        ),
        (
            "DES_REPORT",
            "Reporting boundary",
            "watercli/report.py contains pure today/history functions; caller supplies local day, data and current goal. No storage or units import.",
            ["R7", "R8", "D_STACK", "D_CONTRACT"],
        ),
        (
            "DES_CLI",
            "CLI data flow",
            "argv/env -> argparse -> local date -> load -> units/pure transforms or report -> save on mutations -> stdout. ValueError/DataError/OSError -> clear stderr and exit2. Canonical imported names are patched only in CLI unit proofs; e2e uses real modules and processes.",
            ["R9", "R10", "R11", "D_STACK", "D_CONTRACT"],
        ),
    ]
    for key, title, text, parents in designs:
        store.upsert_node(node(key, "design", title, text, parents))
    store.advance("plan", by="planner")
    for key, file, module, design, deps, provider in [
        ("A", "units", "test_units.py", "DES_UNITS", [], "codex"),
        ("B", "store", "test_store.py", "DES_STORE", [], "claude"),
        ("C", "report", "test_report.py", "DES_REPORT", [], "codex"),
        ("D", "cli", "test_cli.py", "DES_CLI", ["A", "B", "C"], "claude"),
    ]:
        store.upsert_node(
            node(
                key,
                "bead",
                file + " implementation",
                f"Implement only watercli/{file}.py against the fixed public stubs and linked requirements. "
                f"Acceptance: python -B -m unittest discover -s tests -p {module} passes; "
                "do not modify planner-owned tests/docs or __init__.py. Oracle is planner-only and absent from worker packets.",
                [design],
                ownedFiles=[f"watercli/{file}.py"],
                proofModule=module,
                dependsOn=deps,
                lessons=["D_CONTRACT"],
                provider=provider,
            )
        )
        test_key = {"A": "T_UNITS", "B": "T_STORE", "C": "T_REPORT", "D": "T_CLI"}[key]
        proof = store.spine().node(test_key)
        proof["parents"].append(key)
        store.upsert_node(proof)
    store.export_spine(ROOT / "spine.json")
    for name, text in render_markdown(store.spine()).items():
        (ROOT / "template" / name).write_text(text, encoding="utf-8", newline="\n")
    store.advance("roundtable", by="planner")
    record = {
        "labels": {"userProxy": True, "ownerApproval": False, "localOnly": True},
        "banner": banner,
        "understandingCard": card,
        "qualityBeforeRoundtable": quality_report(store),
    }
    write_json(ROOT / "planning-record.json", record)
    print(banner)
    print(render_quality_card(record["qualityBeforeRoundtable"]))


def review():
    desktop = Path(r"C:\Users\oren\AppData\Local\OpenAI\Codex\bin\9614f1d9b40d9c4f\codex.exe")
    if not desktop.is_file():
        raise RuntimeError("Required desktop codex.exe unavailable")
    claude = shutil.which("claude")
    reviewers = [
        {"name": "codex-reviewer", "provider": "codex", "model": "gpt-6.1-sol", "effort": "medium"}
    ]
    if claude:
        reviewers.append(
            {"name": "claude-reviewer", "provider": "claude", "model": "claude-haiku-4-5-20251001"}
        )
    captures = []
    with PlanStore(ROOT) as store:
        if store.phase() != "roundtable":
            raise RuntimeError("Review requires roundtable phase")
        existing = store.connection.execute(
            "SELECT name FROM sqlite_master WHERE name='review_runs'"
        ).fetchone()
        if existing:
            counts = dict(
                store.connection.execute(
                    "SELECT provider,count(*) FROM review_runs GROUP BY provider"
                )
            )
            if any(counts.get(item["provider"], 0) >= 2 for item in reviewers):
                raise RuntimeError("At most two runs per reviewer")

        def real_runner(launch, cwd, timeout):
            # Supported stdin transport avoids Windows' 32K command-line limit.
            prompt = launch.command[-1]
            command = (
                [*launch.command[:-1], "-"] if launch.cli == "codex" else list(launch.command[:-1])
            )
            capture_dir = ROOT / ".codex-harness" / "review-output"
            capture_dir.mkdir(exist_ok=True)
            index = len(list(capture_dir.glob(f"{launch.cli}-*.jsonl"))) + 1
            completed = subprocess.run(
                command,
                input=prompt,
                cwd=cwd,
                env={**os.environ, **launch.environment},
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                check=False,
            )
            (capture_dir / f"{launch.cli}-{index}.jsonl").write_text(
                completed.stdout, encoding="utf-8"
            )
            (capture_dir / f"{launch.cli}-{index}.stderr.txt").write_text(
                completed.stderr, encoding="utf-8"
            )
            captures.append(
                {
                    "provider": launch.cli,
                    "command": command,
                    "promptChars": len(prompt),
                    "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "transport": "supported stdin",
                    "isMocked": False,
                    "didRunProvider": True,
                    "exitCode": completed.returncode,
                    "protections": list(launch.protections),
                }
            )
            return completed.returncode, completed.stdout

        result = run_roundtable(
            store,
            reviewers=reviewers,
            runner=real_runner,
            executable_for={"codex": str(desktop), "claude": claude},
            timeout=600,
        )
        record = json.loads((ROOT / "planning-record.json").read_text(encoding="utf-8"))
        record.setdefault("rounds", []).append({"result": result, "launches": captures})
        record["findings"] = findings(store)
        write_json(ROOT / "planning-record.json", record)
        print(render_roundtable_card(result, findings(store)))


def finish():
    with PlanStore(ROOT) as store:
        if findings(store, "open"):
            raise RuntimeError("Resolve or reject every finding before publication")
        record = json.loads((ROOT / "planning-record.json").read_text(encoding="utf-8"))
        if not record.get("rounds") or record["rounds"][-1]["result"]["invalid"]:
            raise RuntimeError("A successful real roundtable is required by this assignment")
        store.export_spine(ROOT / "spine.json")
        for name, text in render_markdown(store.spine()).items():
            (ROOT / "template" / name).write_text(text, encoding="utf-8", newline="\n")
        subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "codex_harness.core.spine",
                "compile",
                str(ROOT / "spine.json"),
                str(ROOT / "sample.json"),
                "--template",
                str(ROOT / "template"),
            ],
            check=True,
        )
        manifest = json.loads((ROOT / "sample.json").read_text(encoding="utf-8"))
        # Git archive accepts a tree object: capture a genuine stub baseline
        # without creating any commit, changing the worktree index or using HEAD.
        with tempfile.TemporaryDirectory(prefix="watercli-audit-baseline-") as temp:
            baseline = Path(temp) / "repo"
            shutil.copytree(ROOT / "template", baseline)
            subprocess.run(["git", "init", "--quiet", str(baseline)], check=True)
            subprocess.run(["git", "-C", str(baseline), "add", "--all"], check=True)
            base = subprocess.check_output(
                ["git", "-C", str(baseline), "write-tree"], text=True
            ).strip()
            audit = audit_revised_tests(
                repo=baseline,
                base_ref=base,
                revised_template=ROOT / "template",
                manifest=manifest,
                impact={"affectedBeads": store.spine().ids("bead")},
                oracle_dir=ROOT / "reference",
            )
        if not audit["ok"]:
            raise RuntimeError(render_test_audit_card(audit))
        with tempfile.TemporaryDirectory(prefix="watercli-oracle-") as temp:
            workspace = Path(temp) / "workspace"
            shutil.copytree(ROOT / "template", workspace)
            shutil.copytree(ROOT / "reference", workspace, dirs_exist_ok=True)
            proof = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
                cwd=workspace,
                text=True,
                capture_output=True,
                timeout=60,
            )
            if proof.returncode:
                raise RuntimeError(proof.stderr)
        record["testAudit"] = {
            **audit,
            "baselineTree": base,
            "didCreateCommit": False,
            "oracleChecked": True,
            "allRequirementsProof": {"exitCode": proof.returncode, "output": proof.stderr},
            "limitation": "core.test_audit audits bead proofs only; e2e separately ran in the all-requirements oracle suite. Initial stub-baseline tree contains the real tests and no commits; baseline failures are expected. This is an initial satisfiability audit, not a revision-regression audit.",
        }
        record["qualityAfterRoundtable"] = quality_report(store)
        if store.phase() == "roundtable":
            store.advance("publish", by="planner")
        revision = store.publish(by="planner", export_dir=ROOT / "published")
        assert store.published_manifest(revision) == manifest
        record["publishedRevision"] = revision
        record["qualityAfterPublish"] = quality_report(store)
        record["publishSummary"] = publish_summary(
            [store.spine().node(key) for key in store.spine().ids("bead")],
            store.status()["open_assumptions"],
        )
        record["findings"] = findings(store)
        record["approvals"] = [
            dict(row) for row in store.connection.execute("SELECT * FROM approvals ORDER BY at")
        ]
        record["questions"] = [
            dict(row)
            for row in store.connection.execute("SELECT * FROM clarifications ORDER BY asked_at")
        ]
        record["phaseLog"] = [
            json.loads(row[0])
            for row in store.connection.execute(
                "SELECT payload_json FROM events WHERE kind='phase-advanced' ORDER BY id"
            )
        ]
        record["status"] = store.status()
        write_json(ROOT / "planning-record.json", record)
        print(render_test_audit_card(audit))
        print(render_quality_card(record["qualityAfterPublish"]))
        print(record["publishSummary"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "review", "finish"])
    globals()[parser.parse_args().action]()
