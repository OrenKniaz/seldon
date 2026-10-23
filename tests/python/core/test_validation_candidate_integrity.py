"""Real source-process validation must describe the committed candidate it advances."""

import json
from pathlib import Path

import pytest
from tests.python.core import test_runners

from codex_harness.core.dispatch import STATUS_MOCK_ONLY, _commit, _git, _validate, run_plan
from codex_harness.core.runners import validate_proof
from codex_harness.core.spine import compile_manifest

command_plan = test_runners.command_plan


@pytest.mark.parametrize("mutate", [False, True], ids=["clean-control", "mutating-zero-exit"])
def test_canonical_integration_refuses_validation_time_candidate_mutation(
    command_plan, tmp_path, mutate
):
    """Catch PASS/merge for committed bytes the validator changed before checking.

    Existing dispatch checks observe clean candidates and pre-validation worker
    tampering; they miss a real zero-exit command changing source during validation.
    """
    root, spine, _ = command_plan
    driver = root / "template/proofs/check_value.py"
    driver.write_text(
        "import json, subprocess, sys\n"
        "from pathlib import Path\n"
        "root = Path(__file__).resolve().parent.parent\n"
        "value = root / 'value.txt'\n"
        "before = value.read_text().strip()\n"
        "candidate = root.name.startswith('candidate-')\n"
        f"mutate = {mutate!r}\n"
        "if candidate and mutate:\n"
        "    value.write_text('2\\n')\n"
        "after = value.read_text().strip()\n"
        "expected = '2' if candidate and mutate else Path(sys.argv[1]).read_text().strip()\n"
        "commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], "
        "cwd=root, text=True).strip()\n"
        "print(json.dumps(dict(root=str(root), candidate=candidate, before=before, after=after, "
        "expected=expected, commit=commit)))\n"
        "sys.exit(0 if after == expected else 1)\n",
        encoding="utf-8",
    )
    manifest = compile_manifest(spine, root / "template")
    (root / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")
    # The canonical reference stand-in copies value=1; no provider/model runs.
    record = run_plan(root, work_root=tmp_path / "runs", mock_provider="reference")
    bead = record["beads"]["A"]
    integrated = bead["integrated"]
    validation = integrated["validation"]
    observed = json.loads(validation["output"].strip())
    candidate = Path(integrated["root"])
    repo = Path(record["repo"])
    commit = integrated["integrationCommit"]
    receipt = json.loads(Path(integrated["receiptPath"]).read_text())
    evidence = dict(
        simulatedWorker=bead["isMocked"],
        didRunProvider=bead["didRunProvider"],
        planRevision=record["planRevision"],
        candidateCommit=commit,
        candidateTree=bead["candidate"]["tree"],
        validator=observed,
        exitCode=validation["exitCode"],
        passed=validation["passed"],
        claim=receipt["claim"],
        integrationHead=_git(repo, "rev-parse", "HEAD"),
        integrationBase=integrated["recoveryBinding"]["baseIntegration"],
        committedValue=_git(candidate, "show", f"{commit}:value.txt"),
        candidateWorkingValue=(candidate / "value.txt").read_text().strip(),
        candidateDirty=_git(candidate, "status", "--porcelain"),
        integratedWorkingValue=(repo / "value.txt").read_text().strip(),
    )
    (tmp_path / "validation-integrity-evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2))
    assert bead["isMocked"] and not bead["didRunProvider"]
    assert observed["candidate"] and observed["commit"] == commit
    assert observed["before"] == evidence["committedValue"] == "1"
    assert validation["exitCode"] == 0  # Preserve the actual child outcome, even on refusal.
    if mutate:
        assert observed["after"] == observed["expected"] == "2"
        assert not validation["passed"], (
            "Zero-exit source mutation must not pass candidate validation"
        )
        assert not receipt["claim"]["passed"]
        assert not bead["candidate"]["validated"]
        assert evidence["integrationHead"] == evidence["integrationBase"]
    else:
        assert observed["after"] == "1" and validation["passed"]
        assert receipt["claim"]["passed"] and bead["candidate"]["validated"]
        assert evidence["integrationHead"] == commit
        assert evidence["integratedWorkingValue"] == "1" and not evidence["candidateDirty"]
        assert record["status"] == STATUS_MOCK_ONLY


@pytest.mark.parametrize("stage", ["final", "revalidation"])
def test_real_later_validation_mutation_cannot_create_a_passing_claim(
    command_plan, tmp_path, stage
):
    # Integration-only evidence misses the separate final/reused consumers. Their
    # real zero-exit validators must also refuse claims after changing tracked bytes.
    root, spine, _ = command_plan
    driver = root / "template/proofs/check_value.py"
    driver.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "root=Path(__file__).resolve().parent.parent\nproof=Path(sys.argv[1])\n"
        "value=root/'value.txt'\nbefore=value.read_text().strip()\n"
        f"stage={stage!r}\n"
        "mutate=root.name=='repo' and ((stage=='final' and proof.name=='integration.proof') "
        "or (stage=='revalidation' and 'mutate' in proof.read_text()))\n"
        "if mutate: value.write_text('2\\n')\n"
        "after=value.read_text().strip()\n"
        "expected='2' if mutate else proof.read_text().splitlines()[0]\n"
        "print(json.dumps(dict(before=before,after=after,mutate=mutate)))\n"
        "sys.exit(0 if after==expected else 1)\n",
        encoding="utf-8",
    )
    (root / "sample.json").write_text(
        json.dumps(compile_manifest(spine, root / "template")), encoding="utf-8"
    )
    first = run_plan(root, work_root=tmp_path / "runs", mock_provider="reference")
    if stage == "revalidation":
        assert first["status"] == STATUS_MOCK_ONLY
        (root / "template/proofs/unit.proof").write_text("1\nmutate\n", encoding="utf-8")
        (root / "sample.json").write_text(
            json.dumps(compile_manifest(spine, root / "template")), encoding="utf-8"
        )
        record = run_plan(
            root,
            work_root=tmp_path / "unused",
            mock_provider="reference",
            resume_run_dir=tmp_path / "runs" / first["runId"],
        )
        proof = record["beads"]["A"]["revalidation"]
        validation, claim = proof["validation"], proof["claim"]
    else:
        record = first
        validation = record["integrationValidation"]
        claim = validation["claim"]
    assert json.loads(validation["output"])["mutate"] is True
    assert validation["exitCode"] == 0
    assert validation["inputIntegrity"]["passed"] is False
    assert validation["passed"] is claim["passed"] is False
    assert record["status"].startswith("plan-failed")
    assert _git(Path(record["repo"]), "show", "HEAD:value.txt") == "1"


@pytest.mark.parametrize(
    "change_head", [False, True], ids=["dirty-input-and-output", "head-change"]
)
def test_committed_validation_refuses_dirty_preimage_or_validator_head_change(
    command_plan, change_head
):
    # Standalone precommit author checks may inspect dirty bytes. Dispatch claims
    # must describe committed bytes; byte-only checks also miss a changed HEAD.
    root, spine, manifest = command_plan
    repo = root / "template"
    driver = repo / "proofs/check_value.py"
    if change_head:
        driver.write_text(
            "import subprocess\n"
            "subprocess.run(['git','-c','user.name=Source Test','-c',"
            "'user.email=source@example.invalid','commit','--allow-empty','-m',"
            "'Validator moved HEAD'],check=True)\n",
            encoding="utf-8",
        )
        manifest = compile_manifest(spine, repo)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _commit(repo, "Source validation preimage")
    (repo / "value.txt").write_text("1\n", encoding="utf-8")
    before = _git(repo, "rev-parse", "HEAD")
    if not change_head:
        authored = validate_proof(repo, manifest, "proofs/unit.proof", owned_files=("value.txt",))
        assert authored["passed"] is True and authored["exitCode"] == 0
    else:
        _git(repo, "add", "value.txt")
        _commit(repo, "Commit authored input before HEAD mutation")
    result = _validate(repo, manifest, "proofs/unit.proof", owned_files=("value.txt",))
    assert result["exitCode"] == 0
    assert result["passed"] is False
    assert result["inputIntegrity"]["passed"] is False
    if not change_head:
        assert result["inputIntegrity"]["before"] == result["inputIntegrity"]["after"]
        assert result["inputIntegrity"]["matchesCommit"] is False
        assert _git(repo, "rev-parse", "HEAD") == before
        assert _git(repo, "show", "HEAD:value.txt") == "0"
        assert (repo / "value.txt").read_text().strip() == "1"
        assert (repo / ".harness/report.txt").read_text() == "PASS"
        assert _git(repo, "diff", "--name-only") == "value.txt"
