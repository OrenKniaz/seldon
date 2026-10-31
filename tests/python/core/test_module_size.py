"""Catch renewed god-file growth, which behavioral tests cannot detect."""

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE = "src/codex_harness/core"
CEILINGS = {
    "src/codex_harness/core/acceptance.py": 2036,
    "src/codex_harness/core/dispatch.py": 2838,
    "src/codex_harness/core/mcp_planner.py": 3376,
    "src/codex_harness/core/plan_store.py": 3665,
    "src/codex_harness/core/planner_views.py": 1728,
    "src/codex_harness/core/roundtable.py": 2447,
    "src/codex_harness/core/status_api.py": 1765,
    "src/codex_harness/core/test_audit.py": 1676,
}


def test_core_module_size_ratchets_without_new_exemptions():
    """Protect every core module against growth or a relaxed committed ceiling."""

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8")

    guard = Path(__file__).relative_to(ROOT).as_posix()
    baseline = {}
    if git("ls-tree", "--name-only", "HEAD", "--", guard).strip():
        tree = ast.parse(git("show", f"HEAD:{guard}"))
        # The committed mapping is data, never imported or executed.
        for node in tree.body:
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
    failures = []
    for path in sorted((ROOT / CORE).rglob("*.py")):
        name = path.relative_to(ROOT).as_posix()
        count = len(path.read_text(encoding="utf-8").splitlines())
        ceiling = CEILINGS.get(name, 1500)
        reason = ""
        if name in CEILINGS and (count <= 1500 or count != ceiling):
            reason = "remove this entry" if count <= 1500 else "ratchet ceiling to current count"
        if ceiling > baseline.get(name, 1500) or (name in CEILINGS and name not in baseline):
            reason = "new entries and raised ceilings are forbidden"
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
