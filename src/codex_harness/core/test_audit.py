"""Audit revised requirement proofs against integrated code and an optional oracle.

Run this preflight before dispatching a revision. All proof execution and overlays
use temporary copies; failing output is saved under state/runs in the repository.
"""

from __future__ import annotations

import argparse
import ast
import copy
import fnmatch
import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from codex_harness.core.audit_process import run_audit_process
from codex_harness.core.graph import load_plan, topological_order
from codex_harness.core.planner_audits import (
    AuditTemporaryDirectory,
    ProspectiveAuditFailure,
    audit_failure_evidence,
    guarded_cleanup,
)
from codex_harness.core.runners import Runner, resolve_runner, validate_proof
from codex_harness.core.spine import (
    Spine,
    compile_manifest,
    impact,
    load_spine,
    requirement_file_sources,
)

__test__ = False

PROSPECTIVE_LABEL = "Prospective plan · tests not oracle-verified"
ORACLE_LABEL = "Oracle-verified plan"
PROSPECTIVE_WARNING = (
    "This plan's tests were not verified against a reference implementation; "
    "if a protected test is impossible or self-contradictory, block with evidence "
    "instead of working around it."
)


def _interface_assertion(tree: ast.Module, function: ast.AST, owned: set[str]) -> bool:
    """Follow imported interfaces into assertion arguments, including setup values."""
    bindings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bindings.update(
                alias.asname or alias.name.split(".")[0]
                for alias in node.names
                if alias.name in owned
            )
        elif isinstance(node, ast.ImportFrom):
            bindings.update(
                alias.asname or alias.name
                for alias in node.names
                if node.module in owned or f"{node.module}.{alias.name}" in owned
            )

    def references(node):
        return {
            child.id if isinstance(child, ast.Name) else ast.unparse(child)
            for child in ast.walk(node)
            if isinstance(child, ast.Name | ast.Attribute)
        }

    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign | ast.AnnAssign)]
    while True:
        before = bindings.copy()
        for node in assignments:
            if node.value is not None and references(node.value).intersection(bindings):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                bindings.update(ast.unparse(target) for target in targets)
        if bindings == before:
            break
    for node in ast.walk(function):
        if isinstance(node, ast.Assert) and references(node.test).intersection(bindings):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith("assert")
        ):
            if any(
                references(argument).intersection(bindings)
                for argument in [*node.args, *(kw.value for kw in node.keywords)]
            ):
                return True
        if (
            isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr.startswith("assert")
                for item in node.items
            )
            and references(node).intersection(bindings)
        ):
            return True
    return False


def _listed_proofs(stdout: str, runner: Runner) -> set[str]:
    """Read exact file identifiers from newline lists or TAP result lines."""
    identifiers = set()
    tap = bool(
        re.search(r"^(?:TAP version \d+|(?:not )?ok \d+\b|\d+\.\.\d+\b)", stdout, re.MULTILINE)
    )
    for line in stdout.splitlines():
        if tap:
            match = re.fullmatch(r"ok\s+\d+\s+-\s+(.+)", line)
            if match is None:
                continue
            line = re.split(r"\s+#\s*(?:SKIP|TODO)\b", match[1], maxsplit=1)[0]
        identifier = posixpath.normpath(re.sub(r"\\+", "/", line.strip()))
        # Identifiers may be repository-relative or relative to runner cwd.
        identifiers.update((identifier, posixpath.normpath(posixpath.join(runner.cwd, identifier))))
    return identifiers


def _collect_command(repo: Path, runner: Runner, modules: list[str]) -> list[dict]:
    collection = []
    per_proof = "{proof}" in runner.list_argv
    for module in modules if per_proof else modules[:1]:
        command = []
        control = None
        try:
            command = runner.list_argv_for(module)
            result = run_audit_process(
                command, cwd=repo / runner.cwd, capture_output=True, text=True, timeout=120
            )
            control = getattr(result, "audit_control", None)
            output = result.stdout + result.stderr
            listed = _listed_proofs(result.stdout, runner)
            expected = [module] if per_proof else modules
            missing = sorted(set(expected) - listed)
            passed = (
                runner.passed(result.returncode)
                and not re.search(r"^\s*not ok\b|^# fail [1-9]\d*\b", result.stdout, re.MULTILINE)
                and not missing
            )
            if missing:
                output += "\nProof files absent from listArgv stdout: " + ", ".join(missing)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            output, passed = str(error), False
            control = getattr(error, "audit_control", None)
        collection.append(
            dict(
                module=module,
                command=command,
                passed=bool(passed),
                output=output,
                processControl=control,
            )
        )
        if control and control.get("jobHandleRetained") and not control.get("quiescenceVerified"):
            collection[-1]["output"] += "\nFurther collection held: owned Job quiescence unverified"
            break
    return collection


def _imports_owned(source: str, proof: Path, owned: set[Path], runner: Runner) -> bool:
    for pattern in runner.import_patterns:
        for match in re.finditer(pattern, source):
            specifier = match[1]
            if not specifier or not specifier.startswith(("./", "../")):
                continue
            target = proof.parent / specifier
            candidates = [target, *(Path(str(target) + ext) for ext in (".ts", ".js"))]
            candidates.extend(target / ("index" + ext) for ext in (".ts", ".js"))
            if target.suffix == ".js":
                candidates.append(target.with_suffix(".ts"))
            if any(
                candidate.resolve() in owned and candidate.is_file() for candidate in candidates
            ):
                return True
    return False


