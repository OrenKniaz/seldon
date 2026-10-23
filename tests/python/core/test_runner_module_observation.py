"""Real child-process observations of disposable owned CLI stubs, not product proof."""

import hashlib

import pytest

from codex_harness.core.runners import observe_unittest, resolve_runner


def observe_stub(root, *, package=True, environment="", source=None, command=None, options=""):
    target = root / ("tally/__main__.py" if package else "tally.py")
    target.parent.mkdir(parents=True, exist_ok=True)
    if package:
        (target.parent / "__init__.py").write_text("", encoding="utf-8")
    target.write_text(source or "raise NotImplementedError('owned disposable CLI stub')\n")
    proofs = root / "tests"
    proofs.mkdir()
    argv = command or "[sys.executable, '-m', 'tally']"
    (proofs / "test_cli.py").write_text(
        "import os, subprocess, sys, unittest\n"
        "class Proof(unittest.TestCase):\n"
        "    def test_cli(self):\n"
        f"        result = subprocess.run({argv}, capture_output=True, "
        f"text=True{environment}{options})\n"
        "        self.assertEqual(result.returncode, 0)\n",
        encoding="utf-8",
    )
    owned = (target.relative_to(root).as_posix(),)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*.py")}
    result = observe_unittest(
        root,
        resolve_runner({"validation": ["-B", "-m", "unittest", "discover", "-s", "tests", "-p"]}),
        "test_cli.py",
        owned,
    )
    assert result["exitCode"] == 1, result
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*.py")}
    assert result["observations"]["testsRun"] == 1
    launch = result["observations"]["tests"][0]["moduleLaunches"][0]
    return result, launch, target


