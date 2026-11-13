"""C15/C26; R9/R10: every accumulated check observes one integrated commit, without repair."""

from concurrent.futures import ThreadPoolExecutor

from . import gitwork, guide, plan, proof


def verify(store, candidate):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    assignments = plan.assigned(document)
    owned = plan.select(document, "unit")

    def observe_unit(ref):
        try:
            checks, root = assignments[ref], store.root.parent
            tree = gitwork.scope_tree(root, candidate, owned[ref]["scope"], cursor.scratch)
            if ref == "guide":
                # Read the committed guide; observe owns the disposable execution checkout.
                root = store.unit(ref).worktree
                gitwork.clean(root, candidate)
                try:
                    checks = guide.checks(root)
                except ValueError as error:
                    return [store.record("observation", {
                        "check": "guide:format", "unit": ref, "commit": candidate, "purpose": "final",
                        "requirements": owned[ref]["requirements"], "command": [], "cwd": str(root),
                        "scopeTree": tree, "evidenceFiles": [], "executionStarted": False,
                        "timedOut": False, "cleanupVerified": True, "exitCode": None,
                        "stdout": "", "stderr": str(error)})]
            return proof.observe(store, root, candidate, checks, "final", ref, tree)
        finally:
            store.close()

    capacity = store.read(cursor.kickoff)["capacity"]
    with ThreadPoolExecutor(max_workers=capacity) as pool:
        results = list(pool.map(observe_unit, owned))
    wave = proof.observe(store, store.root.parent, candidate, assignments[None], "final")
    return [ref for refs in results for ref in refs] + wave
