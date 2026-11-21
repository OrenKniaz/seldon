"""C14/C15/C26/I1; R6/R9: code observes ordinary commands on exact Git commits."""

import hashlib
import os
import threading
import uuid
from pathlib import Path

from . import features, gitwork, observations as evidence_facts, plan
from .processes import execute
from .providers import ROLES
from .store import encode


_observer = threading.local()


class UncertainCleanup(RuntimeError):
    pass


class Failed(ValueError):
    """Failing checks keep their names, so a repeat of the same failure is recognised (R15)."""

    def __init__(self, message, failed):
        super().__init__(message + explain(failed))
        self.checks = sorted({body["check"] for body in failed})


def observe(store, root, commit, declarations, purpose, unit=None, scope_tree=None, *, walkthroughs=()):
    observations = []
    if not declarations:
        return observations
    setup = store.read(store.cursor().kickoff)["setup"]
    timeout = setup.get("providers", {}).get("check", ROLES["check"])["timeout"]
    scratch = Path(store.cursor().scratch)
    checkout = getattr(_observer, "checkout", None)
    if checkout is None or checkout.parent != scratch:
        checkout = Path(gitwork.admit(root, scratch, commit))
        _observer.checkout = checkout
    else:
        gitwork.checkout(checkout, commit)
    cleanup_verified, dirty = True, False
    witness_commits = {}
    if purpose == "final":
        walkthroughs = plan.guide_checks(plan.execution(store))
    try:
        for ref, check in declarations.items():
            if checkout is None:
                checkout = Path(gitwork.admit(root, scratch, commit))
                _observer.checkout = checkout
            runtime = scratch / ("check-" + uuid.uuid4().hex)
            evidence, temp = runtime / "evidence", runtime / "temp"
            evidence.mkdir(parents=True)
            temp.mkdir()
            command = list(check["command"])
            dirty = True
            retained = check.get("witness")
            executed_commit = commit
            if retained and retained["commit"] != commit and retained["paths"]:
                key = retained["commit"], tuple(retained["paths"])
                if key not in witness_commits:
                    witness_commits[key] = gitwork.check_commit(
                        root, commit, key[0], key[1], scratch, literal=True)
                executed_commit = witness_commits[key]
                if executed_commit != commit:
                    gitwork.checkout(checkout, executed_commit)
            if (checkout / command[0]).is_file():
                command[0] = str((checkout / command[0]).resolve())
            cleanup_verified = False
            result = execute(command, checkout, timeout=timeout,
                             env={**os.environ, "SELDON_EVIDENCE_DIR": str(evidence),
                                  **{key: str(temp) for key in ("TEMP", "TMP", "TMPDIR")}})
            cleanup_verified = result["cleanupVerified"]
            body = {k: v[-4096:].decode("utf-8", errors="replace") if isinstance(v, bytes) else v
                    for k, v in result.items()}
            body.update(evidence_facts.runtime(result, checkout, temp, evidence,
                                              walkthrough=ref in walkthroughs, commit=executed_commit))
            files = [store.artifact(p.read_bytes(), ref) for p in evidence.rglob("*")
                     if p.is_file() and not (p.parent == evidence and evidence_facts.runtime_file(p.name))]
            body.update(check=ref, unit=unit, commit=commit, purpose=purpose,
                        requirements=check["requirements"], command=check["command"], cwd=str(checkout),
                        evidenceFiles=files, cleanCheckout=True, actualCommand=command)
            if retained:
                body.update(witness=retained, executedCommit=executed_commit)
            if scope_tree is not None:
                body["scopeTree"] = scope_tree
            with store.transaction():
                observations.append(store.record("observation", body))
            if not cleanup_verified:
                raise UncertainCleanup("Command cleanup unverified: " + str(body))
            if any(item["locked"] and item["root"] == "checkout"
                   for item in body.get("fileCleanup", {}).get("blockedFiles", [])):
                # Don't turn a cleanup observation into a failed check or reuse locked state.
                _observer.checkout = checkout = None
            else:
                gitwork.clean(checkout, commit)
            dirty = False
    finally:
        # Never reset/reuse a workspace whose descendants may still be running.
        if not cleanup_verified:
            _observer.checkout = None
        elif dirty:
            gitwork.clean(checkout, commit)
    return observations


