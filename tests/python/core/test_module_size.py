"""Catch renewed god-file growth, which behavioral tests cannot detect."""

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE = "src/codex_harness/core"
TOTAL_CEILING = 32001
CEILINGS = {
    "src/codex_harness/core/acceptance.py": 14,
    "src/codex_harness/core/acceptance_reviews.py": 270,
    "src/codex_harness/core/acceptance_revision.py": 120,
    "src/codex_harness/core/acceptance_runs.py": 304,
    "src/codex_harness/core/acceptance_scenarios.py": 279,
    "src/codex_harness/core/acceptance_views.py": 102,
    "src/codex_harness/core/dispatch.py": 836,
    "src/codex_harness/core/execution_assessments.py": 557,
    "src/codex_harness/core/mcp_planner.py": 276,
    "src/codex_harness/core/plan_store.py": 813,
    "src/codex_harness/core/planner_views.py": 51,
    "src/codex_harness/core/review_findings.py": 99,
    "src/codex_harness/core/review_packets.py": 356,
    "src/codex_harness/core/review_provider.py": 324,
    "src/codex_harness/core/review_rounds.py": 387,
    "src/codex_harness/core/roundtable.py": 15,
}


def test_core_module_size_ratchets_without_new_exemptions():
    """Catch module/net growth and relaxed ceilings.

    Per-file limits alone miss growth spread across many small production modules.
    """

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8")

    guard = Path(__file__).relative_to(ROOT).as_posix()
    baseline = {}
    total_baseline = TOTAL_CEILING
    if git("ls-tree", "--name-only", "HEAD", "--", guard).strip():
        tree = ast.parse(git("show", f"HEAD:{guard}"))
        # The committed mapping is data, never imported or executed.
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "TOTAL_CEILING" for t in node.targets
            ):
                total_baseline = ast.literal_eval(node.value)
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "CEILINGS" for target in node.targets
            ):
                baseline = ast.literal_eval(node.value)
    else:
        for path in git("ls-tree", "-r", "--name-only", "HEAD", "--", CORE).splitlines():
            if path.endswith(".py"):
                count = len(git("show", f"HEAD:{path}").splitlines())
                if count > 1500:
                    baseline[path] = count
    total = sum(
        len((ROOT / name).read_text(encoding="utf-8").splitlines())
        for name in git(
            "ls-files", "--cached", "--others", "--exclude-standard", "--", "src/codex_harness"
        ).splitlines()
        if (ROOT / name).is_file()
    )
    assert total <= TOTAL_CEILING <= total_baseline, (total, TOTAL_CEILING, total_baseline)
    failures = []
    for path in sorted((ROOT / CORE).rglob("*.py")):
        name = path.relative_to(ROOT).as_posix()
        count = len(path.read_text(encoding="utf-8").splitlines())
        ceiling = CEILINGS.get(name, 1500)
        reason = ""
        if name in CEILINGS and count != ceiling:
            reason = "ratchet ceiling to current count"
        if ceiling > baseline.get(name, 1500):
            reason = "raised ceilings and new exemptions are forbidden"
        if count > ceiling or reason:
            failures.append(
                f"{name}: count {count}, ceiling {ceiling}; {reason}; "
                "move new logic into a focused module"
            )
    for name in CEILINGS.keys() - {
        p.relative_to(ROOT).as_posix() for p in (ROOT / CORE).rglob("*.py")
    }:
        failures.append(
            f"{name}: count 0, ceiling {CEILINGS[name]}; remove this entry; "
            "move new logic into a focused module"
        )
    assert not failures, "\n".join(failures)
