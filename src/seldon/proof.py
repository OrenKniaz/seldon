"""C14/C15/C26/I1; R6/R9: code observes ordinary commands on exact Git commits."""

import os
import threading
import uuid
from pathlib import Path

from . import gitwork, plan
from .processes import execute
from .providers import ROLES


_observer = threading.local()


class UncertainCleanup(RuntimeError):
    pass


def observe(store, root, commit, declarations, purpose, unit=None, scope_tree=None):
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
    try:
        for ref, check in declarations.items():
            runtime = scratch / ("check-" + uuid.uuid4().hex)
            evidence, temp = runtime / "evidence", runtime / "temp"
            evidence.mkdir(parents=True)
            temp.mkdir()
            command = list(check["command"])
            if (checkout / command[0]).is_file():
                command[0] = str((checkout / command[0]).resolve())
            dirty = True
            cleanup_verified = False
            result = execute(command, checkout, timeout=timeout,
                             env={**os.environ, "SELDON_EVIDENCE_DIR": str(evidence),
                                  **{key: str(temp) for key in ("TEMP", "TMP", "TMPDIR")}})
            cleanup_verified = result["cleanupVerified"]
            body = {k: v[-4096:].decode("utf-8", errors="replace") if isinstance(v, bytes) else v
                    for k, v in result.items()}
            files = [store.artifact(p.read_bytes(), ref) for p in evidence.rglob("*") if p.is_file()]
            body.update(check=ref, unit=unit, commit=commit, purpose=purpose,
                        requirements=check["requirements"], command=check["command"], cwd=str(checkout),
                        evidenceFiles=files)
            if scope_tree is not None:
                body["scopeTree"] = scope_tree
            with store.transaction():
                observations.append(store.record("observation", body))
            if not cleanup_verified:
                raise UncertainCleanup("Command cleanup unverified: " + str(body))
            gitwork.clean(checkout, commit)
            dirty = False
    finally:
        # Never reset/reuse a workspace whose descendants may still be running.
        if not cleanup_verified:
            _observer.checkout = None
        elif dirty:
            gitwork.clean(checkout, commit)
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


def gaps(store, candidate, observations):
    """Return observed check failures to the fixup owner; never change a unit or the run."""
    document = plan.execution(store)
    result = []
    for ref in observations:
        body = store.read(ref)
        if body["commit"] != candidate:
            raise ValueError("Check gaps require observations from the candidate commit")
        if failures(store, [ref]):
            owners = [plan.select(document, "check")[body["check"]]["owner"]]
            result.append({"subject": body["check"], "kind": "check", "commit": candidate,
                           "units": owners, "correction": explain([body]), "source": ref})
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
            raise ValueError("Proof failed: " + explain(bad))
    except UncertainCleanup:
        raise
    except (ValueError, RuntimeError, OSError):
        # A worker resumes its full patch, never a temporary tests-only or integration checkout.
        gitwork.checkout(root, unit.P)
        raise
    return observations
