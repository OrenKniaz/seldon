"""Canonical verification argv, protected inputs and observed proof verdicts."""

from __future__ import annotations

import fnmatch
import inspect
import json
import posixpath
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from codex_harness.core.graph import Plan, PlanError
from codex_harness.core.graph import test_dir as unittest_test_dir

CMD_META = '&|<>^%!"\r\n'


def _unittest_observer():
    """Controller script: observe unittest callbacks and execution, never test reports."""
    import json
    import sys
    import unittest
    from pathlib import Path

    report, argv_json, owned_json = sys.argv[1:]
    root = Path.cwd().resolve()
    owned = {str((root / name).resolve()) for name in json.loads(owned_json)}

    def location(frame, line=None):
        path = Path(frame.f_code.co_filename).resolve()
        return {
            "file": path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path),
            "line": frame.f_lineno if line is None else line,
            "function": frame.f_code.co_name,
        }

    def phase(frame):
        while frame:
            if frame.f_code.co_filename == unittest.case.__file__:
                name = frame.f_code.co_name
                if name in {"_callSetUp", "_callTearDown", "_callCleanup"}:
                    return name
                if name == "_callTestMethod":
                    return "test"
            frame = frame.f_back
        return "fixture"

    class Observed(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.records = []
            self.current = None

        def startTest(self, test):
            super().startTest(test)
            method = getattr(test, test._testMethodName)
            self.current = {
                "id": test.id(),
                "method": method.__name__,
                "source": location_from_code(method.__code__),
                "ownedCalls": [],
                "lines": [],
                "events": [],
            }
            self.records.append(self.current)

        def stopTest(self, test):
            super().stopTest(test)
            self.current = None

        def record(self, test, kind, error=None):
            item = self.current
            if item is None:
                item = {"id": test.id(), "ownedCalls": [], "lines": [], "events": []}
                self.records.append(item)
            event = {"kind": kind}
            if error:
                frames = []
                observed_phase = "fixture"
                tb = error[2]
                while tb:
                    frames.append(location(tb.tb_frame, tb.tb_lineno))
                    if tb.tb_frame.f_code.co_filename == unittest.case.__file__:
                        name = tb.tb_frame.f_code.co_name
                        if name in {"_callSetUp", "_callTearDown", "_callCleanup"}:
                            observed_phase = name
                        elif name == "_callTestMethod":
                            observed_phase = "test"
                    tb = tb.tb_next
                event.update(
                    type=error[0].__name__,
                    module=error[0].__module__,
                    frames=frames,
                    phase=observed_phase,
                )
            item["events"].append(event)

        def addSuccess(self, test):
            self.record(test, "success")
            super().addSuccess(test)

        def addFailure(self, test, err):
            self.record(test, "failure", err)
            super().addFailure(test, err)

        def addError(self, test, err):
            self.record(test, "error", err)
            super().addError(test, err)

        def addSkip(self, test, reason):
            self.record(test, "skip")
            super().addSkip(test, reason)

        def addExpectedFailure(self, test, err):
            self.record(test, "expected-failure", err)
            super().addExpectedFailure(test, err)

        def addUnexpectedSuccess(self, test):
            self.record(test, "unexpected-success")
            super().addUnexpectedSuccess(test)

        def addSubTest(self, test, subtest, err):
            self.record(test, "subtest-error" if err else "subtest-success", err)
            super().addSubTest(test, subtest, err)

    def location_from_code(code):
        path = Path(code.co_filename).resolve()
        return {
            "file": path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path),
            "line": code.co_firstlineno,
        }

    result = None
    count = 0

    def trace(frame, event, arg):
        filename = frame.f_code.co_filename
        if not filename.startswith(str(root)):
            return None
        current = result.current
        if current is not None and event in {"call", "line"}:
            if event == "call" and filename in owned:
                entry = location(frame)
                entry["phase"] = phase(frame)
                entry["caller"] = location(frame.f_back)
                current["ownedCalls"].append(entry)
            if event == "line" and filename == str(root / current["source"]["file"]):
                current["lines"].append(location(frame))
        return trace

    class ObservedRunner(unittest.TextTestRunner):
        resultclass = Observed

        def _makeResult(self):
            nonlocal result
            result = super()._makeResult()
            return result

        def run(self, suite):
            nonlocal count
            count = suite.countTestCases()
            sys.settrace(trace)
            try:
                return super().run(suite)
            finally:
                sys.settrace(None)

    # Delegate argument parsing, selection, failfast and buffering to unittest.
    unittest.TestProgram(
        module=None,
        argv=["unittest", *json.loads(argv_json)],
        testRunner=ObservedRunner,
        exit=False,
    )
    Path(report).write_text(
        json.dumps({"selected": count, "testsRun": result.testsRun, "tests": result.records}),
        encoding="utf-8",
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)


