"""Integration proof: requirement R6 on the merged result of Beads A and B."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from tipsplit.cli import main


class EndToEndR6(unittest.TestCase):
    def test_first_user_moment(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["100.00", "3", "--tip", "15"])
        self.assertEqual(code, 0)
        self.assertEqual(err.getvalue(), "")  # R5 / D5: successful output is on stdout.
        self.assertEqual(
            out.getvalue().splitlines(),
            ["Person 1: $38.34", "Person 2: $38.33", "Person 3: $38.33"],
        )

    def test_real_parse_error_is_reported(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["12.345", "2"])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)  # R5 / D5.


if __name__ == "__main__":
    unittest.main()
