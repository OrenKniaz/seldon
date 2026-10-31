"""Revision and recovery proofs using only reference workers or local stand-ins."""

import json
import shutil
from pathlib import Path

import pytest

from codex_harness.core import dispatch
from codex_harness.core.dispatch import STATUS_MOCK_ONLY, run_plan
from codex_harness.core.mail import Mailbox
from codex_harness.core.spine import (
    requirement_files_revision,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit"


def test_dispatch_resume_recovers_worker_blocker_without_duplicate_mail(tmp_path):
    """Catch worker replay on reconnect or a lost retry; happy flows never block."""
    plan = _plan(tmp_path)
    # Omitting intent from fresh-packet comparison silently relaunches an
    # unchanged blocked worker; ordinary dispatch cannot exercise this branch.
    intent_nodes = [dict(kind="vision", title="Resume product", text="Keep my intent.", fields={})]

    def blocked(worktree):
        (worktree / ".harness").mkdir()
        (worktree / ".harness/blocker.json").write_text(
            json.dumps(
                {
                    "reason": "Requirements conflict",
                    "details": "Two rounding rules.",
                    "conflicts": [{"a": "plan", "b": "proof"}],
                    "suggestedResolution": "Clarify rounding.",
                }
            )
        )

    first = run_plan(
        plan,
        work_root=tmp_path / "runs",
        mock_provider="reference",
        fake_workers={"A": blocked},
        setup={"githubLogin": "resume-user"},
        intent_nodes=intent_nodes,
    )
    assert "GitHub must be resume-user." in first["beads"]["A"]["prompt"]
    assert "## User intent\nResume product\n\nKeep my intent." in first["beads"]["A"]["prompt"]
    mailbox = Mailbox(first["memoryWorkspace"])
    before = mailbox.inbox("planner")
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        setup={"githubLogin": "resume-user"},
        intent_nodes=intent_nodes,
        mock_provider="reference",
        fake_workers={"A": lambda path: pytest.fail("Completed blocked attempt relaunched")},
    )
    assert resumed["beads"]["A"]["status"] == "blocked: worker reported"
    assert resumed["beads"]["A"]["blocker"] == first["beads"]["A"]["blocker"]
    assert resumed["efficiency"] == first["efficiency"]
    assert mailbox.inbox("planner") == before
    assert resumed["beads"]["A"] == first["beads"]["A"]
    assert resumed["resumeSummary"]["redispatched"] == []
    assert resumed["resumeSummary"]["retained"] == [
        {"bead": "A", "reason": "worker-reported blocker retained: packet unchanged"}
    ]
    assert not (_run_dir(first) / "evidence/A/retry-1").exists()

    recovered = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        setup={"githubLogin": "resume-user"},
        intent_nodes=intent_nodes,
        mock_provider="reference",
        retry_blocked="A",
    )
    assert recovered["status"] == STATUS_MOCK_ONLY
    assert all(b["candidate"]["validated"] for b in recovered["beads"].values())
    assert recovered["beads"]["A"]["attempt"]["cwd"] != first["beads"]["A"]["attempt"]["cwd"]


@pytest.fixture(autouse=True)
def no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dispatch, "get_adapter", lambda *args: pytest.fail("real provider launch"))


def _plan(tmp_path: Path) -> Path:
    plan = tmp_path / "plan"
    shutil.copytree(FIXTURE, plan)
    manifest = json.loads((plan / "sample.json").read_text())
    manifest["planRevision"] = "a" * 64
    for bead in manifest["beads"].values():
        bead["requirementRefs"] = [
            {"id": requirement, "revision": "b" * 64} for requirement in bead["requirements"]
        ]
    manifest["integration"]["requirementRefs"] = [{"id": "R6", "revision": "b" * 64}]
    manifest["requirementFilesRevision"] = requirement_files_revision(
        plan / "template", manifest["requirementFiles"]
    )
    (plan / "sample.json").write_text(json.dumps(manifest))
    return plan


def _run_dir(record: dict) -> Path:
    return Path(record["repo"]).parent