def observe_unittest(repo: Path, runner: Runner, module: str, owned: tuple[str, ...]) -> dict:
    """Execute discovery with controller-owned callbacks and a separate result channel.

    The native unittest parser applies configured discovery and runner options.
    """
    argv = list(runner.argv_for(module))[1:]
    # Only interpreter bytecode suppression precedes the supported module entry.
    if argv[:1] == ["-B"]:
        argv.pop(0)
    if argv[:2] != ["-m", "unittest"]:
        raise ValueError("Controller observations require python -m unittest")
    argv = argv[2:]
    sources = {*runner.proof_paths(repo), *(repo / name for name in owned)}
    before = {path: path.read_bytes() for path in sources if path.is_file()}
    with TemporaryDirectory(prefix="harness-observation-") as temp:
        report = Path(temp) / "observations.json"
        command = [
            sys.executable,
            "-B",
            "-c",
            inspect.getsource(_unittest_observer) + "\n_unittest_observer()\n",
            str(report),
            json.dumps(argv),
            json.dumps(owned),
        ]
        result = subprocess.run(
            command, cwd=repo / runner.cwd, capture_output=True, text=True, timeout=120
        )
        output = result.stdout + result.stderr
        try:
            observations = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            observations = {"tests": [], "reason": f"Controller observation missing: {error}"}
        if any(
            not path.is_file() or path.read_bytes() != content for path, content in before.items()
        ):
            observations = {
                "tests": [],
                "reason": "Proof or owned source changed during observation.",
            }
        return dict(
            command=command,
            exitCode=result.returncode,
            passed=runner.passed(result.returncode),
            output=output,
            observations=observations,
        )


def _cmd_safe(arguments) -> None:
    if any(any(character in arg for character in CMD_META) for arg in arguments):
        raise PlanError("plan-schema-invalid", "Batch runner arguments contain cmd metacharacters")


def _argv(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("Runner argv must be a nonempty list")
    if any(not isinstance(arg, str) or not arg or "\0" in arg for arg in value):
        raise ValueError("Runner argv must contain nonempty strings without NUL")
    return tuple(value)


def _relative(value: object, *, allow_dot: bool = False) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("Expected a relative runner path")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in value:
        raise ValueError("Runner paths must stay inside the repository")
    if not path.parts and not allow_dot:
        raise ValueError("Runner path must name a directory or file")
    if str(path).startswith("-"):
        raise ValueError("Runner path cannot be an option")
    return str(path)


def _pattern(value: object) -> str:
    value = _relative(value)
    if "/" in value:
        raise ValueError("Proof selectors must be filename patterns")
    return value


def _command_template(value: object, field: str, *, required_proof: bool) -> tuple[str, ...]:
    argv = _argv(value)
    if (
        argv.count("{proof}") not in ((1,) if required_proof else (0, 1))
        or argv[0] == "{proof}"
        or any(("{" in arg or "}" in arg) and arg != "{proof}" for arg in argv)
    ):
        count = "exactly one" if required_proof else "at most one"
        raise ValueError(f"Command {field} requires {count} standalone {{proof}} argument")
    return argv


def _regex_patterns(config: Mapping, field: str) -> tuple[str, ...] | None:
    if field not in config:
        return None
    values = config[field]
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field} must be a nonempty list of regexes")
    for value in values:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must contain nonempty regex strings")
        try:
            pattern = re.compile(value)
        except re.error as error:
            raise ValueError(f"Invalid {field} regex: {error}") from error
        if field == "importPatterns" and not pattern.groups:
            raise ValueError("importPatterns regexes must capture the import specifier in group 1")
    return tuple(values)


