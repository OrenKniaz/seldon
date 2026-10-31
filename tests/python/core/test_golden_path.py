"""Scripted clients and providers; real stdio MCP, processes, verifier and delivery."""

import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
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

    workspace = Path(workspace)
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
        first = surface == "cli" and not (workspace / "contracts-attempted").exists()
        if first:
            (workspace / "contracts-attempted").write_text("attempted", encoding="utf-8")
        (root / ("index.html" if surface == "web" else "hello.py")).write_text(
            "<!doctype html><h1>SELDON-STUB visit</h1>"
            if surface == "web"
            else "raise RuntimeError('broken stub')"
            if first
            else STUB,
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

    Catch late acceptance reviewer/budget questions, reapproval after repairs/recovery,
    and changed limits launching without a diff; plan-review-only tests miss delivery.
    Run real executable discovery: unknown model access must not hide installed Codex
    at B, plan review or acceptance; the former resolver stub hid this mismatch.
    Stub results are suite evidence only, never actual-provider acceptance.
    """
    if surface == "web":
        request.getfixturevalue("web_browser_ready")
    started = time.monotonic()
    replies = queue.Queue()
    calls = decisions = 0
    revision = None
    tools = tmp_path / "tools"
    tools.mkdir()
    codex = tools / ("codex.exe" if os.name == "nt" else "codex")
    shutil.copy2(sys.executable, codex)
    # Keep OS/git/node tools while excluding the host's provider installations/bundles.
    paths = [str(tools), str(Path(shutil.which("git")).parent)]
    if os.name == "nt":
        paths += [
            os.path.join(os.environ["SystemRoot"], "System32"),
            os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0"),
        ]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
        "PATH": os.pathsep.join(paths),
        "LOCALAPPDATA": str(tools),
    }
    if surface == "web":
        from codex_harness.core.semantic_runner import web_toolchain

        env["SELDON_WEB_CHROMIUM"] = web_toolchain(None, False)["chromium"]["path"]
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

        def call(tool_name, *, allow_error=False, **arguments):
            nonlocal calls, revision
            calls += 1
            result = rpc(
                "tools/call",
                dict(name=tool_name, arguments=dict(expected_workspace=str(tmp_path), **arguments)),
            )
            if result.get("isError") and not allow_error:
                print(json.dumps(result))
            if allow_error:
                assert result.get("isError"), result
                return result
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
            detected = call("harness_preflight", providers=["codex", "claude"])["providers"]
            assert [
                p["provider"] for p in detected if p["executable"]["status"] == "available"
            ] == ["codex"]
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
                            observe=[dict(action="hello", exitEquals=0, stdoutEquals="Hello\n")],
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
            catalog["providers"][0].update(
                executable=detected[0]["executable"]["path"], authentication="unknown"
            )
            catalog["providers"][0]["choices"][0]["availability"] = "unknown"
            catalog = call(
                "harness_reviewer_catalog", catalog=catalog, expected_spine_revision=revision
            )["catalog"]
            choice = mock_choice(catalog)
            choice["fields"]["reviewers"].append(
                {**choice["fields"]["reviewers"][0], "name": "second"}
            )
            call("harness_reviewer_choice", node=choice, expected_spine_revision=revision)
            compiled = call("harness_compile", include_detail=True)
            options = dict(
                expected_revision=compiled["planRevision"],
                max_parallel=1,
                timeout=30,
                call_cap=3,
                workers=[
                    dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                    for b in ("C", "B")
                ],
            )
            if surface == "cli":
                # A second executable requires cross-provider coverage, even if the
                # supplied catalog omitted it; the all-Codex journey cannot prove this.
                claude = tools / ("claude.exe" if os.name == "nt" else "claude")
                shutil.copy2(sys.executable, claude)
                refused = call(
                    "harness_prepare_chat_confirmation",
                    gate="plan-execution",
                    execution=options,
                    reviewer_node_id="CHOICE",
                    allow_error=True,
                )
                message = " ".join(c.get("text", "") for c in refused["content"])
                assert "select reviewers covering ['claude', 'codex']" in message
                claude.unlink()
            b = call(
                "harness_prepare_chat_confirmation",
                gate="plan-execution",
                execution=options,
                reviewer_node_id="CHOICE",
            )
            reviewers = b["subject"]["reviewers"]
            assert reviewers == [
                dict(name="primary", provider="codex", model="gpt-6.1-sol", effort="high"),
                dict(name="second", provider="codex", model="gpt-6.1-sol", effort="high"),
            ]
            assert b["subject"]["reviewSelection"] == dict(
                stages=["plan", "full-vision"],
                assurance=dict(
                    independentRequired=True, minIndependentReviewers=2, crossProviderRequired=False
                ),
            )
            assert b["subject"]["scheduledReviewStarts"] == {"plan": 2, "full-vision": 2}
            assert b["subject"]["repairReserve"] == 2
            assert all(r["name"] in b["card"] and r["model"] in b["card"] for r in reviewers)
            yes(b)
            if surface == "cli":
                # Repair the checker to match the accepted exact greeting, not the requirement.
                vision["fields"]["scenarios"]["scenarios"][0]["observe"][0]["stdoutEquals"] = (
                    "Hello"
                )
                vision["fields"]["productSurface"] = "cli"
                call("harness_upsert_node", node=vision)
                repaired = call("harness_compile", include_detail=True)
                assert repaired["planRevision"] != options["expected_revision"]
                options["expected_revision"] = repaired["planRevision"]
            call("harness_advance", to_phase="roundtable")
            plan_review = call(
                "harness_roundtable",
                max_rounds=1,
                max_total=2,
                timeout=5,
            )
            assert [
                {k: r[k] for k in ("name", "provider", "model", "effort")}
                for r in plan_review["reviewers"]
            ] == reviewers
            call("harness_advance", to_phase="publish")
            call("harness_publish")
            call("harness_advance", to_phase="execute")
            if surface == "cli":
                changed = call(
                    "harness_worker_start", **{**options, "call_cap": 4}, allow_error=True
                )
                diff = changed["structuredContent"]["chatConfirmation"]["diff"]
                assert [(d["field"], d["before"], d["after"]) for d in diff] == [
                    ("execution.callCap", 3, 4)
                ]
                assert (
                    sum(c["text"].count("Your decision: approve B?") for c in changed["content"])
                    == 1
                )
            job = call("harness_worker_start", **options)
            deadline = time.monotonic() + 90
            while job["status"] not in ("completed", "failed") and time.monotonic() < deadline:
                job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=10)
            if surface == "cli":
                failed = json.loads(Path(job["run"]["recordPath"]).read_text())
                assert failed["beads"]["C"]["integrated"]["semantic"]["state"] == "ERROR"
                original = job["jobId"]
                proof = call("harness_recovery", mode="inspect", job_id=original)
                while proof["verdict"] != "VERIFIED-QUIESCENT" and time.monotonic() < deadline:
                    time.sleep(0.1)
                    proof = call("harness_recovery", mode="inspect", job_id=original)
                assert proof["verdict"] == "VERIFIED-QUIESCENT", proof
                resolved = call(
                    "harness_recovery",
                    mode="resolve",
                    job_id=original,
                    choice="readmit",
                    reason="Repair the failed Contracts stub",
                )
                assert "card" not in resolved
                job = call("harness_worker_start", **options)
                assert job["jobId"] == resolved["successor"] != original
                while job["status"] not in ("completed", "failed") and time.monotonic() < deadline:
                    job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=10)
            assert job["status"] == "completed", job
            assert job["run"]["semanticContracts"]["state"] == "RED", job
            assert job["run"]["semanticValidation"]["state"] == "PASS", job
            if surface == "web":
                # Protect mode provenance and preview guidance through canonical dispatch;
                # CLI journeys never render, and packet fixtures do not consume dispatch.
                retained = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
                proof = retained["semanticValidation"]["results"]
                actions = [a for r in proof for a in r["actions"].values()]
                assert all(a["browserMode"] in {"normal", "single-process"} for a in actions)
                # Execute the packet's actual shell command: a quoted Windows executable
                # without PowerShell's & looks plausible but never opens the preview.
                from codex_harness.core.audit_process import run_audit_process

                prompt = retained["beads"]["B"]["prompt"]
                command = next(c for c in prompt.split("`") if "--preview ." in c)
                try:
                    preview = run_audit_process(
                        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                        cwd=tmp_path,
                        timeout=20,
                    )
                    output, cleanup = preview.stdout, preview.audit_control
                except subprocess.TimeoutExpired as error:
                    output, cleanup = error.output, error.audit_control
                observed = json.loads(output)
                assert observed.get("state") != "ERROR", observed
                assert observed["browserMode"] in {"normal", "single-process"}
                assert cleanup["quiescenceVerified"]
                assert cleanup["activeProcessesAfterTeardown"] == 0
                print(
                    f"golden web: scenarios={len(proof)}; journeys={len(actions)}; "
                    f"browserModes={sorted({a['browserMode'] for a in actions})}"
                )
            assert (tmp_path / filename).is_file()
            if surface == "cli":
                assert (
                    subprocess.check_output(
                        [sys.executable, "-B", str(tmp_path / filename)], cwd=tmp_path
                    )
                    == b"Hello"
                )
            call("harness_advance", to_phase="accept")
            deadline = time.monotonic() + 90
            acceptance = call("harness_accept_run", repo=str(tmp_path), subject="product")
            while acceptance.get("result") is None and time.monotonic() < deadline:
                time.sleep(0.1)
                acceptance = call("harness_accept_status", id=acceptance["attemptId"])
            assert acceptance["result"]["passed"], acceptance
            review = call("harness_accept_review", repo=str(tmp_path))
            attempt_id = review["attemptId"]
            deadline = time.monotonic() + 90
            while review["state"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.1)
                review = call("harness_accept_status", id=attempt_id)
                assert review["attemptId"] == attempt_id, review
            assert review["state"] == "completed", review
            assert review["result"]["id"] == attempt_id, review
            assert [
                {k: r[k] for k in ("name", "provider", "model", "effort")}
                for r in review["result"]["reviewers"]
            ] == reviewers
            decisions += 1
            call("harness_accept", by="user", statement="yes")
            final_status = call("harness_plan_status")
            assert final_status["acceptance"]["accepted"]
            # The real saved producer reaches HTTP and the shipped browser view.
            # Catch revision/blocker/next-step divergence and broken asset wiring;
            # planning-only tests and synthetic UI fixtures miss this integration.
            from tests.python.core.test_cockpit_server import get, running

            before = {
                p: p.read_bytes() for p in (tmp_path / ".codex-harness").rglob("*") if p.is_file()
            }
            run_path = Path(job["run"]["recordPath"])
            runs_root = (
                run_path.parents[2] if run_path.parent.name == "evidence" else run_path.parents[1]
            )
            with running(tmp_path, workspace=tmp_path, runs_root=runs_root) as connection:
                status_code, _, body = get(connection, "/api/status")
                assert status_code == 200
                observed = json.loads(body)["plan"]
                for key in ("plan_revision", "pending_gate", "open_findings", "next", "acceptance"):
                    assert observed[key] == final_status[key], key
                if surface == "web":
                    subprocess.run(
                        [
                            "node",
                            str(ROOT / "tests/browser/cockpit/checks.js"),
                            f"http://127.0.0.1:{connection.port}",
                            observed["plan_revision"],
                            observed["next"],
                        ],
                        check=True,
                        timeout=45,
                    )
            after = {
                p: p.read_bytes() for p in (tmp_path / ".codex-harness").rglob("*") if p.is_file()
            }
            assert before == after
            assert decisions == 3
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
