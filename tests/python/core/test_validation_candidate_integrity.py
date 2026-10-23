"""Real source-process validation must describe the committed candidate it advances."""

import json
from pathlib import Path

import pytest
from tests.python.core import test_runners

from codex_harness.core.dispatch import STATUS_MOCK_ONLY, _git, run_plan
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
        "commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()\n"
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
