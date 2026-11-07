"""Immutable execution observations and purpose-bound semantic credit."""

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory


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

    return dict(
        planRevision=plan.plan_revision,
        contractsRevision=plan.requirement_files_revision,
        scenarioBinding=_digest(dict(plan.semantic)),
        **(
            {"webToolchain": semantic_runner.web_toolchain(None, False)}
            if any(t.get("kind") == "web" for t in plan.semantic["targets"].values())
            else {}
        ),
        runner=file_hashes(
            Path(__file__).parent,
            [
                Path(m).name
                for m in (semantic_runner.__file__, semantic_schema.__file__, "semantic_web.cjs")
            ],
        ),
        executionAdapter=file_hashes(
            Path(__file__).parent,
            [
                Path(__file__).name,
                "planner_candidate.py",
                "candidate_delivery.py",
                Path(runners.__file__).name,
            ],
        ),
    )


def _evidence_digest(proof):
    assessment = {
        "evidenceBinding",
        "state",
        "admitted",
        "passed",
        "judgmentStatus",
        "judgmentId",
        "gaps",
        "assessedObservationState",
    }
    return _digest({k: v for k, v in proof.items() if k not in assessment})


def execution_assessment_request(proof, check="execution"):
    return dict(check=check, evidenceBinding=proof["evidenceBinding"], phase=proof["phase"])


def assess_execution(proof, judgment=None):
    """Consume shared current judgment credit; process failures remain observations."""
    if _evidence_digest(proof) != proof["evidenceBinding"]:
        raise ValueError("Execution observation hash changed; retain original evidence")
    result = dict(proof, admitted=False, passed=False, judgmentStatus="pending-judgment")
    for key in ("gaps", "judgmentId", "assessedObservationState"):
        result.pop(key, None)
    contracts = proof["phase"] == "contracts"
    observed = proof["observedState"]
    result["state"] = (
        observed if not contracts and observed in {"ERROR", "FAIL"} else "pending-judgment"
    )
    meaning = (judgment or {}).get("meaning", {})
    bound = bool(
        judgment
        and judgment.get("candidate") == proof["candidate"]
        and judgment.get("current")
        and all(meaning.get(k) == v for k, v in execution_assessment_request(proof).items())
    )
    if not bound:
        return result
    result["judgmentId"] = judgment["id"]
    if judgment["concreteGap"]:
        result.update(state="FAIL", judgmentStatus="judged", gaps=judgment["gaps"])
    elif (
        judgment["positiveCredit"]
        and meaning.get("meaningful") is True
        and meaning.get("correctFailureReason") is True
        and (contracts or meaning.get("noStubMockGaps") is True)
    ):
        result["judgmentStatus"] = "judged"
        for observation in [proof] if contracts else [proof["red"], proof]:
            observed, rows = observation["observedState"], observation["results"]
            is_red = observation["phase"] == "contracts"
            if is_red and any(r["state"] in {"FAIL", "ERROR"} and r.get("actions") for r in rows):
                observed = "RED"  # Meaning, including startup errors, belongs to the bound judge.
            elif (
                observed == "pending-judgment"
                and rows
                and all(r.get("actions") and not r.get("unperformed") for r in rows)
            ):
                missing = [
                    o["check"]
                    for r in rows
                    for o in r.get("observations", [])
                    if o["passed"] is None
                ]
                answers = meaning.get("qualitativeChecks", {})
                if (
                    missing
                    and isinstance(answers, dict)
                    and all(answers.get(k) is True for k in missing)
                ):
                    observed = "PASS"
            if observed != ("RED" if is_red else "PASS") or not all(
                r.get("unperformed")
                or (
                    r.get("cleanup", {}).get("quiescenceVerified")
                    and r["cleanup"].get("activeProcessesAfterTeardown") == 0
                )
                for r in rows
            ):
                return dict(
                    result,
                    state="FAIL" if is_red and observed != "pending-judgment" else result["state"],
                )
        result.update(state=observed, admitted=True, passed=not contracts)
    return result


