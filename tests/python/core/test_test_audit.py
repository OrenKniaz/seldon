"""Real Git snapshots and proof modules; providers are strictly stand-ins."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.python.core.planning_support import (
    source_only_b,
    source_only_confirmation,
    source_only_expansion,
    source_only_publication,
)

from codex_harness.core import dispatch, test_audit
from codex_harness.core.acceptance import derive_scenarios, run_scenarios
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows held-Job containment only")
@pytest.mark.parametrize("runner_kind", ["unittest", "command"])
def test_collection_stops_before_next_launch_after_retained_unknown_job(
    prospective, withheld_audit_bootstrap, monkeypatch, runner_kind
):
    """Catch overlapping collection after failed teardown; ordinary failures verify Job zero."""
    root = prospective["template"]
    (root / "tests/test_second.py").write_text(
        "import unittest\nfrom app import value\nclass Second(unittest.TestCase):\n"
        "    def test_value(self): self.assertEqual(value(), 42)\n"
    )
    if runner_kind == "command":
        data = prospective["spine"].to_dict()
        for node in data["nodes"]:
            if node["kind"] == "vision":
                node["fields"]["verification"] = {
                    "runner": "command",
                    "argv": [sys.executable, "{proof}"],
                    "listArgv": [sys.executable, "-c", "print('tests/test_app.py')", "{proof}"],
                    "testDir": "tests",
                    "proofGlob": "test*.py",
                    "assertPatterns": ["assert"],
                    "importPatterns": [r"from (\w+) import"],
                }
            if node["kind"] == "bead":
                node["fields"]["proofModule"] = "tests/test_app.py"
        prospective["spine"] = Spine(data)
    prospective["manifest"] = compile_manifest(prospective["spine"], root)
    withheld_audit_bootstrap["refuse"] = True
    real_run = test_audit.run_audit_process
    launches = []

    def one_real_launch(*args, **kwargs):
        assert not launches, "A second collection process was delegated after unknown ownership"
        launches.append(args[0])
        kwargs["timeout"] = 0.1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(test_audit, "run_audit_process", one_real_launch)
    result = audit_prospective_tests(**prospective)
    assert not result["ok"] and result["redBaseline"] == []
    assert len(launches) == len(result["collection"]) == 1
    control = result["collection"][0]["processControl"]
    assert control["membershipVerifiedBeforePayload"] and control["payloadReleased"] is False
    assert not control["quiescenceVerified"] and control["jobHandleRetained"]
    assert control["activeProcessesAfterTeardown"] is None


def test_public_empty_plan_audit_cannot_persist_passing_credit(prospective):
    """Catch collection-only audit credit; populated fixtures always supply red obligations.

    Exercise the public producer and reopen its real canonical writer, not a supplied result.
    """
    from tests.python.core.test_mcp_audit import finished_audit, invoke

    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.plan_store import open_plan_store_readonly

    root = prospective["template"]
    workspace = root.parent
    server = PlannerServer(workspace)
    try:
        assert not invoke(
            server, "harness_plan_start", plan_mode="legacy", idea="Draft only", name="Empty"
        )["isError"]
        vision = {**prospective["spine"].node("V"), "id": "V1"}
        assert not invoke(server, "harness_upsert_node", node=vision)["isError"]
        compiled = invoke(server, "harness_compile")
        assert not compiled["isError"], compiled
        reply = finished_audit(server, mode="prospective")
        result = reply["structuredContent"]
        assert all(item["passed"] for item in result["collection"])
        assert result["redBaseline"] == []
        assert result["ok"] is False, result
        assert reply["isError"]
        assert "prospective-no-proof-obligations" in {f["code"] for f in result["findings"]}
        revision = result["manifestRevision"]
    finally:
        server.close()
    with open_plan_store_readonly(workspace) as reopened:
        row = reopened.connection.execute(
            "SELECT passed, result_json FROM audit_runs WHERE id=?", (result["id"],)
        ).fetchone()
        assert row[0] == 0 and json.loads(row[1])["ok"] is False
        assert reopened.passing_audit(revision) is None
        assert reopened.proof_level(revision) is None


def test_prospective_checks_real_stubs_without_modifying_template(prospective):
    """Protect real red and collection facts; refusal-only tests miss count/identity drift."""
    root = prospective["template"]
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = audit_prospective_tests(**prospective)
    assert result["ok"], result["findings"]
    assert result["proofLevel"] == "prospective"
    assert not result["integrationChecked"] and result["integrationProof"] is None
    assert result["redBaseline"][0]["passed"] is False
    assert "NotImplementedError" in result["redBaseline"][0]["output"]
    assert all(item["passed"] for item in result["collection"])
    for item in result["collection"]:
        markers = re.findall(
            r"^HARNESS_COLLECTION_STAGE (\S+) pid=(\d+)(?: count=(\d+))?$",
            item["output"],
            re.MULTILINE,
        )
        assert [stage for stage, _, _ in markers] == [
            "before-unittest-import",
            "before-discovery",
            "discovery-complete",
        ]
        assert len({pid for _, pid, _ in markers}) == 1
        assert int(markers[0][1]) != os.getpid()
        assert markers[-1][2] == "1"
        assert "Collected tests: 1" in item["output"]
        execution = item["serverExecution"]
        assert execution["pid"] == os.getpid() and execution["sysExecutable"] == sys.executable
        assert execution["moduleOrigin"].endswith("test_audit.py")
        assert execution["sourceIdentity"]["scope"] == "on-disk-file"
        assert execution["sourceIdentity"]["isLoadedBytecodeAttestation"] is False
        assert execution["sourceIdentity"]["isCommitAttestation"] is False
        assert re.fullmatch(r"[0-9a-f]{64}", execution["sourceIdentity"]["sha256"])
        evidence = item["stageEvidence"]
        assert evidence["status"] == "read" and evidence["truncated"] is False
        assert evidence["output"].startswith("parent-created\nscript-entered\n")
        assert "discovery-complete" in evidence["output"]
        assert not Path(evidence["path"]).exists()
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_structural_test_links_do_not_supply_missing_clause_witnesses(prospective):
    """Five linked requirements still need their own executable acceptance evidence."""
    data = prospective["spine"].to_dict()
    clauses = [
        "List records",
        "Reject boolean IDs",
        "Keep exact amounts",
        "Reject invalid UTF-8",
        "Preserve failed writes",
    ]
    requirement_ids = [f"R{index}" for index in range(1, 6)]
    data["nodes"] = [node for node in data["nodes"] if node["id"] != "R"]
    data["nodes"].extend(
        dict(id=key, kind="requirement", title=clause, text=clause, parents=["F"], fields={})
        for key, clause in zip(requirement_ids, clauses, strict=True)
    )
    witness = {
        "title": clauses[0],
        "steps": ['"{python}" -B -m app list'],
        "expect": {"exit": 0, "stdoutRegex": r"\A\[\]\r?\n\Z", "stderrRegex": r"\A\Z"},
    }
    for node in data["nodes"]:
        if node["id"] in ("B", "T"):
            node["parents"] = requirement_ids.copy()
        elif node["id"] == "V":
            node["fields"].update(first_user_moment=witness, scope_in=[witness, *clauses[1:]])
    spine = Spine(data)
    assert spine.coverage_gaps() == []  # This checks graph links, not clause semantics.
    assert len(spine.ids("test")) == 1
    (prospective["template"] / "app.py").write_text("print('[]')\n", encoding="utf-8")
    scenarios = derive_scenarios(spine)
    results = {item["id"]: item for item in run_scenarios(prospective["template"], scenarios)}
    assert results["first-moment"]["passed"] is True
    assert results["scope-1"]["passed"] is True
    for index, clause in enumerate(clauses[1:], 2):
        result = results[f"scope-{index}"]
        assert result["title"] == clause
        assert result["passed"] is False
        assert result["steps"] == []
        assert result["error"] == "No executable steps supplied for this vision promise"
    assert not all(item["passed"] for item in results.values())


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


@pytest.mark.parametrize(
    "case",
    [
        "overwritten",
        "unrelated",
        "stderr-spoof",
        "import-error",
        "foreign-module",
        "startup-flag",
        "startup-env",
        "setup-launch",
        "sitecustomize",
        "usercustomize",
    ],
)
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


def test_prospective_module_cli_preserves_simple_helper_result(prospective):
    """One straight-line helper may return its actual CompletedProcess unchanged."""
    root = prospective["template"]
    (root / "app.py").write_text(
        "def _entry():\n    raise NotImplementedError('planned CLI')\n"
        "if __name__ == '__main__':\n    _entry()\n"
    )
    (root / "tests/test_app.py").write_text(
        "import subprocess, sys, unittest\nfrom pathlib import Path\n"
        "def invoke():\n"
        "    result = subprocess.run([sys.executable, '-m', 'app'], "
        "cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)\n"
        "    return result\n"
        "class Proof(unittest.TestCase):\n"
        "    def test_cli(self):\n        result = invoke()\n"
        "        self.assertEqual(result.returncode, 0)\n"
    )
    result = audit_prospective_tests(**prospective)
    assert result["ok"], json.dumps(
        result["redBaseline"][0]["failureProvenance"]["tests"][0], indent=2
    )
    assert (
        result["redBaseline"][0]["failureProvenance"]["tests"][0]["classification"]
        == "owned-cli-stub"
    )


@pytest.mark.parametrize("change", ["unknown-finder", "runtime-function", "path-hook"])
def test_prospective_module_cli_refuses_changed_startup_identity(prospective, change):
    """Only the exact reviewed runtime exception may extend standard startup."""
    root = prospective["template"]
    (root / "app.py").write_text("raise NotImplementedError('planned CLI')\n")
    mutations = {
        "unknown-finder": "sys.meta_path.append(UnknownFinder())",
        "runtime-function": "_virtualenv._Finder.find_spec = replacement",
        "path-hook": "sys.path_hooks[1] = other_hook",
    }
    (root / "tests/test_app.py").write_text(
        "import subprocess, sys, unittest, _virtualenv\nfrom pathlib import Path\n"
        "class UnknownFinder:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n        return None\n"
        "def replacement(self, fullname, path=None, target=None):\n    return None\n"
        "def other_hook(path):\n    return None\n"
        "class Proof(unittest.TestCase):\n    def test_cli(self):\n        "
        + mutations[change]
        + "\n"
        "        result = subprocess.run([sys.executable, '-m', 'app'], "
        "cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)\n"
        "        self.assertEqual(result.returncode, 0)\n"
    )
    result = audit_prospective_tests(**prospective)
    assert not result["ok"]
    observed = result["redBaseline"][0]["failureProvenance"]
    assert observed["classification"] == "unproved"
    launch = observed["tests"][0]["moduleLaunches"][0]
    assert not launch.get("instrumented")
    assert any(word in launch["reason"] for word in ("finder", "hook", "function", "identity"))


@pytest.mark.parametrize("shape", ["chain", "branch", "loop", "package"])
def test_prospective_class_cli_fresh_result_reaches_helper_assertion(prospective, shape):
    """Catch rejection of real helper red; direct/module helpers miss class frames and flow."""
    root = prospective["template"]
    (root / "app.py").write_text("raise NotImplementedError('planned CLI')\n")
    module = "app"
    if shape == "package":
        module = "tally"
        (root / "tally").mkdir()
        (root / "tally/__init__.py").write_text("")
        (root / "tally/__main__.py").write_text("raise NotImplementedError('planned CLI')\n")
        data = prospective["spine"].to_dict()
        next(n for n in data["nodes"] if n["id"] == "B")["fields"]["ownedFiles"] = [
            "tally/__init__.py",
            "tally/__main__.py",
        ]
        prospective["spine"] = Spine(data)
    bodies = {
        "chain": "        self.success(self.command())\n",
        "branch": "        if True:\n            self.success(self.command())\n",
        "loop": (
            "        for command in ('list', 'total'):\n"
            "            self.success(self.command(command))\n"
        ),
    }
    (root / "tests/test_app.py").write_text(
        "import subprocess, sys, unittest\nfrom pathlib import Path\n"
        "class Proof(unittest.TestCase):\n"
        "    def invoke(self, command='list'):\n"
        f"        return subprocess.run([sys.executable, '-m', '{module}', command], "
        "cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)\n"
        "    def command(self, command='list'):\n        return self.invoke(command)\n"
        "    def success(self, result):\n"
        "        self.assertEqual(result.returncode, 0, result.stderr)\n"
        "        return result.stdout\n"
        "    def test_cli(self):\n" + bodies.get(shape, bodies["chain"])
    )
    # Consume the real compiler's manifest, not manually supplied ownership/proof facts.
    prospective["manifest"] = compile_manifest(prospective["spine"], root)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = audit_prospective_tests(**prospective)
    assert result["ok"], result["findings"]
    observed = result["redBaseline"][0]["failureProvenance"]["tests"][0]
    assert observed["classification"] == "owned-cli-stub"
    assert [f["function"] for f in observed["moduleLaunches"][0]["callers"]] == [
        "invoke",
        "command",
        "test_cli",
    ]
    assert observed["moduleLaunches"][0]["child"]["exception"]["type"] == "NotImplementedError"
    assert observed["moduleLaunches"][0]["child"]["targetModule"] == (
        "tally.__main__" if shape == "package" else "app"
    )
    assert not result["integrationChecked"] and result["integrationProof"] is None
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "case",
    [
        "mutated",
        "opaque",
        "message-only",
        "foreign",
        "foreign-package",
        "stored-result",
        "unreached",
    ],
)
def test_prospective_class_cli_refuses_unbound_result_failures(prospective, case):
    """Protect helper lineage: old direct-call refusal cases miss class return/assert frames."""
    root = prospective["template"]
    (root / "app.py").write_text("raise NotImplementedError('planned CLI')\n")
    (root / "foreign.py").write_text("raise NotImplementedError('unowned CLI')\n")
    command = "foreign" if case in {"foreign", "foreign-package"} else "app"
    if case == "foreign-package":
        (root / "foreign.py").unlink()
        (root / "foreign").mkdir()
        (root / "foreign/__init__.py").write_text("")
        (root / "foreign/__main__.py").write_text("raise NotImplementedError('unowned package')\n")
    launch = (
        f"subprocess.run([sys.executable, '-m', '{command}'], "
        "cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)"
    )
    invocation = "        return " + launch + "\n"
    check = "        self.assertEqual(result.returncode, 0, result.stderr)\n"
    test = "        self.success(self.invoke())\n"
    if case == "mutated":
        check = "        result.returncode = 42\n" + check
    elif case == "opaque":
        invocation = (
            "        result = " + launch + "\n"
            "        setattr(result, 'returncode', 42)\n        return result\n"
        )
    elif case == "message-only":
        check = "        self.assertEqual(1, 0, result.stderr)\n"
    elif case == "stored-result":
        test = (
            "        result = self.invoke()\n        result.returncode = 42\n"
            "        for item in (1, 2):\n            self.success(result)\n"
        )
    elif case == "unreached":
        test = "        if False:\n            self.success(self.invoke())\n"
    (root / "tests/test_app.py").write_text(
        "import subprocess, sys, unittest\nfrom pathlib import Path\n"
        "class Proof(unittest.TestCase):\n    def invoke(self):\n"
        + invocation
        + "    def success(self, result):\n"
        + check
        + "    def test_cli(self):\n"
        + test
    )
    prospective["manifest"] = compile_manifest(prospective["spine"], root)
    result = audit_prospective_tests(**prospective)
    assert not result["ok"]
    assert all(item["passed"] for item in result["collection"])
    observed = result["redBaseline"][0]["failureProvenance"]
    assert observed["classification"] == "unproved"
    assert observed["tests"][0]["classification"] == "unproved"
    assert bool(observed["tests"][0]["moduleLaunches"]) is (case != "unreached")


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


@pytest.mark.parametrize("stage_fault", [None, "absent", "oversized", "read-error"])
def test_collection_stage_file_survives_suppressed_stdout_and_real_timeout(
    prospective, monkeypatch, stage_fault
):
    """Catch lost/unbounded diagnostics; existing timeout tests retain working stdout/files."""
    root = prospective["template"]
    proof = root / "tests/test_app.py"
    fault = {
        None: "",
        "absent": "        marker.unlink()\n",
        "oversized": "        marker.write_bytes(b'X' * 5000)\n",
        "read-error": "        marker.unlink()\n        marker.mkdir()\n",
    }[stage_fault]
    proof.write_text(
        "import __main__, time\nfrom pathlib import Path\n"
        "if hasattr(__main__, '_stage_path'):\n"
        "    marker = Path(__main__._stage_path)\n"
        "    if marker.exists():\n"
        + (fault or "        pass\n")
        + "    time.sleep(3)\n"
        + proof.read_text()
    )
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    original_run = test_audit.run_audit_process

    def suppress_stdout(*args, **kwargs):
        command = list(args[0])
        command[3] = command[3].replace(
            "import os, sys\n", "import os, sys\nsys.stdout = open(os.devnull, 'w')\n", 1
        )
        kwargs["timeout"] = min(kwargs.get("timeout", 120), 1)
        return original_run(command, *args[1:], **kwargs)

    monkeypatch.setattr(test_audit, "run_audit_process", suppress_stdout)
    result = audit_prospective_tests(**prospective)
    assert not result["ok"] and not result["redBaseline"]
    paths = []
    for item in result["collection"]:
        assert not item["passed"] and "timed out" in item["output"]
        assert not re.search(r"^HARNESS_COLLECTION_STAGE", item["output"], re.MULTILINE)
        evidence = item["stageEvidence"]
        assert evidence["status"] == (
            stage_fault if stage_fault in ("absent", "read-error") else "read"
        )
        if stage_fault is None:
            assert evidence["truncated"] is False
            assert evidence["output"].startswith("parent-created\nscript-entered\n")
            assert "before-unittest-import" in evidence["output"]
            assert "before-discovery" in evidence["output"]
            assert "discovery-complete" not in evidence["output"]
        elif stage_fault == "oversized":
            assert evidence["truncated"] is True and evidence["output"] == "X" * 4096
        elif stage_fault == "read-error":
            assert evidence["error"] in ("PermissionError", "IsADirectoryError")
        path = Path(evidence["path"])
        assert path.parent == Path(item["cwd"]).parent
        assert not path.exists()  # Evidence was read before its owning temporary root retired.
        paths.append(path)
    assert len(set(paths)) == len(paths) == 2
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_prospective_module_cli_refuses_mutated_result(prospective):
    """A field overwrite must not borrow the real child's earlier stub lineage."""
    root = prospective["template"]
    (root / "app.py").write_text("raise NotImplementedError('planned CLI')\n")
    (root / "tests/test_app.py").write_text(
        "import subprocess, sys, unittest\nfrom pathlib import Path\n"
        "class Proof(unittest.TestCase):\n    def test_cli(self):\n"
        "        result = subprocess.run([sys.executable, '-m', 'app'], "
        "cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)\n"
        "        result.returncode = 42\n"
        "        self.assertEqual(result.returncode, 0)\n"
    )
    result = audit_prospective_tests(**prospective)
    assert not result["ok"], result["findings"]
    assert result["redBaseline"][0]["failureProvenance"]["classification"] == "unproved"


