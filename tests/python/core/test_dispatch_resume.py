"""Revision and recovery proofs using only reference workers or local stand-ins."""

import hashlib
import json
import runpy
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_harness.core import dispatch
from codex_harness.core.dispatch import STATUS_MOCK_ONLY, _git, run_plan
from codex_harness.core.graph import load_plan
from codex_harness.core.mail import Mailbox
from codex_harness.core.spine import (
    Spine,
    compile_manifest,
    load_spine,
    requirement_files_revision,
    save_spine,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dogfood-tipsplit"


def test_dispatch_resume_recovers_worker_blocker_without_duplicate_mail(tmp_path):
    plan = _plan(tmp_path)

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
    )
    assert "GitHub must be resume-user." in first["beads"]["A"]["prompt"]
    mailbox = Mailbox(first["memoryWorkspace"])
    before = mailbox.inbox("planner")
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        setup={"githubLogin": "resume-user"},
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


@pytest.mark.parametrize("evidence_owner", ["current", "superseded"])
def test_resume_denial_never_reuses_or_relaunches_success(tmp_path, evidence_owner):
    plan = _plan(tmp_path)
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    old = first["beads"]["A"]
    if evidence_owner == "superseded":
        old["superseded"] = [
            {
                "evidenceDir": str(tmp_path / "previous"),
                "attempt": {},
                "permissionGuard": {"requiresUserDisposition": True, "disposition": "guard-breach"},
            }
        ]
        dispatch._write_record(_run_dir(first) / "evidence/run.json", first)
    else:
        # Legacy successful receipt has no guard metadata. Raw events are authority.
        (Path(old["evidenceDir"]) / "attempt/events.jsonl").write_text(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "permission_denied",
                    "tool_name": "PowerShell",
                    "tool_use_id": "rejected",
                }
            )
            + "\n"
        )
    before = _git(Path(first["repo"]), "rev-parse", "HEAD")
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
        retry_blocked="all",
        relaunch_ambiguous=True,
        fake_workers={"A": lambda path: pytest.fail("denied attempt relaunched")},
    )
    a = resumed["beads"]["A"]
    assert a["status"] == dispatch.STATUS_PERMISSION_HELD
    assert not a["candidate"]["validated"] and not a.get("reused")
    assert not resumed["requirementValidation"][0]["passed"]
    assert resumed["status"].startswith("plan-failed")
    assert _git(Path(resumed["repo"]), "rev-parse", "HEAD") == before
    assert "A" not in resumed["resumeSummary"]["redispatched"]


@pytest.mark.parametrize("telemetry", ["missing", "unsupported", "incomplete"])
def test_resume_actual_attempt_requires_conclusive_denial_audit(tmp_path, telemetry):
    plan = _plan(tmp_path)
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    old = first["beads"]["A"]
    # Construct real-provider-shaped persisted records; no provider executes.
    old.update(isMocked=False, didRunProvider=True)
    old["attempt"].update(isMocked=False, didRunProvider=True, mode="provider-normal")
    attempt_dir = Path(old["evidenceDir"]) / "attempt"
    result_path = attempt_dir / "result.json"
    result = json.loads(result_path.read_text())
    result.update(isMocked=False, didRunProvider=True, mode="provider-normal")
    dispatch._write_record(result_path, result)
    if telemetry != "missing":
        event = {"type": "future.provider.event" if telemetry == "unsupported" else "turn.started"}
        (attempt_dir / "events.jsonl").write_bytes((json.dumps(event) + "\n").encode())
    dispatch._write_record(_run_dir(first) / "evidence/run.json", first)
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
        retry_blocked="all",
        relaunch_ambiguous=True,
        fake_workers={"A": lambda path: pytest.fail("inconclusive attempt relaunched")},
    )
    a = resumed["beads"]["A"]
    assert a["status"] == "held: denial audit inconclusive"
    assert not a["candidate"]["validated"] and not a.get("reused")
    assert not resumed["requirementValidation"][0]["passed"]
    assert a["permissionGuard"]["didObservePermissionDenial"] is None
    assert a["permissionGuard"]["auditReason"] == telemetry


@pytest.mark.parametrize("change", ["harness", "plan", "force-id", "force-all"])
def test_dispatch_resume_retries_blocker_and_releases_dependent(tmp_path, monkeypatch, change):
    plan = _plan(tmp_path)
    blocker = {"reason": "Packet contradiction"}

    def blocked(worktree):
        (worktree / ".harness").mkdir()
        (worktree / ".harness/blocker.json").write_text(json.dumps(blocker))

    first = run_plan(
        plan, work_root=tmp_path / "runs", mock_provider="reference", fake_workers={"A": blocked}
    )
    old = first["beads"]["A"]
    old_request = Path(old["evidenceDir"]) / "attempt/request.json"
    assert json.loads(old_request.read_text())["prompt"] == old["prompt"]
    if change == "harness":
        builder = dispatch.worker_packet.build_packet
        monkeypatch.setattr(
            dispatch.worker_packet,
            "build_packet",
            lambda *args, **kwargs: builder(*args, **kwargs) + "\nCorrected cwd instructions.",
        )
        # Runs produced before this repair store the prompt on the bead only.
        request = json.loads(old_request.read_text())
        request.pop("prompt")
        dispatch._write_record(old_request, request)
    elif change == "plan":
        path = plan / "sample.json"
        manifest = json.loads(path.read_text())
        manifest["beads"]["A"]["title"] += " with clarified rounding"
        manifest["planRevision"] = "c" * 64
        path.write_text(json.dumps(manifest))
    retry = "A" if change == "force-id" else "all" if change == "force-all" else None
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
        retry_blocked=retry,
    )
    assert resumed["status"] == STATUS_MOCK_ONLY
    a = resumed["beads"]["A"]
    assert a["candidate"]["validated"]
    assert resumed["beads"]["B"]["candidate"]["validated"]
    assert "blocker" not in a
    assert a["attempt"]["cwd"] != old["attempt"]["cwd"]
    assert Path(a["attempt"]["cwd"]).name == "bead-A-retry-1"
    history = a["superseded"][-1]
    assert history["blocker"] == blocker
    assert json.loads(Path(history["blockerPath"]).read_text()) == blocker
    assert not (Path(old["attempt"]["cwd"]) / ".harness/blocker.json").exists()
    assert json.loads(old_request.read_text())["disposition"] == "superseded"
    assert resumed["resumeSummary"]["redispatched"] == ["A", "B"]
    assert resumed["resumeSummary"]["retained"] == []
    for bead_id in resumed["resumeSummary"]["redispatched"]:
        bead = resumed["beads"][bead_id]
        request = json.loads((Path(bead["evidenceDir"]) / "attempt/request.json").read_text())
        assert request["didLaunchMockAttempt"]
        assert (Path(bead["evidenceDir"]) / "attempt/result.json").exists()


