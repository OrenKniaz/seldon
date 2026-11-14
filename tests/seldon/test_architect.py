"""C1/C9/C12/C14; R1-R4: approved area plans execute through the canonical unit and proof loop."""

import base64
import json
import threading
import time
from copy import deepcopy
from pathlib import Path, PurePosixPath

import pytest

from seldon import architecture, plan
from seldon.frontdoor import state as run_state
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
                    unit["consumes"] = [r for r, c in facts["Contracts"].items() if c["side"] == "consumer"
                                        and (not r.startswith("link-") or r == f"link-{numbers[0]-1}")]
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
    """Contract gaps return to architecture until an outline repeats; large answers split without loss.

    Successful fixed-size planning cannot catch DETAIL inventing interfaces, repeated architecture
    loops or truncation at a unit ceiling. The split is checked at B; delivery uses the same merged
    record consumer already exercised by the contracted product and the retained fifty-unit journey.
    """
    began = time.monotonic()
    run, provider = start(tmp_path / mode, 13 if mode == "split" else 3, mode)
    try:
        if mode == "unresolved":
            run.run()
            assert run_state(str(run.project))["status"] == "WAITING"
            waiting = run_state(str(run.project))["waitingFor"]
            assert waiting["gate"] == "DETAIL"
            shown = "\n".join(waiting["subject"]["tried"])
            assert "second value needs the first value's format" in shown
            assert not any(b["gate"] == "B" for _, _, b in run.store.records("decision_request"))
        else:
            if mode == "split":
                while run.store.cursor().state != "EXECUTE":
                    assert run_state(str(run.project))["status"] not in ("PAUSED", "WAITING")
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
    finally:
        run.close()
    print(f"ARCHITECT {mode}: {time.monotonic()-began:.2f}s", flush=True)


def agreed_literals_journey(root):
    """A shared unnamed check value gets one contract revision and reaches B with visible facts.

    The agreed control and missing-agreement twin catch lost ARCHITECT routing or contract/commitment
    delivery; the format-gap journey never consumes an accepted improvement or plans a cross-area
    assertion. Scripted semantic decisions prove the structural path, not real-model judgment.
    """
    literal = "Open reading"
    commitment = "Keep the reading available after restart"
    agreement = f'The reading control has accessible name "{literal}".'

    class LiteralProvider(AreaProvider):
        def areas(self):
            return [{"handle": f"area-{i:03}", "responsibility": "Deliver named values",
                     "scope": [f"unit-{i:03}/**"],
                     "commitments": ["scopeIn:1", "improvements:1", "quality:1", "quality:2"]}
                    for i in (1, 2)]

        def __call__(self, question):
            kind, facts = question["kind"], question["facts"]
            if kind == "DETAIL" and not facts["Contracts"]:
                with self.lock:
                    self.detail_calls.append(deepcopy(question))
                area = facts["Area"]["handle"]
                return json.dumps({"answer": {"verdict": "contract_gap",
                    "need": f'{area} check would assert "{literal}" on the shared reading control',
                    "why": "Two areas need this name but no visible agreement supplies it"}})
            answer = json.loads(super().__call__(question))
            body = answer["answer"]
            if kind == "ALIGN":
                body["improvements"] = [commitment]
            elif kind == "ARCHITECT" and (agreed or self.arch_calls > 1):
                body["contracts"] = [{"handle": "reading-control", "kind": "ui-structure",
                    "text": agreement, "provider": "area-001", "consumers": ["area-002"]}]
            elif kind == "DETAIL":
                # The canonical question, rather than a copied fixture, supplies accepted text.
                requirement = next(e for e in body["edits"] if e["kind"] == "requirement")
                requirement["typedBody"]["obligation"] += (
                    "; " + facts["Accepted commitments"]["improvements:1"])
                if facts["Area"]["handle"] == "area-002":
                    local = requirement["targetHandle"]
                    body["edits"].append({"kind": "check", "targetHandle": "reading-flow",
                        "typedBody": {"purpose": f'Activate the integrated "{literal}" control',
                            "level": "wave", "mode": "preserves",
                            "command": ["python", "-B", "unit-002/flow.py"],
                            "requiredEnvironment": ["Python"], "expectedEvidence": "Reading opens",
                            "requirements": [local, "reading-control"]}})
            return json.dumps(answer)

    for agreed in (True, False):
        run, base = start(root / ("agreed" if agreed else "missing"), 2, "sizing")
        provider = LiteralProvider(base.slot, 2, "sizing")
        run.provider = provider
        try:
            for _ in range(20):
                if run.store.cursor().state == "B":
                    break
                assert run_state(str(run.project))["status"] not in ("PAUSED", "WAITING")
                run.step()
            assert run.store.cursor().state == "B"
            assert provider.arch_calls == (1 if agreed else 2)
            gaps = run.store.records("architecture_gap")
            assert len(gaps) == (0 if agreed else 1)
            if not agreed:
                assert all(a in gaps[0][2]["gap"] for a in ("area-001", "area-002"))
            document = run.store.plan(run.store.cursor().revision)
            contracts = plan.select(document, "contract")
            contract, = contracts.values()
            assert contract["text"] == agreement
            delivered = [q["facts"] for q in provider.detail_calls if q["facts"]["Contracts"]]
            assert {f["Contracts"]["reading-control"]["side"] for f in delivered} == {
                "provider", "consumer"}
            assert all(f["Contracts"]["reading-control"]["text"] == agreement for f in delivered)
            requirements = plan.select(document, "requirement")
            assert all(commitment in r["obligation"] for r in requirements.values())
            wave_check, = [c for c in plan.assigned(document)[None].values()
                           if literal in c["purpose"]]
            assert len(wave_check["requirements"]) == 2
            assert not any(literal in c["purpose"] for ref, checks in plan.assigned(document).items()
                           if ref for c in checks.values())
            print(f"Agreed literals {'control' if agreed else 'fault twin'} -> B; "
                  f"ARCHITECT revisions {provider.arch_calls - 1}; cross-area check wave-only", flush=True)
        finally:
            run.close()


