"""Packets provide the actual launch interfaces without exposing reference bodies."""

import ast
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from codex_harness.core.graph import load_plan
from codex_harness.core.spine import compile_manifest, load_spine
from codex_harness.core.worker_packet import MAX_CHARACTERS, build_packet

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"

EXPECTED_TEST_POLICY = (
    "Workers: for every new or changed test, name the protected observable behavior, "
    "a credible regression, and the proof missing from existing evidence. Put one primary "
    "regression at the strongest owning boundary; add another layer only for a distinct "
    "uncovered risk. For repairs, show a real failure before the fix and a pass after it; "
    "report missing baseline proof. Use disposable realistic data and fixed independent "
    "expectations. Reject source-reading assertions, self-comparisons, mocks that supply "
    "the asserted behavior, call-shape-only checks, and production seams added only for tests. "
    "Extend existing fixtures when useful. Preserve meaningful coverage, including slow tests; "
    "do not delete coverage, weaken assertions, or target a test count.\n"
    "Run focused owner checks and relevant siblings while working. Reuse valid unchanged "
    "proof only when relevant source, configuration, data and execution assumptions still "
    "match; state its scope and invalidation conditions. Broaden or repeat checks only for "
    "changed inputs, a failure, a named unresolved risk, or a required gate. The integrator "
    "owns the required final integration gate on the actual candidate; every worker must "
    "not repeat that gate. Follow the exact proof command and stop rule below.\n"
    "Reviewers: each finding must name a violated guarantee, a justified missing guarantee, "
    "or a user choice. Request proof only for a named uncovered risk, explain what existing "
    "evidence misses, and identify the smallest necessary check. Reuse matching proof. "
    "Report actual commands/results, reused proof and unrun checks truthfully. Rendered "
    "packet tests establish instructions delivered only; they do not establish worker "
    "compliance, product acceptance, full-vision delivery or supervision savings.\n"
)