def executed(body):
    return (body["executionStarted"] and not body["timedOut"] and body["cleanupVerified"]
            and body["exitCode"] is not None)


def failures(store, observations, red=False):
    failed = []
    for ref in observations:
        body = store.read(ref)
        if not executed(body) or (body["exitCode"] == 0 if red else body["exitCode"] != 0):
            failed.append(body)
    return failed


def explain(failed):
    fields = ("check", "purpose", "command", "executionStarted", "timedOut", "cleanupVerified",
              "exitCode", "stdout", "stderr")
    return str([{field: body[field] for field in fields} for body in failed])


def image(content):
    return content.startswith((b"\x89PNG", b"\xff\xd8"))


def account(store, refs, delivered=None, *, mark_images=False):
    """Render the runner's observations for both judgment paths."""
    rows = []
    for ref in refs:
        body = store.read(ref)
        fields = ("commit", "check", "requirements", "purpose", "exitCode", "stdout", "stderr",
                  "executionStarted", "cleanupVerified", "timedOut", "cleanCheckout", "witness", "executedCommit")
        row = {key: body[key] for key in fields if key in body}
        if body["purpose"] in ("milestone-base", "milestone"):
            row["runnerResult"] = ("GREEN" if body["exitCode"] == 0 else "RED") if executed(body) else "unobserved"
        row.update(evidence_facts.judge_fields(body), command=body.get("actualCommand", body["command"]),
                   workingDirectory=body["cwd"], ranOnDeliveredRevision=body["commit"] == delivered)
        row["artifacts"] = {}
        for path in body["evidenceFiles"]:
            content = Path(path).read_bytes()
            artifact = row["artifacts"][path] = {"bytes": len(content)}
            if image(content):
                if mark_images:
                    artifact["image"] = True
                continue
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if text not in body["stdout"] and text not in body["stderr"]:
                artifact["text"] = text[-4096:]
        rows.append(row)
    return rows


def candidate_files(store, root, commit, *, named=None):
    files, images = {}, {}
    for name, content in gitwork.blobs(root, commit, ["**"], named=named).items():
        if image(content):
            images[name] = store.artifact(content, "candidate-image")
        elif b"\0" not in content:
            try:
                files[name] = content.decode("utf-8")
            except UnicodeDecodeError:
                pass
    return files, images


def judge_paths(root, commit, bound):
    paths = gitwork.paths(root, commit)
    if not bound:
        return paths
    generated = {"node_modules", ".venv", "venv", "virtualenv", "site-packages", ".tox", ".nox",
                 "__pycache__", "build", "dist", ".next", ".output", "out", "coverage", "target",
                 "playwright-report", "test-results"}
    return [path for path in paths if not generated.intersection(path.lower().split("/")[:-1])
            and not path.lower().startswith(("docs/try-it/work/", "docs/try-it/fresh/",
                                             "docs/try-it/scratch/", "docs/try-it/tmp/"))]


def milestone_source(store, root, commit, checks, refs, bound):
    inventory = judge_paths(root, commit, bound)
    files, images = candidate_files(store, root, commit, named=set(inventory) if bound else None)
    retained = {}
    for check in checks.values():
        witness = check["witness"]
        if witness["commit"] != commit:
            retained.setdefault(witness["commit"], set()).update(witness["paths"])
    facts = {"Candidate file inventory": inventory, "Product files": files, "Candidate images": images,
             "Code shape (measured)": evidence_facts.code_shape(files),
             "Observations": account(store, refs, commit, mark_images=bound),
             "Checks": {key: {k: v for k, v in check.items() if k != "command"}
                        for key, check in checks.items()},
             "Retained check sources": {revision: candidate_files(store, root, revision, named=(
                 paths & set(judge_paths(root, revision, bound))))[0] for revision, paths in retained.items()}}
    return facts, images


