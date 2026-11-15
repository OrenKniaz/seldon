"""C11; R2/R3: plan approval requires bounded, independently combined area reviews."""

import json
import threading
import time
from copy import deepcopy
from pathlib import Path

from seldon import plan
from seldon.frontdoor import state as run_state
from seldon.run import Run
from test_architect import AreaProvider, start
from test_journey import Scripted, approve, finish, prepare


class ReviewProvider(AreaProvider):
    def __init__(self, slot, mode):
        super().__init__(slot, 3, "contracts")
        self.barrier, self.interrupted = None, True
        self.review_mode = mode
        self.review_calls = []
        self.review_barrier = threading.Barrier(3)

    def __call__(self, question):
        kind, facts = question["kind"], question["facts"]
        if kind == "REVIEW":
            subject = facts["Review subject"]
            with self.lock:
                first = not any(q["facts"]["Review subject"] == subject for q in self.review_calls)
                self.review_calls.append(deepcopy(question))
            if first and subject != "architecture":
                self.review_barrier.wait(timeout=10)
            gap = None
            if (self.review_mode in ("area", "area-records", "persistent") and subject == "area-002"
                    and not any("restart" in c["purpose"] for c in
                                plan.select(facts["Plan"], "check").values())):
                gap = "Plan a check that reads the persisted value after restart"
            if self.review_mode == "cross" and subject == "architecture":
                contract = facts["Architecture outline"]["contracts"][0]
                if "restart" not in contract["text"]:
                    gap = "The first boundary must specify how consumers read a value after restart"
            answer = {"verdict": "gap" if gap else "adequate",
                      "gaps": [{"target": "persistence", "correction": gap}] if gap else [],
                      "why": "The supplied records cover the responsibility and its agreements"}
            return json.dumps({"answer": answer})
        answer = json.loads(super().__call__(question))
        if kind == "ARCHITECT" and "Architecture correction" in facts:
            answer["answer"]["contracts"][0]["text"] += " Consumers can read the value after restart."
        if (kind == "DETAIL" and "Merge correction" in facts
                and self.review_mode in ("area", "area-records")
                and facts["Area"]["handle"] == "area-002"):
            next(e for e in answer["answer"]["edits"] if e["kind"] == "check")[
                "typedBody"]["purpose"] += " after restart"
            if self.review_mode == "area-records":
                body = answer["answer"]
                requirement = deepcopy(next(e for e in body["edits"] if e["kind"] == "requirement"))
                requirement["targetHandle"] = "restart-requirement"
                requirement["typedBody"]["obligation"] += " after restart"
                check = deepcopy(next(e for e in body["edits"] if e["kind"] == "check"))
                check["targetHandle"] = "restart-check"
                check["typedBody"]["requirements"] = ["restart-requirement"]
                body["edits"].extend([requirement, check])
                body["units"][0]["requirements"].append("restart-requirement")
        return json.dumps(answer)


