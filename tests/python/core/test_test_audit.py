"""Real Git snapshots and proof modules; providers are strictly stand-ins."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.python.core.planning_support import source_only_confirmation, source_only_publication

from codex_harness.core import dispatch
from codex_harness.core.dispatch import STATUS_MOCK_ONLY, _git, run_plan
from codex_harness.core.spine import Spine, compile_manifest, impact, load_spine, save_spine
from codex_harness.core.test_audit import (
    audit_prospective_tests,
    audit_revised_tests,
    render_test_audit_card,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit"


@pytest.fixture
def prospective(tmp_path):
    template = tmp_path / "template"
    tests = template / "tests"
    tests.mkdir(parents=True)
    (template / "app.py").write_text("def value(): raise NotImplementedError\n")
    (tests / "test_app.py").write_text(
        "import unittest\nfrom app import value\nclass Proof(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(value(), 42)\n"
    )
    data = load_spine(FIXTURE / "spine.json").to_dict()
    data["nodes"] = [
        dict(id=key, kind=kind, title=key, text=key, parents=parents, fields=fields)
        for key, kind, parents, fields in (
            (
                "V",
                "vision",
                [],
                {
                    "validation": ["-m", "unittest", "discover", "-s", "tests", "-p"],
                    "requirementFiles": ["tests"],
                },
            ),
            ("F", "feature", ["V"], {}),
            ("R", "requirement", ["F"], {}),
            (
                "B",
                "bead",
                ["R"],
                {
                    "ownedFiles": ["app.py"],
                    "dependsOn": [],
                    "lessons": [],
                    "proofModule": "test_app.py",
                },
            ),
            ("T", "test", ["R"], {"module": "test_app.py", "level": "unit"}),
        )
    ]
    spine = Spine(data)
    return dict(template=template, spine=spine, manifest=compile_manifest(spine, template))


def test_prospective_checks_real_stubs_without_modifying_template(prospective):
    root = prospective["template"]
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = audit_prospective_tests(**prospective)
    assert result["ok"], result["findings"]
    assert result["proofLevel"] == "prospective"
    assert not result["integrationChecked"] and result["integrationProof"] is None
    assert result["redBaseline"][0]["passed"] is False
    assert "NotImplementedError" in result["redBaseline"][0]["output"]
    assert all(item["passed"] for item in result["collection"])
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "body,code",
    [
        ("self.assertEqual(42, 42)", "prospective-vacuous-proof"),
        ("value()", "prospective-no-assertion"),
        ("self.assertEqual(", "prospective-syntax-error"),
        ("from missing import value", "prospective-not-collectable"),
    ],
)
def test_prospective_refuses_bad_proofs(prospective, body, code):
    proof = prospective["template"] / "tests/test_app.py"
    proof.write_text(proof.read_text().replace("self.assertEqual(value(), 42)", body))
    if body == "from missing import value":
        proof.write_text(proof.read_text().replace("from app import value", body))
    result = audit_prospective_tests(**prospective)
    assert result["ok"] is False
    assert code in {finding["code"] for finding in result["findings"]}


def test_prospective_requires_test_traceability(prospective):
    data = prospective["spine"].to_dict()
    data["nodes"] = [node for node in data["nodes"] if node["id"] != "T"]
    prospective["spine"] = Spine(data)
    result = audit_prospective_tests(**prospective)
    assert not result["ok"]
    assert "prospective-traceability-gap" in {f["code"] for f in result["findings"]}


def test_prospective_skipped_tests_still_need_assertions(prospective):
    proof = prospective["template"] / "tests/test_app.py"
    proof.write_text(
        proof.read_text().replace(
            "    def test_value", "    @unittest.skip('until implemented')\n    def test_value"
        )
    )
    result = audit_prospective_tests(**prospective)
    assert not result["ok"]  # All skipped is green, not a red baseline.
    assert result["findings"][0]["code"] == "prospective-vacuous-proof"
    assert result["redBaseline"][0]["failureProvenance"]["classification"] == "unproved"
    proof.write_text(proof.read_text().replace("self.assertEqual(value(), 42)", "pass"))
    result = audit_prospective_tests(**prospective)
    assert not result["ok"]
    assert "prospective-no-assertion" in {f["code"] for f in result["findings"]}
    assert result["redBaseline"][0]["failureProvenance"]["classification"] == "unproved"


def test_prospective_publication_cli_mcp_status_and_oracle_upgrade(prospective, tmp_path):
    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.plan_quality import quality_report
    from codex_harness.core.plan_store import PHASES, PlanStore, PlanStoreError
    from codex_harness.core.roundtable import run_roundtable
    from codex_harness.core.status_api import snapshot
    from codex_harness.core.test_audit import ORACLE_LABEL, PROSPECTIVE_LABEL

    source = tmp_path / "spine.json"
    save_spine(prospective["spine"], source)
    with PlanStore(tmp_path) as store:
        store.import_spine(source)
        manifest = store.compile_template(prospective["template"])
        prospective["manifest"] = manifest
        while store.phase() != "roundtable":
            phase = store.phase()
            if phase in ("align", "vision-lock"):
                source_only_confirmation(store, phase, statement="Test scope confirmed")
            store.advance(PHASES[PHASES.index(phase) + 1], by="test")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "codex_harness.core.plan_cli",
                "--workspace",
                str(tmp_path),
                "--json",
                "audit",
                "--prospective",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["data"]["proofLevel"] == "prospective"
        run_roundtable(
            store,
            reviewers=[{"name": "test-review", "provider": "codex"}],
            runner=lambda *args: (0, "[]"),
            executable_for=lambda provider: provider,
        )
        # Injected reviews remain MOCKED and cannot satisfy real publication.
        assert not store.successful_reviews(manifest["planRevision"])
        with pytest.raises(PlanStoreError, match="successful review"):
            store.advance("publish", by="test")
        # SOURCE-ONLY export/upgrade mechanics: the existing evidence double
        # still requires actual audits, mocked flags and every other gate.
        with source_only_publication():
            store.advance("publish", by="test")
            for flags in ([], ["--proof-level", "prospective"], ["--reason", "No oracle"]):
                refused_cli = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "codex_harness.core.plan_cli",
                        "--workspace",
                        str(tmp_path),
                        "--json",
                        "publish",
                        *flags,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                assert refused_cli.returncode == 2, refused_cli.stdout + refused_cli.stderr
                assert json.loads(refused_cli.stdout)["isError"]
            for kwargs, code in (
                ({}, "plan-proof-level-required"),
                ({"proof_level": "prospective"}, "plan-prospective-reason-required"),
                ({"proof_level": "prospective", "reason": " "}, "plan-prospective-reason-required"),
            ):
                with pytest.raises(PlanStoreError) as caught:
                    store.publish(by="test", export_dir=tmp_path / "plan", **kwargs)
                assert caught.value.code == code
            server = PlannerServer(tmp_path)
            try:
                refused = server.call_tool("harness_publish", {})
                assert refused["isError"]
                published = server.call_tool(
                    "harness_publish",
                    {
                        "proof_level": "prospective",
                        "reason": "A full reference implementation is impractical",
                    },
                )
                assert not published["isError"], published
            finally:
                server.close()
            revision = store.status()["published_revision"]
            frozen = store.published_manifest(revision)
            assert frozen["proofLevel"] == "prospective"
            assert frozen["proofLevelReason"] == "A full reference implementation is impractical"
            assert frozen["sourceManifestRevision"] == manifest["planRevision"]
            assert store.status()["proofLevelLabel"] == PROSPECTIVE_LABEL
            assert store.status()["publication"]["up_to_date"]
            for name in store.rendered_docs():
                assert (
                    (tmp_path / "plan" / revision / name)
                    .read_text(encoding="utf-8")
                    .startswith(PROSPECTIVE_LABEL)
                )
            projected = snapshot(workspace=tmp_path)["plan"]
            assert projected["proofLevel"] == "prospective"
            assert projected["proofLevelLabel"] == PROSPECTIVE_LABEL
            check = next(
                check for check in quality_report(store)["checks"] if check["id"] == "proof-level"
            )
            assert check["status"] == "warn" and "first real proof" in check["advice"]
            store.advance("execute", by="test")
            oracle = tmp_path / "oracle"
            oracle.mkdir()
            (oracle / "app.py").write_text("def value(): return 42\n")
            upgraded = audit_revised_tests(
                repo=tmp_path,
                base_ref="template",
                revised_template=prospective["template"],
                manifest=manifest,
                impact={"affectedBeads": ["B"]},
                oracle_dir=oracle,
            )
            assert upgraded["ok"], upgraded
            store.record_audit(manifest["planRevision"], upgraded)
            assert store.status()["proofLevelLabel"] == ORACLE_LABEL
            assert not store.status()["publication"]["up_to_date"]
            # A later prospective audit cannot silently downgrade stronger evidence.
            store.record_audit(manifest["planRevision"], audit_prospective_tests(**prospective))
            assert store.proof_level() == "oracle-verified"
            store.advance("publish", by="test")
            oracle_revision = store.publish(by="test", export_dir=tmp_path / "plan")
            assert oracle_revision == manifest["planRevision"]
            assert store.published_manifest(oracle_revision) == manifest  # Existing oracle bytes.
            assert store.published_manifest(revision) == frozen  # Earlier publication stays honest.
        assert not store.successful_reviews(manifest["planRevision"])


@pytest.mark.parametrize("missing", ["listArgv", "assertPatterns", "importPatterns"])
def test_prospective_command_runner_refuses_missing_collection_contract(prospective, missing):
    prospective["manifest"]["verification"] = {
        "runner": "command",
        "argv": [sys.executable, "{proof}"],
        "testDir": "tests",
        "proofGlob": "*.py",
        "listArgv": [sys.executable, "{proof}"],
        "assertPatterns": [r"assert\w*\("],
        "importPatterns": [r"from (\w+) import"],
    }
    del prospective["manifest"]["verification"][missing]
    prospective["manifest"]["beads"]["B"]["proofModule"] = "tests/test_app.py"
    result = audit_prospective_tests(**prospective)
    assert not result["ok"]
    finding = next(f for f in result["findings"] if f["module"] == missing)
    assert finding["code"] == "prospective-runner-contract-missing"
    assert missing in finding["output"]
    assert result["collection"] == result["redBaseline"] == []


TS_FIXTURE = Path(__file__).parent / "fixtures/ts_runner"


@pytest.fixture
def typescript_plan(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("TypeScript fixture requires node on PATH")
    template = tmp_path / "template"
    shutil.copytree(TS_FIXTURE / "template", template)
    spine = load_spine(TS_FIXTURE / "spine.json")
    return dict(template=template, spine=spine, manifest=compile_manifest(spine, template))


def test_typescript_unproved_tap_baseline_and_oracle_upgrade(typescript_plan, tmp_path):
    from codex_harness.core.plan_store import PlanStore
    from codex_harness.core.runners import resolve_runner
    from codex_harness.core.test_audit import ORACLE_LABEL

    source = tmp_path / "spine.json"
    save_spine(typescript_plan["spine"], source)
    with PlanStore(tmp_path) as store:
        store.import_spine(source)
        manifest = store.compile_template(typescript_plan["template"])
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "codex_harness.core.plan_cli",
                "--workspace",
                str(tmp_path),
                "--json",
                "audit",
                "--prospective",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 2, result.stdout + result.stderr
        audit = json.loads(result.stdout)["data"]
        assert not audit["ok"] and audit["proofLevel"] == "prospective", audit
        assert {f["code"] for f in audit["findings"]} == {"prospective-red-unproved"}
        assert len(audit["collection"]) == 2
        assert all(item["passed"] for item in audit["collection"])
        assert len(audit["redBaseline"]) == 2
        for proof in audit["redBaseline"]:
            assert proof["exitCode"] == 1 and not proof["passed"]
            assert "not implemented" in proof["output"]
            assert proof["command"] == resolve_runner(manifest).argv_for(proof["module"])
            assert proof["failureProvenance"]["classification"] == "unproved"
            assert "Command/TAP" in proof["failureProvenance"]["reason"]
        assert store.proof_level() != "prospective"
        oracle = audit_revised_tests(
            repo=tmp_path,
            base_ref="template",
            revised_template=typescript_plan["template"],
            manifest=manifest,
            impact={"affectedBeads": ["SUM", "FORMAT"]},
            oracle_dir=TS_FIXTURE / "reference",
        )
        assert oracle["ok"], oracle
        assert oracle["integrationChecked"] and oracle["integrationProof"]["passed"]
        store.record_audit(manifest["planRevision"], oracle)
        assert store.proof_level() == "oracle-verified"
        assert store.status()["proofLevelLabel"] == ORACLE_LABEL
        assert all(
            "not implemented" in (typescript_plan["template"] / name).read_text()
            for name in ("src/sum.ts", "src/format.ts")
        )


@pytest.mark.parametrize(
    "old,new,code",
    [
        ("assert.equal(sum(2, 3), 5);", "sum(2, 3);", "prospective-no-assertion"),
        (
            "import { sum } from '../src/sum.ts';",
            "import { format as sum } from '../src/format.ts';",
            "prospective-no-interface-assertion",
        ),
        (
            "import { sum } from '../src/sum.ts';",
            "const sum = () => { throw new Error('unrelated'); };",
            "prospective-no-interface-assertion",
        ),
        ("assert.equal(sum(2, 3), 5);", "assert.equal(5, 5);", "prospective-vacuous-proof"),
        ("../src/sum.ts", "../src/missing.ts", "prospective-not-collectable"),
        ("assert.equal(sum(2, 3), 5);", "assert.equal(", "prospective-not-collectable"),
    ],
)
def test_typescript_refuses_bad_proofs(typescript_plan, old, new, code):
    proof = typescript_plan["template"] / "tests/sum.test.ts"
    proof.write_text(proof.read_text().replace(old, new), encoding="utf-8")
    audit = audit_prospective_tests(**typescript_plan)
    assert not audit["ok"]
    assert code in {finding["code"] for finding in audit["findings"]}, audit


def test_typescript_requires_traceability(typescript_plan):
    data = typescript_plan["spine"].to_dict()
    data["nodes"] = [node for node in data["nodes"] if node["id"] != "T_SUM"]
    typescript_plan["spine"] = Spine(data)
    audit = audit_prospective_tests(**typescript_plan)
    assert not audit["ok"]
    assert "prospective-traceability-gap" in {f["code"] for f in audit["findings"]}


@pytest.mark.parametrize("cwd", [".", "src"])
def test_typescript_lists_all_proofs_in_one_command(typescript_plan, cwd):
    config = typescript_plan["manifest"]["verification"]
    config["cwd"] = cwd
    prefix = "../" if cwd == "src" else ""
    config["listArgv"][-1:] = [prefix + "tests/*.test.ts"]
    audit = audit_prospective_tests(**typescript_plan)
    assert not audit["ok"], audit
    assert {f["code"] for f in audit["findings"]} == {"prospective-red-unproved"}
    assert len(audit["collection"]) == 1


@pytest.mark.parametrize(
    "listing",
    [
        "tests/sum.test.ts",
        "TAP version 13\nnot ok 1 - tests/sum.test.ts\nok 2 - tests/format.test.ts",
    ],
)
def test_typescript_rejects_incomplete_or_failed_listing_with_zero_exit(typescript_plan, listing):
    typescript_plan["manifest"]["verification"]["listArgv"] = [
        sys.executable,
        "-c",
        f"print({listing!r})",
    ]
    audit = audit_prospective_tests(**typescript_plan)
    assert not audit["ok"] and not audit["redBaseline"]
    assert "prospective-not-collectable" in {f["code"] for f in audit["findings"]}


@pytest.fixture
def revision(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "get_adapter", lambda *args: pytest.fail("real provider launch"))
    plan = tmp_path / "plan"
    shutil.copytree(FIXTURE, plan)
    old = load_spine(plan / "spine.json")
    (plan / "sample.json").write_text(json.dumps(compile_manifest(old, plan / "template")))
    integrated = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    assert integrated["status"] == STATUS_MOCK_ONLY
    repo = Path(integrated["repo"])
    base = _git(repo, "rev-parse", "HEAD")
    save_spine(old, tmp_path / "old-spine.json")
    data = old.to_dict()
    next(node for node in data["nodes"] if node["id"] == "D3")["text"] = "Round tip up."
    new = Spine(data)
    save_spine(new, plan / "spine.json")
    proof = plan / "template/tests/test_money.py"
    proof.write_text(
        proof.read_text()
        .replace("+ 50", "+ 99")
        .replace("sum(split(1001, 2, 15)), 1151", "sum(split(1001, 2, 15)), 1152")
        .replace("(111, 4, 10, [31, 31, 30, 30])", "(111, 4, 10, [31, 31, 31, 30])"),
        encoding="utf-8",
    )
    oracle = plan / "reference/tipsplit/money.py"
    oracle.write_text(oracle.read_text().replace("+ 50", "+ 99"), encoding="utf-8")
    return {
        "repo": repo,
        "base_ref": base,
        "revised_template": plan / "template",
        "manifest": compile_manifest(new, plan / "template"),
        "impact": impact(old, new),
        "oracle_dir": plan / "reference",
    }


def test_audit_flags_demo_stale_half_up_row(revision):
    proof = revision["revised_template"] / "tests/test_money.py"
    proof.write_text(
        proof.read_text().replace("sum(split(1001, 2, 15)), 1152", "sum(split(1001, 2, 15)), 1151"),
        encoding="utf-8",
    )
    result = audit_revised_tests(**revision)
    assert not result["ok"]
    assert [
        (finding["code"], finding["severity"], finding["bead"]) for finding in result["findings"]
    ] == [("revised-tests-unsatisfiable", "blocker", "A")]
    assert "1152 != 1151" in result["findings"][0]["output"]
    assert [finding["bead"] for finding in result["expectedFailures"]] == ["A"]


def test_audit_correct_revision_passes_and_preserves_repo_and_plan(revision):
    repo = revision["repo"]
    before = {
        path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()
    }
    plan = revision["revised_template"].parent
    before_plan = {
        path.relative_to(plan): path.read_bytes() for path in plan.rglob("*") if path.is_file()
    }
    result = audit_revised_tests(**revision)
    assert result["ok"] and result["findings"] == []
    assert result["integrationChecked"] is True
    assert result["integrationReason"] is None
    assert result["integrationProof"]["passed"] is True
    assert result["integrationProof"]["module"] == "test_end_to_end.py"
    assert len(result["expectedFailures"]) == 1
    expected = result["expectedFailures"][0]
    assert (expected["bead"], expected["module"], expected["severity"]) == (
        "A",
        "test_money.py",
        "info",
    )
    assert "AssertionError" in expected["output"]
    assert before == {
        path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()
    }
    assert before_plan == {
        path.relative_to(plan): path.read_bytes() for path in plan.rglob("*") if path.is_file()
    }
    assert _git(repo, "rev-parse", "HEAD") == revision["base_ref"]
    assert _git(repo, "status", "--porcelain") == ""
    assert render_test_audit_card(result) == (
        "Test audit: PASS\nBaseline: "
        + revision["base_ref"]
        + "\n- info: expected-failures — A (test_money.py)\n"
    )


def test_audit_rejects_unaffected_cli_failure(revision):
    proof = revision["revised_template"] / "tests/test_cli.py"
    proof.write_text(proof.read_text().replace('"$12.34"', '"$12.35"'), encoding="utf-8")
    result = audit_revised_tests(**revision)
    assert not result["ok"]
    assert [
        (finding["code"], finding["severity"], finding["bead"]) for finding in result["findings"]
    ] == [("unexpected-failure", "major", "B")]
    assert [finding["bead"] for finding in result["expectedFailures"]] == ["A"]


def test_audit_blocks_failing_oracle_integration(revision):
    proof = revision["revised_template"] / "tests/test_end_to_end.py"
    proof.write_text(proof.read_text().replace("$38.34", "$99.99"), encoding="utf-8")
    result = audit_revised_tests(**revision)
    assert result["ok"] is False
    assert result["integrationChecked"] is True
    assert result["integrationProof"]["passed"] is False
    assert [(item["bead"], item["severity"]) for item in result["findings"]] == [
        ("integration", "blocker")
    ]
    assert "$99.99" in result["integrationProof"]["output"]


def test_audit_without_oracle_does_not_claim_satisfiability(revision):
    revision["oracle_dir"] = None
    result = audit_revised_tests(**revision)
    assert result["ok"] and not result["findings"]
    assert result["integrationChecked"] is False
    assert "No oracle" in result["integrationReason"]
    assert result["integrationProof"] is None
    assert "Integration not checked:" in render_test_audit_card(result)
    assert [finding["bead"] for finding in result["expectedFailures"]] == ["A"]


def test_audit_uses_base_ref_instead_of_dirty_working_tree(revision):
    repo = revision["repo"]
    source = repo / "tipsplit/money.py"
    source.write_text("raise RuntimeError('dirty source must not run')\n")
    result = audit_revised_tests(**revision)
    assert result["ok"] and result["expectedFailures"][0]["bead"] == "A"
    assert source.read_text() == "raise RuntimeError('dirty source must not run')\n"


def test_audit_card_deterministic_sort_and_cli_exit_codes(revision, tmp_path):
    result = {
        "ok": False,
        "findings": [
            {
                "severity": "major",
                "code": "unexpected-failure",
                "bead": "B",
                "module": "test_cli.py",
            },
            {
                "severity": "blocker",
                "code": "revised-tests-unsatisfiable",
                "bead": "A",
                "module": "test_money.py",
            },
        ],
        "expectedFailures": [],
    }
    assert render_test_audit_card(result) == render_test_audit_card(
        {**result, "findings": list(reversed(result["findings"]))}
    )
    assert render_test_audit_card({"ok": True, "findings": [], "expectedFailures": []}) == (
        "Test audit: PASS\nNo findings or newly failing proofs.\n"
    )
    plan = revision["revised_template"].parent
    command = [
        sys.executable,
        "-B",
        "-m",
        "codex_harness.core.test_audit",
        "--repo",
        str(revision["repo"]),
        "--base",
        revision["base_ref"],
        "--plan",
        str(plan),
        "--old-spine",
        str(tmp_path / "old-spine.json"),
        "--oracle",
        str(revision["oracle_dir"]),
    ]
    passed = subprocess.run(command, capture_output=True, text=True)
    assert passed.returncode == 0, passed.stderr
    assert passed.stdout.startswith("Test audit: PASS\n")
    proof = revision["revised_template"] / "tests/test_money.py"
    proof.write_text(proof.read_text().replace("1152", "1151"), encoding="utf-8")
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode == 2, failed.stderr
    assert "blocker: revised-tests-unsatisfiable" in failed.stdout
