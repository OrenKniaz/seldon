"""Source identity checks; optional real-wheel console regression uses no providers."""

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.python.core.test_attempt_provenance import originals

from codex_harness.core import attempt_provenance, package_provenance


def test_python_digest_tracks_actual_bytes_and_names_only(tmp_path):
    (tmp_path / "nested").mkdir()
    source = tmp_path / "nested/a.py"
    source.write_bytes(b"a=1\r\n")
    before = package_provenance._source_digest(tmp_path)
    (tmp_path / "ignored.txt").write_text("ignored")
    assert package_provenance._source_digest(tmp_path) == before
    source.write_bytes(b"a=1\n")
    assert package_provenance._source_digest(tmp_path) != before
    before = package_provenance._source_digest(tmp_path)
    source.rename(source.with_name("b.py"))
    assert package_provenance._source_digest(tmp_path) != before


def test_installed_identity_never_queries_git_or_borrows_metadata(tmp_path, monkeypatch):
    package = tmp_path / "site/codex_harness"
    core = package / "core"
    core.mkdir(parents=True)
    helper = core / "package_provenance.py"
    helper.write_text("# installed source\n")
    monkeypatch.setattr(package_provenance, "__file__", str(helper))
    monkeypatch.setattr(
        package_provenance.subprocess, "run", lambda *a, **k: pytest.fail("installed Git query")
    )
    # An unrelated ancestor checkout must never supply adapter HEAD.
    (tmp_path / ".git").mkdir()
    value = package_provenance.adapter_provenance()
    assert value["adapterCommit"] is None
    assert value["adapterProvenance"] == {
        "schema": "seldon/adapter-source-v1",
        "origin": "installed-package",
        "distribution": "seldon",
        "version": None,
        "pythonSourceSha256": package_provenance._source_digest(package),
    }


def test_own_source_checkout_reports_actual_head():
    value = package_provenance.adapter_provenance()
    assert value["adapterProvenance"]["origin"] == "source-tree"
    root = Path(package_provenance.__file__).resolve().parents[3]
    tracked = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--error-unmatch",
            "--",
            "src/codex_harness/core/package_provenance.py",
        ],
        capture_output=True,
    )
    if tracked.returncode == 0:
        assert (
            value["adapterCommit"]
            == subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
        )
    else:
        assert value["adapterCommit"] is None


@pytest.mark.parametrize("legacy", [False, True])
def test_provenance_schema_roundtrip_and_strict_legacy(tmp_path, legacy):
    request, response, record = originals(tmp_path)
    if not legacy:
        source = {
            "schema": "seldon/adapter-source-v1",
            "origin": "installed-package",
            "distribution": "seldon",
            "version": "0.1.0",
            "pythonSourceSha256": "a" * 64,
        }
        for value in (request, response, record, record["attempt"]):
            value.update(adapterCommit=None, adapterProvenance=copy.deepcopy(source))
    envelope = attempt_provenance.publish(
        tmp_path, request, process_id=123, process_observed_at=request["processObservedAt"]
    )
    assert envelope["schema"] == (
        attempt_provenance.SCHEMA if legacy else attempt_provenance.SCHEMA_V2
    )
    assert (
        attempt_provenance.read_and_compare(
            tmp_path, request, response, record=record, expected_owned_files=["A.py"]
        )["state"]
        == "consistent"
    )
    if legacy:
        request["adapterCommit"] = None
        with pytest.raises(ValueError, match="adapterCommit"):
            attempt_provenance.identity_from_request(
                request, process_id=123, process_observed_at="observed"
            )
    else:
        response.pop("adapterProvenance")
        assert (
            attempt_provenance.read_and_compare(
                tmp_path, request, response, record=record, expected_owned_files=["A.py"]
            )["state"]
            == "mismatch"
        )


def test_built_wheel_installed_console_constructs_mock_attempt(tmp_path):
    wheel = os.environ.get("SELDON_PROVENANCE_WHEEL")
    python = os.environ.get("SELDON_PACKAGING_PYTHON")
    if not wheel or not python:
        pytest.skip("requires an actual built wheel and existing pip-capable Python")
    site = tmp_path / "site"
    subprocess.run(
        [python, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(site), wheel],
        check=True,
        capture_output=True,
        text=True,
    )
    fixture = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"
    plan = tmp_path / "plan"
    shutil.copytree(fixture, plan)
    env = {**os.environ, "PYTHONPATH": str(site), "PYTHONDONTWRITEBYTECODE": "1"}
    command = site / ("bin/run_plan.exe" if os.name == "nt" else "bin/run_plan")
    result = subprocess.run(
        [
            str(command),
            "--plan",
            str(plan),
            "--work-root",
            str(tmp_path / "runs"),
            "--mock-provider",
            "reference",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    requests = list((tmp_path / "runs").rglob("attempt/request.json"))
    assert requests, "installed console never constructed a dispatch attempt"
    for path in requests:
        request = json.loads(path.read_text(encoding="utf-8"))
        assert request["mode"] == "mock:reference"
        assert request["adapterCommit"] is None
        provenance = request["adapterProvenance"]
        assert provenance["origin"] == "installed-package"
        assert provenance["version"] == "0.1.0"
        assert provenance["pythonSourceSha256"] == package_provenance._source_digest(
            site / "codex_harness"
        )


@pytest.mark.parametrize("change", ["coordinated", "attempt-missing", "legacy-added", "invalid"])
def test_adapter_object_cannot_be_added_removed_or_rebound(tmp_path, change):
    request, response, record = originals(tmp_path)
    source = {
        "schema": "seldon/adapter-source-v1",
        "origin": "installed-package",
        "distribution": "seldon",
        "version": "0.1.0",
        "pythonSourceSha256": "a" * 64,
    }
    values = (request, response, record, record["attempt"])
    if change != "legacy-added":
        for value in values:
            value.update(adapterCommit=None, adapterProvenance=copy.deepcopy(source))
    attempt_provenance.publish(
        tmp_path, request, process_id=123, process_observed_at=request["processObservedAt"]
    )
    if change == "coordinated":
        for value in values:
            value["adapterProvenance"]["pythonSourceSha256"] = "b" * 64
    elif change == "attempt-missing":
        record["attempt"].pop("adapterProvenance")
    elif change == "legacy-added":
        for value in values:
            value["adapterProvenance"] = copy.deepcopy(source)
    else:
        for value in values:
            value["adapterProvenance"]["pythonSourceSha256"] = "unknown"
    assert (
        attempt_provenance.read_and_compare(
            tmp_path, request, response, record=record, expected_owned_files=["A.py"]
        )["state"]
        != "consistent"
    )