@pytest.mark.parametrize("flag", ["A", "A,B", "all"])
def test_run_plan_cli_retry_blocked(tmp_path, flag, capsys):
    plan = _plan(tmp_path)

    def blocked(worktree):
        (worktree / ".harness").mkdir()
        (worktree / ".harness/blocker.json").write_text('{"reason": "fixed externally"}')

    first = run_plan(
        plan, work_root=tmp_path / "runs", mock_provider="reference", fake_workers={"A": blocked}
    )
    main = runpy.run_path(str(FIXTURE.parents[1] / "scripts/run_plan.py"))["main"]
    assert (
        main(
            [
                "--plan",
                str(plan),
                "--work-root",
                str(tmp_path / "unused"),
                "--resume",
                str(_run_dir(first)),
                "--retry-blocked",
                flag,
                "--mock-provider",
                "reference",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    _, offset = json.JSONDecoder().raw_decode(output)
    assert json.loads(output[offset:])["redispatched"] == ["A", "B"]


@pytest.mark.parametrize("failure", ["command", "spawn"])
def test_dispatch_resume_prelaunch_failure_is_not_redispatched(tmp_path, monkeypatch, failure):
    plan = _plan(tmp_path)

    def fail_before_launch(*args, **kwargs):
        raise ValueError("provider command rejected before launch")

    if failure == "command":
        first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="noop")
        monkeypatch.setattr(dispatch, "_attempt_bead", fail_before_launch)
        options = {"mock_provider": "reference"}
    else:
        from tests.python.core.execution_support import source_execution_context

        context = source_execution_context(tmp_path / "canonical", plan, timeout=10, call_cap=2)
        plan = context["plan"]
        options = context["kwargs"]
        monkeypatch.setattr(
            dispatch,
            "get_adapter",
            lambda _: SimpleNamespace(provider_normal_command=fail_before_launch),
        )
        # The actual owned controller binds the initial run before the builder
        # fails. Resume must use that original, without a fabricated run binding.
        first = run_plan(plan, work_root=context["runs"], **options)
        assert "provider command rejected" in first["beads"]["A"]["status"]
        original = json.loads((context["directory"] / "dispatch-run.json").read_text())
        assert Path(original["runDir"]) == _run_dir(first)
        budget = context["directory"] / "provider-call-budget"
        assert not budget.exists()
        launch = SimpleNamespace(
            command=(str(tmp_path / "nonexistent-provider.exe"),),
            environment={},
            stdin_text=None,
            to_payload=lambda: {"cli": "codex", "command": ["nonexistent-provider.exe"]},
        )
        monkeypatch.setattr(
            dispatch,
            "get_adapter",
            lambda _: SimpleNamespace(provider_normal_command=lambda **kwargs: launch),
        )
        popen = subprocess.Popen
        failed_spawns = []

        def observe_spawn(command, *args, **kwargs):
            if tuple(command) == launch.command:
                failed_spawns.append(list(command))
            return popen(command, *args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", observe_spawn)
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        **options,
    )
    assert (
        "provider command rejected" if failure == "command" else "provider launch-failed"
    ) in resumed["beads"]["A"]["status"]
    assert resumed["resumeSummary"]["redispatched"] == []
    if failure == "spawn":
        assert failed_spawns == [list(launch.command)]
        assert resumed["runId"] == first["runId"] and resumed["repo"] == first["repo"]
        assert json.loads((context["directory"] / "dispatch-run.json").read_text()) == original
        assert len(list((context["directory"] / "dispatch-resume").glob("*.json"))) == 1
        claims = list(budget.glob("*.json"))
        assert len(claims) == 1  # A failed Popen still consumes the original finite cap.
        attempt = Path(resumed["beads"]["A"]["evidenceDir"]) / "attempt"
        assert Path(json.loads(claims[0].read_text())["attemptDir"]) == attempt
        result = json.loads((attempt / "result.json").read_text())
        assert result["status"] == "launch-failed" and result["processId"] is None
        assert result["failure"]["type"] == "FileNotFoundError"
        assert not result["didRunProvider"] and not result["isMocked"]
        assert not (attempt / "process-provenance.json").exists()


def test_dispatch_resume_efficiency_reuses_attempts_without_double_counting(tmp_path):
    plan = _plan(tmp_path)
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
    )
    assert resumed["efficiency"] == first["efficiency"]
    assert resumed["efficiency"]["inputTokens"] is None
    assert all("efficiency" in bead["attempt"] for bead in resumed["beads"].values())


def test_dispatch_custom_manifest_path_supplies_inline_requirement_text(tmp_path):
    plan = _plan(tmp_path)
    original = plan / "sample.json"
    manifest = json.loads(original.read_text())
    text = "Inline requirement supplied by the actual custom manifest."
    manifest["beads"]["A"]["requirementRefs"][0]["text"] = text
    custom = plan / "custom-plan.json"
    custom.write_text(json.dumps(manifest), encoding="utf-8")
    original.unlink()
    record = run_plan(custom, work_root=tmp_path / "runs", mock_provider="reference")
    assert record["status"] == STATUS_MOCK_ONLY
    assert text in record["beads"]["A"]["prompt"]
    assert "def parse_amount(text: str) -> int:" in record["beads"]["B"]["prompt"]


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


def _reference(plan: Path, bead_id: str):
    def worker(worktree: Path) -> None:
        manifest = json.loads((plan / "sample.json").read_text())
        for name in manifest["beads"][bead_id]["ownedFiles"]:
            shutil.copy2(plan / "reference" / name, worktree / name)

    return worker


def _assert_not_ancestor(repo: Path, commit: str, descendant: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, descendant],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stderr


def test_dispatch_resume_failure_branches_without_failed_candidate(tmp_path):
    plan = _plan(tmp_path)

    def incorrect(worktree):
        _reference(plan, "A")(worktree)
        source = worktree / "tipsplit/money.py"
        source.write_text(source.read_text().replace("+ 50", "+ 49"))

    failed = run_plan(
        plan, work_root=tmp_path / "runs", mock_provider="reference", fake_workers={"A": incorrect}
    )
    repo = Path(failed["repo"])
    candidate = failed["beads"]["A"]["candidate"]
    assert failed["beads"]["A"]["status"] == "integrated validation failed"
    assert _git(repo, "rev-parse", "integration") == failed["baseline"]["commit"]

    def retry(worktree):
        assert _git(worktree, "rev-parse", "HEAD") == failed["baseline"]["commit"]
        _assert_not_ancestor(worktree, candidate["commit"], "HEAD")
        _assert_not_ancestor(worktree, failed["beads"]["A"]["integrated"]["beadCommit"], "HEAD")
        _reference(plan, "A")(worktree)

    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(failed),
        mock_provider="reference",
        fake_workers={"A": retry},
    )
    assert resumed["status"] == STATUS_MOCK_ONLY
    assert resumed["beads"]["A"]["attempt"]["changedPaths"] == ["tipsplit/money.py"]
    assert resumed["beads"]["A"]["candidate"]["ref"] != candidate["ref"]
    assert _git(repo, "rev-parse", candidate["ref"]) == candidate["commit"]
    _assert_not_ancestor(repo, candidate["commit"], "integration")
    assert resumed["beads"]["B"]["candidate"]["validated"] is True


