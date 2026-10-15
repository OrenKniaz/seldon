"""Bead B proof: requirements R4-R5 (decisions D1, D5).

These tests replace the money core with a stub so Bead B is provable on its
own branch before Bead A is integrated.
"""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tipsplit import cli


class FormatR4(unittest.TestCase):
    def test_format_cents(self):
        for cents, text in [
            (1234, "$12.34"),
            (5, "$0.05"),
            (0, "$0.00"),
            (100000, "$1000.00"),
            (9007199254740993, "$90071992547409.93"),  # R4 / D1: exact cents.
        ]:
            with self.subTest(cents=cents):
                self.assertEqual(cli.format_cents(cents), text)

    def test_format_shares(self):
        self.assertEqual(
            cli.format_shares([3834, 3833]),
            ["Person 1: $38.34", "Person 2: $38.33"],
        )


class MainR5(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_success_prints_one_line_per_person(self):
        with (
            mock.patch.object(cli, "parse_amount", return_value=1000, create=True) as parse,
            mock.patch.object(cli, "split", return_value=[600, 500], create=True) as split,
        ):
            code, out, err = self._run(["10", "2", "--tip", "10"])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ["Person 1: $6.00", "Person 2: $5.00"])
        self.assertEqual(err, "")
        parse.assert_called_once_with("10")
        split.assert_called_once_with(1000, 2, 10)

    def test_omitted_tip_defaults_to_zero_and_preserves_total_argument(self):
        # R5: TOTAL PEOPLE is valid without the optional --tip.
        with (
            mock.patch.object(cli, "parse_amount", return_value=1000, create=True) as parse,
            mock.patch.object(cli, "split", return_value=[500, 500], create=True) as split,
        ):
            code, out, err = self._run(["10.00", "2"])
        self.assertEqual(
            (code, out.splitlines(), err), (0, ["Person 1: $5.00", "Person 2: $5.00"], "")
        )
        parse.assert_called_once_with("10.00")
        split.assert_called_once_with(1000, 2, 0)

    def test_invalid_numeric_arguments_have_one_error_line(self):
        # R5 / D5: argument conversion errors and money-core errors use the contract.
        for argv in [
            ["10", "two"],
            ["10", "2", "--tip", "bad"],
            ["10", "0"],
            ["10", "2", "--tip", "101"],
        ]:
            with (
                self.subTest(argv=argv),
                mock.patch.object(cli, "parse_amount", return_value=1000, create=True),
                mock.patch.object(
                    cli, "split", side_effect=ValueError("invalid split"), create=True
                ),
            ):
                code, out, err = self._run(argv)
                self.assertEqual((code, out), (2, ""))
                self.assertEqual(len(err.strip().splitlines()), 1)

    def test_invalid_input_prints_one_error_line_and_returns_2(self):
        with mock.patch.object(
            cli, "parse_amount", side_effect=ValueError("bad amount"), create=True
        ):
            code, out, err = self._run(["abc", "2"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_missing_arguments_return_2(self):
        code, out, err = self._run([])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())
        self.assertEqual(len(err.strip().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