def test_prospective_module_cli_refuses_assert_named_mutator(prospective):
    """An assert-prefixed class helper cannot mutate the observed result."""
    root = prospective["template"]
    (root / "app.py").write_text("raise NotImplementedError('planned CLI')\n")
    (root / "tests/test_app.py").write_text(
        "import subprocess, sys, unittest\nfrom pathlib import Path\n"
        "class Proof(unittest.TestCase):\n"
        "    def assertMutate(self, result):\n"
        "        setattr(result, 'returncode', 42)\n"
        "    def test_cli(self):\n"
        "        result = subprocess.run([sys.executable, '-m', 'app'], "
        "cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)\n"
        "        self.assertMutate(result)\n"
        "        self.assertEqual(result.returncode, 0)\n"
    )
    result = audit_prospective_tests(**prospective)
    assert not result["ok"], result["findings"]
    assert result["redBaseline"][0]["failureProvenance"]["classification"] == "unproved"


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
        # The observation reports a delta, so ensure both requested values change.
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
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
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return
    assert not result["ok"], result["findings"]
    observed = result["redBaseline"][0]["failureProvenance"]
    assert observed["classification"] == "unproved"
    launch = observed["tests"][0]["moduleLaunches"][0]
    assert not launch.get("instrumented")
    assert "startup environment" in launch["reason"]


