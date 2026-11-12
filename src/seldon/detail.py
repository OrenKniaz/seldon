"""C1/C9/C12/C23; R3/R4: CODE fans out bounded area questions and merges one approval plan."""

from concurrent.futures import ThreadPoolExecutor

from . import architecture, inference, plan, questions
from .states import advance


def gap(store, need):
    cursor = store.cursor()
    if any(b["vision"] == cursor.vision for _, _, b in store.records("architecture_gap")):
        raise ValueError("Contract gap remains after one ARCHITECT re-ask: " + need)
    with store.transaction():
        ref = store.record("architecture_gap", {"vision": cursor.vision, "gap": need})
        advance(store, "contract-gap", ref)


def step(store, provider, capacity):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    outline = document["architecture"]
    done = {b["area"]: b["answer"] for _, _, b in store.records("area_detail")
            if b["revision"] == cursor.revision}

    def ask(area):
        try:
            if area["handle"] in done:
                return done[area["handle"]]
            relevant = {r: c for r, c in architecture.index(outline["contracts"]).items()
                        if any(architecture.endpoint(a, c)
                               for a in [area["handle"], *area.get("ancestors", [])])}
            local = {r: {**c,
                "provider": area["handle"] if c["provider"] in area.get("ancestors", []) else c["provider"],
                "consumers": [area["handle"] if a in area.get("ancestors", []) else a
                              for a in c["consumers"]]}
                for r, c in relevant.items()}

            def validate(answer):
                if answer.get("verdict") == "subdivide":
                    architecture.subdivision(area, answer, architecture.index(outline["contracts"]))
                elif answer.get("verdict") == "planned":
                    architecture.local_plan(area, local, answer)
                    if len(outline["areas"]) == 1:
                        vision = store.read(cursor.vision)["answer"]
                        plan.merge(vision, outline, [(area, answer)])

            result, _ = inference.call(store, provider, questions.detail_brief(store, outline, area),
                                       check=validate)
            store.record("area_detail", {
                "revision": cursor.revision, "area": area["handle"], "answer": result})
            return result
        finally:
            store.close()

    leaves, refs = [], []
    pending = outline["areas"]
    with ThreadPoolExecutor(max_workers=capacity) as pool:
        while pending:
            batch, pending = pending, []
            results = list(pool.map(ask, batch))
            for area, ref in zip(batch, results, strict=True):
                refs.append(ref)
                answer = store.read(ref)["answer"]
                if answer["verdict"] == "contract_gap":
                    gap(store, area["responsibility"] + ": " + answer["need"])
                    return
                if answer["verdict"] == "subdivide":
                    outline = architecture.expand(outline, area, answer)
                    pending.extend(a for a in outline["areas"]
                                   if a["handle"] in architecture.index(answer["subareas"]))
                else:
                    leaves.append((area, answer))
    vision = store.read(cursor.vision)["answer"]
    try:
        compiled = plan.add_guide(plan.merge(vision, outline, leaves))
    except architecture.ContractGap as error:
        gap(store, str(error))
        return
    with store.transaction():
        revision = store.revision(compiled, cursor.revision)
        advance(store, "detailed", refs, revision=revision)