def _expectations(
    tree: ast.Module, function: ast.AST, owned: set[str]
) -> list[tuple[ast.AST, set[int]]]:
    """Support straight-line lineage; diagnostic messages are never operands.

    The observer records executed lines, not branch-dependent value provenance.
    Do not infer assignment effects across control flow or nested scopes from
    AST order, including assignments in branches that never ran.
    """
    ambiguous_flow = (
        ast.If,
        ast.IfExp,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.Try,
        ast.TryStar,
        ast.Match,
        ast.BoolOp,
        ast.NamedExpr,
        ast.AugAssign,
        ast.Delete,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )
    if any(
        isinstance(node, ambiguous_flow)
        or (
            node is not function
            and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef)
        )
        for node in ast.walk(function)
    ):
        return []
    binary = {
        "assertEqual",
        "assertNotEqual",
        "assertIs",
        "assertIsNot",
        "assertIn",
        "assertNotIn",
        "assertGreater",
        "assertGreaterEqual",
        "assertLess",
        "assertLessEqual",
        "assertAlmostEqual",
        "assertNotAlmostEqual",
        "assertSequenceEqual",
        "assertListEqual",
        "assertTupleEqual",
        "assertSetEqual",
        "assertDictEqual",
        "assertCountEqual",
        "assertRegex",
        "assertNotRegex",
        "assertIsInstance",
        "assertNotIsInstance",
    }
    unary = {"assertTrue", "assertFalse", "assertIsNone", "assertIsNotNone"}
    sites = []
    for node in function.body:
        expression = node.value if isinstance(node, ast.Expr) else None
        if isinstance(node, ast.Assert):
            candidate = ast.Assert(test=node.test)
        elif (
            isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Attribute)
            and isinstance(expression.func.value, ast.Name)
            and expression.func.value.id == "self"
            and expression.func.attr in binary | unary
        ):
            count = 1 if expression.func.attr in unary else 2
            candidate = ast.Call(func=expression.func, args=expression.args[:count], keywords=[])
        elif (
            isinstance(node, ast.With)
            and len(node.items) == 1
            and isinstance(node.items[0].context_expr, ast.Call)
            and isinstance(node.items[0].context_expr.func, ast.Attribute)
            and ast.unparse(node.items[0].context_expr.func)
            in {"self.assertRaises", "self.assertRaisesRegex"}
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Call)
        ):
            # A context is supported only around one direct owned invocation.
            # No following assertion/raise, nested owned call or wrapper can
            # borrow that invocation's provenance for a different failure.
            invocation = node.body[0].value
            owned_calls = [
                call
                for call in ast.walk(invocation)
                if isinstance(call, ast.Call)
                and _interface_assertion(tree, ast.Assert(test=call.func), owned)
            ]
            if owned_calls != [invocation]:
                continue
            candidate = node
        else:
            continue
        if _interface_assertion(tree, candidate, owned):
            # Track simple local value assignments into the expectation. An
            # overwritten value loses its lineage; strings/messages never add it.
            bindings = {}
            imports = set()
            for imported in ast.walk(tree):
                if isinstance(imported, ast.ImportFrom) and imported.module in owned:
                    imports.update(alias.asname or alias.name for alias in imported.names)
                elif isinstance(imported, ast.Import):
                    imports.update(
                        alias.asname or alias.name.split(".")[0]
                        for alias in imported.names
                        if alias.name in owned
                    )

            def references(expression):
                return {
                    ast.unparse(n)
                    for n in ast.walk(expression)
                    if isinstance(n, ast.Name | ast.Attribute)
                }

            for statement in sorted(ast.walk(function), key=lambda n: getattr(n, "lineno", 0)):
                if (
                    not isinstance(statement, ast.Assign | ast.AnnAssign)
                    or statement.lineno >= node.lineno
                    or statement.value is None
                ):
                    continue
                refs = references(statement.value)
                lineage = set().union(*(bindings.get(ref, set()) for ref in refs))
                if refs.intersection(imports):
                    lineage.update(range(statement.lineno, statement.end_lineno + 1))
                targets = (
                    statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                )
                for target in targets:
                    bindings[ast.unparse(target)] = lineage
            refs = references(candidate)
            call_lines = set().union(*(bindings.get(ref, set()) for ref in refs))
            if refs.intersection(imports):
                call_lines.update(range(node.lineno, node.end_lineno + 1))
            if isinstance(node, ast.With):
                call_lines = set(range(node.body[0].lineno, node.body[0].end_lineno + 1))
            sites.append((node, call_lines))
    return sites


