"""Retained candidate identity, bound judgment preparation and continuation."""

from pathlib import Path
from codex_harness.core.record_io import read_record_json
from codex_harness.core.candidate_observations import (
    file_hashes,
    observation_files,
    _digest,
    _execution_identity,
    execution_assessment_request,
    assess_execution,
    _semantic_validation,
)
from codex_harness.core.candidate_delivery import deliver, delivered_candidate


def retained(workspace, job_id):
    """Read-only eligibility and candidate binding; no worker or verifier starts."""
    from codex_harness.core import dispatch_repository as d, planner_jobs as jobs

    directory = jobs._directory(workspace, job_id)
    if (directory / "stop.json").exists():
        raise ValueError("revalidate: quarantined attempt; readmit within B")
    request = jobs._request(directory)
    from codex_harness.core.plan_store import open_plan_store_readonly

    with open_plan_store_readonly(workspace) as store:
        segment = jobs.latest_segment(store, job_id)
    terminal, receipt = (
        read_record_json(segment / name) if (segment / name).is_file() else {}
        for name in ("terminal.json", "quiescence.json")
    )
    if not jobs.verified_segment_exit(
        request, None if segment == directory else segment.name, terminal, receipt
    ):
        raise ValueError(
            "revalidate: retain the latest segment's bound terminal and verified cleanup before continuation"
        )
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
    bead_id, bead = next(
        (key, b)
        for key, b in run["beads"].items()
        if (b.get("integrated") or {}).get("semantic", {}).get("evidenceBinding")
        == semantic["evidenceBinding"]
    )
    root = jobs._inside(directory, Path(bead["integrated"]["root"]))
    repo = jobs._inside(directory, Path(run["repo"]))
    if d._git(root, "status", "--porcelain") or (
        d._git(root, "rev-parse", "HEAD") != semantic["candidateCommit"]
        or d._git(root, "rev-parse", "HEAD^{tree}") != semantic["candidateTree"]
        or observation_files(root) != semantic["observationFiles"]
    ):
        raise ValueError(
            "revalidate: retained candidate changed; inspect evidence before rebuilding"
        )
    d._git(
        repo,
        "merge-base",
        "--is-ancestor",
        d._git(repo, "rev-parse", "HEAD"),
        semantic["candidateCommit"],
    )
    return dict(
        runPath=str(path),
        runDigest=jobs._digest(run),
        bead=bead_id,
        candidateCommit=semantic["candidateCommit"],
        candidateTree=semantic["candidateTree"],
        verifier=_execution_identity(
            jobs.load_plan(Path(run.get("verificationPlan", directory / "plan/sample.json")))
        ),
    )


def current_job_run(store, *, required=False):
    """Views return pending; C/judgment require the completion-owned selection."""
    from codex_harness.core.runners import execution_sources
    from codex_harness.core import planner_jobs as jobs

    if not store.spine().semantic:
        from codex_harness.core.run_loop import cursor

        job_id = cursor(store)["jobId"]
        observed = jobs.worker_job_status(store.workspace, job_id)["run"]
        record = read_record_json(Path(observed["recordPath"]))
        return dict(
            complete=True,
            jobId=job_id,
            runPath=observed["recordPath"],
            candidateRoot=record["repo"],
        ), record
    candidate = {}
    try:
        candidate = read_record_json(store.workspace / ".codex-harness/retained-candidate.json")
        directory = jobs._directory(store.workspace, candidate["jobId"])
        run = read_record_json(jobs._inside(directory, Path(candidate["runPath"])))
        snapshot = Path(candidate["verificationPlan"]).parent
        plan = jobs.load_plan(snapshot)
        if (
            plan.plan_revision != store.manifest_revision()
            or execution_sources(snapshot / "template", plan.trace)["contentHash"]
            != execution_sources(store.workspace / "template", plan.trace)["contentHash"]
        ):
            raise ValueError("verification inputs changed; revalidate the retained candidate")
        if (
            run["runId"] != candidate["runId"]
            or (run["beads"][candidate["bead"]].get("integrated") or {}).get("root")
            != candidate["candidateRoot"]
            or not isinstance(candidate["productHashes"], dict)
        ):
            raise ValueError("candidate record is inconsistent; revalidate its explicit job")
        if (directory / "stop.json").exists():
            raise ValueError("candidate is quarantined; readmit its job within B")
    except (ValueError, OSError, KeyError, TypeError) as error:
        reason = "retained candidate unavailable: " + str(error)
        job_id = candidate.get("jobId") if isinstance(candidate, dict) else None
        next_step = (
            "harness_steer direction=repair with recorded planner meaning and retained observations"
        )
        if required:
            raise ValueError(reason + "; Next: " + next_step) from error
        return dict(complete=False, reason=reason, next=next_step, jobId=job_id), None
    return dict(candidate, complete=True, reason=None), run


