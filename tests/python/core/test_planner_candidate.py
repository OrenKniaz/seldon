"""Public retained-candidate recovery with real verifier/browser/local processes."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.core.planning_support import (
    source_controller_roundtable,
    source_only_b,
    source_only_confirmation,
    source_only_expansion,
)
from tests.python.core.test_planner_recovery import answer, snapshot, tool
from tests.python.core.test_semantic_runner import WEB_PAGE
from tests.python.core.test_semantic_wiring import semantic_plan as semantic_plan
from tests.python.core.test_semantic_wiring import web_plan as web_plan

from codex_harness.core import audit_process, dispatch, planner_recovery, semantic_runner
from codex_harness.core import planner_jobs as jobs
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PHASES


@pytest.mark.parametrize("surface", ["cli", "web"])
@pytest.mark.parametrize(
    "valid,recover",
    [(True, True), (False, True), (True, False)],
    ids=["delivered", "failed-with-reason", "uninterrupted"],
)
def test_public_revalidate_retained_browser_candidate_without_worker_start(
    request, monkeypatch, valid, surface, recover
):
    """Catch UX-4's stranded page and accidental rebuild/spend after verifier ERROR.

    Existing recovery tests only readmit; browser wiring tests never recover a
    held job or read the delivered root page. Inject a real checker process crash,
    then restore the verifier; no mocked verifier verdict or integration receipt.
    """
    if surface == "web":
        request.getfixturevalue("web_browser_ready")
    store, _ = request.getfixturevalue("web_plan" if surface == "web" else "semantic_plan")
    if surface == "cli":
        for key in ("C", "B"):
            bead = store.spine().node(key)
            bead["fields"]["ownedFiles"] = ["hello.py", "index.html"]
            store.upsert_node(bead)
    source_only_expansion(store)
    while store.phase() != "roundtable":
        if store.phase() in {"align", "vision-lock"}:
            source_only_confirmation(store, store.phase())
        if store.phase() == "plan":
            source_only_b(store)
        store.advance(PHASES[PHASES.index(store.phase()) + 1], by="source-test")
    store.compile_template(store.workspace / "template")
    source_controller_roundtable(
        store,
        process_stub=lambda *_: (0, "[]"),
        reviewers=[
            dict(name="source-codex", provider="codex"),
            dict(name="source-claude", provider="claude"),
        ],
        executable_for=lambda p: p,
    )
    store.advance("publish", by="source-test")
    revision = store.publish(by="source-test", export_dir=store.workspace / "published")
    selection = [
        dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium") for b in ("C", "B")
    ]
    # Admission is canonical; intercept only the provider controller launch.
    with monkeypatch.context() as patch:
        patch.setattr(jobs.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=12345))
        job = jobs.start_worker_job(store, revision, selection, max_parallel=1, call_cap=16)
    directory = Path(job["evidenceDir"])
    manifest = jobs.read_record_json(directory / "plan/sample.json")
    page = WEB_PAGE if valid else WEB_PAGE.replace("Welcome", "Missing")
    payload = r"""
import json, subprocess, sys
from pathlib import Path
from codex_harness.core import planner_jobs as jobs, dispatch, semantic_runner
workspace, job_id, surface, valid, page, recover = json.loads(sys.argv[1])
actual_run, actual_check = dispatch.run_plan, semantic_runner.run_audit_process
def write(root, content, code):
    subprocess.run([sys.executable, '-B', '-c',
        "from pathlib import Path; import sys; "
        "Path('index.html').write_text(sys.argv[1], encoding='utf-8')",
        content], cwd=root, check=True)
    if surface == 'cli':
        (root / 'hello.py').write_text(code)
def contracts(root):
    write(root, '<!doctype html><h1>SELDON-STUB visit</h1>',
        "import sys; print('SELDON-STUB hello', file=sys.stderr); sys.exit(3)")
def build(root):
    write(root, page, "print('Hello', end='')" if valid else "print('Wrong')")