def _cli_expectations(tree, function):
    """Use the existing straight-line rules for subprocess result operands only.

    A module-level helper may return the same result name unchanged. This does
    not infer tuple transformations, assertion helpers or class helper effects.
    Runtime launch/caller evidence is still mandatory; this is not CLI credit.
    """

    def writes_fields(scope):
        for statement in ast.walk(scope):
            if isinstance(statement, ast.Assign):
                targets = statement.targets
            elif isinstance(statement, ast.AnnAssign):
                targets = [statement.target]
            else:
                continue
            if any(
                isinstance(node, ast.Attribute | ast.Subscript)
                for target in targets
                for node in ast.walk(target)
            ):
                return True
        return False

    # Attribute/subscript writes can mutate a result through aliases while its
    # base name retains earlier taint. This CLI subset refuses all such writes,
    # rather than pretending to track object identity or alter imported proofs.
    if writes_fields(function):
        return []
    aliases = []
    launches, path_names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.extend(
                ast.alias(name=a.asname or a.name) for a in node.names if a.name == "subprocess"
            )
            launches.update(
                (a.asname or a.name) + ".run" for a in node.names if a.name == "subprocess"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            aliases.extend(
                ast.alias(name=a.asname or a.name) for a in node.names if a.name == "run"
            )
            launches.update(a.asname or a.name for a in node.names if a.name == "run")
        elif isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            path_names.update(a.asname or a.name for a in node.names if a.name == "Path")
    if not aliases:
        return []

    def cwd_call(call):
        if (
            isinstance(call.func, ast.Name)
            and call.func.id in path_names
            and len(call.args) == 1
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "__file__"
            and not call.keywords
        ):
            return True
        return (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "resolve"
            and isinstance(call.func.value, ast.Call)
            and cwd_call(call.func.value)
            and not call.args
            and not call.keywords
        )

    assertions = {
        "assertEqual",
        "assertNotEqual",
        "assertIs",
        "assertIsNot",
        "assertIn",
        "assertNotIn",
        "assertGreater",
        "assertGreaterEqual",
        "assertLess",
        "assertLessEqual",
        "assertAlmostEqual",
        "assertNotAlmostEqual",
        "assertSequenceEqual",
        "assertListEqual",
        "assertTupleEqual",
        "assertSetEqual",
        "assertDictEqual",
        "assertCountEqual",
        "assertRegex",
        "assertNotRegex",
        "assertIsInstance",
        "assertNotIsInstance",
        "assertTrue",
        "assertFalse",
        "assertIsNone",
        "assertIsNotNone",
    }
    overridden = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    def supported_calls(scope, helper_names=()):
        # No effect inference: opaque calls can mutate aliased result objects,
        # even when no attribute/subscript assignment appears in the proof AST.
        for statement in scope.body:
            calls = [n for n in ast.walk(statement) if isinstance(n, ast.Call)]
            if not calls:
                continue
            value = getattr(statement, "value", None)
            if isinstance(statement, ast.Assign | ast.AnnAssign):
                if (
                    not isinstance(value, ast.Call)
                    or ast.unparse(value.func) not in launches | set(helper_names)
                    or any(call is not value and not cwd_call(call) for call in calls)
                ):
                    return False
            elif isinstance(statement, ast.Expr):
                if (
                    not isinstance(value, ast.Call)
                    or not isinstance(value.func, ast.Attribute)
                    or not isinstance(value.func.value, ast.Name)
                    or value.func.value.id != "self"
                    or value.func.attr not in assertions - overridden
                    or calls != [value]
                ):
                    return False
            else:
                return False
        return True

    synthetic = copy.deepcopy(tree)
    synthetic.body.append(ast.ImportFrom(module="_observed_cli", names=aliases, level=0))
    helpers = {}
    for helper in tree.body:
        if (
            not isinstance(helper, ast.FunctionDef)
            or not helper.body
            or not isinstance(helper.body[-1], ast.Return)
            or not isinstance(helper.body[-1].value, ast.Name)
            or writes_fields(helper)
            or not supported_calls(helper)
        ):
            continue
        probe = copy.deepcopy(helper)
        probe.body[-1] = ast.copy_location(ast.Assert(test=helper.body[-1].value), helper.body[-1])
        probe.body[-1].end_lineno = helper.body[-1].end_lineno
        sites = _expectations(synthetic, probe, {"_observed_cli"})
        if sites:
            helpers[helper.name] = sites[0][1]
    if not supported_calls(function, helpers):
        return []
    synthetic.body.append(
        ast.ImportFrom(module="_observed_cli", names=[ast.alias(name=n) for n in helpers], level=0)
    )
    return [
        (site, lines, helpers)
        for site, lines in _expectations(synthetic, function, {"_observed_cli"})
        if not isinstance(site, ast.With)
    ]


def _cli_launch_matches(tree, function, record, launch):
    """Bind a supported assertion and launch to the actual protected frame paths."""
    frames = record["events"][0]["frames"]
    name, method = record["source"]["file"], record["method"]
    indices = [i for i, f in enumerate(frames) if f["file"] == name and f["function"] == method]
    if len(indices) != 1:
        return False
    failed, tail = frames[indices[0]], frames[indices[0] + 1 :]
    callers = launch.get("callers", [])
    for site, lines, helpers in _cli_expectations(tree, function):
        if not site.lineno <= failed["line"] <= site.end_lineno:
            continue
        statements = [
            n for n in ast.walk(function) if isinstance(n, ast.stmt) and n.lineno == failed["line"]
        ]
        if len(statements) != 1:
            continue
        if isinstance(site, ast.Assert):
            if tail:
                continue
        elif (
            not tail
            or tail[0]["function"] != site.value.func.attr
            or any(f["file"] != str(Path(unittest.case.__file__).resolve()) for f in tail)
        ):
            continue
        if (
            callers
            and callers[-1]["function"] == method
            and callers[-1]["file"] == name
            and callers[-1]["line"] in lines
            and (
                len(callers) == 1
                or len(callers) == 2
                and callers[0]["line"] in helpers.get(callers[0]["function"], set())
            )
        ):
            return True
    return False


def _bound_cli_launches(tree, record):
    """Consume actual result/event identity; source locations only constrain the assertion."""
    events, launches = record["events"], record.get("moduleLaunches", [])
    by_id = {launch["launchId"]: launch for launch in launches if launch.get("launchId")}
    if not events or len(by_id) != len(launches):
        return []
    matched, used = [], set()
    for event in events:
        binding = event.get("resultBinding", {})
        launch_id = binding.get("launchId")
        launch = by_id.get(launch_id, {})
        assertion = binding.get("assertion", {})
        if (
            event.get("kind") not in {"failure", "subtest-error"}
            or event.get("type") != "AssertionError"
            or event.get("module") != "builtins"
            or event.get("phase") != "test"
            or not launch
            or launch_id in used
            or launch.get("completedResultObserved") is not True
            or binding.get("resultUnchanged") is not True
            or binding.get("currentEvent") is not True
            or assertion.get("file") != record["source"]["file"]
            or assertion.get("method") != "assertEqual"
            or assertion.get("field") not in {"returncode", "stdout", "stderr"}
        ):
            return []
        # A diagnostic message, a coincidentally equal literal or another
        # assertion on the same line cannot borrow the producer's result ID.
        statements = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.stmt) and n.lineno == assertion.get("line")
        ]
        if len(statements) != 1 or not isinstance(statements[0], ast.Expr):
            return []
        call = statements[0].value
        if (
            not isinstance(call, ast.Call)
            or ast.unparse(call.func) != "self.assertEqual"
            or not any(
                isinstance(a, ast.Attribute)
                and isinstance(a.value, ast.Name)
                and a.value.id == assertion.get("resultName")
                and a.attr == assertion["field"]
                for a in call.args[:2]
            )
        ):
            return []
        frames = event.get("frames", [])
        indices = [
            i
            for i, f in enumerate(frames)
            if f["file"] == assertion["file"]
            and f["line"] == assertion["line"]
            and f["function"] == assertion.get("function")
        ]
        if len(indices) != 1:
            return []
        tail = frames[indices[0] + 1 :]
        if (
            not tail
            or tail[0]["function"] != "assertEqual"
            or any(f["file"] != str(Path(unittest.case.__file__).resolve()) for f in tail)
            or not any(
                f["file"] == record["source"]["file"] and f["function"] == record["method"]
                for f in frames[: indices[0] + 1]
            )
        ):
            return []
        used.add(launch_id)
        matched.append(launch)
    return matched


def _cli_stub_provenance(repo, owned_files, tree, function, record):
    events = record["events"]
    if any("resultBinding" in event for event in events):
        launches = _bound_cli_launches(tree, record)
    else:
        # Preserve the existing bounded direct/module-helper path for bare
        # assertions and standard predicates outside the new assertEqual API.
        if len(events) != 1 or any(
            events[0].get(k) != v
            for k, v in {
                "kind": "failure",
                "type": "AssertionError",
                "module": "builtins",
                "phase": "test",
            }.items()
        ):
            return False
        launches = [
            launch
            for launch in record.get("moduleLaunches", [])
            if _cli_launch_matches(tree, function, record, launch)
        ]
    return bool(launches) and all(_owned_cli_stub(repo, owned_files, launch) for launch in launches)


