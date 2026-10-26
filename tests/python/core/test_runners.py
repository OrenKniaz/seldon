"""Standalone runner parity, actual exit signals and safe argv construction."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from tests.python.core import test_test_audit

from codex_harness.core import audit_process, dispatch, runners
from codex_harness.core.dispatch import STATUS_MOCK_ONLY, _claim_inputs, _commit, _git, run_plan
from codex_harness.core.graph import PlanError, load_plan
from codex_harness.core.graph import test_dir as protected_test_dir
from codex_harness.core.runners import resolve_runner
from codex_harness.core.spine import (
    Spine,
    compile_manifest,
    load_spine,
    requirement_files_revision,
)
from codex_harness.core.test_audit import audit_revised_tests
from codex_harness.core.worker_packet import build_packet

TIPSPLIT = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"
VARIATION = Path(__file__).parent / "fixtures/handoff_variation"
COMMAND = Path(__file__).parent / "fixtures/command_runner"
prospective = test_test_audit.prospective


@pytest.mark.skipif(sys.platform != "win32", reason="Windows held-Job containment only")
def test_windows_job_contains_canonical_proof_descendant(prospective):
    """Catch descendants surviving proof return; prior runner checks wait only the direct child."""
    root = prospective["template"]
    pulse, done = root / "pulse", root / "done"
    child = (
        "import sys,time;from pathlib import Path;p=Path(sys.argv[1]);end=time.monotonic()+2\n"
        "while time.monotonic()<end:\n"
        " with p.open('ab') as f:f.write(b'x')\n"
        " time.sleep(.03)\n"
        "Path(sys.argv[2]).write_text('finished')\n"
    )
    (root / "app.py").write_text("def value(): return 42\n")
    (root / "tests/test_app.py").write_text(
        "import subprocess,sys,time,unittest\nfrom pathlib import Path\nfrom app import value\n"
        f"PULSE=Path({str(pulse)!r})\nclass Proof(unittest.TestCase):\n"
        " def test_value(self):\n"
        f"  subprocess.Popen([sys.executable,'-I','-S','-c',{child!r},"
        f"{str(pulse)!r},{str(done)!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "  end=time.monotonic()+3\n"
        "  while not PULSE.exists() and time.monotonic()<end:time.sleep(.01)\n"
        "  self.assertTrue(PULSE.exists())\n  self.assertEqual(value(),42)\n"
    )
    proof = None
    try:
        manifest = compile_manifest(prospective["spine"], root)
        proof = runners.validate_proof(root, manifest, "test_app.py", owned_files=("app.py",))
        assert proof["passed"], proof
        before = pulse.stat().st_size
        time.sleep(0.15)
        assert pulse.stat().st_size == before, "Descendant still executing after proof return"
        control = proof["processControl"]
        assert control["membershipVerifiedBeforePayload"]
        assert control["quiescenceVerified"] and control["activeProcessesAfterTeardown"] == 0
    finally:
        if proof is None or not proof.get("processControl", {}).get("quiescenceVerified"):
            deadline = time.monotonic() + 5
            while not done.exists() and time.monotonic() < deadline:
                time.sleep(0.02)  # Only a finite owned fixture; never kill by PID.


@pytest.mark.skipif(sys.platform != "win32", reason="Windows held-Job containment only")
def test_windows_job_preserves_payload_io_exit_and_drains_inherited_pipe(tmp_path):
    """Catch wrapper substitutions and held-stdout hangs; canonical proof detaches child pipes."""
    folder = tmp_path / "space folder"
    folder.mkdir()
    source = folder / "stdin.txt"
    source.write_bytes(b"original-stdin\n")
    argument = 'a b"c'
    script = (
        "import json,os,subprocess,sys\n"
        "print(json.dumps(dict(stdin=sys.stdin.read(),argument=sys.argv[1],cwd=os.getcwd(),"
        "fixtureEnv=os.environ['AUDIT_FIXTURE'],executable=sys.executable)),flush=True)\n"
        "sys.stderr.write('original-stderr\\n');sys.stderr.flush()\n"
        "subprocess.Popen([sys.executable,'-I','-S','-c','import time;time.sleep(5)'],"
        "stdin=subprocess.DEVNULL)\n"
        "sys.exit(23)\n"
    )
    command = [sys.executable, "-c", script, argument]
    started_at = time.monotonic()
    with source.open("rb") as stdin:
        result = audit_process.run_audit_process(
            command,
            cwd=folder,
            env={**os.environ, "AUDIT_FIXTURE": "payload-only"},
            stdin=stdin,
            timeout=3,
        )
    assert time.monotonic() - started_at < 3
    assert result.args == command and result.returncode == 23
    observed = json.loads(result.stdout)
    assert observed["stdin"] == "original-stdin\n" and observed["argument"] == argument
    assert Path(observed["cwd"]) == folder and observed["fixtureEnv"] == "payload-only"
    assert Path(observed["executable"]) == Path(sys.executable)
    assert result.stderr == "original-stderr\n"
    assert result.audit_control["quiescenceVerified"]
    assert result.audit_control["activeProcessesAfterTeardown"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows held-Job containment only")
def test_windows_job_timeout_drains_descendant_held_stdout(tmp_path):
    """Catch timeout leaving pipes/descendants alive; completion does not exercise this path."""
    script = (
        "import subprocess,sys,time\nprint('payload-ready',flush=True)\n"
        "subprocess.Popen([sys.executable,'-I','-S','-c','import time;time.sleep(5)'])\n"
        "time.sleep(5)\n"
    )
    started_at = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        audit_process.run_audit_process([sys.executable, "-c", script], cwd=tmp_path, timeout=1)
    assert time.monotonic() - started_at < 4
    assert "payload-ready" in raised.value.stdout
    assert raised.value.audit_control["quiescenceVerified"]
    assert raised.value.audit_control["activeProcessesAfterTeardown"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows held-Job containment only")
def test_windows_bootstrap_delayed_ready_within_caller_budget_runs_payload(tmp_path, monkeypatch):
    """Catch the five-second false refusal; ordinary success has no startup delay."""
    delayed = audit_process._BOOTSTRAP.replace(
        "send({'kind': 'ready'})", "import time;time.sleep(6)\nsend({'kind': 'ready'})"
    )
    monkeypatch.setattr(audit_process, "_BOOTSTRAP", delayed)
    result = audit_process.run_audit_process(
        [sys.executable, "-B", "-c", "print('delayed-ready-payload')"],
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0 and result.stdout == "delayed-ready-payload\n"
    assert result.stderr == ""
    assert result.audit_control["membershipVerifiedBeforePayload"]
    assert result.audit_control["payloadReleased"] is True
    assert result.audit_control["quiescenceVerified"]
    assert result.audit_control["activeProcessesAfterTeardown"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows held-Job containment only")
@pytest.mark.parametrize("query_refused", [False, True])
def test_windows_assigned_unreleased_eof_observes_job_zero_before_close(
    tmp_path, monkeypatch, query_refused
):
    """Catch lost Job evidence or fallback after EOF/query refusal; payload timeout terminates."""
    events = []
    jobs = []
    real_init = audit_process._WindowsJob.__init__
    real_active = audit_process._WindowsJob.active
    real_dumps = audit_process.json.dumps

    class CloseObservation:
        def __init__(self, api, job):
            self.api, self.job = api, job

        def __getattr__(self, name):
            return getattr(self.api, name)

        def CloseHandle(self, handle):
            if handle == self.job.handle:
                events.append(("close-job", None))
            return self.api.CloseHandle(handle)

        def TerminateJobObject(self, *args):
            pytest.fail("Assigned unreleased EOF must finish naturally without termination")

    def observe_init(job):
        real_init(job)
        job.api = CloseObservation(job.api, job)
        jobs.append(job)

    def observe_active(job):
        if query_refused:
            error = PermissionError(5, "Controlled SOURCE-ONLY Job query refusal")
            error.audit_native_failure = "QueryInformationJobObject"
            raise error
        active = real_active(job)  # Delegate to the actual owned Job; never supply a count.
        events.append(("query-job", active))
        return active

    def refuse_config(value, *args, **kwargs):
        if isinstance(value, dict) and set(value) == {"args", "cwd", "env"}:
            raise OSError("Controlled pre-delivery serialization failure")
        return real_dumps(value, *args, **kwargs)

    marker = tmp_path / "payload-must-not-run"
    monkeypatch.setattr(audit_process._WindowsJob, "__init__", observe_init)
    monkeypatch.setattr(audit_process._WindowsJob, "active", observe_active)
    monkeypatch.setattr(audit_process.json, "dumps", refuse_config)
    with pytest.raises(OSError, match="Controlled pre-delivery") as raised:
        audit_process.run_audit_process(
            [sys.executable, "-B", "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"],
            cwd=tmp_path,
        )
    assert not marker.exists()
    control = raised.value.audit_control
    assert control["membershipVerifiedBeforePayload"] and control["payloadReleased"] is False
    if query_refused:
        try:
            assert events == [], "Refused query must not close/terminate the Job as fallback"
            assert control["quiescenceVerified"] is False
            assert control["activeProcessesAfterTeardown"] is None
            assert control["jobHandleRetained"] and control["nativeFailure"]
            assert control["nativeFailureOperation"] == "QueryInformationJobObject"
            assert "Controlled SOURCE-ONLY" in control["unreleasedTeardownWarning"]
        finally:
            # Remove only the controlled test fault. Query this fixture's real held
            # Job to zero before releasing its handle; never perform a fallback kill.
            deadline = time.monotonic() + 5
            while real_active(jobs[0]) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert real_active(jobs[0]) == 0
            assert jobs[0].api.CloseHandle(jobs[0].handle)
        return
    assert events[-2:] == [("query-job", 0), ("close-job", None)]
    assert control["activeProcessesAfterTeardown"] == 0
    assert not control.get("jobHandleRetained")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows held-Job containment only")
def test_windows_bootstrap_failure_reaches_proof_control_with_bounded_diagnostics(
    prospective, monkeypatch
):
    """Catch lost pre-gate diagnostics; success/timeout tests run after bootstrap readiness."""
    root = prospective["template"]
    marker = root / "payload-must-not-run"
    (root / "app.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef value(): return 42\n"
    )
    # Execute a real finite trusted child failure; do not supply a subprocess result.
    failing = audit_process._BOOTSTRAP.replace(
        "send({'kind': 'ready'})",
        "sys.stderr.write('x' * 5000 + 'bootstrap-startup-failed');sys.stderr.flush()\n"
        "status.write(b'partial-private-status');status.close();sys.exit(78)",
    )
    monkeypatch.setattr(audit_process, "_BOOTSTRAP", failing)
    manifest = compile_manifest(prospective["spine"], root)
    result = runners.validate_proof(root, manifest, "test_app.py", owned_files=("app.py",))
    assert not result["passed"]
    assert "did not reach its private gate" in result["output"]
    assert not marker.exists()
    control = result["processControl"]
    assert control["membershipVerifiedBeforePayload"] and control["payloadReleased"] is False
    assert control["quiescenceVerified"] is True
    assert control["activeProcessesAfterTeardown"] == 0
    assert not control.get("jobHandleRetained")
    assert 0 <= control["bootstrapReadyWaitSeconds"] < 5
    assert len(control["bootstrapStderrTail"]) == 4096
    assert control["bootstrapStderrTail"].endswith("bootstrap-startup-failed")
    assert control["bootstrapStatusTail"] == "partial-private-status"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows held-Job containment only")
def test_windows_job_assignment_refusal_never_releases_payload(tmp_path, monkeypatch):
    """Catch fallback launch/kill after refused assignment; membership success misses refusal."""
    marker = tmp_path / "must-not-run"

    def refused(_job, _process):
        raise PermissionError(5, "Controlled SOURCE-ONLY assignment refusal")

    def forbidden_teardown(*args):
        pytest.fail("Unassigned bootstrap must use gate EOF, not a termination fallback")

    monkeypatch.setattr(audit_process._WindowsJob, "assign", refused)
    monkeypatch.setattr(audit_process._WindowsJob, "finish", forbidden_teardown)
    with pytest.raises(PermissionError, match="Controlled") as raised:
        audit_process.run_audit_process(
            [sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"],
            cwd=tmp_path,
            timeout=3,
        )
    assert not marker.exists()
    control = raised.value.audit_control
    assert not control["payloadReleased"] and not control["membershipVerifiedBeforePayload"]
    assert not control["quiescenceVerified"] and not control.get("jobHandleRetained")


def _command(**fields):
    return {
        "verification": {
            "runner": "command",
            "argv": [sys.executable, "run_proof.py", "{proof}"],
            "cwd": ".",
            "pass": {"exit": 0},
            "testDir": "proofs",
            "proofGlob": "*.proof",
            **fields,
        }
    }


@pytest.mark.parametrize("fixture,directory", [(TIPSPLIT, "tests"), (VARIATION, "checks")])
def test_unittest_matches_real_compiled_fixture_commands(fixture, directory):
    manifest = compile_manifest(load_spine(fixture / "spine.json"), fixture / "template")
    runner = resolve_runner(manifest)
    assert runner.name == "unittest" and runner.test_dir == directory and runner.cwd == "."
    proofs = [bead["proofModule"] for bead in manifest["beads"].values()]
    proofs.append(manifest["integration"]["proofModule"])
    for proof in proofs:
        current = [sys.executable, *manifest["validation"], proof]
        assert runner.argv_for(proof) == current
        assert subprocess.list2cmdline(runner.argv_for(proof)).encode() == (
            subprocess.list2cmdline(current).encode()
        )
    assert {path.name for path in runner.proof_paths(fixture / "template")} == set(proofs)


def test_unittest_legacy_list_and_explicit_object_are_identical():
    prefix = ["-B", "-m", "unittest", "discover", "-s", "checks", "-p"]
    legacy = resolve_runner({"validation": prefix})
    explicit = resolve_runner({"validation": {"runner": "unittest", "argv": prefix}})
    assert legacy == explicit
    assert legacy.argv_for("proof_*.py") == [sys.executable, *prefix, "proof_*.py"]
    assert resolve_runner({"validation": ["-m", "unittest"]}).test_dir == "tests"


@pytest.mark.parametrize(
    "proof,exit_code,passed", [("pass.proof", 0, True), ("fail.proof", 1, False)]
)
def test_command_executes_real_script_and_ignores_fake_pass_report(
    tmp_path, proof, exit_code, passed
):
    shutil.copytree(COMMAND, tmp_path, dirs_exist_ok=True)
    runner = resolve_runner(_command())
    assert [path.name for path in runner.proof_paths(tmp_path)] == ["fail.proof", "pass.proof"]
    # A small, controller-owned environment; no provider process or python -c.
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
    result = subprocess.run(
        runner.argv_for(f"proofs/{proof}"),
        cwd=tmp_path / runner.cwd,
        env=env,
        shell=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == exit_code, result.stderr
    assert json.loads((tmp_path / "report.json").read_text())["passed"] is True
    assert runner.passed(result.returncode) is passed
    assert runner.passed(None) is False


def test_command_substitutes_one_argument_and_resolves_executable(monkeypatch):
    monkeypatch.setattr(runners.shutil, "which", lambda executable: "C:/trusted/tool.EXE")
    runner = resolve_runner(_command(argv=["tool", "--", "{proof}"], cwd="tools"))
    assert runner.argv_for("proofs/a & b.proof") == [
        "C:/trusted/tool.EXE",
        "--",
        "../proofs/a & b.proof",
    ]


def test_missing_tool_is_rejected(monkeypatch):
    monkeypatch.setattr(runners.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="not found"):
        resolve_runner(_command()).argv_for("proofs/pass.proof")


@pytest.mark.parametrize("suffix", [".cmd", ".BAT"])
@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd shim execution")
def test_batch_shim_uses_bounded_cmd_command_line(monkeypatch, suffix):
    executable = f"C:/trusted tools/tool{suffix}"
    monkeypatch.setattr(runners.shutil, "which", lambda name: executable)
    runner = resolve_runner(_command(argv=["tool", "{proof}"]))
    inner = subprocess.list2cmdline([executable, "proofs/pass.proof"])
    assert runner.argv_for("proofs/pass.proof") == f'cmd.exe /d /s /c "{inner}"'


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd shim execution")
@pytest.mark.parametrize("suffix", [".cmd", ".bat"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_batch_shim_with_spaces_round_trips_arguments(tmp_path, suffix, exit_code):
    shim = tmp_path / "tool shims with spaces" / f"verify tool{suffix}"
    shim.parent.mkdir()
    shim.write_text(
        "@echo off\necho [%~1]\necho [%~2]\necho [%~3]\nexit /b %~4\n",
        encoding="utf-8",
    )
    runner = resolve_runner(
        _command(argv=[str(shim), "first argument", "{proof}", "last argument", str(exit_code)])
    )
    result = subprocess.run(
        runner.argv_for("proofs/proof with spaces.proof"),
        cwd=tmp_path,
        shell=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == exit_code, result.stderr
    assert result.stdout.splitlines() == [
        "[first argument]",
        "[proofs/proof with spaces.proof]",
        "[last argument]",
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd shim execution")
@pytest.mark.parametrize("character", runners.CMD_META)
def test_batch_shim_rejects_metacharacters_before_launch(monkeypatch, character):
    monkeypatch.setattr(runners.shutil, "which", lambda name: None)
    runner = resolve_runner(_command(argv=["tool", "bad" + character, "{proof}"]))
    monkeypatch.setattr(runners.shutil, "which", lambda name: "C:/trusted tools/tool.cmd")
    with pytest.raises(PlanError, match="metacharacters"):
        runner.argv_for("proofs/pass.proof")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd shim execution")
def test_batch_shim_rejects_quote_in_resolved_path(monkeypatch):
    monkeypatch.setattr(runners.shutil, "which", lambda name: 'C:/trusted" tools/tool.cmd')
    with pytest.raises(PlanError, match="metacharacters"):
        resolve_runner(_command(argv=["tool", "{proof}"]))
    monkeypatch.setattr(runners.shutil, "which", lambda name: None)
    runner = resolve_runner(_command(argv=["tool", "{proof}"]))
    monkeypatch.setattr(runners.shutil, "which", lambda name: 'C:/trusted" tools/tool.cmd')
    with pytest.raises(PlanError, match="metacharacters"):
        runner.argv_for("proofs/pass.proof")


@pytest.mark.parametrize(
    "fields",
    [
        {"argv": "tool {proof}"},
        {"argv": ["tool"]},
        {"argv": ["tool", "prefix{proof}"]},
        {"argv": ["{proof}", "tool"]},
        {"argv": ["tool", "{proof}", "{proof}"]},
        {"argv": ["tool", "{proof}", "{report}"]},
        {"pass": {"exit": 1}},
        {"pass": {"exit": False}},
        {"pass": {"report": "pass"}},
        {"testDir": "../proofs"},
        {"testDir": "C:\\proofs"},
        {"testDir": "."},
        {"cwd": "/outside"},
        {"proofGlob": "../*.proof"},
        {"report": "worker.json"},
        {"runner": "unknown"},
        {"listArgv": ["tool", "prefix{proof}"]},
        {"listArgv": ["{proof}"]},
        {"listArgv": ["tool", "{proof}", "{proof}"]},
        {"assertPatterns": []},
        {"assertPatterns": ["("]},
        {"importPatterns": ["from"]},
        {"importPatterns": "from (.+)"},
    ],
)
def test_command_contract_rejects_unsafe_or_unsupported_fields(fields):
    with pytest.raises(ValueError):
        resolve_runner(_command(**fields))


def test_listing_uses_same_proof_resolution_and_safe_argv(monkeypatch):
    monkeypatch.setattr(runners.shutil, "which", lambda name: "C:/trusted/tool.exe")
    runner = resolve_runner(_command(listArgv=["tool", "--list", "{proof}"], cwd="tools"))
    assert runner.list_argv_for("proofs/pass.proof") == [
        "C:/trusted/tool.exe",
        "--list",
        "../proofs/pass.proof",
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd shim execution")
def test_listing_shim_rejects_metacharacters_at_compile_and_launch(monkeypatch):
    config = _command(listArgv=["list-tool", "bad&arg", "{proof}"])
    monkeypatch.setattr(runners.shutil, "which", lambda name: None)
    runner = resolve_runner(config)
    monkeypatch.setattr(runners.shutil, "which", lambda name: "C:/trusted/list-tool.cmd")
    with pytest.raises(PlanError, match="metacharacters"):
        runner.list_argv_for("proofs/pass.proof")
    with pytest.raises(PlanError, match="metacharacters"):
        resolve_runner(config)


@pytest.mark.parametrize("proof", ["outside/pass.proof", "proofs/../pass.proof", "proofs/*.proof"])
def test_command_rejects_unbound_or_nonconcrete_proofs(proof):
    with pytest.raises(ValueError, match="Proof|paths|proof"):
        resolve_runner(_command()).argv_for(proof)


def test_proof_discovery_rejects_directory_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        (repo / "proofs").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires Windows privileges")
    with pytest.raises(ValueError, match="escapes"):
        resolve_runner(_command()).proof_paths(repo)


@pytest.fixture
def command_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "get_adapter", lambda *args: pytest.fail("real provider launch"))
    root = tmp_path / "plan"
    template = root / "template"
    proofs = template / "proofs"
    proofs.mkdir(parents=True)
    shutil.copy2(COMMAND / "check_value.py", proofs / "check_value.py")
    for name in ("unit.proof", "integration.proof"):
        (proofs / name).write_text("1\n", encoding="utf-8")
    for name in ("VISION.md", "PLAN.md", "REQUIREMENTS.md", "TRACE.md"):
        (template / name).write_text("Command runner fixture.\n", encoding="utf-8")
    (template / "value.txt").write_text("0\n", encoding="utf-8")
    (template / ".gitignore").write_text(".harness/\n", encoding="utf-8")
    (root / "reference").mkdir()
    (root / "reference/value.txt").write_text("1\n", encoding="utf-8")
    verification = _command(argv=[sys.executable, "check_value.py", "{proof}"], cwd="proofs")[
        "verification"
    ]

    def node(node_id, kind, parents, **fields):
        return {
            "id": node_id,
            "kind": kind,
            "title": node_id,
            "text": "Produce value 1.",
            "parents": parents,
            "fields": fields,
        }

    spine = Spine(
        {
            "schema": "codex-harness/spine-v1",
            "name": "command-plan",
            "nodes": [
                node(
                    "V1",
                    "vision",
                    [],
                    validation=["-m", "unittest"],
                    requirementFiles=[],
                    verification=verification,
                ),
                node("F1", "feature", ["V1"]),
                node("S1", "story", ["F1"]),
                node("R1", "requirement", ["S1"]),
                node(
                    "A",
                    "bead",
                    ["R1"],
                    ownedFiles=["value.txt"],
                    dependsOn=[],
                    proofModule="proofs/unit.proof",
                    lessons=[],
                ),
                node("T1", "test", ["R1", "A"], module="proofs/unit.proof", level="unit"),
                node("TI", "test", ["R1"], module="proofs/integration.proof", level="e2e"),
            ],
        }
    )
    manifest = compile_manifest(spine, template)
    (root / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, spine, manifest


@pytest.mark.parametrize("runner_cwd", [".", "proofs"])
def test_command_compile_audit_dispatch_and_revalidation(command_plan, tmp_path, runner_cwd):
    root, spine, manifest = command_plan
    data = spine.to_dict()
    verification = next(node for node in data["nodes"] if node["id"] == "V1")["fields"][
        "verification"
    ]
    verification["cwd"] = runner_cwd
    verification["argv"][1] = "proofs/check_value.py" if runner_cwd == "." else "check_value.py"
    driver = root / "template/proofs/check_value.py"
    driver.write_text(
        "from pathlib import Path\n"
        f"assert Path.cwd() == Path(__file__).resolve().parents[{1 if runner_cwd == '.' else 0}]\n"
        + driver.read_text(),
        encoding="utf-8",
    )
    spine = Spine(data)
    manifest = compile_manifest(spine, root / "template")
    (root / "sample.json").write_text(json.dumps(manifest))
    assert manifest["verification"] == spine.node("V1")["fields"]["verification"]
    assert protected_test_dir(manifest) == "proofs"
    assert "proofs" in manifest["requirementFiles"]
    audit = audit_revised_tests(
        repo=tmp_path,
        base_ref="template",
        revised_template=root / "template",
        manifest=manifest,
        impact={"affectedBeads": ["A"]},
        oracle_dir=root / "reference",
    )
    assert audit["ok"] and audit["integrationChecked"]
    assert audit["integrationProof"]["passed"]
    assert audit["integrationProof"]["command"] == resolve_runner(manifest).argv_for(
        "proofs/integration.proof"
    )
    result = run_plan(root, work_root=tmp_path / "runs", mock_provider="reference")
    assert result["status"] == STATUS_MOCK_ONLY
    assert result["baseline"]["validation"]["A"]["passed"] is False
    assert result["beads"]["A"]["integrated"]["validation"]["passed"]
    assert result["integrationValidation"]["passed"]
    assert result["beads"]["A"]["didRunProvider"] is False
    repo = Path(result["repo"])
    assert (repo / ".harness/report.txt").read_text() == "PASS"

    # A protected proof change forces the existing evidence through revalidation.
    with (root / "template/proofs/unit.proof").open("a") as proof:
        proof.write("\n")
    revised = compile_manifest(spine, root / "template")
    assert revised["planRevision"] != manifest["planRevision"]
    (root / "sample.json").write_text(json.dumps(revised))
    resumed = run_plan(
        root,
        work_root=tmp_path / "runs",
        mock_provider="reference",
        resume_run_dir=tmp_path / "runs" / result["runId"],
    )
    assert resumed["status"] == STATUS_MOCK_ONLY
    validation = resumed["beads"]["A"]["revalidation"]["validation"]
    assert validation["passed"]
    assert validation["command"] == resolve_runner(revised).argv_for("proofs/unit.proof")


@pytest.mark.parametrize("tamper", [False, True])
def test_command_report_cannot_pass_and_worker_cannot_edit_proofs(command_plan, tmp_path, tamper):
    root, spine, manifest = command_plan
    # Protection must hold even for a hand-authored manifest omitting testDir.
    manifest["requirementFiles"] = []
    (root / "sample.json").write_text(json.dumps(manifest))

    calls = []

    def worker(repo):
        calls.append(repo)
        (repo / "value.txt").write_text("2\n")
        if tamper:
            (repo / "proofs/unit.proof").write_text("2\n")

    # A stale digest must refuse before this SOURCE-ONLY worker can run.
    with pytest.raises(ValueError, match="protected requirement files revision mismatch"):
        run_plan(root, work_root=tmp_path / "stale-runs", fake_workers={"A": worker})
    assert calls == []

    # Deliberate hand-authored scope, consistently identified BEFORE any worker.
    # The runner's testDir remains protected independently of the declared list.
    revision = requirement_files_revision(root / "template", manifest["requirementFiles"])
    manifest["requirementFilesRevision"] = revision
    manifest["planRevision"] = hashlib.sha256(
        f"{spine.plan_revision()}:{revision}".encode()
    ).hexdigest()
    (root / "sample.json").write_text(json.dumps(manifest))
    result = run_plan(root, work_root=tmp_path / "runs", fake_workers={"A": worker})
    bead = result["beads"]["A"]
    assert result["status"].startswith("plan-failed:")
    if tamper:
        assert bead["status"] == "rejected: requirement files changed"
        assert bead["guard"]["requirementFilesIntact"] is False
        assert bead["integrated"] is None
    else:
        assert bead["status"] == "integrated validation failed"
        assert bead["integrated"]["validation"]["passed"] is False
        assert (Path(bead["integrated"]["root"]) / ".harness/report.txt").read_text() == "PASS"


def test_command_identity_binds_test_directory_and_runner_contract(command_plan, tmp_path):
    root, spine, manifest = command_plan
    result = run_plan(root, work_root=tmp_path / "runs", mock_provider="reference")
    repo = Path(result["repo"])
    plan = load_plan(root)
    before = _claim_inputs(repo, plan, "A", "HEAD")["verificationIdentity"]
    (repo / "proofs/tool.lock").write_text("changed toolchain\n")
    _git(repo, "add", "proofs/tool.lock")
    _commit(repo, "change protected toolchain")
    after = _claim_inputs(repo, plan, "A", "HEAD")["verificationIdentity"]
    assert before != after
    data = spine.to_dict()
    next(node for node in data["nodes"] if node["id"] == "V1")["fields"]["verification"][
        "singleTest"
    ] = ["tool", "{test}"]
    revised = compile_manifest(Spine(data), root / "template")
    assert revised["planRevision"] != manifest["planRevision"]
    (root / "sample.json").write_text(json.dumps(revised))
    assert _claim_inputs(repo, load_plan(root), "A", "HEAD")["verificationIdentity"] != after


@pytest.mark.parametrize(
    "field,value",
    [
        ("listArgv", ["tool", "--list", "{proof}"]),
        ("assertPatterns", [r"\bassert\("]),
        ("importPatterns", [r"from ['\"]([^'\"]+)['\"]"]),
    ],
)
def test_prospective_runner_fields_are_part_of_plan_revision(command_plan, field, value):
    root, spine, manifest = command_plan
    data = spine.to_dict()
    config = next(node for node in data["nodes"] if node["kind"] == "vision")["fields"][
        "verification"
    ]
    config[field] = value
    revised = compile_manifest(Spine(data), root / "template")
    assert revised["planRevision"] != manifest["planRevision"]
    assert revised["verification"][field] == value


def test_command_packet_exact_argv_and_optional_single_test(command_plan):
    root, _, manifest = command_plan
    plan = load_plan(root)
    packet = build_packet(plan, plan.beads["A"], root / "template", None)
    assert resolve_runner(plan).worker_command("proofs/unit.proof") in packet
    assert "worktree-root-relative directory `proofs`" in packet
    assert "unittest" not in packet and "Single-test" not in packet
    manifest["verification"]["singleTest"] = ["tool", "{proof}", "{test}"]
    (root / "sample.json").write_text(json.dumps(manifest))
    plan = load_plan(root)
    packet = build_packet(plan, plan.beads["A"], root / "template", None)
    assert 'Single-test argv template: ["tool", "{proof}", "{test}"]' in packet


def test_command_root_cwd_packet_is_unambiguous(command_plan):
    root, _, manifest = command_plan
    manifest["verification"]["cwd"] = "."
    (root / "sample.json").write_text(json.dumps(manifest))
    plan = load_plan(root)
    packet = build_packet(plan, plan.beads["A"], root / "template", None)
    assert "Run from your current directory (the worktree root)" in packet
    assert "cwd: .." not in packet


@pytest.mark.parametrize("provider", ["codex", "claude", "copilot"])
def test_command_packet_and_allow_rules_share_portable_command(command_plan, monkeypatch, provider):
    root, _, manifest = command_plan
    manifest["verification"]["cwd"] = "."
    (root / "sample.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(runners.shutil, "which", lambda name: r"C:\trusted\tool.exe")
    plan = load_plan(root)
    packet = build_packet(plan, plan.beads["A"], root / "template", None, provider)
    command = "C:/trusted/tool.exe check_value.py proofs/unit.proof"
    assert command in packet.split("## 5. How you are verified\n")[1].splitlines()
    assert (
        "Run exactly this command, unchanged, from your current directory. "
        "Do not cd, do not use absolute test paths, do not add flags."
    ) in packet
    rules = dispatch._validation_tool_rules(provider, manifest["beads"]["A"], plan)
    expected = {
        "claude": (f"Bash({command})", f"PowerShell({command})"),
        "copilot": (f"shell({command})",),
        "codex": (),
    }
    assert rules == expected[provider]
    # Display normalization must not change the argv used by the controller.
    assert resolve_runner(plan).argv_for("proofs/unit.proof")[0] == r"C:\trusted\tool.exe"


@pytest.mark.parametrize(
    "field,character",
    [
        *(("argv", character) for character in runners.CMD_META),
        *((field, "&") for field in ("testDir", "proofGlob", "cwd", "proofModule", "singleTest")),
    ],
)
def test_batch_metacharacters_are_plan_errors_at_compile(
    command_plan, monkeypatch, character, field
):
    root, spine, _ = command_plan
    data = spine.to_dict()
    config = next(node for node in data["nodes"] if node["id"] == "V1")["fields"]["verification"]
    config["argv"][0] = "tool"
    monkeypatch.setattr(runners.shutil, "which", lambda name: "C:/trusted/tool.cmd")
    if field == "argv":
        config["argv"].insert(1, "bad" + character)
    elif field == "proofModule":
        next(node for node in data["nodes"] if node["id"] == "A")["fields"]["proofModule"] = (
            "proofs/bad" + character + ".proof"
        )
    elif field == "singleTest":
        config[field] = ["tool", "{test}", "bad" + character]
    else:
        config[field] += character
    with pytest.raises(PlanError, match="metacharacters|Proof|proof"):
        compile_manifest(Spine(data), root / "template")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd shim execution")
def test_command_windows_shim_compile_audit_dispatch(command_plan, tmp_path):
    root, spine, _ = command_plan
    shim = tmp_path / "tool shims" / "verify.cmd"
    shim.parent.mkdir()
    shim.write_text(f'@echo off\n"{sys.executable}" %*\n', encoding="utf-8")
    data = spine.to_dict()
    next(node for node in data["nodes"] if node["id"] == "V1")["fields"]["verification"]["argv"][
        0
    ] = str(shim)
    manifest = compile_manifest(Spine(data), root / "template")
    command = resolve_runner(manifest).argv_for("proofs/unit.proof")
    assert isinstance(command, str) and command.startswith('cmd.exe /d /s /c "')
    (root / "sample.json").write_text(json.dumps(manifest))
    audit = audit_revised_tests(
        repo=tmp_path,
        base_ref="template",
        revised_template=root / "template",
        manifest=manifest,
        impact={"affectedBeads": ["A"]},
        oracle_dir=root / "reference",
    )
    assert audit["ok"], audit
    result = run_plan(root, work_root=tmp_path / "runs", mock_provider="reference")
    assert result["status"] == STATUS_MOCK_ONLY
    assert result["beads"]["A"]["integrated"]["validation"]["command"] == command
