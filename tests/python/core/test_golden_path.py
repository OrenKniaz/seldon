"""Scripted clients and providers; real stdio MCP, processes, verifier and delivery."""

import hashlib
import json
import os
import queue
import runpy
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


ROOT = Path(__file__).resolve().parents[3]


def provider_host(mode, workspace, surface, job_id=None):
    """Install external-provider doubles in each ordinary process owner.

    Preserve the detached controller, Windows Job monitor and ordinary dispatch;
    only replace provider execution with deterministic file-writing workers/reviewers.
    """
    from tests.python.core.acceptance_recovery_support import protocol

    from codex_harness.core import (
        audit_process,
        dispatch,
        mcp_planner,
        planner_jobs,
        planner_recovery,
        roundtable,
    )

    workspace = Path(workspace)
    filename = "index.html" if surface == "web" else "hashslip.py"
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
        (root / filename).write_text(
            "<!doctype html>"
            + "".join(
                f"<p>SELDON-STUB {op}</p>"
                for op in ("content", "navigation", "faq-pointer", "faq-keyboard")
            )
            if surface == "web"
            else "raise RuntimeError('broken stub')"
            if first
            else "import sys\n"
            "operation = 'help' if sys.argv[1:] == ['--help'] else 'receipt' "
            "if not sys.argv[1:] or sys.argv[1:2] == ['--label'] and len(sys.argv) == 3 "
            "else 'usage'\nprint('SELDON-STUB ' + operation, file=sys.stderr)\nsys.exit(3)\n",
            encoding="utf-8",
        )

    def build(root):
        if surface == "cli" and not (workspace / "stall-attempted").exists():
            # A real descendant hangs mid-Build with uncommitted poisoned writes.
            # The Job owner must reap it; an exited fake cannot prove recovery.
            (workspace / "stall-attempted").write_text(str(root), encoding="utf-8")
            child = actual_popen(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "from pathlib import Path; import time; "
                    "Path('hashslip.py').write_text('POISON'); "
                    "Path('abandoned.txt').write_text('never merge'); time.sleep(600)",
                ],
                cwd=root,
            )
            child.wait()
        (root / filename).write_bytes((ROOT / "tests/fixtures/efficiency" / filename).read_bytes())
        if surface == "web":
            with (root / filename).open("a", encoding="utf-8") as out:
                out.write("<style>h1{background:linear-gradient(white,#fff)}</style>")

    def workers(plan, **options):
        return actual_run(plan, **options, fake_workers={"C": contracts, "B": build})

    def review(launch, *_):
        if "## Read-only candidate evidence" in launch.stdin_text:
            tier = "same-provider" if surface == "cli" else "cross-provider"
            assert f"## Review tier: {tier}" in launch.stdin_text
            assert "catalog: unverified" in launch.stdin_text
        text = "[]"
        if (
            mode == "server"
            and surface == "cli"
            and not (workspace / "review-output-sent").exists()
        ):
            (workspace / "review-output-sent").write_text("sent")
            payload = json.loads((ROOT / "tests/fixtures/efficiency/run26.json").read_text("utf-8"))
            text = json.dumps([payload["headingFinding"]])
        return tuple(
            value.replace(
                "source-only", "gpt-6.1-sol" if launch.cli == "codex" else "claude-sonnet-5-5"
            ).replace("Some(High)", "Some(Medium)" if surface == "web" else "Some(Medium)")
            if isinstance(value, str)
            else value
            for value in protocol(launch.cli, text=text)
        )

    subprocess.Popen = launch
    roundtable._run = review
    dispatch.run_plan = workers
    if mode == "server":
        original_dispatch = mcp_planner.PlannerServer._dispatch

        def noisy_dispatch(self, name, arguments, **options):
            if name == "harness_plan_start":
                print("tool diagnostic: café 中文")
                os.write(1, "raw diagnostic: 中文\n".encode())
            return original_dispatch(self, name, arguments, **options)

        mcp_planner.PlannerServer._dispatch = noisy_dispatch
        sys.argv = [sys.argv[0], "--workspace", str(workspace)]
        runpy.run_module("codex_harness.core.seldon_mcp", run_name="__main__")
    elif mode == "controller":
        # Accelerate the production no-progress clock, preserving B's scope.
        if surface == "cli":
            planner_recovery.STALL_SECONDS = 15
        audit_process.run_audit_process = controller_payload
        raise SystemExit(planner_recovery.monitor(workspace, job_id))
    else:
        result = planner_jobs.execute_worker_job(workspace, job_id)
        raise SystemExit(0 if result["status"] == "completed" else 1)