@pytest.mark.parametrize(
    "scope,mutation",
    [
        ("direct", "alias"),
        ("direct", "subscript"),
        ("direct", "setattr"),
        ("direct", "update"),
        ("helper", "attribute"),
        ("helper", "alias"),
        ("helper", "subscript"),
        ("helper", "setattr"),
        ("helper", "update"),
    ],
    ids=[
        "direct-alias",
        "direct-subscript",
        "direct-setattr",
        "direct-update",
        "helper-attribute",
        "helper-alias",
        "helper-subscript",
        "helper-setattr",
        "helper-update",
    ],
)
def test_prospective_module_cli_refuses_opaque_result_mutation(prospective, scope, mutation):
    """Aliases and opaque mutators cannot launder an actual child into fake red."""
    root = prospective["template"]
    (root / "app.py").write_text("raise NotImplementedError('planned CLI')\n")
    mutations = {
        "attribute": "result.returncode = 42",
        "alias": "alias = result\nalias.returncode = 42",
        "subscript": "result.__dict__['returncode'] = 42",
        "setattr": "setattr(result, 'returncode', 42)",
        "update": "result.__dict__.update(returncode=42)",
    }
    launch = (
        "result = subprocess.run([sys.executable, '-m', 'app'], "
        "cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)"
    )
    code = "import subprocess, sys, unittest\nfrom pathlib import Path\n"
    statements = launch + "\n" + mutations[mutation]
    if scope == "helper":
        code += "def invoke():\n" + "\n".join("    " + line for line in statements.splitlines())
        code += "\n    return result\nclass Proof(unittest.TestCase):\n    def test_cli(self):\n"
        code += "        result = invoke()\n"
    else:
        code += "class Proof(unittest.TestCase):\n    def test_cli(self):\n"
        code += "\n".join("        " + line for line in statements.splitlines()) + "\n"
    code += "        self.assertEqual(result.returncode, 0)\n"
    (root / "tests/test_app.py").write_text(code)
    result = audit_prospective_tests(**prospective)
    assert not result["ok"], result["findings"]
    assert result["redBaseline"][0]["failureProvenance"]["classification"] == "unproved"