def _semantic_validation(root, plan, record, phase):
    from codex_harness.core import semantic_runner
    from codex_harness.core.dispatch_repository import _git
    from codex_harness.core.runners import executable_scenarios, execution_sources

    sources = execution_sources(root, plan.trace)
    if sources["missing"]:
        return dict(
            state="incomplete",
            reason="incomplete: declared "
            + ", ".join(sources["missing"])
            + " missing from candidate",
        )
    identity = _execution_identity(plan)
    observed_files = observation_files(root)
    product = ((record.get("integrated") or {}).get("semantic") or {}).get("productBaseline")
    if phase == "build" and not record.get("integrated"):
        product = {
            n: h
            for n, h in observed_files.items()
            if n not in plan.requirement_files
            and not any(n == r or n.startswith(r + "/") for r in sources["roots"])
        }
    document = executable_scenarios(plan.trace, plan.semantic["scenarios"])
    requirements = {r["id"] for r in record.get("requirementRefs", [])}
    document["scenarios"] = [
        s
        for s in document["scenarios"]
        if not requirements
        or requirements.intersection(r.split("@", 1)[0] for r in s["requirementRefs"])
    ]
    tests = [
        dict(
            id=s["id"],
            requirementRefs=s["requirementRefs"],
            contractRefs=s["contractRefs"],
            testRef=s.get("testRef"),
            rationale=s.get("rationale"),
        )
        for s in document["scenarios"]
    ]
    names = (
        {name for bead in plan.beads.values() for name in bead.owned_files}
        | {f["path"] for f in sources["files"]}
        | set(((record.get("integrated") or {}).get("semantic") or {}).get("candidateFiles", {}))
    )
    before = {name: observed_files.get(name) for name in names}
    red = (
        next(
            (
                p
                for p in record.get("redObservations", [])
                if p["tests"] == tests
                and p["testSources"]["contentHash"] == sources["contentHash"]
                and all(p.get(k) == v for k, v in identity.items())
            ),
            None,
        )
        if phase == "build"
        else None
    )
    if phase == "build" and red is None:
        with TemporaryDirectory(prefix="trace-red-", dir=Path(root).parent) as scratch:
            baseline = Path(scratch) / "base"
            _git(
                root,
                "worktree",
                "add",
                "-q",
                "--detach",
                str(baseline),
                record["attempt"]["baseCommit"],
            )
            try:
                if sources["roots"]:
                    _git(
                        baseline,
                        "--literal-pathspecs",
                        "restore",
                        "--source",
                        _git(root, "rev-parse", "HEAD"),
                        "--worktree",
                        "--no-overlay",
                        "--",
                        *sources["roots"],
                    )
                if execution_sources(baseline, plan.trace)["contentHash"] != sources["contentHash"]:
                    raise ValueError(
                        "Baseline test bundle differs from the candidate; regenerate the integration candidate"
                    )
                red = _semantic_validation(baseline, plan, record, "contracts")
            finally:
                _git(root, "worktree", "remove", "--force", str(baseline))
    results = semantic_runner.run(
        document,
        root,
        plan.semantic["targets"],
        "contracts" if phase == "contracts" else "validation",
        evidence_dir=Path(record["evidenceDir"]) / "semantic"
        if record.get("evidenceDir")
        else None,
        progress_path=Path(record["evidenceDir"]) / "semantic-progress.json"
        if record.get("evidenceDir")
        else None,
    )
    if observation_files(root) != observed_files:
        raise ValueError("Validation candidate bytes changed during observation")
    states = [r["state"] for r in results]
    complete = [r["id"] for r in results] == [t["id"] for t in tests] and bool(tests)
    observed = (
        "ERROR"
        if not complete or "ERROR" in states
        else "FAIL"
        if "FAIL" in states
        else "pending-judgment"
        if "pending-judgment" in states
        else "PASS"
    )
    proof = dict(
        **identity,
        phase="contracts" if phase == "contracts" else "validation",
        observedState=observed,
        candidateCommit=_git(root, "rev-parse", "HEAD"),
        candidateTree=_git(root, "rev-parse", "HEAD^{tree}"),
        candidateFiles=before,
        observationFiles=observed_files,
        productBaseline=product,
        candidate=_digest(dict(files=observed_files, **identity)),
        linkedRed=_digest(red) if red else None,
        red=red,
        results=results,
        testSources=sources,
        tests=tests,
    )
    proof["evidenceBinding"] = _evidence_digest(proof)
    return assess_execution(
        proof, record.get("judgments", {}).get(execution_assessment_request(proof)["check"])
    )
