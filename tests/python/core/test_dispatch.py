"""Stand-in-only dispatch integration tests; never launch a real provider."""

import json
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


def test_dispatch_attempts_overlap_and_integrate_without_ownership_conflict(tmp_path: Path) -> None:
    plan = _tiny_plan(tmp_path)
    barrier = threading.Barrier(2, timeout=10)
    arrivals = []

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
        fake_workers={bead_id: worker(bead_id) for bead_id in ("A", "B")},
    )
    assert sorted(arrivals) == ["A", "B"]
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

    def observe(cwd, manifest, module):
        if cwd.name.startswith("candidate-"):
            commit = _git(cwd, "rev-parse", "HEAD")
            assert _git(cwd, "status", "--porcelain") == ""
            assert _git(cwd, "branch", "--show-current").startswith("candidate/")
            assert _git(cwd, "rev-parse", "integration") == _git(cwd, "rev-parse", "HEAD^1")
            assert commit != _git(cwd, "rev-parse", "integration")
            observed.append(commit)
        return validate(cwd, manifest, module)

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

    plan = _tiny_plan(tmp_path)
    if declared:
        manifest = json.loads((plan / "sample.json").read_text())
        manifest["integration"] = {"requirements": ["RI"], "proofModule": "test_*.py"}
        (plan / "sample.json").write_text(json.dumps(manifest))

    class ScriptedAdapter:
        def provider_normal_command(self, *, prompt, **kwargs):
            bead_id = "A" if "Bead A" in prompt else "B"
            return ProviderNormalLaunch(
                cli="codex",
                command=(
                    sys.executable,
                    "-c",
                    # This stand-in completes source mechanics, not a real provider turn.
                    f"from pathlib import Path; Path('{bead_id}.py').write_text('VALUE = 1\\n'); "
                    'print(\'{"type":"turn.completed","isMocked":true}\')'
                    "\n# SOURCE-ONLY local process fixture",
                ),
                protections=("scripted stand-in",),
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
