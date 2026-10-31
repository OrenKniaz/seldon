"""Timeout exit observations gate retries; bounded local children are not provider proof.

The existing publication-failure owned-stop test covers a different contract.
This regression reaches timeout cleanup with a conclusive audit and an exact
process original, so neither guard can mask an unsafe replacement launch.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
from tests.python.core.execution_support import source_execution_context
from tests.python.core.test_dispatch import _bind_protected, _tiny_audited_plan
from tests.python.core.test_dispatch_cancel import source_only_packet_renderer

from codex_harness.core import attempt_provenance, dispatch
from codex_harness.core.provider_launch import ProviderNormalLaunch
from codex_harness.core.record_io import read_record_json


def _timeout_run(tmp_path, monkeypatch, *, refuse_stop):
    plan = _tiny_audited_plan(tmp_path, dependent=True)
    _bind_protected(plan)
    children, stops = [], []
    popen, run = subprocess.Popen, subprocess.run
    kill = subprocess.Popen.kill

    class LocalAdapter:
        def provider_normal_command(self, **kwargs):
            return ProviderNormalLaunch(
                cli="codex",
                command=(
                    sys.executable,
                    "-B",
                    "-c",
                    "# SOURCE-ONLY local process fixture\n"
                    "from pathlib import Path; Path('A.py').write_bytes(b'VALUE = 1\\n'); "
                    'import time; print(\'{"type":"turn.completed","isMocked":true}\', '
                    "flush=True); time.sleep(12)",
                ),
                protections=(
                    "Bounded local Python child; explicit mocked telemetry; no provider.",
                ),
            )

    def refused(child):
        stops.append(child.pid)
        assert len(stops) == 1, "timeout stop must only be attempted once"
        raise PermissionError("synthetic one-shot timeout stop refusal")

    def tracked_popen(command, *args, **kwargs):
        child = popen(command, *args, **kwargs)
        if list(command[:3]) == [sys.executable, "-B", "-c"] and "time.sleep(12)" in command[3]:
            children.append(child)
            if refuse_stop and sys.platform != "win32":
                monkeypatch.setattr(child, "kill", lambda: refused(child))
        return child

    def timeout_stop(command, *args, **kwargs):
        if refuse_stop and command[:1] == ["taskkill"]:
            assert command == ["taskkill", "/PID", str(children[0].pid), "/T", "/F"]
            refused(children[0])
        return run(command, *args, **kwargs)

    monkeypatch.setattr(dispatch, "get_adapter", lambda name: LocalAdapter())
    source_only_packet_renderer(monkeypatch)
    context = source_execution_context(tmp_path / "canonical", plan, timeout=0.5)
    plan = context["plan"]
    monkeypatch.setattr(subprocess, "Popen", tracked_popen)
    monkeypatch.setattr(subprocess, "run", timeout_stop)
    try:
        first = dispatch.run_plan(
            plan,
            work_root=context["runs"],
            **context["kwargs"],
        )
        assert len(children) == 1
        a = first["beads"]["A"]
        assert a["attempt"]["status"] == "timed-out"
        assert a["attempt"]["cleanup"]["controllerProcessExited"] is (not refuse_stop)
        assert a["permissionGuard"]["requiresAuditHold"] is False, "audit must not mask retry"
        assert not first["didExecuteRealProvider"]
        assert a["integrated"] is None and not a["candidate"]["validated"]
        assert not first["beads"]["B"]["didRunProvider"]
        attempt = Path(a["evidenceDir"]) / "attempt"
        request = read_record_json(attempt / "request.json")
        original = read_record_json(attempt / attempt_provenance.FILENAME)
        identity = original["identity"]
        assert identity["runId"] == first["runId"] and identity["bead"] == "A"
        assert identity["planRevision"] == first["planRevision"]
        assert identity["processId"] == children[0].pid
        assert identity["attemptDirectory"] == str(attempt.resolve())
        assert (
            identity["packetUtf8Sha256"]
            == hashlib.sha256(request["prompt"].encode("utf-8")).hexdigest()
        )
        yield plan, first, children[0], stops
    finally:
        for child in children:
            try:
                child.wait(timeout=15)  # Natural bounded exit, never infer saved stop evidence.
            except subprocess.TimeoutExpired:
                kill(child)  # Emergency cleanup only for this test's own disposable child.
                child.wait(timeout=5)


def _resume(plan, first, **flags):
    run = Path(first["beads"]["A"]["evidenceDir"]).parents[1]
    return dispatch.run_plan(
        plan,
        work_root=run.parent / "unused",
        resume_run_dir=run,
        fake_workers={
            bead: lambda cwd: pytest.fail("unobserved timeout exit allowed replacement worker")
            for bead in ("A", "B")
        },
        **flags,
    )


@pytest.mark.parametrize("explicit_retry", [True])
def test_unobserved_timeout_preserves_original_even_after_natural_exit(
    tmp_path, monkeypatch, explicit_retry
):
    fixture = _timeout_run(tmp_path, monkeypatch, refuse_stop=True)
    try:
        plan, first, child, stops = next(fixture)
        a = first["beads"]["A"]
        attempt = Path(a["evidenceDir"]) / "attempt"
        saved = {path: path.read_bytes() for path in attempt.rglob("*") if path.is_file()}
        source = Path(a["attempt"]["cwd"]) / "A.py"
        source_bytes = source.read_bytes()
        run = Path(a["evidenceDir"]).parents[1]
        namespaces = set(run.rglob("retry-*"))
        head = dispatch._git(Path(first["repo"]), "rev-parse", "HEAD")
        flags = {"retry_blocked": "A", "relaunch_ambiguous": True} if explicit_retry else {}
        assert child.poll() is None, "must resume while original local child still runs"
        for after_exit in (False, True):
            if after_exit:
                assert child.wait(timeout=15) == 0
            resumed = _resume(plan, first, **flags)
            assert resumed["beads"]["A"]["attempt"] == a["attempt"]
            assert resumed["beads"]["A"]["evidenceDir"] == a["evidenceDir"]
            assert resumed["beads"]["A"]["integrated"] is None
            assert not resumed["beads"]["A"]["candidate"]["validated"]
            assert not resumed["beads"]["B"]["didRunProvider"]
            assert resumed["resumeSummary"]["redispatched"] == []
            assert "observed controller exit" in resumed["resumeSummary"]["retained"][0]["reason"]
            assert {
                path: path.read_bytes() for path in attempt.rglob("*") if path.is_file()
            } == saved
            assert source.read_bytes() == source_bytes == b"VALUE = 1\n"
            assert set(run.rglob("retry-*")) == namespaces
            assert dispatch._git(Path(first["repo"]), "rev-parse", "HEAD") == head
            assert len(stops) == 1
    finally:
        fixture.close()


def test_observed_timeout_exit_preserves_ordinary_retry(tmp_path, monkeypatch):
    fixture = _timeout_run(tmp_path, monkeypatch, refuse_stop=False)
    try:
        plan, first, child, stops = next(fixture)
        a = first["beads"]["A"]
        assert child.poll() is not None and stops == []
        run = Path(a["evidenceDir"]).parents[1]
        resumed = dispatch.run_plan(
            plan,
            work_root=tmp_path / "unused",
            resume_run_dir=run,
            fake_workers={
                bead: lambda cwd, bead=bead: (cwd / f"{bead}.py").write_text("VALUE = 1\n")
                for bead in ("A", "B")
            },
        )
        replacement = resumed["beads"]["A"]
        assert resumed["resumeSummary"]["redispatched"] == ["A", "B"]
        assert replacement["evidenceDir"] != a["evidenceDir"]
        assert replacement["superseded"][0]["attempt"] == a["attempt"]
        assert not resumed["didExecuteRealProvider"]
    finally:
        fixture.close()
