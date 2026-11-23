"""C14/C15/C26/I1; R6/R9: code observes ordinary commands on exact Git commits."""

import os
import threading
import uuid
from pathlib import Path

from . import features, gitwork, observations as evidence_facts
from .processes import UncertainCleanup as UncertainCleanup
from .processes import execute
from .providers import ROLES


_observer = threading.local()


class Failed(ValueError):
    """Failing checks keep their names, so a repeat of the same failure is recognised (R15)."""

    def __init__(self, message, failed):
        super().__init__(message + explain(failed))
        self.checks = sorted({body["check"] for body in failed})


def observe(store, root, commit, declarations, purpose, unit=None, scope_tree=None, *, candidate=None):
    observations = []
    if not declarations:
        return observations
    setup = store.read(store.cursor().kickoff)["setup"]
    timeout = setup.get("providers", {}).get("check", ROLES["check"])["timeout"]
    scratch = Path(store.cursor().scratch)
    checkout = getattr(_observer, "checkout", None)
    checkout = checkout if checkout and checkout.parent == scratch else None
    witness_commits, trees, executions = {}, {}, {}
    walkthroughs = evidence_facts.final_guide_checks(store) if purpose == "final" else ()
    try:
        for ref, check in declarations.items():
            retained = check.get("witness")
            executed_commit = commit
            if retained and retained["commit"] != commit and retained["paths"]:
                key = retained["commit"], tuple(retained["paths"])
                if key not in witness_commits:
                    witness_commits[key] = gitwork.check_commit(
                        root, commit, key[0], key[1], scratch, literal=True)
                executed_commit = witness_commits[key]
            if candidate and executed_commit not in trees:
                trees[executed_commit] = gitwork.git(root, "rev-parse", f"{executed_commit}^{{tree}}")
            key = (trees[executed_commit], tuple(check["command"])) if candidate else (commit, ref)
            executions.setdefault(str(key), (executed_commit, {}))[1][ref] = check
        cached = {row["physicalWitness"]: (ref, row) for ref, _, row in store.records("observation")
                  if row.get("candidate") == candidate and row["purpose"] == purpose} if candidate else {}
        for witness, (executed_commit, linked) in executions.items():
            ref, check = next(iter(linked.items()))
            command = list(check["command"])
            retained = check.get("witness")
            requirements = list(dict.fromkeys(r for row in linked.values() for r in row["requirements"]))
            walk = "tryItGuide" in requirements if candidate else ref in walkthroughs
            if witness in cached:
                original, body = cached[witness]
                if not body["cleanupVerified"]:
                    raise UncertainCleanup("Command cleanup unverified: " + str(body))
                coverage = {"checks": list(linked), "requirements": requirements}
                observations.append(original if all(body[k] == v for k, v in coverage.items()) else
                                    store.record("observation_projection", {**body, **coverage}))
                continue
            if checkout is None:
                _observer.checkout = checkout = Path(gitwork.admit(root, scratch, executed_commit))
            else:
                gitwork.checkout(checkout, executed_commit)
            runtime = scratch / ("check-" + uuid.uuid4().hex)
            evidence, temp = runtime / "evidence", runtime / "temp"
            evidence.mkdir(parents=True)
            temp.mkdir()
            if (checkout / command[0]).is_file():
                command[0] = str((checkout / command[0]).resolve())
            result = execute(command, checkout, timeout=timeout,
                             env={**os.environ, "SELDON_EVIDENCE_DIR": str(evidence),
                                  **{key: str(temp) for key in ("TEMP", "TMP", "TMPDIR")}})
            cleanup_verified = result["cleanupVerified"]
            body = {k: v[-4096:].decode("utf-8", errors="replace") if isinstance(v, bytes) else v
                    for k, v in result.items()}
            body.update(evidence_facts.runtime(result, checkout, temp, evidence, walkthrough=walk,
                                              commit=executed_commit))
            files = [store.artifact(p.read_bytes(), ref) for p in evidence.rglob("*")
                     if p.is_file() and not (p.parent == evidence and evidence_facts.runtime_file(p.name))]
            body.update(check=ref, unit=unit, commit=commit, purpose=purpose,
                        requirements=requirements, command=check["command"], cwd=str(checkout),
                        evidenceFiles=files, cleanCheckout=True, actualCommand=command)
            if candidate:
                body.update(candidate=candidate, checks=list(linked), physicalWitness=witness)
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
    except BaseException:
        # A failed execution or evidence write can leave descendants or dirty files.
        _observer.checkout = None
        raise
    return observations


