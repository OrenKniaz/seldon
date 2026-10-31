"""Packets provide the actual launch interfaces without exposing reference bodies."""

import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from codex_harness.core.graph import load_plan
from codex_harness.core.spine import compile_manifest, load_spine
from codex_harness.core.worker_packet import MAX_CHARACTERS, build_packet

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"


def _checkout(tmp_path):
    root = tmp_path / "plan"
    shutil.copytree(FIXTURE, root)
    manifest = compile_manifest(load_spine(root / "spine.json"))
    (root / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")
    repo = tmp_path / "repo"
    shutil.copytree(root / "template", repo)
    # Simulate A having integrated; the packet must only consult this checkout.
    shutil.copy2(root / "reference/tipsplit/money.py", repo / "tipsplit/money.py")
    return load_plan(root), repo


def test_worker_packet_refuses_oversized_required_spec(tmp_path):
    plan, repo = _checkout(tmp_path)
    (repo / "PLAN.md").write_text("### R4: Huge requirement\n\n" + "x" * (MAX_CHARACTERS + 1))
    with pytest.raises(ValueError, match="contract/spec/interfaces exceed"):
        build_packet(plan, plan.beads["B"], repo, None)


def test_dependency_extraction_never_executes_and_preserves_api(tmp_path):
    plan, repo = _checkout(tmp_path)
    source = '"""Module API.\nSecond module line."""\n'
    source += "raise RuntimeError('IMPORT_MUST_NOT_RUN')\n"
    source += 'def public(value: int = 7, *, label: str = "ok") -> list[int]:\n'
    source += '    """First API line.\nHidden second API line."""\n'
    source += "    BODY_MUST_NOT_APPEAR()\n"
    (repo / "tipsplit/money.py").write_text(source, encoding="utf-8")
    packet = build_packet(plan, plan.beads["B"], repo, None)
    assert "Module API." in packet and "Second module line." in packet
    assert "def public(value: int=7, *, label: str='ok') -> list[int]:" in packet
    assert "First API line." in packet and "Hidden second API line." not in packet
    assert "IMPORT_MUST_NOT_RUN" not in packet and "BODY_MUST_NOT_APPEAR" not in packet


def test_python_protected_helpers_transitive_packages_and_requirements(tmp_path):
    plan, repo = _checkout(tmp_path)
    proof = repo / "tests/test_cli.py"
    proof.write_text("from tests.fixtures import data\nimport contract_inputs\nimport outside\n")
    fixtures = repo / "tests/fixtures"
    fixtures.mkdir()
    (fixtures / "__init__.py").write_text("from . import data\n# PACKAGE_BODY\n")
    (fixtures / "data.py").write_text("from .. import common\n# DATA_BODY\n")
    (repo / "tests/common.py").write_text("from .fixtures import data\n# COMMON_BODY\n")
    (repo / "contract_inputs.py").write_text("# REQUIREMENT_BODY\n")
    (repo / "outside.py").write_text("# OUTSIDE_MUST_NOT_APPEAR\n")
    manifest_path = plan.root / "sample.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["requirementFiles"].append("contract_inputs.py")
    manifest_path.write_text(json.dumps(manifest))
    plan = load_plan(manifest_path)
    packet = build_packet(plan, plan.beads["B"], repo, None)
    assert "Helper files: contract_inputs.py, tests/common.py, " in packet
    for marker in ("PACKAGE_BODY", "DATA_BODY", "COMMON_BODY", "REQUIREMENT_BODY"):
        assert packet.count(marker) == 1
    assert "OUTSIDE_MUST_NOT_APPEAR" not in packet
    assert packet == build_packet(plan, plan.beads["B"], repo, None)


@pytest.mark.parametrize(
    "specifier", ["./fixtures/data.ts", "./fixtures/data", "./fixtures/data.js", "./fixtures"]
)
def test_command_protected_helper_imports(tmp_path, specifier):
    plan, repo = _checkout(tmp_path)
    manifest_path = plan.root / "sample.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["verification"] = {
        "runner": "command",
        "argv": [sys.executable, "{proof}"],
        "cwd": ".",
        "testDir": "tests",
        "proofGlob": "*.test.ts",
        "importPatterns": [r"from\s+['\"]([^'\"]+)['\"]"],
    }
    for key in ("A", "B"):
        manifest["beads"][key]["proofModule"] = "tests/api.test.ts"
    manifest["integration"]["proofModule"] = "tests/api.test.ts"
    fixtures = repo / "tests/fixtures"
    fixtures.mkdir()
    (fixtures / "data.ts").write_text("export { shared } from '../shared.js';\n// TS_HELPER_BODY\n")
    (fixtures / "index.js").write_text("export { shared } from './data.ts';\n// INDEX_BODY\n")
    (repo / "tests/shared.js").write_text(
        "export { data } from './fixtures/data.ts';\n// SHARED_BODY\n"
    )
    (repo / "outside.ts").write_text("// OUTSIDE_MUST_NOT_APPEAR\n")
    (repo / "tests/api.test.ts").write_text(
        f"import {{ data }} from '{specifier}';\nimport {{ x }} from '../outside.ts';\n"
    )
    manifest_path.write_text(json.dumps(manifest))
    plan = load_plan(manifest_path)
    packet = build_packet(plan, plan.beads["B"], repo, None)
    assert "Helper files: tests/fixtures/" in packet
    assert packet.count("TS_HELPER_BODY") == packet.count("SHARED_BODY") == 1
    assert ("INDEX_BODY" in packet) == (specifier == "./fixtures")
    assert "OUTSIDE_MUST_NOT_APPEAR" not in packet


def test_packet_ignores_parent_project_database(tmp_path, monkeypatch):
    """Protect isolation; ancestor lookup leaks identity, absent in golden fixtures."""
    from codex_harness.core.plan_store import open_plan_store

    plan, repo = _checkout(tmp_path)
    with open_plan_store(tmp_path) as store:
        store.upsert_node(
            dict(
                id="V1",
                kind="vision",
                title="Parent",
                text="Foreign project",
                fields={"setup": {"githubLogin": "foreign-user"}},
                parents=[],
            )
        )

    def refuse_foreign_database(*args, **kwargs):
        pytest.fail("Packet construction consulted a parent project database")

    monkeypatch.setattr(sqlite3, "connect", refuse_foreign_database)
    packet = build_packet(plan, plan.beads["B"], repo, None)
    assert "GitHub must be" not in packet and "foreign-user" not in packet
    explicit = build_packet(plan, plan.beads["B"], repo, None, setup={"githubLogin": "caller-user"})
    assert "GitHub must be caller-user." in explicit


def test_linked_intent_over_eight_thousand_characters_is_never_dropped(tmp_path):
    """Protect long linked scope from the 8k cutoff; earlier tests use short references."""
    plan, repo = _checkout(tmp_path)
    requirement = "Mandatory accepted wording. " + "r" * 9000
    nodes = [
        dict(kind="vision", title="Locked", text="Full core intent.", fields={}),
        dict(kind="requirement", id=plan.beads["B"].requirements[0], text=requirement),
    ]
    packet = build_packet(plan, plan.beads["B"], repo, None, intent_nodes=nodes)
    excerpt = packet.split("## User intent\n")[1].split("\nSHA-256")[0]
    assert requirement in excerpt
    assert hashlib.sha256(excerpt.encode()).hexdigest() in packet
