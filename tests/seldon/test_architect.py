"""C1/C9/C12/C14; R1-R4: approved area plans execute through the canonical unit and proof loop."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from seldon import architecture, plan
from seldon.run import Run
from seldon.store import encode

from test_journey import NATIVE, VISION, Scripted, approve, finish, planned


class AreaProvider(Scripted):
    def __init__(self, slot, count, mode):
        super().__init__(slot, "architect", count)
        self.scenario = mode
        self.arch_calls = 0
        self.detail_calls = []
        self.barrier = threading.Barrier(3) if mode == "contracts" else None
        self.lock = threading.Lock()
        self.interrupted = False

    def areas(self):
        if self.scenario == "split" and self.arch_calls == 1:
            return [{"handle": "whole", "responsibility": "Deliver named values", "scope": ["**"],
                     "commitments": ["scopeIn:1", "quality:1", "quality:2"]}]
        groups = ([[i] for i in range(1, 4)] if self.scenario in ("contracts", "gap", "unresolved")
                  else [list(range(i, min(i + 10, self.count + 1))) for i in range(1, self.count + 1, 10)])
        return [{"handle": f"area-{i:03}", "responsibility": "Deliver named values",
                 "scope": [f"unit-{n:03}/**" for n in (
                     range(1, 11) if self.count == 1 else numbers)],
                 "commitments": ["scopeIn:1", "quality:1", "quality:2"]}
                for i, numbers in enumerate(groups, 1)]

    def __call__(self, question):
        kind, facts = question["kind"], question["facts"]
        if kind == "ARCHITECT":
            self.arch_calls += 1
            contracts = []
            if self.scenario == "contracts" or self.scenario == "gap" and self.arch_calls > 1:
                contracts = [{"handle": f"link-{i}", "kind": "file-format", "text": "UTF-8 value-NNN.",
                              "provider": f"area-{i:03}", "consumers": [f"area-{i+1:03}"]}
                             for i in (1, 2)]
            answer = {"areas": self.areas(), "contracts": contracts, "decisions": [],
                      "waves": [{"goal": "Run the complete product end to end"}]}
        elif kind == "DETAIL":
            area = facts["Area"]
            with self.lock:
                self.detail_calls.append(deepcopy(question))
            if self.barrier:
                self.barrier.wait(timeout=10)
            if (self.scenario == "contracts" and area["handle"] == "area-002"
                    and not self.interrupted):
                self.interrupted = True
                raise RuntimeError("Simulated controller interruption during planning")
            if (self.scenario in ("gap", "unresolved")
                    and area["handle"] == "area-002" and not facts["Contracts"]):
                answer = {"verdict": "contract_gap",
                          "need": "The second value needs the first value's format"}
            elif self.scenario == "split" and area["handle"] == "whole" and "Exact corrections" in facts:
                self.arch_calls += 1
                answer = {"verdict": "subdivide", "subareas": self.areas(), "contracts": []}
            else:
                full = planned(self.count, "architect")
                numbers = [i for i, unit in enumerate(full["units"], 1)
                           if all(any(plan.contains(s, p) for s in area["scope"]) for p in unit["scope"])]
                edits = [e for i in numbers for e in full["edits"][3*(i-1):3*i]]
                units = [{k: v for k, v in full["units"][i-1].items() if k != "area"} for i in numbers]
                for unit in units:
                    unit["provides"] = [r for r, c in facts["Contracts"].items() if c["side"] == "provider"]
                    unit["consumes"] = [r for r, c in facts["Contracts"].items() if c["side"] == "consumer"]
                    if unit["consumes"]:
                        unit["reads"] = [f"unit-{numbers[0]-1:03}/value.txt"]
                answer = {"verdict": "planned", "edits": edits, "units": units,
                          "quality": full["quality"] if 1 in numbers else []}
        else:
            return super().__call__(question)
        return json.dumps({"answer": {**answer, "why": "The area owns this responsibility"}})


def start(slot, count=3, mode="contracts"):
    slot.mkdir(parents=True)
    (slot / "mode").write_text("architect")
    native = NATIVE.replace('value = f"value-{number:03}"',
        'value = f"value-{number:03}"\n'
        '    if facts.get("Contracts consumed"):\n'
        '        previous = Path(f"unit-{number-1:03}/value.txt").read_text()\n'
        '        assert previous == f"value-{number-1:03}"\n'
        '        value = f"value-{int(previous.split(chr(45))[1])+1:03}"')
    (slot / "native.py").write_text(native, encoding="utf-8")
    provider = AreaProvider(slot, count, mode)
    return Run(slot / "product", slot / "workers", "Read named values", provider.setup,
               provider, approve, capacity=6), provider


def question_sizes(store):
    return {kind: max(len(encode(q).encode("utf-8")) for _, _, q in store.records("question")
                      if q["kind"] == kind) for kind in ("ARCHITECT", "DETAIL")}


def area_product_journey(tmp_path):
    """Three contracted areas reach C with overlapping DETAIL calls and scoped facts.

    A global PLAN can pass the old delivery journey while serializing planning and leaking sibling
    context. A barrier catches serialization; actual dependent workers read their integrated providers.
    Reopen during DETAIL catches lost completed area answers, which a worker-only crash cannot expose.
    """
    began = time.monotonic()
    run, provider = start(tmp_path / "contracts")
    try:
        while run.store.cursor().state != "DETAIL":
            run.step()
        with pytest.raises(RuntimeError, match="controller interruption"):
            run.step()
        completed = {b["area"] for _, _, b in run.store.records("area_detail")}
        assert completed == {"area-001", "area-003"}
        calls = len(provider.detail_calls)
        run.close()
        provider.barrier = None
        run = Run.reopen(tmp_path / "contracts" / "product", provider, approve)
        finish(run)
        assert not completed.intersection(q["facts"]["Area"]["handle"]
                                          for q in provider.detail_calls[calls:])
        document = run.store.plan(run.store.cursor().revision)
        assert len(plan.select(document, "area")) == 3
        assert len(plan.select(document, "contract")) == 2
        assert provider.arch_calls == 1 and len(provider.detail_calls) == 4
        for question in provider.detail_calls:
            area = question["facts"]["Area"]
            assert question["facts"]["Product in this area"] == {"files": [], "additionalFiles": 0}
            text = encode(question["facts"])
            number = int(area["handle"].split("-")[1])
            assert all(f"unit-{i:03}/" not in text for i in range(1, 4) if i != number)
        assert len([b for _, _, b in run.store.records("decision_request") if b["gate"] == "B"]) == 1
        assert all(run.store.unit(r).state == "DONE" for r in ("u001", "u002", "u003"))
    finally:
        run.close()
    print(f"ARCHITECT three areas/two contracts: {time.monotonic()-began:.2f}s", flush=True)


def area_recovery_journey(tmp_path, mode):
    """Contract gaps get one architectural correction; oversized answers split without losing work.

    Successful fixed-size planning cannot catch DETAIL inventing interfaces, repeated architecture
    loops or truncation at a unit ceiling. The split is checked at B; delivery uses the same merged
    record consumer already exercised by the contracted product and the retained fifty-unit journey.
    """
    began = time.monotonic()
    run, provider = start(tmp_path / mode, 13 if mode == "split" else 3, mode)
    try:
        if mode == "unresolved":
            assert run.run().state == "INCOMPLETE"
            assert "after one ARCHITECT re-ask" in run.store.records("outcome")[-1][2]["reason"]
            assert not any(b["gate"] == "B" for _, _, b in run.store.records("decision_request"))
        else:
            if mode == "split":
                while run.store.cursor().state != "EXECUTE":
                    assert run.store.cursor().state != "INCOMPLETE", run.store.records("outcome")
                    run.step()
                document = run.store.plan(run.store.cursor().revision)
                assert len(plan.select(document, "unit")) == 14
                assert len(plan.select(document, "area")) == 2
                errors = [e for _, _, b in run.store.records("provider_reply") for e in b["errors"]]
                assert any("too many items" in e for e in errors)
            else:
                finish(run)
        if mode in ("gap", "unresolved"):
            assert provider.arch_calls == 2
            assert len(run.store.records("architecture_gap")) == 1
    finally:
        run.close()
    print(f"ARCHITECT {mode}: {time.monotonic()-began:.2f}s", flush=True)


def size_journey(root):
    """One versus fifty reaches B on recorded questions; the retained scale journey executes fifty."""
    sizes = []
    for count in (1, 50):
        run, _ = start(root / str(count), count, "sizing")
        try:
            while run.store.cursor().state != "EXECUTE":
                assert run.store.cursor().state not in ("INCOMPLETE", "SCRIPTED_C")
                run.step()
            sizes.append(question_sizes(run.store))
            document = run.store.plan(run.store.cursor().revision)
            assert len(plan.select(document, "unit")) == count + 1
            assert len(plan.select(document, "area")) == (1 if count == 1 else 5)
        finally:
            run.close()
    assert sizes[0] == sizes[1], sizes
    print(f"ARCHITECT/DETAIL bytes 1 vs 50: {sizes[0]} / {sizes[1]}", flush=True)
    return sizes


def architect_journeys(tmp_path):
    """One and fifty units get B; the largest planning brief has identical bytes.

    A single-call plan passes L0 but grows with unit count and hits the answer ceiling at fifty.
    This exercises the real question recorder, merge and B, rather than sizing fixtures.
    """
    began = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        sizing = pool.submit(size_journey, tmp_path / "size")
        area_product_journey(tmp_path)
        for mode in ("gap", "unresolved", "split"):
            area_recovery_journey(tmp_path, mode)
        sizes = sizing.result()
    print(f"ARCHITECT size journey: {time.monotonic()-began:.2f}s", flush=True)
    return sizes


def retained_area_answer(provider, q):
    """Keep the retained fault/recording corpus on the actual area-planning path."""
    full = json.loads(provider({**q, "kind": "PLAN"}))["answer"]
    units = full["units"]
    areas = [e for e in full["edits"] if e["kind"] == "area"]
    contracts = [deepcopy(e) for e in full["edits"] if e["kind"] == "contract"]
    groups = ([list(range(i, min(i + 10, len(units) + 1)))
               for i in range(1, len(units) + 1, 10)] if len(units) > 12 else
              [[i] for i in range(1, len(units) + 1)] if contracts else [list(range(1, len(units) + 1))])
    # An architectural contract crosses areas; keep its provider and consumer apart.
    for edit in contracts:
        c = edit["typedBody"]
        for consumer in c["consumers"]:
            group = next((g for g in groups if consumer in g and c["provider"] in g), None)
            if group:
                group.remove(consumer)
                groups.append([consumer])
    mapping = {number: (f"new:area-{i:03}" if len(groups) > 1 else "new:area", j)
               for i, group in enumerate(groups, 1) for j, number in enumerate(group, 1)}
    goals = list(architecture.commitments(VISION))
    outline = {"areas": [{"handle": mapping[group[0]][0],
        "responsibility": areas[0]["typedBody"]["responsibility"],
        "scope": (areas[0]["typedBody"]["scope"] if len(groups) == 1 or
                  areas[0]["typedBody"]["scope"] != ["**"] and i == 0 else
                  [s for n in group for s in units[n-1]["scope"]]), "commitments": goals}
        for i, group in enumerate(groups)],
        "contracts": [{"handle": e["targetHandle"], **{k: v for k, v in e["typedBody"].items()
                       if k not in ("provider", "consumers")},
            "provider": mapping.get(e["typedBody"]["provider"], ("missing",))[0],
            "consumers": list(dict.fromkeys(mapping[n][0] for n in e["typedBody"]["consumers"]))}
            for e in contracts],
        "decisions": [{**e["typedBody"], "areas": list(dict.fromkeys(a for a, _ in mapping.values()))}
                      for e in full["edits"] if e["kind"] == "decision"],
        "waves": [{"goal": "Deliver the runnable end-to-end skeleton"}]}
    if q["kind"] == "ARCHITECT":
        if fault := getattr(provider, "architecture_fault", None):
            fault(outline)
        answer = outline
    else:
        area = q["facts"]["Area"]["handle"]
        numbers = next(g for g in groups if mapping[g[0]][0] == area)
        owned = {r for n in numbers for r in units[n-1]["requirements"]}
        features = {f for e in full["edits"] if e["kind"] == "requirement" and e["targetHandle"] in owned
                    for f in e["typedBody"]["features"]}
        edits = [deepcopy(e) for e in full["edits"] if
            e["kind"] == "feature" and e["targetHandle"] in features or
            e["kind"] == "requirement" and e["targetHandle"] in owned or
            e["kind"] == "check" and set(e["typedBody"]["requirements"]) <= owned or
            e["kind"] == "obligation" and e["typedBody"]["owner"] in numbers]
        for e in edits:
            if e["kind"] == "obligation":
                e["typedBody"]["owner"] = mapping[e["typedBody"]["owner"]][1]
        for check in (e for e in full["edits"] if e["kind"] == "check"):
            reqs = set(check["typedBody"]["requirements"])
            if not reqs & owned or reqs <= owned:
                continue
            external = reqs - owned
            links = [e for e in contracts if area in
                     [mapping[n][0] for n in e["typedBody"]["consumers"]]
                     and external <= set(units[e["typedBody"]["provider"]-1]["requirements"])]
            if links:
                local = deepcopy(check)
                local["typedBody"]["requirements"] = [r for r in check["typedBody"]["requirements"]
                                                      if r in owned] + [links[0]["targetHandle"]]
                edits.append(local)
        checks = {e["targetHandle"] for e in edits if e["kind"] == "check"}
        answer = {"verdict": "planned", "edits": edits, "units": [{
            **{k: v for k, v in units[n-1].items() if k not in ("area", "dependencies")},
            "dependencies": [mapping[d][1] if mapping[d][0] == area else f"unit-{d:03}/value.txt"
                             for d in units[n-1]["dependencies"]]}
            for n in numbers], "quality": [{**item, "checks": [c for c in item["checks"] if c in checks]}
                for item in full["quality"] if set(item["checks"]) & checks]}
    return json.dumps({"answer": {**answer, "why": "The retained journey's scoped semantic answer"}})