@pytest.mark.parametrize(
    "body,code",
    [
        ("self.assertEqual(42, 42)", "prospective-vacuous-proof"),
        ("value()", "prospective-no-assertion"),
        ("self.assertEqual(", "prospective-syntax-error"),
        ("from missing import value", "prospective-not-collectable"),
    ],
)
def test_prospective_refuses_bad_proofs(prospective, body, code):
    proof = prospective["template"] / "tests/test_app.py"
    proof.write_text(proof.read_text().replace("self.assertEqual(value(), 42)", body))
    if body == "from missing import value":
        proof.write_text(proof.read_text().replace("from app import value", body))
    result = audit_prospective_tests(**prospective)
    assert result["ok"] is False
    assert code in {finding["code"] for finding in result["findings"]}
    if body == "self.assertEqual(42, 42)":
        # Missing execution must be unassessed; a completed unrelated assertion is a real defect.
        assert all(item["passed"] for item in result["collection"])
        assert "prospective-no-interface-assertion" in {f["code"] for f in result["findings"]}
        assert "prospective-interface-unassessed" not in {f["code"] for f in result["findings"]}


def test_prospective_requires_test_traceability(prospective):
    data = prospective["spine"].to_dict()
    data["nodes"] = [node for node in data["nodes"] if node["id"] != "T"]
    prospective["spine"] = Spine(data)
    result = audit_prospective_tests(**prospective)
    assert not result["ok"]
    assert "prospective-traceability-gap" in {f["code"] for f in result["findings"]}


