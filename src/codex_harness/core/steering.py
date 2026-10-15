"""Read-only, deterministic steering checks against integrated Git snapshots.

Added TODO/FIXME/XXX comments are major. Stub functions are blockers when
public (module functions or public class methods) or named in a requirement;
other stubs are major. These checks identify suspicious work, not proof of
semantic correctness or successful test execution.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from itertools import zip_longest
from pathlib import Path, PurePosixPath

from codex_harness.core.spine import compile_manifest
from codex_harness.core.spine_render import render_markdown

_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_MARKER = re.compile(r"\b(?:TODO|FIXME|XXX)\b")
_SEVERITIES = ("blocker", "major", "minor")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(repo), *args],
        capture_output=True,
        encoding="utf-8",
        check=True,
    ).stdout


def _paths(repo: Path, base: str, head: str) -> set[str]:
    return set(
        _git(
            repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--name-only",
            "-z",
            base,
            head,
            "--",
        )
        .rstrip("\0")
        .split("\0")
    ) - {""}


def _under(path: str, roots: list[str]) -> bool:
    return any(
        path == (root := name.replace("\\", "/").rstrip("/")) or path.startswith(root + "/")
        for name in roots
    )


def _test_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return path.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _test_path(path: str) -> bool:
    return _test_file(path) or any(part in {"test", "tests"} for part in path.split("/"))


def _module_matches(path: str, module: str) -> bool:
    module = module.replace("\\", "/")
    if not module.endswith(".py"):
        module = module.replace(".", "/") + ".py"
    return path == module or path.endswith("/" + module)


def _comments(source: str) -> dict[int, str]:
    return {
        token.start[0]: token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }


def _added_lines(repo: Path, base: str, head: str, path: str) -> set[int]:
    patch = _git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--unified=0",
        base,
        head,
        "--",
        path,
    )
    added = set()
    line = None
    for text in patch.splitlines():
        if match := re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", text):
            line = int(match[1])
        elif line is not None:
            if text.startswith("+"):
                added.add(line)
                line += 1
            elif text.startswith(" "):
                line += 1
    return added


def _stub(node, comments: dict[int, str]) -> bool:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body = body[1:]
    if len(body) == 1:
        statement = body[0]
        if isinstance(statement, ast.Pass) or (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and statement.value.value is Ellipsis
        ):
            return True
        if isinstance(statement, ast.Return) and (
            statement.value is None or isinstance(statement.value, ast.Constant)
        ):
            return any(
                _MARKER.search(comments.get(line, ""))
                for line in range(node.lineno, node.end_lineno + 1)
            )
    # Walk this function's control flow, but not nested functions/classes.
    pending = body[:]
    while pending:
        statement = pending.pop()
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if isinstance(statement, ast.Raise):
            error = statement.exc
            error = error.func if isinstance(error, ast.Call) else error
            if isinstance(error, ast.Name) and error.id == "NotImplementedError":
                return True
            if isinstance(error, ast.Attribute) and error.attr == "NotImplementedError":
                return True
        pending.extend(ast.iter_child_nodes(statement))
    return False


def _python_findings(source: str, added: set[int], requirement_text: str, product: bool):
    tree = ast.parse(source)
    comments = _comments(source)
    for line in sorted(added & comments.keys()):
        if _MARKER.search(comments[line]):
            yield "stub-left", "major", line, f"Added unfinished-work comment: {comments[line]}"
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _stub(node, comments):
            parent = parents[node]
            public = not node.name.startswith("_") and (
                isinstance(parent, ast.Module)
                or isinstance(parent, ast.ClassDef)
                and not parent.name.startswith("_")
            )
            named = re.search(rf"\b{re.escape(node.name)}\b", requirement_text)
            yield (
                "stub-left",
                "blocker" if public or named else "major",
                node.lineno,
                f"Function {node.name} contains a placeholder implementation",
            )
        if not product:
            continue
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
            modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
        if any(
            module == "mock"
            or module.startswith("mock.")
            or module == "unittest.mock"
            or module.startswith("unittest.mock.")
            for module in modules
        ):
            yield "mock-in-product", "major", node.lineno, "Mock library imported in product code"
        name = (
            node.name
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            else node.id
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
            else node.attr
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
            else node.arg
            if isinstance(node, ast.arg)
            else node.asname or node.name.split(".")[0]
            if isinstance(node, ast.alias)
            else None
        )
        if name and name.startswith(("fake_", "dummy_", "stub_")):
            yield "mock-in-product", "major", node.lineno, f"Placeholder identifier defined: {name}"


def steering_report(
    *, repo: Path, base_commit: str, head_commit: str, manifest: dict | None = None, spine=None
) -> dict:
    """Inspect committed content only; Git/read errors propagate instead of claiming clean.

    Ownership is exact-file ownership. Requirement roots include descendants.
    A missing manifest is compiled from the spine when supplied; otherwise all
    changed files are unowned. Missing any mapped test is missing planned proof.
    """
    base, head = (
        _git(
            repo, "show", "--no-patch", "--format=%H", "--end-of-options", f"{ref}^{{commit}}"
        ).strip()
        for ref in (base_commit, head_commit)
    )
    if manifest is None:
        manifest = compile_manifest(spine) if spine is not None else {}
    beads = manifest.get("beads", {})
    requirement_files = manifest.get("requirementFiles", [])
    head_paths = _paths(repo, _EMPTY_TREE, head)
    findings = []

    def add(code, severity, bead, path, line, detail, ids):
        findings.append(
            {
                "code": code,
                "severity": severity,
                "bead": bead,
                "path": path,
                "line": line,
                "detail": detail,
                "requirementIds": sorted(set(ids)),
            }
        )

    for path in sorted(_paths(repo, base, head)):
        owners = sorted(
            key
            for key, bead in beads.items()
            if path in [name.replace("\\", "/") for name in bead["ownedFiles"]]
        )
        if _under(path, requirement_files):
            ids = [item for key in owners for item in beads[key].get("requirements", [])]
            add(
                "test-weakened",
                "blocker",
                owners[0] if owners else None,
                path,
                None,
                "Integrated change modifies a protected requirement file",
                ids,
            )
            continue
        if not owners:
            add("ownership-drift", "major", None, path, None, "Changed file has no bead owner", [])
            continue
        if not path.endswith(".py") or path not in head_paths:
            continue
        source = _git(repo, "show", f"{head}:{path}")
        added = _added_lines(repo, base, head, path)
        for owner in owners:
            ids = beads[owner].get("requirements", [])
            text = "\n".join(ids)
            if spine is not None:
                text += "\n" + "\n".join(spine.node(key)["text"] for key in ids)
            for code, severity, line, detail in _python_findings(
                source, added, text, not _test_path(path)
            ):
                add(code, severity, owner, path, line, detail, ids)

    if spine is not None:
        for path, expected in render_markdown(spine).items():
            actual = _git(repo, "show", f"{head}:{path}") if path in head_paths else ""
            if actual == expected:
                continue
            line = next(
                index
                for index, (old, new) in enumerate(
                    zip_longest(
                        actual.splitlines(keepends=True), expected.splitlines(keepends=True)
                    ),
                    1,
                )
                if old != new
            )
            revision = re.match(
                r"<!-- Generated from spine\.json; document revision ([0-9a-f]{12}) -->",
                actual,
            )
            stale_revision = revision is not None and revision[0] != expected.splitlines()[0]
            add(
                "doc-drift",
                "blocker" if stale_revision else "major",
                None,
                path,
                line,
                f"{path} differs from the spine projection at line {line}"
                + ("; document revision is stale" if stale_revision else ""),
                [],
            )
        tests = {key: spine.node(key)["fields"]["module"] for key in spine.ids("test")}
        for path in sorted(head_paths):
            if _test_file(path) and not any(
                _module_matches(path, module) for module in tests.values()
            ):
                add(
                    "unlinked-test",
                    "minor",
                    None,
                    path,
                    None,
                    "Test module has no spine test node",
                    [],
                )
        for requirement in spine.ids("requirement"):
            mapped = [key for key in spine.descendants(requirement) if key in tests]
            missing = [
                tests[key]
                for key in mapped
                if not any(_module_matches(path, tests[key]) for path in head_paths)
            ]
            if missing or not mapped:
                add(
                    "requirement-without-proof",
                    "major",
                    None,
                    None,
                    None,
                    "Missing planned test modules: " + ", ".join(sorted(missing))
                    if mapped
                    else "Requirement has no mapped test module",
                    [requirement],
                )
    findings.sort(
        key=lambda item: (
            _SEVERITIES.index(item["severity"]),
            item["path"] or "",
            item["line"] or 0,
            item["code"],
            item["bead"] or "",
            item["detail"],
            item["requirementIds"],
        )
    )
    return {
        "findings": findings,
        "summary": {
            **{
                severity: sum(item["severity"] == severity for item in findings)
                for severity in _SEVERITIES
            },
            "total": len(findings),
        },
    }


def has_blockers(report: dict) -> bool:
    return any(item["severity"] == "blocker" for item in report["findings"])


def render_steering_card(report: dict) -> str:
    findings = report["findings"]
    counts = {
        severity: sum(item["severity"] == severity for item in findings) for severity in _SEVERITIES
    }
    lines = [
        "clean"
        if not findings
        else f"steering needed: {counts['blocker']} blockers, {counts['major']} major"
    ]
    for severity in _SEVERITIES:
        group = sorted(
            (item for item in findings if item["severity"] == severity),
            key=lambda item: (
                item["path"] or "",
                item["line"] or 0,
                item["code"],
                item["bead"] or "",
                item["detail"],
                item["requirementIds"],
            ),
        )
        if group:
            lines.extend(["", f"{severity} ({len(group)})"])
            for item in group:
                location = item["path"] or ", ".join(item["requirementIds"]) or "plan"
                if item["line"] is not None:
                    location += f":{item['line']}"
                lines.append(f"- {item['code']} [{location}]: {item['detail']}")
    return "\n".join(lines)
