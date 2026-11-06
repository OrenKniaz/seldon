"""Retained semantic candidates use the ordinary integration and delivery owners."""

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from codex_harness.core.init_hosts import HOSTS
from codex_harness.core.record_io import _record_guard, read_record_json

RESERVED_ROOTS = {".git", ".codex-harness", *(Path(name).parts[0].casefold()
    for host in HOSTS.values() for name in (host.PATH, getattr(host, "PERMISSIONS_PATH", host.PATH)))}


def file_hashes(root, names):
    from codex_harness.core.planner_jobs import _inside

    files = {name: _inside(root, Path(root) / name) for name in names}
    hashes = {}
    for name, file in sorted(files.items()):
        hashes[name] = None
        if file.is_file():
            with file.open("rb") as stream:
                hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
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
    from codex_harness.core import runners, semantic_runner, semantic_schema

    return dict(planRevision=plan.plan_revision, contractsRevision=plan.requirement_files_revision,
        scenarioBinding=_digest(dict(plan.semantic)),
        **({"webToolchain": semantic_runner.web_toolchain(None, False)}
           if any(t.get("kind") == "web" for t in plan.semantic["targets"].values()) else {}),
        runner=file_hashes(Path(__file__).parent, [Path(m).name for m in (
            semantic_runner.__file__, semantic_schema.__file__, "semantic_web.cjs")]),
        executionAdapter=file_hashes(Path(__file__).parent, [Path(__file__).name, Path(runners.__file__).name]))


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
    result["state"] = observed if not contracts and observed in {"ERROR", "FAIL"} else "pending-judgment"
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
            if is_red and any(r["state"] in {"FAIL", "ERROR"} and r.get("actions") for r in rows):
                observed = "RED"  # Meaning, including startup errors, belongs to the bound judge.
            elif observed == "pending-judgment" and rows and all(r.get("actions") and not r.get("unperformed") for r in rows):
                missing = [o["check"] for r in rows for o in r.get("observations", []) if o["passed"] is None]
                answers = meaning.get("qualitativeChecks", {})
                if missing and isinstance(answers, dict) and all(answers.get(k) is True for k in missing):
                    observed = "PASS"
            if observed != ("RED" if is_red else "PASS") or not all(r.get("unperformed") or (r.get("cleanup", {}).get("quiescenceVerified") and r["cleanup"].get("activeProcessesAfterTeardown") == 0) for r in rows):
                return dict(result, state="FAIL" if is_red and observed != "pending-judgment" else result["state"])
        result.update(state=observed, admitted=True, passed=not contracts)
    return result


def _semantic_validation(root, plan, record, phase):
    from codex_harness.core import semantic_runner
    from codex_harness.core.dispatch import _git
    from codex_harness.core.runners import executable_scenarios, execution_sources

    sources = execution_sources(root, plan.trace)
    if sources["missing"]:
        return dict(state="incomplete", reason="incomplete: declared " + ", ".join(sources["missing"]) + " missing from candidate")
    identity = _execution_identity(plan)
    observed_files = observation_files(root)
    product = ((record.get("integrated") or {}).get("semantic") or {}).get("productBaseline")
    if phase == "build" and not record.get("integrated"):
        product = {n: h for n, h in observed_files.items() if n not in plan.requirement_files
                   and not any(n == r or n.startswith(r + "/") for r in sources["roots"])}
    document = executable_scenarios(plan.trace, plan.semantic["scenarios"])
    requirements = {r["id"] for r in record.get("requirementRefs", [])}
    document["scenarios"] = [s for s in document["scenarios"] if not requirements or
        requirements.intersection(r.split("@", 1)[0] for r in s["requirementRefs"])]
    tests = [dict(id=s["id"], requirementRefs=s["requirementRefs"],
                  contractRefs=s["contractRefs"], testRef=s.get("testRef"),
                  rationale=s.get("rationale")) for s in document["scenarios"]]
    names = {name for bead in plan.beads.values() for name in bead.owned_files} | {f["path"] for f in sources["files"]} | set(((record.get("integrated") or {}).get("semantic") or {}).get("candidateFiles", {}))
    before = {name: observed_files.get(name) for name in names}
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
                "FAIL" if "FAIL" in states else
                "pending-judgment" if "pending-judgment" in states else "PASS")
    proof = dict(**identity, phase="contracts" if phase == "contracts" else "validation",
        observedState=observed, candidateCommit=_git(root, "rev-parse", "HEAD"),
        candidateTree=_git(root, "rev-parse", "HEAD^{tree}"), candidateFiles=before,
        observationFiles=observed_files, productBaseline=product,
        candidate=_digest(dict(files=observed_files, **identity)),
        linkedRed=_digest(red) if red else None, red=red, results=results, testSources=sources, tests=tests)
    proof["evidenceBinding"] = _evidence_digest(proof)
    return assess_execution(proof, record.get("judgments", {}).get("execution"))


