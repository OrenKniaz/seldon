"""Keep the owner-approved E2E journeys and structural guards as the entire suite."""

import ast
from pathlib import Path

CEILINGS = {"suite": 19}
ROOT = Path(__file__).resolve().parent
ALLOWED = {
    "core/test_golden_path.py",
    "core/test_approval_v2_journey.py",
    "core/test_module_size.py",
    "test_suite_budget.py",
}


def test_only_owner_approved_e2e_and_structural_guards(pytestconfig):
    """Catch new test files or tests hidden in support modules.

    Golden journeys exercise product behavior but cannot detect suite-policy drift.
    """
    forbidden = []
    for path in ROOT.rglob("*.py"):
        if path.relative_to(ROOT).as_posix() in ALLOWED:
            continue
        if path.name.startswith("test_") or any(
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
            or isinstance(node, ast.ClassDef)
            and node.name.startswith("Test")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        ):
            forbidden.append(path.relative_to(ROOT).as_posix())
    assert not forbidden, (
        "Seldon allows only golden-path/journey E2E tests (owner rule): "
        + ", ".join(sorted(forbidden))
    )
    assert pytestconfig._suite_count <= CEILINGS["suite"]
