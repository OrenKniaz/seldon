"""Observed execution regressions shared by audit and integrated validation."""

import pytest

from codex_harness.core.runners import validate_proof

PASS = (
    "import unittest\nclass Proof(unittest.TestCase):\n"
    " def test_value(self): self.assertEqual(2 + 2, 4)\n"
)


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
def test_useful_execution(case, source, expected, tmp_path):
    """Reject empty/skipped/failed proofs; real commands catch false success unlike packet tests.

    Validate once at the runner; audit and Git integration journeys prove its consumers.
    """
    (tmp_path / "tests").mkdir()
    if source is not None:
        (tmp_path / "tests/test_proof.py").write_text(source)
    manifest = {"validation": ["-B", "-m", "unittest", "discover", "-s", "tests", "-p"]}
    result = validate_proof(tmp_path, manifest, "test_proof.py")
    assert result["passed"] is expected, (case, result)
