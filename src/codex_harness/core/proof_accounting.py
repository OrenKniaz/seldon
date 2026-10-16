"""Read-only revision accounting. Content correspondence is not worker admission."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path
from types import MappingProxyType

from codex_harness.core.dispatch import _changed_claim_inputs, _claim_inputs, _git
from codex_harness.core.graph import Bead, Plan
from codex_harness.core.record_io import open_record_read
from codex_harness.core.spine import Spine


def plan_context(store):
    """Called on the status reader's in-memory SQLite copy, never a new store."""
    revision = store._meta()["published_revision"]
    return {
        "current": store._data(),
        "published": store._snapshot(revision) if revision else None,
    }


def _record(path, root):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Evidence path escapes canonical root")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Evidence is not a regular record")
    with open_record_read(path) as stream:
        payload = stream.read(1024 * 1024 + 1)
    after = path.stat()
    if len(payload) > 1024 * 1024 or (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("Evidence oversized or changed during observation")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Evidence must be an object")
    return value


def _graph(manifest, root):
    """Reuse the canonical frozen manifest's graph, with no temporary manifest write."""
    beads = {}
    for key, row in manifest["beads"].items():
        beads[key] = Bead(
            id=key,
            title=row["title"],
            owned_files=tuple(row["ownedFiles"]),
            depends_on=tuple(row.get("dependsOn", [])),
            requirements=tuple(row["requirements"]),
            proof_module=row["proofModule"],
            provider=row.get("suggestedProvider"),
            requirement_refs=tuple((x["id"], x["revision"]) for x in row["requirementRefs"]),
            spec_refs=tuple((x["id"], x["kind"], x["revision"]) for x in row["specRefs"])
            if "specRefs" in row
            else None,
            proof_depends_on=tuple(row["proofDependsOn"]) if "proofDependsOn" in row else None,
        )
    integration = manifest.get("integration", {})
    return Plan(
        name=manifest["sample"],
        root=root,
        validation=tuple(manifest["validation"]),
        requirement_files=tuple(manifest["requirementFiles"]),
        beads=MappingProxyType(beads),
        integration_requirements=tuple(integration.get("requirements", [])),
        integration_proof_module=integration.get("proofModule"),
        plan_revision=manifest["planRevision"],
        verification=manifest.get("verification"),
    )


def _claim_observation(claim, bead, plan, repo, evidence, current_commit):
    """Check the original assertion and receipt before comparing current inputs."""
    result = {
        "id": claim.get("claimId"),
        "sourceCandidateCommit": claim.get("candidateCommit"),
        "sourceCandidateTree": claim.get("candidateTree"),
        "sourcePublicationRevision": bead.get("planRevision"),
        "acceptedPublicationRevision": bead.get("acceptedPlanRevision"),
        "attemptPublicationRevision": None,
        "currentInputsState": "unavailable",
        "evidenceRefs": [],
        "reasons": [],
    }
    try:
        original = {key: value for key, value in claim.items() if key != "claimId"}
        digest = hashlib.sha256(
            json.dumps(original, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if claim["claimId"] != f"{claim['bead']}:{digest}":
            raise ValueError("claim-identity-mismatch")
        if claim.get("passed") is False:
            raise ValueError("claim-recorded-validation-failed")
        source = claim["candidateCommit"]
        if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source):
            raise ValueError("claim-candidate-identity-invalid")
        if _git(repo, "rev-parse", f"{source}^{{tree}}") != claim["candidateTree"]:
            raise ValueError("claim-candidate-tree-mismatch")
        _git(repo, "merge-base", "--is-ancestor", source, current_commit)
        if _changed_claim_inputs(claim, _claim_inputs(repo, plan, claim["bead"], source)):
            raise ValueError("claim-original-input-mismatch")
        directory = Path(bead["evidenceDir"]).resolve()
        # evidenceDir is canonical dispatch provenance, still confined before any read.
        if not directory.is_relative_to(evidence.resolve()):
            raise ValueError("claim-receipt-outside-root")
        revalidation = bead.get("revalidation")
        revalidated = isinstance(revalidation, dict) and revalidation.get("claim") == claim
        receipt = directory / ("revalidation.json" if revalidated else "integrated.json")
        result["evidenceRefs"].append(str(receipt))
        proof = _record(receipt, evidence)
        projected = revalidation if revalidated else bead.get("integrated")
        if proof != projected or proof.get("claim") != claim:
            raise ValueError("claim-original-receipt-mismatch")
        if revalidated:
            if proof.get("candidateTree") != claim["candidateTree"]:
                raise ValueError("claim-receipt-candidate-mismatch")
        else:
            candidate = proof.get("candidate")
            if (
                not isinstance(candidate, dict)
                or candidate.get("commit") != source
                or candidate.get("tree") != claim["candidateTree"]
                or candidate.get("validated") is not True
                or Path(proof["receiptPath"]).resolve() != receipt
            ):
                raise ValueError("claim-receipt-candidate-mismatch")
        expected = plan.beads[claim["bead"]]
        refs = [{"id": key, "revision": revision} for key, revision in expected.requirement_refs]
        specs = (
            [
                {"id": key, "kind": kind, "revision": revision}
                for key, kind, revision in expected.spec_refs
            ]
            if expected.spec_refs is not None
            else None
        )
        if (
            proof.get("integrationCommit") != source
            or proof.get("planRevision") != plan.plan_revision
            or proof.get("requirementRefs") != refs
            or (specs is not None and proof.get("specRefs") != specs)
            or not isinstance(proof.get("validation"), dict)
            or proof["validation"].get("passed") is not True
            or claim.get("passed") is not True
        ):
            raise ValueError("claim-original-binding-mismatch")
        if _changed_claim_inputs(claim, _claim_inputs(repo, plan, claim["bead"], current_commit)):
            raise ValueError("claim-current-input-changed")
        result["currentInputsState"] = "current"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["currentInputsState"] = (
            "invalidated" if isinstance(error, ValueError) else "unavailable"
        )
        result["reasons"].append(str(error))
    return result


def observe(context, *, run_path=None, runs_root=None, publication_current=False):
    """Account original facts; this release has no genuine runtime-admission producer.

    Consequently requirementValidation remains null even if local claim bytes match.
    No caller flag, synthetic record or injected validator can authorize positive credit.
    """
    source = dict(
        planRevision=None,
        publicationRevision=None,
        runId=None,
        candidateCommit=None,
        candidateTree=None,
        evidenceRefs=[],
    )
    result = {"source": source, "requirements": None, "missingDependencies": [], "reason": None}

    def missing(code, description, refs=()):
        value = dict(code=code, description=description, refs=list(refs))
        if value not in result["missingDependencies"]:
            result["missingDependencies"].append(value)

    # This is an explicit producer gap, not a new approval/lease registry.
    missing(
        "runtime-admission-unavailable",
        "No canonical original binds a thin attempt to its genuine live assignment/runtime",
    )
    if not isinstance(context, dict):
        missing("plan-original-unavailable", "Canonical plan SQLite snapshot unavailable")
        result["reason"] = "Canonical requirement membership unavailable"
        return result
    current = Spine(context["current"]) if context["current"].get("nodes") else None
    source["planRevision"] = current.plan_revision() if current else None
    published = context.get("published")
    if not published:
        missing("publication-unavailable", "No original published requirement membership")
        result["reason"] = "No published requirement membership; draft is not accepted proof"
        return result
    spine = Spine(published)
    manifest = published["manifest"]
    source["publicationRevision"] = manifest["planRevision"]
    if not publication_current:
        missing("publication-stale", "Editable intent/protected publication inputs have changed")
    rows = []
    for key in spine.ids("requirement"):
        beads = sorted(b for b in spine.ids("bead") if key in spine.ancestors(b))
        row = dict(
            id=key,
            revision=spine.revision(key),
            bindingState="unavailable",
            beadIds=beads,
            claimIds=[],
            evidenceRefs=[],
            reasons=[],
            claimObservations=[],
        )
        if current is None or key not in current.ids() or current.revision(key) != row["revision"]:
            row["bindingState"] = "invalidated"
            row["reasons"].append("requirement-revision-changed")
        if not publication_current:
            row["bindingState"] = "invalidated"
            row["reasons"].append("publication-stale")
        if not beads:
            row["reasons"].append("requirement-has-no-trace-linked-work-items")
        rows.append(row)
    result["requirements"] = rows
    observations = {}
    try:
        if run_path is None or runs_root is None:
            raise FileNotFoundError("Original dispatch run unavailable")
        root = Path(runs_root).resolve()
        path = Path(run_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Run record escapes canonical runs root")
        run_root = path.parent.parent if path.parent.name == "evidence" else path.parent
        record = _record(path, root)
        source["evidenceRefs"] = [str(path)]
        source["runId"] = record.get("runId")
        if record.get("runId") != run_root.name:
            raise ValueError("run-original-identity-mismatch")
        if record.get("planRevision") != source["publicationRevision"]:
            raise ValueError("run-publication-mismatch")
        repo = (run_root / "repo").resolve()
        if not repo.is_relative_to(run_root) or Path(record["repo"]).resolve() != repo:
            raise ValueError("candidate-outside-canonical-run")
        if not (repo / ".git").resolve().is_relative_to(run_root) or not (repo / ".git").is_dir():
            raise ValueError("candidate-git-directory-outside-canonical-run")
        metadata = (repo / ".git").resolve()
        pointer = metadata / "commondir"
        try:
            pointer.lstat()
        except FileNotFoundError:
            pass  # Normal standalone repositories have no common-directory indirection.
        else:
            pointer = pointer.resolve()
            if not pointer.is_relative_to(run_root):
                raise ValueError("candidate-common-git-indirection-outside-canonical-run")
            before = pointer.stat()
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("candidate-common-git-indirection-invalid")
            with open_record_read(pointer) as stream:
                raw = stream.read(1024 * 1024 + 1)
            after = pointer.stat()
            if len(raw) > 1024 * 1024 or (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError("candidate-common-git-indirection-oversized-or-changed")
            value = raw.decode("utf-8").rstrip("\r\n")
            if not value or any(char in value for char in "\r\n\0"):
                raise ValueError("candidate-common-git-indirection-invalid")
            common = (metadata / value).resolve()
            if not common.is_relative_to(run_root):
                raise ValueError("candidate-common-git-directory-outside-canonical-run")
            if not common.is_dir():
                raise ValueError("candidate-common-git-directory-unavailable")
        source["candidateCommit"] = _git(repo, "rev-parse", "HEAD")
        source["candidateTree"] = _git(repo, "rev-parse", "HEAD^{tree}")
        status = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        if status.stdout.strip():
            raise ValueError("candidate-worktree-dirty")
        plan = _graph(manifest, repo)
        claims = record.get("claims", [])
        if (
            not isinstance(claims, list)
            or any(not isinstance(c, dict) for c in claims)
            or not isinstance(record.get("beads"), dict)
        ):
            raise ValueError("run-claim-shape-invalid")
        summary = record.get("claimSummary", {})
        if not isinstance(summary, dict):
            raise ValueError("run-claim-summary-invalid")
        impacted = summary.get("impacted", [])
        if not isinstance(impacted, list) or any(not isinstance(x, dict) for x in impacted):
            raise ValueError("run-claim-summary-invalid")
        for bead_id in sorted({b for row in rows for b in row["beadIds"]}):
            selected = [c for c in claims if c.get("bead") == bead_id]
            if not selected:
                missing(
                    "claim-original-unavailable", f"No original claim for {bead_id}", [str(path)]
                )
                continue
            claim = selected[-1]
            if any(c != claim for c in claims if c.get("claimId") == claim.get("claimId")):
                raise ValueError("claim-original-identity-collision")
            bead = record.get("beads", {}).get(bead_id, {})
            if (
                not isinstance(bead, dict)
                or bead.get("bead") != bead_id
                or bead.get("runId") != source["runId"]
                or (
                    bead.get("planRevision") != source["publicationRevision"]
                    and bead.get("acceptedPlanRevision") != source["publicationRevision"]
                )
            ):
                raise ValueError("bead-original-identity-mismatch")
            observation = _claim_observation(
                claim, bead, plan, repo, run_root / "evidence", source["candidateCommit"]
            )
            dispositions = [x for x in impacted if x.get("claimId") == claim["claimId"]]
            if dispositions:
                if observation["currentInputsState"] == "current":
                    observation["reasons"].append(
                        "recorded-invalidation-conflicts-with-matching-current-inputs"
                    )
                observation["currentInputsState"] = "invalidated"
                observation["evidenceRefs"].append(str(path))
                observation["reasons"].extend(
                    "recorded-claim-invalidation: " + json.dumps(x, sort_keys=True)
                    for x in dispositions
                )
            if not publication_current:
                observation["currentInputsState"] = "invalidated"
                observation["reasons"].append("publication-stale")
            observations[bead_id] = observation
            if observation["currentInputsState"] != "current":
                missing(
                    "claim-binding-unproved",
                    f"{bead_id}: original/current claim inputs unproved",
                    observation["evidenceRefs"],
                )
            # Request/result are necessary original facts but do not establish admission.
            directory = Path(bead.get("evidenceDir", run_root / "evidence" / bead_id))
            try:
                request = _record(directory / "attempt" / "request.json", run_root / "evidence")
                response = _record(directory / "attempt" / "result.json", run_root / "evidence")
                if any(
                    request.get(k) != response.get(k)
                    for k in ("runId", "bead", "planRevision", "baseCommit", "cwd", "startedAt")
                ):
                    raise ValueError("attempt-original-identity-mismatch")
                if request.get("runId") != source["runId"] or request.get("bead") != bead_id:
                    raise ValueError("attempt-original-identity-mismatch")
                if request.get("planRevision") != bead.get("planRevision"):
                    raise ValueError("attempt-source-publication-mismatch")
                observation["attemptPublicationRevision"] = request.get("planRevision")
                if (
                    request.get("mode") != "provider-normal"
                    or record.get("isMocked") is not False
                    or response.get("didRunProvider") is not True
                    or response.get("isMocked") is not False
                ):
                    missing(
                        "mock-or-unproved-runtime",
                        f"{bead_id}: originals do not establish unmocked runtime",
                    )
            except (OSError, ValueError, KeyError, TypeError) as error:
                missing("attempt-original-unavailable", f"{bead_id}: {error}")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        missing("run-candidate-binding-unavailable", str(error), source["evidenceRefs"])
    for row in rows:
        for bead_id in row["beadIds"]:
            observation = observations.get(bead_id)
            if observation:
                row["claimObservations"].append(observation)
                row["claimIds"].append(observation["id"])
                row["evidenceRefs"].extend(observation["evidenceRefs"])
                if observation["currentInputsState"] == "invalidated":
                    row["bindingState"] = "invalidated"
                row["reasons"].extend(observation["reasons"])
            else:
                row["reasons"].append(f"claim-original-unavailable:{bead_id}")
        row["claimIds"] = sorted(set(row["claimIds"]) - {None})
        row["evidenceRefs"] = sorted(set(row["evidenceRefs"]))
        row["reasons"].append("runtime-admission-unavailable")
    result["reason"] = (
        "Genuine live assignment/runtime admission unavailable; no validated work-item credit"
    )
    return result