def retained(workspace, job_id):
    """Read-only eligibility and candidate binding; no worker or verifier starts."""
    from codex_harness.core import dispatch as d, planner_jobs as jobs

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
    owner = Path(workspace) / ".codex-harness/retained-candidate.json"
    candidate = read_record_json(owner) if owner.is_file() else {}
    if candidate.get("jobId") == job_id:
        path = jobs._inside(directory, Path(candidate["runPath"]))
    else:  # Explicit pre-upgrade recovery reads the original dispatch owner.
        dispatch = read_record_json(directory / "dispatch-run.json")
        if dispatch["requestDigest"] != request["requestDigest"]:
            raise ValueError("revalidate: dispatch identity mismatch; inspect original job")
        path = jobs._inside(directory, Path(dispatch["runDir"]) / "evidence/run.json")
    run = read_record_json(path)
    semantic = run.get("semanticValidation") or run.get("semanticContracts")
    if run["planRevision"] != request["publicationRevision"] or not semantic:
        raise ValueError("revalidate: retained semantic publication required; inspect job")
    bead_id, bead = next((key, b) for key, b in run["beads"].items()
                        if (b.get("integrated") or {}).get("semantic", {}).get("evidenceBinding") == semantic["evidenceBinding"])
    root = jobs._inside(directory, Path(bead["integrated"]["root"]))
    repo = jobs._inside(directory, Path(run["repo"]))
    if d._git(root, "status", "--porcelain") or (
        d._git(root, "rev-parse", "HEAD") != semantic["candidateCommit"]
        or d._git(root, "rev-parse", "HEAD^{tree}") != semantic["candidateTree"]
        or observation_files(root) != semantic["observationFiles"]
    ):
        raise ValueError("revalidate: retained candidate changed; inspect evidence before rebuilding")
    d._git(repo, "merge-base", "--is-ancestor", d._git(repo, "rev-parse", "HEAD"), semantic["candidateCommit"])
    return dict(runPath=str(path), runDigest=jobs._digest(run), bead=bead_id, candidateCommit=semantic["candidateCommit"], candidateTree=semantic["candidateTree"],
        verifier=_execution_identity(jobs.load_plan(Path(run.get("verificationPlan", directory / "plan/sample.json")))))


def retain_candidate(workspace, request, run, publication=None):
    """The completion writer registers one observation independently of root delivery."""
    from codex_harness.core import dispatch as d, planner_jobs as jobs

    proof = run.get("semanticValidation") or run.get("semanticContracts")
    if not proof:
        return
    bead, integrated = next((key, b["integrated"]) for key, b in run["beads"].items()
        if (b.get("integrated") or {}).get("semantic", {}).get("evidenceBinding") == proof["evidenceBinding"])
    directory = jobs._directory(workspace, request["jobId"])
    candidate = dict(jobId=request["jobId"], runId=run["runId"], bead=bead,
        runPath=str(Path(run["repo"]).parent / "evidence/run.json"),
        candidateRoot=integrated["root"], productBaseline=proof["productBaseline"],
        productHashes={n: h for n, h in {**(proof["productBaseline"] or {}), **proof["candidateFiles"]}.items()
                       if Path(n).parts[0].casefold() not in RESERVED_ROOTS},
        publicationRevision=publication or proof["planRevision"],
        verificationPlan=run.get("verificationPlan", str(directory / "plan/sample.json")),
        verificationRecords=[b["integrated"]["receiptPath"] for b in run["beads"].values() if (b.get("integrated") or {}).get("semantic")])
    d._write_record(Path(workspace) / ".codex-harness/retained-candidate.json", candidate)
    return candidate


