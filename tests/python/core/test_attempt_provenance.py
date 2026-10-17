"""Local protocol proofs only: no provider, live-worker or acceptance credit."""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from codex_harness.core import dispatch, record_io


def originals(tmp_path):
    command = ["local-process-test", "a b", "λ\r\n"]
    request = dict(
        runId="local-source-run",
        bead="A",
        requirementId="group:RA",
        planRevision="a" * 64,
        requirementRefs=[],
        specRefs=None,
        ownedFiles=["A.py"],
        prompt="packet λ\r\nnext\n",
        provider="local-process-test",
        model=None,
        effort=None,
        command=command,
        launch={"cli": "local-process-test", "command": command},
        mode="provider-normal",
        cwd=str(tmp_path.resolve()),
        baseCommit="b" * 40,
        adapterCommit="c" * 40,
        executableSha256="D" * 64,
        startedAt="local-request-start",
        processId=123,
        processObservedAt="local-controller-observation",
    )
    record = {**request, "attempt": copy.deepcopy(request)}
    return request, copy.deepcopy(request), record


def test_create_once_actual_filesystem_never_clobbers_and_concurrent_winner(tmp_path):
    target = tmp_path / "original.json"
    record_io.write_record_json_once(target, {"value": "λ"})
    before = target.read_bytes()
    for value in ("λ", "changed"):
        with pytest.raises(FileExistsError):
            record_io.write_record_json_once(target, {"value": value})
        assert target.read_bytes() == before
    other = tmp_path / "race.json"

    def create(value):
        try:
            record_io.write_record_json_once(other, {"value": value})
            return True
        except FileExistsError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, [1, 2])) == [False, True]
    assert record_io.read_record_json(other) in ({"value": 1}, {"value": 2})


def test_create_once_link_refusal_is_closed_fsynced_one_shot(tmp_path, monkeypatch):
    target = tmp_path / "original.json"
    calls = []
    fsync = os.fsync
    synced = []
    monkeypatch.setattr(record_io.os, "fsync", lambda fd: (synced.append(fd), fsync(fd)))

    def refuse(source, destination):
        assert synced and destination == target
        # Reopening on Windows establishes the temporary stream was closed.
        with open(source, "rb") as stream:
            assert json.load(stream) == {"value": 2}
        calls.append(source)
        raise OSError("synthetic unsupported hard links")

    monkeypatch.setattr(record_io.os, "link", refuse)
    monkeypatch.setattr(record_io, "_replace_record", lambda *_: pytest.fail("replace fallback"))
    with pytest.raises(OSError, match="unsupported hard links"):
        record_io.write_record_json_once(target, {"value": 2})
    assert len(calls) == 1 and calls[0].exists() and not target.exists()


@pytest.mark.parametrize(
    "field",
    [
        "runId",
        "bead",
        "requirementId",
        "planRevision",
        "requirementRefs",
        "specRefs",
        "ownedFiles",
        "prompt",
        "provider",
        "model",
        "effort",
        "command",
        "launch",
        "cwd",
        "baseCommit",
        "adapterCommit",
        "executableSha256",
        "processId",
        "startedAt",
        "processObservedAt",
    ],
)
def test_coordinated_mutable_spoof_cannot_change_original(tmp_path, field):
    from codex_harness.core import attempt_provenance as provenance

    request, response, record = originals(tmp_path)
    provenance.publish(
        tmp_path, request, process_id=123, process_observed_at=request["processObservedAt"]
    )
    control = provenance.read_and_compare(
        tmp_path, request, response, record=record, expected_owned_files=["A.py"]
    )
    assert control["state"] == "consistent"
    before = (tmp_path / provenance.FILENAME).read_bytes()
    value = {
        "requirementRefs": [{"id": "R-other", "revision": "e" * 64}],
        "specRefs": [{"id": "S-other", "kind": "spec", "revision": "e" * 64}],
        "ownedFiles": ["other.py"],
        "command": ["local-process-test", "different arg", "λ"],
        "launch": {
            "cli": "local-process-test",
            "command": list(request["command"]),
            "protections": ["different fixture"],
        },
        "processId": 456,
        "cwd": str(tmp_path / "other"),
    }.get(field, "coordinated-foreign-value")
    for item in (request, response, record, record["attempt"]):
        item[field] = copy.deepcopy(value)
        if field == "command":
            item["launch"]["command"] = copy.deepcopy(value)
        if field == "provider":
            item["launch"]["cli"] = value
    observed = provenance.read_and_compare(
        tmp_path, request, response, record=record, expected_owned_files=["A.py"]
    )
    assert observed["state"] != "consistent"
    assert (tmp_path / provenance.FILENAME).read_bytes() == before