@dataclass(frozen=True)
class Runner:
    """Resolved argv contract, proof discovery and exit-only verdict policy.

    Command proofs are repository-relative paths; unittest proofs are filename
    patterns, preserving its existing discovery CLI. cwd is relative to the bead
    worktree (or integration repository) root. No process is launched here.
    """

    name: str
    argv: tuple[str, ...]
    test_dir: str
    cwd: str
    proof_globs: tuple[str, ...]
    single_test: tuple[str, ...] | None = None
    list_argv: tuple[str, ...] | None = None
    assert_patterns: tuple[str, ...] | None = None
    import_patterns: tuple[str, ...] | None = None

    def protected_paths(self, requirement_files) -> tuple[str, ...]:
        return (self.test_dir, "VISION.md", "PLAN.md", *requirement_files)

    def argv_for(self, proof: str) -> list[str] | str:
        if self.name == "unittest":
            return [sys.executable, *self.argv, _pattern(proof)]
        return self._command_argv(self.argv, proof)

    def list_argv_for(self, proof: str) -> list[str] | str:
        if self.list_argv is None:
            raise ValueError("Command prospective audit requires listArgv")
        return self._command_argv(self.list_argv, proof)

    def worker_command(self, proof: str) -> str:
        """One shell command for packets and provider rules; execution keeps its argv."""
        command = self._command_argv(self.argv, proof, portable=True)
        return command if isinstance(command, str) else subprocess.list2cmdline(command)

    def _command_argv(
        self, template: tuple[str, ...], proof: str, *, portable: bool = False
    ) -> list[str] | str:
        selected = self.proof_argument(proof)
        executable = shutil.which(template[0])
        if executable is None:
            raise ValueError(f"Runner executable not found: {template[0]}")
        if portable:
            executable = executable.replace("\\", "/")
        arguments = [executable, *(selected if arg == "{proof}" else arg for arg in template[1:])]
        if sys.platform == "win32" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
            _cmd_safe(arguments)
            # /s strips the outer quotes, preserving quoted paths and arguments.
            return f'cmd.exe /d /s /c "{subprocess.list2cmdline(arguments)}"'
        return arguments

    def proof_argument(self, proof: str) -> str:
        """Validate a concrete command proof, including before tools are available."""
        proof = _relative(proof)
        path = PurePosixPath(proof)
        if str(path.parent) != self.test_dir or not any(
            fnmatch.fnmatchcase(path.name, pattern) for pattern in self.proof_globs
        ):
            raise ValueError("Proof must match proofGlob inside the protected test directory")
        if any(character in proof for character in "*?["):
            raise ValueError("Command proof must be a concrete file, not a glob")
        return posixpath.relpath(proof, self.cwd)

    def proof_paths(self, repo: Path) -> tuple[Path, ...]:
        root = repo.resolve()
        if not (root / self.cwd).resolve().is_relative_to(root):
            raise ValueError("Runner cwd escapes the repository")
        directory = root / self.test_dir
        if not directory.resolve().is_relative_to(root):
            raise ValueError("Protected test directory escapes the repository")
        paths = sorted({path for pattern in self.proof_globs for path in directory.glob(pattern)})
        for path in paths:
            if not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Proof file escapes the protected test directory")
        return tuple(path for path in paths if path.is_file())

    def passed(self, exit_code: int | None) -> bool:
        """Only an observed zero exit passes; summaries/reports are not inputs."""
        return type(exit_code) is int and exit_code == 0


def resolve_runner(manifest: Mapping | Plan) -> Runner:
    """Resolve optional verification; legacy validation remains the default."""
    if isinstance(manifest, Plan):
        manifest = {
            "validation": list(manifest.validation),
            **(
                {"verification": manifest.verification} if manifest.verification is not None else {}
            ),
            "beads": {
                key: {"proofModule": bead.proof_module} for key, bead in manifest.beads.items()
            },
            "integration": {"proofModule": manifest.integration_proof_module},
        }
    try:
        return _resolve_runner(manifest)
    except ValueError as error:
        if isinstance(error, PlanError):
            raise
        raise PlanError("plan-schema-invalid", str(error)) from error