def user_words_journeys(root):
    """Lossy ALIGN still reaches B with the dream and every A/B revise; uncertainty is corrected.

    Existing named-value dreams fit the vision and revise only once, hiding lost source literals
    and earlier edits. The fault twin catches an ARCHITECT missing-facts answer ending the run
    instead of the ordinary schema correction and a visible planning decision.
    """
    dream = ("Build an importer. GET /api/items -> 200\n"
             "CSV: sku,name,quantity,expires_on\nShow imported N, skipped M.\n"
             "Choose the retention period.")
    changes = ["Revise: preserve status=ready.", "Revise: use UTF-8 CSV.",
               "Revise: include import_id.", "Revise: show retry-after=5."]
    literals = ["GET /api/items -> 200", "sku,name,quantity,expires_on", "imported N, skipped M",
                "status=ready", "UTF-8 CSV", "import_id", "retry-after=5"]

    class WordsProvider(AreaProvider):
        def areas(self):
            return [{"handle": f"area-{i:03}", "responsibility": "Deliver the importer",
                     "scope": [f"unit-{i:03}/**"],
                     "commitments": ["scopeIn:1", "quality:1", "quality:2"]}
                    for i in (1, 2)]

        def __call__(self, question):
            kind, facts = question["kind"], question["facts"]
            if kind == "DECIDE":
                return super().__call__(question)
            words = facts.get("User's words", {})
            text = "\n".join([words.get("Dream", ""), *words.get("Requested change", [])])
            found = sorted((literal for literal in literals if literal in text), key=text.index)
            if kind == "ARCHITECT" and fault and not self.arch_calls:
                self.arch_calls += 1
                return json.dumps({"answer": {"verdict": "uncertain",
                    "neededFacts": ["the referenced API specification", "CSV columns"],
                    "why": "The condensed vision does not specify them"}})
            answer = json.loads(super().__call__(question))
            body = answer["answer"]
            if kind == "ALIGN":
                body["understanding"] = "Import items with an API and CSV"
                body["scopeIn"] = ["Import and list items"]
            elif kind == "ARCHITECT":
                body["contracts"] = [{"handle": "import-format", "kind": "api",
                    "text": "; ".join(found), "provider": "area-001", "consumers": ["area-002"]}]
                body["decisions"] = [{"choice": "Retain imports for 30 days",
                    "reason": "The user left retention open", "origin": "architecture",
                    "areas": ["area-001"]}]
            elif kind == "DETAIL":
                for edit in body["edits"]:
                    if edit["kind"] == "requirement":
                        edit["typedBody"].update(obligation="; ".join(found), contract="; ".join(found))
            elif kind == "REVIEW":
                if not words:
                    body.update(verdict="gap", gaps=[{"target": facts["Review subject"],
                        "correction": "Supply the user's agreed text to review the plan"}])
            return json.dumps(answer)

    for fault in (False, True):
        slot = root / ("fault" if fault else "control")
        slot.mkdir(parents=True)
        provider = WordsProvider(slot, 2, "sizing")
        revised = {"A": 0, "B": 0}

        def user(item):
            gate = item["gate"]
            if gate in revised and revised[gate] < 2:
                offset = 0 if gate == "A" else 2
                words = changes[offset + revised[gate]]
                revised[gate] += 1
                return words
            return approve(item)

        run = Run(slot / "product", slot / "workers", dream, provider.setup, provider, user, capacity=6)
        try:
            for _ in range(45):
                if run.store.cursor().state == "B" and revised["B"] == 2:
                    break
                assert run.store.cursor().state != "EXECUTE"
                assert run_state(str(run.project))["status"] not in ("PAUSED", "WAITING")
                run.step()
            assert run.store.cursor().state == "B"
            run.step()  # The user approves the displayed final plan, without executing workers.
            approval = run.store.read(run.store.cursor().B)
            document = approval["subject"]["plan"]
            requirement = next(iter(plan.select(document, "requirement").values()))
            assert all(literal in requirement["obligation"] for literal in literals)
            assert [requirement["obligation"].index(literal) for literal in literals] == sorted(
                requirement["obligation"].index(literal) for literal in literals)
            contract, = plan.select(document, "contract").values()
            assert all(literal in contract["text"] for literal in literals[:5])
            assert any(d["choice"] == "Retain imports for 30 days"
                       for d in plan.select(document, "decision").values())
            assert approval["subject"]["review"]["answer"]["verdict"] == "adequate"
            assert not run.store.records("outcome")
            replies = [b for _, _, b in run.store.records("provider_reply")]
            rejected = [b for b in replies if b["errors"]]
            assert len(rejected) == (1 if fault else 0), rejected
            if fault:
                assert json.loads(rejected[0]["rawAnswer"])["answer"]["verdict"] == "uncertain"
        finally:
            run.close()
    print("User words twin + fault twin -> B with all A/B revisions and a recorded decision", flush=True)


def test_user_words_journeys(tmp_path):
    user_words_journeys(tmp_path / "user-words")


def test_planning_size_journey(tmp_path):
    size_journey(tmp_path / "size")


def test_review_links_journeys(tmp_path):
    review_links_journeys(tmp_path / "review-links")


