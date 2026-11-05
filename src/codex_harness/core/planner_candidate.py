"""Retained semantic candidates use the ordinary integration and delivery owners."""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from codex_harness.core.record_io import _record_guard, read_record_json


def file_hashes(root, names):
    from codex_harness.core.planner_jobs import _inside

    files = {name: _inside(root, Path(root) / name) for name in names}
    hashes = {}
    for name, file in sorted(files.items()):
        if file.is_file():
            with file.open("rb") as stream:
                hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
        else:
            hashes[name] = None
    return hashes


def observation_files(root):
    from codex_harness.core.semantic_runner import SOURCE_IGNORED

    root, names = Path(root).resolve(), []
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [n for n in dirs if n not in SOURCE_IGNORED]
        for name in files:
            if name not in SOURCE_IGNORED:
                names.append((Path(base) / name).relative_to(root).as_posix())
    return file_hashes(root, names)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _execution_identity(plan):
    from codex_harness.core import runners
    from codex_harness.core.dispatch import _semantic_identity

    return dict(_semantic_identity(plan), executionAdapter=file_hashes(
        Path(__file__).parent, [Path(__file__).name, Path(runners.__file__).name]))


def _evidence_digest(proof):
    assessment = {"evidenceBinding", "state", "admitted", "passed", "judgmentStatus",
                  "judgmentId", "gaps", "assessedObservationState"}
    return _digest({k: v for k, v in proof.items() if k not in assessment})


def assess_execution(proof, judgment=None):
    """Consume shared current judgment credit; process failures remain observations."""
    if _evidence_digest(proof) != proof["evidenceBinding"]:
        raise ValueError("Execution observation hash changed; retain original evidence")
    result = dict(proof, admitted=False, passed=False, judgmentStatus="pending-judgment")
    for key in ("gaps", "judgmentId", "assessedObservationState"):
        result.pop(key, None)
    contracts = proof["phase"] == "contracts"
    observed = proof["observedState"]
    result["state"] = observed if observed in {"ERROR", "FAIL"} else "pending-judgment"
    meaning = (judgment or {}).get("meaning", {})
    bound = bool(judgment and judgment.get("candidate") == proof["candidate"]
                 and judgment.get("current") and meaning.get("check") == "execution"
                 and meaning.get("evidenceBinding") == proof["evidenceBinding"])
    if not bound:
        return result
    result["judgmentId"] = judgment["id"]
    if judgment["concreteGap"]:
        result.update(state="FAIL", judgmentStatus="judged", gaps=judgment["gaps"])
    elif (judgment["positiveCredit"] and meaning.get("meaningful") is True
          and meaning.get("correctFailureReason") is True
          and (contracts or meaning.get("noStubMockGaps") is True)):
        result["judgmentStatus"] = "judged"
        for observation in ([proof] if contracts else [proof["red"], proof]):
            observed, rows = observation["observedState"], observation["results"]
            is_red = observation["phase"] == "contracts"
            if observed == "pending-judgment" and rows and all(
                not r.get("unperformed") and r.get("actions") for r in rows
            ):
                missing = [o["check"] for r in rows for o in r.get("observations", []) if o["passed"] is None]
                answers = meaning.get("qualitativeChecks", {})
                if missing and isinstance(answers, dict) and all(answers.get(k) is True for k in missing):
                    observed = "RED" if is_red and any(r["state"] == "FAIL" for r in rows) else "PASS"
            if observed != ("RED" if is_red else "PASS"):
                if is_red and observed != "pending-judgment":
                    result["state"] = "ERROR" if observed == "ERROR" else "FAIL"
                break
        else:
            result.update(state=observed, admitted=True, passed=not contracts)
    return result