def crashing_checker(command, **kwargs):
    if json.loads(Path(command[-1]).read_text())[2] == 'validation':
        command = [sys.executable, '-B', '-c', "raise RuntimeError('checker bug')"]
    return actual_check(command, **kwargs)
def local_run(plan, **options):
    options.pop('execution_job')
    return actual_run(plan, **options, fake_workers={'C': contracts, 'B': build})
dispatch.run_plan = local_run
if recover:
    semantic_runner.run_audit_process = crashing_checker
result = jobs.execute_worker_job(workspace, job_id)
assert result['status'] == 'completed', result
"""
    actual_process = audit_process.run_audit_process

    def local_controller(command, **options):
        return actual_process(
            [
                sys.executable,
                "-P",
                "-B",
                "-c",
                payload,
                json.dumps([str(store.workspace), revision, surface, valid, page, recover]),
            ],
            **options,
        )

    with monkeypatch.context() as patch:
        patch.setattr(audit_process, "run_audit_process", local_controller)
        planner_recovery.monitor(store.workspace, revision)
    run = jobs.worker_job_status(store.workspace, revision)["run"]
    if not recover:
        assert run["semanticValidation"]["state"] == "PASS", run
        assert (store.workspace / "index.html").read_text(encoding="utf-8") == page
        return
    assert run["semanticValidation"]["state"] == "ERROR", run
    server = PlannerServer(store.workspace)
    try:
        if valid:
            readmit = tool(
                server,
                "harness_recovery",
                mode="resolve",
                job_id=revision,
                choice="readmit",
                reason="Earlier recovery released hold without launching",
            )
            assert not readmit["isError"], readmit
            assert not answer(server, readmit["structuredContent"], "yes")["isError"]
        before = snapshot(directory)
        inspected = tool(server, "harness_recovery", mode="inspect", job_id=revision)
        assert snapshot(directory) == before
        proof = inspected["structuredContent"]
        assert "revalidate" in proof["options"], inspected
        assert not (store.workspace / "index.html").exists()
        offered = tool(
            server,
            "harness_recovery",
            mode="resolve",
            job_id=revision,
            choice="revalidate",
            reason="Checker repaired; retain Build",
        )
        assert not offered["isError"], offered
        if valid:
            record = jobs.read_record_json(Path(proof["revalidation"]["runPath"]))
            candidate = Path(record["beads"]["B"]["integrated"]["root"]) / "index.html"
            original = candidate.read_bytes()
            candidate.write_bytes(original + b"\nchanged after recovery card")
            stale = answer(server, offered["structuredContent"], "yes")
            assert stale["isError"] and "stale" in str(stale), stale
            candidate.write_bytes(original)
            offered = tool(
                server,
                "harness_recovery",
                mode="resolve",
                job_id=revision,
                choice="revalidate",
                reason="Fresh consent for the unchanged retained candidate",
            )
            assert not offered["isError"], offered

        def forbidden(*a, **k):
            pytest.fail("Recovery attempted a worker launch")

        with monkeypatch.context() as patch:
            patch.setattr(jobs, "start_worker_job", forbidden)
            patch.setattr(dispatch, "_attempt_bead", forbidden)
            completed = answer(server, offered["structuredContent"], "yes")
        assert not completed["isError"], completed
        result = completed["structuredContent"]["disposition"]["validation"]
        assert result["state"] == ("PASS" if valid else "FAIL"), result
        assert (
            planner_recovery.inspect(store.workspace, revision)["spentCalls"] == proof["spentCalls"]
        )
        if valid:
            assert (store.workspace / "index.html").read_text(encoding="utf-8") == page
            observed = semantic_runner.run(
                manifest["semantic"]["scenarios"],
                store.workspace,
                manifest["semantic"]["targets"],
                "validation",
            )
            assert all(r["state"] == "PASS" for r in observed), observed
        else:
            assert not (store.workspace / "index.html").exists()
            assert result["results"][0]["reason"]
            assert "revalidate" in planner_recovery.inspect(store.workspace, revision)["options"]
        assert answer(server, offered["structuredContent"], "yes")["isError"]
    finally:
        server.close()