def test_dispatch_resume_legacy_failed_merge_is_not_accepted(tmp_path):
    plan = _plan(tmp_path)

    def incorrect(worktree):
        _reference(plan, "A")(worktree)
        source = worktree / "tipsplit/money.py"
        source.write_text(source.read_text().replace("+ 50", "+ 49"))

    failed = run_plan(
        plan, work_root=tmp_path / "runs", mock_provider="reference", fake_workers={"A": incorrect}
    )
    # Recreate an old record and its merged-before-validation repository.
    _git(Path(failed["repo"]), "merge", "--ff-only", failed["beads"]["A"]["candidate"]["commit"])
    for bead in failed["beads"].values():
        bead.pop("candidate")
        if bead["integrated"]:
            bead["integrated"].pop("candidate")
        bead["acceptsUnchanged"] = True
    dispatch._write_record(_run_dir(failed) / "evidence/run.json", failed)
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(failed),
        fake_workers={"A": lambda path: None, "B": lambda path: pytest.fail("B launched")},
    )
    a = resumed["beads"]["A"]
    assert a["status"] == "rejected: no source change"
    assert a["guard"]["passed"] is False
    assert a["candidate"]["validated"] is False
    assert resumed["resumeSummary"] == {
        "reused": [],
        "redispatched": ["A"],
        "invalidated": [],
        "retained": [],
    }
    assert resumed["beads"]["B"]["status"] == "blocked: dependency A failed"


def test_dispatch_demo_stale_test_failure_then_fixed_resume(tmp_path):
    plan = _plan(tmp_path)
    spine = load_spine(plan / "spine.json")
    (plan / "sample.json").write_text(json.dumps(compile_manifest(spine, plan / "template")))
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    assert first["status"] == STATUS_MOCK_ONLY
    original_head = _git(Path(first["repo"]), "rev-parse", "HEAD")

    spine = _change_rounding(plan)
    proof = plan / "template/tests/test_money.py"
    correct_proof = proof.read_text()
    proof.write_text(
        correct_proof + "\nclass StaleExpectation(unittest.TestCase):\n"
        "    def test_old_rounding(self):\n        self.assertEqual(sum(split(1, 1, 50)), 2)\n"
    )
    (plan / "sample.json").write_text(json.dumps(compile_manifest(spine, plan / "template")))
    failed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
        fake_workers={"B": lambda path: pytest.fail("unaffected B relaunched")},
    )
    a = failed["beads"]["A"]
    repo = Path(failed["repo"])
    assert a["status"] == "integrated validation failed"
    assert a["attempt"]["changedPaths"] == ["tipsplit/money.py"]
    assert failed["beads"]["B"]["status"] == "blocked: dependency A failed"
    assert _git(repo, "rev-parse", "HEAD") == failed["planUpdate"]["commit"]
    assert _git(repo, "rev-parse", "HEAD^1") == original_head
    assert _git(repo, "show", "HEAD:tipsplit/money.py") == _git(
        repo, "show", f"{original_head}:tipsplit/money.py"
    )

    proof.write_text(correct_proof)
    (plan / "sample.json").write_text(json.dumps(compile_manifest(spine, plan / "template")))

    def retry(worktree):
        _assert_not_ancestor(worktree, a["candidate"]["commit"], "HEAD")
        _assert_not_ancestor(worktree, a["integrated"]["beadCommit"], "HEAD")
        _reference(plan, "A")(worktree)

    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
        fake_workers={"A": retry, "B": lambda path: pytest.fail("unaffected B relaunched")},
    )
    assert resumed["status"] == STATUS_MOCK_ONLY
    assert resumed["integrationValidation"]["passed"] is True
    assert all(entry["passed"] for entry in resumed["requirementValidation"])
    assert resumed["beads"]["A"]["attempt"]["changedPaths"] == ["tipsplit/money.py"]
    assert resumed["beads"]["B"]["status"] == "reused"
    assert resumed["beads"]["B"]["attempt"] == first["beads"]["B"]["attempt"]
    assert _git(repo, "rev-parse", a["candidate"]["ref"]) == a["candidate"]["commit"]
    _assert_not_ancestor(repo, a["candidate"]["commit"], "integration")
    assert len(_git(repo, "rev-list", "--merges", "integration").splitlines()) == 3


