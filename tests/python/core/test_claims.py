"""Candidate-bound claim reuse with stand-ins; providers must never launch."""

import json
import shutil
from pathlib import Path

import pytest

from codex_harness.core import dispatch
from codex_harness.core.graph import PlanError, load_plan
from codex_harness.core.spine import Spine, compile_manifest, load_spine

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(dispatch, "get_adapter", lambda *args: pytest.fail("real provider launch"))


def _plan(tmp_path, *, stubbed=True):
    plan = tmp_path / "plan"
    shutil.copytree(FIXTURE, plan)
    spine = load_spine(plan / "spine.json")
    if stubbed:
        # Unit execution imports no A code: patched wrappers import money only
        # when the integration proof calls them for real.
        source = plan / "reference/tipsplit/cli.py"
        source.write_text(
            source.read_text().replace(
                "from tipsplit.money import parse_amount, split",
                "def parse_amount(value):\n"
                "    from tipsplit.money import parse_amount as real\n"
                "    return real(value)\n\n"
                "def split(total, people, tip):\n"
                "    from tipsplit.money import split as real\n"
                "    return real(total, people, tip)",
            ),
            encoding="utf-8",
        )
    _manifest(plan, spine, stubbed=stubbed)
    return plan, spine


def _manifest(plan, spine, *, stubbed=True):
    manifest = compile_manifest(spine, plan / "template")
    # Explicit exercised-dependency rule: test_cli patches money calls, so
    # proofDependsOn=[] binds no A code. Absent this field the transitive graph
    # rule is conservative and binds A, regardless of our knowledge of the stub.
    if stubbed:
        manifest["beads"]["B"]["proofDependsOn"] = []
    (plan / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")


def _latest(record, bead):
    return next(claim for claim in reversed(record["claims"]) if claim["bead"] == bead)


def _resume(plan, first, **kwargs):
    return dispatch.run_plan(
        plan,
        work_root=Path(first["repo"]).parent.parent,
        resume_run_dir=Path(first["repo"]).parent,
        **kwargs,
    )


def test_core_claims_d3_resume_reproves_changed_verification_without_b_provider(tmp_path):
    plan, spine = _plan(tmp_path)
    first = dispatch.run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    data = spine.to_dict()
    next(node for node in data["nodes"] if node["id"] == "D3")["text"] = (
        "Round tip half down using integer cents."
    )
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
    _manifest(plan, Spine(data))
    resumed = _resume(
        plan,
        first,
        mock_provider="reference",
        fake_workers={"B": lambda path: pytest.fail("B provider relaunched")},
    )
    assert resumed["status"] == dispatch.STATUS_MOCK_ONLY
    assert resumed["beads"]["B"]["attempt"] == first["beads"]["B"]["attempt"]
    old_a, a = _latest(first, "A"), _latest(resumed, "A")
    old_b, b = _latest(first, "B"), _latest(resumed, "B")
    assert old_a["sourceIdentity"] != a["sourceIdentity"]
    assert b["sourceIdentity"] == old_b["sourceIdentity"]
    assert b["dependencyIdentities"] == {}
    assert b["verificationIdentity"] != old_b["verificationIdentity"]
    summary = resumed["claimSummary"]
    impacted = {item["claimId"]: item["changedInputs"] for item in summary["impacted"]}
    assert set(impacted[old_b["claimId"]]) == {"verificationIdentity"}
    old_integration = _latest(first, "integration")
    changes = impacted[old_integration["claimId"]]
    assert changes["dependencyIdentities"]["previous"]["A"] == old_a["sourceIdentity"]
    assert changes["dependencyIdentities"]["current"]["A"] == a["sourceIdentity"]
    assert not summary["retained"]
    assert {claim["bead"] for claim in summary["fresh"]} == {"A", "B", "integration"}
    head = dispatch._git(Path(resumed["repo"]), "rev-parse", "HEAD")
    tree = dispatch._git(Path(resumed["repo"]), "rev-parse", "HEAD^{tree}")
    assert all(claim["passed"] for claim in summary["fresh"])
    assert all(claim["candidateCommit"] == head for claim in summary["fresh"])
    assert all(claim["candidateTree"] == tree for claim in summary["fresh"])
    assert json.loads((Path(resumed["repo"]).parent / "evidence/run.json").read_text()) == resumed


@pytest.mark.parametrize("stubbed", [True, False])
def test_core_claims_dependency_only_change_obeys_declared_proof_dependencies(tmp_path, stubbed):
    plan, _ = _plan(tmp_path, stubbed=stubbed)
    first = dispatch.run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    repo = Path(first["repo"])
    source = repo / "tipsplit/money.py"
    source.write_text(source.read_text() + "\n# Dependency-only code change\n", encoding="utf-8")
    dispatch._git(repo, "add", "--", "tipsplit/money.py")
    dispatch._commit(repo, "Change A only; verification unchanged")
    resumed = _resume(
        plan,
        first,
        fake_workers={bead: lambda path: pytest.fail("provider launched") for bead in ("A", "B")},
    )
    assert resumed["status"] == dispatch.STATUS_MOCK_ONLY
    old_b, b = _latest(first, "B"), _latest(resumed, "B")
    assert old_b["verificationIdentity"] == b["verificationIdentity"]
    assert old_b["sourceIdentity"] == b["sourceIdentity"]
    summary = resumed["claimSummary"]
    if stubbed:
        assert b == old_b
        retained = next(item for item in summary["retained"] if item["claimId"] == b["claimId"])
        assert all(
            key in retained["why"]
            for key in (
                "verificationIdentity",
                "sourceIdentity",
                "dependencyIdentities",
            )
        )
    else:
        assert b["claimId"] != old_b["claimId"]
        assert set(b["dependencyIdentities"]) == {"A"}
        changed = next(item for item in summary["impacted"] if item["claimId"] == old_b["claimId"])
        assert set(changed["changedInputs"]) == {"dependencyIdentities"}
    assert _latest(resumed, "integration")["claimId"] != _latest(first, "integration")["claimId"]
    assert {claim["bead"] for claim in summary["fresh"]} == (
        {"A", "integration"} if stubbed else {"A", "B", "integration"}
    )
    assert all(
        resumed["beads"][bead]["attempt"] == first["beads"][bead]["attempt"] for bead in ("A", "B")
    )


def test_core_claims_identities_normalize_crlf_and_bind_content_selection(tmp_path):
    plan, _ = _plan(tmp_path)
    repo = tmp_path / "repo"
    shutil.copytree(plan / "template", repo)
    dispatch._git(repo, "init", "-q", "-b", "integration")
    dispatch._git(repo, "config", "core.autocrlf", "false")
    # Names are exact hash inputs too; the text _git helper strips whitespace.
    (repo / " spaced.py").write_text("VALUE = 1\n")
    dispatch._git(repo, "add", "-A")
    lf = dispatch._commit(repo, "LF")
    loaded = load_plan(plan)
    before = dispatch._claim_inputs(repo, loaded, "B", lf)
    spaced = dispatch._content_identity(repo, lf, [" spaced.py"])
    for path in repo.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    dispatch._git(repo, "add", "-A")
    crlf = dispatch._commit(repo, "CRLF")
    assert lf != crlf
    assert before == dispatch._claim_inputs(repo, loaded, "B", crlf)
    assert spaced == dispatch._content_identity(repo, crlf, [" spaced.py"])
    assert dispatch._content_identity(repo, crlf, ["absent.py"]) != dispatch._content_identity(
        repo,
        crlf,
        [],
    )
    # Owned working files do not change identities until committed.
    (repo / "tipsplit/cli.py").write_text("uncommitted change\n", encoding="utf-8")
    assert before == dispatch._claim_inputs(repo, loaded, "B", crlf)


def test_core_claims_reject_unknown_exercised_dependency(tmp_path):
    plan, _ = _plan(tmp_path)
    manifest = json.loads((plan / "sample.json").read_text())
    manifest["beads"]["B"]["proofDependsOn"] = ["unknown"]
    (plan / "sample.json").write_text(json.dumps(manifest))
    with pytest.raises(PlanError, match="transitive prerequisites"):
        load_plan(plan)


def test_core_claims_failed_reproof_is_never_retained_or_used_to_relaunch_provider(tmp_path):
    plan, spine = _plan(tmp_path)
    first = dispatch.run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    proof = plan / "template/tests/test_cli.py"
    proof.write_text(
        proof.read_text() + "\nclass ChangedProof(unittest.TestCase):\n"
        "    def test_missing_feature(self):\n        self.assertTrue(cli.MISSING_FEATURE)\n",
        encoding="utf-8",
    )
    _manifest(plan, spine)
    forbidden = {bead: lambda path: pytest.fail("provider launched") for bead in ("A", "B")}
    failed = _resume(plan, first, fake_workers=forbidden)
    assert failed["status"] == "plan-failed: B integrated validation failed"
    old_failed = _latest(failed, "B")
    assert not old_failed["passed"]
    retry = _resume(plan, failed, fake_workers=forbidden)
    assert retry["status"] == failed["status"]
    new_failed = _latest(retry, "B")
    assert new_failed["claimId"] != old_failed["claimId"] and not new_failed["passed"]
    changed = next(
        item["changedInputs"]
        for item in retry["claimSummary"]["impacted"]
        if item["claimId"] == old_failed["claimId"]
    )
    assert set(changed) == {"passed"}
    assert all(
        retry["beads"][bead]["attempt"] == first["beads"][bead]["attempt"] for bead in ("A", "B")
    )


def test_core_claims_later_owner_cannot_leave_a_stale_passing_assertion(tmp_path):
    plan = tmp_path / "plan"
    template = plan / "template"
    (template / "tests").mkdir(parents=True)
    (template / "shared.py").write_text("VALUE = 0\n")
    beads = {}
    for bead_id, value in (("A", 1), ("B", 2)):
        (template / "tests" / f"test_{bead_id}.py").write_text(
            "import unittest\nfrom shared import VALUE\n"
            "class Proof(unittest.TestCase):\n"
            f"    def test_value(self):\n        self.assertEqual(VALUE, {value})\n"
        )
        beads[bead_id] = {
            "title": bead_id,
            "ownedFiles": ["shared.py"],
            "requirements": [f"R{bead_id}"],
            "proofModule": f"test_{bead_id}.py",
            "dependsOn": ["A"] if bead_id == "B" else [],
        }
    (plan / "sample.json").write_text(
        json.dumps(
            {
                "sample": "overlapping-owner",
                "requirementFiles": ["tests"],
                "beads": beads,
                "validation": ["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
            }
        )
    )

    def worker(value):
        def write(root):
            (root / "shared.py").write_text(f"VALUE = {value}\n")

        return write

    record = dispatch.run_plan(
        plan,
        work_root=tmp_path / "runs",
        fake_workers={"A": worker(1), "B": worker(2)},
    )
    assert record["status"] == "plan-failed: A integrated validation failed"
    assert [claim["passed"] for claim in record["claims"] if claim["bead"] == "A"] == [True, False]
    assert _latest(record, "A")["candidateCommit"] == dispatch._git(
        Path(record["repo"]),
        "rev-parse",
        "HEAD",
    )