def judgment_observation(store, repo):
    """Candidate identity includes worker tests, trace, product bytes and observation tools."""
    from codex_harness.core import dispatch_repository as d
    from codex_harness.core.planner_jobs import load_plan

    root = Path(repo).resolve()
    candidate, run = current_job_run(store, required=True)
    if root in {store.workspace.resolve(), Path(run["repo"]).resolve()}:
        if root == store.workspace.resolve():
            delivered_candidate(store, root)
        elif (
            d._git(root, "status", "--porcelain")
            or d._git(root, "rev-parse", "HEAD^{tree}")
            != run["beads"][candidate["bead"]]["integrated"]["semantic"]["candidateTree"]
        ):
            raise ValueError(
                "judgment: integrated candidate changed; select unchanged observations"
            )
        root = Path(candidate["candidateRoot"])
    plan = load_plan(Path(candidate["verificationPlan"]))
    return dict(files=observation_files(root), **_execution_identity(plan)), run["beads"][
        candidate["bead"]
    ]["integrated"]["semantic"]


def judgments_for(store, repo):
    """Resolve 1j records through the canonical consumer; never trust legacy passed flags."""
    from codex_harness.core.approvals import records

    try:
        binding, proof = judgment_observation(store, repo)
        candidate = _digest(binding)
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
        if judgment["current"] and all(
            meaning.get(k) == row.get(k) == v
            for k, v in execution_assessment_request(proof, row["check"]).items()
        ):
            result.setdefault(
                row["check"],
                dict(
                    {
                        key: judgment[key]
                        for key in (
                            "id",
                            "binding",
                            "candidate",
                            "status",
                            "current",
                            "positiveCredit",
                            "concreteGap",
                            "verdict",
                            "rationale",
                            "meaning",
                            "gaps",
                            "source",
                            "sourceVerified",
                        )
                    },
                    passed=True
                    if judgment["positiveCredit"]
                    else False
                    if judgment["concreteGap"]
                    else None,
                ),
            )
    return result