def test_dispatch_revision_in_prompt_records_and_git_trailers(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    def worker(worktree: Path) -> None:
        request = json.loads((worktree.parent / "evidence/A/attempt/request.json").read_text())
        assert request["status"] == "launching"
        assert request["command"] == ["mock:test-worker"]
        assert request["planRevision"] == "a" * 64
        assert request["requirementRefs"][1] == {"id": "R2", "revision": "b" * 64}
        _reference(plan, "A")(worktree)

    record = run_plan(
        plan, work_root=tmp_path / "runs", mock_provider="reference", fake_workers={"A": worker}
    )
    assert record["status"] == STATUS_MOCK_ONLY
    for bead in record["beads"].values():
        assert bead["planRevision"] == "a" * 64
        assert bead["requirementRefs"]
        assert "Plan revision: aaaaaaaaaaaa. Requirements:" in bead["prompt"]
        assert all(f"{ref['id']}@bbbbbbbb" in bead["prompt"] for ref in bead["requirementRefs"])
        message = _git(
            Path(record["repo"]), "log", "-1", "--format=%B", bead["integrated"]["beadCommit"]
        )
        assert f"Harness-Plan-Revision: {'a' * 64}" in message
        assert f"Harness-Requirements: {dispatch._refs_text(bead['requirementRefs'])}" in message
    saved = json.loads((_run_dir(record) / "evidence/run.json").read_text())
    assert saved == record
    for entry in saved["requirementValidation"]:
        assert entry["planRevision"] == "a" * 64
        assert entry["requirementRefs"]


def test_dispatch_ambiguous_launch_requires_explicit_relaunch(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    def crash(worktree: Path) -> None:
        assert (worktree.parent / "evidence/A/attempt/request.json").exists()
        raise RuntimeError("crash after durable launch record")

    first = run_plan(
        plan, work_root=tmp_path / "runs", mock_provider="reference", fake_workers={"A": crash}
    )
    run_dir = _run_dir(first)
    request_path = run_dir / "evidence/A/attempt/request.json"
    original_request = request_path.read_text()
    assert not request_path.with_name("result.json").exists()
    for _ in range(2):
        resumed = run_plan(
            plan,
            work_root=tmp_path / "unused",
            resume_run_dir=run_dir,
            fake_workers={
                bead: lambda path: pytest.fail("duplicate launch") for bead in ("A", "B")
            },
        )
        assert resumed["beads"]["A"]["status"] == "ambiguous launch"
        assert resumed["beads"]["B"]["status"] == "blocked: dependency A failed"
        assert resumed["beads"]["B"]["attempt"] is None
        assert request_path.read_text() == original_request
    retried = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=run_dir,
        relaunch_ambiguous=True,
        mock_provider="reference",
    )
    assert retried["status"] == STATUS_MOCK_ONLY
    assert retried["repo"] == first["repo"]
    assert retried["runId"] == first["runId"]
    assert retried["beads"]["A"]["superseded"][0]["status"] == "superseded"
    assert json.loads(request_path.read_text())["disposition"] == "superseded"
    assert Path(retried["beads"]["A"]["attempt"]["cwd"]).name == "bead-A-retry-1"


def test_dispatch_resume_requirement_change_reuses_unaffected_bead_and_reproves_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    run_dir = _run_dir(first)
    old_head = _git(Path(first["repo"]), "rev-parse", "HEAD")
    old_a = first["beads"]["A"]
    old_b = first["beads"]["B"]
    manifest = json.loads((plan / "sample.json").read_text())
    manifest["planRevision"] = "c" * 64
    manifest["beads"]["A"]["requirementRefs"][1]["revision"] = "d" * 64
    (plan / "sample.json").write_text(json.dumps(manifest))
    proofs = []
    validate = dispatch._validate

    def observe(cwd, manifest, module, **kwargs):
        proofs.append((module, _git(cwd, "rev-parse", "HEAD")))
        return validate(cwd, manifest, module, **kwargs)

    monkeypatch.setattr(dispatch, "_validate", observe)
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=run_dir,
        mock_provider="reference",
        fake_workers={"B": lambda path: pytest.fail("B relaunched")},
    )
    assert resumed["status"] == STATUS_MOCK_ONLY
    a, b = resumed["beads"]["A"], resumed["beads"]["B"]
    assert a["attempt"]["baseCommit"] == old_head
    assert a["planRevision"] == "c" * 64
    assert a["requirementRefs"][1]["revision"] == "d" * 64
    assert a["superseded"][0]["status"] == "superseded"
    assert a["superseded"][0]["integrated"] == old_a["integrated"]
    assert (
        json.loads((Path(old_a["evidenceDir"]) / "integrated.json").read_text())["disposition"]
        == "superseded"
    )
    assert b["status"] == "reused" and b["reused"] is True
    assert b["planRevision"] == old_b["planRevision"]
    assert b["acceptedPlanRevision"] == "c" * 64
    assert b["attempt"] == old_b["attempt"]
    assert b["integrated"] == old_b["integrated"]
    new_head = _git(Path(resumed["repo"]), "rev-parse", "HEAD")
    assert new_head == old_head
    assert a["status"] == "already-satisfied"
    assert a["guard"]["passed"] is False
    # Spec refs changed but proof inputs and all source identities did not:
    # A's provider attempt re-proves, B and integration retain their claims.
    assert proofs == [("test_money.py", new_head)]
    assert resumed["integrationValidation"]["integrationCommit"] == new_head
    assert resumed["integrationValidationHistory"] == [first["integrationValidation"]]
    assert resumed["baseline"] == first["baseline"]