def _semantic_validation(root, plan, record, phase):
    from codex_harness.core import semantic_runner
    from codex_harness.core.dispatch import _git
    from codex_harness.core.runners import executable_scenarios, execution_sources

    identity = _execution_identity(plan)
    observed_files = observation_files(root)
    document = executable_scenarios(plan.trace, plan.semantic["scenarios"])
    requirements = {r["id"] for r in record.get("requirementRefs", [])}
    document["scenarios"] = [s for s in document["scenarios"] if not requirements or
        requirements.intersection(r.split("@", 1)[0] for r in s["requirementRefs"])]
    sources = execution_sources(root, plan.trace)
    tests = [dict(id=s["id"], requirementRefs=s["requirementRefs"],
                  contractRefs=s["contractRefs"], testRef=s.get("testRef"),
                  rationale=s.get("rationale")) for s in document["scenarios"]]
    before = {name: observed_files.get(name) for bead in plan.beads.values() for name in bead.owned_files}
    red = next((p for p in record.get("redObservations", []) if p["tests"] == tests and p["testSources"]["contentHash"] == sources["contentHash"]
                and all(p.get(k) == v for k, v in identity.items())), None) if phase == "build" else None
    if phase == "build" and red is None:
        with TemporaryDirectory(prefix="trace-red-", dir=Path(root).parent) as scratch:
            baseline = Path(scratch) / "base"
            _git(root, "worktree", "add", "-q", "--detach", str(baseline), record["attempt"]["baseCommit"])
            try:
                if sources["roots"]:
                    _git(baseline, "--literal-pathspecs", "restore", "--source", _git(root, "rev-parse", "HEAD"), "--worktree", "--no-overlay", "--", *sources["roots"])
                if execution_sources(baseline, plan.trace)["contentHash"] != sources["contentHash"]:
                    raise ValueError("Baseline test bundle differs from the candidate; regenerate the integration candidate")
                red = _semantic_validation(baseline, plan, record, "contracts")
            finally:
                _git(root, "worktree", "remove", "--force", str(baseline))
    results = semantic_runner.run(document, root, plan.semantic["targets"],
        "contracts" if phase == "contracts" else "validation",
        progress_path=Path(record["evidenceDir"]) / "semantic-progress.json" if record.get("evidenceDir") else None)
    if observation_files(root) != observed_files:
        raise ValueError("Validation candidate bytes changed during observation")
    states = [r["state"] for r in results]
    complete = [r["id"] for r in results] == [t["id"] for t in tests] and bool(tests)
    observed = ("ERROR" if not complete or "ERROR" in states else
                "pending-judgment" if phase == "contracts" and "pending-judgment" in states else
                "RED" if phase == "contracts" and "FAIL" in states else
                "FAIL" if "FAIL" in states else
                "pending-judgment" if "pending-judgment" in states else "PASS")
    proof = dict(
        **identity, phase="contracts" if phase == "contracts" else "validation",
        observedState=observed, candidateCommit=_git(root, "rev-parse", "HEAD"),
        candidateTree=_git(root, "rev-parse", "HEAD^{tree}"), candidateFiles=before,
        observationFiles=observed_files,
        candidate=_digest(dict(files=observed_files, **identity)),
        linkedRed=_digest(red) if red else None, red=red, results=results, testSources=sources,
        tests=tests,
    )
    proof["evidenceBinding"] = _evidence_digest(proof)
    return assess_execution(proof, record.get("judgments", {}).get("execution"))


def _read_git(root, *arguments):
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            f"safe.directory={root.as_posix()}",
            "-C",
            str(root),
            *arguments,
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
    )
    if result.returncode:
        raise ValueError(
            "revalidate: cannot read retained Git evidence; inspect run: " + result.stderr[:512]
        )
    return result.stdout.strip()


