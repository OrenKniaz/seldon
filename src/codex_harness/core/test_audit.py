"""Audit revised requirement proofs against integrated code and an optional oracle.

Run this preflight before dispatching a revision. All proof execution and overlays
use temporary copies; failing output is saved under state/runs in the repository.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import io
import json
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from codex_harness.core.graph import load_plan, topological_order
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
        try:
            command = runner.list_argv_for(module)
            result = subprocess.run(
                command, cwd=repo / runner.cwd, capture_output=True, text=True, timeout=120
            )
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
        collection.append(dict(module=module, command=command, passed=bool(passed), output=output))
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
        results.append({**record, "classification": classification, "reason": reason})
    meaningful = any(r["classification"] in {"owned-assertion", "owned-stub"} for r in results)
    complete = seen == expected and all(r["classification"] != "unproved" for r in results)
    return dict(
        classification="meaningful-red" if meaningful and complete else "unproved",
        reason="Every selected obligation exercised with owned failure provenance."
        if meaningful and complete
        else "Unrelated, ambiguous or unexecuted selected test; see test events.",
        tests=results,
    )


def audit_prospective_tests(*, template: str | Path, manifest: dict, spine: Spine) -> dict:
    """Check planned proofs on disposable stubs, without claiming satisfiability."""
    findings, proofs, collection = [], [], []

    def fail(code: str, bead: str, module: str, output: str) -> None:
        findings.append(
            dict(code=code, severity="blocker", bead=bead, module=module, output=output)
        )

    runner = resolve_runner(manifest)
    with TemporaryDirectory(prefix="harness-prospective-") as temp:
        root = Path(temp)
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        plan = load_plan(path)
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
                "import sys, unittest\n"
                "suite = unittest.defaultTestLoader.discover(sys.argv[1], pattern=sys.argv[2])\n"
                "errors = unittest.defaultTestLoader.errors\n"
                "print('\\n'.join(errors))\n"
                "print('Collected tests:', suite.countTestCases())\n"
                "sys.exit(1 if errors or not suite.countTestCases() else 0)\n"
            )
            selectors = dict.fromkeys(["test*.py", *(p.name for p in paths), *runner.proof_globs])
            for selector in selectors:
                command = [sys.executable, "-B", "-c", script, runner.test_dir, selector]
                try:
                    result = subprocess.run(
                        command, cwd=repo / runner.cwd, capture_output=True, text=True, timeout=120
                    )
                    output = result.stdout + result.stderr
                    passed = runner.passed(result.returncode)
                except (OSError, subprocess.TimeoutExpired) as error:
                    output, passed = str(error), False
                collection.append(dict(module=selector, passed=passed, output=output))
                if not passed:
                    fail("prospective-not-collectable", "collection", selector, output)
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
            beads = [
                bead for key, bead in plan.beads.items() if requirement in spine.ancestors(key)
            ]
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
                    if tree is None:
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
                        if not any(
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
                        elif not _interface_assertion(tree, function, owned_modules):
                            fail(
                                "prospective-no-interface-assertion",
                                requirement,
                                module,
                                f"{function.name} has no assertion referencing "
                                "an imported bead-owned interface.",
                            )
        if (
            collection
            and all(item["passed"] for item in collection)
            and not any(finding["code"] == "prospective-syntax-error" for finding in findings)
        ):
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
    return dict(
        baseline="template",
        proofLevel="prospective",
        ok=not findings,
        findings=findings,
        expectedFailures=[],
        collection=collection,
        redBaseline=proofs,
        integrationChecked=False,
        integrationReason="Prospective tests were not verified against a reference implementation.",
        integrationProof=None,
    )


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
    if base_ref == "auto":
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            check=False,
        )
        base_ref = "HEAD" if head.returncode == 0 and (Path(repo) / ".git").exists() else "template"
    with TemporaryDirectory(prefix="harness-test-audit-") as temp:
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
        for bead_id in order:
            proof = _proof(revised, manifest, plan.beads[bead_id].proof_module)
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
    if findings:
        artifacts = Path(repo) / "state" / "runs" / ("test-audit-" + uuid4().hex)
        artifacts.mkdir(parents=True)
        for index, finding in enumerate(findings):
            artifact = artifacts / f"failure-{index + 1}.txt"
            artifact.write_text(finding["output"], encoding="utf-8")
            finding["artifactPath"] = str(artifact.resolve())
            finding["outputPreview"] = "\n".join(finding["output"].splitlines()[:40])
    return {
        "baseline": base_ref,
        **({"proofLevel": "impact-only"} if oracle_dir is None else {}),
        "ok": not findings,
        "findings": findings,
        "expectedFailures": expected,
        "integrationChecked": integration_checked,
        "integrationReason": integration_reason,
        "integrationProof": integration_proof,
    }


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