@pytest.mark.parametrize("change", ["planRevision", "requirementRefs", "specRefs"])
def test_dispatch_stale_attempt_is_never_merged(tmp_path: Path, change: str) -> None:
    plan = _plan(tmp_path)
    if change == "specRefs":
        _compile_plan(plan, load_spine(plan / "spine.json"))

    def stale(worktree: Path) -> None:
        _reference(plan, "A")(worktree)
        manifest = json.loads((plan / "sample.json").read_text())
        if change == "planRevision":
            manifest["planRevision"] = "c" * 64
        elif change == "requirementRefs":
            manifest["beads"]["A"]["requirementRefs"][0]["revision"] = "d" * 64
        else:
            manifest["beads"]["A"]["specRefs"][0]["revision"] = "d" * 64
        (plan / "sample.json").write_text(json.dumps(manifest))

    record = run_plan(
        plan,
        work_root=tmp_path / "runs",
        fake_workers={"A": stale, "B": lambda path: pytest.fail("B launched")},
    )
    assert record["beads"]["A"]["status"] == "rejected: stale plan revision"
    assert record["beads"]["A"]["integrated"] is None
    assert not record["beads"]["A"]["didRunIntegratedValidation"]
    assert record["beads"]["B"]["status"] == "blocked: dependency A failed"
    assert _git(Path(record["repo"]), "rev-parse", "HEAD") == record["baseline"]["commit"]
    assert _git(Path(record["repo"]), "rev-list", "--merges", "HEAD") == ""


@pytest.mark.parametrize("field", ["runId", "bead", "planRevision", "prompt"])
def test_dispatch_resume_refuses_foreign_completed_result(tmp_path, monkeypatch, field):
    plan = _plan(tmp_path)
    integrate = dispatch._integrate_bead

    def crash(*args, **kwargs):
        raise RuntimeError("controller stopped before merge")

    monkeypatch.setattr(dispatch, "_integrate_bead", crash)
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    monkeypatch.setattr(dispatch, "_integrate_bead", integrate)
    attempt_dir = Path(first["beads"]["A"]["evidenceDir"]) / "attempt"
    result = dispatch.read_record_json(attempt_dir / "result.json")
    result[field] = "foreign-identity"
    dispatch._write_record(attempt_dir / "result.json", result)
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
        fake_workers={"A": lambda path: pytest.fail("invalid result relaunched")},
    )
    bead = resumed["beads"]["A"]
    assert bead["status"].startswith("rejected: attempt identity mismatch")
    assert bead["integrated"] is None
    assert not bead["didRunIntegratedValidation"]
    assert not bead["attempt"].get("recoverResult")
    assert _git(Path(resumed["repo"]), "rev-parse", "HEAD") == first["baseline"]["commit"]
    assert not (attempt_dir.parent / "retry-1").exists()


def test_dispatch_resume_completed_attempt_without_duplicate_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    integrate = dispatch._integrate_bead

    def crash(*args, **kwargs):
        raise RuntimeError("controller stopped before merge")

    monkeypatch.setattr(dispatch, "_integrate_bead", crash)
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    monkeypatch.setattr(dispatch, "_integrate_bead", integrate)
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
        fake_workers={"A": lambda path: pytest.fail("A relaunched")},
    )
    assert resumed["status"] == STATUS_MOCK_ONLY
    assert resumed["beads"]["A"]["attempt"]["cwd"] == first["beads"]["A"]["attempt"]["cwd"]
    assert resumed["beads"]["A"]["attempt"]["recoverResult"] is True
    assert "A" not in resumed["resumeSummary"]["redispatched"]
    assert {"bead": "A", "reason": "completed attempt recovered without relaunch"} in resumed[
        "resumeSummary"
    ]["retained"]


