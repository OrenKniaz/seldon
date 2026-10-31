"""Cold-start reviews through canonical packets, findings, CLI and MCP; no providers."""

import json

from tests.python.core import test_planner_replay_gaps

from codex_harness.core import roundtable
from codex_harness.core.plan_quality import quality_report
from codex_harness.core.readiness import readiness_review

project = test_planner_replay_gaps.project
REVIEWERS = test_planner_replay_gaps.REVIEWERS
REAL_RUN = roundtable._run
READY = dict(ready=True, canVerify=True, blockers=[], questions=[])
BLOCKED = dict(
    ready=False,
    canVerify=False,
    blockers=[
        dict(
            kind="missing-interface",
            detail="Define the answer() return type.",
            nodeIds=["B1", "R1"],
        )
    ],
    questions=["What type is returned?"],
)


def review(project, answer=READY, **kwargs):
    return readiness_review(
        project,
        reviewers=REVIEWERS,
        runner=lambda *args: (0, json.dumps(answer)),
        executable_for=lambda provider: provider,
        **kwargs,
    )


def check(project):
    return next(c for c in quality_report(project)["checks"] if c["id"] == "worker-readiness")
