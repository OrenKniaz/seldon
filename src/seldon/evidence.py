"""C14/C15/C26: ordinary bounded commands, raw observations, exact candidates."""

import os
import uuid
from pathlib import Path

from .processes import execute


def observe(project, declarations):
    results = []
    for ref, body in declarations.items():
        folder = project / ".seldon" / ("evidence-" + uuid.uuid4().hex)
        folder.mkdir()
        result = execute(body["command"], project, env={**os.environ,
                         "SELDON_EVIDENCE_DIR": str(folder)})
        results.append({"test": ref, "command": body["command"],
            "requirements": body["requirements"], **result,
            "evidenceFiles": [str(p) for p in folder.rglob("*") if p.is_file()]})
    return results


def save(store, attempt, purpose, candidate, results):
    observations = []
    for result in results:
        body = {key: value for key, value in result.items()
                if key not in ("stdout", "stderr", "evidenceFiles")}
        body.update(attempt=attempt, purpose=purpose, candidate=candidate,
                    outputRefs=[store.artifact(result[s], attempt) for s in ("stdout", "stderr")],
                    artifactRefs=[store.artifact(Path(p).read_bytes(), attempt)
                                  for p in result["evidenceFiles"]])
        observations.append(store.record("observation", body))
    return observations
