"""C14/C15/C26: ordinary bounded commands, raw observations, exact candidates."""

import os
import json
import uuid
from pathlib import Path

from .processes import execute
from .store import digest

REPORT = {"toolReady": True, "checks": [{"name": "actual runner check", "status": "passed|failed|skipped"}]}


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
        try:
            supplied = json.loads((folder / "execution.json").read_text(encoding="utf-8"))
            report = {key: supplied[key] for key in ("toolReady", "checks")}
            if (type(report["toolReady"]) is not bool or not isinstance(report["checks"], list)
                    or any(not isinstance(c["name"], str) or not c["name"] or c["status"] not in ("passed", "failed", "skipped") for c in report["checks"])):
                raise ValueError("Invalid execution report")
        except (OSError, ValueError, KeyError, TypeError) as error:
            report = {"toolReady": False, "checks": [], "reportError": str(error)}
        results.append({"test": ref, "command": body["command"], "cwd": str(project),
            "requirements": body["requirements"], "testIdentity": digest(body), **result, **report,
            "evidenceFiles": [str(p) for p in folder.rglob("*") if p.is_file()]})
        if not result["cleanupVerified"]:
            break
    return results


def save(store, attempt, purpose, candidate, results, document):
    observations = []
    for result in results:
        body = {key: (value[-4096:].decode("utf-8", errors="replace") if isinstance(value, bytes) else value)
                for key, value in result.items() if key != "evidenceFiles"}
        inputs = store.read(candidate)["files"]
        if document["nodes"][result["test"]]["body"]["stage"] == "verification":
            inputs = {p: h for p, h in inputs.items() if p != "TRY.txt"}
        products = {p for n in document["nodes"].values() if n["kind"] == "bead" and n["body"]["task"] == "build"
                    for p in n["body"]["ownedPaths"] if p != "TRY.txt"}
        body.update(attempt=attempt, purpose=purpose, candidate=candidate, inputs=inputs,
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
    for ref in refs:
        body = store.read(ref)
        test = document["nodes"].get(body["test"], {}).get("body")
        applies = (test is not None and digest(test) == body["testIdentity"]
                   and all(delivered.get(p) == h for p, h in {**body["inputs"], **body["products"]}.items()))
        statuses = [c["status"] for c in body["checks"]]
        usable = (body["executionStarted"] and body["cleanupVerified"] and not body["timedOut"]
                  and body["exitCode"] is not None and body["toolReady"] and any(s != "skipped" for s in statuses))
        result[ref] = {**body, "applies": applies, "inputsByteIdentical": applies,
            "executionUsable": usable, "passed": usable and body["exitCode"] == 0 and all(s == "passed" for s in statuses),
            "failed": usable and body["exitCode"] != 0 and "failed" in statuses}
    return result


def ready(facts, purpose):
    values = list(facts.values())
    return (all(o["applies"] and o["executionUsable"] for o in values)
            and (any(o["failed"] for o in values) if purpose == "RED" else all(o["passed"] for o in values)))