@pytest.mark.parametrize("fixture", ["dogfood-tipsplit", "handoff_variation", "dogfood-watercli"])
def test_legacy_packets_match_narrative_revision_bytes(tmp_path, fixture):
    # All fixture/provider classes deliberately add the cross-route denial rule.
    # Protect launch/recovery instruction delivery: existing golden hashes alone
    # cannot detect missing agreed test policy. Check that addition independently,
    # then preserve every other frozen byte, including requirements/source context.
    expected = json.loads(
        (Path(__file__).parent / "fixtures/legacy_packet_hashes.json").read_text()
    )
    source = (
        Path(__file__).parent / "fixtures" / fixture
        if fixture == "handoff_variation"
        else FIXTURE.parent / fixture
    )
    root = tmp_path / fixture
    shutil.copytree(source, root)
    manifest = compile_manifest(load_spine(root / "spine.json"), root / "template")
    (root / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")
    plan = load_plan(root)
    for bead in plan.beads.values():
        for provider in ("codex", "claude", "copilot"):
            packet = build_packet(plan, bead, root / "template", None, provider)
            # Only the explicitly scoped denial clarification changes legacy contract bytes.
            packet = packet.replace(
                "A host/tool/sandbox permission denial", "A permission denial", 1
            )
            before_scope, scope = packet.split("Denial scope/examples:\n", 1)
            _, after_scope = scope.split("Tools:", 1)
            packet = before_scope + "Tools:" + after_scope
            before, policy = packet.split("TEST POLICY (workers and reviewers):\n", 1)
            policy, after = policy.split("Recovery ladder:", 1)
            assert policy == EXPECTED_TEST_POLICY
            packet = before + "Recovery ladder:" + after
            before, guidance = packet.split("Plugin/skill capabilities: ", 1)
            guidance, after = guidance.split("Recovery ladder:", 1)
            assert "this worker runtime's supported capability discovery" in guidance
            assert "current account/subscription" in guidance
            assert "Parent-session plugins do not automatically transfer" in guidance
            assert (
                "MagicPath is an example for relevant design work, not routine coding" in guidance
            )
            assert "verify the intended account and permissions" in guidance
            assert "OrenKniaz" not in guidance
            assert "GitHub must be" not in guidance
            assert "subject to this packet's ownership, tool and denial rules" in guidance
            assert "do not read credentials or unrelated configuration" in guidance
            assert "install plugins, add subscriptions or paid capacity" in guidance
            assert "obtain credentials, or expand permissions" in guidance
            packet = before + "Recovery ladder:" + after
            before, report = packet.split("Briefly distinguish relevant capabilities", 1)
            report, after = report.split("End with HARNESS-RESULT:", 1)
            assert "discovered as available from those actually used" in report
            assert "unavailable or unverified capabilities" in report
            assert "discovery was unavailable or none were needed" in report
            packet = before + "End with HARNESS-RESULT:" + after
            assert (
                hashlib.sha256(packet.encode()).hexdigest()
                == expected[f"{fixture}/{bead.id}/{provider}"]
            )


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


def test_tipsplit_packet_context_and_rules_are_additive(tmp_path):
    plan, repo = _checkout(tmp_path)
    packet = build_packet(plan, plan.beads["B"], repo, None)
    assert "## Context you already have (do not re-read)" in packet
    assert (repo / "tests/test_cli.py").read_text() in packet
    assert "Read files, not directories; the file list is above." in packet
    assert "Owned files: tipsplit/cli.py" in packet
    assert "Proof files: tests/test_cli.py" in packet
    assert "Dependency files: tipsplit/money.py" in packet
    stop_rule = "When the exact proof command passes, stop: "
    stop_rule += "do not re-run single tests or the suite again. Report."
    assert stop_rule in packet


@pytest.mark.parametrize("provider", ["codex", "claude", "copilot"])
def test_browser_acceptance_does_not_require_optional_worker_tools(tmp_path, provider):
    """Protect tool-independent proof: browser prose/refs used to invite Playwright.

    Golden packets miss this boundary: preserve the compiled proof and GUI
    obligations while explicitly deferring browser execution, without proof credit.
    """
    plan, repo = _checkout(tmp_path)
    checks = "Keyboard focus and delete confirmation require browser observation.\n"
    (repo / "BROWSER-CHECKS.md").write_text(checks, encoding="utf-8")
    spine_path = plan.root / "spine.json"
    spine = json.loads(spine_path.read_text())
    vision = next(node for node in spine["nodes"] if node["kind"] == "vision")
    vision["fields"]["requirementFiles"].append("BROWSER-CHECKS.md")
    bead = next(node for node in spine["nodes"] if node["id"] == "B")
    bead["text"] += "\nBrowser acceptance: " + checks
    bead["fields"]["capabilityRefs"] = ["browser.playwright"]
    spine_path.write_text(json.dumps(spine), encoding="utf-8")
    from codex_harness.core.spine_render import render_into_template

    canonical_spine = load_spine(spine_path)
    render_into_template(canonical_spine, repo)
    manifest = compile_manifest(canonical_spine, repo)
    (plan.root / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")
    plan = load_plan(plan.root)
    packet = build_packet(plan, plan.beads["B"], repo, None, provider)
    assert "Worker proofs use the shell and Python" in packet
    assert "urllib.request" in packet
    assert "defer browser checks" in packet
    assert "no configured browser-tool capability is attested by this packet" in packet
    assert "capabilityRefs are requests, not configuration or access grants" in packet
    assert "HTTP checks do not prove rendering, JavaScript, keyboard or focus" in packet
    assert checks.strip() in packet
    assert "NEVER change requirement files:" in packet
    assert "BROWSER-CHECKS.md" in packet
    assert "python -B -m unittest discover -s tests -p test_cli.py" in packet
    assert "required worker proof needs an unattested browser tool, report the blocker" in packet


@pytest.mark.parametrize("provider", ["codex", "claude", "copilot"])
def test_product_runtime_errors_are_not_host_permission_denials(tmp_path, provider):
    """Protect denial scope after real B3 stopped on an expected socket fixture.

    The existing denial test protects stopping, but cannot distinguish a host
    refusal from ordinary product/runtime debugging input.
    """
    plan, repo = _checkout(tmp_path)
    packet = build_packet(plan, plan.beads["B"], repo, None, provider)
    contract = packet.split("## 2. Goal")[0]
    assert "A host/tool/sandbox permission denial blocks the action" in contract
    assert "Product runtime errors are debugging input, not authorization decisions" in contract
    assert "socket WinError 10013/10048 or expected file-fixture errors" in contract
    assert "debug the product within the owned files and exact proof contract" in contract
    assert "MCP approval refused, sandbox write/network blocked, or credential prompts" in contract
    assert "stop and report; do not use an equivalent shell/tool/route" in contract
    assert "fresh explicit user disposition must identify" in contract


@pytest.mark.parametrize("provider", ["codex", "claude", "copilot"])
def test_packet_denial_overrides_recovery_for_all_providers(tmp_path, provider):
    plan, repo = _checkout(tmp_path)
    packet = build_packet(plan, plan.beads["B"], repo, None, provider)
    contract = packet.split("## 2. Goal")[0]
    assert "permission denial blocks the action across equivalent shells and forms" in contract
    assert "Stop and report it; do not retry" in contract
    assert "Do not fabricate approval" in contract
    assert "This rule overrides the recovery ladder" in contract


def test_bead_narrative_precedes_requirements_and_is_capped(tmp_path):
    from codex_harness.core.worker_packet import NARRATIVE_BYTES

    plan, repo = _checkout(tmp_path)
    packet = build_packet(plan, plan.beads["B"], repo, None)
    assert packet.index("What this bead is for") < packet.index("Plan revision:")
    assert '"title":' in packet and '"dependsOn":' in packet
    rendered = (repo / "PLAN.md").read_text(encoding="utf-8")
    own_detail = rendered.split("## B:", 1)[1].split("\n\n", 1)[1].split("\n\n", 1)[0]
    assert own_detail in packet
    manifest = json.loads((plan.root / "sample.json").read_text())
    raw = {
        "id": "B",
        **manifest["beads"]["B"],
        "detail": "é" * 10000,
        "fields": {"acceptance": "keep narrative bounded"},
    }
    packet = build_packet(plan, raw, repo, None)
    narrative = packet.split("### What this bead is for\n", 1)[1].split("\n\nPlan revision:")[0]
    payload = re.search(r"```\n(.*?)\n```", narrative, re.DOTALL)[1]
    assert len(payload.encode()) <= NARRATIVE_BYTES
    assert "[truncated for context byte limit]" in payload
    assert '"acceptance": "keep narrative bounded"' in payload


def test_late_referenced_mapping_fixture_survives_cap(tmp_path, monkeypatch):
    from codex_harness.core import worker_packet

    plan, repo = _checkout(tmp_path)
    (repo / "tests/test_cli.py").write_text("from .fixtures import CASES\nspec = CASES['B13']\n")
    value = "{'request': {'amount': '100.00'}, 'expected': {'minor': 10000}}"
    (repo / "tests/fixtures.py").write_text(
        "CASES = {'B01': '" + "irrelevant" * 1000 + "', 'B13': " + value + "}\n"
    )
    monkeypatch.setattr(worker_packet, "PROOF_FILE_BYTES", 350)
    monkeypatch.setattr(worker_packet, "PROOF_TOTAL_BYTES", 500)
    packet = build_packet(plan, plan.beads["B"], repo, None)
    assert value in packet
    assert "other entries omitted" in packet
    assert "irrelevant" * 50 not in packet


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_referenced_helper_definition_survives_tight_cap(tmp_path, monkeypatch, language):
    from codex_harness.core import worker_packet

    plan, repo = _checkout(tmp_path)
    manifest_path = plan.root / "sample.json"
    manifest = json.loads(manifest_path.read_text())
    if language == "python":
        proof_path = repo / "tests/test_cli.py"
        proof = "from .fixtures import wanted as fixture\n"
        helper_path = repo / "tests/fixtures.py"
        wanted = '@decorate\ndef wanted():\n    return {"fixture": "REFERENCED_FIXTURE"}\n'
        helper = "def irrelevant():\n    return '" + "x" * 3000 + "'\n\n" + wanted
    else:
        manifest["verification"] = {
            "runner": "command",
            "argv": [sys.executable, "{proof}"],
            "cwd": ".",
            "testDir": "tests",
            "proofGlob": "*.test.ts",
            "importPatterns": [r"from\s+['\"]([^'\"]+)['\"]"],
        }
        manifest["beads"]["B"]["proofModule"] = "tests/api.test.ts"
        manifest["beads"]["A"]["proofModule"] = "tests/api.test.ts"
        manifest["integration"]["proofModule"] = "tests/api.test.ts"
        proof_path = repo / "tests/api.test.ts"
        proof = "import { wanted as fixture } from './fixtures.js';\n"
        helper_path = repo / "tests/fixtures.ts"
        wanted = (
            'export function wanted():\n  object {\n  return {fixture: "REFERENCED_FIXTURE"};\n}\n'
        )
        helper = 'export const irrelevant = "' + "x" * 3000 + '";\n' + wanted
    proof_path.write_text(proof, encoding="utf-8")
    helper_path.write_text(helper, encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest))
    plan = load_plan(manifest_path)
    monkeypatch.setattr(worker_packet, "PROOF_FILE_BYTES", 360)
    monkeypatch.setattr(worker_packet, "PROOF_TOTAL_BYTES", 500)
    packet = build_packet(plan, plan.beads["B"], repo, None)
    assert wanted in packet
    assert "symbols: irrelevant" in packet
    assert "x" * 500 not in packet


def test_all_referenced_helpers_precede_rest_of_any_file():
    from codex_harness.core.worker_packet import _bounded_context

    entries = [
        (f"{name}.py", f"def wanted_{name}():\n    return 1\n" + "# padding\n" * 200)
        for name in ("a", "b")
    ]
    text = _bounded_context(entries, 400, 400, symbols={"a.py": {"wanted_a"}, "b.py": {"wanted_b"}})
    assert "def wanted_a():\n    return 1\n" in text
    assert "def wanted_b():\n    return 1\n" in text
    assert (
        sum(len(body.encode()) for body in re.findall(r"```\n(.*?)\n```", text, re.DOTALL)) <= 400
    )


def test_dotted_python_import_prioritizes_referenced_member(tmp_path):
    from codex_harness.core.runners import resolve_runner
    from codex_harness.core.worker_packet import _helper_symbols

    package = tmp_path / "tests/fixtures"
    package.mkdir(parents=True)
    (package / "data.py").write_text("def wanted(): return 42\n")
    runner = resolve_runner({"validation": ["-m", "unittest"]})
    symbols = _helper_symbols(
        [
            (
                "tests/test_proof.py",
                "import tests.fixtures.data\nvalue = tests.fixtures.data.wanted()\n",
            )
        ],
        tmp_path,
        runner,
    )
    assert symbols["tests/fixtures/data.py"] == {"wanted"}


@pytest.mark.parametrize("raw_bead", [True, False])
def test_prospective_warning_is_the_only_packet_change(tmp_path, raw_bead):
    from codex_harness.core.test_audit import PROSPECTIVE_LABEL, PROSPECTIVE_WARNING

    plan, repo = _checkout(tmp_path)
    bead = plan.beads["B"]
    if raw_bead:
        manifest = json.loads((plan.root / "sample.json").read_text())
        bead = {**manifest["beads"]["B"], "id": "B"}
    oracle = build_packet(plan, bead, repo, None)
    assert PROSPECTIVE_WARNING not in oracle
    prospective = build_packet(plan, bead, repo, None, proof_level="prospective")
    assert prospective.count(PROSPECTIVE_WARNING) == 1
    assert prospective.replace(PROSPECTIVE_WARNING + "\n", "").encode() == oracle.encode()
    # The manifest decides, never the generated PLAN.md view.
    document = repo / "PLAN.md"
    document.write_text(
        PROSPECTIVE_LABEL + "\n" + document.read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert PROSPECTIVE_WARNING not in build_packet(plan, bead, repo, None)


@pytest.mark.parametrize("package", [True, False])
@pytest.mark.parametrize("directory", ["tests_cli", "proof_tests/tests_cli", "my-tests"])
@pytest.mark.parametrize("start_option", ["-s", "--start-directory"])
def test_manifest_test_directory_commands_and_real_proof_paths(
    tmp_path, package, directory, start_option
):
    plan, repo = _checkout(tmp_path)
    test_path = repo / directory
    test_path.parent.mkdir(parents=True, exist_ok=True)
    (repo / "tests").rename(test_path)
    if package:
        for parent in (test_path, *test_path.parents):
            if parent == repo:
                break
            (parent / "__init__.py").write_text("", encoding="utf-8")
    manifest_path = plan.root / "sample.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["validation"][manifest["validation"].index("-s") + 1] = directory
    manifest["validation"][manifest["validation"].index("-s")] = start_option
    manifest_path.write_text(json.dumps(manifest))
    plan = load_plan(manifest_path)
    packet = build_packet(plan, plan.beads["B"], repo, None)
    # Packets normalize discovery to -s regardless of the manifest's spelling.
    discover = f"python -B -m unittest discover -s {directory} -p test_cli.py"
    selector = "MainR5.test_success_prints_one_line_per_person"
    command = (
        f"python -B -m unittest {'.'.join(test_path.relative_to(repo).parts)}.test_cli.{selector}"
        if all(part.isidentifier() for part in test_path.relative_to(repo).parts)
        else f"{discover} -k {selector}"
    )
    assert discover in packet and command in packet
    assert "-s tests -p" not in packet
    assert packet == build_packet(plan, plan.beads["B"], repo, None)
    assert len(packet) <= MAX_CHARACTERS
    shutil.copy2(plan.root / "reference/tipsplit/cli.py", repo / "tipsplit/cli.py")
    result = subprocess.run(
        [sys.executable, *command.split()[1:]], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "Ran 1 test" in result.stderr


def test_worker_packet_tipsplit_b_all_sections_full_spec_interfaces_and_tests(tmp_path):
    plan, repo = _checkout(tmp_path)
    memory = "Confirmed: use integer cents; acceptance receipt is verified."
    packet = build_packet(plan, plan.beads["B"], repo, memory)
    titles = [
        "Contract (v1)",
        "Goal",
        "Your files now",
        "Interfaces you may rely on",
        "How you are verified",
        "Confirmed project memory",
        "When blocked or the plan is contradictory",
        "Report",
    ]
    positions = [packet.index(f"## {index}. {title}") for index, title in enumerate(titles, 1)]
    assert positions == sorted(positions)
    spine = load_spine(plan.root / "spine.json")
    for ref in plan.beads["B"].spec_refs:
        assert spine.node(ref[0])["text"] in packet
    assert 'raise NotImplementedError("Bead B: implement R5")' in packet
    assert "def parse_amount(text: str) -> int:" in packet
    assert "def split(total_cents: int, people: int, tip_percent: int=0) -> list[int]:" in packet
    assert "grand_total =" not in packet
    assert "Never shown to workers" in packet  # Dependency module docstrings are supplied.
    assert "python -B -m unittest discover -s tests -p test_cli.py" in packet
    test_tree = ast.parse((repo / "tests/test_cli.py").read_text())
    for node in ast.walk(test_tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
            assert node.name in packet
    assert memory in packet and ".harness/blocker.json" in packet
    assert "Do not install, commit or push" in packet
    assert len(packet) <= MAX_CHARACTERS
    assert packet == build_packet(plan, plan.beads["B"], repo, memory)


@pytest.mark.parametrize("provider", ["claude", "codex", "copilot"])
def test_worker_packet_provider_tools_and_real_single_test_commands(tmp_path, provider):
    plan, repo = _checkout(tmp_path)
    packet = build_packet(plan, plan.beads["B"], repo, None, provider)
    contract = packet.split("## 2. Goal")[0]
    if provider == "claude":
        assert "Use the Bash tool for commands" in contract
        assert "(with optional `2>&1`, `| head`)" in contract
        assert "Other shell commands, `git`, PowerShell and multi-line `python -c`" in contract
        assert "will be denied; do not try them" in contract
        assert "Use Read/Edit/Write for files" in contract
    elif provider == "codex":
        assert "Run the exact test command; stay inside this worktree." in contract
        assert "Bash" not in contract and "will be denied" not in contract
    else:
        assert "Use the shell tool for commands" in contract
        assert "Allowed: `python -B -m unittest …`" in contract
        assert "Use file tools for files" in contract
        assert "Bash" not in contract
    assert "Run commands from your current working directory; do not `cd`" in packet
    assert "Pytest-style `::` selectors do not work with unittest" in packet
    assert (
        "python -B -m unittest tests.test_cli.MainR5.test_success_prints_one_line_per_person"
        in packet
    )
    assert len(packet) <= MAX_CHARACTERS
    assert packet == build_packet(plan, plan.beads["B"], repo, None, provider)

    # Execute an emitted command in the actual fixture checkout, with its implementation.
    shutil.copy2(plan.root / "reference/tipsplit/cli.py", repo / "tipsplit/cli.py")
    command = next(
        line
        for line in packet.splitlines()
        if line.startswith("python -B -m unittest tests.test_cli.MainR5.test_success_")
    )
    result = subprocess.run(
        [sys.executable, *command.split()[1:]], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "Ran 1 test" in result.stderr


def test_worker_packet_interfaces_classes_async_docstrings_without_bodies(tmp_path):
    plan, repo = _checkout(tmp_path)
    (repo / "tipsplit/money.py").write_text(
        'class Money(Base):\n    """Money interface."""\n'
        '    def __init__(self, cents: int):\n        """Construct cents."""\n'
        "        hidden_body()\n"
        '    @staticmethod\n    async def parse(text: str = "0") -> int:\n'
        '        """Parse cents."""\n        hidden_body()\n'
        "    def _private(self):\n        hidden_body()\n"
        "def _helper():\n    hidden_body()\n",
        encoding="utf-8",
    )
    packet = build_packet(plan, plan.beads["B"], repo, None)
    assert "class Money(Base):" in packet
    assert "def __init__(self, cents: int):" in packet
    assert "@staticmethod" in packet
    assert "async def parse(text: str='0') -> int:" in packet
    assert "Money interface." in packet and "Parse cents." in packet
    assert "hidden_body" not in packet and "_private" not in packet


@pytest.mark.parametrize("provider", ["claude", "codex", "copilot"])
def test_worker_packet_caps_owned_source(tmp_path, provider):
    plan, repo = _checkout(tmp_path)
    owned = repo / "tipsplit/cli.py"
    owned.write_text("# small line\n" * 300 + "# MUST_NOT_APPEAR\n", encoding="utf-8")
    packet = build_packet(plan, plan.beads["B"], repo, None, provider)
    assert "MUST_NOT_APPEAR" in packet  # Full source below the byte cap, even past 300 lines.
    owned.write_text("# " + "x" * 20_000 + "\n", encoding="utf-8")
    packet = build_packet(plan, plan.beads["B"], repo, "memory preserved", provider)
    assert len(packet) <= MAX_CHARACTERS
    assert "[truncated for context byte limit]" in packet
    assert "memory preserved" in packet and "## 8. Report" in packet
    assert "def parse_amount(text: str) -> int:" in packet


def test_worker_packet_missing_text_and_manifest_inline_text(tmp_path):
    plan, repo = _checkout(tmp_path)
    (repo / "PLAN.md").unlink()
    manifest_path = plan.root / "sample.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["beads"]["B"]["specRefs"][0]["text"] = "Exact inline decision text."
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    packet = build_packet(plan, plan.beads["B"], repo, None)
    assert "Exact inline decision text." in packet and "Text unavailable." in packet


def test_worker_packet_refuses_oversized_required_spec(tmp_path):
    plan, repo = _checkout(tmp_path)
    (repo / "PLAN.md").write_text("### R4: Huge requirement\n\n" + "x" * (MAX_CHARACTERS + 1))
    with pytest.raises(ValueError, match="contract/spec/interfaces exceed"):
        build_packet(plan, plan.beads["B"], repo, None)


def test_worker_packet_accepts_actual_manifest_text_with_custom_filename(tmp_path):
    plan, repo = _checkout(tmp_path)
    path = plan.root / "sample.json"
    manifest = json.loads(path.read_text())
    manifest["beads"]["B"]["specRefs"][0]["text"] = "Actual manifest decision."
    custom = path.with_name("custom-plan.json")
    custom.write_text(json.dumps(manifest), encoding="utf-8")
    path.unlink()
    plan = load_plan(custom)
    packet = build_packet(plan, {**manifest["beads"]["B"], "id": "B"}, repo, None)
    assert "Actual manifest decision." in packet
    # Standalone typed callers can still obtain all rendered text without a manifest.
    assert "Text unavailable." not in build_packet(plan, plan.beads["B"], repo, None)


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


def test_context_caps_multiple_proof_files_utf8_and_dependencies(tmp_path):
    from codex_harness.core.worker_packet import (
        INTERFACE_BYTES,
        OWNED_BYTES,
        PROOF_FILE_BYTES,
        PROOF_TOTAL_BYTES,
        TRUNCATION_MARKER,
        _bounded_context,
    )

    plan, repo = _checkout(tmp_path)
    manifest = json.loads((plan.root / "sample.json").read_text())
    manifest["beads"]["B"]["proofModule"] = "test_context*.py"
    proof = "# " + "\u00e9" * 10_000 + "\n"
    for index in range(3):
        (repo / f"tests/test_context{index}.py").write_text(proof, encoding="utf-8")
    raw = {**manifest["beads"]["B"], "id": "B"}
    (plan.root / "sample.json").write_text(json.dumps(manifest))
    plan = load_plan(plan.root / "sample.json")
    packet = build_packet(plan, raw, repo, None)
    assert packet == build_packet(plan, raw, repo, None)
    assert TRUNCATION_MARKER in packet and "tests/test_context0.py" in packet
    assert len(packet) <= MAX_CHARACTERS
    for total, per_file in (
        (PROOF_TOTAL_BYTES, PROOF_FILE_BYTES),
        (INTERFACE_BYTES, INTERFACE_BYTES),
        (OWNED_BYTES, OWNED_BYTES),
    ):
        text = _bounded_context([("a.py", proof), ("b.py", proof)], total, per_file)
        payloads = re.findall(r"```\n(.*?)\n```", text, re.DOTALL)
        assert sum(len(payload.encode()) for payload in payloads) <= total
        assert all(len(payload.encode()) <= per_file for payload in payloads)


def test_context_is_provider_neutral(tmp_path):
    plan, repo = _checkout(tmp_path)
    packets = [
        build_packet(plan, plan.beads["B"], repo, None, provider)
        for provider in ("codex", "claude", "copilot")
    ]
    assert len({packet.split("## 2. Goal", 1)[1] for packet in packets}) == 1


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


def test_helpers_share_existing_proof_caps(tmp_path):
    from codex_harness.core.worker_packet import PROOF_FILE_BYTES, PROOF_TOTAL_BYTES

    plan, repo = _checkout(tmp_path)
    (repo / "tests/test_cli.py").write_text("from . import a, b, c\n")
    for name in ("a", "b", "c"):
        (repo / f"tests/{name}.py").write_text("# " + "é" * 10000 + "\n", encoding="utf-8")
    packet = build_packet(plan, plan.beads["B"], repo, None)
    proof_context = packet.split("### Proof module source\n")[1].split("## 5.")[0]
    payloads = re.findall(r"```\n(.*?)\n```", proof_context, re.DOTALL)
    assert sum(len(body.encode()) for body in payloads) <= PROOF_TOTAL_BYTES
    assert all(len(body.encode()) <= PROOF_FILE_BYTES for body in payloads)
    assert "[truncated for context byte limit]" in proof_context
    assert "tests/c.py\n[omitted: context byte limit exhausted]" in proof_context
    assert len(packet) <= MAX_CHARACTERS


@pytest.mark.parametrize("suffix", [".ts", ".js"])
def test_command_dependency_exports_are_signatures_only_and_bounded(tmp_path, suffix):
    plan, repo = _checkout(tmp_path)
    manifest_path = plan.root / "sample.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["verification"] = {
        "runner": "command",
        "argv": [sys.executable, "{proof}"],
        "cwd": ".",
        "testDir": "tests",
        "proofGlob": "*.test" + suffix,
    }
    manifest["beads"]["A"]["ownedFiles"] = ["dependency" + suffix]
    for key in ("A", "B"):
        manifest["beads"][key]["proofModule"] = "tests/api.test" + suffix
    manifest["integration"]["proofModule"] = "tests/api.test" + suffix
    (repo / ("tests/api.test" + suffix)).write_text("// protected proof\n")
    source = """throw new Error("MUST_NOT_EXECUTE");
/*
export function COMMENT_MUST_NOT_APPEAR() {}
*/
export async function parse(text: string): Promise<string[]> { HIDDEN_BODY(); }
export function render(options: Options = {}): string { HIDDEN_BODY(); }
export const limit: number = 12;
export const transform: (value: string) => string = value => value;
export type Mode = 'fast' | 'slow';
export type Options = {
    nested: { flag: boolean };
    brace: '}';
};
export interface Parser extends Base {
    parse(text: string): string[];
    options: { enabled: boolean };
}
export interface NextLine
{
    enabled: boolean;
}
export class Reader {
    constructor(input: string) { HIDDEN_BODY(); }
    public read(size: number): string { HIDDEN_BODY(); }
    async flush(): Promise<void> { HIDDEN_BODY(); }
    private secret(): void { HIDDEN_BODY(); }
    protected hidden(): void { HIDDEN_BODY(); }
    #internal() { HIDDEN_BODY(); }
}
function internal() { HIDDEN_BODY(); }
"""
    dependency = repo / ("dependency" + suffix)
    dependency.write_text(source, encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest))
    plan = load_plan(plan.root)
    packet = build_packet(plan, plan.beads["B"], repo, None)
    interfaces = packet.split("## 4. Interfaces you may rely on\n")[1].split(
        "### Proof module source"
    )[0]
    for signature in (
        "export async function parse(text: string): Promise<string[]>",
        "export function render(options: Options = {}): string",
        "export const limit: number",
        "export const transform: (value: string) => string",
        "export type Mode = 'fast' | 'slow';",
        "export type Options = {\n    nested: { flag: boolean };\n    brace: '}';\n};",
        "export interface Parser extends Base {\n    parse(text: string): string[];",
        "export interface NextLine\n{\n    enabled: boolean;\n}",
        "export class Reader",
        "constructor(input: string)",
        "public read(size: number): string",
        "async flush(): Promise<void>",
    ):
        assert signature in interfaces
    assert not any(
        word in interfaces
        for word in (
            "HIDDEN_BODY",
            "MUST_NOT_EXECUTE",
            "COMMENT_MUST_NOT_APPEAR",
            "secret",
            "hidden",
            "#internal",
        )
    )
    assert packet == build_packet(plan, plan.beads["B"], repo, None)
    dependency.write_text("// no exported declarations\n")
    assert "(no exports found)" in build_packet(plan, plan.beads["B"], repo, None)
    dependency.write_text("export interface Huge {\n" + "  field: string;\n" * 2000 + "}\n")
    packet = build_packet(plan, plan.beads["B"], repo, None)
    payload = re.search(
        r"```\n(.*?)\n```", packet.split("## 4. Interfaces you may rely on\n")[1], re.DOTALL
    )[1]
    assert len(payload.encode()) <= 8000
    assert "[truncated for context byte limit]" in payload


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


def test_packet_intent_is_explicit_linked_bounded_and_digested(tmp_path):
    """Catch missing intent, unrelated scope and overflow; golden packets have no intent input."""
    plan, repo = _checkout(tmp_path)
    nodes = [
        dict(
            kind="vision",
            title="Exact vision",
            text="Keep café data local.",
            fields={
                "qualityBar": "No silent data loss.",
                "done": "I can trust my saved work.",
                "done_means": ["Recover after restart."],
            },
        ),
        dict(kind="requirement", id=plan.beads["B"].requirements[0], text="Exact linked wording."),
        dict(kind="requirement", id="UNLINKED", text="UNRELATED INTENT MUST NOT APPEAR"),
    ]
    expected = (
        "Exact vision\n\nKeep café data local.\n\nQuality:\nNo silent data loss."
        "\n\nDone:\nI can trust my saved work.\n\nDone means:\n- Recover after restart.\n\n"
        + plan.beads["B"].requirements[0]
        + ": Exact linked wording."
    )
    packet = build_packet(plan, plan.beads["B"], repo, None, intent_nodes=nodes)
    assert "## User intent\n" + expected in packet
    assert (
        "SHA-256 (intent excerpt, UTF-8): " + hashlib.sha256(expected.encode()).hexdigest()
        in packet
    )
    assert "UNRELATED INTENT MUST NOT APPEAR" not in packet
    assert (
        "Check your result against this intent; if contracts and intent conflict, "
        "stop and report the conflict instead of choosing." in packet
    )
    legacy = build_packet(plan, plan.beads["B"], repo, None)
    assert "## User intent" not in legacy
    assert build_packet(plan, plan.beads["B"], repo, None, intent_nodes=nodes[1:]) == legacy
    nodes[0]["text"] = "é" * 5000
    (repo / "tipsplit/cli.py").write_text("#" + "x" * 50_000)
    packet = build_packet(plan, plan.beads["B"], repo, None, intent_nodes=nodes)
    excerpt = ("Exact vision\n\n" + "é" * 5000)[:2000]
    assert "## User intent\n" + excerpt + "\n[intent truncated to 2000 characters]" in packet
    assert hashlib.sha256(excerpt.encode()).hexdigest() in packet
    assert len(packet) <= MAX_CHARACTERS
