"""Stand-in-only dispatch integration tests; never launch a real provider."""

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from codex_harness.core.dispatch import (
    STATUS_MOCK_ONLY,
    STATUS_VALIDATED,
    STATUS_VALIDATED_NO_INTEGRATION,
    _bead_prompt,
    _claim_inputs,
    _commit,
    _did_execute_real_provider,
    _git,
    _validation_tool_rules,
    run_plan,
)
from codex_harness.core.graph import load_plan
from codex_harness.core.mail import Mailbox
from codex_harness.core.project_memory import ProjectMemoryStore
from codex_harness.core.spine import requirement_files_revision

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "fixtures" / "dogfood-tipsplit"
D1 = "tip-split amounts are integer cents everywhere; never floats (D1)"


def _bind_protected(plan: Path) -> None:
    path = plan / "sample.json"
    manifest = json.loads(path.read_text())
    manifest["requirementFilesRevision"] = requirement_files_revision(
        plan / "template", manifest["requirementFiles"]
    )
    path.write_text(json.dumps(manifest))


def test_tipsplit_verification_identity_matches_pre_change_bytes(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE / "template", repo)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Tests")
    _git(repo, "add", ".")
    _commit(repo, "fixture")
    identity = _claim_inputs(repo, load_plan(FIXTURE), "B", "HEAD")["verificationIdentity"]
    # Captured from the shared assignment tip before the test-directory repair.
    assert identity == "d8f96b50f4ef53413ea9c46bfca9452f1bc156942d3ad23d7df09c513ae5192e"


@pytest.mark.parametrize("change_tests", [False, True])
def test_dispatch_manifest_tests_identity_and_protected_guard(tmp_path, change_tests):
    root = _tiny_plan(tmp_path)
    (root / "template/tests").rename(root / "template/tests_cli")
    path = root / "sample.json"
    manifest = json.loads(path.read_text())
    manifest["validation"][manifest["validation"].index("-s") + 1] = "tests_cli"
    manifest["requirementFiles"] = []  # Protection must not depend on this list.
    del manifest["beads"]["B"]
    path.write_text(json.dumps(manifest))
    plan = load_plan(root)
    prompt = _bead_prompt(manifest["beads"]["A"], plan=plan)
    assert "anything under tests_cli/" in prompt
    assert "python -B -m unittest discover -s tests_cli -p test_A.py" in prompt

    def worker(worktree):
        (worktree / "A.py").write_text("VALUE = 1\n")
        if change_tests:
            with (worktree / "tests_cli/test_A.py").open("a") as test:
                test.write("# worker changed protected proof\n")

    record = run_plan(root, work_root=tmp_path / "runs", fake_workers={"A": worker})
    bead = record["beads"]["A"]
    if change_tests:
        assert bead["status"] == "rejected: requirement files changed"
        assert not bead["guard"]["requirementFilesIntact"]
        assert bead["integrated"] is None
        return
    assert record["status"] == STATUS_MOCK_ONLY
    assert bead["integrated"]["validation"]["passed"]
    repo = Path(record["repo"])
    before = _claim_inputs(repo, plan, "A", "HEAD")["verificationIdentity"]
    with (repo / "tests_cli/test_A.py").open("a") as test:
        test.write("# changed proof identity\n")
    _git(repo, "add", "tests_cli/test_A.py")
    _commit(repo, "test proof identity sensitivity")
    after = _claim_inputs(repo, plan, "A", "HEAD")["verificationIdentity"]
    assert before != after


def test_claude_validation_tool_rules_accept_bash_and_powershell_without_bypass():
    from codex_harness.core.provider_launch import (
        ProviderNormalPolicyError,
        get_adapter,
        reject_bypass,
        require_tool_rules,
    )

    rules = _validation_tool_rules("claude", {})
    assert rules == ("Bash(python -B -m unittest:*)", "PowerShell(python -B -m unittest:*)")
    assert require_tool_rules(rules) == rules
    launch = get_adapter("claude").provider_normal_command(
        prompt="Run the exact test command", allowed_tools=rules, platform="win32"
    )
    allowed = launch.command[launch.command.index("--allowedTools") + 1]
    assert all(rule in allowed.split(",") for rule in rules)
    reject_bypass(launch.command)
    with pytest.raises(ProviderNormalPolicyError):
        reject_bypass([*launch.command, "--dangerously-skip-permissions"])


def test_failure_mail_is_delivered_once_per_event_including_resume(tmp_path):
    plan = _tiny_plan(tmp_path, dependent=True)
    manifest = json.loads((plan / "sample.json").read_text())
    refs = [{"id": "RA", "kind": "requirement", "revision": "a" * 64}]
    manifest["beads"]["A"]["specRefs"] = refs
    (plan / "sample.json").write_text(json.dumps(manifest))

    def failed_worker(worktree):
        (worktree / "A.py").write_text("VALUE = 2\n")

    _bind_protected(plan)
    record = run_plan(
        plan,
        work_root=tmp_path / "runs",
        mock_provider="reference",
        fake_workers={"A": failed_worker},
    )
    mailbox = Mailbox(record["memoryWorkspace"])
    messages = mailbox.inbox("planner")
    assert len(messages) == 3  # A failed, B blocked, whole plan failed: distinct events.
    assert all(message["kind"] == "replan-trigger" for message in messages)
    a = next(message for message in messages if message["subject"] == "Replan: A")
    assert a["refs"] == ["A", "RA"]
    payload = json.loads(a["body"])
    assert payload["bead"] == "A" and payload["specRefs"] == refs
    assert payload["reason"] == "integrated validation failed"
    assert record["beads"]["A"]["integrated"]["receiptPath"] in payload["evidencePaths"]
    assert all(Path(path).exists() for path in payload["evidencePaths"])
    mailbox.ack(a["id"], "planner")

    def good_worker(bead_id):
        def edit(worktree):
            (worktree / f"{bead_id}.py").write_text("VALUE = 1\n")

        return edit

    resumed = run_plan(
        plan,
        work_root=tmp_path / "runs",
        resume_run_dir=tmp_path / "runs" / record["runId"],
        fake_workers={bead_id: good_worker(bead_id) for bead_id in ("A", "B")},
    )
    assert resumed["status"] == STATUS_MOCK_ONLY
    assert mailbox.inbox("planner", unacked_only=False) == [
        {**message, "acked_by": "planner", "acked_at": mailbox._get(a["id"])["acked_at"]}
        if message["id"] == a["id"]
        else message
        for message in messages
    ]