def deliver(workspace, request, run, publication=None):
    """Completion retains one observation; only validated integrations reach the root."""
    from codex_harness.core import dispatch as d, planner_jobs as jobs

    directory = jobs._directory(workspace, request["jobId"])
    with _record_guard(directory / "stop.json"):
        if (directory / "stop.json").exists():
            raise ValueError("delivery: quarantined attempt; readmit within B")
        candidate = retain_candidate(workspace, request, run, publication)
        if run.get("semanticValidation", {}).get("state") != "PASS" or run["status"].startswith("plan-failed"):
            return
        repo = jobs._inside(directory, Path(run["repo"]))
        if d._git(repo, "status", "--porcelain") or d._git(repo, "rev-parse", "HEAD^{tree}") != run["semanticValidation"]["candidateTree"]:
            raise ValueError("delivery: source differs from validated candidate")
        files, hashes = [], candidate["productHashes"]
        for name in sorted(hashes):
            if Path(name).parts[0].casefold() in RESERVED_ROOTS:
                raise ValueError("delivery: reserved metadata is not product; remove from product inputs and revalidate: " + name)
            source, target = jobs._inside(repo, repo / name), jobs._inside(workspace, Path(workspace) / name)
            raw = source.read_bytes() if source.is_file() else None
            if (hashlib.sha256(raw).hexdigest() if raw is not None else None) != hashes[name]:
                raise ValueError("delivery: source differs from validated candidate bytes: " + name)
            if target.exists() and target.read_bytes() != raw:
                baseline = jobs._inside(directory, directory / "plan/template" / name)
                prior = run.get("delivery", {}).get("fileHashes", {}).get(name)
                if hashlib.sha256(target.read_bytes()).hexdigest() != prior and (not baseline.is_file() or target.read_bytes() != baseline.read_bytes()):
                    raise ValueError(f"delivery: existing {name} changed; preserve it and reconcile first")
            files.append((raw, target))
        for raw, target in files:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw) if raw is not None else target.unlink(missing_ok=True)
        run["delivery"] = dict(root=str(Path(workspace).resolve()), files=[str(t) for raw, t in files if raw is not None], fileHashes=hashes,
            candidateTree=run["semanticValidation"]["candidateTree"])
        _check_delivery(hashes, workspace)

        d._write_record(repo.parent / "evidence/run.json", run)
        return run["delivery"]


def _check_delivery(hashes, root):
    if (observed := file_hashes(root, hashes)) != hashes:
        raise ValueError("delivery: delivered bytes changed or missing; restore retained bytes for " + ", ".join(n for n in hashes if observed[n] != hashes[n]))


def current_job_run(store, *, required=False):
    """Views return pending; C/judgment require the completion-owned selection."""
    from codex_harness.core.runners import execution_sources
    from codex_harness.core import planner_jobs as jobs

    candidate, choice = {}, "revalidate"
    try:
        candidate = read_record_json(store.workspace / ".codex-harness/retained-candidate.json")
        directory = jobs._directory(store.workspace, candidate["jobId"])
        run = read_record_json(jobs._inside(directory, Path(candidate["runPath"])))
        snapshot = Path(candidate["verificationPlan"]).parent
        plan = jobs.load_plan(snapshot)
        if (plan.plan_revision != store.manifest_revision() or
            execution_sources(snapshot / "template", plan.trace)["contentHash"] != execution_sources(store.workspace / "template", plan.trace)["contentHash"]):
            raise ValueError("verification inputs changed; revalidate the retained candidate")
        if run["runId"] != candidate["runId"] or (run["beads"][candidate["bead"]].get("integrated") or {}).get("root") != candidate["candidateRoot"] or not isinstance(candidate["productHashes"], dict):
            raise ValueError("candidate record is inconsistent; revalidate its explicit job")
        if (directory / "stop.json").exists():
            choice = "readmit"
            raise ValueError("candidate is quarantined; readmit its job within B")
    except (ValueError, OSError, KeyError, TypeError) as error:
        reason = "retained candidate unavailable: " + str(error)
        job_id = candidate.get("jobId") if isinstance(candidate, dict) else None
        if job_id:
            arguments = dict(mode="resolve", job_id=job_id, choice=choice, reason="Restore the retained observation")
        else:
            row = store.connection.execute("SELECT json_extract(payload_json, '$.jobId') FROM events WHERE kind='worker-job-admitted' ORDER BY id DESC LIMIT 1").fetchone()
            arguments = dict(mode="inspect", job_id=row[0]) if row else {}
        next_step = "harness_recovery " + " ".join(f"{k}={json.dumps(v)}" for k, v in arguments.items()) if arguments else "No worker job admitted; approve B and start the planned workers with harness_worker_start"
        if required:
            raise ValueError(reason + "; Next: " + next_step) from error
        return dict(complete=False, reason=reason, next=next_step, recoveryArguments=arguments), None
    return dict(candidate, complete=True, reason=None), run


