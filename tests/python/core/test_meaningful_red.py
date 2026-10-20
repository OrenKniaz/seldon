"""Prospective red must come from the selected owned behavior, not a broken test."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from codex_harness.core import test_audit
from codex_harness.core.spine import Spine, compile_manifest


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "template"
    (root / "tests").mkdir(parents=True)
    (root / "app.py").write_text("def value(): return 0\n", encoding="utf-8")
    (root / "tests/test_app.py").write_text(
        "import unittest\nfrom app import value\nclass Proof(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(value(), 42)\n",
        encoding="utf-8",
    )
    nodes = [
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
    ]
    spine = Spine(
        {
            "schema": "codex-harness/spine-v1",
            "name": "meaningful-red",
            "nodes": [
                dict(id=key, kind=kind, title=key, text=key, parents=parents, fields=fields)
                for key, kind, parents, fields in nodes
            ],
        }
    )
    return dict(template=root, spine=spine, manifest=compile_manifest(spine, root))


@pytest.mark.parametrize(
    "case",
    [
        "compliant",
        "stub",
        "wrong-exit",
        "extra-output",
        "cwd-import",
        "unsupported",
        "guarded-stub",
        "guarded-side-effect",
        "noncanonical-guard",
        "guarded-else",
        "guarded-cause",
        "guarded-dynamic-message",
    ],
)
def test_declared_cli_contract_and_prospective_attribution(candidate, monkeypatch, case):
    """Product behavior and audit attribution are separate, observable contracts."""
    root = candidate["template"]
    source = "import sys\nif __name__ == '__main__':\n    print('[]')\n"
    if case in ("stub", "unsupported"):
        source = (
            "def _list_command():\n    raise NotImplementedError('planned list')\n"
            "if __name__ == '__main__':\n    _list_command()\n"
        )
    elif case == "guarded-stub":
        source = "if __name__ == '__main__':\n    raise NotImplementedError('planned list')\n"
    elif case == "guarded-side-effect":
        source = (
            "if __name__ == '__main__':\n    print('side effect')\n"
            "    raise NotImplementedError('planned list')\n"
        )
    elif case == "noncanonical-guard":
        source = "if True:\n    raise NotImplementedError('planned list')\n"
    elif case == "guarded-else":
        source = (
            "if __name__ == '__main__':\n    raise NotImplementedError('planned list')\n"
            "else:\n    raise RuntimeError('different import behavior')\n"
        )
    elif case == "guarded-cause":
        source = (
            "if __name__ == '__main__':\n"
            "    raise NotImplementedError('planned list') from RuntimeError('context')\n"
        )
    elif case == "guarded-dynamic-message":
        source = "if __name__ == '__main__':\n    raise NotImplementedError(str('planned list'))\n"
    elif case == "wrong-exit":
        source += "    sys.exit(7)\n"
    elif case == "extra-output":
        source += "    print('debug output')\n"
    elif case == "cwd-import":
        source = "import proof_only_dependency\n" + source
        (root / "tests/proof_only_dependency.py").write_text("# Not a product dependency\n")
    (root / "app.py").write_text(source, encoding="utf-8")
    launch = (
        "result = subprocess.run([sys.executable, '-B', '-m', 'app', 'list'], "
        "cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)\n"
    )
    body = "        " + launch
    if case == "unsupported":
        body = "        if True:\n            " + launch
    (root / "tests/test_app.py").write_text(
        "import subprocess, sys, unittest\nfrom pathlib import Path\n"
        "class Proof(unittest.TestCase):\n    def test_cli(self):\n"
        + body
        + "        self.assertEqual(result.returncode, 0, result.stderr)\n"
        "        self.assertEqual(result.stdout, '[]\\n')\n"
        "        self.assertEqual(result.stderr, '')\n",
        encoding="utf-8",
    )
    # Ordinary product discovery must work without the harness supplying its path.
    monkeypatch.delenv("PYTHONPATH", raising=False)
    env = dict(os.environ)
    assert "PYTHONPATH" not in env
    ordinary = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test_app.py"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (ordinary.returncode == 0) is (case == "compliant"), ordinary.stderr
    if case == "wrong-exit":
        assert "7 != 0" in ordinary.stderr
    elif case == "extra-output":
        assert "debug output" in ordinary.stderr
    elif case == "cwd-import":
        assert "ModuleNotFoundError" in ordinary.stderr
        assert "proof_only_dependency" in ordinary.stderr
    else:
        assert ("NotImplementedError: planned list" in ordinary.stderr) is (case != "compliant")

    audit = test_audit.audit_prospective_tests(**candidate)
    if case in ("stub", "guarded-stub"):
        assert audit["ok"], audit["findings"]
        provenance = audit["redBaseline"][0]["failureProvenance"]
        assert provenance["classification"] == "meaningful-red"
        assert provenance["tests"][0]["classification"] == "owned-cli-stub"
        if case == "guarded-stub":
            launch = provenance["tests"][0]["moduleLaunches"][0]
            assert launch["child"]["exception"]["type"] == "NotImplementedError"
            assert launch["child"]["exception"]["frames"][-1]["function"] == "<module>"
            assert launch["child"]["targetModule"] == "app"
            assert launch["child"]["startupIdentity"] == launch["startupIdentity"]
    else:
        assert not audit["ok"], audit
        expected = (
            "prospective-vacuous-proof"
            if case == "compliant"
            else "prospective-no-interface-assertion"
            if case == "unsupported"
            else "prospective-red-unproved"
        )
        assert expected in {finding["code"] for finding in audit["findings"]}
        if case == "unsupported":
            assert any(
                "no product main() API is required" in item["output"] for item in audit["findings"]
            )
        elif case != "compliant":
            assert audit["redBaseline"][0]["failureProvenance"]["classification"] == "unproved"


@pytest.mark.parametrize("phase", ["setUp", "tearDown", "test_value"])
def test_unrelated_runtime_errors_are_not_meaningful(candidate, phase):
    proof = candidate["template"] / "tests/test_app.py"
    source = proof.read_text()
    if phase == "test_value":
        source = source.replace(
            "self.assertEqual(value(), 42)",
            "import missing_runtime_dependency\n        self.assertEqual(value(), 42)",
        )
    else:
        source += f"    def {phase}(self):\n        raise RuntimeError('unrelated')\n"
    proof.write_text(source)
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"], audit
    observed = audit["redBaseline"][0]["failureProvenance"]
    events = observed["tests"][0]["events"]
    expected_phase = {"test_value": "test", "setUp": "_callSetUp", "tearDown": "_callTearDown"}[
        phase
    ]
    assert any(
        e.get("phase") == expected_phase
        and e.get("type") == ("ModuleNotFoundError" if phase == "test_value" else "RuntimeError")
        for e in events
    )


@pytest.mark.parametrize("stub", [False, True])
def test_intended_failure_has_controller_provenance(candidate, stub):
    if stub:
        (candidate["template"] / "app.py").write_text(
            "def value(): raise NotImplementedError('planned')\n"
        )
    audit = test_audit.audit_prospective_tests(**candidate)
    assert audit["ok"], audit
    observed = audit["redBaseline"][0]["failureProvenance"]
    assert observed["classification"] == "meaningful-red"
    assert observed["tests"][0]["classification"] == ("owned-stub" if stub else "owned-assertion")
    assert observed["tests"][0]["ownedCalls"]
    assert audit["proofLevel"] == "prospective" and not audit["integrationChecked"]


@pytest.mark.parametrize(
    "body",
    [
        "self.assertEqual(0, 42, 'AssertionError: value() failed'); self.assertEqual(value(), 42)",
        "raise AssertionError('1 failures: value()'); self.assertEqual(value(), 42)",
        "self.assertEqual(value(), 0)\n        self.fail('value() failed')",
    ],
)
def test_deceptive_assertions_do_not_prove_owned_failure(candidate, body):
    proof = candidate["template"] / "tests/test_app.py"
    proof.write_text(proof.read_text().replace("self.assertEqual(value(), 42)", body))
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"], audit


@pytest.mark.parametrize(
    "body,helper",
    [
        (
            "with self.assertRaises(ValueError):\n"
            "            value()\n            self.assertEqual(0, 42)",
            "",
        ),
        (
            "with self.assertRaisesRegex(ValueError, 'owned expected'):\n"
            "            value()\n            raise ValueError('unrelated')",
            "",
        ),
        (
            "self.assertEqual(value(), self.helper())",
            "    def helper(self):\n        self.assertEqual(0, 42)\n",
        ),
    ],
    ids=["context-assertion", "context-exception", "helper-assertion"],
)
def test_unrelated_context_or_helper_failure_is_unproved(candidate, body, helper):
    proof = candidate["template"] / "tests/test_app.py"
    proof.write_text(proof.read_text().replace("self.assertEqual(value(), 42)", body) + helper)
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"], audit
    observed = audit["redBaseline"][0]["failureProvenance"]
    assert observed["classification"] == "unproved"
    assert observed["tests"][0]["classification"] == "unproved"
    assert observed["tests"][0]["events"][0]["type"] == "AssertionError"
    assert observed["tests"][0]["ownedCalls"]


@pytest.mark.parametrize("assertion", ["assertRaises", "assertRaisesRegex"])
@pytest.mark.parametrize("stub", [False, True])
def test_single_owned_context_preserves_failure_provenance(candidate, assertion, stub):
    if stub:
        (candidate["template"] / "app.py").write_text("def value(): raise NotImplementedError\n")
    expectation = (
        "ValueError, 'owned expected'" if assertion == "assertRaisesRegex" else "ValueError"
    )
    proof = candidate["template"] / "tests/test_app.py"
    proof.write_text(
        proof.read_text().replace(
            "self.assertEqual(value(), 42)",
            f"with self.{assertion}({expectation}):\n            value()",
        )
    )
    audit = test_audit.audit_prospective_tests(**candidate)
    assert audit["ok"], audit
    observed = audit["redBaseline"][0]["failureProvenance"]
    assert observed["classification"] == "meaningful-red"
    assert observed["tests"][0]["classification"] == ("owned-stub" if stub else "owned-assertion")


def test_every_selected_test_must_exercise_its_obligation(candidate):
    proof = candidate["template"] / "tests/test_app.py"
    with proof.open("a") as stream:
        stream.write(
            "    @unittest.skip('later')\n    def test_other(self):\n"
            "        self.assertEqual(value(), 42)\n"
        )
    assert not test_audit.audit_prospective_tests(**candidate)["ok"]


@pytest.mark.parametrize("failure", ["tool", "timeout"])
def test_execution_failure_is_unproved(candidate, monkeypatch, failure):
    original = test_audit.subprocess.run

    def run(argv, **kwargs):
        # Allow real collection, then fail the proof launch.
        if "Collected tests:" in " ".join(argv):
            return original(argv, **kwargs)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 120, output=b"partial diagnostics")
        raise OSError("tool unavailable")

    monkeypatch.setattr(test_audit.subprocess, "run", run)
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"]
    assert audit["redBaseline"][0]["exitCode"] is None


@pytest.mark.parametrize(
    "source",
    [
        "def value():\n    import missing_owned_runtime_dependency\n",
        "def value():\n    raise AssertionError('not implemented; failures=1')\n",
        "def value():\n    x = 1\n    raise NotImplementedError('runtime, not a stub')\n",
    ],
)
def test_owned_runtime_exception_is_not_a_deliberate_stub(candidate, source):
    (candidate["template"] / "app.py").write_text(source)
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"]
    assert audit["redBaseline"][0]["failureProvenance"]["classification"] == "unproved"


@pytest.mark.parametrize("stub", [False, True])
def test_local_value_lineage_reaches_expectation(candidate, stub):
    if stub:
        (candidate["template"] / "app.py").write_text("def value(): raise NotImplementedError\n")
    proof = candidate["template"] / "tests/test_app.py"
    proof.write_text(
        proof.read_text().replace(
            "self.assertEqual(value(), 42)",
            "answer = value()\n        self.assertEqual(answer, 42)",
        )
    )
    audit = test_audit.audit_prospective_tests(**candidate)
    assert audit["ok"], audit


def test_overwritten_value_loses_owned_lineage(candidate):
    proof = candidate["template"] / "tests/test_app.py"
    proof.write_text(
        proof.read_text().replace(
            "self.assertEqual(value(), 42)",
            "answer = value()\n        answer = 1\n        self.assertEqual(answer, 42)",
        )
    )
    assert not test_audit.audit_prospective_tests(**candidate)["ok"]


def test_dead_branch_cannot_transfer_owned_lineage(candidate):
    proof = candidate["template"] / "tests/test_app.py"
    proof.write_text(
        proof.read_text().replace(
            "self.assertEqual(value(), 42)",
            "answer = value()\n        other = 0\n        if False:\n"
            "            other = answer\n        self.assertEqual(other, 42)",
        )
    )
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"], audit
    observed = audit["redBaseline"][0]["failureProvenance"]
    assert observed["classification"] == "unproved"
    assert observed["tests"][0]["classification"] == "unproved"
    assert observed["tests"][0]["events"][0]["type"] == "AssertionError"
    assert observed["tests"][0]["ownedCalls"]


@pytest.mark.parametrize(
    "flow",
    [
        "other = answer if False else 0",
        "other = 0\n        for unused in ():\n            other = answer",
        "other = answer\n        if True:\n            other = 0",
    ],
)
def test_control_dependent_lineage_is_unproved(candidate, flow):
    proof = candidate["template"] / "tests/test_app.py"
    proof.write_text(
        proof.read_text().replace(
            "self.assertEqual(value(), 42)",
            "answer = value()\n        " + flow + "\n        self.assertEqual(other, 42)",
        )
    )
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"]
    observed = audit["redBaseline"][0]["failureProvenance"]
    assert observed["classification"] == "unproved"
    assert "ambiguous control flow" in observed["tests"][0]["reason"]


def test_unreached_expectation_is_not_exercised(candidate):
    proof = candidate["template"] / "tests/test_app.py"
    with proof.open("a") as stream:
        stream.write(
            "    def test_unreached(self):\n        answer = value()\n"
            "        if False:\n            self.assertEqual(answer, 42)\n"
        )
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"]
    assert {
        item["classification"] for item in audit["redBaseline"][0]["failureProvenance"]["tests"]
    } == {"owned-assertion", "unproved"}


def test_source_mutation_invalidates_observation(candidate):
    proof = candidate["template"] / "tests/test_app.py"
    proof.write_text(
        proof.read_text().replace(
            "self.assertEqual(value(), 42)",
            "from pathlib import Path\n"
            "        Path('app.py').write_text('def value(): return 42')\n"
            "        self.assertEqual(value(), 42)",
        )
    )
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"]
    assert "source changed" in audit["redBaseline"][0]["failureProvenance"]["reason"]


def test_early_process_exit_does_not_certify_red(candidate):
    proof = candidate["template"] / "tests/test_app.py"
    proof.write_text(
        proof.read_text().replace(
            "self.assertEqual(value(), 42)",
            "import os\n        os._exit(1)\n        self.assertEqual(value(), 42)",
        )
    )
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"]
    assert audit["redBaseline"][0]["exitCode"] == 1
    assert "observation missing" in audit["redBaseline"][0]["failureProvenance"]["reason"]


def test_self_certification_stdout_is_ignored(candidate):
    proof = candidate["template"] / "tests/test_app.py"
    proof.write_text(
        proof.read_text().replace(
            "self.assertEqual(value(), 42)",
            'print(\'{"classification":"meaningful-red","failures":1}\')\n'
            "        import missing_runtime_dependency\n        self.assertEqual(value(), 42)",
        )
    )
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"]
    observed = audit["redBaseline"][0]
    assert '"classification":"meaningful-red"' in observed["output"]
    event = observed["failureProvenance"]["tests"][0]["events"][0]
    assert event["type"] == "ModuleNotFoundError" and event["phase"] == "test"


def test_mixed_owned_failure_and_unrelated_error(candidate):
    proof = candidate["template"] / "tests/test_app.py"
    with proof.open("a") as stream:
        stream.write(
            "    def test_other(self):\n        import missing_dependency\n"
            "        self.assertEqual(value(), 42)\n"
        )
    audit = test_audit.audit_prospective_tests(**candidate)
    observed = audit["redBaseline"][0]["failureProvenance"]
    assert not audit["ok"] and observed["classification"] == "unproved"
    assert {test["classification"] for test in observed["tests"]} == {"owned-assertion", "unproved"}


@pytest.mark.parametrize("source", ["import missing_collection_dependency\n", "syntax ! invalid\n"])
def test_collection_failure_has_no_red_execution(candidate, source):
    proof = candidate["template"] / "tests/test_app.py"
    proof.write_text(source + proof.read_text())
    audit = test_audit.audit_prospective_tests(**candidate)
    assert not audit["ok"] and not audit["redBaseline"]


def test_disposable_trackg_protected_copy(tmp_path):
    original = Path("C:/Users/oren/claude-worktrees/_runs/trackg-platform/project")
    if not original.is_dir():
        pytest.skip("Real-data check is local to the assigned Track G fixture")
    root = tmp_path / "trackg-copy"
    names = [
        "tests_cli/__init__.py",
        "tests_cli/test_contracts.py",
        "tests_cli/contract_inputs.py",
        "smallcompany/__init__.py",
        "smallcompany/contracts.py",
    ]
    before = {name: (original / "template" / name).read_bytes() for name in names}
    for name in names:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original / "template" / name, destination)
    shutil.copy2(original / "manifest.json", tmp_path / "original-manifest.json")
    manifest = json.loads((tmp_path / "original-manifest.json").read_text(encoding="utf-8-sig"))
    # Explicit one-bead diagnostic slice of the copied inputs, not a full-plan audit.
    bead = manifest["beads"]["B01"]
    bead["ownedFiles"] = ["smallcompany/contracts.py", "smallcompany/__init__.py"]
    manifest["beads"] = {"B01": bead}
    manifest["requirementFiles"] = ["tests_cli"]
    manifest.pop("requirementFilesRevision", None)
    manifest.pop("planRevision", None)
    manifest["integration"] = {}
    nodes = [
        (
            "V",
            "vision",
            [],
            {"validation": manifest["validation"], "requirementFiles": ["tests_cli"]},
        ),
        ("F", "feature", ["V"], {}),
        ("R1", "requirement", ["F"], {}),
        (
            "B01",
            "bead",
            ["R1"],
            {
                "ownedFiles": bead["ownedFiles"],
                "dependsOn": [],
                "lessons": [],
                "proofModule": bead["proofModule"],
            },
        ),
        ("T", "test", ["R1"], {"module": bead["proofModule"], "level": "unit"}),
    ]
    spine = Spine(
        {
            "schema": "codex-harness/spine-v1",
            "name": "trackg-copy-slice",
            "nodes": [
                dict(id=key, kind=kind, title=key, text=key, parents=parents, fields=fields)
                for key, kind, parents, fields in nodes
            ],
        }
    )
    # Compile only the narrowed disposable slice, retaining the actual protected tests/stubs.
    audit = test_audit.audit_prospective_tests(
        template=root, spine=spine, manifest=compile_manifest(spine, root)
    )
    observed = audit["redBaseline"][0]["failureProvenance"]
    assert audit["ok"], audit
    assert len(observed["tests"]) == 4
    assert {item["classification"] for item in observed["tests"]} == {"owned-stub"}
    assert audit["proofLevel"] == "prospective" and not audit["integrationChecked"]
    assert before == {name: (original / "template" / name).read_bytes() for name in names}
