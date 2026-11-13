"""C1/C9/C12/C14; R1-R4: approved area plans execute through the canonical unit and proof loop."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path, PurePosixPath

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
                        unit["dependencies"] = list(unit["reads"])
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
    """One versus fifty reaches B; growing siblings cannot enlarge the same area's REVIEW.

    The original fixture also grows each area from one to ten units. Report that changing-subject
    maximum separately; comparing an unchanged area catches the old whole-plan leak directly.
    """
    sizes = []
    review_maxima = []
    for count in (1, 50):
        run, _ = start(root / str(count), count, "sizing")
        try:
            while run.store.cursor().state != "EXECUTE":
                assert run.store.cursor().state not in ("INCOMPLETE", "SCRIPTED_C")
                run.step()
            sizes.append(question_sizes(run.store))
            review_maxima.append(max(len(encode(q).encode()) for _, _, q in run.store.records("question")
                                     if q["kind"] == "REVIEW"))
            document = run.store.plan(run.store.cursor().revision)
            assert len(plan.select(document, "unit")) == count + 1
            assert len(plan.select(document, "area")) == (1 if count == 1 else 5)
        finally:
            run.close()
    assert sizes[0] == sizes[1], sizes
    print(f"ARCHITECT/DETAIL bytes 1 vs 50: {sizes[0]} / {sizes[1]}", flush=True)
    print(f"REVIEW growing-area maximum bytes 1 vs 50: {review_maxima}", flush=True)

    class SameAreaProvider(AreaProvider):
        def areas(self):
            groups = [[1], *[list(range(i, min(i + 10, self.count + 1)))
                             for i in range(2, self.count + 1, 10)]]
            return [{"handle": f"area-{i:03}", "responsibility": "Deliver named values",
                     "scope": [f"unit-{n:03}/**" for n in numbers],
                     "commitments": ["scopeIn:1", "quality:1", "quality:2"]}
                    for i, numbers in enumerate(groups, 1)]

    review_sizes = []
    for count in (1, 50):
        run, base = start(root / "same-area" / str(count), count, "sizing")
        run.provider = SameAreaProvider(base.slot, count, "sizing")
        try:
            while run.store.cursor().state != "EXECUTE":
                run.step()
            question, = [q for _, _, q in run.store.records("question")
                         if q["kind"] == "REVIEW" and q["facts"]["Review subject"] == "area-001"]
            assert len(plan.select(question["facts"]["Plan"], "unit")) == 1
            review_sizes.append(len(encode(question).encode()))
        finally:
            run.close()
    assert review_sizes[0] == review_sizes[1], review_sizes
    print(f"REVIEW same-area bytes 1 vs 50: {review_sizes}", flush=True)
    return sizes


class CorrectionProvider(AreaProvider):
    def __call__(self, question):
        answer = json.loads(super().__call__(question))
        if question["kind"] == "DETAIL" and question["facts"]["Area"]["handle"] == "area-002":
            facts, body = question["facts"], answer["answer"]
            if (self.defect == "local" and "Exact corrections" not in facts
                    or self.defect == "local-persistent" and "Merge correction" not in facts):
                next(e for e in body["edits"] if e["kind"] == "check")["typedBody"][
                    "requirements"].append("link-1")
            elif self.defect in ("path", "persistent") and (
                    self.defect == "persistent" or "Merge correction" not in facts):
                body["units"][0].update(reads=["value.txt"], dependencies=["value.txt"])
            elif self.defect == "endpoint" and "Merge correction" not in facts:
                body["units"][0]["consumes"] = []
        if (question["kind"] == "DETAIL" and self.defect == "cycle"
                and question["facts"]["Area"]["handle"] == "area-001"
                and "Merge correction" not in question["facts"]):
            answer["answer"]["units"][0]["dependencies"] = ["unit-003/value.txt"]
        return json.dumps(answer)


class ScopeProvider(AreaProvider):
    def __call__(self, question):
        facts = question["facts"]
        if (question["kind"] == "DETAIL" and facts["Area"]["handle"] == "area-002"
                and facts["Area"]["scope"] == ["unit-002/value.txt"]):
            self.detail_calls.append(deepcopy(question))
            full = planned(3, "architect")
            unit = {k: v for k, v in full["units"][1].items() if k != "area"}
            unit.update(scope=["unit-002/value.txt"], provides=["link-2"], consumes=["link-1"],
                        reads=["unit-001/value.txt"], dependencies=["unit-001/value.txt"])
            return json.dumps({"answer": {"verdict": "planned", "edits": full["edits"][3:6],
                                          "units": [unit], "quality": [], "why": "Need space for my check"}})
        answer = json.loads(super().__call__(question))
        if question["kind"] == "ARCHITECT" and self.arch_calls == 1:
            answer["answer"]["areas"][1]["scope"] = ["unit-002/value.txt"]
        return json.dumps(answer)


def single_obligation_journey(root):
    """A single-unit obligation exhausts 3+1 answers, then deliberately resumes to approved B.

    Multi-unit ownership tests miss the absent single-unit skeleton. This catches a merge ValueError
    escaping repair, resetting the retry count or losing the stopped run's supported continuation.
    """
    class ObligationProvider(AreaProvider):
        repaired = False

        def __call__(self, question):
            answer = json.loads(super().__call__(question))
            if question["kind"] == "DETAIL" and not self.repaired:
                answer["answer"]["edits"].append({"kind": "obligation", "targetHandle": "new:placeholder",
                    "typedBody": {"what": "Close the value placeholder", "owner": 1,
                                  "closingRequirement": "new:r001"}})
            return json.dumps(answer)

    run, base = start(root, count=1, mode="sizing")
    provider = ObligationProvider(base.slot, 1, "sizing")
    run.provider = provider
    try:
        assert run.run().state == "INCOMPLETE"
        assert len(provider.detail_calls) == 4
        outcome = run.store.records("outcome")[-1][2]
        assert "area-001" in outcome["reason"] and "skeleton" in outcome["reason"]
        assert "Run.resume_planning()" in outcome["nextAction"]
        assert not run.store.cursor().B
        run.close()
        run = Run.reopen(root / "product", provider, approve)
        provider.repaired = True
        run.resume_planning()
        while run.store.cursor().state != "EXECUTE":
            run.step()
        assert run.store.cursor().B and len(provider.detail_calls) == 5
    finally:
        run.close()


def collapsing_scope_journey(root):
    """An architecture collapse revalidates an unchanged subject and corrects its obsolete obligation.

    Ordinary scope repair keeps the area count unchanged. It misses a formerly valid cached skeleton
    answer becoming invalid solely because ARCHITECT removes a redundant area.
    """
    class CollapseProvider(Scripted):
        arch_calls = 0
        detail_calls = []

        def __call__(self, question):
            kind, facts = question["kind"], question["facts"]
            if kind == "ARCHITECT":
                self.arch_calls += 1
                areas = [{"handle": "area-001", "responsibility": "Deliver named values",
                          "scope": ["unit-001/**"], "commitments": list(architecture.commitments(VISION))}]
                if self.arch_calls == 1:
                    areas.append({"handle": "area-002", "responsibility": "Alternate value delivery",
                                  "scope": ["unit-002/value.txt"],
                                  "commitments": list(architecture.commitments(VISION))})
                answer = {"areas": areas, "contracts": [], "decisions": [],
                          "waves": [{"goal": "Deliver the named value"}]}
            elif kind == "DETAIL":
                self.detail_calls.append(deepcopy(question))
                number = 1 if facts["Area"]["handle"] == "area-001" else 2
                full = planned(2, "architect")
                edits = full["edits"][3*(number-1):3*number]
                unit = {k: v for k, v in full["units"][number-1].items() if k != "area"}
                if number == 1 and "Exact corrections" not in facts:
                    edits.append({"kind": "obligation", "targetHandle": "new:placeholder", "typedBody": {
                        "what": "Close the value placeholder", "owner": 1, "closingRequirement": "new:r001"}})
                if number == 2:
                    unit["scope"] = ["unit-002/value.txt"]
                answer = {"verdict": "planned", "edits": edits, "units": [unit],
                          "quality": full["quality"] if number == 1 else []}
            else:
                return super().__call__(question)
            return json.dumps({"answer": {**answer,
                "why": "One complete writer suffices for the accepted value"}})

    root.mkdir(parents=True)
    provider = CollapseProvider(root, "collapse", 2)
    run = Run(root / "product", root / "workers", "Read named values", provider.setup, provider, approve)
    try:
        for _ in range(16):
            if run.store.cursor().state == "EXECUTE":
                break
            run.step()
        assert run.store.cursor().state == "EXECUTE" and run.store.cursor().B
        assert provider.arch_calls == 2
        a = [q for q in provider.detail_calls if q["facts"]["Area"]["handle"] == "area-001"]
        assert len(a) == 3 and "Exact corrections" in a[-1]["facts"]
        assert not plan.select(run.store.plan(run.store.cursor().revision), "obligation")
    finally:
        run.close()


def correction_journeys(root):
    """Bad DETAIL records recover to B locally; persistent cross-area defects name their owner.

    The retained contract-gap journey cannot catch a malformed use of an existing agreement, or a
    local check accidentally assigned to wave verification. These use the real answer validator,
    merger, review and B; unrelated areas must keep their original answers, including across an
    architecture scope correction. Deliberate resume must retain siblings after a bounded stop.
    """
    began = time.monotonic()
    for mode in ("path", "persistent", "local", "endpoint", "local-persistent", "cycle", "scope"):
        run, provider = start(root / mode, mode="contracts")

        provider_type = ScopeProvider if mode == "scope" else CorrectionProvider
        provider = provider_type(provider.slot, 3, "contracts")
        provider.defect = mode
        provider.barrier = None
        provider.interrupted = True
        run.provider = provider
        try:
            if mode == "persistent":
                assert run.run().state == "INCOMPLETE"
                reason = run.store.records("outcome")[-1][2]["reason"]
                assert "After one DETAIL re-ask" in reason and "area-002" in reason and "value.txt" in reason
                assert not any(b["gate"] == "B" for _, _, b in run.store.records("decision_request"))
            else:
                while run.store.cursor().state != "EXECUTE":
                    run.step()
                assert {b["gate"] for _, _, b in run.store.records("decision_request")} == {"A", "B"}
                assert not run.store.units()
            reasks = [q for q in provider.detail_calls if "Merge correction" in q["facts"]]
            assert sorted(q["facts"]["Area"]["handle"] for q in reasks) == (
                [] if mode in ("local", "scope") else ["area-001", "area-002", "area-003"] if mode == "cycle"
                else ["area-002"])
            assert any("Exact corrections" in q["facts"] for q in provider.detail_calls) == (
                mode in ("local", "local-persistent"))
            if mode == "local-persistent":
                assert len([q for q in provider.detail_calls
                            if q["facts"]["Area"]["handle"] == "area-002"]) == 4
            assert provider.arch_calls == (2 if mode == "scope" else 1)
            if mode == "scope":
                assert [q["facts"]["Area"]["handle"] for q in provider.detail_calls].count("area-001") == 1
                assert [q["facts"]["Area"]["handle"] for q in provider.detail_calls].count("area-003") == 1
            if mode == "persistent":
                assert "Run.resume_planning()" in run.store.records("outcome")[-1][2]["nextAction"]
                calls = len(provider.detail_calls)
                run.close()
                run = Run.reopen(root / mode / "product", provider, approve)
                assert run.store.cursor().state == "INCOMPLETE" and len(provider.detail_calls) == calls
                provider.defect = "path"
                run.resume_planning()
                while run.store.cursor().state != "EXECUTE":
                    run.step()
                assert [q["facts"]["Area"]["handle"] for q in provider.detail_calls[calls:]] == ["area-002"]
        finally:
            run.close()
    single_obligation_journey(root / "single-obligation")
    collapsing_scope_journey(root / "collapse")
    print(f"DETAIL correction journeys: {time.monotonic()-began:.2f}s", flush=True)


class RecordedDetailProvider(Scripted):
    """Recorded first replies; new scripted replies answer only observed correction questions."""

    def __init__(self, slot, recording):
        super().__init__(slot, "recorded-detail", 1)
        self.recording, self.arch_calls, self.detail_calls = recording, 0, []

    def __call__(self, question):
        kind, facts = question["kind"], question["facts"]
        if kind == "ALIGN":
            return self.recording["align"]
        if kind == "ARCHITECT":
            self.arch_calls += 1
            if "Architecture correction" not in facts:
                return self.recording["architect"]
            answer = deepcopy(json.loads(self.recording["architect"])["answer"])
            for area in answer["areas"]:
                area["scope"].append(f"checks/{area['handle']}/**")
            return json.dumps({"answer": answer})
        if kind != "DETAIL":
            return super().__call__(question)
        self.detail_calls.append(deepcopy(question))
        area = facts["Area"]["handle"]
        raw = self.recording["details"][area]["raw"]
        if "5e3e40e" in self.recording["source"]:
            if not any(k in facts for k in ("Exact corrections", "Merge correction")):
                return raw
            answer = deepcopy(facts.get("Previous area plan") or json.loads(raw)["answer"])
            handles = {e["targetHandle"] for e in answer["edits"] if e["kind"] == "requirement"}
            consumers = {c["handle"] for c in json.loads(self.recording["architect"])["answer"]["contracts"]
                         if area in c["consumers"]}
            if area == "page":
                # One page owner writes shell and assembly checks; component-dom is its own output.
                units = answer["units"]
                answer["units"] = [{**units[0],
                    **{k: list(dict.fromkeys(p for u in units for p in u[k]))
                       for k in ("scope", "checkPaths", "requirements")},
                    "consumes": sorted(consumers), "dependencies": [], "reads": []}]
            for edit in answer["edits"]:
                if edit["kind"] == "check":
                    edit["typedBody"]["requirements"] = [r for r in edit["typedBody"]["requirements"]
                                                         if r in handles | consumers]
            for unit in answer["units"]:
                # Scripted local-proof corrections need not duplicate check ownership in product scope.
                # Keep check-only units' scope; product owners deliberately omit their check subtree.
                product = [p for p in unit["scope"] if not plan.matches(p, unit["checkPaths"])]
                if product:
                    unit["scope"] = product
                for req in unit["requirements"]:
                    if any(e["kind"] == "check" and req in e["typedBody"]["requirements"]
                           and set(e["typedBody"]["requirements"]) <= set(unit["requirements"])
                           for e in answer["edits"]):
                        continue
                    requirement = next(e["typedBody"] for e in answer["edits"] if e["targetHandle"] == req)
                    answer["edits"].append({"kind": "check", "targetHandle": req + "-local", "typedBody": {
                        "purpose": "Prove the local contribution: " + requirement["obligation"],
                        "mode": "establishes", "command": ["node", unit["checkPaths"][0], "--local", req],
                        "requiredEnvironment": ["Supplied Node, Edge and Playwright"],
                        "expectedEvidence": "Observe local behavior against the agreed interface; "
                                            "integrated checks still exercise the canonical page.",
                        "requirements": [req]}})
                if "Merge correction" in facts:
                    assert any("dependency cycle" in c["error"] for c in facts["Merge correction"])
                    # Local proof uses the agreed interface; sibling assembly checks run at wave level.
                    unit["reads"] = []
                    unit["dependencies"] = []
            return json.dumps({"answer": answer})
        if f"checks/{area}/**" not in facts["Area"]["scope"]:
            return raw
        # The architecture correction supplies space. This new DETAIL reply places its own checks there.
        answer = deepcopy(facts.get("Previous area plan") or json.loads(raw)["answer"])
        paths = {p: f"checks/{area}/{PurePosixPath(p).name}"
                 for u in answer["units"] for p in u["checkPaths"]}
        for unit in answer["units"]:
            unit["scope"] = list(dict.fromkeys([*unit["scope"], f"checks/{area}/**"]))
            unit["checkPaths"] = [paths[p] for p in unit["checkPaths"]]
        for edit in answer["edits"]:
            if edit["kind"] == "check":
                edit["typedBody"]["command"] = [paths.get(a, a) for a in edit["typedBody"]["command"]]
        if "Exact corrections" in facts:
            # The unit proves its local contribution; the original integrated checks remain at wave level.
            for unit in answer["units"]:
                for req in unit["requirements"]:
                    local = [e for e in answer["edits"] if e["kind"] == "check"
                             and set(e["typedBody"]["requirements"]) <= set(unit["requirements"])]
                    if any(req in e["typedBody"]["requirements"] for e in local):
                        continue
                    requirement = next(e["typedBody"] for e in answer["edits"] if e["targetHandle"] == req)
                    if req in ("new:page-assembly", "new:offline-composition"):
                        requirement["obligation"] = ("Reference the agreed local deferred script entrypoints "
                            "from the canonical page, with no page-owned interaction or external resources.")
                    answer["edits"].append({"kind": "check", "targetHandle": req + "-local", "typedBody": {
                        "purpose": "Prove the local contribution: " + requirement["obligation"],
                        "mode": "establishes", "command": ["node", unit["checkPaths"][0], "--local", req],
                        "requiredEnvironment": ["Supplied Node, Edge and Playwright"],
                        "expectedEvidence": "Observe the actual local product against the agreed interface; "
                                            "cross-area interaction remains an integrated wave check.",
                        "requirements": [req]}})
        if "Merge correction" in facts:
            assert area == "page-shell"
            assert any("planned writer" in c["error"] for c in facts["Merge correction"])
            # Shell-local checks need no sibling implementation; integration runs after the units integrate.
            answer["units"][0].update(dependencies=[], reads=[])
        answer["why"] = ("Correct the observed ownership/check boundary; "
                         "retain the approved product and wave checks.")
        return json.dumps({"answer": answer})


def recorded_detail_journeys(root):
    """Real planning failures reach reviewed B through recorded, observable corrections.

    Synthetic valid plans cannot expose the literal scopes, unowned bare file paths or wave-only unit
    proof in these replies. Raw first answers reach the real validator; only subsequent correction
    questions receive new scripted answers. No provider implementation or execution acceptance is claimed.
    The 5e3e40e corrections omit duplicated check scope, catching both local and merged ownership
    rejection that the older recordings' scripted replies (which repeat check scopes) cannot expose.
    """
    began = time.monotonic()
    recordings = json.loads(Path(__file__).with_name("recorded_detail.json").read_text("utf-8"))
    for recording in recordings:
        slot = root / recording["source"].replace("\\", "/").rsplit("/", 1)[-1]
        slot.mkdir(parents=True)
        provider = RecordedDetailProvider(slot, recording)
        run = Run(slot / "product", slot / "workers", recording["dream"], provider.setup,
                  provider, approve, capacity=6)
        try:
            for _ in range(16):
                if run.store.cursor().state == "EXECUTE":
                    break
                assert run.store.cursor().state != "INCOMPLETE", run.store.records("outcome")
                run.step()
            assert run.store.cursor().state == "EXECUTE" and run.store.cursor().B
            assert not run.store.units()
            rows = run.store.records()
            replies = [b["rawAnswer"] for _, k, b in rows if k == "provider_reply"]
            assert recording["architect"] in replies
            assert all(d["raw"] in replies for d in recording["details"].values())
            owns_checks = "5e3e40e" in recording["source"]
            assert provider.arch_calls == (1 if owns_checks else 2)
            assert len(run.store.records("architecture_gap")) == (0 if owns_checks else 1)
            assert any(k == "transition" and b["next"] == "REVIEW" for _, k, b in rows)
            assert {b["gate"] for _, k, b in rows if k == "decision_request"} == {"A", "B"}
            assert any("Exact corrections" in q["facts"] for q in provider.detail_calls)
            wave = json.loads(recording["architect"])["answer"]["waves"][0]
            assert all(q["facts"]["Current wave"] == wave
                       for q in provider.detail_calls)
            corrections = [q for q in provider.detail_calls if "Merge correction" in q["facts"]]
            assert sorted(q["facts"]["Area"]["handle"] for q in corrections) == (
                ["foundation", "seldon"] if owns_checks else
                ["page-shell"] if "2073b1d-c01" in slot.name else [])
            document = run.store.plan(run.store.cursor().revision)
            plan.validate(document)
            assert len(plan.assigned(document)[None]) > 0
            if owns_checks:
                assert not any("checkPaths must be inside scope" in json.dumps(q["facts"])
                               for q in provider.detail_calls)
                units = plan.select(document, "unit")
                assert all(any(plan.contains(s, c) for s in u["scope"])
                           for u in units.values() for c in u["checkPaths"])
                # The canonical producer reply omits check scope; its persisted consumer plan owns it.
                answers = [json.loads(raw)["answer"] for raw in replies]
                omitted = [u for a in answers for u in a.get("units", [])
                           if any(not any(plan.contains(s, c) for s in u["scope"])
                                  for c in u["checkPaths"])]
                assert omitted
                assert all(any(u["checkPaths"] == original["checkPaths"]
                               and set(original["scope"]) <= set(u["scope"]) for u in units.values())
                           for original in omitted)
            print(f"Recorded {slot.name}: DETAIL corrections -> REVIEW -> B",
                  flush=True)
        finally:
            run.close()
    print(f"Recorded DETAIL journeys: {time.monotonic()-began:.2f}s", flush=True)


def architect_journeys(tmp_path):
    """One and fifty units get B; the largest planning brief has identical bytes.

    A single-call plan passes L0 but grows with unit count and hits the answer ceiling at fifty.
    This exercises the real question recorder, merge and B, rather than sizing fixtures.
    """
    began = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        sizing = pool.submit(size_journey, tmp_path / "size")
        correction_journeys(tmp_path / "corrections")
        recorded_detail_journeys(tmp_path / "recorded")
        area_product_journey(tmp_path)
        for mode in ("gap", "unresolved", "split"):
            area_recovery_journey(tmp_path, mode)
        sizes = sizing.result()
    print(f"ARCHITECT size journey: {time.monotonic()-began:.2f}s", flush=True)
    return sizes


def retained_area_answer(provider, q):
    """Keep the retained fault/recording corpus on the actual area-planning path."""
    if provider.mode == "user-revise" and "Merge correction" in q["facts"]:
        assert q["facts"]["Merge correction"][0]["error"] == (
            "Revise the plan: describe the exact stored value.")
        provider.plan_revised = True
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
