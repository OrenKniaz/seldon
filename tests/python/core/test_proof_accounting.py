"""Synthetic local accounting regressions; no actual provider or acceptance proof."""

import hashlib
import json
from pathlib import Path

import pytest
from tests.python.core.test_cockpit_projection import node, publish_local_fixture

from codex_harness.core.dispatch import _commit, _git, run_plan
from codex_harness.core.plan_store import PlanStore
from codex_harness.core.proof_accounting import observe, plan_context
from codex_harness.core.record_io import read_record_json, write_record_json
from codex_harness.core.status_api import snapshot


def test_unpublished_requirements_and_self_claim_do_not_create_proof(tmp_path):
    workspace = tmp_path / "planner"
    workspace.mkdir()
    with PlanStore(workspace) as store:
        store.upsert_node(node("V1", "vision", []))
        store.upsert_node(node("F1", "feature", ["V1"]))
        store.upsert_node(node("R1", "requirement", ["F1"]))
        revision = store.spine().plan_revision()
    run_root = tmp_path / "runs" / "synthetic"
    (run_root / "evidence").mkdir(parents=True)
    write_record_json(
        run_root / "evidence" / "run.json",
        {
            "runId": "synthetic",
            "planRevision": revision,
            "status": "validated",
            "didExecuteRealProvider": True,
            "isMocked": False,
            "claims": [{"bead": "A", "claimId": "self-claim", "passed": True}],
            "beads": {},
        },
    )
    report = snapshot(workspace=workspace, runs_root=tmp_path / "runs")
    accounting = report["cockpit"]["vision"]["proofAccounting"]
    assert accounting["requirements"] is None
    assert accounting["source"]["planRevision"] == revision
    assert accounting["source"]["publicationRevision"] is None
    assert accounting["source"]["candidateCommit"] is None
    assert "publication-unavailable" in {x["code"] for x in accounting["missingDependencies"]}
    assert report["cockpit"]["vision"]["proof"]["requirementValidation"] is None


