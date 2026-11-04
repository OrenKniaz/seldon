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
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from uuid import uuid4

from codex_harness.core.graph import load_plan, topological_order
from codex_harness.core.planner_audits import AuditTemporaryDirectory
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
    # Subprocess/HTTP tests exercise the same public boundary without importing it.
    # This recognizes a planned assertion, not executed RED or product acceptance.
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update((a.asname or a.name.split(".")[0], a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update((a.asname or a.name, f"{node.module}.{a.name}") for a in node.names)
    launches = {name for name, module in imported.items() if module.startswith("subprocess")}
    product = {name.rsplit(".", 1)[-1] for name in owned}
    subprocess_interface = any(
        isinstance(node, ast.Call)
        and ast.unparse(node.func).split(".")[0] in launches
        and any(
            isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and arg.value.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".py") in product
            for arg in ast.walk(tree)
        )
        for node in ast.walk(tree)
    )
    if subprocess_interface:
        bindings.update(
            name
            for name, module in imported.items()
            if module.startswith(("subprocess", "urllib.request", "http.client"))
        )
    bindings.update(
        name
        for name, module in imported.items()
        if module in owned or module.rsplit(".", 1)[0] in owned
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
        for node in ast.walk(tree):
            if isinstance(node, ast.withitem) and references(node.context_expr) & bindings:
                if node.optional_vars is not None:
                    bindings.add(ast.unparse(node.optional_vars))
            elif isinstance(node, ast.FunctionDef) and references(node) & bindings:
                bindings.add(node.name)
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


def audit_prospective_tests(*, template: str | Path, manifest: dict, spine: Spine) -> dict:
    """Classify planned interfaces; execution owns collection and RED→GREEN.

    An analyzer limitation is advisory and never an invitation to rewrite a real
    interface proof. No test import or subprocess runs during planning.
    """
    repo, runner, findings = Path(template), resolve_runner(manifest), []

    def unrecognized(bead, module, output):
        findings.append(
            dict(
                code="prospective-interface-unrecognized",
                severity="info",
                bead=bead,
                module=module,
                output=output,
            )
        )

    paths = runner.proof_paths(repo)
    for bead_id, bead in manifest["beads"].items():
        module = bead["proofModule"]
        selected = [
            p
            for p in paths
            if (
                p.relative_to(repo).as_posix() == module
                if runner.name == "command"
                else fnmatch.fnmatchcase(p.name, module)
            )
        ]
        owned = {
            name.removeprefix("src/")[:-3].removesuffix("/__init__").replace("/", ".")
            for name in bead["ownedFiles"]
            if name.endswith(".py")
        }
        if not selected:
            unrecognized(bead_id, module, "No protected proof matched; execution must collect it.")
        for path in selected:
            try:
                source = path.read_text(encoding="utf-8-sig")
                if runner.name == "command":
                    recognized = bool(runner.assert_patterns and runner.import_patterns) and (
                        any(re.search(pattern, source) for pattern in runner.assert_patterns)
                        and _imports_owned(
                            source,
                            path,
                            {(repo / name).resolve() for name in bead["ownedFiles"]},
                            runner,
                        )
                    )
                else:
                    tree = ast.parse(source)
                    tests = [
                        n
                        for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef) and n.name.startswith("test")
                    ]
                    recognized = bool(tests) and all(
                        _interface_assertion(tree, test, owned) for test in tests
                    )
                if not recognized:
                    unrecognized(
                        bead_id,
                        module,
                        "Planned interface assertion is unrecognized; "
                        "execution must prove collection and RED→GREEN.",
                    )
            except (OSError, SyntaxError, UnicodeError) as error:
                unrecognized(bead_id, module, str(error))
    for requirement in spine.ids("requirement"):
        if not any(requirement in spine.ancestors(key) for key in spine.ids("test")):
            unrecognized(requirement, "", "Requirement has no linked proof; execution remains due.")
    return dict(
        baseline="template",
        proofLevel="prospective",
        ok=True,
        evidenceTier="audit: unrecognized interface" if findings else "audit: recognized interface",
        findings=findings,
        expectedFailures=[],
        collection=[],
        redBaseline=[],
        didRunValidation=False,
        integrationChecked=False,
        integrationReason="Planning classification only; collection and RED→GREEN await execution.",
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


def _proof(repo: Path, manifest: dict, module: str) -> dict:
    return validate_proof(repo, manifest, module)


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
        if result.get("evidenceTier"):
            lines.append(result["evidenceTier"] + "; execution RED→GREEN still decides.")
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
