"""Production admission regressions; controller subprocesses are source-test stubs."""

import pytest
from tests.python.core.planning_support import (
    source_only_b,
    source_only_confirmation,
)
from tests.python.core.test_planner_replay_gaps import REVIEWERS, audit, confirmed_vision
from tests.python.core.test_planner_replay_gaps import project as project_fixture

from codex_harness.core import roundtable
from codex_harness.core.plan_store import PHASES, PlanStore, PlanStoreError


@pytest.fixture(name="project")
def admission_project(tmp_path, monkeypatch):
    for store in project_fixture.__wrapped__(tmp_path, monkeypatch):
        confirmed_vision(store)
        source_only_b(store, reviewers=REVIEWERS)
        yield store


def enter_publish(store):
    while store.phase() != "publish":
        current = store.phase()
        if current in ("align", "vision-lock"):
            source_only_confirmation(store, current, statement="Approve")
        store.advance(PHASES[PHASES.index(current) + 1], by="source-test-planner")


def test_injected_pair_never_earns_real_review_or_publication_credit(project):
    """Catch injected reviews granting publication; canonical journeys use bound process returns."""

    def injected(*args):
        return 0, "[]"

    audit(project)
    result = roundtable.run_roundtable(
        project,
        reviewers=REVIEWERS,
        runner=injected,
        executable_for=lambda provider: provider,
    )
    assert not project.successful_reviews(project.manifest_revision())
    assert result["crossSubscription"] is False
    assert result["isMocked"] is True and result["didRunProvider"] is False
    assert all(r["isMocked"] and not r["didRunProvider"] for r in result["reviewers"])
    with PlanStore(project.workspace) as reopened:
        rows = reopened.review(result["roundId"])["reviewers"]
        assert all(r["isMocked"] and not r["didRunProvider"] for r in rows)
        with pytest.raises(PlanStoreError):
            enter_publish(reopened)
        assert reopened.phase() == "roundtable"
        assert reopened.status()["published_revision"] is None