def retained(workspace, job_id):
    """Read-only eligibility and candidate binding; no worker or verifier starts."""
    from codex_harness.core import dispatch as d
    from codex_harness.core import planner_jobs as jobs

    directory = jobs._directory(workspace, job_id)
    if (directory / "stop.json").exists():
        raise ValueError("revalidate: quarantined attempt; readmit within B")
    request = jobs._request(directory)
    terminal_path = jobs._inside(directory, directory / "terminal.json")
    terminal = read_record_json(terminal_path if terminal_path.is_file() else directory / "started.json")
    if terminal.get("requestDigest") != request["requestDigest"]:
        raise ValueError("revalidate: terminal identity mismatch; inspect original job")
    if not terminal_path.is_file() and terminal.get("pid") != os.getpid():
        raise ValueError("revalidate: wait for the original worker owner or verified quiescence")
    paths = list(directory.glob("runs/*/evidence/run.json"))
    if len(paths) != 1:
        raise ValueError("revalidate: one retained run required; inspect job evidence")
    path = jobs._inside(directory, paths[0])
    run = read_record_json(path)
    plan = d.graph.load_plan(directory / "plan")
    if run["planRevision"] != request["publicationRevision"] or plan.semantic is None:
        raise ValueError("revalidate: retained semantic publication required; inspect job")
    pending = [key for key in plan.beads if
               (run["beads"][key].get("integrated") or {}).get("semantic", {}).get("state")
               in {"FAIL", "ERROR", "pending-judgment"}]
    if len(pending) != 1:
        raise ValueError("revalidate: select one retained observed candidate")
    bead_id = pending[0]
    bead = run["beads"][bead_id]
    state = read_record_json(
        jobs._inside(directory, Path(bead["evidenceDir"]) / "integration-state.json")
    )
    semantic = bead["integrated"]["semantic"]
    root = jobs._inside(directory, Path(state["candidateWorktree"]))
    repo = jobs._inside(directory, Path(run["repo"]))
    if _read_git(root, "status", "--porcelain") or (
        _read_git(root, "rev-parse", "HEAD") != semantic["candidateCommit"]
        or _read_git(root, "rev-parse", "HEAD^{tree}") != semantic["candidateTree"]
    ):
        raise ValueError(
            "revalidate: retained candidate changed; inspect evidence before rebuilding"
        )
    if _read_git(repo, "rev-parse", "HEAD") != state["baseIntegration"]:
        raise ValueError("revalidate: integration base changed; inspect original run")
    return dict(
        runPath=str(path),
        runDigest=jobs._digest(run),
        bead=bead_id,
        candidateCommit=semantic["candidateCommit"],
        candidateTree=semantic["candidateTree"],
        verifier=d._semantic_identity(plan),
    )


def deliver(workspace, request, run):
    """Shared worker completion: expose only a validated integration's owned files."""
    from codex_harness.core import dispatch as d
    from codex_harness.core import planner_jobs as jobs

    if run.get("semanticValidation", {}).get("state") != "PASS" or run["status"].startswith(
        "plan-failed"
    ):
        return
    directory = jobs._directory(workspace, request["jobId"])
    with _record_guard(directory / "stop.json"):
        if (directory / "stop.json").exists():
            raise ValueError("delivery: quarantined attempt; readmit within B")
        manifest = read_record_json(directory / "plan/sample.json")
        repo = jobs._inside(directory, Path(run["repo"]))
        if d._git(repo, "rev-parse", "HEAD^{tree}") != run["semanticValidation"]["candidateTree"]:
            raise ValueError("delivery: integration tree differs from Validation PASS")
        if _read_git(repo, "status", "--porcelain"):
            raise ValueError("delivery: source differs from validated candidate")
        names = sorted({n for b in manifest["beads"].values() for n in b["ownedFiles"]})
        files, hashes = [], {}
        for name in names:
            if Path(name).parts[0].startswith("."):
                raise ValueError("delivery: metadata is not a product file")
            source = jobs._inside(repo, repo / name)
            target = jobs._inside(workspace, Path(workspace) / name)
            raw = source.read_bytes() if source.is_file() else None
            hashes[name] = hashlib.sha256(raw).hexdigest() if raw is not None else None
            if target.exists() and target.read_bytes() != raw:
                baseline = jobs._inside(directory, directory / "plan/template" / name)
                if not baseline.is_file() or target.read_bytes() != baseline.read_bytes():
                    raise ValueError(
                        f"delivery: existing {name} changed; preserve it and reconcile first"
                    )
            if raw is not None:
                files.append((raw, target))
        if hashes != run["semanticValidation"]["candidateFiles"]:
            raise ValueError("delivery: source differs from validated candidate bytes")
        for raw, target in files:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        run["delivery"] = dict(
            root=str(Path(workspace).resolve()),
            files=[str(t) for _, t in files],
            fileHashes=hashes,
            candidateTree=run["semanticValidation"]["candidateTree"],
        )
        _check_delivery(run["delivery"], workspace)

        d._write_record(repo.parent / "evidence/run.json", run)
        return run["delivery"]