def rule2_journey(root, mode):
    """Foreign quality proof stays visible; changed gaps replan and a repeated outline asks at DETAIL.

    The c05 twin's importer reviewer needs delivery's API/UI check in its proof map.
    Existing local-link reviews miss that omission, and their single-gap repair misses a second,
    unrelated gap being refused by a count instead of answered by ARCHITECT.
    """
    from test_architect import area_recovery_journey

    if mode == "fixed-point":
        area_recovery_journey(root, "unresolved")
        return

    class Rule2Provider(AreaProvider):
        def __init__(self, slot, mode):
            super().__init__(slot, 3, "gap")
            self.rule2_mode = mode

        def __call__(self, question):
            kind, facts = question["kind"], question["facts"]
            if kind == "REVIEW" and facts["Review subject"] != "architecture":
                rows = [row for r, row in facts["Proof map"].items() if r.startswith("quality:")]
                missing = self.rule2_mode == "foreign" and any(not any(
                    c["owner"] == "u003" and "API" in c["purpose"] and "UI" in c["purpose"]
                    for c in row) for row in rows)
                return json.dumps({"answer": {"verdict": "gap" if missing else "adequate",
                    "gaps": [{"target": "quality", "correction": "Name delivery's API/UI check"}]
                            if missing else [], "why": "Review the supplied quality proof locations"}})
            answer = json.loads(super().__call__(question))["answer"]
            if kind == "ARCHITECT":
                if self.rule2_mode == "foreign" and not answer["contracts"]:
                    answer["contracts"] = [
                        {"handle": f"link-{i}", "kind": "file-format", "text": "UTF-8 value-NNN.",
                         "provider": f"area-{i:03}", "consumers": [f"area-{i+1:03}"]} for i in (1, 2)]
                if self.rule2_mode == "progress" and self.arch_calls == 2:
                    answer["contracts"] = answer["contracts"][:1]
            if kind == "DETAIL":
                area = facts["Area"]["handle"]
                if self.rule2_mode == "progress" and self.arch_calls == 2 and area == "area-002":
                    answer = {"verdict": "contract_gap",
                              "need": "The API needs delivery's consuming agreement"}
                if self.rule2_mode == "foreign" and answer["verdict"] == "planned":
                    check = next(e for e in answer["edits"] if e["kind"] == "check")
                    if area == "area-003":
                        check = deepcopy(check)
                        check["targetHandle"] += "-wave"
                        check["typedBody"].update(level="wave",
                                                  command=[*check["typedBody"]["command"], "--api-ui"])
                        answer["edits"].append(check)
                    check["typedBody"]["purpose"] = {
                        "area-001": "Parse the imported CSV rows", "area-002": "Persist imported rows",
                        "area-003": "Import CSV via the API and display the rows in the browser UI"}[area]
                    answer["quality"] = [{"criterion": i, "checks": [check["targetHandle"]]} for i in (1, 2)]
            return json.dumps({"answer": {**answer, "why": "Answer the current area agreement gap"}})

    run, base = start(root / mode, mode="gap")
    run.provider = Rule2Provider(base.slot, mode)
    try:
        while run.store.cursor().state != "B":
            run.step()
        review = run.store.read(run.store.cursor().review)
        assert review["answer"] == {"verdict": "adequate", "gaps": []}
        assert not run.store.records("review_repair")
        if mode == "progress":
            gaps = [b["gap"] for _, _, b in run.store.records("architecture_gap")]
            assert any("second value needs the first" in g for g in gaps)
            assert any("API needs delivery's consuming agreement" in g for g in gaps)
        else:
            importer = run.store.read(review["subjects"]["area-001"])["answer"]
            assert importer["verdict"] == "adequate"
        assert run.step() == "EXECUTE" and run.store.cursor().B
    finally:
        run.close()


def review_journeys(root):
    """Architecture is reviewed before three concurrent area verdicts and one B; gaps replan their owners.

    DETAIL success cannot catch a missing review, serialized fan-out, a false adequate aggregate,
    or lost sibling verdicts after repair/reopen, including when new records renumber sibling handles.
    A cross-area persistence gap must reach ARCHITECT,
    while repeated unchanged answers reach one unresolved-work question rather than approve or loop.
    """
    began = time.monotonic()
    for mode in ("adequate", "area", "area-records", "cross", "persistent"):
        slot = root / mode
        run, base = start(slot)
        provider = ReviewProvider(base.slot, mode)
        run.provider = provider
        try:
            while run.store.cursor().state not in ("B", "REPAIR", "ARCHITECT") or not provider.arch_calls:
                run.step()
            if mode == "cross":
                assert run.store.cursor().state == "ARCHITECT" and not provider.detail_calls
                assert provider.review_calls[0]["facts"]["Review subject"] == "architecture"
                run.close()
                run = Run.reopen(slot / "product", provider, approve)
                while run.store.cursor().state != "B":
                    run.step()
                assert provider.arch_calls == 2
                assert len(provider.review_calls) == 5
                assert all("restart" in q["facts"]["Contracts"]["link-1"]["text"]
                           for q in provider.detail_calls if "link-1" in q["facts"]["Contracts"])
                assert run.step() == "EXECUTE"
                continue
            assert len(provider.review_calls) == 4
            initial = run.store.read(run.store.cursor().review)
            assert set(initial["subjects"]) == {"area-001", "area-002", "area-003", "architecture"}
            for q in provider.review_calls:
                facts = q["facts"]
                if facts["Review subject"] == "architecture":
                    assert "Plan" not in facts and "unit-001" not in json.dumps(facts)
                    assert "Per-area coverage counts" not in facts
                    assert "How the plan executes" not in facts
                    assert "check sources" not in json.dumps(q)
                else:
                    units = plan.select(facts["Plan"], "unit")
                    assert len(units) == 1
                    assert facts["Proof map"] and facts["Contracts"]
            if mode != "adequate":
                assert run.store.cursor().state == "REPAIR" and not run.store.cursor().B
                assert initial["answer"]["gaps"][0]["area"] == (
                    "area-002")
                run.close()
                run = Run.reopen(slot / "product", provider, approve)
                if mode == "persistent":
                    run.run()
                    waiting = run_state(str(run.project))["waitingFor"]
                    assert waiting["gate"] == "REPAIR" and "area-002" in str(waiting["subject"])
                    assert not run.store.cursor().B and not run.store.records("outcome")
                    continue
                while run.store.cursor().state != "B":
                    run.step()
                final = run.store.read(run.store.cursor().review)
                assert initial["subjects"]["area-003"] != final["subjects"]["area-003"]
                if mode in ("area", "area-records"):
                    assert provider.arch_calls == 1
                    assert [q["facts"]["Area"]["handle"] for q in provider.detail_calls[3:]] == ["area-002"]
                    assert {q["facts"]["Review subject"] for q in provider.review_calls[4:]} == (
                        {"area-002", "area-003"})
                    assert initial["subjects"]["architecture"] == final["subjects"]["architecture"]
                    assert initial["subjects"]["area-001"] == final["subjects"]["area-001"]
            assert run.step() == "EXECUTE"
            assert len([b for _, _, b in run.store.records("decision_request") if b["gate"] == "B"]) == 1
            assert run.store.cursor().repairs == (0 if mode == "adequate" else 1)
        finally:
            run.close()
    print(f"Area REVIEW adequate/local/cross/three-repair limit: {time.monotonic()-began:.2f}s", flush=True)