def milestone_evidence_key(root, facts, schema):
    trees = {}

    def tree(commit):
        if commit not in trees:
            trees[commit] = gitwork.git(root, "rev-parse", f"{commit}^{{tree}}")
        return trees[commit]

    def witness_tree(row):
        witness = row["witness"]
        return {**row, "witness": {**witness, "commit": tree(witness["commit"])}}

    rows = []
    for observation in facts["Observations"]:
        row = witness_tree(observation) if "witness" in observation else dict(observation)
        cwd = row.pop("workingDirectory")
        for field in ("commit", "executedCommit"):
            if field in row:
                row[field] = tree(row[field])
        for field in ("stdout", "stderr"):
            row[field] = row[field].replace(cwd, "<checkout>").replace(cwd.replace("\\", "/"), "<checkout>")
        row["command"] = [part.replace(cwd, "<checkout>") for part in row["command"]]
        rows.append(row)
    subject = {key: facts[key] for key in ("Subjects", "Walkthrough checks", "New promise checks")}
    subject["Checks"] = {key: witness_tree(check) for key, check in facts["Checks"].items()}
    payload = [tree(facts["Milestone base"]), tree(facts["Candidate"]), schema, subject, rows]
    return hashlib.sha256(encode(payload).encode()).hexdigest()


def milestone_checks(store, previous, milestone, current, subjects, receipt, candidate):
    """Retain the original reviewed witness even when a later builder offers a replacement."""
    retained = dict(previous.get("checks", {}))
    walks = previous.get("walkthroughs", [])
    declared = [c.get("vision") for c in receipt["checks"]]
    if (not set(current).issubset(declared) or len(declared) != len(set(declared))
            or any(key not in subjects for key in declared)):
        raise ValueError(f"Supply one check per new promise: {', '.join(current)}.")
    if not receipt["walkthrough"]:
        raise ValueError("Supply an unattended walkthrough of the try-it guide.")
    root = store.root.parent
    # Older stores mixed plan commands and builder witnesses. Recover only commands the accepted
    # builder actually declared, using its receipt and candidate rather than the planner's list.
    ancestor = previous
    while any("witness" not in c for c in retained.values()) and ancestor:
        old = store.read(ancestor["return"])["answer"]
        commit = ancestor["commit"]
        paths = [p for p in gitwork.paths(root, commit) if plan.matches(p, old["checkPaths"])]
        for key, check in retained.items():
            if ("witness" not in check and key.startswith(f"milestone-{ancestor['milestone']}-")
                    and any(c["command"] == check["command"] for c in [*old["checks"], old["walkthrough"]])):
                retained[key] = {**check, "witness": {"commit": commit, "paths": paths}}
        ancestor = store.read(ancestor["previous"]) if ancestor.get("previous") else {}
    retained = {key: check for key, check in retained.items() if "witness" in check}
    paths = list(dict.fromkeys(plan.safe_path(path) for path in receipt["checkPaths"]))
    missing = set(paths) - set(gitwork.paths(root, candidate))
    if missing:
        raise ValueError(f"Check files absent from candidate: {', '.join(sorted(missing))}.")
    witness = {"commit": candidate, "paths": paths}
    submitted = {f"milestone-{milestone}:{c['vision']}": {
        **c, "requirements": [c["vision"]], "witness": witness,
        **({"replacement": True} if c["vision"] not in current else {})} for c in receipt["checks"]}
    walk = f"milestone-{milestone}:walkthrough"
    submitted[walk] = {**receipt["walkthrough"], "requirements": ["tryItGuide"], "witness": witness}
    return retained, submitted, [*(key for key in walks if key in retained), walk]