def test_exact_bytes_missing_digest_schema_and_unknown_executable(tmp_path):
    from codex_harness.core import attempt_provenance as provenance

    request, response, record = originals(tmp_path)

    def observe():
        return provenance.read_and_compare(
            tmp_path, request, response, record=record, expected_owned_files=["A.py"]
        )

    assert observe()["code"] == "attempt-provenance-unavailable"
    envelope = provenance.publish(
        tmp_path, request, process_id=123, process_observed_at=request["processObservedAt"]
    )
    identity = envelope["identity"]
    assert identity["packetUtf8Sha256"] == hashlib.sha256(request["prompt"].encode()).hexdigest()
    assert (
        identity["commandSha256"]
        == hashlib.sha256(
            json.dumps(
                request["command"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    assert observe()["state"] == "consistent"
    record["acceptedPlanRevision"] = "f" * 64
    assert observe()["state"] == "consistent"  # Source publication is still the original.
    path = tmp_path / provenance.FILENAME
    altered = copy.deepcopy(envelope)
    altered["identity"]["bead"] = "foreign"
    path.write_text(json.dumps(altered), encoding="utf-8")
    assert observe()["code"] == "attempt-provenance-digest-mismatch"
    path.write_text(json.dumps({**envelope, "schema": "future"}), encoding="utf-8")
    assert observe()["code"] == "attempt-provenance-schema-unavailable"
    unknown = tmp_path / "unknown-executable"
    unknown.mkdir()
    for value in (request, response, record["attempt"]):
        value["executableSha256"] = None
    provenance.publish(
        unknown, request, process_id=123, process_observed_at=request["processObservedAt"]
    )
    incomplete = provenance.read_and_compare(
        unknown, request, response, record=record, expected_owned_files=["A.py"]
    )
    assert incomplete["state"] == "incomplete"
    assert incomplete["code"] == "attempt-provenance-executable-unavailable"


@pytest.mark.parametrize("integrated", [False, True])
def test_legacy_provider_shaped_original_cannot_recover_or_reuse(tmp_path, monkeypatch, integrated):
    from tests.python.core.test_dispatch_resume import _plan, _run_dir

    plan = _plan(tmp_path)
    if not integrated:
        monkeypatch.setattr(
            dispatch,
            "_integrate_bead",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("source-only crash before integration")
            ),
        )
    first = dispatch.run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    bead = first["beads"]["A"]
    directory = Path(bead["evidenceDir"]) / "attempt"
    for name in ("request.json", "result.json"):
        value = record_io.read_record_json(directory / name)
        value.update(mode="provider-normal", isMocked=False, didRunProvider=True)
        value["launch"]["cli"] = "codex"
        dispatch._write_record(directory / name, value)
    bead.update(isMocked=False, didRunProvider=True)
    bead["attempt"].update(mode="provider-normal", isMocked=False, didRunProvider=True)
    dispatch._write_record(_run_dir(first) / "evidence/run.json", first)
    # Keep denial holds out of this provenance-specific source regression.
    monkeypatch.setattr(dispatch, "_hold_permission_guard", lambda *a: False)
    resumed = dispatch.run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
    )
    current = resumed["beads"]["A"]
    assert current["status"] == "held: local process provenance"
    assert current["localProcessProvenance"]["code"] == "attempt-provenance-unavailable"
    assert not current.get("reused") and not current["candidate"]["validated"]
    assert not resumed["requirementValidation"][0]["passed"]
    assert not (directory / "process-provenance.json").exists()


def test_copied_original_to_other_retry_refuses_even_with_matching_mutable_context(tmp_path):
    from codex_harness.core import attempt_provenance as provenance

    first, retry = tmp_path / "first/attempt", tmp_path / "retry-1/attempt"
    first.mkdir(parents=True)
    retry.mkdir(parents=True)
    request, response, record = originals(tmp_path)
    provenance.publish(
        first, request, process_id=123, process_observed_at=request["processObservedAt"]
    )
    shutil.copyfile(first / provenance.FILENAME, retry / provenance.FILENAME)
    record["evidenceDir"] = str(retry.parent)
    observed = provenance.read_and_compare(
        retry, request, response, record=record, expected_owned_files=["A.py"]
    )
    assert observed["code"] == "attempt-provenance-attempt-directory-mismatch"


def test_actual_local_child_publishes_after_spawn_before_result(tmp_path, monkeypatch):
    from tests.python.core.test_dispatch_cancel import local_attempt

    from codex_harness.core import attempt_provenance as provenance

    _, cwd, directory, request, launch = local_attempt(tmp_path, seconds=0.01)
    children = []
    popen, publish = subprocess.Popen, provenance.publish

    def spawn(*args, **kwargs):
        if tuple(args[0]) != launch.command:
            return popen(*args, **kwargs)
        assert not (directory / provenance.FILENAME).exists()
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    def original(attempt, value, **kwargs):
        assert len(children) == 1 and kwargs["process_id"] == children[0].pid
        assert not (directory / "result.json").exists()
        return publish(attempt, value, **kwargs)

    monkeypatch.setattr(dispatch.subprocess, "Popen", spawn)
    monkeypatch.setattr(provenance, "publish", original)
    result = dispatch._run_provider(launch, cwd, directory, request, 5, {})
    envelope = record_io.read_record_json(directory / provenance.FILENAME)
    assert envelope["identity"]["processId"] == children[0].pid == result["processId"]
    assert envelope["identity"]["processObservedAt"] == request["processObservedAt"]
    assert (
        envelope["identity"]["packetUtf8Sha256"]
        == hashlib.sha256(request["prompt"].encode("utf-8")).hexdigest()
    )
    assert "\u03bb" in request["prompt"]
    assert result["isLiveProductProof"] is False


@pytest.mark.parametrize("failed_stop", [False, True])
def test_immutable_publication_refusal_stops_exact_owned_child_once(
    tmp_path, monkeypatch, failed_stop
):
    from tests.python.core.test_dispatch_cancel import local_attempt

    from codex_harness.core import attempt_provenance as provenance

    _, cwd, directory, request, launch = local_attempt(tmp_path, seconds=1)
    path = directory / provenance.FILENAME
    path.write_bytes(b"retained original")
    stops = []
    kill = subprocess.Popen.kill

    def stop(child):
        stops.append(child)
        if failed_stop:
            raise PermissionError("source-only synthetic stop refusal")
        return kill(child)

    monkeypatch.setattr(subprocess.Popen, "kill", stop)
    result = dispatch._run_provider(launch, cwd, directory, request, 5, {})
    assert len(stops) == 1 and stops[0].pid == result["processId"]
    assert result["failure"]["stage"] == "post-spawn-publication"
    assert result["failure"]["path"] == str(path)
    assert result["failure"]["type"] == "FileExistsError"
    assert result["cleanup"]["controllerProcessExited"] is not failed_stop
    assert result["outputComplete"] is result["isLiveProductProof"] is False
    assert path.read_bytes() == b"retained original"
    stops[0].wait(timeout=5)  # Bounded own helper; natural exit if stop was refused.


def test_failed_popen_has_no_original(tmp_path, monkeypatch):
    from tests.python.core.test_dispatch_cancel import local_attempt

    from codex_harness.core import attempt_provenance as provenance

    _, cwd, directory, request, launch = local_attempt(tmp_path)

    def refuse(*args, **kwargs):
        raise OSError("source-only synthetic spawn refusal")

    monkeypatch.setattr(dispatch.subprocess, "Popen", refuse)
    result = dispatch._run_provider(launch, cwd, directory, request, 5, {})
    assert result["status"] == "launch-failed" and not result["didRunProvider"]
    assert not (directory / provenance.FILENAME).exists()


def local_run(tmp_path, monkeypatch, *, crash=False):
    from tests.python.core.test_dispatch import _tiny_plan
    from tests.python.core.test_dispatch_cancel import source_only_packet_renderer

    from codex_harness.core.provider_launch import ProviderNormalLaunch

    plan = _tiny_plan(tmp_path)

    class LocalAdapter:
        def provider_normal_command(self, *, prompt, **kwargs):
            bead = "A" if "Bead A" in prompt else "B"
            return ProviderNormalLaunch(
                cli="local-process-test",
                command=(
                    sys.executable,
                    "-B",
                    "-c",
                    f"from pathlib import Path; Path('{bead}.py').write_text('VALUE = 1\\n'); "
                    'print(\'{"type":"turn.completed","isMocked":true}\')',
                ),
                protections=("Source-only bounded local Python fixture; no provider.",),
            )

    monkeypatch.setattr(dispatch, "get_adapter", lambda _: LocalAdapter())
    source_only_packet_renderer(monkeypatch)
    integrate = dispatch._integrate_bead
    if crash:

        def interrupted(*args, **kwargs):
            raise RuntimeError("source-only interruption before integration")

        monkeypatch.setattr(dispatch, "_integrate_bead", interrupted)
    providers = {"A": "local-process-test", "B": "local-process-test"}
    first = dispatch.run_plan(plan, work_root=tmp_path / "runs", provider_for=providers)
    monkeypatch.setattr(dispatch, "_integrate_bead", integrate)
    return plan, first, Path(first["repo"]).parent, providers


@pytest.mark.parametrize("change", ["missing-result", "missing-original", "after-selection"])
def test_integrated_reuse_reads_original_at_each_use(tmp_path, monkeypatch, change):
    from codex_harness.core import attempt_provenance as provenance

    plan, first, run, providers = local_run(tmp_path, monkeypatch)
    directory = Path(first["beads"]["A"]["evidenceDir"]) / "attempt"
    assert first["beads"]["A"]["candidate"]["validated"]
    if change == "after-selection":
        ready = dispatch.graph.ready_beads
        changed = []

        def between(*args, **kwargs):
            if not changed:
                (directory / provenance.FILENAME).unlink()
                changed.append(True)
            return ready(*args, **kwargs)

        monkeypatch.setattr(dispatch.graph, "ready_beads", between)
    else:
        (
            directory / ("result.json" if change == "missing-result" else provenance.FILENAME)
        ).unlink()
    resumed = dispatch.run_plan(
        plan, work_root=tmp_path / "unused", resume_run_dir=run, provider_for=providers
    )
    current = resumed["beads"]["A"]
    assert current["status"] == dispatch.STATUS_PROVENANCE_HELD
    assert not current["candidate"]["validated"] and not current.get("reused")
    assert not resumed["requirementValidation"][0]["passed"]


def test_completed_recovery_second_read_and_explicit_retry_namespace(tmp_path, monkeypatch):
    from codex_harness.core import attempt_provenance as provenance

    plan, first, run, providers = local_run(tmp_path, monkeypatch, crash=True)
    directory = Path(first["beads"]["A"]["evidenceDir"]) / "attempt"
    original = (directory / provenance.FILENAME).read_bytes()
    attempt_bead = dispatch._attempt_bead

    def between(*args, **kwargs):
        (directory / provenance.FILENAME).unlink(missing_ok=True)
        return attempt_bead(*args, **kwargs)

    monkeypatch.setattr(dispatch, "_attempt_bead", between)
    held = dispatch.run_plan(
        plan, work_root=tmp_path / "unused", resume_run_dir=run, provider_for=providers
    )
    assert held["beads"]["A"]["status"] == dispatch.STATUS_PROVENANCE_HELD
    assert held["resumeSummary"]["redispatched"] == []
    monkeypatch.setattr(dispatch, "_attempt_bead", attempt_bead)
    retried = dispatch.run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=run,
        retry_blocked="A",
        provider_for=providers,
    )
    current = retried["beads"]["A"]
    fresh = Path(current["evidenceDir"]) / "attempt"
    assert fresh != directory and fresh.parent.name == "retry-1"
    assert (fresh / provenance.FILENAME).read_bytes() != original
    assert not (directory / provenance.FILENAME).exists()
    assert current["candidate"]["validated"] and not retried["didExecuteRealProvider"]


@pytest.mark.parametrize("field", ["prompt", "model", "command"])
def test_coordinated_valid_mutable_spoof_refused_by_recovery(tmp_path, monkeypatch, field):
    from codex_harness.core import attempt_provenance as provenance

    plan, first, run, providers = local_run(tmp_path, monkeypatch, crash=True)
    bead = first["beads"]["A"]
    directory = Path(bead["evidenceDir"]) / "attempt"
    original = (directory / provenance.FILENAME).read_bytes()
    request = record_io.read_record_json(directory / "request.json")
    response = record_io.read_record_json(directory / "result.json")
    changed = {
        "prompt": "coordinated changed packet\r\n\u03bb",
        "model": "different-source-fixture-model",
        "command": [*request["command"], "different argv boundary"],
    }[field]
    for value in (request, response, bead, bead["attempt"]):
        value[field] = copy.deepcopy(changed)
        if field == "command" and "launch" in value:
            value["launch"]["command"] = copy.deepcopy(changed)
    # This deliberately passes the previous mutable-only identity check.
    assert dispatch._completed_attempt_identity_error(bead, request, response) is None
    dispatch._write_record(directory / "request.json", request)
    dispatch._write_record(directory / "result.json", response)
    dispatch._write_record(run / "evidence/run.json", first)
    resumed = dispatch.run_plan(
        plan, work_root=tmp_path / "unused", resume_run_dir=run, provider_for=providers
    )
    current = resumed["beads"]["A"]
    assert current["status"] == dispatch.STATUS_PROVENANCE_HELD
    assert current["localProcessProvenance"]["code"].startswith(
        "attempt-provenance-identity-mismatch:"
    )
    assert not current["candidate"]["validated"] and not current["attempt"].get("recoverResult")
    assert (directory / provenance.FILENAME).read_bytes() == original


def test_legacy_mock_recovery_without_new_context_keeps_source_only_semantics(
    tmp_path, monkeypatch
):
    from tests.python.core.test_dispatch_resume import _plan, _run_dir

    plan = _plan(tmp_path)
    integrate = dispatch._integrate_bead

    def crash(*args, **kwargs):
        raise RuntimeError("source-only interruption")

    monkeypatch.setattr(dispatch, "_integrate_bead", crash)
    first = dispatch.run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    monkeypatch.setattr(dispatch, "_integrate_bead", integrate)
    directory = Path(first["beads"]["A"]["evidenceDir"]) / "attempt"
    for name in ("request.json", "result.json"):
        value = record_io.read_record_json(directory / name)
        for field in ("provider", "model", "effort", "ownedFiles"):
            value.pop(field, None)
        dispatch._write_record(directory / name, value)
    resumed = dispatch.run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
    )
    assert resumed["beads"]["A"]["attempt"]["recoverResult"]
    assert resumed["status"] == dispatch.STATUS_MOCK_ONLY
    assert not (directory / "process-provenance.json").exists()