def delivered_candidate(store, repo):
    """C consumes the canonical delivery, including when review used the integration repo."""
    candidate, run = current_job_run(store, required=True)

    delivery, proof = run.get("delivery"), run.get("semanticValidation", {})
    if (not delivery or delivery["root"] != str(store.workspace.resolve())
        or delivery["candidateTree"] != proof.get("candidateTree")
        or proof.get("state") != "PASS"
        or Path(repo).resolve() not in {store.workspace.resolve(), Path(run["repo"]).resolve()}):
        raise ValueError("delivery: current validated root delivery required before C")
    _check_delivery(candidate["productHashes"], store.workspace)
    _check_delivery(candidate["productHashes"], repo)
    return delivery


def judgment_binding(store, repo):
    """Candidate identity includes worker tests, trace, product bytes and observation tools."""
    from codex_harness.core import dispatch as d
    from codex_harness.core.planner_jobs import load_plan

    root = Path(repo).resolve()
    candidate, run = current_job_run(store, required=True)
    if root in {store.workspace.resolve(), Path(run["repo"]).resolve()}:
        if root == store.workspace.resolve():
            delivered_candidate(store, root)
        elif (d._git(root, "status", "--porcelain") or
              d._git(root, "rev-parse", "HEAD^{tree}") != run["beads"][candidate["bead"]]["integrated"]["semantic"]["candidateTree"]):
            raise ValueError("judgment: integrated candidate changed; select unchanged observations")
        root = Path(candidate["candidateRoot"])
    plan = load_plan(Path(candidate["verificationPlan"]))
    return dict(files=observation_files(root), **_execution_identity(plan))