def _owned_cli_stub(repo, owned_files, launch):
    child = launch.get("child", {})
    target = child.get("target")
    owned = {str((repo / n).resolve()): n for n in owned_files}
    module = launch.get("module")
    package_entry = (
        isinstance(module, str)
        and child.get("targetModule") == module + ".__main__"
        and owned.get(target, "").removeprefix("src/") == module.replace(".", "/") + "/__main__.py"
    )
    same_pid = child.get("pid") == launch.get("pid")
    redirector = (
        launch.get("platform") == child.get("platform") == "win32"
        and child.get("launcherExecutable") != launch.get("actualInterpreter")
        and child.get("runtimeParentPid") == launch.get("pid")
    )
    if (
        launch.get("phase") != "test"
        or not launch.get("instrumented")
        or launch.get("exitCode") is None
        or launch["exitCode"] <= 0
        or child.get("startup") != "observed-equivalent-with-private-bootstrap"
        or child.get("startupIdentity") != launch.get("startupIdentity")
        or child.get("targetModule") != module
        and not package_entry
        or not (same_pid or redirector)
        or child.get("runtimePid") != child.get("pid")
        or child.get("atexitObserved") is not True
        or child.get("requestedArgv") != launch["argv"]
        or child.get("launcherExecutable") != launch["argv"][0]
        or child.get("argv", [None])[0] != launch.get("actualInterpreter")
        or child.get("argv", [None])[1:] != launch["argv"][1:]
        or child.get("cwd") != launch["cwd"]
    ):
        return False
    exception = child.get("exception", {})
    traceback = exception.get("frames", [])
    if (
        target not in owned
        or not traceback
        or exception.get("type") != "NotImplementedError"
        or exception.get("module") != "builtins"
        or exception.get("foreignTail")
        or any(f["file"] not in owned_files for f in traceback)
        or child.get("loadedOwned", {}).get(target)
        != hashlib.sha256(Path(target).read_bytes()).hexdigest()
    ):
        return False
    last = traceback[-1]
    source = ast.parse((repo / last["file"]).read_text(encoding="utf-8-sig"))
    bodies = [
        n.body
        for n in ast.walk(source)
        if isinstance(n, ast.FunctionDef)
        and n.name == last["function"]
        and n.lineno <= last["line"] <= n.end_lineno
    ]
    if last["function"] == "<module>":
        bodies = [source.body]
        # Support only a canonical, otherwise inert module guard. The
        # observed child/owned-source/exception checks above still bind
        # this exact raise; arbitrary conditionals or side effects do
        # not become pure stubs merely because they raise the same type.
        module_body = [
            n
            for n in source.body
            if not (
                isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            )
        ]
        if len(module_body) == 1 and isinstance(module_body[0], ast.If):
            guard = module_body[0]
            test = guard.test
            if (
                not guard.orelse
                and isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and len(test.ops) == len(test.comparators) == 1
                and isinstance(test.ops[0], ast.Eq)
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == "__main__"
            ):
                bodies = [guard.body]
    for body in bodies:
        body = [
            n
            for n in body
            if not (
                isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            )
        ]
        if len(body) != 1 or not isinstance(body[0], ast.Raise) or body[0].cause is not None:
            continue
        raised = body[0].exc
        constructor = raised.func if isinstance(raised, ast.Call) else raised
        if (
            isinstance(constructor, ast.Name)
            and constructor.id == "NotImplementedError"
            and (
                not isinstance(raised, ast.Call)
                or not raised.keywords
                and all(isinstance(a, ast.Constant) for a in raised.args)
            )
        ):
            return True
    return False