def test_completed_attempt_checks_original_request_and_rechecks_before_use(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    integrate = dispatch._integrate_bead

    def crash(*args, **kwargs):
        raise RuntimeError("controller stopped before merge")

    monkeypatch.setattr(dispatch, "_integrate_bead", crash)
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    monkeypatch.setattr(dispatch, "_integrate_bead", integrate)
    bead = first["beads"]["A"]
    attempt_dir = Path(bead["evidenceDir"]) / "attempt"
    request = dispatch.read_record_json(attempt_dir / "request.json")
    result = dispatch.read_record_json(attempt_dir / "result.json")
    assert dispatch._completed_attempt_identity_error(bead, request, result) is None
    # Use the actual dispatch-generated request/result shape, not a new admission schema.
    for field in request.keys() - {"status", "didLaunchMockAttempt", "finishedAt"}:
        changed = {**result, field: "foreign-value"}
        assert dispatch._completed_attempt_identity_error(bead, request, changed), field
    assert (
        dispatch._completed_attempt_identity_error(
            bead, request, {**result, "finishedAt": "later-completion-observation"}
        )
        is None
    )
    from codex_harness.core.provider_launch import get_adapter

    launch = get_adapter("codex").provider_normal_command(prompt=request["prompt"]).to_payload()
    normal = {**request, "mode": "provider-normal", "launch": launch, "command": launch["command"]}
    normal_record = {**bead, "attempt": {**bead["attempt"], **normal}}
    assert dispatch._completed_attempt_identity_error(normal_record, normal, normal) is None
    missing = {key: value for key, value in result.items() if key != "cwd"}
    assert dispatch._completed_attempt_identity_error(bead, request, missing)
    changed_request = {**request, "bead": "different-bead"}
    assert dispatch._completed_attempt_identity_error(bead, changed_request, changed_request)
    changed_attempt = {**bead, "attempt": {**bead["attempt"], "baseCommit": "different-base"}}
    assert dispatch._completed_attempt_identity_error(changed_attempt, request, result)

    attempt_bead = dispatch._attempt_bead

    def substitute_after_resume_check(*args, **kwargs):
        dispatch._write_record(attempt_dir / "result.json", {**result, "runId": "foreign-run"})
        return attempt_bead(*args, **kwargs)

    monkeypatch.setattr(dispatch, "_attempt_bead", substitute_after_resume_check)
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
        fake_workers={"A": lambda path: pytest.fail("substituted result relaunched")},
    )
    recovered = resumed["beads"]["A"]
    assert recovered["status"].startswith("rejected: attempt identity mismatch")
    assert recovered["integrated"] is None
    assert not recovered["attempt"].get("recoverResult")
    assert resumed["resumeSummary"]["redispatched"] == []
    assert dispatch.read_record_json(attempt_dir / "result.json")["runId"] == "foreign-run"
    assert _git(Path(resumed["repo"]), "rev-parse", "HEAD") == first["baseline"]["commit"]


@pytest.mark.parametrize("legacy", [False, True])
def test_dispatch_resume_unchanged_plan_launches_no_workers(tmp_path: Path, legacy: bool) -> None:
    plan = _plan(tmp_path)
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    if legacy:
        # Legacy records have no immutable claim inputs; re-prove without a
        # provider launch rather than synthesizing evidence from today's files.
        first.pop("claims")
        for bead in first["beads"].values():
            bead.pop("candidate")
            bead["integrated"].pop("candidate")
        dispatch._write_record(_run_dir(first) / "evidence/run.json", first)
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        fake_workers={bead: lambda path: pytest.fail("worker relaunched") for bead in ("A", "B")},
    )
    assert resumed["status"] == STATUS_MOCK_ONLY
    assert all(bead["status"] == "reused" for bead in resumed["beads"].values())
    assert all(bead["candidate"]["validated"] for bead in resumed["beads"].values())
    if legacy:
        assert not resumed["claimSummary"]["retained"]
        assert {claim["bead"] for claim in resumed["claimSummary"]["fresh"]} == {
            "A",
            "B",
            "integration",
        }
    assert (
        resumed["integrationValidation"]["integrationCommit"]
        == first["integrationValidation"]["integrationCommit"]
    )