def plan_contract_gap_journey(slot, case):
    """A late shared-semantics gap reaches its contract owner, not endless area DETAIL.

    Outline-only review misses j04's gap discovered from an area's integrated check. The fixed-point
    twin protects accepted commitments; the local-read twin catches needless structural re-asks.
    """
    from seldon import architecture
    from seldon.decisions import Waiting
    from test_journey import VISION

    need = ("Supply an agreed query-semantics contract covering search matching, the calendar-date "
            "basis and expiry-window inclusion rules; the vision lists these only as proposals.")
    agreement = ("Search is a case-insensitive name substring; use the server's local calendar date, "
                 "include overdue items through today plus DAYS, and exclude undated items.")
    if case == "accepted":
        need += " This would change the accepted case-sensitive search and overdue exclusion commitments."
    if case == "derived":
        run, provider = prepare(slot, "ask", 2)
        provider.plan_fault = lambda body: body["units"][1].update(reads=["unit-001/value.txt"])
    else:
        run, base = start(slot, 3, "sizing")

        class ContractProvider(AreaProvider):
            def __init__(self):
                super().__init__(base.slot, 3, "sizing")
                self.vision = deepcopy(VISION)
                self.vision["unknowns"] = ["Proposed: " + agreement]
                if case == "accepted":
                    self.vision["scopeIn"].append("Keep search case-sensitive; never include overdue items.")

            def areas(self):
                return [{"handle": f"area-{i:03}", "responsibility": "Deliver named values",
                         "scope": [f"unit-{i:03}/**"],
                         "commitments": list(architecture.commitments(self.vision))} for i in (1, 2, 3)]

            def __call__(self, question):
                kind, facts = question["kind"], question["facts"]
                if kind == "ALIGN":
                    return json.dumps({"answer": {**self.vision, "why": "The query proposals remain open"}})
                if kind == "REVIEW":
                    missing = facts["Review subject"] == "area-002" and not facts["Contracts"]
                    return json.dumps({"answer": {"verdict": "gap" if missing else "adequate",
                        "gaps": [{"target": "architecture", "correction": need}] if missing else [],
                        "why": "The integrated query check needs agreed semantics"}})
                reply = json.loads(super().__call__(question))
                if kind == "ARCHITECT" and "Architecture correction" in facts and case != "accepted":
                    reply["answer"]["contracts"] = [{"handle": "query-semantics", "kind": "api",
                        "text": agreement, "provider": "area-001", "consumers": ["area-002"]}]
                    reply["answer"]["decisions"] = [{"choice": "Settle the proposed query semantics",
                        "origin": "architecture",
                        "reason": "Makes the accepted query behaviour consistent across areas",
                        "areas": ["area-001", "area-002"]}]
                if kind == "DETAIL" and facts["Area"]["handle"] == "area-002" and facts["Contracts"]:
                    check = deepcopy(next(e for e in reply["answer"]["edits"] if e["kind"] == "check"))
                    check["targetHandle"] = "query-integration"
                    check["typedBody"].update(level="wave", requirements=["new:r002", "query-semantics"])
                    reply["answer"]["edits"].append(check)
                if kind == "DETAIL" and facts["Area"]["handle"] == "area-003":
                    reply["answer"]["units"][0].update(consumes=[], reads=[], dependencies=[])
                return json.dumps(reply)

        run.provider = provider = ContractProvider()
    shown = []

    def user(item):
        shown.append(item)
        if item["gate"] != "A":
            raise Waiting()
        return approve(item)

    run.user = user
    try:
        run.run()
        pending = run_state(str(run.project))["waitingFor"]
        requests = [b for _, _, b in run.store.records("decision_request")]
        if case == "accepted":
            assert pending["gate"] == "REPAIR" and need in str(pending["subject"])
            assert len(requests) == 2 and not run.store.cursor().B
            assert not run.store.records("outcome")
            assert run.store.plan(run.store.cursor().revision)["nodes"]["vision"]["body"]["scopeIn"] == (
                provider.vision["scopeIn"])
            # Lineage exposes a fresh-words question before ending this session; next turn delivers it.
            run.close()
            run = Run.reopen(slot / "product", provider, user)
            run.run()
            assert shown[-1]["request"] == pending["request"]
            assert len([i for i in shown if i["gate"] == "REPAIR"]) == 1
            assert run_state(str(run.project))["waitingFor"] == pending
            return
        assert pending["gate"] == "B" and [b["gate"] for b in requests] == ["A", "B"]
        document = run.store.plan(run.store.cursor().revision)
        assert run.store.read(run.store.cursor().review)["answer"] == {"verdict": "adequate", "gaps": []}
        if case == "derived":
            assert plan.select(document, "unit")["u002"]["dependencies"] == ["u001"]
            assert not run.store.records("detail_correction")
        else:
            assert len(run.store.records("architecture_gap")) == 1
            contract, = plan.select(document, "contract").values()
            assert contract["text"] == agreement and contract["consumers"] == ["u002"]
            crossing, = plan.assigned(document)[None].values()
            assert crossing["requirements"] == plan.select(document, "unit")["u002"]["requirements"]
            assert crossing["integrates"] == list(plan.select(document, "contract"))
            assert not any("Merge correction" in q["facts"] for q in provider.detail_calls)
    finally:
        run.close()


