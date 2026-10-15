import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


class EndToEndTests(unittest.TestCase):
    # R8/R11: persisted multi-day history through the real subprocess CLI.
    def test_history_keeps_empty_calendar_days(self):
        day = date.today()
        older = (day - timedelta(days=2)).isoformat()
        empty = (day - timedelta(days=1)).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "water.json"
            path.write_text(
                json.dumps(
                    {
                        "goal_ml": 2000,
                        "entries": [
                            {"day": older, "ml": 100},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            env = {**os.environ, "WATER_CLI_DATA": str(path)}
            for args in [("add", "200"), ("history", "3")]:
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "watercli.cli", *args],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    f"{older}: 100 ml / 2000 ml (5.0%)",
                    f"{empty}: 0 ml / 2000 ml (0.0%)",
                    f"{day.isoformat()}: 200 ml / 2000 ml (10.0%)",
                ],
            )

    # R11: separate real CLI processes -> units -> storage -> report -> persistence.
    def test_daily_journey(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "water.json"
            path.write_text('{"goal_ml":2000,"entries":[]}', encoding="utf-8")
            env = {**os.environ, "WATER_CLI_DATA": str(path)}

            def run(*args, code=0):
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "watercli.cli", *args],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                return result

            self.assertEqual(run("goal").stdout, "Goal: 2000 ml.\n")
            self.assertEqual(run("add", "250").stdout, "Added 250 ml.\n")
            self.assertEqual(run("add", "8", "oz").stdout, "Added 237 ml.\n")
            day = date.today().isoformat()
            self.assertEqual(run("today").stdout, f"{day}: 487 ml / 2000 ml (24.3%)\n")
            self.assertEqual(len(run("history").stdout.splitlines()), 7)
            self.assertEqual(len(run("history", "2").stdout.splitlines()), 2)
            run("goal", "1000")
            self.assertIn("(48.7%)", run("today").stdout)
            self.assertEqual(run("undo").stdout, "Removed 237 ml.\n")
            self.assertEqual(json.loads(path.read_text())["entries"], [{"day": day, "ml": 250}])
            run("undo")
            self.assertEqual(run("undo").stdout, "Nothing to undo today.\n")
            before = path.read_bytes()
            for args in [
                ("add", "0"),
                ("add", "-1"),
                ("add", "3", "cups"),
                ("history", "0"),
                ("history", str(10**30)),
                ("goal", "-1"),
            ]:
                result = run(*args, code=2)
                self.assertTrue(result.stderr.startswith("Error:"))
                self.assertEqual(path.read_bytes(), before)
            path.write_text("{corrupt", encoding="utf-8")
            run("add", "200", code=2)
            self.assertEqual(path.read_text(), "{corrupt")
            path.unlink()
            run("today", code=2)
            self.assertFalse(path.exists())
