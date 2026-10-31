"""Actual local native-record/job boundaries; source forms and launch spies only.

Protected: caller tuples cannot admit without the current native response and a
durable local call ceiling. Credible regression: shared jobs API skips the bridge.
Prior resume containment does not protect direct ordinary worker-start. Real
PlanStore publication and job record I/O own this boundary; no provider runs.
"""

import pytest
from tests.python.core import test_planner_replay_gaps
from tests.python.core.test_planner_resume import _published

from codex_harness.core import planner_jobs

project = test_planner_replay_gaps.project


def grant_execution(store, options, *, call_cap=2):
    """Real native-domain records with a synthetic correlated form; no human proof."""
    from codex_harness.core.planner_execution import complete_execution, prepare_execution

    request = prepare_execution(
        store, options["expected_revision"], options["workers"], timeout=5, call_cap=call_cap
    )
    result = complete_execution(
        store,
        request["request_id"],
        "accept",
        {
            "approve": True,
            "statement": "Approve",
            "conditions": "",
        },
    )
    assert result["state"] == "approved" and result["human_identity_verified"] is False
    return request


def test_copied_canonical_database_cannot_reuse_foreign_native_grant(project, monkeypatch):
    import shutil

    from codex_harness.core.plan_store import PlanStore

    options = _published(project)
    grant_execution(project, options)
    foreign = project.workspace / "foreign"
    shutil.copytree(project.workspace / "template", foreign / "template")
    with PlanStore(foreign) as copied:
        project.connection.backup(copied.connection)
        monkeypatch.setattr(
            planner_jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("Foreign grant launched")
        )
        with pytest.raises(ValueError, match="consent-required|publication-stale-or-gated"):
            planner_jobs.start_worker_job(
                copied, options["expected_revision"], options["workers"], timeout=5, call_cap=2
            )
    assert not (foreign / ".codex-harness/worker-jobs").exists()


@pytest.mark.parametrize("cap", [None, True, 0, -1])
def test_local_call_cap_requires_positive_nonboolean_original(project, cap):
    from codex_harness.core.planner_execution import prepare_execution

    options = _published(project)
    with pytest.raises(ValueError, match="call-cap"):
        prepare_execution(project, options["expected_revision"], options["workers"], call_cap=cap)