def uncertain_review_journey(root):
    """Unknown review facts remain visible through B, reopen and C; an area can resolve from real facts.

    Adequate/gap journeys miss fatal MissingFacts, repeated empty briefs, and a falsely adequate
    aggregate. The user-facing B subject and terminal outcome must retain the missing judgment.
    """
    run, base = prepare(root, "review-uncertain", 1)

    class UncertainProvider(Scripted):
        def __call__(self, question):
            if question["kind"] != "REVIEW":
                return super().__call__(question)
            facts = question["facts"]
            if facts["Review subject"] == "architecture":
                if "Requested facts" in facts:
                    supplied = facts["Requested facts"]["Available subject facts"]
                    assert supplied["Accepted commitments"] and supplied["Runtime"]
                    assert "have not been written" in supplied["Planning stage"]
                return json.dumps({"answer": {"verdict": "uncertain",
                    "neededFacts": ["A user study confirming that the responsibilities meet the audience needs"],
                    "why": "The outline has no user-study results"}})
            if "Requested facts" not in facts:
                return json.dumps({"answer": {"verdict": "uncertain",
                    "neededFacts": ["The original area plan and the product currently in that area"],
                    "why": "Need the recorded area answer and current product inventory"}})
            supplied = facts["Requested facts"]["Available subject facts"]
            assert supplied["Recorded area plan"]["units"]
            product = supplied["Product in this area"]
            assert product["additionalFiles"] == 0
            assert all(p.startswith("checks/kit/") for p in product["files"])
            return json.dumps({"answer": {"verdict": "adequate", "gaps": [],
                                          "why": "The recorded plan covers this area"}})

    run.provider = provider = UncertainProvider(base.slot, base.mode, 1)
    try:
        while run.store.cursor().state != "B":
            run.step()
        revision = run.store.cursor().revision
        # B is an actual user request, then reopen the run: pending judgment is durable.
        requests = []
        def user(request):
            if request["gate"] == "B":
                requests.append(request)
            return approve(request)
        run.user = user
        run.step()
        review = requests[0]["subject"]["review"]
        assert review["answer"] == {"verdict": "not judged", "gaps": []}
        assert "not judged" in review["reviewTier"]
        assert review["notJudged"]["architecture"]["reason"] == "The outline has no user-study results"
        assert "user study" in review["notJudged"]["architecture"]["neededFacts"][0]
        area = next(s for s in review["subjects"] if s != "architecture")
        assert run.store.read(review["subjects"][area])["answer"]["verdict"] == "adequate"
        run.close()
        run = Run.reopen(root / "product", provider, approve)
        assert run.store.cursor().revision == revision
        finish(run)
        outcome = run.store.records("outcome")[-1][2]
        assert outcome["verification_complete"] is False
        assert outcome["notJudged"]["review:architecture"] == "not judged: The outline has no user-study results"
        qs = [q for _, _, q in run.store.records("question") if q["kind"] == "REVIEW"]
        assert len(qs) == 4
        assert all(qs[i]["facts"] != qs[i+1]["facts"] for i in (0, 2))
    finally:
        run.close()