@pytest.fixture
def local_claim(tmp_path):
    """Real canonical writers, local validation and fake worker, explicitly synthetic."""
    workspace = tmp_path / "p"
    workspace.mkdir()
    with PlanStore(workspace) as store:
        publication = publish_local_fixture(store, tmp_path)
        manifest = store.published_manifest(publication)
        context = plan_context(store)
    (workspace / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")

    def local_worker(worktree):
        (worktree / "value.py").write_text("def value(n): return n + 1\n", encoding="utf-8")

    record = run_plan(workspace, work_root=tmp_path / "r", fake_workers={"A": local_worker})
    assert record["isMocked"] is True
    assert record["beads"]["A"]["integrated"]["validation"]["passed"] is True
    run_root = Path(record["repo"]).parent
    path = run_root / "evidence" / "run.json"
    return workspace, context, record, run_root, path


def accounting(fixture):
    _, context, _, root, path = fixture
    return observe(context, run_path=path, runs_root=root.parent, publication_current=True)


def bead_claim(record):
    return next(c for c in reversed(record["claims"]) if c["bead"] == "A")


def test_real_mock_claim(local_claim):
    observed = accounting(local_claim)
    row = observed["requirements"][0]
    assert row["id"] == "R1" and row["beadIds"] == ["A"]
    assert row["bindingState"] == "unavailable"
    assert row["claimObservations"][0]["currentInputsState"] == "current"
    codes = {x["code"] for x in observed["missingDependencies"]}
    assert "actual-worker-proof-unavailable" in codes
    assert "mock-or-unproved-runtime" in codes
    assert "claim-binding-unproved" not in codes
    assert observed["source"]["candidateCommit"] != "2466c25955900c0d43dca702149a1c83bfd9dc1a"
    workspace, _, record, root, _ = local_claim
    projection = snapshot(workspace=workspace, runs_root=root.parent)["cockpit"]["vision"]
    assert projection["proofAccounting"] == observed
    assert projection["proof"]["requirementValidation"] is None
    assert projection["proofAccounting"]["requirements"][0]["claimIds"] == [
        bead_claim(record)["claimId"]
    ]


def test_provider_shaped_legacy_cannot_get_local_provenance_eligibility(local_claim):
    """Mutable labels on a source fixture cannot create a Popen original."""
    _, _, record, _, path = local_claim
    bead = record["beads"]["A"]
    directory = Path(bead["evidenceDir"]) / "attempt"
    for name in ("request.json", "result.json"):
        value = read_record_json(directory / name)
        value.update(mode="provider-normal", isMocked=False, didRunProvider=True)
        write_record_json(directory / name, value)
    bead.update(isMocked=False, didRunProvider=True)
    bead["attempt"].update(mode="provider-normal", isMocked=False, didRunProvider=True)
    record["isMocked"] = False
    write_record_json(path, record)
    observed = accounting(local_claim)
    claim = observed["requirements"][0]["claimObservations"][0]
    assert claim["currentInputsState"] == "current"  # Preserve actual source correspondence.
    assert claim["attemptEligible"] is False
    assert claim["localProcessProvenance"]["code"] == "attempt-provenance-unavailable"
    assert "attempt-provenance-unavailable" in {x["code"] for x in observed["missingDependencies"]}
    assert observed["requirements"][0]["bindingState"] == "unavailable"


@pytest.mark.parametrize("change", ["unrelated", "source", "tests", "dependency"])
def test_later_candidate(local_claim, change):
    _, context, record, root, _ = local_claim
    repo = root / "repo"
    old = bead_claim(record)
    if change == "unrelated":
        (repo / "notes.txt").write_text("unrelated local fixture commit\n")
    elif change == "source":
        (repo / "value.py").write_text("def value(n): return n + 2\n")
    elif change == "tests":
        with (repo / "tests" / "test_value.py").open("a") as stream:
            stream.write("# protected verification changed\n")
    else:
        # Add a real prerequisite source to the manifest graph and both candidate commits.
        # Its original claim cannot acquire a dependency retroactively.
        manifest = context["published"]["manifest"]
        manifest["beads"]["A"]["dependsOn"] = ["D"]
        manifest["beads"]["D"] = {
            **manifest["beads"]["A"],
            "dependsOn": [],
            "ownedFiles": ["dependency.py"],
        }
        (repo / "dependency.py").write_text("VALUE = 2\n")
    _git(repo, "add", ".")
    _commit(repo, "Synthetic later candidate")
    observed = accounting(local_claim)
    claim = observed["requirements"][0]["claimObservations"][0]
    assert claim["sourceCandidateCommit"] == old["candidateCommit"]
    assert claim["sourceCandidateTree"] == old["candidateTree"]
    assert observed["source"]["candidateCommit"] != old["candidateCommit"]
    if change == "unrelated":
        assert claim["currentInputsState"] == "current"
        assert observed["requirements"][0]["bindingState"] == "unavailable"
    else:
        assert claim["currentInputsState"] == "invalidated"
        assert observed["requirements"][0]["bindingState"] == "invalidated"


@pytest.mark.parametrize(
    "change", ["claim-id", "tree", "receipt", "spec", "missing", "outside", "duplicate"]
)
def test_original_refusals(local_claim, change, tmp_path):
    _, _, record, root, path = local_claim
    bead = record["beads"]["A"]
    claim = bead_claim(record)
    receipt_path = Path(bead["evidenceDir"]) / "integrated.json"
    if change == "claim-id":
        claim["claimId"] = "forged"
    elif change == "tree":
        claim["candidateTree"] = "f" * 40
        body = {k: v for k, v in claim.items() if k != "claimId"}
        claim["claimId"] = (
            "A:"
            + hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
    elif change == "receipt":
        # Deserialize independent bytes: producer objects may alias the selected claim.
        receipt = read_record_json(receipt_path)
        receipt["candidate"]["validated"] = False
        write_record_json(receipt_path, receipt)
        assert bead["integrated"]["claim"] == claim == receipt["claim"]
    elif change == "spec":
        bead["integrated"]["specRefs"][0]["revision"] = "e" * 64
        write_record_json(receipt_path, bead["integrated"])
    elif change == "missing":
        receipt_path.unlink()
    elif change == "outside":
        bead["evidenceDir"] = str(tmp_path / "outside")
    else:
        record["claims"].append({**claim, "passed": False})
    write_record_json(path, record)
    observed = accounting(local_claim)
    assert observed["requirements"][0]["bindingState"] != "bound-evidence"
    assert {x["code"] for x in observed["missingDependencies"]} & {
        "claim-binding-unproved",
        "run-candidate-binding-unavailable",
    }
    assert observed["reason"]
    if change == "receipt":
        observation = observed["requirements"][0]["claimObservations"][0]
        assert observation["currentInputsState"] == "invalidated"
        assert observation["reasons"] == ["claim-original-receipt-mismatch"]


def test_editable_and_stale(local_claim):
    workspace, _, _, root, path = local_claim
    with PlanStore(workspace) as store:
        before = store.spine().revision("R1")
        changed = store.spine().node("R1")
        changed["text"] = "Changed requirement, unchanged ID"
        store.upsert_node(changed)
        context = plan_context(store)
    observed = observe(context, run_path=path, runs_root=root.parent, publication_current=False)
    assert observed["source"]["planRevision"] != observed["source"]["publicationRevision"]
    assert observed["requirements"][0]["revision"] == before
    assert observed["requirements"][0]["bindingState"] == "invalidated"
    assert "requirement-revision-changed" in observed["requirements"][0]["reasons"]


def test_duplicate_claims_unique(local_claim):
    _, _, record, _, path = local_claim
    record["claims"].append(dict(bead_claim(record)))
    write_record_json(path, record)
    row = accounting(local_claim)["requirements"][0]
    assert len(row["claimIds"]) == 1 and row["beadIds"] == ["A"]


def test_missing_plan_and_escape(tmp_path):
    assert observe(None)["requirements"] is None
    assert observe(None)["source"]["candidateTree"] is None


def test_latest_failed_claim(local_claim):
    _, _, record, _, path = local_claim
    failed = {**bead_claim(record), "passed": False}
    failed.pop("claimId")
    failed["claimId"] = (
        "A:"
        + hashlib.sha256(
            json.dumps(failed, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    record["claims"].append(failed)
    write_record_json(path, record)
    row = accounting(local_claim)["requirements"][0]
    assert row["claimIds"] == [failed["claimId"]]
    assert row["bindingState"] == "invalidated"
    assert row["claimObservations"][0]["currentInputsState"] == "invalidated"
    assert "claim-recorded-validation-failed" in row["reasons"]


@pytest.mark.parametrize("changed_input", ["verificationIdentity", "sourceIdentity"])
def test_recorded_invalidation(local_claim, changed_input):
    """Matching bytes must not discard an authoritative invalidation of this claim."""
    _, _, record, _, path = local_claim
    claim = bead_claim(record)
    assert (
        accounting(local_claim)["requirements"][0]["claimObservations"][0]["currentInputsState"]
        == "current"
    )
    disposition = {
        "claimId": claim["claimId"],
        "changedInputs": {changed_input: "recorded-invalidation"},
    }
    record["claimSummary"]["impacted"].append(disposition)
    write_record_json(path, record)
    row = accounting(local_claim)["requirements"][0]
    observation = row["claimObservations"][0]
    assert row["claimIds"] == [claim["claimId"]]
    assert observation["currentInputsState"] == "invalidated"
    assert row["bindingState"] == "invalidated"
    assert "recorded-invalidation-conflicts-with-matching-current-inputs" in row["reasons"]
    assert (
        "recorded-claim-invalidation: " + json.dumps(disposition, sort_keys=True)
        in observation["reasons"]
    )
    assert str(path) in observation["evidenceRefs"]


def test_fresh_replaces_impacted(local_claim):
    """A canonical new local claim/receipt must not inherit an older claim's disposition."""
    from codex_harness.core.dispatch import _claim, _validate

    _, context, record, root, path = local_claim
    old = bead_claim(record)
    record["claimSummary"]["impacted"].append(
        {"claimId": old["claimId"], "changedInputs": {"verificationIdentity": "old-invalidation"}}
    )
    manifest = context["published"]["manifest"]
    repo = root / "repo"
    fresh = _claim(repo, observe_graph(context, repo), "A", old["candidateCommit"], True)
    assert fresh["claimId"] != old["claimId"]
    validation = _validate(repo, manifest, manifest["beads"]["A"]["proofModule"])
    assert validation["passed"] is True
    bead = record["beads"]["A"]
    bead["revalidation"] = {
        "integrationCommit": fresh["candidateCommit"],
        "candidateTree": fresh["candidateTree"],
        "planRevision": record["planRevision"],
        "requirementRefs": bead["requirementRefs"],
        "specRefs": bead["specRefs"],
        "validation": validation,
        "claim": fresh,
    }
    write_record_json(Path(bead["evidenceDir"]) / "revalidation.json", bead["revalidation"])
    record["claims"].append(fresh)
    record["claimSummary"]["fresh"].append(fresh)
    write_record_json(path, record)
    row = accounting(local_claim)["requirements"][0]
    assert row["claimIds"] == [fresh["claimId"]]
    assert row["claimObservations"][0]["currentInputsState"] == "current"
    assert row["bindingState"] == "unavailable"
    assert not any("recorded-claim-invalidation" in reason for reason in row["reasons"])


def observe_graph(context, repo):
    from codex_harness.core.proof_accounting import _graph

    return _graph(context["published"]["manifest"], repo)


@pytest.fixture
def resumed_claim(local_claim, tmp_path):
    """Canonical protected-input republication and actual local resume/revalidation."""
    from codex_harness.core.roundtable import run_roundtable
    from codex_harness.core.test_audit import audit_revised_tests

    workspace, _, old_record, root, path = local_claim
    old_revision = old_record["planRevision"]
    with PlanStore(workspace) as store:
        template = workspace / "template"
        proof = template / "tests" / "test_value.py"
        proof.write_text(proof.read_text() + "\n# New protected proof revision\n")
        manifest = store.compile_template(template)
        audit = audit_revised_tests(
            repo=workspace,
            base_ref="template",
            revised_template=template,
            manifest=manifest,
            impact={"affectedBeads": ["A"]},
            oracle_dir=tmp_path / "local-implementation-fixture",
        )
        assert audit["ok"], audit
        store.record_audit(store.manifest_revision(), audit)
        run_roundtable(
            store,
            reviewers=[
                dict(name="fixture-codex", provider="codex"),
                dict(name="fixture-claude", provider="claude"),
            ],
            runner=lambda *args: (0, "[]"),
            executable_for=lambda provider: provider,
        )
        published = store.publish(by="planner", export_dir=workspace / "plan")
        assert published != old_revision
        context = plan_context(store)
        (workspace / "sample.json").write_text(json.dumps(store.published_manifest(published)))

    def forbidden_worker(worktree):
        pytest.fail("Unchanged source must be reused, with local proof revalidation")

    record = run_plan(
        workspace, work_root=root.parent, resume_run_dir=root, fake_workers={"A": forbidden_worker}
    )
    bead = record["beads"]["A"]
    assert bead["planRevision"] == old_revision
    assert bead["acceptedPlanRevision"] == record["planRevision"] == published
    assert bead["revalidation"]["validation"]["passed"] is True
    assert bead["revalidation"]["planRevision"] == published
    assert bead["revalidation"]["claim"] == bead_claim(record)
    return workspace, context, record, root, path


def test_current_revalidation(resumed_claim):
    _, _, record, _, _ = resumed_claim
    bead = record["beads"]["A"]
    row = accounting(resumed_claim)["requirements"][0]
    assert row["claimIds"] == [bead["revalidation"]["claim"]["claimId"]]
    observed = row["claimObservations"][0]
    assert observed["currentInputsState"] == "current"
    assert observed["sourcePublicationRevision"] == bead["planRevision"]
    assert observed["acceptedPublicationRevision"] == record["planRevision"]
    assert observed["attemptPublicationRevision"] == bead["planRevision"]
    assert row["bindingState"] == "unavailable"  # Real runtime admission remains unknown.


@pytest.mark.parametrize("kind", ["missing", "wrong-publication", "label-only"])
def test_revalidation_negatives(resumed_claim, kind):
    _, _, record, _, path = resumed_claim
    bead = record["beads"]["A"]
    receipt_path = Path(bead["evidenceDir"]) / "revalidation.json"
    if kind == "missing":
        receipt_path.unlink()
    elif kind == "wrong-publication":
        bead["revalidation"]["planRevision"] = bead["planRevision"]
        write_record_json(receipt_path, bead["revalidation"])
    else:
        bead.pop("revalidation")
        receipt_path.unlink()
    write_record_json(path, record)
    row = accounting(resumed_claim)["requirements"][0]
    assert row["claimObservations"][0]["currentInputsState"] != "current"
    if kind == "missing":
        assert row["claimObservations"][0]["currentInputsState"] == "unavailable"
        assert str(receipt_path) in row["claimObservations"][0]["evidenceRefs"]
    else:
        assert row["bindingState"] == "invalidated"
        reason = (
            "claim-original-binding-mismatch"
            if kind == "wrong-publication"
            else "claim-original-receipt-mismatch"
        )
        assert reason in row["claimObservations"][0]["reasons"]


def test_wrong_run_and_outside(local_claim, tmp_path, monkeypatch):
    _, context, record, root, path = local_claim
    record["planRevision"] = "wrong-publication"
    write_record_json(path, record)
    result = accounting(local_claim)
    assert "run-publication-mismatch" in str(result["missingDependencies"])
    from codex_harness.core import proof_accounting

    def forbidden(*args):
        pytest.fail("Foreign run must be refused before any record read")

    monkeypatch.setattr(proof_accounting, "_record", forbidden)
    result = observe(
        context, run_path=tmp_path / "foreign.json", runs_root=root.parent, publication_current=True
    )
    assert result["source"]["candidateCommit"] is None
    assert "escapes" in str(result["missingDependencies"])


def test_shared_bead_membership(local_claim):
    """Synthetic membership extension only; it cannot grant execution credit."""
    _, context, _, _, _ = local_claim
    from copy import deepcopy

    from codex_harness.core.spine import Spine

    context = deepcopy(context)
    for state in (context["current"], context["published"]):
        state["nodes"].append(node("R2", "requirement", ["F1"]))
        next(n for n in state["nodes"] if n["id"] == "A")["parents"].append("R2")
    result = observe(context, publication_current=False)
    assert len(result["requirements"]) == 2
    assert {b for r in result["requirements"] for b in r["beadIds"]} == {"A"}
    assert all(r["beadIds"] == ["A"] for r in result["requirements"])
    assert result["requirements"][1]["revision"] == Spine(context["published"]).revision("R2")


def test_observation_is_readonly(local_claim):
    from tests.python.core.test_cockpit_projection import tree

    workspace, _, _, root, _ = local_claim
    before = tree(workspace), tree(root)
    first = snapshot(workspace=workspace, runs_root=root.parent)
    assert (tree(workspace), tree(root)) == before
    first["cockpit"]["vision"]["proofAccounting"]["requirements"].clear()
    assert accounting(local_claim)["requirements"]


def test_external_common_git_dir(local_claim, tmp_path, monkeypatch):
    """Ordinary local indirection file; no foreign Git content is accessed."""
    from codex_harness.core import proof_accounting

    _, _, _, root, _ = local_claim
    assert (
        accounting(local_claim)["requirements"][0]["claimObservations"][0]["currentInputsState"]
        == "current"
    )
    foreign = tmp_path / "foreign-metadata"
    foreign.mkdir()
    (root / "repo" / ".git" / "commondir").write_text(str(foreign) + "\n", encoding="utf-8")
    calls = []

    def forbidden_git(*args):
        calls.append(args)
        pytest.fail("External common metadata must be rejected before any Git call")

    monkeypatch.setattr(proof_accounting, "_git", forbidden_git)
    result = accounting(local_claim)
    assert calls == []
    assert result["source"]["candidateCommit"] is None
    assert result["source"]["candidateTree"] is None
    assert "candidate-common-git-directory-outside-canonical-run" in str(
        result["missingDependencies"]
    )


@pytest.mark.parametrize("text", ["", "first\nsecond\n"])
def test_invalid_common_git_dir(local_claim, monkeypatch, text):
    from codex_harness.core import proof_accounting

    _, _, _, root, _ = local_claim
    (root / "repo" / ".git" / "commondir").write_text(text, encoding="utf-8")

    def forbidden_git(*args):
        pytest.fail("Malformed common-directory metadata must be refused before Git")

    monkeypatch.setattr(proof_accounting, "_git", forbidden_git)
    result = accounting(local_claim)
    assert result["source"]["candidateCommit"] is None
    assert result["source"]["candidateTree"] is None
    assert "candidate-common-git-indirection-invalid" in str(result["missingDependencies"])
