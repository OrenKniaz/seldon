import contextlib
import io
import os
import unittest
from datetime import date as real_date
from pathlib import Path
from unittest.mock import patch

from watercli import cli
from watercli.store import DataError


class CliTests(unittest.TestCase):
    def setUp(self):
        self.data = {"goal_ml": 2000, "entries": []}
        self.mocks = {}
        for name in [
            "load",
            "save",
            "parse_amount",
            "add_entry",
            "undo_entry",
            "set_goal",
            "today_report",
            "history_report",
        ]:
            self.mocks[name] = self.enterContext(patch.object(cli, name))
        self.mocks["load"].return_value = self.data
        self.mocks["parse_amount"].return_value = 237
        self.mocks["add_entry"].return_value = self.data
        self.mocks["set_goal"].return_value = self.data
        self.mocks["today_report"].return_value = "progress"
        self.mocks["history_report"].return_value = ["day1", "day2"]
        self.mocks["undo_entry"].return_value = (self.data, None)
        self.enterContext(patch.dict(os.environ, {"WATER_CLI_DATA": "custom.json"}))
        clock = self.enterContext(patch.object(cli, "date"))
        clock.today.return_value = real_date(2026, 10, 14)

    def run_cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
        return code, out.getvalue(), err.getvalue()

    # R9: command contracts, env path, local date; dependencies mocked for isolation.
    def test_commands(self):
        self.assertEqual(self.run_cli(["add", "8", "oz"]), (0, "Added 237 ml.\n", ""))
        self.mocks["parse_amount"].assert_called_with("8", "oz")
        self.mocks["add_entry"].assert_called_with(self.data, "2026-10-14", 237)
        self.mocks["load"].assert_called_with(Path("custom.json"))
        self.mocks["save"].assert_called_once_with(Path("custom.json"), self.data)
        self.assertEqual(self.run_cli(["today"]), (0, "progress\n", ""))
        self.mocks["today_report"].assert_called_with(self.data, "2026-10-14")
        self.assertEqual(self.run_cli(["history"]), (0, "day1\nday2\n", ""))
        self.mocks["history_report"].assert_called_with(self.data, "2026-10-14", 7)
        self.run_cli(["history", "3"])
        self.mocks["history_report"].assert_called_with(self.data, "2026-10-14", 3)
        self.assertEqual(self.run_cli(["goal"]), (0, "Goal: 2000 ml.\n", ""))
        self.assertEqual(self.run_cli(["goal", "2500"]), (0, "Goal: 2500 ml.\n", ""))
        self.mocks["set_goal"].assert_called_with(self.data, 2500)

    # R9/R5: empty undo writes nothing; populated undo persists the new state.
    def test_undo(self):
        self.assertEqual(self.run_cli(["undo"]), (0, "Nothing to undo today.\n", ""))
        self.mocks["save"].assert_not_called()
        self.mocks["undo_entry"].return_value = (self.data, 237)
        self.assertEqual(self.run_cli(["undo"]), (0, "Removed 237 ml.\n", ""))
        self.mocks["save"].assert_called_once()

    # R10: successful read commands are nonmutating; R9 captures one local day.
    def test_reads_never_save(self):
        for args in [["today"], ["history"], ["goal"]]:
            with self.subTest(args=args):
                self.assertEqual(self.run_cli(args)[0], 0)
                self.mocks["save"].assert_not_called()

    # R10: default relative path and explicit one-line failures, no writes.
    def test_default_path_and_errors(self):
        with patch.dict(os.environ, {}, clear=True):
            self.run_cli(["today"])
        self.mocks["load"].assert_called_with(Path("water.json"))
        for args in [[], ["unknown"], ["history", "x"], ["goal", "x"], ["add"]]:
            with self.subTest(args=args):
                code, out, err = self.run_cli(args)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("Error:"))
                self.assertEqual(len(err.splitlines()), 1)
                self.assertNotIn("Traceback", err)
        self.mocks["load"].side_effect = DataError("missing or corrupt data")
        self.assertEqual(self.run_cli(["today"]), (2, "", "Error: missing or corrupt data\n"))
        self.mocks["save"].assert_not_called()