def recorded_review_journey(root):
    """Replay y01's literal planning/review answers through B, including its concrete keyboard gap.

    Scripted uncertainty misses the real model's requests for details at the wrong review boundary;
    retaining the real area gap catches a fix that reclassifies a concrete defect as not judged.
    Only new fact/correction questions receive scripted replies. No product execution is claimed.
    """
    recording = json.loads(Path(__file__).with_name("recorded_review.json").read_text("utf-8"))
    root.mkdir(parents=True)

    class Replay(Scripted):
        def __init__(self):
            super().__init__(root, "recorded-review", 1)
            self.used = set()
            self.corrected = False

        def __call__(self, question):
            kind, facts = question["kind"], question["facts"]
            subject = facts.get("Review subject", facts.get("Area", {}).get("handle", ""))
            if kind == "REVIEW" and "Requested facts" in facts:
                return json.dumps({"answer": {"verdict": "uncertain",
                    "neededFacts": ["Detailed requirements and checks are not available before DETAIL"],
                    "why": "Not judged: detailed plans are outside the outline review"}})
            if kind == "DETAIL" and "Merge correction" in facts:
                assert subject == "seldon"
                assert "Tab navigation" in facts["Merge correction"][0]["error"]
                answer = deepcopy(facts["Previous area plan"])
                for edit in answer["edits"]:
                    if edit["kind"] == "requirement":
                        edit["typedBody"]["obligation"] += (
                            " Reach the Seldon button with Tab navigation, show focus, activate with Enter/Space.")
                    if edit["kind"] == "check":
                        edit["typedBody"]["purpose"] += (
                            " Reach the Seldon button by Tab, verify visible focus and Enter/Space activation.")
                self.corrected = True
                return json.dumps({"answer": answer})
            if kind == "REVIEW" and subject == "seldon" and self.corrected:
                assert any("by Tab" in c["purpose"] for c in plan.select(facts["Plan"], "check").values())
                return json.dumps({"answer": {"verdict": "adequate", "gaps": [],
                    "why": "The corrected plan includes keyboard reachability and activation"}})
            for i, reply in enumerate(recording["replies"]):
                original_subject = "cross-area" if subject == "architecture" else subject
                if i not in self.used and reply["kind"] == kind and reply["subject"] == original_subject:
                    self.used.add(i)
                    return reply["raw"]
            # A refreshed REVIEW of an already-reviewed subject (peer checks changed) reuses its recorded verdict.
            for i in sorted(self.used, reverse=True):
                reply = recording["replies"][i]
                if kind == "REVIEW" and reply["kind"] == kind and reply["subject"] == subject:
                    return reply["raw"]
            raise AssertionError((kind, subject, list(facts)))

    provider = Replay()
    run = Run(root / "product", root / "workers", recording["dream"], provider.setup,
              provider, approve, capacity=6)
    try:
        for _ in range(20):
            if run.store.cursor().state == "EXECUTE":
                break
            run.step()
        assert run.store.cursor().state == "EXECUTE" and run.store.cursor().B
        # Outcome only: a recorded correction to a since-deleted refusal is simply no longer asked.
        assert provider.corrected
        replies = [b["rawAnswer"] for _, _, b in run.store.records("provider_reply")]
        assert all(recording["replies"][i]["raw"] in replies for i in provider.used)
        reviews = [b for _, _, b in run.store.records("plan_review")]
        assert reviews[0]["answer"]["verdict"] == "gap"
        assert reviews[0]["answer"]["gaps"][0]["area"] == "seldon"
        assert reviews[-1]["answer"] == {"verdict": "not judged", "gaps": []}
        assert not run.store.units()
        print("Recorded y01 ARCHITECT/DETAIL/area REVIEW -> corrected B; architecture not judged", flush=True)
    finally:
        run.close()