def gaps(store, candidate, observations):
    """Return observed check failures to the fixup owner; never change a unit or the run."""
    document = plan.execution(store)
    units = plan.select(document, "unit")
    contracts = plan.select(document, "contract") if features.enabled("checkContract") else {}
    result = []
    for ref in observations:
        body = store.read(ref)
        if body["commit"] != candidate:
            raise ValueError("Check gaps require observations from the candidate commit")
        if failures(store, [ref]):
            writer = plan.select(document, "check")[body["check"]]["owner"]
            handed = units[writer]["handed"]
            # Work its writer hands to no unit of this wave is a plan gap, never a wrong owner's fixup.
            # An area named before the planner gave it a unit now means that unit.
            owners = [r for o in handed for r in [u for u, b in units.items()
                                                  if b["area"] == o and u != "guide"] or [o]]
            unowned = [o for o in owners if o not in units]
            # A writer's handoff alone can be an ordinary defect. If its fixer hands work
            # across a provider/consumer edge, let the planner settle the reported disagreement.
            evidence = [f"{writer} → {owner}: {text}" for owner, text in handed.items()]
            agreements = {}
            for fixer in owners if contracts and not unowned else ():
                for peer, correction in units[fixer]["handed"].items():
                    shared = {r: c for r, c in contracts.items()
                              if (c["provider"] == fixer and peer in c["consumers"])
                              or (c["provider"] == peer and fixer in c["consumers"])}
                    if shared:
                        agreements.update(shared)
                        evidence.append(f"{fixer} → {peer}: {correction}")
            result.append({"subject": body["check"],
                           "kind": "contract" if agreements else "plan" if unowned else "check",
                           "commit": candidate, "units": sorted(agreements) or unowned or owners or [writer],
                           "correction": "\n".join([*(evidence if agreements else handed.values()),
                                                     explain([body])]), "source": ref})
    return result


def prove(store, ref, contract, checks):
    from dataclasses import replace
    unit = store.unit(ref)
    root = Path(unit.worktree)
    if unit.P is None:
        T, P, changed = gitwork.snapshots(root, unit.base, contract["scope"],
                                         contract["checkPaths"], store.cursor().scratch)
        with store.transaction():
            store.record("patch", {"unit": ref, "base": unit.base, "T": T, "P": P, "paths": changed})
            unit = replace(unit, T=T, P=P)
            store.unit(ref, unit)
    observations = []
    try:
        red = observe(store, root, unit.T, plan.assigned(plan.execution(store))[ref], "T", ref)
        observations.extend(red)
        baseline = {store.read(r)["check"]: store.read(r)
                    for r in red if store.read(r)["exitCode"] == 0 and not failures(store, [r])}
        document = plan.execution(store)
        if baseline:
            # Remove product only; keep every unit's check machinery as it stands at T (T-104).
            mechanics = [p for u in plan.select(document, "unit").values() for p in u["checkPaths"]]
            start = gitwork.check_commit(root, store.cursor().waveStart, unit.T,
                                         mechanics, store.cursor().scratch)
            store.record("proof_base", {"unit": ref, "commit": start, "waveStart": store.cursor().waveStart,
                                        "product": unit.P})
            start_red = observe(store, root, start, baseline, "wave-start", ref)
            observations.extend(start_red)
        gitwork.checkout(root, unit.P)
        green = observe(store, root, unit.P, checks, "P", ref)
        observations.extend(green)
        bad = failures(store, green)
        with store.transaction():
            store.unit(ref, replace(store.unit(ref), observations=observations))
        if bad:
            raise Failed("Proof failed: ", bad)
    except UncertainCleanup:
        raise
    except (ValueError, RuntimeError, OSError):
        # A worker resumes its full patch, never a temporary tests-only or integration checkout.
        gitwork.checkout(root, unit.P)
        raise
    return observations
