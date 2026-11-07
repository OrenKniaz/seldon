"""Complete validated product delivery, ZIP packaging and C readback."""

import hashlib
import os
from pathlib import Path
from codex_harness.core.init_hosts import HOSTS
from codex_harness.core.record_io import _record_guard


RESERVED_ROOTS = {
    ".git",
    ".codex-harness",
    *(
        Path(name).parts[0].casefold()
        for host in HOSTS.values()
        for name in (host.PATH, getattr(host, "PERMISSIONS_PATH", host.PATH))
    ),
}

from codex_harness.core.candidate_observations import file_hashes


def retain_candidate(workspace, request, run, publication=None):
    """The completion writer registers one observation independently of root delivery."""
    from codex_harness.core import dispatch_repository as d, planner_jobs as jobs

    proof = run.get("semanticValidation") or run.get("semanticContracts")
    if not proof:
        return
    bead, integrated = next(
        (key, b["integrated"])
        for key, b in run["beads"].items()
        if (b.get("integrated") or {}).get("semantic", {}).get("evidenceBinding")
        == proof["evidenceBinding"]
    )
    directory = jobs._directory(workspace, request["jobId"])
    candidate = dict(
        jobId=request["jobId"],
        runId=run["runId"],
        bead=bead,
        runPath=str(Path(run["repo"]).parent / "evidence/run.json"),
        candidateRoot=integrated["root"],
        productBaseline=proof["productBaseline"],
        productHashes={
            n: h
            for n, h in {**(proof["productBaseline"] or {}), **proof["candidateFiles"]}.items()
            if Path(n).parts[0].casefold() not in RESERVED_ROOTS
        },
        publicationRevision=publication or proof["planRevision"],
        verificationPlan=run.get("verificationPlan", str(directory / "plan/sample.json")),
        verificationRecords=[
            b["integrated"]["receiptPath"]
            for b in run["beads"].values()
            if (b.get("integrated") or {}).get("semantic")
        ],
    )
    d._write_record(Path(workspace) / ".codex-harness/retained-candidate.json", candidate)
    return candidate


def deliver(workspace, request, run, publication=None):
    from codex_harness.core.run_ownership import effect_guard

    with effect_guard(run.get("control")):
        """Completion retains one observation; only validated integrations reach the root."""
        from codex_harness.core import dispatch_repository as d, planner_jobs as jobs

        directory = jobs._directory(workspace, request["jobId"])
        with _record_guard(directory / "stop.json"):
            if (directory / "stop.json").exists():
                raise ValueError("delivery: quarantined attempt; readmit within B")
            candidate = retain_candidate(workspace, request, run, publication)
            if run.get("semanticValidation", {}).get("state") != "PASS" or run["status"].startswith(
                "plan-failed"
            ):
                return
            repo = jobs._inside(directory, Path(run["repo"]))
            if (
                d._git(repo, "status", "--porcelain")
                or d._git(repo, "rev-parse", "HEAD^{tree}")
                != run["semanticValidation"]["candidateTree"]
            ):
                raise ValueError("delivery: source differs from validated candidate")
            files, hashes = [], candidate["productHashes"]
            for name in sorted(hashes):
                if Path(name).parts[0].casefold() in RESERVED_ROOTS:
                    raise ValueError(
                        "delivery: reserved metadata is not product; remove from product inputs and revalidate: "
                        + name
                    )
                source, target = (
                    jobs._inside(repo, repo / name),
                    jobs._inside(workspace, Path(workspace) / name),
                )
                raw = source.read_bytes() if source.is_file() else None
                if (hashlib.sha256(raw).hexdigest() if raw is not None else None) != hashes[name]:
                    raise ValueError(
                        "delivery: source differs from validated candidate bytes: " + name
                    )
                if target.exists() and target.read_bytes() != raw:
                    baseline = jobs._inside(directory, directory / "plan/template" / name)
                    prior = (
                        (run.get("delivery") or request.get("previousDelivery", {}))
                        .get("fileHashes", {})
                        .get(name)
                    )
                    if hashlib.sha256(target.read_bytes()).hexdigest() != prior and (
                        not baseline.is_file() or target.read_bytes() != baseline.read_bytes()
                    ):
                        raise ValueError(
                            f"delivery: existing {name} changed; preserve it and reconcile first"
                        )
                files.append((raw, target))
            for raw, target in files:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw) if raw is not None else target.unlink(missing_ok=True)
            run["delivery"] = dict(
                root=str(Path(workspace).resolve()),
                files=[str(t) for raw, t in files if raw is not None],
                fileHashes=hashes,
                candidateTree=run["semanticValidation"]["candidateTree"],
            )
            _check_delivery(hashes, workspace)

            import zipfile

            archive = (
                Path(workspace)
                / ".codex-harness"
                / "delivery"
                / (candidate["candidateRoot"].split(os.sep)[-1] + ".zip")
            )
            archive.parent.mkdir(exist_ok=True)
            temporary = archive.with_suffix(".zip.tmp")
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as package:
                for raw, target in files:
                    if raw is not None:
                        package.writestr(
                            zipfile.ZipInfo(target.relative_to(workspace).as_posix()), raw
                        )
            os.replace(temporary, archive)
            run["delivery"].update(
                archive=str(archive), archiveSha256=hashlib.sha256(archive.read_bytes()).hexdigest()
            )

            d._write_record(repo.parent / "evidence/run.json", run)
            return run["delivery"]


def _check_delivery(hashes, root):
    if (observed := file_hashes(root, hashes)) != hashes:
        raise ValueError(
            "delivery: delivered bytes changed or missing; restore retained bytes for "
            + ", ".join(n for n in hashes if observed[n] != hashes[n])
        )


def delivered_candidate(store, repo):
    """C consumes the canonical delivery, including when review used the integration repo."""
    from codex_harness.core.planner_candidate import current_job_run

    candidate, run = current_job_run(store, required=True)

    delivery, proof = run.get("delivery"), run.get("semanticValidation", {})
    if (
        not delivery
        or delivery["root"] != str(store.workspace.resolve())
        or delivery["candidateTree"] != proof.get("candidateTree")
        or proof.get("state") != "PASS"
        or Path(repo).resolve() not in {store.workspace.resolve(), Path(run["repo"]).resolve()}
    ):
        raise ValueError("delivery: current validated root delivery required before C")
    _check_delivery(candidate["productHashes"], store.workspace)
    _check_delivery(candidate["productHashes"], run["repo"])
    delivery = run.get("delivery", {})
    if delivery.get("archive"):
        from codex_harness.core.planner_jobs import _inside

        archive = _inside(store.workspace, Path(delivery["archive"]))
        if (
            not archive.is_file()
            or hashlib.sha256(archive.read_bytes()).hexdigest() != delivery["archiveSha256"]
        ):
            raise ValueError(
                "delivery: archive changed or missing; restore the retained delivery package"
            )
    return delivery