def review_links_journeys(root):
    """Recorded g04 interface DETAIL reaches B with resolvable links; persistent gaps stay visible.

    Existing adequate reviewers ignore foreign ids, hiding merge-derived links the area cannot resolve.
    Their repaired reviews also cannot catch a count ending a run before the user sees the open gap.
    Retain g04's HTTP consumer checks; remove its obsolete provider-owned integration instructions.
    """
    # shelflife-26e68f0-g04: recorded answer r47 (interface DETAIL, first valid answer).
    interface_detail = {'edits': [{'kind': 'feature',
                'targetHandle': 'new:pantry-ledger',
                'typedBody': {'acceptance': 'Urgent food appears first, the searchable pantry '
                                            'second, and a clearly labeled add-item form last. The '
                                            'responsive page supports daily household use without '
                                            'downloaded assets.',
                              'story': 'Understand what needs using and manage the household pantry '
                                       'from one calm, accessible page.'}},
               {'kind': 'feature',
                'targetHandle': 'new:reliable-interactions',
                'typedBody': {'acceptance': 'Add, search, filter, consume and delete work without '
                                            'discarding search/filter choices or entered values on '
                                            'failure. Loading, empty, success and failure states '
                                            'remain understandable and actionable.',
                              'story': 'Make pantry changes confidently, including when validation '
                                       'or API requests fail.'}},
               {'kind': 'requirement',
                'targetHandle': 'new:ledger-presentation',
                'typedBody': {'contract': 'Page title: ShelfLife. Section headings, in order: '
                                          'Expiring soon, Pantry, Add item. Overdue items carry the '
                                          'label Expired. Keyboard accessible with visible focus; '
                                          'readable contrast; no horizontal scrolling at 375px. '
                                          'Clean, calm visual design.',
                              'design': 'Use warm ivory surfaces, ink-dark text, forest-green '
                                        'actions, restrained rust expiry warnings, Georgia headings '
                                        'and an Arial, Helvetica, sans-serif body. Use deliberate '
                                        'hierarchy, spacing and structured rows rather than nested '
                                        'cards. Use local-date expiry labels and treat loading, '
                                        'empty, success and failure states as designed content. No '
                                        'TODOs, no partial implementations.',
                              'features': ['new:pantry-ledger'],
                              'obligation': 'Deliver the complete responsive pantry page at phone '
                                            'and desktop sizes, with urgent food first, the '
                                            'searchable pantry second, and a clearly labeled '
                                            'add-item form.'}},
               {'kind': 'requirement',
                'targetHandle': 'new:pantry-interactions',
                'typedBody': {'contract': 'Consume http-items and http-errors. Expiring soon uses '
                                          'GET /api/items?expiring_within=7. Preserve search and '
                                          'filter choices after actions; show useful inline feedback '
                                          'without discarding entered values. Items have name (1-80 '
                                          'chars), quantity (integer >= 0), optional unit, and '
                                          'expires (YYYY-MM-DD or null).',
                              'design': 'Keep form and search/filter state independent of fetched '
                                        'results. Use the agreed query parameters and mutation '
                                        'routes; consume positive integer amounts. Refresh affected '
                                        'views after successful mutations without resetting '
                                        'controls. Provide labeled controls, inline validation, '
                                        'pending feedback, useful server/network error messages and '
                                        'explicit recovery. Render item strings as text, not HTML. '
                                        'Prevent duplicate submission while an action is pending.',
                              'features': ['new:reliable-interactions', 'new:pantry-ledger'],
                              'obligation': 'Implement full-list search and expiry filtering, '
                                            'add-item inline validation, consume and delete actions, '
                                            'expired labels, empty states and visible recoverable '
                                            'API failure states through the agreed API.'}},
               {'kind': 'requirement',
                'targetHandle': 'new:offline-page-assets',
                'typedBody': {'contract': 'Provide page-assets: / serves static/index.html as '
                                          'text/html; charset=utf-8. /app.css serves static/app.css '
                                          'as text/css; charset=utf-8. /app.js serves static/app.js '
                                          'as text/javascript; charset=utf-8. The page title is '
                                          'ShelfLife. Section headings, in order, are Expiring soon, '
                                          'Pantry, Add item. Overdue items carry the label Expired. '
                                          'Expiring soon uses GET /api/items?expiring_within=7.',
                              'design': 'Deliver plain HTML, CSS and browser JavaScript at the '
                                        'contracted paths. Reference only local resources. Use no '
                                        'third-party application dependencies, downloaded assets or '
                                        'build tooling. Keep UI-only copy and styling local to this '
                                        'owner.',
                              'features': ['new:pantry-ledger', 'new:reliable-interactions'],
                              'obligation': 'Supply complete offline assets consumable by the '
                                            'canonical server and demonstrate their integrated '
                                            'behavior through its real routes.'}},
               {'kind': 'check',
                'targetHandle': 'new:interface-local',
                'typedBody': {'command': ['C:\\nvm4w\\nodejs\\node.EXE',
                                          'checks/interface/local.cjs'],
                              'expectedEvidence': 'Assertions and actual Edge screenshots in '
                                                  'SELDON_EVIDENCE_DIR at 375px and desktop: '
                                                  'populated/expired, loading, empty, invalid form, '
                                                  'successful action, recoverable failure and '
                                                  'keyboard-focus states. Record no horizontal '
                                                  'overflow, labeled controls, retained '
                                                  'inputs/filters, safe rendering of hostile item '
                                                  'text and no external requests. Close browser and '
                                                  'fixture server.',
                              'level': 'unit',
                              'mode': 'establishes',
                              'purpose': 'Prove the actual owned page and interactions before '
                                         'integration. A bounded HTTP fixture serves the actual '
                                         'static files and supplies controlled API inputs, delayed '
                                         'responses and failures; it must not implement UI behavior '
                                         'or stand in for backend integration. Catch lost control '
                                         'state, inaccessible feedback, duplicate submissions and '
                                         'narrow-screen overflow. No existing interface tests or '
                                         'product files cover these behaviors.',
                              'requiredEnvironment': ['Supplied Node executable and '
                                                      'playwrightPackage',
                                                      'Supplied Edge browserExecutable',
                                                      'SELDON_EVIDENCE_DIR writable',
                                                      'Loopback HTTP fixture using Node '
                                                      'standard-library facilities'],
                              'requirements': ['new:ledger-presentation',
                                               'new:pantry-interactions',
                                               'new:offline-page-assets']}},
               {'kind': 'check',
                'targetHandle': 'new:page-assets-wave',
                'typedBody': {'command': ['C:\\nvm4w\\nodejs\\node.EXE',
                                          'checks/interface/page-assets-wave.cjs'],
                              'expectedEvidence': 'One integrated page-assets check verifies '
                                                  'canonical asset responses and MIME types, title '
                                                  'and ordered headings, the agreed urgent-list '
                                                  'request, and real '
                                                  'add/search/filter/consume/reload/restart/delete '
                                                  'behavior against a temporary SQLite database. '
                                                  'Screenshots show the real served page and '
                                                  'retained data after restart. Requests remain '
                                                  'local. Browser, child servers and temporary '
                                                  'resources are cleaned up.',
                              'level': 'wave',
                              'mode': 'establishes',
                              'purpose': 'Consumer integration check '
                                         'consuming http-items and http-errors. Catch mismatched '
                                         'asset routes, API envelopes or mutation refresh behavior '
                                         'hidden by controlled local responses. Use the canonical '
                                         'server and persistence, never a reconstructed backend. '
                                         'Exercise a real API validation failure and verify visible '
                                         'feedback without losing entered values. Local fixture '
                                         'evidence cannot establish this integration.',
                              'requiredEnvironment': ['Integrated server.py and its canonical '
                                                      'persistence dependency',
                                                      'C:\\Users\\oren\\AppData\\Local\\Programs\\'
                                                      'Python\\Python312\\python.exe, '
                                                      'invoked with -B',
                                                      'Supplied Node, playwrightPackage and Edge '
                                                      'browserExecutable',
                                                      'Writable temporary database directory and '
                                                      'SELDON_EVIDENCE_DIR',
                                                      'Available loopback port'],
                              'requirements': ['new:offline-page-assets',
                                               'new:pantry-interactions',
                                               'http-items',
                                               'http-errors']}}],
     'quality': [{'checks': ['new:interface-local'], 'criterion': 1},
                 {'checks': ['new:interface-local'], 'criterion': 5}],
     'units': [{'checkPaths': ['checks/interface/**'],
                'consumes': ['http-items', 'http-errors'],
                'dependencies': [],
                'instructions': 'Own the whole interface and its checks as one coherent '
                                'implementation. Create static/index.html, static/app.css and '
                                'static/app.js; keep all additional interface helpers and fixtures '
                                'beneath checks/interface/**. Implement only the accepted '
                                'page/actions, not extra editing screens or backend behavior. The '
                                'local check must exercise actual browser assets and save all states '
                                'needed for visual judgment. Its fixture proves UI behavior only. '
                                'Use the supplied '
                                'runtime paths without installs or downloads. Link consumed '
                                'contracts to their provider requirements through the contract '
                                'handles, not guessed foreign requirement names. Backend validation, '
                                'concurrency, importer and README ownership remain outside this '
                                'unit.',
                'provides': ['page-assets'],
                'reads': [],
                'requirements': ['new:ledger-presentation',
                                 'new:pantry-interactions',
                                 'new:offline-page-assets'],
                'scope': ['static/**', 'checks/interface/**']}],
     'verdict': 'planned',
     'why': 'One owner covers the complete interface scope without introducing internal contracts or '
            'serial handoffs. Local browser evidence establishes owned behavior and visual states; '
            'Backend, importer and fresh-folder delivery criteria remain with their '
            'respective owners.'}
    correction = "Explain how API failure feedback preserves entered values"

    class ReviewLinksProvider(Scripted):
        def __call__(self, question):
            kind, facts = question["kind"], question["facts"]
            if kind == "ALIGN":
                answer = {**VISION, "quality": [
                    {"criterion": f"Pantry quality {i}", "verification": "Observe the pantry workflow"}
                    for i in range(1, 7)]}
            elif kind == "ARCHITECT":
                answer = {"areas": [
                    {"handle": "server", "responsibility": "Serve the canonical pantry API",
                     "scope": ["server.py", "checks/server/**"],
                     "commitments": ["scopeIn:1", "quality:2", "quality:3", "quality:4", "quality:6"]},
                    {"handle": "interface", "responsibility": "Deliver the complete pantry interface",
                     "scope": ["static/**", "checks/interface/**"],
                     "commitments": ["scopeIn:1", "quality:1", "quality:5"]}],
                    "contracts": [
                        {"handle": h, "kind": "api", "text": text,
                         "provider": "server", "consumers": ["interface"]}
                        for h, text in (("http-items", "Canonical pantry item routes and JSON responses"),
                                       ("http-errors", "HTTP validation and failure response envelopes"))] + [
                        {"handle": "page-assets", "kind": "file-format",
                         "text": "ShelfLife offline HTML, CSS and JavaScript assets",
                         "provider": "interface", "consumers": ["server"]}],
                    "decisions": [], "waves": [{"goal": "Run the pantry end to end"}]}
            elif kind == "DETAIL":
                if facts["Area"]["handle"] == "interface":
                    return json.dumps({"answer": interface_detail})
                requirements = ["new:http-operations", "new:http-failures",
                                "new:server-launch", "new:server-assets"]
                local = {"purpose": "Exercise the server's owned behavior", "level": "unit",
                         "mode": "establishes", "command": ["python", "checks/server/local.py"],
                         "requiredEnvironment": ["Python"], "expectedEvidence": "Real HTTP responses",
                         "requirements": requirements}
                answer = {"verdict": "planned", "edits": [
                    {"kind": "feature", "targetHandle": "new:server", "typedBody": {
                        "story": "Use the pantry API", "acceptance": "Persist and retrieve pantry items"}},
                    *[{"kind": "requirement", "targetHandle": r, "typedBody": {
                        "obligation": r, "design": "Canonical HTTP server", "contract": r,
                        "features": ["new:server"]}} for r in requirements],
                    {"kind": "check", "targetHandle": "new:server-local", "typedBody": local},
                    *[{"kind": "check", "targetHandle": "new:" + h, "typedBody": {
                        **local, "level": "wave", "requirements": [r],
                        "command": ["python", f"checks/server/{h}.py"]}}
                      for h, r in zip(("http-items", "http-errors"), requirements)]],
                    "units": [{"scope": ["server.py", "checks/server/**"],
                        "checkPaths": ["checks/server/**"], "requirements": requirements,
                        "provides": ["http-items", "http-errors"], "consumes": ["page-assets"],
                        "reads": [], "dependencies": [], "instructions": "Deliver the pantry server"}],
                    "quality": [{"criterion": i, "checks": ["new:server-local"]} for i in (2, 3, 4, 6)]}
            elif kind == "REVIEW" and facts["Review subject"] == "interface":
                nodes = facts["Plan"]["nodes"]
                missing = sorted({r for c in plan.select(facts["Plan"], "check").values()
                                  for r in c["requirements"] if r not in nodes})
                wave = next(r for r, c in plan.select(facts["Plan"], "check").items()
                            if c["command"][-1] == "checks/interface/page-assets-wave.cjs")
                gaps = ([{"target": wave, "correction": "Resolve foreign requirements: " + str(missing)}]
                        if missing else [{"target": wave, "correction": correction}]
                        if persistent_gap else [])
                answer = {"verdict": "gap" if gaps else "adequate", "gaps": gaps}
            else:
                return super().__call__(question)
            return json.dumps({"answer": {**answer, "why": "Review the area's own plan and agreements"}})

    class DisplayedB(Exception):
        pass

    def user(request):
        if request["gate"] == "B":
            raise DisplayedB
        return approve(request)

    for persistent_gap in (False, True):
        slot = root / ("gap" if persistent_gap else "resolvable")
        slot.mkdir(parents=True)
        provider = ReviewLinksProvider(slot, "review-links", 1)
        run = Run(slot / "product", slot / "workers", "Build the ShelfLife pantry", provider.setup,
                  provider, user, capacity=2)
        try:
            if persistent_gap:
                run.run()
                shown = run_state(str(run.project))
                assert shown["status"] == "WAITING" and not run.store.cursor().B
                assert correction in str(shown["waitingFor"]["subject"])
                assert not run.store.records("outcome")
            else:
                while run.store.cursor().state != "B":
                    run.step()
                with pytest.raises(DisplayedB):
                    run.step()
                displayed = run.store.read(run.store.cursor().decision)["subject"]
                assert displayed["review"]["answer"]["verdict"] == "adequate"
                question, = [q for _, _, q in run.store.records("question")
                             if q["kind"] == "REVIEW" and q["facts"]["Review subject"] == "interface"]
                check = next(c for c in plan.select(question["facts"]["Plan"], "check").values()
                             if c["command"][-1] == "checks/interface/page-assets-wave.cjs")
                assert check["integrates"] == ["http-items", "http-errors"]
                canonical_check = next(c for c in plan.select(displayed["plan"], "check").values()
                                       if c["command"] == check["command"])
                assert len(canonical_check["requirements"]) == len(check["requirements"]) + 4
                assert "integrates" not in canonical_check
        finally:
            run.close()


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
                assert run.store.cursor().state not in ("STOPPED", "SCRIPTED_C")
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
            if self.defect in ("path", "persistent", "targeted-exact") and (
                    self.defect == "persistent" or "Merge correction" not in facts):
                body["units"][0].update(reads=["value.txt"], dependencies=["value.txt"])
        if (question["kind"] == "DETAIL" and self.defect == "cycle"
                and question["facts"]["Area"]["handle"] == "area-001"
                and "Merge correction" not in question["facts"]):
            answer["answer"]["units"][0]["dependencies"] = ["unit-003/value.txt"]
        if (question["kind"] == "DETAIL" and self.defect == "targeted-exact"
                and "Merge correction" in question["facts"]
                and "Exact corrections" not in question["facts"]):
            del answer["answer"]["units"]
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
    """A single-unit obligation exhausts 3+3 answers, then deliberately resumes to approved B.

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
        run.run()
        assert run_state(str(run.project))["status"] == "WAITING"
        assert len(provider.detail_calls) == 6
        subject = run_state(str(run.project))["waitingFor"]["subject"]
        assert "area-001" in str(subject) and "skeleton" in str(subject)
        assert not run.store.cursor().B
        run.close()
        run = Run.reopen(root / "product", provider, approve)
        provider.repaired = True
        run.user = lambda item: "try again" if item["gate"] == "DETAIL" else approve(item)
        run.step()
        while run.store.cursor().state != "EXECUTE":
            run.step()
        assert run.store.cursor().B and len(provider.detail_calls) == 7
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
    A malformed targeted re-ask needs an exact-fix retry; ordinary valid re-asks miss the y09 failure.
    """
    began = time.monotonic()
    for mode in ("path", "persistent", "cycle", "scope", "targeted-exact"):
        run, provider = start(root / mode, mode="contracts")

        provider_type = ScopeProvider if mode == "scope" else CorrectionProvider
        provider = provider_type(provider.slot, 3, "contracts")
        provider.defect = mode
        provider.barrier = None
        provider.interrupted = True
        run.provider = provider
        try:
            if mode == "persistent":
                run.run()
                assert run_state(str(run.project))["status"] == "WAITING"
                reason = str(run_state(str(run.project))["waitingFor"]["subject"])
                assert "area-002" in reason and "value.txt" in reason
                assert not any(b["gate"] == "B" for _, _, b in run.store.records("decision_request"))
            else:
                while run.store.cursor().state != "EXECUTE":
                    run.step()
                assert {b["gate"] for _, _, b in run.store.records("decision_request")} == {"A", "B"}
                assert not run.store.units()
            reasks = [q for q in provider.detail_calls if "Merge correction" in q["facts"]]
            assert sorted(q["facts"]["Area"]["handle"] for q in reasks) == (
                [] if mode in ("scope", "cycle")
                else ["area-002", "area-002"] if mode == "targeted-exact" else ["area-002"])
            if mode == "cycle":
                assert not plan.select(run.store.plan(run.store.cursor().revision), 'unit')['u001'][
                    'dependencies']
            assert any("Exact corrections" in q["facts"] for q in provider.detail_calls) == (
                mode == "targeted-exact")
            assert provider.arch_calls == (2 if mode == "scope" else 1)
            if mode == "scope":
                assert [q["facts"]["Area"]["handle"] for q in provider.detail_calls].count("area-001") == 1
                assert [q["facts"]["Area"]["handle"] for q in provider.detail_calls].count("area-003") == 1
            if mode == "persistent":
                assert not run.store.records("outcome")
                calls = len(provider.detail_calls)
                run.close()
                run = Run.reopen(root / mode / "product", provider, approve)
                assert run_state(str(run.project))["status"] == "WAITING" and len(provider.detail_calls) == calls
                provider.defect = "path"
                run.user = lambda item: "try again" if item["gate"] == "DETAIL" else approve(item)
                run.step()
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
            if "architectureCorrection" in self.recording:
                return self.recording["architectureCorrection"]
            answer = deepcopy(json.loads(self.recording["architect"])["answer"])
            for area in answer["areas"]:
                area["scope"].append(f"checks/{area['handle']}/**")
            return json.dumps({"answer": answer})
        if kind != "DETAIL":
            return super().__call__(question)
        self.detail_calls.append(deepcopy(question))
        area = facts["Area"]["handle"]
        raw = self.recording["details"][area]["raw"]
        if "pagePlan" in self.recording:
            if area == "page" and facts["Contracts"]["assembly-slots"]["text"] != json.loads(
                    self.recording["architect"])["answer"]["contracts"][0]["text"]:
                raw = self.recording["pagePlan"]
            if not any(k in facts for k in ("Exact corrections", "Merge correction")):
                return raw
            answer = deepcopy(facts.get("Previous area plan") or json.loads(raw)["answer"])
            for unit in answer["units"]:
                unit["consumes"] = [r for r in unit["consumes"]
                                    if facts["Contracts"][r]["side"] == "consumer"]
                unit["reads"] = ["checks/shared/assembly.cjs" if p == "checks/shared/**" else p
                                 for p in unit["reads"]]
                for req in unit["requirements"]:
                    if any(e["kind"] == "check" and req in e["typedBody"]["requirements"]
                           and set(e["typedBody"]["requirements"]) <= set(unit["requirements"])
                           for e in answer["edits"]):
                        continue
                    answer["edits"].append({"kind": "check", "targetHandle": req + "-local", "typedBody": {
                        "purpose": "Prove this owner's contribution against the agreed contract text; "
                                   "integrated browser checks retain real cross-area proof.",
                        "mode": "establishes", "command": ["node", unit["checkPaths"][0].replace(
                            "**", "local.cjs"), "--local", req],
                        "requiredEnvironment": ["Supplied Node, Edge and Playwright"],
                        "expectedEvidence": "Observe the owned contribution against the published interface; "
                                            "no sibling implementation supplied by a mock.",
                        "requirements": [req]}})
            return json.dumps({"answer": answer})
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
    The bd6d374 reply exposes an in-area integration owner using its own area's contract. Earlier
    recordings collapse the page to one unit, so cannot catch missing shared text or a false endpoint.
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
                assert run_state(str(run.project))["status"] not in ("PAUSED", "WAITING")
                run.step()
            assert run.store.cursor().state == "EXECUTE" and run.store.cursor().B
            assert not run.store.units()
            rows = run.store.records()
            replies = [b["rawAnswer"] for _, k, b in rows if k == "provider_reply"]
            assert recording["architect"] in replies
            assert all(d["raw"] in replies for d in recording["details"].values())
            own_contract = "pagePlan" in recording
            if own_contract:
                assert recording["architectureCorrection"] in replies
                assert recording["pagePlan"] in replies
            owns_checks = "5e3e40e" in recording["source"]
            assert provider.arch_calls == (1 if owns_checks else 2)
            assert len(run.store.records("architecture_gap")) == (0 if owns_checks else 1)
            assert any(k == "transition" and b["next"] == "REVIEW" for _, k, b in rows)
            assert {b["gate"] for _, k, b in rows if k == "decision_request"} == {"A", "B"}
            wave = json.loads(recording["architect"])["answer"]["waves"][0]
            for question in provider.detail_calls:
                expected = wave
                if own_contract:
                    initial = architecture.index(json.loads(recording["architect"])["answer"]["contracts"])
                    if question["facts"]["Contracts"]["assembly-slots"]["text"] != initial[
                            "assembly-slots"]["text"]:
                        expected = json.loads(recording["architectureCorrection"])["answer"]["waves"][0]
                assert question["facts"]["Current wave"] == expected
            document = run.store.plan(run.store.cursor().revision)
            plan.validate(document)
            if own_contract:
                units = plan.select(document, "unit")
                contracts = plan.select(document, "contract")
                agreed = architecture.index(json.loads(recording["architectureCorrection"])[
                    "answer"]["contracts"])["assembly-slots"]["text"]
                shared = next(r for r, c in contracts.items() if c["text"] == agreed)
                provider_unit = units[contracts[shared]["provider"]]
                integration = next(r for r, u in units.items() if u["area"] == provider_unit["area"]
                                   and r != contracts[shared]["provider"] and r != "guide")
                from seldon import questions
                assert contracts[shared]["text"] == questions.agreement_facts(document, integration)[
                    "Area contracts"][shared]["text"]
                assert integration not in contracts[shared]["consumers"]
                assert contracts[shared]["provider"] in units[integration]["dependencies"]
                invalid = deepcopy(document)
                plan.select(invalid, "contract")[shared]["consumers"].append(integration)
                plan.select(invalid, "unit")[integration]["consumes"].append(shared)
                with pytest.raises(ValueError, match="consumer endpoints must cross area boundaries"):
                    architecture.validate(invalid)
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