def _resolve_runner(manifest: Mapping) -> Runner:
    validation = manifest.get("validation")
    if "verification" in manifest:
        config = manifest["verification"]
        if not isinstance(config, Mapping):
            raise ValueError("Manifest verification must be a runner object")
    elif isinstance(validation, list):
        config = {"runner": "unittest", "argv": validation}
    elif isinstance(validation, Mapping):
        config = validation
    else:
        raise ValueError("Manifest validation must be an argv list or runner object")
    name = config.get("runner", "unittest")
    if not isinstance(name, str) or name not in {"unittest", "command"}:
        raise ValueError(f"Unsupported verification runner: {name!r}")
    argv = _argv(config.get("argv"))
    if name == "unittest":
        if set(config) - {"runner", "argv", "singleTest"}:
            raise ValueError("Unittest runner derives its test directory from validation -s")
        selectors = [bead["proofModule"] for bead in manifest.get("beads", {}).values()]
        integration = manifest.get("integration", {}).get("proofModule")
        if integration is not None:
            selectors.append(integration)
        return Runner(
            name,
            argv,
            unittest_test_dir({"validation": list(argv)}),
            ".",
            tuple(_pattern(selector) for selector in selectors) or ("test*.py",),
            _argv(config["singleTest"]) if "singleTest" in config else ("{test}",),
        )
    if set(config) - {
        "runner",
        "argv",
        "cwd",
        "pass",
        "testDir",
        "proofGlob",
        "singleTest",
        "listArgv",
        "assertPatterns",
        "importPatterns",
    }:
        raise ValueError("Unsupported command runner field")
    argv = _command_template(config.get("argv"), "argv", required_proof=True)
    policy = config.get("pass", {"exit": 0})
    if (
        not isinstance(policy, Mapping)
        or set(policy) != {"exit"}
        or (type(policy["exit"]) is not int or policy["exit"] != 0)
    ):
        raise ValueError("Only pass.exit = 0 is supported")
    runner = Runner(
        name,
        argv,
        _relative(config.get("testDir")),
        _relative(config.get("cwd", "."), allow_dot=True),
        (_pattern(config.get("proofGlob")),),
        _argv(config["singleTest"]) if "singleTest" in config else None,
        _command_template(config["listArgv"], "listArgv", required_proof=False)
        if "listArgv" in config
        else None,
        _regex_patterns(config, "assertPatterns"),
        _regex_patterns(config, "importPatterns"),
    )
    executable = shutil.which(argv[0])
    proofs = [bead["proofModule"] for bead in manifest.get("beads", {}).values()]
    if manifest.get("integration", {}).get("proofModule") is not None:
        proofs.append(manifest["integration"]["proofModule"])
    for proof in proofs:
        runner.proof_argument(proof)
    if executable and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        _cmd_safe((executable, *argv, runner.cwd, runner.test_dir, *runner.proof_globs))
        _cmd_safe(proofs)
        if runner.single_test:
            _cmd_safe(runner.single_test)
    if runner.list_argv:
        executable = shutil.which(runner.list_argv[0])
        if executable and Path(executable).suffix.lower() in {".cmd", ".bat"}:
            _cmd_safe((executable, *runner.list_argv, runner.cwd, *proofs))
    return runner


def validate_proof(
    repo: Path, manifest: Mapping | Plan, module: str, *, owned_files: tuple[str, ...] = ()
) -> dict:
    """Require useful observed unittest execution, or command exit success."""
    runner = resolve_runner(manifest)
    command = []
    try:
        runner.proof_paths(repo)
        command = runner.argv_for(module)
        if runner.name == "unittest":
            result = observe_unittest(repo, runner, module, owned_files)
            observations = result["observations"]
            records = observations.get("tests", [])
            observed = sum(
                bool(record.get("events"))
                and (
                    not any(event.get("kind") == "skip" for event in record["events"])
                    or any(event.get("kind") == "subtest-success" for event in record["events"])
                )
                for record in records
            )
            successful = sum(
                any(
                    event.get("kind") in {"success", "subtest-success"}
                    for event in record.get("events", [])
                )
                and not any(
                    event.get("kind")
                    in {
                        "expected-failure",
                        "unexpected-success",
                        "failure",
                        "error",
                        "subtest-error",
                    }
                    for event in record["events"]
                )
                for record in records
            )
            tests_run = observations.get("testsRun")
            valid = (
                not observations.get("reason")
                and type(tests_run) is int
                and tests_run == len(records)
                and type(observations.get("selected")) is int
                and observations["selected"] >= tests_run
            )
            return {
                "module": module,
                **result,
                "configuredCommand": command,
                "selectedTests": observations.get("selected"),
                "testsRun": tests_run,
                "successfulTests": successful,
                "nonSkippedTestsRun": observed,
                "passed": bool(result["passed"] and valid and observed > 0 and successful > 0),
            }
        result = subprocess.run(
            command, cwd=repo / runner.cwd, capture_output=True, text=True, timeout=120
        )
        return {
            "module": module,
            "command": command,
            "passed": runner.passed(result.returncode),
            "exitCode": result.returncode,
            "output": result.stdout + result.stderr,
            "observations": None,
            "selectedTests": None,
            "successfulTests": None,
            "testsRun": None,
            "nonSkippedTestsRun": None,
        }
    except subprocess.TimeoutExpired as error:
        output = "".join(
            value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
            for value in (error.stdout, error.stderr)
        )
        return {
            "module": module,
            "command": command,
            "passed": False,
            "exitCode": None,
            "output": output + "\nProof timed out after 120 seconds",
            "observations": {"tests": [], "reason": "Proof execution timed out"},
            "selectedTests": None,
            "successfulTests": None,
            "testsRun": None,
            "nonSkippedTestsRun": 0,
        }
    except (OSError, ValueError) as error:
        return {
            "module": module,
            "command": command,
            "passed": False,
            "exitCode": None,
            "output": str(error),
            "observations": {"tests": [], "reason": str(error)},
            "selectedTests": None,
            "successfulTests": None,
            "testsRun": None,
            "nonSkippedTestsRun": 0,
        }