def test_prospective_skipped_tests_still_need_assertions(prospective):
    proof = prospective["template"] / "tests/test_app.py"
    proof.write_text(
        proof.read_text().replace(
            "    def test_value", "    @unittest.skip('until implemented')\n    def test_value"
        )
    )
    result = audit_prospective_tests(**prospective)
    assert not result["ok"]  # All skipped is green, not a red baseline.
    assert result["findings"][0]["code"] == "prospective-vacuous-proof"
    assert result["redBaseline"][0]["failureProvenance"]["classification"] == "unproved"
    proof.write_text(proof.read_text().replace("self.assertEqual(value(), 42)", "pass"))
    result = audit_prospective_tests(**prospective)
    assert not result["ok"]
    assert "prospective-no-assertion" in {f["code"] for f in result["findings"]}
    assert result["redBaseline"][0]["failureProvenance"]["classification"] == "unproved"


def test_prospective_publication_cli_mcp_status_and_oracle_upgrade(prospective, tmp_path):
    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.plan_quality import quality_report
    from codex_harness.core.plan_store import PHASES, PlanStore, PlanStoreError
    from codex_harness.core.roundtable import run_roundtable
    from codex_harness.core.status_api import snapshot
    from codex_harness.core.test_audit import ORACLE_LABEL, PROSPECTIVE_LABEL

    source = tmp_path / "spine.json"
    save_spine(prospective["spine"], source)
    with PlanStore(tmp_path) as store:
        store.import_spine(source)
        source_only_expansion(store)
        manifest = store.compile_template(prospective["template"])
        prospective["manifest"] = manifest
        while store.phase() != "roundtable":
            phase = store.phase()
            if phase in ("align", "vision-lock"):
                source_only_confirmation(store, phase, statement="Approve")
            if phase == "plan":
                source_only_b(store)
            store.advance(PHASES[PHASES.index(phase) + 1], by="test")
        manifest = store._manifest(store.spine())
        prospective["manifest"] = manifest
        prospective["spine"] = store.spine()
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
        assert result.returncode == 0, result.stdout + result.stderr
        bound = json.loads(result.stdout)["data"]
        assert bound["proofLevel"] == "prospective"
        assert bound["prospectiveAuditContract"] == "captured-input-v1"
        from codex_harness.core.plan_store import open_plan_store_readonly

        with open_plan_store_readonly(tmp_path) as reopened:
            receipt = reopened.audit_attempt(bound["attemptId"])
            assert receipt["state"] == "completed" and receipt["proofCreditGranted"]
            assert receipt["auditId"] == bound["id"]
        run_roundtable(
            store,
            reviewers=[{"name": "test-review", "provider": "codex"}],
            runner=lambda *args: (0, "[]"),
            executable_for=lambda provider: provider,
        )
        # Injected reviews remain MOCKED and cannot satisfy real publication.
        assert not store.successful_reviews(manifest["planRevision"])
        with pytest.raises(PlanStoreError, match="successful review"):
            store.advance("publish", by="test")
        # SOURCE-ONLY export/upgrade mechanics: the existing evidence double
        # still requires actual audits, mocked flags and every other gate.
        with source_only_publication():
            store.advance("publish", by="test")
            for flags in ([], ["--proof-level", "prospective"], ["--reason", "No oracle"]):
                refused_cli = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "codex_harness.core.plan_cli",
                        "--workspace",
                        str(tmp_path),
                        "--json",
                        "publish",
                        *flags,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                assert refused_cli.returncode == 2, refused_cli.stdout + refused_cli.stderr
                assert json.loads(refused_cli.stdout)["isError"]
            for kwargs, code in (
                ({}, "plan-proof-level-required"),
                ({"proof_level": "prospective"}, "plan-prospective-reason-required"),
                ({"proof_level": "prospective", "reason": " "}, "plan-prospective-reason-required"),
            ):
                with pytest.raises(PlanStoreError) as caught:
                    store.publish(by="test", export_dir=tmp_path / "plan", **kwargs)
                assert caught.value.code == code
            server = PlannerServer(tmp_path)
            try:
                refused = server.call_tool("harness_publish", {})
                assert refused["isError"]
                published = server.call_tool(
                    "harness_publish",
                    {
                        "proof_level": "prospective",
                        "reason": "A full reference implementation is impractical",
                    },
                )
                assert not published["isError"], published
            finally:
                server.close()
            revision = store.status()["published_revision"]
            frozen = store.published_manifest(revision)
            assert frozen["proofLevel"] == "prospective"
            assert frozen["proofLevelReason"] == "A full reference implementation is impractical"
            assert frozen["sourceManifestRevision"] == manifest["planRevision"]
            assert store.status()["proofLevelLabel"] == PROSPECTIVE_LABEL
            assert store.status()["publication"]["up_to_date"]
            for name in store.rendered_docs():
                assert (
                    (tmp_path / "plan" / revision / name)
                    .read_text(encoding="utf-8")
                    .startswith(PROSPECTIVE_LABEL)
                )
            projected = snapshot(workspace=tmp_path)["plan"]
            assert projected["proofLevel"] == "prospective"
            assert projected["proofLevelLabel"] == PROSPECTIVE_LABEL
            check = next(
                check for check in quality_report(store)["checks"] if check["id"] == "proof-level"
            )
            assert check["status"] == "warn" and "first real proof" in check["advice"]
            store.advance("execute", by="test")
            oracle = tmp_path / "oracle"
            oracle.mkdir()
            (oracle / "app.py").write_text("def value(): return 42\n")
            upgraded = audit_revised_tests(
                repo=tmp_path,
                base_ref="template",
                revised_template=prospective["template"],
                manifest=manifest,
                impact={"affectedBeads": ["B"]},
                oracle_dir=oracle,
            )
            assert upgraded["ok"], upgraded
            store.record_audit(manifest["planRevision"], upgraded)
            assert store.status()["proofLevelLabel"] == ORACLE_LABEL
            assert not store.status()["publication"]["up_to_date"]
            # A later prospective audit cannot silently downgrade stronger evidence.
            store.record_audit(manifest["planRevision"], audit_prospective_tests(**prospective))
            assert store.proof_level() == "oracle-verified"
            store.advance("publish", by="test")
            oracle_revision = store.publish(by="test", export_dir=tmp_path / "plan")
            assert oracle_revision == manifest["planRevision"]
            assert store.published_manifest(oracle_revision) == manifest  # Existing oracle bytes.
            assert store.published_manifest(revision) == frozen  # Earlier publication stays honest.
        assert not store.successful_reviews(manifest["planRevision"])


