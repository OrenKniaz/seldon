import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from watercli.store import DataError, add_entry, load, save, set_goal, undo_entry


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "water.json"
        self.data = {"goal_ml": 2000, "entries": []}
        self.path.write_text(json.dumps(self.data), encoding="utf-8")

    # R3: load/save schema and real persistence.
    def test_roundtrip_and_copy(self):
        self.assertEqual(load(self.path), self.data)
        changed = add_entry(self.data, "2026-10-14", 237)
        self.assertEqual(self.data["entries"], [])
        self.assertEqual(changed["entries"], [{"day": "2026-10-14", "ml": 237}])
        save(self.path, changed)
        self.assertEqual(load(self.path), changed)

    # R4: missing/corrupt files error without data loss, including bad schema.
    def test_missing_and_corrupt(self):
        self.path.unlink()
        with self.assertRaises(DataError):
            load(self.path)
        self.assertFalse(self.path.exists())
        for raw in [
            "{broken",
            "null",
            '{"goal_ml":0,"entries":[]}',
            '{"goal_ml":true,"entries":[]}',
            '{"goal_ml":2000,"entries":[{"day":"2026-02-30","ml":1}]}',
            '{"goal_ml":2000,"entries":[{"day":"2026-10-14","ml":-1}]}',
        ]:
            self.path.write_text(raw, encoding="utf-8")
            with self.subTest(raw=raw), self.assertRaises(DataError):
                load(self.path)
            self.assertEqual(self.path.read_text(encoding="utf-8"), raw)

    # R4: failed replacement leaves original bytes and no temp files.
    def test_failed_write_preserves_original(self):
        before = self.path.read_bytes()
        with patch("watercli.store.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(DataError):
                save(self.path, {"goal_ml": 3000, "entries": []})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    # R5: remove only the last entry of today even if another day follows it.
    def test_undo_today(self):
        data = {
            "goal_ml": 2000,
            "entries": [
                {"day": "2026-10-14", "ml": 100},
                {"day": "2026-10-14", "ml": 200},
                {"day": "2026-10-13", "ml": 300},
            ],
        }
        before = copy.deepcopy(data)
        changed, removed = undo_entry(data, "2026-10-14")
        self.assertEqual(removed, 200)
        self.assertEqual(changed["entries"], [data["entries"][0], data["entries"][2]])
        self.assertEqual(data, before)
        unchanged, removed = undo_entry(data, "2026-10-15")
        self.assertIsNone(removed)
        self.assertEqual(unchanged, data)

    # R6: configurable positive integer goal, current data preserved.
    def test_goal(self):
        changed = set_goal(self.data, 2500)
        self.assertEqual(changed, {"goal_ml": 2500, "entries": []})
        self.assertEqual(self.data["goal_ml"], 2000)
        save(self.path, changed)
        self.assertEqual(load(self.path)["goal_ml"], 2500)
        for value in [0, -1, True, 2.5]:
            with self.subTest(value=value), self.assertRaises(DataError):
                set_goal(self.data, value)