def _failure_provenance(
    repo: Path, runner: Runner, owned_files: tuple[str, ...], proof: dict
) -> dict:
    """Consume controller events against protected source and bead ownership.

    Unknown is deliberately nonpassing. TAP diagnostics and arbitrary command
    summaries do not establish runtime identity or distinguish lifecycle errors.
    """
    unknown = dict(classification="unproved", tests=[])
    if proof.get("exitCode") is None or proof["exitCode"] < 0:
        return {
            **unknown,
            "reason": "Proof did not complete execution (launch, timeout or signal).",
        }
    if runner.name != "unittest":
        return {
            **unknown,
            "reason": "Command/TAP output has no controller-owned per-test execution "
            "and exception channel; assertion text, counts and TAP diagnostics are insufficient.",
        }
    observed = proof.get("observations", {})
    records = observed.get("tests", [])
    if (
        not records
        or observed.get("selected") != observed.get("testsRun")
        or observed.get("testsRun") != len(records)
    ):
        return {
            **unknown,
            "tests": records,
            "reason": observed.get("reason", "Not every selected test executed."),
        }
    owned_modules = {
        name.removeprefix("src/")[:-3].removesuffix("/__init__").replace("/", ".")
        for name in owned_files
        if name.endswith(".py")
    }
    selected_paths = [
        p for p in runner.proof_paths(repo) if fnmatch.fnmatchcase(p.name, proof["module"])
    ]
    expected = set()
    trees = {}
    for path in selected_paths:
        name = path.relative_to(repo).as_posix()
        trees[name] = ast.parse(path.read_text(encoding="utf-8-sig"))
        expected.update(
            (name, min([node.lineno, *(d.lineno for d in node.decorator_list)]))
            for node in ast.walk(trees[name])
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test")
        )
    seen, results = set(), []
    for record in records:
        source = record.get("source", {})
        name, method = source.get("file"), record.get("method")
        seen.add((name, source.get("line")))
        tree = trees.get(name)
        functions = (
            []
            if tree is None
            else [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                and n.name == method
                and min([n.lineno, *(d.lineno for d in n.decorator_list)])
                <= source.get("line", 0)
                <= n.end_lineno
            ]
        )
        sites = _expectations(tree, functions[0], owned_modules) if len(functions) == 1 else []
        calls = [call for call in record["ownedCalls"] if call["phase"] == "test"]
        exercised = [
            site
            for site, call_lines in sites
            if any(
                call["caller"]["file"] == name
                and call["caller"]["function"] == method
                and call["caller"]["line"] in call_lines
                for call in calls
            )
        ]
        classification = "unproved"
        reason = (
            "No observed owned call at a supported straight-line protected expectation; "
            "ambiguous control flow and nested scopes remain unproved."
        )
        events = record["events"]
        if any(event.get("phase") not in {None, "test"} for event in events):
            reason = "Unrelated setup, teardown, cleanup or fixture exception."
        elif any(event.get("type") in {"ImportError", "ModuleNotFoundError"} for event in events):
            reason = "Runtime import failure is not missing owned behavior."
        elif any(
            event["kind"] in {"skip", "expected-failure", "unexpected-success"} for event in events
        ):
            reason = "Selected obligation skipped or altered unittest outcome semantics."
        reached = any(
            line["function"] == method and site.lineno <= line["line"] <= site.end_lineno
            for line in record["lines"]
            for site in exercised
        )
        if reached and events and all(e["kind"] == "success" for e in events):
            classification, reason = "exercised", "Owned behavior reached its declared expectation."
        elif exercised and len(events) == 1:
            event = events[0]
            frames = event.get("frames", [])
            proof_frames = [f for f in frames if f["file"] == name and f["function"] == method]
            method_indices = [
                i for i, f in enumerate(frames) if f["file"] == name and f["function"] == method
            ]
            tail = frames[method_indices[0] + 1 :] if len(method_indices) == 1 else []
            # Multiple statements on the failing line are ambiguous; never use
            # a later, unexecuted assertion to justify an earlier failure.
            failure_sites = [
                site
                for site, call_lines in sites
                if site in exercised
                and proof_frames
                and (
                    (
                        proof_frames[-1]["line"] == site.lineno
                        if isinstance(site, ast.With)
                        else site.lineno <= proof_frames[-1]["line"] <= site.end_lineno
                    )
                    or (
                        event.get("type") == "NotImplementedError"
                        and proof_frames[-1]["line"] in call_lines
                    )
                )
            ]
            if event.get("type") == "NotImplementedError" and failure_sites:
                failure_sites = sorted(failure_sites, key=lambda site: site.lineno)[:1]
            same_line_statements = [
                n
                for n in ast.walk(functions[0])
                if isinstance(n, ast.stmt) and proof_frames and n.lineno == proof_frames[-1]["line"]
            ]
            at_expectation = len(failure_sites) == 1 and len(same_line_statements) <= 1
            if event.get("phase") == "test" and at_expectation:
                site = failure_sites[0]
                expected_callee = (
                    "__exit__"
                    if isinstance(site, ast.With)
                    else site.value.func.attr
                    if isinstance(site, ast.Expr)
                    else None
                )
                direct_assertion = len(method_indices) == 1 and (
                    (isinstance(site, ast.Assert) and not tail)
                    or (
                        tail
                        and tail[0]["function"] == expected_callee
                        and all(
                            frame["file"] == str(Path(unittest.case.__file__).resolve())
                            for frame in tail
                        )
                    )
                )
                if (
                    event["kind"] == "failure"
                    and event.get("module") == "builtins"
                    and event.get("type") == "AssertionError"
                    and direct_assertion
                ):
                    classification = "owned-assertion"
                    reason = "Observed AssertionError at the owned behavior expectation."
                elif (
                    event["kind"] == "error"
                    and event.get("module") == "builtins"
                    and event.get("type") == "NotImplementedError"
                    and frames
                    and frames[-1]["file"] in owned_files
                    and tail
                    and all(frame["file"] in owned_files for frame in tail)
                ):
                    target = frames[-1]
                    target_tree = ast.parse((repo / target["file"]).read_text(encoding="utf-8-sig"))
                    stubs = [
                        n
                        for n in ast.walk(target_tree)
                        if isinstance(n, ast.FunctionDef)
                        and n.name == target["function"]
                        and n.lineno <= target["line"] <= n.end_lineno
                    ]
                    for stub in stubs:
                        body = [
                            n
                            for n in stub.body
                            if not (
                                isinstance(n, ast.Expr)
                                and isinstance(n.value, ast.Constant)
                                and isinstance(n.value.value, str)
                            )
                        ]
                        if len(body) != 1 or not isinstance(body[0], ast.Raise):
                            continue
                        exception = body[0].exc
                        constructor = (
                            exception.func if isinstance(exception, ast.Call) else exception
                        )
                        if (
                            isinstance(constructor, ast.Name)
                            and constructor.id == "NotImplementedError"
                            and (
                                not isinstance(exception, ast.Call)
                                or all(isinstance(arg, ast.Constant) for arg in exception.args)
                            )
                        ):
                            classification = "owned-stub"
                            reason = (
                                "Declared expectation called the owned function whose entire body "
                            )
                            reason += "deliberately raises NotImplementedError; "
                            reason += "source and traceback agree."
        if (
            classification == "unproved"
            and len(functions) == 1
            and _cli_stub_provenance(repo, owned_files, tree, functions[0], record)
        ):
            classification = "owned-cli-stub"
            reason = (
                "Instrumented same-interpreter module child reached the exact owned stub; "
                "the selected supported assertion consumes its result. "
                "Windows redirector parentage "
                "is child-reported one-hop metadata, not independent OS-tree/tamper-proof evidence."
            )
        elif classification == "unproved" and record.get("moduleLaunches"):
            reasons = [
                launch.get("reason") or launch.get("child", {}).get("reason")
                for launch in record["moduleLaunches"]
            ]
            reason = (
                "Controlled CLI result provenance is unproved: "
                + "; ".join(r for r in reasons if r)
                if any(reasons)
                else "Controlled CLI requires an actual owned stub exception and an attributable "
                "straight-line result expectation. Supported pure stubs are a sole raise, "
                "or a sole canonical if __name__ == '__main__' containing that raise without "
                "else/side effects. Keep the intended CLI contract; assert its exit/stdout/stderr "
                "from the product cwd. No main() API is required; other topology remains unproved."
            )
        results.append({**record, "classification": classification, "reason": reason})
    meaningful = any(
        r["classification"] in {"owned-assertion", "owned-stub", "owned-cli-stub"} for r in results
    )
    complete = seen == expected and all(r["classification"] != "unproved" for r in results)
    return dict(
        classification="meaningful-red" if meaningful and complete else "unproved",
        reason="Every selected obligation exercised with owned failure provenance."
        if meaningful and complete
        else "Unrelated, ambiguous or unexecuted selected test; see test events.",
        tests=results,
    )


class _ProspectiveWorkspace:
    """Preserve engine failures and report nonfatal housekeeping warnings."""

    def __init__(self, stage, findings, proofs, collection):
        self.stage, self.findings = stage, findings
        self.proofs, self.collection = proofs, collection
        self.cleanup_warnings = []

    def __enter__(self):
        self.temp = TemporaryDirectory(prefix="harness-prospective-")
        return self.temp.name

    def __exit__(self, kind, body_error, tb):
        if body_error is not None and not isinstance(body_error, Exception):
            return self.temp.__exit__(kind, body_error, tb)
        if body_error is None:
            self.stage["value"] = "body-complete"
        evidence = audit_failure_evidence(
            self.stage["value"], self.findings, self.proofs, self.collection
        )
        if self.cleanup_warnings:
            evidence["cleanupWarnings"] = {
                "count": len(self.cleanup_warnings),
                "items": self.cleanup_warnings[:3],
                "truncated": len(self.cleanup_warnings) > 3,
            }
        warning = guarded_cleanup(self.temp)
        if warning:
            self.cleanup_warnings.append(warning)
        if body_error is not None:
            raise ProspectiveAuditFailure(body_error, warning, evidence) from body_error
        return False