def test_notification_replay_and_new_attempt_identity(tmp_path):
    from codex_harness.core.dispatch import _notify_failures

    evidence = tmp_path / "evidence"
    record = {
        "runId": "run",
        "planRevision": "r1",
        "memoryWorkspace": str(tmp_path / "memory"),
        "status": "running",
        "beads": {
            "A": {
                "status": "ambiguous launch",
                "planRevision": "r1",
                "requirementRefs": [],
                "evidenceDir": str(evidence / "A"),
            },
        },
    }
    _notify_failures(record, evidence)
    _notify_failures(record, evidence)
    mailbox = Mailbox(record["memoryWorkspace"])
    assert len(mailbox.inbox("planner")) == 1
    record["beads"]["A"]["evidenceDir"] = str(evidence / "A" / "retry-1")
    _notify_failures(record, evidence)
    assert len(mailbox.inbox("planner")) == 2


@pytest.mark.parametrize("signal", ["file", "codex-message", "claude-message"])
def test_dispatch_worker_blocker_is_forwarded_once_with_spec_refs(tmp_path, signal):
    from codex_harness.core.dispatch import _changed_paths, _notify_failures

    plan = _tiny_plan(tmp_path, dependent=True)
    manifest = json.loads((plan / "sample.json").read_text())
    refs = [{"id": "RA", "kind": "requirement", "revision": "a" * 64}]
    manifest["beads"]["A"]["specRefs"] = refs
    (plan / "sample.json").write_text(json.dumps(manifest))
    report = "The plan contradicts its fixed proof. Resolve rounding.\nHARNESS-RESULT: blocked"
    blocker = {
        "reason": "Contradictory rounding",
        "details": "Plan says up; proof says down.",
        "conflicts": [{"a": "PLAN.md", "b": "tests/test_A.py"}],
        "suggestedResolution": "Update the proof to match the accepted plan.",
    }

    def blocked(worktree):
        directory = worktree.parent / "evidence/A/attempt"
        event = (
            {"type": "result", "result": report}
            if signal == "claude-message"
            else {"type": "item.completed", "item": {"type": "agent_message", "text": report}}
        )
        if signal == "file":
            event["item"]["text"] = "The rounding contract is contradictory."
            (worktree / ".harness").mkdir()
            (worktree / ".harness/blocker.json").write_text(json.dumps(blocker))
            assert _changed_paths(worktree) == []
        (directory / "events.jsonl").write_text(json.dumps(event))

    _bind_protected(plan)
    record = run_plan(
        plan, work_root=tmp_path / "runs", mock_provider="reference", fake_workers={"A": blocked}
    )
    a = record["beads"]["A"]
    assert a["status"] == "blocked: worker reported"
    assert a["workerReport"] == (
        "The rounding contract is contradictory." if signal == "file" else report
    )
    assert a["blocker"] == (blocker if signal == "file" else {"reason": report})
    assert a["guard"]["changesOutsideOwnedFiles"] == []
    assert a["guard"]["requirementFilesIntact"]
    assert a["attempt"]["changedPaths"] == []
    assert a["integrated"] is None and not a["didRunIntegratedValidation"]
    assert _git(Path(record["repo"]), "rev-parse", "HEAD") == record["baseline"]["commit"]
    evidence = Path(record["repo"]).parent / "evidence"
    _notify_failures(record, evidence)
    messages = [
        message
        for message in Mailbox(record["memoryWorkspace"]).inbox("planner")
        if message["kind"] == "blocker"
    ]
    assert len(messages) == 1
    message = messages[0]
    payload = json.loads(message["body"])
    assert payload["blocker"] == a["blocker"] and payload["workerReport"] == a["workerReport"]
    assert payload["specRefs"] == refs and message["refs"] == ["A", "RA"]
    assert (
        json.loads((evidence / "run.json").read_text())["beads"]["A"]["workerReport"]
        == a["workerReport"]
    )


def test_dispatch_no_change_stores_report_and_attempt_efficiency(tmp_path):
    plan = _tiny_plan(tmp_path, dependent=True)
    report = "Nothing implemented. " + "x" * 4500

    def unchanged(worktree):
        event = {"type": "item.completed", "item": {"type": "agent_message", "text": report}}
        directory = worktree.parent / "evidence/A/attempt"
        (directory / "events.jsonl").write_text(json.dumps(event))

    _bind_protected(plan)
    record = run_plan(
        plan, work_root=tmp_path / "runs", mock_provider="reference", fake_workers={"A": unchanged}
    )
    a = record["beads"]["A"]
    assert a["status"] == "rejected: no source change"
    assert a["workerReport"] == report[:4000]
    assert a["attempt"]["efficiency"]["commands"] == 0
    assert a["attempt"]["efficiency"]["inputTokens"] is None
    assert record["efficiency"] == a["attempt"]["efficiency"]
    assert record["efficiency"]["durationSeconds"] >= 0


def test_dispatch_block_marker_beyond_report_cap_is_observed(tmp_path):
    plan = _tiny_plan(tmp_path, dependent=True)

    def unchanged(worktree):
        report = "x" * 4500 + "\nHARNESS-RESULT: blocked"
        event = {"type": "item.completed", "item": {"type": "agent_message", "text": report}}
        (worktree.parent / "evidence/A/attempt/events.jsonl").write_text(json.dumps(event))

    _bind_protected(plan)
    record = run_plan(
        plan, work_root=tmp_path / "runs", mock_provider="reference", fake_workers={"A": unchanged}
    )
    assert record["beads"]["A"]["status"] == "blocked: worker reported"
    assert len(record["beads"]["A"]["workerReport"]) == 4000