@pytest.mark.parametrize("missing", ["listArgv", "assertPatterns", "importPatterns"])
def test_prospective_command_runner_refuses_missing_collection_contract(prospective, missing):
    prospective["manifest"]["verification"] = {
        "runner": "command",
        "argv": [sys.executable, "{proof}"],
        "testDir": "tests",
        "proofGlob": "*.py",
        "listArgv": [sys.executable, "{proof}"],
        "assertPatterns": [r"assert\w*\("],
        "importPatterns": [r"from (\w+) import"],
    }
    del prospective["manifest"]["verification"][missing]
    prospective["manifest"]["beads"]["B"]["proofModule"] = "tests/test_app.py"
    result = audit_prospective_tests(**prospective)
    assert not result["ok"]
    finding = next(f for f in result["findings"] if f["module"] == missing)
    assert finding["code"] == "prospective-runner-contract-missing"
    assert missing in finding["output"]
    assert result["collection"] == result["redBaseline"] == []


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


@pytest.mark.parametrize(
    "old,new,code",
    [
        ("assert.equal(sum(2, 3), 5);", "sum(2, 3);", "prospective-no-assertion"),
        (
            "import { sum } from '../src/sum.ts';",
            "import { format as sum } from '../src/format.ts';",
            "prospective-no-interface-assertion",
        ),
        (
            "import { sum } from '../src/sum.ts';",
            "const sum = () => { throw new Error('unrelated'); };",
            "prospective-no-interface-assertion",
        ),
        ("assert.equal(sum(2, 3), 5);", "assert.equal(5, 5);", "prospective-vacuous-proof"),
        ("../src/sum.ts", "../src/missing.ts", "prospective-not-collectable"),
        ("assert.equal(sum(2, 3), 5);", "assert.equal(", "prospective-not-collectable"),
    ],
)
def test_typescript_refuses_bad_proofs(typescript_plan, old, new, code):
    proof = typescript_plan["template"] / "tests/sum.test.ts"
    proof.write_text(proof.read_text().replace(old, new), encoding="utf-8")
    audit = audit_prospective_tests(**typescript_plan)
    assert not audit["ok"]
    assert code in {finding["code"] for finding in audit["findings"]}, audit


