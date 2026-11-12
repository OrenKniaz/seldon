"""C14/C15/C26/I1; R6/R9: code observes ordinary commands on exact Git commits."""

import os
import uuid
from pathlib import Path

from . import gitwork
from .processes import execute
from .providers import ROLES


class UncertainCleanup(RuntimeError):
    pass


def observe(store, root, commit, declarations, purpose, unit=None, scope_tree=None, disposable=False):
    observations = []
    setup = store.read(store.cursor().kickoff)["setup"]
    timeout = setup.get("providers", {}).get("check", ROLES["check"])["timeout"]
    for ref, check in declarations.items():
        evidence = Path(store.cursor().scratch) / ("evidence-" + uuid.uuid4().hex)
        evidence.mkdir()
        command = list(check["command"])
        if (Path(root) / command[0]).is_file():
            command[0] = str((Path(root) / command[0]).resolve())
        result = execute(command, root, timeout=timeout,
                         env={**os.environ, "SELDON_EVIDENCE_DIR": str(evidence)})
        body = {k: v[-4096:].decode("utf-8", errors="replace") if isinstance(v, bytes) else v
                for k, v in result.items()}
        files = [store.artifact(p.read_bytes(), ref) for p in evidence.rglob("*") if p.is_file()]
        body.update(check=ref, unit=unit, commit=commit, purpose=purpose,
                    requirements=check["requirements"], command=check["command"], cwd=str(root),
                    evidenceFiles=files)
        if scope_tree is not None:
            body["scopeTree"] = scope_tree
        with store.transaction():
            observations.append(store.record("observation", body))
        if not result["cleanupVerified"]:
            raise UncertainCleanup("Command cleanup unverified: " + str(body))
    if not disposable:  # A disposable copy (the guide walk) may gain the outputs its steps create.
        gitwork.unchanged(root, commit)
    return observations


def failures(store, observations, red=False):
    failed = []
    for ref in observations:
        body = store.read(ref)
        usable = (body["executionStarted"] and not body["timedOut"] and body["cleanupVerified"]
                  and body["exitCode"] is not None)
        if not usable or (body["exitCode"] == 0 if red else body["exitCode"] != 0):
            failed.append(body)
    return failed


def explain(failed):
    fields = ("check", "purpose", "command", "executionStarted", "timedOut", "cleanupVerified",
              "exitCode", "stdout", "stderr")
    return str([{field: body[field] for field in fields} for body in failed])


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
        establishes = {r: c for r, c in checks.items() if c["mode"] == "establishes"}
        gitwork.checkout(root, unit.T)
        red = observe(store, root, unit.T, establishes, "T", ref)
        observations.extend(red)
        # T contains the current check sources on the pre-unit product. A newly written
        # preservation check must exercise that product before it can claim existing behavior.
        preserves = {r: c for r, c in checks.items() if c["mode"] == "preserves"}
        base = observe(store, root, unit.T, preserves, "base", ref) if preserves else []
        observations.extend(base)
        passing_T = {store.read(r)["check"] for r in red if store.read(r)["exitCode"] == 0
                     and not failures(store, [r])}
        start_red = []
        if passing_T:
            start = gitwork.check_commit(root, store.cursor().waveStart, unit.P,
                                         contract["checkPaths"], store.cursor().scratch)
            store.record("proof_base", {"unit": ref, "commit": start, "waveStart": store.cursor().waveStart,
                                        "product": unit.P})
            gitwork.checkout(root, start)
            start_red = observe(store, root, start, {r: establishes[r] for r in passing_T}, "wave-start", ref)
            observations.extend(start_red)
        proven_red = {store.read(r)["check"] for r in [*red, *start_red]
                      if not failures(store, [r], red=True)}
        undetected = [store.read(r)["check"] for r in start_red if store.read(r)["exitCode"] == 0]
        gitwork.checkout(root, unit.P)
        green = observe(store, root, unit.P, checks, "P", ref)
        observations.extend(green)
        bad = [store.read(r) for r in [*red, *start_red] if store.read(r)["check"] not in proven_red]
        bad += failures(store, [*base, *green])
        with store.transaction():
            store.unit(ref, replace(store.unit(ref), observations=observations))
        if bad:
            if undetected:
                raise ValueError("this check does not detect the missing requirement; make it assert it, "
                                 "or declare it preserves: " + ", ".join(undetected) + "; " + explain(bad))
            raise ValueError("Proof failed: " + explain(bad))
    except UncertainCleanup:
        raise
    except (ValueError, RuntimeError, OSError):
        # A worker resumes its full patch, never a temporary tests-only or integration checkout.
        gitwork.checkout(root, unit.P)
        raise
    return observations