def existing_agreement_journey(root, case):
    """T-172 b01 twin: REVIEW receives agreements the merged plan binds, not the outline's endpoints.

    DETAIL may consume an existing contract the outline did not list for its area (b01 r190
    "server-command absent"). Endpoint-aligned fixtures hide that projection, so the reviewer
    calls the agreement missing and the run rewrites its architecture. The missing twin keeps a
    genuinely absent agreement routed to ARCHITECT. A sibling re-emitting an identical plan under a
    new record keeps its own and its consumer's verdicts; record-id bases re-ask both.
    """
    assets = {"handle": "assets", "kind": "file-format", "text": "Static assets live under assets/.",
              "provider": "area-001", "consumers": ["area-002"]}

    class AgreementProvider(AreaProvider):
        def __init__(self, slot):
            super().__init__(slot, 3, "contracts")
            self.barrier, self.interrupted, self.reviews = None, True, []

        def __call__(self, question):
            kind, facts = question["kind"], question["facts"]
            if kind == "REVIEW" and facts["Review subject"] != "architecture":
                self.reviews.append(deepcopy(question))
                gaps = []
                if facts["Review subject"] == "area-003":
                    agreed = {c["handle"]: c for c in facts["Contracts"]}
                    if "assets" not in agreed:
                        gaps = [{"target": "architecture", "correction": "Agree where static assets live"}]
                    else:
                        assert "area-003" in agreed["assets"]["consumers"]
                        peer = next(p["check"] for p in facts["Peer checks"] if p["owner"] == "area-001")
                        if not any("restart" in c["purpose"] for c in
                                   plan.select(facts["Plan"], "check").values()):
                            gaps = [{"target": "area-003", "correction": "Read the assets after restart"},
                                    {"target": peer, "correction": "Confirm the assets check covers restart"}]
                return json.dumps({"answer": {"verdict": "gap" if gaps else "adequate", "gaps": gaps,
                                              "why": "Review the supplied agreements"}})
            reply = json.loads(super().__call__(question))
            answer = reply["answer"]
            if kind == "ARCHITECT" and (case == "existing" or "Architecture correction" in facts):
                answer["contracts"].append(dict(assets))
            # AreaProvider's units consume every non-link agreement, so area-003 binds assets too.
            if kind == "DETAIL" and "Merge correction" in facts and facts["Area"]["handle"] == "area-003":
                next(e for e in answer["edits"] if e["kind"] == "check")["typedBody"]["purpose"] += (
                    " after restart")
            return json.dumps(reply)

    run, base = start(root / case)
    run.provider = provider = AgreementProvider(base.slot)
    try:
        for _ in range(40):
            if run.store.cursor().state == "B":
                break
            run.step()
        reviews = [b for _, _, b in run.store.records("plan_review")]
        assert reviews[-1]["answer"] == {"verdict": "adequate", "gaps": []}
        assert provider.arch_calls == (1 if case == "existing" else 2)
        assert len(run.store.records("architecture_gap")) == (0 if case == "existing" else 1)
        document = run.store.plan(run.store.cursor().revision)
        units = plan.select(document, "unit")
        agreement = next(c for c in plan.select(document, "contract").values() if c["text"] == assets["text"])
        assert any(units[u]["area"] == units[c]["area"] for u in agreement["consumers"]
                   for c in units if "unit-003/**" in units[c]["scope"])
        if case == "existing":
            first, final = reviews[0]["subjects"], reviews[-1]["subjects"]
            assert first["area-001"] == final["area-001"] and first["area-002"] == final["area-002"]
            assert [q["facts"]["Review subject"] for q in provider.reviews[3:]] == ["area-003"]
        assert run.step() == "EXECUTE" and run.store.cursor().B
        calls = {k: sum(q["kind"] == k for _, _, q in run.store.records("question"))
                 for k in ("ARCHITECT", "DETAIL", "REVIEW")}
        print(f"existing-agreement {case}: calls {calls}", flush=True)
    finally:
        run.close()