def _check_delivery(delivery, root):
    if file_hashes(root, delivery["fileHashes"]) != delivery["fileHashes"]:
        raise ValueError("delivery: delivered bytes changed or missing")


def current_job_run(store):
    from codex_harness.core import planner_jobs as jobs
    from codex_harness.core.planner_recovery import admission_id

    directory = jobs._directory(
        store.workspace, admission_id(store, store._meta()["published_revision"])
    )
    paths = list(directory.glob("runs/*/evidence/run.json"))
    if len(paths) != 1:
        raise ValueError("delivery: one retained candidate required before C/judgment")
    return directory, read_record_json(jobs._inside(directory, paths[0]))


def delivered_candidate(store, repo):
    """C consumes the canonical delivery, including when review used the integration repo."""
    directory, run = current_job_run(store)

    delivery = run.get("delivery")
    manifest = read_record_json(directory / "plan/sample.json")
    names = {n for b in manifest["beads"].values() for n in b["ownedFiles"]}
    if (
        not delivery
        or set(delivery.get("fileHashes", {})) != names
        or delivery["root"] != str(store.workspace.resolve())
        or delivery["candidateTree"] != run["semanticValidation"]["candidateTree"]
        or delivery["fileHashes"] != run["semanticValidation"]["candidateFiles"]
        or run["semanticValidation"]["state"] != "PASS"
        or Path(repo).resolve() not in {store.workspace.resolve(), Path(run["repo"]).resolve()}
    ):
        raise ValueError("delivery: current validated root delivery required before C")
    _check_delivery(delivery, store.workspace)
    _check_delivery(delivery, repo)
    return delivery


def judgment_binding(store, repo):
    """Candidate identity includes worker tests, trace, product bytes and observation tools."""
    from codex_harness.core.graph import load_plan
    from codex_harness.core.planner_audits import AuditTemporaryDirectory

    root = Path(repo).resolve()
    _, run = current_job_run(store)
    if root in {store.workspace.resolve(), Path(run["repo"]).resolve()}:
        if root == store.workspace.resolve():
            delivered_candidate(store, root)
        elif (_read_git(root, "status", "--porcelain") or
              _read_git(root, "rev-parse", "HEAD^{tree}") != run["semanticValidation"]["candidateTree"]):
            raise ValueError("judgment: integrated candidate changed; select unchanged observations")
        root = Path(next(b["integrated"]["root"] for b in run["beads"].values()
            if (b.get("integrated") or {}).get("semantic", {}).get("candidate") ==
            run["semanticValidation"]["candidate"]))
    manifest = store._manifest(store.spine())
    with AuditTemporaryDirectory(prefix="seldon-judgment-") as temp:
        file = Path(temp) / "sample.json"
        file.write_text(json.dumps(manifest), encoding="utf-8")
        plan = load_plan(file)
    return dict(files=observation_files(root), **_execution_identity(plan))


