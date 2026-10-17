"""Observed execution regressions shared by audit and integrated validation."""

import pytest

from codex_harness.core.dispatch import _validate
from codex_harness.core.runners import validate_proof
from codex_harness.core.test_audit import _proof

PASS = (
    "import unittest\nclass Proof(unittest.TestCase):\n"
    " def test_value(self): self.assertEqual(2 + 2, 4)\n"
)


@pytest.mark.parametrize("entry", [_validate, _proof, validate_proof])
@pytest.mark.parametrize(
    "case,source,expected",
    [
        ("missing", None, False),
        ("pytest_only", "def test_value(): assert True\n", False),
        ("empty_case", "import unittest\nclass Proof(unittest.TestCase): pass\n", False),
        ("all_skip", PASS.replace(" def test_", ' @unittest.skip("later")\n def test_'), False),
        ("mixed", PASS + ' @unittest.skip("later")\n def test_skip(self): pass\n', True),
        ("pass", PASS, True),
        (
            "expected_failure",
            PASS.replace(" def test_", " @unittest.expectedFailure\n def test_").replace(
                "2 + 2, 4", "2 + 2, 5"
            ),
            False,
        ),
        (
            "mixed_expected",
            PASS + " @unittest.expectedFailure\n def test_expected(self): self.fail()\n",
            True,
        ),
        (
            "skipped_subtest",
            "import unittest\nclass Proof(unittest.TestCase):\n def test_value(self):\n"
            "  with self.subTest(): self.skipTest('later')\n",
            False,
        ),
        (
            "mixed_subtest",
            "import unittest\nclass Proof(unittest.TestCase):\n def test_value(self):\n"
            "  with self.subTest('pass'): self.assertEqual(2 + 2, 4)\n"
            "  with self.subTest('skip'): self.skipTest('later')\n",
            True,
        ),
        ("setup_skip", PASS + " def setUp(self): self.skipTest('later')\n", False),
        ("fail", PASS.replace("2 + 2, 4", "2 + 2, 5"), False),
        ("import_error", "import missing_proof_dependency\n", False),
    ],
)
def test_useful_execution(entry, case, source, expected, tmp_path):
    (tmp_path / "tests").mkdir()
    if source is not None:
        (tmp_path / "tests/test_proof.py").write_text(source)
    manifest = {"validation": ["-B", "-m", "unittest", "discover", "-s", "tests", "-p"]}
    result = entry(tmp_path, manifest, "test_proof.py")
    assert result["passed"] is expected, (case, result)


@pytest.mark.parametrize("entry", [_validate, _proof, validate_proof])
def test_unittest_k_no_match(entry, tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_proof.py").write_text(PASS)
    manifest = {"validation": ["-m", "unittest", "discover", "-s", "tests", "-k", "no_match", "-p"]}
    assert entry(tmp_path, manifest, "test_proof.py")["passed"] is False


def test_command_retains_explicit_exit_contract(tmp_path):
    import sys

    (tmp_path / "proofs").mkdir()
    (tmp_path / "proofs/pass.proof").write_text("")
    for code in (0, 1):
        manifest = {
            "verification": {
                "runner": "command",
                "argv": [sys.executable, "-c", f"raise SystemExit({code})", "{proof}"],
                "testDir": "proofs",
                "proofGlob": "*.proof",
                "pass": {"exit": 0},
            }
        }
        result = validate_proof(tmp_path, manifest, "proofs/pass.proof")
        assert result["passed"] is (code == 0)
        assert result["observations"] is None


def test_native_k_selects_only_matching_test(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_proof.py").write_text(PASS + " def test_failure(self): self.fail()\n")
    manifest = {"validation": ["-m", "unittest", "discover", "-s", "tests", "-k", "value", "-p"]}
    result = validate_proof(tmp_path, manifest, "test_proof.py")
    assert result["passed"], result
    assert result["testsRun"] == result["nonSkippedTestsRun"] == 1


def test_native_failfast_and_buffer(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_proof.py").write_text(
        "import unittest\nclass Proof(unittest.TestCase):\n"
        " def test_a(self): print('buffered-marker'); self.fail()\n"
        " def test_b(self): self.fail('must not execute')\n"
    )
    manifest = {"validation": ["-m", "unittest", "discover", "-s", "tests", "-f", "-b", "-p"]}
    result = validate_proof(tmp_path, manifest, "test_proof.py")
    assert not result["passed"]
    assert result["testsRun"] == 1
    assert "must not execute" not in result["output"]


def test_timeout_retains_partial_output(monkeypatch, tmp_path):
    import subprocess

    from codex_harness.core import runners

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            "proof", 120, output=b"partial stdout", stderr=b"partial stderr"
        )

    monkeypatch.setattr(runners.subprocess, "run", timeout)
    result = validate_proof(
        tmp_path,
        {"validation": ["-m", "unittest", "discover", "-s", "tests", "-p"]},
        "test_proof.py",
    )
    assert not result["passed"]
    assert result["exitCode"] is None
    assert "partial stdoutpartial stderr" in result["output"]
    assert "timed out after 120 seconds" in result["output"]
