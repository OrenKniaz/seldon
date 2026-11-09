"""Audit revised requirement proofs against integrated code and an optional oracle.

Run this preflight before dispatching a revision. All proof execution and overlays
use temporary copies; failing output is saved under state/runs in the repository.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from uuid import uuid4

from codex_harness.core.graph import load_plan, topological_order
from codex_harness.core.planner_audits import AuditTemporaryDirectory
from codex_harness.core.process_launch import hidden_process_kwargs
from codex_harness.core.runners import resolve_runner, validate_proof
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


def audit_prospective_tests(*, template: str | Path, manifest: dict, spine: Spine) -> dict:
    """Gather linked test source; only a bound LLM verdict can judge meaningfulness."""
    from codex_harness.core.runners import execution_sources

    sources = execution_sources(template, spine.to_dict())
    return dict(
        baseline="template", proofLevel="prospective", ok=True,
        evidenceTier="audit: pending judgment", judgmentStatus="pending-judgment",
        findings=[], expectedFailures=[], collection=[], redBaseline=[],
        testSources=sources, testRefs=[dict(id=k, revision=spine.revision(k))
                                     for k in spine.ids("test")],
        didRunValidation=False, integrationChecked=False,
        integrationReason="Source observations only; meaningfulness and correct RED await judgment.",
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
            **hidden_process_kwargs(),
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
                **hidden_process_kwargs(),
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