@pytest.mark.parametrize("package", [True, False], ids=["package-entry", "explicit-output-env"])
def test_real_owned_cli_records_package_entry_and_safe_output_environment(
    tmp_path, monkeypatch, package
):
    """Catch P1's rejected package/explicit env; old tests only launch module.py inherited env."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    environment = (
        ""
        if package
        else ", env=dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')"
    )
    result, launch, target = observe_stub(tmp_path, package=package, environment=environment)
    assert launch.get("instrumented"), launch
    child = launch["child"]
    assert child["startup"] == "observed-equivalent-with-private-bootstrap", child
    assert child["targetModule"] == ("tally.__main__" if package else "tally")
    assert child["target"] == str(target.resolve())
    assert (
        child["loadedOwned"][str(target.resolve())]
        == hashlib.sha256(target.read_bytes()).hexdigest()
    )
    assert child["exception"]["type"] == "NotImplementedError"
    assert child["exception"]["foreignTail"] is False
    assert child["atexitObserved"] is True and launch["exitCode"] == 1
    assert child["requestedArgv"] == launch["argv"]
    assert child["runtimePid"] == child["pid"]
    assert child["pid"] == launch["pid"] or (
        child["platform"] == launch["platform"] == "win32"
        and child["runtimeParentPid"] == launch["pid"]
        and child["launcherExecutable"] != launch["actualInterpreter"]
    )
    assert result["passed"] is False
    if not package:
        assert launch["requestedOutputEnvironment"] == {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        assert child["stdoutEncoding"] == child["stderrEncoding"] == "utf-8"
        assert child["dontWriteBytecode"] is True
        assert not list(tmp_path.rglob("*.pyc"))


@pytest.mark.parametrize(
    "setting",
    [
        "PYTHONIOENCODING='utf-8:ignore'",
        "PYTHONDONTWRITEBYTECODE=''",
        "PYTHONSAFEPATH='1'",
        "PYTHONHOME=str(os.getcwd())",
    ],
)
def test_output_setting_exception_does_not_admit_other_startup_changes(
    tmp_path, monkeypatch, setting
):
    """Catch overbroad env exemption; prior negative proves only PYTHONSAFEPATH on module.py."""
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    monkeypatch.delenv("PYTHONHOME", raising=False)
    _, launch, _ = observe_stub(tmp_path, environment=f", env=dict(os.environ, {setting})")
    assert not launch.get("instrumented")
    assert launch["reason"] == "Changed interpreter startup environment."
    assert "child" not in launch


def test_package_child_keeps_observed_exception_instead_of_stderr_claim(tmp_path):
    """Catch fake owned-package success credit; old stderr-spoof proof only exercises module.py."""
    _, launch, _ = observe_stub(
        tmp_path,
        source="import sys\nsys.stderr.write('NotImplementedError: fake stub')\nsys.exit(1)\n",
        environment=", env=dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')",
    )
    assert launch["instrumented"]
    child = launch["child"]
    assert child["startup"] == "observed-equivalent-with-private-bootstrap"
    assert child["targetModule"] == "tally.__main__"
    assert "exception" not in child
    assert child["atexitObserved"] is True


@pytest.mark.parametrize("case", ["timeout", "missing-executable", "bootstrap"])
def test_package_failure_without_complete_child_provenance_cannot_look_observed(tmp_path, case):
    """Protect terminal child evidence; prior CLI negatives omit package timeout."""
    options = {}
    if case == "timeout":
        options = {"source": "import time\ntime.sleep(10)\n", "options": ", timeout=0.05"}
    elif case == "missing-executable":
        options = {"command": "[os.path.join(os.getcwd(), 'missing-python'), '-m', 'tally']"}
    else:
        options = {"command": "[sys.executable, '-c', 'raise NotImplementedError(42)']"}
    result, launch, _ = observe_stub(tmp_path, **options)
    if case == "timeout":
        assert any(
            event.get("type") == "TimeoutExpired"
            for event in result["observations"]["tests"][0]["events"]
        )
        assert launch.get("child", {}).get("atexitObserved") is not True
    else:
        assert not launch.get("instrumented")
        assert "child" not in launch


@pytest.mark.parametrize("case", ["fresh", "unrelated", "stale", "replaced", "mutated"])
def test_each_subtest_failure_binds_only_its_actual_unchanged_completed_result(tmp_path, case):
    """Catch position/line matching: old observations cannot distinguish 2 failures on one line."""
    (tmp_path / "app.py").write_text("raise NotImplementedError('owned stub')\n")
    (tmp_path / "tests").mkdir()
    second = {
        "fresh": "result = self.invoke()",
        "unrelated": "result = self.invoke()",
        "stale": "pass",
        "replaced": (
            "result = self.invoke(); result = subprocess.CompletedProcess("
            "result.args, 1, result.stdout, result.stderr)"
        ),
        "mutated": "result = self.invoke(); result.returncode = 99",
    }[case]
    assertion = "self.assertEqual(1, 0)" if case == "unrelated" else "self.check(result)"
    source = (
        "import subprocess, sys, unittest\n"
        "class Proof(unittest.TestCase):\n"
        "    def invoke(self):\n"
        "        return subprocess.run([sys.executable, '-B', '-m', 'app'], "
        "capture_output=True, text=True)\n"
        "    def check(self, result):\n"
        "        self.assertEqual(result.returncode, 0, result.stderr)\n"
        "    def test_cli(self):\n"
        "        result = self.invoke()\n"
        "        with self.subTest(part=1):\n"
        "            self.check(result)\n"
        "        with self.subTest(part=2):\n"
        f"            {second}\n"
        f"            {assertion}\n"
    )
    (tmp_path / "tests/test_cli.py").write_text(source, encoding="utf-8")
    observed = observe_unittest(
        tmp_path,
        resolve_runner({"validation": ["-B", "-m", "unittest", "discover", "-s", "tests", "-p"]}),
        "test_cli.py",
        ("app.py",),
    )
    record = observed["observations"]["tests"][0]
    failures = [event for event in record["events"] if event["kind"] == "subtest-error"]
    assert len(failures) == 2
    assert all(event["phase"] == "test" for event in failures)
    first = failures[0].get("resultBinding")
    assert first is not None
    launches = {launch["launchId"]: launch for launch in record["moduleLaunches"]}
    assert launches[first["launchId"]]["completedResultObserved"] is True
    assert first["resultUnchanged"] is True and first["currentEvent"] is True
    assert first["assertion"]["field"] == "returncode"
    if case == "fresh":
        second_binding = failures[1]["resultBinding"]
        assert second_binding["launchId"] != first["launchId"]
        assert launches[second_binding["launchId"]]["completedResultObserved"] is True
    else:
        assert "resultBinding" not in failures[1]


def test_class_fixture_failure_preserves_non_cli_observation(tmp_path):
    """Catch new binding crashing before startTest; prior CLI cases always start a test method."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_fixture.py").write_text(
        "import unittest\n"
        "class Proof(unittest.TestCase):\n"
        "    @classmethod\n"
        "    def setUpClass(cls):\n"
        "        raise AssertionError('fixture failure')\n"
        "    def test_unused(self):\n"
        "        self.assertEqual(1, 1)\n"
    )
    observed = observe_unittest(
        tmp_path,
        resolve_runner({"validation": ["-B", "-m", "unittest", "discover", "-s", "tests", "-p"]}),
        "test_fixture.py",
        (),
    )
    assert observed["exitCode"] == 1
    assert observed["observations"]["testsRun"] == 0
    event = observed["observations"]["tests"][0]["events"][0]
    assert event["kind"] == "error" and event["phase"] == "fixture"
    assert "resultBinding" not in event