def test_typescript_requires_traceability(typescript_plan):
    data = typescript_plan["spine"].to_dict()
    data["nodes"] = [node for node in data["nodes"] if node["id"] != "T_SUM"]
    typescript_plan["spine"] = Spine(data)
    audit = audit_prospective_tests(**typescript_plan)
    assert not audit["ok"]
    assert "prospective-traceability-gap" in {f["code"] for f in audit["findings"]}


@pytest.mark.parametrize("cwd", [".", "src"])
def test_typescript_lists_all_proofs_in_one_command(typescript_plan, cwd):
    config = typescript_plan["manifest"]["verification"]
    config["cwd"] = cwd
    prefix = "../" if cwd == "src" else ""
    config["listArgv"][-1:] = [prefix + "tests/*.test.ts"]
    audit = audit_prospective_tests(**typescript_plan)
    assert not audit["ok"], audit
    assert {f["code"] for f in audit["findings"]} == {"prospective-red-unproved"}
    assert len(audit["collection"]) == 1


@pytest.mark.parametrize(
    "listing",
    [
        "tests/sum.test.ts",
        "TAP version 13\nnot ok 1 - tests/sum.test.ts\nok 2 - tests/format.test.ts",
    ],
)
def test_typescript_rejects_incomplete_or_failed_listing_with_zero_exit(typescript_plan, listing):
    typescript_plan["manifest"]["verification"]["listArgv"] = [
        sys.executable,
        "-c",
        f"print({listing!r})",
    ]
    audit = audit_prospective_tests(**typescript_plan)
    assert not audit["ok"] and not audit["redBaseline"]
    assert "prospective-not-collectable" in {f["code"] for f in audit["findings"]}


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


def test_audit_flags_demo_stale_half_up_row(revision):
    proof = revision["revised_template"] / "tests/test_money.py"
    proof.write_text(
        proof.read_text().replace("sum(split(1001, 2, 15)), 1152", "sum(split(1001, 2, 15)), 1151"),
        encoding="utf-8",
    )
    result = audit_revised_tests(**revision)
    assert not result["ok"]
    assert [
        (finding["code"], finding["severity"], finding["bead"]) for finding in result["findings"]
    ] == [("revised-tests-unsatisfiable", "blocker", "A")]
    assert "1152 != 1151" in result["findings"][0]["output"]
    assert [finding["bead"] for finding in result["expectedFailures"]] == ["A"]


def test_oracle_verdict_survives_owned_workspace_cleanup_failure(revision, monkeypatch):
    """Catch discarded oracle verdicts; public prospective cases never use this workspace.

    Only cleanup of a new owned oracle fixture is injected; real validation runs.
    """
    from tempfile import TemporaryDirectory

    original_cleanup = TemporaryDirectory.cleanup
    owned = []

    def cleanup(directory):
        if Path(directory.name).name.startswith("harness-test-audit-"):
            if directory not in owned:
                owned.append(directory)
            raise PermissionError(32, "Controlled oracle cleanup fault", directory.name)
        return original_cleanup(directory)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(TemporaryDirectory, "cleanup", cleanup)
            result = audit_revised_tests(**revision)
        assert result["ok"] and result["integrationChecked"]
        assert result["integrationProof"]["passed"]
        warning = result["cleanupWarning"][0]
        assert warning["error"]["kind"] == "PermissionError" and warning["attempts"] == 3
        assert warning["leftoverPath"] == owned[0].name
        assert Path(warning["leftoverPath"]).is_dir()
    finally:
        for directory in owned:
            original_cleanup(directory)


