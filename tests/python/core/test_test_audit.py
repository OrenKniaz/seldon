"""Real Git snapshots and proof modules; providers are strictly stand-ins."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from codex_harness.core import dispatch, test_audit
from codex_harness.core.dispatch import STATUS_MOCK_ONLY, _git, run_plan
from codex_harness.core.spine import Spine, compile_manifest, impact, load_spine, save_spine
from codex_harness.core.test_audit import (
    audit_prospective_tests,
    audit_revised_tests,
    render_test_audit_card,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit"


@pytest.fixture
def prospective(tmp_path):
    template = tmp_path / "template"
    tests = template / "tests"
    tests.mkdir(parents=True)
    (template / "app.py").write_text("def value(): raise NotImplementedError\n")
    (tests / "test_app.py").write_text(
        "import unittest\nfrom app import value\nclass Proof(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(value(), 42)\n"
    )
    data = load_spine(FIXTURE / "spine.json").to_dict()
    data["nodes"] = [
        dict(id=key, kind=kind, title=key, text=key, parents=parents, fields=fields)
        for key, kind, parents, fields in (
            (
                "V",
                "vision",
                [],
                {
                    "validation": ["-m", "unittest", "discover", "-s", "tests", "-p"],
                    "requirementFiles": ["tests"],
                },
            ),
            ("F", "feature", ["V"], {}),
            ("R", "requirement", ["F"], {}),
            (
                "B",
                "bead",
                ["R"],
                {
                    "ownedFiles": ["app.py"],
                    "dependsOn": [],
                    "lessons": [],
                    "proofModule": "test_app.py",
                },
            ),
            ("T", "test", ["R"], {"module": "test_app.py", "level": "unit"}),
        )
    ]
    spine = Spine(data)
    return dict(template=template, spine=spine, manifest=compile_manifest(spine, template))


@pytest.fixture
def withheld_audit_bootstrap(tmp_path, monkeypatch):
    """Finite owned bootstrap, real handles/counts, optional controlled teardown refusal."""
    import time

    from codex_harness.core import audit_process

    release = tmp_path / "release-owned-bootstrap"
    state = {"jobs": [], "closed": set(), "refuse": False, "terminations": 0}
    real_init = audit_process._WindowsJob.__init__
    real_active = audit_process._WindowsJob.active

    class ObservedApi:
        def __init__(self, api, job):
            self.api, self.job = api, job

        def __getattr__(self, name):
            return getattr(self.api, name)

        def TerminateJobObject(self, *args):
            state["terminations"] += 1
            if state["refuse"]:
                error = PermissionError(5, "Controlled SOURCE-ONLY owned teardown refusal")
                error.audit_native_failure = "TerminateJobObject"
                raise error
            return self.api.TerminateJobObject(*args)

        def CloseHandle(self, handle):
            result = self.api.CloseHandle(handle)
            if result and handle == self.job.handle:
                state["closed"].add(handle)
            return result

    def observe_init(job):
        real_init(job)
        job.api = ObservedApi(job.api, job)
        state["jobs"].append(job)

    delayed = audit_process._BOOTSTRAP.replace(
        "send({'kind': 'ready'})",
        "import time\n_end = time.monotonic() + 10\n"
        f"while not os.path.exists({str(release)!r}) and time.monotonic() < _end:\n"
        "    time.sleep(.01)\n"
        "send({'kind': 'ready'})",
    )
    monkeypatch.setattr(audit_process, "_BOOTSTRAP", delayed)
    monkeypatch.setattr(audit_process._WindowsJob, "__init__", observe_init)
    try:
        yield state
    finally:
        # Restore only our injected refusal and release the finite trusted fixture.
        # Query each still-held real Job to zero before closing; never kill by PID.
        state["refuse"] = False
        release.write_text("release", encoding="utf-8")
        for job in state["jobs"]:
            if job.handle not in state["closed"]:
                deadline = time.monotonic() + 5
                while real_active(job) and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert real_active(job) == 0
                assert job.api.CloseHandle(job.handle)


def test_prospective_owned_module_cli_needs_no_imported_main_api(prospective):
    """Protect real CLI red: imported-function proofs miss the child boundary."""
    root = prospective["template"]
    (root / "app.py").write_text("raise NotImplementedError('planned CLI')\n")
    (root / "tests/test_app.py").write_text(
        "import subprocess, sys, unittest\nfrom pathlib import Path\n"
        "class Proof(unittest.TestCase):\n"
        "    def test_cli(self):\n"
        "        result = subprocess.run([sys.executable, '-B', '-m', 'app', 'list'], "
        "cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)\n"
        "        self.assertEqual(result.returncode, 0)\n"
        "        self.assertEqual(result.stdout, '[]\\n')\n"
        "        self.assertEqual(result.stderr, '')\n"
    )
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = audit_prospective_tests(**prospective)
    assert result["ok"], json.dumps(
        result["redBaseline"][0]["failureProvenance"]["tests"][0], indent=2
    )
    proof = result["redBaseline"][0]
    assert proof["failureProvenance"]["classification"] == "meaningful-red"
    observed = proof["failureProvenance"]["tests"][0]
    assert observed["classification"] == "owned-cli-stub"
    launch = observed["moduleLaunches"][0]
    assert launch["argv"][1:] == ["-B", "-m", "app", "list"]
    child = launch["child"]
    assert child["runtimePid"] == child["pid"]
    assert child["pid"] == launch["pid"] or (
        child["platform"] == launch["platform"] == "win32"
        and child["runtimeParentPid"] == launch["pid"]
        and child["launcherExecutable"] != launch["actualInterpreter"]
    )
    assert child["atexitObserved"] is True
    assert child["observationStartedAtUtc"] <= child["atexitObservedAtUtc"]
    assert launch["child"]["startup"] == "observed-equivalent-with-private-bootstrap"
    assert launch["child"]["startupIdentity"] == launch["startupIdentity"]
    assert launch["child"]["exception"]["type"] == "NotImplementedError"
    assert result["proofLevel"] == "prospective" and not result["integrationChecked"]
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("case", ["overwritten", "stderr-spoof", "import-error"])
def test_prospective_module_cli_refuses_unattributed_failures(prospective, case):
    """A real child is insufficient without startup, owned-stub and result lineage."""
    root = prospective["template"]
    (root / "app.py").write_text("raise NotImplementedError('planned CLI')\n")
    command = "[sys.executable, '-B', '-m', 'app']"
    extra = ""
    assertion = "self.assertEqual(result.returncode, 0)"
    if case == "overwritten":
        extra = "        result = subprocess.CompletedProcess([], 0)\n"
        assertion = "self.assertEqual(result.returncode, 1)"
    elif case == "unrelated":
        assertion = "self.assertEqual(1, 42)"
    elif case == "stderr-spoof":
        (root / "app.py").write_text(
            "import sys\nsys.stderr.write('NotImplementedError: planned CLI')\nsys.exit(1)\n"
        )
    elif case == "import-error":
        (root / "app.py").write_text("import missing_cli_dependency\n")
    elif case == "foreign-module":
        (root / "foreign.py").write_text("raise NotImplementedError('foreign CLI')\n")
        command = "[sys.executable, '-m', 'foreign']"
    elif case == "startup-flag":
        command = "[sys.executable, '-S', '-m', 'app']"
    elif case == "startup-env":
        extra = ""
    elif case in {"sitecustomize", "usercustomize"}:
        (root / (case + ".py")).write_text("# Legitimate project startup customization.\n")
    launch = (
        f"subprocess.run({command}, cwd=Path(__file__).resolve().parents[1], "
        "capture_output=True, text=True"
    )
    if case == "startup-env":
        launch += ", env={**os.environ, 'PYTHONSAFEPATH': '1'}"
    launch += ")"
    if case == "setup-launch":
        body = (
            "    def setUp(self):\n        self.result = " + launch + "\n"
            "    def test_cli(self):\n        self.assertEqual(self.result.returncode, 0)\n"
        )
    else:
        body = (
            "    def test_cli(self):\n        result = "
            + launch
            + "\n"
            + extra
            + "        "
            + assertion
            + "\n"
        )
    if case == "stderr-spoof":
        # Validate the actual captured child stderr; unittest does not forward it.
        body = body.replace(
            "        self.assertEqual(result.returncode, 0)",
            "        self.assertIn('NotImplementedError: planned CLI', result.stderr)\n"
            "        self.assertEqual(result.returncode, 0)",
        )
    (root / "tests/test_app.py").write_text(
        "import os, subprocess, sys, unittest\nfrom pathlib import Path\n"
        "class Proof(unittest.TestCase):\n" + body
    )
    result = audit_prospective_tests(**prospective)
    assert not result["ok"], result
    provenance = result["redBaseline"][0]["failureProvenance"]
    assert provenance["classification"] == "unproved"
    launch = provenance["tests"][0]["moduleLaunches"][0]
    if case in {"sitecustomize", "usercustomize"}:
        assert not launch.get("instrumented")
        assert "startup customization" in launch["reason"]
    elif case == "startup-env":
        assert not launch.get("instrumented")
        assert "startup environment" in launch["reason"]
    elif case == "stderr-spoof":
        assert not launch.get("child", {}).get("exception")


@pytest.mark.parametrize("case", ["all-bound", "unrelated-second", "stale", "replaced", "mutated"])
def test_prospective_cli_binds_each_subtest_failure_to_its_actual_result(prospective, case):
    """Protect per-event attribution; single-failure tests miss stale/foreign later subtests."""
    root = prospective["template"]
    (root / "app.py").write_text("raise NotImplementedError('planned CLI')\n")
    invocation = "                result = self.invoke()\n"
    if case == "stale":
        invocation = "                if index == 0:\n                    result = self.invoke()\n"
    elif case == "replaced":
        invocation += "                result = subprocess.CompletedProcess([], 1, '', '')\n"
    elif case == "mutated":
        invocation += "                result.returncode = 42\n"
    check = "                self.success(result)\n"
    if case == "unrelated-second":
        check = (
            "                if index == 1:\n                    self.assertEqual(1, 0)\n"
            "                self.success(result)\n"
        )
    (root / "tests/test_app.py").write_text(
        "import subprocess, sys, unittest\nfrom pathlib import Path\n"
        "class Proof(unittest.TestCase):\n"
        "    def invoke(self):\n"
        "        command = [sys.executable, '-m', 'app']\n"
        "        return subprocess.run(command, cwd=Path(__file__).resolve().parents[1], "
        "capture_output=True, text=True)\n"
        "    def success(self, result):\n"
        "        self.assertEqual(result.returncode, 0, result.stderr)\n"
        "    def test_cli(self):\n        for index in (0, 1):\n"
        "            with self.subTest(index=index):\n" + invocation + check
    )
    prospective["manifest"] = compile_manifest(prospective["spine"], root)
    if case == "all-bound":
        from codex_harness.core.plan_store import PlanStore

        workspace = root.parent
        source = workspace / "spine.json"
        data = prospective["spine"].to_dict()
        for node in data["nodes"]:
            node["id"] = "V1" if node["id"] == "V" else node["id"]
            node["parents"] = ["V1" if p == "V" else p for p in node["parents"]]
        save_spine(Spine(data), source)
        with PlanStore(workspace) as store:
            store.import_spine(source)
            manifest = store.compile_template(root)
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "codex_harness.core.plan_cli",
                "--workspace",
                str(workspace),
                "--json",
                "audit",
                "--prospective",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        result = json.loads(completed.stdout)["data"]
        with PlanStore(workspace) as reopened:
            persisted = reopened.passing_audit(manifest["planRevision"])
            assert persisted == result
            assert reopened.manifest_revision() == manifest["planRevision"]
        assert result["proofLevel"] == "prospective" and not result["integrationChecked"]
        assert result["integrationProof"] is None
    else:
        result = audit_prospective_tests(**prospective)
    assert result["ok"] is (case == "all-bound"), result["findings"]
    observed = result["redBaseline"][0]["failureProvenance"]["tests"][0]
    assert len(observed["events"]) == 2
    if case == "all-bound":
        assert observed["classification"] == "owned-cli-stub"
        bindings = [e["resultBinding"] for e in observed["events"]]
        assert len({b["launchId"] for b in bindings}) == 2
        assert all(b["resultUnchanged"] and b["currentEvent"] for b in bindings)
    else:
        assert observed["classification"] == "unproved"


@pytest.mark.parametrize("case", ["import-error", "timeout"])
def test_prospective_collection_failure_never_becomes_red_proof(prospective, monkeypatch, case):
    """Catch false CLI defects after collection fails; successful proofs miss absent execution."""
    proof = prospective["template"] / "tests/test_app.py"
    prefix = (
        "import absent_dependency\n"
        if case == "import-error"
        else "import os, time\n"
        "print('collection started pid=' + str(os.getpid()), flush=True)\ntime.sleep(3)\n"
    )
    proof.write_text(
        prefix + "import subprocess, sys, unittest\nfrom pathlib import Path\n"
        "class Proof(unittest.TestCase):\n    def test_cli(self):\n"
        "        result = subprocess.run([sys.executable, '-m', 'app'], "
        "cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)\n"
        "        self.assertEqual(result.returncode, 0)\n"
    )
    root = prospective["template"]
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    if case == "timeout":
        original_run = test_audit.run_audit_process

        def bounded_run(*args, **kwargs):
            kwargs["timeout"] = min(kwargs.get("timeout", 120), 1)
            return original_run(*args, **kwargs)

        # Exercise real discovery and OS timeout; no fabricated subprocess result.
        monkeypatch.setattr(test_audit, "run_audit_process", bounded_run)
    result = audit_prospective_tests(**prospective)
    assert not result["ok"]
    assert "prospective-not-collectable" in {f["code"] for f in result["findings"]}
    assert result["redBaseline"] == []
    assert all(not c["passed"] for c in result["collection"])
    for item in result["collection"]:
        markers = re.findall(
            r"^HARNESS_COLLECTION_STAGE (\S+) pid=(\d+)(?: count=(\d+))?$",
            item["output"],
            re.MULTILINE,
        )
        assert [stage for stage, _, _ in markers[:2]] == [
            "before-unittest-import",
            "before-discovery",
        ]
        assert len({pid for _, pid, _ in markers}) == 1
        if case == "timeout":
            assert len(markers) == 2  # Discovery entered the sleeping protected import.
            child_pid = re.search(r"collection started pid=(\d+)", item["output"])[1]
            assert markers[0][1] == child_pid
        else:
            assert markers[-1][0] == "discovery-complete"
            assert markers[-1][2] == "1"  # Loader's failed-test placeholder is not credit.
    if case == "timeout":
        assert all("collection started" in c["output"] for c in result["collection"])
    codes = {f["code"] for f in result["findings"]}
    assert "prospective-no-interface-assertion" not in codes
    assert "prospective-interface-unassessed" in codes
    assert "UNASSESSED" in render_test_audit_card(result)
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("environment", ["empty", "empty-no-python", "pythonpath", "output-only"])
def test_prospective_module_cli_preserves_explicit_empty_environment(
    prospective, monkeypatch, environment
):
    """Protect requested startup context; previous env={} regression missed absent PYTHON keys.

    Changed PYTHONPATH must refuse observation; supported output settings must still
    run. Both expose overly broad or ambient-dependent fixes to the empty-env bug.
    """
    if environment == "empty-no-python":
        for key in list(os.environ):
            if key.startswith("PYTHON"):
                monkeypatch.delenv(key)
    elif environment == "output-only":
        # The launcher already enforces no bytecode; only encoding changes.
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    requested = {
        "empty": "{}",
        "empty-no-python": "{}",
        "pythonpath": "{**os.environ, 'PYTHONPATH': 'unsupported-startup-path'}",
        "output-only": (
            "{**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}"
        ),
    }[environment]
    root = prospective["template"]
    (root / "app.py").write_text("raise NotImplementedError('planned CLI')\n")
    (root / "tests/test_app.py").write_text(
        "import os, subprocess, sys, unittest\nfrom pathlib import Path\n"
        "class Proof(unittest.TestCase):\n    def test_cli(self):\n"
        "        result = subprocess.run([sys.executable, '-m', 'app'], "
        "cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, env="
        + requested
        + ")\n"
        "        self.assertEqual(result.returncode, 0)\n"
    )
    result = audit_prospective_tests(**prospective)
    if environment == "output-only":
        assert result["ok"], result["findings"]
        launch = result["redBaseline"][0]["failureProvenance"]["tests"][0]["moduleLaunches"][0]
        assert launch["instrumented"]
        assert launch["requestedOutputEnvironment"] == {
            "PYTHONIOENCODING": "utf-8",
        }
        return
    assert not result["ok"], result["findings"]
    observed = result["redBaseline"][0]["failureProvenance"]
    assert observed["classification"] == "unproved"
    launch = observed["tests"][0]["moduleLaunches"][0]
    assert not launch.get("instrumented")
    assert "startup environment" in launch["reason"]


TS_FIXTURE = Path(__file__).parent / "fixtures/ts_runner"


@pytest.fixture
def typescript_plan(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("TypeScript fixture requires node on PATH")
    template = tmp_path / "template"
    shutil.copytree(TS_FIXTURE / "template", template)
    spine = load_spine(TS_FIXTURE / "spine.json")
    return dict(template=template, spine=spine, manifest=compile_manifest(spine, template))


def test_typescript_unproved_tap_baseline_and_oracle_upgrade(typescript_plan, tmp_path):
    from codex_harness.core.plan_store import PlanStore
    from codex_harness.core.runners import resolve_runner
    from codex_harness.core.test_audit import ORACLE_LABEL

    source = tmp_path / "spine.json"
    save_spine(typescript_plan["spine"], source)
    with PlanStore(tmp_path) as store:
        store.import_spine(source)
        manifest = store.compile_template(typescript_plan["template"])
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "codex_harness.core.plan_cli",
                "--workspace",
                str(tmp_path),
                "--json",
                "audit",
                "--prospective",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 2, result.stdout + result.stderr
        audit = json.loads(result.stdout)["data"]
        assert not audit["ok"] and audit["proofLevel"] == "prospective", audit
        assert {f["code"] for f in audit["findings"]} == {"prospective-red-unproved"}
        assert len(audit["collection"]) == 2
        assert all(item["passed"] for item in audit["collection"])
        assert len(audit["redBaseline"]) == 2
        for proof in audit["redBaseline"]:
            assert proof["exitCode"] == 1 and not proof["passed"]
            assert "not implemented" in proof["output"]
            assert proof["command"] == resolve_runner(manifest).argv_for(proof["module"])
            assert proof["failureProvenance"]["classification"] == "unproved"
            assert "Command/TAP" in proof["failureProvenance"]["reason"]
        assert store.proof_level() != "prospective"
        oracle = audit_revised_tests(
            repo=tmp_path,
            base_ref="template",
            revised_template=typescript_plan["template"],
            manifest=manifest,
            impact={"affectedBeads": ["SUM", "FORMAT"]},
            oracle_dir=TS_FIXTURE / "reference",
        )
        assert oracle["ok"], oracle
        assert oracle["integrationChecked"] and oracle["integrationProof"]["passed"]
        store.record_audit(manifest["planRevision"], oracle)
        assert store.proof_level() == "oracle-verified"
        assert store.status()["proofLevelLabel"] == ORACLE_LABEL
        assert all(
            "not implemented" in (typescript_plan["template"] / name).read_text()
            for name in ("src/sum.ts", "src/format.ts")
        )


@pytest.fixture
def revision(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "get_adapter", lambda *args: pytest.fail("real provider launch"))
    plan = tmp_path / "plan"
    shutil.copytree(FIXTURE, plan)
    old = load_spine(plan / "spine.json")
    (plan / "sample.json").write_text(json.dumps(compile_manifest(old, plan / "template")))
    integrated = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    assert integrated["status"] == STATUS_MOCK_ONLY
    repo = Path(integrated["repo"])
    base = _git(repo, "rev-parse", "HEAD")
    save_spine(old, tmp_path / "old-spine.json")
    data = old.to_dict()
    next(node for node in data["nodes"] if node["id"] == "D3")["text"] = "Round tip up."
    new = Spine(data)
    save_spine(new, plan / "spine.json")
    proof = plan / "template/tests/test_money.py"
    proof.write_text(
        proof.read_text()
        .replace("+ 50", "+ 99")
        .replace("sum(split(1001, 2, 15)), 1151", "sum(split(1001, 2, 15)), 1152")
        .replace("(111, 4, 10, [31, 31, 30, 30])", "(111, 4, 10, [31, 31, 31, 30])"),
        encoding="utf-8",
    )
    oracle = plan / "reference/tipsplit/money.py"
    oracle.write_text(oracle.read_text().replace("+ 50", "+ 99"), encoding="utf-8")
    return {
        "repo": repo,
        "base_ref": base,
        "revised_template": plan / "template",
        "manifest": compile_manifest(new, plan / "template"),
        "impact": impact(old, new),
        "oracle_dir": plan / "reference",
    }


def test_audit_correct_revision_passes_and_preserves_repo_and_plan(revision):
    repo = revision["repo"]
    before = {
        path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()
    }
    plan = revision["revised_template"].parent
    before_plan = {
        path.relative_to(plan): path.read_bytes() for path in plan.rglob("*") if path.is_file()
    }
    result = audit_revised_tests(**revision)
    assert result["ok"] and result["findings"] == []
    assert result["integrationChecked"] is True
    assert result["integrationReason"] is None
    assert result["integrationProof"]["passed"] is True
    assert result["integrationProof"]["module"] == "test_end_to_end.py"
    assert len(result["expectedFailures"]) == 1
    expected = result["expectedFailures"][0]
    assert (expected["bead"], expected["module"], expected["severity"]) == (
        "A",
        "test_money.py",
        "info",
    )
    assert "AssertionError" in expected["output"]
    assert before == {
        path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()
    }
    assert before_plan == {
        path.relative_to(plan): path.read_bytes() for path in plan.rglob("*") if path.is_file()
    }
    assert _git(repo, "rev-parse", "HEAD") == revision["base_ref"]
    assert _git(repo, "status", "--porcelain") == ""
    assert render_test_audit_card(result) == (
        "Test audit: PASS\nBaseline: "
        + revision["base_ref"]
        + "\n- info: expected-failures — A (test_money.py)\n"
    )


def test_audit_blocks_failing_oracle_integration(revision):
    proof = revision["revised_template"] / "tests/test_end_to_end.py"
    proof.write_text(proof.read_text().replace("$38.34", "$99.99"), encoding="utf-8")
    result = audit_revised_tests(**revision)
    assert result["ok"] is False
    assert result["integrationChecked"] is True
    assert result["integrationProof"]["passed"] is False
    assert [(item["bead"], item["severity"]) for item in result["findings"]] == [
        ("integration", "blocker")
    ]
    assert "$99.99" in result["integrationProof"]["output"]
