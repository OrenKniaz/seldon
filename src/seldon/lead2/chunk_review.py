"""C13/C16: check a finished chunk away from the continuing builder."""

import json
from pathlib import Path

from .. import desktop, gitwork, inference
from ..processes import UncertainCleanup, execute
from ..providers import ROLES
from . import build_brief
from .store import SpecStore


def progress(store):
    reviews = {row["chunk"]: (ref, row) for ref, _, row in store.records("lead2_chunk_review")}
    return {row.get("delivery", row["feature"]): (ref, row, reviews.get(ref))
            for ref, _, row in store.records("lead2_chunk")}


def feature_status(order, chunks, sent):
    features = {}
    for ref, name in order.items():
        key, _, verdict = chunks.get(ref, chunks.get(name, (None, {}, None)))
        result = "building"
        if key:
            result = "under review"
        if verdict:
            review, assessment = verdict
            if assessment["findings"]:
                result = "fixing" if review in sent else "failed"
            elif assessment.get("verdict") == "can't-test-here":
                result = "not-verifiable-here"
            elif assessment.get("verdict") != "pending":
                result = "passed"
        features[name] = result
    return features


def sent_feedback(store, last):
    current = store.db.execute("""
        SELECT body -> 'execution' -> 'feedback' FROM record
        WHERE kind = 'question' AND body ->> 'kind' = 'WORK' ORDER BY id DESC LIMIT 1
    """).fetchone()
    return set(last.get("feedback", [])) | set(json.loads(current[0]) if current and current[0] else [])


def venue_limits(store):
    refs = {ref for ref, _, _ in progress(store).values()}
    limits = []
    for _, _, row in store.records("lead2_chunk_review"):
        if row["chunk"] in refs:
            limits.extend(row.get("limits", []))
            if row.get("verdict") == "can't-test-here" and row.get("reason"):
                limits.append(row["reason"])
    return list(dict.fromkeys(limits))


def review(store, provider, ref, chunk):
    try:
        project, candidate = store.root.parent, chunk["candidate"]
        cursor = store.cursor()
        timeout = store.read(cursor.kickoff)["setup"].get("providers", {}).get("check", ROLES["check"])
        results = _checks(project, cursor.scratch, chunk, timeout["timeout"])
        findings = [f"Check failed: {r['command']}\n{r['output']}" for r in results if not r["passed"]]
        args = {a.replace("\\", "/").removeprefix("./").rstrip("/")
                for row in results for a in row["command"][1:]}
        paths = {p for p in gitwork.paths(project, candidate)
                 if any(p == a or p.startswith(a + "/") for a in args)}
        sources = gitwork.blobs(project, candidate, ("**",), named=paths)
        delivery = chunk.get("delivery", chunk["feature"])
        prior = {r for r, _, row in store.records("lead2_chunk")
                 if row.get("delivery", row["feature"]) == delivery}
        previous = next((r["findings"] for _, _, r in reversed(store.records("lead2_chunk_review"))
                         if r["chunk"] in prior), None)
        task = ("Recheck listed findings and identify any new material gap." if previous is not None else
                "Check the feature's local tests for a concrete material gap.")
        limits = [obs.result for obs in SpecStore(store).observations()
                  if obs.outcome == "not-verifiable-here"]
        question = build_brief.question("LEAD2-CHUNK", build_brief.CHUNK,
            task + " Review product behavior. B already records planning choices. "
            "Use broken when any local failure needs repair. Use can't-test-here when the only gap is "
            "unavailable venue or device access. List every such limit even when there is a local defect. "
            "Give a reason for the verdict.",
            Feature=chunk["feature"], Checks=results,
            Prior_findings=previous or [], Unverified_for_C=limits,
            Tests={p: data.decode("utf-8", "replace") for p, data in sources.items()})

        def save(_, answer):
            if answer["verdict"] == "broken" and answer["reason"]:
                findings.append(answer["reason"])
            store.record("lead2_chunk_review", {"chunk": ref, "checks": results, "findings": findings,
                                                 **answer})

        inference.call(store, provider, question, normalise=build_brief.normalise, consume=save)
    finally:
        store.close()  # Only the review thread's SQLite connection.


def _checks(project, scratch, chunk, timeout):
    checkout = gitwork.admit(project, scratch, chunk["candidate"])
    env = desktop.environment({"SELDON_EVIDENCE_DIR": str(Path(checkout) / ".seldon-review")})
    output, quiescent = [], True
    try:
        for args in dict.fromkeys(map(tuple, chunk["commands"])):
            quiescent = False
            run = execute(list(args), checkout, timeout=timeout, env=env)
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
