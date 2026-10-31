"""Local protocol proofs only: no provider, live-worker or acceptance credit."""

import copy
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

from codex_harness.core import record_io


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


@pytest.mark.parametrize("field", ["command", "ownedFiles", "prompt"])
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