def executed(body):
    return (body["executionStarted"] and not body["timedOut"] and body["cleanupVerified"]
            and body["exitCode"] is not None)


def failures(store, observations):
    return [body for body in map(store.read, observations) if not executed(body) or body["exitCode"] != 0]


def explain(failed):
    fields = ("check", "purpose", "command", "executionStarted", "timedOut", "cleanupVerified",
              "exitCode", "stdout", "stderr")
    return str([{field: body[field] for field in fields} for body in failed])


def image(content):
    return content.startswith((b"\x89PNG", b"\xff\xd8"))


def account(store, refs, delivered=None):
    """Render the runner's observations for both judgment paths."""
    rows = []
    for ref in refs:
        body = store.read(ref)
        fields = ("commit", "check", "requirements", "purpose", "exitCode", "stdout", "stderr",
                  "executionStarted", "cleanupVerified", "timedOut", "cleanCheckout", "witness", "executedCommit")
        row = {key: body[key] for key in fields if key in body}
        row.update(evidence_facts.judge_fields(body), command=body.get("actualCommand", body["command"]),
                   workingDirectory=body["cwd"], ranOnDeliveredRevision=body["commit"] == delivered)
        row["artifacts"] = {}
        for path in body["evidenceFiles"]:
            content = Path(path).read_bytes()
            artifact = row["artifacts"][path] = {"bytes": len(content)}
            if image(content):
                continue
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if text not in body["stdout"] and text not in body["stderr"]:
                artifact["text"] = text[-4096:]
        rows.append(row)
    return rows


def milestone_checks(store, previous, milestone, current, receipt, candidate, workspace=None):
    retained = dict(previous.get("checks", {}))
    submitted = receipt["checks"] + ([{**walkthrough, "promises": ["tryItGuide"]}]
                                     if (walkthrough := receipt.get("walkthrough")) else [])
    root = store.root.parent
    # Backfill pre-witness receipts from the builder's recorded command and candidate.
    ancestor = previous
    while any("witness" not in c for c in retained.values()) and ancestor:
        old = store.read(ancestor["return"])["answer"]
        commit = ancestor["commit"]
        paths = [p for p in gitwork.paths(root, commit) if gitwork.matches(p, old["checkPaths"])]
        commands = [c["command"] for c in [*old["checks"], old.get("walkthrough")] if c]
        for key, check in retained.items():
            if ("witness" not in check and key.startswith(f"milestone-{ancestor['milestone']}-")
                    and check["command"] in commands):
                check.update(witness={"commit": commit, "paths": paths})
        ancestor = store.read(ancestor["previous"]) if ancestor.get("previous") else {}
    retained = {key: check for key, check in retained.items() if "witness" in check}
    tracked = set(gitwork.paths(root, candidate))
    paths, omitted = [], set()
    normalise = features.enabled("leadPlausibleGuard")
    arguments = {arg for check in submitted for arg in check["command"]}
    for path in receipt["checkPaths"]:
        if workspace and (submitted_path := Path(path)).anchor:
            absolute = Path(os.path.abspath(path)) if submitted_path.is_absolute() else None
            relative = absolute.relative_to(workspace).as_posix() if absolute and absolute.is_relative_to(workspace) else None
            if relative not in tracked:
                omitted.add(path)
                continue
            path = relative
        if normalise and path not in arguments and path not in tracked:
            omitted.add(path)
        else:
            paths.append(gitwork.safe_path(path))
    if missing := set(paths) - tracked:
        required = missing & arguments
        if required or not features.enabled("leadSeldonOwns"):
            raise ValueError(f"Check files absent from candidate: {', '.join(sorted(required or missing))}.")
    paths = list(dict.fromkeys(path for path in paths if path in tracked))
    carried = {gap["promise"] for gap in previous.get("carriedWitnesses", ())}
    renewed = {f"milestone-{milestone}:{key}": {
        "command": c["command"], "requirements": [key], "witness": {"commit": candidate, "paths": paths}}
        for c in submitted for key in (c.get("promises") or [c["vision"]])
        if key in current or key in carried or key == "tryItGuide"}
    replaced = carried & {c["requirements"][0] for c in renewed.values()}
    return ({key: c for key, c in retained.items() if replaced.isdisjoint(c["requirements"])} | renewed,
            sorted(missing | omitted))


def gaps(store, candidate, observations):
    """Return observed check failures to the fixup owner; never change a unit or the run."""
    from . import plan
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
    from . import plan
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