def test_dispatch_cached_dependent_waits_for_ambiguous_dependency(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    manifest = json.loads((plan / "sample.json").read_text())
    manifest["planRevision"] = "c" * 64
    manifest["beads"]["A"]["requirementRefs"][1]["revision"] = "d" * 64
    (plan / "sample.json").write_text(json.dumps(manifest))

    def crash(path: Path) -> None:
        raise RuntimeError("crashed after re-dispatch request")

    run_dir = _run_dir(first)
    failed = run_plan(
        plan,
        work_root=tmp_path / "runs",
        resume_run_dir=run_dir,
        fake_workers={"A": crash, "B": lambda path: pytest.fail("B relaunched")},
    )
    assert failed["beads"]["B"]["status"] == "blocked: dependency A failed"
    ambiguous = run_plan(
        plan,
        work_root=tmp_path / "runs",
        resume_run_dir=run_dir,
        fake_workers={bead: lambda path: pytest.fail("worker launched") for bead in ("A", "B")},
    )
    assert ambiguous["beads"]["A"]["status"] == "ambiguous launch"
    assert ambiguous["beads"]["B"]["status"] == "blocked: dependency A failed"
    retried = run_plan(
        plan,
        work_root=tmp_path / "runs",
        resume_run_dir=run_dir,
        relaunch_ambiguous=True,
        mock_provider="reference",
        fake_workers={"B": lambda path: pytest.fail("B relaunched")},
    )
    assert retried["status"] == STATUS_MOCK_ONLY
    assert retried["beads"]["B"]["status"] == "reused"
    assert retried["beads"]["B"]["integrated"] == first["beads"]["B"]["integrated"]


def _compile_plan(plan: Path, spine: Spine) -> None:
    save_spine(spine, plan / "spine.json")
    (plan / "sample.json").write_text(json.dumps(compile_manifest(spine)), encoding="utf-8")


def _change_rounding(plan: Path) -> Spine:
    data = load_spine(plan / "spine.json").to_dict()
    next(node for node in data["nodes"] if node["id"] == "D3")["text"] = (
        "Round tip half down using integer cents."
    )
    spine = Spine(data)
    _compile_plan(plan, spine)
    proof = plan / "template/tests/test_money.py"
    proof.write_text(
        proof.read_text()
        .replace("+ 50", "+ 49")
        .replace("sum(split(1, 1, 50)), 2", "sum(split(1, 1, 50)), 1")
        .replace("(5, 2, 50, [4, 4])", "(5, 2, 50, [4, 3])"),
        encoding="utf-8",
    )
    reference = plan / "reference/tipsplit/money.py"
    reference.write_text(reference.read_text().replace("+ 50", "+ 49"), encoding="utf-8")
    return spine


@pytest.mark.parametrize("invalidate_b", [False, True])
def test_dispatch_resume_spine_change_updates_requirements_and_revalidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    invalidate_b: bool,
) -> None:
    plan = _plan(tmp_path)
    _compile_plan(plan, load_spine(plan / "spine.json"))
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    assert first["status"] == STATUS_MOCK_ONLY
    spine = _change_rounding(plan)
    if invalidate_b:
        proof = plan / "template/tests/test_cli.py"
        proof.write_text(
            proof.read_text() + "\nclass PlanUpdateProof(unittest.TestCase):\n"
            '    def test_new_label(self):\n        self.assertEqual(cli.PLAN_LABEL, "revised")\n',
            encoding="utf-8",
        )
        reference = plan / "reference/tipsplit/cli.py"
        reference.write_text(reference.read_text() + '\nPLAN_LABEL = "revised"\n', encoding="utf-8")
    proofs = []
    validate = dispatch._validate

    def observe(cwd, manifest, module, **kwargs):
        result = validate(cwd, manifest, module, **kwargs)
        proofs.append((module, _git(cwd, "rev-parse", "HEAD"), result["passed"]))
        return result

    monkeypatch.setattr(dispatch, "_validate", observe)
    if invalidate_b:
        main = runpy.run_path(str(FIXTURE.parents[1] / "scripts/run_plan.py"))["main"]
        assert (
            main(
                [
                    "--plan",
                    str(plan),
                    "--work-root",
                    str(tmp_path / "unused"),
                    "--resume",
                    str(_run_dir(first)),
                    "--mock-provider",
                    "reference",
                ]
            )
            == 1  # B's changed proof fails; unchanged spec refs forbid a provider relaunch.
        )
        output = capsys.readouterr().out
        decoder = json.JSONDecoder()
        headline, offset = decoder.raw_decode(output)
        summary = json.loads(output[offset:])
        resumed = json.loads((_run_dir(first) / "evidence/run.json").read_text())
        assert headline["claimSummary"] == resumed["claimSummary"]
        assert summary == {
            **resumed["resumeSummary"],
            "planUpdate": resumed["planUpdate"]["commit"],
        }
    else:
        resumed = run_plan(
            plan,
            work_root=tmp_path / "unused",
            resume_run_dir=_run_dir(first),
            mock_provider="reference",
            fake_workers={"B": lambda path: pytest.fail("unaffected B relaunched")},
        )
    assert resumed["status"] == (
        "plan-failed: B integrated validation failed" if invalidate_b else STATUS_MOCK_ONLY
    )
    assert resumed["isMocked"] and not resumed["didExecuteRealProvider"]
    update = resumed["planUpdate"]
    repo = Path(resumed["repo"])
    assert update["fromRevision"] == first["planRevision"]
    assert update["toRevision"] == spine.plan_revision()
    assert update["commit"]
    expected_paths = (
        ["tests/test_cli.py", "tests/test_money.py"] if invalidate_b else ["tests/test_money.py"]
    )
    assert update["paths"] == expected_paths
    assert (
        _git(repo, "diff", "--name-only", f"{update['commit']}^", update["commit"]).splitlines()
        == expected_paths
    )
    message = _git(repo, "log", "-1", "--format=%B", update["commit"])
    assert message.startswith(
        f"Plan revision {spine.plan_revision()[:12]}: update requirement files"
    )
    assert f"Harness-Plan-Revision: {spine.plan_revision()}" in message
    assert "Harness-Plan-Update: true" in message
    assert not (repo / "reference").exists()
    assert resumed["baseline"] == first["baseline"]
    a, b = resumed["beads"]["A"], resumed["beads"]["B"]
    assert a["attempt"]["baseCommit"] == update["commit"]
    assert a["guard"]["requirementFilesIntact"]
    assert a["specRefs"] == compile_manifest(spine)["beads"]["A"]["specRefs"]
    assert a["specRefs"] != first["beads"]["A"]["specRefs"]
    assert a["requirementRefs"] == first["beads"]["A"]["requirementRefs"]
    assert a["superseded"][-1]["status"] == "superseded"
    assert a["superseded"][-1]["specRefs"] == first["beads"]["A"]["specRefs"]
    assert all(json.dumps(ref, sort_keys=True) in a["prompt"] for ref in a["specRefs"])
    request = json.loads((Path(a["evidenceDir"]) / "attempt/request.json").read_text())
    assert request["specRefs"] == a["specRefs"]
    spec_hash = hashlib.sha256(
        json.dumps(a["specRefs"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert f"Harness-Spec-Revision: {spec_hash}" in _git(
        repo, "log", "-1", "--format=%B", a["integrated"]["beadCommit"]
    )
    assert b["specRefs"] == first["beads"]["B"]["specRefs"]
    final_head = _git(repo, "rev-parse", "HEAD")
    assert proofs[0] == ("test_money.py", final_head, True)
    assert proofs[1] == ("test_cli.py", final_head, not invalidate_b)
    assert proofs[-1] == ("test_end_to_end.py", final_head, True)
    assert resumed["integrationValidationHistory"] == [first["integrationValidation"]]
    if invalidate_b:
        assert b["status"] == "integrated validation failed"
        assert not b["revalidation"]["validation"]["passed"]
        assert b["attempt"] == first["beads"]["B"]["attempt"]
        assert resumed["resumeSummary"] == {
            "reused": ["B"],
            "redispatched": ["A"],
            "invalidated": ["B"],
            "retained": [],
        }
    else:
        assert b["status"] == "reused" and b["revalidation"]["validation"]["passed"]
        assert b["attempt"] == first["beads"]["B"]["attempt"]
        assert resumed["resumeSummary"] == {
            "reused": ["B"],
            "redispatched": ["A"],
            "invalidated": [],
            "retained": [],
        }
        entry = next(entry for entry in resumed["requirementValidation"] if entry["bead"] == "B")
        assert entry["candidateCommit"] == final_head
        assert entry["planRevision"] == spine.plan_revision()


def test_dispatch_plan_update_copies_only_current_requirement_files(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    repo = tmp_path / "repo"
    shutil.copytree(plan / "template", repo)
    _git(repo, "init", "-q", "-b", "integration")
    _git(repo, "add", "-A")
    dispatch._commit(repo, "baseline")
    original_money = (repo / "tipsplit/money.py").read_bytes()
    (plan / "template/tipsplit/money.py").write_text("unowned template update\n")
    (plan / "template/tests/test_cli.py").unlink()
    (plan / "template/tests/new_proof.py").write_text("# new protected proof\n")
    update = dispatch._update_requirement_files(repo, load_plan(plan), "b" * 64)
    assert update["paths"] == ["tests/new_proof.py", "tests/test_cli.py"]
    assert not (repo / "tests/test_cli.py").exists()
    assert (repo / "tests/new_proof.py").read_text() == "# new protected proof\n"
    assert (repo / "tipsplit/money.py").read_bytes() == original_money
    assert not (repo / "reference").exists()
    assert _git(repo, "status", "--porcelain") == ""
    repeated = dispatch._update_requirement_files(repo, load_plan(plan), "b" * 64)
    assert repeated["commit"] is None and repeated["paths"] == []


def test_dispatch_plan_update_preserves_worker_requirement_guard(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    _compile_plan(plan, load_spine(plan / "spine.json"))
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    _change_rounding(plan)

    def edit_protected_proof(worktree: Path) -> None:
        proof = worktree / "tests/test_money.py"
        assert proof.read_bytes() == (plan / "template/tests/test_money.py").read_bytes()
        _reference(plan, "A")(worktree)
        proof.write_text("# worker bypasses new requirement\n")

    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
        fake_workers={"A": edit_protected_proof},
    )
    assert resumed["beads"]["A"]["status"] == "rejected: requirement files changed"
    assert not resumed["beads"]["A"]["guard"]["requirementFilesIntact"]
    assert _git(Path(resumed["repo"]), "rev-parse", "HEAD") == resumed["planUpdate"]["commit"]


@pytest.mark.parametrize("invalidates_a", [False, True])
def test_dispatch_test_only_revision_revalidates_before_redispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalidates_a: bool
) -> None:
    plan = _plan(tmp_path)
    spine = load_spine(plan / "spine.json")
    (plan / "sample.json").write_text(json.dumps(compile_manifest(spine, plan / "template")))
    first = run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    proof = plan / "template/tests/test_money.py"
    addition = (
        "\nclass RevisedRequirement(unittest.TestCase):\n"
        "    def test_revision(self):\n"
        "        from tipsplit import money\n"
        '        self.assertEqual(money.PLAN_LABEL, "revised")\n'
        if invalidates_a
        else "\n# Requirement clarification\n"
    )
    proof.write_text(proof.read_text() + addition, encoding="utf-8")
    if invalidates_a:
        reference = plan / "reference/tipsplit/money.py"
        reference.write_text(reference.read_text() + '\nPLAN_LABEL = "revised"\n', encoding="utf-8")
    revised = compile_manifest(spine, plan / "template")
    (plan / "sample.json").write_text(json.dumps(revised))
    assert revised["beads"] == compile_manifest(spine)["beads"]
    proofs = []
    validate = dispatch._validate

    def observe(cwd, manifest, module, **kwargs):
        result = validate(cwd, manifest, module, **kwargs)
        proofs.append((module, _git(cwd, "rev-parse", "HEAD"), result["passed"]))
        return result

    monkeypatch.setattr(dispatch, "_validate", observe)
    fake_workers = {"B": lambda path: pytest.fail("B relaunched")}
    if not invalidates_a:
        fake_workers["A"] = lambda path: pytest.fail("A relaunched")
    resumed = run_plan(
        plan,
        work_root=tmp_path / "unused",
        resume_run_dir=_run_dir(first),
        mock_provider="reference",
        fake_workers=fake_workers,
    )
    assert resumed["status"] == (
        "plan-failed: A integrated validation failed" if invalidates_a else STATUS_MOCK_ONLY
    )
    update = resumed["planUpdate"]
    assert update["commit"] and update["paths"] == ["tests/test_money.py"]
    assert update["fromRevision"] == first["planRevision"]
    assert update["toRevision"] == revised["planRevision"]
    assert proofs[0] == ("test_money.py", update["commit"], not invalidates_a)
    assert resumed["beads"]["B"]["attempt"] == first["beads"]["B"]["attempt"]
    if invalidates_a:
        assert resumed["resumeSummary"] == {
            "reused": ["A"],
            "redispatched": [],
            "invalidated": ["A"],
            "retained": [],
        }
        assert resumed["beads"]["B"]["status"] == "blocked: dependency A failed"
        assert resumed["beads"]["A"]["attempt"] == first["beads"]["A"]["attempt"]
        assert [proof[0] for proof in proofs] == ["test_money.py", "test_end_to_end.py"]
    else:
        assert resumed["beads"]["B"]["status"] == "reused"
        assert proofs[1] == ("test_cli.py", update["commit"], True)
        assert resumed["resumeSummary"] == {
            "reused": ["A", "B"],
            "redispatched": [],
            "invalidated": [],
            "retained": [],
        }
        assert resumed["beads"]["A"]["attempt"] == first["beads"]["A"]["attempt"]
    assert proofs[-1][0] == "test_end_to_end.py" and proofs[-1][2]
    assert not resumed["didExecuteRealProvider"]
