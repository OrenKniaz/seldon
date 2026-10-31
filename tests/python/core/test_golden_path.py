"""Scripted clients and providers; real stdio MCP, processes, verifier and delivery."""

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from copy import deepcopy
from pathlib import Path

import pytest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.python.core.test_plan_store import mock_catalog, mock_choice
from tests.python.core.test_semantic_runner import WEB_PAGE, web_document
from tests.python.core.test_semantic_wiring import PRODUCT, STUB, node

ROOT = Path(__file__).resolve().parents[3]


def provider_host(mode, workspace, surface, job_id=None):
    """Install external-provider doubles in each ordinary process owner.

    Preserve the detached controller, Windows Job monitor and ordinary dispatch;
    only replace provider execution with deterministic file-writing workers/reviewers.
    """
    from tests.python.core.test_acceptance_recovery import protocol

    from codex_harness.core import (
        audit_process,
        dispatch,
        mcp_planner,
        planner_jobs,
        planner_recovery,
        roundtable,
    )

    actual_popen = subprocess.Popen
    actual_audit = audit_process.run_audit_process
    actual_run = dispatch.run_plan

    def command(role, project, job=None):
        return [sys.executable, "-B", str(Path(__file__).resolve()), role, project, surface] + (
            [job] if job else []
        )

    def launch(argv, **options):
        if "codex_harness.core.planner_jobs" in argv:
            argv = command("controller", argv[argv.index("--workspace") + 1], argv[-1])
        return actual_popen(argv, **options)

    def controller_payload(argv, **options):
        return actual_audit(command("worker", str(workspace), job_id), **options)

    def contracts(root):
        (root / ("index.html" if surface == "web" else "hello.py")).write_text(
            "<!doctype html><h1>SELDON-STUB visit</h1>" if surface == "web" else STUB,
            encoding="utf-8",
        )

    def build(root):
        (root / ("index.html" if surface == "web" else "hello.py")).write_text(
            WEB_PAGE if surface == "web" else PRODUCT, encoding="utf-8"
        )

    def workers(plan, **options):
        return actual_run(plan, **options, fake_workers={"C": contracts, "B": build})

    def review(launch, *_):
        return tuple(
            value.replace("source-only", "gpt-6.1-sol" if launch.cli == "codex" else "stub")
            if isinstance(value, str)
            else value
            for value in protocol(launch.cli, text="[]")
        )

    subprocess.Popen = launch
    roundtable._executable = lambda *_: sys.executable
    roundtable._run = review
    dispatch.run_plan = workers
    if mode == "server":
        sys.argv = [sys.argv[0], "--workspace", str(workspace)]
        mcp_planner.main()
    elif mode == "controller":
        audit_process.run_audit_process = controller_payload
        raise SystemExit(planner_recovery.monitor(workspace, job_id))
    else:
        result = planner_jobs.execute_worker_job(workspace, job_id)
        raise SystemExit(0 if result["status"] == "completed" else 1)


