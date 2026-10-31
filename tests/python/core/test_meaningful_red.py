"""Prospective red must come from the selected owned behavior, not a broken test."""

import pytest

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
