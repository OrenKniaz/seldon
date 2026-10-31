"""Real worker launch/assessment using a private harmless CLI executable, no provider."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from tests.python.core.test_acceptance import public_assessment_repair_fixture
from tests.python.core.test_dispatch_cancel import local_attempt

from codex_harness.core import dispatch, roundtable
from codex_harness.core.provider_launch import get_adapter


@pytest.mark.skipif(sys.platform != "win32", reason="Windows executable/command-line regression")
def test_path_only_worker_original_reaches_assessment_with_exact_packet(tmp_path, monkeypatch):
    # Existing assessment fixtures replace the adapter with absolute Python argv;
    # they miss PATH identity drift and actual Windows transport of admitted packets.
    which, resolver = shutil.which, roundtable._executable
    capture = tmp_path / "actual-stdin.bin"
    binary = tmp_path / "private-cli"
    binary.mkdir()
    executable = binary / "codex.exe"
    shutil.copy2(sys.executable, executable)
    (binary / "pyvenv.cfg").write_text(
        f"home = {Path(sys._base_executable).parent}\ninclude-system-site-packages = false\n"
    )

    def setup(worker, workspace):
        # The copied Python launcher executes this local SOURCE-ONLY child in lieu
        # of opening a provider. The real codex adapter still builds every argv flag.
        (binary / "sitecustomize.py").write_text(
            "import sys\nif sys.argv[0] == 'exec':\n"
            "    import os,runpy\n    from pathlib import Path\n"
            f"    Path({str(capture)!r}).write_bytes(sys.stdin.buffer.read())\n"
            f"    runpy.run_path({str(worker)!r}, run_name='__main__')\n"
            "    sys.stdout.flush(); sys.stderr.flush(); os._exit(0)\n"
        )
        monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
        monkeypatch.setenv(
            "PYTHONPATH", str(binary) + os.pathsep + os.environ.get("PYTHONPATH", "")
        )
        # Do not select an installed bundled provider or the helper's which double.
        monkeypatch.setenv("LOCALAPPDATA", str(binary))
        monkeypatch.setattr(shutil, "which", which)
        monkeypatch.setattr(roundtable, "_executable", resolver)
        assert Path(which("codex")) == executable
        assert not (workspace / "codex").exists()
        return get_adapter

    store, _server, tool, _calls, finding, _current, repo, _reviewers, _evidence = (
        public_assessment_repair_fixture(
            tmp_path,
            monkeypatch,
            worker_setup=setup,
            worker_context=("# Context: example input λ and output for the owned parser.\n" * 400),
        )
    )
    try:
        status = tool(
            "harness_worker_status",
            {"job_id": store._meta()["published_revision"], "include_detail": True},
        )
        run = json.loads(Path(status["run"]["recordPath"]).read_text())
        attempt = Path(run["beads"]["A"]["evidenceDir"]) / "attempt"
        request = json.loads((attempt / "request.json").read_text())
        result = json.loads((attempt / "result.json").read_text())
        prepared = tool(
            "harness_accept_assess", {"prepare": {"finding_ids": [finding["id"]], "bead_id": "A"}}
        )
        assert prepared["requestTemplate"]["expected_product_run_id"]
        assert Path(request["command"][0]) == executable.resolve()
        assert (
            request["executableSha256"]
            == hashlib.sha256(executable.read_bytes()).hexdigest().upper()
        )
        assert result["exitCode"] == 0 and result["outputComplete"]
        assert request["launch"]["stdinText"] == request["prompt"]
        assert capture.read_bytes() == request["prompt"].encode("utf-8")
        assert 32767 < len(request["prompt"]) <= 44000
        assert sum(len(part) + 3 for part in request["command"]) < 32767
        # The original image remains authoritative after PATH changes, and a
        # changed image must still refuse at the actual assessment consumer.
        executable.write_bytes(executable.read_bytes() + b"SOURCE-ONLY changed image")
        refused = tool(
            "harness_accept_assess",
            {"prepare": {"finding_ids": [finding["id"]], "bead_id": "A"}},
            error=True,
        )
        assert refused["holdReasons"] == ["execution-author-executable-unverified"]
    finally:
        store.close()


@pytest.mark.parametrize("reads_nothing", ["exits", "times-out"])
def test_unconsumed_packet_never_blocks_owned_child_exit_or_timeout(
    tmp_path, monkeypatch, reads_nothing
):
    # Protect controller liveness when the provider never consumes stdin. Existing
    # timeout fixtures use DEVNULL and cannot expose a blocking prompt-pipe write.
    run, cwd, attempt, request, launch = local_attempt(
        tmp_path, seconds=0 if reads_nothing == "exits" else 1.5, timeout=0.3
    )
    if sys.platform == "win32":
        original_run = subprocess.run

        def unavailable_cleanup(command, *args, **kwargs):
            if command[:1] == ["taskkill"]:
                # No host process-control permission is exercised: simulate an
                # unavailable cleanup and let this finite harmless child exit.
                return subprocess.CompletedProcess(
                    command, 1, b"", b"SOURCE-ONLY simulated unavailable cleanup"
                )
            return original_run(command, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", unavailable_cleanup)
    packet = "SOURCE-ONLY unconsumed packet λ\r\n" * 1200
    launch = replace(launch, stdin_text=packet)
    request.update(prompt=packet, launch=launch.to_payload())
    started = time.monotonic()
    result = dispatch._run_provider(launch, cwd, attempt, request, 0.3, {})
    assert time.monotonic() - started < 3
    assert result["status"] == ("exited" if reads_nothing == "exits" else "timed-out")
    assert result["cleanup"]["controllerProcessExited"] is True
    assert result["outputComplete"] is (reads_nothing == "exits")
    original = json.loads((attempt / "process-provenance.json").read_text(encoding="utf-8"))
    assert (
        hashlib.sha256(original["identity"]["launch"]["stdinText"].encode()).hexdigest()
        == hashlib.sha256(packet.encode()).hexdigest()
    )
