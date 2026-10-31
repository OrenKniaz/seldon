"""Track G and linkshelf planner regressions; providers are never launched."""

from tests.python.core import test_planner_replay_gaps
from tests.python.core.planning_support import source_only_vision_lock

from codex_harness.core.acceptance import derive_scenarios
from codex_harness.core.plan_quality import quality_report

project = test_planner_replay_gaps.project


def check(store, name):
    return next(item for item in quality_report(store)["checks"] if item["id"] == name)


def lock(store):
    source_only_vision_lock(store, statement="Approve")


def scenarios(store):
    lock(store)
    result = derive_scenarios(store.locked_acceptance_spine())
    for item in result:
        item.update(steps=['"{python}" -c "print(42)"'], expect={"stdout": "42"})
    store.save_acceptance_scenarios(result)
