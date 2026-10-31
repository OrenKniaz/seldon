"""Quality checks through real store gates, publication and graph validation."""

import shutil
from pathlib import Path

import pytest
from tests.python.core.planning_support import (
    publication_evidence,
    source_only_b,
    source_only_confirmation,
    source_only_expansion,
)

from codex_harness.core.plan_store import PHASES, open_plan_store

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit" / "spine.json"


@pytest.fixture
def store(tmp_path):
    with open_plan_store(tmp_path) as result:
        result.import_spine(FIXTURE)
        shutil.copytree(FIXTURE.parent / "template", tmp_path / "template")
        source_only_expansion(result)
        vision = result.spine().node("V1")
        vision["fields"].update(
            users=["diners"],
            first_user_moment="Split a bill",
            scope_in=["CLI"],
            scope_out=["history"],
        )
        result.upsert_node(vision)
        # Use the existing integration test, preserving one integration module.
        test = next(
            result.spine().node(key)
            for key in result.spine().ids("test")
            if result.spine().node(key)["fields"]["level"] == "e2e"
        )
        test["fields"].update(firstMoment=True, cleanState=True)
        result.upsert_node(test)
        bead = result.spine().node("A")
        bead["parents"].append("R6")
        result.upsert_node(bead)
        for question in ("Who uses it?", "First moment?", "Scope?"):
            result.answer(result.ask(question), "Confirmed by operator")
        yield result


def advance(store, target, *, reviewers=None):
    while PHASES.index(store.phase()) < PHASES.index(target):
        current = store.phase()
        if current in ("align", "vision-lock"):
            source_only_confirmation(store, current, statement="Approve")
        if current == "plan":
            source_only_b(store, reviewers=reviewers or REVIEWERS)
        if current == "roundtable":
            publication_evidence(store)
        store.advance(PHASES[PHASES.index(current) + 1], by="planner")


def publish(store, tmp_path):
    advance(store, "publish")
    publication_evidence(store)
    return store.publish(by="operator", export_dir=tmp_path / "exports")


REVIEWERS = [
    {"name": "guardian", "provider": "codex", "model": "gpt-6.1-sol"},
    {"name": "realist", "provider": "claude", "model": "claude-sonnet-5-5"},
]
FINDING = {
    "node_id": "R1",
    "severity": "major",
    "finding": "Missing verification",
    "suggestion": "Add real verification",
}