def record_judgment(store, *, repo, check, judgment_id=None, passed=None, statement=None, by=None):
    """Prepare or attach the one 1j judgment. Caller observations alone grant no credit."""
    binding, proof = judgment_observation(store, repo)
    candidate = _digest(binding)
    spine = store.spine()
    subjects = sorted({*spine.ids("test"), *spine.ids("requirement"), *spine.ids("vision")})
    if proof["candidate"] != candidate:
        raise ValueError("judgment: select the unchanged observed candidate")
    if not isinstance(check, str) or not check.strip():
        raise ValueError("judgment: name the semantic check to prepare")
    request = execution_assessment_request(proof, check)
    if judgment_id is None:
        with store._transaction():
            store._event(
                "candidate-observation",
                dict(
                    **request,
                    candidate=candidate,
                    proof=proof,
                    callerObservation=dict(passed=passed, statement=statement, by=by),
                    humanIdentityVerified=False,
                ),
            )
            event_id = store.connection.execute("SELECT max(id) FROM events").fetchone()[0]
        judgment = store.prepare_judgment(
            question=(
                f"Assess {check} in {request['phase']}: meaningful tests, whether observed baseline failures are missing/incorrect product behavior or broken test infrastructure, and stub/mock gaps. "
                "Execution positive credit requires meaningful:true, correctFailureReason:true for the bound RED and noStubMockGaps:true for Validation."
            ),
            subject_ids=subjects,
            source_event_ids=[event_id],
            candidate=candidate,
        )
    else:
        judgment = store.judgment(judgment_id, candidate=candidate)
        packets = [
            r["payload"] for r in judgment["sourceRefs"] if r["kind"] == "candidate-observation"
        ]
        if (
            not judgment["current"]
            or not any(
                p.get("candidate") == candidate and all(p.get(k) == v for k, v in request.items())
                for p in packets
            )
            or {r["id"] for r in judgment["subjectRefs"]} != set(subjects)
        ):
            raise ValueError(
                f"judgment: expected prepared request(s) {[{k: p[k] for k in request} for p in packets]!r}; use their exact check and current observed repo/subjects"
            )
    with store._transaction():
        store._event(
            "candidate-judgment", dict(**request, candidate=candidate, judgmentId=judgment["id"])
        )
    return store.judgment(judgment["id"], candidate=candidate)