def audit_prospective_tests(*, template: str | Path, manifest: dict, spine: Spine) -> dict:
    """Check planned proofs on disposable stubs, without claiming satisfiability."""
    findings, proofs, collection = [], [], []

    def fail(code: str, bead: str, module: str, output: str) -> None:
        findings.append(
            dict(code=code, severity="blocker", bead=bead, module=module, output=output)
        )

    runner = resolve_runner(manifest)
    stage = {"value": "preparation"}
    workspace = _ProspectiveWorkspace(stage, findings, proofs, collection)
    with workspace as temp:
        root = Path(temp)
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        plan = load_plan(path)
        if not plan.beads or not spine.ids("requirement"):
            fail(
                "prospective-no-proof-obligations",
                "plan",
                "",
                "Prospective credit requires requirements and beads with red proof obligations; "
                "successful collection alone is not proof.",
            )
        repo = root / "template"
        requirement_file_sources(
            template, list(dict.fromkeys((*plan.requirement_files, runner.test_dir)))
        )
        shutil.copytree(template, repo, symlinks=False)
        protected = requirement_file_sources(
            repo, list(dict.fromkeys((*plan.requirement_files, runner.test_dir)))
        )
        paths = runner.proof_paths(repo)
        trees = {}
        stage["value"] = "collection"
        if runner.name == "command":
            for field, value in (
                ("listArgv", runner.list_argv),
                ("assertPatterns", runner.assert_patterns),
                ("importPatterns", runner.import_patterns),
            ):
                if value is None:
                    fail(
                        "prospective-runner-contract-missing",
                        "runner",
                        field,
                        f"Command prospective audit requires {field}.",
                    )
            if not findings:
                modules = sorted(
                    {
                        *(p.relative_to(repo).as_posix() for p in paths),
                        *(bead.proof_module for bead in plan.beads.values()),
                        *([plan.integration_proof_module] if plan.integration_proof_module else []),
                    }
                )
                collection = _collect_command(repo, runner, modules)
                for item in collection:
                    if not item["passed"]:
                        fail(
                            "prospective-not-collectable",
                            "collection",
                            item["module"],
                            item["output"],
                        )
                for bead_id, bead in plan.beads.items():
                    if not all(item["passed"] for item in collection):
                        continue
                    proof_path = repo / bead.proof_module
                    if not proof_path.is_file():
                        fail(
                            "prospective-not-collectable",
                            bead_id,
                            bead.proof_module,
                            "Proof file is missing.",
                        )
                        continue
                    source = proof_path.read_text(encoding="utf-8-sig")
                    if not any(re.search(pattern, source) for pattern in runner.assert_patterns):
                        fail(
                            "prospective-no-assertion",
                            bead_id,
                            bead.proof_module,
                            "Proof file has no assertPatterns match.",
                        )
                    owned = {(repo / name).resolve() for name in bead.owned_files}
                    if not _imports_owned(source, proof_path, owned, runner):
                        fail(
                            "prospective-no-interface-assertion",
                            bead_id,
                            bead.proof_module,
                            "Proof file has no import resolving to a bead-owned file.",
                        )
        else:
            # Parse helpers too: skipped tests must not conceal syntax errors.
            for name, source in protected.items():
                if source.suffix == ".py":
                    try:
                        trees[name] = ast.parse(source.read_text(encoding="utf-8-sig"))
                    except SyntaxError as error:
                        fail("prospective-syntax-error", "collection", name, str(error))
            # Discovery imports every module without running test bodies. Failed
            # imports and empty selectors cannot masquerade as expected red tests.
            script = (
                "_stage_errors = []\n"
                "def _record_stage(text):\n"
                "    try:\n"
                "        with open(_stage_path, 'ab', buffering=0) as marker:\n"
                "            marker.write((text + '\\n').encode('ascii'))\n"
                "    except OSError as error:\n"
                "        _stage_errors.append(type(error).__name__)\n"
                "_record_stage('script-entered')\n"
                "import os, sys\n"
                "def _stage(name, count=None):\n"
                "    text = name + ' pid=' + str(os.getpid())\n"
                "    if count is not None:\n"
                "        text += ' count=' + str(count)\n"
                "    _record_stage(text)\n"
                "    while _stage_errors:\n"
                "        print('HARNESS_COLLECTION_DIAGNOSTIC write-error=' + "
                "_stage_errors.pop(0), file=sys.stderr, flush=True)\n"
                "    print('HARNESS_COLLECTION_STAGE ' + text, flush=True)\n"
                "_stage('before-unittest-import')\n"
                "import unittest\n"
                "_stage('before-discovery')\n"
                "suite = unittest.defaultTestLoader.discover(sys.argv[1], pattern=sys.argv[2])\n"
                "errors = unittest.defaultTestLoader.errors\n"
                "print('\\n'.join(errors))\n"
                "count = suite.countTestCases()\n"
                "_stage('discovery-complete', count)\n"
                "print('Collected tests:', count)\n"
                "sys.exit(1 if errors or not suite.countTestCases() else 0)\n"
            )
            source_path = Path(__file__).resolve()
            source_identity = dict(
                scope="on-disk-file",
                path=str(source_path),
                sha256=None,
                isLoadedBytecodeAttestation=False,
                isCommitAttestation=False,
            )
            try:
                source_identity["sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
            except OSError as error:
                source_identity["error"] = type(error).__name__
            server_execution = dict(
                pid=os.getpid(),
                sysExecutable=sys.executable,
                moduleOrigin=__spec__.origin,
                sourceIdentity=source_identity,
            )
            selectors = dict.fromkeys(["test*.py", *(p.name for p in paths), *runner.proof_globs])
            for index, selector in enumerate(selectors):
                stage_path = root / f"collection-{index}.stages"
                preparation_error = None
                try:
                    stage_path.write_bytes(b"parent-created\n")
                except OSError as error:
                    preparation_error = type(error).__name__
                child_script = f"_stage_path = {str(stage_path)!r}\n" + script
                command = [sys.executable, "-B", "-c", child_script, runner.test_dir, selector]
                collection_started = time.monotonic()
                captured_stdout = captured_stderr = None
                control = None
                try:
                    result = run_audit_process(
                        command, cwd=repo / runner.cwd, capture_output=True, text=True, timeout=120
                    )
                    control = getattr(result, "audit_control", None)
                    output = result.stdout + result.stderr
                    captured_stdout, captured_stderr = result.stdout, result.stderr
                    passed = runner.passed(result.returncode)
                except (OSError, subprocess.TimeoutExpired) as error:
                    output, passed = str(error), False
                    control = getattr(error, "audit_control", None)
                    if isinstance(error, subprocess.TimeoutExpired):
                        captured_stdout, captured_stderr = error.stdout, error.stderr
                        for partial in (error.stdout, error.stderr):
                            if partial:
                                if isinstance(partial, bytes):
                                    partial = partial.decode("utf-8", errors="replace")
                                output += "\n" + partial[:4000]
                stage_evidence = dict(path=str(stage_path), preparationError=preparation_error)
                try:
                    with stage_path.open("rb") as marker:
                        recorded = marker.read(4097)
                    stage_evidence.update(
                        status="read",
                        output=recorded[:4096].decode("ascii", errors="replace"),
                        truncated=len(recorded) > 4096,
                    )
                except FileNotFoundError:
                    stage_evidence.update(status="absent")
                except OSError as error:
                    stage_evidence.update(status="read-error", error=type(error).__name__)
                collection.append(
                    dict(
                        module=selector,
                        passed=passed,
                        output=output,
                        command=command,
                        cwd=str(repo / runner.cwd),
                        stage="unittest-discovery",
                        processControl=control,
                        serverExecution=server_execution,
                        stageEvidence=stage_evidence,
                        capturedOutput={
                            name: dict(
                                state="absent"
                                if value is None
                                else "captured"
                                if value
                                else "empty",
                                length=len(value) if value is not None else None,
                            )
                            for name, value in (
                                ("stdout", captured_stdout),
                                ("stderr", captured_stderr),
                            )
                        },
                        elapsedSeconds=round(time.monotonic() - collection_started, 3),
                    )
                )
                if not passed:
                    fail("prospective-not-collectable", "collection", selector, output)
                if (
                    control
                    and control.get("jobHandleRetained")
                    and not control.get("quiescenceVerified")
                ):
                    collection[-1]["output"] += (
                        "\nFurther collection held: owned Job quiescence unverified"
                    )
                    break
        collection_ready = bool(
            collection
            and all(item["passed"] for item in collection)
            and not any(finding["code"] == "prospective-syntax-error" for finding in findings)
        )
        if not collection_ready:
            findings.append(
                dict(
                    code="prospective-interface-unassessed",
                    severity="info",
                    bead="collection",
                    module="",
                    output="Runtime-dependent interface checks: UNASSESSED because collection "
                    "did not complete successfully. No interface-authoring defect is inferred.",
                )
            )
        if collection_ready:
            stage["value"] = "proof"
            for bead_id in topological_order(plan):
                proof = _proof(
                    repo,
                    manifest,
                    plan.beads[bead_id].proof_module,
                    record_execution=True,
                    owned_files=plan.beads[bead_id].owned_files,
                )
                provenance = _failure_provenance(
                    repo, runner, plan.beads[bead_id].owned_files, proof
                )
                proof["failureProvenance"] = provenance
                workspace.cleanup_warnings.extend(proof.get("cleanupWarning", []))
                proofs.append({"bead": bead_id, **proof})
                if runner.passed(proof["exitCode"]):
                    fail("prospective-vacuous-proof", bead_id, proof["module"], proof["output"])
                elif proof["exitCode"] is None or proof["exitCode"] < 0:
                    fail(
                        "prospective-proof-not-executed",
                        bead_id,
                        proof["module"],
                        proof["output"],
                    )
                elif provenance["classification"] != "meaningful-red":
                    fail(
                        "prospective-red-unproved",
                        bead_id,
                        proof["module"],
                        provenance["reason"] + "\n" + proof["output"],
                    )
        stage["value"] = "traceability"
        for requirement in spine.ids("requirement"):
            linked = [
                spine.node(key) for key in spine.ids("test") if requirement in spine.ancestors(key)
            ]
            if not linked:
                fail(
                    "prospective-traceability-gap",
                    requirement,
                    "",
                    "Requirement has no test node.",
                )
            bead_ids = {key for key in plan.beads if requirement in spine.ancestors(key)}
            beads = [plan.beads[key] for key in bead_ids]
            owned_modules = {
                name.removeprefix("src/")[:-3].removesuffix("/__init__").replace("/", ".")
                for bead in beads
                for name in bead.owned_files
                if name.endswith(".py")
            }
            for test in linked:
                module = test["fields"]["module"]
                selected = [
                    p
                    for p in paths
                    if (
                        p.relative_to(repo).as_posix() == module
                        if runner.name == "command"
                        else fnmatch.fnmatchcase(p.name, module)
                    )
                ]
                if not selected:
                    fail(
                        "prospective-traceability-gap",
                        requirement,
                        module,
                        "Test has no protected proof module.",
                    )
                for proof_path in selected:
                    tree = trees.get(proof_path.relative_to(repo).as_posix())
                    if tree is None or not collection_ready:
                        continue
                    tests = [
                        node
                        for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                        and node.name.startswith("test")
                    ]
                    if not tests:
                        fail(
                            "prospective-no-interface-assertion",
                            requirement,
                            module,
                            "Protected proof must contain test functions.",
                        )
                    for function in tests:
                        runtime_cli = any(
                            proof["bead"] in bead_ids
                            and any(
                                record["classification"] == "owned-cli-stub"
                                and record["source"]["file"]
                                == proof_path.relative_to(repo).as_posix()
                                and record["method"] == function.name
                                and record["source"]["line"]
                                == min(
                                    [function.lineno, *(d.lineno for d in function.decorator_list)]
                                )
                                for record in proof["failureProvenance"]["tests"]
                            )
                            for proof in proofs
                        )
                        if not runtime_cli and not any(
                            isinstance(node, ast.Assert)
                            or isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr.startswith("assert")
                            for node in ast.walk(function)
                        ):
                            fail(
                                "prospective-no-assertion",
                                requirement,
                                module,
                                f"{function.name} has no assertion (including skipped tests).",
                            )
                        elif not (
                            _interface_assertion(tree, function, owned_modules) or runtime_cli
                        ):
                            fail(
                                "prospective-no-interface-assertion",
                                requirement,
                                module,
                                f"{function.name} has no supported owned-interface assertion or "
                                "controlled Python -m result expectation. CLI helper/control-flow "
                                "topology may be unsupported; no product main() API is required.",
                            )
        result = dict(
            baseline="template",
            proofLevel="prospective",
            ok=not findings,
            findings=findings,
            expectedFailures=[],
            collection=collection,
            redBaseline=proofs,
            integrationChecked=False,
            integrationReason=(
                "Prospective tests were not verified against a reference implementation."
            ),
            integrationProof=None,
        )
    if workspace.cleanup_warnings:
        result["cleanupWarning"] = workspace.cleanup_warnings
    return result


def _overlay_requirements(destination: Path, template: Path, names: list[str]) -> None:
    sources = requirement_file_sources(template, names)
    # Replace the whole protected set, including files deleted in the revision.
    for name in names:
        target = destination / name.replace("\\", "/")
        if target.is_symlink() or not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError(f"unsafe requirement destination: {name}")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    for name, source in sources.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _proof(
    repo: Path,
    manifest: dict,
    module: str,
    *,
    record_execution: bool = False,
    owned_files: tuple[str, ...] | None = None,
) -> dict:
    return validate_proof(repo, manifest, module, owned_files=owned_files or ())


def audit_revised_tests(
    *,
    repo: str | Path,
    base_ref: str = "auto",
    revised_template: str | Path,
    manifest: dict,
    impact: dict,
    oracle_dir: str | Path | None = None,
) -> dict:
    """Return major/blocker findings and informational newly failing bead proofs.

    ``expectedFailures`` contains only affected proofs that passed at base_ref.
    An unaffected failing proof is always a major finding, even if already broken
    at base_ref. Without an oracle this checks impact, not satisfiability.
    """
    findings, expected = [], []
    integration_checked = False
    integration_reason = "No oracle was supplied; integration satisfiability was not checked."
    integration_proof = None
    cleanup_warnings = []
    if base_ref == "auto":
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            check=False,
        )
        base_ref = "HEAD" if head.returncode == 0 and (Path(repo) / ".git").exists() else "template"
    workspace = AuditTemporaryDirectory(prefix="harness-test-audit-")
    with workspace as temp:
        root = Path(temp)
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        plan = load_plan(manifest_path)  # Canonical path/ownership/dependency validation.
        affected = set(impact["affectedBeads"]) & plan.beads.keys()
        # A greenfield template has stubs, not integrated unaffected code.
        if base_ref == "template":
            affected = set(plan.beads)
        order = topological_order(plan)
        baseline = root / "baseline"
        if base_ref == "template":
            if oracle_dir is None:
                raise ValueError("Template baseline requires an oracle")
            shutil.copytree(revised_template, baseline, symlinks=True)
        else:
            baseline.mkdir()
            archive = subprocess.run(
                ["git", "-C", str(repo), "archive", "--format=tar", base_ref],
                capture_output=True,
                check=True,
            ).stdout
            with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
                tree.extractall(baseline, filter="data")
        revised = root / "revised"
        shutil.copytree(baseline, revised, symlinks=True)
        _overlay_requirements(
            revised,
            Path(revised_template),
            list(dict.fromkeys((*plan.requirement_files, resolve_runner(plan).test_dir))),
        )
        baseline_proofs = {
            bead_id: _proof(baseline, manifest, plan.beads[bead_id].proof_module)
            for bead_id in order
        }
        for proof in baseline_proofs.values():
            cleanup_warnings.extend(proof.get("cleanupWarning", []))
        for bead_id in order:
            proof = _proof(revised, manifest, plan.beads[bead_id].proof_module)
            cleanup_warnings.extend(proof.get("cleanupWarning", []))
            if proof["passed"]:
                continue
            if bead_id not in affected:
                findings.append(
                    {"code": "unexpected-failure", "severity": "major", "bead": bead_id, **proof}
                )
            elif baseline_proofs[bead_id]["passed"]:
                expected.append(
                    {"code": "expected-failures", "severity": "info", "bead": bead_id, **proof}
                )
        if oracle_dir is not None:
            oracle = root / "oracle"
            shutil.copytree(revised, oracle, symlinks=True)
            sources = requirement_file_sources(
                oracle_dir,
                sorted({name for bead_id in affected for name in plan.beads[bead_id].owned_files}),
            )
            owned_files = {name for bead_id in affected for name in plan.beads[bead_id].owned_files}
            if set(sources) != {name.replace("\\", "/") for name in owned_files}:
                raise ValueError("Oracle must supply every affected owned file")
            for name, source in sources.items():
                target = oracle / name
                if target.is_symlink() or not target.resolve().is_relative_to(oracle.resolve()):
                    raise ValueError(f"unsafe oracle destination: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            for bead_id in order:
                if bead_id in affected:
                    proof = _proof(oracle, manifest, plan.beads[bead_id].proof_module)
                    cleanup_warnings.extend(proof.get("cleanupWarning", []))
                    if not proof["passed"]:
                        findings.append(
                            {
                                "code": "revised-tests-unsatisfiable",
                                "severity": "blocker",
                                "bead": bead_id,
                                **proof,
                            }
                        )
            if plan.integration_proof_module:
                integration_proof = _proof(oracle, manifest, plan.integration_proof_module)
                cleanup_warnings.extend(integration_proof.get("cleanupWarning", []))
                integration_checked = True
                integration_reason = None
                if not integration_proof["passed"]:
                    findings.append(
                        {
                            "code": "revised-tests-unsatisfiable",
                            "severity": "blocker",
                            "bead": "integration",
                            **integration_proof,
                        }
                    )
            else:
                integration_reason = "The manifest declares no integration proof module."
        result = {
            "baseline": base_ref,
            **({"proofLevel": "impact-only"} if oracle_dir is None else {}),
            "ok": not findings,
            "findings": findings,
            "expectedFailures": expected,
            "integrationChecked": integration_checked,
            "integrationReason": integration_reason,
            "integrationProof": integration_proof,
        }
    if workspace.cleanup_warning:
        cleanup_warnings.append(workspace.cleanup_warning)
    if cleanup_warnings:
        result["cleanupWarning"] = cleanup_warnings
    if findings:
        artifacts = Path(repo) / "state" / "runs" / ("test-audit-" + uuid4().hex)
        artifacts.mkdir(parents=True)
        for index, finding in enumerate(findings):
            artifact = artifacts / f"failure-{index + 1}.txt"
            artifact.write_text(finding["output"], encoding="utf-8")
            finding["artifactPath"] = str(artifact.resolve())
            finding["outputPreview"] = "\n".join(finding["output"].splitlines()[:40])
    return result


def render_test_audit_card(result: dict) -> str:
    """Render a stable, compact Markdown pre-dispatch report."""
    lines = [f"Test audit: {'PASS' if result['ok'] else 'FAIL'}"]
    if result.get("proofLevel") == "prospective":
        lines.append(PROSPECTIVE_LABEL)
    if result.get("baseline"):
        lines.append("Baseline: " + result["baseline"])
    if result.get("integrationChecked") is False:
        lines.append("Integration not checked: " + result["integrationReason"])
    for key in ("findings", "expectedFailures"):
        for finding in sorted(
            result[key], key=lambda item: (item["bead"], item["module"], item["code"])
        ):
            lines.append(
                f"- {finding['severity']}: {finding['code']} — "
                f"{finding['bead']} ({finding['module']})"
            )
            if key == "findings":
                lines.extend(
                    [
                        "```text",
                        finding.get(
                            "outputPreview", "\n".join(finding.get("output", "").splitlines()[:40])
                        ),
                        "```",
                    ]
                )
                if finding.get("artifactPath"):
                    lines.append("Full output: " + finding["artifactPath"])
    if not result["findings"] and not result["expectedFailures"]:
        lines.append("No findings or newly failing proofs.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--old-spine", type=Path, required=True)
    parser.add_argument("--oracle", type=Path)
    args = parser.parse_args()
    spine = load_spine(args.plan / "spine.json")
    template = args.plan / "template"
    result = audit_revised_tests(
        repo=args.repo,
        base_ref=args.base,
        revised_template=template,
        manifest=compile_manifest(spine, template),
        impact=impact(load_spine(args.old_spine), spine),
        oracle_dir=args.oracle,
    )
    print(render_test_audit_card(result), end="")
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
