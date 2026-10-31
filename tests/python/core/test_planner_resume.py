"""Persisted inspection/reattachment and fail-closed new admission.

Caller declarations cannot launch, and an ambiguous original cannot be replaced.
Existing jobs tests use a
store double; these tests exercise canonical PlanStore producers and real job I/O.
Synthetic native forms/audit records/controller returns are not live user/provider
or acceptance proof. Public transport owns baseline red; missing imports do not.
"""

from datetime import UTC, datetime, timedelta

from tests.python.core import test_planner_replay_gaps
from tests.python.core.planning_support import (
    source_controller_roundtable,
)
from tests.python.core.test_planner_resolution import _at_roundtable

from codex_harness.core.planner_audits import audit_synchronously

project = test_planner_replay_gaps.project


def _resume(*args, **kwargs):
    from codex_harness.core.planner_resume import resume_plan

    return resume_plan(*args, **kwargs)


def _published(store):
    _at_roundtable(store)
    assert audit_synchronously(store)["proofCreditGranted"]
    source_controller_roundtable(
        store,
        process_stub=lambda *_: (0, "[]"),
        reviewers=[dict(name="Alpha", provider="codex")],
        executable_for=lambda provider: provider,
        max_rounds=4,
    )
    store.advance("publish", by="source-test")
    revision = store.publish(
        by="source-test",
        export_dir=store.workspace / "export",
        proof_level="prospective",
        reason="SOURCE-ONLY bookkeeping fixture, not audit execution",
    )
    workers = [dict(bead="B1", provider="codex", model="gpt-6.1-sol", effort="medium")]
    return dict(
        expected_revision=revision,
        workers=workers,
        execution_authorized=True,
        reserved_worker_calls=1,
        deadline=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        timeout=5,
        call_cap=1,
    )