def revalidate(store, job_id, request_id):
    """Observe current verification inputs on retained product bytes, then consume 1j."""
    from codex_harness.core import dispatch_repository as d, planner_jobs as jobs
    from codex_harness.core.planner_execution import execution_scope, require_execution
    from codex_harness.core.runners import execution_sources
    from codex_harness.core import (
        dispatch_attempts as attempts,
        dispatch_integration as integration,
        graph,
        dispatch_shared as constants,
    )

    selected = retained(store.workspace, job_id)
    directory = jobs._directory(store.workspace, job_id)
    request = jobs._request(directory)
    publication = store._meta()["published_revision"]
    manifest, template = jobs._publication(store, publication)
    scope = execution_scope(
        store,
        publication,
        request["options"]["workers"],
        max_parallel=request["options"]["maxParallel"],
        timeout=request["options"]["timeout"],
    )
    require_execution(store, scope)
    path = Path(selected["runPath"])
    run = read_record_json(path)
    plan_path = Path(run.get("verificationPlan", directory / "plan/sample.json"))
    plan = graph.load_plan(plan_path)
    bead = run["beads"][selected["bead"]]
    from codex_harness.core.run_ownership import effect_binding, ensure_effect

    bead["control"] = run["control"] = effect_binding(store)
    built_plan = jobs.load_plan(directory / "plan")
    phase = manifest["beads"][selected["bead"]]["semanticPhase"]
    root = Path(bead["integrated"]["root"])
    proof = bead["integrated"]["semantic"]
    if attempts._hold_permission_guard(
        bead, Path(bead["evidenceDir"]) / "attempt"
    ) or not attempts._check_attempt_provenance(
        bead, owned_files=built_plan.beads[selected["bead"]].owned_files
    ):
        raise ValueError("revalidate: inspect the retained attempt's permission/provenance hold")
    receipt = jobs._inside(directory, Path(bead["integrated"]["receiptPath"]))
    original = read_record_json(receipt)
    if original["semantic"]["evidenceBinding"] != proof["evidenceBinding"]:
        raise ValueError("revalidate: original observation receipt differs")
    if (
        proof["planRevision"] != manifest["planRevision"]
        or scope["templateFiles"] != jobs._files(plan_path.parent / "template")
        or any(proof.get(k) != v for k, v in _execution_identity(plan).items())
    ):
        product = proof.get("productBaseline")
        if product is None:
            raise ValueError(
                "revalidate: build product baseline required; readmit the current plan"
            )
        verifier_roots = execution_sources(root, plan.trace)["roots"]
        before = jobs._files(plan_path.parent / "template")
        frozen = jobs._inside(directory, jobs.directory(directory / "verification", request_id))
        after = scope["templateFiles"]
        plan, template = jobs.snapshot_plan(template, frozen, manifest, after), frozen / "template"
        plan_path = frozen / "sample.json"
        verifier_roots += execution_sources(template, plan.trace)["roots"]
        changes = {n for n in before.keys() | after.keys() if before.get(n) != after.get(n)}
        if any(
            after.get(n) != product[n]
            if n in product
            else not (
                n in built_plan.requirement_files
                or any(n == r or n.startswith(r + "/") for r in verifier_roots)
            )
            for n in changes
        ):
            raise ValueError("revalidate: product inputs changed; readmit the current plan")
        root = frozen / "candidate"
        d._git(
            Path(run["repo"]),
            "worktree",
            "add",
            "-q",
            "--detach",
            str(root),
            proof["candidateCommit"],
        )
        for name in changes:
            target, source = (
                jobs._inside(root, root / name),
                jobs._inside(template, template / name),
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes()) if source.is_file() else target.unlink(
                missing_ok=True
            )
        d._git(root, "add", "--all")
        d._commit(root, "Refresh verification inputs; retain product bytes")
        added = (
            observation_files(root).keys()
            - proof["observationFiles"].keys()
            - set(built_plan.requirement_files)
        )
        if (
            file_hashes(root, product) != product
            or any(not any(n == r or n.startswith(r + "/") for r in verifier_roots) for n in added)
            or file_hashes(root, changes) != {n: after.get(n) for n in changes}
        ):
            raise ValueError(
                "revalidate: retained product or captured verifier bytes changed; inspect candidate"
            )
        proof = _semantic_validation(root, plan, bead, phase)
        if proof["state"] == "incomplete":
            raise ValueError(proof["reason"])
        receipt = frozen / "integrated.json"
        original.update(
            root=str(root), integrationCommit=proof["candidateCommit"], receiptPath=str(receipt)
        )
        run["verificationPlan"] = str(plan_path)
    else:
        proof = assess_execution(
            proof, judgments_for(store, root).get(execution_assessment_request(proof)["check"])
        )
    original.update(semantic=proof, validation=dict(passed=proof["passed"]))
    bead["candidate"].update(commit=proof["candidateCommit"], tree=proof["candidateTree"])
    with store._transaction():
        ensure_effect(store, run["control"]["generation"], run["control"]["effectId"])
        d._write_record(receipt, original)
        ensure_effect(store, run["control"]["generation"], run["control"]["effectId"])
    bead["integrated"] = original
    integration._complete_integration(
        bead,
        manifest["beads"][selected["bead"]],
        Path(run["repo"]),
        manifest,
        path.parent.parent / "memory-workspace",
        receipt,
        "harness-integrator",
        plan_path,
        plan,
        bool(bead["attempt"]["changedPaths"]),
        original.get("recoveryBinding"),
    )
    run["semanticContracts" if phase == "contracts" else "semanticValidation"] = proof
    run["status"] = (
        "pending-judgment"
        if proof["state"] == "pending-judgment"
        else "pending-build"
        if phase == "contracts" and proof["admitted"]
        else (constants.STATUS_MOCK_ONLY if run["isMocked"] else constants.STATUS_VALIDATED)
        if proof["passed"]
        else f"plan-failed: {selected['bead']} {bead['status']}"
    )
    run["finishedAt"] = d._now()
    index = list(graph.topological_order(plan)).index(selected["bead"])
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
    with store._transaction():
        ensure_effect(store, run["control"]["generation"], run["control"]["effectId"])
        d._write_record(path, run)
        ensure_effect(store, run["control"]["generation"], run["control"]["effectId"])
    delivery = deliver(store.workspace, request, run, publication)
    return dict(
        state="pending-build" if run["status"] == "pending-build" else proof["state"],
        results=proof["results"],
        proof=proof,
        delivery=delivery,
        runId=run["runId"],
        publicationRevision=publication,
    )