@pytest.mark.parametrize("surface", ["cli", "web"])
def test_golden_path(tmp_path, request, surface):
    """Protect idea→root product→accepted with three plain user decisions.

    Catch late acceptance reviewer/budget questions, reapproval after repairs/recovery,
    and changed limits launching without a diff; plan-review-only tests miss delivery.
    Replay run26/UX6 plans, reviewer tuples, schema refusals and browser actions.
    Catch incomplete preflight, invented TRACE nodes, illegal phase guidance and a late
    visual judgment staling scenarios; earlier authored-perfect journeys missed these seams.
    Replay the JSONKeys farm B refusal with installed Codex+Claude but two authorized Sol
    reviewers; prove the same-provider tier survives B, acceptance packet and final C.
    No model catalog is supplied: discovery-unavailable must still reach C with three
    decisions and catalog: unverified in B and the real acceptance packet. Catalog-backed
    fixtures missed the farm deadlock before B; exact proposed tuples must reach launches.
    Stub results are suite evidence only, never actual-provider acceptance.
    Replay UX-5's captured Unicode upsert and survive an uncaught malformed tool
    request plus tool stdout noise; direct handle_line tests miss pipe death.
    Replay ShelfLife: lose an exited owner's receipt, then hang a real descendant
    mid-Build. Both attempts must be quarantined and readmitted under the same B;
    ordinary successful fixtures missed UNKNOWN holding delivery forever.
    """
    if surface == "web":
        request.getfixturevalue("web_browser_ready")
    started = time.monotonic()
    (tmp_path / "AGENTS.md").write_text(
        "Use gpt-6.1-sol at medium effort for all helper assignments, including reviews.\n",
        encoding="utf-8",
    )
    replies = queue.Queue()
    calls = decisions = 0
    counts = dict(happy=0, probes=0, polls=0)
    probing = False
    revision = None
    tools = tmp_path / "tools"
    tools.mkdir()
    codex = tools / ("codex.exe" if os.name == "nt" else "codex")
    captured_run = json.loads(
        (
            ROOT / "tests/fixtures/efficiency" / ("ux6.json" if surface == "web" else "run26.json")
        ).read_text("utf-8")
    )
    shutil.copy2(sys.executable, codex)
    shutil.copy2(sys.executable, tools / ("claude.exe" if os.name == "nt" else "claude"))
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
        "PYTHONIOENCODING": "cp1252",
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
                json.dumps(
                    dict(jsonrpc="2.0", id=calls, method=method, params=params), ensure_ascii=False
                )
                + "\n"
            )
            process.stdin.flush()
            while True:
                reply = replies.get(timeout=180)
                assert reply is not None, (tmp_path / "server.log").read_text()
                if "id" in reply:
                    assert "error" not in reply, reply
                    return reply["result"]

        def call(tool_name, *, allow_error=False, _probe=False, **arguments):
            nonlocal calls, revision
            calls += 1
            category = (
                "polls"
                if tool_name in {"harness_worker_status", "harness_accept_status"}
                else "probes"
                if probing or _probe or allow_error
                else "happy"
            )
            counts[category] += 1
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
            content = "\n".join(c.get("text", "") for c in result.get("content", []))
            data = result.get("structuredContent", {})
            if tool_name == "harness_publish":
                assert "Next: harness_advance to_phase=execute" in content, content
            if tool_name == "harness_worker_status" and data.get("status") == "failed":
                assert "Next: harness_recovery mode=inspect" in content, content
            if tool_name == "harness_worker_status" and data.get("run", {}).get("delivery"):
                assert "Next: harness_advance to_phase=accept" in content, content
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
                quoted_message="Yes",
                quote_sha256=hashlib.sha256(b"Yes").hexdigest(),
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
                idea=captured_run["nodes"][0]["text"],
                name=captured_run["nodes"][0]["title"],
                plan_mode="semantic-v1",
            )
            if surface == "cli":
                calls += 1
                counts["probes"] += 1
                failed = rpc(
                    "tools/call",
                    dict(
                        name=["harness_upsert_node"],
                        arguments=dict(expected_workspace=str(tmp_path)),
                    ),
                )
                assert failed["isError"] and "unconfirmed" in failed["content"][0]["text"]
                assert rpc("ping", {}) == {}
                probing = True
                captured = json.loads(
                    (ROOT / "tests/fixtures/ux5-turn7-upsert.json").read_text("utf-8")
                )
                before = call("harness_show_node", id="V1")
                refused = call("harness_upsert_node", node=captured["node"], allow_error=True)
                assert "value" in str(refused)
                assert call("harness_show_node", id="V1") == before
                captured_f3 = json.loads(
                    (ROOT / "tests/fixtures/f3-turn11-vision.json").read_text("utf-8")
                )
                call("harness_upsert_node", node=captured_f3)
                assert call("harness_show_node", id="V1")["fields"] == captured_f3["fields"]
                call(
                    "harness_upsert_node",
                    node={
                        k: before[k] for k in ("id", "kind", "title", "text", "parents", "fields")
                    },
                )
                probing = False
            a = call("harness_understanding_card", **captured_run["understanding"])
            yes(a)
            filename = "index.html" if surface == "web" else "hashslip.py"
            nodes = captured_run["nodes"]
            scenarios = nodes[0]["fields"]["scenarios"]
            written = call("harness_upsert_nodes", nodes=nodes)
            call("harness_advance", to_phase="plan")
            preflight = call(
                "harness_preflight",
                providers=["codex", "claude"],
                execution={},
                reviewer_node=next(
                    r["arguments"]["node"]
                    for r in captured_run["refusals"]
                    if r["tool"] == "harness_reviewer_choice"
                ),
            )
            issues = "; ".join(preflight["planningPrerequisites"])
            assert all(
                part in issues
                for part in (
                    "execution.call_cap",
                    "execution.expected_revision",
                    "execution.workers",
                    "reviewer-effort-invalid",
                )
            ), issues
            assert "reviewer-catalog" not in issues, issues
            assert preflight["catalog"] == "unverified"
            detected = preflight["providers"]
            assert all(p["executable"]["status"] == "available" for p in detected)
            farm = json.loads(
                (ROOT / "tests/fixtures/efficiency/jsonkeys-reviewers.json").read_text("utf-8")
            )
            choice = farm["choice"] if surface == "cli" else captured_run["choice"]
            choice["fields"].update(catalogId=None, catalogDigest=None)
            for reviewer in choice["fields"]["reviewers"]:
                reviewer["choiceId"] = None
            choice["fields"]["recommendation"]["choiceIds"] = []
            call("harness_reviewer_choice", node=choice, expected_spine_revision=revision)
            compiled = call("harness_compile", include_detail=True)
            options = dict(
                expected_revision=compiled["planRevision"],
                max_parallel=1,
                timeout=300,
                call_cap=4,
                workers=[
                    dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                    for b in ("C", "B")
                ],
            )
            proposal = dict(gate="plan-execution", execution=options, reviewer_node_id=choice["id"])
            if surface == "cli":
                # Farm turns 2-6: installed Claude must not override this two-Sol selection.
                captured = next(t["refusals"][-1] for t in reversed(farm["turns"]) if t["refusals"])
                proposal = {
                    k: v for k, v in captured["arguments"].items() if k != "expected_workspace"
                }
                proposal.update(execution=options, reviewer_node_id=choice["id"])
            b = call("harness_prepare_chat_confirmation", **proposal)
            repeated = call(
                "harness_prepare_chat_confirmation",
                gate="plan-execution",
                execution=options,
                reviewer_node_id=choice["id"],
                _probe=True,
            )
            assert repeated["request_id"] == b["request_id"]
            reviewers = b["subject"]["reviewers"]
            assert reviewers == [
                {
                    k: r[k] if k != "effort" else r[k]["value"]
                    for k in ("name", "provider", "model", "effort")
                }
                for r in choice["fields"]["reviewers"]
            ]
            tier = "same-provider" if surface == "cli" else "cross-provider"
            assert b["subject"]["reviewTier"] == tier and f"Review tier: {tier}" in b["card"]
            assert "Cross-subscription is preferred, never required" in b["card"]
            assert b["subject"]["scheduledReviewStarts"] == {"plan": 4, "full-vision": 4}
            assert b["subject"]["repairReserve"] == 2
            assert all(r["name"] in b["card"] and r["model"] in b["card"] for r in reviewers)
            assert b["card"].count("up to 10 reviews") == 1
            assert b["subject"]["catalog"] == "unverified"
            assert "catalog: unverified" in b["card"]
            yes(b)
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
            if surface == "cli":
                review = call("harness_review", id=plan_review["roundId"], _probe=True)
                row = review["findingMembers"][0]
                assert row["node_id"] == "plan" and row["finding"].startswith("[TRACE]")
                captured_closure = next(
                    r["arguments"]["findings"][0]
                    for r in captured_run["refusals"]
                    if r["tool"] == "harness_resolve_findings"
                )
                closure = captured_closure["closure"]
                closure.update(
                    expectedManifestRevision=options["expected_revision"],
                    memberIds=[row["id"]],
                    nodeRefs=[dict(nodeId="G1", revision=written["revisions"]["G1"])],
                )
                call(
                    "harness_resolve_findings",
                    findings=[
                        dict(
                            id=row["id"],
                            status="resolved",
                            resolution=captured_closure["resolution"],
                            closure=closure,
                        )
                    ],
                )
            call("harness_advance", to_phase="publish")
            call("harness_publish")
            call("harness_advance", to_phase="execute")
            if surface == "cli":
                changed = call(
                    "harness_worker_start", **{**options, "call_cap": 5}, allow_error=True
                )
                diff = changed["structuredContent"]["chatConfirmation"]["diff"]
                assert [(d["field"], d["before"], d["after"]) for d in diff] == [
                    ("execution.callCap", 4, 5)
                ]
                assert (
                    sum(c["text"].count("Your decision: approve B?") for c in changed["content"])
                    == 1
                )
            job = call("harness_worker_start", **options)
            deadline = time.monotonic() + 300
            while job["status"] not in ("completed", "failed") and time.monotonic() < deadline:
                job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=60)
            if surface == "web":
                saved = json.loads(Path(job["run"]["recordPath"]).read_text("utf-8"))
                assert saved["semanticValidation"]["state"] == "FAIL"
                roots = saved["beads"]["B"]["integrated"]["root"]
                assert any(
                    a["checks"]["textContrastAA"] is None
                    for r in saved["semanticValidation"]["results"]
                    for a in r["actions"].values()
                )
                call(
                    "harness_accept_assess",
                    judgment=dict(
                        repo=roots,
                        check="textContrastAA",
                        passed=True,
                        statement="Scripted human fixture observation: all gradient endpoints are"
                        " white and generated label is dark; review retained viewport/f"
                        "ull-page images in both motion preferences. This is not automa"
                        "ted contrast proof.",
                        by="scripted observer",
                    ),
                )
                repaired = call(
                    "harness_recovery",
                    mode="resolve",
                    job_id=job["jobId"],
                    choice="revalidate",
                    reason="Consume candidate-bound manual contrast observation within B",
                )
                assert repaired["validation"]["state"] == "PASS", repaired
                job = call("harness_worker_status", job_id=job["jobId"])
            if surface == "cli":
                failed = json.loads(Path(job["run"]["recordPath"]).read_text())
                assert failed["beads"]["C"]["integrated"]["semantic"]["state"] == "ERROR"
                probing = True
                original = job["jobId"]
                proof = call("harness_recovery", mode="inspect", job_id=original)
                while proof["verdict"] != "VERIFIED-QUIESCENT" and time.monotonic() < deadline:
                    time.sleep(0.1)
                    proof = call("harness_recovery", mode="inspect", job_id=original)
                assert proof["verdict"] == "VERIFIED-QUIESCENT", proof
                evidence = Path(job["evidenceDir"])
                (evidence / "quiescence.json").rename(evidence / "retained-quiescence.json")
                resolved = call(
                    "harness_recovery",
                    mode="resolve",
                    job_id=original,
                    choice="readmit",
                    reason="Repair the failed Contracts stub",
                )
                assert "card" not in resolved
                assert resolved["proof"]["verdict"] == "ABANDONED"
                assert resolved["quarantine"] is True
                job = call("harness_worker_start", **options)
                assert job["jobId"] == resolved["successor"] != original
                while job["status"] not in ("completed", "failed") and time.monotonic() < deadline:
                    job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=60)
                assert job["status"] == "failed", job
                stalled = job["jobId"]
                proof = call("harness_recovery", mode="inspect", job_id=stalled)
                assert proof["verdict"] == "VERIFIED-QUIESCENT", proof
                assert proof["controllerAndChildren"]["stopReason"] == "worker-stalled"
                assert proof["controllerAndChildren"]["activeProcessesAfterTeardown"] == 0
                poisoned = Path((tmp_path / "stall-attempted").read_text(encoding="utf-8"))
                assert (poisoned / "hashslip.py").read_text() == "POISON"
                assert not (tmp_path / "abandoned.txt").exists()
                resolved = call(
                    "harness_recovery",
                    mode="resolve",
                    job_id=stalled,
                    choice="readmit",
                    reason="End stalled Build and retry within the same B",
                )
                assert resolved["quarantine"] is True
                job = call("harness_worker_start", **options)
                assert job["jobId"] == resolved["successor"] != stalled
                while job["status"] not in ("completed", "failed") and time.monotonic() < deadline:
                    job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=60)
                assert not (tmp_path / "abandoned.txt").exists()
                probing = False
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
                assert subprocess.check_output(
                    [sys.executable, "-B", str(tmp_path / filename), "--help"], cwd=tmp_path
                ).startswith(b"usage:")
            if surface == "web":
                call(
                    "harness_accept_assess",
                    judgment=dict(
                        repo=str(tmp_path),
                        check="visualQuality",
                        passed=True,
                        statement="Scripted observer: actual fixture page matches the locked read"
                        "able quality bar and Done; source-only journey",
                        by="scripted user",
                    ),
                )
            call("harness_advance", to_phase="accept")
            deadline = time.monotonic() + 300
            acceptance = call("harness_accept_run", repo=str(tmp_path), subject="product")
            if surface == "web":
                judgment = dict(captured_run["judgments"][-1], repo=str(tmp_path))
                call("harness_accept_assess", judgment=judgment, _probe=True)
            while acceptance.get("result") is None and time.monotonic() < deadline:
                acceptance = call(
                    "harness_accept_status", id=acceptance["attemptId"], wait_seconds=60
                )
            assert acceptance["result"]["passed"], acceptance
            review = call("harness_accept_review", repo=str(tmp_path))
            attempt_id = review["attemptId"]
            deadline = time.monotonic() + 300
            while review["state"] in {"queued", "running"} and time.monotonic() < deadline:
                review = call("harness_accept_status", id=attempt_id, wait_seconds=60)
                assert review["attemptId"] == attempt_id, review
            assert review["state"] == "completed", review
            assert review["result"]["id"] == attempt_id, review
            assert [
                {k: r[k] for k in ("name", "provider", "model", "effort")}
                for r in review["result"]["reviewers"]
            ] == reviewers
            decisions += 1
            call("harness_accept", by="user", statement="yes")
            final_status = call("harness_plan_status", _probe=True)
            assert final_status["acceptance"]["accepted"]
            assert {s["id"] for s in final_status["acceptance"]["scenarios"]} == {
                s["id"] for s in scenarios["scenarios"]
            }
            assert all(s["lastResult"] == "pass" for s in final_status["acceptance"]["scenarios"])
            # The real saved producer reaches HTTP and the shipped browser view.
            # Catch revision/blocker/next-step divergence and broken asset wiring;
            # planning-only tests and synthetic UI fixtures miss this integration.
            from tests.python.core.cockpit_server_support import get, running

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
            assert counts["happy"] <= 25, counts
            assert sum(counts.values()) == calls
            print(
                f"golden {surface}: decisions={decisions}; planner MCP calls={calls}; "
                f"happy={counts['happy']}; probes={counts['probes']}; polls={counts['polls']}; "
                f"seconds={time.monotonic() - started:.2f}"
            )
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            assert process.returncode == 0, (tmp_path / "server.log").read_text("utf-8")


if __name__ == "__main__":
    provider_host(*sys.argv[1:])
