"""The ten guide-only replay failures, through canonical producers and CLI consumers."""

import json

import pytest
from tests.python.core.planning_support import (
    source_controller_roundtable,
    source_only_vision_lock,
)
from tests.python.core.test_plan_cli import cli

from codex_harness.core import approvals, roundtable
from codex_harness.core.plan_quality import quality_report
from codex_harness.core.plan_store import PlanStore

REVIEWERS = [
    {"name": "codex-reviewer", "provider": "codex"},
    {"name": "claude-reviewer", "provider": "claude"},
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(roundtable, "_run", lambda *args: pytest.fail("Real reviewer forbidden"))
    template = tmp_path / "template"
    (template / "tests").mkdir(parents=True)
    (template / "answer.py").write_text("def answer(): raise NotImplementedError\n")
    for module in ("test_answer.py", "test_journey.py"):
        (template / "tests" / module).write_text(
            "import unittest\nfrom answer import answer\n"
            "class Proof(unittest.TestCase):\n"
            "    def test_result(self): self.assertEqual(answer(), 42)\n"
        )
    oracle = tmp_path / "oracle"
    oracle.mkdir()
    (oracle / "answer.py").write_text("def answer(): return 42\n")

    def node(id, kind, parents, fields):
        return dict(id=id, kind=kind, title=id, text=id, parents=parents, fields=fields)

    nodes = [
        node(
            "V1",
            "vision",
            [],
            dict(
                first_user_moment="Receive the answer",
                scope_in=["Ask", "Receive 42"],
                validation=["-m", "unittest", "discover", "-s", "tests", "-p"],
                requirementFiles=["tests"],
            ),
        ),
        node("F1", "feature", ["V1"], {}),
        node("R1", "requirement", ["F1"], {}),
        node(
            "B1",
            "bead",
            ["R1"],
            dict(
                ownedFiles=["answer.py"],
                dependsOn=[],
                proofModule="test_answer.py",
                lessons=[],
            ),
        ),
        node("T1", "test", ["R1"], dict(module="test_answer.py", level="unit")),
        node("TE", "test", ["R1"], dict(module="test_journey.py", level="e2e")),
    ]
    with PlanStore(tmp_path) as store:
        path = tmp_path / "input.json"
        path.write_text(
            json.dumps(dict(schema="codex-harness/spine-v1", name="Answer", nodes=nodes))
        )
        store.import_spine(path)
        store.compile_template(template)
        yield store


def review(store, reviewers=REVIEWERS, process_stub=None, **kwargs):
    """Controller-path source simulation for coverage/retry mechanics, not provider proof."""
    if approvals.current(store, "A") is None:
        confirmed_vision(store)
    return source_controller_roundtable(
        store,
        reviewers=reviewers,
        process_stub=process_stub or (lambda *args: (0, "[]")),
        executable_for=lambda provider: provider,
        **kwargs,
    )


def audit(store):
    return cli(store.workspace, "audit", "--oracle", "oracle", machine=True)


def check(store, name):
    return next(item for item in quality_report(store)["checks"] if item["id"] == name)


def confirmed_vision(store):
    """Complete this source fixture's card and pass real gates with a synthetic client form.

    This is neither human approval nor native-transport/provider evidence. Call it
    only for cases testing downstream behavior after the alignment/vision gates.
    """
    node = store.spine().node("V1")
    node["fields"].update(
        vision="A tiny offline command-line tool that returns 42.",
        users="One person using the local command line.",
        scope_out="Network services and graphical interfaces.",
        assumptions=[],
    )
    store.upsert_node(node)
    source_only_vision_lock(store, statement="Approve")
    store.advance("features", by="source-test")