def test_whole_plan_integration_failure_mail(tmp_path):
    from codex_harness.core.dispatch import _notify_failures

    record = {
        "runId": "run",
        "planRevision": "r1",
        "memoryWorkspace": str(tmp_path / "memory"),
        "status": "plan-failed: integration proof failed",
        "beads": {},
        "integrationValidation": {"integrationCommit": "candidate"},
    }
    _notify_failures(record, tmp_path / "evidence")
    _notify_failures(record, tmp_path / "evidence")
    messages = Mailbox(record["memoryWorkspace"]).inbox("planner")
    assert len(messages) == 1 and messages[0]["kind"] == "replan-trigger"
    assert json.loads(messages[0]["body"])["integrationCommit"] == "candidate"


@pytest.fixture(autouse=True)
def no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "codex_harness.core.dispatch.get_adapter",
        lambda *args: pytest.fail("tests must never launch a provider"),
    )


def test_dispatch_tipsplit_promotes_confirmed_lesson_before_b(tmp_path: Path) -> None:
    assert load_plan(FIXTURE).beads["A"].provider == "codex"
    record = run_plan(FIXTURE, work_root=tmp_path, mock_provider="reference")
    a, b = record["beads"]["A"], record["beads"]["B"]
    assert record["status"] == STATUS_MOCK_ONLY
    assert record["isMocked"] is True
    assert record["isLiveProductProof"] is False
    assert record["didExecuteRealProvider"] is False
    assert record["liveAcceptance"] == {
        "claimed": False,
        "reason": "local dispatcher run; no served or live acceptance step",
    }
    for entry in record["requirementValidation"]:
        assert entry["passed"] is True
        if entry["bead"] == "integration":
            validation = record["integrationValidation"]
            assert entry["requirements"] == ["R6"]
            assert entry["candidateCommit"] == validation["integrationCommit"]
        else:
            integrated = record["beads"][entry["bead"]]["integrated"]
            validation = integrated["validation"]
            assert entry["candidateCommit"] == integrated["integrationCommit"]
        assert entry["proofModule"] == validation["module"]
        assert entry["command"] == validation["command"]
    assert a["provider"] == "codex" and b["provider"] == "claude"
    assert "Use the Bash tool for commands" in b["prompt"]
    assert "Use the Bash tool for commands" not in a["prompt"]
    assert "Run the exact test command; stay inside this worktree." in a["prompt"]
    assert D1 not in a["prompt"] and D1 in b["prompt"]
    assert "def parse_amount(text: str) -> int:" in b["prompt"]
    assert (
        "def split(total_cents: int, people: int, tip_percent: int=0) -> list[int]:" in b["prompt"]
    )
    assert "MainR5.test_success_prints_one_line_per_person" in b["prompt"]
    assert (
        "python -B -m unittest tests.test_cli.MainR5.test_success_prints_one_line_per_person"
        in b["prompt"]
    )
    assert "grand_total =" not in b["prompt"]
    assert all("efficiency" in bead["attempt"] for bead in (a, b))
    assert record["efficiency"]["durationSeconds"] == sum(
        bead["attempt"]["efficiency"]["durationSeconds"] for bead in (a, b)
    )
    assert a["integrated"]["validation"]["passed"]
    assert b["integrated"]["validation"]["passed"]
    assert b["attempt"]["baseCommit"] == a["integrated"]["integrationCommit"]
    promoted = a["lessonsPromoted"][0]
    assert promoted["decisionId"] == "D1" and promoted["confirmed"]
    memory = Path(record["memoryWorkspace"])
    store = ProjectMemoryStore(memory)
    original = store.get(promoted["entryId"])
    assert original.provenance.author == "worker:A:codex"
    assert original.provenance.commit == a["integrated"]["integrationCommit"]
    confirmed = store.confirmed_slice()[0]
    assert confirmed.supersedes == original.id
    assert confirmed.provenance.author == "harness-integrator"
    receipt = Path(a["integrated"]["receiptPath"])
    assert json.loads(receipt.read_text())["validation"]["passed"]
    assert str(receipt) in confirmed.text
    assert record["integrationValidation"]["passed"]
    run_dir = tmp_path / record["runId"]
    saved = json.loads((run_dir / "evidence" / "run.json").read_text())
    assert saved == record
    assert not any(bead["didRunProvider"] for bead in record["beads"].values())
    for directory in (Path(record["repo"]), Path(record["baseline"]["root"])):
        assert not (directory / "reference").exists()
        assert not (directory / ".codex-harness").exists()
        assert _git(directory, "status", "--porcelain") == ""
    assert record["baseline"]["requirementFiles"] == {
        name: _git(Path(record["repo"]), "rev-parse", f"HEAD:{name}")
        for name in ("VISION.md", "PLAN.md", "tests")
    }


def test_dispatch_failed_validation_blocks_dependents(tmp_path: Path) -> None:
    def float_split(worktree: Path) -> None:
        reference = (FIXTURE / "reference" / "tipsplit" / "money.py").read_text()
        (worktree / "tipsplit" / "money.py").write_text(
            reference.replace(
                "return [base + 1 if index < extra else base for index in range(people)]",
                "return [grand_total / people] * people",
            )
        )

    record = run_plan(
        FIXTURE,
        work_root=tmp_path,
        fake_workers={"A": float_split, "B": lambda path: pytest.fail("B must not launch")},
    )
    assert record["beads"]["A"]["status"] == "integrated validation failed"
    assert record["beads"]["A"]["guard"]["passed"]
    assert not record["beads"]["A"]["lessonsPromoted"]
    assert record["beads"]["B"]["status"] == "blocked: dependency A failed"
    assert record["beads"]["B"]["attempt"] is None
    assert record["status"] == "plan-failed: A integrated validation failed"
    repo = Path(record["repo"])
    candidate = record["beads"]["A"]["candidate"]
    assert _git(repo, "rev-parse", "integration") == record["baseline"]["commit"]
    assert _git(repo, "rev-parse", candidate["ref"]) == candidate["commit"]
    assert _git(repo, "rev-parse", f"{candidate['commit']}^{{tree}}") == candidate["tree"]
    assert candidate["validated"] is False
    assert record["requirementValidation"][0]["candidateCommit"] == candidate["commit"]
    assert _git(repo, "rev-list", "--merges", "integration") == ""