def test_proof_launch_error_keeps_cleanup_warning(prospective, monkeypatch):
    """Catch lost nested cleanup evidence; successful cleanup tests miss launch refusal."""
    from codex_harness.core import runners

    base = runners.TemporaryDirectory
    owned = []

    class CleanupFault(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            owned.append(self)

        def cleanup(self):
            raise PermissionError(32, "Controlled proof cleanup fault", self.name)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(runners, "TemporaryDirectory", CleanupFault)
            missing = str(prospective["template"] / "absent-python.exe")
            patch.setattr(sys, "executable", missing)
            proof = runners.validate_proof(
                prospective["template"], prospective["manifest"], "test_app.py"
            )
        assert not proof["passed"] and proof["exitCode"] is None
        assert proof["command"][0] == missing and proof["output"]
        warning = proof["cleanupWarning"][0]
        assert warning["error"]["kind"] == "PermissionError"
        assert warning["leftoverPath"] == owned[0].name
    finally:
        for directory in owned:
            base.cleanup(directory)


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


def test_audit_rejects_affected_dependent_cli_failure(revision):
    """Protect oracle satisfiability for B after canonical A-to-B dependency impact."""
    proof = revision["revised_template"] / "tests/test_cli.py"
    proof.write_text(proof.read_text().replace('"$12.34"', '"$12.35"'), encoding="utf-8")
    result = audit_revised_tests(**revision)
    assert not result["ok"]
    assert [
        (finding["code"], finding["severity"], finding["bead"]) for finding in result["findings"]
    ] == [("revised-tests-unsatisfiable", "blocker", "B")]
    assert [finding["bead"] for finding in result["expectedFailures"]] == ["A", "B"]


def test_audit_rejects_unaffected_independent_proof_failure(revision):
    """Protect unexpected-failure for independent C; dependent B no longer covers it."""
    repo, template, oracle = (revision[key] for key in ("repo", "revised_template", "oracle_dir"))
    source = "def value(): return 42\n"
    proof = (
        "import unittest\nfrom independent import value\nclass Proof(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(value(), 42)\n"
    )
    for root in (repo, template, oracle):
        (root / "independent.py").write_text(source, encoding="utf-8")
    (repo / "tests/test_independent.py").write_text(proof, encoding="utf-8")
    _git(repo, "add", "independent.py", "tests/test_independent.py")
    _git(repo, "commit", "-m", "Independent audit fixture baseline")
    (template / "tests/test_independent.py").write_text(
        proof.replace("value(), 42", "value(), 43"), encoding="utf-8"
    )
    old = load_spine(FIXTURE / "spine.json").to_dict()
    new = load_spine(template.parent / "spine.json").to_dict()
    for data in (old, new):
        data["nodes"].extend(
            [
                dict(
                    id="RC",
                    kind="requirement",
                    title="Independent value",
                    text="Value 42",
                    parents=["F1"],
                    fields={},
                ),
                dict(
                    id="C",
                    kind="bead",
                    title="Independent",
                    text="Independent value",
                    parents=["RC"],
                    fields={
                        "ownedFiles": ["independent.py"],
                        "dependsOn": [],
                        "proofModule": "test_independent.py",
                        "lessons": [],
                    },
                ),
                dict(
                    id="TC",
                    kind="test",
                    title="Independent proof",
                    text="Value 42",
                    parents=["RC"],
                    fields={"module": "test_independent.py", "level": "unit"},
                ),
            ]
        )
    old, new = Spine(old), Spine(new)
    actual_impact = impact(old, new)
    assert actual_impact["affectedBeads"] == ["A", "B"]
    assert "C" in actual_impact["untouchedBeads"]
    result = audit_revised_tests(
        **{
            **revision,
            "base_ref": _git(repo, "rev-parse", "HEAD"),
            "manifest": compile_manifest(new, template),
            "impact": actual_impact,
        }
    )
    assert [(row["code"], row["severity"], row["bead"]) for row in result["findings"]] == [
        ("unexpected-failure", "major", "C")
    ]
    assert [row["bead"] for row in result["expectedFailures"]] == ["A"]


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


def test_audit_without_oracle_does_not_claim_satisfiability(revision):
    revision["oracle_dir"] = None
    result = audit_revised_tests(**revision)
    assert result["ok"] and not result["findings"]
    assert result["integrationChecked"] is False
    assert "No oracle" in result["integrationReason"]
    assert result["integrationProof"] is None
    assert "Integration not checked:" in render_test_audit_card(result)
    assert [finding["bead"] for finding in result["expectedFailures"]] == ["A"]


def test_audit_uses_base_ref_instead_of_dirty_working_tree(revision):
    repo = revision["repo"]
    source = repo / "tipsplit/money.py"
    source.write_text("raise RuntimeError('dirty source must not run')\n")
    result = audit_revised_tests(**revision)
    assert result["ok"] and result["expectedFailures"][0]["bead"] == "A"
    assert source.read_text() == "raise RuntimeError('dirty source must not run')\n"


def test_audit_card_deterministic_sort_and_cli_exit_codes(revision, tmp_path):
    result = {
        "ok": False,
        "findings": [
            {
                "severity": "major",
                "code": "unexpected-failure",
                "bead": "B",
                "module": "test_cli.py",
            },
            {
                "severity": "blocker",
                "code": "revised-tests-unsatisfiable",
                "bead": "A",
                "module": "test_money.py",
            },
        ],
        "expectedFailures": [],
    }
    assert render_test_audit_card(result) == render_test_audit_card(
        {**result, "findings": list(reversed(result["findings"]))}
    )
    assert render_test_audit_card({"ok": True, "findings": [], "expectedFailures": []}) == (
        "Test audit: PASS\nNo findings or newly failing proofs.\n"
    )
    plan = revision["revised_template"].parent
    command = [
        sys.executable,
        "-B",
        "-m",
        "codex_harness.core.test_audit",
        "--repo",
        str(revision["repo"]),
        "--base",
        revision["base_ref"],
        "--plan",
        str(plan),
        "--old-spine",
        str(tmp_path / "old-spine.json"),
        "--oracle",
        str(revision["oracle_dir"]),
    ]
    passed = subprocess.run(command, capture_output=True, text=True)
    assert passed.returncode == 0, passed.stderr
    assert passed.stdout.startswith("Test audit: PASS\n")
    proof = revision["revised_template"] / "tests/test_money.py"
    proof.write_text(proof.read_text().replace("1152", "1151"), encoding="utf-8")
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode == 2, failed.stderr
    assert "blocker: revised-tests-unsatisfiable" in failed.stdout
