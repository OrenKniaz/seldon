"""Prevent test/fast-gate growth, which product journeys cannot detect."""

import ast
import subprocess
from pathlib import Path

CEILINGS = {"suite": 473, "fast": 175}
ROOT = Path(__file__).resolve().parents[2]


def test_suite_and_fast_gate_counts_only_shrink(pytestconfig):
    """Catch collected parameter explosions and raised budgets; module size misses both.

    Count actual pytest items before marker deselection, including passing tests.
    Run against tests/python; focused invocations may collect fewer items.
    """
    path = Path(__file__).relative_to(ROOT).as_posix()
    tracked = subprocess.check_output(
        ["git", "ls-tree", "--name-only", "HEAD", "--", path], cwd=ROOT, text=True
    ).strip()
    baseline = CEILINGS  # Bootstrap until the initial budget is committed.
    if tracked:
        tree = ast.parse(
            subprocess.check_output(["git", "show", f"HEAD:{path}"], cwd=ROOT, text=True)
        )
        baseline = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "CEILINGS" for t in node.targets)
        )
    assert CEILINGS.keys() == baseline.keys()
    for name, count in pytestconfig._diet_counts.items():
        assert CEILINGS[name] <= baseline[name], f"{name}: ceilings only go down"
        assert count <= CEILINGS[name], f"{name}: {count} exceeds {CEILINGS[name]}"
