"""Read-only structural ownership, protected-file and projection observations."""

from __future__ import annotations

import re
import subprocess
from itertools import zip_longest
from pathlib import Path, PurePosixPath

from codex_harness.core.process_launch import hidden_process_kwargs
from codex_harness.core.spine import compile_manifest
from codex_harness.core.spine_render import render_markdown

_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_SEVERITIES = ("blocker", "major", "minor")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(repo), *args],
        **hidden_process_kwargs(),
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
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


def _module_matches(path: str, module: str) -> bool:
    module = module.replace("\\", "/")
    if not module.endswith(".py"):
        module = module.replace(".", "/") + ".py"
    return path == module or path.endswith("/" + module)


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