@pytest.mark.fast
@pytest.mark.parametrize("surface", ["cli", "web"])
def test_golden_path(tmp_path, request, surface):
    """Protect idea→root product→accepted with three plain user decisions.

    Catch stranded delivery, approval churn and broken planner/worker/acceptance
    integration; existing journeys prepopulate the store and dispatch outside MCP.
    Stub results are suite evidence only, never actual-provider acceptance.
    """
    if surface == "web":
        request.getfixturevalue("web_browser_ready")
    started = time.monotonic()
    replies = queue.Queue()
    calls = decisions = 0
    revision = None
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)])}
    with (tmp_path / "server.log").open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "server", str(tmp_path), surface],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            env=env,
        )

        def read():
            for line in process.stdout:
                replies.put(json.loads(line))
            replies.put(None)

        threading.Thread(target=read, daemon=True).start()

        def rpc(method, params):
            process.stdin.write(
                json.dumps(dict(jsonrpc="2.0", id=calls, method=method, params=params)) + "\n"
            )
            process.stdin.flush()
            while True:
                reply = replies.get(timeout=90)
                assert reply is not None, (tmp_path / "server.log").read_text()
                if "id" in reply:
                    assert "error" not in reply, reply
                    return reply["result"]

        def call(tool_name, **arguments):
            nonlocal calls, revision
            calls += 1
            result = rpc(
                "tools/call",
                dict(name=tool_name, arguments=dict(expected_workspace=str(tmp_path), **arguments)),
            )
            if result.get("isError"):
                print(json.dumps(result))
            assert not result.get("isError"), (tool_name, json.dumps(result, ensure_ascii=True))
            revision = result.get("plannerConnection", {}).get("planRevision", revision)
            return result.get("structuredContent", {})

        def yes(card):
            nonlocal decisions
            decisions += 1
            call(
                "harness_record_chat_confirmation",
                gate=card["gate"],
                request_id=card["request_id"],
                expected_revision=card["plan_revision"],
                quoted_message="yes",
                quote_sha256=hashlib.sha256(b"yes").hexdigest(),
                actor_label="scripted user",
            )

        try:
            rpc(
                "initialize",
                dict(
                    protocolVersion="2025-06-18",
                    capabilities={},
                    clientInfo=dict(name="golden", version="1"),
                ),
            )
            process.stdin.write(
                json.dumps(dict(jsonrpc="2.0", method="notifications/initialized")) + "\n"
            )
            process.stdin.flush()
            call("harness_plan_status")
            call(
                "harness_plan_start",
                idea="A welcome page" if surface == "web" else "An offline greeting CLI",
                name="greeting",
                plan_mode="semantic-v1",
            )
            vision = node(
                "V1",
                "vision",
                [],
                planMode="semantic-v1",
                quality_bar="Accessible and readable"
                if surface == "web"
                else "Exact offline greeting",
                done_feels_like="Visit and explore" if surface == "web" else "Read Hello",
                expansion=dict(
                    mode="quality",
                    reason="Small explicit scope",
                    upgrades=[
                        dict(id=str(i), text=text, kind="risk", impact=text, disposition="accept")
                        for i, text in enumerate(["Offline", "Readable", "Simple"], 1)
                    ],
                ),
            )
            if surface == "web":
                vision["fields"].update(
                    taste=dict(
                        audience="Visitors",
                        tone="Calm",
                        references=["Quiet spaces"],
                        brand="Ink on white",
                        requiredSections=["Welcome", "Details"],
                        sourceRef="scripted-user:idea",
                    )
                )
            call("harness_upsert_node", node=vision)
            a = call(
                "harness_understanding_card",
                vision="A welcome page" if surface == "web" else "Offline greeting CLI",
                users="Visitors",
                first_user_moment="Explore the page" if surface == "web" else "Read Hello",
                scope_in="One page" if surface == "web" else "Offline greeting",
                scope_out="Accounts",
                assumptions=[],
            )
            yes(a)
            call("harness_advance", to_phase="expand")
            call("harness_advance", to_phase="vision-lock")
            filename = "index.html" if surface == "web" else "hello.py"
            nodes = [
                node("F", "feature", ["V1"]),
                node("R", "requirement", ["F"]),
                node("D", "decision", ["V1"]),
                node(
                    "CLI",
                    "design",
                    ["R"],
                    grammar="Visit page" if surface == "web" else "hello prints Hello and exits 0",
                ),
                node(
                    "C",
                    "bead",
                    ["CLI"],
                    ownedFiles=[filename],
                    dependsOn=[],
                    lessons=["D"],
                    semanticPhase="contracts",
                ),
                node(
                    "B",
                    "bead",
                    ["CLI"],
                    ownedFiles=[filename],
                    dependsOn=["C"],
                    lessons=[],
                    semanticPhase="build",
                ),
            ]
            scenarios = (
                web_document()
                if surface == "web"
                else dict(
                    schema="seldon/scenarios-v1",
                    scenarios=[
                        dict(
                            id="greeting",
                            requirementRefs=["R"],
                            contractRefs=["CLI"],
                            setup={},
                            actions=[
                                dict(
                                    id="hello", cli=dict(target="greet", operation="hello", argv=[])
                                )
                            ],
                            observe=[dict(action="hello", exitEquals=0, stdoutEquals="Hello")],
                            verdict="all",
                        )
                    ],
                )
            )
            scenarios["scenarios"][0].update(requirementRefs=["R"], contractRefs=["CLI"])
            vision["fields"].update(
                targets={"page": dict(kind="web", entry=filename)}
                if surface == "web"
                else {"greet": dict(entry=filename, argv=[])},
                scenarios=scenarios,
            )
            # Preserve the displayed A wording while adding its subordinate specification.
            vision["fields"].update(
                vision="A welcome page" if surface == "web" else "Offline greeting CLI",
                users="Visitors",
                first_user_moment="Explore the page" if surface == "web" else "Read Hello",
                scope_in="One page" if surface == "web" else "Offline greeting",
                scope_out="Accounts",
                assumptions=[],
            )
            call("harness_upsert_nodes", nodes=[*nodes, vision])
            call("harness_advance", to_phase="features")
            call("harness_advance", to_phase="plan")
            catalog = mock_catalog()
            other = deepcopy(catalog["providers"][0])
            other.update(provider="claude", executable="C:/mock/claude.exe")
            other["choices"][0]["model"] = "stub"
            catalog["providers"].append(other)
            catalog = call(
                "harness_reviewer_catalog", catalog=catalog, expected_spine_revision=revision
            )["catalog"]
            choice = mock_choice(catalog)
            choice["fields"]["reviewers"].append(
                dict(
                    name="secondary",
                    provider="claude",
                    **{
                        k: catalog["providers"][1]["choices"][0][k]
                        for k in ("choiceId", "model", "effort")
                    },
                )
            )
            call("harness_reviewer_choice", node=choice, expected_spine_revision=revision)
            compiled = call("harness_compile", include_detail=True)
            options = dict(
                expected_revision=compiled["planRevision"],
                max_parallel=1,
                timeout=30,
                call_cap=2,
                workers=[
                    dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                    for b in ("C", "B")
                ],
            )
            b = call(
                "harness_prepare_chat_confirmation",
                gate="plan-execution",
                execution=options,
                reviewer_node_id="CHOICE",
            )
            yes(b)
            call("harness_advance", to_phase="roundtable")
            call(
                "harness_roundtable",
                reviewers=b["subject"]["reviewers"],
                max_rounds=1,
                max_total=2,
                timeout=5,
            )
            call("harness_advance", to_phase="publish")
            call("harness_publish")
            call("harness_advance", to_phase="execute")
            job = call("harness_worker_start", **options)
            deadline = time.monotonic() + 90
            while job["status"] not in ("completed", "failed") and time.monotonic() < deadline:
                job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=10)
            assert job["status"] == "completed", job
            assert job["run"]["semanticContracts"]["state"] == "RED", job
            assert job["run"]["semanticValidation"]["state"] == "PASS", job
            assert (tmp_path / filename).is_file()
            if surface == "cli":
                assert (
                    subprocess.check_output(
                        [sys.executable, "-B", str(tmp_path / filename)], cwd=tmp_path
                    )
                    == b"Hello"
                )
            call("harness_advance", to_phase="accept")
            acceptance = call("harness_accept_run", repo=str(tmp_path), subject="product")
            while acceptance.get("result") is None and time.monotonic() < deadline:
                time.sleep(0.1)
                acceptance = call("harness_accept_status", id=acceptance["attemptId"])
            assert acceptance["result"]["passed"], acceptance
            call("harness_accept_review", repo=str(tmp_path), reviewers=b["subject"]["reviewers"])
            decisions += 1
            call("harness_accept", by="user", statement="yes")
            assert call("harness_plan_status")["acceptance"]["accepted"]
            assert decisions <= 3
            print(
                f"golden {surface}: decisions={decisions}; planner MCP calls={calls}; "
                f"seconds={time.monotonic() - started:.2f}"
            )
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    provider_host(*sys.argv[1:])