def judgments_for(store, repo):
    """Resolve 1j records through the canonical consumer; never trust legacy passed flags."""
    from codex_harness.core.approvals import records

    candidate = _digest(judgment_binding(store, repo))
    result, seen = {}, set()
    for row in records(store, "candidate-judgment"):
        if row.get("candidate") != candidate or not row.get("judgmentId"):
            continue
        if row["check"] in seen:
            continue
        seen.add(row["check"])
        judgment = store.judgment(row["judgmentId"], candidate=candidate)
        meaning = judgment["meaning"]
        if (judgment["current"] and meaning.get("check") == row["check"]
                and meaning.get("evidenceBinding") == row["evidenceBinding"]):
            result.setdefault(row["check"], dict({key: judgment[key] for key in (
                "id", "binding", "candidate", "status", "current", "positiveCredit", "concreteGap",
                "verdict", "rationale", "meaning", "gaps", "source", "sourceVerified")},
                passed=True if judgment["positiveCredit"] else
                       False if judgment["concreteGap"] else None))
    return result


def record_judgment(store, *, repo, check, judgment_id=None, passed=None, statement=None, by=None):
    """Prepare or attach the one 1j judgment. Caller observations alone grant no credit."""
    if not isinstance(check, str) or not check.strip():
        raise ValueError("judgment: name the assessment check")
    root = Path(repo).resolve()
    binding = judgment_binding(store, root)
    candidate = _digest(binding)
    spine = store.spine()
    subjects = sorted({*spine.ids("test"), *spine.ids("requirement"), *spine.ids("vision")})
    _, run = current_job_run(store)
    proofs = [(b.get("integrated") or {}).get("semantic") for b in run["beads"].values()]
    proof = next((p for p in proofs if p and p.get("candidate") == candidate), None)
    if proof is None:
        raise ValueError("judgment: select the unchanged observed candidate")
    evidence_binding = proof["evidenceBinding"] if check == "execution" else _digest(
        dict(candidate=candidate, check=check, results=proof["results"]))
    if judgment_id is None:
        with store._transaction():
            store._event("candidate-observation", dict(check=check, candidate=candidate,
                evidenceBinding=evidence_binding, proof=proof,
                callerObservation=dict(passed=passed, statement=statement, by=by),
                humanIdentityVerified=False))
            event_id = store.connection.execute("SELECT max(id) FROM events").fetchone()[0]
        judgment = store.prepare_judgment(question=(
            f"Assess {check}: meaningful tests, correct failure reason and identified stub/mock gaps. "
            f"Return meaning.check={check!r}, meaning.evidenceBinding={evidence_binding!r}; "
            "for execution positive credit also requires meaningful:true and "
            "correctFailureReason:true for the bound RED and noStubMockGaps:true for Validation. "
            "Uncertain assessments stay inconclusive/unsupported; negative verdicts name gaps."),
            subject_ids=subjects, source_event_ids=[event_id], candidate=candidate)
    else:
        judgment = store.judgment(judgment_id, candidate=candidate)
        packets = [r["payload"] for r in judgment["sourceRefs"] if r["kind"] == "candidate-observation"]
        if (not judgment["current"] or not any(
            p.get("candidate") == candidate and p.get("check") == check
            and p.get("evidenceBinding") == evidence_binding for p in packets)
            or {r["id"] for r in judgment["subjectRefs"]} != set(subjects)):
            raise ValueError("judgment: bind the current candidate observation and subjects")
    with store._transaction():
        store._event("candidate-judgment", dict(check=check, candidate=candidate,
            evidenceBinding=evidence_binding, judgmentId=judgment["id"]))
    return store.judgment(judgment["id"], candidate=candidate)


