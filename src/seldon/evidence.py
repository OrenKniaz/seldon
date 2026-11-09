"""C14/C15/C26: ordinary bounded commands, raw observations, exact candidates."""

import os
import subprocess
import time


def hidden_process_kwargs(creationflags=0):
    """C20: lifted window policy from process_launch; no legacy runtime import."""
    if os.name == "nt":
        return {"creationflags": creationflags | subprocess.CREATE_NO_WINDOW}
    return {}


def execute(command, cwd, timeout=15):
    """A day-one bounded subprocess; descendant containment is not established."""
    start = time.monotonic()
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, timeout=timeout,
                                **hidden_process_kwargs())
        code, timed_out, started = result.returncode, False, True
        stdout, stderr = result.stdout, result.stderr
    except subprocess.TimeoutExpired as error:
        code, timed_out, started = None, True, True
        stdout, stderr = error.stdout or b"", error.stderr or b""
    except OSError as error:
        code, timed_out, started = None, False, False
        stdout, stderr = b"", str(error).encode("utf-8")
    return {"exitCode": code, "timedOut": timed_out, "executionStarted": started,
            "cleanupVerified": None, "seconds": time.monotonic() - start,
            "stdout": stdout, "stderr": stderr}


def observe(project, declarations):
    return [{"test": ref, "command": body["command"], "requirements": body["requirements"],
             **execute(body["command"], project)} for ref, body in declarations.items()]


def save(store, attempt, purpose, candidate, results):
    observations = []
    for result in results:
        body = {key: value for key, value in result.items() if key not in ("stdout", "stderr")}
        body.update(attempt=attempt, purpose=purpose, candidate=candidate,
                    outputRefs=[store.artifact(result[s], attempt) for s in ("stdout", "stderr")],
                    artifactRefs=[])
        observations.append(store.record("observation", body))
    return observations