@pytest.mark.fast
def test_detail_correction_journeys(tmp_path):
    correction_journeys(tmp_path / "corrections")


@pytest.mark.fast
def test_recorded_detail_journeys(tmp_path):
    recorded_detail_journeys(tmp_path / "recorded-detail")


def architect_journeys(tmp_path):
    """Contract delivery and local planning recovery share the canonical plan and recorder."""
    began = time.monotonic()
    review_links_journeys(tmp_path / "review-links")
    recorded_detail_journeys(tmp_path / "recorded")
    area_product_journey(tmp_path)
    for mode in ("gap", "split"):
        area_recovery_journey(tmp_path, mode)
    print(f"ARCHITECT contract journeys: {time.monotonic()-began:.2f}s", flush=True)


def check_owner_journeys(root):
    """DETAIL's declared placement reaches real workers and integrated proof, with one file owner.

    Existing multi-requirement fixtures hide an own-only wave check wrongly run at T or P.
    The same delivery exercises old missing levels, promotion across units, argv naming a sibling,
    and binary check evidence that previously overflowed the unit judge. WORK's real recorder must
    correct a prose owner once; done-only workers never exercise the owner enum.
    """
    from seldon import gitwork, inference, questions

    class CheckProvider(AreaProvider):
        def __call__(self, question):
            answer = json.loads(super().__call__(question))
            if question["kind"] == "DETAIL":
                body = answer["answer"]
                body["units"][0]["checkPaths"] = ["unit-001/*.py", "unit-001/evidence/**"]
                for handle, level, requirements in (
                        ("declared-wave", "wave", ["new:r001"]),
                        ("spanning", "unit", ["new:r001", "new:r002"]),
                        ("old-spanning", None, ["new:r001", "new:r002"])):
                    check = deepcopy(body["edits"][2])
                    check.update(targetHandle=handle)
                    check["typedBody"].update(purpose=handle, mode="preserves",
                        command=[body["edits"][2]["typedBody"]["command"][0], "-B",
                                 "unit-001/wave.py", "unit-002/value.txt"],
                        requirements=requirements)
                    if level:
                        check["typedBody"]["level"] = level
                    body["edits"].append(check)
            return json.dumps(answer)

    run, base = start(root, 2, "sizing")
    provider = CheckProvider(base.slot, 2, "sizing")
    run.provider = provider
    native = root / "native.py"
    wave_source = ("from pathlib import Path\n"
                   "assert Path('unit-001/value.txt').read_text() == 'value-001'\n"
                   "assert Path('unit-002/value.txt').read_text() == 'value-002'\n")
    native.write_text(native.read_text("utf-8").replace(
        'print(json.dumps({"type":"item.completed"',
        "if name == 'unit-001':\n"
        + "    Path('unit-001/wave.py').write_text(" + repr(wave_source) + ")\n"
        + 'print(json.dumps({"type":"item.completed"'), encoding="utf-8")
    try:
        while run.store.cursor().state != "WAVE-VERIFY":
            assert run_state(str(run.project))["status"] not in ("PAUSED", "WAITING")
            run.step()
        document = run.store.plan(run.store.cursor().revision)
        waves = plan.assigned(document)[None]
        assert {c["purpose"] for c in waves.values()} == {
            "declared-wave", "spanning", "old-spanning"}
        assert all(plan.owner(document, ref) == "u001" for ref in waves)
        before = run.store.records("observation")
        baseline = [b for _, _, b in before if b["check"] in waves]
        assert baseline and all(b["purpose"] == "wave-start" for b in baseline)
        assert all(b["exitCode"] != 0 for b in baseline)
        run.step()
        assert run.store.cursor().state == "FLOW-JUDGE", run.store.records("outcome")
        observations = [b for _, _, b in run.store.records("observation")
                        if b["check"] in waves and b["purpose"] == "final"]
        assert len(observations) == 3 and all(b["exitCode"] == 0 for b in observations)
        assert all(b["commit"] == run.store.cursor().candidate for b in observations)

        # Preserve source text and bound the actual JUDGE after adding the retained failure's 13 PNGs.
        project = run.project
        unit = run.store.unit("u001")
        text_question = questions.judge_brief(run.store, "u001", commit=unit.P)
        evidence = project / "unit-001/evidence"
        evidence.mkdir()
        recording = json.loads(Path(__file__).with_name("recorded_judgment.json").read_text("utf-8"))
        png = base64.b64decode(next(iter(recording["images"].values())))
        for i in range(13):
            (evidence / f"{i:02}.png").write_bytes(png)
        gitwork.git(project, "add", "--", "unit-001/evidence")
        tree = gitwork.git(project, "write-tree")
        candidate = gitwork.commit(project, tree, unit.P)
        binary_question = questions.judge_brief(run.store, "u001", commit=candidate)
        assert binary_question["facts"]["Check sources"] == text_question["facts"]["Check sources"]
        assert binary_question["facts"]["Binary files at P (bytes)"] == {
            f"unit-001/evidence/{i:02}.png": len(png) for i in range(13)}
        delta = len(encode(binary_question).encode()) - len(encode(text_question).encode())
        assert delta < 3000, delta

        question = questions.work_brief(run.store, "guide")
        replies = []

        def blocked(current):
            replies.append(current)
            return json.dumps({"answer": {"outcome": "blocked", "summary": "Needs its provider",
                "discoveries": [{"owner": "the first value owner" if len(replies) == 1 else "u001",
                                 "correction": "Correct the shared reading"}],
                "why": "The defect belongs to the prerequisite's scope"}})

        _, answer = inference.call(run.store, blocked, question)
        assert answer["discoveries"] == [{"owner": "u001", "correction": "Correct the shared reading"}]
        assert len(replies) == 2 and "Exact corrections" in replies[-1]["facts"]
        print(f"Check ownership: declared/spanning/legacy wave baseline observed, integrated GREEN; "
              f"13 PNGs add {delta} JUDGE bytes; prose WORK owner corrected once", flush=True)
    finally:
        run.close()
    size_journey(root / "size")