def _tiny_plan(tmp_path: Path, *, dependent: bool = False) -> Path:
    plan = tmp_path / "plan"
    template = plan / "template"
    (template / "tests").mkdir(parents=True)
    for bead_id in ("A", "B"):
        (template / f"{bead_id}.py").write_text("VALUE = 0\n")
        (template / "tests" / f"test_{bead_id}.py").write_text(
            f"import unittest\nfrom {bead_id} import VALUE\n"
            f"class Proof(unittest.TestCase):\n"
            f"    def test_value(self):\n        self.assertEqual(VALUE, 1)\n"
        )
    manifest = {
        "sample": "parallel-plan",
        "validation": ["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
        "requirementFiles": ["tests"],
        "beads": {
            bead_id: {
                "title": f"Bead {bead_id}",
                "ownedFiles": [f"{bead_id}.py"],
                "requirements": [f"R{bead_id}"],
                "proofModule": f"test_{bead_id}.py",
            }
            for bead_id in ("A", "B")
        },
    }
    if dependent:
        manifest["beads"]["B"]["dependsOn"] = ["A"]
    (plan / "sample.json").write_text(json.dumps(manifest))
    return plan


@pytest.mark.parametrize("worker_committed", [False, True])
def test_dispatch_stages_sparse_owned_files_and_deletions(tmp_path, worker_committed):
    """Integrate actual edits when owned scope includes absent and deleted paths.

    Catches Contracts staging a not-yet-created Build file (Git128) and restaging
    committed deletions; existing dispatch fixtures create every owned file.
    """
    plan = _tiny_plan(tmp_path)
    manifest_path = plan / "sample.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["beads"]["B"]
    manifest["beads"]["A"]["ownedFiles"] += ["later.css", "obsolete.css"]
    manifest_path.write_text(json.dumps(manifest))
    (plan / "template/obsolete.css").write_text("obsolete")

    def worker(root):
        (root / "A.py").write_text("VALUE = 1\n")
        (root / "obsolete.css").unlink()
        if worker_committed:
            _git(root, "add", "-A")
            _commit(root, "worker edit and deletion")

    record = run_plan(plan, work_root=tmp_path / "runs", fake_workers={"A": worker})
    assert record["status"] == STATUS_MOCK_ONLY, record
    repo = Path(record["repo"])
    assert _git(repo, "show", "HEAD:A.py") == "VALUE = 1"
    assert not (repo / "obsolete.css").exists() and not (repo / "later.css").exists()
    assert record["beads"]["A"]["integrated"]["validation"]["passed"]


def test_dispatch_git_trust_is_exact_and_command_local(tmp_path, monkeypatch):
    """Run real Git under its different-owner simulation without persisting trust.

    Catches a missing per-command exemption or global trust write; ordinary
    dispatch tests create repositories owned by their own process principal.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    config = (repo / ".git/config").read_bytes()
    global_config = tmp_path / "global-config-must-not-be-created"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    rejected = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True
    )
    assert rejected.returncode == 128 and "dubious ownership" in rejected.stderr
    assert _git(repo, "status", "--porcelain") == ""
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    rejected_other = subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(other), "status"],
        capture_output=True,
        text=True,
    )
    assert rejected_other.returncode == 128 and "dubious ownership" in rejected_other.stderr
    assert not global_config.exists() and (repo / ".git/config").read_bytes() == config


@pytest.mark.parametrize("text", [False, True])
def test_dispatch_git_failure_preserves_bounded_stderr(tmp_path, text):
    """Keep real binary/text Git diagnostics bounded in failure evidence and errors.

    Catches CalledProcessError dropping stderr and binary decoding failures;
    successful Git fixtures never exercise the error-reporting boundary.
    """
    from codex_harness.core.dispatch import _git_run

    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "evidence").mkdir()
    _git(repo, "init", "-q")
    bad_object = "missing-object-" + "x" * 5000
    with pytest.raises(subprocess.CalledProcessError) as caught:
        _git_run(repo, "cat-file", "-p", bad_object, text=text)
    failure = json.loads((tmp_path / "evidence/git-failures.jsonl").read_text())
    assert failure["returncode"] == 128
    assert failure["stderr"] == caught.value.stderr and len(failure["stderr"]) == 4096
    assert "fatal:" in failure["stderr"] and failure["stderr"] in str(caught.value)


def test_dispatch_git_staging_failure_reaches_terminal_evidence(tmp_path):
    """Expose a real Git index-lock failure through dispatch and its saved terminal run.

    Catches the UX2 opaque Git128 status; helper-only diagnostics do not prove
    the worker→integrator→run-record failure path preserves the original cause.
    """
    plan = _tiny_plan(tmp_path, dependent=True)
    _bind_protected(plan)
    locks = []

    def worker(root):
        (root / "A.py").write_text("VALUE = 1\n")
        lock = Path(_git(root, "rev-parse", "--git-path", "index.lock"))
        lock.write_text("held by another Git operation")
        locks.append(lock)

    try:
        record = run_plan(
            plan,
            work_root=tmp_path / "runs",
            fake_workers={"A": worker, "B": lambda _: pytest.fail("Blocked B must never run")},
        )
    finally:
        for lock in locks:
            lock.unlink()
    assert "stderr: fatal:" in record["status"] and "index.lock" in record["status"]
    assert record["beads"]["B"]["status"] == "blocked: dependency A failed"
    evidence = Path(record["repo"]).parent / "evidence"
    saved = json.loads((evidence / "run.json").read_text())
    failure = json.loads((evidence / "git-failures.jsonl").read_text())
    assert saved["status"] == record["status"] and failure["stderr"] in record["status"]
    assert failure["command"][-3:] == ["add", "--", "A.py"]


def _tiny_audited_plan(tmp_path: Path) -> Path:
    """Give canonical source fixtures an actual owned-call red expectation.

    Direct constant assertions remain sufficient for ordinary dispatch tests,
    but prospective publication needs observed owned-function failure lineage.
    Workers still implement the same VALUE=1 contract; accessors are not mocks.
    """
    plan = _tiny_plan(tmp_path)
    manifest = json.loads((plan / "sample.json").read_text())
    for bead_id in ("A", "B"):
        module = f"value_{bead_id}"
        (plan / "template" / f"{module}.py").write_text(
            f"from {bead_id} import VALUE\n\ndef value() -> int:\n    return VALUE\n"
        )
        manifest["beads"][bead_id]["ownedFiles"].append(f"{module}.py")
        (plan / "template/tests" / f"test_{bead_id}.py").write_text(
            f"import unittest\nfrom {module} import value\n"
            "class Proof(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(value(), 1)\n"
        )
    (plan / "sample.json").write_text(json.dumps(manifest))
    return plan


@pytest.fixture
def lesson_dispatch(tmp_path, monkeypatch):
    """Actual canonical publication/attempt/child/proof/memory; no provider or proof double."""
    from tests.python.core import execution_support
    from tests.python.core.test_dispatch_cancel import source_only_packet_renderer

    from codex_harness.core import dispatch
    from codex_harness.core.provider_launch import ProviderNormalLaunch

    def run(mode="ok"):
        plan = _tiny_audited_plan(tmp_path)
        lesson = ("Retain the full agreed decision and its validated evidence. " * 40)[:1854]

        def node(id, kind, parents, fields=None, text=None):
            return dict(
                id=id,
                kind=kind,
                title=f"Bead {id}",
                text=text or id,
                parents=parents,
                fields=fields or {},
            )

        nodes = [
            node(
                "V1",
                "vision",
                [],
                dict(
                    validation=["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
                    requirementFiles=["tests"],
                ),
            ),
            node("F", "feature", ["V1"]),
            node("RA", "requirement", ["F"]),
            node("RB", "requirement", ["F"]),
            node("D0", "decision", ["V1"], text=lesson),
        ]
        for bead_id in ("A", "B"):
            nodes += [
                node(
                    bead_id,
                    "bead",
                    ["R" + bead_id],
                    dict(
                        ownedFiles=[bead_id + ".py", "value_" + bead_id + ".py"],
                        dependsOn=["A"] if bead_id == "B" else [],
                        proofModule="test_" + bead_id + ".py",
                        lessons=["D0"] if bead_id == "A" else [],
                    ),
                ),
                node(
                    "T" + bead_id,
                    "test",
                    ["R" + bead_id],
                    dict(
                        module="test_" + bead_id + ".py",
                        level="unit",
                    ),
                ),
            ]
        source = plan / "source-spine.json"
        source.write_text(json.dumps(dict(schema="codex-harness/spine-v1", name="m", nodes=nodes)))
        publish = execution_support.publish_source_store
        # Select genuine typed input for the existing producer; no admission guard is patched.
        monkeypatch.setattr(
            execution_support,
            "publish_source_store",
            lambda workspace, ignored: publish(workspace, spine_file=source),
        )
        context = execution_support.source_execution_context(tmp_path / "canonical", plan)

        class FiniteAdapter:
            def provider_normal_command(self, *, prompt, **kwargs):
                action = (
                    "Path('A.py').write_text('VALUE = 1\\n')"
                    if "Bead A" in prompt
                    else "Path('.harness').mkdir(exist_ok=True); "
                    "Path('.harness/blocker.json').write_text("
                    '\'{"reason":"source fixture B blocked"}\')'
                )
                script = (
                    "from pathlib import Path\n" + action + "\n"
                    'print(\'{"type":"turn.completed","isMocked":true}\')\n'
                    "# SOURCE-ONLY local process fixture\n"
                )
                return ProviderNormalLaunch(
                    cli="codex",
                    command=(sys.executable, "-c", script),
                    protections=("finite source-only child",),
                )

        monkeypatch.setattr(dispatch, "get_adapter", lambda name: FiniteAdapter())
        source_only_packet_renderer(monkeypatch)
        execution_support.source_process_observations(monkeypatch)
        options = dict(context["kwargs"])
        if mode == "unavailable":

            def unavailable(*args, **kwargs):
                raise PermissionError("controlled advisory memory write unavailable")

            monkeypatch.setattr(ProjectMemoryStore, "add", unavailable)
        if mode == "self-confirmation":
            options["confirmer"] = "worker:A:codex"
        if mode == "receipt-failure":
            write = dispatch._write_record

            def unavailable_receipt(path, value):
                if Path(path).name == "integrated.json":
                    raise OSError("controlled canonical receipt write failure")
                return write(path, value)

            monkeypatch.setattr(dispatch, "_write_record", unavailable_receipt)
        record = run_plan(context["plan"], work_root=context["runs"], **options)
        if mode == "reused":
            record = run_plan(
                context["plan"],
                work_root=context["runs"],
                resume_run_dir=context["runs"] / record["runId"],
                **options,
            )
            assert record["beads"]["A"]["status"] == "reused"
        return dict(
            context=context, record=record, lesson=lesson, workspace=context["directory"].parents[2]
        )

    return run


@pytest.mark.parametrize("mode", ["ok", "unavailable"])
def test_dispatch_lesson_summary_and_advisory_failure_preserve_validated_bead(
    lesson_dispatch, mode
):
    """Catch post-proof memory failing a bead; short-lesson tests miss this boundary."""
    case = lesson_dispatch(mode)
    record = case["record"]
    bead = record["beads"]["A"]
    assert bead["status"] == "requirement-validated"
    assert bead["candidate"]["validated"] and bead["integrated"]["validation"]["passed"]
    assert bead["didRunProvider"] and bead["didRunIntegratedValidation"]
    assert not record["didExecuteRealProvider"] and not record["isLiveProductProof"]
    assert len(bead["workerReport"]) < 2000
    promoted = bead["lessonsPromoted"][0]
    assert promoted["text"] == case["lesson"]
    assert record["beads"]["B"]["status"] == "blocked: worker reported"
    saved = json.loads((Path(record["repo"]).parent / "evidence/run.json").read_text())
    assert saved == record
    if mode == "ok":
        entry = ProjectMemoryStore(record["memoryWorkspace"]).get(promoted["entryId"])
        assert promoted["confirmed"] and len(entry.text) <= 2000
        assert f"{case['context']['plan'] / 'sample.json'}#/beads/A/lessons/0/text" in entry.text
        assert bead["integrated"]["receiptPath"] in entry.text
    else:
        assert promoted["entryId"] is None and not promoted["confirmed"]
        assert promoted["error"]["code"] == "advisory-memory-unavailable"


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("self-confirmation", "cannot confirm their own lesson"),
        ("receipt-failure", "canonical receipt write failure"),
    ],
)
def test_memory_advisory_catch_excludes_auth_and_canonical_receipts(lesson_dispatch, mode, reason):
    """Catch an overbroad memory catch certifying auth/receipt failures; I/O failure is weaker."""
    case = lesson_dispatch(mode)
    assert "bead failed" in case["record"]["beads"]["A"]["status"]
    assert reason in case["record"]["beads"]["A"]["status"]
    assert case["record"]["beads"]["B"]["status"] == "blocked: dependency A failed"


def test_dispatch_attempts_overlap_and_integrate_without_ownership_conflict(tmp_path: Path) -> None:
    plan = _tiny_plan(tmp_path)
    barrier = threading.Barrier(2, timeout=10)
    arrivals = []
    # Real packet consumption catches a dropped argument at dispatch; the
    # canonical store producer is covered in the owning worker-job test.
    intent_nodes = [
        dict(kind="vision", title="Parallel product", text="Both parts work.", fields={})
    ]

    def worker(bead_id: str):
        def edit(worktree: Path) -> None:
            arrivals.append(bead_id)
            barrier.wait()
            (worktree / f"{bead_id}.py").write_text("VALUE = 1\n")

        return edit

    record = run_plan(
        plan,
        work_root=tmp_path / "runs",
        max_parallel=2,
        setup={"githubLogin": "dispatch-user"},
        intent_nodes=intent_nodes,
        fake_workers={bead_id: worker(bead_id) for bead_id in ("A", "B")},
    )
    assert all(
        "GitHub must be dispatch-user." in bead["prompt"] for bead in record["beads"].values()
    )
    assert sorted(arrivals) == ["A", "B"]
    assert all(
        "## User intent\nParallel product\n\nBoth parts work." in b["prompt"]
        for b in record["beads"].values()
    )
    assert record["status"] == STATUS_MOCK_ONLY
    assert all(bead["integrated"]["validation"]["passed"] for bead in record["beads"].values())
    assert record["beads"]["A"]["attempt"]["baseCommit"] == record["baseline"]["commit"]
    assert record["beads"]["B"]["attempt"]["baseCommit"] == record["baseline"]["commit"]
    repo = Path(record["repo"])
    assert all((repo / f"{bead_id}.py").read_text() == "VALUE = 1\n" for bead_id in ("A", "B"))
    assert len(_git(repo, "rev-list", "--merges", "HEAD").splitlines()) == 2


def test_dispatch_proves_clean_candidate_before_fast_forward(tmp_path, monkeypatch):
    from codex_harness.core import dispatch

    plan = _tiny_plan(tmp_path, dependent=True)
    validate = dispatch._validate
    observed = []

    def observe(cwd, manifest, module, **kwargs):
        if _git(cwd, "branch", "--show-current").startswith("candidate/"):
            commit = _git(cwd, "rev-parse", "HEAD")
            assert _git(cwd, "status", "--porcelain") == ""
            assert _git(cwd, "branch", "--show-current").startswith("candidate/")
            assert _git(cwd, "rev-parse", "integration") == _git(cwd, "rev-parse", "HEAD^1")
            assert commit != _git(cwd, "rev-parse", "integration")
            observed.append(commit)
        return validate(cwd, manifest, module, **kwargs)

    monkeypatch.setattr(dispatch, "_validate", observe)
    record = run_plan(
        plan,
        work_root=tmp_path / "runs",
        fake_workers={
            bead: lambda path, bead=bead: (path / f"{bead}.py").write_text("VALUE = 1\n")
            for bead in ("A", "B")
        },
    )
    repo = Path(record["repo"])
    assert record["status"] == STATUS_MOCK_ONLY
    assert observed == [record["beads"][bead]["candidate"]["commit"] for bead in ("A", "B")]
    assert _git(repo, "rev-parse", "integration") == observed[-1]
    assert record["beads"]["B"]["attempt"]["baseCommit"] == observed[0]
    for bead in record["beads"].values():
        assert bead["candidate"]["validated"] is True
        assert bead["integrated"]["integrationCommit"] == bead["candidate"]["commit"]


@pytest.mark.parametrize("satisfied", [False, True])
@pytest.mark.parametrize("worker_committed", [False, True])
def test_dispatch_no_change_requires_current_head_proof(tmp_path, satisfied, worker_committed):
    plan = _tiny_plan(tmp_path, dependent=True)
    if satisfied:
        (plan / "template/A.py").write_text("VALUE = 1\n")

    def unchanged(worktree):
        if worker_committed:
            original = (worktree / "A.py").read_text()
            (worktree / "A.py").write_text("VALUE = 99\n")
            _git(worktree, "add", "A.py")
            _commit(worktree, "Worker transient change")
            (worktree / "A.py").write_text(original)

    record = run_plan(
        plan,
        work_root=tmp_path / "runs",
        fake_workers={
            "A": unchanged,
            "B": lambda path: (path / "B.py").write_text("VALUE = 1\n"),
        },
    )
    a = record["beads"]["A"]
    assert a["status"] == ("already-satisfied" if satisfied else "rejected: no source change")
    assert a["attempt"]["changedPaths"] == []
    assert a["guard"]["passed"] is False
    assert a["integrated"]["beadCommit"] is None
    assert a["candidate"] == {
        "ref": None,
        "commit": record["baseline"]["commit"],
        "tree": _git(Path(record["repo"]), "rev-parse", f"{record['baseline']['commit']}^{{tree}}"),
        "validated": satisfied,
    }
    assert a["didRunIntegratedValidation"] is True
    assert a["integrated"]["validation"]["passed"] is satisfied
    assert Path(a["integrated"]["receiptPath"]).exists()
    entry = record["requirementValidation"][0]
    assert entry["candidateCommit"] == record["baseline"]["commit"]
    assert entry["passed"] is satisfied
    merges = _git(Path(record["repo"]), "rev-list", "--merges", "integration").splitlines()
    assert len(merges) == int(satisfied)  # Only B can merge.
    if satisfied:
        assert record["status"] == STATUS_MOCK_ONLY
    else:
        assert record["beads"]["B"]["status"] == "blocked: dependency A failed"


@pytest.mark.parametrize("declared", [False, True])
def test_dispatch_labels_local_validation_without_claiming_live_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: bool
) -> None:
    from tests.python.core.execution_support import source_execution_context
    from tests.python.core.test_dispatch_cancel import source_only_packet_renderer

    from codex_harness.core.provider_launch import ProviderNormalLaunch

    plan = _tiny_audited_plan(tmp_path)
    if declared:
        manifest = json.loads((plan / "sample.json").read_text())
        manifest["integration"] = {"requirements": ["RI"], "proofModule": "test_*.py"}
        # The wildcard includes B's proof too; RI must trace to that owner.
        # Source publication already assigns integration requirements to A.
        manifest["beads"]["B"]["requirements"].append("RI")
        (plan / "sample.json").write_text(json.dumps(manifest))

    class ScriptedAdapter:
        def provider_normal_command(self, *, prompt, **kwargs):
            bead_id = "A" if "Bead A" in prompt else "B"
            # Launch-record checks alone miss the actual child's inherited cache policy.
            return ProviderNormalLaunch(
                cli="codex",
                command=(
                    sys.executable,
                    "-c",
                    # This stand-in completes source mechanics, not a real provider turn.
                    f"from pathlib import Path; Path('{bead_id}.py').write_text('VALUE = 1\\n'); "
                    "import os; assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'; "
                    f"__import__('{bead_id}'); assert not Path('__pycache__').exists(); "
                    'print(\'{"type":"turn.completed","isMocked":true}\')'
                    "\n# SOURCE-ONLY local process fixture",
                ),
                protections=("scripted stand-in",),
                environment={"PYTHONDONTWRITEBYTECODE": "0"},
            )

    monkeypatch.setattr("codex_harness.core.dispatch.get_adapter", lambda name: ScriptedAdapter())
    source_only_packet_renderer(monkeypatch)
    popen = subprocess.Popen
    spawned = []

    def observe_spawn(command, *args, **kwargs):
        if (
            command[0] == sys.executable
            and "-c" in command
            and command[command.index("-c") + 1].startswith("from pathlib import Path; Path(")
        ):
            worktree = Path(kwargs["cwd"])
            bead_id = worktree.name.removeprefix("bead-")
            attempt = worktree.parent / "evidence" / bead_id / "attempt"
            request = json.loads((attempt / "request.json").read_text())
            assert request["status"] == "launching"
            assert request["command"] == list(command)
            assert len(request["planRevision"]) == 64
            assert request["requirementRefs"] == canonical["beads"][bead_id]["requirementRefs"]
            assert (attempt / f"R{bead_id}" / "request.json").exists()
            spawned.append(bead_id)
        return popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", observe_spawn)
    _bind_protected(plan)
    context = source_execution_context(tmp_path / "canonical", plan)
    plan = context["plan"]
    canonical = json.loads((plan / "sample.json").read_text())
    record = run_plan(plan, work_root=context["runs"], **context["kwargs"])
    assert sorted(spawned) == ["A", "B"]
    assert record["status"] == (STATUS_VALIDATED if declared else STATUS_VALIDATED_NO_INTEGRATION)
    assert record["isLiveProductProof"] is False
    assert record["liveAcceptance"]["claimed"] is False
    assert record["didExecuteRealProvider"] is False
    assert all(bead["didRunProvider"] for bead in record["beads"].values())
    assert all(entry["passed"] for entry in record["requirementValidation"][:2])
    integration = record["requirementValidation"][-1]
    if declared:
        assert integration["passed"] is True
        assert integration["requirements"] == ["RI"]
    else:
        assert integration == {
            "bead": "integration",
            "declared": False,
            "planRevision": record["planRevision"],
            "requirementRefs": [],
        }
        assert record["integrationValidation"] is None
    saved = json.loads((context["runs"] / record["runId"] / "evidence" / "run.json").read_text())
    assert saved == record


@pytest.mark.parametrize("artifact", ["bytecode", "source"])
def test_generated_bytecode_integrates_but_untracked_source_refuses(
    tmp_path, monkeypatch, artifact
):
    """Catch post-worker import caches blocking integration; existing retries create no caches."""
    from codex_harness.core import dispatch

    plan = _tiny_plan(tmp_path)
    path = plan / "sample.json"
    manifest = json.loads(path.read_text())
    del manifest["beads"]["B"]
    path.write_text(json.dumps(manifest))
    original = dispatch._integration_candidate

    def integrate(record, bead, repo):
        worker = Path(record["attempt"]["cwd"])
        exclude = repo / ".git/info/exclude"
        # Appending must retain an existing local owner's bytes, including a missing LF.
        assert exclude.read_bytes().startswith(b"# retained local entry")
        common = _git(worker, "rev-parse", "--path-format=absolute", "--git-common-dir")
        assert Path(common).resolve() == (repo / ".git").resolve()
        if artifact == "bytecode":
            environment = dict(os.environ)
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            environment.pop("PYTHONPYCACHEPREFIX", None)
            subprocess.run(
                [sys.executable, "-c", "import A"], cwd=worker, env=environment, check=True
            )
            assert list((worker / "__pycache__").glob("*.pyc"))
            (worker / "loose.pyc").write_bytes(b"generated bytecode")
        else:
            (worker / "unexpected.py").write_text("VALUE = 9\n")
        return original(record, bead, repo)

    template = tmp_path / "git-template/info"
    template.mkdir(parents=True)
    (template / "exclude").write_bytes(b"# retained local entry")
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template.parent))
    monkeypatch.setattr(dispatch, "_integration_candidate", integrate)
    result = run_plan(
        plan,
        work_root=tmp_path / "runs",
        fake_workers={"A": lambda tree: (tree / "A.py").write_text("VALUE = 1\n")},
    )
    if artifact == "bytecode":
        assert result["status"] == STATUS_MOCK_ONLY
        assert _git(Path(result["repo"]), "status", "--porcelain") == ""
    else:
        assert "integration recovery worker changed" in result["beads"]["A"]["status"]


def test_new_dispatch_repo_refuses_foreign_git_metadata(tmp_path):
    """Catch editing another repo's common exclude; ordinary fixtures have no .git indirection."""
    plan = _tiny_plan(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init", "-q")
    exclude = foreign / ".git/info/exclude"
    exclude.write_bytes(b"foreign sentinel\n")
    (plan / "template/.git").write_text(f"gitdir: {foreign / '.git'}\n")
    with pytest.raises(ValueError, match="dispatch repository metadata"):
        run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    assert exclude.read_bytes() == b"foreign sentinel\n"


@pytest.mark.parametrize("provider", ["codex", "claude", "copilot"])
def test_dispatch_real_provider_fact_requires_execution_and_provider_identity(
    provider: str,
) -> None:
    record = {
        "provider": provider,
        "didRunProvider": True,
        "isMocked": False,
        "attempt": {"launch": {"cli": provider}},
    }
    assert _did_execute_real_provider(record) is True
    assert _did_execute_real_provider({**record, "didRunProvider": False}) is False
    assert _did_execute_real_provider({**record, "isMocked": True}) is False
    assert (
        _did_execute_real_provider({**record, "attempt": {"launch": {"cli": "scripted"}}}) is False
    )


@pytest.mark.parametrize("committed", [False, True])
def test_dispatch_guard_rejection_blocks_dependents(tmp_path: Path, committed: bool) -> None:
    def change_tests(worktree: Path) -> None:
        (worktree / "tests" / "test_money.py").write_text("# weakened tests\n")
        if committed:
            _git(worktree, "add", "-A")
            _commit(worktree, "Attempt to hide requirement changes in a worker commit")

    record = run_plan(
        FIXTURE,
        work_root=tmp_path,
        fake_workers={"A": change_tests, "B": lambda path: pytest.fail("B must not launch")},
    )
    assert record["beads"]["A"]["status"] == "rejected: requirement files changed"
    assert not record["beads"]["A"]["guard"]["requirementFilesIntact"]
    assert record["beads"]["B"]["status"] == "blocked: dependency A failed"
    assert record["beads"]["B"]["attempt"] is None
    assert record["status"].startswith("plan-failed: A rejected:")


def test_dispatch_cli_reference_summary(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_plan.py"),
            "--plan",
            str(FIXTURE),
            "--work-root",
            str(tmp_path),
            "--mock-provider",
            "reference",
            "--provider",
            "B=codex",
            "--model",
            "A=test-model",
            "--effort",
            "A=high",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "Efficiency A:" in result.stderr and "Efficiency B:" in result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == STATUS_MOCK_ONLY
    record = json.loads((tmp_path / summary["runId"] / "evidence" / "run.json").read_text())
    assert record["beads"]["B"]["provider"] == "codex"
    assert record["beads"]["A"]["model"] == "test-model"
    assert record["beads"]["A"]["effort"] == "high"
    resumed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_plan.py"),
            "--plan",
            str(FIXTURE),
            "--work-root",
            str(tmp_path),
            "--resume",
            str(tmp_path / summary["runId"]),
            "--relaunch-ambiguous",
            "--mock-provider",
            "reference",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert "Efficiency A:" in resumed.stderr and "Efficiency B:" in resumed.stderr
    saved = json.loads((tmp_path / summary["runId"] / "evidence" / "run.json").read_text())
    assert all(bead["status"] == "reused" for bead in saved["beads"].values())
    assert saved["repo"] == record["repo"]


def test_dispatch_failed_bead_blocks_transitive_dependents_but_independent_continues(
    tmp_path: Path,
) -> None:
    plan = _tiny_plan(tmp_path, dependent=True)
    manifest = json.loads((plan / "sample.json").read_text())
    manifest["beads"]["C"] = {**manifest["beads"]["B"], "dependsOn": ["B"]}
    manifest["beads"]["D"] = {**manifest["beads"]["B"], "ownedFiles": ["D.py"]}
    manifest["beads"]["D"].pop("dependsOn")
    shutil.copy2(plan / "template" / "B.py", plan / "template" / "D.py")
    (plan / "sample.json").write_text(json.dumps(manifest))

    def independent(worktree: Path) -> None:
        (worktree / "D.py").write_text("VALUE = 1\n")

    # D proves its own file, avoiding a proof that silently depends on B.
    manifest["beads"]["D"]["proofModule"] = "test_D.py"
    (plan / "sample.json").write_text(json.dumps(manifest))
    (plan / "template" / "tests" / "test_D.py").write_text(
        (plan / "template" / "tests" / "test_B.py").read_text().replace("from B", "from D")
    )
    record = run_plan(
        plan,
        work_root=tmp_path / "runs",
        fake_workers={
            "A": lambda path: None,
            "D": independent,
            "B": lambda path: pytest.fail("B launched"),
            "C": lambda path: pytest.fail("C launched"),
        },
    )
    assert record["beads"]["A"]["status"] == "rejected: no source change"
    assert record["beads"]["B"]["status"] == "blocked: dependency A failed"
    assert record["beads"]["C"]["status"] == "blocked: dependency A failed"
    assert record["beads"]["D"]["status"] == STATUS_MOCK_ONLY


def test_dispatch_final_proof_failure_is_not_plan_validated(tmp_path: Path) -> None:
    plan = _tiny_plan(tmp_path)
    manifest = json.loads((plan / "sample.json").read_text())
    manifest["integration"] = {"proofModule": "test_final.py"}
    (plan / "sample.json").write_text(json.dumps(manifest))
    (plan / "template" / "tests" / "test_final.py").write_text(
        "import unittest\nclass Final(unittest.TestCase):\n"
        "    def test_final(self):\n        self.fail('integration requirement unmet')\n"
    )

    def worker(bead_id: str):
        def edit(worktree: Path) -> None:
            (worktree / f"{bead_id}.py").write_text("VALUE = 1\n")

        return edit

    record = run_plan(
        plan,
        work_root=tmp_path / "runs",
        fake_workers={bead_id: worker(bead_id) for bead_id in ("A", "B")},
    )
    assert all(bead["status"] == STATUS_MOCK_ONLY for bead in record["beads"].values())
    assert not record["integrationValidation"]["passed"]
    assert record["status"] == "plan-failed: integration proof failed"
    assert all(bead["candidate"]["validated"] for bead in record["beads"].values())
    assert (
        _git(Path(record["repo"]), "rev-parse", "HEAD")
        == record["beads"]["B"]["candidate"]["commit"]
    )
    assert (
        record["integrationValidation"]["integrationCommit"]
        == record["beads"]["B"]["candidate"]["commit"]
    )
