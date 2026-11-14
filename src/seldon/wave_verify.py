"""C15/C26; R9/R10: every accumulated check observes one integrated commit, without repair."""

from concurrent.futures import ThreadPoolExecutor

from . import gitwork, plan, proof


def verify(store, candidate):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    assignments = plan.assigned(document)
    owned = plan.select(document, "unit")

    def observe_unit(ref):
        try:
            checks, root = assignments[ref], store.root.parent
            tree = gitwork.scope_tree(root, candidate, owned[ref]["scope"], cursor.scratch)
            return proof.observe(store, root, candidate, checks, "final", ref, tree)
        finally:
            store.close()

    capacity = store.read(cursor.kickoff)["capacity"]
    with ThreadPoolExecutor(max_workers=capacity) as pool:
        results = list(pool.map(observe_unit, owned))
    wave = proof.observe(store, store.root.parent, candidate, assignments[None], "final")
    return [ref for refs in results for ref in refs] + wave