def judgments_for(store, repo):
    """Resolve 1j records through the canonical consumer; never trust legacy passed flags."""
    from codex_harness.core.approvals import records

    try:
        candidate = _digest(judgment_binding(store, repo))
    except ValueError:
        return {}  # Unavailable observations grant no credit; C/judgment explains the prerequisite.
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
    retained_candidate, run = current_job_run(store, required=True)
    proof = run["beads"][retained_candidate["bead"]]["integrated"]["semantic"]
    if proof["candidate"] != candidate:
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
            f"Assess {check}: meaningful tests, whether observed baseline failures are missing/incorrect product behavior or broken test infrastructure, and stub/mock gaps. "
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
    """Observe current verification inputs on retained product bytes, then consume 1j."""
    from codex_harness.core import dispatch as d, planner_jobs as jobs
    from codex_harness.core.planner_execution import execution_scope, require_execution
    from codex_harness.core.runners import execution_sources

    selected = retained(store.workspace, job_id)
    directory = jobs._directory(store.workspace, job_id)
    request = jobs._request(directory)
    publication = store._meta()["published_revision"]
    manifest, template = jobs._publication(store, publication)
    scope = execution_scope(store, publication, request["options"]["workers"],
        max_parallel=request["options"]["maxParallel"], timeout=request["options"]["timeout"])
    require_execution(store, scope)
    path = Path(selected["runPath"])
    run = read_record_json(path)
    plan_path = Path(run.get("verificationPlan", directory / "plan/sample.json"))
    plan = d.graph.load_plan(plan_path)
    bead = run["beads"][selected["bead"]]
    built_plan = jobs.load_plan(directory / "plan")
    phase = manifest["beads"][selected["bead"]]["semanticPhase"]
    root = Path(bead["integrated"]["root"])
    proof = bead["integrated"]["semantic"]
    if d._hold_permission_guard(bead, Path(bead["evidenceDir"]) / "attempt") or not d._check_attempt_provenance(bead, owned_files=built_plan.beads[selected["bead"]].owned_files):
        raise ValueError("revalidate: inspect the retained attempt's permission/provenance hold")
    receipt = jobs._inside(directory, Path(bead["integrated"]["receiptPath"]))
    original = read_record_json(receipt)
    if original["semantic"]["evidenceBinding"] != proof["evidenceBinding"]:
        raise ValueError("revalidate: original observation receipt differs")
    if (proof["planRevision"] != manifest["planRevision"] or scope["templateFiles"] != jobs._files(plan_path.parent / "template")
            or any(proof.get(k) != v for k, v in _execution_identity(plan).items())):
        product = proof.get("productBaseline")
        if product is None:
            raise ValueError("revalidate: build product baseline required; readmit the current plan")
        verifier_roots = execution_sources(root, plan.trace)["roots"]
        before = jobs._files(plan_path.parent / "template")
        frozen = jobs._inside(directory, jobs.directory(directory / "verification", request_id))
        after = scope["templateFiles"]
        plan, template = jobs.snapshot_plan(template, frozen, manifest, after), frozen / "template"
        plan_path = frozen / "sample.json"
        verifier_roots += execution_sources(template, plan.trace)["roots"]
        changes = {n for n in before.keys() | after.keys() if before.get(n) != after.get(n)}
        if any(after.get(n) != product[n] if n in product else not (n in built_plan.requirement_files or any(n == r or n.startswith(r + "/") for r in verifier_roots)) for n in changes):
            raise ValueError("revalidate: product inputs changed; readmit the current plan")
        root = frozen / "candidate"
        d._git(Path(run["repo"]), "worktree", "add", "-q", "--detach", str(root), proof["candidateCommit"])
        for name in changes:
            target, source = jobs._inside(root, root / name), jobs._inside(template, template / name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes()) if source.is_file() else target.unlink(missing_ok=True)
        d._git(root, "add", "--all")
        d._commit(root, "Refresh verification inputs; retain product bytes")
        added = observation_files(root).keys() - proof["observationFiles"].keys() - set(built_plan.requirement_files)
        if (file_hashes(root, product) != product or any(not any(n == r or n.startswith(r + "/") for r in verifier_roots) for n in added)
                or file_hashes(root, changes) != {n: after.get(n) for n in changes}):
            raise ValueError("revalidate: retained product or captured verifier bytes changed; inspect candidate")
        proof = _semantic_validation(root, plan, bead, phase)
        if proof["state"] == "incomplete":
            raise ValueError(proof["reason"])
        receipt = frozen / "integrated.json"
        original.update(root=str(root), integrationCommit=proof["candidateCommit"], receiptPath=str(receipt))
        run["verificationPlan"] = str(plan_path)
    else:
        proof = assess_execution(proof, judgments_for(store, root).get("execution"))
    original.update(semantic=proof, validation=dict(passed=proof["passed"]))
    bead["candidate"].update(commit=proof["candidateCommit"], tree=proof["candidateTree"])
    d._write_record(receipt, original)
    bead["integrated"] = original
    d._complete_integration(bead, manifest["beads"][selected["bead"]], Path(run["repo"]), manifest,
        path.parent.parent / "memory-workspace", receipt, "harness-integrator", plan_path, plan,
        bool(bead["attempt"]["changedPaths"]), original.get("recoveryBinding"))
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
    run["requirementValidation"][index] = d._requirement_validation(b.id, b.requirements,
        b.proof_module, bead["integrated"], plan.plan_revision, bead["requirementRefs"], bead.get("specRefs"))
    d._write_record(path, run)
    if (phase == "contracts" and proof["admitted"] and
            read_record_json(directory / "started.json")["pid"] == os.getpid()):
        run = jobs.dispatch_job(store, directory, request, path.parent.parent)
        proof = run.get("semanticValidation") or run["semanticContracts"]
    delivery = deliver(store.workspace, request, run, publication)
    return dict(
        state="pending-build" if run["status"] == "pending-build" else proof["state"],
        results=proof["results"], delivery=delivery, runId=run["runId"], publicationRevision=publication
    )
