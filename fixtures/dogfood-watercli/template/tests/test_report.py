import unittest

from watercli.report import history_report, today_report


class ReportTests(unittest.TestCase):
    # R8: reject windows before date.min without timedelta overflow.
    def test_history_calendar_limits(self):
        data = {"goal_ml": 2000, "entries": []}
        for day, days in [("0001-01-01", 2), ("2026-10-14", 10**30)]:
            with self.subTest(day=day, days=days), self.assertRaises(ValueError):
                history_report(data, day, days)

    # R7: only the requested day, zero and over-goal, one decimal uncapped.
    def test_today(self):
        data = {
            "goal_ml": 2000,
            "entries": [{"day": "2026-10-14", "ml": 500}, {"day": "2026-10-13", "ml": 1000}],
        }
        self.assertEqual(today_report(data, "2026-10-14"), "2026-10-14: 500 ml / 2000 ml (25.0%)")
        self.assertEqual(today_report(data, "2026-10-15"), "2026-10-15: 0 ml / 2000 ml (0.0%)")
        data["goal_ml"] = 400
        self.assertIn("(125.0%)", today_report(data, "2026-10-14"))

    # R8: chronological rolling calendar window, includes empty days and today.
    def test_history(self):
        data = {"goal_ml": 2000, "entries": [{"day": "2026-09-30", "ml": 250}]}
        rows = history_report(data, "2026-10-01", 2)
        self.assertEqual(
            rows, ["2026-09-30: 250 ml / 2000 ml (12.5%)", "2026-10-01: 0 ml / 2000 ml (0.0%)"]
        )
        default = history_report(data, "2026-10-01")
        self.assertEqual(len(default), 7)
        self.assertTrue(default[0].startswith("2026-09-25:"))
        for days in [0, -1]:
            with self.subTest(days=days), self.assertRaises(ValueError):
                history_report(data, "2026-10-01", days)

    # R8: calendar windows also cross the year boundary.
    def test_year_boundary(self):
        data = {"goal_ml": 2000, "entries": [{"day": "2025-12-31", "ml": 500}]}
        self.assertEqual(
            history_report(data, "2026-01-01", 2),
            [
                "2025-12-31: 500 ml / 2000 ml (25.0%)",
                "2026-01-01: 0 ml / 2000 ml (0.0%)",
            ],
        )
