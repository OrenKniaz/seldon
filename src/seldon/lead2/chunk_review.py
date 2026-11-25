"""C13/C16: check a finished chunk away from the continuing builder."""

import json

from .. import desktop, gitwork, inference
from ..processes import UncertainCleanup, execute
from ..providers import ROLES
from . import build_brief
from .store import SpecStore


def progress(store):
    reviews = {row["chunk"]: (ref, row["findings"]) for ref, _, row in store.records("lead2_chunk_review")}
    return {row["feature"]: (ref, row, reviews.get(ref)) for ref, _, row in store.records("lead2_chunk")}


def feature_status(order, chunks, sent):
    features = dict.fromkeys(order, "building")
    for feature, (ref, chunk, verdict) in chunks.items():
        key, findings = verdict or (ref, chunk.get("missing", []))
        features[feature] = ("fixing" if key in sent else "failed") if findings else (
            "passed" if verdict else "under review")
    return features


def sent_feedback(store, last):
    current = store.db.execute("""
        SELECT body -> 'execution' -> 'feedback' FROM record
        WHERE kind = 'question' AND body ->> 'kind' = 'WORK' ORDER BY id DESC LIMIT 1
    """).fetchone()
    return set(last.get("feedback", [])) | set(json.loads(current[0]) if current and current[0] else [])


def review(store, provider, ref, chunk):
    try:
        project, candidate = store.root.parent, chunk["candidate"]
        cursor = store.cursor()
        timeout = store.read(cursor.kickoff)["setup"].get("providers", {}).get("check", ROLES["check"])
        results = _checks(project, cursor.scratch, chunk, timeout["timeout"])
        findings = [f"Check failed: {r['command']}\n{r['output']}" for r in results if not r["passed"]]
        args = {a.replace("\\", "/").removeprefix("./").rstrip("/")
                for row in results for a in row["command"][1:]}
        paths = set(chunk["tests"]) | {p for p in gitwork.paths(project, candidate)
                                       if any(p == a or p.startswith(a + "/") for a in args)}
        sources = gitwork.blobs(project, candidate, ("**",), named=paths)
        prior = {r for r, _, row in store.records("lead2_chunk") if row["feature"] == chunk["feature"]}
        previous = next((r["findings"] for _, _, r in reversed(store.records("lead2_chunk_review"))
                         if r["chunk"] in prior), None)
        task = ("Check only whether any listed prior finding still applies." if previous is not None else
                "Check the feature's local tests for a concrete material gap.")
        limits = [obs.result for obs in SpecStore(store).observations()
                  if obs.outcome == "not-verifiable-here"]
        question = build_brief.question("LEAD2-CHUNK", build_brief.CHUNK,
            task + " Review product behavior. B already records planning choices. "
            "Exclude checks recorded as unverified for C. Set needs_fix for unresolved local gaps.",
            Feature=chunk["feature"], Promises=chunk["promises"], Checks=results,
            Prior_findings=previous or [], Unverified_for_C=limits,
            Tests={p: data.decode("utf-8", "replace") for p, data in sources.items()})

        def save(_, answer):
            if answer["needs_fix"] and answer["finding"]:
                findings.append(answer["finding"])
            store.record("lead2_chunk_review", {"chunk": ref, "checks": results, "findings": findings})

        inference.call(store, provider, question, normalise=build_brief.normalise, consume=save)
    finally:
        store.close()  # Only the review thread's SQLite connection.


def _checks(project, scratch, chunk, timeout):
    checkout = gitwork.admit(project, scratch, chunk["candidate"])
    output, quiescent = [], True
    try:
        for args in dict.fromkeys(map(tuple, chunk["commands"])):
            quiescent = False
            run = execute(list(args), checkout, timeout=timeout, env=desktop.environment())
            quiescent = not run["executionStarted"] or run["cleanupVerified"]
            if not quiescent:
                raise UncertainCleanup(f"Awaiting cleanup for {args}")
            logs = (run["stdout"] + b"\n" + run["stderr"]).decode("utf-8", "replace").strip()
            output.append({"command": args, "passed": run["exitCode"] == 0 and not run["timedOut"],
                           "output": logs[-4000:], "seconds": run["seconds"]})
            gitwork.clean(checkout, chunk["candidate"])
        return output
    finally:
        if quiescent:
            gitwork.git(project, "worktree", "remove", "--force", checkout)
