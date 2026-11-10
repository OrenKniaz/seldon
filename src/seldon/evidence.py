"""C14/C15/C26: ordinary bounded commands, raw observations, exact candidates."""

import os
import uuid
from pathlib import Path

from .processes import execute
from .store import digest


def observe(project, declarations, scratch):
    results = []
    for ref, body in declarations.items():
        folder = scratch / ("evidence-" + uuid.uuid4().hex)
        folder.mkdir()
        command = body["command"]
        if (project / command[0]).is_file():
            command = [str((project / command[0]).resolve()), *command[1:]]
        result = execute(command, project, env={**os.environ,
                         "SELDON_EVIDENCE_DIR": str(folder)})
        results.append({"test": ref, "command": body["command"], "cwd": str(project),
            "requirements": body["requirements"], "testIdentity": digest(body), **result,
            "evidenceFiles": [str(p) for p in folder.rglob("*") if p.is_file()]})
        if not result["cleanupVerified"]:
            break
    return results


def save(store, attempt, purpose, candidate, results, document):
    observations = []
    for result in results:
        body = {key: (value[-4096:].decode("utf-8", errors="replace") if isinstance(value, bytes) else value)
                for key, value in result.items() if key != "evidenceFiles"}
        scopes = {r: n["body"] for r, n in document["nodes"].items() if n["kind"] == "bead"}
        owners = {r for r, scope in scopes.items() if result["test"] in scope["tests"]}
        while True:
            expanded = owners | {d for r in owners for d in scopes[r]["dependencies"]}
            if expanded == owners:
                break
            owners = expanded
        paths = {p for r in owners for p in scopes[r]["ownedPaths"]}
        if document["nodes"][result["test"]]["body"]["stage"] == "verification":
            paths.discard("TRY.txt")
        inputs = {p: store.read(candidate)["files"].get(p) for p in paths}
        products = {p for r in owners for p in scopes[r]["ownedPaths"] if p not in scopes[r]["testPaths"] and p != "TRY.txt"}
        obligations = {r: digest(document["nodes"][r]) for owner in owners for r in scopes[owner]["requirements"]}
        units = store.units()
        scope_identities = {r: [digest(scopes[r]), units[r].get("checkGeneration", 0)] for r in owners}
        body.update(attempt=attempt, purpose=purpose, candidate=candidate, inputs=inputs, obligations=obligations, scopes=scope_identities,
                    testPaths=sorted({p for r in owners for p in scopes[r]["testPaths"]}),
                    products={p: store.read(candidate)["files"].get(p) for p in products},
                    outputRefs=[store.artifact(result[s], attempt) for s in ("stdout", "stderr")],
                    artifactRefs=[store.artifact(Path(p).read_bytes(), attempt)
                                  for p in result["evidenceFiles"]])
        observations.append(store.record("observation", body))
    return observations


def facts(store, refs, candidate, document):
    """I6: the sole applicability/execution owner, shared by judgment and C."""
    delivered = store.read(candidate)["files"]
    result = {}
    units = store.units()
    for ref in refs:
        body = store.read(ref)
        test = document["nodes"].get(body["test"], {}).get("body")
        applies = (test is not None and digest(test) == body["testIdentity"]
                   and all(delivered.get(p) == h for p, h in {**body["inputs"], **body["products"]}.items())
                   and all(r in document["nodes"] and digest(document["nodes"][r]) == h for r, h in body["obligations"].items()))
        applies = applies and all(r in document["nodes"] and r in units and
            [digest(document["nodes"][r]["body"]), units[r].get("checkGeneration", 0)] == identity
            for r, identity in body["scopes"].items())
        usable = (body["executionStarted"] and body["cleanupVerified"] and not body["timedOut"]
                  and body["exitCode"] is not None)
        result[ref] = {**body, "applies": applies, "inputsByteIdentical": applies,
            "productAbsent": bool(body["products"]) and all(h is None for h in body["products"].values()),
            "testSources": {p: (store.root / "artifacts" / body["inputs"][p]).read_text(encoding="utf-8", errors="replace")
                            for p in body["testPaths"] if body["inputs"].get(p) is not None},
            "executionUsable": usable, "passed": usable and body["exitCode"] == 0,
            "failed": usable and body["exitCode"] != 0}
    return result


def ready(facts, purpose):
    values = list(facts.values())
    return (all(o["applies"] and o["executionUsable"] for o in values)
            and (any(o["failed"] for o in values) if purpose == "RED" else all(o["passed"] for o in values)))