def retained_area_answer(provider, q):
    """Keep the retained fault/recording corpus on the actual area-planning path."""
    if provider.mode == "user-revise" and "Merge correction" in q["facts"]:
        assert q["facts"]["Merge correction"][0]["error"] == (
            "Revise the plan: describe the exact stored value.")
        provider.plan_revised = True
    full = json.loads(provider({**q, "kind": "PLAN"}))["answer"]
    units = full["units"]
    # These old synthetic commands had no source file, so could not have a file owner. Give each
    # fixture an ordinary worker-written check; production ownership never guesses from coverage.
    for i, edit in enumerate(full["edits"]):
        if edit["kind"] != "check" or "-c" not in edit["typedBody"]["command"]:
            continue
        check = edit["typedBody"]
        writer = next(u for u in units if check["requirements"][-1] in u["requirements"])
        directory = writer["checkPaths"][0].split("/")[0]
        path = f"{directory}/inline-{i}.py"
        source = check["command"][-1]
        check["command"] = [*check["command"][:-2], path]
        writer["checkPaths"].append(path)
        native = provider.slot / "native.py"
        marker = f"# owned fixture {path}"
        text = native.read_text("utf-8")
        if marker not in text:
            native.write_text(text.replace('print(json.dumps({"type":"item.completed"',
                f"{marker}\nif name == {directory!r}:\n    Path({path!r}).write_text({source!r})\n"
                + 'print(json.dumps({"type":"item.completed"'), encoding="utf-8")
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
            if not any(plan.matches(arg, plan.owned_scope(units[n-1]))
                       for n in numbers for arg in check["typedBody"]["command"]):
                continue
            external = reqs - owned
            links = [e for e in contracts if area in
                     [mapping[n][0] for n in e["typedBody"]["consumers"]]
                     and external <= set(units[e["typedBody"]["provider"]-1]["requirements"])]
            if links:
                local = deepcopy(check)
                local["typedBody"]["level"] = "wave"
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