def revalidate(store, job_id, request_id):
    """Reuse unchanged observations, consume judgment, then use the ordinary integrator."""
    from codex_harness.core import dispatch as d
    from codex_harness.core import planner_jobs as jobs

    selected = retained(store.workspace, job_id)
    directory = jobs._directory(store.workspace, job_id)
    request = jobs._request(directory)
    manifest, _ = jobs._publication(store, request["publicationRevision"])
    if jobs._digest(manifest) != request["manifestDigest"]:
        raise ValueError("revalidate: publication changed; inspect current plan")
    path = Path(selected["runPath"])
    run = read_record_json(path)
    plan = d.graph.load_plan(directory / "plan")
    bead = run["beads"][selected["bead"]]
    phase = manifest["beads"][selected["bead"]]["semanticPhase"]
    root = Path(bead["integrated"]["root"])
    proof = bead["integrated"]["semantic"]
    if (any(proof.get(k) != v for k, v in _execution_identity(plan).items())
            or observation_files(root) != proof["observationFiles"]):
        raise ValueError("revalidate: changed evidence requires fresh execution before judgment")
    bead["judgments"] = judgments_for(store, root)
    proof = assess_execution(proof, bead["judgments"].get("execution"))
    receipt = jobs._inside(directory, Path(bead["evidenceDir"]) / "integrated.json")
    original = read_record_json(receipt)
    if original["semantic"]["evidenceBinding"] != proof["evidenceBinding"]:
        raise ValueError("revalidate: original observation receipt differs")
    original.update(semantic=proof, validation=dict(passed=proof["passed"]))
    d._write_record(receipt, original)
    bead["status"] = "running"
    bead["integrated"] = None
    d._integrate_bead(
        bead,
        manifest["beads"][selected["bead"]],
        repo=Path(run["repo"]),
        manifest=manifest,
        memory=path.parent.parent / "memory-workspace",
        receipt=receipt,
        confirmer="harness-integrator",
        manifest_path=directory / "plan/sample.json",
    )
    if bead["integrated"] is None:
        raise ValueError("revalidate refused: " + bead["status"] + "; inspect attempt provenance")
    proof = bead["integrated"]["semantic"]
    run["semanticContracts" if phase == "contracts" else "semanticValidation"] = proof
    run["status"] = (
        "pending-judgment" if proof["state"] == "pending-judgment" else
        "pending-build" if phase == "contracts" and proof["admitted"] else
        (d.STATUS_MOCK_ONLY if run["isMocked"] else d.STATUS_VALIDATED) if proof["passed"] else
        f"plan-failed: {selected['bead']} {bead['status']}"
    )
    run["finishedAt"] = d._now()
    index = list(d.graph.topological_order(plan)).index(selected["bead"])
    b = plan.beads[selected["bead"]]
    run["requirementValidation"][index] = d._requirement_validation(
        b.id,
        b.requirements,
        b.proof_module,
        bead["integrated"],
        plan.plan_revision,
        bead["requirementRefs"],
        bead.get("specRefs"),
    )
    d._write_record(path, run)
    if (phase == "contracts" and proof["admitted"] and
            read_record_json(directory / "started.json")["pid"] == os.getpid()):
        workers = jobs._workers(manifest, request["options"]["workers"])
        run = d.run_plan(directory / "plan", work_root=directory / "runs",
            resume_run_dir=path.parent.parent,
            provider_for={w["bead"]: w["provider"] for w in workers},
            model_for={w["bead"]: w["model"] for w in workers},
            effort_for={w["bead"]: w["effort"] for w in workers},
            max_parallel=request["options"]["maxParallel"], timeout=request["options"]["timeout"],
            execution_job=directory,
            setup=store.spine().node(store.spine().ids("vision")[0])["fields"].get("setup", {}),
            intent_nodes=store.locked_acceptance_spine().to_dict()["nodes"])
        proof = run.get("semanticValidation") or run["semanticContracts"]
    delivery = deliver(store.workspace, request, run)
    return dict(
        state="pending-build" if run["status"] == "pending-build" else proof["state"],
        results=proof["results"], delivery=delivery, runId=run["runId"]
    )