@pytest.mark.fast
@pytest.mark.parametrize("decline", [False, True])
def test_stuck_step_ask_journey(tmp_path, decline):
    """Three invalid ARCHITECT answers ask at that step; only the user's decline ends it.

    Valid planning never exercises the exact-fix limit, and unit recovery cannot protect a planning
    question's persistence or delivery of the user's words to its next canonical ARCHITECT brief.
    """
    from seldon.frontdoor import state
    from test_journey import directed_provider, prepare

    run, provider = prepare(tmp_path / "stuck", "ask", 1)
    provider.architecture_fault = lambda answer: answer.update(units=[])
    try:
        assert run.run().state == "ARCHITECT"
        waiting = state(str(run.project))["waitingFor"]
        assert state(str(run.project))["status"] == "WAITING"
        assert waiting["gate"] == "ARCHITECT" and "unexpected field" in str(waiting["subject"])
        assert not run.store.records("outcome")
        directed_provider(provider)
        words = "end this dream" if decline else "use the items route, not a fixture"
        provider.architecture_fault = None
        run.close()
        run = Run.reopen(provider.slot / "product", provider,
                         lambda item: words if item["gate"] == "ARCHITECT" else approve(item))
        if decline:
            assert run.run().state == "STOPPED"
            assert state(str(run.project))["status"] == "STOPPED"
            assert run.store.records("outcome")[-1][2]["reason"] == words
        else:
            finish(run)
            question = [b for _, _, b in run.store.records("question") if b["kind"] == "ARCHITECT"][-1]
            assert words in str(question["facts"]["User's words"])
    finally:
        run.close()
